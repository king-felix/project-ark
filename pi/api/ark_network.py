"""Project ARK — the owner's own network, in the owner's own words.

Two small things live here, and both exist for the same reason: **the box must
never be somewhere nobody is standing next to it.**

* **adding a saved Wi-Fi network.**  The Ark may be carried to another house, and
  the network it will one day join has to be writable down without an SSH
  session and without a stack of NetworkManager commands.  The passphrase goes to
  a root helper on **stdin** — never on a command line, which every process on
  the box can read and which lands in shell history — and it never enters the
  database, a log or this module's memory beyond the one call.
  Adding a network does **not** make the box join it: the profile is written with
  ``autoconnect=false``, so nothing happens until a person asks for it.
* **the one-time administrative (LAN) request.**  "Restart on my own network,
  for this boot."  A boot that is asked this way joins the named network once; if
  it is not there, the box broadcasts as usual.  That is deliberately a request
  that **expires** and is **used up**, not a mode: a box in a cupboard must never
  come back reachable only from a router that is switched off.

Neither function writes anything itself when a root helper can:

* ``ark-mode request-lan`` is still the only writer of ``/etc/ark/lan-request``
  (and the only consumer — ``cmd_boot``), and ``ark-wifi-add`` is the only writer
  of a NetworkManager profile.  One author per file, the §6 rule one folder over.
  This module **reads** both and shells out for everything else.
* A refusal is a ``NetworkRefused`` carrying a sentence a person can act on —
  never a traceback and never a silent no-op.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

# A household password and a network name, in the sizes Wi-Fi really has.
SSID_MIN, SSID_MAX = 1, 32
PSK_MIN, PSK_MAX = 8, 63

# How long a request waits for a boot, in minutes, and the widest it may be set.
LAN_MINUTES_DEFAULT = 30
LAN_MINUTES_MAX = 24 * 60

# How long a helper may take before the page stops waiting for it.  Writing a
# profile and asking for a restart are quick; joining a network is not done here.
RUN_SECONDS = 30


class NetworkRefused(Exception):
    """A refusal in words a person can act on.  Never a traceback.

    ``ran`` says whether the helper on the box was actually reached: a refusal the
    *page* made (a name with a space in it, minutes of ``0``) never ran anything, so
    it cannot have written a request.  The restart path cares — see
    ``ark_admin.network_lan``, where a helper that failed to schedule the reboot has
    already written the request and must not be described as having asked nothing.
    """

    def __init__(self, words, *, ran: bool = False):
        super().__init__(words)
        self.ran = ran


# ---------------------------------------------------------------------------
#  where things are — read at call time, so a test can point them somewhere else
# ---------------------------------------------------------------------------
def network_dir() -> Path:
    """The folder NetworkManager keeps its saved networks in."""
    return Path(os.environ.get("ARK_NM_DIR", "/etc/NetworkManager/system-connections"))


def request_file() -> Path:
    """The one-time request.  Same default and same seam as ark-mode."""
    return Path(os.environ.get("LAN_REQUEST_FILE", "/etc/ark/lan-request"))


def env_file() -> Path:
    """Where BOOT_MODE and the connection names live."""
    return Path(os.environ.get("ARK_ENV_FILE", "/etc/ark/ark.env"))


def mode_file() -> Path:
    return Path(os.environ.get("MODE_FILE", "/etc/ark/mode"))


def pin_file() -> Path:
    return Path(os.environ.get("PIN_FILE", "/etc/ark/mode.pinned"))


def ark_mode_bin() -> str:
    return os.environ.get("ARK_MODE_BIN", "/usr/local/bin/ark-mode")


def wifi_add_bin() -> str:
    return os.environ.get("ARK_WIFI_ADD", "/usr/local/sbin/ark-wifi-add")


def sudo_prefix() -> list:
    """``['/usr/bin/sudo', '-n']`` on the box; nothing at all in a test."""
    value = os.environ.get("ARK_SUDO", "/usr/bin/sudo")
    return [value, "-n"] if value else []


# ---------------------------------------------------------------------------
#  reading — never raises, never guesses
# ---------------------------------------------------------------------------
def _first_line(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()[0].strip()
    except (OSError, IndexError):
        return ""


def _env_values(path=None) -> dict:
    """``KEY="value"`` lines out of ark.env, quotes and comments out of the way."""
    values = {}
    try:
        text = (Path(path) if path else env_file()).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return values
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def default_network() -> str:
    """Which saved network a request means when it names none.

    Mirrors ``ark-mode``: ``LAN_WIFI_CONNECTION``, falling back to the home one,
    so a request written before this page existed still behaves.
    """
    values = _env_values()
    return (values.get("LAN_WIFI_CONNECTION")
            or values.get("HOME_WIFI_CONNECTION")
            or os.environ.get("LAN_WIFI_CONNECTION")
            or os.environ.get("HOME_WIFI_CONNECTION")
            or "home-wifi")


def boot_mode() -> str:
    """The box's standing answer for a cold boot.  ``hotspot`` is the safe default."""
    return (_env_values().get("BOOT_MODE") or "hotspot").strip() or "hotspot"


def mode_now() -> str:
    """How the box is reachable right now, as ark-mode last recorded it."""
    value = _first_line(mode_file()).strip().lower()
    return value if value in ("hotspot", "home", "lan", "auto") else "unknown"


def pinned_now() -> str:
    """The owner's own answer, if one is pinned (`ark-mode auto` clears it)."""
    return _first_line(pin_file()).strip().lower()


def mode_words() -> str:
    """One sentence a person can read out loud, about where the box is now."""
    mode, pinned = mode_now(), pinned_now()
    if mode == "hotspot":
        words = ("The box is broadcasting its own Wi-Fi. Anyone nearby can open "
                 "http://ark.lan — and that works with the internet unplugged.")
    elif mode in ("lan", "home"):
        words = ("The box is on a saved network of yours, so it answers at "
                 "https://ark.local:8443 from that network. Its own Wi-Fi still "
                 "serves the portal if it is up.")
    elif mode == "auto":
        words = ("The box is looking for a saved network, and falls back to "
                 "broadcasting its own if it cannot find one.")
    else:
        words = ("The box has not recorded how it is reachable. That is what a box "
                 "looks like before its first boot has finished.")
    if pinned:
        words += f" You chose this ({pinned}), so a restart keeps it."
    else:
        words += " A restart goes back to the standing choice."
    return words


def _int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _request_values(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    values = {}
    for line in text.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            values.setdefault(key.strip(), value.strip())   # a value may hold '='
    return values


def request_state(path=None) -> dict:
    """What a boot would find waiting: ``waiting``, ``expired`` or ``absent``.

    The clock decides, exactly as ``ark-mode``'s own ``lan_request_state()`` does
    (``now > expires`` is expired; equal is still waiting), so the page and the
    boot can never disagree about whether a request is live.
    """
    path = Path(path) if path else request_file()
    values = _request_values(path)
    if not values:
        return {"state": "absent", "network": "", "ssid": "", "by": "",
                "minutes_left": None, "requested": "", "expires": None}
    expires = _int(values.get("expires"))
    now = int(time.time())
    minutes_left = None
    if expires is None:
        state = "waiting"          # no expiry recorded = somebody wrote it by hand
    elif now > expires:
        state = "expired"
    else:
        state = "waiting"
        minutes_left = -(-(expires - now) // 60)     # whole minutes, rounded up
    return {
        "state": state,
        "network": values.get("network", "") or default_network(),
        "ssid": values.get("ssid", ""),
        "by": values.get("by", ""),
        "minutes_left": minutes_left,
        "requested": values.get("requested", ""),
        "expires": expires,
    }


def request_words(state: dict) -> str:
    """The sentence the owner's page shows about the waiting request.

    Written for a person; ``ark-mode status`` has its own words for a console and
    both say the same thing.
    """
    if state.get("state") == "waiting":
        where = f'"{state["ssid"]}"' if state.get("ssid") else f'"{state["network"]}"'
        left = state.get("minutes_left")
        if left:
            when = (f"a restart in the next {left} minute{'' if left == 1 else 's'} "
                    f"will try {where}")
        else:
            when = f"the next restart will try {where}"
        return (f"Waiting: {when}. If that network is not there, the box broadcasts "
                f"as usual.")
    if state.get("state") == "expired":
        return ("The last request has lapsed, so a restart ignores it and broadcasts "
                "as usual. Ask again if you still need it.")
    return "Nothing is waiting. A restart does what the standing choice says."


# ---------------------------------------------------------------------------
#  what is already saved — the list the owner picks from
# ---------------------------------------------------------------------------
def _keyfile(path: Path) -> dict:
    """The handful of fields a saved Wi-Fi profile needs, section-qualified."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    wanted = {"connection": ("id", "autoconnect", "type"),
              "wifi": ("ssid", "mode")}
    fields, section = {}, ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
            continue
        key, sep, value = line.partition("=")
        if not sep or section not in wanted:
            continue
        key = key.strip()
        if key in wanted[section]:
            fields[f"{section}.{key}"] = value.strip()
    return fields


def saved_networks(folder=None):
    """Every network someone could ask the box to join, or ``None`` if unreadable.

    ``None`` and ``[]`` are different answers on purpose (§8.7): a folder that
    cannot be listed is not a box with no networks saved, and saying "none" there
    would be a claim the box has not earned.
    """
    folder = Path(folder) if folder else network_dir()
    try:
        paths = sorted(folder.iterdir(), key=lambda p: p.name)
    except FileNotFoundError:
        return []
    except OSError:
        return None
    found = []
    for path in paths:
        if not path.name.endswith(".nmconnection"):
            continue
        fields = _keyfile(path)
        kind = fields.get("connection.type", "wifi")
        if kind not in ("wifi", "802-11-wireless"):
            continue
        if fields.get("wifi.mode", "infrastructure") != "infrastructure":
            continue        # the box's own access point is not somewhere to join
        found.append({
            "profile": fields.get("connection.id", path.stem),
            "ssid": fields.get("wifi.ssid", ""),
            "autoconnect": fields.get("connection.autoconnect", "").lower() == "true",
        })
    found.sort(key=lambda row: (row["ssid"].lower(), row["profile"]))
    return found


# ---------------------------------------------------------------------------
#  checking what a person typed — refuse with the limit named, never trim
# ---------------------------------------------------------------------------
def clean_ssid(value) -> str:
    """A Wi-Fi network name: 1…32 characters, no control characters."""
    text = str(value or "")
    if text != text.strip():
        raise NetworkRefused("A network name cannot begin or end with a space. "
                             "Check for a stray space and try again.")
    if not text:
        raise NetworkRefused("A network name is needed — it is the name the phone "
                             "shows when it lists the Wi-Fi nearby.")
    if len(text) > SSID_MAX:
        raise NetworkRefused(f"A network name is at most {SSID_MAX} characters; "
                             f"that one is {len(text)}.")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in text):
        raise NetworkRefused("A network name cannot hold a control character "
                             "(a line break, for instance).")
    return text


def clean_psk(value) -> str:
    """A Wi-Fi password, exactly as typed: 8…63 characters, nothing stripped."""
    text = str(value or "")
    if text != text.strip():
        raise NetworkRefused("A Wi-Fi password cannot begin or end with a space. "
                             "Nothing was saved — check for a stray space.")
    if len(text) < PSK_MIN:
        raise NetworkRefused(f"A Wi-Fi password is at least {PSK_MIN} characters; "
                             f"that one is {len(text)}.")
    if len(text) > PSK_MAX:
        raise NetworkRefused(f"A Wi-Fi password is at most {PSK_MAX} characters; "
                             f"that one is {len(text)}.")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in text):
        raise NetworkRefused("A Wi-Fi password cannot hold a control character "
                             "(a line break, for instance).")
    return text


def clean_profile(value) -> str:
    """The name of a saved network as the box knows it — never a path, never a flag."""
    text = str(value or "").strip()
    if (not text or len(text) > 64 or text.startswith("-")
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in text)):
        raise NetworkRefused("That is not the name of a saved network. Choose one "
                             "from the list on this page.")
    return text


def clean_actor(value) -> str:
    """Who asked.  A name for the history page, never a place for a sentence."""
    text = "".join(ch for ch in str(value or "") if ch.isalnum() or ch in "._-@ ").strip()
    return text[:32] or "an administrator"


# ---------------------------------------------------------------------------
#  doing it — every privileged step goes through one helper, with the seam a
#  test needs, and no password on any command line
# ---------------------------------------------------------------------------
def _last_sentence(text: str) -> str:
    """The last thing a helper said, with its own prefix taken off."""
    for line in reversed((text or "").splitlines()):
        line = line.strip()
        if not line:
            continue
        for prefix in ("Not done:", "ark-wifi-add:", "ark-mode:"):
            if line.startswith(prefix):
                line = line[len(prefix):].strip()
        return line
    return ""


def last_words(text: str) -> str:
    """What a helper said last, for a page.  The same reader `_run` uses."""
    return _last_sentence(text)


def _run(argv, *, password=None, timeout: int = RUN_SECONDS) -> str:
    """Run one helper as root; answer with what it said, or refuse in its words.

    The passphrase, when there is one, arrives on **stdin**: a command line is
    readable by every process on the box and is remembered by shell history.
    """
    try:
        done = subprocess.run(
            argv,
            input=(password + "\n") if password is not None else None,
            capture_output=True, text=True, timeout=timeout, check=False,
        )
    except FileNotFoundError:
        raise NetworkRefused(
            "The part of the box that changes the network is not installed, so "
            "nothing was changed.") from None
    except subprocess.TimeoutExpired:
        raise NetworkRefused(
            f"The box was still working after {timeout} seconds, so this page stopped "
            f"waiting. Read the top of the page — it asks the box rather than "
            f"believing this form.", ran=True) from None
    except OSError as exc:
        raise NetworkRefused(f"The box could not run that: {exc}") from None
    out = (done.stdout or "").strip()
    if done.returncode != 0:
        # The helper *ran*, so whatever it refused it may already have written —
        # `request-lan` keeps its request and fails on the reboot (§33.4).
        raise NetworkRefused(_last_sentence(done.stderr) or _last_sentence(out)
                             or "The box refused, and did not say why.", ran=True)
    return out


def add_network(ssid, password) -> dict:
    """Save one network so the box can join it later.  Writes nothing else.

    The helper derives the profile name from the network name, so adding the same
    network again **replaces** it instead of leaving a stale second copy — and
    there is exactly one place that name is derived (§8.7: two derivations of one
    name is how a door and a library come to disagree).
    """
    name = clean_ssid(ssid)
    secret = clean_psk(password)
    said = _run(sudo_prefix() + [wifi_add_bin(), "--ssid", name], password=secret)
    return {"ssid": name, "words": said or f'Saved "{name}".'}


def request_lan(network=None, *, ssid="", by="an administrator",
                minutes: int = LAN_MINUTES_DEFAULT, restart: bool = False) -> str:
    """Ask for ONE boot on a saved network.  Never a standing answer.

    ``restart`` does it now instead of at the next boot, through a transient
    systemd unit (``ark-mode request-lan --restart``), so the restart survives the
    page's own request being answered.
    """
    profile = clean_profile(network) if network else ""
    if isinstance(minutes, bool) or not isinstance(minutes, int):
        raise NetworkRefused("The number of minutes has to be a whole number.")
    if minutes < 1 or minutes > LAN_MINUTES_MAX:
        raise NetworkRefused(f"A request can wait between 1 and {LAN_MINUTES_MAX} "
                             f"minutes for a restart; {minutes} is outside that.")
    argv = sudo_prefix() + [ark_mode_bin(), "request-lan",
                            "--minutes", str(int(minutes)),
                            "--by", clean_actor(by)]
    if profile:
        argv += ["--network", profile]
    if ssid:
        argv += ["--ssid", clean_ssid(ssid)]
    if restart:
        argv.append("--restart")
    return _run(argv)


def cancel_request() -> str:
    """Withdraw a waiting request.  Nothing else about the box changes."""
    return _run(sudo_prefix() + [ark_mode_bin(), "request-lan", "--cancel"])

