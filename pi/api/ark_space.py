"""Project ARK — one policy for how full the card may get.

Every part of the box that makes the card grow (a shared file, a book, a game, a
video, a collection, a model, a backup, a recovery copy) used to carry its own
idea of "enough room": half a gigabyte here, sixty-four megabytes there, twelve
gigabytes in a shell script.  Those numbers were not wrong individually — they
were three policies pretending to be one, and none of them knew what the others
had already promised.  This module is the single answer.

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
Something outside the box (a package, a log, a person with a shell, another
process on the same card) can still fill it; the reserve cannot stop that.  What
it can do is refuse to be the thing that crosses the line, notice when somebody
else is crossing it, and say so in a sentence that names the shortfall.

Reservations
------------
A check made once is not enough when two jobs run together: both can look at the
same free bytes, both be admitted, and the second one cross the reserve.  So an
operation that will write bytes takes a **reservation** first, in the database
every process already shares.  Admission asks:

    free - reserve - (what every other job has promised but not yet written) >= need

The ledger row stores two numbers: ``bytes`` (the **peak** the operation may need
at once) and ``written`` (bytes that are on the card now).  ``written`` is
subtracted from the promise as bytes reach the disk, so nothing is counted twice:
``statvfs`` already reflects the bytes that exist, and the ledger only adds the
part that does not exist yet.  ``bytes`` is a *peak*, never a sum of stages: a
request body and the copy the application makes from it overlap, so the peak is
what the card must hold, and adding the two stages would refuse work the card can
do.

Reservations belong to a **process**, not to a clock.  A row records the pid that
made it and the boot it was made in, and it is released when that process is
proven gone — not because a heartbeat looks old.  A conversion that takes an hour
is not abandoned because it has not spoken for fifteen minutes; a worker that was
killed by a power cut is gone whether its last heartbeat was a second or a day
ago.  Wall-clock time is only used for rows that predate this rule and carry no
pid at all, and then generously.

A ledger that cannot be read is **not** a ledger that says zero.  For a bulk write
that distinction is the difference between "nobody has promised anything" and
"somebody has promised something and we cannot see it", so a bulk write is
refused when the records cannot be read.  Freeing space, saving the records and
putting a good copy back are *essential* operations: they are admitted against
real free space so a broken ledger can never make the box unrepairable.
"""

from __future__ import annotations

import contextvars
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

import ark_db
import ark_maintenance

#: The owner's reserve, as a percentage of the filesystem's own capacity.
DEFAULT_PERCENT = 7.0
#: ...but never less than this, however small the card is.
FLOOR_BYTES = 2 * 1024 ** 3
#: How long a row made by a build that recorded no pid is believed.  A real
#: process is never released by this clock: see :func:`live`.
STALE_SECONDS = 15 * 60
#: A row with no pid and no boot id (written before migration 8) is kept this
#: long, because there is nothing else to go on.  Generous on purpose.
LEGACY_GRACE = 24 * 3600
#: How far ahead an unknown-size operation promises, so a chunk can always land.
STEP_BYTES = 16 * 1024 ** 2
#: The smallest read ahead that is worth a ledger write.
CHUNK = 1024 ** 2
#: A percentage outside this range is a typo, not a policy.
PERCENT_MIN, PERCENT_MAX = 0.0, 50.0

CONFIG_ENV = "ARK_SPACE_CONFIG"
ROOT_ENV = "ARK_ROOT"

#: One process may touch the ledger from several threads; the DB does the locking.
_TABLE_READY = False

#: The admission in force for this request/task, if any.  ``ark_admission`` binds
#: one so an upload's body and the copy the route makes from it argue from one
#: promise instead of two that cannot see each other.  A plain variable (not an
#: import of ``ark_admission``) keeps the two modules from importing each other.
_CURRENT: contextvars.ContextVar = contextvars.ContextVar("ark_admission", default=None)


class Refused(Exception):
    """A refusal a person can read and act on. Never a stack trace.

    Defined here rather than in ``ark_storage`` because the reserve is the thing
    that refuses, and ``ark_storage`` re-exports this name so every caller that
    already catches ``ark_storage.Refused`` keeps catching it.
    """


class LedgerUnreadable(Refused):
    """The box cannot read its own record of promised space.

    Deliberately a ``Refused``: every existing caller already handles a refusal
    with a sentence, and the safe answer to "I cannot see what is promised" is
    "do not start a bulk write".  It is a *different* class so a page can say why.
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


def _simulated(path: Path | str | None = None) -> dict | None:
    """A pretend filesystem, only when a test asks for one by name.

    Real capacity cannot be bounded without root, and filling the card under the
    test suite would be a catastrophic way to prove a refusal.  A test that needs
    a *real* multi-process race over a *small* card therefore asks the policy to
    pretend: ``ARK_SPACE_FAKE=1`` plus both numbers.

    It is inert on any box: all three variables must be set, the total must be
    positive, and nothing in this project sets them.  ``free`` is reduced by the
    bytes the ledger says are already on the card, so a simulated card fills up
    as jobs write — the arithmetic a test sees is the arithmetic a real card
    would show, not a number that stays conveniently large.
    """
    if os.environ.get("ARK_SPACE_FAKE") != "1":
        return None
    try:
        total = int(os.environ.get("ARK_SPACE_FAKE_TOTAL", "0") or 0)
        base = int(os.environ.get("ARK_SPACE_FAKE_FREE", "0") or 0)
    except ValueError:
        return None
    if total <= 0 or base < 0:
        return None
    written = 0
    try:
        held = ark_db.current_transaction()
        sql = "SELECT COALESCE(SUM(written), 0) FROM space_reservations"
        if held is not None:
            written = int(held.execute(sql).fetchone()[0] or 0)
        else:
            with ark_db.read() as own:
                written = int(own.execute(sql).fetchone()[0] or 0)
    except Exception:                        # noqa: BLE001 — no ledger, no spent bytes
        written = 0
    free = max(0, base - written)
    return {"path": str(path or "/srv/ark"), "device": device_key(path),
            "total_bytes": total, "free_bytes": free,
            "reserve_bytes": reserve_for(total), "usable_bytes": max(0, free - reserve_for(total)),
            "percent": reserve_percent()}


def facts(path: Path | str | None = None) -> dict:
    """What this filesystem holds, and what the policy keeps back.

    ``None`` anywhere means the question could not be asked; every page prints
    *not measured yet* rather than zero, the same `None` rule as the rest of the
    box.  ``usable_bytes`` is raw — it has not had other jobs' promises taken out
    of it; :func:`account` and :func:`slack` do that.
    """
    pretend = _simulated(path)
    if pretend is not None:
        return pretend
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


def essential_room(path: Path | str | None = None) -> int | None:
    """Room for an operation the box must be able to do even when it is full.

    Free space minus the reserve, with **no** regard for other jobs' promises and
    no ledger read at all.  Freeing space, saving the records and putting a good
    copy back use this: a ledger that cannot be read, or a card that is inside
    its reserve, must never leave the box unable to repair itself or unable to
    delete something.  ``None`` means the filesystem could not be measured.
    """
    base = facts(path)
    if base["usable_bytes"] is None:
        return None
    return int(base["usable_bytes"])


def device_key(path: Path | str | None = None) -> int:
    """A stable identity for the filesystem, so reservations are per-card.

    ``st_dev`` is the kernel's own answer and needs no parsing; the fallback to a
    hash of the path keeps a platform without it working rather than crashing.
    """
    probe = Path(path) if path else Path(os.environ.get(ROOT_ENV, "/srv/ark"))
    for candidate in (probe, *probe.parents):
        try:
            return int(os.stat(str(candidate)).st_dev)
        except OSError:
            continue
    return 0


def boot_id() -> str:
    """An identity for this power-on, so a row from a previous boot is provably dead.

    A pid alone is not enough across a reboot: after a power cut the card comes
    back with a pid 1 that has nothing to do with the process that made the
    promise.  Linux publishes a boot id; on macOS the kernel's own boot time is
    the equivalent.  An empty string means "this platform would not say", and
    :func:`live` then falls back to the pid alone.
    """
    for name in ("/proc/sys/kernel/random/boot_id", "/sys/kernel/random/boot_id"):
        try:
            value = Path(name).read_text(encoding="utf-8").strip()
            if value:
                return value
        except OSError:
            continue
    try:
        import subprocess
        out = subprocess.run(["sysctl", "-n", "kern.boottime"], capture_output=True,
                             text=True, timeout=2).stdout
        # { sec = 1712345678, usec = 12345 } Thu Apr  5 12:34:56 2024
        if "sec" in out:
            digits = out.split("sec", 1)[1].split("=", 1)[1].split(",", 1)[0]
            return "boot-" + digits.strip()
    except Exception:                        # noqa: BLE001 — a missing answer is fine
        pass
    return ""


_BOOT = None


def current_boot() -> str:
    """The boot id, read once per process (it cannot change while we run)."""
    global _BOOT
    if _BOOT is None:
        _BOOT = boot_id()
    return _BOOT


def pid_alive(pid: int) -> bool:
    """Is this process still running on this box?

    ``os.kill(pid, 0)`` asks the kernel without sending anything.  A permission
    error means the process exists and belongs to somebody else — still alive.
    """
    pid = int(pid or 0)
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def live(row, *, now: float | None = None) -> bool:
    """Does this promise still belong to a running process?

    The order matters.  A **live pid in this boot** is alive, whatever its
    heartbeat says: an hour-long conversion is not abandoned because it has been
    quiet for fifteen minutes, and a clock that jumped must not take its space
    away.  A promise is dead when the boot id proves the box restarted, or when
    its pid is provably gone.  Only a row with no pid at all — written before
    migration 8 — falls back to the clock, and then on a day-long grace.
    """
    stamp = time.time() if now is None else float(now)
    try:
        heartbeat = float(row["heartbeat"] or 0)
        pid = int(row["pid"] or 0)
        boot = str(row["boot"] or "")
    except (IndexError, KeyError, TypeError, ValueError):
        return False
    if heartbeat > stamp + 1.0:
        # The clock went backwards.  A promise made "in the future" is fresh.
        return True
    ours = current_boot()
    if boot and ours and boot != ours:
        return False                         # nothing from the old boot is running
    if pid:
        return pid_alive(pid)
    return (stamp - heartbeat) < LEGACY_GRACE


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
    written   INTEGER NOT NULL DEFAULT 0,
    pid       INTEGER NOT NULL DEFAULT 0,
    boot      TEXT NOT NULL DEFAULT '',
    kind      TEXT NOT NULL DEFAULT ''
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


def committed(conn, device: int, *, exclude: int | None = None) -> int:
    """Bytes promised to running jobs on this filesystem that are not on disk yet.

    Raises whatever SQLite raises when the ledger cannot be read.  Callers that
    must not fail (a status page) use :func:`committed_for`; a caller about to
    write bulk bytes must let the error through — see the module docstring.
    """
    _ensure(conn)
    if exclude:
        row = conn.execute(
            "SELECT COALESCE(SUM(MAX(0, bytes - written)), 0) FROM space_reservations"
            " WHERE device = ? AND id <> ?", (int(device), int(exclude))).fetchone()
    else:
        row = conn.execute(
            "SELECT COALESCE(SUM(MAX(0, bytes - written)), 0) FROM space_reservations"
            " WHERE device = ?", (int(device),)).fetchone()
    return int(row[0] or 0)


def committed_for(path: Path | str | None = None, conn=None) -> int | None:
    """Bytes promised on this filesystem, or ``None`` when the ledger cannot be read.

    ``None`` is not zero.  A page prints *not measured yet*; a bulk write is
    refused rather than being told the card is empty of promises.
    """
    device = device_key(path)
    try:
        held = conn if conn is not None else ark_db.current_transaction()
        if held is not None:
            return committed(held, device)
        with ark_db.read() as own:
            return committed(own, device)
    except Exception:                        # noqa: BLE001 — a reader must not 500
        return None


def account(path: Path | str | None = None, conn=None) -> dict:
    """Everything an owner needs to see: total, free, reserve, promised, usable.

    ``committed_bytes`` and ``usable_bytes`` are ``None`` — *not measured yet* —
    when the ledger cannot be read.  ``breached`` and ``ledger`` still answer, so
    a page can say which of the two problems it is looking at.
    """
    base = facts(path)
    promised = committed_for(path, conn=conn)
    usable = None
    if promised is not None and base["usable_bytes"] is not None:
        usable = max(0, int(base["usable_bytes"]) - int(promised))
    return {
        "path": base["path"],
        "total_bytes": base["total_bytes"],
        "free_bytes": base["free_bytes"],
        "reserve_bytes": base["reserve_bytes"],
        "committed_bytes": promised,
        "usable_bytes": usable,
        "percent": base["percent"],
        "ledger": "ok" if promised is not None else "unreadable",
        "breached": (base["free_bytes"] is not None
                     and base["reserve_bytes"] is not None
                     and base["free_bytes"] < base["reserve_bytes"]),
    }


def _refusal(need: int, usable: int, path: Path | str) -> Refused:
    return Refused(
        f"There is not enough room on the card for that: it needs "
        f"{_size_words(need)} and {_size_words(max(0, usable))} may be used. "
        "Delete something you no longer need, then try again.")


def _ledger_refusal(path: Path | str) -> LedgerUnreadable:
    return LedgerUnreadable(
        "The box could not read its own record of space promised to work already "
        "running, so it will not start another large write. Nothing was changed. "
        "Try again in a moment; if it keeps saying this, the records need checking.")


def _size_words(n: int) -> str:
    n = int(n or 0)
    for unit, size in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("kB", 1024)):
        if abs(n) >= size:
            return f"{n / size:.1f} {unit}"
    return f"{n} bytes"


def _insert(conn, path, device: int, need: int, owner: str, label: str,
            kind: str, pid: int, boot: str) -> int:
    now = time.time()
    cur = conn.execute(
        "INSERT INTO space_reservations"
        " (ts, heartbeat, owner, device, path, label, bytes, written, pid, boot, kind)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)",
        (now, now, str(owner)[:120], device, str(path), str(label)[:200],
         max(0, int(need)), int(pid), str(boot), str(kind)[:20]))
    return int(cur.lastrowid)


def reserve(path: Path | str, need: int, *, owner: str, label: str = "",
            conn=None, kind: str = "job", essential: bool = False,
            pid: int | None = None) -> int:
    """Promise ``need`` bytes on this filesystem, or refuse.

    The promise is a row any process can see, so two conversions started a second
    apart cannot both be told there is room for them.

    ``essential`` is for the work the box must be able to do on a full card:
    freeing space, writing the records, putting a good copy back.  It is admitted
    against real free space alone — other jobs' promises are ignored, because
    refusing to save a recovery copy would be worse than competing for the last
    few bytes.  A ledger that cannot be read does not block an essential write.
    """
    need = max(0, int(need or 0))
    device = device_key(path)
    if essential:
        # The check that matters for an essential write is physical: is there room
        # at all?  Other jobs' promises are ignored on purpose, and a ledger that
        # cannot be opened does not stop the box saving its own records.
        room = essential_room(path)
        if room is not None and need > room:
            raise _refusal(need, room, path)
    held = conn if conn is not None else ark_db.current_transaction()
    try:
        if held is not None:
            # Join the caller's write transaction: SQLite has one writer, and a
            # second connection would wait for it and then fail — in the middle of
            # publishing a game, with the bytes already staged.
            return _reserve(held, path, device, need, owner, label, kind, essential, pid)
        with ark_db.tx() as own:
            return _reserve(own, path, device, need, owner, label, kind, essential, pid)
    except Exception as exc:                 # noqa: BLE001 — a ledger is not the work
        if essential:
            # No records to write a promise into (a card in a reader, a database
            # being replaced, a box with no database yet).  The physical check
            # above is what admits the work; the promise stays in this process.
            return 0
        if isinstance(exc, Refused):
            raise
        raise _ledger_refusal(path) from exc


def _reserve(conn, path, device: int, need: int, owner: str, label: str,
             kind: str, essential: bool, pid: int | None) -> int:
    global _TABLE_READY
    try:
        _ensure(conn)
    except sqlite3.Error as exc:
        _TABLE_READY = False
        raise _ledger_refusal(path) from exc
    stats = facts(path)
    free = int(stats["free_bytes"] or 0)
    reserve_now = int(stats["reserve_bytes"] or 0)
    if essential:
        room = max(0, free - reserve_now)
        if need > room:
            raise _refusal(need, room, path)
    else:
        try:
            promised = committed(conn, device)
        except sqlite3.Error as exc:
            # "I cannot see what is promised" is not "nothing is promised".
            raise _ledger_refusal(path) from exc
        room = max(0, free - reserve_now - promised)
        if need > room:
            raise _refusal(need, room, path)
    try:
        return _insert(conn, path, device, need, owner, label, kind,
                       os.getpid() if pid is None else int(pid), current_boot())
    except sqlite3.Error:
        # A refusal rolls the whole transaction back — including the CREATE that
        # `_ensure` may just have run.  Forget that it was done, so the next call
        # checks again instead of trusting a table that no longer exists.
        _TABLE_READY = False
        if essential:
            return 0          # no ledger to write to; the free-space check stands
        raise
    except Exception:
        _TABLE_READY = False
        raise


def touch(conn, reservation_id: int, written: int | None = None) -> None:
    """Refresh the heartbeat, and optionally how much of the promise is on disk."""
    _ensure(conn)
    now = time.time()
    if written is None:
        conn.execute("UPDATE space_reservations SET heartbeat = ? WHERE id = ?",
                     (now, int(reservation_id)))
    else:
        conn.execute(
            "UPDATE space_reservations SET heartbeat = ?,"
            " written = MAX(written, MIN(bytes, ?)) WHERE id = ?",
            (now, max(0, int(written)), int(reservation_id)))


def grow(conn, reservation_id: int, total: int, *, path=None, device=None,
         essential: bool = False) -> int:
    """Raise one promise to at least ``total`` bytes — the peak, never a sum.

    The admission question is asked again, inside the same write transaction that
    changes the row, with **this** row's remaining promise taken out and put back
    once: another job's promise is not free space, and neither is the part of this
    job's promise that has not been written yet.
    """
    try:
        _ensure(conn)
        row = conn.execute(
            "SELECT bytes, written, path, device FROM space_reservations WHERE id = ?",
            (int(reservation_id),)).fetchone()
    except sqlite3.Error as exc:
        raise _ledger_refusal(path or "/srv/ark") from exc
    if row is None:
        raise Refused("That promise of space was released while the work was still "
                      "running, so the box stopped instead of guessing. Try again.")
    current = int(row["bytes"] or 0)
    written = int(row["written"] or 0)
    total = max(0, int(total or 0))
    if total <= current:
        return current
    where = Path(path) if path else Path(str(row["path"] or "/"))
    dev = int(device) if device is not None else int(row["device"] or 0)
    stats = facts(where)
    free = int(stats["free_bytes"] or 0)
    reserve_now = int(stats["reserve_bytes"] or 0)
    if essential:
        room = max(0, free - reserve_now)
    else:
        try:
            others = committed(conn, dev, exclude=int(reservation_id))
        except sqlite3.Error as exc:
            raise _ledger_refusal(where) from exc
        room = max(0, free - reserve_now - others - max(0, current - written))
    delta = total - current
    if delta > room:
        raise _refusal(delta, room, where)
    conn.execute("UPDATE space_reservations SET bytes = ?, heartbeat = ? WHERE id = ?",
                 (total, time.time(), int(reservation_id)))
    return total


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


def reconcile(max_age: int | None = None, conn=None) -> int:
    """Release the promises whose process is proven gone.

    Called when the portal and the media worker start.  A row left by a power cut
    must not hold space nothing is using, and a legitimate hour-long worker must
    not lose its promise because its heartbeat looks old — :func:`live` decides,
    from the pid and the boot id, not from the clock.
    """
    def _sweep(conn) -> int:
        _ensure(conn)
        rows = conn.execute("SELECT * FROM space_reservations").fetchall()
        dead = [int(row["id"]) for row in rows if not live(row)]
        # One statement per row: SQLite has a variable limit, and a box with a
        # hundred promises is not one to be clever about.
        for ident in dead:
            conn.execute("DELETE FROM space_reservations WHERE id = ?", (ident,))
        return len(dead)

    if conn is not None:
        return _sweep(conn)
    with ark_db.tx() as own:
        return _sweep(own)


# ---------------------------------------------------------------------------
#  receipts: one promise, changed as the work proceeds
# ---------------------------------------------------------------------------
class Receipt:
    """One reservation, held by the code that is writing the bytes.

    ``need(total)`` raises the promise to cover a **peak**.  ``spent(key, n)``
    says how many bytes of one stage are on the card now.  Both are absolute and
    monotone: a caller can call them as often as it likes with the numbers it
    already knows, and a repeated call costs nothing.

    Every mutation is its own short write transaction, because the receipt may
    outlive any one database call (an upload lasts minutes).  A row that has been
    released underneath us is an outcome, not a crash: the work is stopped with a
    sentence rather than writing bytes nothing has promised.
    """

    def __init__(self, reservation_id: int, device: int, path, reserved: int,
                 written: int = 0, *, conn=None, essential: bool = False) -> None:
        self.id = int(reservation_id)
        self.device = int(device)
        self.path = str(path)
        self.reserved = int(reserved)
        self.written = int(written)
        self.conn = conn
        self.essential = bool(essential)
        self._spent: dict = {}
        self._released = False

    # -- internals ---------------------------------------------------------
    def _write(self, action):
        held = self.conn if self.conn is not None else ark_db.current_transaction()
        if held is not None:
            return action(held)
        with ark_db.tx() as own:
            return action(own)

    def __repr__(self) -> str:                # pragma: no cover - debugging only
        return (f"<Receipt {self.id} {self.path} reserved={self.reserved} "
                f"written={self.written}>")

    # -- the two verbs -----------------------------------------------------
    def need(self, total: int) -> int:
        """Promise at least ``total`` bytes at the peak.  Never shrinks."""
        total = max(0, int(total or 0))
        if total <= self.reserved:
            return self.reserved
        if self._released:
            raise Refused("That work was cancelled, so the box stopped instead of "
                          "writing without a promise. Try again.")
        if self.id == 0:
            # An essential operation with no ledger to write to.  It is still
            # bounded by the card: the promise is checked against real free space
            # and kept in this process for the life of the work.
            room = essential_room(self.path)
            if room is not None and total > room:
                raise _refusal(total, room, self.path)
            self.reserved = total
            return self.reserved
        try:
            value = self._write(lambda conn: grow(
                conn, self.id, total, path=self.path, device=self.device,
                essential=self.essential))
        except Refused:
            raise
        except Exception as exc:             # noqa: BLE001 — a ledger is not the work
            # A maintenance window ("the box is putting its records back") is a
            # *busy* answer, not a broken ledger: let it through so the door can
            # say so instead of showing a page that reports damage.
            if isinstance(exc, ark_maintenance.Busy):
                raise
            raise _ledger_refusal(self.path) from exc
        self.reserved = int(value)
        return self.reserved

    def peak(self, key: str, total: int) -> int:
        """Promise enough for stage ``key`` to hold ``total`` bytes, and no more.

        The peak is what is already on the card from *other* stages plus this
        stage's whole total — never a sum of every stage's total, because a
        request body and the copy made from it overlap in time and only the
        larger of the two has to fit beside the rest.
        """
        key = str(key or "file")
        other_stages = max(0, self.written - self._spent.get(key, 0))
        return self.need(other_stages + max(0, int(total or 0)))

    def spent(self, key: str, written: int) -> None:
        """Record that ``written`` bytes of stage ``key`` are on the card.

        ``written`` is the stage's total, not an increment.  Bytes that exist are
        already missing from ``statvfs``; the ledger must stop promising them too,
        or the same byte would be subtracted twice and the box would refuse work
        it can do.
        """
        key = str(key or "main")
        value = max(0, int(written or 0))
        if value <= self._spent.get(key, 0):
            return
        self._spent[key] = value
        total = sum(self._spent.values())
        # A stage must never have written more than the promise covers; grow
        # first, so the row can never say "more bytes than were promised".
        if total > self.reserved:
            self.need(total)
        self.written = total
        if self._released or self.id == 0:
            return

        def _touch(conn):
            _ensure(conn)
            conn.execute(
                "UPDATE space_reservations SET heartbeat = ?,"
                " written = MAX(written, MIN(bytes, ?)) WHERE id = ?",
                (time.time(), int(total), self.id))

        try:
            self._write(_touch)
        except Exception:                    # noqa: BLE001 — progress is telemetry
            # A heartbeat that could not be written is repaired by reconcile();
            # losing one must not stop a film that is otherwise fine.
            pass

    # -- the end -----------------------------------------------------------
    def release(self) -> None:
        if self._released:
            return
        self._released = True
        if self.id == 0:
            return
        try:
            self._write(lambda conn: release(conn, self.id))
        except Exception:                    # noqa: BLE001 — a tidy-up never fails work
            # A row that could not be deleted is released by the next reconcile
            # (the pid is gone the moment this process ends, and the boot id
            # handles a restart).  Losing a delete is not a reason to fail a page.
            pass

    def __enter__(self) -> "Receipt":
        return self

    def __exit__(self, *_exc) -> bool:
        self.release()
        return False


def open_receipt(path: Path | str, *, owner: str, label: str = "", kind: str = "job",
                 pid: int | None = None, essential: bool = False) -> Receipt:
    """Start a promise of zero bytes, so it can be grown as the size becomes known.

    A zero-byte row costs nothing and is not a lie: nothing is promised until a
    byte is.  It exists so that a body arriving without a ``Content-Length`` can
    grow one promise instead of racing a check it will lose.
    """
    device = device_key(path)
    held = ark_db.current_transaction()
    try:
        if held is not None:
            _ensure(held)
            ident = _insert(held, path, device, 0, owner, label, kind,
                            os.getpid() if pid is None else int(pid), current_boot())
        else:
            with ark_db.tx() as conn:
                _ensure(conn)
                ident = _insert(conn, path, device, 0, owner, label, kind,
                                os.getpid() if pid is None else int(pid), current_boot())
    except Exception as exc:                 # noqa: BLE001 — a ledger is not the work
        if essential:
            # Recovery and the carry-away pack must work on a box whose records
            # are being replaced right now; the free-space check is what guards
            # them, and the promise lives in this process until the work ends.
            return Receipt(0, device, path, 0, essential=True)
        if isinstance(exc, Refused):
            raise
        raise _ledger_refusal(path) from exc
    return Receipt(ident, device, path, 0, essential=essential)


@contextmanager
def hold(path: Path | str | None, need: int, *, owner: str, label: str = "",
         conn=None, essential: bool = False):
    """Reserve ``need`` for the duration of a block; release it however it ends.

    ``spent`` is how much of the promise actually reached the disk; a cancelled
    conversion releases the rest at once instead of holding room for output that
    will never exist.
    """
    target = path or "/srv/ark"
    ident = reserve(target, need, owner=owner, label=label, conn=conn,
                    essential=essential)
    device = device_key(target)
    receipt = Receipt(ident, device, target, int(need or 0), conn=conn,
                      essential=essential)
    try:
        yield receipt
    finally:
        receipt.release()


# ---------------------------------------------------------------------------
#  what an upload uses — one promise per request, shared by its stages
# ---------------------------------------------------------------------------
def current_admission():
    """The admission bound to this request/task, or ``None`` outside one."""
    return _CURRENT.get()


def bind(admission):
    """Make ``admission`` the promise upload stages in this task adopt."""
    return _CURRENT.set(admission)


def unbind(token) -> None:
    try:
        _CURRENT.reset(token)
    except (ValueError, LookupError):        # pragma: no cover - cross-task misuse
        _CURRENT.set(None)


@contextmanager
def cover(path: Path | str, need: int, *, owner: str, label: str = "",
          key: str = "file", essential: bool = False):
    """Make sure this write has ``need`` bytes promised — and adopt a request's promise.

    Inside a request, ``ark_admission`` has already promised the peak a body can
    cost (the framework's spool **and** the copy the route makes).  A stage that
    is part of that peak must not promise the same bytes a second time: it takes
    the same receipt, grows it only if the stage genuinely needs more, and leaves
    the release to the request that owns it.

    Outside a request (a CLI, the importer, the media worker) there is nothing to
    adopt, so this is a normal reservation released when the block ends.
    """
    admission = current_admission()
    if admission is not None:
        receipt = admission.claim(path, need, owner=owner, label=label)
        yield receipt
        return
    with hold(path, need, owner=owner, label=label, essential=essential) as receipt:
        yield receipt


# ---------------------------------------------------------------------------
#  the ordinary checks
# ---------------------------------------------------------------------------
def slack(path: Path | str | None = None) -> int | None:
    """Bytes a new operation may still add, or ``None`` when the ledger cannot be read.

    Includes nothing for this operation: it is what is left *before* a promise.
    """
    base = facts(path)
    if base["usable_bytes"] is None:
        return None
    promised = committed_for(path)
    if promised is None:
        return None
    return max(0, int(base["usable_bytes"]) - int(promised))


def first_step(path: Path | str, limit: int) -> int:
    """What to promise before an unknown-size write: a step, but never more than is free.

    A fixed 16 MiB step is a fine idea until the card has 8 MiB left: asking for
    the step would refuse a file that fits.  The step is therefore bounded by the
    room that exists *now*, and the write grows again as it goes.  A ledger that
    cannot be read refuses, exactly as a known-size write does.
    """
    want = min(STEP_BYTES, max(CHUNK, int(limit or 0)))
    room = slack(path)
    if room is None:
        raise _ledger_refusal(path)
    return int(min(want, max(0, int(room))))


def growth_step(path: Path | str, written: int, limit: int) -> int:
    """The absolute total to promise after ``written`` bytes of an unknown-size write.

    At least one chunk ahead (so the next read always lands), never more than a
    step, and never more than the room that is really left.  Returns ``written``
    itself when there is no room left — the caller's ``need`` then refuses, with
    the ordinary sentence, before it writes another byte.
    """
    written = max(0, int(written or 0))
    room = slack(path)
    if room is None:
        raise _ledger_refusal(path)
    step = min(STEP_BYTES, max(CHUNK, int(limit or 0) - written))
    return written + int(min(step, max(0, int(room))))


def require(path: Path | str | None, need: int, *, owner: str = "",
            label: str = "", essential: bool = False) -> None:
    """Refuse a known-size operation that would cross the reserve.

    This is a plain check and writes nothing to the ledger — it is for an
    operation that is about to stream bytes it controls, or for a dry run.  An
    operation that needs the space kept for it uses :func:`hold` instead.

    A ledger that cannot be read refuses a bulk write (``LedgerUnreadable``)
    rather than pretending nothing is promised.  ``essential`` skips the ledger
    entirely and asks only whether the bytes physically fit.
    """
    need = max(0, int(need or 0))
    if essential:
        room = essential_room(path)
        if room is not None and need > room:
            raise _refusal(need, room, path or "/srv/ark")
        return
    room = slack(path)
    if room is None:
        raise _ledger_refusal(path or "/srv/ark")
    if need > room:
        raise _refusal(need, room, path or "/srv/ark")


def describe(path: Path | str | None = None) -> str:
    """One sentence for a page: free, kept back, and what may still be added."""
    base = account(path)
    if base["free_bytes"] is None or base["usable_bytes"] is None \
            and base["committed_bytes"] is not None:
        return "Space on the card is not measured yet."
    if base["committed_bytes"] is None:
        return (f"{_size_words(base['free_bytes'])} is free; the box could not read "
                f"its own record of space promised to work already running, so it "
                f"will not start another large write.")
    return (f"{_size_words(base['usable_bytes'])} may still be added; "
            f"{_size_words(base['reserve_bytes'])} is kept back and "
            f"{_size_words(base['free_bytes'])} is free.")
