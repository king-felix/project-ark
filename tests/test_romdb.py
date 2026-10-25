"""Recognising a game from the bytes inside it — and saying so in plain words.

Two things are proved here, and they are different questions:

* the *reader* (``ark_romdb``) finds the one catalogue row whose fingerprints are
  these bytes, looking through an emulator header, through an archive, and with
  the strongest digest first;
* the *words* (``ark_rommeta``) turn that answer into a pre-filled shelf entry
  without overwriting a single word the owner typed, and into sentences that name
  no algorithm at all — a reader on a phone has never heard of a checksum.

The catalogue a test builds is one made-up game, so nothing here needs the
internet, the real database or the Pi.  A box with **no** database is a working
box too, and that is one of the tests: every game plays, named from its file name.
"""

from __future__ import annotations

import hashlib
import io
import os
import time
import zipfile
import zlib
from pathlib import Path

import pytest

#: The made-up cartridge the sample catalogue lists, and the header an emulator
#: adds on top of it.  The catalogue lists the body only, which is the whole point.
BODY = bytes((i * 37) % 251 for i in range(16384))
NES_HEADER = b"NES\x1a" + bytes(12)


def _catalogue(tmp_path, monkeypatch):
    """Build a one-game catalogue and point the reader at it."""
    import ark_romdb as romdb
    import ark_romdb_build as build

    src = build._sample_tree(tmp_path / "src", BODY)
    db = tmp_path / "romdb.sqlite"
    rows, facts = build.collect(src)
    build.write_db(rows, db, facts, source="test")
    monkeypatch.setenv("ARK_ROMDB", str(db))
    romdb.forget_stats()
    return romdb


def _gb_rom() -> bytes:
    """The smallest file the installer accepts as a Game Boy game."""
    data = bytearray(0x150)
    data[0x134:0x144] = b"ARK TEST".ljust(16, b"\0")
    csum = 0
    for byte in data[0x134:0x14D]:
        csum = (csum - byte - 1) & 0xFF
    data[0x14D] = csum
    return bytes(data)


def test_a_game_is_named_from_its_bytes_even_when_the_file_name_says_nothing(
        tmp_path, monkeypatch):
    _catalogue(tmp_path, monkeypatch)
    import ark_rommeta as rommeta

    rom = tmp_path / "mystery.nes"
    rom.write_bytes(NES_HEADER + BODY)
    pre = rommeta.prefill(rom)

    assert pre["known"] is True
    assert pre["title"] == "Test Quest"
    assert pre["system"] == "nes" and pre["playable"] is True
    assert pre["confidence"] == "sha1"
    assert pre["facts"]["rom_year"] == "1989"
    assert pre["facts"]["rom_developer"] == "A Test Studio"
    assert pre["facts"]["rom_genre"] == "Puzzle"
    assert "Test Quest" in pre["sentence"]


def test_the_fingerprint_kept_describes_the_cartridge_not_the_wrapper(tmp_path, monkeypatch):
    """A copy with a header and a copy without one must find the same row."""
    romdb = _catalogue(tmp_path, monkeypatch)
    import ark_rommeta as rommeta

    with_header = tmp_path / "with-header.nes"
    with_header.write_bytes(NES_HEADER + BODY)
    pre = rommeta.prefill(with_header)

    assert romdb.stats()["rows"] == 1
    assert pre["view"] == "without its 16-byte Nintendo header"
    assert pre["fingerprints"]["sha1"] == hashlib.sha1(BODY).hexdigest()
    assert pre["fingerprints"]["crc32"] == "%08x" % (zlib.crc32(BODY) & 0xFFFFFFFF)

    bare = tmp_path / "bare.nes"
    bare.write_bytes(BODY)
    again = rommeta.prefill(bare)
    assert again["known"] is True and again["title"] == "Test Quest"
    assert again["fingerprints"] == pre["fingerprints"]


def test_the_strongest_fingerprint_is_the_one_used_and_kept(tmp_path, monkeypatch):
    romdb = _catalogue(tmp_path, monkeypatch)

    rom = tmp_path / "Test Quest (USA) (Rev 1).nes"
    rom.write_bytes(NES_HEADER + BODY)
    found = romdb.match(romdb.identify_path(rom))
    assert found["matched_by"] == "sha1"
    # The weaker sums point at the same row, so a partial match is never a lie.
    conn = romdb.connect()
    try:
        by_crc = romdb.lookup(conn, crc32=found["crc32"])
        by_md5 = romdb.lookup(conn, md5=found["md5"])
    finally:
        conn.close()
    assert by_crc["id"] == by_md5["id"] == found["id"]


def test_a_game_inside_an_archive_is_looked_inside(tmp_path, monkeypatch):
    _catalogue(tmp_path, monkeypatch)
    import ark_rommeta as rommeta

    carried = tmp_path / "carried.zip"
    with zipfile.ZipFile(carried, "w") as zf:
        zf.writestr("readme.txt", "not the game")
        zf.writestr("Test Quest (USA) (Rev 1).nes", NES_HEADER + BODY)
    pre = rommeta.prefill(carried)
    assert pre["known"] is True and pre["title"] == "Test Quest"


def test_a_file_nobody_listed_is_still_usable_and_claims_nothing(tmp_path, monkeypatch):
    romdb = _catalogue(tmp_path, monkeypatch)
    import ark_rommeta as rommeta

    stranger = tmp_path / "Some Game (USA).nes"
    stranger.write_bytes(b"NES\x1a" + bytes(12) + bytes(reversed(BODY)))
    pre = rommeta.prefill(stranger)

    assert pre["known"] is False
    assert pre["confidence"] == ""
    assert pre["title"] == "Some Game"
    assert pre["facts"]["rom_title"] == "" and pre["facts"]["rom_confidence"] == ""
    assert "Nobody has listed this file" in pre["sentence"]
    assert romdb.match(pre["ident"]) == {}


def test_a_word_the_owner_typed_is_never_replaced(tmp_path, monkeypatch):
    _catalogue(tmp_path, monkeypatch)
    import ark_rommeta as rommeta

    rom = tmp_path / "whatever.nes"
    rom.write_bytes(NES_HEADER + BODY)
    pre = rommeta.prefill(rom)

    typed = rommeta.apply({"title": "My Own Name", "description": "typed by me",
                           "system": "gb"}, pre)
    assert typed["title"] == "My Own Name"
    assert typed["description"] == "typed by me"
    assert typed["system"] == "gb"
    # The facts are still recorded, so the page can show them beside the words.
    assert typed["rom_year"] == "1989"

    blank = rommeta.apply({}, pre)
    assert blank["title"] == "Test Quest"
    assert blank["system"] == "nes"
    assert blank["release"] == "1989"
    assert blank["author"] == "A Test Studio"
    assert blank["description"] == "Puzzle game from 1989"
    assert blank["provenance"] == "recognised-from-contents"


def test_the_box_only_recognises_the_consoles_it_can_play():
    import ark_arcade as arc
    import ark_romdb as romdb
    assert set(romdb.PLAYABLE) == set(arc.SYSTEMS)


def test_a_box_with_no_catalogue_still_names_every_game_from_its_file_name(
        tmp_path, monkeypatch):
    import ark_romdb as romdb
    import ark_rommeta as rommeta

    monkeypatch.setenv("ARK_ROMDB", str(tmp_path / "nowhere" / "romdb.sqlite"))
    romdb.forget_stats()
    facts = romdb.stats()
    assert facts["present"] is False and facts["problem"] == "not built yet"

    rom = tmp_path / "Pocket Quest (Europe).nes"
    rom.write_bytes(NES_HEADER + BODY)
    pre = rommeta.prefill(rom)
    assert pre["known"] is False
    assert pre["title"] == "Pocket Quest"
    assert pre["confidence"] == ""
    assert pre["facts"]["rom_title"] == ""
    # ...and it is still a playable answer, from the extension.
    assert pre["system"] == "nes" and pre["playable"] is True


def test_the_page_words_never_name_an_algorithm(tmp_path, monkeypatch):
    _catalogue(tmp_path, monkeypatch)
    import ark_rommeta as rommeta

    rom = tmp_path / "mystery.nes"
    rom.write_bytes(NES_HEADER + BODY)
    pre = rommeta.prefill(rom)
    said = " ".join([
        pre["sentence"], rommeta.summary(pre),
        rommeta.confidence_words("sha1"), rommeta.confidence_words("md5"),
        rommeta.confidence_words("crc32"), rommeta.confidence_words("name"),
        rommeta.confidence_words(""),
    ]).lower()
    for word in ("sha", "md5", "crc", "hash", "checksum", "dictionary"):
        assert word not in said, word
    assert rommeta.confidence_words("sha1") == "the file itself"
    assert rommeta.confidence_words("name") == "its file name"
    assert rommeta.confidence_words("") == "nothing yet"


def test_a_page_shows_the_facts_and_the_catalogues_own_spelling():
    import ark_rommeta as rommeta

    game = {"title": "Test Quest", "rom_title": "Test Quest (USA) (Rev 1)",
            "rom_year": "1989", "rom_publisher": "A Test Label", "rom_genre": "Puzzle",
            "rom_players": "2", "rom_region": "USA", "rom_confidence": "sha1"}
    assert rommeta.recognised(game) is True
    facts = rommeta.game_facts(game, limit=6)
    assert facts[0] == "1989" and "A Test Label" in facts and "2 players" in facts
    assert rommeta.also_listed_as(game) == (
        'The box\'s own list calls this "Test Quest (USA) (Rev 1)".')
    # A game named from its file name claims nothing, and says nothing extra.
    plain = {"title": "Test Quest", "rom_title": "Test Quest", "rom_confidence": "name"}
    assert rommeta.recognised(plain) is False
    assert rommeta.also_listed_as(plain) == ""



def test_a_question_about_a_file_is_answered_about_that_file(tmp_path, monkeypatch, capsys):
    """``--status --out`` names one file, and says which file it looked at.

    Reading the box's default path while the caller named another is the same
    fault as ``--list`` answering for the wrong folder: silent, plausible and
    wrong.  A missing file must be reported as missing *there*.
    """
    import ark_romdb_build as build

    monkeypatch.delenv("ARK_ROMDB", raising=False)
    absent = tmp_path / "carried" / "romdb.sqlite"
    code = build.main(["--status", "--out", str(absent)])
    said = capsys.readouterr().out
    assert code == 1
    assert str(absent) in said and "No game-name database" in said

    src = build._sample_tree(tmp_path / "src", BODY)
    db = tmp_path / "carried" / "romdb.sqlite"
    db.parent.mkdir()
    rows, facts = build.collect(src)
    build.write_db(rows, db, facts, source="test")
    code = build.main(["--status", "--out", str(db)])
    said = capsys.readouterr().out
    assert code == 0
    assert str(db) in said and "1 game(s)" in said


def test_an_uploaded_file_waits_under_a_name_the_box_chose(ark):
    """A browser-supplied name never becomes a path: it is only a label."""
    import ark_arcade as arc
    import ark_arcade_install as install

    data = _gb_rom()
    waiting = install.spool_upload(io.BytesIO(data), filename="../../etc/passwd.gb")

    assert waiting.parent == arc.temp_root()
    assert install._UPLOAD_TOKEN.fullmatch(waiting.name), waiting.name
    assert waiting.name.startswith("upload-") and waiting.name.endswith(".gb")
    assert waiting.read_bytes() == data
    assert (waiting.stat().st_mode & 0o777) == 0o600
    assert install.upload_path(waiting.name) == waiting
    for attempt in ("..", "../ark.db", "/etc/passwd", "upload-zzz.gb", "", "ark.db"):
        assert install.upload_path(attempt) is None, attempt


def test_an_upload_is_not_a_game_until_somebody_confirms_it(ark):
    """Waiting files are swept; a game appears only after the publish path."""
    import ark_arcade as arc
    import ark_arcade_install as install

    waiting = install.spool_upload(io.BytesIO(_gb_rom()), filename="stars.gb")
    assert waiting.is_file()
    games = arc.games_dir()
    assert not games.exists() or not any(games.iterdir())

    assert install.discard_upload(waiting) is True
    assert not waiting.exists()

    again = install.spool_upload(io.BytesIO(_gb_rom()), filename="stars.gb")
    os.utime(again, (time.time() - 7 * 3600, time.time() - 7 * 3600))
    assert install.sweep_temp() >= 1
    assert not again.exists()


def test_an_uploaded_copy_of_a_game_already_on_the_shelf_is_refused_by_its_bytes(
        ark, tmp_path, monkeypatch):
    """The same cartridges under a second file name are the same game."""
    _catalogue(tmp_path, monkeypatch)          # the catalogue knows NES, not this GB file
    import ark_arcade as arc
    import ark_arcade_install as install

    data = _gb_rom()
    first = tmp_path / "stars.gb"
    first.write_bytes(data)
    facts = {"slug": "stars", "title": "Stars", "system": "gb",
             "license_text": "MIT", "language": "en"}
    with ark.db.tx() as conn:
        assert install.import_owner_file(conn, first, **facts)["installed"] is True

    with ark.db.read() as conn:
        game = conn.execute("SELECT * FROM games WHERE slug = 'stars'").fetchone()
        revision = conn.execute(
            "SELECT * FROM game_revisions WHERE game_id = ?", (game["id"],)).fetchone()
    # The fingerprints of the file itself are kept on the game and its revision,
    # even with no catalogue entry: they are what the next copy is judged against.
    assert game["sha1"] and game["md5"] and game["crc32"]
    assert game["rom_confidence"] == ""      # nothing listed these bytes
    assert revision["sha1"] == game["sha1"] and revision["crc32"] == game["crc32"]

    # The same bytes, arriving as an upload under a different name.
    waiting = install.spool_upload(io.BytesIO(data), filename="A Totally Different Game.gb")
    with ark.db.tx() as conn:
        with pytest.raises(install.InstallRefused) as refused:
            install.import_owner_file(
                conn, waiting, slug="different", title="Different", system="gb",
                filename="A Totally Different Game.gb", language="en")
    assert "already on the shelf" in str(refused.value)
    assert len(list(arc.games_dir().iterdir())) == 1
    install.discard_upload(waiting)
    assert not waiting.exists()


def test_a_recognised_file_is_stored_with_what_the_catalogue_said(
        ark, tmp_path, monkeypatch):
    """The pre-fill is not decoration: it is what the shelf row is written from."""
    _catalogue(tmp_path, monkeypatch)
    import ark_arcade_install as install

    rom = tmp_path / "no idea what this is.nes"
    rom.write_bytes(NES_HEADER + BODY)
    with ark.db.tx() as conn:
        result = install.import_owner_file(conn, rom, slug="recognised", language="en")
    assert result["installed"] is True

    with ark.db.read() as conn:
        game = conn.execute("SELECT * FROM games WHERE slug = 'recognised'").fetchone()
    assert game["title"] == "Test Quest"
    assert game["system"] == "nes"
    assert game["rom_confidence"] == "sha1"
    assert game["rom_year"] == "1989" and game["rom_developer"] == "A Test Studio"
    assert game["release"] == "1989" and game["author"] == "A Test Studio"
    assert game["description"] == "Puzzle game from 1989"
    # Where the game *came from* is the owner's own file; that it was *named* by
    # what is inside it is the rom_* facts (rom_confidence above) — two different
    # questions, recorded separately so neither can be mistaken for the other.
    assert game["provenance"] == "owner-supplied:no idea what this is.nes"

