"""The box's clock — reading it, proposing a correction, and applying one.

Two subjects share this file because they cannot be separated in practice: the
*clock* an administrator can now set, and the *sessions* that a clock correction
used to destroy.

Four layers, each checked its own way:

* **the calendar** — a date is refused or accepted by real ``datetime``
  arithmetic, and the window it must fall inside is absolute (2020..2100) rather
  than a distance from the box's own — because it is exactly the box's own clock
  that a person is correcting.  A test puts the box in 2016 and asks for 2026.
* **the words** — malformed input, impossible dates, a typed offset, a local time
  in the other daylight-saving period, and a device whose clock disagrees.
* **the page** — behind the sign-in and behind TLS, with a form token; the device's
  reading is taken *when the button is pressed*; the proposal is shown before
  anything changes; a stale page and a missing form token change nothing.
* **the bench** — the helper ``ark-set-clock`` itself, in both of its moods: the
  check-only modes that need no privilege, and the root path that is proved to run
  ``date`` with an argv array (never a shell) and to persist the time through
  ``fake-hwclock``.

Nothing here touches the machine's real clock: the "box clock" is a file, the
helper is a stub that writes it, and ``ark_clock.reading`` is pointed at that file
for the length of each test.  That is the isolated environment the task asks for —
the live Pi's clock is never falsified, here or on the box.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from helpers import (BIG_PASSWORD, LAN, SETUP, admin_cookie, claim_box, csrf_of,
                     get, post)

REPO = Path(__file__).resolve().parents[1]
HELPER = REPO / "pi" / "bin" / "ark-set-clock"

#: 2026-11-04T23:40:00Z — a plain reference instant inside the accepted window.
NOW = 1793835600

#: A stand-in for the root helper.  It records the argv it was given, can be told
#: to refuse, and "sets the clock" by writing the instant to a file — the same file
#: ``ark_clock.reading`` is pointed at — so the whole flow, including the read-back
#: that proves the clock really moved, runs without touching this machine.
STUB = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> {calls}
if [[ "${{1:-}}" != "--unix" ]]; then
  echo "stub: only --unix is simulated" >&2
  exit 2
fi
for arg in "$@"; do
  if [[ "$arg" == "--dry-run" ]]; then
    echo "Would set the clock, but this is a dry run."
    exit 0
  fi
done
if [[ -e {refuse} ]]; then
  echo "the system refused the change: pretend hardware said no" >&2
  exit 5
fi
printf '%s' "$2" > {state}
echo "The clock was pretend and is now pretend."
exit 0
"""


@pytest.fixture()
def clock(ark, monkeypatch):
    """A simulated box clock, its stub helper, and the module pointed at both."""
    import ark_clock

    state = ark.root / "pretend-clock"
    state.write_text(str(NOW))
    calls = ark.root / "helper-calls"
    refuse = ark.root / "helper-refuse"
    stub = ark.root / "stub-set-clock"
    stub.write_text(STUB.format(state=state, calls=calls, refuse=refuse))
    stub.chmod(0o755)

    monkeypatch.setenv("ARK_SETCLOCK", str(stub))
    monkeypatch.setenv("ARK_SUDO", "")                  # no sudo in a test
    monkeypatch.setenv("ARK_TIMEDATECTL", "")           # no systemd here
    monkeypatch.setenv("ARK_FAKE_HWCLOCK_FILE", str(ark.root / "fake-hwclock.data"))

    real_reading = ark_clock.reading

    def pretend(now=None):
        stamp = float(state.read_text()) if now is None else float(now)
        return real_reading(stamp)

    monkeypatch.setattr(ark_clock, "reading", pretend)
    return SimpleNamespace(module=ark_clock, state=state, calls=calls, refuse=refuse,
                           stub=stub, root=ark.root)


def _called(calls: Path) -> list:
    if not calls.exists():
        return []
    return [line for line in calls.read_text().splitlines() if line.strip()]


def _box_clock(clock) -> float:
    return float(clock.state.read_text())


def _signed_in(client, ark, *, password: str = BIG_PASSWORD):
    cookie = claim_box(client)
    page = get(client, "/admin/clock", cookie=f"ark_admin={cookie}")
    assert page.status_code == 200, page.text[:600]
    return cookie, page


# ---------------------------------------------------------------------------
#  the calendar window: absolute, so a wrong box clock cannot refuse a right date
# ---------------------------------------------------------------------------
def test_a_correct_date_is_accepted_even_when_the_box_believes_it_is_2016(clock):
    """The rule that must not exist: "refuse anything far from what the box says".

    A box whose clock is in 2016 is exactly the box that needs correcting, so a
    rule measured against its own clock would refuse the right answer.  The window
    is the calendar's, and this is the test that keeps it that way.
    """
    module = clock.module
    seen = module.reading(now=1451606400)          # 2016-01-01T00:00:00Z
    assert seen["utc"].startswith("2016-01-01")
    row = module.proposal("2026-11-04 10:00", current=seen)
    assert row["epoch"] == 1793786400              # 2026-11-04T10:00:00Z
    assert row["direction"] == "forward"
    assert row["delta"] > 300 * 86400


def test_a_date_outside_the_window_is_refused_and_says_why(clock):
    module = clock.module
    for text, word in (("1999-12-31 23:59", "before 1 January 2020"),
                       ("2100-06-01 00:00", "after 1 January 2100")):
        with pytest.raises(module.ClockRefused) as caught:
            module.proposal(text)
        said = str(caught.value)
        assert word in said
        assert "calendar" in said          # the reason, not just the refusal
        assert caught.value.ran is False   # nothing was run, so nothing changed


@pytest.mark.parametrize("text", [
    "2026-02-30 10:00",     # February has no 30th
    "2026-02-29 10:00",     # 2026 is not a leap year
    "2026-04-31 10:00",     # April has 30 days
    "2026-13-01 10:00",     # there is no 13th month
    "2026-00-10 10:00",     # nor a 0th
    "2026-11-00 10:00",
    "2026-11-04 24:00",     # hours run 00..23
    "2026-11-04 10:60",
    "2026-11-04 10:00:60",  # no leap seconds in a civil clock
])
def test_an_impossible_calendar_date_is_refused_by_arithmetic_not_a_pattern(clock, text):
    module = clock.module
    with pytest.raises(module.ClockRefused) as caught:
        module.parse_text(text)
    assert "not a date on the calendar" in str(caught.value)


def test_a_real_leap_day_is_accepted(clock):
    module = clock.module
    naive, tz = module.parse_text("2024-02-29 10:00")
    assert tz is None
    assert naive.day == 29


@pytest.mark.parametrize("text", [
    "", "   ", "tomorrow", "04/11/2026", "2026-11-04T10", "2026-11-04 10:00:00 x",
    "1793835600", "2026-11-04T10:00:00.500Z", "x" * 60,
])
def test_malformed_input_is_refused_with_the_shape_expected(clock, text):
    module = clock.module
    with pytest.raises(module.ClockRefused) as caught:
        module.parse_text(text)
    said = str(caught.value)
    assert "2026-11-04" in said or "longer than a date" in said


# ---------------------------------------------------------------------------
#  timezones: never guessed at
# ---------------------------------------------------------------------------
def test_a_typed_offset_wins_over_everything(clock):
    module = clock.module
    naive, tz = module.parse_text("2026-11-04T09:30:00+01:00")
    assert tz is not None
    epoch = module.epoch_of(naive, tz, basis="local", box_offset=-7 * 3600)
    assert epoch == 1793781000                    # 08:30Z, whatever the box says


def test_utc_is_the_default_and_local_uses_the_offset_shown(clock):
    module = clock.module
    naive, tz = module.parse_text("2026-11-04 10:00")
    assert module.epoch_of(naive, tz, basis="utc") == 1793786400
    assert module.epoch_of(naive, tz, basis="local", box_offset=3600) == 1793782800
    assert module.epoch_of(naive, tz, basis="local", box_offset=-18000) == 1793804400


def test_a_local_time_in_the_other_daylight_period_is_flagged_not_silently_moved(
        clock, monkeypatch):
    """The one honest limit of "box local": the offset is the one shown now."""
    module = clock.module
    monkeypatch.setattr(module, "local_render", lambda stamp: "2026-01-15 09:00:00")
    row = module.proposal("2026-01-15 10:00", basis="local")
    assert any("daylight-saving" in warning for warning in row["warns"])
    monkeypatch.setattr(module, "local_render", lambda stamp: "2026-01-15 10:00:00")
    quiet = module.proposal("2026-01-15 10:00", basis="local")
    assert not any("daylight-saving" in warning for warning in quiet["warns"])


def test_a_disagreeing_device_is_a_suggestion_and_never_the_answer(clock):
    module = clock.module
    three_hours_ahead = module.iso_utc(1793786400 + 3 * 3600)
    row = module.proposal("2026-11-04 10:00", device=three_hours_ahead)
    assert row["epoch"] == 1793786400                       # the typed time, not the device
    assert row["device"] == three_hours_ahead
    assert any("only a suggestion" in warning for warning in row["warns"])


def test_a_nonsense_device_value_is_ignored_rather_than_refused(clock):
    module = clock.module
    for junk in ("", "not a time", "9999-01-01T00:00:00Z", "2026-11-04T10:00"):
        row = module.proposal("2026-11-04 10:00", device=junk)
        assert row["epoch"] == 1793786400
        assert row["device"] == ""


# ---------------------------------------------------------------------------
#  the page: sign-in, TLS, form token, and the proposal before the change
# ---------------------------------------------------------------------------
def test_the_clock_page_needs_the_secure_address(client, clock):
    page = get(client, "/admin/clock", headers={"x-forwarded-proto": "http"})
    assert page.status_code == 403
    assert "Use the secure address" in page.text


def test_the_clock_page_needs_a_sign_in(client, clock):
    page = get(client, "/admin/clock")
    assert page.status_code == 303
    assert "/admin/login" in page.headers["location"]
    changed = post(client, "/admin/clock/apply", {"unix": "1793786400", "csrf": ""})
    assert changed.status_code == 403
    assert _called(clock.calls) == []


def test_the_page_shows_the_date_the_time_and_the_timezone(client, ark, clock):
    cookie, page = _signed_in(client, ark)
    seen = clock.module.reading()
    text = page.text
    assert seen["date"] in text                      # e.g. "Wednesday, 04 November 2026"
    assert seen["local"] in text
    assert seen["timezone"] in text
    assert seen["offset_words"] in text
    assert seen["utc_words"] in text
    assert "What this box thinks the time is" in text


def test_the_device_time_is_read_at_the_press_not_at_the_page(client, ark, clock):
    """The regression this page exists to avoid: a value drawn hours ago.

    The field starts **empty**; a script fills it from ``new Date()`` inside the
    submit handler, so a page left open all afternoon offers the time it is now.
    """
    cookie, page = _signed_in(client, ark)
    text = page.text
    assert re.search(r'id="utc"[^>]*value=""', text)
    assert "addEventListener('submit'" in text
    assert "device.value = at.toISOString()" in text      # captured at the press
    assert "if (!typed)" in text                          # and never over a typed time
    assert "setInterval" in text                          # the readout is live, too


def test_the_form_works_with_no_script_at_all(client, ark, clock):
    """The manual path: a typed reading, an explicit timezone, no hidden fields."""
    cookie, page = _signed_in(client, ark)
    token = csrf_of(page.text)
    assert 'action="/admin/clock/propose"' in page.text
    assert 'method="post"' in page.text
    assert '<noscript>' in page.text
    typed = post(client, "/admin/clock/propose",
                 {"utc": "2026-11-04 10:00", "basis": "utc", "device": "", "csrf": token},
                 cookie=f"ark_admin={cookie}")
    assert typed.status_code == 200, typed.text[:400]
    assert "Check this before it is used" in typed.text
    assert _called(clock.calls) == []          # proposing runs nothing
    assert _box_clock(clock) == NOW            # and changes nothing


def test_the_proposal_shows_both_times_before_anything_is_applied(client, ark, clock):
    cookie, page = _signed_in(client, ark)
    token = csrf_of(page.text)
    shown = post(client, "/admin/clock/propose",
                 {"utc": "2026-11-05 09:30", "basis": "utc", "csrf": token},
                 cookie=f"ark_admin={cookie}")
    assert shown.status_code == 200
    module = clock.module
    assert "2026-11-04 23:40:00 UTC" in shown.text            # what the box says now
    assert "2026-11-05 09:30:00 UTC" in shown.text            # what it would become
    assert "forward by" in shown.text
    assert 'action="/admin/clock/apply"' in shown.text
    assert _called(clock.calls) == []


def test_an_impossible_date_on_the_page_changes_nothing(client, ark, clock):
    cookie, page = _signed_in(client, ark)
    token = csrf_of(page.text)
    refused = post(client, "/admin/clock/propose",
                   {"utc": "2026-02-30 10:00", "basis": "utc", "csrf": token},
                   cookie=f"ark_admin={cookie}")
    assert refused.status_code == 400
    assert "not a date on the calendar" in refused.text
    assert "Nothing was changed" in refused.text
    assert _called(clock.calls) == []


def test_applying_needs_the_form_token(client, ark, clock):
    cookie, _page = _signed_in(client, ark)
    refused = post(client, "/admin/clock/apply",
                   {"unix": "1793786400", "csrf": "not-the-token"},
                   cookie=f"ark_admin={cookie}")
    assert refused.status_code == 403
    assert "form expired" in refused.text.lower() or "Nothing was changed" in refused.text
    assert _called(clock.calls) == []
    assert _box_clock(clock) == NOW


def test_a_stale_page_is_refused_after_the_session_has_gone_quiet(client, ark, clock):
    """The page was opened, left alone, and pressed half an hour later."""
    cookie, page = _signed_in(client, ark)
    token = csrf_of(page.text)
    with ark.db.tx() as conn:                 # 40 minutes of *elapsed* time
        conn.execute("UPDATE admin_sessions SET mono_seen = mono_seen - 2400")
    late = post(client, "/admin/clock/apply",
                {"unix": "1793786400", "csrf": token}, cookie=f"ark_admin={cookie}")
    assert late.status_code == 403
    assert _called(clock.calls) == []
    assert _box_clock(clock) == NOW


def test_applying_runs_the_helper_once_with_validated_arguments(client, ark, clock):
    cookie, page = _signed_in(client, ark)
    token = csrf_of(page.text)
    done = post(client, "/admin/clock/apply",
                {"unix": "1793786400", "device": "2026-11-04T09:59:58Z",
                 "basis": "utc", "source": "your device", "csrf": token},
                cookie=f"ark_admin={cookie}")
    assert done.status_code == 303, done.text[:400]
    calls = _called(clock.calls)
    assert calls == ["--unix 1793786400"]       # exactly once, one plain integer
    assert not any(ch in calls[0] for ch in ";|&$`<>\\\"'")
    assert _box_clock(clock) == 1793786400
    with ark.db.read() as conn:
        row = conn.execute("SELECT result, detail, actor_name FROM audit_log"
                           " WHERE action = 'clock-set' ORDER BY id DESC").fetchone()
    assert row["result"] == "ok"
    assert "clock 2026-11-04T23:40:00Z -> 2026-11-04T10:00:00Z" in row["detail"]
    assert "delta -49200s" in row["detail"]
    assert "device said 2026-11-04T09:59:58Z" in row["detail"]


def test_a_helper_that_refuses_is_not_described_as_having_changed_nothing(
        client, ark, clock):
    clock.refuse.write_text("no")
    cookie, page = _signed_in(client, ark)
    token = csrf_of(page.text)
    failed = post(client, "/admin/clock/apply",
                  {"unix": "1793786400", "csrf": token}, cookie=f"ark_admin={cookie}")
    assert failed.status_code == 400
    assert "may have changed" in failed.text
    assert "pretend hardware said no" in failed.text
    with ark.db.read() as conn:
        row = conn.execute("SELECT result FROM audit_log WHERE action = 'clock-set'"
                           " ORDER BY id DESC").fetchone()
    assert row["result"] == "failed"


def test_a_missing_helper_is_honest_about_it(client, ark, clock, monkeypatch):
    monkeypatch.setenv("ARK_SETCLOCK", str(clock.root / "nowhere"))
    cookie, page = _signed_in(client, ark)
    assert "helper that does it is not installed" in page.text
    token = csrf_of(page.text)
    refused = post(client, "/admin/clock/apply",
                   {"unix": "1793786400", "csrf": token}, cookie=f"ark_admin={cookie}")
    assert refused.status_code == 400
    assert "Nothing was changed" in refused.text
    assert "does not have the clock helper" in refused.text


def test_a_backward_correction_is_recorded_with_both_instants(client, ark, clock):
    """The discontinuity every other timestamp on the box will show."""
    cookie, page = _signed_in(client, ark)
    token = csrf_of(page.text)
    done = post(client, "/admin/clock/apply",
                {"unix": str(NOW - 2 * 3600), "csrf": token},
                cookie=f"ark_admin={cookie}")
    assert done.status_code == 303
    with ark.db.read() as conn:
        row = conn.execute("SELECT detail FROM audit_log WHERE action = 'clock-set'"
                           " ORDER BY id DESC").fetchone()
    assert "delta -7200s" in row["detail"]
    after = get(client, "/admin/clock", cookie=f"ark_admin={cookie}")
    assert "Last corrections" in after.text
    assert "delta -7200s" in after.text


def test_the_history_is_not_readable_without_signing_in(client, ark, clock):
    cookie, page = _signed_in(client, ark)
    token = csrf_of(page.text)
    post(client, "/admin/clock/apply", {"unix": str(NOW - 60), "csrf": token},
         cookie=f"ark_admin={cookie}")
    anonymous = get(client, "/admin/clock")
    assert anonymous.status_code == 303
    assert "clock" not in anonymous.text.lower().replace("clock page", "")


def test_nothing_about_the_clock_is_public(public, clock):
    """The audit line names no household member and no public page carries it."""
    answer = public.client.get("/api/status", headers=LAN)
    assert answer.status_code == 200
    body = json.dumps(answer.json())
    assert "clock-set" not in body
    assert "ark-set-clock" not in body


# ---------------------------------------------------------------------------
#  sessions are judged by elapsed time, not by the wall clock
# ---------------------------------------------------------------------------
class _Clock:
    """A wall clock a person can move, and a monotonic one they cannot."""

    def __init__(self, wall: float, mono: float):
        self.wall, self.mono = wall, mono

    def time(self):
        return self.wall

    def monotonic(self):
        return self.mono


def _mint(ark, monkeypatch, *, wall=1_000_000.0, mono=5_000.0):
    import ark_security
    monkeypatch.setattr(ark_security, "time", _Clock(wall, mono))
    with ark.db.tx() as conn:
        epoch = ark.db.session_epoch(conn)
        issued = ark_security.create_session(conn, admin_id=None, kind="admin",
                                             epoch=epoch)
    return ark_security, issued.token, epoch


def test_a_session_survives_the_clock_being_put_forward(ark, monkeypatch):
    security, token, epoch = _mint(ark, monkeypatch)
    security.time.wall += 3 * 86400           # the clock is corrected forward
    security.time.mono += 2                   # two seconds have really passed
    with ark.db.tx() as conn:
        assert security.load_session(conn, token, epoch) is not None


def test_a_session_survives_the_clock_being_put_back(ark, monkeypatch):
    security, token, epoch = _mint(ark, monkeypatch)
    security.time.wall -= 3 * 86400
    security.time.mono += 2
    with ark.db.tx() as conn:
        assert security.load_session(conn, token, epoch) is not None


def test_a_session_still_ends_after_thirty_quiet_minutes(ark, monkeypatch):
    security, token, epoch = _mint(ark, monkeypatch)
    security.time.mono += security.IDLE_TIMEOUT + 1
    with ark.db.tx() as conn:
        assert security.load_session(conn, token, epoch) is None


def test_a_session_still_ends_after_eight_hours_of_elapsed_time(ark, monkeypatch):
    security, token, epoch = _mint(ark, monkeypatch)
    security.time.mono += security.ABSOLUTE_TIMEOUT + 1
    security.time.wall += security.ABSOLUTE_TIMEOUT + 1
    with ark.db.tx() as conn:
        assert security.load_session(conn, token, epoch) is None


def test_a_session_row_written_before_this_column_existed_still_expires(ark, monkeypatch):
    """The upgrade path: an old row is judged exactly as it was before."""
    security, token, epoch = _mint(ark, monkeypatch)
    with ark.db.tx() as conn:
        conn.execute("UPDATE admin_sessions SET mono_created = 0, mono_seen = 0")
    security.time.wall += security.ABSOLUTE_TIMEOUT + 1
    with ark.db.tx() as conn:
        assert security.load_session(conn, token, epoch) is None


def test_a_monotonic_reading_that_went_backwards_fails_closed(ark, monkeypatch):
    security, token, epoch = _mint(ark, monkeypatch)
    security.time.mono -= 10
    with ark.db.tx() as conn:
        assert security.load_session(conn, token, epoch) is None


def test_failures_recorded_by_a_wrong_clock_do_not_lock_the_owner_out(ark, monkeypatch):
    """A box three days fast recorded its failures in the future.

    Counting them would lock the owner out of the page that fixes the clock — for
    three days.  They are noise, not evidence.
    """
    import ark_security
    now = 1_793_835_600.0
    with ark.db.tx() as conn:
        for i in range(8):
            ark_security.record_login(conn, "admin", "192.0.2.50", False,
                                      now=now + 3 * 86400 + i)
        assert ark_security.login_backoff(conn, "admin", "192.0.2.50", now=now) == 0


def test_a_real_run_of_failures_still_backs_off_and_is_capped(ark):
    import ark_security
    now = 1_793_835_600.0
    with ark.db.tx() as conn:
        for i in range(9):
            ark_security.record_login(conn, "admin", "192.0.2.50", False, now=now - i)
        wait = ark_security.login_backoff(conn, "admin", "192.0.2.50", now=now)
    assert 0 < wait <= ark_security.BACKOFF_MAX


# ---------------------------------------------------------------------------
#  the helper itself, unprivileged
# ---------------------------------------------------------------------------
def _helper_env(root: Path) -> dict:
    env = dict(os.environ)
    env["ARK_ROOT"] = str(REPO / "pi")          # where ark_clock/ark_atomic live
    env["ARK_TIMEDATECTL"] = ""
    env["ARK_FAKE_HWCLOCK_FILE"] = str(root / "fake-hwclock.data")
    return env


def test_the_helper_reads_the_clock_without_any_privilege(tmp_path):
    done = subprocess.run([sys.executable, str(HELPER), "--check"],
                          capture_output=True, text=True, env=_helper_env(tmp_path),
                          timeout=60)
    assert done.returncode == 0, done.stderr
    fields = dict(line.split("=", 1) for line in done.stdout.splitlines() if "=" in line)
    assert fields["clock"] == "ok"
    assert fields["root"] == "no"                   # this proves it needs no privilege
    assert fields["utc"].endswith("Z")
    assert re.match(r"^[+-]\d\d:\d\d$", fields["offset"])


def test_the_helper_refuses_to_set_the_clock_without_root(tmp_path):
    done = subprocess.run([sys.executable, str(HELPER), "--unix", "1793786400"],
                          capture_output=True, text=True, env=_helper_env(tmp_path),
                          timeout=60)
    assert done.returncode == 3
    assert "needs root" in done.stderr


@pytest.mark.parametrize("argv", [
    ["--unix", "12abc"], ["--unix", "-5"], ["--unix", "0"], ["--unix", "9999999999"],
    ["--unix", "²"], ["--unix", "١٢٣"], ["--unix", "1e9"],
    ["--utc", "2026-02-30 10:00"], ["--utc", ""], ["--nonsense"],
])
def test_the_helper_refuses_bad_arguments_before_running_anything(tmp_path, argv):
    done = subprocess.run([sys.executable, str(HELPER), *argv],
                          capture_output=True, text=True, env=_helper_env(tmp_path),
                          timeout=60)
    assert done.returncode == 2
    assert "ark-set-clock:" in done.stderr


def test_the_helper_dry_run_needs_no_privilege_and_changes_nothing(tmp_path):
    done = subprocess.run([sys.executable, str(HELPER), "--utc", "2026-11-04 10:00",
                           "--dry-run"],
                          capture_output=True, text=True, env=_helper_env(tmp_path),
                          timeout=60)
    assert done.returncode == 0, done.stderr
    assert "Would set the clock" in done.stdout


def _load_helper(monkeypatch, root: Path):
    monkeypatch.setenv("ARK_ROOT", str(REPO / "pi"))
    monkeypatch.setenv("ARK_FAKE_HWCLOCK_FILE", str(root / "fake-hwclock.data"))
    monkeypatch.setenv("ARK_TIMEDATECTL", "")
    loader = importlib.machinery.SourceFileLoader("ark_set_clock_helper", str(HELPER))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # The helper writes one plain line per change; keep that inside the test's own
    # throwaway root rather than beside the repository.
    module.ARK_ROOT = str(root)
    return module


def test_the_root_path_sets_and_persists_with_an_argv_array(tmp_path, monkeypatch):
    """Proved as root by standing in for the two things root actually does."""
    helper = _load_helper(monkeypatch, tmp_path)
    monkeypatch.setattr(helper.os, "geteuid", lambda: 0)
    monkeypatch.setattr(helper.ark_clock, "reading",
                        lambda now=None: {"utc": "2026-11-04T23:40:00Z",
                                          "local": "pretend", "timezone": "UTC",
                                          "offset_seconds": 0,
                                          "offset_words": "+00:00",
                                          "synced": None, "stored": None,
                                          "stored_utc": "", "helper": True,
                                          "now": 1793835600.0, "monotonic": 1.0,
                                          "utc_words": "2026-11-04 23:40:00 UTC"})
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(helper.subprocess, "run", fake_run)
    monkeypatch.setattr(helper.time, "time", lambda: 1793786400.0)
    kept = {}
    monkeypatch.setattr(helper.ark_atomic, "save_clock",
                        lambda: kept.setdefault("called", True))
    code = helper.set_clock(1793786400, dry=False)
    assert code == 0
    assert seen["argv"] == ["/bin/date", "-u", "-s", "@1793786400"]   # a list, not a string
    assert "shell" not in seen["kwargs"] or seen["kwargs"]["shell"] is False
    assert kept.get("called") is True                                  # fake-hwclock ran
    journal = tmp_path / "logs" / "clock.log"      # one plain line, never fatal
    assert journal.is_file()
    assert "before=2026-11-04T23:40:00Z" in journal.read_text()


def test_a_change_that_did_not_stick_is_a_failure_not_a_success(tmp_path, monkeypatch):
    helper = _load_helper(monkeypatch, tmp_path)
    monkeypatch.setattr(helper.os, "geteuid", lambda: 0)
    monkeypatch.setattr(helper.ark_clock, "reading",
                        lambda now=None: {"utc": "2026-11-04T00:00:00Z",
                                          "timezone": "UTC", "offset_seconds": 0,
                                          "offset_words": "+00:00", "synced": None,
                                          "stored": None, "stored_utc": "",
                                          "helper": True, "now": 0.0, "monotonic": 0.0,
                                          "local": "x", "utc_words": "x"})
    monkeypatch.setattr(helper.subprocess, "run",
                        lambda argv, **kwargs: SimpleNamespace(returncode=0, stdout=b"",
                                                              stderr=b""))
    monkeypatch.setattr(helper.time, "time", lambda: 1793835600.0)   # unchanged
    assert helper.set_clock(1793786400, dry=False) == helper.EXIT_DID_NOT_STICK


def test_a_refusal_from_the_system_is_reported_with_its_reason(tmp_path, monkeypatch,
                                                              capsys):
    helper = _load_helper(monkeypatch, tmp_path)
    monkeypatch.setattr(helper.os, "geteuid", lambda: 0)
    monkeypatch.setattr(helper.ark_clock, "reading",
                        lambda now=None: {"utc": "x", "timezone": "UTC",
                                          "offset_seconds": 0, "offset_words": "+00:00",
                                          "synced": None, "stored": None,
                                          "stored_utc": "", "helper": True, "now": 0.0,
                                          "monotonic": 0.0, "local": "x", "utc_words": "x"})
    monkeypatch.setattr(helper.subprocess, "run",
                        lambda argv, **kwargs: SimpleNamespace(
                            returncode=1, stdout=b"", stderr=b"Operation not permitted"))
    assert helper.set_clock(1793786400, dry=False) == helper.EXIT_FAILED
    assert "Operation not permitted" in capsys.readouterr().err


# ---------------------------------------------------------------------------
#  no shell anywhere on the path
# ---------------------------------------------------------------------------
def test_neither_the_page_nor_the_helper_can_reach_a_shell():
    for path in (REPO / "pi" / "api" / "ark_clock.py", HELPER):
        text = path.read_text(encoding="utf-8")
        assert "shell=True" not in text
        assert "os.system(" not in text
        assert "subprocess.call(" not in text
        assert "eval(" not in text


# ---------------------------------------------------------------------------
#  a field a stranger can craft is a sentence, never a traceback
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("crafted", [
    "--5", "-", " ", "", "1793786400.5", "۱٧۹۳۷۸۶۴۰۰",
    "²", "1e9", "0x10", "1_000", "9" * 400,
])
def test_a_crafted_instant_field_is_refused_in_words(client, ark, clock, crafted):
    """`int()` and ``str.isdigit()`` disagree about several of these.

    A signed-in page is not a licence to 500: each of these must come back as a
    refusal that changes nothing, and the helper must never be reached.
    """
    cookie, page = _signed_in(client, ark)
    token = csrf_of(page.text)
    refused = post(client, "/admin/clock/apply", {"unix": crafted, "csrf": token},
                   cookie=f"ark_admin={cookie}")
    assert refused.status_code == 400, (crafted, refused.status_code)
    assert "did not arrive as a time" in refused.text
    assert "Nothing was changed" in refused.text
    assert _called(clock.calls) == []
    assert _box_clock(clock) == NOW


def test_surrounding_whitespace_on_the_instant_is_trimmed_not_refused(client, ark, clock):
    """A field with a space round it is still a number — the shape is what matters."""
    cookie, page = _signed_in(client, ark)
    token = csrf_of(page.text)
    done = post(client, "/admin/clock/apply", {"unix": " 1793786400\n", "csrf": token},
                cookie=f"ark_admin={cookie}")
    assert done.status_code == 303
    assert _called(clock.calls) == ["--unix 1793786400"]


def test_the_clock_is_not_read_while_the_records_are_held_for_writing(client, ark, clock,
                                                                     monkeypatch):
    """Reading the clock can spend seconds in systemd; that must not block writers.

    Every other writer on the box waits for the records' write lock, so a page view
    that held it across `timedatectl` would make a posted message wait too.
    """
    import ark_db
    real_tx = ark_db.tx
    held = []

    class Watch:
        def __init__(self, inner):
            self.inner = inner

        def __enter__(self):
            conn = self.inner.__enter__()
            held.append(True)
            return conn

        def __exit__(self, *exc):
            held.pop()
            return self.inner.__exit__(*exc)

    def watching():
        return Watch(real_tx())

    monkeypatch.setattr(ark_db, "tx", watching)
    module = clock.module
    real_reading = module.reading

    def reading_says_who(now=None):
        assert not held, "the clock was read while the write lock was held"
        return real_reading(now)

    monkeypatch.setattr(module, "reading", reading_says_who)
    cookie, page = _signed_in(client, ark)
    assert page.status_code == 200
    token = csrf_of(page.text)
    shown = post(client, "/admin/clock/propose",
                 {"utc": "2026-11-04 10:00", "basis": "utc", "csrf": token},
                 cookie=f"ark_admin={cookie}")
    assert shown.status_code == 200
    done = post(client, "/admin/clock/apply", {"unix": "1793786400", "csrf": token},
                cookie=f"ark_admin={cookie}")
    assert done.status_code == 303
