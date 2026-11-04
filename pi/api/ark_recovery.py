"""Project ARK — a recovery copy that agrees with the files its records name.

The daily pass keeps a copy of the records so a damaged database can be put back.
That copy used to be the database file and nothing else, and a save is not one
file: a player's save is a row *and* a content-addressed blob beside it.
``ark_arcade.player_save_put()`` writes the new blob, moves the row, and only then
removes the blob it replaced.  A database-only copy taken before that replacement
therefore names a blob the next save deletes — a recovery that restores a row
whose bytes are gone.

This module owns the missing half: a **recovery generation**.

What a generation is
--------------------
A directory under ``<data>/recovery/generations/`` holding:

* ``ark.db`` — SQLite's own ``backup()`` of the records, in one self-contained
  file (no ``-wal`` beside it), checked with ``quick_check``;
* ``manifest.json`` — the fingerprint of that database **and** every external
  file its rows name: game saves, shared files, and the shelf's pictures and
  playing copies.  Each entry carries its path on the box, its size, its sha256
  where that is affordable, whether it is **pinned**, and whether the file was
  present when the copy was taken.

The database is the only thing a generation **copies**.  Save blobs, shared
files, films and their derivatives are **pinned in place**: while a generation is
retained, the deletion paths ask this module first and leave a referenced blob
alone.  That is the difference between a coherent recovery copy and either a lie
or a second copy of a 97 GiB library.  The bytes a person cannot re-fetch — a
save — are small (``MAX_SAVE`` is 16 MiB) and are always pinned.  Files larger
than ``PIN_LIMIT``, and anything the film shelf owns, are recorded as **bulk**:
present and sized at capture, but never duplicated and never allowed to hold a
deletion hostage.  A film deliberately deleted after a copy is reported as
missing by ``validate()``; it is not silently pretended to be recoverable.

The three rules that make it safe
---------------------------------
1. **Capture and deletion take turns.**  ``capture`` holds ``state.lock``
   exclusively for the whole of "copy the database, read its references, write
   the manifest, publish".  ``player_save_put``, ``player_save_delete`` and
   ``gc`` take it without waiting while they decide to remove something (so a
   save never waits for a copy).  A blob cannot disappear between the database
   copy naming it and the manifest protecting it: the deleter either ran before
   the copy, or is held outside it and then sees the pin.  The lock is a
   process-wide gate **and** a ``flock``: on macOS a second ``flock`` from
   another thread of the same process is not reliably refused, and the portal is
   one process with many request threads.  The ``flock`` is what a backup or a
   restore process sees.
2. **A generation is published once, after everything it needs is durable.**
   The database is copied and checked, the references are checked, the manifest
   is fsynced and renamed, the directory is fsynced, and only then is the
   ``.partial`` name renamed to a real generation.  A cut leaves an unfinished
   ``.`` directory that ``gc`` removes; it can never be mistaken for a copy.
3. **Retention is bounded, and the last complete copy is never spent on a
   replacement that is not verified.**  ``KEEP`` generations are kept.  A new
   generation is published before any old one is removed; pruning never takes
   the only generation whose database and pinned files were all present, and a
   capture that cannot fit is refused rather than made to fit by deleting one.

``.good.bak`` is still written, as a **hard link** to the newest generation's
database.  It is what an older repair or a person's habit expects to find, and it
costs no second copy of the bytes.  The manifest is the authority; a bare
``.good.bak`` from an older build is adopted as a legacy generation (and its
missing files are reported) the first time this module runs.
"""

from __future__ import annotations

import calendar
import hashlib
import json
import os
import shutil
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

try:  # the one storage policy; absent on a half-installed tree, never fatal
    import ark_space
except Exception:                            # noqa: BLE001 — see below
    ark_space = None  # type: ignore[assignment]

try:
    import ark_atomic
except Exception:                            # noqa: BLE001 — a checkout always has it
    ark_atomic = None  # type: ignore[assignment]

try:
    import fcntl
except ImportError:                          # pragma: no cover - both boxes have it
    fcntl = None  # type: ignore[assignment]

FORMAT = 1
KIND = "recovery"
GEN_PREFIX = "gen-"
LEGACY_PREFIX = "gen-legacy-"
RETIRED_PREFIX = ".retired-"
PARTIAL_PREFIX = "."
MANIFEST = "manifest.json"
DATABASE = "ark.db"
LOCK = "state.lock"

#: How many published generations are kept.  Two daily copies is enough to go
#: back past one bad save, and small enough that the database copies are noise
#: beside the reserve the rest of the box keeps.
KEEP = 2

#: A file larger than this is recorded by size, never pinned and never hashed.
#: It is bulk content: a film, a 2 GiB upload, an archive.  Saves are far under
#: it; the File Box's ordinary documents are too.
PIN_LIMIT = 64 * 1024 * 1024
HASH_LIMIT = 64 * 1024 * 1024

#: An unreferenced blob younger than this may belong to a save that is being
#: written right now.  Six hours is the same grace ``ark_arcade`` uses.
STALE_SECONDS = 6 * 3600.0

#: A retired generation keeps its evidence this long, then its database copy is
#: removed.  It no longer pins anything the moment it is retired.
RETIRED_SECONDS = 24 * 3600.0

#: Room the manifest and directory entries need beyond the database itself.
SPACE_MARGIN = 1024 * 1024

#: How long a capture waits for another capture (or a backup) to finish.  A save
#: delete holds the shared side for milliseconds; a full backup can hold the
#: exclusive side for minutes, and waiting that long at boot is not useful.
CAPTURE_WAIT = 30.0
GC_WAIT = 5.0
DISCARD_WAIT = 5.0

#: What a generation deliberately does not carry.  It is written into every
#: manifest so a person reading one does not have to guess why the card is not
#: twice its own size.
EXCLUDED = [
    "the offline library, the book shelf and the map packs — content that is imported again",
    "films' playing copies and pictures — recorded by name and size, not copied; a film deleted after this copy is reported missing",
    "the book import list — an import cache, not a record; a bundle carries it separately",
    "Wi-Fi and other secrets in /etc/ark — set up again by the rebuild guide",
]


class RecoveryRefused(Exception):
    """A recovery copy could not be made, with a sentence a person can read."""


@dataclass
class Capture:
    """What one attempt to keep a recovery copy did."""

    status: str                       # captured | unchanged | refused | adopted | deferred
    generation: str = ""
    path: Optional[Path] = None
    manifest: Dict[str, Any] = field(default_factory=dict)
    said: str = ""
    missing: List[Dict[str, Any]] = field(default_factory=list)
    removed: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status in ("captured", "adopted")


@dataclass
class RestoreSource:
    """The copy a repair should put back, and what it knows is missing."""

    path: Path
    generation: str = ""
    legacy: bool = False
    complete: bool = True
    missing: List[Dict[str, Any]] = field(default_factory=list)
    mismatched: List[Dict[str, Any]] = field(default_factory=list)
    said: str = ""


# ---------------------------------------------------------------------------
#  where things live
# ---------------------------------------------------------------------------
def _root(db_path: Any) -> Path:
    """The box's root, from the database's own place or ``ARK_ROOT``.

    The layout is fixed: ``<root>/api/data/ark.db``.  A test or a reader may keep
    a database elsewhere; then ``ARK_ROOT`` is the honest answer, and the
    database's own folder is the last fallback.
    """
    path = Path(db_path).absolute()
    if path.parent.name == "data" and path.parent.parent.name == "api":
        return path.parent.parent.parent
    told = os.environ.get("ARK_ROOT") or ""
    return Path(told) if told else path.parent


def recovery_dir(db_path: Any) -> Path:
    return Path(db_path).absolute().parent / "recovery"


def generations_dir(db_path: Any) -> Path:
    return recovery_dir(db_path) / "generations"


def lock_path(db_path: Any) -> Path:
    return recovery_dir(db_path) / LOCK


def _ensure(path: Path, mode: int = 0o700) -> None:
    """Make a folder, and give it the owner the box's pages run as.

    The boot pass runs as root and the daily pass as ``pi``.  A root-created
    ``0700 root`` folder would lock the daily pass out of its own recovery
    copies, so a folder made by root takes the owner of the folder it lives in
    (``api/data`` is ``pi``'s).  A folder made by ``pi`` is already right.
    """
    path.mkdir(parents=True, exist_ok=True)
    try:
        if os.geteuid() == 0:
            parent = path.parent.stat()
            os.chown(path, parent.st_uid, parent.st_gid)
    except OSError:
        pass
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def _adopt_tree(tree: Path) -> None:
    """Hand a root-made generation to the owner of its parent, like a bundle."""
    if os.geteuid() != 0:
        return
    try:
        owner = tree.parent.stat()
    except OSError:
        return
    for base, _dirs, names in os.walk(tree):
        try:
            os.chown(base, owner.st_uid, owner.st_gid)
        except OSError:
            pass
        for name in names:
            try:
                os.chown(os.path.join(base, name), owner.st_uid, owner.st_gid)
            except OSError:
                pass


# ---------------------------------------------------------------------------
#  the one lock: capture writes, deletion reads
# ---------------------------------------------------------------------------
def _open_lock(db_path: Any) -> int:
    """Open (and create) the lock file.  ``-1`` when this card refuses one."""
    path = lock_path(db_path)
    try:
        _ensure(path.parent)
    except OSError:
        return -1
    try:
        fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o666)
    except OSError:
        return -1
    try:
        os.fchmod(fd, 0o666)
    except OSError:
        pass
    return fd


# ``flock`` is not enough inside one process on every supported platform: on
# macOS a second lock request from a different thread of the same process is not
# reliably refused, so a save deletion in one request thread could run straight
# through a capture in another.  The gate below is the in-process half; the
# ``flock`` is the half that a backup or a restore process sees.
_THREAD_GATES: Dict[str, threading.Lock] = {}
_THREAD_GATES_LOCK = threading.Lock()


def _thread_gate(db_path: Any) -> threading.Lock:
    key = str(lock_path(db_path).absolute())
    with _THREAD_GATES_LOCK:
        gate = _THREAD_GATES.get(key)
        if gate is None:
            gate = threading.Lock()
            _THREAD_GATES[key] = gate
        return gate


@contextmanager
def _holding(db_path: Any, *, exclusive: bool, wait: float) -> Iterator[bool]:
    """Take the state lock, or answer that it could not be had in time."""
    gate = _thread_gate(db_path)
    if wait > 0:
        held_here = gate.acquire(timeout=float(wait))
    else:
        held_here = gate.acquire(blocking=False)
    if not held_here:
        yield False
        return
    fd = -1
    acquired = False
    try:
        if fcntl is None:                    # pragma: no cover - supported boxes
            yield True
            return
        fd = _open_lock(db_path)
        if fd < 0:
            # A read-only card cannot hold a lock.  Refusing every save would be
            # the worse failure; the caller decides what an unheld lock means.
            yield False
            return
        mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        deadline = time.monotonic() + max(0.0, float(wait))
        while True:
            try:
                fcntl.flock(fd, mode | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.05)
        # Only the flock call is guarded above: an exception from the caller's
        # block must travel through this yield, not be mistaken for a lock that
        # was refused and then be swallowed by another loop.
        yield acquired
    finally:
        if acquired:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        gate.release()


def capture_lock(db_path: Any, *, wait: float = CAPTURE_WAIT) -> Any:
    """The exclusive side, for a whole backup or a restore that rewrites files.

    A caller that holds this may copy a save and its database and be sure no save
    deletion runs between the two.  The lock is deliberately *not* a SQLite lock:
    it covers the files beside the database, which SQLite never sees.
    """
    return _holding(db_path, exclusive=True, wait=wait)


def deletion_guard(db_path: Any) -> Any:
    """The shared side, taken without waiting: a save must never block on a copy."""
    return _holding(db_path, exclusive=False, wait=0.0)


# ---------------------------------------------------------------------------
#  small files
# ---------------------------------------------------------------------------
def fsync_dir(folder: Any) -> None:
    if ark_atomic is not None:
        ark_atomic.fsync_dir(folder)
        return
    try:                                     # pragma: no cover - checkout always has it
        fd = os.open(str(folder), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _plain(name: Any) -> bool:
    """A file name that cannot leave the folder it is joined to."""
    text = str(name or "")
    return bool(text) and text == Path(text).name and "/" not in text and "\\" not in text


def _iso(when: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(when))


def _when(manifest: Dict[str, Any], fallback: Path) -> float:
    raw = str(manifest.get("created") or "")
    try:
        return float(calendar.timegm(time.strptime(raw, "%Y-%m-%dT%H:%M:%SZ")))
    except (ValueError, OverflowError):
        try:
            return fallback.stat().st_mtime
        except OSError:
            return 0.0


def _load(generation: Path) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads((generation / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("format") != FORMAT \
            or data.get("kind") != KIND:
        return None
    return data


def published(db_path: Any) -> List[Path]:
    """Every complete-looking generation, newest first.

    Only a real ``gen-*`` directory with a readable manifest is a generation.  A
    ``.gen-*.partial`` is a capture that did not finish; a ``.retired-*`` has
    been deliberately taken out of service and pins nothing.
    """
    folder = generations_dir(db_path)
    try:
        entries = [p for p in folder.iterdir()
                   if p.is_dir() and p.name.startswith(GEN_PREFIX)
                   and not p.name.startswith(".")]
    except OSError:
        return []
    out = []
    for path in entries:
        data = _load(path)
        if data is not None:
            out.append((path, data))
    out.sort(key=lambda item: (_when(item[1], item[0]), item[0].name), reverse=True)
    return [path for path, _data in out]


def newest(db_path: Any) -> Optional[Path]:
    found = published(db_path)
    return found[0] if found else None


# ---------------------------------------------------------------------------
#  what the records name
# ---------------------------------------------------------------------------
def _table(conn: sqlite3.Connection, name: str) -> bool:
    try:
        conn.execute("SELECT 1 FROM %s LIMIT 1" % name).fetchone()
        return True
    except sqlite3.Error:
        return False


def _column(row: sqlite3.Row, name: str, default: Any = "") -> Any:
    try:
        return row[name]
    except (IndexError, KeyError):
        return default


def references(conn: sqlite3.Connection) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Every external file the records name, and the records that cannot be used.

    The answer is deliberately a *description*, not a check: the file may be
    there or not, and checking belongs to the caller so a copy and a validation
    can use the same list.  Saves are pinned; a shared file is pinned unless it is
    a film's carrier or larger than ``PIN_LIMIT``; a film's derivatives are bulk.

    A record that names a path which is not one plain name (``../x``, ``a/b``) is
    reported and skipped.  It is not followed: a recovery manifest is not a way
    to make the box read outside its own folders.
    """
    refs: Dict[str, Dict[str, Any]] = {}
    problems: List[str] = []

    def add(to: str, kind: str, *, pin: bool, bulk: bool,
            expected_size: Optional[int] = None, expected_sha: str = "",
            label: str = "") -> None:
        previous = refs.get(to)
        if previous is not None:
            # The same bytes named twice (a shared payload and a film carrier):
            # keep the strictest treatment, never a weaker one.
            previous["pin"] = bool(previous["pin"] or pin)
            previous["bulk"] = bool(previous["bulk"] and bulk)
            if expected_sha:
                previous["expected_sha256"] = expected_sha
            if expected_size is not None and not previous.get("expected_size"):
                previous["expected_size"] = int(expected_size)
            return
        refs[to] = {
            "to": to, "kind": kind, "pin": bool(pin), "bulk": bool(bulk),
            "expected_size": int(expected_size) if expected_size is not None else None,
            "expected_sha256": str(expected_sha or ""), "label": label,
        }

    try:
        conn.row_factory = sqlite3.Row
    except sqlite3.Error:                    # pragma: no cover - already set
        pass

    if _table(conn, "player_saves"):
        for row in conn.execute("SELECT * FROM player_saves"):
            blob = str(_column(row, "blob", "") or "")
            filename = str(_column(row, "filename", "") or "")
            name = blob or filename
            try:
                pid = int(_column(row, "player_id", 0) or 0)
                gid = int(_column(row, "game_id", 0) or 0)
            except (TypeError, ValueError):
                problems.append("a save record names no player or game")
                continue
            if not _plain(name) or pid <= 0 or gid <= 0:
                problems.append("a save record names a file outside its own folder")
                continue
            where = "blobs/" + name if blob else name
            add("arcade/saves/%d/%d/%s" % (pid, gid, where), "save", pin=True,
                bulk=False, expected_size=_column(row, "size", None),
                expected_sha=str(_column(row, "sha256", "") or ""),
                label="save %d/%d" % (pid, gid))

    films = set()
    if _table(conn, "media"):
        for row in conn.execute("SELECT * FROM media"):
            stored = str(_column(row, "stored", "") or "")
            if _plain(stored):
                films.add(stored)
            play = str(_column(row, "play_stored", "") or "")
            if _plain(play):
                add("media/play/" + play, "film", pin=False, bulk=True,
                    expected_size=_column(row, "play_size", None),
                    label="film copy")
            poster = str(_column(row, "poster", "") or "")
            if _plain(poster):
                add("media/thumbs/" + poster, "film", pin=False, bulk=True,
                    label="film picture")
            elif poster:
                problems.append("a film record names a picture outside its own folder")
    if _table(conn, "media_subs"):
        for row in conn.execute("SELECT * FROM media_subs"):
            stored = str(_column(row, "stored", "") or "")
            if _plain(stored):
                add("media/subs/" + stored, "film", pin=False, bulk=True,
                    expected_size=_column(row, "size", None), label="film captions")
            elif stored:
                problems.append("a film record names captions outside their own folder")

    if _table(conn, "files"):
        for row in conn.execute("SELECT * FROM files"):
            stored = str(_column(row, "stored", "") or "")
            if not stored:
                stored = str(_column(row, "name", "") or "")
            if not _plain(stored):
                problems.append("a shared-file record names a path outside the File Box")
                continue
            # ``hidden`` is also the owner's Hide action on an ordinary document;
            # it is not evidence that the bytes are a film.  The media set and the
            # file's own size are.
            bulk = (stored in films
                    or int(_column(row, "size", 0) or 0) > PIN_LIMIT)
            add("drop/files/" + stored, "film" if stored in films else "shared",
                pin=not bulk, bulk=bulk, expected_size=_column(row, "size", None),
                label="shared file")

    return list(refs.values()), problems


def references_from_database(db_path: Any, *,
                             readonly: bool = True) -> Tuple[List[Dict[str, Any]], List[str]]:
    """The list above, read from a finished database file (a bundle's copy too).

    A generation is a self-contained file and is read read-only.  An older
    ``.good.bak`` may be a WAL file whose journal is gone; the caller that owns
    that legacy copy asks for a read-write open, exactly as the repair pass has
    always done, so SQLite can answer rather than refusing the file.
    """
    try:
        if readonly:
            conn = sqlite3.connect("file:%s?mode=ro" % Path(db_path), uri=True, timeout=10)
        else:
            conn = sqlite3.connect(str(db_path), timeout=10)
    except sqlite3.Error as exc:
        raise RecoveryRefused("the records could not be read (%s)." % exc) from exc
    try:
        return references(conn)
    finally:
        conn.close()


def _abs(root: Path, rel: str) -> Optional[Path]:
    """``root/rel`` when the answer really is inside ``root``; never a traversal."""
    if not isinstance(rel, str) or not rel or rel.startswith("/"):
        return None
    parts = [part for part in rel.replace("\\", "/").split("/") if part not in ("", ".")]
    if not parts or any(part == ".." for part in parts):
        return None
    target = root.joinpath(*parts)
    try:
        resolved = target.resolve()
        base = root.resolve()
    except OSError:
        return None
    if resolved == base or base not in resolved.parents:
        return None
    return target


def _check(root: Path, ref: Dict[str, Any]) -> Dict[str, Any]:
    """Read one reference and record what is really there.

    A save is always hashed: it is small, and its row already carries the digest
    it must match.  A shared file is hashed up to ``HASH_LIMIT`` and sized beyond
    it.  A film's playing copy is sized only — re-reading a 20 GiB derivative
    every day would turn a recovery copy into a second full-time job.
    """
    checked = dict(ref)
    path = _abs(root, checked["to"])
    checked["present"] = False
    checked["size"] = None
    checked["sha256"] = ""
    checked["checked"] = "missing"
    checked["mismatch"] = ""
    if path is None:
        checked["mismatch"] = "path"
        return checked
    try:
        stat = path.stat()
    except OSError:
        return checked
    if not path.is_file():
        return checked
    checked["present"] = True
    checked["size"] = int(stat.st_size)
    # A record can carry a wrong or zero size (a row from before sizes were
    # written).  The file on the card is the fact: a shared file over the pin
    # limit is bulk, never pinned, whatever its row claimed.
    if checked.get("kind") == "shared" and checked["size"] > PIN_LIMIT:
        checked["bulk"] = True
        checked["pin"] = False
    expected_size = checked.get("expected_size")
    if expected_size is not None and int(expected_size) > 0 \
            and int(expected_size) != checked["size"]:
        checked["mismatch"] = "size"
    if checked.get("pin") or int(stat.st_size) <= HASH_LIMIT:
        try:
            checked["sha256"] = _sha256(path)
            checked["checked"] = "hash"
        except OSError:
            checked["checked"] = "unreadable"
            checked["mismatch"] = checked["mismatch"] or "unreadable"
            return checked
        expected_sha = str(checked.get("expected_sha256") or "")
        if expected_sha and expected_sha != checked["sha256"]:
            checked["mismatch"] = checked["mismatch"] or "sha256"
    else:
        checked["checked"] = "size"
    return checked


# ---------------------------------------------------------------------------
#  taking one
# ---------------------------------------------------------------------------
def _copy_database(source: Path, target: Path) -> None:
    """SQLite's own copy, one file, fsynced — never ``cp`` on a live WAL."""
    src = sqlite3.connect(str(source), timeout=10.0)
    try:
        dst = sqlite3.connect(str(target), timeout=10.0)
        try:
            src.backup(dst)
            mode = dst.execute("PRAGMA journal_mode=DELETE").fetchone()
            dst.commit()
            if not mode or str(mode[0]).lower() != "delete":
                raise RecoveryRefused(
                    "the database copy could not be made self-contained (%s)."
                    % (mode[0] if mode else "no answer"))
        finally:
            dst.close()
    finally:
        src.close()
    # Switching out of WAL can leave empty ``-shm``/``-wal`` names behind.  A
    # generation is one file; a stray sidecar is not a second copy, but it is
    # noise a person would have to explain.  In DELETE mode there is no journal
    # to hold commits, so these are empty.
    for suffix in ("-wal", "-shm"):
        try:
            os.unlink(str(target) + suffix)
        except OSError:
            pass
    with open(target, "rb+") as handle:
        os.fsync(handle.fileno())


def _quick_check(path: Path, *, readonly: bool = True) -> Tuple[bool, str]:
    """Ask SQLite's own integrity question.

    A generation's database is one self-contained file in DELETE journal mode, so
    it is read read-only.  An older ``.good.bak`` may still be in WAL mode with
    its journal gone — the shape a plain ``cp`` leaves — and only a read-write
    open lets SQLite recover enough to answer.  That is the same question the
    repair pass has always asked of that file.
    """
    try:
        if readonly:
            conn = sqlite3.connect("file:%s?mode=ro" % path, uri=True, timeout=10.0)
        else:
            conn = sqlite3.connect(str(path), timeout=10.0)
    except sqlite3.Error as exc:
        return False, str(exc)
    try:
        row = conn.execute("PRAGMA quick_check").fetchone()
        answer = str(row[0]) if row else "no answer"
        return answer.lower() == "ok", answer
    except sqlite3.Error as exc:
        return False, str(exc)
    finally:
        conn.close()


def _schema_version(conn: sqlite3.Connection) -> Optional[int]:
    try:
        row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
        return int(row[0]) if row and row[0] is not None else None
    except (sqlite3.Error, TypeError, ValueError):
        return None


def _write_manifest(folder: Path, data: Dict[str, Any]) -> None:
    """The finishing line: written in full, fsynced, and renamed into place."""
    final = folder / MANIFEST
    tmp = folder / (MANIFEST + ".tmp")
    payload = (json.dumps(data, indent=2, sort_keys=True) + "\n").encode("utf-8")
    with open(tmp, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(tmp, 0o600)
    os.replace(tmp, final)
    fsync_dir(folder)


def _space_allows(db_path: Any, need: int) -> Tuple[bool, str]:
    """Ask the one storage policy; a missing policy is not a refusal."""
    if ark_space is None:
        return True, ""
    try:
        ark_space.require(Path(db_path).parent, int(need), owner="recovery",
                          label="a recovery copy", essential=True)
        return True, ""
    except ark_space.Refused as exc:
        return False, str(exc)
    except Exception:                        # noqa: BLE001 — the copy is not the place to fail
        return True, ""


def _prune(db_path: Any, keep: int) -> List[str]:
    """Remove older generations, never the only complete one.

    Called only after a new generation has been published and verified.  The
    order is what makes the rule true: there is always a verified copy on the
    card while an older one is being removed.
    """
    keep = max(1, int(keep))
    found = published(db_path)
    if len(found) <= keep:
        return []
    complete = {}
    for path in found:
        data = _load(path) or {}
        complete[path] = bool(data.get("complete"))
    remaining = list(found)
    removed: List[str] = []
    for path in found[keep:]:
        rest = [item for item in remaining if item != path]
        if complete.get(path) and not any(complete.get(item) for item in rest):
            continue                         # the last verified copy stays
        try:
            shutil.rmtree(path)
        except OSError:
            continue
        remaining.remove(path)
        removed.append(path.name)
    return removed


def _drop_sidecars(database: Path) -> None:
    """Remove journal names that belonged to a file this name no longer is.

    A ``-wal`` belongs to the database it was written for.  Replacing
    ``<db>.good.bak`` with a different inode and leaving the old ``-wal`` beside
    it is how SQLite replays old rows into a new file.  The new copy is switched
    to DELETE mode, so it has no journal of its own to keep.
    """
    for suffix in ("-wal", "-shm"):
        try:
            Path(str(database) + suffix).unlink()
        except OSError:
            pass


def _refresh_good_bak(db_path: Any, generation: Path) -> None:
    """Point ``<db>.good.bak`` at the newest generation by a hard link.

    A link is not a second copy: the bytes are the same inode.  It keeps the
    name older tools and a person's nervous habit expect, while the manifest
    remains the authority on what the copy needs.
    """
    good = Path(db_path).with_name(Path(db_path).name + ".good.bak")
    source = generation / DATABASE
    try:
        tmp = good.with_name(good.name + ".tmp")
        try:
            tmp.unlink()
        except OSError:
            pass
        os.link(source, tmp)
        os.replace(tmp, good)
        _drop_sidecars(good)
        fsync_dir(good.parent)
    except OSError:
        # A filesystem that will not hard link is not a reason to refuse the
        # generation; copy the small file instead.
        try:
            shutil.copyfile(source, good)
            fsync_dir(good.parent)
        except OSError:
            pass


def mark_good(db_path: Any, source: Any) -> bool:
    """Replace ``<db>.good.bak`` with the bytes a restore just put back.

    ``source`` is the candidate that was installed (a generation's database, a
    legacy copy, or a bundle's copy) — **not** the live target.  Opening a WAL
    database read-write to make the copy can leave a journal beside it, and a
    restore that leaves a journal beside the new records is the very thing
    ``ark_db_replace`` exists to prevent.  The source is a finished file; reading
    it is safe.

    Used while the caller holds the state lock and the maintenance gate.  The old
    ``.good.bak`` describes the records the restore replaced; leaving it there
    would let the next pass adopt the *pre-restore* state as its only recovery
    copy.  A failure is not a restore failure: the records are in place, and the
    next capture will refresh the name.
    """
    target = Path(db_path)
    source = Path(source)
    good = target.with_name(target.name + ".good.bak")
    tmp = target.with_name(target.name + ".good.bak.tmp")
    try:
        try:
            tmp.unlink()
        except OSError:
            pass
        _copy_database(source, tmp)
        os.replace(tmp, good)
        _drop_sidecars(good)
        fsync_dir(good.parent)
        return True
    except (OSError, sqlite3.Error, RecoveryRefused):
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        return False


def _clean_partials(db_path: Any, *, now: float, grace: float) -> List[str]:
    """Remove captures that were interrupted before publication, once old."""
    folder = generations_dir(db_path)
    removed: List[str] = []
    try:
        entries = list(folder.iterdir())
    except OSError:
        return removed
    for entry in entries:
        if not entry.is_dir() or not entry.name.startswith(PARTIAL_PREFIX):
            continue
        if entry.name.startswith(RETIRED_PREFIX):
            # A retired generation has its own, longer life (RETIRED_SECONDS);
            # it is not an unfinished capture and must not be called one.
            continue
        try:
            if now - entry.stat().st_mtime < grace:
                continue
        except OSError:
            continue
        try:
            shutil.rmtree(entry)
            removed.append(entry.name)
        except OSError:
            continue
    return removed


def capture(db_path: Any, *, root: Optional[Path] = None,
            keep: int = KEEP, min_interval_hours: Optional[float] = None,
            now: Optional[float] = None, reason: str = "daily",
            wait: float = CAPTURE_WAIT) -> Capture:
    """Keep one coherent recovery generation, or say why not.

    ``min_interval_hours`` is the old "at most one a day" rule: a generation
    younger than that is not replaced.  ``reason`` is recorded for a person
    reading the manifest, not used to make a decision.
    """
    target = Path(db_path)
    root = Path(root) if root is not None else _root(target)
    when = time.time() if now is None else float(now)
    if not target.is_file():
        return Capture("refused", said="there are no records to copy yet.")

    # The cheap answers first: if today's copy is already here, nothing else
    # needs opening.  Asked again under the lock below, because two passes can
    # decide at the same moment.
    found = published(target)
    if min_interval_hours is not None and found:
        newest_data = _load(found[0]) or {}
        age = when - _when(newest_data, found[0])
        # An incomplete copy is not a reason to wait: the box may have changed
        # since (a file was deleted, a save replaced), and one day of no fresh
        # copy is exactly the window this module exists to close.
        if newest_data.get("complete") and age < float(min_interval_hours) * 3600.0:
            return Capture("unchanged", generation=found[0].name, path=found[0],
                           manifest=newest_data,
                           said="a recovery copy younger than a day is already here.")

    # Ask the storage policy *before* taking the state lock: the policy opens a
    # read connection, which takes the maintenance gate shared.  A restore holds
    # that gate exclusively and then wants this lock; taking them in the other
    # order here would make the two wait on each other.
    try:
        size = target.stat().st_size
    except OSError:
        size = 0
    allowed, refusal = _space_allows(target, size + SPACE_MARGIN)
    if not allowed:
        return Capture("refused", said=refusal)

    with _holding(target, exclusive=True, wait=wait) as held:
        if not held:
            return Capture(
                "deferred",
                said="another copy or a backup is using the records right now; "
                     "nothing was changed and the next pass will try again.")

        found = published(target)
        if min_interval_hours is not None and found:
            newest_data = _load(found[0]) or {}
            age = when - _when(newest_data, found[0])
            if newest_data.get("complete") and age < float(min_interval_hours) * 3600.0:
                return Capture("unchanged", generation=found[0].name, path=found[0],
                               manifest=newest_data,
                               said="a recovery copy younger than a day is already here.")

        _ensure(generations_dir(target))
        # A stale `.partial` is not a generation.  Clear an old one; leave a
        # fresh one alone, because it may be another capture that is still
        # writing (the lock says it is not, but clocks and copies are cheap).
        _clean_partials(target, now=when, grace=STALE_SECONDS)

        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(when))
        # A random tail, not a counter: two captures in the same second (a test,
        # or a person running the pass while the timer fires) must never try to
        # publish onto the same directory name.
        name = "%s%s-%s" % (GEN_PREFIX, stamp, os.urandom(4).hex())
        folder = generations_dir(target)
        stage = folder / ("." + name + ".partial")
        final = folder / name
        # A recovery copy is *essential*: it is admitted against real free space,
        # ignoring what other jobs have promised, because refusing to keep the
        # records is worse than competing for the last few bytes.  The promise is
        # still written down, so the copy is visible to everything else while it
        # is being made and released however this ends.
        receipt = ark_space.open_receipt(folder, owner="recovery",
                                         label="a recovery copy", kind="recovery",
                                         essential=True)
        receipt.need(size + SPACE_MARGIN)
        try:
            if stage.exists():
                shutil.rmtree(stage)
            stage.mkdir()
            os.chmod(stage, 0o700)
            _copy_database(target, stage / DATABASE)
            os.chmod(stage / DATABASE, 0o600)
            sound, answer = _quick_check(stage / DATABASE)
            if not sound:
                raise RecoveryRefused(
                    "the database copy cannot be read (%s); no recovery copy was kept."
                    % answer)

            conn = sqlite3.connect("file:%s?mode=ro" % (stage / DATABASE),
                                   uri=True, timeout=10.0)
            try:
                conn.row_factory = sqlite3.Row
                refs, problems = references(conn)
                schema = _schema_version(conn)
            finally:
                conn.close()

            checked = [_check(root, ref) for ref in refs]
            missing = [ref for ref in checked if not ref["present"]]
            mismatched = [ref for ref in checked if ref["mismatch"]]
            complete = not missing and not mismatched and not problems
            pinned = [ref for ref in checked if ref["pin"]]
            manifest: Dict[str, Any] = {
                "format": FORMAT,
                "kind": KIND,
                "generation": name,
                "created": _iso(when),
                "reason": str(reason),
                "database": {
                    "file": DATABASE,
                    "size": (stage / DATABASE).stat().st_size,
                    "sha256": _sha256(stage / DATABASE),
                    "schema_version": schema,
                },
                "references": checked,
                "missing": [{"to": ref["to"], "kind": ref["kind"], "pin": ref["pin"]}
                            for ref in missing],
                "mismatched": [{"to": ref["to"], "kind": ref["kind"],
                                "why": ref["mismatch"], "pin": ref["pin"]}
                               for ref in mismatched],
                "problems": problems,
                "complete": complete,
                "excluded": EXCLUDED,
                "counts": {
                    "references": len(checked),
                    "pinned": len(pinned),
                    "bulk": len([ref for ref in checked if ref["bulk"]]),
                    "pinned_bytes": sum(int(ref["size"] or 0) for ref in pinned),
                    "referenced_bytes": sum(int(ref["size"] or 0) for ref in checked
                                            if ref["present"]),
                },
                "policy": {"keep": int(keep), "pin_limit": PIN_LIMIT,
                           "hash_limit": HASH_LIMIT},
            }
            receipt.need((stage / DATABASE).stat().st_size
                         + sum(int(ref["size"] or 0) for ref in pinned)
                         + SPACE_MARGIN)
            _write_manifest(stage, manifest)
            fsync_dir(stage)
            _adopt_tree(stage)
            os.replace(stage, final)        # publish: one rename, no half copies
            fsync_dir(folder)
            _adopt_tree(final)
            _refresh_good_bak(target, final)
        except BaseException:
            receipt.release()
            try:
                if stage.exists():
                    shutil.rmtree(stage)
            except OSError:
                pass
            raise
        receipt.release()

        removed = _prune(target, keep)
        said = "a recovery copy was kept (%s)" % name
        if missing or mismatched:
            said += ("; it is incomplete: %d file%s named by the records %s not there"
                     % (len(missing) + len(mismatched),
                        "" if len(missing) + len(mismatched) == 1 else "s",
                        "is" if len(missing) + len(mismatched) == 1 else "are"))
        return Capture("captured", generation=name, path=final, manifest=manifest,
                       said=said, missing=missing, removed=removed)


# ---------------------------------------------------------------------------
#  the copy a repair should put back
# ---------------------------------------------------------------------------
def validate(db_path: Any, generation: Optional[Any] = None, *,
             root: Optional[Path] = None, rehash: bool = True) -> Dict[str, Any]:
    """Check one generation: its database *and* every file its records name.

    ``quick_check`` alone was the old standard, and it is not enough: a database
    can be perfectly readable and name a save that is gone.  This re-reads the
    digest of every pinned file (and every file small enough to hash) and the
    size of the rest.
    """
    target = Path(db_path)
    root = Path(root) if root is not None else _root(target)
    if generation is None:
        found = published(target)
        if not found:
            return {"ok": False, "name": "", "said": "there is no recovery copy yet.",
                    "missing": [], "mismatched": [], "complete": False,
                    "pinned_missing": []}
        path = found[0]
    else:
        path = Path(generation)
        if not path.is_absolute():
            path = generations_dir(target) / path
    data = _load(path)
    if data is None:
        return {"ok": False, "name": path.name,
                "said": "%s has no readable manifest, so it cannot be trusted." % path.name,
                "missing": [], "mismatched": [], "complete": False,
                "pinned_missing": []}
    db = path / DATABASE
    problems: List[str] = []
    db_ok = False
    try:
        if not db.is_file():
            problems.append("%s has no database file" % path.name)
        else:
            recorded = data.get("database") or {}
            if recorded.get("size") is not None and db.stat().st_size != int(recorded["size"]):
                problems.append("%s's database is not the size its manifest records" % path.name)
            elif recorded.get("sha256") and _sha256(db) != recorded["sha256"]:
                problems.append("%s's database does not match its fingerprint" % path.name)
            else:
                db_ok, answer = _quick_check(db)
                if not db_ok:
                    problems.append("%s's database cannot be read (%s)" % (path.name, answer))
    except OSError as exc:
        problems.append("%s's database could not be read (%s)" % (path.name, exc))

    missing: List[Dict[str, Any]] = []
    mismatched: List[Dict[str, Any]] = []
    for ref in data.get("references") or []:
        checked = _check(root, ref) if rehash else dict(ref)
        if not checked.get("present"):
            missing.append({"to": checked.get("to"), "kind": checked.get("kind"),
                            "pin": bool(checked.get("pin"))})
        elif checked.get("mismatch"):
            mismatched.append({"to": checked.get("to"), "kind": checked.get("kind"),
                               "why": checked.get("mismatch"),
                               "pin": bool(checked.get("pin"))})
    pinned_missing = [item for item in missing if item.get("pin")]
    recorded_problems = list(data.get("problems") or [])
    complete = db_ok and not missing and not mismatched and not recorded_problems
    if not db_ok:
        said = "; ".join(problems)
    elif missing or mismatched:
        said = ("%s is readable, but %d file%s its records name %s missing or changed"
                % (path.name, len(missing) + len(mismatched),
                   "" if len(missing) + len(mismatched) == 1 else "s",
                   "is" if len(missing) + len(mismatched) == 1 else "are"))
    elif recorded_problems:
        said = ("%s is readable, but %d record%s could not be used (%s)"
                % (path.name, len(recorded_problems),
                   "" if len(recorded_problems) == 1 else "s",
                   "; ".join(recorded_problems[:2])))
    else:
        said = "%s is whole: its records and every file they name agree." % path.name
    return {"ok": db_ok, "name": path.name, "path": path, "said": said,
            "missing": missing, "mismatched": mismatched, "complete": complete,
            "recorded_problems": recorded_problems,
            "pinned_missing": pinned_missing, "manifest": data}


def validate_all(db_path: Any, *, root: Optional[Path] = None) -> Dict[str, Any]:
    """Every retained generation, with the sentences a pass should print."""
    target = Path(db_path)
    reports = []
    problems: List[str] = []
    warnings: List[str] = []
    for path in published(target):
        report = validate(target, path, root=root)
        item = {
            "name": report["name"],
            "created": (report.get("manifest") or {}).get("created", ""),
            "complete": bool(report.get("complete")),
            "db_ok": bool(report.get("ok")),
            "missing": report.get("missing") or [],
            "mismatched": report.get("mismatched") or [],
            "recorded_problems": report.get("recorded_problems") or [],
            "said": report.get("said", ""),
        }
        reports.append(item)
        if not report.get("ok"):
            problems.append(report.get("said", ""))
        elif report.get("pinned_missing"):
            problems.append(
                "%s names save%s that %s no longer on the card: %s"
                % (report["name"],
                   "" if len(report["pinned_missing"]) == 1 else "s",
                   "is" if len(report["pinned_missing"]) == 1 else "are",
                   ", ".join(str(item.get("to")) for item in report["pinned_missing"][:4])))
        elif report.get("missing") or report.get("mismatched") \
                or report.get("recorded_problems"):
            warnings.append(report.get("said", ""))
    return {"generations": reports, "problems": [p for p in problems if p],
            "warnings": [w for w in warnings if w]}


def restore_source(db_path: Any, *, root: Optional[Path] = None) -> Optional[RestoreSource]:
    """The best copy to put back, or ``None`` when there is not one.

    The newest readable database wins: records committed after an older copy are
    worth more than a single save whose bytes were already gone.  What is missing
    is carried back to the caller to print — a repair that silently restores a
    row whose file is absent is the defect this module exists to end.
    """
    target = Path(db_path)
    root = Path(root) if root is not None else _root(target)
    found = published(target)
    best: Optional[RestoreSource] = None
    for path in found:
        report = validate(target, path, root=root)
        if not report.get("ok"):
            continue
        source = RestoreSource(
            path=path / DATABASE, generation=path.name,
            complete=bool(report.get("complete")),
            missing=report.get("missing") or [],
            mismatched=report.get("mismatched") or [],
            said=report.get("said", ""),
        )
        best = source
        break
    if best is not None:
        return best
    # No generation: adopt the old database-only copy as a *description* so the
    # caller can say what it would and would not restore.  Nothing is written.
    legacy = Path(str(target) + ".good.bak")
    if legacy.is_file():
        sound, _answer = _quick_check(legacy, readonly=False)
        if sound:
            try:
                refs, problems = references_from_database(legacy, readonly=False)
            except RecoveryRefused as exc:
                return RestoreSource(
                    path=legacy, generation="", legacy=True, complete=False,
                    said="the older database-only copy is readable, but the files its "
                         "records name could not be listed (%s)" % exc)
            checked = [_check(root, ref) for ref in refs]
            missing = [ref for ref in checked if not ref["present"]]
            mismatched = [ref for ref in checked if ref["mismatch"]]
            return RestoreSource(
                path=legacy, generation="", legacy=True,
                complete=not missing and not mismatched and not problems,
                missing=missing, mismatched=mismatched,
                said="the older database-only copy is readable"
                     + (" but %d file%s it names %s missing or changed"
                        % (len(missing) + len(mismatched),
                           "" if len(missing) + len(mismatched) == 1 else "s",
                           "is" if len(missing) + len(mismatched) == 1 else "are")
                        if missing or mismatched else ""),
            )
    return None


# ---------------------------------------------------------------------------
#  protecting what a retained generation names
# ---------------------------------------------------------------------------
def protected_paths(db_path: Any, *, root: Optional[Path] = None) -> Tuple[set, bool]:
    """Every pinned file a retained generation names, and whether one was unknown.

    ``unknown`` is true when a manifest could not be read.  A caller about to
    delete must treat that as "protect everything": a corrupt manifest is a
    reason to keep a file, never a licence to remove one.
    """
    target = Path(db_path)
    root = Path(root) if root is not None else _root(target)
    protected = set()
    unknown = False
    for path in published(target):
        data = _load(path)
        if data is None:
            unknown = True
            continue
        for ref in data.get("references") or []:
            if ref.get("pin"):
                absolute = _abs(root, str(ref.get("to") or ""))
                if absolute is not None:
                    try:
                        protected.add(str(absolute.resolve()))
                    except OSError:
                        unknown = True
    return protected, unknown


def protects(db_path: Any, path: Any, *, root: Optional[Path] = None) -> bool:
    """Is this file needed by a retained recovery copy?"""
    protected, unknown = protected_paths(db_path, root=root)
    if unknown:
        return True
    if not protected:
        return False
    try:
        return str(Path(path).resolve()) in protected
    except OSError:
        return False


# ---------------------------------------------------------------------------
#  retirement and collection
# ---------------------------------------------------------------------------
def _retire_now(db_path: Any, path: Path, reason: str) -> bool:
    """Take one directory out of service.  The caller holds the state lock."""
    if not path.is_dir() or not path.name.startswith(GEN_PREFIX):
        return False
    # ``.good.bak`` is normally a hard link to the newest generation's database.
    # Retiring the generation would leave that name pointing at the *pre-change*
    # records, which the next pass could adopt as its only recovery copy.  Drop
    # the name; the retired database itself is kept for a day.
    good = Path(db_path).with_name(Path(db_path).name + ".good.bak")
    try:
        if good.is_file() and os.path.samefile(good, path / DATABASE):
            good.unlink()
            _drop_sidecars(good)
    except OSError:
        pass
    try:
        destination = path.with_name("%s%s-%d" % (RETIRED_PREFIX, path.name,
                                                  int(time.time())))
        os.replace(path, destination)
        try:
            (destination / "retired.json").write_text(
                json.dumps({"retired": _iso(time.time()), "reason": str(reason)},
                           indent=2) + "\n", encoding="utf-8")
        except OSError:
            pass
        fsync_dir(path.parent)
        return True
    except OSError:
        return False


def _retire_for_paths_now(target: Path, paths: Sequence[Any], reason: str) -> List[str]:
    """The body of ``retire_for_paths``; the caller owns the lock."""
    root = _root(target)
    wanted = set()
    for item in paths:
        try:
            wanted.add(str(Path(item).resolve()))
        except OSError:
            continue
    if not wanted:
        return []
    doomed: List[Path] = []
    for path in published(target):
        data = _load(path) or {}
        for ref in data.get("references") or []:
            if not ref.get("pin"):
                continue
            absolute = _abs(root, str(ref.get("to") or ""))
            if absolute is not None and str(absolute.resolve()) in wanted:
                doomed.append(path)
                break
    retired: List[str] = []
    for path in doomed:
        if _retire_now(target, path, reason):
            retired.append(path.name)
    return retired


def retire(db_path: Any, generation: Any, *, reason: str = "",
           wait: float = DISCARD_WAIT) -> bool:
    """Take one generation out of service.  Its pins stop applying at once."""
    target = Path(db_path)
    path = Path(generation)
    if not path.is_absolute():
        path = generations_dir(target) / path
    if not path.is_dir() or not path.name.startswith(GEN_PREFIX):
        return False
    with _holding(target, exclusive=True, wait=wait) as held:
        if not held:
            return False
        return _retire_now(target, path, reason)


def retire_all(db_path: Any, *, reason: str = "", wait: float = DISCARD_WAIT) -> List[str]:
    """Retire every published generation, oldest first, under one lock."""
    target = Path(db_path)
    retired: List[str] = []
    with _holding(target, exclusive=True, wait=wait) as held:
        if not held:
            return retired
        for path in published(target):
            if _retire_now(target, path, reason):
                retired.append(path.name)
    return retired


def retire_all_locked(db_path: Any, *, reason: str = "") -> List[str]:
    """Retire every published generation.  The caller already holds the state lock.

    A restore uses this inside its own capture lock: the copies it is about to
    replace must be out of service before that lock is released, or a capture
    could publish a fresh copy in the gap and be retired by this same restore.
    """
    target = Path(db_path)
    retired: List[str] = []
    for path in published(target):
        if _retire_now(target, path, reason):
            retired.append(path.name)
    return retired


def retire_for_paths(db_path: Any, paths: Sequence[Any], *, reason: str = "") -> List[str]:
    """Retire generations that pin any of ``paths``, so a deliberate delete can go.

    This is the honest way to delete a file a recovery copy still needs: the copy
    stops claiming it.  The next capture describes the box as it now is.
    """
    target = Path(db_path)
    with _holding(target, exclusive=True, wait=DISCARD_WAIT) as held:
        if not held:
            return []
        return _retire_for_paths_now(target, paths, reason)


def discard(db_path: Any, path: Any, *, reason: str = "a person deleted it") -> bool:
    """Retire copies that need this file, then remove it — or refuse while busy.

    The lock is the point: a capture cannot be half-way through naming this file
    while it is removed.  Answering ``False`` means the caller must not remove it
    either; the file is still protected.
    """
    target = Path(db_path)
    with _holding(target, exclusive=True, wait=DISCARD_WAIT) as held:
        if not held:
            return False
        try:
            _retire_for_paths_now(target, [path], reason)
        except Exception:                    # noqa: BLE001 — retiring is best effort
            pass
        # Retiring is how a pinned file is allowed to go.  If a copy still names
        # it (a manifest that would not move, or another path in the same copy),
        # the file stays: a delete is not worth an incoherent recovery copy.
        still_pinned, unknown = protected_paths(target, root=_root(target))
        if unknown:
            return False
        try:
            if str(Path(path).resolve()) in still_pinned:
                return False
        except OSError:
            return False
        try:
            Path(path).unlink()
        except FileNotFoundError:
            return True
        except OSError:
            return False
        return True


def gc(db_path: Any, *, root: Optional[Path] = None, now: Optional[float] = None,
       grace: float = STALE_SECONDS, keep: int = KEEP,
       wait: float = GC_WAIT) -> Dict[str, Any]:
    """Clear away what no live record and no retained copy needs.

    Runs under the exclusive side, so a capture cannot be naming a blob while it
    is removed.  Age is the second guard: a blob written a moment ago may belong
    to a save whose row is about to commit, and deleting it is worse than leaving
    it for another day.
    """
    target = Path(db_path)
    root = Path(root) if root is not None else _root(target)
    when = time.time() if now is None else float(now)
    report: Dict[str, Any] = {"status": "done", "removed": 0, "bytes": 0,
                              "partials": [], "retired": [], "pruned": [],
                              "problems": []}
    with _holding(target, exclusive=True, wait=wait) as held:
        if not held:
            report["status"] = "deferred"
            report["said"] = "a copy or a backup is using the records right now."
            return report

        try:
            conn = sqlite3.connect("file:%s?mode=ro" % target, uri=True, timeout=10)
        except sqlite3.Error as exc:
            report["status"] = "deferred"
            report["problems"].append("the records cannot be read (%s)" % exc)
            return report
        try:
            conn.row_factory = sqlite3.Row
            try:
                live_refs, _problems = references(conn)
            except sqlite3.Error as exc:
                report["status"] = "deferred"
                report["problems"].append("the records cannot be read (%s)" % exc)
                return report
        finally:
            conn.close()

        live = set()
        for ref in live_refs:
            absolute = _abs(root, str(ref.get("to") or ""))
            if absolute is not None:
                live.add(str(absolute.resolve()))
        pinned, unknown = protected_paths(target, root=root)
        if unknown:
            report["status"] = "deferred"
            report["problems"].append(
                "a recovery copy has no readable manifest, so nothing was removed.")
            return report
        keepers = live | pinned

        saves = root / "arcade" / "saves"
        cutoff = when - max(0.0, float(grace))
        try:
            candidates = [p for p in saves.rglob("*") if p.is_file()]
        except OSError:
            candidates = []
        for path in candidates:
            try:
                if path.is_symlink():
                    continue
                if path.name.startswith(".") and path.suffix not in (".part", ".tmp"):
                    continue
                if str(path.resolve()) in keepers:
                    continue
                if path.stat().st_mtime > cutoff:
                    continue
                size = path.stat().st_size
                path.unlink()
                report["removed"] += 1
                report["bytes"] += int(size or 0)
            except OSError:
                continue

        report["partials"] = _clean_partials(target, now=when, grace=grace)
        retired_cutoff = when - RETIRED_SECONDS
        try:
            retired = [p for p in generations_dir(target).iterdir()
                       if p.is_dir() and p.name.startswith(RETIRED_PREFIX)]
        except OSError:
            retired = []
        for path in retired:
            try:
                if path.stat().st_mtime > retired_cutoff:
                    continue
                shutil.rmtree(path)
                report["retired"].append(path.name)
            except OSError:
                continue
        report["pruned"] = _prune(target, keep)
        return report


# ---------------------------------------------------------------------------
#  an older database-only copy becomes a generation
# ---------------------------------------------------------------------------
def adopt_legacy(db_path: Any, *, root: Optional[Path] = None,
                 now: Optional[float] = None,
                 wait: float = CAPTURE_WAIT) -> Optional[Capture]:
    """Turn ``<db>.good.bak`` into a generation with a manifest, once.

    Before this module, the daily copy was the database and nothing else.  It is
    still useful — it is the records — but it may name save blobs the box has
    already forgotten.  Adopting it records exactly what is missing instead of
    discovering that on the bad day.  No new bytes are invented: the database is
    copied with SQLite, and every external file is checked where it already is.
    """
    target = Path(db_path)
    root = Path(root) if root is not None else _root(target)
    when = time.time() if now is None else float(now)
    legacy = Path(str(target) + ".good.bak")
    if not target.is_file() or not legacy.is_file():
        return None
    if published(target):
        return None
    with _holding(target, exclusive=True, wait=wait) as held:
        if not held:
            return None
        if published(target) or not legacy.is_file():
            return None
        # A hard link to a retired generation is that generation, not a fresh
        # database-only copy.  Adopting it would bring pre-delete records back as
        # the only recovery copy.
        try:
            for retired in generations_dir(target).iterdir():
                if not retired.is_dir() or not retired.name.startswith(RETIRED_PREFIX):
                    continue
                candidate = retired / DATABASE
                if candidate.is_file() and os.path.samefile(legacy, candidate):
                    return None
        except OSError:
            pass
        sound, answer = _quick_check(legacy, readonly=False)
        if not sound:
            return Capture("refused",
                           said="the older database-only copy cannot be read (%s)."
                                % answer)
        _ensure(generations_dir(target))
        try:
            mtime = legacy.stat().st_mtime
        except OSError:
            mtime = when
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(mtime))
        name = "%s%s-%s" % (LEGACY_PREFIX, stamp,
                            hashlib.sha256(("legacy:%s:%s" % (mtime, when)).encode())
                            .hexdigest()[:8])
        folder = generations_dir(target)
        stage = folder / ("." + name + ".partial")
        final = folder / name
        try:
            if stage.exists():
                shutil.rmtree(stage)
            stage.mkdir()
            os.chmod(stage, 0o700)
            _copy_database(legacy, stage / DATABASE)
            os.chmod(stage / DATABASE, 0o600)
            sound, answer = _quick_check(stage / DATABASE)
            if not sound:
                raise RecoveryRefused(
                    "the older copy could not be checked (%s)." % answer)
            conn = sqlite3.connect("file:%s?mode=ro" % (stage / DATABASE),
                                   uri=True, timeout=10.0)
            try:
                conn.row_factory = sqlite3.Row
                refs, problems = references(conn)
                schema = _schema_version(conn)
            finally:
                conn.close()
            checked = [_check(root, ref) for ref in refs]
            missing = [ref for ref in checked if not ref["present"]]
            mismatched = [ref for ref in checked if ref["mismatch"]]
            manifest: Dict[str, Any] = {
                "format": FORMAT,
                "kind": KIND,
                "generation": name,
                "created": _iso(mtime),
                "reason": "adopted the database-only copy that was already here",
                "legacy": True,
                "database": {
                    "file": DATABASE,
                    "size": (stage / DATABASE).stat().st_size,
                    "sha256": _sha256(stage / DATABASE),
                    "schema_version": schema,
                },
                "references": checked,
                "missing": [{"to": ref["to"], "kind": ref["kind"], "pin": ref["pin"]}
                            for ref in missing],
                "mismatched": [{"to": ref["to"], "kind": ref["kind"],
                                "why": ref["mismatch"], "pin": ref["pin"]}
                               for ref in mismatched],
                "problems": problems,
                "complete": not missing and not mismatched and not problems,
                "excluded": EXCLUDED,
                "counts": {
                    "references": len(checked),
                    "pinned": len([r for r in checked if r["pin"]]),
                    "pinned_bytes": sum(int(r["size"] or 0) for r in checked if r["pin"]),
                },
            }
            _write_manifest(stage, manifest)
            fsync_dir(stage)
            _adopt_tree(stage)
            os.replace(stage, final)
            fsync_dir(folder)
            _adopt_tree(final)
            # The name beside the records now points at the adopted copy by a
            # hard link, not at the old database-only file it was made from.
            _refresh_good_bak(target, final)
        except BaseException:
            try:
                if stage.exists():
                    shutil.rmtree(stage)
            except OSError:
                pass
            raise
        return Capture("adopted", generation=name, path=final, manifest=manifest,
                       said="the older database-only copy was adopted as a recovery "
                            "copy%s" % ("" if manifest["complete"] else
                                        "; %d file%s it names %s already missing"
                                        % (len(missing) + len(mismatched),
                                           "" if len(missing) + len(mismatched) == 1 else "s",
                                           "is" if len(missing) + len(mismatched) == 1 else "are")),
                       missing=missing)
