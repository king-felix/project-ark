"""Durability: what the box must still have after the power goes away.

Two defects this file exists to keep out of the box:

* **A record that was confirmed but not on the card.** SQLite's default
  ``synchronous=NORMAL`` reports a commit as soon as the bytes reach the kernel's
  cache, so a cut can silently swallow a note that was posted or a save that was
  confirmed.  ``ark_db.connect()`` sets ``FULL`` on every connection; the tests
  below read the pragma back from the connection the app really uses.
* **A save that was written over the one it replaced.** A save used to be one file
  per slot, overwritten in place, so a cut between "file written" and "row
  committed" left a row describing bytes that were no longer there — the reader
  then refused to hand out a save that had worked a moment before.  Saves are
  content-addressed now: the new bytes are a *new* file, the row moves after, and
  the old bytes are removed only once nothing names them.

The point of every test here is the same: the old thing must still be readable.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import os
import sqlite3
import time
import zipfile
from pathlib import Path

import pytest

SAVE = b"a save state nobody else has"


def _game(ark, slug: str = "stars") -> int:
    data = ("game bytes for " + slug).encode()
    games = ark.root / "arcade" / "games"
    games.mkdir(parents=True, exist_ok=True)
    (games / f"{slug}.gb").write_bytes(data)
    with ark.db.tx() as conn:
        cur = conn.execute(
            "INSERT INTO games (slug, title, system, core, filename, size, sha256, revision, "
            "installed_at, updated_at, published, hidden) VALUES (?, ?, 'gb', 'gambatte', ?, ?, "
            "?, 1, '2026-10-24T00:00:00Z', '2026-10-24T00:00:00Z', 1, 0)",
            (slug, slug.title(), f"{slug}.gb", len(data),
             hashlib.sha256(data).hexdigest()),
        )
        return int(cur.lastrowid)


def _player(ark, name: str = "reader") -> int:
    with ark.db.tx() as conn:
        cur = conn.execute(
            "INSERT INTO players (username, pass_hash, created_at) VALUES (?, ?, ?)",
            (name, "x", "2026-10-24T00:00:00Z"),
        )
        return int(cur.lastrowid)


@pytest.fixture()
def arcade(ark):
    import ark_arcade

    return ark_arcade


@pytest.fixture()
def stage(ark, arcade):
    """A player and a game on a throwaway box; ids only, no open connection."""
    return _player(ark), _game(ark)


def _put(ark, arcade, player: int, game: int, data: bytes, **kwargs):
    with ark.db.tx() as conn:
        return arcade.player_save_put(conn, player, game, "state", 1, data, **kwargs)


def _get(ark, arcade, player: int, game: int):
    with ark.db.read() as conn:
        return arcade.player_save_get(conn, player, game, "state", 1)


def _blobs(ark, player: int, game: int) -> list:
    folder = ark.root / "arcade" / "saves" / str(player) / str(game) / "blobs"
    return sorted(folder.iterdir()) if folder.is_dir() else []


def _refuse_the_record(monkeypatch):
    """Let the bytes be written, then refuse to move the row — the cut's worst moment.

    The outer transaction a test opens is left working, so ``player_save_put`` gets
    all the way to step 2; only the compare-and-set inside it fails, exactly as a
    box whose database is locked (or whose power went) would.
    """
    import ark_db

    real = ark_db.tx

    def guard(*args, conn=None, **kwargs):
        held = conn
        if args and isinstance(args[0], sqlite3.Connection):
            held = args[0]
            args = args[1:]
        if held is not None:
            @contextlib.contextmanager
            def broken():
                raise sqlite3.OperationalError("database is locked")
                yield                           # pragma: no cover - never reached

            return broken()
        return real(*args, **kwargs)

    monkeypatch.setattr(ark_db, "tx", guard)

    def restore():
        setattr(ark_db, "tx", real)

    return restore


# ---------------------------------------------------------------------------
#  the records: a commit the box reported is on the card
# ---------------------------------------------------------------------------
def test_every_connection_writes_fully_synced(ark):
    """WAL alone is not enough: with NORMAL a reported commit can still be lost."""
    with ark.db.read() as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert conn.execute("PRAGMA synchronous").fetchone()[0] == 2      # FULL
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    with ark.db.tx() as conn:
        assert conn.execute("PRAGMA synchronous").fetchone()[0] == 2


def test_no_module_weakens_the_connection_the_box_writes_with():
    """A pragma is per connection, so any second connection can undo the first.

    Only the throwaway catalogue build is allowed to write unsynced: it has no
    rows anybody depends on and it is verified after the fact.
    """
    import re as _re

    api = Path(__file__).resolve().parents[1] / "pi"
    allowed = {"ark_romdb_build.py"}
    offenders = []
    for path in list((api / "api").glob("*.py")) + list((api / "bin").glob("*")):
        if not path.is_file() or path.suffix == ".pyc":
            continue
        try:
            body = path.read_text(encoding="utf-8", errors="replace")
        except OSError:                              # pragma: no cover - unreadable
            continue
        for match in _re.finditer(r"PRAGMA\s+synchronous\s*=\s*(\w+)", body, _re.I):
            if match.group(1).upper() != "FULL" and path.name not in allowed:
                offenders.append(f"{path.name}: synchronous={match.group(1)}")
    assert offenders == []


def test_a_commit_the_box_confirmed_is_readable_from_a_connection_that_was_not_there(ark):
    with ark.db.tx() as conn:
        conn.execute(
            "INSERT INTO notes (ts, name, body) VALUES ('2026-10-24T00:00:00Z', 'reader', 'kept')"
        )
    other = sqlite3.connect(str(ark.root / "api/data/ark.db"))
    try:
        rows = other.execute("SELECT body FROM notes").fetchall()
    finally:
        other.close()
    assert rows == [("kept",)]


# ---------------------------------------------------------------------------
#  saves: the old bytes stay readable until the record has moved
# ---------------------------------------------------------------------------
def test_a_save_that_could_not_be_recorded_leaves_the_last_one_readable(ark, arcade, stage, monkeypatch):
    """The cut's worst moment: new bytes written, the row never moved.

    With one file per slot this is how a working save came back as “the file does
    not match its record” — the file had already been overwritten.
    """
    player, game = stage
    _put(ark, arcade, player, game, b"the save that worked")
    restore = _refuse_the_record(monkeypatch)
    with pytest.raises(sqlite3.OperationalError):
        _put(ark, arcade, player, game, SAVE)
    restore()
    kept = _get(ark, arcade, player, game)
    assert kept is not None
    assert kept["data"] == b"the save that worked"
    assert kept["revision"] == 1
    # Both copies are on the card: the one a row names, and the one it does not.
    contents = sorted(p.read_bytes() for p in _blobs(ark, player, game))
    assert contents == [SAVE, b"the save that worked"]


def test_the_save_a_new_one_replaces_is_forgotten_only_after_the_record_moved(ark, arcade, stage):
    player, game = stage
    _put(ark, arcade, player, game, b"the first save")
    second = _put(ark, arcade, player, game, SAVE)
    assert second["revision"] == 2
    blobs = _blobs(ark, player, game)
    assert len(blobs) == 1
    assert blobs[0].read_bytes() == SAVE
    assert _get(ark, arcade, player, game)["data"] == SAVE


def test_the_same_save_twice_keeps_one_copy_and_two_revisions(ark, arcade, stage):
    player, game = stage
    first = _put(ark, arcade, player, game, SAVE)
    again = _put(ark, arcade, player, game, SAVE)
    assert (first["revision"], again["revision"]) == (1, 2)
    assert len(_blobs(ark, player, game)) == 1


def test_a_blob_no_row_names_is_cleared_away_once_it_is_old(ark, arcade, stage):
    """A put cut short leaves bytes nothing names; they must not accumulate for ever."""
    player, game = stage
    _put(ark, arcade, player, game, b"the save that worked")
    folder = ark.root / "arcade" / "saves" / str(player) / str(game) / "blobs"
    stale = folder / "state-01-deadbeef.bin"
    stale.write_bytes(b"a put that never finished")
    old = time.time() - 48 * 3600
    os.utime(stale, (old, old))
    fresh = folder / "state-01-feedface.bin"
    fresh.write_bytes(b"another browser, still saving")
    _put(ark, arcade, player, game, b"the next save")
    assert not stale.exists()
    assert fresh.exists()                     # too young to be written off
    assert _get(ark, arcade, player, game)["data"] == b"the next save"


def test_a_record_whose_bytes_have_gone_answers_nothing_rather_than_a_lie(ark, arcade, stage):
    player, game = stage
    _put(ark, arcade, player, game, SAVE)
    for blob in _blobs(ark, player, game):
        blob.unlink()
    assert _get(ark, arcade, player, game) is None


def test_a_record_cannot_name_a_file_outside_its_own_folder(ark, arcade, stage):
    player, game = stage
    assert arcade.blob_path(player, game, "nested/save.bin") is None
    assert arcade.blob_path(player, game, "../save.bin") is None
    assert arcade.blob_path(player, game, "") is None
    row = {"game_id": game, "blob": "../escape.bin", "filename": "state-01.state"}
    assert arcade.save_file(player, row) is None
    row = {"game_id": game, "blob": "", "filename": "../../escape.state"}
    assert arcade.save_file(player, row) is None


def test_a_save_written_before_this_layout_is_still_read_and_then_retired(ark, arcade, stage):
    """A card that predates the blobs keeps its saves — and loses them only on a new put."""
    player, game = stage
    legacy = arcade.save_path(player, game, "state", 1)
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_bytes(b"an old-layout save")
    with ark.db.tx() as conn:
        conn.execute(
            "INSERT INTO player_saves (player_id, game_id, kind, slot, filename, size, sha256, "
            "  blob, revision, updated_at) VALUES (?, ?, 'state', 1, 'state-01.state', ?, ?, '', "
            "  1, '2026-10-24T00:00:00Z')",
            (player, game, len(b"an old-layout save"),
             hashlib.sha256(b"an old-layout save").hexdigest()),
        )
    kept = _get(ark, arcade, player, game)
    assert kept is not None and kept["data"] == b"an old-layout save"
    _put(ark, arcade, player, game, SAVE)
    assert not legacy.exists()
    assert _get(ark, arcade, player, game)["data"] == SAVE


def test_a_save_is_staged_beside_itself_and_its_folder_is_synced(ark, arcade, stage, monkeypatch):
    """A rename is only atomic inside one folder, and only durable once fsynced."""
    player, game = stage
    replaced = []
    real_replace = os.replace

    def recording(src, dst):
        replaced.append((Path(src), Path(dst)))
        return real_replace(src, dst)

    synced = []
    real_sync = arcade.sync_dir

    def recording_sync(folder):
        synced.append(Path(folder))
        return real_sync(folder)

    monkeypatch.setattr(arcade.os, "replace", recording)
    monkeypatch.setattr(arcade, "sync_dir", recording_sync)
    _put(ark, arcade, player, game, SAVE)
    monkeypatch.setattr(arcade.os, "replace", real_replace)
    monkeypatch.setattr(arcade, "sync_dir", real_sync)
    assert replaced, "a save that is written without a staged rename is not durable"
    src, dst = replaced[0]
    assert src.parent == dst.parent
    assert src.name.startswith(".save-") and src.name.endswith(".part")
    assert Path(synced[0]) == dst.parent


def test_a_write_that_fails_leaves_nothing_that_looks_like_a_save(ark, arcade, stage, monkeypatch):
    """Half a save must never be reachable, and must never be a row's answer."""
    player, game = stage
    _put(ark, arcade, player, game, b"the save that worked")
    real_fsync = os.fsync

    def refusing(fd):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(arcade.os, "fsync", refusing)
    with pytest.raises(OSError):
        _put(ark, arcade, player, game, SAVE)
    monkeypatch.setattr(arcade.os, "fsync", real_fsync)
    folder = ark.root / "arcade" / "saves" / str(player) / str(game)
    leftovers = [p.name for p in folder.rglob("*") if p.is_file() and p.name != "blobs"]
    assert [n for n in leftovers if n.startswith(".save-")] == []
    assert _get(ark, arcade, player, game)["data"] == b"the save that worked"


def test_the_listing_a_page_renders_does_not_hand_out_names_on_the_card(ark, arcade, stage):
    player, game = stage
    _put(ark, arcade, player, game, SAVE)
    with ark.db.read() as conn:
        listed = arcade.saves_for(conn, player, game)
    assert len(listed) == 1
    assert "blob" not in listed[0]
    assert listed[0]["filename"] == "state-01.state"


def test_the_zip_a_person_carries_away_holds_the_bytes_their_records_name(ark, arcade, stage):
    player, game = stage
    _put(ark, arcade, player, game, SAVE)
    with ark.db.read() as conn:
        blob = arcade.player_export_bytes(conn, player)
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        names = archive.namelist()
        assert "saves/stars/state-01.state" in names
        assert archive.read("saves/stars/state-01.state") == SAVE
    assert [n for n in names if "/blobs/" in n or n.endswith(".bin")] == []


def test_a_save_that_is_deleted_takes_its_bytes_with_it(ark, arcade, stage):
    player, game = stage
    _put(ark, arcade, player, game, SAVE)
    with ark.db.tx() as conn:
        save_id = conn.execute("SELECT id FROM player_saves").fetchone()[0]
        assert arcade.player_save_delete(conn, player, int(save_id)) is True
    assert _blobs(ark, player, game) == []
    assert _get(ark, arcade, player, game) is None
