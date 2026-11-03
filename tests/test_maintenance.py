"""The maintenance protocol — proved with real processes, not stand-ins.

A lock is only worth what it stops.  So these tests do not ask a function whether
it *would* lock: they hold the gate from another process, kill that process, and
run the real restore and the real repair against the result.  Four promises are
under test, and each one is a way a box loses rows if it is wrong:

* **a reader that keeps its handle blocks a replacement** — including a handle the
  reader forgot to close, which is why the hold is bound to the connection;
* **maintenance stops new access at the door** — a bounded wait, then a plain
  "busy", never a page hanging for ever;
* **two repairs take turns** — their intervals do not overlap, so neither reads a
  good copy from either side of the other's replacement;
* **a killed owner releases its lock** — the kernel does it, and the lock file is
  never deleted, because a lock file that can be removed is not a lock.
"""

from __future__ import annotations

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

import ark_maintenance
import ark_self_heal as heal


# ---------------------------------------------------------------------------
#  a box, built by hand
# ---------------------------------------------------------------------------
def make_box(tmp_path: Path) -> Path:
    root = tmp_path / "srv-ark"
    for sub in ("logs", "api/data", "drop/files", "drop/tmp", "kiwix", "backup",
                "essentials"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root


def make_database(path: Path, rows: int = 4) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT NOT NULL);")
    for n in range(rows):
        conn.execute("INSERT INTO notes (body) VALUES (?)", ("note %d" % n,))
    conn.commit()
    conn.close()


def bodies(path: Path) -> list:
    conn = sqlite3.connect(str(path))
    try:
        return [row[0] for row in conn.execute("SELECT body FROM notes ORDER BY id")]
    finally:
        conn.close()


def a_box_with_a_good_copy(tmp_path: Path):
    """A sound database and the copy a repair would put back."""
    root = make_box(tmp_path)
    db = root / "api/data/ark.db"
    make_database(db)
    shutil.copyfile(str(db), str(db) + ".good.bak")
    return root, db


def box_env(root: Path, **extra) -> dict:
    env = {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": str(root.parent),
        "PYTHONPATH": str(API),
        "ARK_ROOT": str(root),
        "ARK_DB": str(root / "api/data/ark.db"),
        "ARK_MARKER": str(root / "api/data/ark.installed"),
    }
    env.update(extra)
    return env


def spawn(code: str, root: Path, **extra):
    return subprocess.Popen([sys.executable, "-c", code], env=box_env(root, **extra),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def run_child(code: str, root: Path, timeout: float = 20, **extra):
    return subprocess.run([sys.executable, "-c", code], env=box_env(root, **extra),
                          capture_output=True, text=True, timeout=timeout)


def wait_for(path: Path, timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(0.02)
    return False


def stop(proc) -> None:
    if proc.poll() is None:
        proc.kill()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:          # pragma: no cover - a stuck child
        pass


def reader_holding_the_records(db: Path, ready: Path, seconds: float = 30.0) -> str:
    return (
        "import sys, time\n"
        "sys.path.insert(0, %r)\n"
        "import ark_db\n"
        "conn = ark_db.connect()\n"
        "conn.execute('SELECT COUNT(*) FROM notes').fetchone()\n"
        "open(%r, 'w').write('held')\n"
        "time.sleep(%r)\n"
        "conn.close()\n" % (str(API), str(ready), seconds))


def reader_once() -> str:
    return (
        "import sys\n"
        "sys.path.insert(0, %r)\n"
        "import ark_db\n"
        "try:\n"
        "    with ark_db.read() as conn:\n"
        "        conn.execute('SELECT COUNT(*) FROM notes').fetchone()\n"
        "except Exception as exc:\n"
        "    print('busy' if exc.__class__.__name__ == 'Busy' else 'error ' + repr(exc))\n"
        "else:\n"
        "    print('ok')\n" % (str(API),))


# ---------------------------------------------------------------------------
#  a hold is a hold — and it ends with the connection
# ---------------------------------------------------------------------------
def test_a_connection_is_the_shared_hold(tmp_path):
    """A shared ticket blocks the exclusive gate; giving it back lets it through."""
    _root, db = a_box_with_a_good_copy(tmp_path)
    ticket = ark_maintenance.admit(db)
    try:
        with ark_maintenance.maintain(db, wait=0.2) as lease:
            assert lease.held is False
            assert "another program" in lease.reason
    finally:
        ticket.release()
    with ark_maintenance.maintain(db, wait=1.0) as lease:
        assert lease.held is True, lease.reason


def test_an_open_reader_blocks_a_repair_and_a_kill_releases_it(tmp_path):
    """The finding, end to end: a live handle stops the replacement of its records.

    The reader is a real ``ark_db`` connection in another process — the same object
    every portal request holds for its lifetime.  The damage is written *after* it
    opens, so the reader can exist beside a file that needs repairing; the pass must
    answer ``deferred`` and change nothing.  Killing the process is the crash case:
    the kernel drops the lock, and the very next pass repairs normally.
    """
    root, db = a_box_with_a_good_copy(tmp_path)
    ready = tmp_path / "reader-ready"
    child = spawn(reader_holding_the_records(db, ready), root)
    try:
        assert wait_for(ready), "the reader never opened the records"
        # The damage arrives while the reader is connected — a failing card does not
        # wait for the box to be idle.  The reader still holds the file open.
        db.write_bytes(b"this main file is not a database" * 200)
        was = db.read_bytes()
        state, said = heal.heal_sqlite(db, gate_wait=0.3)
        assert state == "deferred", said
        assert "another program still has the records open" in said
        assert "nothing was changed" in said
        assert "not damage" in said
        assert db.read_bytes() == was, "a replacement happened under a live reader"
    finally:
        stop(child)

    assert db.read_bytes() == was, "the refusal itself must not have written"
    state, said = heal.heal_sqlite(db, gate_wait=2.0)
    assert state == "restored", said
    assert bodies(db) == ["note %d" % n for n in range(4)]
    assert ark_maintenance.gate_path(db).exists(), (
        "the lock file is a name, not the lock: deleting it would let two processes "
        "hold two different files")


def test_an_active_writer_blocks_a_repair(tmp_path):
    """A write transaction in another process is "the box is writing to it".

    The repair asks SQLite's own write lock as well as the gate, because a program
    that is not ours (or an older build) may hold one without taking the gate.  The
    answer is ``busy`` and nothing is replaced; the file is not called corrupt just
    because somebody is committing to it.
    """
    root, db = a_box_with_a_good_copy(tmp_path)
    ready = tmp_path / "writer-ready"
    code = (
        "import sys, time\n"
        "sys.path.insert(0, %r)\n"
        "import ark_db\n"
        "conn = ark_db.connect()\n"
        "conn.execute('BEGIN IMMEDIATE')\n"
        "conn.execute('UPDATE notes SET body = body WHERE id = 1')\n"
        "open(%r, 'w').write('held')\n"
        "time.sleep(30)\n"
        "conn.execute('ROLLBACK')\n"
        "conn.close()\n" % (str(API), str(ready)))
    child = spawn(code, root)
    try:
        assert wait_for(ready), "the writer never began its transaction"
        db.write_bytes(b"this main file is not a database" * 200)
        was = db.read_bytes()
        state, said = heal.heal_sqlite(db, gate_wait=0.3)
        # Which lock answers first depends on the moment the damage is written: the
        # gate (the writer's handle) or SQLite's own write lock.  Both are refusals;
        # neither is allowed to replace anything.
        assert state in ("busy", "deferred"), said
        assert "nothing was changed" in said
        assert db.read_bytes() == was
    finally:
        stop(child)

    state, said = heal.heal_sqlite(db, gate_wait=2.0)
    assert state == "restored", said
    assert bodies(db) == ["note %d" % n for n in range(4)]


def test_maintenance_stops_new_access_at_the_door(tmp_path):
    """While the gate is held, a new request is told the box is busy — quickly.

    The wait is bounded on purpose: a page may be slower during a repair, but it may
    not hang.  ``ARK_GATE_ADMIT_WAIT`` makes the bound small enough to prove here and
    is the same knob a very slow card could turn.
    """
    root, db = a_box_with_a_good_copy(tmp_path)
    assert run_child(reader_once(), root).stdout.strip() == "ok"

    with ark_maintenance.maintain(db, wait=1.0) as lease:
        assert lease.held, lease.reason
        started = time.monotonic()
        done = run_child(reader_once(), root, ARK_GATE_ADMIT_WAIT="0.2")
        elapsed = time.monotonic() - started

    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "busy", done.stdout
    assert elapsed < 5.0, "a refused door must not hang the caller"

    assert run_child(reader_once(), root).stdout.strip() == "ok"


# ---------------------------------------------------------------------------
#  two repairs take turns
# ---------------------------------------------------------------------------
def test_two_repairs_take_turns_across_processes(tmp_path):
    """Measured intervals: the second repair starts after the first one ends.

    Two passes can meet — a person running it by hand while the timer fires, the boot
    pass a minute before the daily one.  Two put-backs that overlap read their good
    copies from either side of each other; the turnstile is what makes the whole
    decision-and-replacement one step.
    """
    root, db = a_box_with_a_good_copy(tmp_path)
    log = tmp_path / "turns.log"
    code = (
        "import sys, time\n"
        "sys.path.insert(0, %r)\n"
        "import ark_maintenance as m\n"
        "with m.repairing(%r, wait=8.0) as turn:\n"
        "    if not turn.held:\n"
        "        print('busy'); raise SystemExit(2)\n"
        "    with open(%r, 'a') as fh:\n"
        "        fh.write('start %%.4f\\n' %% time.time()); fh.flush()\n"
        "    time.sleep(0.5)\n"
        "    with open(%r, 'a') as fh:\n"
        "        fh.write('end %%.4f\\n' %% time.time()); fh.flush()\n"
        % (str(API), str(db), str(log), str(log)))

    first = spawn(code, root)
    assert wait_for(log, timeout=10.0)
    second = spawn(code, root)
    try:
        assert first.wait(timeout=15) == 0
        assert second.wait(timeout=15) == 0
    finally:
        stop(first)
        stop(second)

    events = [line.split() for line in log.read_text().splitlines() if line.strip()]
    assert [kind for kind, _ in events] == ["start", "end", "start", "end"], events
    first_end = float(events[1][1])
    second_start = float(events[2][1])
    assert second_start >= first_end, "two repairs overlapped: %r" % (events,)


def test_a_finished_repair_leaves_the_turnstile_free(tmp_path):
    _root, db = a_box_with_a_good_copy(tmp_path)
    with ark_maintenance.repairing(db, wait=1.0) as turn:
        assert turn.held
    with ark_maintenance.repairing(db, wait=1.0) as again:
        assert again.held is True, "a finished repair must not lock the database for ever"


# ---------------------------------------------------------------------------
#  a crash is not a permanent lock
# ---------------------------------------------------------------------------
def test_a_killed_owner_releases_the_gate_and_the_file_stays(tmp_path):
    root, db = a_box_with_a_good_copy(tmp_path)
    ready = tmp_path / "maintenance-ready"
    code = (
        "import sys, time\n"
        "sys.path.insert(0, %r)\n"
        "import ark_maintenance as m\n"
        "with m.maintain(%r, wait=2.0) as lease:\n"
        "    if not lease.held:\n"
        "        raise SystemExit('could not hold the gate')\n"
        "    open(%r, 'w').write('held')\n"
        "    time.sleep(30)\n" % (str(API), str(db), str(ready)))
    child = spawn(code, root)
    try:
        assert wait_for(ready)
        with ark_maintenance.maintain(db, wait=0.2) as lease:
            assert lease.held is False
    finally:
        stop(child)

    with ark_maintenance.maintain(db, wait=1.0) as lease:
        assert lease.held is True, "the kernel did not release a killed owner's lock"
    gate = ark_maintenance.gate_path(db)
    assert gate.exists(), "the lock file is never deleted; only the kernel lock matters"
    assert (gate.stat().st_mode & 0o777) == 0o666, (
        "root and pi both use it; a root-only gate would lock every page out")


def test_the_gate_is_never_swept(tmp_path):
    """A sweep may not match the gate or the turnstile, however old they are."""
    root, db = a_box_with_a_good_copy(tmp_path)
    ticket = ark_maintenance.admit(db)
    ticket.release()
    old = time.time() - 90 * 24 * 3600
    for path in (ark_maintenance.gate_path(db), ark_maintenance.lock_path(db)):
        path.write_bytes(b"")
        os.utime(str(path), (old, old))

    heal.sweep(root)

    assert ark_maintenance.gate_path(db).exists()
    assert ark_maintenance.lock_path(db).exists()


def test_the_gate_beside_one_database_does_not_hold_up_another(tmp_path):
    root, db = a_box_with_a_good_copy(tmp_path)
    other = root / "api/data/other.db"
    make_database(other)
    with ark_maintenance.maintain(other, wait=0.0) as lease:
        assert lease.held
        with ark_maintenance.maintain(db, wait=0.2) as mine:
            assert mine.held is True


# ---------------------------------------------------------------------------
#  the periodic pass defers; it does not call a busy box corrupt
# ---------------------------------------------------------------------------
def test_a_periodic_pass_defers_when_a_reader_holds_the_records(tmp_path):
    root, db = a_box_with_a_good_copy(tmp_path)
    ready = tmp_path / "reader-ready"
    child = spawn(reader_holding_the_records(db, ready), root)
    try:
        assert wait_for(ready)
        db.write_bytes(b"this main file is not a database" * 200)
        was = db.read_bytes()
        report = heal.run(root, current=time.time() + 1, setter=lambda stamp: True)
    finally:
        stop(child)

    rows = [row for row in report["databases"] if row["file"].endswith("ark.db")]
    assert [row["state"] for row in rows] == ["deferred"], report["databases"]
    assert db.read_bytes() == was
    assert any("another program still has the records open" in said
               for said in report["problems"]), report["problems"]
    assert not any("good copy" in said for said in report["problems"]), (
        "a deferred repair must not claim to have replaced anything")

    after = heal.run(root, current=time.time() + 1, setter=lambda stamp: True)
    rows = [row for row in after["databases"] if row["file"].endswith("ark.db")]
    assert [row["state"] for row in rows] == ["restored"], after["databases"]
    assert after["problems"] == [], after["problems"]
    assert bodies(db) == ["note %d" % n for n in range(4)]


def test_a_busy_gate_is_reported_as_an_ordinary_refusal(tmp_path):
    """``deferred`` is its own state: not ``damaged``, not ``locked``, not ``busy``."""
    _root, db = a_box_with_a_good_copy(tmp_path)
    db.write_bytes(b"broken")
    holding = ark_maintenance.admit(db)
    try:
        state, said = heal.heal_sqlite(db, gate_wait=0.2)
    finally:
        holding.release()
    assert state == "deferred", said
    assert "not damage" in said
    assert db.read_bytes() == b"broken"


# ---------------------------------------------------------------------------
#  the protocol ships with every module that needs it
# ---------------------------------------------------------------------------
def test_the_installer_copies_the_protocol_with_every_module_that_imports_it():
    """A module the installer never copies is a service that will not start.

    ``ark_db`` (every portal request) and ``ark_self_heal`` (the boot repair) import
    it, and ``ark-restore``/``ark-backup`` load it from the API tree, so all three
    phases must ship it.
    """
    for phase in ("15-resilience.sh", "60-portal.sh", "95-backup.sh"):
        text = (REPO / "pi" / "steps" / phase).read_text(encoding="utf-8")
        assert "ark_maintenance.py" in text, f"{phase} never installs the protocol"


# ---------------------------------------------------------------------------
#  a page during the window says so plainly
# ---------------------------------------------------------------------------
def test_a_page_during_maintenance_is_a_503_with_a_sentence(ark):
    """Not a traceback, not a half-read page: the door says it is shut for a moment."""
    import main
    from fastapi.testclient import TestClient

    with ark_maintenance.maintain(ark.db.DB_PATH, wait=1.0) as lease:
        assert lease.held, lease.reason
        with TestClient(main.app) as client:
            response = client.get("/board")
            ready = client.get("/api/ready")

    assert response.status_code == 503
    assert "putting its records back" in response.text
    # Readiness answers its own verdict rather than the page sentence; either way
    # the box says "not ready" instead of 500, which is the truthful answer while
    # its records are being changed.
    assert ready.status_code == 503
    assert ready.json()["ok"] is False
