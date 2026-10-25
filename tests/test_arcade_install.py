"""The install pipeline: a suggestion is not a download, and a download is not a game.

Every gate is tested on its own, because the gate is the feature.  The source may
say whatever it likes; what reaches ``/srv/ark/arcade/games`` is decided by the
allowlist, the size cap, the SHA-256 and the shape of the file — never by a URL a
catalogue entry supplied.  A licence is recorded beside a game, not used to refuse
it (owner's decision, 25 Oct 2026).
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


def test_a_licence_is_recorded_and_never_refuses_a_game():
    """The owner's decision: the shelf is built from what is good to play."""
    import ark_arcade as arc
    import ark_arcade_install as install
    for licence in ("", "Freeware", "All rights reserved", "CC-BY-NC", "CC-BY-ND", "MIT"):
        assert arc.license_problem(licence) == "", licence
        ok, reason = install.eligibility(_candidate(license=licence))
        assert ok, (licence, reason)
    # ...and it is still carried on the row, so the game's page can say what it is.
    entry = {
        "typetag": "game", "slug": "stars", "title": "Stars", "platform": "GB",
        "license": "CC-BY-NC-ND", "files": [{"filename": "stars.gb", "default": True}],
    }
    candidate = install.entry_to_candidate(entry)
    assert candidate and candidate["license"] == "CC-BY-NC-ND"


def test_a_game_boy_advance_entry_is_fetched_from_the_repository_that_holds_it():
    """GBA entries are not in gbdev/database; the old single prefix answered 404."""
    import ark_arcade_install as install
    entry = {
        "typetag": "game", "slug": "Apotris", "title": "Apotris", "platform": "GBA",
        "files": [{"filename": "Apotris.gba", "default": True}],
    }
    candidate = install.entry_to_candidate(entry)
    assert candidate and candidate["system"] == "gba"
    assert candidate["download_url"] == install.SOURCE_RAW_GBA + "apotris/Apotris.gba"
    assert install.eligibility(candidate)[0]
    assert install._trusted(candidate["download_url"])
    assert install._trusted(install.SOURCE_RAW + "stars/stars.gb")
    assert not install._trusted("https://raw.githubusercontent.com/gbadev-org/games-evil/x")
    assert not install._trusted("https://raw.githubusercontent.com.evil.example/x.gb")


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


def test_the_first_shelf_can_be_carried_to_a_box_with_no_internet(ark, tmp_path, monkeypatch):
    """A box with no route to the catalogue still gets its shelf, from a stick."""
    import ark_arcade_install as install
    data = _gb_rom()
    (tmp_path / "stars.gb").write_bytes(data)
    candidate = _candidate(filename="stars.gb", size=len(data),
                           sha256=hashlib.sha256(data).hexdigest())
    monkeypatch.setattr(install, "seed_candidates", lambda: [candidate])
    with ark.db.tx() as conn:
        answer = install.install_seed(conn, source_dir=tmp_path)
    assert answer["installed"] == ["stars"], answer
    with ark.db.read() as conn:
        game = conn.execute("SELECT * FROM games WHERE slug = 'stars'").fetchone()
    assert game["sha256"] == candidate["sha256"]
    stored = ark.root / "arcade" / "games" / game["filename"]
    assert stored.read_bytes() == data
    with ark.db.tx() as conn:
        again = install.install_seed(conn, source_dir=tmp_path)
    assert again["installed"] == [] and again["skipped"] == ["Stars"]


def test_a_carried_file_that_is_not_the_one_recorded_is_refused(ark, tmp_path, monkeypatch):
    import ark_arcade_install as install
    data = _gb_rom()
    (tmp_path / "stars.gb").write_bytes(data)
    monkeypatch.setattr(install, "seed_candidates", lambda: [
        _candidate(filename="stars.gb", size=len(data), sha256="0" * 64)])
    with ark.db.tx() as conn:
        answer = install.install_seed(conn, source_dir=tmp_path)
    assert answer["installed"] == []
    assert answer["errors"] and "promised" in answer["errors"][0]
    with ark.db.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 0
    assert not any((ark.root / "arcade" / "games").iterdir())


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


def test_an_owner_file_is_accepted_without_a_licence_but_held_back_in_another_language(ark, tmp_path):
    """A licence is provenance; language is still about whether a reader can play."""
    import ark_arcade_install as install
    source = _owner_source(tmp_path)
    facts = _owner_facts(source)
    facts.update({"license_text": "", "license_evidence": "", "rights_evidence": "",
                  "language": "", "language_evidence": ""})
    with ark.db.tx() as conn:
        result = install.import_owner_file(conn, source, **facts)
    assert result["installed"] is True
    with ark.db.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 1

    other = tmp_path / "other.gb"
    other.write_bytes(_gb_rom())
    facts = _owner_facts(other)
    facts.update({"slug": "other", "title": "Other", "language": "de"})
    with ark.db.tx() as conn:
        with pytest.raises(install.InstallRefused) as refused:
            install.import_owner_file(conn, other, **facts)
    assert "english" in str(refused.value).lower()
    tmp = ark.root / "arcade" / "tmp"
    assert not tmp.exists() or not any(tmp.iterdir())


def test_a_candidate_without_a_licence_is_not_held_for_review(ark):
    import ark_arcade_install as install
    assert install.candidate_status(_candidate())[0] == "pending"
    # A blank licence, a "freeware" string and missing words beside it are fine.
    status, _ = install.candidate_status(
        _candidate(license="", license_evidence="", rights_evidence=""))
    assert status == "pending"
    # A game the source says is in another language is still the owner's call.
    status, reason = install.candidate_status(_candidate(language="ja"))
    assert status == "review" and "japanese" in reason.lower()


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
    # A tag alone is not evidence — but an unrecorded language is no longer a
    # refusal: the catalogue is homebrew and most entries say nothing at all.
    ({"language": "", "language_evidence": "the filename ends in (J)"}, True),
    ({"language": "", "language_evidence": ""}, True),
    ({"language": "en", "language_evidence": "not stated"}, True),
    # The source saying two different things is a conflict, not a guess.
    ({"language": "en", "language_conflict": True}, False),
])
def test_the_language_table_allows_english_wordless_and_unrecorded(overrides, allowed):
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
    shelf = install.seed_candidates()
    assert shelf, "the pinned first shelf must not be empty"
    with ark.db.tx() as conn:
        answer = install.install_seed(conn, dry_run=True)
    assert len(answer["installed"]) == len(shelf)
    assert not answer["errors"]
    with ark.db.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 0
    games = ark.root / "arcade" / "games"
    assert not games.exists() or not any(games.iterdir())


def test_a_game_the_owner_describes_carries_its_words_onto_the_shelf(ark, tmp_path):
    """The one line and the credits reach the shelf row the game page reads."""
    import ark_arcade_install as install
    source = _owner_source(tmp_path)
    facts = _owner_facts(source)
    facts.update({"description": "A quiet game about climbing.",
                  "notices": "Made at a kitchen table, 2026."})
    with ark.db.tx() as conn:
        install.import_owner_file(conn, source, **facts)
    with ark.db.read() as conn:
        game = conn.execute("SELECT * FROM games WHERE slug = 'stars'").fetchone()
    assert game["description"] == "A quiet game about climbing."
    assert game["notices"] == "Made at a kitchen table, 2026."


def test_the_same_file_given_twice_is_not_put_on_the_shelf_twice(ark, tmp_path):
    """Dropping one copy in again says so — it does not become revision 2.

    An owner-supplied file used to carry no fingerprint, so the shelf could not
    tell "this is the game I already have" from "this is a new version of it":
    every re-import of an identical file added a revision and left a second
    copy on the card.
    """
    import ark_arcade_install as install
    source = _owner_source(tmp_path)
    with ark.db.tx() as conn:
        first = install.import_owner_file(conn, source, **_owner_facts(source))
    with ark.db.tx() as conn:
        again = install.import_owner_file(conn, source, **_owner_facts(source))
    assert first["installed"] is True
    assert again["installed"] is False
    assert "unchanged" in again["reason"]
    games = sorted(p.name for p in (ark.root / "arcade" / "games").iterdir())
    assert games == ["stars.gb"], games
    with ark.db.read() as conn:
        assert [r["revision"] for r in conn.execute("SELECT revision FROM games")] == [1]


def test_the_same_game_under_a_second_name_is_refused_with_the_name_it_has(ark, tmp_path):
    """Same bytes, different slug: one game, and the answer says which one."""
    import ark_arcade_install as install
    source = _owner_source(tmp_path)
    with ark.db.tx() as conn:
        install.import_owner_file(conn, source, **_owner_facts(source))
    twin = tmp_path / "copy.gb"
    twin.write_bytes(source.read_bytes())
    facts = _owner_facts(twin)
    facts.update({"slug": "stars-again", "title": "Stars Again"})
    with ark.db.tx() as conn:
        with pytest.raises(install.InstallRefused) as refused:
            install.import_owner_file(conn, twin, **facts)
    assert '"Stars"' in str(refused.value)
    with ark.db.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 1
    games = sorted(p.name for p in (ark.root / "arcade" / "games").iterdir())
    assert games == ["stars.gb"], games


def test_a_changed_file_under_the_same_name_is_still_a_new_revision(ark, tmp_path):
    """A real update keeps working: different bytes, same name, revision 2."""
    import ark_arcade_install as install
    source = _owner_source(tmp_path)
    with ark.db.tx() as conn:
        install.import_owner_file(conn, source, **_owner_facts(source))
    changed = tmp_path / "stars.gb"
    data = bytearray(_gb_rom())
    data[0x14F] = 0x01 if data[0x14F] == 0 else 0x00   # inside the game, outside its header
    changed.write_bytes(bytes(data))
    with ark.db.tx() as conn:
        result = install.import_owner_file(conn, changed, **_owner_facts(changed))
    assert result["installed"] is True
    assert result["revision"] == 2
    with ark.db.read() as conn:
        assert conn.execute("SELECT revision FROM games").fetchone()[0] == 2
