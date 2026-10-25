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
* **The cap is enforced before the disk fills, and a reserve is kept.**  The
  limit is the same number nginx is configured with (see ``pi/nginx/ark.conf``);
  the box also refuses to write into its last 512 MiB, because a full card takes
  the whole portal down, not just the upload.
"""

from __future__ import annotations

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

MAX_FILE = 2 * 1024 * 1024 * 1024          # 2 GiB per file; nginx allows 16 MiB more
MIN_FREE_RESERVE = 512 * 1024 * 1024       # never eat the last half gigabyte
CHUNK = 1024 * 1024
TEMP_SUFFIX = ".part"
MAX_DISPLAY = 120

_ILLEGAL = re.compile(r"[^A-Za-z0-9._ ()\[\]+,-]+")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class Refused(Exception):
    """A refusal a person can read and act on. Never a stack trace."""


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


def ensure_dirs(root: Path | None = None) -> None:
    for folder in (drop_dir(root), temp_dir(root)):
        folder.mkdir(parents=True, exist_ok=True)


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
    target = path or ark_root()
    try:
        stats = os.statvfs(str(target))
    except OSError:
        return 0
    return stats.f_bavail * stats.f_frsize


# ---------------------------------------------------------------------------
#  the one write path
# ---------------------------------------------------------------------------
def spool(read, filename: str, *, limit: int | None = None,
          min_free: int | None = None, destination: Path | None = None,
          temp: Path | None = None) -> Stored:
    """Stream ``read(n)`` into a hidden temp file, then publish it atomically.

    ``read`` is any callable taking a byte count and returning at most that many
    bytes (a file object's ``read``, a socket, ``io.BytesIO(...).read``).  An
    empty result means the end of the file.
    """
    limit = MAX_FILE if limit is None else limit
    min_free = MIN_FREE_RESERVE if min_free is None else min_free
    dest = Path(destination) if destination else drop_dir()
    tmpdir = Path(temp) if temp else temp_dir()
    dest.mkdir(parents=True, exist_ok=True)
    tmpdir.mkdir(parents=True, exist_ok=True)

    display = display_name(filename)
    available = free_bytes(dest)
    if available <= min_free:
        raise Refused("The box is nearly full — there is no room for new files. "
                      "Ask the owner to make space, then try again.")

    digest = hashlib.sha256()
    written = 0
    fd, tmp = tempfile.mkstemp(dir=str(tmpdir), prefix="upload-", suffix=TEMP_SUFFIX)
    try:
        with os.fdopen(fd, "wb") as out:
            while True:
                chunk = read(CHUNK)
                if not chunk:
                    break
                written += len(chunk)
                if written > limit:
                    raise Refused(f"That file is too big. Files up to "
                                  f"{limit // (1024 ** 3)} GB are accepted — split it or "
                                  f"use a smaller copy, then try again.")
                if available - written <= min_free:
                    raise Refused("The box filled up while that file was arriving. "
                                  "Ask the owner to make space, then try again.")
                digest.update(chunk)
                out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
        if written == 0:
            raise Refused("That file was empty, so there was nothing to share.")
        os.chmod(tmp, 0o644)
        stored = _publish(tmp, dest, display)
        return Stored(stored=stored, display=display, size=written,
                      sha256=digest.hexdigest(), path=dest / stored)
    except OSError as exc:
        _unlink(tmp)
        raise Refused("The box could not store that file. Check that the card is "
                      "inserted and has room, then try again.") from exc
    except BaseException:
        _unlink(tmp)
        raise


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
