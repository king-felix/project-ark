"""Project ARK — the one rule for changing records while the box is running.

A database is not one file.  In WAL mode the committed pages can live in
``<name>-wal`` beside it, and those pages belong to the file that was there when
they were written.  Replacing the main file, or taking its journal away, while
another process still holds it is how a restore looks successful and then loses
rows — and it is how a repair turns one damaged file into two.  So no program on
this box may replace a database by itself: it must first make the box quiet.

There are two locks, and they answer two different questions.  Both live beside
the database, both are taken with ``flock`` (so the kernel releases them when the
process dies), and neither is ever deleted — a lock file left behind by a crash
means nothing, because ownership is the kernel's lock on it, not the file's
existence.  A lock that can be deleted, or a lock that is inferred from a process
list, is not a lock.

The **gate** — ``<database>.maintenance.lock``
    Every ordinary connection takes a *shared* hold for exactly as long as it
    holds the database handle (``ark_db.connect()`` binds the hold to the
    connection's lifetime).  Maintenance takes the *exclusive* hold.  Taking it
    therefore:
      * stops new connections at the door (they wait a bounded moment, then are
        told the box is busy rather than hanging a page), and
      * waits for every existing handle to be closed — which is the drain, and it
        includes a reader that kept a connection instead of closing it.

    A shared hold is taken *before* SQLite opens the file, so there is no moment
    when a connection exists outside the gate.  A process that ignores the gate
    (an older build, a foreign tool) is not covered by it; the repair paths also
    ask SQLite's own write lock (``writer_busy``) and stop the units that own a
    database nobody has taught to cooperate.

The **turnstile** — ``<database>.repair.lock``
    Maintenance takes this *first*, so two repairs of one database take turns
    instead of reading their good copies from either side of each other.  The
    gate is always taken second; nothing takes the turnstile while holding the
    gate, so the two cannot deadlock.

The order is therefore, always:

    turnstile  →  stop what cannot cooperate  →  gate  →  change the files
                                                            (drain the sidecars)
               →  release the gate  →  start what was stopped  →  release the turnstile

A lock that cannot be taken in the time allowed is an **outcome, not damage**:
the caller changes nothing, says the box was busy, and a periodic pass tries
again on its next run.  It is never evidence that the database is corrupt.

The files are mode ``0666`` on purpose.  The portal and the media worker run as
``pi``; ``ark-restore`` and the boot repair run as ``root``.  A root-created
``0644`` lock file would make every later ``pi`` connection fail, which is a total
outage caused by a safety device.  The box has one local account, and the worst a
holder of the file can do is make the box wait — not read or write a record.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

try:  # every machine that runs this is Linux or macOS; Windows is not a box
    import fcntl
except ImportError:  # pragma: no cover - the two supported platforms both have it
    fcntl = None  # type: ignore[assignment]

#: The gate, beside the database it guards.
GATE_SUFFIX = ".maintenance.lock"
#: The turnstile that makes two repairs take turns.  The older name is kept in
#: ``ark_self_heal.lock_path`` because a repair's own receipt names it.
REPAIR_SUFFIX = ".repair.lock"

#: How long an ordinary connection waits for a maintenance window to close.
#: Requests are held for milliseconds, not refused the instant a repair starts;
#: past this they are told the box is busy instead of hanging a page.
ADMIT_WAIT = 5.0
#: How long maintenance waits for the last existing handle to close.
MAINTENANCE_WAIT = 10.0
#: How long a repair waits for the turnstile when another repair holds it.
REPAIR_WAIT = 10.0

#: How often a blocked attempt is retried.  Small enough to feel immediate,
#: large enough that a waiting process is not a busy loop.
POLL = 0.02

#: A process-wide, per-thread depth.  The maintenance owner is allowed through
#: its own gate (a repair's helper may read the database it is repairing); a
#: different thread of the same process is not, because flock is held by the
#: process's *open file*, and admitting it would break the drain.
_LOCAL = threading.local()


class Busy(sqlite3.OperationalError):
    """The box is changing its records; try again in a moment.

    It is an ``OperationalError`` so every existing caller that already treats a
    database hiccup as "not available" keeps doing so; a page that wants to say
    something friendlier catches ``Busy`` first.
    """


class Ticket:
    """A shared hold on one database, released when its connection closes."""

    __slots__ = ("_fd",)

    def __init__(self, fd: int = -1) -> None:
        self._fd = fd

    @property
    def held(self) -> bool:
        return self._fd >= 0

    def release(self) -> None:
        """Give the hold back.  Safe to call twice; never raises."""
        fd, self._fd = self._fd, -1
        if fd >= 0:
            _let_go(fd)


class Lease:
    """What a maintenance attempt answers: held or not, and why not."""

    __slots__ = ("held", "nested", "reason", "waited")

    def __init__(self, held: bool, *, nested: bool = False, reason: str = "",
                 waited: float = 0.0) -> None:
        self.held = held
        self.nested = nested
        self.reason = reason
        self.waited = waited

    def __bool__(self) -> bool:  # ``with ... as lease:`` reads naturally either way
        return self.held


# ---------------------------------------------------------------------------
#  the files
# ---------------------------------------------------------------------------
def gate_path(target) -> Path:
    """The gate for this database.  Beside it, never the database itself."""
    target = Path(target)
    return target.with_name(target.name + GATE_SUFFIX)


def lock_path(target) -> Path:
    """The repair turnstile for this database."""
    target = Path(target)
    return target.with_name(target.name + REPAIR_SUFFIX)


def _open_lock(path: Path) -> int:
    """Open (and if needed create) one lock file; ``-1`` when it cannot be made.

    ``0666`` because root and ``pi`` both use it (see the module docstring).  If a
    lock file made by an older build is not writable by this account, a read-only
    open is enough: ``flock`` does not need write permission, only the same file.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    try:
        fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o666)
    except FileNotFoundError:
        return -1
    except PermissionError:
        try:
            fd = os.open(str(path), os.O_RDONLY)
        except OSError:
            return -1
    except OSError:
        return -1
    try:
        os.fchmod(fd, 0o666)
    except OSError:
        pass                    # not ours to change; the open already succeeded
    return fd


def _let_go(fd: int) -> None:
    if fcntl is not None:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:         # pragma: no cover - the file is already gone
            pass
    try:
        os.close(fd)
    except OSError:             # pragma: no cover - already closed
        pass


def _waited_until(deadline: float) -> bool:
    return time.monotonic() >= deadline


def _limit(value: Optional[float], default: float) -> float:
    return float(default if value is None else value)


def _default_from_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    try:
        return max(0.0, float(raw)) if raw not in (None, "") else default
    except (TypeError, ValueError):
        return default


def holding_here() -> bool:
    """Is this thread the one running a maintenance window?"""
    return bool(getattr(_LOCAL, "depth", 0))


# ---------------------------------------------------------------------------
#  ordinary access — the shared side
# ---------------------------------------------------------------------------
def admit(target, *, wait: Optional[float] = None) -> Ticket:
    """Take a shared hold on ``target`` for an ordinary connection.

    Returns a ``Ticket`` that must be released (``ark_db`` ties it to the
    connection).  Raises ``Busy`` when maintenance owns the gate for longer than
    ``wait``.  A platform with no ``flock``, or a folder where a lock file cannot
    be made, answers a ticket that holds nothing: refusing every read on such a
    card would be the worse failure, and the lock is a guard, not a condition for
    reading.
    """
    if holding_here():
        return Ticket()
    if fcntl is None:             # pragma: no cover - supported boxes have flock
        return Ticket()
    fd = _open_lock(gate_path(target))
    if fd < 0:
        return Ticket()
    limit = _limit(wait if wait is not None
                   else _default_from_env("ARK_GATE_ADMIT_WAIT", ADMIT_WAIT),
                   ADMIT_WAIT)
    deadline = time.monotonic() + limit
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            return Ticket(fd)
        except OSError:
            if _waited_until(deadline):
                os.close(fd)
                raise Busy(
                    "The box is putting its records back just now. Try again in "
                    "a moment.") from None
            time.sleep(POLL)


# ---------------------------------------------------------------------------
#  maintenance — the exclusive side
# ---------------------------------------------------------------------------
@contextmanager
def maintain(target, *, wait: Optional[float] = None) -> Iterator[Lease]:
    """Take the exclusive gate: no ordinary connection may exist, old or new.

    Yields a ``Lease``.  When ``lease.held`` is false, **nothing may be changed**:
    another program still has the records open (or a repair owns them), and the
    caller's correct answer is to say so and try later.
    """
    depth = getattr(_LOCAL, "depth", 0)
    if depth:
        _LOCAL.depth = depth + 1
        try:
            yield Lease(True, nested=True)
        finally:
            _LOCAL.depth = depth
        return
    if fcntl is None:             # pragma: no cover - supported boxes have flock
        yield Lease(True, reason="this platform has no flock")
        return
    fd = _open_lock(gate_path(target))
    if fd < 0:
        yield Lease(False, reason="the lock file could not be opened")
        return
    limit = _limit(wait if wait is not None
                   else _default_from_env("ARK_GATE_MAINT_WAIT", MAINTENANCE_WAIT),
                   MAINTENANCE_WAIT)
    started = time.monotonic()
    deadline = started + limit
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except OSError:
            if _waited_until(deadline):
                os.close(fd)
                yield Lease(
                    False, waited=time.monotonic() - started,
                    reason="another program still has the records open")
                return
            time.sleep(POLL)
    _LOCAL.depth = 1
    try:
        yield Lease(True, waited=time.monotonic() - started)
    finally:
        _LOCAL.depth = 0
        _let_go(fd)


@contextmanager
def repairing(target, *, wait: Optional[float] = None) -> Iterator[Lease]:
    """The turnstile: one repair of ``target`` at a time.

    Taken **before** the gate, always.  A pass that cannot have it changes
    nothing and says another repair owns the database; that is not an alarm,
    because the owner keeps the record of what it found.
    """
    depth = getattr(_LOCAL, "repair_depth", 0)
    if depth:
        _LOCAL.repair_depth = depth + 1
        try:
            yield Lease(True, nested=True)
        finally:
            _LOCAL.repair_depth = depth
        return
    if fcntl is None:             # pragma: no cover - supported boxes have flock
        yield Lease(True, reason="this platform has no flock")
        return
    fd = _open_lock(lock_path(target))
    if fd < 0:
        yield Lease(False, reason="the lock file could not be opened")
        return
    limit = _limit(wait if wait is not None
                   else _default_from_env("ARK_REPAIR_WAIT", REPAIR_WAIT),
                   REPAIR_WAIT)
    started = time.monotonic()
    deadline = started + limit
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except OSError:
            if _waited_until(deadline):
                os.close(fd)
                yield Lease(
                    False, waited=time.monotonic() - started,
                    reason="another repair is working on these records")
                return
            time.sleep(POLL)
    _LOCAL.repair_depth = 1
    try:
        yield Lease(True, waited=time.monotonic() - started)
    finally:
        _LOCAL.repair_depth = 0
        _let_go(fd)
