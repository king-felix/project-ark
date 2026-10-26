"""Power-cut resilience — what the box does about itself when the power comes back.

The interesting cases are all of the "it looks fine but it isn't" family, and each
of them is a way to lose records if it is handled the wrong way round:

* a file from the **future** is not corruption, it is proof the clock is wrong;
* a database that SQLite refuses to open **read-only** after a cut is healthy, and
  calling it broken would put an older copy over real rows (the ``-shm`` case);
* a broken file with **no** copy is set aside and reported, never deleted;
* a database with no good copy is left exactly where it is — emptying a box is the
  owner's decision, not a repair's.

Nothing here needs a Pi, a service or the real card: every test builds its own box
in ``tmp_path`` and points ``ARK_ROOT`` at it.
"""

from __future__ import annotations

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
TOOL = REPO / "pi" / "bin" / "ark-self-heal"
PHASE = REPO / "pi" / "steps" / "15-resilience.sh"
SERVICE = REPO / "pi" / "systemd" / "ark-self-heal.service"
TIMER = REPO / "pi" / "systemd" / "ark-self-heal.timer"
INSTALLER = REPO / "pi" / "install-ark.sh"
VERIFY = REPO / "pi" / "steps" / "90-verify.sh"

import ark_atomic                                   # noqa: E402
import ark_self_heal as heal                        # noqa: E402


# ---------------------------------------------------------------------------
#  building a box by hand
# ---------------------------------------------------------------------------
def make_box(tmp_path: Path) -> Path:
    root = tmp_path / "srv-ark"
    for sub in ("logs", "api/data", "drop/files", "drop/tmp", "arcade/tmp",
                "kiwix", "backup", "essentials"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root


def make_database(path: Path, rows: int = 1) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT NOT NULL);")
    for n in range(rows):
        conn.execute("INSERT INTO notes (body) VALUES (?)", ("note %d" % n,))
    conn.commit()
    conn.close()


def touch(path: Path, *, age_hours: float = 0.0, size: int = 4) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    when = time.time() - age_hours * 3600
    os.utime(str(path), (when, when))
    return path


# ---------------------------------------------------------------------------
#  the clock
# ---------------------------------------------------------------------------
def test_a_file_from_the_future_means_the_clock_is_behind():
    assert heal.clock_is_behind(1_000.0, 1_000.0 + 3600) is True


def test_a_file_written_a_moment_ago_is_not_evidence():
    # Clocks drift and files are written continuously; two minutes is the slack.
    assert heal.clock_is_behind(1_000.0, 1_000.0 + 60) is False
    assert heal.clock_is_behind(1_000.0, 0.0) is False


def test_the_clock_is_put_forward_to_the_newest_thing_on_the_card(tmp_path):
    root = make_box(tmp_path)
    later = time.time() + 7200
    os.utime(str(touch(root / "drop/files/book.epub")), (later, later))
    asked = []

    said = heal.fix_clock(root, current=time.time(),
                          setter=lambda stamp: asked.append(stamp) or True)

    assert asked == [pytest.approx(later, abs=1)], "the newest file is the evidence"
    assert "put forward" in said
    assert "book.epub" in said, "the sentence says what the time was judged by"


def test_a_clock_that_cannot_be_set_says_so_and_still_returns_a_sentence(tmp_path):
    root = make_box(tmp_path)
    later = time.time() + 7200
    os.utime(str(touch(root / "kiwix/library.xml")), (later, later))

    said = heal.fix_clock(root, current=time.time(), setter=lambda stamp: False)

    assert "could not set it" in said


def test_a_dry_run_never_sets_the_clock(tmp_path):
    root = make_box(tmp_path)
    later = time.time() + 7200
    os.utime(str(touch(root / "drop/files/book.epub")), (later, later))
    asked = []

    said = heal.fix_clock(root, current=time.time(), dry_run=True,
                          setter=lambda stamp: asked.append(stamp) or True)

    assert asked == []
    assert "would be put forward" in said


def test_the_clock_is_remembered_for_the_next_cold_boot(tmp_path, monkeypatch):
    root = make_box(tmp_path)
    later = time.time() + 7200
    os.utime(str(touch(root / "drop/files/book.epub")), (later, later))
    kept = []
    monkeypatch.setattr(ark_atomic, "save_clock", lambda: kept.append(1) or True)

    said = heal.fix_clock(root, current=time.time(), setter=lambda stamp: True)

    assert kept == [1], "a clock that is not remembered is wrong again next boot"
    assert "remembered" in said


def test_a_clock_that_agrees_with_the_card_says_nothing_alarming(tmp_path):
    root = make_box(tmp_path)
    touch(root / "drop/files/book.epub")

    said = heal.fix_clock(root, current=time.time(), setter=lambda stamp: False)

    assert said == "The clock agrees with the card."



# ---------------------------------------------------------------------------
#  a file that came back unreadable
# ---------------------------------------------------------------------------
def test_a_broken_file_is_set_aside_and_the_copy_from_before_put_back(tmp_path):
    root = make_box(tmp_path)
    good = {"ok": True, "parts": []}
    target = root / "ark_status.json"
    target.write_text(json.dumps(good), encoding="utf-8")
    target.with_name("ark_status.json.bak").write_text(json.dumps(good), encoding="utf-8")
    target.write_text('{"ok": tru', encoding="utf-8")         # a cut in the middle

    state, said = heal.heal_json(target)

    assert state == "restored"
    assert json.loads(target.read_text(encoding="utf-8")) == good
    assert "set aside" in said and ".corrupt-" in said
    assert list(root.glob("ark_status.json.corrupt-*")), "the broken bytes are kept"


def test_a_broken_file_with_no_copy_is_set_aside_never_deleted(tmp_path):
    root = make_box(tmp_path)
    target = root / "ark_status.json"
    target.write_text("not json at all", encoding="utf-8")

    state, said = heal.heal_json(target)

    assert state == "quarantined"
    assert not target.exists(), "the unreadable file is moved, not left to be read"
    assert "nothing was put back" in said
    kept = list(root.glob("ark_status.json.corrupt-*"))
    assert len(kept) == 1 and kept[0].read_bytes() == b"not json at all"


def test_a_file_that_is_simply_not_there_is_not_a_problem(tmp_path):
    root = make_box(tmp_path)

    assert heal.heal_json(root / "ark_status.json") == ("absent", "")


def test_a_file_that_vanished_is_put_back_from_its_copy(tmp_path):
    root = make_box(tmp_path)
    target = root / "ark_status.json"
    target.with_name("ark_status.json.bak").write_text('{"ok": true}', encoding="utf-8")

    state, said = heal.heal_json(target)

    assert state == "restored"
    assert json.loads(target.read_text(encoding="utf-8")) == {"ok": True}
    assert "was gone" in said


# ---------------------------------------------------------------------------
#  the network watchdog's counters — key=value, and never read as JSON
# ---------------------------------------------------------------------------
def test_healthy_counters_are_left_alone(tmp_path):
    """The counters are not JSON.  Reading them as JSON is how a healthy box gets
    its watchdog counters thrown away on every boot."""
    root = make_box(tmp_path)
    target = root / "logs/net-watch.state"
    target.write_text("lost_streak=1\nhome_streak=0\nhome_block_until=0\n"
                      "updated=2026-10-25T00:18:00+01:00\n", encoding="utf-8")

    assert heal.heal_state(root) == ("fine", "")
    assert target.exists(), "a healthy file is never moved"

    #  ...and it is not in the JSON list, which is what the defect was.
    assert "logs/net-watch.state" not in heal.JSON_TARGETS


def test_damaged_counters_are_set_aside_and_the_watchdog_starts_again(tmp_path):
    root = make_box(tmp_path)
    target = root / "logs/net-watch.state"
    target.write_text("lost_streak=1\nhome_st", encoding="utf-8")

    state, said = heal.heal_state(root)

    assert state == "set aside"
    assert "starts counting again" in said
    assert not target.exists(), "the half-written file is moved out of the way"
    assert list(root.glob("logs/net-watch.state.corrupt-*")), "the bytes are kept"


def test_counters_that_are_not_numbers_are_damage(tmp_path):
    assert "not a number" in heal.state_problem(
        "lost_streak=x\nhome_streak=0\nhome_block_until=0\n")
    assert "missing" in heal.state_problem("lost_streak=0\n")
    assert heal.state_problem("") != ""


def test_missing_counters_are_not_a_fault(tmp_path):
    root = make_box(tmp_path)

    assert heal.heal_state(root) == ("absent", ""), "no decision has been made yet"


def test_only_the_newest_three_broken_copies_are_kept(tmp_path):
    root = make_box(tmp_path)
    target = root / "ark_status.json"
    for n in range(5):
        old = target.with_name("ark_status.json.corrupt-2026010%d-000000" % n)
        old.write_text("{}", encoding="utf-8")
    target.write_text("broken", encoding="utf-8")

    heal.heal_json(target)

    kept = sorted(p.name for p in root.glob("ark_status.json.corrupt-*"))
    assert len(kept) == heal.CORRUPT_KEEP, kept
    assert kept[-1] > "ark_status.json.corrupt-20260104", "the newest is the one kept"


def test_a_dry_run_moves_nothing(tmp_path):
    root = make_box(tmp_path)
    target = root / "ark_status.json"
    target.with_name("ark_status.json.bak").write_text('{"ok": true}', encoding="utf-8")
    target.write_text("broken", encoding="utf-8")

    state, said = heal.heal_json(target, dry_run=True)

    assert state == "would"
    assert target.read_text(encoding="utf-8") == "broken"
    assert "would be put back" in said


# ---------------------------------------------------------------------------
#  a database
# ---------------------------------------------------------------------------
def test_a_sound_database_gets_a_good_copy_and_wal(tmp_path):
    root = make_box(tmp_path)
    db = root / "api/data/ark.db"
    make_database(db, rows=3)

    state, said = heal.heal_sqlite(db)

    assert state == "fine"
    assert (root / "api/data/ark.db.good.bak").is_file()
    assert "a good copy" in said
    conn = sqlite3.connect(str(db))
    assert conn.execute("PRAGMA journal_mode;").fetchone()[0] == "wal"
    conn.close()


def test_a_database_that_cannot_be_read_is_put_back_from_its_good_copy(tmp_path):
    root = make_box(tmp_path)
    db = root / "api/data/ark.db"
    make_database(db, rows=2)
    heal.heal_sqlite(db)                                   # takes the good copy
    db.write_bytes(b"this is not a database" * 500)

    state, said = heal.heal_sqlite(db)

    assert state == "restored"
    assert "good copy" in said
    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT COUNT(*) FROM notes;").fetchone()[0] == 2
    conn.close()


def test_a_broken_database_with_no_copy_is_left_exactly_where_it_is(tmp_path):
    root = make_box(tmp_path)
    db = root / "api/data/ark.db"
    db.write_bytes(b"ruined" * 100)

    state, said = heal.heal_sqlite(db)

    assert state == "left"
    assert db.read_bytes() == b"ruined" * 100, "emptying a box is the owner's decision"
    assert "left where it is" in said


def test_a_good_copy_that_is_itself_broken_is_never_put_back(tmp_path):
    root = make_box(tmp_path)
    db = root / "api/data/ark.db"
    db.write_bytes(b"broken")
    (root / "api/data/ark.db.good.bak").write_bytes(b"broken too")

    state, said = heal.heal_sqlite(db)

    assert state == "left"
    assert db.read_bytes() == b"broken"


def test_a_database_whose_shared_memory_is_gone_after_a_cut_is_healthy(tmp_path):
    """The ``-shm`` case: a read-only check would throw real rows away.

    A cut leaves the ``-wal`` holding committed pages and the ``-shm`` gone, and
    SQLite refuses a *read-only* open of that pair.  Calling it corrupt would put an
    older copy over rows the box had already committed.

    The card is copied as a cut leaves it — the database and its ``-wal``, no
    ``-shm``, and no process holding either open — because a cut has no live
    connection left behind.
    """
    root = make_box(tmp_path)
    db = root / "api/data/ark.db"
    make_database(db)
    conn = sqlite3.connect(str(db))
    conn.execute("INSERT INTO notes (body) VALUES ('written just before the cut');")
    conn.commit()
    assert sqlite3.connect(str(db)).execute("PRAGMA journal_mode;").fetchone()[0] == "wal"
    assert (root / "api/data/ark.db-wal").stat().st_size > 0, "the commit is only in the WAL"
    cut = root / "api/data/after-the-cut.db"
    shutil.copy2(str(db), str(cut))
    shutil.copy2(str(db) + "-wal", str(cut) + "-wal")
    conn.close()
    assert not (root / "api/data/after-the-cut.db-shm").exists()

    state, said = heal.heal_sqlite(cut)

    assert state == "fine", said
    conn = sqlite3.connect(str(cut))
    assert conn.execute("SELECT COUNT(*) FROM notes;").fetchone()[0] == 2
    conn.close()


def test_a_database_is_not_copied_into_a_wal_missing_its_commits(tmp_path):
    """The good copy is taken with SQLite's own ``backup()``, never a plain copy."""
    root = make_box(tmp_path)
    db = root / "api/data/ark.db"
    make_database(db)
    conn = sqlite3.connect(str(db))
    conn.execute("INSERT INTO notes (body) VALUES ('newest row');")
    conn.commit()                                   # committed, still only in the WAL
    heal.heal_sqlite(db)                            # snapshot of the live database
    conn.close()

    copy = sqlite3.connect(str(root / "api/data/ark.db.good.bak"))
    bodies = [row[0] for row in copy.execute("SELECT body FROM notes;")]
    copy.close()
    assert "newest row" in bodies, "a plain cp would have missed this row"


# ---------------------------------------------------------------------------
#  half-written things
# ---------------------------------------------------------------------------
def test_old_temp_files_are_swept_and_fresh_ones_are_left(tmp_path):
    root = make_box(tmp_path)
    old = touch(root / "drop/tmp/upload-0123456789abcdef.part", age_hours=24)
    fresh = touch(root / "drop/tmp/upload-fedcba9876543210.part", age_hours=1)
    settled = touch(root / "drop/files/0123456789abcdef-a-book.epub", age_hours=24)

    gone = heal.sweep(root)

    assert gone == ["drop/tmp/upload-0123456789abcdef.part"]
    assert not old.exists() and fresh.exists()
    assert settled.exists(), "a published file is never swept, however old"


def test_a_half_built_copy_is_swept_but_a_finished_one_is_not(tmp_path):
    root = make_box(tmp_path)
    partial = root / "backup/ark-backup-20260101-000000.partial"
    partial.mkdir()
    (partial / "manifest.json").write_text("{}", encoding="utf-8")
    when = time.time() - 48 * 3600
    os.utime(str(partial), (when, when))
    finished = root / "backup/ark-backup-20251231-000000"
    finished.mkdir()
    os.utime(str(finished), (when, when))

    gone = heal.sweep(root)

    assert gone == ["backup/ark-backup-20260101-000000.partial"]
    assert not partial.exists() and finished.is_dir()


def test_the_database_journal_is_never_swept(tmp_path):
    root = make_box(tmp_path)
    for name in ("ark.db-wal", "ark.db-shm", "ark.installed"):
        touch(root / "api/data" / name, age_hours=100)

    assert heal.sweep(root) == []


def test_a_dry_run_sweeps_nothing(tmp_path):
    root = make_box(tmp_path)
    old = touch(root / "drop/tmp/upload-0123456789abcdef.part", age_hours=24)

    assert heal.sweep(root, dry_run=True) == ["drop/tmp/upload-0123456789abcdef.part"]
    assert old.exists()


def test_a_half_written_counter_file_is_swept_and_the_real_one_is_not(tmp_path):
    """The watchdog writes its counters by rename now, so a cut leaves a dot-temp
    file rather than half a counter file — and that temp file must be cleared."""
    root = make_box(tmp_path)
    stale = touch(root / "logs/.net-watch.state.tmp", age_hours=12)
    real = root / "logs/net-watch.state"
    real.write_text("lost_streak=0\nhome_streak=0\nhome_block_until=0\n",
                    encoding="utf-8")
    old = time.time() - 12 * 3600
    os.utime(str(real), (old, old))

    gone = heal.sweep(root)

    assert gone == ["logs/.net-watch.state.tmp"]
    assert not stale.exists() and real.is_file(), "the counters are never swept"


# ---------------------------------------------------------------------------
#  the library catalogue
# ---------------------------------------------------------------------------
BUILDER = """#!/usr/bin/env python3
import os, pathlib, sys
folder = pathlib.Path(os.environ["ZIM_DIR"])
books = "".join('<book id="%s" path="%s"/>' % (z.name, z) for z in folder.glob("*.zim"))
(folder / "library.xml").write_text("<library>%s</library>" % books, encoding="utf-8")
sys.exit(0)
"""


def make_builder(tmp_path: Path) -> Path:
    script = tmp_path / "fake-kiwix-library"
    script.write_text(BUILDER, encoding="utf-8")
    script.chmod(0o755)
    return script


def test_a_catalogue_that_points_at_missing_files_is_rebuilt(tmp_path):
    root = make_box(tmp_path)
    (root / "kiwix/book.zim").write_bytes(b"zim")
    (root / "kiwix/library.xml").write_text(
        '<library><book id="gone" path="/srv/ark/srv/ark/kiwix/gone.zim"/></library>',
        encoding="utf-8")

    state, said = heal.heal_library(root, builder=str(make_builder(tmp_path)))

    assert state == "rebuilt", said
    assert "book.zim" in (root / "kiwix/library.xml").read_text(encoding="utf-8")
    assert "point at a file that is not there" in said


def test_a_catalogue_beside_its_bundles_is_left_alone(tmp_path):
    root = make_box(tmp_path)
    (root / "kiwix/book.zim").write_bytes(b"zim")
    (root / "kiwix/library.xml").write_text(
        '<library><book id="book" path="book.zim"/></library>', encoding="utf-8")
    script = make_builder(tmp_path)

    assert heal.heal_library(root, builder=str(script)) == ("fine", "")
    # And the builder really would have changed it, so "fine" is a real answer.
    assert "book" in (root / "kiwix/library.xml").read_text(encoding="utf-8")


def test_without_a_builder_the_catalogue_is_reported_never_guessed(tmp_path):
    root = make_box(tmp_path)
    (root / "kiwix/book.zim").write_bytes(b"zim")

    state, said = heal.heal_library(root, builder=str(tmp_path / "not-installed"))

    assert state == "stale"
    assert "could not be built" in said
    assert not (root / "kiwix/library.xml").exists(), "nothing was invented"


def test_no_bundles_means_no_catalogue_question(tmp_path):
    root = make_box(tmp_path)

    assert heal.heal_library(root) == ("absent", "")


def test_a_catalogue_that_cannot_be_rebuilt_is_reported_and_not_broken(tmp_path):
    """A failing builder must answer, not raise: the pass carries on regardless."""
    root = make_box(tmp_path)
    (root / "kiwix/book.zim").write_bytes(b"zim")
    broken = tmp_path / "bad-builder"
    broken.write_text("#!/usr/bin/env bash\necho 'no cataloguing today' >&2\nexit 3\n",
                      encoding="utf-8")
    broken.chmod(0o755)

    state, said = heal.heal_library(root, builder=str(broken))

    assert state == "stale"
    assert "could not be built" in said and "no cataloguing today" in said
    assert not (root / "kiwix/library.xml").exists()


# ---------------------------------------------------------------------------
#  the whole pass
# ---------------------------------------------------------------------------
def test_a_pass_repairs_everything_and_answers_a_report(tmp_path):
    root = make_box(tmp_path)
    # a small file that came back unreadable, with the copy from before it
    target = root / "ark_status.json"
    target.with_name("ark_status.json.bak").write_text('{"ok": true}', encoding="utf-8")
    target.write_text("{{{", encoding="utf-8")
    # the watchdog's counters, half-written by the cut
    counters = root / "logs/net-watch.state"
    counters.write_text("lost_streak=1\nhom", encoding="utf-8")
    # a database with a good copy beside it
    database = root / "api/data/ark.db"
    make_database(database, rows=3)
    make_database(Path(str(database) + ".good.bak"), rows=3)
    database.write_bytes(b"not a database at all")
    # something half-written from the cut
    leftover = touch(root / "drop/tmp/upload-0123456789abcdef.part", age_hours=24)

    report = heal.run(root, current=time.time() + 1, setter=lambda stamp: True)

    assert report["ok"] is True, report["problems"]
    assert report["healed"] >= 4
    assert json.loads(target.read_text(encoding="utf-8")) == {"ok": True}
    assert report["state"]["state"] == "set aside"
    assert not counters.exists(), "the counters are moved, never half-read"
    conn = sqlite3.connect(str(database))
    try:
        assert conn.execute("SELECT COUNT(*) FROM notes;").fetchone()[0] == 3
    finally:
        conn.close()
    assert not leftover.exists()
    assert report["swept"] == ["drop/tmp/upload-0123456789abcdef.part"]
    assert [item["state"] for item in report["json"]] == ["restored"]
    assert [item["state"] for item in report["databases"]][0] == "restored"


def test_a_pass_that_can_do_nothing_says_so_rather_than_guessing(tmp_path):
    root = make_box(tmp_path)
    database = root / "api/data/ark.db"
    database.write_bytes(b"not a database at all")
    report = heal.run(root, current=time.time() + 1, setter=lambda stamp: True)

    assert report["ok"] is False
    assert database.exists(), "a database with no good copy is never removed"
    assert any("no good copy" in problem for problem in report["problems"])
    assert heal.summary(report), "a problem is always a sentence"


def test_a_dry_run_changes_nothing_at_all(tmp_path):
    root = make_box(tmp_path)
    target = root / "ark_status.json"
    target.write_text("{{{", encoding="utf-8")
    leftover = touch(root / "drop/tmp/upload-0123456789abcdef.part", age_hours=24)
    before = sorted(str(p.relative_to(root)) for p in root.rglob("*"))

    report = heal.run(root, current=time.time() + 1, dry_run=True,
                      setter=lambda stamp: True)

    assert sorted(str(p.relative_to(root)) for p in root.rglob("*")) == before
    assert leftover.exists() and target.read_text(encoding="utf-8") == "{{{"
    assert report["dry_run"] is True
    assert report["swept"] == ["drop/tmp/upload-0123456789abcdef.part"]
    assert [item["state"] for item in report["json"]] == ["would"]


def test_the_sweep_is_judged_by_the_clock_after_it_is_fixed(tmp_path, monkeypatch):
    """A clock in the past makes a leftover look like it came from the future."""
    behind = 1_600_000_000.0                       # 2020: what a cut leaves behind
    later = behind + 12 * 3600                     # the card says it is 12 h later
    root = make_box(tmp_path)
    os.utime(str(touch(root / "drop/files/book.epub")), (later, later))
    leftover = touch(root / "drop/tmp/upload-0123456789abcdef.part")
    os.utime(str(leftover), (behind + 2 * 3600, behind + 2 * 3600))
    clock = [behind]
    monkeypatch.setattr(heal, "now", lambda: clock[0])

    def setter(stamp):                              # the clock really does move
        clock[0] = stamp
        return True

    report = heal.run(root, setter=setter)

    # Ten hours old by the corrected clock (the rule is six), and *minus* ten by the
    # clock the box woke up with — so this only passes if the sweep asks again.
    assert "put forward" in report["clock"]
    assert report["swept"] == ["drop/tmp/upload-0123456789abcdef.part"]
    assert not leftover.exists()


def test_a_report_reads_as_sentences_for_a_person():
    report = {"clock": "The clock agrees with the card.",
              "json": [{"said": "ark_status.json was broken; its copy from before "
                                "it was put back."}],
              "databases": [], "library": {"said": ""},
              "swept": ["drop/tmp/a.part"], "problems": ["something else"]}
    lines = heal.summary(report)

    assert lines[0] == "The clock agrees with the card."
    assert "copy from before" in lines[1]
    assert lines[2].startswith("1 half-written thing cleared away")
    assert lines[-1] == "Still to look at: something else"
    assert heal.summary({}) == [], "an empty report says nothing"


# ---------------------------------------------------------------------------
#  the command line
# ---------------------------------------------------------------------------
def run_tool(root: Path, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, ARK_ROOT=str(root), ARK_API_DIR=str(REPO / "pi" / "api"))
    return subprocess.run([sys.executable, str(TOOL), "--root", str(root)] + list(args),
                          capture_output=True, text=True, env=env, timeout=120)


def test_the_command_writes_a_report_a_verifier_can_read(tmp_path):
    root = make_box(tmp_path)
    done = run_tool(root, "--json")

    assert done.returncode == 0, done.stderr
    report = json.loads(done.stdout)
    assert report["clock"] and report["dry_run"] is False
    saved = json.loads((root / "logs/self-heal.json").read_text(encoding="utf-8"))
    assert saved["when"] == report["when"], "the file and the console agree"


def test_the_command_repairs_a_broken_file_and_says_what_it_did(tmp_path):
    root = make_box(tmp_path)
    target = root / "ark_status.json"
    target.with_name("ark_status.json.bak").write_text('{"ready": true}',
                                                       encoding="utf-8")
    target.write_text("{{{", encoding="utf-8")

    done = run_tool(root)

    assert done.returncode == 0, done.stderr
    assert "ark_status.json" in done.stdout and "copy from before" in done.stdout
    assert json.loads(target.read_text(encoding="utf-8")) == {"ready": True}
    logged = (root / "logs/self-heal.log").read_text(encoding="utf-8")
    assert "ark_status.json" in logged, "what happened is written down"


def test_the_command_exits_one_when_something_is_left_to_look_at(tmp_path):
    root = make_box(tmp_path)
    (root / "api/data/ark.db").write_bytes(b"not a database at all")

    done = run_tool(root, "--quiet")

    assert done.returncode == 1
    assert "Still to look at:" in done.stderr
    assert done.stdout.strip() == "", "--quiet with nothing repaired prints nothing"
    assert (root / "logs/self-heal.json").is_file(), "the report is still written"


def test_a_dry_run_at_the_command_leaves_no_trace(tmp_path):
    root = make_box(tmp_path)
    # A copy from before it, so the dry run can say what it *would* put back.
    (root / "ark_status.json.bak").write_text('{"ready": true}', encoding="utf-8")
    (root / "ark_status.json").write_text("{{{\n", encoding="utf-8")

    done = run_tool(root, "--dry-run")

    assert done.returncode == 0, done.stderr
    assert "would be put back" in done.stdout
    assert not (root / "logs/self-heal.json").exists(), "a dry run writes no report"
    assert not (root / "logs/self-heal.log").exists(), "and no log line either"
    assert (root / "ark_status.json").read_text(encoding="utf-8") == "{{{\n"
    assert (root / "ark_status.json.bak").is_file(), "nothing was moved or replaced"


def test_a_quiet_pass_on_a_healthy_box_says_nothing(tmp_path):
    root = make_box(tmp_path)

    done = run_tool(root, "--quiet")

    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == ""
    assert done.stderr.strip() == ""


# ---------------------------------------------------------------------------
#  what the phase installs, and what the boot order does with it
# ---------------------------------------------------------------------------
def test_the_phase_and_its_units_are_where_the_installer_looks_for_them():
    for path in (TOOL, PHASE, SERVICE, TIMER):
        assert path.is_file(), "%s is missing" % path
    assert os.access(str(TOOL), os.X_OK), "the command has to be executable"
    assert os.access(str(PHASE), os.X_OK), "the phase has to be executable"


def test_the_units_say_what_the_box_needs_them_to_say():
    service = SERVICE.read_text(encoding="utf-8")
    timer = TIMER.read_text(encoding="utf-8")

    assert "Type=oneshot" in service
    assert "ExecStart=/usr/local/sbin/ark-self-heal" in service
    assert "User=root" in service, "setting the clock and fake-hwclock both need root"
    assert "Before=ark-api.service" in service, "the clock is fixed before a page reads it"
    assert "TimeoutStartSec=" in service, "a slow card must not hang the boot for ever"
    assert "Restart=" not in service, "a oneshot repair is not restarted"

    assert "OnCalendar=" in timer and "Persistent=true" in timer
    assert "Unit=ark-self-heal.service" in timer
    assert "WantedBy=timers.target" in timer


def test_the_phase_enables_both_units_and_proves_itself():
    text = PHASE.read_text(encoding="utf-8")

    assert "systemctl enable ark-self-heal.service" in text, \
        "an installed oneshot never runs at boot; `enable` is what makes it run"
    assert "systemctl enable ark-self-heal.timer" in text
    assert "ark-self-heal --dry-run" in text, "the phase proves itself before it ends"
    assert "systemctl start ark-self-heal.service" in text


def test_the_phase_is_installed_before_anything_that_serves_a_page():
    text = INSTALLER.read_text(encoding="utf-8")
    order = text.split("ALL_STEPS=(", 1)[1].split(")", 1)[0].split()
    assert "15-resilience" in order
    assert order.index("10-base") < order.index("15-resilience")
    assert order.index("15-resilience") < order.index("60-portal")


def test_the_phase_sets_the_things_that_make_a_cut_cheap():
    text = PHASE.read_text(encoding="utf-8")

    for needle in ("fake-hwclock",
                   "vm.dirty_writeback_centisecs = 1500",
                   "vm.swappiness = 10",
                   "kernel.panic = 10",
                   "Storage=volatile",
                   "RuntimeWatchdogSec=15s",
                   "fsck.mode=force", "fsck.repair=yes",
                   "Restart=always", "StartLimitIntervalSec=0",
                   "journal_mode=WAL", "synchronous=FULL"):
        assert needle in text, "%s is missing from the phase" % needle
    assert "kernel.panic=10" in text.replace(" ", ""), \
        "a panic in the boot arguments is what reboots a wedged box"


def test_the_phase_can_never_leave_the_box_unbootable():
    """Two lines in cmdline.txt is a Pi that does not boot; one guard, at least."""
    text = PHASE.read_text(encoding="utf-8")

    assert "ark-before-resilience" in text, "the boot arguments are copied aside first"
    assert 'wc -l < "$CMDLINE"' in text, "the file is proved to still be one line"
    assert "ark_die" in text.split("wc -l < \"$CMDLINE\"", 1)[1][:200]
    assert "fstab.ark-review" in text, "a tmpfs line that does not verify is taken back out"


def test_the_verifier_keeps_the_receipt_from_before(tmp_path):
    """A receipt that is being rewritten when the lights go out has to leave a copy.

    ``ark-self-heal`` puts a damaged JSON file back from ``<name>.bak`` or sets it
    aside and says so; without the copy the second half of that sentence is all the
    box can ever do.  The verifier is the only writer of this file.
    """
    text = VERIFY.read_text(encoding="utf-8")

    assert '"$STATUS.bak"' in text, "the previous receipt is kept before the new one"
    kept = text.index('"$STATUS.bak"')
    written = text.index('install -m 644 "$tmp" "$STATUS"')
    assert kept < written, "the copy is taken before the file is replaced"


def test_the_verifier_reads_the_record_the_boot_pass_leaves_behind():
    """The verifier is the gate, so it has to say what the last pass found.

    A record that is absent is a normal state (a box that has not rebooted since
    the phase), and the verifier says so rather than failing the box.  A pass that
    left something for a person to look at is the one thing that is a real fault.
    """
    text = VERIFY.read_text(encoding="utf-8")

    assert "ark-self-heal.timer" in text, "the daily look is part of the gate"
    assert "logs/self-heal.json" in text, "the record is where the boot pass writes it"
    assert "no pass recorded yet" in text, "an absent record is reported, never failed"
    assert 'say_bad "self-heal"' in text, "a pass that could not repair something is a fault"
    assert '"self_heal":' in text, "the Mac reads the receipt, not the console"


def test_no_shell_file_ends_a_command_with_a_doubled_quote():
    """The defect that broke 90-verify: 'x="$(cmd)""' closes nothing."""
    for path in (PHASE, TOOL, VERIFY):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            assert not line.rstrip().endswith(')""'), "%s:%d" % (path.name, number)



# ---------------------------------------------------------------------------
#  two passes, two jobs: prove nothing, and never repair under a writer
# ---------------------------------------------------------------------------
def make_plain_database(path: Path) -> None:
    """A database in the default journal, so a journal-mode change is visible."""
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT NOT NULL);")
    conn.execute("INSERT INTO notes (body) VALUES ('one');")
    conn.commit()
    conn.close()


def test_a_dry_run_writes_nothing_at_all(tmp_path):
    """``--dry-run`` is the phase's own proof, so it may not change the card.

    It used to.  A sound database was *hardened* (switched to WAL) and copied before
    the dry-run flag was ever read, so ``15-resilience.sh`` proving itself wrote to
    every database on the box — a proof that alters the thing it is proving.
    """
    root = make_box(tmp_path)
    db = root / "api/data/ark.db"
    make_plain_database(db)
    before = sorted(str(p.relative_to(root)) for p in root.rglob("*"))

    state, said = heal.heal_sqlite(db, dry_run=True)

    assert (state, said) == ("fine", "")
    assert not (root / "api/data/ark.db.good.bak").exists(), "a dry run keeps no copy"
    assert sorted(str(p.relative_to(root)) for p in root.rglob("*")) == before
    conn = sqlite3.connect(str(db))
    assert conn.execute("PRAGMA journal_mode;").fetchone()[0] == "delete"
    conn.close()


def test_a_dry_run_on_a_broken_database_reports_and_restores_nothing(tmp_path):
    root = make_box(tmp_path)
    db = root / "api/data/ark.db"
    make_database(db)
    make_database(Path(str(db) + ".good.bak"))
    db.write_bytes(b"broken")

    state, said = heal.heal_sqlite(db, dry_run=True)

    assert state == "would", said
    assert db.read_bytes() == b"broken"
    assert "good copy" in said


def test_a_real_writer_stops_the_pass_touching_its_database(tmp_path):
    """The daily look runs while the box serves pages, so it must not repair.

    The test is the lock, not a process list: ``BEGIN IMMEDIATE`` is what every writer
    on this box takes, and a second one is refused at once — which is exactly the
    state ``ark-api`` is in for the few milliseconds a save or a note is committed.
    """
    root = make_box(tmp_path)
    db = root / "api/data/ark.db"
    make_database(db)
    holder = sqlite3.connect(str(db), isolation_level=None)
    holder.execute("BEGIN IMMEDIATE;")
    try:
        assert heal.writer_busy(db) is True
        assert heal.snapshot(db) is False, "no copy is taken of a moving target"
        state, said = heal.heal_sqlite(db)
        assert state == "in use", said
        assert "only checked" in said
        assert not (root / "api/data/ark.db.good.bak").exists()
    finally:
        holder.execute("ROLLBACK;")
        holder.close()
    assert heal.writer_busy(db) is False, "an idle database is not 'in use'"


def test_a_broken_database_the_box_is_writing_to_is_left_where_it_is(tmp_path):
    root = make_box(tmp_path)
    db = root / "api/data/ark.db"
    make_database(db)
    make_database(Path(str(db) + ".good.bak"))
    db.write_bytes(b"broken")

    state, said = heal.heal_sqlite(db, in_use=True)

    assert state == "busy", said
    assert "nothing was changed" in said
    assert db.read_bytes() == b"broken", "a copy is never put back under a live writer"


def test_the_pass_reports_a_database_it_could_not_repair_because_it_was_in_use(tmp_path):
    root = make_box(tmp_path)
    db = root / "api/data/ark.db"
    make_database(db)
    db.write_bytes(b"broken")

    report = heal.run(root, current=time.time() + 1, setter=lambda stamp: True,
                      in_use=True)

    assert report["ok"] is False
    assert any("writing to it" in said for said in report["problems"]), report["problems"]
    assert db.read_bytes() == b"broken"
    assert any("Still to look at" in line for line in heal.summary(report))


# ---------------------------------------------------------------------------
#  a clock, and evidence that must not be followed
# ---------------------------------------------------------------------------
def test_a_file_from_the_far_future_is_not_trusted_with_the_clock(tmp_path):
    """A stamp years ahead is not evidence of the time: it is a wrong clock talking.

    Following it would put the certificate and every age on every page years out, and
    only a person can set the date on this box — so the pass leaves the clock alone and
    says so, in the one list a human reads.
    """
    root = make_box(tmp_path)
    somewhere = touch(root / "drop/files/book.epub")
    remote = time.time() + heal.CLOCK_MAX_AHEAD + 86400
    os.utime(str(somewhere), (remote, remote))
    set_calls = []

    said = heal.fix_clock(root, setter=lambda stamp: set_calls.append(stamp) or True)

    assert set_calls == [], "the clock must not follow a stamp years in the future"
    assert "left exactly as it is" in said
    assert "a person has to set the date" in said

    report = heal.run(root, current=time.time(), setter=lambda stamp: set_calls.append(stamp))
    assert set_calls == []
    assert any("a person has to set the date" in text for text in report["problems"])
    assert report["ok"] is False
    assert sum("Still to look at" in line for line in heal.summary(report)) == 1


# ---------------------------------------------------------------------------
#  one boot checks the card; the boot after that must not pay for it again
# ---------------------------------------------------------------------------
FSCK_TOOL = REPO / "pi" / "bin" / "ark-fsck-once"
FSCK_UNIT = REPO / "pi" / "systemd" / "ark-fsck-once.service"

#  What `15-resilience.sh` reads on a Raspberry Pi, one line, as the firmware writes it.
BOOT_LINE = ("console=serial0,115200 console=tty1 root=PARTUUID=6c586e13-02 "
             "rootfstype=ext4 fsck.repair=yes fsck.mode=force "
             "rootwait quiet splash plymouth.ignore-serial-consoles panic=10")


def run_fsck_once(root: Path, *args: str, cmdline: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ, ARK_ROOT=str(root))
    return subprocess.run([str(FSCK_TOOL), "--cmdline", str(cmdline)] + list(args),
                          capture_output=True, text=True, env=env, timeout=60)


def make_cmdline(tmp_path: Path, line: str = BOOT_LINE) -> Path:
    file = tmp_path / "cmdline.txt"
    file.write_text(line + "\n", encoding="utf-8")
    return file


def test_the_forced_check_is_withdrawn_from_the_boot_line_once(tmp_path):
    """The first boot after a cut checks the card; every later boot must not.

    ``fsck.mode=force`` left on the line makes every reboot wait for a full check of a
    card that was already checked — minutes of a box that looks dead, for ever.  So the
    boot that used the check takes the two options back off, and everything else on the
    line is proved to be there afterwards.
    """
    root = make_box(tmp_path)
    cmdline = make_cmdline(tmp_path)

    result = run_fsck_once(root, cmdline=cmdline)

    assert result.returncode == 0, result.stderr
    after = cmdline.read_text(encoding="utf-8")
    assert after.endswith("\n") and after.count("\n") == 1, "cmdline.txt must stay ONE line"
    words_after = after.split()
    assert words_after == [w for w in BOOT_LINE.split()
                           if w not in ("fsck.mode=force", "fsck.repair=yes")], \
        "nothing but the two options may change"
    assert set(BOOT_LINE.split()) - set(words_after) == {"fsck.mode=force", "fsck.repair=yes"}
    # The old line is kept, so a person can put the check back by hand.
    assert (tmp_path / "cmdline.txt.ark-with-check").read_text(encoding="utf-8").strip() \
        == BOOT_LINE
    log = (root / "logs" / "fsck-once.log").read_text(encoding="utf-8")
    assert "withdrew" in log


def test_a_later_boot_has_nothing_to_withdraw_and_changes_nothing(tmp_path):
    """The unit is enabled for ever, so most boots it must be a no-op."""
    root = make_box(tmp_path)
    cmdline = make_cmdline(tmp_path)
    assert run_fsck_once(root, cmdline=cmdline).returncode == 0
    settled = cmdline.read_bytes()

    for _ in range(2):
        result = run_fsck_once(root, cmdline=cmdline)
        assert result.returncode == 0, result.stderr
        assert "nothing to withdraw" in result.stdout
        assert cmdline.read_bytes() == settled


def test_a_cmdline_that_is_already_two_lines_is_left_alone(tmp_path):
    """A newline in cmdline.txt stops the Pi dead, and there is no console to see it."""
    root = make_box(tmp_path)
    cmdline = tmp_path / "cmdline.txt"
    cmdline.write_text("root=/dev/mmcblk0p2 fsck.mode=force\nconsole=tty1\n", encoding="utf-8")
    before = cmdline.read_bytes()

    result = run_fsck_once(root, cmdline=cmdline)

    assert result.returncode == 1
    assert "more than one line" in result.stderr
    assert cmdline.read_bytes() == before, "a refused run writes nothing at all"


def test_a_line_that_would_lose_root_is_never_written(tmp_path):
    """A boot that lost ``root=`` is a box that cannot come back."""
    root = make_box(tmp_path)
    cmdline = make_cmdline(tmp_path, "console=tty1 fsck.mode=force quiet")
    before = cmdline.read_bytes()

    result = run_fsck_once(root, cmdline=cmdline)

    assert result.returncode == 1
    assert "root=" in result.stderr
    assert cmdline.read_bytes() == before


def test_a_dry_run_withdraws_nothing(tmp_path):
    """Phase 15 proves the tool on the real line; a proof may not change the box."""
    root = make_box(tmp_path)
    cmdline = make_cmdline(tmp_path)
    before = cmdline.read_bytes()

    result = run_fsck_once(root, "--dry-run", cmdline=cmdline)

    assert result.returncode == 0, result.stderr
    assert "Would withdraw" in result.stdout
    assert cmdline.read_bytes() == before
    assert not (tmp_path / "cmdline.txt.ark-with-check").exists(), "a dry run keeps no copy"


def test_the_unit_runs_the_withdrawal_on_the_boot_that_used_the_check():
    unit = FSCK_UNIT.read_text(encoding="utf-8")
    assert "ExecStart=/usr/local/sbin/ark-fsck-once --quiet" in unit
    assert "Before=ark-self-heal.service" in unit, "repair runs after the check, not before"
    assert "After=local-fs.target" in unit, "the check has to have happened first"
    assert "Type=oneshot" in unit
    assert "WantedBy=multi-user.target" in unit
    assert os.access(str(FSCK_TOOL), os.X_OK), "the tool has to be executable"


def test_the_phase_installs_the_withdrawal_and_does_not_run_it_now():
    """Enabled, never started: starting it would cancel the check it is arranging."""
    text = PHASE.read_text(encoding="utf-8")
    assert "bin/ark-fsck-once\" /usr/local/sbin/ark-fsck-once" in text
    assert "ark_install_unit ark-fsck-once.service" in text
    assert "systemctl enable ark-fsck-once.service" in text
    assert "enable --now ark-fsck-once" not in text
    assert "systemctl start ark-fsck-once" not in text
    assert "ark-fsck-once --cmdline \"$CMDLINE\" --dry-run" in text, \
        "the phase proves the tool on the real line, changing nothing"
    for line in text.splitlines():
        if "ark-fsck-once --cmdline" in line:
            assert "--dry-run" in line, \
                "the phase must not withdraw the check it has just arranged: %s" % line.strip()



# ---------------------------------------------------------------------------
#  the card check, as the gate reports it
# ---------------------------------------------------------------------------
def test_the_verifier_reports_the_forced_check_without_failing_a_box_that_waiting():
    """A box that has not rebooted since the phase is *waiting*, not broken.

    The check belongs to one boot, so "the line still carries it" is a fact about
    the calendar rather than a fault — and the verifier is the release gate, so a
    soft line here and a hard line there are different verdicts.  What may never
    happen is the verifier staying quiet: the receipt the Mac reads has to carry
    the answer too, or nobody learns that every reboot will check the whole card.
    """
    text = VERIFY.read_text(encoding="utf-8")

    assert "card_check=" in text, "the gate records the answer it printed"
    assert "ark-fsck-once.service" in text, "the tool that withdraws it is what it asks about"
    assert "fsck.mode=force" in text, "the line itself is the evidence"
    assert 'say_soft "card check"' in text, "waiting is not a failure"
    assert 'say_bad "card check"' not in text, "a box that has not rebooted is not broken"
    assert '"card_check":' in text, "the Mac reads the receipt, not the console"
    for word in ("withdrawn", "waiting", "absent"):
        assert word in text, "a state nobody can name is a state nobody can read: %s" % word

