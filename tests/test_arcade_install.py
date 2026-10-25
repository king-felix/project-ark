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
        "rights_evidence": "the test entry's licence file allows copying",
        "language": "en", "language_evidence": "the test entry says English",
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
        "languages": ["en"], "languageEvidence": "the test entry says English",
        "rightsEvidence": "the test entry's licence file allows copying",
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
        "languages": ["en"], "languageEvidence": "the test entry says English",
        "rightsEvidence": "the test entry's licence file allows copying",
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
        "languages": ["en"], "languageEvidence": "the test entry says English",
    }

    def fake_json(url):
        if "/search?" in url:
            return {"entries": [search_entry]} if "platform=GB" in url else {"entries": []}
        if url.endswith("/waternet/game.json"):
            return {"slug": "waternet", "gameLicense": "MIT",
                    "languages": ["en"], "languageEvidence": "the test entry says English",
                    "rightsEvidence": "the test entry's licence file allows copying",
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
        assert conn.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 0


def test_automatic_installing_is_off_unless_the_owner_says_yes(ark):
    import ark_arcade_install as install
    with ark.db.tx() as conn:
        answer = install.auto_install(conn)
    assert answer["installed"] == []
    assert "off" in answer["skipped"]


def _owner_source(tmp_path: Path) -> Path:
    source = tmp_path / "stars.gb"
    source.write_bytes(_gb_rom())
    return source


def _owner_facts(source: Path) -> dict:
    return {
        "slug": "stars", "title": "Stars", "system": "gb",
        "license_text": "MIT",
        "license_evidence": "the owner kept the licence file beside this copy",
        "rights_evidence": "the licence file says the game may be copied and shared",
        "language": "en",
        "language_evidence": "the game's own title screen is in English",
    }


def test_an_owner_file_goes_through_the_same_gate_and_is_marked_owner_supplied(ark, tmp_path):
    import ark_arcade_install as install
    source = _owner_source(tmp_path)
    with ark.db.tx() as conn:
        result = install.import_owner_file(conn, source, **_owner_facts(source))
    assert result["installed"] is True
    with ark.db.read() as conn:
        game = conn.execute("SELECT * FROM games WHERE slug = 'stars'").fetchone()
    assert game["provider"] == install.OWNER_PROVIDER
    assert game["provenance"] == "owner-supplied:stars.gb"
    assert game["license"] == "MIT"
    assert game["language"] == "en"
    assert game["published"] == 1
    stored = ark.root / "arcade" / "games" / game["filename"]
    assert stored.is_file() and stored.read_bytes() == source.read_bytes()


def test_an_owner_file_without_proof_of_language_or_rights_is_refused(ark, tmp_path):
    import ark_arcade_install as install
    source = _owner_source(tmp_path)
    for field, value, words in (
        ("rights_evidence", "", "copied"),
        ("rights_evidence", "unknown", "copied"),
        ("language", "", "language"),
        ("language_evidence", "not stated", "language"),
        ("language", "de", "english"),
    ):
        facts = _owner_facts(source)
        facts[field] = value
        with ark.db.tx() as conn:
            with pytest.raises(install.InstallRefused) as refused:
                install.import_owner_file(conn, source, **facts)
        assert words in str(refused.value).lower(), (field, value)
    with ark.db.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 0
    games = ark.root / "arcade" / "games"
    assert not games.exists() or not any(games.iterdir())
    tmp = ark.root / "arcade" / "tmp"
    assert not tmp.exists() or not any(tmp.iterdir())


def test_a_candidate_without_proof_is_held_for_review_not_silently_installed():
    import ark_arcade_install as install
    assert install.candidate_status(_candidate())[0] == "pending"
    status, reason = install.candidate_status(_candidate(language_evidence="not stated"))
    assert status == "review" and "language" in reason
    status, reason = install.candidate_status(_candidate(rights_evidence=""))
    assert status == "review" and "copied" in reason
    assert install.candidate_status(_candidate(license="Freeware"))[0] == "refused"


@pytest.mark.parametrize("overrides, allowed", [
    # English, including a regional tag and the joined form a stored
    # candidate carries after a multilingual entry ("en, ja").
    ({"language": "en"}, True),
    ({"language": "en-GB"}, True),
    ({"language": "en, ja"}, True),
    ({"language": ["en", "ja"]}, True),
    # A game with no words at all is usable in any household.
    ({"language": "neutral", "language_evidence": "the game has no words"}, True),
    ({"language": "no-text", "language_evidence": "no written words"}, True),
    # A game in another language is not made English by a title or a filename.
    ({"language": "ja"}, False),
    ({"language": "de, fr"}, False),
    # A tag alone is not evidence, and an unrecorded language is not English.
    ({"language": "", "language_evidence": "the filename ends in (J)"}, False),
    ({"language": "", "language_evidence": ""}, False),
    ({"language": "en", "language_evidence": "not stated"}, False),
    # The source saying two different things is a conflict, not a guess.
    ({"language": "en", "language_conflict": True}, False),
])
def test_the_language_table_refuses_everything_it_cannot_prove_is_english(overrides, allowed):
    import ark_arcade_install as install
    ok, reason = install.language_facts(_candidate(**overrides))
    assert ok is allowed, (overrides, reason)
    if not allowed:
        assert reason


def test_multilingual_english_survives_the_trip_through_a_catalogue_entry():
    """The entry says ["en", "ja"]; the stored candidate says "en, ja"."""
    import ark_arcade_install as install
    entry = {
        "typetag": "game", "slug": "stars", "title": "Stars", "platform": "GB",
        "license": "MIT", "files": [{"filename": "stars.gb", "default": True}],
        "languages": ["en", "ja"], "languageEvidence": "the source lists English and Japanese",
        "rightsEvidence": "the entry's licence file allows copying",
    }
    candidate = install.entry_to_candidate(entry)
    assert candidate and candidate["language"].startswith("en")
    assert install.candidate_status(candidate)[0] == "pending"


def test_a_full_card_is_refused_before_a_game_is_fetched(ark, tmp_path, monkeypatch):
    import ark_arcade_install as install
    source = _owner_source(tmp_path)
    monkeypatch.setattr(install, "_free_bytes",
                        lambda where: install.MIN_FREE_AFTER_INSTALL - 1)
    with ark.db.tx() as conn:
        with pytest.raises(install.InstallRefused) as refused:
            install.import_owner_file(conn, source, **_owner_facts(source))
    assert "not enough room" in str(refused.value).lower()
    with ark.db.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 0
    games = ark.root / "arcade" / "games"
    assert not games.exists() or not any(games.iterdir())


def test_a_card_that_fills_while_copying_leaves_nothing_behind(ark, tmp_path, monkeypatch):
    import errno
    import ark_arcade_install as install
    source = _owner_source(tmp_path)
    monkeypatch.setattr(install, "_free_bytes", lambda where: 10 ** 12)

    def full(*args, **kwargs):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(install.os, "fsync", full)
    with ark.db.tx() as conn:
        with pytest.raises(install.InstallRefused) as refused:
            install.import_owner_file(conn, source, **_owner_facts(source))
    assert "filled up" in str(refused.value).lower()
    tmp = ark.root / "arcade" / "tmp"
    assert not tmp.exists() or not any(tmp.iterdir())
    with ark.db.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 0


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
