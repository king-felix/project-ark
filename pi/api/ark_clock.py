"""Project ARK — what time the box thinks it is, and the person who can correct it.

A Pi has no battery-backed clock.  After a power cut it wakes up believing it is
the day the card was written, and ``ark_self_heal.fix_clock`` puts it forward from
the newest thing on the card.  That rule only ever moves the clock **forward**, and
only when the card carries evidence — so two cases are left for a human:

* the clock is **ahead of the truth** (a wrong date was written once, and every
  file made since agrees with it), or
* the clock is **far enough behind** that the card's own evidence is either absent
  (a freshly cloned card) or too far ahead to be believed
  (``ark_self_heal.CLOCK_MAX_AHEAD``).

This module is the owner's door for both.  It is deliberately small and has one
hard rule: **it never sets the clock itself.**  ``ark-set-clock`` is the only
writer (root, validated arguments, argv-only), exactly as ``ark-wifi-add`` is the
only writer of a Wi-Fi profile.  Everything here either *reads* the clock or asks
that helper to change it.

Three decisions worth knowing before reading the code:

* **The device's clock is a suggestion, never an authority.**  A phone's clock is
  refreshed by its own network and is usually right to the second, so the page
  offers it — but it arrives as a *claim* with the rest of the form, it is
  validated like anything else a stranger can type, and the audit line records it
  as the suggestion it was.  It is also captured **when the button is pressed**,
  not when the page was drawn: a page left open all afternoon must not offer
  yesterday's time as "now".
* **The bounds are absolute, not relative.**  The obvious rule — "refuse a date
  more than a day from what the box says" — is circular: it is exactly the box's
  wrong clock that the person is trying to fix, so a *correct* date would be
  refused by the broken thing being corrected.  ``EARLIEST``/``LATEST`` are
  calendar bounds instead, and the sentence a refusal prints says so.
* **A timezone is always explicit.**  Manual entry is read as UTC, or as the box's
  own local time *at the offset printed beside the field*, or as the offset the
  person typed (``2026-11-04T09:30:00+01:00``).  A bare local time is never
  guessed at.
"""

from __future__ import annotations

import calendar
import os
import re
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
#  the window a date has to fall inside
# ---------------------------------------------------------------------------
#: 2020-01-01T00:00:00Z.  Before this nothing on the card can be right: the
#: project, its images and every file it ships are newer, and a date this old is
#: a typo (``2016`` for ``2026``) rather than a correction.
EARLIEST = 1577836800

#: 2100-01-01T00:00:00Z.  After this the box's own machinery stops being able to
#: describe itself honestly — TLS certificates, the certificate's 825-day ceiling,
#: SQLite's dates and every "how long ago" on every page.  A date beyond it is a
#: century typo, not a fact.
LATEST = 4102444800

#: The two bounds as words, for a refusal that has to explain itself.
EARLIEST_WORDS = "1 January 2020"
LATEST_WORDS = "1 January 2100"

#: How far the applied clock may differ from what was asked for before the helper
#: is called a failure.  Setting a clock is a single syscall; a couple of seconds
#: of drift between asking and reading back is normal, a minute is not.
SET_TOLERANCE = 5

#: How long the helper may take.  It runs `date` and `fake-hwclock`; both are
#: instant, and a helper that hangs must not hang the page.
RUN_SECONDS = 20

#: A device timestamp further than this from the typed one is shown as a
#: disagreement rather than silently preferred — neither is trusted.
DEVICE_AGREES = 90

#: A date field, shaped exactly.  **Shape only** — the calendar check is
#: ``datetime()``'s job below, because a regular expression that tries to know
#: which Februaries have 29 days is a regular expression that is wrong once a
#: year.  ``2026-02-30T10:00`` matches this and is then refused by the calendar.
_SHAPE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::(\d{2}))?"
    r"(?:\s*(Z|[+-]\d{2}:?\d{2}))?$")

_MAX_TEXT = 40


class ClockRefused(Exception):
    """A refusal in words a person can act on.  Never a traceback.

    ``ran`` says whether the root helper was actually reached: a refusal the page
    made (an impossible date, an hour of ``25``) never ran anything, so the clock
    cannot have moved.  A page that says "nothing was changed" is only honest when
    it knows that.
    """

    def __init__(self, words, *, ran: bool = False):
        super().__init__(words)
        self.ran = ran


# ---------------------------------------------------------------------------
#  where things are — read at call time, so a test can point them elsewhere
# ---------------------------------------------------------------------------
def ark_root() -> Path:
    return Path(os.environ.get("ARK_ROOT") or "/srv/ark")


def helper_path() -> str:
    return os.environ.get("ARK_SETCLOCK", "/usr/local/sbin/ark-set-clock")


def tz_file() -> Path:
    return Path(os.environ.get("ARK_TZ_FILE", "/etc/timezone"))


def sudo_prefix() -> list:
    """``['/usr/bin/sudo', '-n']`` on the box; nothing at all in a test."""
    value = os.environ.get("ARK_SUDO", "/usr/bin/sudo")
    return [value, "-n"] if value else []


# ---------------------------------------------------------------------------
#  reading — never raises, never guesses
# ---------------------------------------------------------------------------
def iso_utc(stamp: float) -> str:
    return datetime.fromtimestamp(float(stamp), timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def offset_words(seconds: int) -> str:
    """``+01:00`` / ``-05:30`` / ``+00:00`` — the way a person writes an offset."""
    sign = "-" if seconds < 0 else "+"
    seconds = abs(int(seconds))
    return "%s%02d:%02d" % (sign, seconds // 3600, (seconds % 3600) // 60)


def timezone_name() -> str:
    """The zone the box displays times in, as a name (``Europe/London``) if known.

    Debian keeps ``/etc/timezone`` as one line; a box configured another way has a
    symlink at ``/etc/localtime``.  If neither answers, the C library's own
    abbreviation (``GMT``/``UTC``) is as much as can be said — and that is said.
    """
    try:
        name = tz_file().read_text(encoding="utf-8", errors="replace").strip()
        if name and "\n" not in name:
            return name
    except OSError:
        pass
    try:
        target = os.readlink("/etc/localtime")
        marker = "zoneinfo/"
        if marker in target:
            return target.split(marker, 1)[1]
    except OSError:
        pass
    return time.tzname[0] if time.tzname else "UTC"


def _timedatectl() -> Dict[str, str]:
    """``NTP``/``NTPSynchronized`` if systemd can answer; empty if it cannot."""
    tool = os.environ.get("ARK_TIMEDATECTL", "timedatectl")
    if not tool:
        return {}
    try:
        done = subprocess.run([tool, "show", "-p", "NTP", "-p", "NTPSynchronized"],
                              capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return {}
    values: Dict[str, str] = {}
    for line in (done.stdout or "").splitlines():
        if "=" in line:
            key, _sep, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def stored_time() -> Optional[float]:
    """What ``fake-hwclock`` has written down for the next cold boot.

    ``None`` means "not measured" — the file is absent on a box where the tool is
    not installed, and that is a supported state, not a zero.
    """
    path = Path(os.environ.get("ARK_FAKE_HWCLOCK_FILE", "/etc/fake-hwclock.data"))
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    if not text:
        return None
    for shape in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return float(calendar.timegm(datetime.strptime(text, shape).timetuple()))
        except ValueError:
            continue
    return None


def reading(now: Optional[float] = None) -> Dict[str, Any]:
    """Everything the page shows about the box's clock, in one dictionary."""
    when = time.time() if now is None else float(now)
    local = datetime.fromtimestamp(when).astimezone()
    parsed = _timedatectl()
    offset = int(local.utcoffset().total_seconds()) if local.utcoffset() else 0
    kept = stored_time()
    return {
        "now": when,
        "utc": iso_utc(when),
        "utc_words": datetime.fromtimestamp(when, timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S UTC"),
        "local": local.strftime("%Y-%m-%d %H:%M:%S"),
        "date": local.strftime("%A, %d %B %Y"),
        "timezone": timezone_name(),
        "offset_seconds": offset,
        "offset_words": offset_words(offset),
        "ntp": _truth(parsed.get("NTP")),
        "synced": _truth(parsed.get("NTPSynchronized")),
        "stored": kept,
        "stored_utc": iso_utc(kept) if kept else "",
        "helper": os.path.exists(helper_path()),
        "monotonic": time.monotonic(),
    }


def _truth(value: Optional[str]) -> Optional[bool]:
    if value is None:
        return None
    lowered = value.strip().lower()
    if lowered in ("yes", "true", "1"):
        return True
    if lowered in ("no", "false", "0"):
        return False
    return None


def local_render(stamp: float) -> str:
    """How the box will print that instant in its own zone, once it is set."""
    return datetime.fromtimestamp(float(stamp)).astimezone().strftime(
        "%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
#  reading what a person typed
# ---------------------------------------------------------------------------
def parse_text(text: str) -> Tuple[datetime, Optional[timezone]]:
    """``YYYY-MM-DD HH:MM[:SS]`` (or with a ``T``), optionally with an offset.

    Returns the naive datetime and the offset the person *typed*, if any.  Raises
    ``ClockRefused`` with a sentence that shows the shape expected.  The calendar
    is checked by constructing a real ``datetime`` — so 30 February, 31 April, an
    hour of 24 and a minute of 60 are all refused by the same code that knows why.
    """
    raw = (text or "").strip()
    if not raw:
        raise ClockRefused("Enter the date and time, for example 2026-11-04 09:30.")
    if len(raw) > _MAX_TEXT:
        raise ClockRefused(
            "That is longer than a date and time. Use YYYY-MM-DD HH:MM, for "
            "example 2026-11-04 09:30.")
    match = _SHAPE.match(raw)
    if not match:
        raise ClockRefused(
            "Use the date and time as four digits, dash, two, dash, two — then "
            "the time. For example 2026-11-04 09:30 (or 09:30:00 for seconds).")
    year, month, day = (int(match.group(i)) for i in (1, 2, 3))
    hour, minute = int(match.group(4)), int(match.group(5))
    second = int(match.group(6) or 0)
    try:
        naive = datetime(year, month, day, hour, minute, second)
    except ValueError as exc:
        raise ClockRefused("That is not a date on the calendar: %s." % exc) from None
    suffix = match.group(7)
    tz = None
    if suffix:
        if suffix.upper() == "Z":
            tz = timezone.utc
        else:
            sign = -1 if suffix[0] == "-" else 1
            digits = suffix[1:].replace(":", "")
            hours, minutes = int(digits[:2]), int(digits[2:])
            if hours > 23 or minutes > 59:
                raise ClockRefused("That offset is not a real timezone: %s." % suffix)
            tz = timezone(sign * timedelta(hours=hours, minutes=minutes))
    return naive, tz


def epoch_of(naive: datetime, tz: Optional[timezone], *,
             basis: str = "utc", box_offset: int = 0) -> float:
    """The instant a typed clock reading names.  Never guesses a timezone.

    * an offset the person typed wins, whatever the basis says;
    * ``basis="utc"`` reads the text as UTC;
    * ``basis="local"`` reads it as the box's own local time at ``box_offset`` —
      the offset printed beside the field, so a date in the other daylight-saving
      period is off by that shift *and visible as such* in the proposal, rather
      than silently reinterpreted.
    """
    if tz is not None:
        moment = naive.replace(tzinfo=tz)
        return float(moment.timestamp())
    seconds = 0 if basis != "local" else int(box_offset)
    return float(calendar.timegm(naive.timetuple()) - seconds)


def bounds_problem(stamp: float) -> str:
    """Why this instant is outside the calendar window — or ``""`` if it is in it."""
    if stamp < EARLIEST:
        return ("That date is before %s, which is outside the years this box can "
                "describe. Check the year. This limit is about the calendar, not "
                "about what this box currently believes, so a correct date is never "
                "refused because the box's own clock is wrong." % EARLIEST_WORDS)
    if stamp > LATEST:
        return ("That date is after %s, which is outside the years this box can "
                "describe. Check the year — a century typo is the usual cause. This "
                "limit is about the calendar, not about what this box currently "
                "believes, so a correct date is never refused because the box's own "
                "clock is wrong." % LATEST_WORDS)
    return ""


def _sane_epoch(stamp: float, *, ran: bool = False) -> int:
    problem = bounds_problem(stamp)
    if problem:
        raise ClockRefused(problem, ran=ran)
    return int(round(stamp))


# ---------------------------------------------------------------------------
#  the proposal — what would change, before anything changes
# ---------------------------------------------------------------------------
def proposal(text: str, *, basis: str = "utc", device: str = "",
             now: Optional[float] = None,
             current: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """What the box would say if it were set to the time a person typed.

    Nothing is written and no helper is run: this is the page's *before* picture,
    computed from the form alone, so a mistake is caught while it is still a
    sentence rather than a clock.
    """
    current = current or reading(now)
    basis = "local" if str(basis).lower() == "local" else "utc"
    naive, typed_tz = parse_text(text)
    stamp = epoch_of(naive, typed_tz, basis=basis,
                     box_offset=current["offset_seconds"])
    epoch = _sane_epoch(stamp)
    delta = int(epoch - int(current["now"]))
    device_epoch = device_hint(device)
    typed_words = naive.strftime("%Y-%m-%d %H:%M:%S")
    will_say = local_render(epoch)
    source = "your device" if device_epoch is not None else "typed"
    if typed_tz is not None:
        source = "typed, with its own offset"
    warns: List[str] = []
    if basis == "local" and typed_tz is None and will_say != typed_words:
        warns.append(
            "You entered %s as the box's own local time, but once the box is set it "
            "will show %s — that date falls in the other daylight-saving period. If "
            "you meant the clock reading you typed, enter it as UTC instead."
            % (typed_words, will_say))
    if device_epoch is not None and abs(device_epoch - epoch) > DEVICE_AGREES:
        warns.append(
            "Your device says %s, which is %d seconds away from the time typed. The "
            "typed time is what will be used — your device is only a suggestion."
            % (iso_utc(device_epoch), int(device_epoch - epoch)))
    if current["synced"] is True and abs(delta) > 60:
        warns.append(
            "The box is currently keeping its time from the network. Setting it by "
            "hand now is a correction for a box that is offline; while it stays "
            "connected it may set itself back to network time within a minute.")
    return {
        "epoch": epoch,
        "utc": iso_utc(epoch),
        "utc_words": datetime.fromtimestamp(epoch, timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S UTC"),
        "local": local_render(epoch),
        "typed": typed_words,
        "typed_offset": offset_words(int(typed_tz.utcoffset(None).total_seconds()))
                        if typed_tz is not None else "",
        "basis": basis,
        "delta": delta,
        "direction": ("forward" if delta > 0 else
                      "backward" if delta < 0 else "none"),
        "device": iso_utc(device_epoch) if device_epoch is not None else "",
        "device_epoch": device_epoch,
        "source": source,
        "warns": warns,
        "before": current,
    }


def device_hint(value: str) -> Optional[float]:
    """The device's own claim, if the browser sent one that parses.

    A suggestion only: a malformed or absurd value is ignored, never a refusal.
    The form must work with no JavaScript at all, and an odd hidden field is not
    a reason to refuse a person's correction.
    """
    raw = (value or "").strip()
    if not raw or len(raw) > 40:
        return None
    try:
        moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        return None
    stamp = float(moment.timestamp())
    if bounds_problem(stamp):
        return None
    return stamp


# ---------------------------------------------------------------------------
#  applying it — through the one root helper
# ---------------------------------------------------------------------------
def apply(epoch: int, *, dry_run: bool = False,
          now: Optional[float] = None) -> Dict[str, Any]:
    """Set the box's clock through ``ark-set-clock``, and read it back.

    Raises ``ClockRefused`` on every failure with a sentence, and ``ran`` says
    whether the helper was reached.  A helper that answered but left the clock
    where it was is a failure too — "present" is not "works".
    """
    _sane_epoch(float(epoch))
    before = reading(now)
    argv = sudo_prefix() + [helper_path(), "--unix", str(int(epoch))]
    if dry_run:
        argv.append("--dry-run")
    if not os.path.exists(helper_path()):
        raise ClockRefused(
            "This box does not have the clock helper installed, so the time cannot "
            "be changed from here. Nothing was changed. A console or SSH session can "
            "still set it: sudo ark-set-clock --show")
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=RUN_SECONDS)
    except subprocess.TimeoutExpired:
        raise ClockRefused(
            "The box's clock helper did not answer in %d seconds. Nothing is known "
            "about whether the clock changed — check it again before doing anything "
            "else." % RUN_SECONDS, ran=True) from None
    except OSError as exc:
        raise ClockRefused(
            "The box could not run its clock helper (%s). Nothing was changed." % exc,
            ran=False) from None
    said = last_line(done.stdout or "") or last_line(done.stderr or "")
    if done.returncode != 0:
        raise ClockRefused(
            "The clock was not changed. The box's helper said: %s"
            % (said or "no reason given (code %d)" % done.returncode), ran=True)
    after = reading()
    drift = int(after["now"] - int(epoch))
    if not dry_run and abs(drift) > SET_TOLERANCE:
        raise ClockRefused(
            "The box accepted the change but still says %s, which is %d seconds from "
            "the time asked for. Something else on this box is setting the clock — "
            "check whether it keeps its time from the network." %
            (after["utc_words"], drift), ran=True)
    return {
        "before": before,
        "after": after,
        "asked": int(epoch),
        "delta": int(int(epoch) - int(before["now"])),
        "dry_run": bool(dry_run),
        "words": said,
    }


def last_line(text: str) -> str:
    for line in reversed((text or "").splitlines()):
        if line.strip():
            return line.strip()[:300]
    return ""


# ---------------------------------------------------------------------------
#  the record — what changed, when, and by how much
# ---------------------------------------------------------------------------
def audit_detail(result: Dict[str, Any], proposal_row: Dict[str, Any]) -> str:
    """One line for the history: before, after, the size of the jump, and why.

    ``audit_log.detail`` is capped at 300 characters by ``ark_db.audit``, so this
    is written to fit: the two instants are what explain a discontinuity in every
    other timestamp on the box, and the device's claim is kept because it is the
    only record of *why* a person typed that number.
    """
    before, after = result["before"], result["after"]
    bits = ["clock %s -> %s" % (before["utc"], after["utc"]),
            "delta %+ds" % int(result["delta"]),
            "from %s" % proposal_row.get("source", "typed")]
    if proposal_row.get("device"):
        bits.append("device said %s" % proposal_row["device"])
    if proposal_row.get("basis") == "local":
        bits.append("read as box-local %s" % before["offset_words"])
    return "; ".join(bits)[:300]


def history(conn, limit: int = 6) -> List[Any]:
    """The last few clock corrections, straight out of the one audit table."""
    rows = conn.execute(
        "SELECT ts, actor_name, result, detail FROM audit_log WHERE action = 'clock-set'"
        " ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
    return rows
