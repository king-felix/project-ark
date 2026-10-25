"""Stage 6 — a copy of the box that can be put back, and the page that says how.

Two promises are tested here, and they are different promises:

* a backup is either whole or it is not a backup — it is built under a
  ``.partial`` name, published by rename, and carries a sha256 for every byte
  it claims to hold (so ``--verify`` can be wrong in the safe direction);
* a restore is deliberate — it will not overwrite the records of a box that
  already has some without the owner saying ``--force``, it will never replace a
  file someone already shared, and it will not let the import list pretend that
  books on an empty shelf are already there.

The database is the awkward one: the real box runs SQLite in WAL mode, so a
plain ``cp`` can miss committed rows.  Every test therefore writes its newest row
into the WAL and leaves the connection open — the backup that returns it proves
``.backup`` was used, and a ``cp`` implementation fails the first test.
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BACKUP = REPO / "pi" / "bin" / "ark-backup"
RESTORE = REPO / "pi" / "bin" / "ark-restore"
PHASE = REPO / "pi" / "steps" / "95-backup.sh"
SHEET = REPO / "docs" / "RECOVERY-SHEET.txt"

NOTE = "The well by the east field is working again."

SCHEMA = """
CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
CREATE TABLE notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, name TEXT NOT NULL,
    body TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', pinned INTEGER NOT NULL DEFAULT 0,
    resolved INTEGER NOT NULL DEFAULT 0, hidden INTEGER NOT NULL DEFAULT 0);
CREATE TABLE files (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, name TEXT NOT NULL,
    stored TEXT NOT NULL DEFAULT '', display TEXT NOT NULL DEFAULT '',
    size INTEGER NOT NULL DEFAULT 0, note TEXT NOT NULL DEFAULT '',
    sha256 TEXT NOT NULL DEFAULT '', hidden INTEGER NOT NULL DEFAULT 0);
"""


# ---------------------------------------------------------------------------
#  building a box by hand
# ---------------------------------------------------------------------------
def make_box(root: Path, *, note: str = NOTE, installed: bool = False,
             shelf: bool = False, shared: dict | None = None,
             game_saves: dict | None = None):
    """A box with records (in WAL), shared files and an import list."""
    (root / "api" / "data").mkdir(parents=True, exist_ok=True)
    (root / "drop" / "files").mkdir(parents=True, exist_ok=True)
    (root / "calibre-incoming").mkdir(parents=True, exist_ok=True)
    db = root / "api" / "data" / "ark.db"
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO schema_version (version, applied_at)"
                 " VALUES (2, '2026-10-24T00:00:00Z')")
    conn.execute("INSERT INTO notes (ts, name, body)"
                 " VALUES ('2026-10-24T09:00:00Z', 'Ada', ?)", (note,))
    for name, payload in (shared or {"a1b2c3d4e5f60718-gasket.pdf": b"PDF" * 100}).items():
        (root / "drop" / "files" / name).write_bytes(payload)
        conn.execute("INSERT INTO files (ts, name, stored, display, size, sha256)"
                     " VALUES ('2026-10-24T09:00:00Z', ?, ?, ?, ?, '')",
                     (name, name, name.split("-", 1)[1], len(payload)))
    conn.commit()
    if shelf:
        (root / "calibre-library").mkdir(exist_ok=True)
        (root / "calibre-library" / "metadata.db").write_bytes(
            b"SQLite format 3\x00" + b"x" * 64)
    (root / "calibre-incoming" / ".imported.json").write_text(
        json.dumps({"sha256": {"a" * 64: "2026-10-24T09:00:00Z"}}), encoding="utf-8")
    for rel, payload in (game_saves or {}).items():
        save = root / "arcade" / "saves" / rel
        save.parent.mkdir(parents=True, exist_ok=True)
        save.write_bytes(payload)
    if installed:
        (root / "api" / "data" / "ark.installed").write_text("configured\n", encoding="utf-8")
    return conn          # still open: the newest row lives only in the WAL



# ---------------------------------------------------------------------------
#  running the real tools
# ---------------------------------------------------------------------------
def run(tool: Path, *args, root: Path, extra_env: dict | None = None, timeout: int = 90):
    env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(root.parent),
           "ARK_ROOT": str(root), "ARK_DB": str(root / "api" / "data" / "ark.db"),
           "ARK_MARKER": str(root / "api" / "data" / "ark.installed"),
           "ARK_PI_USER": "nobody-in-this-test"}
    env.update(extra_env or {})
    return subprocess.run([sys.executable, str(tool), *[str(a) for a in args]],
                          capture_output=True, text=True, env=env, timeout=timeout)


def backup(root: Path, to: Path, *args):
    return run(BACKUP, "--to", to, *args, root=root)


def restore(bundle: Path, root: Path, *args):
    return run(RESTORE, "--from", bundle, *args, root=root)


def bundles(folder: Path) -> list[Path]:
    return sorted(p for p in folder.iterdir() if p.is_dir() and p.name.startswith("ark-backup-"))


def manifest_of(bundle: Path) -> dict:
    return json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))


def only_bundle(folder: Path) -> Path:
    found = [p for p in bundles(folder) if not p.name.endswith(".partial")]
    assert len(found) == 1, [p.name for p in bundles(folder)]
    return found[0]


def rows(db: Path, sql: str, params=()):
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()



# ---------------------------------------------------------------------------
#  what a backup is
# ---------------------------------------------------------------------------
def test_a_backup_holds_the_records_the_shared_files_and_the_import_list(tmp_path):
    root, dest = tmp_path / "ark", tmp_path / "backups"
    keep_open = make_box(root, shelf=True)

    done = backup(root, dest)
    assert done.returncode == 0, done.stderr
    bundle = only_bundle(dest)

    assert not list(dest.glob("*.partial")), "a half-written backup was left behind"
    data = manifest_of(bundle)
    assert data["format"] == 1
    assert data["schema_version"] == 2
    assert data["created"].endswith("+00:00") or data["created"].endswith("Z")
    paths = {e["path"] for e in data["entries"]}
    assert "database/ark.db" in paths
    assert "calibre-incoming/.imported.json" in paths
    assert any(p.startswith("files/") for p in paths)
    for entry in data["entries"]:
        assert re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]), entry
        assert entry["size"] == (bundle / entry["path"]).stat().st_size
    assert data["totals"]["files"] == len(data["entries"])
    assert data["totals"]["bytes"] == sum(e["size"] for e in data["entries"])

    # Collections are content, not state: they are fetched again, never copied.
    assert not any(p.endswith(".zim") for p in paths)
    assert "collections" in " ".join(data["excluded"]).lower()

    # The row written into the WAL — and only the WAL — came along.
    assert rows(bundle / "database" / "ark.db", "SELECT body FROM notes")[0][0] == NOTE
    keep_open.close()


def test_a_damaged_copy_is_refused_rather_than_restored(tmp_path):
    root, dest = tmp_path / "ark", tmp_path / "backups"
    make_box(root).close()
    assert backup(root, dest).returncode == 0
    bundle = only_bundle(dest)

    victim = next(p for p in (bundle / "files").iterdir())
    victim.write_bytes(victim.read_bytes() + b"corruption")

    checked = run(BACKUP, "--verify", bundle, root=root)
    assert checked.returncode == 1
    assert "does not match" in checked.stdout + checked.stderr

    target = tmp_path / "fresh"
    (target / "api" / "data").mkdir(parents=True)
    refused = restore(bundle, target, "--dry-run")
    assert refused.returncode == 1
    assert "damaged" in (refused.stdout + refused.stderr).lower()
    assert not (target / "api" / "data" / "ark.db").exists()


def test_a_half_written_backup_is_swept_and_never_listed(tmp_path):
    root, dest = tmp_path / "ark", tmp_path / "backups"
    make_box(root).close()
    dest.mkdir(parents=True)
    left = dest / "ark-backup-20200101-000000.partial"
    left.mkdir()
    (left / "database").mkdir()
    (left / "database" / "ark.db").write_bytes(b"half")

    assert backup(root, dest).returncode == 0
    assert not left.exists(), "a crashed run's leftovers were never cleaned up"

    listing = run(RESTORE, "--list", root=root, extra_env={"ARK_BACKUP_DIR": str(dest)})
    assert listing.returncode == 0
    assert ".partial" not in listing.stdout
    assert only_bundle(dest).name in listing.stdout


def test_only_the_newest_backups_are_kept(tmp_path):
    root, dest = tmp_path / "ark", tmp_path / "backups"
    make_box(root).close()
    made = []
    for _ in range(3):
        assert backup(root, dest, "--keep", "2").returncode == 0
        made = [p.name for p in bundles(dest)]
    assert len(made) == 2, made



# ---------------------------------------------------------------------------
#  what a restore is
# ---------------------------------------------------------------------------
def test_a_restore_puts_everything_back_in_the_same_places(tmp_path):
    root, dest = tmp_path / "ark", tmp_path / "backups"
    make_box(root, shelf=True).close()
    assert backup(root, dest).returncode == 0
    bundle = only_bundle(dest)

    target = tmp_path / "rebuilt"
    for sub in ("api/data", "drop/files", "calibre-incoming", "calibre-library"):
        (target / sub).mkdir(parents=True)
    shutil.copy2(root / "calibre-library" / "metadata.db",
                 target / "calibre-library" / "metadata.db")

    done = restore(bundle, target)
    assert done.returncode == 0, done.stderr
    assert rows(target / "api/data/ark.db", "SELECT body FROM notes")[0][0] == NOTE
    assert (target / "drop/files" / "a1b2c3d4e5f60718-gasket.pdf").read_bytes() == b"PDF" * 100
    assert json.loads((target / "calibre-incoming/.imported.json").read_text())["sha256"]
    assert "put back" in done.stdout.lower()


def test_a_named_players_save_is_backed_up_and_put_back(tmp_path):
    root, dest = tmp_path / "ark", tmp_path / "backups"
    make_box(root, game_saves={"7/3/state-01.state": b"a kept game"}).close()
    assert backup(root, dest).returncode == 0
    bundle = only_bundle(dest)
    data = manifest_of(bundle)
    entry = next(e for e in data["entries"] if e["path"] == "arcade-saves/7/3/state-01.state")
    assert entry["to"] == "arcade/saves/7/3/state-01.state"

    target = tmp_path / "rebuilt"
    (target / "api/data").mkdir(parents=True)
    done = restore(bundle, target)
    assert done.returncode == 0, done.stderr
    put_back = target / "arcade/saves/7/3/state-01.state"
    assert put_back.read_bytes() == b"a kept game"
    assert (put_back.stat().st_mode & 0o777) == 0o600
    assert (put_back.parent.stat().st_mode & 0o777) == 0o700


def test_a_box_that_already_has_records_is_not_overwritten_without_force(tmp_path):
    root, dest = tmp_path / "ark", tmp_path / "backups"
    make_box(root).close()
    assert backup(root, dest).returncode == 0
    bundle = only_bundle(dest)

    occupied = tmp_path / "occupied"
    make_box(occupied, note="Something the owner wrote here", installed=True).close()

    refused = restore(bundle, occupied)
    assert refused.returncode == 1
    assert "--force" in (refused.stdout + refused.stderr)
    assert rows(occupied / "api/data/ark.db", "SELECT body FROM notes")[0][0] != NOTE

    forced = restore(bundle, occupied, "--force")
    assert forced.returncode == 0, forced.stderr
    assert rows(occupied / "api/data/ark.db", "SELECT body FROM notes")[0][0] == NOTE


def test_a_restore_never_replaces_a_file_someone_already_shared(tmp_path):
    root, dest = tmp_path / "ark", tmp_path / "backups"
    name = "a1b2c3d4e5f60718-gasket.pdf"
    make_box(root).close()
    assert backup(root, dest).returncode == 0
    bundle = only_bundle(dest)

    target = tmp_path / "rebuilt"
    (target / "api/data").mkdir(parents=True)
    (target / "drop/files").mkdir(parents=True)
    (target / "drop/files" / name).write_bytes(b"a different file with the same name")

    done = restore(bundle, target)
    assert done.returncode == 0, done.stderr
    assert (target / "drop/files" / name).read_bytes() == b"a different file with the same name"
    assert "kept" in done.stdout.lower()


def test_the_import_list_stays_beside_an_empty_shelf_instead_of_hiding_books(tmp_path):
    root, dest = tmp_path / "ark", tmp_path / "backups"
    make_box(root).close()                       # the shelf is not in the bundle
    assert backup(root, dest).returncode == 0
    bundle = only_bundle(dest)

    target = tmp_path / "rebuilt"
    (target / "api/data").mkdir(parents=True)
    (target / "calibre-incoming").mkdir(parents=True)

    done = restore(bundle, target)
    assert done.returncode == 0, done.stderr
    assert not (target / "calibre-incoming/.imported.json").exists()
    assert (target / "calibre-incoming/.imported.json.from-backup").exists()
    assert "shelf" in done.stdout.lower()


def test_a_manifest_that_points_outside_the_box_is_refused(tmp_path):
    root, dest = tmp_path / "ark", tmp_path / "backups"
    make_box(root).close()
    assert backup(root, dest).returncode == 0
    bundle = only_bundle(dest)

    data = manifest_of(bundle)
    data["entries"][0]["path"] = "../../etc/passwd"
    (bundle / "manifest.json").write_text(json.dumps(data), encoding="utf-8")

    target = tmp_path / "rebuilt"
    (target / "api/data").mkdir(parents=True)
    refused = restore(bundle, target)
    assert refused.returncode == 1
    assert "outside" in (refused.stdout + refused.stderr).lower()


def test_a_dry_run_writes_nothing(tmp_path):
    root, dest = tmp_path / "ark", tmp_path / "backups"
    make_box(root).close()

    planned = backup(root, dest, "--dry-run")
    assert planned.returncode == 0, planned.stderr
    assert not bundles(dest), "a dry run created a backup"

    assert backup(root, dest).returncode == 0
    target = tmp_path / "rebuilt"
    (target / "api/data").mkdir(parents=True)
    assert restore(only_bundle(dest), target, "--dry-run").returncode == 0
    assert not (target / "api/data/ark.db").exists()

# ---------------------------------------------------------------------------
#  the page that works when the box does not
# ---------------------------------------------------------------------------
def test_the_recovery_sheet_is_printable_and_uses_no_words_to_look_up():
    sheet = SHEET.read_text(encoding="utf-8")
    for jargon in ("Kiwix", "ZIM", "Calibre", "OPDS", "FastAPI", "nginx", "systemd",
                   "SQLite", "APK", "localhost", "scp", "rsync"):
        assert jargon not in sheet, jargon
    assert "card" in sheet.lower()
    assert "ark-restore" in sheet
    assert "backup" in sheet.lower()
    assert len(sheet.splitlines()) < 90, "it has to fit on one page"


def test_every_bundle_carries_the_recovery_sheet(tmp_path):
    root, dest = tmp_path / "ark", tmp_path / "backups"
    make_box(root).close()
    assert backup(root, dest).returncode == 0
    text = (only_bundle(dest) / "RESTORE-ME.txt").read_text(encoding="utf-8")
    assert "ark-restore" in text
    for jargon in ("Kiwix", "ZIM", "Calibre", "nginx", "systemd"):
        assert jargon not in text, jargon


# ---------------------------------------------------------------------------
#  the phase that installs it
# ---------------------------------------------------------------------------
def test_the_phase_installs_both_tools_and_proves_a_dry_run():
    done = subprocess.run(["bash", "-n", str(PHASE)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    text = PHASE.read_text(encoding="utf-8")
    assert "ark-backup" in text and "ark-restore" in text
    assert "RECOVERY-SHEET" in text
    assert "--dry-run" in text
    assert "ark-backup --to" in text
