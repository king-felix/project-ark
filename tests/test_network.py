"""Stage 5 — the box survives losing its network *after* boot.

ark-boot-guard answers "which network?" once, on the way up.  Everything here is
about that question being asked again every minute afterwards, and about the two
ways the answer must never cost a reader anything:

* the hotspot is never taken away from somebody using it;
* an answer the owner gave (``ark-mode home`` / ``ark-mode hotspot``) is not
  quietly reversed.

The decision is a shell function, so these tests run the real script under
``bash`` rather than re-implementing it: ``--decide`` needs no radio, no root and
no NetworkManager, and the observer is driven by a fake ``nmcli``/``iw``.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[1]
WATCH = REPO / "pi" / "bin" / "ark-net-watch"
MODE = REPO / "pi" / "bin" / "ark-mode"
SYSTEMD = REPO / "pi" / "systemd"

# A stand-in for NetworkManager that answers the three questions the watchdog
# asks and nothing else.  The values come from the environment, so one file can
# describe "at home", "broadcasting with a reader on it", and "nothing at all".
FAKE_NMCLI = """#!/bin/sh
case "$*" in
  *"--active"*)             printf '%s\\n' "${FAKE_ACTIVE:-}" ;;
  *"802-11-wireless.ssid"*) printf '%s\\n' "${FAKE_SSID:-HouseNet}" ;;
  *"wifi list"*)            printf '%s\\n' "${FAKE_VISIBLE:-}" ;;
esac
"""

FAKE_IW = """#!/bin/sh
case "$*" in
  *" info"*)        exit 0 ;;
  *"station dump"*) printf '%s\\n' "${FAKE_STATIONS:-}" ;;
esac
"""


def run_watch(*args, env=None, timeout=40):
    base = {**os.environ, "ENV_FILE": "/dev/null"}
    base.update(env or {})
    return subprocess.run(["bash", str(WATCH), *[str(a) for a in args]],
                          capture_output=True, text=True, env=base, timeout=timeout)


def decide(*values, **env):
    done = run_watch("--decide", *values, env=env)
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


@pytest.fixture()
def radio(tmp_path):
    """Fake nmcli + iw on disk, so the observer can be asked real questions."""
    folder = tmp_path / "fakebin"
    folder.mkdir()
    nmcli = folder / "nmcli"
    nmcli.write_text(FAKE_NMCLI, encoding="utf-8")
    iw = folder / "iw"
    iw.write_text(FAKE_IW, encoding="utf-8")
    for path in (nmcli, iw):
        path.chmod(0o755)
    return SimpleNamespace(nmcli=nmcli, iw=iw)


def watch(tmp_path, radio, *, dry=True, mode="home", active="", visible="",
          stations="", state=None, ark_mode="/bin/false", extra=None):
    """Run the watchdog against the fakes; returns (result, state file)."""
    mode_file = tmp_path / "mode"
    mode_file.write_text(mode + "\n", encoding="utf-8")
    state_file = tmp_path / "logs" / "net-watch.state"
    if state is not None:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(state, encoding="utf-8")
    env = {
        "ARK_ROOT": str(tmp_path / "srv-ark"),
        "ARK_LOG_DIR": str(tmp_path / "logs"),
        "ARK_NET_STATE": str(state_file),
        "MODE_FILE": str(mode_file),
        "PIN_FILE": str(tmp_path / "mode.pinned"),
        "NMCLI": str(radio.nmcli),
        "IW": str(radio.iw),
        "FAKE_ACTIVE": active,
        "FAKE_VISIBLE": visible,
        "FAKE_STATIONS": stations,
        "ARK_MODE_BIN": ark_mode,
        "WIFI_DEV": "wlan0",
    }
    env.update(extra or {})
    args = ["--dry-run"] if dry else []
    return run_watch(*args, env=env), state_file


# ---------------------------------------------------------------------------
#  the decision table
# ---------------------------------------------------------------------------
# mode, any-link, hotspot, home-in-range, clients, pinned, lost, back, blocked
@pytest.mark.parametrize("values,expected", [
    (("home", 1, 0, 0, 0, 0, 0, 0, 0), "stay"),        # at home, nothing to do
    (("home", 0, 0, 0, 0, 0, 1, 0, 0), "stay"),        # one miss is a blink
    (("home", 0, 0, 0, 0, 0, 2, 0, 0), "to-hotspot"),  # two misses is a network
    (("hotspot", 0, 0, 0, 0, 0, 0, 0, 0), "to-hotspot"),   # the access point died
    (("unknown", 0, 0, 0, 0, 0, 0, 0, 0), "to-hotspot"),   # no record, nothing up
    (("hotspot", 0, 1, 1, 0, 0, 0, 3, 0), "to-home"),  # home back, nobody here
    (("hotspot", 0, 1, 1, 0, 0, 0, 2, 0), "stay"),     # not quiet long enough
    (("hotspot", 0, 1, 1, 1, 0, 0, 9, 0), "stay"),     # somebody is reading
    (("hotspot", 0, 1, 1, -1, 0, 0, 9, 0), "stay"),    # cannot count: assume yes
    (("hotspot", 0, 1, 0, 0, 0, 0, 9, 0), "stay"),     # home still out of range
    (("hotspot", 0, 1, 1, 0, 1, 0, 9, 0), "stay"),     # the owner chose this
    (("hotspot", 0, 1, 1, 0, 0, 0, 9, 1), "stay"),     # a return already failed
    (("hotspot", 1, 1, 1, 0, 0, 0, 9, 0), "stay"),     # a cable is carrying it
    (("home", 0, 0, 0, 0, 1, 5, 0, 0), "to-hotspot"),  # a pin never leaves it dark
])
def test_the_decision_table(values, expected):
    assert decide(*values) == expected


def test_the_thresholds_are_counted_not_guessed():
    assert decide("home", 0, 0, 0, 0, 0, 1, 0, 0) == "stay"
    assert decide("home", 0, 0, 0, 0, 0, 2, 0, 0) == "to-hotspot"
    assert decide("hotspot", 0, 1, 1, 0, 0, 0, 2, 0) == "stay"
    assert decide("hotspot", 0, 1, 1, 0, 0, 0, 3, 0) == "to-home"


def test_decide_refuses_to_guess_when_a_value_is_missing():
    done = run_watch("--decide", "home", 1, 0)
    assert done.returncode == 2
    assert "9 values" in done.stderr



# ---------------------------------------------------------------------------
#  the observer — what the box can actually see
# ---------------------------------------------------------------------------
def test_a_healthy_home_network_is_left_alone(tmp_path, radio):
    done, _ = watch(tmp_path, radio, active="home-wifi:802-11-wireless")
    assert done.returncode == 0, done.stderr
    assert "link=1" in done.stdout
    assert done.stdout.rstrip().endswith("-> stay")


def test_a_lost_network_is_counted_then_rescued(tmp_path, radio):
    first, _ = watch(tmp_path, radio, state="lost_streak=0\n")
    assert "lost=1" in first.stdout, first.stdout
    assert first.stdout.rstrip().endswith("-> stay")

    second, _ = watch(tmp_path, radio, state="lost_streak=1\n")
    assert "lost=2" in second.stdout, second.stdout
    assert second.stdout.rstrip().endswith("-> to-hotspot")


def test_the_hotspot_is_never_taken_from_a_reader(tmp_path, radio):
    done, _ = watch(tmp_path, radio, mode="hotspot",
                    active="ark-hotspot:802-11-wireless", visible="HouseNet",
                    stations="Station 11:22:33:44:55:66 (on wlan0)\n",
                    state="home_streak=5\n")
    assert "clients=1" in done.stdout, done.stdout
    assert done.stdout.rstrip().endswith("-> stay")


def test_an_uncountable_hotspot_is_treated_as_occupied(tmp_path, radio):
    # iw cannot see the interface (not in AP mode yet): -1, never "nobody here".
    done, _ = watch(tmp_path, radio, mode="hotspot",
                    active="ark-hotspot:802-11-wireless", visible="HouseNet",
                    state="home_streak=5\n",
                    extra={"IW": str(tmp_path / "missing-iw")})
    assert "clients=-1" in done.stdout, done.stdout
    assert done.stdout.rstrip().endswith("-> stay")


def test_it_returns_home_once_the_network_is_back_and_nobody_is_reading(tmp_path, radio):
    done, _ = watch(tmp_path, radio, mode="hotspot",
                    active="ark-hotspot:802-11-wireless", visible="HouseNet",
                    state="home_streak=2\n")
    assert "clients=0" in done.stdout, done.stdout
    assert "back=3" in done.stdout, done.stdout
    assert done.stdout.rstrip().endswith("-> to-home")


def test_a_dry_run_writes_nothing_at_all(tmp_path, radio):
    done, state_file = watch(tmp_path, radio, state="lost_streak=1\n")
    assert done.returncode == 0
    assert state_file.read_text(encoding="utf-8") == "lost_streak=1\n"
    assert not (tmp_path / "logs" / "net-watch.log").exists()


def test_a_quiet_check_leaves_no_log_line(tmp_path, radio):
    done, _ = watch(tmp_path, radio, dry=False, active="home-wifi:802-11-wireless")
    assert done.returncode == 0, done.stderr
    assert not (tmp_path / "logs" / "net-watch.log").exists()


def test_a_lost_network_really_switches_the_radio(tmp_path, radio):
    """The wiring, not just the decision: the hotspot is asked for, and logged."""
    calls = tmp_path / "ark-mode.calls"
    mode_file = tmp_path / "mode"
    fake = tmp_path / "ark-mode"
    fake.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$*" >> "{calls}"\n'
        f'printf "hotspot\\n" > "{mode_file}"\n',
        encoding="utf-8")
    fake.chmod(0o755)

    done, state_file = watch(tmp_path, radio, dry=False, state="lost_streak=1\n",
                             ark_mode=str(fake))
    assert done.returncode == 0, done.stderr
    assert calls.read_text(encoding="utf-8").split() == ["hotspot", "--auto"]
    assert state_file.read_text(encoding="utf-8").startswith("lost_streak=2")
    log = (tmp_path / "logs" / "net-watch.log").read_text(encoding="utf-8")
    assert "to-hotspot" in log
    assert "broadcasting" in log


# ---------------------------------------------------------------------------
#  the pin, the units, and the two phases that install / verify them
# ---------------------------------------------------------------------------
def test_a_pin_is_written_only_by_a_person():
    """ark-mode's own --auto means "the box did this", so no pin is left."""
    text = MODE.read_text(encoding="utf-8")
    assert 'PIN_FILE="${PIN_FILE:-/etc/ark/mode.pinned}"' in text
    assert "pin home" in text
    assert "pin hotspot" in text
    assert '[[ "${1:-}" == "--auto" ]]' in text
    assert "hotspot --auto" in WATCH.read_text(encoding="utf-8")


def test_a_cold_boot_broadcasts_unless_the_owner_says_otherwise():
    """The shipped default is the hotspot, and the guard asks for it by name."""
    root = Path(__file__).resolve().parents[1] / "pi"
    mode = (root / "bin" / "ark-mode").read_text()
    guard = (root / "bin" / "ark-boot-guard").read_text()
    step = (root / "steps" / "70-network.sh").read_text()
    assert 'local want="${BOOT_MODE:-hotspot}"' in mode        # default is broadcast
    assert "boot)    cmd_boot" in mode
    assert '"$dry" == "--dry-run"' in mode                     # provable with no radio
    assert "/usr/local/bin/ark-mode boot" in guard              # the guard asks "boot"
    assert "ark-mode auto" not in guard                        # ...and never the old call
    assert 'BOOT_MODE=' in step and "hotspot" in step          # the installer records it


def test_the_fields_a_boot_choice_relies_on_are_kept_in_one_file():
    """BOOT_MODE lives in /etc/ark/ark.env, which ark-mode already sources."""
    root = Path(__file__).resolve().parents[1] / "pi"
    mode = (root / "bin" / "ark-mode").read_text()
    assert 'ENV_FILE=/etc/ark/ark.env' in mode
    assert '[[ -r "$ENV_FILE" ]] && . "$ENV_FILE"' in mode

    service = (SYSTEMD / "ark-net-watch.service").read_text(encoding="utf-8")
    timer = (SYSTEMD / "ark-net-watch.timer").read_text(encoding="utf-8")
    assert "ExecStart=/usr/local/bin/ark-net-watch" in service
    assert "Type=oneshot" in service
    assert "User=" not in service          # it needs NetworkManager, so root
    assert "OnBootSec=3min" in timer
    assert "OnUnitActiveSec=60s" in timer
    assert "Unit=ark-net-watch.service" in timer
    assert "WantedBy=timers.target" in timer


def test_the_installer_enables_it_and_the_verifier_checks_it():
    network = (REPO / "pi" / "steps" / "70-network.sh").read_text(encoding="utf-8")
    verify = (REPO / "pi" / "steps" / "90-verify.sh").read_text(encoding="utf-8")
    assert "ark-boot-guard ark-net-watch; do" in network
    assert "ark_install_unit ark-net-watch.timer" in network
    assert "enable --now ark-net-watch.timer" in network
    assert "ark-net-watch --dry-run" in network      # proof without touching the radio
    assert "ark-net-watch.timer" in verify


def test_the_watchdog_keeps_its_own_record_of_decisions():
    text = WATCH.read_text(encoding="utf-8")
    assert "net-watch.log" in text
    assert "net-watch.state" in text
    assert "set -Eeuo pipefail" in text
