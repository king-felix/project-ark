#!/usr/bin/env python3
"""The parts of a backup that both tools must agree on.

`ark-backup` writes a bundle; `ark-restore` reads one, and neither may guess what
the other meant.  So the layout, the manifest, the path rules and the hash are
defined once, here, and the two commands are thin on top of it.

Three rules decide the design:

* **A bundle is either whole or it is not a bundle.**  It is built under
  ``<name>.partial`` and only becomes ``<name>`` by a rename, so a killed run
  can never be mistaken for a finished backup.
* **The manifest is the claim, the hashes are the proof.**  Every entry carries
  its size and sha256, and verifying means re-reading every byte — a backup you
  have not verified is a hope.
* **Nothing may point outside its own tree.**  Entry paths are relative to the
  bundle, target paths are relative to the box, and both are rejected if they
  climb (``..``), are absolute, or reach through a symlink.

The database is copied with SQLite's own ``backup()`` — never ``cp`` — because
the live file is in WAL mode and the newest rows can still be sitting in the
``-wal`` sidecar.  The copy is switched back to a single self-contained file, so
a bundle can be read on any machine without its sidecars.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

FORMAT = 1
CHUNK = 1024 * 1024
STAMP = re.compile(r"^ark-backup-\d{8}-\d{6}(-\d+)?$")

# What a bundle deliberately does not carry, said out loud in every manifest.
EXCLUDED = [
    "collections (the offline library) — content that is fetched again, not state",
    "the book shelf — content that is rebuilt or imported again",
    "the games themselves — fetched again by the shelf keeper; a named player's saves are included",
    "the programming environment — recreated by the installer",
    "Wi-Fi and other secrets in /etc/ark — set up again by the rebuild guide",
]


# ---------------------------------------------------------------------------
#  where things are (read at call time, never frozen at import)
# ---------------------------------------------------------------------------
def box_paths() -> dict:
    root = Path(os.environ.get("ARK_ROOT") or "/srv/ark")
    return {
        "root": root,
        "db": Path(os.environ.get("ARK_DB") or root / "api" / "data" / "ark.db"),
        "marker": Path(os.environ.get("ARK_MARKER") or root / "api" / "data" / "ark.installed"),
        "files": root / "drop" / "files",
        "arcade_saves": root / "arcade" / "saves",
        "incoming": root / "calibre-incoming",
        "ledger": root / "calibre-incoming" / ".imported.json",
        "shelf": root / "calibre-library" / "metadata.db",
        "backups": Path(os.environ.get("ARK_BACKUP_DIR") or root / "backup"),
    }


# ---------------------------------------------------------------------------
#  small honest helpers
# ---------------------------------------------------------------------------
def human_size(size) -> str:
    if size is None:
        return "not measured"
    step = float(size)
    for unit in ("B", "kB", "MB", "GB", "TB"):
        if step < 1000 or unit == "TB":
            return (f"{int(step)} {unit}" if unit == "B" else f"{step:.1f} {unit}")
        step /= 1000
    return f"{step:.1f} TB"


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_rel(value) -> str | None:
    """A relative path that cannot leave its own tree, or ``None``."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.replace("\\", "/").strip()
    if text.startswith("/") or re.match(r"^[A-Za-z]:", text):
        return None
    parts = [p for p in text.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        return None
    return "/".join(parts) or None


def inside(base: Path, rel: str) -> Path | None:
    """``base/rel`` when the result really is inside ``base`` (symlinks included)."""
    base = base.resolve()
    target = base / rel
    try:
        resolved = target.resolve()
    except OSError:
        return None
    if resolved == base or base not in resolved.parents:
        return None
    return target


# ---------------------------------------------------------------------------
#  writing
# ---------------------------------------------------------------------------
def _copy_hashing(source: Path, target: Path) -> tuple:
    target.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    size = 0
    with open(source, "rb") as reader, open(target, "wb") as writer:
        for block in iter(lambda: reader.read(CHUNK), b""):
            digest.update(block)
            writer.write(block)
            size += len(block)
        writer.flush()
        os.fsync(writer.fileno())
    return digest.hexdigest(), size


def copy_database(source: Path, target: Path) -> None:
    """SQLite's own copy: page-consistent, including rows still in the WAL."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    source_conn = sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=10)
    try:
        dest_conn = sqlite3.connect(str(tmp))
        try:
            source_conn.backup(dest_conn)
            # One file, no sidecars: a bundle must be readable on its own.
            dest_conn.execute("PRAGMA journal_mode=DELETE")
            dest_conn.commit()
        finally:
            dest_conn.close()
    finally:
        source_conn.close()
    with open(tmp, "rb+") as handle:
        os.fsync(handle.fileno())
    os.replace(tmp, target)


def file_entry(bundle: Path, source: Path, rel: str, to: str) -> dict:
    """Copy one file in and describe it.  ``to`` is where it belongs on a box."""
    digest, size = _copy_hashing(source, bundle / rel)
    return {"path": rel, "to": to, "size": size, "sha256": digest,
            "mode": oct(source.stat().st_mode & 0o777)[2:].rjust(4, "0")}


def database_entry(bundle: Path, rel: str, to: str, source: Path) -> dict:
    copy_database(source, bundle / rel)
    payload = bundle / rel
    return {"path": rel, "to": to, "size": payload.stat().st_size,
            "sha256": sha256_file(payload), "mode": "0640"}


def write_text(path: Path, text: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(path, mode)


def write_manifest(bundle: Path, data: dict) -> None:
    """The manifest goes last, and arrives by rename: it is the finishing line."""
    payload = json.dumps(data, indent=2, sort_keys=True) + "\n"
    tmp = bundle / "manifest.json.tmp"
    write_text(tmp, payload, 0o600)
    os.replace(tmp, bundle / "manifest.json")


# ---------------------------------------------------------------------------
#  reading and proving
# ---------------------------------------------------------------------------
def read_manifest(bundle: Path) -> dict:
    return json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))


def verify_bundle(bundle: Path) -> tuple:
    """Re-read every byte a bundle claims.  Returns (manifest, problems)."""
    bundle = Path(bundle)
    problems = []
    if not bundle.is_dir():
        return None, [f"{bundle} is not a folder"]
    try:
        data = read_manifest(bundle)
    except (OSError, ValueError) as exc:
        return None, [f"there is no readable manifest in {bundle.name} "
                      f"({exc.__class__.__name__})"]
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        return None, [f"the manifest is not the shape this tool writes (format {FORMAT})"]
    entries = data.get("entries")
    if not isinstance(entries, list) or not entries:
        return None, ["the manifest lists nothing to put back"]

    seen = set()
    for entry in entries:
        if not isinstance(entry, dict):
            problems.append(f"a manifest entry is not a record: {entry!r}")
            continue
        rel = safe_rel(entry.get("path"))
        if rel is None:
            problems.append(f"a manifest entry points outside the bundle: {entry.get('path')!r}")
            continue
        if rel in seen:
            problems.append(f"{rel} is listed twice")
            continue
        seen.add(rel)
        target = inside(bundle, rel)
        if target is None or not target.is_file():
            problems.append(f"{rel} is missing from the bundle")
            continue
        size = target.stat().st_size
        if entry.get("size") != size:
            problems.append(f"{rel} does not match the manifest: it is {size} bytes, "
                            f"the manifest says {entry.get('size')}")
            continue
        if entry.get("sha256") != sha256_file(target):
            problems.append(f"{rel} does not match its fingerprint — the copy is damaged")
            continue
        if entry.get("to") is not None and safe_rel(entry.get("to")) is None:
            problems.append(f"{rel} would be put back outside the box: {entry.get('to')!r}")
    return data, problems


def bundle_size(bundle: Path) -> tuple:
    """(files, bytes) actually on disk — measured, never taken from the manifest."""
    count = total = 0
    for path in Path(bundle).rglob("*"):
        if path.is_file():
            count += 1
            total += path.stat().st_size
    return count, total


def existing_bundles(folder: Path) -> list:
    """Finished bundles only, oldest first.  A ``.partial`` is not a backup."""
    folder = Path(folder)
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir()
                  if p.is_dir() and STAMP.match(p.name) and (p / "manifest.json").is_file())
