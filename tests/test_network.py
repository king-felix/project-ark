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

The last section is the other question the same subject asks, and it is the one
that went wrong on 31 Oct 2026: **which Wi-Fi is "home"?**  Phase 7 used to answer
it from whichever wireless link was up while the installer ran, so a box that was
broadcasting recorded its own access point as the network to go home to
(``docs/IMPLEMENTATION-LOG.md`` §36).  ``pi/lib/home-network.sh`` is the one place
that decides now, and it is driven here through a stand-in ``nmcli`` — the same
script the phase sources, no root and no radio.
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
    (("lan", 0, 0, 0, 0, 0, 1, 0, 0), "stay"),         # a network the owner asked for gets a blink too
    (("lan", 0, 0, 0, 0, 0, 2, 0, 0), "to-hotspot"),   # ...and then the hotspot, because dark is worse
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


def _verifier_watchdog_block() -> str:
    """The verifier's watchdog paragraph, lifted out of the shipped script.

    Taken from the source rather than retyped, so the test runs the code that will
    run on the box.  ``set -Eeuo pipefail`` is how that script starts.  The block
    is one outer ``if`` wrapping an inner one, so it is cut by counting ``if`` and
    ``fi`` rather than by searching for the first ``fi``.
    """
    text = (REPO / "pi" / "steps" / "90-verify.sh").read_text(encoding="utf-8")
    start = text.index('if [[ -s "$ARK_ROOT/logs/net-watch.log" ]]')
    depth = 0
    lines = text[start:].splitlines(keepends=True)
    for i, line in enumerate(lines):
        word = line.strip().split(" ", 1)[0]
        if word == "if":
            depth += 1
        elif word == "fi":
            depth -= 1
            if depth == 0:
                return "".join(lines[:i + 1])
    raise AssertionError("the watchdog paragraph is not closed")


def test_the_watchdog_line_cannot_kill_the_gate_on_a_box_that_has_not_checked_yet(tmp_path):
    """A missing counter file is the *normal* state for the first three minutes.

    The timer waits ``OnBootSec`` before its first check, so a box that has just
    booted has no ``net-watch.state``.  An unguarded ``$(sed … | head -1)`` under
    ``set -Eeuo pipefail`` exits 1 there, and because this block sits above the
    receipt the whole gate would stop before printing anything at all — the one
    moment the owner most wants it to speak.  Reproduced here by running the
    shipped paragraph with no state file, with no log file, and with both.
    """
    block = _verifier_watchdog_block()
    (tmp_path / "logs").mkdir()

    def run() -> subprocess.CompletedProcess:
        script = (
            "set -Eeuo pipefail\n"
            'say_soft() { printf " %s | %s\\n" "$1" "$2"; }\n'
            f'ARK_ROOT="{tmp_path}"\n'
            f"{block}\n"
            'echo "REACHED"\n')
        return subprocess.run(["bash", "-c", script], capture_output=True,
                              text=True, timeout=60)

    bare = run()
    assert bare.returncode == 0, bare.stderr
    assert "REACHED" in bare.stdout, "a box with no counters must not stop the gate"
    assert "no counters yet" in bare.stdout

    (tmp_path / "logs" / "net-watch.state").write_text(
        "lost_streak=0\nhome_streak=0\nupdated=2026-11-12T16:25:27+00:00\n", encoding="utf-8")
    counted = run()
    assert counted.returncode == 0, counted.stderr
    assert "watching since 2026-11-12T16:25:27+00:00" in counted.stdout

    (tmp_path / "logs" / "net-watch.log").write_text(
        "2026-11-12T16:26:00+00:00  stayed on the home network\n", encoding="utf-8")
    decided = run()
    assert decided.returncode == 0, decided.stderr
    assert "last: stayed on the home network" in decided.stdout


def test_the_watchdog_keeps_its_own_record_of_decisions():
    text = WATCH.read_text(encoding="utf-8")
    assert "net-watch.log" in text
    assert "net-watch.state" in text
    assert "set -Eeuo pipefail" in text


def test_the_watchdog_writes_its_counters_in_one_move():
    """A cut mid-write used to leave half a counter file, and ``ark-self-heal``
    then had to set it aside — losing counters the watchdog had earned.  Written to
    a dot-temp file and renamed, the file is either the old one or the new one."""
    text = WATCH.read_text(encoding="utf-8")
    start = text.index("write_state()")
    body = text[start:text.index("\n}", start) + 2]

    assert 'tmp="$dir/.net-watch.state.tmp"' in body, "a dot-temp name, never the real one"
    assert '>"$tmp"' in body, "the bytes go to the temp file"
    assert 'mv -f "$tmp" "$STATE"' in body, "and are published by one rename"
    assert '>"$STATE"' not in body, "nothing writes the real file in place"


# ---------------------------------------------------------------------------
#  ...and the other half of the same subject: which Wi-Fi is "home"?
#
#  "Home" is a network the Ark JOINS, so the box's own access point can never be
#  it — however the radio happens to be configured while an installer runs.  Phase
#  7 used to guess from the active link, and on 31 Oct 2026 it recorded
#  "ark-hotspot" as the network to go home to, after which `ark-mode home`
#  "joined" the home network by bringing the access point back up
#  (docs/IMPLEMENTATION-LOG.md §36).
#
#  The decision lives in pi/lib/home-network.sh, which the phase sources; these
#  tests source the same file and drive it with a stand-in nmcli.
# ---------------------------------------------------------------------------
HOME_LIB = REPO / "pi" / "lib" / "home-network.sh"
NETWORK_STEP = REPO / "pi" / "steps" / "70-network.sh"

# A stand-in for NetworkManager that answers the two questions the decision asks
# and nothing else: what a saved profile is *for*, and what is joined right now.
# Entries are separated by ";" — never by a space — because a Wi-Fi name may hold
# spaces ("Home Network-5G-1"), and a fake that split on whitespace would make
# a real network name untestable.
#   FAKE_PROFILES = "name:mode;name:mode"   "ap" is this box broadcasting
#   FAKE_ACTIVE   = "name:type;name:type"   what `connection show --active` lists
FAKE_NMCLI_HOME = """#!/bin/sh
if [ "${1:-}" = "-g" ]; then
  IFS=';'
  for pair in ${FAKE_PROFILES:-}; do
    if [ "${pair%%:*}" = "${5:-}" ]; then
      printf '%s\\n' "${pair#*:}"
      exit 0
    fi
  done
  printf "Error: unknown connection '%s'.\\n" "${5:-}" >&2
  exit 10
fi
if [ "${1:-}" = "-t" ]; then
  IFS=';'
  for pair in ${FAKE_ACTIVE:-}; do
    printf '%s\\n' "$pair"
  done
  exit 0
fi
exit 0
"""

# ark.env as the box really holds it, and the shape phase 7 repairs.
ARK_ENV = '''# Project ARK — settings the phases and services read.
HOTSPOT_SSID="The-Ark-Offline"
HOTSPOT_CONNECTION="ark-hotspot"
HOME_WIFI_CONNECTION="{home}"
BOOT_MODE="hotspot"
'''

JOINABLE = '''if ark_home_joinable "$1" "$2"; then echo joinable; else echo no; fi
'''

RECORD = '''rc=0
ark_home_record "$1" "$2" "$3" || rc=$?
printf 'result=%s\\nname=%s\\nrc=%s\\n' "$ARK_HOME_RESULT" "$ARK_HOME_NAME" "$rc"
'''


@pytest.fixture()
def home_radio(tmp_path):
    """A stand-in nmcli on disk: the decision is asked for real, by name."""
    folder = tmp_path / "homebin"
    folder.mkdir()
    nmcli = folder / "nmcli"
    nmcli.write_text(FAKE_NMCLI_HOME, encoding="utf-8")
    nmcli.chmod(0o755)
    return folder


def run_home(script, radio, *, profiles="", active="", args=()):
    """Run a snippet with the library sourced, the way phase 7 runs it."""
    env = {**os.environ,
           "PATH": f"{radio}:{os.environ.get('PATH', '')}",
           "FAKE_PROFILES": profiles,
           "FAKE_ACTIVE": active}
    for name in ("HOME_WIFI_CONNECTION", "HOTSPOT_CONNECTION", "ARK_HOME_PROFILE",
                 "ARK_HOTSPOT_AP"):
        env.pop(name, None)          # the shell this runs in is not this box
    body = 'set -Eeuo pipefail\n. "%s"\n%s' % (HOME_LIB, script)
    return subprocess.run(["bash", "-c", body, "home", *[str(a) for a in args]],
                          capture_output=True, text=True, env=env, timeout=60)


def ark_env(tmp_path, *, home="ark-hotspot"):
    """A throwaway ark.env whose home network is wrong, as the box's was."""
    path = tmp_path / "ark.env"
    path.write_text(ARK_ENV.format(home=home), encoding="utf-8")
    return path


def record(env_file, configured, radio, *, profiles="", active="", hotspot="ark-hotspot"):
    """One `ark_home_record`, and the two facts it leaves for its caller."""
    done = run_home(RECORD, radio, profiles=profiles, active=active,
                    args=(env_file, configured, hotspot))
    assert done.returncode == 0, f"{done.stdout}\n{done.stderr}"
    return dict(line.split("=", 1) for line in done.stdout.strip().splitlines())


@pytest.mark.parametrize("profile, mode, expected", [
    ("home-wifi", "infrastructure", True),      # the build-time network
    ("ark-hotspot", "ap", False),               # this box's own broadcast
    ("ark-hotspot", "infrastructure", False),   # the NAME refuses it even so
    ("", "infrastructure", False),              # nothing is not a network
    ("never-saved", "", False),                 # no such profile on the machine
])
def test_a_network_the_box_could_not_join_is_never_called_home(home_radio, profile, mode,
                                                               expected):
    """One rule, shared by the phase and `ark-mode home`: a profile this box really
    has, that is a station, and that is not the Ark's own access point."""
    profiles = f"{profile}:{mode}" if profile else ""
    done = run_home(JOINABLE, home_radio, profiles=profiles, args=(profile, "ark-hotspot"))
    assert done.stdout.strip() == ("joinable" if expected else "no"), done.stderr


def test_the_installer_repairs_a_home_network_that_is_the_boxes_own_hotspot(tmp_path,
                                                                          home_radio):
    """The defect, end to end: the box is broadcasting, ark.env says "ark-hotspot",
    and the phase records the network this box can really join instead."""
    env_file = ark_env(tmp_path, home="ark-hotspot")
    env_file.chmod(0o644)

    said = record(env_file, "ark-hotspot", home_radio,
                  profiles="ark-hotspot:ap;home-wifi:infrastructure",
                  active="ark-hotspot:802-11-wireless")

    assert said["result"] == "recorded", said
    assert said["name"] == "home-wifi", said
    text = env_file.read_text(encoding="utf-8")
    assert 'HOME_WIFI_CONNECTION="home-wifi"' in text
    assert 'HOME_WIFI_CONNECTION="ark-hotspot"' not in text
    # ...and nothing else in the file was rewritten on the way past.
    assert 'BOOT_MODE="hotspot"' in text
    assert 'HOTSPOT_CONNECTION="ark-hotspot"' in text
    assert env_file.stat().st_mode & 0o777 == 0o644, "the mode the box reads it with"
    assert not list(tmp_path.glob("ark.env.ark-home.*")), "no half-written copy left behind"


def test_a_home_network_that_is_already_right_is_left_exactly_as_it_was(tmp_path,
                                                                      home_radio):
    """A phase that is re-run must not rewrite a correct answer — that is how an
    installer turns a setting into a habit."""
    env_file = ark_env(tmp_path, home="home-wifi")
    before = env_file.read_text(encoding="utf-8")

    said = record(env_file, "home-wifi", home_radio,
                  profiles="ark-hotspot:ap;home-wifi:infrastructure",
                  active="home-wifi:802-11-wireless")

    assert said["result"] == "kept", said
    assert said["name"] == "home-wifi"
    assert env_file.read_text(encoding="utf-8") == before


def test_a_box_with_nothing_it_could_join_records_nothing_and_invents_no_name(tmp_path,
                                                                           home_radio):
    """A wrong name is a fact to report, never a hole to fill with a guess: with only
    the access point saved, the file keeps saying so and the phase says it out loud."""
    env_file = ark_env(tmp_path, home="ark-hotspot")
    before = env_file.read_text(encoding="utf-8")

    said = record(env_file, "ark-hotspot", home_radio,
                  profiles="ark-hotspot:ap",
                  active="ark-hotspot:802-11-wireless")

    assert said["result"] == "none", said
    assert said["name"] == "", said
    assert env_file.read_text(encoding="utf-8") == before


def test_a_renamed_profile_that_is_up_now_is_still_a_home_network(tmp_path, home_radio):
    """The build-time profile may be gone and the house network saved under another
    name — a station that is up is somewhere this box can join, whatever it is
    called, so a box set up by hand is not left broadcasting for ever."""
    env_file = ark_env(tmp_path, home="ark-hotspot")

    said = record(env_file, "ark-hotspot", home_radio,
                  profiles="ark-hotspot:ap;Home Network-5G-1:infrastructure",
                  active="Home Network-5G-1:802-11-wireless")

    assert said["result"] == "recorded", said
    assert said["name"] == "Home Network-5G-1", said
    assert ('HOME_WIFI_CONNECTION="Home Network-5G-1"'
            in env_file.read_text(encoding="utf-8"))


def test_a_file_that_never_held_a_home_network_gets_the_line_kept_beside_the_rest(tmp_path,
                                                                                home_radio):
    """An older ark.env has no such line; the repair adds one and keeps every other."""
    env_file = tmp_path / "ark.env"
    env_file.write_text('BOOT_MODE="hotspot"\nHOTSPOT_CONNECTION="ark-hotspot"\n',
                        encoding="utf-8")

    said = record(env_file, "", home_radio, profiles="home-wifi:infrastructure")

    assert said["result"] == "recorded", said
    assert said["name"] == "home-wifi"
    text = env_file.read_text(encoding="utf-8")
    assert 'HOME_WIFI_CONNECTION="home-wifi"' in text
    assert 'BOOT_MODE="hotspot"' in text and 'HOTSPOT_CONNECTION="ark-hotspot"' in text


def test_the_installer_asks_the_library_and_never_guesses_from_the_radio():
    """The regression itself: the phase must not go back to reading the active link.

    `connection show --active` is the observation that recorded "ark-hotspot" as the
    home network.  It belongs in the library's fallback for a *renamed* profile, and
    nowhere in the phase."""
    step = NETWORK_STEP.read_text(encoding="utf-8")
    assert '. "$ARK_SRC/lib/home-network.sh"' in step
    assert 'ark_home_record "$ENV_FILE"' in step
    assert "connection show --active" not in step
    assert "sed -i" not in step           # ark.env is repaired by the library, in one move
    assert HOME_LIB.is_file()
    lib = HOME_LIB.read_text(encoding="utf-8")
    for name in ("ark_home_mode", "ark_home_joinable", "ark_home_choose", "ark_home_record"):
        assert name in lib, name


# ---------------------------------------------------------------------------
#  ...and the console side of the same fault.
#
#  Recording the wrong name was half of it; the other half was what the script did
#  with it.  `ark-mode home` took the access point down first and then "joined the
#  home network" by bringing the access point back up, and `hotspot_up()` (the
#  watchdog's own rescue) deactivated "$HOME_WIFI_CONNECTION" before activating the
#  AP — which, with home (wrongly) the AP's own name, bounced the access point on
#  every rescue and dropped whoever was reading it (§36).
#
#  These drive the real pi/bin/ark-mode: ARK_MODE_REHEARSAL=1 is the script's own
#  door (it skips only the root check), the state files are throwaway, and the
#  nmcli first on PATH is a stand-in that records every word it is given.
# ---------------------------------------------------------------------------
FAKE_NMCLI_MODE = """#!/bin/sh
printf '%s\\n' "$*" >> "$FAKE_LOG"
case "${1:-}" in
  -t) cat "$FAKE_ACTIVE_STATE" 2>/dev/null; exit 0 ;;
  --wait)
     shift 2
     if [ "${1:-}" = "connection" ] && [ "${2:-}" = "up" ] && [ -n "${3:-}" ]; then
       if [ "${FAKE_FAIL_UP:-0}" = "1" ] && [ "${3:-}" != "$FAKE_AP" ]; then exit 10; fi
       printf '%s\\n' "$3" >> "$FAKE_ACTIVE_STATE"
     fi
     exit 0 ;;
  connection)
     if [ "${2:-}" = "down" ] && [ -n "${3:-}" ]; then
       grep -Fxv "$3" "$FAKE_ACTIVE_STATE" > "$FAKE_ACTIVE_STATE.tmp" 2>/dev/null || true
       [ -e "$FAKE_ACTIVE_STATE.tmp" ] && mv -f "$FAKE_ACTIVE_STATE.tmp" "$FAKE_ACTIVE_STATE"
     fi
     exit 0 ;;
esac
exit 0
"""


def run_mode(tmp_path, *args, home="ark-hotspot", active="", fail_up=False):
    """Run the real `ark-mode` against a stand-in radio and throwaway state files."""
    radio = tmp_path / "modebin"
    radio.mkdir(parents=True, exist_ok=True)
    nmcli = radio / "nmcli"
    nmcli.write_text(FAKE_NMCLI_MODE, encoding="utf-8")
    nmcli.chmod(0o755)
    log = tmp_path / "nmcli.log"
    log.write_text("", encoding="utf-8")
    state = tmp_path / "active.txt"
    state.write_text(active, encoding="utf-8")
    mode_file = tmp_path / "mode"
    mode_file.write_text("hotspot\n", encoding="utf-8")
    pin_file = tmp_path / "mode.pinned"
    env = {**os.environ,
           "PATH": f"{radio}:{os.environ.get('PATH', '')}",
           "ARK_MODE_REHEARSAL": "1",
           "FAKE_LOG": str(log),
           "FAKE_ACTIVE_STATE": str(state),
           "FAKE_AP": "ark-hotspot",
           "FAKE_FAIL_UP": "1" if fail_up else "0",
           "HOME_WIFI_CONNECTION": home,
           "HOTSPOT_CONNECTION": "ark-hotspot",
           "HOTSPOT_SSID": "The-Ark-Offline",
           "HOTSPOT_ADDRESS": "10.42.0.1",
           "HOME_WIFI_TIMEOUT": "1",
           "MODE_FILE": str(mode_file),
           "PIN_FILE": str(pin_file),
           "LAN_REQUEST_FILE": str(tmp_path / "lan-request"),
           "WIFI_DEV": "wlan0"}
    done = subprocess.run(["bash", str(MODE), *[str(a) for a in args]],
                          capture_output=True, text=True, env=env, timeout=60)
    said = log.read_text(encoding="utf-8").splitlines()
    return SimpleNamespace(done=done, calls=[line for line in said if line],
                           mode=mode_file, pin=pin_file, state=state)


def test_going_home_where_home_is_the_boxes_own_hotspot_is_refused_before_the_radio(tmp_path):
    """The defect in one command: with "ark-hotspot" stored as home, `ark-mode home`
    took the access point down, brought it back up, and then reported "back on
    »home«" about a network the box had never joined.  It answers with a sentence
    now — and touches nothing on its way to saying no."""
    said = run_mode(tmp_path, "home", home="ark-hotspot", active="ark-hotspot\n")

    assert said.done.returncode == 1, f"{said.done.stdout}\n{said.done.stderr}"
    assert "own access point" in said.done.stderr, said.done.stderr
    assert said.calls == [], f"nothing was asked of the radio: {said.calls}"
    assert not said.pin.exists(), "a refusal must not pin the box anywhere"
    assert said.mode.read_text(encoding="utf-8") == "hotspot\n", "nor claim a mode it is not in"
    assert said.state.read_text(encoding="utf-8") == "ark-hotspot\n", "the AP was left alone"


def test_the_rescue_never_takes_down_the_access_point_it_is_bringing_up(tmp_path):
    """`hotspot_up()` deactivated "$HOME_WIFI_CONNECTION" first.  With home (wrongly)
    the AP's own name that was a bounce of the access point on every rescue — which
    drops a reader mid-page — so the line is skipped when the two are one name."""
    said = run_mode(tmp_path, "hotspot", "--auto", home="ark-hotspot",
                    active="ark-hotspot\n")

    assert said.done.returncode == 0, said.done.stderr
    assert not [c for c in said.calls if c.startswith("connection down")], said.calls
    assert said.mode.read_text(encoding="utf-8") == "hotspot\n"
    assert not said.pin.exists(), "--auto is the Ark doing it to itself, so it leaves no pin"


def test_a_home_network_that_does_not_answer_puts_the_access_point_back(tmp_path):
    """Nothing answered the join, so the box must not be left dark: the hotspot comes
    back up and the console says what failed and what happened instead (§8.8 rule 10,
    which applies to a console command exactly as it does to the watchdog)."""
    said = run_mode(tmp_path, "home", home="home-wifi", active="ark-hotspot\n", fail_up=True)

    assert said.done.returncode == 1, f"{said.done.stdout}\n{said.done.stderr}"
    assert '"home-wifi"' in said.done.stderr, said.done.stderr
    assert "Broadcasting again instead" in said.done.stderr, said.done.stderr
    up = said.state.read_text(encoding="utf-8").strip()
    assert up == "ark-hotspot", f"the access point is up again: {up!r}"
    assert said.mode.read_text(encoding="utf-8") == "hotspot\n"
    assert not said.pin.exists(), "a failed join must not pin the box to home"


def test_a_status_line_names_the_home_network_and_says_when_it_cannot_join_it(tmp_path):
    """The console and the owner's page must not disagree about an unusable answer:
    the status says which name is stored and, in one plain sentence, why it is no
    use — and stays silent about the second line when the answer is fine.  `status`
    needs no root (§33.4)."""
    broken = run_mode(tmp_path / "broken", "status", home="ark-hotspot",
                      active="ark-hotspot\n")
    assert broken.done.returncode == 0, broken.done.stderr
    out = broken.done.stdout
    assert "home network  : ark-hotspot" in out, out
    assert "cannot join   : " in out, out
    assert "own access point" in out, out

    fine = run_mode(tmp_path / "fine", "status", home="home-wifi", active="ark-hotspot\n")
    assert fine.done.returncode == 0, fine.done.stderr
    assert "home network  : home-wifi" in fine.done.stdout, fine.done.stdout
    assert "cannot join" not in fine.done.stdout, fine.done.stdout


