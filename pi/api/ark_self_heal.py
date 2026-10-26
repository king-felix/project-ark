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
  healthy file broken and throw the counters away on every boot.  A broken
  database is opened read-only and checked with SQLite's own ``quick_check``, and
  is only ever replaced by a copy that passes the same check (``heal_sqlite``).

Two things this module deliberately does **not** do: it never deletes a database,
and it never touches a file it does not recognise.  A row and its file can
disagree on purpose (``ark-restore --no-files``), and "tidy up" is how data loss
gets dressed as maintenance.

The pass runs twice a day and the two runs are **not the same job**:

* **at boot**, before ``ark-api`` exists, nothing is writing and the pass repairs
  what the cut broke, keeping a good copy of every database on the way through;
* **once a day at 04:17**, while the box is serving pages, a writer may be mid-commit,
  so a database that another process holds is only *looked at* (``writer_busy``) —
  it is never replaced and its journal mode is never changed under it.  A cut is
  repaired at the next boot; a daily look is a diagnosis, and turning it into a
  repair under a live writer is how one damaged file becomes two.

A dry run (``--dry-run``) writes **nothing at all**: no copy, no journal-mode change,
no sweep.  The phase runs it to prove the tool, and a proof that alters the card is
not a proof.

``run()`` is the whole pass and answers a report a page or a verifier can print.
``ark-self-heal`` is the command line around it; ``ark-self-heal.service`` runs it
once at boot, before anything else looks at the card.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import ark_atomic

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
def quick_check(path: Any) -> Tuple[bool, str]:
    """Ask SQLite whether it can read this file.  Never raises.

    ``quick_check`` is the cheap one: it finds the damage that matters after a power
    cut (a broken b-tree, a torn page) without the minutes a full ``integrity_check``
    takes on a shelf of a thousand books.

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
        return False, "no such file"
    try:
        conn = sqlite3.connect(str(target), timeout=5.0)
    except sqlite3.Error as exc:
        return False, str(exc)
    try:
        row = conn.execute("PRAGMA quick_check;").fetchone()
        answer = str((row or [""])[0] or "").strip()
        return answer.lower() == "ok", answer or "no answer"
    except (sqlite3.Error, ValueError) as exc:
        return False, str(exc)
    finally:
        try:
            conn.close()
        except sqlite3.Error:                 # pragma: no cover - already closed
            pass


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
    """Keep one known-good copy of a database, at most once a day.

    Written with SQLite's own ``backup()`` into a temp file and renamed into place:
    a plain copy of a live WAL database can miss the last commits, which is the same
    mistake ``ark-backup`` avoids by using ``backup()`` too.
    """
    target = Path(path)
    good = Path(str(target) + ".good.bak")
    when = now() if stamp is None else float(stamp)
    try:
        if good.is_file() and (when - good.stat().st_mtime) < hours * 3600:
            return False
    except OSError:
        pass
    # A copy taken while another process is mid-write is a copy of a moving target;
    # ``backup()`` would keep restarting and could still catch a torn page from a
    # writer that never finishes.  The boot pass has the database to itself, and that
    # is when this copy is taken.
    if writer_busy(target):
        return False
    tmp = Path(str(target) + ".good.tmp")
    try:
        if tmp.exists():
            tmp.unlink()
        # Read-write on purpose: a live database is in WAL, and SQLite's read-only
        # mode refuses it when the ``-wal`` file may need reading.  Opening it
        # read-write is what ``ark-backup`` does too, and it takes no lock that a
        # reader waits on.
        src = sqlite3.connect(str(target), timeout=5.0)
        try:
            dst = sqlite3.connect(str(tmp), timeout=5.0)
            try:
                src.backup(dst)
                dst.commit()
            finally:
                dst.close()
        finally:
            src.close()
        os.replace(str(tmp), str(good))
        ark_atomic.fsync_dir(good.parent)
        return True
    except (OSError, sqlite3.Error):
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        return False


def _copy_over(source: Path, target: Path) -> bool:
    """Put a checked copy back in place, atomically."""
    try:
        data = source.read_bytes()
    except OSError:
        return False
    try:
        ark_atomic.save_bytes(target, data, keep_backup=False)
        return True
    except OSError:                           # pragma: no cover - card full
        return False


def heal_sqlite(path: Any, *, dry_run: bool = False,
                in_use: Optional[bool] = None) -> Tuple[str, str]:
    """Check one database, keep a good copy of it, and put one back if it is broken.

    Answers ``(state, sentence)`` with ``state`` one of ``fine``, ``absent``,
    ``in use``, ``restored``, ``busy``, ``left`` or ``would``.  A database with no
    good copy is **left exactly where it is** and reported: replacing it would empty
    the box, and the owner has ``ark-restore`` and a bundle for that decision.

    Three rules, and each one is here because of a way this can go wrong:

    * **a dry run writes nothing** — no journal-mode change, no copy.  The phase runs
      ``--dry-run`` to prove itself, and a proof that alters the card is not a proof.
    * **a database somebody is writing is only looked at** (``writer_busy``): the
      daily pass runs while the portal is serving pages, and a repair must never
      replace or re-mode a file another process holds.  ``in_use`` lets a test say so
      without arranging a real lock.
    * **the boot pass is the one that repairs**, because nothing is writing yet.
    """
    target = Path(path)
    if not target.is_file():
        return "absent", ""
    ok, answer = quick_check(target)
    if dry_run:
        if ok:
            return "fine", ""
        return "would", "%s cannot be read (%s); a good copy would be put back." % (
            target.name, answer)
    busy = bool(in_use) if in_use is not None else writer_busy(target)
    if ok:
        if busy:
            return ("in use",
                    "%s is being written right now, so it was only checked — nothing "
                    "on it was changed." % target.name)
        note = harden(target)
        taken = snapshot(target)
        if note:
            return "fine", "%s could not be set for the next cut: %s." % (target.name, note)
        if taken:
            return "fine", "%s is sound; a good copy of it was kept." % target.name
        return "fine", ""
    if busy:
        return ("busy",
                "%s cannot be read (%s) and the box is writing to it, so nothing was "
                "changed — stop the pages and run the pass again when the box is "
                "quiet." % (target.name, answer))
    for candidate in (Path(str(target) + ".good.bak"),
                      Path(str(target) + ark_atomic.BACKUP_SUFFIX)):
        if not candidate.is_file():
            continue
        sound, _ = quick_check(candidate)
        if not sound:
            continue
        if not _copy_over(candidate, target):
            continue
        return ("restored",
                "%s came back unreadable (%s); the good copy from before it was "
                "put back." % (target.name, answer))
    return ("left",
            "%s came back unreadable (%s) and there is no good copy of it on the "
            "card — it was left where it is." % (target.name, answer))


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
    ("backup", "*.partial", STALE_PARTIAL_HOURS),
    ("essentials", "*.partial", STALE_TEMP_HOURS),
    ("kiwix", ".library-*", 1.0),
    ("api/data", ".*.tmp", STALE_TEMP_HOURS),
    ("api/data", "*.good.tmp", STALE_TEMP_HOURS),
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
    below is judged by it), then the small files, then the databases, then the
    catalogue, and the sweeping last — so nothing half-written is judged against a
    clock that is still wrong.
    """
    base = Path(root or ark_root())
    when = now() if current is None else float(current)
    report: Dict[str, Any] = {
        "when": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(when)),
        "dry_run": bool(dry_run),
        "clock": "",
        "json": [],
        "state": {"state": "absent", "said": ""},
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
        state, said = heal_sqlite(path, dry_run=dry_run, in_use=in_use)
        if state in ("restored", "left", "busy", "would", "in use") or said:
            report["databases"].append({"file": str(path.relative_to(base)),
                                        "state": state, "said": said})
        if state == "restored":
            report["healed"] += 1
        if state in ("left", "busy"):
            # A copy was put back, so that is a repair, not a problem to look at — its
            # sentence is still printed.  What needs a human is a database that is
            # *still* unreadable (only the owner may replace it) or one that is broken
            # while the box is writing to it (nothing was touched, and it has to be
            # looked at again when the box is quiet).
            report["problems"].append(said)

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
    for item in report.get("json") or []:
        add(item.get("said"))
    add((report.get("state") or {}).get("said"))
    for item in report.get("databases") or []:
        add(item.get("said"))
    library = report.get("library") or {}
    add(library.get("said"))
    swept = report.get("swept") or []
    if swept:
        lines.append("%d half-written thing%s cleared away: %s" %
                     (len(swept), "" if len(swept) == 1 else "s",
                      ", ".join(swept[:6]) + (", …" if len(swept) > 6 else "")))
    for problem in problems:
        lines.append("Still to look at: " + problem)
    return [line for line in lines if line]
