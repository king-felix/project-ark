"""Project ARK — recognising a game file by the bytes inside it.

A game file does not need a human to name it.  Every cartridge ever dumped for
the consoles this box can play is listed in the *offline game-name database*
(``romdb.sqlite``), keyed by three fingerprints: CRC-32, MD5 and SHA-1.  This
module is the only reader of that database, and it is deliberately boring:

* it **reads** — nothing here writes a game, a row or a folder;
* every lookup is a single indexed ``SELECT`` on a small file, so it works with
  no internet, no service and no waiting;
* the answer is a plain dict of facts, or ``None``.  A miss is not an error.

Two things make the difference between "matches nothing" and "matches almost
everything", and both live here:

1. **Headers.**  An iNES file carries 16 bytes of emulator header that are not
   part of the cartridge; a SNES copier image carries 512 bytes; a 7800 or Lynx
   header carries 128 or 64.  The database lists the *carted* dump, so the raw
   bytes of the owner's file hash differently.  ``strip_headers()`` answers every
   candidate view of the same file (as it is, and without its header) and the
   lookup tries them all — strongest hash first, SHA-1 then MD5 then CRC-32.
2. **Archives.**  A file that arrives as a ``.zip`` is hashed from inside the
   archive, because the archive's own bytes are meaningless and the file inside
   is what gets played.

A filename is only ever a *hint*: ``system_from_name()`` and
``title_from_filename()`` are the fallback when the database has never heard of
the file, and they are labelled as guesses on every page that shows them.
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import time
import zipfile
import zlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

#: Where the database lives on the box.  ``ARK_ROMDB`` is the seam the tests and
#: a rebuild use; the default is beside the other box-wide state.
DB_NAME = "romdb.sqlite"

#: The layout of the tables below.  A build with a different number is refused by
#: the reader rather than half-understood (``ark_romdb_build.FORMAT``).
FORMAT = 1

CHUNK = 1 << 20

#: The same ceiling the installer enforces on a game file.  Nothing bigger is
#: read into a hash: a cartridge is not a video.
MAX_ROM = 64 * 1024 * 1024

#: The consoles this box can actually play (the keys of ``ark_arcade.SYSTEMS``).
#: Kept as a literal so the builder never needs the service's imports; a test
#: proves the two sets are equal, so they cannot drift apart.
PLAYABLE = ("gb", "gbc", "gba", "nes", "snes", "genesis")

#: libretro-database's ``metadat/no-intro`` file stem -> (our key, the label a
#: reader understands).  A console that is not here is not in the database at
#: all — a list nobody can play from is noise on a box with a small card.
SYSTEM_DATS = {
    "Arduboy Inc - Arduboy": ("arduboy", "Arduboy"),
    "Atari - 2600": ("a2600", "Atari 2600"),
    "Atari - 5200": ("a5200", "Atari 5200"),
    "Atari - 7800": ("a7800", "Atari 7800"),
    "Atari - 8-bit Family": ("atari8", "Atari 8-bit"),
    "Atari - Jaguar": ("jaguar", "Atari Jaguar"),
    "Atari - Lynx": ("lynx", "Atari Lynx"),
    "Atari - ST": ("atarist", "Atari ST"),
    "Bandai - WonderSwan Color": ("wsc", "WonderSwan Color"),
    "Bandai - WonderSwan": ("ws", "WonderSwan"),
    "Benesse - Pocket Challenge V2": ("pocketchallenge", "Pocket Challenge V2"),
    "Casio - Loopy": ("loopy", "Casio Loopy"),
    "Casio - PV-1000": ("pv1000", "Casio PV-1000"),
    "Coleco - ColecoVision": ("colecovision", "ColecoVision"),
    "Commodore - 64": ("c64", "Commodore 64"),
    "Commodore - Amiga": ("amiga", "Commodore Amiga"),
    "Commodore - Plus-4": ("plus4", "Commodore Plus/4"),
    "Commodore - VIC-20": ("vic20", "Commodore VIC-20"),
    "Emerson - Arcadia 2001": ("arcadia2001", "Arcadia 2001"),
    "Entex - Adventure Vision": ("adventurevision", "Adventure Vision"),
    "Epoch - Super Cassette Vision": ("scv", "Super Cassette Vision"),
    "Fairchild - Channel F": ("channelf", "Fairchild Channel F"),
    "Funtech - Super Acan": ("superacan", "Super A'can"),
    "GCE - Vectrex": ("vectrex", "Vectrex"),
    "GamePark - GP32": ("gp32", "GP32"),
    "Hartung - Game Master": ("gamemaster", "Game Master"),
    "Interton - VC 4000": ("vc4000", "VC 4000"),
    "Konami - Picno": ("picno", "Konami Picno"),
    "Magnavox - Odyssey2": ("odyssey2", "Magnavox Odyssey 2"),
    "Mattel - Intellivision": ("intellivision", "Intellivision"),
    "Microsoft - MSX": ("msx", "MSX"),
    "Microsoft - MSX2": ("msx2", "MSX2"),
    "NEC - PC Engine - TurboGrafx 16": ("pce", "PC Engine / TurboGrafx-16"),
    "NEC - PC Engine SuperGrafx": ("sgx", "PC Engine SuperGrafx"),
    "Nintendo - Family Computer Disk System": ("fds", "Famicom Disk System"),
    "Nintendo - Game Boy Advance": ("gba", "Game Boy Advance"),
    "Nintendo - Game Boy Color": ("gbc", "Game Boy Color"),
    "Nintendo - Game Boy": ("gb", "Game Boy"),
    "Nintendo - Nintendo 64": ("n64", "Nintendo 64"),
    "Nintendo - Nintendo 64DD": ("n64dd", "Nintendo 64DD"),
    "Nintendo - Nintendo DS": ("nds", "Nintendo DS"),
    "Nintendo - Nintendo DSi": ("ndsi", "Nintendo DSi"),
    "Nintendo - Nintendo Entertainment System": ("nes", "Nintendo Entertainment System"),
    "Nintendo - Pokemon Mini": ("pokemonmini", "Pokemon Mini"),
    "Nintendo - Satellaview": ("satellaview", "Satellaview"),
    "Nintendo - Sufami Turbo": ("sufamiturbo", "Sufami Turbo"),
    "Nintendo - Super Nintendo Entertainment System": ("snes", "Super Nintendo"),
    "Nintendo - Virtual Boy": ("virtualboy", "Virtual Boy"),
    "Philips - Videopac+": ("videopac", "Videopac+"),
    "RCA - Studio II": ("studio2", "RCA Studio II"),
    "SNK - Neo Geo Pocket Color": ("ngpc", "Neo Geo Pocket Color"),
    "SNK - Neo Geo Pocket": ("ngp", "Neo Geo Pocket"),
    "Sega - 32X": ("sega32x", "Sega 32X"),
    "Sega - Beena": ("beena", "Sega Beena"),
    "Sega - Game Gear": ("gamegear", "Game Gear"),
    "Sega - Master System - Mark III": ("mastersystem", "Master System"),
    "Sega - Mega Drive - Genesis": ("genesis", "Mega Drive / Genesis"),
    "Sega - PICO": ("pico", "Sega PICO"),
    "Sega - SG-1000": ("sg1000", "SG-1000"),
    "Sharp - X1": ("x1", "Sharp X1"),
    "Sharp - X68000": ("x68000", "Sharp X68000"),
    "Sinclair - ZX Spectrum +3": ("zxspectrum", "ZX Spectrum +3"),
    "Tiger - Game.com": ("gamecom", "Game.com"),
    "VTech - CreatiVision": ("creativision", "CreatiVision"),
    "VTech - V.Smile": ("vsmile", "V.Smile"),
    "Watara - Supervision": ("supervision", "Watara Supervision"),
}

#: Deliberately **not** in the database, and the reason is size, not snobbery:
#: disc images, download-only stores and toys whose files are not cartridges.
#: Keeping them out roughly halves the build; a console nobody can play here
#: does not need thousands of rows on a card that also holds the library.
SKIP_DATS = (
    "LeapFrog - LeapPad",
    "LeapFrog - Leapster Learning Game System",
    "Microsoft - XBOX 360 (Games on Demand)",
    "Microsoft - XBOX 360 (Title Updates)",
    "Microsoft - Xbox 360 (Digital)",
    "Microsoft - Xbox 360",
    "Mobile - J2ME",
    "Mobile - Palm OS",
    "Mobile - Symbian",
    "Mobile - Zeebo",
    "Nintendo - New Nintendo 3DS (Digital)",
    "Nintendo - New Nintendo 3DS",
    "Nintendo - Nintendo 3DS (Digital)",
    "Nintendo - Nintendo 3DS",
    "Nintendo - Nintendo DS (Download Play)",
    "Nintendo - Wii (Digital)",
    "Nintendo - Wii U (Digital)",
    "Nintendo - e-Reader",
    "Sony - PlayStation 3 (PSN)",
    "Sony - PlayStation Portable (PSN)",
    "Sony - PlayStation Portable (PSX2PSP)",
    "Sony - PlayStation Portable (UMD Music)",
    "Sony - PlayStation Portable (UMD Video)",
    "Sony - PlayStation Portable",
    "Sony - PlayStation Vita (PSN)",
    "Sony - PlayStation Vita",
)

#: The metadata sidecars: the folder in ``metadat/``, the field name inside the
#: DAT, and the column it fills.  ``releasemonth`` is joined but not stored: a
#: month is noise on a page that says "1985".  (``maxusers`` files carry a
#: ``users`` field — the folder name and the field name are not the same thing.)
META_SOURCES = (
    ("developer", "developer", "developer"),
    ("publisher", "publisher", "publisher"),
    ("releaseyear", "releaseyear", "year"),
    ("releasemonth", "releasemonth", ""),
    ("genre", "genre", "genre"),
    ("maxusers", "users", "players"),
    ("franchise", "franchise", "franchise"),
    ("esrb", "esrb_rating", "esrb"),
)


#: Extensions this box can name from the file alone.  ``.bin`` is absent on
#: purpose: two consoles share it, so guessing would be a lie.
EXT_SYSTEM = {
    ".gb": "gb", ".gbc": "gbc", ".gba": "gba",
    ".nes": "nes", ".fds": "fds",
    ".sfc": "snes", ".smc": "snes", ".swc": "snes", ".fig": "snes",
    ".md": "genesis", ".gen": "genesis", ".smd": "genesis",
    ".a26": "a2600", ".a52": "a5200", ".a78": "a7800",
    ".lnx": "lynx", ".gg": "gamegear", ".sms": "mastersystem",
    ".pce": "pce", ".sgx": "sgx", ".vb": "virtualboy",
    ".n64": "n64", ".z64": "n64", ".nds": "nds", ".dsi": "ndsi",
    ".ws": "ws", ".wsc": "wsc", ".ngp": "ngp", ".ngc": "ngpc",
    ".col": "colecovision", ".int": "intellivision", ".vec": "vectrex",
    ".j64": "jaguar", ".jag": "jaguar",
    ".min": "pokemonmini", ".pmin": "pokemonmini",
    ".scv": "scv", ".vc": "vc4000", ".chf": "channelf",
}

#: Every extension inside an archive is looked at, and only these are played.
ZIP_EXTS = tuple(sorted(set(EXT_SYSTEM) | {".zip"}))

#: Parenthesised tags a No-Intro name hangs off the title.  Only these count as a
#: region; anything else in brackets is a note ("Rev 1", "Beta", "En,Fr,De").
REGION_WORDS = (
    "World", "USA", "Europe", "Japan", "Australia", "Brazil", "Canada", "China",
    "France", "Germany", "Hong Kong", "Italy", "Korea", "Netherlands", "Russia",
    "Spain", "Sweden", "Taiwan", "Asia", "UK", "Finland", "Denmark", "Norway",
    "Poland", "Portugal", "India", "New Zealand", "Argentina", "Mexico",
    "Belgium", "Austria", "Switzerland", "Greece", "Israel", "South Africa",
    "Ireland", "Czech Republic", "Hungary", "Croatia", "Serbia", "Turkey",
)

_TAG_RE = re.compile(r"\s*[\(\[]([^\)\]]*)[\)\]]")


# ---------------------------------------------------------------------------
#  where the database is
# ---------------------------------------------------------------------------

def root() -> Path:
    """The box root, read at call time (tests point ``ARK_ROOT`` elsewhere)."""
    return Path(os.environ.get("ARK_ROOT", "/srv/ark"))


def db_path() -> Path:
    """The database file: ``ARK_ROMDB`` if set, else ``<box>/romdb.sqlite``."""
    override = os.environ.get("ARK_ROMDB", "")
    if override:
        return Path(override)
    return root() / DB_NAME


def connect() -> Optional[sqlite3.Connection]:
    """A read-only connection, or ``None`` when there is no usable database.

    Never raises: a box whose database has not been built yet is a box that
    falls back to filenames, not a box that breaks.
    """
    path = db_path()
    if not path.is_file():
        return None
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % path, uri=True, timeout=2)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error:
        return None


_STATS_CACHE: Dict[Any, Dict[str, Any]] = {}


def forget_stats() -> None:
    """Drop the memoised stats answer (a rebuild, or a test that moves a file)."""
    _STATS_CACHE.clear()


def stats() -> Dict[str, Any]:
    """What is in the database, in words a page can print.  Never raises.

    Memoised on ``(path, size, mtime)``: a page may ask on every request, and
    three ``COUNT(*)`` scans over 125 k rows is waste when nothing changed.
    """
    path = db_path()
    try:
        info = path.stat()
        stamp: Any = (str(path), info.st_size, int(info.st_mtime))
    except OSError:
        stamp = (str(path), 0, 0)
    cached = _STATS_CACHE.get(stamp)
    if cached is not None:
        return dict(cached)
    answer = _stats_now(path)
    _STATS_CACHE.clear()
    _STATS_CACHE[stamp] = dict(answer)
    return answer


def _stats_now(path: Path) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "present": False, "rows": 0, "systems": 0, "playable_rows": 0,
        "built": "", "source_date": "", "source_commit": "", "format": FORMAT,
        "problem": "",
    }
    if not path.is_file():
        out["problem"] = "not built yet"
        return out
    conn = connect()
    if conn is None:
        out["problem"] = "cannot be opened"
        return out
    try:
        meta: Dict[str, str] = {}
        for row in conn.execute("SELECT key, value FROM meta"):
            meta[str(row["key"])] = str(row["value"])
        out["built"] = meta.get("built", "")
        out["source_date"] = meta.get("source_date", "")
        out["source_commit"] = meta.get("source_commit", "")
        try:
            out["format"] = int(meta.get("format", FORMAT))
        except (TypeError, ValueError):
            out["format"] = 0
        out["rows"] = int(conn.execute("SELECT COUNT(*) FROM roms").fetchone()[0])
        out["systems"] = int(
            conn.execute("SELECT COUNT(DISTINCT system) FROM roms").fetchone()[0]
        )
        out["playable_rows"] = int(
            conn.execute("SELECT COUNT(*) FROM roms WHERE playable = 1").fetchone()[0]
        )
        out["present"] = out["rows"] > 0
        if out["format"] != FORMAT:
            out["problem"] = "built by another version of the box"
    except sqlite3.Error as exc:
        out["problem"] = "cannot be read (%s)" % exc.__class__.__name__
    finally:
        conn.close()
    return out


# ---------------------------------------------------------------------------
#  hashing a file the way the database expects it
# ---------------------------------------------------------------------------

def strip_headers(head: bytes, filename: str, size: int) -> List[Tuple[str, int]]:
    """Every view of this file worth hashing: ``[(label, bytes to skip)]``.

    The first entry is always the file untouched.  The others remove the
    emulator header a dump may carry, because the database lists the cartridge
    without it.
    """
    views: List[Tuple[str, int]] = [("as it is", 0)]
    ext = Path(filename).suffix.lower()
    if head[:4] == b"NES\x1a":
        views.append(("without its 16-byte Nintendo header", 16))
    elif ext == ".fds" and head[:4] == b"FDS\x1a":
        views.append(("without its 16-byte disk header", 16))
    if ext in (".sfc", ".smc", ".swc", ".fig") and size > 512 and size % 1024 == 512:
        views.append(("without its 512-byte copier header", 512))
    if ext == ".a78" and size > 128 and head[:10] == b"\x01ATARI7800":
        views.append(("without its 128-byte header", 128))
    if ext == ".lnx" and size > 64 and head[:4] == b"LYNX":
        views.append(("without its 64-byte header", 64))
    return views


def _hash_stream(fh, skip: int = 0) -> Dict[str, Any]:
    """CRC-32, MD5 and SHA-1 of a stream, optionally skipping a header."""
    crc = 0
    md5 = hashlib.md5()
    sha1 = hashlib.sha1()
    left = int(skip)
    total = 0
    while True:
        block = fh.read(CHUNK)
        if not block:
            break
        if left:
            take = min(left, len(block))
            left -= take
            block = block[take:]
            if not block:
                continue
        total += len(block)
        crc = zlib.crc32(block, crc)
        md5.update(block)
        sha1.update(block)
    return {
        "size": total,
        "crc32": "%08x" % (crc & 0xFFFFFFFF),
        "md5": md5.hexdigest(),
        "sha1": sha1.hexdigest(),
    }


def _candidates_from(stream_factory, filename: str, size: int) -> List[Dict[str, Any]]:
    """Hash every header view of one file."""
    opener = stream_factory()
    try:
        head = opener.read(512) or b""
    finally:
        opener.close()
    out = []
    for label, skip in strip_headers(head, filename, size):
        fh = stream_factory()
        try:
            facts = _hash_stream(fh, skip)
        finally:
            fh.close()
        facts["label"] = label
        facts["header_bytes"] = skip
        out.append(facts)
    return out


def _zip_member(path: Path) -> Optional[str]:
    """The entry inside a ``.zip`` worth hashing: the biggest playable one."""
    try:
        with zipfile.ZipFile(path) as zf:
            best: Optional[Tuple[int, str]] = None
            for info in zf.infolist():
                if info.is_dir():
                    continue
                name = os.path.basename(info.filename)
                ext = Path(name).suffix.lower()
                if ext not in ZIP_EXTS or ext == ".zip":
                    continue
                if best is None or info.file_size > best[0]:
                    best = (info.file_size, info.filename)
            return best[1] if best else None
    except (zipfile.BadZipFile, OSError, RuntimeError):
        return None


def identify_path(path: Path, filename: str = "") -> Dict[str, Any]:
    """Fingerprint a file on disk (or the game inside a ``.zip``).

    Returns ``candidates`` (one per header view, strongest first), a
    ``system_hint`` from the extension, and the size/entry facts.  It does
    **not** touch the database: :func:`match` does that.
    """
    path = Path(path)
    shown = filename or path.name
    out: Dict[str, Any] = {
        "filename": shown, "path": str(path), "entry": "", "size": 0,
        "system_hint": system_from_name(shown), "candidates": [], "problem": "",
    }
    if not path.is_file():
        out["problem"] = "that file is not there"
        return out
    try:
        out["size"] = path.stat().st_size
    except OSError:
        out["problem"] = "that file cannot be read"
        return out
    if out["size"] > MAX_ROM:
        out["problem"] = "that file is bigger than a cartridge"
        return out

    if path.suffix.lower() == ".zip":
        entry = _zip_member(path)
        if not entry:
            out["problem"] = "that archive holds no game file this box can play"
            return out
        out["entry"] = os.path.basename(entry)
        try:
            with zipfile.ZipFile(path) as zf:
                inner_size = zf.getinfo(entry).file_size
        except (KeyError, zipfile.BadZipFile, OSError):
            inner_size = out["size"]

        def opener() -> Any:
            return zipfile.ZipFile(path).open(entry)

        try:
            out["candidates"] = _candidates_from(opener, out["entry"], inner_size)
        except (zipfile.BadZipFile, OSError, RuntimeError, EOFError):
            out["problem"] = "that archive cannot be read"
            return out
        out["size"] = inner_size
        out["system_hint"] = system_from_name(out["entry"]) or out["system_hint"]
        return out

    def plain() -> Any:
        return path.open("rb")

    try:
        out["candidates"] = _candidates_from(plain, shown, out["size"])
    except OSError:
        out["problem"] = "that file cannot be read"
    return out


def identify_bytes(data: bytes, filename: str = "game") -> Dict[str, Any]:
    """The same answer for bytes already in memory (tests, small imports)."""
    import io

    out: Dict[str, Any] = {
        "filename": filename, "path": "", "entry": "", "size": len(data),
        "system_hint": system_from_name(filename), "candidates": [], "problem": "",
    }
    if len(data) > MAX_ROM:
        out["problem"] = "that file is bigger than a cartridge"
        return out

    def opener() -> Any:
        return io.BytesIO(data)

    out["candidates"] = _candidates_from(opener, filename, len(data))
    return out


# ---------------------------------------------------------------------------
#  looking it up
# ---------------------------------------------------------------------------

def lookup(
    conn: Optional[sqlite3.Connection],
    *,
    sha1: str = "",
    md5: str = "",
    crc32: str = "",
) -> Optional[Dict[str, Any]]:
    """One indexed lookup by whichever fingerprint was given (strongest first)."""
    if conn is None:
        return None
    for column, value in (("sha1", sha1), ("md5", md5), ("crc32", crc32)):
        value = (value or "").strip().lower()
        if not value:
            continue
        try:
            row = conn.execute(
                "SELECT * FROM roms WHERE %s = ? ORDER BY playable DESC, id LIMIT 1" % column,
                (value,),
            ).fetchone()
        except sqlite3.Error:
            return None
        if row is not None:
            found = dict(row)
            found["matched_by"] = column
            return found
    return None


def match(ident: Dict[str, Any], conn: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
    """Match an :func:`identify_path` answer against the database.

    SHA-1 is asked first for every view, then MD5, then CRC-32: a 32-bit sum can
    collide, a SHA-1 digest does not in practice, so the strong answer wins even
    when a weaker one would have been found first.
    """
    own = conn is None
    if conn is None:
        conn = connect()
    try:
        for column in ("sha1", "md5", "crc32"):
            for cand in ident.get("candidates") or []:
                row = lookup(conn, **{column: cand.get(column, "")})
                if row:
                    row["hash_label"] = cand.get("label", "")
                    row["hash_value"] = cand.get(column, "")
                    return row
    finally:
        if own and conn is not None:
            conn.close()
    return {}


def game_by_identity(
    conn: sqlite3.Connection, ident: Dict[str, Any], *, not_slug: str = ""
) -> Optional[Dict[str, Any]]:
    """A game already on the shelf with these very bytes, if there is one."""
    for column in ("sha1", "md5", "crc32"):
        for cand in ident.get("candidates") or []:
            value = (cand.get(column) or "").lower()
            if not value:
                continue
            try:
                row = conn.execute(
                    "SELECT * FROM games WHERE lower(%s) = ? AND slug <> ? "
                    "ORDER BY id LIMIT 1" % column,
                    (value, not_slug or ""),
                ).fetchone()
            except sqlite3.Error:
                return None
            if row is not None:
                return dict(row)
    return None


# ---------------------------------------------------------------------------
#  names, when the database has never heard of the file
# ---------------------------------------------------------------------------

def _stem(name: str) -> str:
    base = os.path.basename(str(name or "").strip())
    ext = Path(base).suffix.lower()
    if ext in EXT_SYSTEM or ext == ".zip":
        base = base[: -len(ext)]
    return base


def clean_title(name: str) -> str:
    """'Super Mario Bros. (World) (Rev 1)' -> 'Super Mario Bros.'"""
    base = _stem(name)
    title = _TAG_RE.sub("", " " + base).strip()
    title = re.sub(r"\s{2,}", " ", title)
    return title or base or str(name or "").strip()


def title_from_filename(name: str) -> str:
    """A serviceable title for a file nobody has ever seen before."""
    title = clean_title(name).replace("_", " ")
    return re.sub(r"\s{2,}", " ", title).strip()


def tags_of(name: str) -> List[str]:
    """Every parenthesised tag in a name, in order."""
    return [m.group(1).strip() for m in _TAG_RE.finditer(" " + _stem(name))]


def region_of(name: str) -> str:
    """The regions a name declares ('USA, Europe'), or '' when it says nothing."""
    found: List[str] = []
    for tag in tags_of(name):
        for part in re.split(r"[,/]", tag):
            part = part.strip()
            if part in REGION_WORDS and part not in found:
                found.append(part)
    return ", ".join(found)


def system_from_name(name: str) -> str:
    """The console an extension implies, or '' when it implies nothing."""
    return EXT_SYSTEM.get(Path(os.path.basename(str(name or ""))).suffix.lower(), "")


def system_label(system: str) -> str:
    """A console key in words, for a page."""
    for key, label in SYSTEM_DATS.values():
        if key == system:
            return label
    return (system or "").upper()


def describe(ident: Dict[str, Any], found: Optional[Dict[str, Any]] = None) -> str:
    """One plain-English sentence about what was recognised."""
    if found:
        bits = [system_label(found.get("system", "")) or "a console"]
        if found.get("year"):
            bits.append(str(found["year"]))
        if found.get("publisher") or found.get("developer"):
            bits.append(str(found.get("publisher") or found.get("developer")))
        title = found.get("title") or found.get("name") or "a game"
        return "%s — %s (%s)" % (
            title, found.get("region") or "region not stated",
            ", ".join(b for b in bits if b),
        )
    if ident.get("problem"):
        return ident["problem"]
    hint = ident.get("system_hint") or ""
    if hint:
        return "nobody has listed these bytes, but the name looks like %s" % (
            system_label(hint) or hint
        )
    return "nobody has listed these bytes and the name says nothing"


def may_reuse(path: Optional[Path] = None, *, stale_days: int = 120) -> bool:
    """A rough "may this build be reused?" clock, used by the builder."""
    path = Path(path) if path else db_path()
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        return False
    return age < stale_days * 86400



