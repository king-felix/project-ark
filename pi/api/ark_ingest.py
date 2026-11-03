"""Project ARK — the owner's own door for files: a book, a game, or twenty of them.

The owner's page at ``/admin/upload`` takes **several files at once** and puts
each one where it already belongs.  Nothing here is a second ingest path:

* **a book** is written into ``/srv/ark/calibre-incoming`` — the folder the
  ``ark-book-import.path`` unit already watches, which runs
  ``ark-import-books`` and puts the book in the Book Library.  Dropping the file
  there *is* the normal behaviour, so the page starts nothing, needs no root and
  cannot disagree with the importer;
* **a game** goes through ``ark_arcade_install.spool_upload()`` and
  ``import_owner_file()`` — the same shape proof, the same fingerprint check and
  the same single writer of ``arcade/games`` that the one-at-a-time door and
  ``ark-arcade --import`` use.

The rules that keep that safe, in one place:

* **This module is the only creator of a file in the book staging folder.**
  ``spool_book()`` writes ``.upload-<32 hex><ext>.part`` (a leading dot *and* a
  ``.part`` suffix, both of which ``ark-import-books`` and the repair pass
  ignore), flushes it to the card, and only then hard-links it to a readable
  name.  A half-written book is therefore never a book, even if the watcher
  fires while the bytes are still arriving.
* **The name a person sees is kept, the name on disk is ours.**  The published
  name is ``<readable stem>-<8 hex><ext>``, so the Book Library shows a title a
  person recognises while two people (or one person twice) can never overwrite
  each other — the link refuses an existing name instead of replacing it.
* **The caps are the box's real ones.**  A game is capped at the arcade's own
  ``MAX_DOWNLOAD``; a batch is capped at ``MAX_BATCH`` and the free-space reserve
  below which the box refuses to write is ``ark_storage``'s, not a second number.
* **A book is only "in the library" when the importer's own record says so.**
  ``wait_for_library()`` reads ``calibre-incoming/.imported.json`` — the ledger
  ``ark-import-books`` writes *after* Calibre has accepted the file — so the page
  can say "in the library" and mean it, and says "the box still has it" when it
  does not know.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Set

import ark_arcade
import ark_arcade_install
import ark_media
import ark_space
import ark_storage

#: A refusal a person can read and act on — the same class the portal's uploader
#: raises, so the page has one kind of sentence to show.
Refused = ark_storage.Refused

KIND_BOOK = "book"
KIND_GAME = "game"
#: A film is not stored here — it goes to the Media shelf — but this page is the
#: owner's one door for "several files at once", so it must recognise one.
KIND_MEDIA = "video"

#: The book kinds ``ark-import-books`` accepts.  This is a copy on purpose (the
#: page must not import the importer), and ``tests/test_admin_upload.py`` reads
#: the shipped script and fails if the two ever disagree.
BOOK_EXT = frozenset({
    ".epub", ".pdf", ".mobi", ".azw", ".azw3", ".fb2", ".djvu",
    ".cbz", ".cbr", ".cb7", ".txt", ".rtf", ".doc", ".docx", ".odt",
    ".html", ".htm", ".lit", ".pdb", ".prc", ".ibooks", ".azw4", ".kepub",
})

#: The game kinds this box can play — taken from the arcade itself, so a console
#: added there is offered here without anybody editing a second list.
GAME_EXT = frozenset(ext for info in ark_arcade.SYSTEMS.values() for ext in info["ext"])

#: The video kinds the shelf accepts — taken from the shelf itself.
MEDIA_EXT = frozenset(ark_media.VIDEO_EXT)
SUB_EXT = frozenset(ark_media.SUB_EXT)

#: How many files one press may carry, and how large the whole press may be.
#: Books and games share the batch cap; a film has its own, larger cap and is
#: bounded by the card's reserve instead of by this number.
MAX_FILES = 20
MAX_BOOK = 512 * 1024 * 1024            # a book, not a video
MAX_GAME = ark_arcade_install.MAX_DOWNLOAD
MAX_BATCH = 512 * 1024 * 1024
MAX_MEDIA = ark_media.MAX_MEDIA
#: The reserve is ark_space's, asked at the moment a book is written — there is
#: no second number here, on purpose (see ark_space).
CHUNK = 1024 * 1024

#: How long the page waits for the book importer before it says "still working".
#: The watcher fires within a second of the file landing; a big PDF takes longer
#: than this, and a page that guessed would be worse than one that admits it.
IMPORT_WAIT = 40.0
POLL_SECONDS = 0.5

TEMP_PREFIX = ".upload-"
TEMP_SUFFIX = ".part"


@dataclass(frozen=True)
class Book:
    """A book waiting in the importer's own folder, and what it really is."""

    path: Path          # where it is on the card
    name: str           # the name on the card (readable, and ours)
    display: str        # what the person called it
    size: int
    sha256: str



# ---------------------------------------------------------------------------
#  where books wait
# ---------------------------------------------------------------------------
def incoming_dir() -> Path:
    """The folder ``ark-book-import.path`` watches.  Read at call time."""
    value = os.environ.get("ARK_INCOMING") or ""
    if value:
        return Path(value)
    return ark_storage.ark_root() / "calibre-incoming"


def ledger_path() -> Path:
    """The importer's own record of what really reached the library."""
    return incoming_dir() / ".imported.json"


def read_ledger() -> dict:
    """The ledger as a dictionary; a missing or damaged one is simply empty.

    Empty means "nothing is known to be in the library", which is the safe way to
    be wrong: the book is offered again rather than silently dropped.
    """
    try:
        data = json.loads(ledger_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def imported(digest: str) -> bool:
    return bool(digest) and str(digest).lower() in read_ledger()


def wait_for_library(digests: Iterable[str],
                     seconds: Optional[float] = None) -> Set[str]:
    """Which of these fingerprints the importer has recorded, within ``seconds``.

    The answer comes from the importer's own ledger, never from "the file is not
    in the folder any more" — a file the importer could not add is left where it
    is, and one it *did* add is recorded.  Asking the ledger is the only way to
    tell those apart.
    """
    wanted = {str(d).lower() for d in digests if d}
    if not wanted:
        return set()
    deadline = time.monotonic() + (IMPORT_WAIT if seconds is None else float(seconds))
    found: Set[str] = set()
    while True:
        found = wanted & {str(k).lower() for k in read_ledger()}
        if found == wanted or time.monotonic() >= deadline:
            return found
        time.sleep(POLL_SECONDS)


# ---------------------------------------------------------------------------
#  what a file is
# ---------------------------------------------------------------------------
def classify(filename: str) -> str:
    """``"book"``, ``"game"`` or ``""`` — decided by the extension alone.

    An extension is a claim, not a fact: a game is still proved by the arcade's
    own shape checks after this, and a book is still read by Calibre afterwards.
    Being wrong here can only produce a refusal, never a bad file on the shelf.
    """
    ext = Path(str(filename or "")).suffix.lower()
    if ext in BOOK_EXT:
        return KIND_BOOK
    if ext in MEDIA_EXT:
        return KIND_MEDIA
    if ext in GAME_EXT:
        return KIND_GAME
    return ""


def kinds() -> dict:
    """The two lists, in plain words, for the page."""
    return {KIND_BOOK: sorted(BOOK_EXT), KIND_GAME: sorted(GAME_EXT),
            KIND_MEDIA: sorted(MEDIA_EXT)}


def max_for(kind: str) -> int:
    """The per-file cap for one kind, so the page and the check cannot disagree."""
    if kind == KIND_BOOK:
        return MAX_BOOK
    if kind == KIND_MEDIA:
        return MAX_MEDIA
    return MAX_GAME


def _size_words(n: int) -> str:
    """A size a person reads, for a refusal.  Never ``0 MB``."""
    n = int(n or 0)
    for unit, size in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("kB", 1024)):
        if n >= size:
            return f"{n / size:.0f} {unit}"
    return f"{n} bytes"





# ---------------------------------------------------------------------------
#  the one writer of the book folder
# ---------------------------------------------------------------------------
def _unique_name(folder: Path, stem: str, ext: str) -> Path:
    """A name nobody else has, made readable and never replacing a file."""
    stem = stem[:80].strip(" .") or "book"
    for _attempt in range(8):
        candidate = folder / f"{stem}-{secrets.token_hex(4)}{ext}"
        if not candidate.exists():
            return candidate
    raise Refused("The box could not find a free name for that book. Try once more.")


def spool_book(read, filename: str, *, limit: Optional[int] = None) -> Book:
    """Stream a book into the importer's folder and publish it atomically.

    ``read`` is any callable taking a byte count and returning at most that many
    bytes (a file object's ``read``).  The bytes land in a hidden ``.upload-…part``
    file, are flushed to the card, and are only then linked to a real name — so
    the watcher may fire as often as it likes and the importer can never read half
    a book.
    """
    limit = MAX_BOOK if limit is None else int(limit)
    if limit <= 0:
        raise Refused("This press is as full as one upload may be — send the rest "
                      "in a second go.")
    display = ark_storage.display_name(filename)
    ext = Path(display).suffix.lower()
    if ext not in BOOK_EXT:
        raise Refused(f"A {ext or 'nameless'} file is not a kind of book this box "
                      "knows how to add.")
    folder = incoming_dir()
    folder.mkdir(parents=True, exist_ok=True)
    available = ark_storage.free_bytes(folder)
    room = ark_space.reserve_bytes(folder)
    if available <= room:
        raise Refused("The card is nearly full — there is no room for another book. "
                      "Make some space, then try again.")

    digest = hashlib.sha256()
    written = 0
    temp = folder / (TEMP_PREFIX + secrets.token_hex(16) + ext + TEMP_SUFFIX)
    # O_EXCL: the name is unguessable, and this refuses a file that is already there.
    fd = os.open(str(temp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as out:
            while True:
                chunk = read(CHUNK)
                if not chunk:
                    break
                written += len(chunk)
                if written > limit:
                    raise Refused("That file is larger than this page will take "
                                  f"({_size_words(limit)}).")
                if available - written <= room:
                    raise Refused("The card filled up while that book was arriving.")
                digest.update(chunk)
                out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
        if written == 0:
            raise Refused("That file was empty, so there was nothing to add.")
        os.chmod(temp, 0o644)
        target = _unique_name(folder, Path(display).stem, ext)
        os.link(temp, target)          # refuses to replace: that is the point
        os.unlink(temp)
        return Book(path=target, name=target.name, display=display,
                    size=written, sha256=digest.hexdigest())
    except OSError as exc:
        _unlink(temp)
        raise Refused("The box could not store that book. Check that the card is "
                      "inserted and has room, then try again.") from exc
    except BaseException:
        _unlink(temp)
        raise


def discard(path: Optional[Path]) -> bool:
    """Throw away a copy this module wrote.  A file already gone is not an error."""
    if path is None:
        return False
    try:
        Path(path).unlink()
        return True
    except OSError:
        return False


def _unlink(path) -> None:
    try:
        os.unlink(str(path))
    except OSError:
        pass          # already gone, or never created
