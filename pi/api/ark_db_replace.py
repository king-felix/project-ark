"""Project ARK — the one crash-safe way to put a database back.

A database is not one file.  In WAL mode the committed pages can live in
``<name>-wal`` beside it, and those pages belong to the file that was there when
they were written.  The old replacement code removed that journal **before** it
wrote its replacement.  A write failure then left the original main file present
but its journal gone — committed rows lost, and a message that said the file was
untouched.  The same shape lived in ``ark-restore``.

This module replaces one database with another as a **recorded sequence**, never
as "one atomic transaction".  ``os.replace()`` is atomic for one name; it cannot
make a main file and a journal change together.  So the sequence is made
recoverable instead:

1.  **Maintenance first.**  The repair turnstile and the exclusive gate are taken
    before the existing file is even read for the copy.  If they cannot be had,
    nothing is changed.  (``ark_maintenance`` owns the locks; this module is only
    a user of them.)
2.  **Stage and validate the candidate.**  The candidate is copied into a
    staging folder on the **destination filesystem**, opened with SQLite, checked
    with ``quick_check``, and written back out as one standalone database with
    ``backup()``.  Its bytes are flushed and its folder is synced.  Nothing beside
    the live file has been touched yet.
3.  **Preserve the original as a coherent set.**  The original main file is
    copied, and its ``-wal``/``-shm`` files are **moved** into the same recovery
    folder (never deleted).  The main copy is made first; the ``-shm`` goes
    before the ``-wal`` so every intermediate state is readable by SQLite.  This
    is the rollback set and the evidence.
4.  **Move the old journal away, then install.**  The old journal must not sit
    beside a new main file.  Only after both sidecars are out of the way does
    ``os.replace(candidate, target)`` run.  A journal that appears between those
    two steps is treated as a writer that ignored the gate: the attempt is
    abandoned, not paired with the replacement.
5.  **Verify the installed file.**  The target's fingerprint and ``quick_check``
    are checked.  Validation of the candidate (step 2) and preservation of the
    original (step 3) are deliberately separate responsibilities; neither is
    inferred from the other.
6.  **Record and commit.**  ``state.json`` is written atomically before and after
    every destructive step, with a sequence number and a ``.bak``.  A ``BLOCK``
    marker sits in the recovery folder while the target may be inconsistent;
    ``ark_db.connect()`` refuses to open a database with that marker, so no
    service can read a half-swapped file even if it starts before the repair.
7.  **Retain evidence.**  After a verified commit the whole recovery folder is
    archived as ``<name>.recovery-<stamp>-committed-<id>``.  Failed attempts are
    archived too.  A documented, size-aware retention policy keeps the newest
    sets and never deletes a pending or blocked one.

``recover_pending()`` is the startup half.  It reads the recorded progress and
finishes an interrupted operation **before** normal services open the database:

* a killed attempt before the journal moved is left exactly as it was;
* a killed attempt after the journal moved but before the replacement was
  installed has its journal put back, so the original reads as it did;
* a killed attempt after a verified install is rolled forward;
* an attempt that cannot be resolved either way is left ``blocked``: the
  evidence stays, the ``BLOCK`` marker stays, and the caller keeps services
  stopped.  Nothing prints "nothing changed" after a file was changed.

The live box uses this from ``ark-self-heal`` (boot and the daily pass) and from
``ark-restore`` (the owner's deliberate restore).  The two must agree about what
"put back" means, so there is one implementation here rather than two similar
ones that drift apart.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import shutil
import sqlite3
import stat
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import ark_atomic
import ark_maintenance as maintenance

#: Where the live replacement is recorded.  Beside the database, on the same
#: filesystem, so every rename below is a rename and not a copy across devices.
ACTIVE_SUFFIX = ".recovery"
ARCHIVE_PREFIX = ".recovery-"
STATE_NAME = "state.json"
STATE_BAK = STATE_NAME + ".bak"
BLOCK_NAME = "BLOCK"
ORIGINAL_DIR = "original"
STAGING_DIR = "staging"
REPLACED_DIR = "replaced"
CANDIDATE_NAME = "candidate.db"
INPUT_NAME = "input.db"
SIDECAR_SUFFIXES = ("-wal", "-shm")

#: The phases that mean the target (or its sidecars) may be inconsistent.  While
#: one of these is recorded, ``BLOCK`` is present and ``ark_db`` refuses reads.
BLOCKING_PHASES = frozenset((
    "moving-sidecars", "sidecars-moved", "installing", "installed",
    "rolling-back", "blocked",
))

#: Phases in which the original file has not been disturbed.  A failure here can
#: be archived as "failed" without any rollback.
PRE_DESTRUCTIVE_PHASES = frozenset((
    "preparing", "staged", "original-preserved",
))

#: How many archived recovery sets of each kind to keep.  The newest one is
#: never deleted, even if it exceeds the byte cap: it is the only evidence of
#: the operation that just happened.
KEEP_COMMITTED = 2
KEEP_FAILED = 2

#: A soft ceiling for all archived evidence beside one database.  Deleting old
#: evidence is always safe; keeping a card full of it is not.
MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024

#: Space kept free beyond the bytes this operation can be asked to copy.  This is
#: not a second reserve policy: it is a margin for filesystem bookkeeping.  The
#: reserve itself comes from ``ark_space`` (the one storage policy).
SPACE_MARGIN = 4 * 1024 * 1024

#: How much to read/write at once.  Large enough for a card, small enough not to
#: hold a whole film database in memory.
CHUNK = 1024 * 1024

#: A test seam.  Tests set this to a callable and can raise or ``os._exit(97)``
#: at a named boundary to prove the next startup resolves the state.  It is not
#: read from the environment, so production cannot be crashed by an env var.
fault_hook: Optional[Callable[[str], None]] = None


class ReplaceError(Exception):
    """The replacement could not continue.  The caller decides what to say."""


@dataclass
class Replacement:
    """What ``replace()`` did, in terms a caller can act on.

    ``status`` is one of ``ok``, ``busy``, ``refused``, ``unsafe`` or
    ``blocked``.  ``changed`` means a file belonging to the database (the main
    file or a sidecar) was changed and then put back; it is deliberately not
    "false" merely because the request failed.  ``safe`` means the target and
    its sidecars may be opened by a service now.
    """

    status: str
    changed: bool
    replaced: bool
    safe: bool
    evidence: Optional[Path]
    said: str
    state: Dict[str, Any] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return self.status == "ok"


@dataclass
class Recovery:
    """What ``recover_pending()`` found and did."""

    status: str
    pending: bool
    said: str
    evidence: Optional[Path] = None


# ---------------------------------------------------------------------------
#  names and small durable helpers
# ---------------------------------------------------------------------------
def active_dir(target: Any) -> Path:
    """The one recovery folder for this database."""
    target = Path(target)
    return target.with_name(target.name + ACTIVE_SUFFIX)


def block_path(target: Any) -> Path:
    """The marker that keeps services out while the target may be mid-swap."""
    return active_dir(target) / BLOCK_NAME


def pending(target: Any) -> bool:
    """May a service *not* open this database just now?

    This is asked on every ordinary connection, so it is one ``stat``, not a
    parse of the recovery record.  The marker is written and synced before the
    first destructive step and removed after the file is verified (or after a
    coherent rollback), so a process that starts before the boot repair cannot
    read a half-swapped file.
    """
    try:
        os.stat(str(block_path(target)))
        return True
    except FileNotFoundError:
        return False
    except OSError:
        # The recovery folder is 0700 and the portal runs as ``pi``: a
        # permission error means "there is something there this account cannot
        # inspect", which is not permission to open the records.
        return True


def has_active(target: Any) -> bool:
    """Is there an unresolved replacement folder, blocking or not?"""
    try:
        return active_dir(target).is_dir()
    except OSError:
        return True


def _fault(point: str) -> None:
    hook = fault_hook
    if hook is not None:
        hook(point)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(str(path), "rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def _size(path: Path) -> int:
    try:
        return int(path.stat().st_size)
    except OSError:
        return 0


def _is_regular(path: Path) -> bool:
    try:
        return stat.S_ISREG(path.lstat().st_mode)
    except OSError:
        return False


def _sidecar_present(target: Path, suffix: str) -> bool:
    """A sidecar exists, including a broken link that must still be moved away."""
    sidecar = Path(str(target) + suffix)
    return sidecar.exists() or sidecar.is_symlink()


def _owner_record(path: Path) -> Dict[str, int]:
    """The mode and owner a replacement must keep.

    ``ark-self-heal`` runs as root and the portal runs as ``pi``.  A root-created
    replacement left mode 0640 root:root is invisible to every page — a repair
    that locks the owner out of the records.  The original file's own mode and
    owner are therefore carried into the staged copy before it is installed.
    """
    try:
        st = path.stat()
        return {"mode": stat.S_IMODE(st.st_mode) or 0o644,
                "uid": int(st.st_uid), "gid": int(st.st_gid)}
    except OSError:
        # A new file (a restore onto an empty box) belongs to whoever owns the
        # folder the box keeps its records in, not to root.
        try:
            parent = path.parent.stat()
            return {"mode": 0o640, "uid": int(parent.st_uid),
                    "gid": int(parent.st_gid)}
        except OSError:
            return {"mode": 0o640, "uid": -1, "gid": -1}


def _adopt_mode_owner(path: Path, record: Dict[str, Any], *,
                      set_mode: bool = True) -> None:
    if set_mode:
        mode = int(record.get("mode") or 0o640)
        try:
            os.chmod(str(path), mode)
        except OSError:
            pass
    uid = int(record.get("uid", -1) or -1)
    gid = int(record.get("gid", -1) or -1)
    if uid >= 0 and gid >= 0 and hasattr(os, "geteuid") and os.geteuid() == 0:
        try:
            os.chown(str(path), uid, gid)
        except OSError:
            pass


def _adopt_like(path: Path, reference: Path) -> None:
    """Give a staging file the mode and owner the live file had."""
    _adopt_mode_owner(path, _owner_record(reference))


def _adopt_sidecars(target: Path, record: Optional[Dict[str, Any]] = None) -> None:
    """Keep journals owned by the account that owns the database.

    Opening a WAL database read-write (as a root-run rollback check must) can
    create ``-shm``/``-wal`` owned by root.  The portal then cannot write them.
    """
    try:
        st = target.stat()
        default = {"uid": int(st.st_uid), "gid": int(st.st_gid)}
    except OSError:
        default = {}
    wanted = {**default, **(record or {})}
    for suffix in SIDECAR_SUFFIXES:
        sidecar = Path(str(target) + suffix)
        if not sidecar.exists():
            continue
        try:
            os.chmod(str(sidecar), 0o600)
        except OSError:
            pass
        uid = int(wanted.get("uid", -1) or -1)
        gid = int(wanted.get("gid", -1) or -1)
        if uid >= 0 and gid >= 0 and hasattr(os, "geteuid") and os.geteuid() == 0:
            try:
                os.chown(str(sidecar), uid, gid)
            except OSError:
                pass


def _fsync_file(path: Path) -> None:
    """Flush one file's bytes to the card."""
    handle = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(handle)
    finally:
        os.close(handle)


def _copy_file(source: Path, target: Path, *, mode: Optional[int] = None) -> None:
    """Copy bytes, flush them, and leave ``target`` a regular file.

    No rename here: the caller decides whether the copy is published.  A failure
    leaves a partial file at ``target``; callers put staging files in a private
    folder so a partial copy can never be mistaken for a database.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(str(source), "rb") as reader, open(str(target), "wb") as writer:
        for block in iter(lambda: reader.read(CHUNK), b""):
            writer.write(block)
        writer.flush()
        os.fsync(writer.fileno())
    if mode is not None:
        os.chmod(str(target), mode)


def _install_copy(source: Path, target: Path, *, mode: Optional[int] = None,
                  owner: Optional[Dict[str, Any]] = None) -> None:
    """Put a **copy** of ``source`` at ``target`` by one rename.

    A copy, not a hard link: after a rollback the target is live and may be
    written again, and the recovery copy must remain the bytes that were saved,
    not a second name for a file SQLite is about to change.
    """
    folder = target.parent
    handle, temporary = tempfile.mkstemp(
        prefix="." + target.name + ".recovery-", suffix=".tmp", dir=str(folder))
    try:
        with os.fdopen(handle, "wb") as writer, open(str(source), "rb") as reader:
            for block in iter(lambda: reader.read(CHUNK), b""):
                writer.write(block)
            writer.flush()
            os.fsync(writer.fileno())
        if mode is not None:
            os.chmod(temporary, mode)
        if owner:
            _adopt_mode_owner(Path(temporary), owner, set_mode=(mode is None))
        os.replace(temporary, str(target))
        temporary = ""
        ark_atomic.fsync_dir(folder)
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def _replace_path(source: Path, target: Path) -> None:
    """The one rename a test may intercept; production is ``os.replace``."""
    os.replace(str(source), str(target))


def _verify_immutable(path: Path) -> Tuple[bool, str]:
    """Ask SQLite whether a standalone file is readable, without creating sidecars.

    ``immutable=1`` is exactly the right question for a staged candidate: there
    is no journal beside it, and opening it read-only would otherwise try to
    create a ``-shm`` file just to ask.  ``quick_check`` is the project's cheap
    check; it finds the damage a cut leaves without a full ``integrity_check``.
    """
    if not path.is_file():
        return False, "there is no file"
    try:
        uri = path.absolute().as_uri() + "?mode=ro&immutable=1"
        conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    except sqlite3.Error as exc:
        return False, str(exc)
    try:
        row = conn.execute("PRAGMA quick_check;").fetchone()
        answer = str((row or [""])[0] or "").strip()
        conn.execute("SELECT COUNT(*) FROM sqlite_master;").fetchone()
    except sqlite3.Error as exc:
        return False, str(exc)
    finally:
        try:
            conn.close()
        except sqlite3.Error:                 # pragma: no cover - already closed
            pass
    if answer.lower() == "ok":
        return True, "ok"
    return False, answer or "no answer"


def _verify_live(path: Path) -> Tuple[bool, str]:
    """Ask SQLite about a live file and its own journal.

    Read-write on purpose, for the same reason ``quick_check_state`` is: a WAL
    database whose ``-shm`` is missing refuses a read-only open, and the file is
    not damaged merely because a cut took its index.  Opening it lets SQLite
    recover the journal first.  This is used **after** a rollback, when the file
    at ``path`` is the original and its sidecars are the original's.
    """
    if not path.is_file():
        return False, "there is no file"
    try:
        conn = sqlite3.connect(str(path), timeout=10.0)
    except sqlite3.Error as exc:
        return False, str(exc)
    try:
        row = conn.execute("PRAGMA quick_check;").fetchone()
        answer = str((row or [""])[0] or "").strip()
    except sqlite3.Error as exc:
        return False, str(exc)
    finally:
        try:
            conn.close()
        except sqlite3.Error:                 # pragma: no cover - already closed
            pass
    if answer.lower() == "ok":
        return True, "ok"
    return False, answer or "no answer"


def _usable_bytes(path: Path) -> Optional[int]:
    """What the one storage policy says may be written here, or ``None``.

    ``ark_space`` is imported lazily: it imports ``ark_db``, and ``ark_db``
    imports this module.  A fallback to raw free space avoids inventing a second
    reserve: the reserve is ``ark_space``'s alone, so if it cannot be asked the
    check is "is there free space at all", not "is there 7 % more".
    """
    try:
        import ark_space
        # Replacing a database is a recovery operation: it is admitted against
        # real free space, not against other jobs' promises, so a card that is
        # full (or a ledger that cannot be read) never stops the records being
        # put back.  The reserve itself is still kept back.
        value = ark_space.essential_room(path)
        if value is not None:
            return int(value)
    except Exception:                         # noqa: BLE001 — measurement is optional
        pass
    try:
        return int(shutil.disk_usage(str(path)).free)
    except OSError:
        return None


def _space_needed(candidate_size: int, original_size: int,
                  sidecar_sizes: int) -> int:
    """Worst case, including the rollback copy.

    The candidate is copied twice while it is made standalone (input + checked
    output), and a rollback copies the original back once more while keeping the
    recovery copy.  Refusing before the first byte is better than discovering
    the shortfall halfway through a rollback.
    """
    return max(0, 2 * candidate_size + 2 * original_size
               + sidecar_sizes + SPACE_MARGIN)


def _check_room(source: Path, target: Path) -> None:
    candidate_size = _size(source)
    original_size = _size(target) if target.exists() else 0
    sidecar_sizes = 0
    for suffix in SIDECAR_SUFFIXES:
        sidecar = Path(str(target) + suffix)
        if sidecar.exists():
            sidecar_sizes += _size(sidecar)
    need = _space_needed(candidate_size, original_size, sidecar_sizes)
    usable = _usable_bytes(target.parent if target.parent.exists() else target.parent)
    if usable is not None and usable < need:
        raise ReplaceError(
            "there is not enough room to keep the old records while the new ones "
            "are put in place: this needs about %s and only %s can be used.  "
            "Nothing was changed." % (_human(need), _human(usable)))


def _human(number: int) -> str:
    step = float(number)
    for unit in ("B", "kB", "MB", "GB", "TB"):
        if step < 1000 or unit == "TB":
            return ("%d %s" % (int(step), unit) if unit == "B"
                    else "%.1f %s" % (step, unit))
        step /= 1000.0
    return "%d B" % int(number)


# ---------------------------------------------------------------------------
#  the recovery record
# ---------------------------------------------------------------------------
def _state_of(target: Path) -> Dict[str, Any]:
    return {
        "version": 1,
        "seq": 0,
        "id": time.strftime("%Y%m%d-%H%M%S", time.localtime()) + "-"
              + secrets.token_hex(4),
        "target": str(target),
        "reason": "",
        "phase": "preparing",
        "started": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
        "updated": "",
        "source": "",
        "candidate": {},
        "original": {},
        "problem": "",
    }


def _write_state(active: Path, state: Dict[str, Any]) -> None:
    """Record one step durably, with a sequence number and a previous copy.

    ``ark_atomic.save_json`` writes a temp file in the same folder, flushes it,
    keeps ``state.json.bak`` and renames.  A torn write is therefore a previous
    step, never a half-line, and ``_read_state`` prefers the highest sequence.
    """
    state["seq"] = int(state.get("seq") or 0) + 1
    state["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
    ark_atomic.save_json(active / STATE_NAME, state, mode=0o600, keep_backup=True)
    ark_atomic.fsync_dir(active)


def _read_state(active: Path, target: Path) -> Tuple[Optional[Dict[str, Any]], str]:
    """The newest readable record for this target, or ``(None, why not)``."""
    best: Optional[Dict[str, Any]] = None
    reason = "no replacement record"
    for name in (STATE_NAME, STATE_BAK):
        value, problem = ark_atomic.read_json(active / name)
        if problem or not isinstance(value, dict):
            reason = problem or "the replacement record is not an object"
            continue
        try:
            recorded = Path(str(value.get("target") or "")).resolve()
            if recorded != target.resolve():
                reason = "the replacement record names a different database"
                continue
        except OSError:
            reason = "the replacement record names an unreadable path"
            continue
        if best is None or int(value.get("seq") or 0) > int(best.get("seq") or 0):
            best = value
    if best is None:
        return None, reason
    return best, ""


def _set_block(active: Path) -> None:
    """Put the marker in place before the first destructive step, durably."""
    ark_atomic.save_text(active / BLOCK_NAME,
                         "the records are being put back; do not open them\n",
                         mode=0o600, keep_backup=False)
    ark_atomic.fsync_dir(active)


def _clear_block(active: Path) -> None:
    """Remove the marker only after verification or a coherent rollback."""
    try:
        (active / BLOCK_NAME).unlink()
    except FileNotFoundError:
        pass
    except OSError:                           # pragma: no cover - read-only folder
        return
    ark_atomic.fsync_dir(active)


# ---------------------------------------------------------------------------
#  the sequence
# ---------------------------------------------------------------------------
def _stage_candidate(source: Path, active: Path, target: Path,
                     state: Dict[str, Any]) -> None:
    """Make one checked, standalone candidate on the destination filesystem.

    This touches nothing beside ``target``.  The candidate's own journal (if the
    source happened to have one) is copied into a private staging folder and
    resolved there by SQLite; ``backup()`` then writes one file with no sidecars,
    so the thing that will be installed can never be paired with an old journal.
    """
    if not source.is_file():
        raise ReplaceError("the copy to put back does not exist.")
    if source.resolve() == target.resolve():
        raise ReplaceError("the copy to put back is the database itself.")
    staging = active / STAGING_DIR
    staging.mkdir(parents=True, exist_ok=True, mode=0o700)
    input_copy = staging / INPUT_NAME
    candidate = staging / CANDIDATE_NAME
    for old in (input_copy, Path(str(input_copy) + "-wal"),
                Path(str(input_copy) + "-shm"), candidate):
        try:
            old.unlink()
        except FileNotFoundError:
            pass
    _copy_file(source, input_copy, mode=0o600)
    for suffix in SIDECAR_SUFFIXES:
        sidecar = Path(str(source) + suffix)
        if sidecar.exists() or sidecar.is_symlink():
            if not _is_regular(sidecar):
                raise ReplaceError(
                    "something that is not a journal file is beside the copy to "
                    "put back (%s)." % sidecar.name)
            _copy_file(sidecar, Path(str(input_copy) + suffix), mode=0o600)
    _fsync_file(input_copy)
    ark_atomic.fsync_dir(staging)

    try:
        source_conn = sqlite3.connect(str(input_copy), timeout=10.0)
    except sqlite3.Error as exc:
        raise ReplaceError("the copy to put back could not be opened (%s)." % exc)
    try:
        try:
            answer = source_conn.execute("PRAGMA quick_check;").fetchone()
        except sqlite3.Error as exc:
            raise ReplaceError("the copy to put back could not be checked (%s)." % exc)
        said = str((answer or [""])[0] or "").strip()
        if said.lower() != "ok":
            raise ReplaceError(
                "the copy to put back cannot be read (%s)." % (said or "no answer"))
        try:
            dest = sqlite3.connect(str(candidate), timeout=10.0)
        except sqlite3.Error as exc:
            raise ReplaceError("the checked copy could not be written (%s)." % exc)
        try:
            source_conn.backup(dest)
            dest.commit()
        except sqlite3.Error as exc:
            raise ReplaceError("the checked copy could not be written (%s)." % exc)
        finally:
            try:
                dest.close()
            except sqlite3.Error:             # pragma: no cover - already closed
                pass
    finally:
        try:
            source_conn.close()
        except sqlite3.Error:                 # pragma: no cover - already closed
            pass
    # The staging input has done its job.  Removing it keeps the peak space
    # small, and it is never the thing installed.
    for leftover in (input_copy, Path(str(input_copy) + "-wal"),
                     Path(str(input_copy) + "-shm"),
                     Path(str(candidate) + "-wal"), Path(str(candidate) + "-shm")):
        try:
            leftover.unlink()
        except FileNotFoundError:
            pass
        except OSError:                       # pragma: no cover - read-only folder
            pass
    ok, why = _verify_immutable(candidate)
    if not ok:
        raise ReplaceError(
            "the checked copy does not open as a database (%s)." % why)
    _adopt_like(candidate, target)
    _fsync_file(candidate)
    ark_atomic.fsync_dir(staging)
    state["candidate"] = {
        "path": str(candidate.relative_to(active)),
        "sha256": _sha256(candidate),
        "size": _size(candidate),
    }
    state["phase"] = "staged"
    _write_state(active, state)
    _fault("staged")


def _preserve_original(target: Path, active: Path,
                       state: Dict[str, Any]) -> None:
    """Copy the main file and record the journal files before touching either.

    The sidecars are not copied here: they are moved in ``_move_sidecars`` after
    the main copy exists.  Recording their fingerprints first is what lets a
    startup tell an original journal from a new, unrelated one.
    """
    original = active / ORIGINAL_DIR
    original.mkdir(parents=True, exist_ok=True, mode=0o700)
    record: Dict[str, Any] = {"existed": target.exists(), "main": {},
                              "sidecars": {}}
    if target.is_symlink():
        raise ReplaceError(
            "the existing records are a symbolic link, so they were not replaced.")
    if target.exists():
        if not _is_regular(target):
            raise ReplaceError(
                "the existing records are not a regular file, so they were not "
                "replaced.")
        main_copy = original / target.name
        _copy_file(target, main_copy, mode=0o600)
        _fsync_file(main_copy)
        record["main"] = {
            "name": target.name,
            "sha256": _sha256(main_copy),
            "size": _size(main_copy),
            **_owner_record(target),
        }
    for suffix in SIDECAR_SUFFIXES:
        sidecar = Path(str(target) + suffix)
        if not sidecar.exists() and not sidecar.is_symlink():
            continue
        if not _is_regular(sidecar):
            if suffix == "-shm":
                # The index is not the journal: a junk one is cleared out by
                # ``_move_sidecars`` but is not a reason to abandon a repair.
                continue
            raise ReplaceError(
                "something that is not a journal file is in the way beside the "
                "records (%s); nothing was changed." % sidecar.name)
        record["sidecars"][suffix] = {
            "name": sidecar.name,
            "sha256": _sha256(sidecar),
            "size": _size(sidecar),
            "moved": False,
            **_owner_record(sidecar),
        }
    state["original"] = record
    state["phase"] = "original-preserved"
    _write_state(active, state)
    ark_atomic.fsync_dir(original)
    _fault("original-preserved")


def _move_sidecars(target: Path, active: Path,
                   state: Dict[str, Any]) -> None:
    """Take the old journal out of the live folder, into the recovery set.

    ``-shm`` goes first and ``-wal`` last: at every intermediate point the live
    main file either still has its journal, or is the original main alone (which
    SQLite can open and will give a fresh index).  The bytes are moved, never
    deleted, so a failure at any point can put them back.
    """
    original = active / ORIGINAL_DIR
    state["phase"] = "moving-sidecars"
    _write_state(active, state)
    _set_block(active)
    for suffix in ("-shm", "-wal"):
        sidecar = Path(str(target) + suffix)
        if not sidecar.exists() and not sidecar.is_symlink():
            continue
        if not _is_regular(sidecar):
            if suffix == "-shm":
                # A directory or link where the index should be is not a journal.
                # It still must not sit beside the replacement, so it is moved
                # into the evidence folder rather than deleted.
                unusable = active / REPLACED_DIR / (sidecar.name + ".unusable")
                unusable.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                _replace_path(sidecar, unusable)
                ark_atomic.fsync_dir(unusable.parent)
                ark_atomic.fsync_dir(target.parent)
                continue
            raise ReplaceError(
                "the old journal could not be moved (%s is not a regular file); "
                "nothing was replaced." % sidecar.name)
        destination = original / sidecar.name
        _replace_path(sidecar, destination)
        ark_atomic.fsync_dir(original)
        ark_atomic.fsync_dir(target.parent)
        entry = state.get("original", {}).get("sidecars", {}).get(suffix)
        if entry:
            if _sha256(destination) != str(entry.get("sha256") or ""):
                raise ReplaceError(
                    "the old journal changed while it was being moved; nothing "
                    "was replaced.")
            entry["moved"] = True
    # A writer that ignores the gate could have made a new journal in the gap.
    # If it did, this attempt must not install a replacement beside it.
    for suffix in SIDECAR_SUFFIXES:
        if _sidecar_present(target, suffix):
            raise ReplaceError(
                "a new journal appeared beside the records while they were being "
                "put back; nothing was replaced.  Try again when the box is quiet.")
    state["phase"] = "sidecars-moved"
    _write_state(active, state)
    _fault("sidecars-moved")


def _install_candidate(target: Path, active: Path,
                       state: Dict[str, Any]) -> None:
    """Publish the checked copy by one rename, with no old journal beside it."""
    candidate = active / STAGING_DIR / CANDIDATE_NAME
    if not candidate.is_file():
        raise ReplaceError("the checked copy is missing; nothing was replaced.")
    if _sha256(candidate) != str(state.get("candidate", {}).get("sha256") or ""):
        raise ReplaceError(
            "the checked copy changed before it could be installed; nothing was "
            "replaced.")
    for suffix in SIDECAR_SUFFIXES:
        if _sidecar_present(target, suffix):
            raise ReplaceError(
                "the old journal is still beside the records; they were not "
                "replaced.")
    state["phase"] = "installing"
    _write_state(active, state)
    _fault("before-install")
    _replace_path(candidate, target)
    ark_atomic.fsync_dir(target.parent)
    state["phase"] = "installed"
    _write_state(active, state)
    _fault("after-install")


def _verify_installed(target: Path, state: Dict[str, Any]) -> None:
    """Prove the installed bytes are the checked bytes and SQLite can read them."""
    expected = str(state.get("candidate", {}).get("sha256") or "")
    if not target.is_file():
        raise ReplaceError("the records are missing after they were put back.")
    if _sha256(target) != expected:
        raise ReplaceError(
            "the records on the card are not the bytes that were checked.")
    has_sidecars = any(_sidecar_present(target, suffix)
                       for suffix in SIDECAR_SUFFIXES)
    ok, why = (_verify_live(target) if has_sidecars
               else _verify_immutable(target))
    if not ok:
        raise ReplaceError(
            "the new records do not open as a database (%s)." % why)
    if has_sidecars:
        # These appeared after the old journal was moved, so they belong to the
        # new file; keep them, with the owner the new file has.
        _adopt_sidecars(target, (state.get("original") or {}).get("main"))
    _fault("after-verify")


def _archive(active: Path, target: Path, label: str,
             state: Optional[Dict[str, Any]] = None) -> Optional[Path]:
    """Turn a finished (or abandoned) live folder into a dated evidence folder."""
    if not active.exists():
        return None
    ident = str((state or {}).get("id") or secrets.token_hex(4))
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    destination = active.with_name("%s%s%s-%s-%s" % (
        target.name, ARCHIVE_PREFIX, stamp, label, ident))
    counter = 1
    while destination.exists():
        destination = active.with_name("%s%s%s-%s-%s-%d" % (
            target.name, ARCHIVE_PREFIX, stamp, label, ident, counter))
        counter += 1
    try:
        os.replace(str(active), str(destination))
        ark_atomic.fsync_dir(target.parent)
    except OSError:
        return None
    return destination


def _tree_bytes(folder: Path) -> int:
    total = 0
    try:
        for root, _dirs, files in os.walk(str(folder)):
            for name in files:
                try:
                    total += os.stat(os.path.join(root, name)).st_size
                except OSError:
                    continue
    except OSError:
        return total
    return total


def _archive_kind(folder: Path) -> str:
    name = folder.name
    for kind in ("committed", "rolled-back", "failed", "abandoned",
                 "unreadable", "blocked"):
        if ("-" + kind + "-") in name:
            return kind
    return "failed"


def prune(target: Any, *, keep_committed: int = KEEP_COMMITTED,
          keep_failed: int = KEEP_FAILED,
          max_bytes: int = MAX_ARCHIVE_BYTES) -> List[Path]:
    """Delete old evidence, never a pending folder and never the newest set.

    Policy (documented in ``docs/ADMIN.md``): keep the newest
    ``keep_committed`` committed sets and the newest ``keep_failed`` sets of the
    other kinds **in total**; then, while the total is above ``max_bytes``, delete
    the oldest non-newest set.  An archive whose record cannot be read is kept: it is
    evidence precisely when it is not understood, so neither the count policy
    nor the byte ceiling removes it.
    """
    target = Path(target)
    folder = target.parent
    try:
        archives = [item for item in folder.iterdir()
                    if item.is_dir() and item.name.startswith(
                        target.name + ARCHIVE_PREFIX)]
    except OSError:
        return []
    archives.sort(key=lambda item: (item.stat().st_mtime, item.name)
                  if _safe_stat(item) else (0.0, item.name))
    groups: Dict[str, List[Path]] = {}
    kept_unreadable: List[Path] = []
    kept_pending: List[Path] = []
    for item in archives:
        if (item / BLOCK_NAME).exists():
            # An archived folder that still says "do not open" is never swept
            # away by a retention rule, however old it is.
            kept_pending.append(item)
            continue
        _state, problem = _read_state(item, target)
        if problem:
            kept_unreadable.append(item)
            continue
        groups.setdefault(_archive_kind(item), []).append(item)
    removed: List[Path] = []
    newest = archives[-1] if archives else None
    committed = groups.get("committed", [])
    others = [item for kind, items in groups.items() if kind != "committed"
              for item in items]
    others.sort(key=lambda item: (item.stat().st_mtime, item.name)
                if _safe_stat(item) else (0.0, item.name))
    for items, limit in ((committed, keep_committed), (others, keep_failed)):
        limit = max(1, limit)
        for old in items[:-limit] if len(items) > limit else []:
            if old == newest:
                continue
            if _remove_tree(old):
                removed.append(old)
    # A byte ceiling applies after the count policy.  The newest archive is
    # always kept; deleting it would remove the only evidence of the operation
    # that just happened.
    remaining = [item for item in archives if item.exists()]
    total = sum(_tree_bytes(item) for item in remaining)
    for item in remaining:
        if total <= max_bytes:
            break
        if item == newest or item in kept_unreadable or item in kept_pending:
            continue
        size = _tree_bytes(item)
        if _remove_tree(item):
            total -= size
            removed.append(item)
    # Storage-aware last pass: if the retained evidence has pushed the card past
    # the one reserve policy (``ark_space``), remove the oldest non-newest sets
    # until the reserve is clear again.  Deleting evidence frees space; it never
    # creates a second free-space rule.
    usable = _usable_bytes(folder)
    if usable is not None and usable < 0:
        for item in remaining:
            if usable >= 0:
                break
            if (item == newest or item in kept_unreadable
                    or item in kept_pending or not item.exists()):
                continue
            size = _tree_bytes(item)
            if _remove_tree(item):
                usable += size
                removed.append(item)
    return removed


def _safe_stat(path: Path) -> bool:
    try:
        return path.stat() is not None
    except OSError:
        return False


def _remove_tree(path: Path) -> bool:
    try:
        shutil.rmtree(str(path))
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
#  rollback
# ---------------------------------------------------------------------------
def _sidecar_record(state: Dict[str, Any], suffix: str) -> Optional[Dict[str, Any]]:
    value = state.get("original", {}).get("sidecars", {}).get(suffix)
    return value if isinstance(value, dict) else None


def _rollback(target: Path, active: Path, state: Dict[str, Any],
              *, problem: str) -> None:
    """Put the preserved original back, or raise and leave the folder blocked.

    The candidate (if it is at the target) is moved into the evidence folder, not
    deleted.  The original main and its journal are copied back — copies, so the
    evidence keeps the bytes that were saved — and then the result is checked
    with SQLite.  If the original itself cannot be read, this raises: a caller
    must not start a service on a file it could not verify, and must not throw
    away a candidate that may be the only readable copy.
    """
    state["phase"] = "rolling-back"
    state["problem"] = problem
    _write_state(active, state)
    _set_block(active)
    original = active / ORIGINAL_DIR
    replaced = active / REPLACED_DIR
    replaced.mkdir(parents=True, exist_ok=True, mode=0o700)
    original_record = state.get("original") or {}
    main_record = original_record.get("main") or {}
    expected = str(main_record.get("sha256") or "")

    # Anything at the target that is not the original is the candidate (or an
    # unknown file).  Move it aside; never delete it.
    if target.exists() and _is_regular(target):
        current = _sha256(target)
        if current != expected:
            destination = replaced / target.name
            try:
                os.replace(str(target), str(destination))
                ark_atomic.fsync_dir(replaced)
                ark_atomic.fsync_dir(target.parent)
            except OSError:
                # A copy keeps the candidate as evidence even when the rename is
                # refused.  If even that cannot be done, stop rather than
                # overwrite the only readable copy with the original.
                try:
                    _copy_file(target, destination, mode=0o600)
                    _fsync_file(destination)
                except OSError as exc:
                    raise ReplaceError(
                        "the new records could not be moved aside before the "
                        "original was put back (%s)." % exc) from exc
    # A candidate's own journal must not survive beside the original.  A journal
    # that was never moved out is the original's own and must stay where it is.
    for suffix in SIDECAR_SUFFIXES:
        sidecar = Path(str(target) + suffix)
        if not sidecar.exists() and not sidecar.is_symlink():
            continue
        entry = _sidecar_record(state, suffix)
        saved = original / str(entry.get("name") or (target.name + suffix)) \
            if entry else None
        if entry and saved is not None and not saved.is_file():
            continue
        destination = replaced / sidecar.name
        try:
            os.replace(str(sidecar), str(destination))
            ark_atomic.fsync_dir(replaced)
            ark_atomic.fsync_dir(target.parent)
        except OSError as exc:
            raise ReplaceError(
                "a journal beside the records could not be moved aside (%s); "
                "it was not deleted." % exc) from exc
    if main_record:
        if _target_hash(target) != expected:
            source = original / str(main_record.get("name") or target.name)
            if not source.is_file():
                raise ReplaceError(
                    "the saved original records are missing from the recovery "
                    "folder.")
            _install_copy(source, target,
                          mode=int(main_record.get("mode") or 0o640),
                          owner=main_record)
    elif target.exists():
        # There was no original database.  The candidate has already been moved
        # aside (or could not be); do not delete an unknown file.
        raise ReplaceError(
            "there was no database before the replacement, but a file is still "
            "at the target and was not moved aside.")
    # The index first, then the journal: every intermediate state is readable.
    for suffix in ("-shm", "-wal"):
        entry = _sidecar_record(state, suffix)
        if not entry:
            continue
        source = original / str(entry.get("name") or (target.name + suffix))
        live = target.with_name(target.name + suffix)
        if source.is_file():
            _install_copy(source, live, mode=0o600, owner=entry)
        elif live.is_file():
            # It was never moved (the failure happened before its turn).  It is
            # still the original only if its bytes are the ones recorded.
            if _sha256(live) != str(entry.get("sha256") or ""):
                raise ReplaceError(
                    "the journal beside the records is not the one that was "
                    "saved, and no saved copy is available.")
        else:
            raise ReplaceError(
                "the saved original journal (%s) is missing from the recovery "
                "folder." % source.name)
    if main_record:
        ok, why = _verify_live(target)
        if not ok:
            raise ReplaceError(
                "the original records could not be read after they were put back "
                "(%s)." % why)
        _adopt_sidecars(target, main_record)
    elif target.exists():
        raise ReplaceError(
            "there was no database before the replacement, and one is still at "
            "the target after the rollback.")
    # else: the original state was "no database", and the name is empty again.
    state["phase"] = "rolled-back"
    state["problem"] = problem
    _write_state(active, state)
    _clear_block(active)


def _failure(target: Path, active: Path, state: Dict[str, Any],
             exc: BaseException) -> Replacement:
    """Turn a failure into an honest outcome: untouched, rolled back, or blocked."""
    phase = str(state.get("phase") or "")
    why = str(exc) or exc.__class__.__name__
    if phase in PRE_DESTRUCTIVE_PHASES or not (active / ORIGINAL_DIR).exists():
        # Nothing beside the target was touched.  This is the only case in which
        # "nothing was changed" is true about the records.
        state["phase"] = "failed"
        state["problem"] = why
        try:
            _write_state(active, state)
        except OSError:                       # pragma: no cover - card full
            pass
        _clear_block(active)
        evidence = _archive(active, target, "failed", state)
        try:
            prune(target)
        except OSError:                       # pragma: no cover - best effort
            pass
        state["status"] = "refused"
        state["replaced"] = False
        state["safe"] = True
        state["evidence"] = str(evidence or active)
        return Replacement(
            "refused", False, False, True, evidence or active,
            "the records were left exactly as they were: %s" % why, state)
    try:
        _rollback(target, active, state, problem=why)
    except Exception as rollback_exc:        # noqa: BLE001 — report, never hide
        state["phase"] = "blocked"
        state["problem"] = "%s; putting the original back also failed: %s" % (
            why, rollback_exc)
        try:
            _write_state(active, state)
            _set_block(active)
        except OSError:                       # pragma: no cover - card full
            pass
        state["status"] = "blocked"
        state["replaced"] = target.exists()
        state["safe"] = False
        state["evidence"] = str(active)
        return Replacement(
            "blocked", True, target.exists(), False, active,
            "the replacement failed (%s) and the original could not be put back "
            "(%s).  The box must stay stopped; the recovery record is at %s."
            % (why, rollback_exc, active), state)
    _clear_block(active)
    evidence = _archive(active, target, "rolled-back", state)
    try:
        prune(target)
    except OSError:                           # pragma: no cover - best effort
        pass
    state["status"] = "refused"
    state["replaced"] = False
    state["safe"] = True
    state["evidence"] = str(evidence or active)
    return Replacement(
        "refused", True, False, True, evidence or active,
        "the replacement failed (%s); the original records were put back and "
        "are safe to open.  The evidence is at %s." % (why, evidence or active),
        state)


def _replace_under_gate(source: Path, target: Path, reason: str,
                        state_out: Optional[Dict[str, Any]]) -> Replacement:
    """The sequence itself.  The caller holds the turnstile and the gate."""
    target = Path(target)
    source = Path(source)
    active = active_dir(target)
    track = state_out if state_out is not None else {}
    track.update({"replaced": False, "safe": False, "status": "", "evidence": ""})
    if active.exists():
        outcome = _recover_locked(target, active)
        if active.exists():
            said = ("a previous replacement of these records has not been "
                    "resolved (%s); nothing was changed this time."
                    % (outcome.said or outcome.status))
            track["status"] = "blocked"
            track["safe"] = False
            track["evidence"] = str(active)
            return Replacement("blocked", False, False, False, active, said, track)
    if not target.parent.exists():
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            track.update(status="refused", safe=True)
            return Replacement(
                "refused", False, False, True, None,
                "the folder for the records could not be made (%s); nothing was "
                "changed." % exc, track)
    try:
        prune(target)                         # old evidence must not crowd out the new
    except OSError:                           # pragma: no cover - best effort
        pass
    try:
        _check_room(source, target)
    except ReplaceError as exc:
        track.update(status="refused", safe=True)
        return Replacement("refused", False, False, True, None, str(exc), track)
    try:
        # The folder itself is 0755 so the ``pi`` portal can *stat* the BLOCK
        # marker inside it; the original, staging and replaced folders below are
        # 0700 and the files are 0600, so it can see that a replacement exists
        # without reading the records.
        active.mkdir(parents=True, exist_ok=False, mode=0o755)
    except FileExistsError:
        # It appeared between the check and the mkdir; the startup half owns it.
        track.update(status="blocked", safe=False)
        return Replacement(
            "blocked", False, False, False, active,
            "another replacement of these records started at the same moment; "
            "nothing was changed by this one.", track)
    except OSError as exc:
        track.update(status="refused", safe=True)
        return Replacement(
            "refused", False, False, True, None,
            "the recovery folder could not be made (%s); nothing was changed."
            % exc, track)
    state = _state_of(target)
    state["reason"] = reason
    state["source"] = str(source)
    state["phase"] = "preparing"
    try:
        _write_state(active, state)
        _stage_candidate(source, active, target, state)
        _preserve_original(target, active, state)
        _move_sidecars(target, active, state)
        _install_candidate(target, active, state)
        _verify_installed(target, state)
        state["phase"] = "verified"
        try:
            _write_state(active, state)
        except OSError as exc:                # pragma: no cover - card full
            state["problem"] = "the verified step could not be recorded: %s" % exc
        _clear_block(active)
        state["phase"] = "committed"
        try:
            _write_state(active, state)
        except OSError as exc:                # pragma: no cover - card full
            state["problem"] = "the committed step could not be recorded: %s" % exc
        _fault("committed")
        evidence = _archive(active, target, "committed", state)
        try:
            prune(target)
        except OSError:                       # pragma: no cover - best effort
            pass
        track.update(status="ok", replaced=True, safe=True,
                     evidence=str(evidence or active))
        return Replacement(
            "ok", True, True, True, evidence or active,
            "the new records are in place and were checked; the old records are "
            "kept at %s." % (evidence or active), track)
    except BaseException as exc:             # noqa: BLE001 — classify every failure
        # Ctrl-C and SystemExit are handled like any other interruption while the
        # files are still mutable: put the coherent original back when possible,
        # record what happened, then let the caller's interrupt propagate.  A
        # SIGKILL cannot be caught; the durable record and BLOCK marker cover it.
        result = _failure(target, active, state, exc)
        if state_out is not None:
            state_out.update(status=result.status, replaced=result.replaced,
                             safe=result.safe,
                             evidence=str(result.evidence or ""))
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        return result


# ---------------------------------------------------------------------------
#  the public replacement door
# ---------------------------------------------------------------------------
def replace(source: Any, target: Any, *, reason: str = "",
            state: Optional[Dict[str, Any]] = None,
            repair_wait: Optional[float] = None,
            gate_wait: Optional[float] = None) -> Replacement:
    """Replace ``target`` with a checked copy of ``source``, crash-safely.

    The turnstile and the exclusive gate are taken **before the original is
    read**.  When they cannot be had, this returns ``busy`` and nothing is
    changed.  The caller may already hold them (``heal_sqlite`` and
    ``ark-restore`` do); the nested maintenance calls are no-ops by design.
    """
    target = Path(target)
    with maintenance.repairing(target, wait=repair_wait) as turn:
        if not turn.held:
            if "could not be opened" in (turn.reason or ""):
                said = ("this card will not let a maintenance lock be made, so the "
                        "records were not replaced.  Nothing was changed.")
                if state is not None:
                    state.update(status="refused", replaced=False, safe=True)
                return Replacement("refused", False, False, True, None, said,
                                   state if state is not None else {})
            said = ("another repair is working on these records, so they were not "
                    "replaced: %s" % (turn.reason or "busy"))
            if state is not None:
                state.update(status="busy", replaced=False, safe=False)
            return Replacement("busy", False, False, False, None, said,
                               state if state is not None else {})
        with maintenance.maintain(target, wait=gate_wait) as lease:
            if not lease.held:
                if "could not be opened" in (lease.reason or ""):
                    said = ("this card will not let a maintenance lock be made, so "
                            "the records were not replaced.  Nothing was changed.")
                    if state is not None:
                        state.update(status="refused", replaced=False, safe=True)
                    return Replacement("refused", False, False, True, None, said,
                                       state if state is not None else {})
                said = ("another program still has the records open, so they were "
                        "not replaced: %s.  Nothing was changed."
                        % (lease.reason or "busy"))
                if state is not None:
                    state.update(status="busy", replaced=False, safe=False)
                return Replacement("busy", False, False, False, None, said,
                                   state if state is not None else {})
            return _replace_under_gate(Path(source), target, reason, state)


# ---------------------------------------------------------------------------
#  the startup half
# ---------------------------------------------------------------------------
def _recover_description(target: Path, active: Path) -> Recovery:
    state, problem = _read_state(active, target)
    if state is None:
        return Recovery(
            "would", block_path(target).exists(),
            "a replacement record exists beside %s but cannot be read (%s); a "
            "startup would leave it for a person rather than guess." % (
                target.name, problem), active)
    phase = str(state.get("phase") or "unknown")
    return Recovery(
        "would", block_path(target).exists(),
        "a replacement of %s was interrupted at %s; a startup would resolve it "
        "before services open the records." % (target.name, phase), active)


def _target_hash(target: Path) -> str:
    try:
        return _sha256(target) if target.is_file() else ""
    except OSError:
        return ""


def _target_is_candidate(target: Path, state: Dict[str, Any]) -> bool:
    expected = str(state.get("candidate", {}).get("sha256") or "")
    return bool(expected) and _target_hash(target) == expected


def _target_is_original(target: Path, state: Dict[str, Any]) -> bool:
    expected = str((state.get("original") or {}).get("main", {}).get("sha256") or "")
    if not expected:
        return not (state.get("original") or {}).get("existed", False) \
            and not target.exists()
    return _target_hash(target) == expected


def _archive_trusted(target: Path, active: Path,
                     state: Dict[str, Any]) -> Recovery:
    """Commit a replacement whose bytes were already verified.

    Used only for the ``verified``/``committed`` phases: the hash was checked
    before that step was recorded, and anything written since is a legitimate
    later write (services are allowed to use the file once ``BLOCK`` is gone).
    Re-hashing would call such a file "not the candidate" and roll it back.
    """
    ok, why = _verify_live(target)
    if not ok:
        raise ReplaceError(
            "the installed records do not open as a database (%s)." % why)
    _adopt_sidecars(target, (state.get("original") or {}).get("main"))
    state["phase"] = "committed"
    _write_state(active, state)
    _clear_block(active)
    evidence = _archive(active, target, "committed", state)
    prune(target)
    return Recovery(
        "committed", False,
        "an interrupted replacement of %s was already verified; it is finished "
        "and the old records are kept at %s." % (
            target.name, evidence or active), evidence)


def _finish_commit(target: Path, active: Path,
                   state: Dict[str, Any]) -> Recovery:
    if not _target_is_candidate(target, state):
        raise ReplaceError(
            "the installed records are not the bytes that were checked.")
    has_sidecars = any(_sidecar_present(target, suffix)
                       for suffix in SIDECAR_SUFFIXES)
    ok, why = (_verify_live(target) if has_sidecars
               else _verify_immutable(target))
    if not ok:
        raise ReplaceError(
            "the installed records do not open as a database (%s)." % why)
    if has_sidecars:
        _adopt_sidecars(target, (state.get("original") or {}).get("main"))
    state["phase"] = "verified"
    _write_state(active, state)
    _clear_block(active)
    state["phase"] = "committed"
    _write_state(active, state)
    evidence = _archive(active, target, "committed", state)
    prune(target)
    return Recovery("committed", False,
                    "an interrupted replacement of %s was finished; the new "
                    "records are in place and were checked.  The old records are "
                    "kept at %s." % (target.name, evidence or active), evidence)


def _recover_locked(target: Path, active: Path) -> Recovery:
    """Resolve an interrupted replacement.  The caller holds both locks."""
    state, problem = _read_state(active, target)
    if state is None:
        return _recover_unreadable(target, active, problem)
    phase = str(state.get("phase") or "unknown")
    if phase in ("rolled-back", "failed", "abandoned"):
        evidence = _archive(active, target, phase, state)
        _clear_block(active)
        prune(target)
        return Recovery(phase, False,
                        "an interrupted replacement of %s was already resolved "
                        "(%s); its evidence is at %s." % (
                            target.name, phase, evidence or active), evidence)
    if phase == "blocked":
        if not (state.get("original") or {}).get("main"):
            # There is no saved original to put back.  A rollback would move the
            # target into evidence and leave the live name empty; leave it where
            # it is for a person to inspect instead.
            return Recovery(
                "blocked", True,
                "the replacement of %s is blocked and has no saved original to put "
                "back; the records were left where they are.  The recovery record "
                "is at %s and the box must stay stopped."
                % (target.name, active), active)
        # Try one more time: the reason it failed may have gone away (a card
        # that was full, a file that was missing).
        try:
            _rollback(target, active, state,
                      problem=str(state.get("problem") or "blocked"))
        except Exception as exc:              # noqa: BLE001 — remain blocked
            state["problem"] = str(exc)
            try:
                _write_state(active, state)
                _set_block(active)
            except OSError:
                pass
            return Recovery("blocked", True,
                            "the replacement of %s is still blocked (%s); the "
                            "recovery record is at %s and the box must stay "
                            "stopped." % (target.name, exc, active), active)
        evidence = _archive(active, target, "rolled-back", state)
        prune(target)
        return Recovery("rolled-back", False,
                        "the blocked replacement of %s was put back; the original "
                        "records are in place and safe to open.  Evidence: %s."
                        % (target.name, evidence or active), evidence)
    try:
        if phase in ("preparing", "staged", "original-preserved"):
            if _target_is_candidate(target, state):
                if any(_sidecar_present(target, suffix)
                       for suffix in SIDECAR_SUFFIXES):
                    raise ReplaceError(
                        "the checked copy is sitting beside a journal that was "
                        "not part of the replacement; it is not safe to commit.")
                return _finish_commit(target, active, state)
            # The original journal and main file were never moved.  Whatever
            # state they are in (a self-heal candidate exists precisely because
            # the original may be damaged), they are exactly as this attempt
            # found them; set the attempt aside and let the normal repair decide.
            # A service that opened the file between the crash and this recovery
            # may have checkpointed its WAL; that is still the original set, and
            # it is not a reason to block.
            evidence = _archive(active, target, "abandoned", state)
            _clear_block(active)
            prune(target)
            return Recovery(
                "abandoned", False,
                "an interrupted replacement of %s had not touched the records; "
                "the attempt was set aside at %s." % (
                    target.name, evidence or active), evidence)
        if phase in ("moving-sidecars", "sidecars-moved", "installing"):
            if _target_is_candidate(target, state):
                if any(_sidecar_present(target, suffix)
                       for suffix in SIDECAR_SUFFIXES):
                    raise ReplaceError(
                        "the checked copy is sitting beside a journal that was "
                        "not part of the replacement; it is not safe to commit.")
                return _finish_commit(target, active, state)
            # The original main is still at the target (or the target is absent
            # for a first install); put its journal back.
            _rollback(target, active, state,
                      problem="the replacement was interrupted at %s" % phase)
            evidence = _archive(active, target, "rolled-back", state)
            prune(target)
            return Recovery(
                "rolled-back", False,
                "an interrupted replacement of %s was put back; the original "
                "records and their journal are in place.  Evidence: %s." % (
                    target.name, evidence or active), evidence)
        if phase == "rolling-back":
            # The previous process had already decided to put the original back.
            # Retry that decision; it is idempotent and re-checks the result.
            _rollback(target, active, state,
                      problem=str(state.get("problem") or "rolling back"))
            evidence = _archive(active, target, "rolled-back", state)
            prune(target)
            return Recovery(
                "rolled-back", False,
                "an interrupted rollback of %s was finished; the original "
                "records are in place.  Evidence: %s." % (
                    target.name, evidence or active), evidence)
        if phase in ("verified", "committed"):
            # These phases mean the installed bytes were already checked.  A
            # service may have written to the target since (or a checkpoint may
            # have changed the main file); re-hashing would mistake that for a
            # different database and roll it back.  Ask SQLite, not the old hash.
            if target.is_file():
                ok, _why = _verify_live(target)
                _adopt_sidecars(target, (state.get("original") or {}).get("main"))
                if ok:
                    return _archive_trusted(target, active, state)
            _rollback(target, active, state,
                      problem="the checked records could not be opened at %s" % phase)
            evidence = _archive(active, target, "rolled-back", state)
            prune(target)
            return Recovery(
                "rolled-back", False,
                "an interrupted replacement of %s could not be verified at %s; "
                "the original records were put back.  Evidence: %s." % (
                    target.name, phase, evidence or active), evidence)
        if phase == "installed":
            if _target_is_candidate(target, state):
                return _finish_commit(target, active, state)
            _rollback(target, active, state,
                      problem="the replacement was interrupted at %s" % phase)
            evidence = _archive(active, target, "rolled-back", state)
            prune(target)
            return Recovery(
                "rolled-back", False,
                "an interrupted replacement of %s did not leave the checked "
                "bytes in place; the original records were put back.  Evidence: "
                "%s." % (target.name, evidence or active), evidence)
        # Unknown phase: treat the checked copy as a candidate, otherwise put
        # the original back.  Never guess that a file is safe to start.
        if _target_is_candidate(target, state):
            return _finish_commit(target, active, state)
        if _target_is_original(target, state):
            ok, why = (True, "absent") if not target.exists() else _verify_live(target)
            if not ok:
                raise ReplaceError(
                    "the original records do not open (%s)." % why)
            _adopt_sidecars(target, (state.get("original") or {}).get("main"))
            evidence = _archive(active, target, "abandoned", state)
            _clear_block(active)
            prune(target)
            return Recovery(
                "abandoned", False,
                "an interrupted replacement of %s left the original records in "
                "place; the attempt was set aside at %s." % (
                    target.name, evidence or active), evidence)
        _rollback(target, active, state,
                  problem="the replacement stopped at an unknown step")
        evidence = _archive(active, target, "rolled-back", state)
        prune(target)
        return Recovery(
            "rolled-back", False,
            "an interrupted replacement of %s was put back; the original records "
            "are in place.  Evidence: %s." % (
                target.name, evidence or active), evidence)
    except Exception as exc:                  # noqa: BLE001 — blocked is an outcome
        state["phase"] = "blocked"
        state["problem"] = str(exc)
        try:
            _write_state(active, state)
            _set_block(active)
        except OSError:
            pass
        return Recovery(
            "blocked", True,
            "the interrupted replacement of %s could not be resolved (%s).  The "
            "original records and the checked copy are kept at %s; the box must "
            "stay stopped." % (target.name, exc, active), active)


def _recover_unreadable(target: Path, active: Path, problem: str) -> Recovery:
    """No readable record: keep everything, restore only if that is provably safe."""
    original = active / ORIGINAL_DIR
    main = original / target.name
    if target.is_file():
        ok, why = _verify_live(target)
        _adopt_sidecars(target)
        if ok:
            # The record is lost, but the database and whatever sidecars are
            # beside it open together.  It may be an early folder (nothing was
            # moved), or a complete install whose record was torn; either way it
            # is readable, and the evidence folder keeps the original copies for
            # a person.  Do not guess that it is corrupt and do not delete it.
            #
            # One case is not safe to leave: the target is not the saved original
            # and a journal is beside it.  Without the record we cannot prove that
            # journal belongs to this file, so put the coherent original back
            # instead of guessing.
            saved_sidecars = any(
                (original / (target.name + suffix)).is_file()
                for suffix in SIDECAR_SUFFIXES)
            suspect = (saved_sidecars
                       or (main.is_file()
                           and _sha256(main) != _target_hash(target)
                           and any(_sidecar_present(target, suffix)
                                   for suffix in SIDECAR_SUFFIXES)))
            if suspect:
                pass
            else:
                evidence = _archive(active, target, "unreadable")
                _clear_block(active)
                try:
                    prune(target)
                except OSError:               # pragma: no cover - best effort
                    pass
                return Recovery(
                    "resolved", False,
                    "the replacement record beside %s could not be read (%s), but "
                    "the records themselves open cleanly; the evidence was kept at "
                    "%s." % (target.name, problem, evidence or active), evidence)
    if main.is_file():
        # We have what looks like a saved original.  Put it back only through
        # the same verified rollback path.
        state = _state_of(target)
        state["phase"] = "rolling-back"
        state["original"] = {
            "existed": True,
            "main": {"name": main.name, "sha256": _sha256(main),
                     "size": _size(main), **_owner_record(target)},
            "sidecars": {},
        }
        for suffix in SIDECAR_SUFFIXES:
            sidecar = original / (target.name + suffix)
            if sidecar.is_file():
                state["original"]["sidecars"][suffix] = {
                    "name": sidecar.name, "sha256": _sha256(sidecar),
                    "size": _size(sidecar), "moved": True,
                    **_owner_record(sidecar),
                }
        try:
            _rollback(target, active, state,
                      problem="the replacement record could not be read (%s)"
                              % problem)
        except Exception as exc:              # noqa: BLE001
            state["phase"] = "blocked"
            state["problem"] = str(exc)
            try:
                _write_state(active, state)
                _set_block(active)
            except OSError:
                pass
            return Recovery(
                "blocked", True,
                "the replacement record beside %s could not be read (%s) and the "
                "saved original could not be put back (%s); the box must stay "
                "stopped." % (target.name, problem, exc), active)
        evidence = _archive(active, target, "rolled-back", state)
        try:
            prune(target)
        except OSError:                       # pragma: no cover - best effort
            pass
        return Recovery(
            "rolled-back", False,
            "the replacement record beside %s could not be read (%s); the saved "
            "original was put back and checked.  Evidence: %s." % (
                target.name, problem, evidence or active), evidence)
    # Nothing can be proved.  Keep the folder and the marker; a person decides.
    try:
        state = _state_of(target)
        state["phase"] = "blocked"
        state["problem"] = "the replacement record could not be read (%s)" % problem
        _write_state(active, state)
        _set_block(active)
    except OSError:
        pass
    return Recovery(
        "blocked", True,
        "an interrupted replacement of %s cannot be resolved: its record could "
        "not be read (%s) and no saved original is available.  The evidence is "
        "kept at %s and the box must stay stopped." % (
            target.name, problem, active), active)


def recover_pending(target: Any, *, dry_run: bool = False,
                    repair_wait: Optional[float] = None,
                    gate_wait: Optional[float] = None) -> Recovery:
    """Resolve an interrupted replacement before normal services open the file.

    A dry run changes nothing and takes no lock.  A real run takes the turnstile
    and the exclusive gate, so it cannot race a live repair or a reader; when it
    cannot have them, it answers ``deferred`` and leaves the evidence (and any
    ``BLOCK`` marker) exactly where it is.
    """
    target = Path(target)
    active = active_dir(target)
    if not active.is_dir():
        if not dry_run:
            try:
                prune(target)
            except OSError:                   # pragma: no cover - best effort
                pass
        return Recovery("none", False, "", None)
    if dry_run:
        return _recover_description(target, active)
    with maintenance.repairing(target, wait=repair_wait) as turn:
        if not turn.held:
            return Recovery(
                "deferred", block_path(target).exists(),
                "another repair is working on the records, so the interrupted "
                "replacement of %s was left for the next pass." % target.name,
                active)
        with maintenance.maintain(target, wait=gate_wait) as lease:
            if not lease.held:
                return Recovery(
                    "deferred", block_path(target).exists(),
                    "another program still has the records open, so the "
                    "interrupted replacement of %s was left for the next pass."
                    % target.name, active)
            return _recover_locked(target, active)
