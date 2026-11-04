"""Recovery copies that agree with the files their records name.

The defect this file exists to keep out of the box: the daily pass kept the
database and nothing else, and a save is a row **and** a blob beside it.  A
database-only copy taken before a save was replaced names a blob the next save
deletes — so the copy restores a row whose bytes are gone.

Every test below is about one of the promises that replace it:

* a retained copy pins the save blobs its records name, so replacing or deleting
  a save does not take them away;
* capture and deletion take turns on one lock, so a blob cannot vanish between
  the database copy naming it and the manifest protecting it;
* a copy is published only after its database, manifest and pinned files have
  been checked and made durable;
* retention is bounded, and the last verified copy is never spent to make room;
* a copy is honest about a file that was already missing, including the older
  database-only shape this module adopts;
* validation reads the files, not just ``quick_check``;
* a backup of the records and the files beside them cannot be split by a save.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from importlib.machinery import SourceFileLoader
import os
import sqlite3
import threading
import time
from pathlib import Path

import pytest

import ark_db_replace

SAVE_ONE = b"the save that was named by the first recovery copy"
SAVE_TWO = b"the save that replaced it after the copy was taken"
SAVE_THREE = b"a third save, written later still"


# ---------------------------------------------------------------------------
#  a player, a game and a save
# ---------------------------------------------------------------------------
def _game(ark, slug: str = "stars") -> int:
    payload = ("game bytes for " + slug).encode()
    games = ark.root / "arcade" / "games"
    games.mkdir(parents=True, exist_ok=True)
    (games / f"{slug}.gb").write_bytes(payload)
    with ark.db.tx() as conn:
        cur = conn.execute(
            "INSERT INTO games (slug, title, system, core, filename, size, sha256, revision, "
            "installed_at, updated_at, published, hidden) VALUES (?, ?, 'gb', 'gambatte', ?, ?, "
            "?, 1, '2026-10-24T00:00:00Z', '2026-10-24T00:00:00Z', 1, 0)",
            (slug, slug.title(), f"{slug}.gb", len(payload),
             hashlib.sha256(payload).hexdigest()),
        )
        return int(cur.lastrowid)


def _player(ark, name: str = "reader") -> int:
    with ark.db.tx() as conn:
        cur = conn.execute(
            "INSERT INTO players (username, pass_hash, created_at) VALUES (?, ?, ?)",
            (name, "x", "2026-10-24T00:00:00Z"),
        )
        return int(cur.lastrowid)


def _stage(ark):
    return _player(ark), _game(ark)


def _put(ark, player: int, game: int, data: bytes):
    import ark_arcade
    with ark.db.tx() as conn:
        return ark_arcade.player_save_put(conn, player, game, "state", 1, data)


def _get(ark, player: int, game: int):
    import ark_arcade
    with ark.db.read() as conn:
        return ark_arcade.player_save_get(conn, player, game, "state", 1)


def _blobs(ark, player: int, game: int) -> list[Path]:
    folder = ark.root / "arcade" / "saves" / str(player) / str(game) / "blobs"
    return sorted(folder.iterdir()) if folder.is_dir() else []


def _capture(ark, **kwargs):
    import ark_recovery
    return ark_recovery.capture(ark.db.DB_PATH, root=ark.root, **kwargs)


def _copy_database(source: Path, target: Path) -> None:
    """A consistent database-only copy, the shape the old pass used."""
    src = sqlite3.connect(str(source))
    try:
        dst = sqlite3.connect(str(target))
        try:
            src.backup(dst)
            dst.execute("PRAGMA journal_mode=DELETE")
            dst.commit()
        finally:
            dst.close()
    finally:
        src.close()


# ---------------------------------------------------------------------------
#  the defect: an old copy names a blob a newer save removed
# ---------------------------------------------------------------------------
def test_a_recovery_copy_restores_the_exact_bytes_its_row_named(ark):
    """Create, snapshot, replace repeatedly, restore the old copy — exact bytes."""
    import ark_recovery

    player, game = _stage(ark)
    _put(ark, player, game, SAVE_ONE)
    taken = _capture(ark, reason="the test's first copy")
    assert taken.status == "captured", taken.said
    old_blob = _blobs(ark, player, game)[0]
    assert old_blob.read_bytes() == SAVE_ONE

    for payload in (SAVE_TWO, SAVE_THREE, b"a fourth save"):
        _put(ark, player, game, payload)
    assert _get(ark, player, game)["data"] == b"a fourth save"
    assert old_blob.is_file(), "the copy that names this blob is still retained"

    source = ark_recovery.restore_source(ark.db.DB_PATH)
    assert source is not None and source.generation == taken.generation
    assert source.complete is True, source.missing

    result = ark_db_replace.replace(source.path, ark.db.DB_PATH, reason="test restore")
    assert result.status == "ok", result.said
    restored = _get(ark, player, game)
    assert restored is not None
    assert restored["data"] == SAVE_ONE
    assert restored["revision"] == 1

    checked = ark_recovery.validate(ark.db.DB_PATH, taken.path)
    assert checked["ok"] is True and checked["complete"] is True, checked
    assert checked["pinned_missing"] == []


def test_replacing_a_save_keeps_its_blob_while_a_copy_names_it(ark):
    import ark_recovery

    player, game = _stage(ark)
    _put(ark, player, game, SAVE_ONE)
    taken = _capture(ark)
    old = _blobs(ark, player, game)[0]

    _put(ark, player, game, SAVE_TWO)
    assert old.is_file(), "a save replacement deleted a blob a retained copy needs"

    # Age it so a garbage collection that ignored the copy would remove it.
    stamp = time.time() - 48 * 3600
    os.utime(old, (stamp, stamp))
    report = ark_recovery.gc(ark.db.DB_PATH, now=time.time() + 1, grace=0)
    assert old.is_file(), report
    assert report["status"] == "done"

    assert ark_recovery.retire(ark.db.DB_PATH, taken.generation) is True
    report = ark_recovery.gc(ark.db.DB_PATH, now=time.time() + 1, grace=0)
    assert not old.exists(), "a retired copy must stop pinning its old blob"
    assert [p.read_bytes() for p in _blobs(ark, player, game)] == [SAVE_TWO]


def test_deleting_a_save_leaves_its_bytes_while_a_copy_names_them(ark):
    import ark_arcade
    import ark_recovery

    player, game = _stage(ark)
    _put(ark, player, game, SAVE_ONE)
    taken = _capture(ark)
    old = _blobs(ark, player, game)[0]

    with ark.db.tx() as conn:
        save_id = conn.execute("SELECT id FROM player_saves").fetchone()[0]
        assert ark_arcade.player_save_delete(conn, player, int(save_id)) is True
    assert _get(ark, player, game) is None
    assert old.is_file(), "deleting the row took bytes a retained copy names"

    ark_recovery.retire(ark.db.DB_PATH, taken.generation)
    stamp = time.time() - 48 * 3600
    os.utime(old, (stamp, stamp))
    ark_recovery.gc(ark.db.DB_PATH, now=time.time() + 1, grace=0)
    assert not old.exists()


# ---------------------------------------------------------------------------
#  publication is a finishing line, not a folder that happens to exist
# ---------------------------------------------------------------------------
def test_an_interrupted_copy_is_never_published_and_is_cleared(ark):
    import ark_recovery

    player, game = _stage(ark)
    _put(ark, player, game, SAVE_ONE)
    folder = ark_recovery.generations_dir(ark.db.DB_PATH)
    partial = folder / ".gen-20260101T000000Z-deadbeef.partial"
    partial.mkdir(parents=True)
    (partial / "ark.db").write_bytes(b"half a database")
    (partial / "manifest.json.tmp").write_text("{}", encoding="utf-8")
    old = time.time() - 48 * 3600
    os.utime(partial, (old, old))

    assert ark_recovery.published(ark.db.DB_PATH) == []
    taken = _capture(ark, now=time.time())
    assert taken.status == "captured", taken.said
    assert not partial.exists(), "an unfinished copy was left where a copy belongs"
    assert [p.name for p in ark_recovery.published(ark.db.DB_PATH)] == [taken.generation]


def test_a_copy_that_fails_before_publication_leaves_no_generation(ark, monkeypatch):
    import ark_recovery

    player, game = _stage(ark)
    _put(ark, player, game, SAVE_ONE)
    real_replace = os.replace

    def refuse(source, target):
        if str(source).endswith(".partial"):
            raise OSError("the power went away before the copy was published")
        return real_replace(source, target)

    monkeypatch.setattr(ark_recovery.os, "replace", refuse)
    with pytest.raises(OSError):
        _capture(ark)
    assert ark_recovery.published(ark.db.DB_PATH) == []
    assert not list(ark_recovery.generations_dir(ark.db.DB_PATH).glob(".gen-*")), \
        "a failed copy left its staging folder behind"

    monkeypatch.setattr(ark_recovery.os, "replace", real_replace)
    taken = _capture(ark)
    assert taken.status == "captured"
    assert ark_recovery.validate(ark.db.DB_PATH, taken.path)["complete"] is True


def test_a_copy_that_cannot_fit_leaves_the_last_verified_copy_alone(ark, monkeypatch):
    import ark_recovery

    player, game = _stage(ark)
    _put(ark, player, game, SAVE_ONE)
    first = _capture(ark)
    assert first.status == "captured"
    old = _blobs(ark, player, game)[0]

    monkeypatch.setattr(
        ark_recovery, "_space_allows",
        lambda db_path, need: (False, "There is not enough room on the card for that."))
    _put(ark, player, game, SAVE_TWO)
    second = _capture(ark)

    assert second.status == "refused"
    assert "not enough room" in second.said
    assert ark_recovery.published(ark.db.DB_PATH) == [first.path]
    assert ark_recovery.validate(ark.db.DB_PATH, first.path)["complete"] is True
    assert old.is_file(), "a refused copy must not take the last copy's bytes with it"


def test_retention_removes_an_old_copy_only_after_a_newer_one_is_whole(ark):
    import ark_recovery

    player, game = _stage(ark)
    _put(ark, player, game, SAVE_ONE)
    first = _capture(ark, now=time.time(), keep=2)
    first_blob = _blobs(ark, player, game)[0]

    _put(ark, player, game, SAVE_TWO)
    second = _capture(ark, now=time.time() + 10, keep=2)
    _put(ark, player, game, SAVE_THREE)
    third = _capture(ark, now=time.time() + 20, keep=2)

    living = {p.name for p in ark_recovery.published(ark.db.DB_PATH)}
    assert living == {second.generation, third.generation}
    assert not first.path.exists(), "the oldest copy was kept beyond the bound"
    for name in living:
        assert ark_recovery.validate(ark.db.DB_PATH, name)["complete"] is True

    old = time.time() - 48 * 3600
    os.utime(first_blob, (old, old))
    ark_recovery.gc(ark.db.DB_PATH, now=time.time() + 30, grace=0)
    assert not first_blob.exists(), "a retired generation kept pinning its blob"


# ---------------------------------------------------------------------------
#  capture and deletion take turns
# ---------------------------------------------------------------------------
def test_a_capture_in_progress_makes_collection_wait_instead_of_guessing(ark):
    import ark_recovery

    player, game = _stage(ark)
    _put(ark, player, game, SAVE_ONE)
    with ark_recovery.capture_lock(ark.db.DB_PATH, wait=1.0) as held:
        assert held is True
        report = ark_recovery.gc(ark.db.DB_PATH, wait=0.0, now=time.time() + 1)
        assert report["status"] == "deferred"
        assert report["removed"] == 0
    report = ark_recovery.gc(ark.db.DB_PATH, wait=1.0, now=time.time() + 1)
    assert report["status"] == "done"


def test_a_save_never_waits_for_a_copy_and_never_loses_its_bytes(ark):
    """While a copy owns the state lock, a replacement keeps both blobs."""
    import ark_recovery

    player, game = _stage(ark)
    _put(ark, player, game, SAVE_ONE)
    with ark_recovery.capture_lock(ark.db.DB_PATH, wait=1.0) as held:
        assert held is True
        _put(ark, player, game, SAVE_TWO)
        assert _get(ark, player, game)["data"] == SAVE_TWO
        assert {p.read_bytes() for p in _blobs(ark, player, game)} == {SAVE_ONE, SAVE_TWO}


def test_a_save_put_inside_a_callers_transaction_does_not_remove_the_blob_early(ark):
    """The row change and the removal become durable in that order, not the reverse."""
    import ark_arcade
    import ark_recovery

    player, game = _stage(ark)
    _put(ark, player, game, SAVE_ONE)
    old = _blobs(ark, player, game)[0]
    with ark.db.tx() as conn:
        ark_arcade.player_save_put(conn, player, game, "state", 1, SAVE_TWO)
        # The caller has not committed.  A copy taken now must see the old row
        # and the old blob together.
        taken = _capture(ark)
        assert taken.status == "captured", taken.said
        assert old.is_file(), "the blob was removed before the row that named it was committed"
        assert ark_recovery.protects(ark.db.DB_PATH, old) is True

    # The outer commit ran the deferred removal, and the copy's pin kept the blob.
    assert old.is_file(), "committing ran the cleanup through a retained copy's pin"
    assert ark_recovery.validate(ark.db.DB_PATH, taken.path)["complete"] is True
    assert ark_recovery.retire(ark.db.DB_PATH, taken.generation) is True
    stamp = time.time() - 48 * 3600
    os.utime(old, (stamp, stamp))
    ark_recovery.gc(ark.db.DB_PATH, now=time.time() + 1, grace=0)
    assert not old.exists()


def test_saves_and_copies_at_once_never_leave_a_copy_naming_a_gone_blob(ark):
    """The concurrency test the whole design exists for.

    Three players' worth of writers replace one save while copies are taken.
    Every published copy is then asked the only question that matters: do the
    files its records name still exist, with the bytes the records expect?
    """
    import ark_recovery

    player, game = _stage(ark)
    _put(ark, player, game, b"the first save")
    failures: list = []

    def writer(worker: int) -> None:
        for step in range(12):
            try:
                _put(ark, player, game, ("save %d from writer %d" % (step, worker)).encode())
            except Exception as exc:          # noqa: BLE001 — a lost compare-and-set is expected
                if exc.__class__.__name__ != "SaveConflict":
                    failures.append(exc)

    def copier() -> None:
        for _ in range(6):
            taken = _capture(ark)
            if taken.status != "captured":
                continue
            checked = ark_recovery.validate(ark.db.DB_PATH, taken.path)
            if checked["pinned_missing"] or checked["mismatched"]:
                failures.append((taken.generation, checked))
            time.sleep(0.01)

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(3)]
    threads.append(threading.Thread(target=copier))
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
        assert not thread.is_alive(), "a save or a copy stopped answering"

    assert failures == [], failures
    for path in ark_recovery.published(ark.db.DB_PATH):
        checked = ark_recovery.validate(ark.db.DB_PATH, path)
        assert checked["ok"] is True, checked
        assert checked["pinned_missing"] == [], checked
        assert checked["mismatched"] == [], checked


# ---------------------------------------------------------------------------
#  validation reads the files, not only the database
# ---------------------------------------------------------------------------
def test_validation_notices_a_missing_blob_and_a_changed_one(ark):
    import ark_recovery

    player, game = _stage(ark)
    _put(ark, player, game, SAVE_ONE)
    taken = _capture(ark)
    blob = _blobs(ark, player, game)[0]

    blob.write_bytes(b"bytes that are not the save the row describes")
    changed = ark_recovery.validate(ark.db.DB_PATH, taken.path)
    assert changed["ok"] is True, "the database itself is still readable"
    assert changed["complete"] is False
    assert changed["mismatched"], changed

    blob.unlink()
    missing = ark_recovery.validate(ark.db.DB_PATH, taken.path)
    assert missing["ok"] is True
    assert missing["complete"] is False
    assert missing["pinned_missing"], missing


# ---------------------------------------------------------------------------
#  the older database-only copy is adopted, honestly
# ---------------------------------------------------------------------------
def test_the_repair_pass_restores_a_generation_and_reports_its_files(ark):
    """The self-heal wiring, not just the module underneath it."""
    import ark_self_heal as heal

    player, game = _stage(ark)
    _put(ark, player, game, SAVE_ONE)
    taken = _capture(ark)
    ark.db.DB_PATH.write_bytes(b"this is not a database any more" * 100)

    report = heal.run(ark.root, current=time.time() + 1, setter=lambda stamp: True)

    rows = [row for row in report["databases"] if row["file"].endswith("ark.db")]
    assert rows and rows[0]["state"] == "restored", report["databases"]
    assert report["problems"] == [], report["problems"]
    assert _get(ark, player, game)["data"] == SAVE_ONE
    assert any(item.get("name") == taken.generation for item in report["recovery_copies"])
    assert "Still to look at" not in " ".join(heal.summary(report))


def test_the_database_only_copy_is_refreshed_from_the_live_records(ark):
    """After a restore, the old .good.bak must not still describe the old state."""
    import ark_recovery

    player, game = _stage(ark)
    _put(ark, player, game, SAVE_ONE)
    _capture(ark)
    _put(ark, player, game, SAVE_TWO)

    source = ark.root / "api" / "data" / "restored-copy.db"
    _copy_database(ark.db.DB_PATH, source)
    assert ark_recovery.mark_good(ark.db.DB_PATH, source) is True
    legacy = Path(str(ark.db.DB_PATH) + ".good.bak")
    conn = sqlite3.connect(str(legacy))
    try:
        revision = conn.execute("SELECT revision FROM player_saves").fetchone()[0]
    finally:
        conn.close()
    assert revision == 2, "the database-only copy is still the pre-restore state"


def test_a_replaced_good_bak_has_no_old_journal_beside_it(ark):
    """A journal belongs to the file it was written for, not to the name."""
    import ark_recovery

    player, game = _stage(ark)
    _put(ark, player, game, SAVE_ONE)
    _capture(ark)
    legacy = Path(str(ark.db.DB_PATH) + ".good.bak")
    for suffix in ("-wal", "-shm"):
        Path(str(legacy) + suffix).write_bytes(b"belongs to the old file")
    source = ark.root / "api" / "data" / "fresh-copy.db"
    _copy_database(ark.db.DB_PATH, source)
    assert ark_recovery.mark_good(ark.db.DB_PATH, source) is True
    assert not Path(str(legacy) + "-wal").exists()
    assert not Path(str(legacy) + "-shm").exists()


def test_an_old_database_only_copy_is_adopted_and_its_missing_files_named(ark):
    import ark_recovery

    player, game = _stage(ark)
    _put(ark, player, game, SAVE_ONE)
    legacy = Path(str(ark.db.DB_PATH) + ".good.bak")
    _copy_database(ark.db.DB_PATH, legacy)
    for blob in _blobs(ark, player, game):
        blob.unlink()                         # the old defect, already happened

    adopted = ark_recovery.adopt_legacy(ark.db.DB_PATH, root=ark.root, now=time.time())
    assert adopted is not None
    assert adopted.status == "adopted", adopted.said
    assert adopted.manifest["legacy"] is True
    assert adopted.manifest["complete"] is False
    assert adopted.missing and adopted.missing[0]["to"].startswith("arcade/saves/")
    assert "missing" in adopted.said
    assert os.path.samefile(legacy, adopted.path / "ark.db"), \
        "the database-only name should now point at the adopted copy"

    source = ark_recovery.restore_source(ark.db.DB_PATH)
    assert source is not None
    assert source.legacy is False, "the adopted copy should be a real generation"
    assert source.complete is False
    assert source.missing


def test_a_restore_takes_the_copies_that_described_the_old_records_out_of_service(ark):
    """``ark-restore`` calls this while it still holds the capture lock."""
    import ark_recovery

    player, game = _stage(ark)
    _put(ark, player, game, SAVE_ONE)
    taken = _capture(ark)
    assert ark_recovery.published(ark.db.DB_PATH) == [taken.path]

    retired = ark_recovery.retire_all_locked(
        ark.db.DB_PATH, reason="the records were restored from a bundle")
    assert retired == [taken.generation]
    assert ark_recovery.published(ark.db.DB_PATH) == []
    assert any(path.name.startswith(".retired-")
               for path in ark_recovery.generations_dir(ark.db.DB_PATH).iterdir())


# ---------------------------------------------------------------------------
#  other external state: pinned user files, bulk content, no duplication
# ---------------------------------------------------------------------------
def _shared(ark, name: str, payload: bytes) -> Path:
    path = ark.root / "drop" / "files" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    with ark.db.tx() as conn:
        conn.execute(
            "INSERT INTO files (ts, name, stored, display, size, sha256) "
            "VALUES ('2026-10-24T00:00:00Z', ?, ?, ?, ?, ?)",
            (name, name, name.split("-", 1)[-1], len(payload),
             hashlib.sha256(payload).hexdigest()),
        )
    return path


def test_a_small_shared_file_is_pinned_and_not_copied(ark):
    import ark_recovery

    payload = b"a document a person shared" * 1000
    shared = _shared(ark, "0123456789abcdef-gasket.pdf", payload)
    taken = _capture(ark)
    assert taken.status == "captured"

    inside = [p.name for p in taken.path.rglob("*") if p.is_file()]
    assert sorted(inside) == ["ark.db", "manifest.json"], \
        "a recovery copy must not duplicate the files it pins"
    refs = [ref for ref in taken.manifest["references"]
            if ref["to"] == "drop/files/" + shared.name]
    assert refs and refs[0]["pin"] is True and refs[0]["checked"] == "hash"
    assert taken.manifest["counts"]["pinned_bytes"] >= len(payload)


def test_a_large_shared_file_is_bulk_even_when_its_row_says_zero(ark):
    """The card's size is the fact, not a wrong or legacy row in the records."""
    import ark_recovery

    path = ark.root / "drop" / "files" / "0123456789abcdef-big.iso"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as handle:
        handle.seek(ark_recovery.PIN_LIMIT)
        handle.write(b"x")
    with ark.db.tx() as conn:
        conn.execute(
            "INSERT INTO files (ts, name, stored, display, size, sha256) "
            "VALUES ('2026-10-24T00:00:00Z', ?, ?, 'big.iso', 0, '')",
            (path.name, path.name),
        )
    taken = _capture(ark)
    assert taken.status == "captured"
    refs = [ref for ref in taken.manifest["references"]
            if ref["to"] == "drop/files/" + path.name]
    assert refs and refs[0]["pin"] is False and refs[0]["bulk"] is True
    assert refs[0]["checked"] == "size"
    assert not any(p.name == path.name for p in taken.path.rglob("*"))


def test_a_film_is_recorded_but_not_pinned_or_copied(ark):
    import ark_recovery

    play = ark.root / "media" / "play" / "m7.mp4"
    play.parent.mkdir(parents=True, exist_ok=True)
    play.write_bytes(b"a playing copy is bulk content" * 1000)
    with ark.db.tx() as conn:
        conn.execute(
            "INSERT INTO media (ts, title, filename, stored, play_stored, play_size, state) "
            "VALUES ('2026-10-24T00:00:00Z', 'A film', 'film.mp4', 'film-original', "
            "'m7.mp4', ?, 'ready')", (play.stat().st_size,),
        )
    taken = _capture(ark)
    assert taken.status == "captured"
    refs = [ref for ref in taken.manifest["references"] if ref["to"] == "media/play/m7.mp4"]
    assert refs and refs[0]["pin"] is False and refs[0]["bulk"] is True
    assert not any(p.name == "m7.mp4" for p in taken.path.rglob("*"))
    # A bulk file that goes away later is reported, not silently pretended.
    play.unlink()
    checked = ark_recovery.validate(ark.db.DB_PATH, taken.path)
    assert checked["missing"]
    assert not checked["pinned_missing"]


def test_deleting_a_pinned_shared_file_retires_the_copy_that_needs_it(ark):
    import ark_recovery

    shared = _shared(ark, "0123456789abcdef-notes.txt", b"the only copy of these notes")
    taken = _capture(ark)
    assert taken.status == "captured"
    assert ark_recovery.published(ark.db.DB_PATH) == [taken.path]

    assert ark_recovery.discard(ark.db.DB_PATH, shared,
                                reason="the owner deleted it") is True
    assert not shared.exists()
    assert ark_recovery.published(ark.db.DB_PATH) == [], \
        "a copy that named the deleted file must not remain in service"
    assert any(p.name.startswith(".retired-")
               for p in ark_recovery.generations_dir(ark.db.DB_PATH).iterdir())


def test_a_retired_copy_does_not_come_back_as_the_only_copy(ark):
    """Deleting the file a copy pinned must not re-publish the pre-delete records."""
    import ark_admin
    import ark_recovery

    shared = _shared(ark, "0123456789abcdef-notes.txt", b"the notes the owner deletes")
    taken = _capture(ark)
    assert taken.status == "captured"
    assert ark_admin._remove_from_card(shared.name) is True
    with ark.db.tx() as conn:
        conn.execute("DELETE FROM files WHERE stored = ?", (shared.name,))
    assert not shared.exists()

    fresh = _capture(ark)
    assert fresh.status == "captured", fresh.said
    assert fresh.manifest["complete"] is True, fresh.manifest.get("missing")
    assert fresh.generation != taken.generation
    assert ark_recovery.published(ark.db.DB_PATH) == [fresh.path]
    legacy = Path(str(ark.db.DB_PATH) + ".good.bak")
    assert legacy.is_file()
    assert os.path.samefile(legacy, fresh.path / "ark.db"), \
        "the database-only name must point at the fresh copy, not the retired one"


def test_a_good_bak_linked_to_a_retired_copy_is_not_adopted(ark):
    """A hard link to a retired database is that database, not a fresh copy."""
    import ark_recovery

    player, game = _stage(ark)
    _put(ark, player, game, SAVE_ONE)
    taken = _capture(ark)
    assert ark_recovery.retire(ark.db.DB_PATH, taken.generation) is True
    retired = [p for p in ark_recovery.generations_dir(ark.db.DB_PATH).iterdir()
               if p.name.startswith(".retired-")]
    assert retired
    good = Path(str(ark.db.DB_PATH) + ".good.bak")
    os.link(retired[0] / "ark.db", good)

    assert ark_recovery.adopt_legacy(ark.db.DB_PATH, root=ark.root) is None
    fresh = _capture(ark)
    assert fresh.status == "captured"
    assert fresh.manifest["complete"] is True


def test_an_incomplete_copy_does_not_suppress_a_fresh_one(ark):
    """A copy that names a file already gone is not a reason to wait a day."""
    import ark_recovery

    player, game = _stage(ark)
    _put(ark, player, game, SAVE_ONE)
    blob = _blobs(ark, player, game)[0]
    blob.unlink()
    first = _capture(ark)
    assert first.status == "captured"
    assert first.manifest["complete"] is False

    again = ark_recovery.capture(ark.db.DB_PATH, root=ark.root,
                                 min_interval_hours=24)
    assert again.status == "captured", again.said
    assert again.generation != first.generation
    assert len(ark_recovery.published(ark.db.DB_PATH)) == 2


def test_a_retired_generation_is_not_swept_as_an_unfinished_copy(ark):
    import ark_recovery

    folder = ark_recovery.generations_dir(ark.db.DB_PATH)
    folder.mkdir(parents=True, exist_ok=True)
    retired = folder / ".retired-gen-20260101T000000Z-deadbeef-1"
    retired.mkdir()
    (retired / "ark.db").write_bytes(b"retired evidence")
    (retired / "retired.json").write_text("{}", encoding="utf-8")
    old = time.time() - 8 * 3600
    os.utime(retired, (old, old))

    report = ark_recovery.gc(ark.db.DB_PATH, now=time.time(), grace=ark_recovery.STALE_SECONDS)
    assert retired.is_dir(), "a retired copy was called an unfinished one and removed"
    assert report["partials"] == []

    later = time.time() + ark_recovery.RETIRED_SECONDS + 1
    report = ark_recovery.gc(ark.db.DB_PATH, now=later, grace=ark_recovery.STALE_SECONDS)
    assert not retired.exists()
    assert report["retired"]


def test_a_hidden_shared_file_is_still_pinned(ark):
    """Hide is not a synonym for film: a hidden document is still user state."""
    import ark_recovery

    path = ark.root / "drop" / "files" / "0123456789abcdef-hidden.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"a document the owner hid from the File Box")
    with ark.db.tx() as conn:
        conn.execute(
            "INSERT INTO files (ts, name, stored, display, size, sha256, hidden) "
            "VALUES ('2026-10-24T00:00:00Z', ?, ?, 'hidden.txt', ?, '', 1)",
            (path.name, path.name, path.stat().st_size),
        )
    taken = _capture(ark)
    assert taken.status == "captured"
    refs = [ref for ref in taken.manifest["references"]
            if ref["to"] == "drop/files/" + path.name]
    assert refs and refs[0]["pin"] is True and refs[0]["bulk"] is False


def test_a_film_delete_refuses_while_a_copy_owns_its_bytes(ark, monkeypatch):
    """A busy lock leaves the film and its rows; it never splits the card."""
    import ark_media
    import ark_recovery

    play = ark.root / "media" / "play" / "m9.mp4"
    play.parent.mkdir(parents=True, exist_ok=True)
    play.write_bytes(b"a playing copy")
    with ark.db.tx() as conn:
        cur = conn.execute(
            "INSERT INTO media (ts, title, filename, stored, play_stored, play_size, state) "
            "VALUES ('2026-10-24T00:00:00Z', 'A film', 'film.mp4', '', 'm9.mp4', ?, 'ready')",
            (play.stat().st_size,),
        )
        media_id = int(cur.lastrowid)

    monkeypatch.setattr(ark_recovery, "DISCARD_WAIT", 0.1)
    with ark_recovery.capture_lock(ark.db.DB_PATH, wait=1.0) as held:
        assert held
        with ark.db.tx() as conn:
            result = ark_media.delete(conn, media_id)
        assert result["removed"] == 0
        assert result["reason"] == "busy"
    with ark.db.read() as conn:
        assert ark_media.item(conn, media_id) is not None
    assert play.is_file()


# ---------------------------------------------------------------------------
#  an explicit backup of the records and the files beside them
# ---------------------------------------------------------------------------
def _load_backup_tool():
    path = Path(__file__).resolve().parents[1] / "pi" / "bin" / "ark-backup"
    loader = SourceFileLoader("ark_backup_under_test", str(path))
    spec = importlib.util.spec_from_loader("ark_backup_under_test", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def test_a_backup_cannot_be_split_by_a_save_landing_during_the_copy(ark, monkeypatch):
    """The same race as the daily copy, in the explicit backup.

    The backup copies the records, then the files beside them.  A save that is
    replaced between those two moments must not delete the blob the copied
    records name.  The lock is what makes that true; this test makes the save
    land exactly in the window and then proves the bundle still agrees with
    itself.
    """
    tool = _load_backup_tool()
    player, game = _stage(ark)
    _put(ark, player, game, SAVE_ONE)
    old_blob = _blobs(ark, player, game)[0]

    real_file_entry = tool.bundle_tools.file_entry
    landed = threading.Event()

    def file_entry_with_a_save_landing(bundle, source, rel, to):
        if rel.startswith("arcade-saves/") and not landed.is_set():
            landed.set()
            writer = threading.Thread(target=lambda: _put(ark, player, game, SAVE_TWO))
            writer.start()
            writer.join(timeout=30)
            assert not writer.is_alive()
        return real_file_entry(bundle, source, rel, to)

    monkeypatch.setattr(tool.bundle_tools, "file_entry", file_entry_with_a_save_landing)
    destination = ark.root / "backup"
    destination.mkdir(parents=True, exist_ok=True)
    assert tool.take(destination, keep=2, no_files=False, quiet=True, dry=False) == 0

    bundles = [p for p in destination.iterdir() if p.name.startswith("ark-backup-")]
    assert len(bundles) == 1
    bundle = bundles[0]
    data = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    references = data["references"]
    assert references["available"] is True
    assert references["missing_saves"] == [], references
    carried = {entry["to"] for entry in data["entries"] if entry.get("to")}
    assert f"arcade/saves/{player}/{game}/blobs/{old_blob.name}" in carried

    # The copied records still name the old save, and the bundle carries it.
    copied = sqlite3.connect(str(bundle / "database" / "ark.db"))
    try:
        row = copied.execute("SELECT sha256, blob FROM player_saves").fetchone()
    finally:
        copied.close()
    assert row[0] == hashlib.sha256(SAVE_ONE).hexdigest()
    assert row[1] == old_blob.name

    problems = tool.bundle_tools.verify_bundle(bundle)[1]
    assert problems == [], problems
