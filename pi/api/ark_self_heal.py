"""What the Ark does about itself when the power comes back.

A power cut does not just stop the box; it leaves three kinds of mess behind, and
this module is the one place that knows about all three:

* **the clock is in the past.**  A Pi has no battery-backed clock, so after a cut
  it wakes up believing it is the day the card was written.  Files on the card
  then look *newer than now*: every "3 hours ago" is wrong, a copy looks like it
  came from the future, and log timestamps cannot be trusted.  If anything on the
  card is newer than the clock, the clock is wrong and the newest such file is the
  best evidence of the real time (``fix_clock``).
* **half-written things are still there.**  Temp files from an upload or a bundle
  that was being written when the lights went out, and a library catalogue whose
  half-built copy survived.  They are removed after a generous age, by pattern,
  never by guessing (``sweep``).
* **a file can be unreadable.**  ``ark_status.json`` and the import ledger are
  written atomically and carry a ``.bak`` of the version before them, so after a
  cut the file that survives is either the old one or the new one — but the card
  itself can damage a file.  A broken JSON file is set aside and the ``.bak`` put
  back if it is readable (``heal_json``).  The network watchdog's counters are
  **not** JSON — three numbers as ``key=value`` lines — so they get their own
  shape check (``heal_state``): reading them as JSON would call a perfectly
  healthy file broken and throw the counters away on every boot.  A database is
  asked with SQLite's own ``quick_check`` and is only ever replaced by a copy that
  passes the same check (``heal_sqlite``).  **A database that could not be asked is
  not a damaged one**: a lock is not corruption, and reading one as the other
  replaces a perfectly good file with an older copy — losing every row committed
  since that copy was taken.  ``quick_check_state()`` keeps those answers apart
  (``ok``/``damaged``/``unasked``/``absent``) so nothing downstream has to guess.
  A good copy is now a **recovery generation** (``ark_recovery``): the database, a
  manifest of every file its records name, and the pins that keep a save blob
  alive while the copy can still be put back.  ``quick_check`` alone is not the
  verdict; the generation's files are read too, and a missing one is reported.

Two things this module deliberately does **not** do: it never deletes a database,
and it never touches a file it does not recognise.  A row and its file can
disagree on purpose (``ark-restore --no-files``), and "tidy up" is how data loss
gets dressed as maintenance.

The pass runs twice a day and the two runs are **not the same job**:

* **at boot**, before ``ark-api`` exists, nothing is writing and the pass repairs
  what the cut broke, keeping a good copy of every database on the way through;
* **once a day at 04:17**, while the box is serving pages, a writer may be mid-commit,
  so a database that another process holds is only *looked at* (``writer_busy``) —
  it is never replaced and its journal mode is never changed under it.  A repair
  takes the repair turnstile and then the maintenance gate (``ark_maintenance``),
  so every ordinary connection has drained and no new one can arrive; when the gate
  cannot be had in time the pass **defers** and changes nothing.  A cut is repaired
  at the next boot; a daily look is a diagnosis, and turning it into a repair under
  a live writer is how one damaged file becomes two.

A dry run (``--dry-run``) writes **nothing at all**: no copy, no journal-mode change,
no sweep.  The phase runs it to prove the tool, and a proof that alters the card is
not a proof.

``run()`` is the whole pass and answers a report a page or a verifier can print.
``ark-self-heal`` is the command line around it; ``ark-self-heal.service`` runs it
once at boot, before anything else looks at the card.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

import ark_atomic
import ark_db_replace
import ark_maintenance as maintenance
import ark_recovery

try:  # every machine that runs this is Linux or macOS; Windows is not a box
    import fcntl
except ImportError:  # pragma: no cover - the two supported platforms both have it
    fcntl = None  # type: ignore[assignment]

#: How long a temp file is left alone before it is swept.  Long enough that a slow
#: upload over a phone's Wi-Fi is never mistaken for a leftover.
STALE_UPLOAD_HOURS = 6
STALE_PARTIAL_HOURS = 12
STALE_TEMP_HOURS = 6

#: How many damaged copies of one file are kept for a human to look at.
CORRUPT_KEEP = 3

#: How far ahead of the clock a file has to be before the clock is called wrong.
#: Two minutes of slack: clocks drift, and a file written a second ago is normal.
CLOCK_SLACK = 120.0

#: How old an accepted database copy has to be before a fresh one is taken.
GOOD_COPY_HOURS = 24

#: The furthest ahead of its own clock the card may push it.  A file stamped years
#: in the future is not evidence of the time: it is evidence that the clock was
#: wrong *when that file was written*.  Following it would put the certificate and
#: every age on the box out by years, and only a person can set that right — so past
#: this bound the clock is left alone and the evidence is reported.
CLOCK_MAX_AHEAD = 400 * 86400.0

#: How long to wait for a database's write lock before calling it "in use".  A box
#: that is serving pages holds that lock for milliseconds; anything slower than this
#: and the pass stops trying to repair it and only looks.
BUSY_TIMEOUT = 0.2

#: How long a repair waits for another process to finish replacing the same database.
#: A repair is a handful of file operations, so this is generous; past it the pass
#: reports the wait rather than guessing what the other writer is doing.  Two passes
#: that both decided a file was damaged would otherwise swap in two different copies.
LOCK_WAIT = 10.0

#: How long a repair waits for every ordinary connection to close before it gives up
#: and reports that the box was busy.  The portal opens a connection per request and
#: closes it at once, so this is many times longer than a busy box needs; a reader
#: that *keeps* a handle open is exactly what the wait is for — and when it does not
#: end, the pass changes nothing and says so, because a lock is not damage.
GATE_WAIT = 5.0

#: The service that owns each database which cannot take the gate itself.  Calibre's
#: engine is not ours to change, so a repair of its records stops it first and starts
#: it again afterwards — but only if it was running, and only when the repair left the
#: file safe to open.  ``ark.db`` has no entry: every one of its users cooperates.
OWNER_UNITS = {
    "calibre-library/metadata.db": "ark-calibre.service",
    "calibre-config/app.db": "ark-calibre.service",
}


def ark_root() -> Path:
    """Where the box keeps its things — read at call time, never captured."""
    return Path(os.environ.get("ARK_ROOT") or "/srv/ark")


def log_path(root: Optional[Path] = None) -> Path:
    return Path(root or ark_root()) / "logs" / "self-heal.log"


def now() -> float:
    return time.time()


def log(message: str, *, root: Optional[Path] = None,
        when: Optional[float] = None) -> None:
    """Append one line to the self-heal log.  A log is not state: no atomic write.

    Failing to write a log line must never fail the repair that produced it, so
    every error here is swallowed on purpose.
    """
    line = "%s  %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S",
                                       time.localtime(when or now())), message)
    target = log_path(root)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.stat().st_size > 256 * 1024:
            # Keep one previous file and start again: a repair log that fills the
            # card is a repair that causes the next problem.
            shutil.copy2(str(target), str(target) + ".1")
            target.write_text("", encoding="utf-8")
        with open(str(target), "a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError:
        # A log is a courtesy.  Never let it fail the repair that produced it.
        return


# ---------------------------------------------------------------------------
#  the clock
# ---------------------------------------------------------------------------
#: The places that show the card has been used — and so what time it really is.
#: Read relative to ``ARK_ROOT``; a folder that is absent contributes nothing.
EVIDENCE_DIRS = ("drop/files", "arcade/games", "kiwix", "api/data", "backup",
                 "logs", "essentials", "calibre-incoming")

#: How many entries one folder is asked about.  A box with a thousand books must
#: not make this scan take a minute at every boot; the newest of a few hundred
#: files is already overwhelming evidence.
EVIDENCE_LIMIT = 400


def evidence(root: Optional[Path] = None) -> List[Tuple[float, str]]:
    """Every mtime on the card that could tell the time, newest last."""
    base = Path(root or ark_root())
    found: List[Tuple[float, str]] = []
    for rel in EVIDENCE_DIRS:
        folder = base / rel
        if not folder.is_dir():
            continue
        try:
            entries = list(os.scandir(str(folder)))[:EVIDENCE_LIMIT]
        except OSError:
            continue
        for entry in entries:
            try:
                found.append((entry.stat().st_mtime, str(Path(rel) / entry.name)))
            except OSError:
                continue
    for marker in ("ark_status.json", "domains"):
        try:
            found.append(((base / marker).stat().st_mtime, marker))
        except OSError:
            continue
    found.sort()
    return found


def newest_evidence(root: Optional[Path] = None) -> Tuple[float, str]:
    """The newest thing on the card, as ``(timestamp, path)``; ``(0, "")`` if none."""
    found = evidence(root)
    return found[-1] if found else (0.0, "")


def clock_is_behind(current: float, newest: float,
                    slack: float = CLOCK_SLACK) -> bool:
    """Is the clock in the past, judged by something on the card?

    A file written a moment ago is normal; a file stamped *further ahead than the
    slack* means the clock lost time while the box was off.
    """
    return bool(newest) and newest > (current + slack)


def clock_too_far(current: float, newest: float,
                  limit: float = CLOCK_MAX_AHEAD) -> bool:
    """Is the evidence so far ahead that it cannot be trusted at all?

    A cut leaves the clock in the past by hours or days, and the newest file on the
    card is then the best guess at the time.  A file stamped *years* ahead is a
    different thing: it was written while the clock was already wrong, so it is not
    evidence — following it would break the certificate and every age on every page.
    """
    return bool(newest) and newest > (current + limit)


def _set_time(stamp: float) -> bool:
    """Set the system clock.  Root only; a failure is reported, never raised."""
    try:
        subprocess.run(["date", "-u", "-s", "@%d" % int(stamp)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=10, check=True)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def fix_clock(root: Optional[Path] = None, *, current: Optional[float] = None,
              dry_run: bool = False,
              setter: Optional[Callable[[float], bool]] = None,
              problems: Optional[List[str]] = None) -> str:
    """Put the clock forward to what the card says, and remember it for next boot.

    Returns one plain sentence.  Never raises: a box that cannot set its own clock
    still serves every page, it just says the times are not to be trusted.

    ``problems`` is the list the pass reports what a person has to look at through.
    Only one thing here can land in it: evidence so far ahead that the clock must not
    follow it (``clock_too_far``).  Every other outcome is the box fixing itself.
    """
    when = now() if current is None else float(current)
    newest, where = newest_evidence(root)
    told = time.strftime("%Y-%m-%d %H:%M", time.localtime(when))
    if newest and clock_too_far(when, newest):
        said = ("Something on the card is stamped %s (%s) but the clock says %s.  It "
                "was written while the clock was already wrong, so the clock was left "
                "exactly as it is — a person has to set the date on this box."
                % (time.strftime("%Y-%m-%d %H:%M", time.localtime(newest)), where,
                   told))
        if problems is not None:
            problems.append(said)
        return said
    if not clock_is_behind(when, newest):
        return "The clock agrees with the card."
    said = time.strftime("%Y-%m-%d %H:%M", time.localtime(newest))
    if dry_run:
        return ("The clock says %s but %s says %s — it would be put forward."
                % (told, where, said))
    worked = (setter or _set_time)(newest)
    if not worked:
        return ("The clock says %s but %s says %s, and this box could not set it."
                % (told, where, said))
    # Remember it, so the next cold boot starts near the truth rather than at the
    # day the card was written.  fake-hwclock may not be installed; that is not a
    # failure, it only means the next boot starts wrong again.
    kept = ark_atomic.save_clock()
    return ("The clock said %s but %s says %s; it has been put forward%s."
            % (told, where, said, " and remembered" if kept else ""))


# ---------------------------------------------------------------------------
#  a file that came back unreadable
# ---------------------------------------------------------------------------
#: The JSON files the box keeps about itself.  Both are written through
#: ``ark_atomic`` (and ``90-verify`` keeps a copy of the receipt from before), so a
#: ``.bak`` sits beside each one once it has been written twice.
#:
#: ``logs/net-watch.state`` is deliberately **not** here: it is three numbers as
#: ``key=value`` lines, not JSON, and putting it in this list made the first boot
#: pass quarantine a perfectly healthy counters file.  It is checked by
#: ``heal_state()`` instead.
JSON_TARGETS = ("ark_status.json", "calibre-incoming/.imported.json")

#: The databases that hold something nobody can retype.  ``metadata.db`` is the
#: book shelf, ``app.db`` the book shelf's settings, ``ark.db`` everything else.
SQL_TARGETS = ("api/data/ark.db", "calibre-library/metadata.db",
               "calibre-config/app.db")

#: Read-only catalogues: checked and reported, never replaced or moved, because
#: nothing here can rebuild them without an internet connection.
SQL_CATALOGUES = ("romdb.sqlite",)


def json_targets(root: Optional[Path] = None) -> List[Path]:
    base = Path(root or ark_root())
    return [base / rel for rel in JSON_TARGETS]


def sql_targets(root: Optional[Path] = None) -> List[Path]:
    base = Path(root or ark_root())
    return [base / rel for rel in SQL_TARGETS]


def catalogue_targets(root: Optional[Path] = None) -> List[Path]:
    base = Path(root or ark_root())
    return [base / rel for rel in SQL_CATALOGUES]


def quarantine(path: Any, *, stamp: Optional[float] = None,
               keep: int = CORRUPT_KEEP) -> Optional[Path]:
    """Move a damaged file aside under a dated name.  Nothing is deleted.

    The newest ``keep`` damaged copies of one file are kept; older ones go, because
    a card can only hold so many.  A database is never removed this way unless the
    caller asks for it explicitly.
    """
    target = Path(path)
    when = time.localtime(stamp if stamp is not None else now())
    aside = Path(str(target) + ".corrupt-" + time.strftime("%Y%m%d-%H%M%S", when))
    try:
        os.replace(str(target), str(aside))
    except OSError:
        return None
    try:
        older = sorted(target.parent.glob(target.name + ".corrupt-*"),
                       key=lambda p: p.name, reverse=True)
        for extra in older[max(keep, 0):]:
            try:
                extra.unlink()
            except OSError:
                pass
    except OSError:                          # pragma: no cover - unlistable folder
        pass
    return aside


def heal_json(path: Any, *, dry_run: bool = False) -> Tuple[str, str]:
    """Make one JSON file readable again.  Answers ``(state, sentence)``.

    ``state`` is ``fine``, ``absent``, ``restored``, ``quarantined`` or ``would``;
    the sentence is written for a person and is empty when nothing happened.
    """
    target = Path(path)
    value, problem = ark_atomic.read_json(target)
    backup = Path(str(target) + ark_atomic.BACKUP_SUFFIX)
    if not problem:
        return "fine", ""
    if problem == "missing":
        # Nothing there is normal — unless a copy from before it exists, which means
        # this file vanished rather than never having been written.
        if backup.is_file() and ark_atomic.good(backup):
            if dry_run:
                return "would", "%s is gone; its copy from before would be put back." % target.name
            ark_atomic.restore_backup(target)
            return "restored", "%s was gone; its copy from before was put back." % target.name
        return "absent", ""
    if dry_run:
        could = (" and its copy from before would be put back"
                 if ark_atomic.good(backup) else "")
        return "would", "%s %s%s." % (target.name, problem, could)
    aside = quarantine(target)
    where = (" and set aside as %s" % aside.name) if aside else ""
    if ark_atomic.restore_backup(target):
        return ("restored",
                "%s %s%s; the copy from before it was put back." % (target.name, problem, where))
    return ("quarantined",
            "%s %s%s; nothing was put back, because there is no copy to put back."
            % (target.name, problem, where))


# ---------------------------------------------------------------------------
#  the watchdog's counters (key=value, not JSON)
# ---------------------------------------------------------------------------
#: The network watchdog's counters.  Three numbers; the file does not exist until
#: something has actually been decided, which is why "absent" is not a fault.
STATE_FILE = "logs/net-watch.state"
STATE_KEYS = ("lost_streak", "home_streak", "home_block_until")


def state_problem(text: Any) -> str:
    """What is wrong with the watchdog's counters, or ``""``.

    A truncated file, a line with no ``=`` and a counter that is not a number are
    all damage; an extra line (``updated=…``) is normal and ignored.
    """
    values: Dict[str, str] = {}
    for line in str(text or "").splitlines():
        key, sep, value = line.partition("=")
        key = key.strip()
        if sep and key in STATE_KEYS:
            values[key] = value.strip()
    missing = [key for key in STATE_KEYS if key not in values]
    if missing:
        return "%s missing" % ", ".join(missing)
    for key in STATE_KEYS:
        if not values[key].isdigit():
            return "%s is not a number" % key
    return ""


def heal_state(root: Optional[Path] = None, *, dry_run: bool = False
               ) -> Tuple[str, str]:
    """Check the watchdog's counters.  A damaged file is set aside, never invented.

    Answers ``(state, sentence)`` with ``state`` one of ``fine``, ``absent``,
    ``set aside`` or ``would``.  Counters that restart at zero only ever make the
    watchdog **slower** — it asks again in a minute and still needs two lost checks
    before it moves the box — so this is a repair, not something to look at, and it
    is never written by this module: ``ark-net-watch`` is the one writer.
    """
    base = Path(root or ark_root())
    target = base / STATE_FILE
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return "absent", ""
    except OSError:
        return "absent", ""
    problem = state_problem(text)
    if not problem:
        return "fine", ""
    if dry_run:
        return "would", ("%s cannot be read (%s) and would be set aside; the "
                         "watchdog would start counting again." % (target.name, problem))
    aside = quarantine(target)
    where = (" as %s" % aside.name) if aside else ""
    return ("set aside",
            "%s could not be read (%s) and was set aside%s; the watchdog starts "
            "counting again from zero." % (target.name, problem, where))


# ---------------------------------------------------------------------------
#  a database
# ---------------------------------------------------------------------------
#: How long a check waits for a lock before giving up.  Deliberately small: the question
#: is "may I look?", and a database another process is holding must be *reported*, not
#: waited on.  Kept apart from the ``5.0`` a writer uses, because the two are different
#: questions.
CHECK_TIMEOUT = 5.0

#: Words that mean SQLite *found* something wrong, as opposed to "I could not look".
#: ``sqlite3.DatabaseError`` is already the corruption class; this is the belt beside it
#: for a driver that reports a damaged image as an operational error.
CORRUPTION_WORDS = ("malformed", "not a database", "corrupt", "encrypted",
                    "file is encrypted")


def _looks_corrupt(text: Any) -> bool:
    """Does this message describe damage rather than a lock?  Never raises."""
    lowered = str(text or "").lower()
    return any(word in lowered for word in CORRUPTION_WORDS)


def quick_check_state(path: Any) -> Tuple[str, str]:
    """Ask SQLite whether it can read this file, and say *which* answer that was.

    Returns one of four states with a sentence beside it:

    ``ok``
        SQLite answered ``ok``.
    ``damaged``
        SQLite found damage — ``DatabaseError``, or a message that says so.
    ``unasked``
        The file could not be asked: locked, busy, or a driver that refused for some
        other reason.  **This is not damage** (see ``heal_sqlite``).
    ``absent``
        There is no such file.

    Getting ``damaged`` and ``unasked`` apart is the whole difference between a repair
    and a data loss.  A database a live writer is holding answers *"database is locked"*,
    which is not corruption — but a pass that reads it as corruption replaces a perfectly
    good file with an older copy, and every row committed since that copy was taken is
    gone.  That is not a theory: it was measured against the live records on
    1 November 2026, where a locked database plus a stale ``.good.bak`` cost one
    committed row.  SQLite already tells the two apart — ``OperationalError`` is a lock
    or a busy file, ``DatabaseError`` is corruption — so nothing here has to guess.

    ``quick_check`` is the cheap check on purpose: it finds the damage that matters
    after a power cut (a broken b-tree, a torn page) without the minutes a full
    ``integrity_check`` takes on a shelf of a thousand books.

    The connection is **read-write on purpose**, and that is not a slip: every
    database here is in WAL mode, a cut leaves the ``-shm`` file missing with
    committed pages still in the ``-wal``, and SQLite refuses to open such a file
    read-only ("unable to open database file").  A read-only check would therefore
    call a perfectly good database corrupt — and this function is what decides
    whether an older copy is put back, so it would have thrown away real records.
    Opening read-write lets SQLite do its own recovery first, which is the correct
    thing after a cut.  Nothing here writes a row.
    """
    target = Path(path)
    if not target.is_file():
        return "absent", "no such file"
    try:
        conn = sqlite3.connect(str(target), timeout=CHECK_TIMEOUT)
    except sqlite3.OperationalError as exc:
        # A lock is not damage; a driver reporting a malformed image is.
        return ("damaged" if _looks_corrupt(exc) else "unasked"), str(exc)
    except sqlite3.DatabaseError as exc:
        return "damaged", str(exc)
    except (sqlite3.Error, ValueError) as exc:
        return "unasked", str(exc)
    try:
        row = conn.execute("PRAGMA quick_check;").fetchone()
        answer = str((row or [""])[0] or "").strip()
    except sqlite3.OperationalError as exc:
        return ("damaged" if _looks_corrupt(exc) else "unasked"), str(exc)
    except sqlite3.DatabaseError as exc:
        return "damaged", str(exc)
    except (sqlite3.Error, ValueError) as exc:
        return "unasked", str(exc)
    finally:
        try:
            conn.close()
        except sqlite3.Error:                 # pragma: no cover - already closed
            pass
    if answer.lower() == "ok":
        return "ok", "ok"
    return "damaged", answer or "no answer"


def quick_check(path: Any) -> Tuple[bool, str]:
    """Is this database readable?  ``True`` only when SQLite answered ``ok``.

    Kept for callers that want one yes/no — the offline catalogue's check and the
    installer's own proof.  ``False`` therefore covers both "damaged" and "could not
    look"; anything that may *act* on the answer must ask ``quick_check_state``.
    """
    state, answer = quick_check_state(path)
    return state == "ok", answer


def writer_busy(path: Any) -> bool:
    """Is something writing this database right now?  Never raises.

    A repair pass runs twice: at boot, before the portal exists (nothing is writing,
    so a copy may be kept and a broken file may be put back), and once a day at 04:17
    while the box is serving pages (a writer may be mid-commit).  The two are not the
    same job.  Replacing a database under a live writer, or switching its journal mode
    out from under it, is how a repair turns one damaged file into two.

    The test is the lock itself, not a guess from a process list: begin an immediate
    transaction and roll it back.  SQLite refuses at once when another connection
    holds the write lock — and ``BEGIN IMMEDIATE`` is the same lock every writer on
    this box takes, so an idle database answers instantly.
    """
    target = Path(path)
    if not target.is_file():
        return False
    try:
        conn = sqlite3.connect(str(target), timeout=BUSY_TIMEOUT, isolation_level=None)
    except sqlite3.Error:
        return False
    try:
        conn.execute("BEGIN IMMEDIATE;")
    except sqlite3.OperationalError:
        return True
    except sqlite3.Error:                     # pragma: no cover - odd driver answer
        return False
    try:
        conn.execute("ROLLBACK;")
    except sqlite3.Error:                     # pragma: no cover - read-only file
        pass
    finally:
        try:
            conn.close()
        except sqlite3.Error:                 # pragma: no cover - already closed
            pass
    return False


def harden(path: Any) -> str:
    """Make a healthy database survive the next cut: WAL, and fsync every commit.

    ``synchronous=FULL`` is the setting that matters on a card: with the default,
    SQLite may report a commit before the bytes are on the card, and a power cut
    then loses the last thing the box said it had written.  It costs milliseconds
    on a Pi and is the difference between "the note is there" and "the note is not".
    """
    target = Path(path)
    try:
        conn = sqlite3.connect(str(target), timeout=5.0)
    except sqlite3.Error as exc:
        return "could not be opened (%s)" % exc
    try:
        conn.execute("PRAGMA journal_mode=WAL;").fetchone()
        conn.execute("PRAGMA synchronous=FULL;")
        conn.commit()
        return ""
    except sqlite3.Error as exc:
        return "could not be set up (%s)" % exc
    finally:
        try:
            conn.close()
        except sqlite3.Error:                 # pragma: no cover
            pass


def snapshot(path: Any, *, hours: float = GOOD_COPY_HOURS,
             stamp: Optional[float] = None) -> bool:
    """Keep one coherent recovery copy, at most once a day.

    The copy is a **generation** (``ark_recovery``): SQLite's own ``backup()`` of
    the records, a manifest of every external file those records name, and the
    pins that keep a save blob alive while the copy can still be put back.  The
    old ``.good.bak`` is still written, as a hard link to the same database, so
    an older repair and a person's habit keep working — but the manifest is what
    says whether the copy is whole.

    A copy taken while another process is mid-write is a copy of a moving target;
    ``backup()`` would keep restarting and could still catch a torn page from a
    writer that never finishes.  The boot pass has the database to itself, and that
    is when this copy is taken.
    """
    target = Path(path)
    if writer_busy(target):
        return False
    try:
        # A database-only copy from before this module is described before a
        # newer generation can hide it: its missing files are worth saying once.
        if not ark_recovery.published(target):
            ark_recovery.adopt_legacy(target, now=stamp)
        result = ark_recovery.capture(target, min_interval_hours=hours,
                                      now=stamp, reason="the repair pass")
    except (OSError, sqlite3.Error, ark_recovery.RecoveryRefused):
        return False
    except Exception:                        # noqa: BLE001 — a copy must not stop the pass
        return False
    return result.status == "captured"


def _sidecar(target: Path, suffix: str) -> Path:
    """The file SQLite keeps beside a database: ``<name>-wal``, ``<name>-shm``."""
    return target.with_name(target.name + suffix)


def lock_path(target: Any) -> Path:
    """Where the repair turnstile for this database lives.  Beside it, and separate.

    Deliberately **not** SQLite's own lock: that one lives in the ``-shm`` sidecar,
    which is one of the files a repair replaces and removes.  A lock a repair can
    delete is not a lock.  This file is created empty, is never swept (no rule in
    ``SWEEP_RULES`` can match it), and a leftover one means nothing — the kernel
    releases the lock when the process dies, and the file is only the name it was
    held on.  ``ark_maintenance`` owns the rule; this is the repair half of it.
    """
    return maintenance.lock_path(target)


@contextlib.contextmanager
def _repair_lock(target: Path, *, wait: Optional[float] = None) -> Iterator[bool]:
    """Hold the repair turnstile for ``target`` for the length of the block.

    Two passes can decide a file is damaged at the same moment, and two repairs of
    one database swapping in two different copies is how a good file becomes a
    mixed one.  ``flock`` on a file beside it makes the decision and the
    replacement one step.  This is the repair half of ``ark_maintenance``; the
    gate is taken separately by ``heal_sqlite`` before anything is changed.
    """
    with maintenance.repairing(target, wait=wait) as turn:
        yield turn.held


def _copy_over_status(source: Path, target: Path,
                     state: Optional[Dict[str, Any]] = None) -> str:
    """Put a checked copy back through the one crash-safe replacement path.

    Answers ``ok`` (the copy is in place and verified), ``refused`` (the original
    was left coherent — either untouched or put back) or ``unsafe`` (the box must
    stay stopped because the original could not be put back).  ``ark_db_replace``
    owns the sequence: stage and check the copy, preserve the original main file
    and journal as one set, move the journal aside, install by one rename, verify,
    and record every step so the next boot can finish or undo an interruption.
    """
    result = ark_db_replace.replace(source, target, reason="self-heal", state=state)
    if state is not None:
        state["said"] = result.said
        state["evidence"] = str(result.evidence or "")
    if result.status == "ok":
        return "ok"
    if result.status in ("unsafe", "blocked"):
        return "unsafe"
    return "refused"


def _copy_over(source: Path, target: Path) -> bool:
    """``True`` only when the copy is in place and safe to open."""
    return _copy_over_status(source, target) == "ok"


def _systemctl(*args: str):
    """Ask the service manager, or answer ``None`` on a box that has no systemd."""
    tool = shutil.which("systemctl")
    if not tool:
        return None
    try:
        return subprocess.run([tool, *args], capture_output=True, text=True,
                              timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None


def _unit_active(unit: str) -> bool:
    """Is this unit running (or on its way up)?  A box with no systemd says no."""
    done = _systemctl("is-active", unit)
    if done is None:
        return False
    return done.stdout.strip() in ("active", "activating", "reloading")


def _stop_owner(unit: str):
    """Stop a service that cannot take the gate.  ``True`` if this call stopped it.

    ``False`` means it was not running (nothing to resume); ``None`` means it could
    not be stopped, and the caller must change nothing at all.
    """
    if not unit or not _unit_active(unit):
        return False
    done = _systemctl("stop", unit)
    if done is None or done.returncode != 0:
        return None
    return True


def _start_owner(unit: str, problems: Optional[List[str]] = None) -> str:
    """Start again a service this pass stopped.  A failure is reported, never hidden."""
    done = _systemctl("start", unit)
    if done is not None and done.returncode == 0:
        return ""
    said = ("%s was stopped so its records could be repaired and could not be started "
            "again — start it by hand." % unit)
    if problems is not None:
        problems.append(said)
    return said


def heal_sqlite(path: Any, *, dry_run: bool = False,
                in_use: Optional[bool] = None, owner_unit: str = "",
                problems: Optional[List[str]] = None,
                gate_wait: Optional[float] = None) -> Tuple[str, str]:
    """Check one database, keep a good copy of it, and put one back if it is broken.

    Answers ``(state, sentence)`` with ``state`` one of ``fine``, ``absent``,
    ``unchecked``, ``in use``, ``restored``, ``busy``, ``locked``, ``deferred``,
    ``unsafe``, ``left`` or ``would``.  A database with no good copy is **left
    exactly where it is** and reported: replacing it would empty the box, and the
    owner has ``ark-restore`` and a bundle for that decision.

    Six rules, and each one is here because of a way this can go wrong:

    * **a dry run writes nothing** — no journal-mode change, no copy.  The phase runs
      ``--dry-run`` to prove itself, and a proof that alters the card is not a proof.
    * **a database somebody is writing is only looked at** (``writer_busy``): the
      daily pass runs while the portal is serving pages, and a repair must never
      replace or re-mode a file another process holds.  ``in_use`` lets a test say so
      without arranging a real lock.
    * **a database that could not be asked is not a damaged one** (``unchecked``).
      ``quick_check_state`` keeps "locked" and "corrupt" apart, and only corruption may
      put an older copy back — otherwise a pass running beside a live writer replaces
      good records with yesterday's copy and silently loses every row committed since.
      The lock is asked again immediately before a copy goes back, because the first
      answer is a moment old by then.
    * **the repair takes the turnstile, then the gate** (``ark_maintenance``).  Two
      repairs take turns; the gate waits for every ordinary connection to close,
      including a handle somebody kept, and stops new ones at the door.  A gate that
      cannot be had in ``GATE_WAIT`` seconds means **deferred**: nothing was changed,
      and an unavailable lock is not evidence of damage.  The verdict is asked again
      under the gate, because the first one was a moment old.
    * **a service that cannot cooperate is stopped while its records are repaired**.
      Calibre's engine is not ours to change, so ``owner_unit`` is stopped first and
      started again afterwards — only if it was running, and only when the outcome
      left the file safe to open.
    * **the boot pass is the one that repairs reliably**, because nothing is writing
      yet; the daily pass may defer, and says so.
    """
    target = Path(path)
    if not target.is_file():
        return "absent", ""
    if ark_db_replace.pending(target):
        return ("blocked",
                "%s is in the middle of being put back; its recovery record at %s "
                "has to be resolved before any service opens it.  This pass changed "
                "nothing." % (target.name, ark_db_replace.active_dir(target)))
    state, answer = quick_check_state(target)
    if state == "absent":
        return "absent", ""
    if dry_run:
        if state == "ok":
            return "fine", ""
        if state != "damaged":
            return ("unchecked",
                    "%s could not be checked (%s); nothing would be changed." % (
                        target.name, answer))
        return "would", "%s cannot be read (%s); a good copy would be put back." % (
            target.name, answer)
    busy = bool(in_use) if in_use is not None else writer_busy(target)
    if state == "ok":
        if busy:
            return ("in use",
                    "%s is being written right now, so it was only checked — nothing "
                    "on it was changed." % target.name)
        # A healthy database still gets two small writes: the journal mode (so the
        # next cut is cheap) and a good copy.  The turnstile keeps a repair from
        # working on it at the same moment; the gate is taken only for the mode
        # change, which is a file-header write.  The copy is taken by SQLite itself
        # and is safe beside a live writer, so it does not hold the portal open.
        with maintenance.repairing(target, wait=0.0) as turn:
            if not turn.held:
                if "could not be opened" in (turn.reason or ""):
                    # A read-only card cannot hold a lock file.  Nothing is wrong
                    # with the database; there is simply nothing to write here.
                    return "fine", ""
                return ("in use",
                        "%s is being repaired by another pass right now, so it was "
                        "only checked — nothing on it was changed." % target.name)
            note = ""
            with maintenance.maintain(target, wait=0.0) as lease:
                if lease.held:
                    note = harden(target)
            taken = snapshot(target)
        if note:
            return "fine", "%s could not be set for the next cut: %s." % (target.name, note)
        if taken:
            return "fine", "%s is sound; a good copy of it was kept." % target.name
        return "fine", ""
    if state != "damaged":
        # Locked, busy, or a driver that refused for another reason: not damage, so
        # nothing is put back and nothing is re-moded.
        return ("unchecked",
                "%s could not be checked (%s), so nothing was changed — a database "
                "that could not be asked is not a damaged one." % (target.name, answer))
    if busy:
        return ("busy",
                "%s cannot be read (%s) and the box is writing to it, so nothing was "
                "changed — stop the pages and run the pass again when the box is "
                "quiet." % (target.name, answer))
    # The check above took a moment; ask the lock again before anything goes back, because
    # a writer that arrived in between would be overwritten mid-commit.
    if in_use is None and writer_busy(target):
        return ("busy",
                "%s cannot be read (%s) and something began writing it while it was "
                "being looked at, so nothing was changed — run the pass again when the "
                "box is quiet." % (target.name, answer))
    # A copy that reads but could not be put back is a different sentence from "there is
    # no copy": the reason it stayed is worth saying, and the shared replacement path
    # also says whether it had to put the original back after changing a file.
    #
    # The turnstile first, then the gate.  Two passes can find the same file damaged at
    # the same moment — a person running it by hand while the timer fires, the boot pass
    # a minute before the daily one — and two put-backs read their copies from either
    # side of each other, which is how one whole file becomes a mixed one.  The gate is
    # what makes the verdict true for the whole replacement: no ordinary connection
    # exists outside it, and none can arrive until it is released.
    with maintenance.repairing(target, wait=LOCK_WAIT) as turn:
        if not turn.held:
            # Something else is repairing this database right now.  Its own receipt says
            # what it did; this pass changed nothing and has nothing for a person to look
            # at, so the state is its own word and not one of the problem states.
            return ("locked",
                    "%s cannot be read (%s) and another repair is working on it at this "
                    "same moment, so nothing was changed — that pass keeps the record of "
                    "what it found." % (target.name, answer))
        with maintenance.maintain(
                target, wait=GATE_WAIT if gate_wait is None else gate_wait) as lease:
            if not lease.held:
                if "could not be opened" in (lease.reason or ""):
                    return ("deferred",
                            "%s cannot be read (%s) and this card will not let a "
                            "maintenance lock be made, so nothing was changed — a lock "
                            "that cannot be made is not damage." % (target.name, answer))
                # A reader that kept its handle (or a program that never closes one)
                # is still out there.  That is a reason to try again, never a reason to
                # call the file corrupt or to replace it under the reader.
                return ("deferred",
                        "%s cannot be read (%s) and another program still has the "
                        "records open, so nothing was changed — an unavailable lock is "
                        "not damage.  The pass will look again on its next run."
                        % (target.name, answer))
            again, answer_again = quick_check_state(target)
            if again == "ok":
                return ("fine",
                        "%s was being used while it was looked at; checked again with "
                        "nothing else running, it is sound — nothing was changed."
                        % target.name)
            if again != "damaged":
                return ("unchecked",
                        "%s could not be checked (%s) even with the box quiet, so "
                        "nothing was changed — a database that could not be asked is "
                        "not a damaged one." % (target.name, answer_again))
            if in_use is None and writer_busy(target):
                return ("busy",
                        "%s cannot be read (%s) and a program that does not take the "
                        "maintenance gate is writing to it, so nothing was changed — "
                        "run the pass again when the box is quiet."
                        % (target.name, answer_again))
            stopped = False
            if owner_unit:
                stopped = _stop_owner(owner_unit)
                if stopped is None:
                    return ("busy",
                            "%s cannot be read (%s) and %s could not be stopped, so "
                            "nothing was changed — stop it by hand and run the pass "
                            "again." % (target.name, answer_again, owner_unit))
            safe_to_start = True
            track: Dict[str, Any] = {"replaced": False, "safe": False}
            refused = ""
            try:
                with ark_recovery.capture_lock(target, wait=LOCK_WAIT) as files_held:
                    if not files_held:
                        return ("locked",
                                "%s is being copied right now, so nothing was "
                                "changed — the pass will try again." % target.name)
                    # A generation knows what its records need.  Ask it first, then
                    # fall back to the older database-only names for a card that has
                    # not been through this module yet.
                    source = None
                    try:
                        source = ark_recovery.restore_source(target)
                    except (OSError, sqlite3.Error, ark_recovery.RecoveryRefused):
                        source = None
                    candidates = []
                    if source is not None:
                        candidates.append((source.path, source))
                    for candidate in (Path(str(target) + ".good.bak"),
                                      Path(str(target) + ark_atomic.BACKUP_SUFFIX)):
                        if any(candidate == known[0] for known in candidates):
                            continue
                        candidates.append((candidate, None))
                    for candidate, origin in candidates:
                        if not candidate.is_file():
                            continue
                        good_state, _good_said = quick_check_state(candidate)
                        if good_state != "ok":
                            continue
                        outcome = _copy_over_status(candidate, target, track)
                        if outcome == "ok":
                            try:
                                # The old .good.bak described the records this
                                # repair just replaced; make it describe the ones
                                # now in place.
                                ark_recovery.mark_good(target, candidate)
                            except Exception:    # noqa: BLE001 — the restore already succeeded
                                pass
                            said = ("%s came back unreadable (%s); the good copy from "
                                    "before it was put back."
                                    % (target.name, answer_again))
                            missing = list(getattr(origin, "missing", None) or [])
                            mismatched = list(getattr(origin, "mismatched", None) or [])
                            if missing or mismatched:
                                # The records are back; the files they name are not
                                # all here.  Saying so is the difference between a
                                # repair and a silent half-restore.
                                detail = ("The copy names %d file%s that %s no longer on "
                                          "the card: %s."
                                          % (len(missing) + len(mismatched),
                                             "" if len(missing) + len(mismatched) == 1 else "s",
                                             "is" if len(missing) + len(mismatched) == 1 else "are",
                                             ", ".join(str(item.get("to")) for item in
                                                       (missing + mismatched)[:4])))
                                said += " " + detail
                                if problems is not None:
                                    problems.append(detail)
                            return ("restored", said)
                        if outcome == "unsafe":
                            safe_to_start = False
                            detail = str(track.get("said") or "").strip()
                            return ("unsafe",
                                    "%s came back unreadable (%s); %s"
                                    % (target.name, answer_again,
                                       detail or "the original could not be put back.  Do "
                                       "not start the box."))
                        refused = candidate.name
                    if refused:
                        detail = str(track.get("said") or "").strip()
                        return ("left",
                                "%s came back unreadable (%s); %s"
                                % (target.name, answer_again,
                                   detail or "the good copy could not be put back and the "
                                   "original was left where it is."))
                    return ("left",
                            "%s came back unreadable (%s) and there is no good copy of it on "
                            "the card — it was left where it is."
                            % (target.name, answer_again))
            finally:
                # A service is started again only when the outcome left the file
                # safe to open: either nothing was replaced, or the replacement
                # finished and no journal of the old file survives.  An
                # interruption between those two points leaves it stopped.
                if stopped and safe_to_start and (
                        track["safe"] or not track["replaced"]):
                    _start_owner(owner_unit, problems)


# ---------------------------------------------------------------------------
#  half-written things
# ---------------------------------------------------------------------------
#: What may be swept, and how old it has to be.  Every rule is a pattern in one
#: known folder — nothing here walks a tree looking for something to delete, and
#: no rule can match ``-wal``, ``-shm`` or a ``.lock``, which must survive.
SWEEP_RULES: Tuple[Tuple[str, str, float], ...] = (
    ("drop/tmp", "upload-*.part", STALE_UPLOAD_HOURS),
    ("drop/tmp", "*.part", STALE_UPLOAD_HOURS),
    ("drop/tmp", ".*.tmp", STALE_TEMP_HOURS),
    ("arcade/tmp", "rom-*.part", STALE_UPLOAD_HOURS),
    ("arcade/tmp", "upload-*", STALE_UPLOAD_HOURS),
    #: A book on its way in from the owner's batch page (``ark_ingest.spool_book``).
    #: The name keeps a leading dot AND a ``.part`` suffix, so the book importer and
    #: the File Box ignore it; a cut in the middle of a 400 MB book therefore leaves
    #: one hidden temp file that this rule takes away, never a half book on the shelf.
    ("calibre-incoming", ".*.part", STALE_UPLOAD_HOURS),
    ("backup", "*.partial", STALE_PARTIAL_HOURS),
    ("essentials", "*.partial", STALE_TEMP_HOURS),
    ("kiwix", ".library-*", 1.0),
    ("api/data", ".*.tmp", STALE_TEMP_HOURS),
    ("api/data", "*.good.tmp", STALE_TEMP_HOURS),
    ("api/data", "*.good.bak.tmp", STALE_TEMP_HOURS),
    #: A temp file from the one careful writer (the repair report, and the watchdog's
    #: counters since they are written by rename): the name keeps a leading dot, so a
    #: half-written file can never be mistaken for the real one.
    ("logs", ".*.tmp", STALE_TEMP_HOURS),
)


def sweep(root: Optional[Path] = None, *, dry_run: bool = False,
          stamp: Optional[float] = None) -> List[str]:
    """Remove what a cut left half-written.  Answers the names it removed.

    Age is judged by mtime, generously: an upload over a phone's Wi-Fi is slow, and
    sweeping it out from under somebody is worse than leaving a temp file for
    another day.
    """
    base = Path(root or ark_root())
    when = now() if stamp is None else float(stamp)
    gone: List[str] = []
    #: Two rules can name the same file (``upload-*.part`` is a ``*.part``); it is
    #: swept once and named once, so the report and the repaired count stay honest.
    seen: set = set()
    for folder_rel, pattern, hours in SWEEP_RULES:
        folder = base / folder_rel
        if not folder.is_dir():
            continue
        try:
            matches = list(folder.glob(pattern))
        except OSError:
            continue
        for match in matches:
            name = str(Path(folder_rel) / match.name)
            if name in seen:
                continue
            try:
                age = when - match.stat().st_mtime
            except OSError:
                continue
            if age < hours * 3600:
                continue
            seen.add(name)
            gone.append(name)
            if dry_run:
                continue
            try:
                if match.is_dir() and not match.is_symlink():
                    shutil.rmtree(str(match))
                else:
                    match.unlink()
            except OSError:
                gone.pop()               # could not be removed: not a sweep
    return gone


# ---------------------------------------------------------------------------
#  the library catalogue
# ---------------------------------------------------------------------------
#: The one builder of ``library.xml``; the phase installs it here.
LIBRARY_BUILDER = "/usr/local/bin/ark-kiwix-library"


def _rebuild_library(folder: Path, builder: str) -> Tuple[bool, str]:
    try:
        done = subprocess.run([builder], env=dict(os.environ, ZIM_DIR=str(folder)),
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              timeout=300, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    answer = (done.stdout or b"").decode("utf-8", "replace").strip().splitlines()
    return done.returncode == 0, (answer[-1] if answer else "")


def heal_library(root: Optional[Path] = None, *, dry_run: bool = False,
                 builder: str = "") -> Tuple[str, str]:
    """Make sure the catalogue names every bundle that is really there.

    A ``library.xml`` built in the wrong folder records paths that do not exist, and
    a bundle removed by hand leaves an entry that points at nothing — both make
    search answer "Content not found" for every book while the files sit right
    there.  So: parse the catalogue, resolve each path, and rebuild it through
    ``ark-kiwix-library`` (the one builder) when anything does not add up.
    """
    import xml.etree.ElementTree as ET

    base = Path(root or ark_root())
    folder = base / "kiwix"
    if not folder.is_dir():
        return "absent", ""
    bundles = sorted(folder.glob("*.zim"))
    if not bundles:
        return "absent", ""
    library = folder / "library.xml"
    if not library.is_file():
        if dry_run:
            return "would", "the catalogue is missing and would be built from %d bundle(s)." % len(bundles)
        worked, said = _rebuild_library(folder, builder or LIBRARY_BUILDER)
        if worked:
            return "rebuilt", "the catalogue was missing and has been built from %d bundle(s)." % len(bundles)
        return "stale", "the catalogue is missing and could not be built (%s)." % said
    try:
        tree = ET.parse(str(library))
    except (ET.ParseError, OSError) as exc:
        problem = str(exc)
    else:
        missing = []
        for book in tree.getroot().iter("book"):
            path = str(book.get("path") or "").strip()
            if not path:
                continue
            found = Path(path) if os.path.isabs(path) else (folder / path)
            if not found.exists():
                missing.append(path)
        if not missing and len(list(tree.getroot().iter("book"))) >= len(bundles):
            return "fine", ""
        problem = ("%d entr%s point at a file that is not there" %
                   (len(missing), "y" if len(missing) == 1 else "ies")
                   if missing else "a bundle is not in the catalogue")
    if dry_run:
        return "would", "the catalogue is wrong (%s) and would be rebuilt." % problem
    worked, said = _rebuild_library(folder, builder or LIBRARY_BUILDER)
    if worked:
        return "rebuilt", "the catalogue was wrong (%s) and has been rebuilt." % problem
    return ("stale",
            "the catalogue is wrong (%s) and could not be built (%s)." % (problem, said))


# ---------------------------------------------------------------------------
#  the whole pass
# ---------------------------------------------------------------------------
def run(root: Optional[Path] = None, *, dry_run: bool = False,
        current: Optional[float] = None,
        setter: Optional[Callable[[float], bool]] = None,
        builder: str = "",
        in_use: Optional[bool] = None) -> Dict[str, Any]:
    """Do the whole repair pass and answer a report a page or a verifier can print.

    Order matters, and it is the order a power cut needs: the clock first (every age
    below is judged by it), then **any interrupted database replacement is resolved
    before a database is checked**, then the small files, then the databases, then
    the catalogue, and the sweeping last — so nothing half-written is judged against
    a clock that is still wrong and no service can open a half-swapped file.
    """
    base = Path(root or ark_root())
    when = now() if current is None else float(current)
    report: Dict[str, Any] = {
        "when": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(when)),
        "dry_run": bool(dry_run),
        "clock": "",
        "json": [],
        "state": {"state": "absent", "said": ""},
        "recovery": [],
        "recovery_copies": [],
        "recovery_gc": {},
        "recovery_warnings": [],
        "databases": [],
        "catalogues": [],
        "library": {"state": "absent", "said": ""},
        "swept": [],
        "healed": 0,
        "problems": [],
    }

    report["clock"] = fix_clock(base, current=when, dry_run=dry_run, setter=setter,
                                problems=report["problems"])

    # Every age below is judged by the clock the box now believes.  After a cut the
    # clock is in the past, and a file written before the cut then looks like it came
    # from the *future* — which would leave every leftover lying there for ever.  So
    # when the pass has just set the clock, the sweep asks the clock again.  A caller
    # that handed in ``current`` (a verifier, a test) gets exactly that moment back.
    stamp = when
    if not dry_run and current is None:
        stamp = now()

    #  An interrupted replacement must be resolved *before* anything reads or
    #  repairs the database.  This is the startup half of ``ark_db_replace``: it
    #  either finishes the install it had verified, puts the coherent original and
    #  its journal back, or leaves a ``BLOCK`` marker that ``ark_db.connect()``
    #  refuses to open.  It never guesses that a half-swapped file is safe.
    interrupted: Dict[Path, Any] = {}
    for path in sql_targets(base):
        try:
            outcome = ark_db_replace.recover_pending(path, dry_run=dry_run)
        except Exception as exc:              # noqa: BLE001 — one file must not stop the pass
            outcome = ark_db_replace.Recovery(
                "blocked", True,
                "the interrupted replacement of %s could not be checked (%s); "
                "its evidence is left in place and the box must stay stopped."
                % (path.name, exc), ark_db_replace.active_dir(path))
        rel = str(path.relative_to(base))
        if outcome.status != "none":
            report["recovery"].append({"file": rel, "status": outcome.status,
                                       "said": outcome.said})
            if outcome.status in ("committed", "rolled-back", "resolved",
                                  "abandoned"):
                report["healed"] += 1
            if outcome.status == "blocked" or (
                    outcome.status == "deferred" and outcome.pending):
                report["problems"].append(outcome.said)
        if ark_db_replace.has_active(path):
            interrupted[path] = outcome

    for path in json_targets(base):
        state, said = heal_json(path, dry_run=dry_run)
        if state in ("restored", "quarantined", "would"):
            report["json"].append({"file": str(path.relative_to(base)), "state": state,
                                   "said": said})
        if state in ("restored", "quarantined"):
            report["healed"] += 1
        if state == "quarantined":
            report["problems"].append(said)

    #  The watchdog's counters are a repair, not a problem: they restart at zero and
    #  the watchdog can only become slower, never wrong.
    state, said = heal_state(base, dry_run=dry_run)
    report["state"] = {"state": state, "said": said}
    if state == "set aside":
        report["healed"] += 1

    for path in sql_targets(base):
        rel = str(path.relative_to(base))
        if path in interrupted:
            outcome = interrupted[path]
            report["databases"].append({
                "file": rel,
                "state": "blocked" if outcome.pending else "deferred",
                "said": outcome.said,
            })
            continue
        state, said = heal_sqlite(path, dry_run=dry_run, in_use=in_use,
                                  owner_unit=OWNER_UNITS.get(rel, ""),
                                  problems=report["problems"])
        if state in ("restored", "left", "busy", "deferred", "unsafe",
                     "would", "in use", "blocked") or said:
            report["databases"].append({"file": str(path.relative_to(base)),
                                        "state": state, "said": said})
        if state == "restored":
            report["healed"] += 1
        if state in ("left", "busy", "deferred", "unsafe", "blocked"):
            # A copy was put back, so that is a repair, not a problem to look at — its
            # sentence is still printed.  What needs a human is a database that is
            # *still* unreadable (only the owner may replace it), one that is broken
            # while the box is writing to it, one whose repair was **deferred** because
            # another program kept the records open (try again; not damage), or one
            # whose replacement could not be resolved and left the box blocked (do not
            # start it).  ``locked`` is none of these: another repair pass owns that
            # database this second, and its own receipt says what it found.
            report["problems"].append(said)

    #  The recovery copies are part of the pass, not a side effect of it.  A
    #  database-only copy made before this module existed is adopted once; every
    #  retained copy is checked against the files its records name; and what
    #  neither the live records nor a retained copy needs is cleared away.
    live = base / "api" / "data" / "ark.db"
    if live.is_file():
        if not dry_run:
            try:
                adopted = ark_recovery.adopt_legacy(live, root=base, now=stamp)
            except Exception as exc:      # noqa: BLE001 — one copy must not stop the pass
                adopted = None
                report["problems"].append(
                    "the older database-only copy could not be adopted (%s)." % exc)
            if adopted is not None:
                report["recovery_copies"].append({
                    "name": adopted.generation,
                    "state": adopted.status,
                    "complete": bool((adopted.manifest or {}).get("complete")),
                    "said": adopted.said,
                })
                if adopted.status == "refused" or adopted.missing:
                    report["problems"].append(adopted.said)
        try:
            audit = ark_recovery.validate_all(live, root=base)
        except Exception as exc:          # noqa: BLE001 — ditto
            audit = {"generations": [],
                     "problems": ["the recovery copies could not be checked (%s)." % exc],
                     "warnings": []}
        report["recovery_copies"].extend(audit.get("generations") or [])
        report["recovery_warnings"] = audit.get("warnings") or []
        report["problems"].extend(audit.get("problems") or [])
        if not dry_run:
            try:
                collected = ark_recovery.gc(live, root=base, now=stamp)
            except Exception as exc:      # noqa: BLE001 — ditto
                collected = {"status": "failed", "problems": [str(exc)]}
            report["recovery_gc"] = collected
            if collected.get("status") == "deferred" and collected.get("problems"):
                report["problems"].extend(collected["problems"])

    for path in catalogue_targets(base):
        if not path.is_file():
            continue
        sound, answer = quick_check(path)
        report["catalogues"].append({"file": path.name, "ok": sound, "answer": answer})
        if not sound:
            report["problems"].append(
                "%s cannot be read (%s); it is an optional catalogue and nothing was "
                "changed — rebuild it with ark-romdb-build." % (path.name, answer))

    state, said = heal_library(base, dry_run=dry_run, builder=builder)
    report["library"] = {"state": state, "said": said}
    if state in ("rebuilt",):
        report["healed"] += 1
    if state == "stale":
        report["problems"].append(said)

    report["swept"] = sweep(base, dry_run=dry_run, stamp=stamp)
    report["healed"] += len(report["swept"])
    report["ok"] = not report["problems"]
    return report


def summary(report: Dict[str, Any]) -> List[str]:
    """The report as sentences, for a console and for ``90-verify``.

    One sentence appears once and once only: anything that is a problem is printed
    as a problem, so a reader never has to work out whether the line above the
    "Still to look at" line is the same thing said twice.
    """
    report = report or {}
    problems = list(report.get("problems") or [])
    lines: List[str] = []

    def add(said: str) -> None:
        said = (said or "").strip()
        if said and said not in problems:
            lines.append(said)

    if report.get("clock"):
        add(report["clock"])
    for item in report.get("recovery") or []:
        add(item.get("said"))
    for item in report.get("json") or []:
        add(item.get("said"))
    add((report.get("state") or {}).get("said"))
    for item in report.get("databases") or []:
        add(item.get("said"))
    library = report.get("library") or {}
    add(library.get("said"))
    for item in report.get("recovery_copies") or []:
        if item.get("state") == "adopted" or not item.get("db_ok", True) \
                or item.get("missing") or item.get("mismatched") or item.get("state") == "refused":
            add(item.get("said"))
    for warning in report.get("recovery_warnings") or []:
        add(warning)
    collected = report.get("recovery_gc") or {}
    bits = []
    if collected.get("removed"):
        bits.append("%d file%s no copy needs" % (
            collected["removed"], "" if collected["removed"] == 1 else "s"))
    for key, word in (("partials", "unfinished copy"), ("retired", "retired copy"),
                      ("pruned", "old copy")):
        count = len(collected.get(key) or [])
        if count:
            bits.append("%d %s%s" % (count, word, "" if count == 1 else "s"))
    if bits:
        lines.append("Recovery housekeeping: " + ", ".join(bits) + " cleared away.")
    swept = report.get("swept") or []
    if swept:
        lines.append("%d half-written thing%s cleared away: %s" %
                     (len(swept), "" if len(swept) == 1 else "s",
                      ", ".join(swept[:6]) + (", …" if len(swept) > 6 else "")))
    for problem in problems:
        lines.append("Still to look at: " + problem)
    return [line for line in lines if line]
