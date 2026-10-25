"""Two numbers that must never disagree again, and one listener that must stay local.

Both were recorded defects: nginx accepted a 4 GiB request body while the
backend refused anything over 2 GiB (so a 3 GiB upload was read from the network
and then thrown away), and ``kiwix-serve`` listened on every address, so the
library could be reached without going through the proxy at all.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
NGINX = (REPO / "pi" / "nginx" / "ark.conf").read_text(encoding="utf-8")
KIWIX = (REPO / "pi" / "systemd" / "ark-kiwix.service").read_text(encoding="utf-8")

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


def test_the_library_engine_is_only_reachable_through_the_proxy():
    assert "--address=127.0.0.1" in KIWIX, "kiwix-serve must not listen on every interface"
    assert "--port=8081" in KIWIX


def test_the_admin_area_is_not_proxied_on_the_public_port():
    assert "location ^~ /admin { return 404; }" in NGINX
