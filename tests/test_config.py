"""Two numbers that must never disagree again, and one listener that must stay local.

Both were recorded defects: nginx accepted a 4 GiB request body while the
backend refused anything over 2 GiB (so a 3 GiB upload was read from the network
and then thrown away), and ``kiwix-serve`` listened on every address, so the
library could be reached without going through the proxy at all.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
NGINX = (REPO / "pi" / "nginx" / "ark.conf").read_text(encoding="utf-8")
KIWIX = (REPO / "pi" / "systemd" / "ark-kiwix.service").read_text(encoding="utf-8")
PORTAL_PHASE = (REPO / "pi" / "steps" / "60-portal.sh").read_text(encoding="utf-8")
EMULATOR = REPO / "pi" / "arcade" / "emulatorjs"

UNITS = {"k": 1024, "m": 1024 ** 2, "g": 1024 ** 3}


def _nginx_body_limit() -> int:
    match = re.search(r"^\s*client_max_body_size\s+(\d+)([kKmMgG])\s*;", NGINX, re.M)
    assert match, "client_max_body_size is missing from the portal server block"
    return int(match.group(1)) * UNITS[match.group(2).lower()]


def test_nginx_allows_the_app_limit_plus_framing_only():
    import ark_storage

    limit = _nginx_body_limit()
    assert limit >= ark_storage.MAX_FILE, "nginx would refuse a file the backend accepts"
    assert limit <= ark_storage.MAX_FILE + 16 * 1024 ** 2, (
        "nginx must not accept bodies the backend will only throw away"
    )


def test_ssh_stays_key_and_password_without_root_and_secrets_stay_out_of_git():
    """The first-boot convenience must not quietly weaken SSH, and no secret ships."""
    firstrun = (REPO / "staging" / "firstrun.sh.tmpl").read_text(encoding="utf-8")
    assert "PasswordAuthentication yes" in firstrun
    assert "PubkeyAuthentication yes" in firstrun
    assert "PermitRootLogin no" in firstrun
    assert "/home/pi/.ssh/authorized_keys" in firstrun

    ignore = (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()
    for secret in ("staging/wifi.env", "staging/firstrun.sh",
                   "staging/pi-password.txt", "staging/*.nmconnection"):
        assert secret in ignore, secret


def test_the_library_engine_is_only_reachable_through_the_proxy():
    assert "--address=127.0.0.1" in KIWIX, "kiwix-serve must not listen on every interface"
    assert "--port=8081" in KIWIX


def test_the_admin_area_is_not_proxied_on_the_public_port():
    assert "location ^~ /admin { return 404; }" in NGINX


# ---------------------------------------------------------------------------
#  the offline player: every byte it will ask for is here, and is the byte named
# ---------------------------------------------------------------------------
def _manifest() -> dict[str, str]:
    wanted = {}
    for line in (EMULATOR / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, _, rel = line.partition("  ")
        assert rel and len(digest) == 64, f"malformed SHA256SUMS line: {line!r}"
        wanted[rel] = digest
    return wanted


def test_every_vendored_player_file_matches_its_checksum():
    manifest = _manifest()
    actual = {p.relative_to(EMULATOR).as_posix()
              for p in EMULATOR.rglob("*") if p.is_file() and p.name != "SHA256SUMS"}
    assert actual == set(manifest), (
        "SHA256SUMS must list every vendored file and nothing else: "
        f"unlisted={sorted(actual - set(manifest))} missing={sorted(set(manifest) - actual)}")
    for rel, digest in manifest.items():
        assert hashlib.sha256((EMULATOR / rel).read_bytes()).hexdigest() == digest, rel


def test_the_player_has_every_add_on_it_loads_at_run_time():
    """The decompressors are fetched only when a game archive is opened.

    They are not imported by the page and nothing else references them, so their
    absence is invisible until a phone with no internet tries to play.  Every
    ``compression/…`` the minified player mentions must exist and be listed.
    """
    player = (EMULATOR / "emulator.min.js").read_text(encoding="utf-8", errors="replace")
    referenced = set(re.findall(r"compression/[A-Za-z0-9_.-]+", player))
    assert "compression/extractzip.js" in referenced
    assert "compression/libunrar.wasm" in referenced
    manifest = _manifest()
    for rel in referenced:
        assert (EMULATOR / rel).is_file(), rel
        assert rel in manifest, f"{rel} is served but not covered by SHA256SUMS"


def test_the_installer_proves_the_player_before_serving_it():
    assert "SHA256SUMS" in PORTAL_PHASE
    assert "sha256sum --quiet -c SHA256SUMS" in PORTAL_PHASE
    for rel in ("compression/extractzip.js", "compression/extract7z.js",
                "compression/libunrar.js", "compression/libunrar.wasm"):
        assert rel in PORTAL_PHASE, rel
