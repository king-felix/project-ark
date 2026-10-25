"""Project ARK — what actually works, and why not.

`/api/status` used to answer with a bare ``ready`` boolean per section: true
whenever a file existed, even if the program that serves that file was not
running.  A green dot that cannot be wrong is worse than no dot, so the
decision now lives in one place and every answer carries a sentence a person can
act on:

* **a section is ready only when a reader could use it** — the content is on the
  card *and* the thing that serves it answered on the loopback address;
* **not ready always has a reason**, in plain words, plus what to do about it;
* **numbers come from the box**, never from hope: bytes on disk, bytes still in
  the queue, free space left on the card.

Nothing in here changes state.  It only looks, and it never raises: a health
check that can fail is a health check that lies about why it failed.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

import ark_db
import ark_storage

# How long a loopback probe may take, and how long its answer is reused.  Two
# probes run for every status refresh; without the cache a phone left on the
# home page would ask the library six times a minute for nothing.
PROBE_TIMEOUT = float(os.environ.get("ARK_PROBE_TIMEOUT", "0.8"))
PROBE_CACHE_SECONDS = 10.0

# A download that has not reported for this long is called stalled.  The
# downloader writes every 15 seconds while it is fetching, so two minutes is
# several missed reports.
STALL_SECONDS = 120

# Sections a reader came for: if one of these is not ready the box is not ready.
# `downloads` is a note about work in progress, never a reason to call the box
# broken while somebody is reading what is already on it.
BLOCKING_SECTIONS = ("library", "books", "manuals", "messages", "files", "storage")


# ---------------------------------------------------------------------------
#  where things live — read at call time so a test can point ARK_ROOT anywhere
# ---------------------------------------------------------------------------
def ark_root() -> Path:
    return Path(os.environ.get("ARK_ROOT", "/srv/ark"))


def zim_dir() -> Path:
    return Path(os.environ.get("ZIM_DIR", str(ark_root() / "kiwix")))


def library_xml() -> Path:
    return zim_dir() / "library.xml"


def status_file() -> Path:
    return zim_dir() / "state" / "status.json"


def calibre_db() -> Path:
    return ark_root() / "calibre-library" / "metadata.db"


def vault_dir() -> Path:
    return ark_root() / "vault"


def kiwix_base() -> str:
    """The library reader on the loopback address (no trailing slash)."""
    return os.environ.get("KIWIX_INTERNAL", "http://127.0.0.1:8081/kiwix").rstrip("/")


def calibre_base() -> str:
    """The bookshelf on the loopback address (no trailing slash)."""
    return os.environ.get("CALIBRE_INTERNAL", "http://127.0.0.1:8083").rstrip("/")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def human_size(n) -> str:
    n = float(n or 0)
    for unit in ("B", "kB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


# ---------------------------------------------------------------------------
#  what is on the box (the readers used by the pages and by the checks)
# ---------------------------------------------------------------------------
def library_books() -> list[dict]:
    """Books listed in the library index (id, title, size)."""
    index = library_xml()
    if not index.is_file():
        return []
    try:
        root = ET.parse(index).getroot()
    except Exception:
        return []
    out = []
    for book in root.iter():
        if book.tag.lower() not in ("book", "zim"):
            continue
        ident = book.get("id") or book.get("name") or ""
        if not ident:
            continue
        title = book.get("title") or book.get("name") or ident
        size = book.get("size") or book.get("sizeBytes") or 0
        try:
            size = int(size)
        except (TypeError, ValueError):
            size = 0
        out.append({"id": ident, "title": title, "size": size})
    out.sort(key=lambda b: b["title"].lower())
    return out


def library_bytes() -> int:
    total = 0
    try:
        for f in zim_dir().glob("*.zim"):
            try:
                total += f.stat().st_size
            except OSError:
                pass
    except OSError:
        pass
    return total


def book_count() -> int:
    db = calibre_db()
    if not db.is_file():
        return 0
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        try:
            return int(conn.execute("SELECT COUNT(*) FROM books").fetchone()[0])
        finally:
            conn.close()
    except Exception:
        return 0


def guide_pages() -> list[Path]:
    folder = vault_dir()
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.rglob("*.html") if p.name != "index.html")


def download_state() -> dict:
    try:
        return json.loads(status_file().read_text(encoding="utf-8"))
    except Exception:
        return {}


# ---------------------------------------------------------------------------
#  probing a loopback service — "is anything answering at this address?"
# ---------------------------------------------------------------------------
_PROBE_CACHE: dict[str, tuple[float, bool]] = {}


def answers(url: str, timeout: float | None = None) -> bool:
    """True when something HTTP-ish answered.

    Any status code counts: a 404 is a working server saying the page moved, and
    that is a different problem from a refused connection.  A refused or silent
    address is the only thing that makes a section unusable.
    """
    now = time.monotonic()
    cached = _PROBE_CACHE.get(url)
    if cached is not None and now - cached[0] < PROBE_CACHE_SECONDS:
        return cached[1]
    ok = False
    try:
        with urllib.request.urlopen(url, timeout=timeout or PROBE_TIMEOUT) as resp:
            resp.read(1)
            ok = True
    except urllib.error.HTTPError:
        ok = True          # the service answered; its status is its own business
    except Exception:
        ok = False         # refused, timed out, or not HTTP at all
    _PROBE_CACHE[url] = (now, ok)
    return ok


def forget_probes() -> None:
    """Drop every cached answer — the owner's page asks the box again with this."""
    _PROBE_CACHE.clear()



# ---------------------------------------------------------------------------
#  one section, and the sentence that goes with it
# ---------------------------------------------------------------------------
def _section(key: str, label: str, ready: bool, reason: str,
             action: str = "", content: bool = True, **facts) -> dict:
    """One row of the answer.

    ``ready`` is the verdict a reader cares about.  ``content`` says whether
    there is anything on the card for that section yet — a box that is still
    filling up is not broken, and the checks use this to tell the two apart.
    """
    return {"key": key, "label": label, "ready": bool(ready), "reason": reason,
            "action": action if not ready else "", "content": bool(content),
            "facts": facts}


RESTART_ADVICE = ("Switch the box off, wait five seconds and switch it back on.  If this "
                  "comes back, that part of the box needs looking at.")


def library_section() -> dict:
    """The encyclopedias: files present *and* the reader answering."""
    books = library_books()
    on_disk = library_bytes()
    facts = {"titles": len(books), "bytes": on_disk}
    if not library_xml().is_file():
        return _section(
            "library", "Offline Library", False,
            "No collection has arrived yet.",
            "Nothing to do while the box is still filling up: the queue brings collections "
            "in by itself, and the front page shows how far it has got.",
            content=False, **facts)
    if not books:
        return _section(
            "library", "Offline Library", False,
            "The collections are on the card but the list of them is empty.",
            "The list is rebuilt on every start and after every finished collection.  "
            "Switch the box off and on; if it stays empty, the queue needs looking at.",
            content=False, **facts)
    if not answers(kiwix_base() + "/"):
        return _section(
            "library", "Offline Library", False,
            f"{len(books)} collections are on the card, but the reader is not answering.",
            RESTART_ADVICE, **facts)
    return _section(
        "library", "Offline Library", True,
        f"{len(books)} collections are on the card and the reader is answering.",
        "", **facts)


def books_section() -> dict:
    """The bookshelf: a real catalogue *and* the shelf page answering."""
    count = book_count()
    facts = {"count": count}
    if not calibre_db().is_file():
        return _section(
            "books", "Books", False,
            "The bookshelf has not been set up yet.",
            "This is done once when the box is built.  Until then the File Box and the "
            "manuals still work normally.", content=False, **facts)
    if count == 0:
        return _section(
            "books", "Books", False,
            "The shelf is empty.",
            "Drop a book into the File Box and it lands on this shelf by itself — no "
            "command, no second step.", content=False, **facts)
    if not answers(calibre_base() + "/"):
        return _section(
            "books", "Books", False,
            f"{count} books are on the shelf, but the shelf page is not answering.",
            RESTART_ADVICE, **facts)
    return _section(
        "books", "Books", True,
        f"{count} books are on the shelf and the shelf page is answering.",
        "", **facts)


def manuals_section() -> dict:
    pages = guide_pages()
    facts = {"pages": len(pages)}
    if not pages:
        return _section(
            "manuals", "Manuals & Guides", False,
            "No manual pages are installed.",
            "Re-run the manuals phase of the build on the box; these pages are plain "
            "files and cannot fail on their own.", content=False, **facts)
    return _section(
        "manuals", "Manuals & Guides", True,
        f"{len(pages)} manual and guide pages are ready to read.",
        "", **facts)


def _switches() -> tuple[bool, bool]:
    """(posting open, uploads open) — a missing setting means open."""
    try:
        with ark_db.read() as conn:
            return (ark_db.get_setting(conn, "public_board_post", "1") != "0",
                    ark_db.get_setting(conn, "public_uploads", "1") != "0")
    except Exception:
        return (True, True)




def messages_section() -> dict:
    posting_open, _ = _switches()
    try:
        with ark_db.read() as conn:
            total = int(conn.execute(
                "SELECT COUNT(*) FROM notes WHERE hidden = 0").fetchone()[0])
    except Exception as exc:
        return _section(
            "messages", "Message Board", False,
            "The message board cannot be read.",
            "This is a database problem, not something you did.  Switch the box off and on; "
            "if it stays like this, copy the records off before anything else.",
            error=type(exc).__name__)
    if posting_open:
        return _section(
            "messages", "Message Board", True,
            f"{total} message{'s' if total != 1 else ''}, and anyone on the network can "
            "add another.", "", messages=total, posting=True)
    return _section(
        "messages", "Message Board", True,
        f"{total} message{'s' if total != 1 else ''} can still be read; new ones are "
        "switched off by the owner.", "", messages=total, posting=False)


def files_section() -> dict:
    _, uploads_open = _switches()
    folder = ark_storage.drop_dir()
    if not folder.is_dir():
        return _section(
            "files", "File Box", False,
            "The shared folder is missing.",
            "The box creates it on every start, so this usually means the card is not "
            "mounted properly.  Switch the box off and on before adding anything else.",
            content=False)
    try:
        with ark_db.read() as conn:
            total = int(conn.execute(
                "SELECT COUNT(*) FROM files WHERE hidden = 0").fetchone()[0])
    except Exception as exc:
        return _section(
            "files", "File Box", False,
            "The list of shared files cannot be read.",
            "The files themselves are still on the card.  Switch the box off and on; if "
            "this stays like this, copy the shared folder off before anything else.",
            error=type(exc).__name__)
    if not os.access(str(folder), os.R_OK):
        return _section(
            "files", "File Box", False,
            "The shared folder cannot be opened.",
            "The card may be failing.  Copy the shared folder off before doing anything "
            "else, then rebuild the box from the backup sheet.", files=total)
    if uploads_open:
        reason = (f"{total} file{'s' if total != 1 else ''} shared, and anyone can add "
                  "another.")
    else:
        reason = (f"{total} file{'s' if total != 1 else ''} can still be downloaded; new "
                  "uploads are switched off by the owner.")
    return _section("files", "File Box", True, reason, "", files=total,
                    uploads=uploads_open)


def storage_section() -> dict:
    free = ark_storage.free_bytes(ark_root())
    facts = {"free_bytes": free, "reserve_bytes": ark_storage.MIN_FREE_RESERVE}
    if free < ark_storage.MIN_FREE_RESERVE:
        return _section(
            "storage", "Space on the card", False,
            f"Only {human_size(free)} is left.  New files are refused until space is free.",
            "Remove a shared file you no longer need, or delete a collection you never "
            "read.  The box keeps the last half gigabyte for itself on purpose.", **facts)
    return _section(
        "storage", "Space on the card", True, f"{human_size(free)} free.", "", **facts)


# ---------------------------------------------------------------------------
#  how far the downloads have actually got
# ---------------------------------------------------------------------------
def _number(state: dict, key: str) -> int:
    try:
        return max(0, int(state.get(key) or 0))
    except (TypeError, ValueError):
        return 0


def _age_seconds(stamp) -> int | None:
    if not stamp:
        return None
    try:
        then = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except Exception:
        return None
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return max(0, int((datetime.now(timezone.utc) - then).total_seconds()))


def progress(state: dict | None = None) -> dict:
    """Real bytes, real percentages — and no number invented when one is absent.

    ``bytes_on_disk`` counts finished collections only and ``current_bytes`` is
    the partly written one, so both are added before dividing.  A queue whose
    total is unknown reports ``None`` rather than a cheerful 0, and an ETA is
    only given when the downloader has measured a rate.
    """
    s = download_state() if state is None else state
    bytes_total = _number(s, "bytes_total")
    on_disk = _number(s, "bytes_on_disk")
    current_bytes = _number(s, "current_bytes")
    current_total = _number(s, "current_total")
    rate = _number(s, "current_rate_bps")

    current_percent = (min(100, int(round(100.0 * current_bytes / current_total)))
                       if current_total > 0 else None)
    moved = on_disk + current_bytes
    overall_percent = (min(100, int(round(100.0 * moved / bytes_total)))
                       if bytes_total else None)
    remaining = max(0, bytes_total - moved) if bytes_total else None
    eta = int(round(remaining / rate)) if (rate > 0 and remaining) else None
    age = _age_seconds(s.get("updated"))
    label = str(s.get("current") or "")
    busy = current_total > 0 and current_bytes < current_total
    stalled = bool(label) and busy and (age is None or age > STALL_SECONDS)

    return {
        "present": _number(s, "present"),
        "pending": _number(s, "pending"),
        "total": _number(s, "total"),
        "bytes_total": bytes_total,
        "bytes_on_disk": on_disk,
        "current": label,
        "current_bytes": current_bytes,
        "current_total": current_total,
        # `percent` stays for older readers of this endpoint; it is the current
        # collection.  `overall_percent` is the whole queue.
        "percent": current_percent,
        "current_percent": current_percent,
        "overall_percent": overall_percent,
        "overall_bytes": moved,
        "remaining_bytes": remaining,
        "rate_bps": rate,
        "eta_seconds": eta,
        "free_gib": s.get("free_gib", 0),
        "paused": bool(s.get("paused", False)),
        "updated": s.get("updated", ""),
        "updated_age_seconds": age,
        "stalled": stalled,
        "reported": bool(s),
    }


def downloads_section() -> dict:
    p = progress()
    facts = {k: p[k] for k in ("pending", "present", "percent", "overall_percent",
                               "remaining_bytes", "rate_bps", "eta_seconds",
                               "paused", "stalled")}
    if not p["reported"]:
        return _section(
            "downloads", "Incoming collections", False,
            "The download queue has not reported yet.",
            "Soon after a start it writes its first line.  If this is still here an hour "
            "after a reboot, the queue is not running and the box needs looking at.", **facts)
    if p["paused"]:
        return _section(
            "downloads", "Incoming collections", True,
            f"Bringing more in is paused.  {p['pending']} collection"
            f"{'' if p['pending'] == 1 else 's'} still waiting.", "", **facts)
    if p["stalled"]:
        return _section(
            "downloads", "Incoming collections", False,
            f"Nothing has been reported for {p['updated_age_seconds']} seconds while "
            f"“{p['current']}” was coming in.", RESTART_ADVICE, **facts)
    if p["pending"] == 0:
        return _section(
            "downloads", "Incoming collections", True,
            "Nothing is waiting.  Everything in the queue is on the card.", "", **facts)
    if p["current"] and p["current_percent"] is not None:
        return _section(
            "downloads", "Incoming collections", True,
            f"{p['pending']} still to come — {p['current_percent']}% of "
            f"“{p['current']}” ({human_size(p['current_bytes'])} of "
            f"{human_size(p['current_total'])}).", "", **facts)
    return _section(
        "downloads", "Incoming collections", True,
        f"{p['pending']} collection{'' if p['pending'] == 1 else 's'} still to come; "
        "nothing is in flight this second.", "", **facts)


SECTIONS = (library_section, books_section, manuals_section, messages_section,
            files_section, storage_section, downloads_section)


# ---------------------------------------------------------------------------
#  the whole picture
# ---------------------------------------------------------------------------
def report() -> dict:
    """Every section, plus the one verdict a caller should act on."""
    sections = [build() for build in SECTIONS]
    blocking = [s["label"] for s in sections
                if s["key"] in BLOCKING_SECTIONS and not s["ready"]]
    return {
        "checked": now_iso(),
        "ok": not blocking,
        "ready": sum(1 for s in sections if s["ready"]),
        "total": len(sections),
        "blocking": blocking,
        "sections": sections,
    }
