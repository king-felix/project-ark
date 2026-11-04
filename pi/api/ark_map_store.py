"""PROJECT ARK — the card's map shelf.

A map pack is built on a workstation (`scripts/ark-map-pack`) and arrives here as
one folder: a manifest, a routing graph, a search index, the drawing layers and
the attribution.  This module is the only thing on the box that decides where
those folders live, which of them may be offered to a reader, and how a new one
gets in.

The rules it keeps:

* **A version is immutable.**  `packs/<id>/<version>/` is never rewritten in
  place.  A new build is a new version; an update publishes beside the old one,
  and the old one keeps serving until the owner removes it.
* **Nothing is offered before it is proved.**  An imported folder is checked
  byte-for-byte against its manifest (`ark_maps.verify_pack`) *before* it is
  moved into the published tree, and an unchanged pack is re-checked cheaply on
  every catalogue read (every file present, every size as stated).
* **A failed import leaves nothing behind** — not a half folder, not a catalogue
  row, not a reservation of space.
* **An archive cannot escape its folder.**  Zip members are refused if they are
  absolute, contain `..`, are symlinks, are encrypted, are too many, or are
  larger than a pack may be.  Nothing is executed from an archive and nothing is
  fetched because an archive asks for it.
* **Space is the shared policy's.**  `ark_space.hold()` reserves the extraction
  before a byte is written and releases it however the import ends; this module
  adds no second quota.

The `catalog.json` file records publication and the last check.  It is a small
state file, written atomically with `ark_atomic`, and it is deliberately *not* a
claim that the files exist: every catalogue read stats the files again.  A
restored database, or a copied catalogue, can therefore never say "ready" about
a pack whose bytes are not on the card.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import secrets
import shutil
import stat
import time
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Dict, Iterable, List, Optional

import ark_atomic
import ark_maps
import ark_space

CATALOG_SCHEMA = 1
MAX_ARCHIVE_BYTES = 1024 ** 3            # 1 GiB uploaded at once
MAX_EXTRACTED_BYTES = ark_maps.MAX_PACK_BYTES
MAX_ENTRIES = 4000
MAX_MEMBER_BYTES = 1024 ** 3             # one file inside a pack
SPOOL_BLOCK = 1 << 20
SPACE_MARGIN = 64 * 1024 ** 2            # never drive the card into the reserve
SWEEP_SECONDS = 24 * 3600

_SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_SAFE_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class Refused(Exception):
    """Something about a pack, or the room for it, is not acceptable."""


def maps_root() -> Path:
    """Where the maps tree lives, read at call time so a test can move it."""
    return Path(os.environ.get("ARK_MAPS_DIR",
                               str(Path(os.environ.get("ARK_ROOT", "/srv/ark")) / "maps")))


def web_root() -> Path:
    """The application files (`pi/maps/web` in the repository)."""
    override = os.environ.get("ARK_MAPS_WEB")
    if override:
        return Path(override)
    installed = maps_root() / "web"
    if (installed / "index.html").exists():
        return installed
    return Path(__file__).resolve().parents[1] / "maps" / "web"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _human(n: int) -> str:
    value = float(max(0, int(n or 0)))
    for unit, size in (("GB", 1000 ** 3), ("MB", 1000 ** 2), ("kB", 1000)):
        if value >= size:
            return "%.1f %s" % (value / size, unit)
    return "%d B" % value


class Store:
    """The one writer of the map shelf."""

    def __init__(self, root: Optional[Path | str] = None) -> None:
        self.root = Path(root) if root is not None else maps_root()
        self.packs = self.root / "packs"
        self.incoming = self.root / "incoming"
        self.tmp = self.root / "tmp"
        self.catalog_path = self.root / "catalog.json"
        self.lock_path = self.root / ".catalog.lock"

    # -- folders and the catalogue -------------------------------------------

    def ensure(self) -> None:
        for folder in (self.root, self.packs, self.incoming, self.tmp):
            folder.mkdir(parents=True, exist_ok=True)

    def _lock(self):
        @contextmanager
        def held():
            self.ensure()
            with open(self.lock_path, "a+") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return held()

    def _read_catalog(self) -> Dict[str, object]:
        value, problem = ark_atomic.read_json(self.catalog_path)
        if problem and problem != "missing":
            # A damaged catalogue must not make every pack disappear.  Start from
            # what is on disk; publication falls back to "not published", which
            # is the safe direction.
            return {"schema": CATALOG_SCHEMA, "packs": {}}
        if not isinstance(value, dict) or value.get("schema") != CATALOG_SCHEMA:
            return {"schema": CATALOG_SCHEMA, "packs": {}}
        if not isinstance(value.get("packs"), dict):
            return {"schema": CATALOG_SCHEMA, "packs": {}}
        return value

    def _write_catalog(self, catalog: Dict[str, object]) -> None:
        self.ensure()
        ark_atomic.save_json(self.catalog_path, catalog, mode=0o644)

    @staticmethod
    def _entry_key(ident: str, version: str) -> str:
        return "%s@%s" % (ident, version)

    # -- one pack on disk -----------------------------------------------------

    def folder(self, ident: str, version: str) -> Path:
        if not _SAFE_ID.match(ident or "") or not _SAFE_VERSION.match(version or ""):
            raise Refused("That is not a pack identifier and version this shelf can hold.")
        return self.packs / ident / version

    def _quick_problems(self, folder: Path, manifest: Dict[str, object]) -> List[str]:
        """Cheap truth: the manifest is sane and every file is there at its size."""
        problems = ark_maps.manifest_problems(manifest)
        listed = {}
        for row in manifest.get("files") or []:
            if isinstance(row, (list, tuple)) and len(row) == 3:
                listed[str(row[0])] = (int(row[1]), str(row[2]))
        for name, (size, _digest) in sorted(listed.items()):
            path = folder / name
            if not path.is_file():
                problems.append("%s is missing" % name)
            elif size and path.stat().st_size != size:
                problems.append("%s is %d bytes and should be %d"
                                % (name, path.stat().st_size, size))
        binary = folder / "graph.bin"
        try:
            with binary.open("rb") as handle:
                head = handle.read(24)
            if len(head) < 24 or head[:4] != ark_maps.GRAPH_MAGIC:
                problems.append("graph.bin does not start like a map graph")
        except OSError:
            problems.append("graph.bin cannot be read")
        return problems

    def _row(self, folder: Path, catalog: Dict[str, object]) -> Optional[Dict[str, object]]:
        try:
            summary = ark_maps.pack_summary(str(folder))
        except (OSError, ValueError) as exc:
            return {
                "id": folder.parent.name,
                "version": folder.name,
                "name": folder.parent.name,
                "bytes": 0,
                "problems": [str(exc)],
                "published": False,
                "verified": False,
                "ready": False,
                "folder": str(folder),
            }
        key = self._entry_key(str(summary.get("id")), str(summary.get("version")))
        entry = (catalog.get("packs") or {}).get(key) or {}
        if not isinstance(entry, dict):
            entry = {}
        try:
            manifest = ark_maps.read_manifest(str(folder))
        except (OSError, ValueError):
            manifest = {}
        problems = self._quick_problems(folder, manifest)
        if isinstance(summary.get("problems"), list):
            problems.extend(summary["problems"])
        # A pack that exists but was never imported through this shelf has no
        # record: it is visible to the owner and it is *not* offered.
        published = bool(entry.get("published"))
        verified = bool(entry.get("verified"))
        ready = verified and not problems
        return {
            "id": summary.get("id"),
            "version": summary.get("version"),
            "name": summary.get("name") or summary.get("id"),
            "code": summary.get("code") or "",
            "bounds": summary.get("bounds") or [],
            "data_date": summary.get("data_date") or "",
            "built": summary.get("built") or "",
            "bytes": int(summary.get("bytes") or 0),
            "capabilities": summary.get("capabilities") or {},
            "map": summary.get("map") or {},
            "search": summary.get("search") or {},
            "app": summary.get("app") or {},
            "licence": summary.get("licence") or "",
            "attribution": summary.get("attribution") or {},
            "limitations": (manifest.get("coverage") or {}).get("limitations", ""),
            "problems": problems,
            "published": published,
            "verified": verified,
            "ready": ready,
            "added": entry.get("added") or datetime.fromtimestamp(
                folder.stat().st_mtime, timezone.utc).isoformat(timespec="seconds"),
            "verified_at": entry.get("verified_at") or "",
            "folder": str(folder),
        }

    def rows(self) -> List[Dict[str, object]]:
        """Every pack folder, published or not, with a cheap readiness check."""
        self.ensure()
        catalog = self._read_catalog()
        found: List[Dict[str, object]] = []
        if self.packs.exists():
            for ident in sorted(self.packs.iterdir()):
                if not ident.is_dir() or not _SAFE_ID.match(ident.name):
                    continue
                for version in sorted(ident.iterdir()):
                    if not version.is_dir() or not _SAFE_VERSION.match(version.name):
                        continue
                    if not (version / "manifest.json").is_file():
                        continue
                    row = self._row(version, catalog)
                    if row:
                        found.append(row)
        found.sort(key=lambda row: (str(row.get("name") or ""), str(row.get("version") or "")),
                   reverse=False)
        return found

    def published_rows(self) -> List[Dict[str, object]]:
        """What a reader may see: published *and* every file still present."""
        rows = []
        for row in self.rows():
            if not row.get("published") or not row.get("ready"):
                continue
            public = {key: value for key, value in row.items() if key != "folder"}
            public["version"] = row.get("version")
            rows.append(public)
        return rows

    def row(self, ident: str, version: str) -> Optional[Dict[str, object]]:
        for row in self.rows():
            if row.get("id") == ident and row.get("version") == version:
                return row
        return None

    # -- import ---------------------------------------------------------------

    def _spool(self, stream: BinaryIO, target: Path, receipt=None) -> int:
        """Write an upload to the card, bounded and with its room promised first.

        ``receipt`` is the promise this import already holds.  Without one (a
        caller that is not an import) a local promise is opened and released
        here.  The archive is promised a step at a time, so a 1 GiB pack does not
        reserve its whole size before the first byte and an unknown-size stream
        still cannot write past what it has promised.
        """
        written = 0
        held = receipt
        local = None
        if held is None:
            local = ark_space.open_receipt(self.root, owner="maps-import",
                                           label="a map pack", kind="maps")
            held = local
        try:
            held.need(ark_space.first_step(self.root, MAX_ARCHIVE_BYTES))
            with target.open("wb") as handle:
                while True:
                    block = stream.read(SPOOL_BLOCK)
                    if not block:
                        break
                    written += len(block)
                    if written > MAX_ARCHIVE_BYTES:
                        raise Refused("That archive is larger than %s, the most this "
                                      "shelf imports in one piece." % _human(MAX_ARCHIVE_BYTES))
                    if written + SPOOL_BLOCK > held.reserved:
                        held.need(ark_space.growth_step(self.root, written,
                                                        MAX_ARCHIVE_BYTES))
                    held.spent("archive", written)
                    handle.write(block)
                handle.flush()
                os.fsync(handle.fileno())
                held.spent("archive", written)
        finally:
            if local is not None:
                local.release()
        return written

    def _zip_entries(self, archive: Path) -> List[zipfile.ZipInfo]:
        try:
            with zipfile.ZipFile(archive) as bundle:
                infos = bundle.infolist()
        except (zipfile.BadZipFile, OSError) as exc:
            raise Refused("That file is not a readable zip archive: %s" % (exc,))
        if not infos:
            raise Refused("That archive is empty.")
        if len(infos) > MAX_ENTRIES:
            raise Refused("That archive holds %d entries; a pack may hold at most %d."
                          % (len(infos), MAX_ENTRIES))
        total = 0
        seen = set()
        for info in infos:
            name = info.filename
            if not name or name.endswith("/"):
                continue
            if "\\" in name or name.startswith("/") or any(part in ("", ".", "..") for part in name.split("/")):
                raise Refused("That archive contains a path this shelf will not write: %r" % (name,))
            if name in seen:
                raise Refused("That archive lists %r twice." % (name,))
            seen.add(name)
            mode = (info.external_attr >> 16) & 0o170000
            if mode == stat.S_IFLNK:
                raise Refused("That archive contains a link, which a pack may not carry.")
            if info.flag_bits & 0x1:
                raise Refused("That archive is encrypted; a pack must be readable without a secret.")
            if info.file_size > MAX_MEMBER_BYTES:
                raise Refused("%s is larger than one pack file may be." % (name,))
            total += int(info.file_size)
            if total > MAX_EXTRACTED_BYTES:
                raise Refused("That archive expands to more than %s, the most a pack may be."
                              % _human(MAX_EXTRACTED_BYTES))
        return infos

    def _extract(self, archive: Path, staging: Path, receipt=None) -> Path:
        """Extract safely and answer the folder that holds manifest.json."""
        infos = self._zip_entries(archive)
        staging.mkdir(parents=True, exist_ok=True)
        archive_size = archive.stat().st_size if receipt is not None else 0
        extracted = 0
        with zipfile.ZipFile(archive) as bundle:
            for info in infos:
                name = info.filename
                if not name or name.endswith("/"):
                    continue
                target = staging.joinpath(*name.split("/"))
                target.parent.mkdir(parents=True, exist_ok=True)
                written = 0
                if receipt is not None:
                    # The archive and the folder it expands to exist together, so
                    # the peak is both of them plus the file being written now.
                    receipt.need(archive_size + extracted + int(info.file_size) + SPOOL_BLOCK)
                with bundle.open(info) as source, target.open("xb") as sink:
                    while True:
                        block = source.read(SPOOL_BLOCK)
                        if not block:
                            break
                        written += len(block)
                        if written > MAX_MEMBER_BYTES:
                            raise Refused("%s is larger than its own archive said." % (name,))
                        sink.write(block)
                    sink.flush()
                    os.fsync(sink.fileno())
                if info.file_size and written != info.file_size:
                    raise Refused("%s arrived with %d bytes and the archive said %d."
                                  % (name, written, info.file_size))
                extracted += written
                if receipt is not None:
                    receipt.spent("extract", extracted)
        roots = {str(path.relative_to(staging)).split("/")[0] for path in staging.rglob("manifest.json")}
        if roots == {"manifest.json"}:
            return staging
        if len(roots) == 1:
            candidate = staging / next(iter(roots))
            if (candidate / "manifest.json").is_file():
                return candidate
        raise Refused("That archive does not hold one pack folder with a manifest.json "
                      "at its top.")

    def _publish_folder(self, folder: Path) -> Dict[str, object]:
        """Verify a finished folder, then move it into the immutable tree."""
        report = ark_maps.verify_pack(str(folder))
        if not report["ok"]:
            raise Refused("The pack did not pass its own check: %s"
                          % ("; ".join(report["problems"][:6]) or "unknown problem"))
        manifest = report["manifest"] or {}
        ident = str(manifest.get("id") or "")
        version = str(manifest.get("version") or "")
        destination = self.folder(ident, version)
        if destination.exists():
            raise Refused("%s version %s is already on the card. A version is immutable; "
                          "build a new one instead." % (ident, version))
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(folder, destination)
        try:
            os.chmod(destination, 0o755)
        except OSError:
            pass
        with self._lock():
            catalog = self._read_catalog()
            packs = catalog.setdefault("packs", {})
            if not isinstance(packs, dict):
                packs = {}
                catalog["packs"] = packs
            packs[self._entry_key(ident, version)] = {
                "published": False,
                "verified": True,
                "verified_at": _now(),
                "added": _now(),
                "bytes": int(manifest.get("total_bytes") or 0),
            }
            self._write_catalog(catalog)
        return self._row(destination, self._read_catalog()) or {}

    def import_zip(self, stream: BinaryIO, *, filename: str = "") -> Dict[str, object]:
        """Receive one archive, prove it, and put it on the shelf unpublished."""
        self.ensure()
        token = secrets.token_hex(8)
        archive = self.incoming / ("upload-%s.zip.part" % token)
        staging = self.tmp / ("import-%s" % token)
        try:
            # One promise covers the whole import: the archive arriving, the
            # folder it expands to while the archive still exists, and the
            # published version (a rename, so no third copy).  Inside an upload
            # this adopts the request's promise rather than reserving the body a
            # second time; from a console it is a reservation of its own.
            with ark_space.cover(self.root, 0, owner="maps-import",
                                 label=filename or "map pack",
                                 key="archive") as receipt:
                size = self._spool(stream, archive, receipt)
                if not size:
                    raise Refused("The uploaded archive was empty.")
                self._zip_entries(archive)          # refuses a bad archive first
                root = self._extract(archive, staging, receipt)
                return self._publish_folder(root)
        except ark_space.Refused as exc:
            raise Refused(str(exc))
        finally:
            for path in (archive, staging):
                try:
                    if path.is_dir():
                        shutil.rmtree(path)
                    elif path.exists():
                        path.unlink()
                except OSError:
                    pass

    def import_path(self, path: Path | str) -> Dict[str, object]:
        """Import a folder or an archive already in `incoming/` (scp, a stick)."""
        self.ensure()
        source = Path(path).resolve()
        # Resolve before the containment check: `incoming/../etc` is lexically
        # under `incoming` and is not under it at all.
        try:
            source.relative_to(self.incoming.resolve())
        except ValueError:
            raise Refused("Only something inside the incoming folder may be imported.")
        if source.is_symlink() or not source.exists():
            raise Refused("That incoming item does not exist.")
        if source.is_dir():
            staging = self.tmp / ("import-%s" % secrets.token_hex(8))
            try:
                # Copy first, so a failed check never leaves a half-moved folder.
                # The copy is the growth this import causes, so it asks the one
                # storage policy for room before it starts.
                size = sum(path.stat().st_size for path in source.rglob("*") if path.is_file())
                with ark_space.cover(self.root, size, owner="maps-import",
                                     label=source.name, key="archive") as receipt:
                    shutil.copytree(source, staging, symlinks=False)
                    receipt.spent("archive", size)
                    root = staging
                    if not (root / "manifest.json").is_file():
                        found = [path for path in root.rglob("manifest.json")]
                        if len(found) != 1:
                            raise Refused("That folder does not hold one pack with a manifest.json.")
                        root = found[0].parent
                    return self._publish_folder(root)
            except ark_space.Refused as exc:
                raise Refused(str(exc))
            finally:
                if staging.exists():
                    shutil.rmtree(staging, ignore_errors=True)
        if source.suffix.lower() != ".zip":
            raise Refused("That incoming item is neither a folder nor a zip archive.")
        token = secrets.token_hex(8)
        archive = self.incoming / ("import-%s.zip" % token)
        staging = self.tmp / ("import-%s" % token)
        try:
            os.replace(source, archive)
            infos = self._zip_entries(archive)
            projected = sum(int(info.file_size) for info in infos) + archive.stat().st_size
            with ark_space.cover(self.root, projected, owner="maps-import",
                                 label=source.name, key="archive") as receipt:
                root = self._extract(archive, staging, receipt)
                return self._publish_folder(root)
        except ark_space.Refused as exc:
            raise Refused(str(exc))
        finally:
            for path in (archive, staging):
                try:
                    if path.is_dir():
                        shutil.rmtree(path)
                    elif path.exists():
                        path.unlink()
                except OSError:
                    pass

    def incoming_items(self) -> List[Dict[str, object]]:
        self.ensure()
        found = []
        for path in sorted(self.incoming.iterdir()):
            if path.name.startswith(".") or path.name.endswith(".part"):
                continue
            if path.is_dir() or path.suffix.lower() == ".zip":
                try:
                    size = sum(p.stat().st_size for p in path.rglob("*") if p.is_file()) \
                        if path.is_dir() else path.stat().st_size
                except OSError:
                    size = 0
                found.append({"name": path.name, "bytes": size,
                              "kind": "folder" if path.is_dir() else "archive"})
        return found

    # -- publication and removal ---------------------------------------------

    def publish(self, ident: str, version: str, on: bool = True) -> Dict[str, object]:
        row = self.row(ident, version)
        if not row:
            raise Refused("There is no %s version %s on the card." % (ident, version))
        if on:
            if row.get("problems"):
                raise Refused("This pack is not ready to publish: %s" % (row["problems"][0],))
            if not row.get("verified"):
                raise Refused("This pack has not been checked since it arrived. Run a check first, then publish it.")
        with self._lock():
            catalog = self._read_catalog()
            packs = catalog.setdefault("packs", {})
            if not isinstance(packs, dict):
                packs = {}
                catalog["packs"] = packs
            key = self._entry_key(ident, version)
            entry = packs.get(key) if isinstance(packs.get(key), dict) else {}
            entry.update({"published": bool(on),
                          "verified": bool(row.get("verified")),
                          "verified_at": row.get("verified_at") or entry.get("verified_at") or "",
                          "added": entry.get("added") or row.get("added") or _now()})
            packs[key] = entry
            self._write_catalog(catalog)
        return self.row(ident, version) or row

    def verify(self, ident: str, version: str) -> Dict[str, object]:
        """Run the full byte check and remember the verdict."""
        folder = self.folder(ident, version)
        if not folder.is_dir():
            raise Refused("There is no %s version %s on the card." % (ident, version))
        report = ark_maps.verify_pack(str(folder))
        with self._lock():
            catalog = self._read_catalog()
            packs = catalog.setdefault("packs", {})
            if not isinstance(packs, dict):
                packs = {}
                catalog["packs"] = packs
            key = self._entry_key(ident, version)
            entry = packs.get(key) if isinstance(packs.get(key), dict) else {}
            entry["verified"] = bool(report["ok"])
            entry["verified_at"] = _now()
            if not report["ok"]:
                # A pack whose bytes moved is not an offered pack.  The previous
                # *version* is untouched and stays available.
                entry["published"] = False
            entry.setdefault("published", False)
            entry.setdefault("added", _now())
            packs[key] = entry
            self._write_catalog(catalog)
        report["id"] = ident
        report["version"] = version
        return report

    def delete(self, ident: str, version: str) -> None:
        folder = self.folder(ident, version)
        if not folder.is_dir():
            raise Refused("There is no %s version %s on the card." % (ident, version))
        with self._lock():
            shutil.rmtree(folder)
            try:
                folder.parent.rmdir()
            except OSError:
                pass
            catalog = self._read_catalog()
            packs = catalog.get("packs")
            if isinstance(packs, dict):
                packs.pop(self._entry_key(ident, version), None)
                self._write_catalog(catalog)

    # -- housekeeping and storage --------------------------------------------

    def sweep(self, max_age: int = SWEEP_SECONDS) -> int:
        """Remove stale half-imports.  Never a pack, published or not."""
        self.ensure()
        cut = time.time() - max(60, int(max_age))
        removed = 0
        for folder in (self.incoming, self.tmp):
            if not folder.exists():
                continue
            for path in folder.iterdir():
                try:
                    if path.stat().st_mtime >= cut:
                        continue
                    if path.is_dir():
                        shutil.rmtree(path)
                    else:
                        path.unlink()
                    removed += 1
                except OSError:
                    pass
        return removed

    def storage(self) -> Dict[str, object]:
        self.ensure()
        try:
            account = ark_space.account(self.root)
        except Exception:            # noqa: BLE001 — a page must still render
            account = {}
        rows = self.rows()
        pack_bytes = sum(int(row.get("bytes") or 0) for row in rows)
        return {
            "account": account,
            "packs": len(rows),
            "published": sum(1 for row in rows if row.get("published") and row.get("ready")),
            "pack_bytes": pack_bytes,
            "largest": sorted(
                [{"id": row.get("id"), "version": row.get("version"),
                  "bytes": row.get("bytes"), "name": row.get("name")} for row in rows],
                key=lambda item: int(item.get("bytes") or 0), reverse=True)[:5],
        }


def describe_storage(storage: Dict[str, object]) -> str:
    """One sentence for an owner, from measured numbers only."""
    account = storage.get("account") or {}
    parts = []
    if account.get("free_bytes") is not None:
        parts.append("%s free" % _human(account.get("free_bytes")))
        parts.append("%s kept back" % _human(account.get("reserve_bytes")))
    parts.append("%d pack%s, %s" % (storage.get("packs", 0),
                                    "" if storage.get("packs") == 1 else "s",
                                    _human(storage.get("pack_bytes"))))
    return " · ".join(parts)
