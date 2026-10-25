"""Project ARK — finding and installing games without a human guessing.

Only **one** place is allowed to put a game file on this shelf, and it is this
module.  Every file travels the same road:

1. every candidate is recorded with whatever its catalogue entry says about the
   licence (``ark_arcade.license_problem``) — that text is provenance on the
   game's own page, not a door: the owner decided on 25 Oct 2026 that the shelf
   is built from what is good to play;
2. the download URL is **built** here from a validated entry id and filename,
   never taken from the response, and must still be under the one curated
   source when the first byte arrives;
3. the bytes are capped, written to ``<ARK_ARCADE_DATA>/tmp``, fsynced, and
   hashed before anything can be published;
4. the file must look like the console it claims (``rom_facts``);
5. only then is it published with ``os.replace`` and recorded in ``games`` and
   ``game_revisions``.

Nothing here trusts a page, a redirect or a filename.  What a source says about a
licence is recorded beside the game and shown to the owner, not used to refuse it.
"""

from __future__ import annotations

import errno
import json
import os
import re
import shutil
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import ark_arcade as arc
import ark_db

SOURCE_API = "https://hh3.gbdev.io/api"
SOURCE_RAW = "https://raw.githubusercontent.com/gbdev/database/master/entries/"
# Each console's entries live in its own community repository — gbdev for the two
# Game Boys, gbadev-org for Advance, nesdev-org for the NES.  One prefix meant a
# GBA or NES candidate was offered an address that answers 404: the box could name
# the game and never fetch it.
SOURCE_RAW_GBA = "https://raw.githubusercontent.com/gbadev-org/games/master/entries/"
SOURCE_RAW_NES = "https://raw.githubusercontent.com/nesdev-org/homebrew-db/master/entries/"
SOURCE_RAW_ALL = (SOURCE_RAW, SOURCE_RAW_GBA, SOURCE_RAW_NES)
# Console -> the one repository that holds its entries.  A console with no entry
# here is not fetched at all: the address is never guessed from a filename, and
# SNES and Genesis have nothing in this catalogue yet.
SOURCE_RAW_BY_SYSTEM = {
    "gb": SOURCE_RAW, "gbc": SOURCE_RAW,
    "gba": SOURCE_RAW_GBA, "nes": SOURCE_RAW_NES,
}
MAX_DOWNLOAD = 64 * 1024 * 1024       # a homebrew cartridge, not a video
MIN_FREE_AFTER_INSTALL = 64 * 1024 * 1024   # the card must still have room to live
HTTP_TIMEOUT = 20
PROVIDER_ID = "homebrew-hub"
CATALOG_QUERIES = ("", "puzzle", "platform", "action", "adventure")
DETAIL_LOOKUPS = 48        # per discovery run; each is one small game.json


class InstallRefused(RuntimeError):
    """The candidate is not something this box may publish."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is not a download; it is a change of subject."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _raw_prefix(system: Any) -> str:
    """The trusted repository that holds this console's entries, or "" (none)."""
    return SOURCE_RAW_BY_SYSTEM.get(str(system or "").strip().lower(), "")


def _trusted(url: str) -> bool:
    """True for the catalogue API and either curated repository, and nothing else."""
    text = str(url or "")
    return text.startswith(SOURCE_API + "/") or any(text.startswith(p) for p in SOURCE_RAW_ALL)


def _get(url: str, limit: int, timeout: int = HTTP_TIMEOUT) -> bytes:
    if not _trusted(url):
        raise InstallRefused("That address is not one of the game sources this box trusts.")
    request = urllib.request.Request(url, headers={"User-Agent": "ARK-Arcade/1"})
    opener = urllib.request.build_opener(_NoRedirect)
    with opener.open(request, timeout=timeout) as response:
        final = response.geturl()
        if not _trusted(final):
            raise InstallRefused("The source tried to send this box somewhere else.")
        chunks: List[bytes] = []
        total = 0
        while True:
            chunk = response.read(64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise InstallRefused("That download is larger than this box will accept.")
            chunks.append(chunk)
    return b"".join(chunks)


def _json(url: str) -> Any:
    raw = _get(url, 2 * 1024 * 1024)
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InstallRefused("The source did not send something this box could read.") from exc


def _entry_id(entry: Dict[str, Any]) -> str:
    """The source's own slug.  It may contain an underscore; the game's local
    slug is made separately from it, so the download address still names the
    file the source actually has."""
    raw = str(entry.get("slug") or entry.get("id") or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", raw):
        return ""
    return raw


def _default_file(entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    files = entry.get("files") or []
    if not isinstance(files, list):
        return None
    usable = [f for f in files if isinstance(f, dict) and f.get("filename")]
    for f in usable:
        if f.get("default") and f.get("playable", True) is not False:
            return f
    for f in usable:
        if f.get("playable", True) is not False:
            return f
    return None


def _source_file(raw: str) -> Optional[str]:
    """A file path inside one catalogue entry, or None.

    The API writes paths such as ``files/drakoko.gbc``.  Only a bare name or one
    ``files/`` level is accepted; anything else (a climb, a second folder, a
    hidden file, a URL) is refused before it can reach a download address.
    """
    text = (raw or "").replace("\\", "/").strip()
    parts = text.split("/")
    if len(parts) == 1:
        name = parts[0]
    elif len(parts) == 2 and parts[0] == "files":
        name = parts[1]
    else:
        return None
    if not name or name.startswith(".") or name in (".", ".."):
        return None
    if any(part in ("", ".", "..") for part in parts):
        return None
    return text


#: Extensions that name exactly one console.  ``.bin`` is deliberately absent:
#: it is used by more than one machine, so it is not a name this box will guess.
_EXT_SYSTEM = {
    "gb": "gb", "gbc": "gbc", "gba": "gba", "nes": "nes",
    "sfc": "snes", "smc": "snes", "md": "genesis", "gen": "genesis",
}


def _system(entry: Dict[str, Any], filename: str) -> str:
    raw = str(entry.get("platform") or entry.get("system") or "").strip().lower()
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if raw == "gb":
        return "gbc" if ext == "gbc" else "gb"
    if raw == "gbc":
        return "gbc"
    if raw in ("nes", "snes", "gba"):
        return raw
    if raw in ("genesis", "megadrive"):
        return "genesis"
    # A search answer sometimes leaves the platform out.  An extension that names
    # exactly one console is enough to name the game; an extension shared by two
    # consoles is not, and stays unnamed rather than guessed.
    return _EXT_SYSTEM.get(ext, "")


LANGUAGE_NEUTRAL = {"neutral", "none", "no-text", "no_text", "wordless", "silent"}
LANGUAGE_ENGLISH = {"en", "eng", "english"}
LANGUAGE_NAMES = {
    "en": "English", "eng": "English", "english": "English",
    "ja": "Japanese", "jp": "Japanese", "jpn": "Japanese", "japanese": "Japanese",
    "fr": "French", "de": "German", "es": "Spanish", "it": "Italian",
    "pt": "Portuguese", "nl": "Dutch", "sv": "Swedish", "pl": "Polish",
    "ru": "Russian", "zh": "Chinese", "ko": "Korean",
}


def _language_values(raw: Any) -> List[str]:
    """Read whatever shape a catalogue uses for languages, without inventing one."""
    values: List[str] = []
    if isinstance(raw, dict):
        for key, value in raw.items():
            # A source may write {"en": 100} or {"English": true}; either names
            # a language.  A false/zero value says it is not present.
            if value not in (False, 0, None, ""):
                values.append(str(key))
    elif isinstance(raw, (list, tuple, set)):
        for item in raw:
            if isinstance(item, dict):
                values.extend(_language_values(item))
            elif item not in (None, ""):
                values.extend(_language_values(item))
    elif raw not in (None, ""):
        # A stored candidate holds the languages as one joined string
        # ("en, ja"), and a catalogue may use any of these separators.  Split
        # them here so multilingual English is recognised as English rather
        # than becoming an unknown language called "en, ja".
        for part in re.split(r"[,;|/+]", str(raw)):
            if part.strip():
                values.append(part.strip())
    out: List[str] = []
    for value in values:
        cleaned = value.strip().lower()
        # A neutral marker is a whole value, not a language with a region tag:
        # splitting "no-text" on the hyphen would leave "no" and lose the one
        # thing the source actually said.
        code = cleaned if cleaned in LANGUAGE_NEUTRAL else cleaned.replace("_", "-").split("-", 1)[0]
        if code and code not in out:
            out.append(code)
    return out


def _language_conflict(entry: Dict[str, Any]) -> bool:
    marker = entry.get("languageConflict", entry.get("language_conflict"))
    if marker is True or str(marker).strip().lower() in ("1", "true", "yes", "conflict"):
        return True
    return False


def language_label(code: str) -> str:
    if code in LANGUAGE_NEUTRAL:
        return "no words"
    return LANGUAGE_NAMES.get(code, code.upper() or "not stated")


def language_facts(candidate: Dict[str, Any]) -> Tuple[bool, str]:
    """Eligibility by language: a game the household cannot read is held back.

    The household reads English.  A game with no words at all is fine, and a
    game whose language the source never recorded is no longer refused for that
    silence — the curated catalogue is homebrew, and most of its entries say
    nothing about language.  What *is* refused is a game the source says is in
    another language, and a source that contradicts itself.
    """
    if candidate.get("language_conflict"):
        return False, "the source says two different things about this game's language"
    values = _language_values(candidate.get("language"))
    if not values:
        return True, ""
    if LANGUAGE_NEUTRAL & set(values):
        return True, ""
    if LANGUAGE_ENGLISH & set(values):
        return True, ""
    spoken = ", ".join(language_label(v) for v in values[:3])
    return False, f"the game is in {spoken}, and this box offers English games"


def rights_facts(candidate: Dict[str, Any]) -> Tuple[bool, str]:
    """Kept for the record, never a refusal.

    This used to hold a game back when the source said nothing about the right to
    copy it.  The owner's decision (25 Oct 2026) is that licences are information,
    so whatever the source says here is stored beside the game and asked no
    further questions.  The function stays so callers and tests share one name.
    """
    return True, ""


def _entry_language(entry: Dict[str, Any]) -> Tuple[str, str]:
    raw = (entry.get("languages") or entry.get("language") or entry.get("lang")
           or entry.get("gameLanguage") or "")
    values = _language_values(raw)
    evidence = str(entry.get("languageEvidence") or entry.get("language_evidence") or "").strip()
    if not evidence:
        evidence = ("the source entry does not state a language"
                    if not values else f"the source entry says {', '.join(values)}")
    return ", ".join(values)[:80], evidence[:300]


def _entry_rights(entry: Dict[str, Any]) -> str:
    return str(entry.get("rightsEvidence") or entry.get("rights_evidence") or "")[:300]


def entry_to_candidate(entry: Dict[str, Any],
                       provider: str = PROVIDER_ID) -> Optional[Dict[str, Any]]:
    """Turn a catalogue entry into a row, or None if it cannot even be named."""
    if not isinstance(entry, dict):
        return None
    if str(entry.get("typetag") or "game").lower() != "game":
        return None
    slug = _entry_id(entry)
    if not slug:
        return None
    file_info = _default_file(entry)
    if not file_info:
        return None
    filename = _source_file(str(file_info.get("filename") or ""))
    if not filename:
        return None
    system = _system(entry, filename)
    if not system:
        return None
    if not filename.lower().endswith(arc.SYSTEMS[system]["ext"]):
        return None
    url = (_raw_prefix(system) + urllib.parse.quote(slug, safe="") + "/"
           + urllib.parse.quote(filename, safe="/"))
    if not _raw_prefix(system):
        return None
    license_text = str(entry.get("license") or entry.get("gameLicense") or "").strip()
    title = str(entry.get("title") or entry.get("name") or slug).strip()[:120] or slug
    developer = entry.get("developer") or entry.get("author") or ""
    if isinstance(developer, list):
        names = [str(d.get("name")) for d in developer if isinstance(d, dict) and d.get("name")]
        developer = ", ".join(names)
    language, language_evidence = _entry_language(entry)
    return {
        "provider": provider,
        "entry_id": slug,
        "title": title,
        "system": system,
        "core": arc.SYSTEMS[system]["core"],
        "filename": filename,
        "download_url": url,
        "license": license_text[:80],
        "license_evidence": (f"{arc.PROVIDER_LABEL} entry {slug}: license {license_text or 'not stated'}"
                             + (f"; repository {entry.get('repository')}" if entry.get("repository") else ""))[:300],
        "rights_evidence": _entry_rights(entry),
        "language": language,
        "language_evidence": language_evidence,
        "language_conflict": _language_conflict(entry),
        "author": str(developer or "")[:80],
        "homepage": str(entry.get("repository") or entry.get("website")
                         or entry.get("gameWebsite") or entry.get("devWebsite") or "")[:200],
        "description": str(entry.get("description") or "")[:300],
        "release": str(entry.get("releaseDate") or entry.get("date")
                       or entry.get("version") or "")[:40],
        "notices": str(entry.get("license") or entry.get("gameLicense") or "")[:300],
        "provenance": f"{provider}:{slug}",
    }


def _base_problem(candidate: Dict[str, Any]) -> str:
    """The shape gates: a file must match its console and come from the source.

    A licence is recorded beside a game, not used to refuse it (§8.12, 25 Oct
    2026), so it is no longer asked about here.
    """
    system = candidate.get("system", "")
    if system not in arc.SYSTEMS:
        return "this box does not play that kind of game"
    filename = candidate.get("filename", "")
    if not filename.lower().endswith(arc.SYSTEMS[system]["ext"]):
        return "the file does not match the console it claims"
    if candidate.get("provider") != "owner":
        if not _trusted(candidate.get("download_url", "")):
            return "the download address is not the curated source"
    return ""


def review_reason(candidate: Dict[str, Any]) -> str:
    """Why a candidate needs the owner.  Language is the only remaining reason.

    A licence and the words a source keeps beside it are recorded as provenance
    (§8.12); they no longer hold a game back.  What still matters is that the
    household can read the game — see ``language_facts()``.
    """
    ok, reason = language_facts(candidate)
    if not ok:
        return reason
    return ""


def candidate_status(candidate: Dict[str, Any]) -> Tuple[str, str]:
    """``refused`` (never), ``review`` (a human must look) or ``pending`` (ready)."""
    problem = _base_problem(candidate)
    if problem:
        return "refused", problem
    problem = review_reason(candidate)
    if problem:
        return "review", problem
    return "pending", ""


def eligibility(candidate: Dict[str, Any]) -> Tuple[bool, str]:
    """The complete gate.  Anything short of this may not be published."""
    problem = _base_problem(candidate)
    if problem:
        return False, problem
    problem = review_reason(candidate)
    if problem:
        return False, problem
    return True, ""


def _upsert_candidate(conn: sqlite3.Connection, candidate: Dict[str, Any]) -> str:
    status, reason = candidate_status(candidate)
    stamp = arc.now_iso()
    with ark_db.tx(conn):
        conn.execute(
            "INSERT INTO acquisition_candidates "
            "(provider, entry_id, title, system, filename, download_url, license, license_evidence, "
            " rights_evidence, language, language_evidence, provenance, release, notices, "
            " author, homepage, status, reason, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(provider, entry_id) DO UPDATE SET "
            "  title = excluded.title, system = excluded.system, filename = excluded.filename, "
            "  download_url = excluded.download_url, license = excluded.license, "
            "  license_evidence = excluded.license_evidence, rights_evidence = excluded.rights_evidence, "
            "  language = excluded.language, language_evidence = excluded.language_evidence, "
            "  provenance = excluded.provenance, release = excluded.release, notices = excluded.notices, "
            "  author = excluded.author, "
            "  homepage = excluded.homepage, reason = excluded.reason, updated_at = excluded.updated_at, "
            "  status = CASE WHEN acquisition_candidates.game_id IS NOT NULL THEN 'installed' "
            "                ELSE excluded.status END",
            (candidate["provider"], candidate["entry_id"], candidate["title"],
             candidate["system"], candidate["filename"], candidate["download_url"],
             candidate["license"], candidate.get("license_evidence", ""),
             candidate.get("rights_evidence", ""), candidate.get("language", ""),
             candidate.get("language_evidence", ""), candidate.get("provenance", ""),
             candidate.get("release", ""), candidate.get("notices", ""),
             candidate["author"], candidate["homepage"], status, reason, stamp, stamp),
        )
    return status


def sync_catalog(conn: sqlite3.Connection, limit: int = 80) -> Dict[str, Any]:
    """Ask the curated source what it has, and remember every answer."""
    started = arc.now_iso()
    seen: Dict[str, Dict[str, Any]] = {}
    checked: set = set()
    errors: List[str] = []
    details_asked = 0
    for platform in ("GB", "GBC", "NES", "SNES", "GBA", "GENESIS"):
        for query in CATALOG_QUERIES:
            if len(seen) >= limit:
                break
            url = (SOURCE_API + "/search?" + urllib.parse.urlencode(
                {"q": query, "type": "game", "platform": platform}))
            try:
                payload = _json(url)
            except (InstallRefused, urllib.error.URLError, TimeoutError, OSError) as exc:
                errors.append(f"{platform} {query or 'all'}: {exc}")
                continue
            entries = payload.get("entries", []) if isinstance(payload, dict) else []
            for entry in entries:
                slug = _entry_id(entry)
                if not slug or slug in checked:
                    continue
                checked.add(slug)
                candidate = entry_to_candidate(entry)
                needs_detail = (candidate is None or not candidate.get("license")
                                or not candidate.get("language"))
                prefix = _raw_prefix(platform)
                if needs_detail and prefix and details_asked < DETAIL_LOOKUPS:
                    details_asked += 1
                    try:
                        detail = _json(prefix + urllib.parse.quote(slug, safe="")
                                       + "/game.json")
                        if isinstance(detail, dict):
                            merged = dict(entry)
                            merged.update({k: v for k, v in detail.items()
                                           if v not in (None, "", [], {})})
                            merged["license"] = (detail.get("license")
                                                  or detail.get("gameLicense")
                                                  or entry.get("license") or "")
                            candidate = entry_to_candidate(merged)
                    except (InstallRefused, urllib.error.URLError,
                            TimeoutError, OSError, ValueError):
                        pass          # the search row remains, refused for no licence
                if candidate and candidate["entry_id"] not in seen:
                    seen[candidate["entry_id"]] = candidate
            if not entries:
                break
    accepted = refused = review = 0
    for candidate in list(seen.values())[:limit]:
        status = _upsert_candidate(conn, candidate)
        accepted += 1 if status in ("pending", "installed") else 0
        refused += 1 if status == "refused" else 0
        review += 1 if status == "review" else 0
    summary = f"{accepted} offered, {refused} refused, {review} need a look"
    if errors:
        summary += f", {len(errors)} lookups failed"
    with ark_db.tx(conn):
        conn.execute(
            "INSERT INTO providers (id, label, kind, home, enabled, last_run_at, last_result) "
            "VALUES (?, ?, 'gbdev-database', ?, 1, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET last_run_at = excluded.last_run_at, "
            "last_result = excluded.last_result",
            (PROVIDER_ID, arc.PROVIDER_LABEL, arc.PROVIDER_HOME, arc.now_iso(), summary),
        )
    _job(conn, "discovery", summary, started)
    return {"offered": accepted, "refused": refused, "review": review,
            "seen": len(seen), "errors": errors[:5], "detail": summary}


def _job(conn: sqlite3.Connection, kind: str, detail: str, started: str,
         status: str = "finished") -> None:
    with ark_db.tx(conn):
        conn.execute(
            "INSERT INTO acquisition_jobs (kind, status, started_at, finished_at, detail) "
            "VALUES (?, ?, ?, ?, ?)",
            (kind, status, started, arc.now_iso(), detail[:500]),
        )


def _free_bytes(where: Path) -> int:
    """Free bytes on the card that holds ``where``; -1 when it cannot be read."""
    probe = where
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        return int(shutil.disk_usage(str(probe)).free)
    except OSError:
        return -1


def _ensure_room(needed: int, where: Path) -> None:
    """Leave the box room to keep working, not just room for this game."""
    free = _free_bytes(where)
    if free >= 0 and int(needed) + MIN_FREE_AFTER_INSTALL > free:
        raise InstallRefused(
            "There is not enough room on the card to add this game and still leave "
            "space for the rest of the box."
        )


def _download_to_temp(url: str) -> Path:
    """Stream a candidate into the box's own temp folder, never into games/."""
    data = _get(url, MAX_DOWNLOAD)
    if not data:
        raise InstallRefused("The source sent an empty file.")
    arc.ensure_dirs()
    _ensure_room(len(data), arc.temp_root())
    import tempfile

    fd, name = tempfile.mkstemp(prefix="rom-", suffix=".part", dir=str(arc.temp_root()))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
    except OSError as exc:
        try:
            os.unlink(name)
        except OSError:
            pass
        if exc.errno == errno.ENOSPC:
            raise InstallRefused("The card filled up while the game was being fetched.") from exc
        raise
    except Exception:
        try:
            os.unlink(name)
        except OSError:
            pass
        raise
    return Path(name)


def _copy_to_temp(source: Path) -> Path:
    """Copy a file the owner supplied into temp, with the same room and ENOSPC guard."""
    data_size = source.stat().st_size
    arc.ensure_dirs()
    _ensure_room(data_size, arc.temp_root())
    import tempfile

    fd, name = tempfile.mkstemp(prefix="rom-", suffix=".part", dir=str(arc.temp_root()))
    try:
        with open(source, "rb") as src, os.fdopen(fd, "wb") as dst:
            while True:
                chunk = src.read(1024 * 1024)
                if not chunk:
                    break
                dst.write(chunk)
            dst.flush()
            os.fsync(dst.fileno())
    except OSError as exc:
        try:
            os.unlink(name)
        except OSError:
            pass
        if exc.errno == errno.ENOSPC:
            raise InstallRefused("The card filled up while the game was being copied.") from exc
        raise
    except Exception:
        try:
            os.unlink(name)
        except OSError:
            pass
        raise
    return Path(name)


def publish_candidate(conn: sqlite3.Connection, candidate: Dict[str, Any],
                      auto: bool = False, source: Optional[Path] = None) -> Dict[str, Any]:
    """Download, verify, and only then show a game.  A failure changes nothing."""
    ok, reason = eligibility(candidate)
    if not ok:
        raise InstallRefused(f"This one cannot be put on the shelf: {reason}.")
    if auto and (ark_db.get_setting(conn, "arcade_auto_install", "0") or "0") != "1":
        raise InstallRefused("Automatic installing is switched off on this box.")
    system = candidate["system"]
    slug = arc.safe_slug(candidate["entry_id"])
    if not slug:
        raise InstallRefused("That entry has no usable name.")
    existing = arc.game_by_slug(conn, slug)
    if existing and existing["sha256"] == candidate.get("sha256") and candidate.get("sha256"):
        return {"slug": slug, "installed": False, "reason": "already on the shelf, unchanged"}
    twin = arc.game_by_sha(conn, candidate.get("sha256") or "", slug)
    if twin:
        # The same bytes under another name is the same game, not a second one.
        raise InstallRefused(
            f'That game is already on the shelf as "{twin["title"]}".')

    arc.ensure_dirs()
    _ensure_room(int(candidate.get("size") or 0), arc.temp_root())
    temp = _copy_to_temp(source) if source is not None else _download_to_temp(candidate["download_url"])
    want_sha = (candidate.get("sha256") or "").lower()
    try:
        digest = arc.sha256_file(temp)
        size = temp.stat().st_size
        _ensure_room(0, arc.temp_root())
        if want_sha and digest != want_sha:
            raise InstallRefused("The file that arrived is not the file that was promised.")
        if candidate.get("size") and int(candidate["size"]) != size:
            raise InstallRefused("The file that arrived is not the size that was promised.")
        facts = arc.rom_facts(temp, system)
        if not facts["ok"]:
            raise InstallRefused(facts["reason"])
        ext = Path(candidate["filename"]).suffix.lower()
        revision = 1 if not existing else int(existing["revision"]) + 1
        stored = arc.stored_rom_name(slug, ext) if revision == 1 else f"{slug}-r{revision}{ext}"
        final = arc.games_dir() / stored
        arc.games_dir().mkdir(parents=True, exist_ok=True)
        os.chmod(temp, 0o644)
        os.replace(temp, final)
    except Exception:
        try:
            temp.unlink()
        except OSError:
            pass
        raise

    stamp = arc.now_iso()
    try:
        with ark_db.tx(conn):
            if existing:
                conn.execute(
                    "UPDATE games SET title = ?, system = ?, core = ?, filename = ?, size = ?, "
                    "sha256 = ?, revision = ?, license = ?, license_evidence = ?, "
                    "rights_evidence = ?, language = ?, language_evidence = ?, provenance = ?, "
                    "release = ?, notices = ?, author = ?, homepage = ?, description = ?, "
                    "provider = ?, entry_id = ?, updated_at = ?, published = 1 WHERE id = ?",
                    (candidate["title"], system, arc.SYSTEMS[system]["core"], stored, size,
                     digest, revision, candidate.get("license", ""),
                     candidate.get("license_evidence", ""), candidate.get("rights_evidence", ""),
                     candidate.get("language", ""), candidate.get("language_evidence", ""),
                     candidate.get("provenance", ""), candidate.get("release", ""),
                     candidate.get("notices", ""), candidate.get("author", ""),
                     candidate.get("homepage", ""), candidate.get("description", ""),
                     candidate["provider"], candidate["entry_id"], stamp, int(existing["id"])),
                )
                game_id = int(existing["id"])
            else:
                cur = conn.execute(
                    "INSERT INTO games (slug, title, system, core, filename, size, sha256, revision, "
                    "license, license_evidence, rights_evidence, language, language_evidence, "
                    "provenance, release, notices, author, homepage, description, provider, "
                    "entry_id, installed_at, updated_at, published, hidden) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0)",
                    (slug, candidate["title"], system, arc.SYSTEMS[system]["core"], stored, size,
                     digest, candidate.get("license", ""), candidate.get("license_evidence", ""),
                     candidate.get("rights_evidence", ""), candidate.get("language", ""),
                     candidate.get("language_evidence", ""), candidate.get("provenance", ""),
                     candidate.get("release", ""), candidate.get("notices", ""),
                     candidate.get("author", ""), candidate.get("homepage", ""),
                     candidate.get("description", ""), candidate["provider"],
                     candidate["entry_id"], stamp, stamp),
                )
                game_id = int(cur.lastrowid)
            conn.execute(
                "INSERT INTO game_revisions (game_id, revision, filename, size, sha256, installed_at, "
                "note, release, provenance, notices) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (game_id, revision, stored, size, digest, stamp,
                 f"from {candidate['provider']} {candidate['entry_id']}"[:200],
                 candidate.get("release", ""), candidate.get("provenance", ""),
                 candidate.get("notices", "")),
            )
            conn.execute(
                "UPDATE acquisition_candidates SET status = 'installed', game_id = ?, sha256 = ?, "
                "size = ?, updated_at = ? WHERE provider = ? AND entry_id = ?",
                (game_id, digest, size, stamp, candidate["provider"], candidate["entry_id"]),
            )
    except sqlite3.DatabaseError:
        final.unlink()
        raise
    return {"slug": slug, "installed": True, "game_id": game_id, "size": size,
            "sha256": digest, "revision": revision}


# ---------------------------------------------------------------------------
#  a file the owner supplied, with the owner's own evidence
# ---------------------------------------------------------------------------
OWNER_PROVIDER = "owner"


def owner_candidate(source: Path, *, slug: str, title: str, system: str,
                    license_text: str, license_evidence: str, rights_evidence: str,
                    language: str = "", language_evidence: str = "",
                    release: str = "", notices: str = "",
                    author: str = "", homepage: str = "",
                    description: str = "") -> Dict[str, Any]:
    """Build the candidate for a hand-supplied game, asking for the same checks.

    The owner is trusted to own the file but not to skip the shape checks: the
    file must match its console and be a sane size, and a game the source says is
    in another language is still held back.  Its licence, if the owner records
    one, is kept as provenance.
    """
    if not source.is_file() or source.is_symlink():
        raise InstallRefused("That is not a file this box can read.")
    safe = arc.safe_slug(slug)
    if not safe:
        raise InstallRefused("That name has no letters or numbers this box can use.")
    if system not in arc.SYSTEMS:
        raise InstallRefused("That is not a kind of game this box can play.")
    ext = arc.SYSTEMS[system]["ext"]
    if source.suffix.lower() not in ext:
        raise InstallRefused(f"That file is not a {arc.SYSTEMS[system]['label']} game.")
    size = source.stat().st_size
    if size <= 0:
        raise InstallRefused("That file is empty.")
    if size > MAX_DOWNLOAD:
        raise InstallRefused("That file is larger than this box will accept.")
    license_text = str(license_text or "").strip()
    candidate = {
        "provider": OWNER_PROVIDER,
        "entry_id": safe,
        "title": (str(title or "").strip() or safe)[:120],
        "system": system,
        "core": arc.SYSTEMS[system]["core"],
        "filename": source.name,
        "size": size,
        # The fingerprint is what lets the shelf say "you already gave me this
        # one" instead of adding revision 2 of an identical file.
        "sha256": arc.sha256_file(source),
        "download_url": "",
        "license": license_text[:80],
        "license_evidence": str(license_evidence or "").strip()[:300],
        "rights_evidence": str(rights_evidence or "").strip()[:300],
        "language": str(language or "").strip()[:80],
        "language_evidence": str(language_evidence or "").strip()[:300],
        "release": str(release or "").strip()[:40],
        "notices": str(notices or "").strip()[:300],
        "author": str(author or "").strip()[:80],
        "homepage": str(homepage or "").strip()[:200],
        "description": str(description or "").strip()[:300],
        "provenance": f"owner-supplied:{source.name}",
    }
    problem = review_reason(candidate)
    if problem:
        raise InstallRefused(f"This one cannot be put on the shelf yet: {problem}.")
    return candidate


def import_owner_file(conn: sqlite3.Connection, source: Path, **facts: Any) -> Dict[str, Any]:
    """Publish one owner-supplied file through the one publish path."""
    candidate = owner_candidate(source, **facts)
    return publish_candidate(conn, candidate, source=source)


# ---------------------------------------------------------------------------
#  the first shelf (a pinned file, never a search result)
# ---------------------------------------------------------------------------
ARK_SEED = "ark-seed"


def seed_candidates() -> List[Dict[str, Any]]:
    try:
        data = json.loads(arc.seed_file().read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise InstallRefused("The first-shelf list on this box could not be read.") from exc
    out: List[Dict[str, Any]] = []
    for entry in data.get("entries", []):
        if not isinstance(entry, dict):
            continue
        system = str(entry.get("system") or "")
        if system not in arc.SYSTEMS:
            continue
        entry_id = arc.safe_slug(str(entry.get("id") or ""))
        if not entry_id:
            continue
        out.append({
            "provider": ARK_SEED,
            "entry_id": entry_id,
            "title": str(entry.get("title") or "")[:120],
            "system": system,
            "core": arc.SYSTEMS[system]["core"],
            "filename": str(entry.get("filename") or ""),
            "download_url": str(entry.get("download_url") or ""),
            "license": str(entry.get("license") or "")[:80],
            "license_evidence": str(entry.get("license_evidence") or "")[:300],
            "rights_evidence": str(entry.get("rights_evidence") or "")[:300],
            "language": str(entry.get("language") or "")[:80],
            "language_evidence": str(entry.get("language_evidence") or "")[:300],
            "language_conflict": bool(entry.get("language_conflict") or False),
            "provenance": str(entry.get("provenance") or f"{ARK_SEED}:{entry_id}")[:300],
            "release": str(entry.get("release") or "")[:40],
            "notices": str(entry.get("notices") or "")[:300],
            "author": str(entry.get("author") or "")[:80],
            "homepage": str(entry.get("homepage") or "")[:200],
            "description": str(entry.get("description") or "")[:300],
            "sha256": str(entry.get("sha256") or "").lower(),
            "size": int(entry.get("size") or 0),
        })
    return out


def install_seed(conn: sqlite3.Connection, dry_run: bool = False,
                 source_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Put the pinned first shelf on a new box once.  Re-running is quiet.

    ``source_dir`` installs the same list from files carried over (a stick, an
    ``scp``) instead of the network: a box with no route to the catalogue still
    gets its first shelf, and each carried file is proved against the SHA-256 and
    the size recorded beside it — a different file with the right name is
    refused, never published.
    """
    installed, skipped, errors = [], [], []
    for candidate in seed_candidates():
        carried = None
        if source_dir is not None:
            carried = Path(source_dir) / Path(str(candidate.get("filename") or "")).name
            if not carried.is_file():
                errors.append(f"{candidate['title']}: not carried in {source_dir}")
                continue
        if dry_run:
            ok, reason = eligibility(candidate)
            (errors if not ok else installed).append(
                f"{candidate['title']}: {reason}" if not ok else candidate["title"])
            continue
        if arc.game_by_slug(conn, candidate["entry_id"]):
            skipped.append(candidate["title"])
            continue
        try:
            result = publish_candidate(conn, candidate, source=carried)
            installed.append(result["slug"])
        except (InstallRefused, urllib.error.URLError, TimeoutError, OSError,
                sqlite3.DatabaseError) as exc:
            errors.append(f"{candidate['title']}: {exc}")
    return {"installed": installed, "skipped": skipped, "errors": errors}


# ---------------------------------------------------------------------------
#  the owner's decisions
# ---------------------------------------------------------------------------
def candidate_row(conn: sqlite3.Connection, candidate_id: int) -> Optional[Dict[str, Any]]:
    row = conn.execute("SELECT * FROM acquisition_candidates WHERE id = ?",
                       (int(candidate_id),)).fetchone()
    return arc._row(row) if row else None


def candidate_rows(conn: sqlite3.Connection, limit: int = 200) -> List[Dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM acquisition_candidates ORDER BY "
        "CASE status WHEN 'pending' THEN 0 WHEN 'refused' THEN 1 ELSE 2 END, title COLLATE NOCASE "
        "LIMIT ?", (int(limit),),
    ).fetchall()
    return [arc._row(r) for r in rows]


def install_approved(conn: sqlite3.Connection, candidate_id: int) -> Dict[str, Any]:
    row = candidate_row(conn, candidate_id)
    if not row:
        raise InstallRefused("That suggestion is no longer on this box.")
    candidate = dict(row)
    candidate["entry_id"] = row["entry_id"]
    return publish_candidate(conn, candidate, auto=False)


def set_candidate_status(conn: sqlite3.Connection, candidate_id: int, status: str,
                         reason: str = "") -> bool:
    if status not in ("pending", "refused", "hidden", "review"):
        raise InstallRefused("That is not a state a suggestion can be in.")
    row = candidate_row(conn, candidate_id)
    if not row or row["status"] == "installed":
        return False
    with ark_db.tx(conn):
        conn.execute("UPDATE acquisition_candidates SET status = ?, reason = ?, updated_at = ? "
                     "WHERE id = ?", (status, reason[:300], arc.now_iso(), int(candidate_id)))
    return True


def auto_install(conn: sqlite3.Connection, limit: int = 3) -> Dict[str, Any]:
    """Only ever runs when the owner switched automatic installing on."""
    if (ark_db.get_setting(conn, "arcade_auto_install", "0") or "0") != "1":
        return {"installed": [], "errors": [], "skipped": "automatic installing is off"}
    rows = conn.execute(
        "SELECT * FROM acquisition_candidates WHERE status = 'pending' "
        "ORDER BY id DESC LIMIT ?", (int(limit),),
    ).fetchall()
    installed, errors = [], []
    for row in rows:
        try:
            installed.append(publish_candidate(conn, dict(row), auto=True)["slug"])
        except (InstallRefused, urllib.error.URLError, OSError) as exc:
            errors.append(f"{row['title']}: {exc}")
            set_candidate_status(conn, row["id"], "refused", str(exc))
    return {"installed": installed, "errors": errors}


def sweep_temp(max_age: int = 6 * 3600) -> int:
    """A half-downloaded game is never a game; old leftovers are removed."""
    removed = 0
    try:
        entries = list(arc.temp_root().glob("*.part"))
    except OSError:
        return 0
    cutoff = time.time() - max_age
    for path in entries:
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed
