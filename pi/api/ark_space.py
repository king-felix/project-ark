"""Project ARK — one policy for how full the card may get.

Every part of the box that makes the card grow (a shared file, a book, a game, a
video, a collection, a model, a backup) used to carry its own idea of "enough
room": half a gigabyte here, sixty-four megabytes there, twelve gigabytes in a
shell script.  Those numbers were not wrong individually — they were three
policies pretending to be one, and none of them knew what the others had already
promised.  This module is the single answer.

The rule
--------
**The application may use the card until 7 % of its capacity is left, or 2 GiB,
whichever is larger.**  The percentage is the owner's (`ARK_RESERVE_PERCENT`,
default 7) and the floor is fixed: a small card still needs room to breathe.

Two details make that honest:

* the capacity is the filesystem **that is actually receiving the bytes**, asked
  with ``statvfs`` at the moment of use — never a nominal SD-card size;
* the free figure is ``f_bavail``, the blocks an ordinary account may write.
  ext4 keeps a slice back for root, and counting that slice as room the box can
  use would promise space that a write by ``pi`` cannot have.

This is an **application admission policy**, not a partition and not a quota.
Something outside the box (a package, a log, a person with a shell) can still
fill the card; the reserve cannot stop that.  What it can do is refuse to be the
thing that crosses the line, and say so in a sentence that names the shortfall.

Reservations
------------
A check made once is not enough when two jobs run together: both can look at the
same free bytes, both can be admitted, and the second one crosses the reserve.
So an operation whose size is known *before* it starts (a conversion output, a
collection being fetched, a model, a backup) takes a **reservation** first, in
the database every process already shares.  Admission asks:

    free - reserve - (what other jobs have promised but not yet written) >= need

``written`` is tracked per reservation and subtracted from the promise as bytes
reach the disk, so nothing is counted twice: ``statvfs`` already reflects the
bytes that exist, and the ledger only adds the part that does not exist yet.

A reservation is durable and reconcilable: it carries a heartbeat, so a worker
that is killed mid-job leaves a row that the next start-up can recognise as
stale and release.  Nothing is deleted from the card to make room for another
job — the answer to "no room" is a refusal, never a silent cleanup.
"""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path

import ark_db

#: The owner's reserve, as a percentage of the filesystem's own capacity.
DEFAULT_PERCENT = 7.0
#: ...but never less than this, however small the card is.
FLOOR_BYTES = 2 * 1024 ** 3
#: A promised byte that nobody has refreshed for this long is abandoned.
STALE_SECONDS = 15 * 60
#: A percentage outside this range is a typo, not a policy.
PERCENT_MIN, PERCENT_MAX = 0.0, 50.0

CONFIG_ENV = "ARK_SPACE_CONFIG"
ROOT_ENV = "ARK_ROOT"

#: One process may touch the ledger from several threads; the DB does the locking.
_TABLE_READY = False


class Refused(Exception):
    """A refusal a person can read and act on. Never a stack trace.

    Defined here rather than in ``ark_storage`` because the reserve is the thing
    that refuses, and ``ark_storage`` re-exports this name so every caller that
    already catches ``ark_storage.Refused`` keeps catching it.
    """


# ---------------------------------------------------------------------------
#  the policy
# ---------------------------------------------------------------------------
def config_path() -> Path:
    """The operator's environment file — the same one the installer writes.

    ``/etc/ark/ark.env`` is outside ``ARK_ROOT`` on purpose, so it is named
    absolutely; a test or an unusual layout can point ``ARK_SPACE_CONFIG`` at its
    own file instead.
    """
    value = os.environ.get(CONFIG_ENV) or ""
    if value:
        return Path(value)
    return Path("/etc/ark/ark.env")


def _percent_from(text: str) -> float | None:
    """The last ``ARK_RESERVE_PERCENT=`` line in an env file, if it is sane."""
    found = None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, raw = line.partition("=")
        if key.strip() != "ARK_RESERVE_PERCENT":
            continue
        raw = raw.strip().strip('"').strip("'").rstrip("%")
        try:
            found = float(raw)
        except ValueError:
            continue
    return found


def reserve_percent() -> float:
    """The configured percentage, with a bad value falling back — never crashing.

    The environment wins, then the operator's ``ark.env``, then 7 %.  A value
    outside 0…50 is treated as absent: a person who typed ``ARK_RESERVE_PERCENT=%``
    should get the default, not an unbounded refusal of everything.
    """
    raw = os.environ.get("ARK_RESERVE_PERCENT")
    value = None
    if raw not in (None, ""):
        try:
            value = float(str(raw).strip().rstrip("%"))
        except ValueError:
            value = None
    if value is None:
        try:
            value = _percent_from(config_path().read_text(encoding="utf-8"))
        except OSError:
            value = None
    if value is None:
        value = DEFAULT_PERCENT
    if not (PERCENT_MIN <= value <= PERCENT_MAX):
        value = DEFAULT_PERCENT
    return float(value)


def policy() -> dict:
    """The policy as data, for a page or a receipt."""
    return {"percent": reserve_percent(), "floor_bytes": FLOOR_BYTES}


# ---------------------------------------------------------------------------
#  measuring one filesystem
# ---------------------------------------------------------------------------
def _stats(path: Path | str) -> os.statvfs_result | None:
    probe = Path(path)
    # A path that does not exist yet is measured by its nearest real parent:
    # a folder the installer has not created is not a reason to invent numbers.
    for candidate in (probe, *probe.parents):
        try:
            return os.statvfs(str(candidate))
        except OSError:
            continue
    return None


def facts(path: Path | str | None = None) -> dict:
    """What this filesystem holds, and what the policy keeps back.

    ``None`` anywhere means the question could not be asked; every page prints
    *not measured yet* rather than zero, the same `None` rule as the rest of the
    box.
    """
    target = Path(path) if path else Path(os.environ.get(ROOT_ENV, "/srv/ark"))
    stats = _stats(target)
    if stats is None:
        return {"path": str(target), "device": None, "total_bytes": None,
                "free_bytes": None, "reserve_bytes": None, "usable_bytes": None,
                "percent": reserve_percent()}
    total = int(stats.f_blocks) * int(stats.f_frsize)
    free = int(stats.f_bavail) * int(stats.f_frsize)
    reserve = reserve_for(total)
    return {"path": str(target), "device": device_key(target),
            "total_bytes": total, "free_bytes": free, "reserve_bytes": reserve,
            "usable_bytes": max(0, free - reserve), "percent": reserve_percent()}


def reserve_for(total_bytes: int) -> int:
    """``max(percent × capacity, 2 GiB)`` — never more than the card itself."""
    total = max(0, int(total_bytes or 0))
    wanted = max(int(total * reserve_percent() / 100.0), FLOOR_BYTES)
    return min(wanted, total)


def free_bytes(path: Path | str | None = None) -> int:
    value = facts(path)["free_bytes"]
    return int(value or 0)


def reserve_bytes(path: Path | str | None = None) -> int:
    value = facts(path)["reserve_bytes"]
    return int(value or 0)


def usable_bytes(path: Path | str | None = None) -> int:
    value = facts(path)["usable_bytes"]
    return int(value or 0)


def device_key(path: Path | str | None = None) -> int:
    """A stable identity for the filesystem, so reservations are per-card.

    ``st_dev`` is the kernel's own answer and needs no parsing; the fallback to a
    hash of the path keeps a platform without it working rather than crashing.
    """
    probe = Path(path) if path else Path(os.environ.get(ROOT_ENV, "/srv/ark"))
    try:
        return int(os.stat(str(probe)).st_dev)
    except OSError:
        return 0


# ---------------------------------------------------------------------------
#  the shared ledger
# ---------------------------------------------------------------------------
_CREATE = """CREATE TABLE IF NOT EXISTS space_reservations (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        REAL NOT NULL,
    heartbeat REAL NOT NULL,
    owner     TEXT NOT NULL,
    device    INTEGER NOT NULL DEFAULT 0,
    path      TEXT NOT NULL DEFAULT '',
    label     TEXT NOT NULL DEFAULT '',
    bytes     INTEGER NOT NULL DEFAULT 0,
    written   INTEGER NOT NULL DEFAULT 0
)"""


def _ensure(conn) -> None:
    """Make sure the ledger exists — by *asking*, not by writing.

    ``CREATE TABLE IF NOT EXISTS`` looks harmless and is a write: run from a
    reader while another connection is publishing a film, it waits for the write
    lock and then fails.  The migration creates this table already; this is the
    safety net for a script that runs before the portal ever has, so it asks the
    catalogue first and only writes when the table is genuinely absent.
    """
    global _TABLE_READY
    if _TABLE_READY:
        return
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
        " AND name = 'space_reservations'").fetchone()
    if row is None:
        conn.execute(_CREATE)
        conn.execute("CREATE INDEX IF NOT EXISTS space_reservations_device"
                     " ON space_reservations(device)")
    _TABLE_READY = True


def committed(conn, device: int) -> int:
    """Bytes promised to running jobs on this filesystem that are not on disk yet."""
    _ensure(conn)
    row = conn.execute(
        "SELECT COALESCE(SUM(MAX(0, bytes - written)), 0) FROM space_reservations"
        " WHERE device = ?", (int(device),)).fetchone()
    return int(row[0] or 0)


def committed_for(path: Path | str | None = None, conn=None) -> int:
    """Bytes promised on this filesystem, readable from anywhere — 0 if unknown."""
    device = device_key(path)
    try:
        if conn is not None:
            return committed(conn, device)
        with ark_db.read() as own:
            return committed(own, device)
    except Exception:
        return 0


def account(path: Path | str | None = None, conn=None) -> dict:
    """Everything an owner needs to see: total, free, reserve, promised, usable."""
    base = facts(path)
    device = device_key(path)
    promised = 0
    if conn is not None:
        promised = committed(conn, device)
    else:
        try:
            with ark_db.read() as own:
                promised = committed(own, device)
        except Exception:
            promised = 0
    return {
        "path": base["path"],
        "total_bytes": base["total_bytes"],
        "free_bytes": base["free_bytes"],
        "reserve_bytes": base["reserve_bytes"],
        "committed_bytes": promised,
        "usable_bytes": max(0, int(base["usable_bytes"] or 0) - promised),
        "percent": base["percent"],
        "breached": (base["free_bytes"] is not None
                     and base["reserve_bytes"] is not None
                     and base["free_bytes"] < base["reserve_bytes"]),
    }


def _refusal(need: int, usable: int, path: Path | str) -> Refused:
    return Refused(
        f"There is not enough room on the card for that: it needs "
        f"{_size_words(need)} and {_size_words(max(0, usable))} may be used. "
        "Delete something you no longer need, then try again.")


def _size_words(n: int) -> str:
    n = int(n or 0)
    for unit, size in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("kB", 1024)):
        if abs(n) >= size:
            return f"{n / size:.1f} {unit}"
    return f"{n} bytes"


def reserve(path: Path | str, need: int, *, owner: str, label: str = "",
            conn=None) -> int:
    """Promise ``need`` bytes on this filesystem, or refuse.

    The promise is a row any process can see, so two conversions started a second
    apart cannot both be told there is room for them.
    """
    need = max(0, int(need or 0))
    device = device_key(path)
    if conn is not None:
        return _reserve(conn, path, device, need, owner, label)
    with ark_db.tx() as own:
        return _reserve(own, path, device, need, owner, label)


def _reserve(conn, path, device: int, need: int, owner: str, label: str) -> int:
    _ensure(conn)
    stats = facts(path)
    free = int(stats["free_bytes"] or 0)
    reserve = int(stats["reserve_bytes"] or 0)
    promised = committed(conn, device)
    usable = max(0, free - reserve - promised)
    if need > usable:
        raise _refusal(need, usable, path)
    now = time.time()
    try:
        cur = conn.execute(
            "INSERT INTO space_reservations (ts, heartbeat, owner, device, path, label,"
            " bytes, written) VALUES (?, ?, ?, ?, ?, ?, ?, 0)",
            (now, now, str(owner), device, str(path), str(label)[:200], need))
    except Exception:
        # A refusal rolls the whole transaction back — including the CREATE that
        # `_ensure` may just have run.  Forget that it was done, so the next call
        # checks again instead of trusting a table that no longer exists.
        global _TABLE_READY
        _TABLE_READY = False
        raise
    return int(cur.lastrowid)


def touch(conn, reservation_id: int, written: int | None = None) -> None:
    """Refresh the heartbeat, and optionally how much of the promise is on disk."""
    _ensure(conn)
    if written is None:
        conn.execute("UPDATE space_reservations SET heartbeat = ? WHERE id = ?",
                     (time.time(), int(reservation_id)))
    else:
        conn.execute("UPDATE space_reservations SET heartbeat = ?,"
                     " written = MAX(written, ?) WHERE id = ?",
                     (time.time(), max(0, int(written)), int(reservation_id)))


def release(conn, reservation_id: int | None) -> None:
    if not reservation_id:
        return
    _ensure(conn)
    conn.execute("DELETE FROM space_reservations WHERE id = ?", (int(reservation_id),))


def release_owner(owner: str, conn=None) -> int:
    """Drop every promise one owner made — a clean finish, or a cancellation."""
    if conn is not None:
        _ensure(conn)
        return int(conn.execute("DELETE FROM space_reservations WHERE owner = ?",
                                (str(owner),)).rowcount or 0)
    with ark_db.tx() as own:
        _ensure(own)
        return int(own.execute("DELETE FROM space_reservations WHERE owner = ?",
                               (str(owner),)).rowcount or 0)


def reconcile(max_age: int = STALE_SECONDS, conn=None) -> int:
    """Release promises whose worker has not spoken for ``max_age`` seconds.

    Called when the portal and the media worker start.  A row left by a power cut
    would otherwise hold space that nothing is using, and the box would refuse
    work it can perfectly well do.
    """
    cut = time.time() - max(60, int(max_age))
    if conn is not None:
        _ensure(conn)
        return int(conn.execute("DELETE FROM space_reservations WHERE heartbeat < ?",
                                (cut,)).rowcount or 0)
    with ark_db.tx() as own:
        _ensure(own)
        return int(own.execute("DELETE FROM space_reservations WHERE heartbeat < ?",
                               (cut,)).rowcount or 0)


# ---------------------------------------------------------------------------
#  the ordinary checks
# ---------------------------------------------------------------------------
def slack(path: Path | str | None = None) -> int:
    """Bytes a new operation may still add on this filesystem, before any promise."""
    base = facts(path)
    promised = 0
    try:
        with ark_db.read() as conn:
            promised = committed(conn, device_key(path))
    except Exception:
        promised = 0
    return max(0, int(base["usable_bytes"] or 0) - promised)


def require(path: Path | str | None, need: int, *, owner: str = "",
            label: str = "") -> None:
    """Refuse a known-size operation that would cross the reserve.

    This is a plain check and writes nothing to the ledger — it is for an
    operation that is about to stream bytes it controls, or for a dry run.  An
    operation that needs the space kept for it uses :func:`hold` instead.
    """
    need = max(0, int(need or 0))
    room = slack(path)
    if need > room:
        raise _refusal(need, room, path)


@contextmanager
def hold(path: Path | str | None, need: int, *, owner: str, label: str = "",
         conn=None):
    """Reserve ``need`` for the duration of a block; release it however it ends.

    ``spent`` is how much of the promise actually reached the disk; a cancelled
    conversion releases the rest at once instead of holding room for output that
    will never exist.
    """
    reservation = reserve(path or "/srv/ark", need, owner=owner, label=label, conn=conn)

    class _Receipt:
        def __init__(self) -> None:
            self.id = reservation
            self.written = 0

        def spent(self, written: int) -> None:
            self.written = max(self.written, int(written or 0))
            try:
                if conn is not None:
                    touch(conn, reservation, self.written)
                else:
                    with ark_db.tx() as own:
                        touch(own, reservation, self.written)
            except Exception:
                pass      # a lost heartbeat is repaired by reconcile(), never fatal

    receipt = _Receipt()
    try:
        yield receipt
    finally:
        try:
            if conn is not None:
                release(conn, reservation)
            else:
                with ark_db.tx() as own:
                    release(own, reservation)
        except Exception:
            pass


def describe(path: Path | str | None = None) -> str:
    """One sentence for a page: free, kept back, and what may still be added."""
    base = account(path)
    if base["free_bytes"] is None:
        return "Space on the card is not measured yet."
    return (f"{_size_words(base['usable_bytes'])} may still be added; "
            f"{_size_words(base['reserve_bytes'])} is kept back and "
            f"{_size_words(base['free_bytes'])} is free.")
