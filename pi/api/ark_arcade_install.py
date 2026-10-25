"""Project ARK — finding and installing games without a human guessing.

Only **one** place is allowed to put a game file on this shelf, and it is this
module.  Every file travels the same road:

1. the candidate's own catalogue entry states a licence that allows copying
   (``ark_arcade.license_problem``) — a blank, "freeware" or an unknown string
   is a refusal, not a maybe;
2. the download URL is **built** here from a validated entry id and filename,
   never taken from the response, and must still be under the one curated
   source when the first byte arrives;
3. the bytes are capped, written to ``<ARK_ARCADE_DATA>/tmp``, fsynced, and
   hashed before anything can be published;
4. the file must look like the console it claims (``rom_facts``);
5. only then is it published with ``os.replace`` and recorded in ``games`` and
   ``game_revisions``.

Nothing here trusts a page, a redirect or a filename.  An entry that cannot
prove it may be copied is still shown to the owner, with the reason.
"""

from __future__ import annotations

import json
import os
import re
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
MAX_DOWNLOAD = 64 * 1024 * 1024       # a homebrew cartridge, not a video
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


def _get(url: str, limit: int, timeout: int = HTTP_TIMEOUT) -> bytes:
    if not url.startswith(SOURCE_API + "/") and not url.startswith(SOURCE_RAW):
        raise InstallRefused("That address is not one of the game sources this box trusts.")
    request = urllib.request.Request(url, headers={"User-Agent": "ARK-Arcade/1"})
    opener = urllib.request.build_opener(_NoRedirect)
    with opener.open(request, timeout=timeout) as response:
        final = response.geturl()
        if not final.startswith(SOURCE_API + "/") and not final.startswith(SOURCE_RAW):
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
    return ""


def entry_to_candidate(entry: Dict[str, Any], provider: str = PROVIDER_ID) -> Optional[Dict[str, Any]]:
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
    url = (SOURCE_RAW + urllib.parse.quote(slug, safe="") + "/"
           + urllib.parse.quote(filename, safe="/"))
    license_text = str(entry.get("license") or entry.get("gameLicense") or "").strip()
    title = str(entry.get("title") or entry.get("name") or slug).strip()[:120] or slug
    developer = entry.get("developer") or entry.get("author") or ""
    if isinstance(developer, list):
        names = [str(d.get("name")) for d in developer if isinstance(d, dict) and d.get("name")]
        developer = ", ".join(names)
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
        "author": str(developer or "")[:80],
        "homepage": str(entry.get("repository") or entry.get("website")
                         or entry.get("gameWebsite") or entry.get("devWebsite") or "")[:200],
        "description": str(entry.get("description") or "")[:300],
    }


def eligibility(candidate: Dict[str, Any]) -> Tuple[bool, str]:
    problem = arc.license_problem(candidate.get("license", ""))
    if problem:
        return False, problem
    system = candidate.get("system", "")
    if system not in arc.SYSTEMS:
        return False, "this box does not play that kind of game"
    filename = candidate.get("filename", "")
    if not filename.lower().endswith(arc.SYSTEMS[system]["ext"]):
        return False, "the file does not match the console it claims"
    if not str(candidate.get("download_url", "")).startswith(SOURCE_RAW):
        return False, "the download address is not the curated source"
    return True, ""


def _upsert_candidate(conn: sqlite3.Connection, candidate: Dict[str, Any]) -> str:
    ok, reason = eligibility(candidate)
    stamp = arc.now_iso()
    status = "pending" if ok else "refused"
    with ark_db.tx(conn):
        conn.execute(
            "INSERT INTO acquisition_candidates "
            "(provider, entry_id, title, system, filename, download_url, license, license_evidence, "
            " author, homepage, status, reason, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(provider, entry_id) DO UPDATE SET "
            "  title = excluded.title, system = excluded.system, filename = excluded.filename, "
            "  download_url = excluded.download_url, license = excluded.license, "
            "  license_evidence = excluded.license_evidence, author = excluded.author, "
            "  homepage = excluded.homepage, reason = excluded.reason, updated_at = excluded.updated_at, "
            "  status = CASE WHEN acquisition_candidates.game_id IS NOT NULL THEN 'installed' "
            "                ELSE excluded.status END",
            (candidate["provider"], candidate["entry_id"], candidate["title"],
             candidate["system"], candidate["filename"], candidate["download_url"],
             candidate["license"], candidate["license_evidence"], candidate["author"],
             candidate["homepage"], status, reason, stamp, stamp),
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
                if ((candidate is None or not candidate["license"])
                        and details_asked < DETAIL_LOOKUPS):
                    details_asked += 1
                    try:
                        detail = _json(SOURCE_RAW + urllib.parse.quote(slug, safe="") + "/game.json")
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
    accepted = refused = 0
    for candidate in list(seen.values())[:limit]:
        status = _upsert_candidate(conn, candidate)
        accepted += 1 if status in ("pending", "installed") else 0
        refused += 1 if status == "refused" else 0
    with ark_db.tx(conn):
        conn.execute(
            "INSERT INTO providers (id, label, kind, home, enabled, last_run_at, last_result) "
            "VALUES (?, ?, 'gbdev-database', ?, 1, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET last_run_at = excluded.last_run_at, "
            "last_result = excluded.last_result",
            (PROVIDER_ID, arc.PROVIDER_LABEL, arc.PROVIDER_HOME, arc.now_iso(),
             f"{accepted} offered, {refused} refused" + (f", {len(errors)} lookups failed" if errors else "")),
        )
    detail = f"{accepted} offered, {refused} refused" + (f", {len(errors)} lookups failed" if errors else "")
    _job(conn, "discovery", detail, started)
    return {"offered": accepted, "refused": refused, "seen": len(seen), "errors": errors[:5], "detail": detail}


def _job(conn: sqlite3.Connection, kind: str, detail: str, started: str,
         status: str = "finished") -> None:
    with ark_db.tx(conn):
        conn.execute(
            "INSERT INTO acquisition_jobs (kind, status, started_at, finished_at, detail) "
            "VALUES (?, ?, ?, ?, ?)",
            (kind, status, started, arc.now_iso(), detail[:500]),
        )


def _download_to_temp(url: str) -> Path:
    """Stream a candidate into the box's own temp folder, never into games/."""
    data = _get(url, MAX_DOWNLOAD)
    if not data:
        raise InstallRefused("The source sent an empty file.")
    arc.ensure_dirs()
    import tempfile

    fd, name = tempfile.mkstemp(prefix="rom-", suffix=".part", dir=str(arc.temp_root()))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
    except Exception:
        try:
            os.unlink(name)
        except OSError:
            pass
        raise
    return Path(name)


def publish_candidate(conn: sqlite3.Connection, candidate: Dict[str, Any],
                      auto: bool = False) -> Dict[str, Any]:
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

    temp = _download_to_temp(candidate["download_url"])
    want_sha = (candidate.get("sha256") or "").lower()
    try:
        digest = arc.sha256_file(temp)
        size = temp.stat().st_size
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
                    "sha256 = ?, revision = ?, license = ?, license_evidence = ?, author = ?, "
                    "homepage = ?, description = ?, provider = ?, entry_id = ?, updated_at = ?, "
                    "published = 1 WHERE id = ?",
                    (candidate["title"], system, arc.SYSTEMS[system]["core"], stored, size,
                     digest, revision, candidate.get("license", ""),
                     candidate.get("license_evidence", ""), candidate.get("author", ""),
                     candidate.get("homepage", ""), candidate.get("description", ""),
                     candidate["provider"], candidate["entry_id"], stamp, int(existing["id"])),
                )
                game_id = int(existing["id"])
            else:
                cur = conn.execute(
                    "INSERT INTO games (slug, title, system, core, filename, size, sha256, revision, "
                    "license, license_evidence, author, homepage, description, provider, entry_id, "
                    "installed_at, updated_at, published, hidden) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0)",
                    (slug, candidate["title"], system, arc.SYSTEMS[system]["core"], stored, size,
                     digest, candidate.get("license", ""), candidate.get("license_evidence", ""),
                     candidate.get("author", ""), candidate.get("homepage", ""),
                     candidate.get("description", ""), candidate["provider"],
                     candidate["entry_id"], stamp, stamp),
                )
                game_id = int(cur.lastrowid)
            conn.execute(
                "INSERT INTO game_revisions (game_id, revision, filename, size, sha256, installed_at, note) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (game_id, revision, stored, size, digest, stamp,
                 f"from {candidate['provider']} {candidate['entry_id']}"[:200]),
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
            "author": str(entry.get("author") or "")[:80],
            "homepage": str(entry.get("homepage") or "")[:200],
            "description": str(entry.get("description") or "")[:300],
            "sha256": str(entry.get("sha256") or "").lower(),
            "size": int(entry.get("size") or 0),
        })
    return out


def install_seed(conn: sqlite3.Connection, dry_run: bool = False) -> Dict[str, Any]:
    """Put the pinned first shelf on a new box once.  Re-running is quiet."""
    installed, skipped, errors = [], [], []
    for candidate in seed_candidates():
        if dry_run:
            ok, reason = eligibility(candidate)
            (errors if not ok else installed).append(
                f"{candidate['title']}: {reason}" if not ok else candidate["title"])
            continue
        if arc.game_by_slug(conn, candidate["entry_id"]):
            skipped.append(candidate["title"])
            continue
        try:
            result = publish_candidate(conn, candidate)
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
    if status not in ("pending", "refused", "hidden"):
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
