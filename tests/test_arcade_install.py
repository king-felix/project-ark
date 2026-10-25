"""The install pipeline: a suggestion is not a download, and a download is not a game.

Every gate is tested on its own, because the gate is the feature.  The source may
say whatever it likes; what reaches ``/srv/ark/arcade/games`` is decided by the
licence, the allowlist, the size cap, the SHA-256 and the shape of the file —
never by a URL a catalogue entry supplied.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest


def _candidate(**overrides):
    import ark_arcade_install as install
    candidate = {
        "provider": "test", "entry_id": "stars", "title": "Stars", "system": "gb",
        "core": "gambatte", "filename": "stars.gb", "license": "MIT",
        "license_evidence": "test entry: license MIT", "author": "Tester",
        "homepage": "", "description": "", "sha256": "", "size": 0,
        "download_url": install.SOURCE_RAW + "stars/stars.gb",
    }
    candidate.update(overrides)
    return candidate


def _gb_rom() -> bytes:
    """The smallest file ``rom_facts`` will accept as a Game Boy game."""
    data = bytearray(0x150)
    data[0x134:0x144] = b"ARK TEST".ljust(16, b"\0")
    csum = 0
    for byte in data[0x134:0x14D]:
        csum = (csum - byte - 1) & 0xFF
    data[0x14D] = csum
    return bytes(data)


def test_the_licence_is_the_first_gate():
    import ark_arcade_install as install
    for licence in ("", "Freeware", "All rights reserved", "CC-BY-NC", "CC-BY-ND"):
        ok, reason = install.eligibility(_candidate(license=licence))
        assert not ok, licence
        assert reason
    for licence in ("MIT", "Zlib", "GPL-3.0-or-later", "public-domain"):
        assert install.eligibility(_candidate(license=licence))[0], licence


def test_an_entry_cannot_move_the_download_to_another_host():
    import ark_arcade_install as install
    entry = {
        "typetag": "game", "slug": "stars", "title": "Stars", "platform": "GB",
        "license": "MIT", "files": [{"filename": "stars.gb", "default": True}],
        "download_url": "https://evil.example/stars.gb",
    }
    candidate = install.entry_to_candidate(entry)
    assert candidate
    assert candidate["download_url"] == install.SOURCE_RAW + "stars/stars.gb"
    assert install.eligibility(candidate)[0]
    assert not install.eligibility(_candidate(download_url="https://evil.example/stars.gb"))[0]


def test_an_entry_cannot_offer_an_archive_or_a_path():
    import ark_arcade_install as install
    for filename in ("../../stars.gb", "..\\stars.gb", ".hidden.gb", "stars.zip", "stars.txt",
                     "files/../stars.gb", "files/deeper/stars.gb", "https://evil/stars.gb"):
        entry = {
            "typetag": "game", "slug": "stars", "title": "Stars", "platform": "GB",
            "license": "MIT", "files": [{"filename": filename, "default": True}],
        }
        assert install.entry_to_candidate(entry) is None, filename


def test_the_api_file_shape_is_understood_and_a_gbc_is_not_a_gb():
    import ark_arcade_install as install
    entry = {
        "typetag": "game", "slug": "ryuha-studio_drakoko-gbc", "title": "Drakoko",
        "platform": "GB", "license": "MIT",
        "files": [{"filename": "files/drakoko2026demo.gbc", "playable": True,
                   "default": True}],
    }
    candidate = install.entry_to_candidate(entry)
    assert candidate
    assert candidate["system"] == "gbc"
    assert candidate["download_url"] == (
        install.SOURCE_RAW + "ryuha-studio_drakoko-gbc/files/drakoko2026demo.gbc")
    assert install.eligibility(candidate)[0]


def test_a_source_outside_the_allowlist_is_never_opened():
    import ark_arcade_install as install
    for url in ("https://evil.example/game.gb",
                "http://raw.githubusercontent.com.evil.example/x.gb",
                "file:///etc/passwd"):
        with pytest.raises(install.InstallRefused):
            install._get(url, 100)


def test_a_redirect_is_not_a_download():
    import ark_arcade_install as install
    assert install._NoRedirect().redirect_request(
        None, None, 302, "Found", {}, "https://evil.example/game.gb") is None


def test_a_renamed_web_page_is_refused_and_changes_nothing(ark, monkeypatch):
    import ark_arcade_install as install
    temp = Path(ark.root / "arcade" / "tmp")
    temp.mkdir(parents=True, exist_ok=True)
    fake = temp / "rom-bad.part"
    fake.write_bytes(b"<html>not a game</html>")
    monkeypatch.setattr(install, "_download_to_temp", lambda url: fake)

    with ark.db.tx() as conn:
        with pytest.raises(install.InstallRefused):
            install.publish_candidate(conn, _candidate())
    with ark.db.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 0
    games = ark.root / "arcade" / "games"
    assert not games.exists() or not any(games.iterdir())


def test_a_checked_game_is_published_with_a_fingerprint(ark, monkeypatch):
    import ark_arcade_install as install
    data = _gb_rom()
    temp = Path(ark.root / "arcade" / "tmp")
    temp.mkdir(parents=True, exist_ok=True)
    fake = temp / "rom-good.part"
    fake.write_bytes(data)
    monkeypatch.setattr(install, "_download_to_temp", lambda url: fake)

    with ark.db.tx() as conn:
        result = install.publish_candidate(conn, _candidate())
    assert result["installed"] is True
    assert result["sha256"] == hashlib.sha256(data).hexdigest()
    with ark.db.read() as conn:
        game = conn.execute("SELECT * FROM games WHERE slug = 'stars'").fetchone()
    stored = ark.root / "arcade" / "games" / game["filename"]
    assert stored.is_file() and stored.read_bytes() == data
    with ark.db.read() as conn:
        game = conn.execute("SELECT * FROM games WHERE slug = 'stars'").fetchone()
        revision = conn.execute("SELECT * FROM game_revisions WHERE game_id = ?",
                                (game["id"],)).fetchone()
    assert game["sha256"] == result["sha256"]
    assert revision["sha256"] == result["sha256"]


def test_discovery_asks_for_the_game_file_when_search_has_no_licence(ark, monkeypatch):
    """The search response carries no licence; the entry's game.json does."""
    import ark_arcade_install as install
    search_entry = {
        "typetag": "game", "slug": "waternet", "title": "Waternet", "platform": "GB",
        "license": None, "files": [{"filename": "files/waternet.gbc", "default": True}],
    }

    def fake_json(url):
        if "/search?" in url:
            return {"entries": [search_entry]} if "platform=GB" in url else {"entries": []}
        if url.endswith("/waternet/game.json"):
            return {"slug": "waternet", "gameLicense": "MIT",
                    "files": [{"filename": "files/waternet.gbc", "default": True}]}
        raise AssertionError(url)

    monkeypatch.setattr(install, "_json", fake_json)
    with ark.db.tx() as conn:
        answer = install.sync_catalog(conn, limit=5)
    assert answer["offered"] == 1
    with ark.db.read() as conn:
        row = conn.execute("SELECT title, license, status FROM acquisition_candidates").fetchone()
    assert row["license"] == "MIT"
    assert row["status"] == "pending"


def test_automatic_installing_is_off_unless_the_owner_says_yes(ark):
    import ark_arcade_install as install
    with ark.db.tx() as conn:
        answer = install.auto_install(conn)
    assert answer["installed"] == []
    assert "off" in answer["skipped"]


def test_the_first_shelf_dry_run_touches_nothing(ark):
    import ark_arcade_install as install
    with ark.db.tx() as conn:
        answer = install.install_seed(conn, dry_run=True)
    assert len(answer["installed"]) == 3
    assert not answer["errors"]
    with ark.db.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 0
    games = ark.root / "arcade" / "games"
    assert not games.exists() or not any(games.iterdir())
