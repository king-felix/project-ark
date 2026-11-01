"""The owner's own network — a Wi-Fi saved from a page, and ONE restart on it.

``test_network.py`` is the watchdog: what the box does *after* a boot when a link
disappears.  This file is the other half of the same subject — what a person types
into the owner's **Network** page, what the box does with it, and the one promise
the whole design rests on: **the box never leaves the air by accident**.

Three layers, each checked its own way:

* **the words** — a name or a password that cannot work is refused with the limit
  named, never quietly trimmed.  A value the box invented is worse than a refusal;
* **the flow** — a request written by ``ark_network`` is used up by exactly ONE
  boot, and the boot after it broadcasts again.  Driven through the real
  ``pi/bin/ark-mode`` in its rehearsal seam, against a fake radio, so the script
  that ships is the script under test;
* **the page** — ``/admin/network`` behind the sign-in: both forms, the history
  line each one writes, and a password that is never written back onto the page.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
import urllib.parse
from pathlib import Path
from types import SimpleNamespace

import pytest

from helpers import LAN, claim_box, csrf_of, get, post

REPO = Path(__file__).resolve().parents[1]
ARK_MODE = REPO / "pi" / "bin" / "ark-mode"

# ---------------------------------------------------------------------------
#  a fake radio — the same shape the rehearsal script uses, because `ark-mode`
#  is the thing being driven and it must not be told a story only a mock knows
# ---------------------------------------------------------------------------
FAKE_NMCLI = r"""#!/usr/bin/env bash
STATE="${FAKE_NM_STATE:?}"
mkdir -p "$STATE/active"
fields=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -f|--fields) fields="${2:-}"; shift 2; continue ;;
    --wait) shift 2; continue ;;
    --wait=*|-t|--terse) shift; continue ;;
    *) break ;;
  esac
done
case "$1" in
  connection)
    case "${2:-}" in
      show)
        for f in "$STATE"/active/*; do
          [[ -e "$f" ]] || continue
          name="$(basename "$f")"
          if [[ "$fields" == *TYPE* ]]; then printf '%s:802-11-wireless\n' "$name"
          else printf '%s\n' "$name"; fi
        done
        exit 0 ;;
      up)
        name="${4:-${3:-}}"
        echo "up $name" >> "$STATE/calls"
        [[ -e "$STATE/refuse-$name" ]] && { echo "Error: no network" >&2; exit 4; }
        : > "$STATE/active/$name"
        exit 0 ;;
      down)
        echo "down ${3:-}" >> "$STATE/calls"
        rm -f "$STATE/active/${3:-}"
        exit 0 ;;
    esac ;;
  device) echo rescan >> "$STATE/calls"; exit 0 ;;
esac
exit 0
"""

FAKE_IP = r"""#!/usr/bin/env bash
if [[ "${1:-}" == -4 ]]; then
  printf '2: wlan0    inet 192.168.1.50/24 brd 192.168.1.255 scope global wlan0\n'
  exit 0
fi
if [[ "${1:-}" == route ]]; then
  printf 'default via 192.168.1.1 dev wlan0\n'
  exit 0
fi
exit 0
"""

# The two helpers the page is allowed to reach, and a record of every word they
# were given.  The passphrase arrives on stdin, because a command line is readable
# by every process on the box — that is the point of the test below.
FAKE_WIFI_ADD = r"""#!/usr/bin/env bash
{ printf 'wifi-add'; printf ' %s' "$@"; printf '\n'; } >> "${FAKE_CALLS:?}"
psk=""
IFS= read -r psk || true
printf 'stdin=%s\n' "$psk" >> "$FAKE_CALLS"
if [[ -n "${FAKE_SAY:-}" ]]; then
  if [[ "${FAKE_WHERE:-out}" == err ]]; then printf '%s\n' "$FAKE_SAY" >&2
  else printf '%s\n' "$FAKE_SAY"; fi
  exit "${FAKE_EXIT:-0}"
fi
printf 'Saved "%s".\n' "${2:-}"
"""

FAKE_ARK_MODE = r"""#!/usr/bin/env bash
{ printf 'ark-mode'; printf ' %s' "$@"; printf '\n'; } >> "${FAKE_CALLS:?}"
if [[ -n "${FAKE_SAY:-}" ]]; then
  if [[ "${FAKE_WHERE:-out}" == err ]]; then printf '%s\n' "$FAKE_SAY" >&2
  else printf '%s\n' "$FAKE_SAY"; fi
  exit "${FAKE_EXIT:-0}"
fi
printf 'The request is waiting for the next restart.\n'
"""


def _script(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)
    return path


def _profile(folder: Path, name: str, *, ssid: str, mode: str = "infrastructure",
             kind: str = "wifi", autoconnect: str = "true") -> Path:
    """One saved NetworkManager profile, as the keyfile NetworkManager writes."""
    body = ("[connection]\n"
            f"id={name}\n"
            "uuid=00000000-0000-0000-0000-000000000000\n"
            f"type={kind}\n"
            f"autoconnect={autoconnect}\n"
            "\n[wifi]\n"
            f"ssid={ssid}\n"
            f"mode={mode}\n")
    path = folder / f"{name}.nmconnection"
    path.write_text(body, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
#  fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def radio(tmp_path):
    """A fake radio and a throwaway ``/etc/ark``, wired the way ``ark-mode`` expects."""
    work = tmp_path / "radio"
    (work / "state" / "active").mkdir(parents=True)
    _script(work / "bin/nmcli", FAKE_NMCLI)
    _script(work / "bin/ip", FAKE_IP)
    env = os.environ.copy()
    env.update({
        "PATH": f"{work / 'bin'}:{env.get('PATH', '')}",
        "FAKE_NM_STATE": str(work / "state"),
        "ARK_MODE_REHEARSAL": "1",
        "MODE_FILE": str(work / "mode"),
        "PIN_FILE": str(work / "mode.pinned"),
        "LAN_REQUEST_FILE": str(work / "lan-request"),
        "ARK_ENV_FILE": str(work / "ark.env"),
        "HOME_WIFI_CONNECTION": "home-wifi",
        "HOTSPOT_CONNECTION": "ark-hotspot",
        "LAN_WIFI_CONNECTION": "home-wifi",
        "ARK_HOME_WIFI_TIMEOUT": "1",
        "BOOT_MODE": "hotspot",
    })
    env.pop("SYSTEMD_RUN_COMMAND", None)     # a rehearsal never restarts this machine
    return SimpleNamespace(work=work, env=env, state=work / "state",
                           calls=work / "state" / "calls",
                           request=work / "lan-request", mode=work / "mode",
                           pin=work / "mode.pinned", env_file=work / "ark.env")


def run_mode(box, *args, boot_mode: str = "hotspot"):
    """One console command, or one boot, through the **real** ``ark-mode``."""
    env = dict(box.env)
    env["BOOT_MODE"] = boot_mode
    return subprocess.run(["bash", str(ARK_MODE), *args], env=env,
                          capture_output=True, text=True, timeout=120)


def no_radio(box) -> None:
    """A radio with nothing joined, and a clean sheet of calls."""
    for path in sorted((box.state / "active").glob("*")):
        path.unlink()
    try:
        (box.state / "calls").unlink()
    except FileNotFoundError:
        pass


def said(done) -> str:
    """Everything a helper printed, on either stream — what a person would read."""
    return f"{done.stdout}\n{done.stderr}"


@pytest.fixture()
def helpers(tmp_path, monkeypatch):
    """Stand-in helpers, and every switch ``ark_network`` reads pointed at them."""
    work = tmp_path / "helpers"
    work.mkdir()
    nm = work / "nm"
    nm.mkdir()
    _profile(nm, "home-wifi", ssid="Rosebank Road")
    _profile(nm, "ark-hotspot", ssid="The-Ark-Offline", mode="ap")   # not somewhere to join
    wifi = _script(work / "ark-wifi-add", FAKE_WIFI_ADD)
    mode = _script(work / "ark-mode", FAKE_ARK_MODE)
    monkeypatch.setenv("ARK_SUDO", "")          # the seam: no sudo in a test
    monkeypatch.setenv("ARK_WIFI_ADD", str(wifi))
    monkeypatch.setenv("ARK_MODE_BIN", str(mode))
    monkeypatch.setenv("ARK_NM_DIR", str(nm))
    monkeypatch.setenv("LAN_REQUEST_FILE", str(work / "lan-request"))
    monkeypatch.setenv("ARK_ENV_FILE", str(work / "ark.env"))
    monkeypatch.setenv("MODE_FILE", str(work / "mode"))
    monkeypatch.setenv("PIN_FILE", str(work / "mode.pinned"))
    monkeypatch.setenv("FAKE_CALLS", str(work / "calls"))
    for name in ("FAKE_SAY", "FAKE_WHERE", "FAKE_EXIT"):
        monkeypatch.delenv(name, raising=False)
    return SimpleNamespace(work=work, calls=work / "calls", wifi=wifi, mode=mode, nm=nm,
                           request=work / "lan-request", env_file=work / "ark.env")


@pytest.fixture()
def network(helpers):
    """The module, imported after the throwaway box exists (it reads env at call time)."""
    import ark_network
    return ark_network


@pytest.fixture()
def owner(client):
    """A claimed box and a signed-in owner."""
    return claim_box(client)


def _calls(helpers) -> list:
    """Every word the stand-in helpers were given, one line each."""
    try:
        return helpers.calls.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []


def _audit(ark, action: str):
    """The newest history line for one action, or ``None``."""
    with ark.db.read() as conn:
        return conn.execute(
            "SELECT action, result, detail FROM audit_log WHERE action = ? "
            "ORDER BY id DESC LIMIT 1", (action,)).fetchone()


def _redirected(response) -> str:
    """Where a form sent the browser, with the ``?ok=`` sentence decoded."""
    assert response.status_code == 303, response.text[:600]
    where = response.headers["location"]
    query = urllib.parse.parse_qs(urllib.parse.urlparse(where).query)
    return query.get("ok", [""])[0]


# ---------------------------------------------------------------------------
#  the words — a value the box invented is worse than a refusal
# ---------------------------------------------------------------------------
def test_a_stray_space_in_a_network_name_is_named_not_quietly_removed(network):
    with pytest.raises(network.NetworkRefused) as caught:
        network.clean_ssid(" Rosebank Road")
    assert "space" in str(caught.value)
    assert network.clean_ssid("Rosebank Road") == "Rosebank Road"


def test_a_name_that_is_too_long_says_how_long_it_may_be(network):
    with pytest.raises(network.NetworkRefused) as caught:
        network.clean_ssid("x" * (network.SSID_MAX + 1))
    assert str(network.SSID_MAX) in str(caught.value)
    assert network.clean_ssid("x" * network.SSID_MAX) == "x" * network.SSID_MAX


@pytest.mark.parametrize("bad", ["", "   ", "a\nb", "a\tb"])
def test_a_name_that_cannot_be_one_word_led_line_is_refused(network, bad):
    with pytest.raises(network.NetworkRefused):
        network.clean_ssid(bad)


def test_a_wifi_password_that_is_too_short_says_the_number(network):
    with pytest.raises(network.NetworkRefused) as caught:
        network.clean_psk("short")
    assert str(network.PSK_MIN) in str(caught.value)


def test_a_wifi_password_is_never_trimmed_for_you(network):
    """A password with a space at the end is a *different* password.

    Trimming it, or handing it on silently, would make the box join nothing and
    blame the router.  The spaces are reported, and the exact string is kept.
    """
    for bad in (" Let me in 42", "Let me in 42 ", "Let me in  42 "):
        with pytest.raises(network.NetworkRefused) as caught:
            network.clean_psk(bad)
        assert "space" in str(caught.value)
    assert network.clean_psk("Let me in  42") == "Let me in  42"   # inside, kept as typed
    assert network.clean_psk("Let me in 42") == "Let me in 42"


def test_a_saved_name_that_would_read_as_a_flag_is_refused(network):
    for bad in ("-x", "--all", "", "  ", "a\nb", "x" * 65):
        with pytest.raises(network.NetworkRefused):
            network.clean_profile(bad)
    assert network.clean_profile(" home-wifi ") == "home-wifi"


def test_the_boxs_own_access_point_is_not_offered_as_somewhere_to_join(network):
    """``ark-hotspot`` is this box broadcasting; "joining" it is a small loop."""
    rows = network.saved_networks()
    assert [row["profile"] for row in rows] == ["home-wifi"]
    assert rows[0]["ssid"] == "Rosebank Road"
    assert rows[0]["autoconnect"] is True


def test_a_folder_the_box_cannot_read_is_not_an_empty_folder(network, monkeypatch):
    def refuse(self):
        raise PermissionError("not for you")

    monkeypatch.setattr(Path, "iterdir", refuse)
    assert network.saved_networks() is None


def test_a_request_that_is_waiting_says_where_and_for_how_much_longer(network, helpers):
    helpers.request.write_text(
        "requested=2026-10-31T09:00:00Z\n"
        f"expires={int(time.time()) + 1500}\n"
        "minutes=25\nnetwork=home-wifi\nssid=Rosebank Road\nby=owner\n",
        encoding="utf-8")
    state = network.request_state()
    assert state["state"] == "waiting"
    assert state["minutes_left"] == 25
    assert state["by"] == "owner"
    words = network.request_words(state)
    assert "Rosebank Road" in words and "25 minutes" in words


def test_a_lapsed_request_is_said_out_loud_and_not_promised(network, helpers):
    helpers.request.write_text(
        "requested=2026-10-31T09:00:00Z\n"
        f"expires={int(time.time()) - 120}\n"
        "minutes=30\nnetwork=home-wifi\nssid=Rosebank Road\nby=owner\n",
        encoding="utf-8")
    state = network.request_state()
    assert state["state"] == "expired"
    assert state["minutes_left"] is None
    assert "lapsed" in network.request_words(state)


def test_nothing_waiting_is_said_plainly(network, helpers):
    assert not helpers.request.exists()
    state = network.request_state()
    assert state["state"] == "absent"
    assert "Nothing is waiting" in network.request_words(state)


def test_a_request_with_no_expiry_is_still_one_restart_and_no_promise_of_time(network, helpers):
    helpers.request.write_text("network=home-wifi\nssid=Rosebank Road\n", encoding="utf-8")
    state = network.request_state()
    assert state["state"] == "waiting"
    assert state["minutes_left"] is None
    assert "the next restart will try" in network.request_words(state)


# ---------------------------------------------------------------------------
#  the flow — ONE restart, driven through the real `ark-mode`
# ---------------------------------------------------------------------------
@pytest.fixture()
def wired(radio, monkeypatch):
    """The module and the real ``ark-mode`` sharing one throwaway ``/etc/ark``.

    Nothing here is mocked at the level of the decision: the request file, the
    mode file, the pin and the boot are the ones the shipped script writes.
    """
    monkeypatch.setenv("ARK_SUDO", "")
    monkeypatch.setenv("ARK_MODE_BIN", str(ARK_MODE))
    monkeypatch.setenv("ARK_MODE_REHEARSAL", "1")   # the script's own test door: no root
    monkeypatch.setenv("LAN_REQUEST_FILE", str(radio.request))
    monkeypatch.setenv("ARK_ENV_FILE", str(radio.env_file))
    monkeypatch.setenv("MODE_FILE", str(radio.mode))
    monkeypatch.setenv("PIN_FILE", str(radio.pin))
    for key in ("HOME_WIFI_CONNECTION", "HOTSPOT_CONNECTION", "LAN_WIFI_CONNECTION",
                "ARK_HOME_WIFI_TIMEOUT"):
        monkeypatch.setenv(key, radio.env[key])
    import ark_network
    return ark_network


def test_reading_where_the_box_is_needs_no_root_at_all(radio):
    """`ark-mode status` writes nothing, so it may not demand root.

    The script's own comment said reading was free while the gate in front of it
    exempted only `boot --dry-run`, so a person following `docs/ADMIN.md` was
    answered *"run this as root (sudo ark-mode status)"* for a read — and a read
    that insists on root is a read nobody can check.  This drives the real gate:
    the rehearsal seam is **removed**, so what is tested is what a person meets.
    An argument-less `ark-mode` is the same question and must answer too.
    """
    if os.geteuid() == 0:                       # a root test can never see a refusal
        pytest.skip("run as an ordinary account to see what one is allowed to read")
    env = dict(radio.env)
    env.pop("ARK_MODE_REHEARSAL", None)         # the real door, not the test one
    for args in (["status"], []):
        done = subprocess.run(["bash", str(ARK_MODE), *args], env=env,
                              capture_output=True, text=True, timeout=120)
        assert done.returncode == 0, said(done)
        assert "run this as root" not in said(done)
        assert "at boot" in said(done)          # the answer, not a refusal
    # a read leaves the box exactly as it found it
    assert not radio.mode.exists()
    assert not radio.pin.exists()
    assert not radio.request.exists()


def test_asking_for_one_restart_writes_a_request_the_console_can_read(wired, radio):
    no_radio(radio)
    told = wired.request_lan("home-wifi", minutes=30, by="owner")
    assert "home-wifi" in told and "once" in told
    written = radio.request.read_text(encoding="utf-8")
    assert "network=home-wifi" in written
    assert "by=owner" in written
    assert "expires=" in written
    status = run_mode(radio, "status")
    assert "waiting" in said(status)


def test_one_boot_uses_the_request_and_the_boot_after_that_broadcasts(wired, radio):
    """The whole promise of the feature, in two boots.

    A person asks for the box on their own network once.  The next restart joins
    it; the one after that broadcasts again.  If this test ever fails, the box
    can be left off the air by a button — which is the one thing it may not do.
    """
    no_radio(radio)
    wired.request_lan("home-wifi", minutes=30, by="owner")

    first = run_mode(radio, "boot")
    assert "home-wifi" in said(first) and "joined" in said(first)
    assert radio.mode.read_text(encoding="utf-8").strip() == "lan"
    assert not radio.request.exists()

    before = len(_calls(radio))
    second = run_mode(radio, "boot")
    assert "broadcast" in said(second)
    assert radio.mode.read_text(encoding="utf-8").strip() == "hotspot"
    after = _calls(radio)[before:]
    assert "up home-wifi" not in after          # the second boot joined nothing
    assert "up ark-hotspot" in after


def test_a_lapsed_request_is_thrown_away_and_the_box_broadcasts(wired, radio):
    no_radio(radio)
    radio.request.write_text(
        "requested=2026-01-01T00:00:00Z\n"
        f"expires={int(time.time()) - 120}\n"
        "minutes=30\nnetwork=home-wifi\nssid=Rosebank Road\nby=owner\n",
        encoding="utf-8")
    done = run_mode(radio, "boot")
    assert "expired" in said(done)
    assert not radio.request.exists()
    assert radio.mode.read_text(encoding="utf-8").strip() == "hotspot"
    assert "up home-wifi" not in _calls(radio)  # the lapsed request joined nothing


def test_a_network_that_will_not_join_leaves_the_box_broadcasting(wired, radio):
    """The failure that would strand the box: asked for, gone, and still off the air."""
    no_radio(radio)
    (radio.state / "refuse-home-wifi").write_text("", encoding="utf-8")
    wired.request_lan("home-wifi", minutes=30)
    done = run_mode(radio, "boot")
    assert "instead" in said(done)
    assert radio.mode.read_text(encoding="utf-8").strip() == "hotspot"
    assert not radio.request.exists()


def test_the_network_a_person_names_is_the_one_the_boot_joins(wired, radio):
    no_radio(radio)
    wired.request_lan("ark-wifi-Office-9e380d99", minutes=20)
    run_mode(radio, "boot")
    calls = radio.calls.read_text(encoding="utf-8")
    assert "up ark-wifi-Office-9e380d99" in calls
    assert "up home-wifi" not in calls


def test_taking_a_request_back_leaves_nothing_waiting(wired, radio):
    no_radio(radio)
    wired.request_lan("home-wifi", minutes=30)
    assert radio.request.exists()
    assert "waiting" in wired.cancel_request()
    assert not radio.request.exists()
    assert "broadcast" in said(run_mode(radio, "boot"))


@pytest.mark.parametrize("bad", [0, -5])
def test_a_request_for_no_minutes_is_refused_before_anything_is_written(wired, radio, bad):
    no_radio(radio)
    with pytest.raises(wired.NetworkRefused) as caught:
        wired.request_lan("home-wifi", minutes=bad)
    assert str(wired.LAN_MINUTES_MAX) in str(caught.value)
    assert not radio.request.exists()          # nothing was asked of the box


# ---------------------------------------------------------------------------
#  the page — /admin/network behind the sign-in
# ---------------------------------------------------------------------------
def _page(client, cookie, path: str = "/admin/network"):
    """One GET of the owner's page, as a browser on the LAN makes it."""
    return get(client, path, cookie=f"ark_admin={cookie}")


def test_the_network_page_is_behind_the_sign_in(client, helpers, network):
    """A stranger with the address is sent to the sign-in, and no helper runs."""
    stranger = _page(client, "")
    assert stranger.status_code == 303
    assert "/admin/login" in stranger.headers["location"]
    assert "Where the box is now" not in stranger.text

    made = post(client, "/admin/network/add",
                {"ssid": "Rosebank Road", "password": "Let me in 42", "csrf": ""})
    assert made.status_code == 403                     # an explicit refusal, not a 200
    assert "Please sign in first" in made.text
    assert _calls(helpers) == []                       # the box was never asked


def test_a_page_with_forms_is_never_served_over_plain_http(client, owner, helpers, network):
    """A password must not cross a plain wire, so the page refuses at the door."""
    plain = get(client, "/admin/network", headers={})
    assert plain.status_code == 403
    assert "Use the secure address" in plain.text

    made = post(client, "/admin/network/add",
                {"ssid": "Rosebank Road", "password": "Let me in 42", "csrf": "whatever"},
                headers={})
    assert made.status_code == 403
    assert _calls(helpers) == []


def test_the_page_says_where_the_box_is_and_offers_both_things_to_do(client, owner,
                                                                    helpers, network):
    """The two jobs on one page: ask for one restart, and save a network."""
    (helpers.work / "mode").write_text("hotspot\n", encoding="utf-8")
    page = _page(client, owner)
    assert page.status_code == 200, page.text[:600]

    assert "Where the box is now" in page.text
    assert "broadcasting its own Wi-Fi" in page.text        # the state, in plain words
    assert "Restart on my own network for a while" in page.text
    assert 'action="/admin/network/lan"' in page.text
    assert "Add a Wi-Fi network" in page.text
    assert 'action="/admin/network/add"' in page.text
    assert 'name="password" type="password"' in page.text   # never a text field

    # the picker is built from the box's own saved networks, and its own access
    # point is not one of them: joining it would take the portal away.
    assert '<select id="network" name="network">' in page.text
    assert '<option value="home-wifi">Rosebank Road</option>' in page.text
    assert "The-Ark-Offline" not in page.text


def test_a_request_must_be_a_number_of_minutes_not_a_word(wired, radio):
    no_radio(radio)
    with pytest.raises(wired.NetworkRefused) as caught:
        wired.request_lan("home-wifi", minutes="soon")
    assert "whole number" in str(caught.value)
    assert not radio.request.exists()


# ---------------------------------------------------------------------------
#  the page — saving a network
# ---------------------------------------------------------------------------
def test_a_network_saved_from_the_page_reaches_the_box_with_the_password_on_stdin(
        client, owner, helpers, network, ark):
    """The form's one job: hand the box a name and a password, and say so."""
    page = _page(client, owner)
    done = post(client, "/admin/network/add",
                {"ssid": "Rosebank Road", "password": "Let me in 42",
                 "csrf": csrf_of(page.text)},
                cookie=f"ark_admin={owner}")
    assert _redirected(done) == 'Saved "Rosebank Road".'

    calls = _calls(helpers)
    assert calls[0] == "wifi-add --ssid Rosebank Road"    # the name only, on the line
    assert calls[1] == "stdin=Let me in 42"               # the password, on stdin
    assert "Let me in 42" not in calls[0]                 # never both on one line

    row = _audit(ark, "network-add")
    assert row is not None and row["result"] == "ok"
    assert 'saved a network called "Rosebank Road"' in row["detail"]


def test_the_password_is_never_written_back_onto_the_page(client, owner, helpers, network):
    """Not in the form, not in the banner, not anywhere the eye lands."""
    secret = "Let me in 42, seagull"
    page = _page(client, owner)
    done = post(client, "/admin/network/add",
                {"ssid": "Rosebank Road", "password": secret, "csrf": csrf_of(page.text)},
                cookie=f"ark_admin={owner}")
    assert done.status_code == 303, done.text[:600]

    after = get(client, done.headers["location"], cookie=f"ark_admin={owner}")
    assert after.status_code == 200
    assert secret not in after.text
    assert "Rosebank Road" in after.text          # the name may be shown, the password never


def test_a_password_that_cannot_work_is_refused_on_the_page_and_nothing_is_sent(
        client, owner, helpers, network, ark):
    """A refusal names the limit, and the helper is never run with a bad secret."""
    page = _page(client, owner)
    short = "short"
    made = post(client, "/admin/network/add",
                {"ssid": "Rosebank Road", "password": short, "csrf": csrf_of(page.text)},
                cookie=f"ark_admin={owner}")
    assert made.status_code == 400, made.text[:600]
    assert "Nothing was saved." in made.text
    assert str(network.PSK_MIN) in made.text      # the number to fix, in the sentence
    assert _calls(helpers) == []
    assert _audit(ark, "network-add") is None     # no history line for a thing not done


def test_a_network_name_with_a_stray_space_is_refused_by_the_page_not_trimmed(
        client, owner, helpers, network):
    """A name the box invented is worse than a refusal: the phone shows the name."""
    page = _page(client, owner)
    made = post(client, "/admin/network/add",
                {"ssid": " Rosebank Road ", "password": "Let me in 42",
                 "csrf": csrf_of(page.text)},
                cookie=f"ark_admin={owner}")
    assert made.status_code == 400, made.text[:600]
    assert "space" in made.text
    assert _calls(helpers) == []


# ---------------------------------------------------------------------------
#  the page — one restart, and taking it back
# ---------------------------------------------------------------------------
def test_the_page_hands_the_helper_one_name_and_one_number_and_nothing_more(
        client, owner, helpers, network):
    """The password is not here, but the words still are: name, minutes, who asked."""
    page = _page(client, owner)
    done = post(client, "/admin/network/lan",
                {"network": "home-wifi", "minutes": "20", "csrf": csrf_of(page.text)},
                cookie=f"ark_admin={owner}")
    assert _redirected(done) == "The request is waiting for the next restart."
    assert _calls(helpers) == [
        "ark-mode request-lan --minutes 20 --by admin --network home-wifi"]


def test_restarting_the_box_now_is_a_second_decision_and_it_reaches_the_helper(
        client, owner, helpers, network, ark):
    """The tick-box is a different thing to ask for, so it says so — and audits it."""
    page = _page(client, owner)
    done = post(client, "/admin/network/lan",
                {"network": "home-wifi", "minutes": "20", "restart": "1",
                 "csrf": csrf_of(page.text)},
                cookie=f"ark_admin={owner}")
    assert _redirected(done) == "The request is waiting for the next restart."
    assert _calls(helpers) == [
        "ark-mode request-lan --minutes 20 --by admin --network home-wifi --restart"]

    row = _audit(ark, "network-request-lan")
    assert row is not None and row["result"] == "ok"
    assert 'asked for one restart on "home-wifi" within 20 minutes' in row["detail"]
    assert "restarted the box now" in row["detail"]


def test_a_restart_that_failed_while_the_request_was_kept_says_so_first(
        client, owner, helpers, network, monkeypatch):
    """*The request is kept* — not the sentence the page then contradicts.

    ``ark-mode request-lan --restart`` writes the request and *then* asks for the
    reboot, so the failure it can report is the reboot, not the request.  A banner
    opening with *Nothing was asked.* would be denied two lines further down by the
    body's own **Waiting** block and its *Take that request back* button, both drawn
    from ``request_state()`` rather than from the form.
    """
    helpers.request.write_text(
        f"requested=2026-10-31T00:00:00Z\nexpires={int(time.time()) + 1200}\n"
        "minutes=20\nnetwork=home-wifi\nssid=Rosebank Road\nby=admin\n",
        encoding="utf-8")
    monkeypatch.setenv("FAKE_SAY", "the request is saved, but this box did not restart "
                                   "itself — restart it by hand (sudo reboot)")
    monkeypatch.setenv("FAKE_EXIT", "1")
    page = _page(client, owner)
    done = post(client, "/admin/network/lan",
                {"network": "home-wifi", "minutes": "20", "restart": "1",
                 "csrf": csrf_of(page.text)},
                cookie=f"ark_admin={owner}")

    assert done.status_code == 400, done.text[:600]
    assert "The request is kept." in done.text
    assert "Nothing was asked." not in done.text
    assert "did not restart itself" in done.text      # the helper's own words, escaped
    assert 'action="/admin/network/cancel"' in done.text   # and the way to take it back


def test_a_refusal_that_wrote_no_request_still_says_nothing_was_asked(
        client, owner, helpers, network, monkeypatch):
    """The other half: the honest sentence is not replaced by the flattering one."""
    monkeypatch.setenv("FAKE_SAY", "the box could not run that: no route to host")
    monkeypatch.setenv("FAKE_EXIT", "1")
    page = _page(client, owner)
    done = post(client, "/admin/network/lan",
                {"network": "home-wifi", "minutes": "20", "restart": "1",
                 "csrf": csrf_of(page.text)},
                cookie=f"ark_admin={owner}")

    assert done.status_code == 400, done.text[:600]
    assert "Nothing was asked." in done.text
    assert "The request is kept." not in done.text
    assert not helpers.request.exists()


def test_a_refusal_made_before_anything_runs_does_not_claim_the_old_request(
        client, owner, helpers, network, monkeypatch):
    """An old request waiting is not the request this form just tried to make.

    ``--minutes 0`` is refused inside ``ark_network.request_lan`` before any helper
    runs, so nothing was written — even when a request from earlier is still waiting.
    The kept sentence needs three things (the restart, a helper that ran, a request
    the clock still calls waiting); this is the case that proves the first two are
    required and not just the third.
    """
    waiting = (f"requested=2026-10-31T00:00:00Z\nexpires={int(time.time()) + 1200}\n"
               "minutes=20\nnetwork=home-wifi\nssid=Rosebank Road\nby=admin\n")
    helpers.request.write_text(waiting, encoding="utf-8")
    page = _page(client, owner)
    done = post(client, "/admin/network/lan",
                {"network": "home-wifi", "minutes": "0", "restart": "1",
                 "csrf": csrf_of(page.text)},
                cookie=f"ark_admin={owner}")

    assert done.status_code == 400, done.text[:600]
    assert "Nothing was asked." in done.text
    assert "The request is kept." not in done.text
    assert helpers.request.read_text(encoding="utf-8") == waiting


def test_a_restart_asked_for_from_the_page_is_used_by_exactly_one_boot(
        client, owner, wired, radio, ark):
    """The whole promise, from the form to the next start, through the real script."""
    no_radio(radio)
    page = _page(client, owner)
    done = post(client, "/admin/network/lan",
                {"network": "home-wifi", "minutes": "20", "csrf": csrf_of(page.text)},
                cookie=f"ark_admin={owner}")

    told = _redirected(done)
    assert told.startswith("asked:"), told          # the request, not the aside after it
    assert "home-wifi" in told and "once" in told

    written = radio.request.read_text(encoding="utf-8")
    assert "network=home-wifi" in written
    assert "minutes=20" in written
    assert "by=admin" in written

    run_mode(radio, "boot")
    calls = radio.calls.read_text(encoding="utf-8")
    assert "up home-wifi" in calls
    assert not radio.request.exists()               # used once, and gone

    row = _audit(ark, "network-request-lan")
    assert row is not None
    assert 'asked for one restart on "home-wifi" within 20 minutes' in row["detail"]


def test_a_waiting_request_is_shown_on_the_page_with_the_way_to_take_it_back(
        client, owner, wired, radio):
    """What is waiting, how long for, and one button — not a memory test."""
    no_radio(radio)
    wired.request_lan("home-wifi", minutes=30, by="admin")
    page = _page(client, owner)
    assert page.status_code == 200, page.text[:600]
    assert "Waiting:" in page.text
    assert "home-wifi" in page.text
    assert "in the next 30 minutes" in page.text
    assert 'action="/admin/network/cancel"' in page.text
    assert "Take that request back" in page.text


def test_taking_a_request_back_from_the_page_leaves_the_box_broadcasting(
        client, owner, wired, radio, ark):
    """A withdrawal is a thing the page can do, and the next boot proves it."""
    no_radio(radio)
    wired.request_lan("home-wifi", minutes=30, by="admin")
    assert radio.request.exists()

    page = _page(client, owner)
    done = post(client, "/admin/network/cancel", {"csrf": csrf_of(page.text)},
                cookie=f"ark_admin={owner}")
    assert "nothing is waiting" in _redirected(done)
    assert not radio.request.exists()

    run_mode(radio, "boot")
    assert "up ark-hotspot" in radio.calls.read_text(encoding="utf-8")
    assert _audit(ark, "network-cancel")["detail"] == "took the restart request back"


def test_a_lapsed_request_is_read_from_the_clock_not_from_its_own_words(
        client, owner, helpers, network):
    """The page says it has lapsed, and offers nothing to take back."""
    helpers.request.write_text("expires=1\nminutes=30\nnetwork=home-wifi\nby=admin\n",
                               encoding="utf-8")
    page = _page(client, owner)
    assert page.status_code == 200, page.text[:600]
    assert "has lapsed" in page.text
    assert "Waiting:" not in page.text
    assert 'action="/admin/network/cancel"' not in page.text


# ---------------------------------------------------------------------------
#  the installer's own knock on that door
#
#  Phase 70 proves the page can reach the helper by running it as `pi` with a
#  rehearsal name.  That proof can lie in one direction only — it can say "the
#  door is shut" about a door that answered — and it did, on the card, on
#  1 Nov 2026: the rehearsal asked to save "A network this box has never joined"
#  (35 characters), `ark-wifi-add` refused it at its own limit of 32, and a
#  perfectly good door was reported as unreachable:
#
#      ! the Network page cannot reach ark-wifi-add as pi — saving a network
#        will fail: Not done: a network name is at most 32 characters; that
#        one is 35.
#
#  Two things keep it honest, and both are held here: the name is one the
#  helper would accept, and a sentence in the helper's own words ("Not done:")
#  counts as having reached it, because a door nobody can knock on says
#  "sudo: a password is required" or "No such file", never "Not done:".
# ---------------------------------------------------------------------------
NETWORK_PHASE = REPO / "pi" / "steps" / "70-network.sh"


def _phase_rehearsal() -> str:
    text = NETWORK_PHASE.read_text(encoding="utf-8")
    match = re.search(r'ark-wifi-add --ssid "([^"]*)" --check', text)
    assert match, "phase 70 no longer knocks on ark-wifi-add at all"
    return match.group(1)


def test_the_installer_rehearses_a_network_name_the_helper_would_accept():
    """A rehearsal the helper refuses proves nothing about the door."""
    import ark_network

    name = _phase_rehearsal()
    assert ark_network.SSID_MIN <= len(name) <= ark_network.SSID_MAX, (
        f"the rehearsal asks to save a {len(name)}-character network name, which "
        f"ark-wifi-add refuses (its limit is {ark_network.SSID_MAX}) — the proof "
        "would then report a working door as a missing one"
    )


def test_the_installer_counts_the_helpers_own_refusal_as_having_reached_it():
    """`Not done:` is a sentence only the helper writes, so it proves the knock."""
    text = NETWORK_PHASE.read_text(encoding="utf-8")
    assert "Not done:" in text, (
        "a refusal in the helper's own words must not be reported as "
        "'cannot reach ark-wifi-add'"
    )
    lines = [line for line in text.splitlines()
             if not line.lstrip().startswith("#")]      # the words in the comment too
    cannot = [i for i, line in enumerate(lines) if "cannot reach ark-wifi-add" in line]
    assert len(cannot) == 1, "the 'cannot reach' sentence should exist exactly once"
    assert lines[cannot[0] - 1].strip() == "else", (
        "the 'cannot reach' sentence must be the last resort, after the "
        "helper's own refusal has been considered"
    )


