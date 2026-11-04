"""The crash-safe replacement sequence, proved with real WAL rows and real crashes.

The finding was concrete: the old put-back removed the ``-wal`` before writing
its replacement, so an injected write failure left the original main file present
and its committed journal gone.  These tests build that exact shape — a main file
with four rows and a journal holding forty-seven more — and then:

* replace it successfully and prove the journal's rows are in the preserved set;
* fail at staging, sync, sidecar movement, the install rename, and post-install
  verification, and prove the target either keeps or gets back a coherent set;
* kill a real subprocess at each recorded boundary and run the next startup;
* prove an interrupted replacement leaves a ``BLOCK`` marker that stops
  ``ark_db`` from opening the file;
* prove insufficient space and a read-only card are refused before the original
  is touched;
* prove the retention policy keeps the newest evidence and never deletes a
  pending folder.
"""

from __future__ import annotations

import errno
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
API = REPO / "pi" / "api"

import ark_db_replace as replacement
import ark_self_heal as heal


# ---------------------------------------------------------------------------
#  a card shaped the way a cut leaves it
# ---------------------------------------------------------------------------
def make_wal_crash(target: Path, *, rows: int = 4, extra: int = 47) -> None:
    """A main file with ``rows`` and a journal holding ``extra`` more.

    The writer is still open when the files are copied, which is the only way to
    catch committed frames before the last close checkpoints them.  After the
    copy, the target is never opened before the replacement under test.
    """
    source = target.parent / ("source-" + target.name)
    conn = sqlite3.connect(str(source))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT NOT NULL)")
    for n in range(rows):
        conn.execute("INSERT INTO notes (body) VALUES (?)", ("note %d" % n,))
    conn.commit()
    conn.executemany("INSERT INTO notes (body) VALUES (?)",
                     [("wal %d" % n,) for n in range(extra)])
    conn.commit()
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(str(source), str(target))
    shutil.copyfile(str(source) + "-wal", str(target) + "-wal")
    conn.close()
    assert Path(str(target) + "-wal").stat().st_size > 0


def make_database(path: Path, body: str = "candidate") -> None:
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT NOT NULL)")
    conn.execute("INSERT INTO notes (body) VALUES (?)", (body,))
    conn.commit()
    conn.close()


def bodies(path: Path) -> list:
    conn = sqlite3.connect(str(path))
    try:
        return [row[0] for row in conn.execute("SELECT body FROM notes ORDER BY id")]
    finally:
        conn.close()


def bodies_without_touching(path: Path) -> list:
    """Read a main file and its sidecars without opening the live paths.

    Opening the target would let SQLite checkpoint its journal, which changes the
    very bytes a crash test is trying to inspect.
    """
    folder = path.parent / ("read-" + path.name)
    folder.mkdir(exist_ok=True)
    copy = folder / path.name
    shutil.copyfile(str(path), str(copy))
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(path) + suffix)
        if sidecar.exists():
            shutil.copyfile(str(sidecar), str(copy) + suffix)
    try:
        return bodies(copy)
    finally:
        shutil.rmtree(str(folder), ignore_errors=True)


def archives(target: Path) -> list:
    return sorted(item for item in target.parent.iterdir()
                  if item.is_dir()
                  and item.name.startswith(target.name + replacement.ARCHIVE_PREFIX))


def archived_state(folder: Path) -> dict:
    return json.loads((folder / replacement.STATE_NAME).read_text(encoding="utf-8"))


def child_env(target: Path) -> dict:
    return {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": str(target.parent),
        "PYTHONPATH": str(API),
        "ARK_ROOT": str(target.parent),
        "ARK_DB": str(target),
        "ARK_MARKER": str(target.parent / "ark.installed"),
    }


def kill_at(point: str, source: Path, target: Path) -> None:
    """Kill a real process at one recorded boundary, leaving the card as it falls."""
    code = (
        "import os, sys\n"
        "sys.path.insert(0, %r)\n"
        "import ark_db_replace as r\n"
        "def hook(p):\n"
        "    if p == %r:\n"
        "        os._exit(97)\n"
        "r.fault_hook = hook\n"
        "r.replace(%r, %r, reason='kill test')\n"
        % (str(API), point, str(source), str(target)))
    done = subprocess.run([sys.executable, "-c", code], env=child_env(target),
                          capture_output=True, text=True, timeout=90)
    assert done.returncode == 97, (done.returncode, done.stdout, done.stderr)


# ---------------------------------------------------------------------------
#  a successful replacement
# ---------------------------------------------------------------------------
def test_a_verified_replace_keeps_the_wal_only_rows_in_the_recovery_set(tmp_path):
    target = tmp_path / "ark.db"
    source = tmp_path / "new.db"
    make_wal_crash(target)
    make_database(source, "the new record")
    assert len(bodies_without_touching(target)) == 51

    result = replacement.replace(source, target, reason="test")

    assert result.status == "ok", result.said
    assert result.safe and result.replaced
    assert not replacement.pending(target)
    assert not Path(str(target) + "-wal").exists(), (
        "the replacement was paired with the old journal")
    assert bodies(target) == ["the new record"]

    evidence = result.evidence
    assert evidence is not None and evidence.is_dir()
    state = archived_state(evidence)
    assert state["phase"] == "committed"
    assert state["original"]["main"]["sha256"]
    assert state["original"]["sidecars"]["-wal"]["moved"] is True
    assert bodies_without_touching(evidence / replacement.ORIGINAL_DIR / target.name) == (
        ["note %d" % n for n in range(4)]
        + ["wal %d" % n for n in range(47)])


# ---------------------------------------------------------------------------
#  crashes at every recorded boundary, resolved by the next startup
# ---------------------------------------------------------------------------
def test_a_kill_before_the_journal_moves_is_set_aside_without_touching_the_records(tmp_path):
    target = tmp_path / "ark.db"
    source = tmp_path / "new.db"
    make_wal_crash(target)
    make_database(source)

    kill_at("original-preserved", source, target)

    assert replacement.active_dir(target).is_dir()
    assert replacement.pending(target) is False
    assert bodies_without_touching(target) == (
        ["note %d" % n for n in range(4)]
        + ["wal %d" % n for n in range(47)])
    recovered = replacement.recover_pending(target)
    assert recovered.status == "abandoned", recovered.said
    assert recovered.pending is False
    assert not replacement.active_dir(target).exists()
    assert bodies_without_touching(target) == (
        ["note %d" % n for n in range(4)]
        + ["wal %d" % n for n in range(47)])


def test_a_kill_after_the_journal_moves_is_rolled_back_on_the_next_startup(tmp_path):
    target = tmp_path / "ark.db"
    source = tmp_path / "new.db"
    make_wal_crash(target)
    make_database(source)

    kill_at("sidecars-moved", source, target)

    assert replacement.pending(target) is True, "the marker must keep services out"
    assert not Path(str(target) + "-wal").exists(), "the journal is in the recovery set"
    recovered = replacement.recover_pending(target)
    assert recovered.status == "rolled-back", recovered.said
    assert recovered.pending is False
    assert bodies(target) == (
        ["note %d" % n for n in range(4)]
        + ["wal %d" % n for n in range(47)])


def test_a_kill_after_the_install_is_finished_on_the_next_startup(tmp_path):
    target = tmp_path / "ark.db"
    source = tmp_path / "new.db"
    make_wal_crash(target)
    make_database(source, "the new record")

    kill_at("after-install", source, target)

    assert replacement.pending(target) is True
    recovered = replacement.recover_pending(target)
    assert recovered.status == "committed", recovered.said
    assert recovered.pending is False
    assert bodies(target) == ["the new record"]
    assert archives(target)[-1].name.endswith("-committed-" + archived_state(
        archives(target)[-1])["id"])


@pytest.mark.parametrize("point", ["after-verify", "committed"])
def test_a_kill_after_verification_or_commit_is_rolled_forward(tmp_path, point):
    target = tmp_path / "ark.db"
    source = tmp_path / "new.db"
    make_wal_crash(target)
    make_database(source, "the new record")

    kill_at(point, source, target)

    recovered = replacement.recover_pending(target)
    assert recovered.status == "committed", recovered.said
    assert recovered.pending is False
    assert bodies(target) == ["the new record"]


def test_a_ctrl_c_interruption_is_rolled_back_before_it_propagates(tmp_path):
    """SIGKILL is covered by the record; Ctrl-C can do better and put it back now."""
    target = tmp_path / "ark.db"
    source = tmp_path / "new.db"
    make_wal_crash(target)
    make_database(source)
    state = {}

    def interrupt(point):
        if point == "sidecars-moved":
            raise KeyboardInterrupt()

    replacement.fault_hook = interrupt
    try:
        with pytest.raises(KeyboardInterrupt):
            replacement.replace(source, target, reason="test", state=state)
    finally:
        replacement.fault_hook = None

    assert state["safe"] is True
    assert state["replaced"] is False
    assert replacement.pending(target) is False
    assert bodies(target) == (
        ["note %d" % n for n in range(4)]
        + ["wal %d" % n for n in range(47)])


def test_a_torn_recovery_record_falls_back_to_the_previous_durable_step(tmp_path):
    target = tmp_path / "ark.db"
    source = tmp_path / "new.db"
    make_wal_crash(target)
    make_database(source)

    kill_at("sidecars-moved", source, target)
    active = replacement.active_dir(target)
    (active / replacement.STATE_NAME).write_text("{ this is not json", encoding="utf-8")

    recovered = replacement.recover_pending(target)
    assert recovered.status == "rolled-back", recovered.said
    assert recovered.pending is False
    assert bodies(target) == (
        ["note %d" % n for n in range(4)]
        + ["wal %d" % n for n in range(47)])


# ---------------------------------------------------------------------------
#  the marker that keeps a service out
# ---------------------------------------------------------------------------
def test_an_empty_recovery_folder_from_an_early_crash_is_set_aside(tmp_path):
    """A crash after the folder was made but before the record must not block a healthy box."""
    target = tmp_path / "ark.db"
    make_wal_crash(target)
    active = replacement.active_dir(target)
    active.mkdir()
    try:
        recovered = replacement.recover_pending(target)
        assert recovered.status == "resolved", recovered.said
        assert recovered.pending is False
        assert not active.exists()
        assert bodies(target) == (
            ["note %d" % n for n in range(4)]
            + ["wal %d" % n for n in range(47)])
    finally:
        shutil.rmtree(str(active), ignore_errors=True)


def test_an_unreadable_record_with_a_moved_journal_puts_it_back(tmp_path):
    """A corrupt record must not make the main file look complete when its WAL is aside."""
    target = tmp_path / "ark.db"
    source = tmp_path / "new.db"
    make_wal_crash(target)
    make_database(source, "the new record")
    kill_at("sidecars-moved", source, target)
    active = replacement.active_dir(target)
    for name in (replacement.STATE_NAME, replacement.STATE_BAK):
        (active / name).write_text("{ not json", encoding="utf-8")

    recovered = replacement.recover_pending(target)

    assert recovered.status == "rolled-back", recovered.said
    assert recovered.pending is False
    assert bodies(target) == (
        ["note %d" % n for n in range(4)]
        + ["wal %d" % n for n in range(47)])


def test_a_committed_record_survives_a_later_write_and_checkpoint(tmp_path):
    """Once verified, later legitimate writes must not make it look like a different file."""
    target = tmp_path / "ark.db"
    source = tmp_path / "new.db"
    make_wal_crash(target)
    make_database(source, "the new record")
    kill_at("committed", source, target)

    conn = sqlite3.connect(str(target))
    try:
        conn.execute("INSERT INTO notes (body) VALUES ('written after commit')")
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(FULL)").fetchone()
    finally:
        conn.close()

    recovered = replacement.recover_pending(target)

    assert recovered.status == "committed", recovered.said
    assert recovered.pending is False
    rows = bodies(target)
    assert rows[0] == "the new record"
    assert "written after commit" in rows


def test_a_blocked_record_without_an_original_does_not_empty_the_target(tmp_path):
    """A blocked state with no saved original must not run a rollback that removes the file."""
    target = tmp_path / "ark.db"
    make_database(target, "still here")
    active = replacement.active_dir(target)
    active.mkdir()
    state = {
        "version": 1, "seq": 2, "id": "blocked-test", "target": str(target),
        "phase": "blocked", "original": {}, "candidate": {},
        "problem": "a previous failure",
    }
    (active / replacement.STATE_NAME).write_text(json.dumps(state), encoding="utf-8")
    replacement._set_block(active)
    try:
        recovered = replacement.recover_pending(target)
        assert recovered.status == "blocked", recovered.said
        assert recovered.pending is True
        assert bodies(target) == ["still here"], "the only readable copy was moved away"
    finally:
        shutil.rmtree(str(active), ignore_errors=True)


def test_a_pending_replacement_stops_ark_db_from_opening_the_file(ark):
    target = ark.db.DB_PATH
    active = replacement.active_dir(target)
    active.mkdir(parents=True, exist_ok=True)
    replacement._set_block(active)
    try:
        with pytest.raises(ark.db.ark_maintenance.Busy) as caught:
            ark.db.connect()
        assert "putting its records back" in str(caught.value)
    finally:
        replacement._clear_block(active)
        shutil.rmtree(str(active), ignore_errors=True)

    conn = ark.db.connect()
    try:
        assert conn.execute("SELECT 1").fetchone()[0] == 1
    finally:
        conn.close()


# ---------------------------------------------------------------------------
#  maintenance comes first
# ---------------------------------------------------------------------------
def test_an_unreadable_recovery_folder_is_not_permission_to_open(tmp_path):
    """The portal runs as ``pi``; the boot repair creates root-owned 0700 folders.

    ``Path.exists()`` would swallow the permission error and answer "no marker".
    The guard must read that as "something is there and I cannot inspect it", not
    as permission to open the records.
    """
    target = tmp_path / "ark.db"
    make_database(target)
    active = replacement.active_dir(target)
    active.mkdir()
    replacement._set_block(active)
    os.chmod(str(active), 0o000)
    try:
        assert replacement.pending(target) is True
    finally:
        os.chmod(str(active), 0o700)
        shutil.rmtree(str(active), ignore_errors=True)


def test_replace_refuses_while_another_process_holds_the_gate(tmp_path):
    target = tmp_path / "ark.db"
    source = tmp_path / "new.db"
    make_wal_crash(target)
    make_database(source)
    ready = tmp_path / "gate-ready"
    code = (
        "import sys, time\n"
        "sys.path.insert(0, %r)\n"
        "import ark_maintenance as m\n"
        "with m.maintain(%r, wait=5.0) as lease:\n"
        "    if not lease.held:\n"
        "        raise SystemExit(2)\n"
        "    open(%r, 'w').write('held')\n"
        "    time.sleep(30)\n"
        % (str(API), str(target), str(ready)))
    child = subprocess.Popen([sys.executable, "-c", code], env=child_env(target),
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True)
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not ready.exists():
            time.sleep(0.02)
        assert ready.exists(), "the gate holder never started"
        was = target.read_bytes()
        result = replacement.replace(source, target, reason="test", gate_wait=0.2)
        assert result.status == "busy", result.said
        assert result.changed is False
        assert target.read_bytes() == was
        assert Path(str(target) + "-wal").exists()
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=10)

    assert replacement.active_dir(target).exists() is False


# ---------------------------------------------------------------------------
#  failures during staging, sync, sidecars, rename and verification
# ---------------------------------------------------------------------------
def test_a_staging_failure_leaves_the_original_and_journal_untouched(tmp_path, monkeypatch):
    target = tmp_path / "ark.db"
    source = tmp_path / "new.db"
    make_wal_crash(target)
    make_database(source)
    was = target.read_bytes()
    real = replacement._copy_file

    def no_room(src, dest, **kwargs):
        if replacement.STAGING_DIR in str(dest):
            raise OSError(errno.ENOSPC, "injected no space")
        return real(src, dest, **kwargs)

    monkeypatch.setattr(replacement, "_copy_file", no_room)
    result = replacement.replace(source, target, reason="test")

    assert result.status == "refused", result.said
    assert "left exactly as they were" in result.said
    assert result.changed is False and result.safe is True
    assert target.read_bytes() == was
    assert Path(str(target) + "-wal").exists()
    assert replacement.pending(target) is False


def test_a_sync_failure_in_staging_is_refused_before_preservation(tmp_path, monkeypatch):
    target = tmp_path / "ark.db"
    source = tmp_path / "new.db"
    make_wal_crash(target)
    make_database(source)
    was = target.read_bytes()
    real = replacement._fsync_file

    def bad_sync(path):
        if replacement.STAGING_DIR in str(path):
            raise OSError(errno.EIO, "injected sync failure")
        return real(path)

    monkeypatch.setattr(replacement, "_fsync_file", bad_sync)
    result = replacement.replace(source, target, reason="test")

    assert result.status == "refused", result.said
    assert result.changed is False and result.safe is True
    assert target.read_bytes() == was
    assert replacement.pending(target) is False


def test_a_sidecar_move_failure_puts_the_original_back(tmp_path, monkeypatch):
    target = tmp_path / "ark.db"
    source = tmp_path / "new.db"
    make_wal_crash(target)
    make_database(source)
    real = replacement._replace_path

    def fail_wal(src, dest):
        if str(src).endswith("-wal"):
            raise OSError(errno.EIO, "injected sidecar failure")
        return real(src, dest)

    monkeypatch.setattr(replacement, "_replace_path", fail_wal)
    result = replacement.replace(source, target, reason="test")

    assert result.status == "refused", result.said
    assert result.changed is True and result.safe is True
    assert "put back" in result.said
    assert bodies(target) == (
        ["note %d" % n for n in range(4)]
        + ["wal %d" % n for n in range(47)])
    assert replacement.pending(target) is False


def test_an_install_rename_failure_puts_the_original_back(tmp_path, monkeypatch):
    target = tmp_path / "ark.db"
    source = tmp_path / "new.db"
    make_wal_crash(target)
    make_database(source)
    real = replacement._replace_path

    def fail_install(src, dest):
        if str(src).endswith(replacement.CANDIDATE_NAME):
            raise OSError(errno.EIO, "injected rename failure")
        return real(src, dest)

    monkeypatch.setattr(replacement, "_replace_path", fail_install)
    result = replacement.replace(source, target, reason="test")

    assert result.status == "refused", result.said
    assert result.changed is True and result.safe is True
    assert bodies(target) == (
        ["note %d" % n for n in range(4)]
        + ["wal %d" % n for n in range(47)])
    assert replacement.pending(target) is False


def test_a_post_install_verification_failure_rolls_back(tmp_path, monkeypatch):
    target = tmp_path / "ark.db"
    source = tmp_path / "new.db"
    make_wal_crash(target)
    make_database(source)

    def bad_verify(_target, _state):
        raise replacement.ReplaceError("injected verification failure")

    monkeypatch.setattr(replacement, "_verify_installed", bad_verify)
    result = replacement.replace(source, target, reason="test")

    assert result.status == "refused", result.said
    assert result.changed is True and result.safe is True
    assert bodies(target) == (
        ["note %d" % n for n in range(4)]
        + ["wal %d" % n for n in range(47)])
    assert replacement.pending(target) is False
    assert any(item.name.endswith("-rolled-back-")
               or "-rolled-back-" in item.name for item in archives(target))


def test_a_failed_first_install_returns_to_a_box_with_no_database(tmp_path, monkeypatch):
    """When there was no original database, "put back" means the name is empty again."""
    source = tmp_path / "new.db"
    target = tmp_path / "ark.db"
    make_database(source)

    def bad_verify(_target, _state):
        raise replacement.ReplaceError("injected verification failure")

    monkeypatch.setattr(replacement, "_verify_installed", bad_verify)
    result = replacement.replace(source, target, reason="test")

    assert result.status == "refused", result.said
    assert result.changed is True and result.safe is True
    assert not target.exists()
    assert replacement.pending(target) is False


def test_an_unresolvable_failure_leaves_a_blocked_marker_and_services_stopped(
        tmp_path, monkeypatch):
    target = tmp_path / "ark.db"
    source = tmp_path / "new.db"
    make_wal_crash(target)
    make_database(source)

    def bad_verify(_target, _state):
        raise replacement.ReplaceError("injected verification failure")

    def bad_rollback(*_args, **_kwargs):
        raise OSError(errno.ENOSPC, "injected rollback failure")

    monkeypatch.setattr(replacement, "_verify_installed", bad_verify)
    monkeypatch.setattr(replacement, "_rollback", bad_rollback)
    result = replacement.replace(source, target, reason="test")

    assert result.status == "blocked", result.said
    assert result.safe is False
    assert replacement.pending(target) is True
    assert "must stay stopped" in result.said

    monkeypatch.undo()
    recovered = replacement.recover_pending(target)
    assert recovered.status == "rolled-back", recovered.said
    assert replacement.pending(target) is False
    assert bodies(target) == (
        ["note %d" % n for n in range(4)]
        + ["wal %d" % n for n in range(47)])


def test_a_candidate_that_is_not_a_database_is_refused_before_preservation(tmp_path):
    target = tmp_path / "ark.db"
    source = tmp_path / "bad.db"
    make_wal_crash(target)
    source.write_bytes(b"this is not a database" * 100)

    result = replacement.replace(source, target, reason="test")

    assert result.status == "refused", result.said
    assert result.changed is False and result.safe is True
    assert result.evidence is not None
    assert not (result.evidence / replacement.ORIGINAL_DIR).exists(), (
        "the original was preserved even though the candidate never validated")
    assert target.read_bytes()  # still there
    assert Path(str(target) + "-wal").exists()
    assert replacement.pending(target) is False


# ---------------------------------------------------------------------------
#  no room, and a card that will not be written
# ---------------------------------------------------------------------------
def test_insufficient_space_is_refused_before_a_recovery_folder_is_made(tmp_path, monkeypatch):
    target = tmp_path / "ark.db"
    source = tmp_path / "new.db"
    make_wal_crash(target)
    make_database(source)
    monkeypatch.setattr(replacement, "_usable_bytes", lambda _path: 1)

    result = replacement.replace(source, target, reason="test")

    assert result.status == "refused", result.said
    assert "not enough room" in result.said
    assert result.changed is False
    assert not replacement.active_dir(target).exists()
    assert Path(str(target) + "-wal").exists()


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0,
                    reason="root ignores a read-only folder")
def test_a_read_only_destination_is_refused_without_touching_the_database(tmp_path):
    target = tmp_path / "ark.db"
    source = tmp_path / "new.db"
    make_wal_crash(target)
    make_database(source)
    was = target.read_bytes()
    os.chmod(str(tmp_path), 0o500)
    try:
        result = replacement.replace(source, target, reason="test")
    finally:
        os.chmod(str(tmp_path), 0o700)

    assert result.status == "refused", result.said
    assert "maintenance lock" in result.said
    assert result.changed is False and result.safe is True
    assert target.read_bytes() == was
    assert Path(str(target) + "-wal").exists()


# ---------------------------------------------------------------------------
#  retention and sweep
# ---------------------------------------------------------------------------
def _fake_archive(folder: Path, target: Path, kind: str, ident: str,
                  payload: int = 16) -> Path:
    folder.mkdir()
    state = {"version": 1, "seq": 1, "id": ident, "target": str(target),
             "phase": "committed" if kind == "committed" else "rolled-back"}
    (folder / replacement.STATE_NAME).write_text(json.dumps(state), encoding="utf-8")
    (folder / "payload.bin").write_bytes(b"x" * payload)
    old = time.time() - len(ident) * 10
    os.utime(str(folder), (old, old))
    return folder


def test_retention_keeps_the_newest_sets_and_never_a_pending_one(tmp_path):
    target = tmp_path / "ark.db"
    make_database(target)
    committed = [
        _fake_archive(tmp_path / (target.name + replacement.ARCHIVE_PREFIX
                                  + "2026010%d-000000-committed-%d" % (n, n)),
                      target, "committed", str(n))
        for n in range(1, 5)
    ]
    too_old = committed[0]
    active = replacement.active_dir(target)
    active.mkdir()
    replacement._set_block(active)

    removed = replacement.prune(target, keep_committed=2, keep_failed=2,
                                max_bytes=10 ** 9)

    assert too_old in removed
    assert too_old.exists() is False
    assert [item for item in committed[1:] if item.exists()] == committed[1:][-2:]
    assert active.is_dir(), "a pending set is never deleted"
    assert replacement.pending(target) is True


def test_retention_keeps_two_non_committed_sets_in_total(tmp_path):
    target = tmp_path / "ark.db"
    make_database(target)
    failed = [
        _fake_archive(tmp_path / (target.name + replacement.ARCHIVE_PREFIX
                                  + "2026010%d-000000-failed-%d" % (n, n)),
                      target, "failed", str(n))
        for n in range(1, 4)
    ]

    removed = replacement.prune(target, keep_committed=2, keep_failed=2,
                                max_bytes=10 ** 9)

    assert failed[0] in removed
    assert [item for item in failed if item.exists()] == failed[1:]


def test_the_retention_byte_ceiling_never_deletes_the_newest_set(tmp_path):
    target = tmp_path / "ark.db"
    make_database(target)
    old = _fake_archive(tmp_path / (target.name + replacement.ARCHIVE_PREFIX
                                    + "20260101-000000-failed-1"),
                        target, "failed", "1", payload=2048)
    new = _fake_archive(tmp_path / (target.name + replacement.ARCHIVE_PREFIX
                                    + "20260102-000000-failed-2"),
                        target, "failed", "2", payload=2048)

    replacement.prune(target, keep_committed=2, keep_failed=2, max_bytes=1024)

    assert new.is_dir(), "the newest evidence is the one that must survive"
    assert old.exists() is False


def test_recovery_evidence_is_never_swept(tmp_path):
    root = tmp_path / "srv-ark"
    (root / "api/data").mkdir(parents=True)
    target = root / "api/data/ark.db"
    make_database(target)
    active = replacement.active_dir(target)
    active.mkdir()
    replacement._set_block(active)
    archive = _fake_archive(root / "api/data" / (target.name + replacement.ARCHIVE_PREFIX
                                                  + "20260101-000000-committed-1"),
                            target, "committed", "1")
    old = time.time() - 90 * 24 * 3600
    for folder in (active, archive):
        os.utime(str(folder), (old, old))

    heal.sweep(root)

    assert active.is_dir() and archive.is_dir()
