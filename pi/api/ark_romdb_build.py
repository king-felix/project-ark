"""Project ARK — building the offline game-name database.

The box recognises a game by its fingerprints, and those fingerprints come from
libretro-database's No-Intro catalogues: one DAT per console listing every
cartridge ever dumped, with its CRC-32, MD5 and SHA-1.  Seven sidecar folders
(developer, publisher, release year, genre, players, franchise, age rating) join
onto the same entries by CRC.

This module turns all of that into one small SQLite file the box reads at run
time.  It is a **build** step, not a service:

* the source is fetched with a *sparse* clone — only ``metadat/``, never the
  multi-gigabyte ``dat/`` folder beside it;
* the file is written as ``romdb.sqlite.partial`` and published by rename, so a
  half-written database can never be read by the pages;
* nothing is fetched or written unless somebody asks: a box with no internet and
  no database still serves every game, it just falls back to filenames.

Run it as ``ark-romdb-build`` (in ``/usr/local/sbin``) on the box, or on a
machine with internet and copy the file over:

    ark-romdb-build --fetch                       # build from GitHub
    ark-romdb-build --from-dir /path/to/libretro-database
    scp romdb.sqlite pi@ark.local:/tmp/romdb.sqlite
    sudo install -m 644 -o pi -g pi /tmp/romdb.sqlite /srv/ark/romdb.sqlite
"""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import ark_romdb as romdb

SOURCE_REPO = "https://github.com/libretro/libretro-database.git"
SOURCE_URL = "https://github.com/libretro/libretro-database"
MIN_ROWS = 50000        # below this the build is not worth keeping

_FIELD_RE = re.compile(r'^([A-Za-z_][A-Za-z0-9_]*)\s+(?:"(.*)"|(\S+))\s*$')
_NAME_RE = re.compile(r'\bname\s+"([^"]*)"')
_ROM_FIELD_RE = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)\s+("[^"]*"|\S+)')

SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '');
CREATE TABLE roms (
    id            INTEGER PRIMARY KEY,
    system        TEXT NOT NULL,
    system_label  TEXT NOT NULL DEFAULT '',
    playable      INTEGER NOT NULL DEFAULT 0,
    title         TEXT NOT NULL DEFAULT '',
    name          TEXT NOT NULL DEFAULT '',
    region        TEXT NOT NULL DEFAULT '',
    rom_name      TEXT NOT NULL DEFAULT '',
    size          INTEGER NOT NULL DEFAULT 0,
    crc32         TEXT NOT NULL DEFAULT '',
    md5           TEXT NOT NULL DEFAULT '',
    sha1          TEXT NOT NULL DEFAULT '',
    developer     TEXT NOT NULL DEFAULT '',
    publisher     TEXT NOT NULL DEFAULT '',
    year          TEXT NOT NULL DEFAULT '',
    genre         TEXT NOT NULL DEFAULT '',
    players       TEXT NOT NULL DEFAULT '',
    franchise     TEXT NOT NULL DEFAULT '',
    esrb          TEXT NOT NULL DEFAULT '',
    source        TEXT NOT NULL DEFAULT ''
);
CREATE INDEX roms_sha1  ON roms(sha1)  WHERE sha1  <> '';
CREATE INDEX roms_md5   ON roms(md5)   WHERE md5   <> '';
CREATE INDEX roms_crc32 ON roms(crc32) WHERE crc32 <> '';
CREATE INDEX roms_title ON roms(title COLLATE NOCASE);
"""

COLUMNS = (
    "system", "system_label", "playable", "title", "name", "region", "rom_name",
    "size", "crc32", "md5", "sha1", "developer", "publisher", "year", "genre",
    "players", "franchise", "esrb", "source",
)


# ---------------------------------------------------------------------------
#  reading the DAT files (clrmamepro text: one field per line)
# ---------------------------------------------------------------------------

def iter_blocks(text: str) -> Iterable[Dict[str, Any]]:
    """Every ``game ( … )`` block in a DAT, as fields plus rom lines.

    Line-based on purpose: libretro's DATs put one field and one rom on a line
    each, and the values contain parentheses ("Super Mario Bros. (World)"), so a
    regex over a whole block would cut names in half.
    """
    cur: Optional[Dict[str, Any]] = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if cur is None:
            if line.startswith("game ("):
                cur = {"fields": {}, "roms": []}
            continue
        if line == ")":
            yield cur
            cur = None
            continue
        if line.startswith("rom ("):
            inner = line[line.index("(") + 1:]
            if ")" in inner:
                inner = inner[: inner.rindex(")")]
            rom: Dict[str, str] = {}
            found = _NAME_RE.search(inner)
            if found:
                rom["name"] = found.group(1)
            rest = _NAME_RE.sub(" ", inner)
            for key, value in _ROM_FIELD_RE.findall(rest):
                rom[key.lower()] = value.strip('"')
            cur["roms"].append(rom)
            continue
        match = _FIELD_RE.match(line)
        if match:
            key = match.group(1).lower()
            value = match.group(2) if match.group(2) is not None else match.group(3)
            cur["fields"][key] = value


def _size_of(value: Any) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def _year_of(value: Any) -> str:
    text = str(value or "").strip()
    return text if re.match(r"^(19|20)\d\d$", text) else ""


def parse_no_intro(text: str, system: str, label: str, source: str) -> List[Dict[str, Any]]:
    """One console's DAT as database rows (one per rom line)."""
    rows: List[Dict[str, Any]] = []
    for block in iter_blocks(text):
        name = block["fields"].get("name", "")
        if not name:
            continue
        region = block["fields"].get("region", "") or romdb.region_of(name)
        title = romdb.clean_title(name)
        for rom in block["roms"]:
            crc = (rom.get("crc") or "").strip().lower()
            if not crc:
                continue
            rows.append({
                "system": system, "system_label": label,
                "playable": 1 if system in romdb.PLAYABLE else 0,
                "title": title, "name": name, "region": region,
                "rom_name": rom.get("name", "") or "",
                "size": _size_of(rom.get("size")),
                "crc32": crc,
                "md5": (rom.get("md5") or "").strip().lower(),
                "sha1": (rom.get("sha1") or "").strip().lower(),
                "developer": "", "publisher": "", "year": "", "genre": "",
                "players": "", "franchise": "", "esrb": "", "source": source,
            })
    return rows


def parse_metadata(text: str, field: str) -> Dict[str, str]:
    """CRC -> the value of one sidecar field, for every game in the file."""
    out: Dict[str, str] = {}
    for block in iter_blocks(text):
        value = str(block["fields"].get(field, "") or "").strip()
        if not value:
            continue
        for rom in block["roms"]:
            crc = (rom.get("crc") or "").strip().lower()
            if crc and crc not in out:
                out[crc] = value
    return out


def _version_of(text: str) -> str:
    found = re.search(r'^\s*version\s+"([^"]*)"', text, re.MULTILINE)
    return found.group(1) if found else ""


# ---------------------------------------------------------------------------
#  building the file
# ---------------------------------------------------------------------------

def collect(source: Path, *, log=lambda _msg: None) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Read every DAT under ``<source>/metadat`` and join the sidecars in."""
    metadat = Path(source) / "metadat"
    if not (metadat / "no-intro").is_dir():
        raise SystemExit("Not done: %s holds no metadat/no-intro folder" % source)

    sidecars: Dict[str, Dict[str, str]] = {}
    for folder, field, column in romdb.META_SOURCES:
        if not column:
            continue
        folder_path = metadat / folder
        if not folder_path.is_dir():
            continue
        seen = 0
        for dat in sorted(folder_path.glob("*.dat")):
            try:
                text = dat.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for crc, value in parse_metadata(text, field).items():
                sidecars.setdefault(crc, {})[column] = value
                seen += 1
        log("  %-12s %7d fact(s)" % (folder, seen))

    rows: List[Dict[str, Any]] = []
    versions: List[str] = []
    missing: List[str] = []
    for stem, (system, label) in sorted(romdb.SYSTEM_DATS.items()):
        dat = metadat / "no-intro" / (stem + ".dat")
        if not dat.is_file():
            missing.append(stem)
            continue
        try:
            text = dat.read_text(encoding="utf-8", errors="replace")
        except OSError:
            missing.append(stem)
            continue
        versions.append(_version_of(text))
        found = parse_no_intro(text, system, label, dat.name)
        rows.extend(found)
        log("  %-46s %7d" % (label, len(found)))

    if missing:
        log("  (no DAT for: %s)" % ", ".join(missing))

    for row in rows:
        extra = sidecars.get(row["crc32"])
        if not extra:
            continue
        for key, value in extra.items():
            if not value or row.get(key):
                continue
            row[key] = _year_of(value) if key == "year" else value

    facts = {
        "source_date": max(versions) if versions else "",
        "systems": len(romdb.SYSTEM_DATS) - len(missing),
        "missing": len(missing),
    }
    return rows, facts


def write_db(rows: List[Dict[str, Any]], path: Path, facts: Dict[str, Any], *, source: str = "") -> Path:
    """Write the rows as a new database, then publish it by rename."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".partial")
    for leftover in (temp, path.with_name(path.name + ".partial-journal")):
        try:
            leftover.unlink()
        except OSError:
            pass  # nothing to sweep, which is the usual case

    conn = sqlite3.connect(str(temp))
    try:
        conn.executescript(SCHEMA)
        conn.execute("PRAGMA synchronous = OFF")
        placeholders = ", ".join("?" for _ in COLUMNS)
        insert = "INSERT INTO roms (%s) VALUES (%s)" % (", ".join(COLUMNS), placeholders)
        batch: List[Tuple[Any, ...]] = []
        for row in rows:
            batch.append(tuple(row.get(col) for col in COLUMNS))
            if len(batch) >= 4000:
                conn.executemany(insert, batch)
                batch = []
        if batch:
            conn.executemany(insert, batch)
        meta = {
            "format": "1",
            "built": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "source": source or SOURCE_URL,
            "source_date": str(facts.get("source_date", "")),
            "source_commit": str(facts.get("source_commit", "")),
            "systems": str(facts.get("systems", "")),
            "rows": str(len(rows)),
            "playable": str(sum(1 for r in rows if r.get("playable"))),
        }
        conn.executemany("INSERT INTO meta (key, value) VALUES (?, ?)", sorted(meta.items()))
        conn.commit()
        conn.execute("PRAGMA optimize")
        conn.commit()
    finally:
        conn.close()

    try:
        os.chmod(temp, 0o644)
    except OSError:
        pass  # a build on a stick or a checkout may not need it
    os.replace(temp, path)
    return path


def build(source: Path, out: Optional[Path] = None, *, log=lambda _msg: None) -> Path:
    """Parse a checkout of libretro-database into ``out`` (default: the box's)."""
    target = Path(out) if out else romdb.db_path()
    rows, facts = collect(Path(source), log=log)
    if len(rows) < MIN_ROWS:
        raise SystemExit(
            "Not done: only %d game(s) were read, fewer than %d — the file is not worth keeping"
            % (len(rows), MIN_ROWS)
        )
    written = write_db(rows, target, facts, source=str(source))
    romdb.forget_stats()
    log(
        "Built %s: %d game(s), %d on a console this box can play, source dated %s"
        % (written, len(rows), sum(1 for r in rows if r.get("playable")),
           facts.get("source_date") or "unknown")
    )
    return written


# ---------------------------------------------------------------------------
#  getting the source, on a box that may have no internet
# ---------------------------------------------------------------------------

def fetch(dest: Path, *, ref: str = "master", log=lambda _msg: None) -> Path:
    """A sparse clone of the catalogue: ``metadat/`` and nothing else.

    A full clone is a few gigabytes and mostly the ``dat/`` folder this box has
    no use for.  ``--filter=blob:none`` plus a sparse checkout fetches the trees
    first and only the DAT files after, which is tens of megabytes.
    """
    dest = Path(dest)
    if shutil.which("git") is None:
        raise SystemExit("Not done: this box has no git, so the catalogue cannot be fetched")
    if (dest / "metadat").is_dir():
        log("  using the copy already at %s" % dest)
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    steps = [
        ["git", "clone", "--depth", "1", "--filter=blob:none", "--sparse",
         "--branch", ref, SOURCE_REPO, str(dest)],
        ["git", "-C", str(dest), "sparse-checkout", "set", "metadat"],
    ]
    for step in steps:
        log("  " + " ".join(step[3:] if step[0] == "git" else step))
        done = subprocess.run(step, capture_output=True, text=True, timeout=1800)
        if done.returncode != 0:
            detail = (done.stderr or done.stdout or "").strip().splitlines()
            raise SystemExit(
                "Not done: git could not fetch the catalogue (%s)"
                % (detail[-1] if detail else "no reason given")
            )
    if not (dest / "metadat" / "no-intro").is_dir():
        raise SystemExit("Not done: %s came back without metadat/no-intro" % dest)
    return dest


def source_commit(path: Path) -> str:
    """The catalogue's commit, when the source is a git checkout (else '')."""
    try:
        done = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


# ---------------------------------------------------------------------------
#  proving it works without the catalogue at all
# ---------------------------------------------------------------------------

_SAMPLE_DAT = """clrmamepro (
\tname "%(system)s"
\tdescription "%(system)s"
\tversion "2026.01.01"
\textensions "nes"
)

game (
\tname "Test Quest (USA) (Rev 1)"
\tregion "USA"
\trom ( name "Test Quest (USA) (Rev 1).nes" size %(size)d crc %(crc)s md5 %(md5)s sha1 %(sha1)s )
)
"""


def _sample_tree(base: Path, body: bytes) -> Path:
    """A tiny catalogue with one game, so a build can be proved on any box."""
    import hashlib
    import zlib

    metadat = base / "metadat"
    (metadat / "no-intro").mkdir(parents=True, exist_ok=True)
    system = "Nintendo - Nintendo Entertainment System"
    facts = {
        "crc": ("%08x" % (zlib.crc32(body) & 0xFFFFFFFF)).upper(),
        "md5": hashlib.md5(body).hexdigest().upper(),
        "sha1": hashlib.sha1(body).hexdigest().upper(),
        "size": len(body),
        "system": system,
    }
    (metadat / "no-intro" / (system + ".dat")).write_text(_SAMPLE_DAT % facts, encoding="utf-8")
    sidecar = (
        'clrmamepro (\n\tname "%s"\n)\n\n'
        "game (\n\tcomment \"Test Quest (USA)\"\n\t%s\n\trom ( crc %s )\n)\n"
    )
    values = {
        "developer": "developer", "publisher": "publisher", "releaseyear": "releaseyear",
        "genre": "genre", "maxusers": "users", "franchise": "franchise",
        "esrb": "esrb_rating",
    }
    fill = {
        "developer": "A Test Studio", "publisher": "A Test Label", "releaseyear": "1989",
        "genre": "Puzzle", "users": "2", "franchise": "Test Quest", "esrb_rating": "E",
    }
    for folder, field in values.items():
        target = metadat / folder
        target.mkdir(parents=True, exist_ok=True)
        (target / (system + ".dat")).write_text(
            sidecar % (system, '%s "%s"' % (field, fill[field]), facts["crc"]),
            encoding="utf-8",
        )
    return base


def self_test() -> Tuple[bool, List[str]]:
    """Build a one-game database in a temp folder and recognise a file with it.

    The file carries a 16-byte Nintendo header the catalogue does not list, so a
    pass proves the header rules, the sidecar join, the indexes and the lookup at
    once — on a box that has never seen the real catalogue.
    """
    import zipfile as _zip

    body = bytes((i * 37) % 251 for i in range(16384))
    header = b"NES\x1a" + bytes(12)
    lines: List[str] = []
    ok = True
    with tempfile.TemporaryDirectory(prefix="ark-romdb-selftest-") as tmp:
        base = _sample_tree(Path(tmp) / "src", body)
        db = Path(tmp) / "romdb.sqlite"
        rows, facts = collect(base)
        write_db(rows, db, facts, source="self-test")
        saved = os.environ.get("ARK_ROMDB")
        os.environ["ARK_ROMDB"] = str(db)
        romdb.forget_stats()
        try:
            rom = Path(tmp) / "Test Quest (USA) (Rev 1).nes"
            rom.write_bytes(header + body)
            found = romdb.match(romdb.identify_path(rom))
            lines.append("  file with a header: %s" % (romdb.describe({}, found) or "-"))
            for field, want in (("title", "Test Quest"), ("year", "1989"),
                                ("developer", "A Test Studio"), ("genre", "Puzzle"),
                                ("players", "2"), ("region", "USA")):
                got = str(found.get(field, ""))
                if got != want:
                    ok = False
                    lines.append("  FAIL %s: wanted %r, got %r" % (field, want, got))
            if found.get("hash_label") != "without its 16-byte Nintendo header":
                ok = False
                lines.append("  FAIL the header was not the view that matched (%r)" % found.get("hash_label"))
            if found.get("matched_by") != "sha1":
                ok = False
                lines.append("  FAIL the strongest hash did not win (%r)" % found.get("matched_by"))

            archived = Path(tmp) / "carried.zip"
            with _zip.ZipFile(archived, "w") as zf:
                zf.writestr("Test Quest (USA) (Rev 1).nes", header + body)
            in_zip = romdb.match(romdb.identify_path(archived))
            lines.append("  game inside an archive: %s" % (romdb.describe({}, in_zip) or "-"))
            if in_zip.get("title") != "Test Quest":
                ok = False
                lines.append("  FAIL the archive was not recognised")

            stranger = Path(tmp) / "Mystery (Japan).nes"
            stranger.write_bytes(b"NES\x1a" + bytes(12) + bytes(reversed(body)))
            answer = romdb.identify_path(stranger)
            missed = romdb.match(answer)
            lines.append("  unknown file: %s" % romdb.describe(answer, missed))
            if missed:
                ok = False
                lines.append("  FAIL an unknown file was matched to something")

            stats = romdb.stats()
            lines.append("  database: %d row(s), format %d" % (stats["rows"], stats["format"]))
            if stats["rows"] != 1 or stats["format"] != romdb.FORMAT:
                ok = False
                lines.append("  FAIL the database is not what was just built")
        finally:
            if saved is None:
                os.environ.pop("ARK_ROMDB", None)
            else:
                os.environ["ARK_ROMDB"] = saved
            romdb.forget_stats()
    return ok, lines


# ---------------------------------------------------------------------------
#  the command
# ---------------------------------------------------------------------------

def _status(target: str = "") -> int:
    """What a database holds — the one named, or the box's own.

    ``--out`` names the file to answer about, and the answer says which file it
    read.  Looking at the default path while the caller named another is the same
    silent fault as ``--list`` answering for the wrong folder (§8.7).
    """
    saved = os.environ.get("ARK_ROMDB")
    if target:
        os.environ["ARK_ROMDB"] = target
        romdb.forget_stats()
    try:
        facts = romdb.stats()
        path = romdb.db_path()
        if not facts["present"]:
            print("No game-name database at %s (%s)." % (path, facts["problem"] or "empty"))
            print("Every game still plays; a file is only named from its file name.")
            print("To build one:  ark-romdb-build --fetch")
            return 1
        print("Game-name database: %s" % path)
        print("  %d game(s), %d on a console this box can play, %d console(s)"
              % (facts["rows"], facts["playable_rows"], facts["systems"]))
        print("  catalogue dated %s, built %s, format %d"
              % (facts["source_date"] or "unknown", facts["built"] or "unknown",
                 facts["format"]))
        if facts["problem"]:
            print("  problem: %s" % facts["problem"])
        return 0 if not facts["problem"] else 1
    finally:
        if target:
            if saved is None:
                os.environ.pop("ARK_ROMDB", None)
            else:
                os.environ["ARK_ROMDB"] = saved
            romdb.forget_stats()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ark-romdb-build",
        description="Build the offline game-name database the box uses to recognise a game file.",
    )
    parser.add_argument("--fetch", action="store_true",
                        help="fetch the catalogue from GitHub (needs internet), then build")
    parser.add_argument("--from-dir", default="",
                        help="build from a libretro-database checkout or a carried copy")
    parser.add_argument("--ref", default="master", help="git branch of the catalogue (with --fetch)")
    parser.add_argument("--cache", default="", help="where a fetch is kept (default: a temp folder)")
    parser.add_argument("--out", default="", help="where to write the database (default: the box's)")
    parser.add_argument("--force", action="store_true", help="rebuild even if the file is recent")
    parser.add_argument("--status", action="store_true", help="say what the database holds and stop")
    parser.add_argument("--self-test", action="store_true",
                        help="prove the reader and the builder on synthetic files, then stop")
    parser.add_argument("--quiet", action="store_true", help="say nothing unless something is wrong")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = _parser().parse_args(argv)
    if args.self_test:
        ok, lines = self_test()
        if not args.quiet:
            for line in lines:
                print(line)
            print("Self-test %s." % ("passed" if ok else "FAILED"))
        elif not ok:
            for line in lines:
                print(line)
            print("Self-test FAILED.")
        return 0 if ok else 1
    if args.status:
        return _status(args.out)

    target = Path(args.out) if args.out else romdb.db_path()
    quiet = args.quiet
    log = (lambda _msg: None) if quiet else print

    if not args.force and target.is_file() and romdb.may_reuse(target):
        if not quiet:
            print("%s was built recently; nothing to do (--force to rebuild)." % target)
        return 0

    temp_dir: Optional[str] = None
    written = Path("")
    try:
        source: Optional[Path]
        if args.from_dir:
            source = Path(args.from_dir)
        elif args.fetch or not target.is_file():
            temp_dir = tempfile.mkdtemp(prefix="ark-romdb-src-")
            cache = Path(args.cache) if args.cache else Path(temp_dir) / "libretro-database"
            log("Fetching the catalogue (only the metadata folder)…")
            source = fetch(cache, ref=args.ref, log=log)
        else:
            source = None
        if source is None or not source.is_dir():
            print("Not done: this box needs either --from-dir or a catalogue it can fetch.")
            return 1
        written = build(source, target, log=log)
    except SystemExit as exc:
        print(str(exc))
        return 1
    finally:
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)

    if not quiet:
        print("Done.  %s is ready to recognise game files." % written)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())





