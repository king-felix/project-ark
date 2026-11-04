"""Project ARK — how a file gets from a phone's browser onto the card, safely.

The rules in one place, so the reading page, the owner's page and the importer
cannot drift apart:

* **The name a person sees is a label, not a path.**  ``display_name()`` strips
  directories, control characters and anything else a filesystem or a URL might
  read as structure.  It never decides where a file is stored.
* **Every upload gets its own place on disk.**  The stored name starts with 128
  random bits and is only accepted if it is genuinely new — the final move uses
  a hard link, which *refuses* to replace an existing file.  Two people sharing
  something called ``photo.jpg`` in the same minute therefore cannot lose one of
  them, which is exactly what the previous release did.
* **Nothing half-written is ever visible.**  Bytes land in ``drop/tmp`` (which
  nginx does not serve), are flushed to disk, and only then appear in
  ``drop/files``.  A refused upload leaves no residue.
* **The cap is enforced before the disk fills, and room is promised first.**  The
  limit is the same number nginx is configured with (see ``pi/nginx/ark.conf``);
  the reserve is ``ark_space``'s (7 % of the receiving filesystem, or 2 GiB) —
  there is no second number here.  Inside an upload the spool adopts the promise
  the request already made; outside one it takes its own reservation.  A full card
  takes the whole portal down, not just the upload, which is what the reserve is
  for.
"""

from __future__ import annotations

import errno
import hashlib
import mimetypes
import os
import re
import secrets
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import ark_space

MAX_FILE = 2 * 1024 * 1024 * 1024          # 2 GiB per file; nginx allows 16 MiB more
CHUNK = 1024 * 1024
#: How far ahead an unknown-size file promises.  Ark-space's number, not a second
#: policy: one step is small enough to refuse early and large enough that a
#: multi-gigabyte upload does not write a ledger row every megabyte.
STEP_BYTES = ark_space.STEP_BYTES
TEMP_SUFFIX = ".part"
MAX_DISPLAY = 120

_ILLEGAL = re.compile(r"[^A-Za-z0-9._ ()\[\]+,-]+")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


#: The reserve is not a number in this module any more.  ``ark_space`` owns the
#: policy — 7 % of the filesystem that is receiving the bytes, or 2 GiB, whichever
#: is larger — so an upload and a conversion and a collection being fetched all
#: argue from the same arithmetic instead of three that disagree.
Refused = ark_space.Refused


@dataclass(frozen=True)
class Stored:
    stored: str        # the name on disk, and the last part of the download link
    display: str       # what the person called it
    size: int
    sha256: str
    path: Path


# ---------------------------------------------------------------------------
#  where things live
# ---------------------------------------------------------------------------
def ark_root() -> Path:
    return Path(os.environ.get("ARK_ROOT", "/srv/ark"))


def drop_dir(root: Path | None = None) -> Path:
    """Served by nginx as /drop/ — finished files only."""
    return (root or ark_root()) / "drop" / "files"


def temp_dir(root: Path | None = None) -> Path:
    """Not served by anything. A partial upload lives and dies here."""
    return (root or ark_root()) / "drop" / "tmp"


def ensure_dirs(root: Path | None = None) -> list:
    """Create the shared folders, for as many of them as the card allows.

    A shared folder that cannot be made is a broken File Box, not a broken box:
    the library, the books, the guides and the games are separate paths and must
    still start.  The failures are returned so a caller may report them; the
    health page measures the folders directly rather than trusting this pass.
    """
    failed = []
    for folder in (drop_dir(root), temp_dir(root)):
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            failed.append("%s: %s" % (folder, exc))
    return failed


def present_names(root: Path | None = None) -> set[str] | None:
    """The names really in the shared folder — ``None`` if it cannot be read.

    A row in the records and a file on the card are two different facts, and the
    box creates the difference on purpose: ``ark-restore --no-files`` puts the
    records back on a card whose File Box is empty.  So anything that offers a
    download asks the folder as well as the records.  ``None`` is not an empty
    box: it means the folder could not be read, which is a different sentence.
    """
    try:
        return {entry.name for entry in os.scandir(drop_dir(root)) if entry.is_file()}
    except OSError:
        return None


# ---------------------------------------------------------------------------
#  names
# ---------------------------------------------------------------------------
def display_name(raw: str) -> str:
    """A safe, human label for an upload — never a path, never empty."""
    name = os.path.basename((raw or "").replace("\\", "/")).strip()
    name = _CONTROL.sub("", name)
    name = _ILLEGAL.sub("_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    if not name:
        return "file"
    stem, ext = os.path.splitext(name)
    if len(ext) > 12:                 # not really an extension
        stem, ext = name, ""
    stem = stem[:MAX_DISPLAY - len(ext)].rstrip(" .") or "file"
    return stem + ext


def public_url(stored: str) -> str:
    return "/drop/" + quote(stored, safe="")


def guess_type(display: str, fallback: str = "application/octet-stream") -> str:
    return mimetypes.guess_type(display)[0] or fallback


# ---------------------------------------------------------------------------
#  free space
# ---------------------------------------------------------------------------
def free_bytes(path: Path | None = None) -> int:
    """Space an ordinary account may still write, on the card that holds ``path``."""
    return ark_space.free_bytes(path or ark_root())


def reserve_bytes(path: Path | None = None) -> int:
    """What the policy keeps back on that filesystem."""
    return ark_space.reserve_bytes(path or ark_root())


# ---------------------------------------------------------------------------
#  the one write path
# ---------------------------------------------------------------------------
def spool(read, filename: str, *, limit: int | None = None,
          destination: Path | None = None, temp: Path | None = None) -> Stored:
    """Stream ``read(n)`` into a hidden temp file, then publish it atomically.

    ``read`` is any callable taking a byte count and returning at most that many
    bytes (a file object's ``read``, a socket, ``io.BytesIO(...).read``).  An
    empty result means the end of the file.

    The room this needs is **promised before it is used**, through
    ``ark_space.cover``: inside an upload that means adopting the promise the
    request already made (the framework's temp copy is part of the same peak), and
    outside one it means a reservation of its own.  A file whose size is not known
    grows its promise a step at a time and is refused before it writes past it —
    the old check-then-write loop let two uploads spend the same free bytes.
    """
    limit = MAX_FILE if limit is None else int(limit)
    if limit <= 0:
        raise Refused("This upload is as large as one file may be — send the rest "
                      "separately.")
    dest = Path(destination) if destination else drop_dir()
    tmpdir = Path(temp) if temp else temp_dir()
    dest.mkdir(parents=True, exist_ok=True)
    tmpdir.mkdir(parents=True, exist_ok=True)

    display = display_name(filename)
    digest = hashlib.sha256()
    written = 0
    fd, tmp = tempfile.mkstemp(dir=str(tmpdir), prefix="upload-", suffix=TEMP_SUFFIX)
    try:
        with os.fdopen(fd, "wb") as out:
            with ark_space.cover(dest, ark_space.first_step(dest, limit),
                                 owner=f"spool:{display}", label="a shared file",
                                 key="file") as receipt:
                while True:
                    chunk = read(CHUNK)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > limit:
                        raise Refused(f"That file is too big. Files up to "
                                      f"{_size_words(limit)} are accepted — split it or "
                                      f"use a smaller copy, then try again.")
                    # Grow *before* writing past the promise; a chunk is never
                    # larger than a step, so there is always room for the next one.
                    if written + CHUNK > receipt.reserved:
                        receipt.need(ark_space.growth_step(dest, written, limit))
                    receipt.spent("file", written)
                    digest.update(chunk)
                    out.write(chunk)
                out.flush()
                os.fsync(out.fileno())
                receipt.spent("file", written)
        if written == 0:
            raise Refused("That file was empty, so there was nothing to share.")
        os.chmod(tmp, 0o644)
        stored = _publish(tmp, dest, display)
        return Stored(stored=stored, display=display, size=written,
                      sha256=digest.hexdigest(), path=dest / stored)
    except OSError as exc:
        _unlink(tmp)
        if exc.errno == errno.ENOSPC:
            raise Refused("The card filled up while that file was arriving. "
                          "Nothing was kept. Free some room, then try again.") from exc
        raise Refused("The box could not store that file. Check that the card is "
                      "inserted and has room, then try again.") from exc
    except BaseException:
        _unlink(tmp)
        raise


def _size_words(n: int) -> str:
    n = int(n or 0)
    for unit, size in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("kB", 1024)):
        if abs(n) >= size:
            return f"{n / size:.1f} {unit}"
    return f"{n} bytes"


def _publish(tmp: str, dest: Path, display: str) -> str:
    """Move the finished temp file into place without ever replacing a file."""
    for attempt in range(1, 9):
        token = secrets.token_hex(8)
        candidate = f"{token}-{display}" if attempt == 1 else f"{token}-{attempt}-{display}"
        target = dest / candidate
        try:
            os.link(tmp, target)        # fails if target exists: that is the point
        except FileExistsError:
            continue
        except OSError:
            # A filesystem without hard links. Fall back to a checked rename:
            # still atomic, and it only refuses one racing writer.
            if target.exists():
                continue
            os.replace(tmp, target)
            os.chmod(target, 0o644)
            return candidate
        _unlink(tmp)
        return candidate
    _unlink(tmp)
    raise Refused("The box could not find a free name for that file. Try once more.")


def discard(path: str | Path) -> None:
    """Remove a copy this box wrote.  A file already gone is not an error."""
    _unlink(path)


def _unlink(path: str | Path) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass          # already gone, or never created


# ---------------------------------------------------------------------------
#  housekeeping
# ---------------------------------------------------------------------------
def reconcile_temp(max_age: int = 6 * 3600) -> int:
    """Delete partial uploads left behind by a crash or a closed browser."""
    tmpdir = temp_dir()
    if not tmpdir.is_dir():
        return 0
    removed = 0
    now = time.time()
    for path in tmpdir.iterdir():
        try:
            if not path.is_file():
                continue
            if now - path.stat().st_mtime < max_age:
                continue
            path.unlink()
            removed += 1
        except OSError:
            continue
    return removed
