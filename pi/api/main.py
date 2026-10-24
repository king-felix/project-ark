"""Project ARK — the small backend behind the portal.

Serves what a nearby reader needs, all anonymously:

  GET  /api/status   what is on the box, as JSON (used by the home page strip)
  GET  /search       one search box across the library, the books and the guides
  GET  /board        message board (read + post)
  GET  /files        shared file box (read + upload)

Reading needs no account, on purpose: this is a box in a room, not a website.
Everything is rendered server-side so the critical paths need zero JavaScript.

The owner's side lives in ``ark_admin`` under ``/admin``.  It is mounted here
but never reachable from the portal: nginx answers 404 for it on :80 and only
proxies it on the HTTPS management listener — so an administrator password
cannot travel over plain HTTP even by accident.
"""

from __future__ import annotations

import html
import json
import os
import re
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Form, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

import ark_admin
import ark_db

ARK_ROOT = Path(os.environ.get("ARK_ROOT", "/srv/ark"))
LIBRARY_XML = ARK_ROOT / "kiwix" / "library.xml"
ZIM_DIR = Path(os.environ.get("ZIM_DIR", str(ARK_ROOT / "kiwix")))
ZIM_STATUS = ZIM_DIR / "state" / "status.json"
CALIBRE_DB = ARK_ROOT / "calibre-library" / "metadata.db"
VAULT_DIR = ARK_ROOT / "vault"
DROP_DIR = ARK_ROOT / "drop" / "files"
KIWIX_INTERNAL = os.environ.get("KIWIX_INTERNAL", "http://127.0.0.1:8081/kiwix")

MAX_UPLOAD = 2 * 1024 * 1024 * 1024        # 2 GiB (nginx currently allows 4g: see
                                           # docs/IMPLEMENTATION-LOG.md §3 — the two
                                           # limits are aligned in the storage stage)
MAX_NOTE = 4000
MAX_NAME = 60

app = FastAPI(title="Project ARK", docs_url=None, redoc_url=None, openapi_url=None)


# ---------------------------------------------------------------------------
#  storage
# ---------------------------------------------------------------------------
def init_db() -> None:
    """Schema, migrations, the one-time administrator account, fresh sessions.

    Called once per process, at import: a restart therefore never resurrects a
    default login, never resets a password, and always starts from "signed out".
    """
    DROP_DIR.mkdir(parents=True, exist_ok=True)
    ark_db.migrate()
    ark_admin.ensure_initialised()
    with ark_db.tx() as conn:
        ark_db.bump_session_epoch(conn)


init_db()

# The administrator area lives under /admin on this same backend, but nginx only
# exposes it on the HTTPS management listener — never on the public portal.
app.include_router(ark_admin.router)


# ---------------------------------------------------------------------------
#  tiny helpers
# ---------------------------------------------------------------------------
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def human_size(n) -> str:
    n = float(n or 0)
    for unit in ("B", "kB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def ago(iso: str) -> str:
    """'2 hours ago' — friendlier than a timestamp for a non-technical reader."""
    try:
        then = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except Exception:
        return ""
    secs = (datetime.now(timezone.utc) - then).total_seconds()
    if secs < 90:
        return "just now"
    for limit, div, word in ((3600, 60, "minute"), (86400, 3600, "hour"),
                             (2592000, 86400, "day"), (31536000, 2592000, "month")):
        if secs < limit:
            n = int(secs // div)
            return f"{n} {word}{'' if n == 1 else 's'} ago"
    return f"{int(secs // 31536000)} years ago"


def safe_name(raw: str) -> str:
    """Keep a file name usable on disk and safe in a URL."""
    name = os.path.basename((raw or "").replace("\\", "/")).strip()
    name = re.sub(r"[\x00-\x1f\x7f]", "", name)
    name = re.sub(r"[^A-Za-z0-9._ ()\[\]-]+", "_", name).strip(" .")
    return name[:120] or "file"


def esc(value) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


NAV = [("/", "Home"), ("/search", "Search"), ("/kiwix/", "Library"),
       ("/books/", "Books"), ("/vault/", "Manuals"), ("/board", "Messages"),
       ("/files", "File box")]


def page(title: str, body: str, here: str = "", tagline: str = "",
         status: int = 200) -> HTMLResponse:
    """Every page shares the same head, navigation and footer."""
    parts = []
    for href, label in NAV:
        current = ' aria-current="page"' if href == here else ""
        parts.append(f'<a href="{esc(href)}"{current}>{esc(label)}</a>')
    sub = f'<p class="tagline">{esc(tagline)}</p>' if tagline else ""
    doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark">
<meta name="theme-color" content="#000000">
<title>{esc(title)} — Project Ark</title>
<link rel="stylesheet" href="/assets/ark.css">
</head>
<body>
<div class="wrap">
  <header>
    <span class="logo">Project Ark</span>
    <h1>{esc(title)}</h1>
    {sub}
  </header>
  <div class="nav">{''.join(parts)}</div>
{body}
  <footer>
    <p><b>Project Ark</b> — the library that lives on a small computer. No accounts, no tracking.</p>
  </footer>
</div>
</body>
</html>"""
    return HTMLResponse(doc, status_code=status)


def error_page(message: str, hint: str = "", code: int = 400) -> HTMLResponse:
    """One response, with the right status.

    The previous version passed an ``HTMLResponse`` object into another one and
    raised ``AttributeError`` — a 500 instead of a friendly 400.
    """
    hint_html = f'<p class="note">{esc(hint)}</p>' if hint else ""
    return page("Something went wrong", f"""
  <p class="status"><span class="dot" aria-hidden="true"></span><span>{esc(message)}</span></p>
  {hint_html}
  <div class="nav"><a href="/">Back to the home page</a></div>
""", status=code)


# ---------------------------------------------------------------------------
#  what is on the box
# ---------------------------------------------------------------------------
def library_books() -> list[dict]:
    """Books listed in the library index (id, title, size)."""
    if not LIBRARY_XML.is_file():
        return []
    try:
        root = ET.parse(LIBRARY_XML).getroot()
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
        for f in ZIM_DIR.glob("*.zim"):
            try:
                total += f.stat().st_size
            except OSError:
                pass
    except OSError:
        pass
    return total


def book_count() -> int:
    if not CALIBRE_DB.is_file():
        return 0
    try:
        conn = sqlite3.connect(f"file:{CALIBRE_DB}?mode=ro", uri=True, timeout=5)
        try:
            return int(conn.execute("SELECT COUNT(*) FROM books").fetchone()[0])
        finally:
            conn.close()
    except Exception:
        return 0


def guide_pages() -> list[Path]:
    if not VAULT_DIR.is_dir():
        return []
    return sorted(p for p in VAULT_DIR.rglob("*.html") if p.name != "index.html")


def download_state() -> dict:
    try:
        return json.loads(ZIM_STATUS.read_text(encoding="utf-8"))
    except Exception:
        return {}


@app.get("/api/health")
def health():
    return {"ok": True, "time": now_iso()}


@app.get("/api/status")
def status():
    books = library_books()
    dl = download_state()
    guides = guide_pages()
    n_books = book_count()
    # Progress of the bundle being fetched right now: the home page strip shows
    # "42% of <label>" so a 49 GiB entry is not a silent day.
    cur = int(dl.get("current_bytes") or 0)
    tot = int(dl.get("current_total") or 0)
    pct = min(100, int(round(100.0 * cur / tot))) if tot > 0 else 0
    return {
        "time": now_iso(),
        "library": {
            "ready": bool(books),
            "titles": len(books),
            "bytes": library_bytes(),
            "items": [{"title": b["title"], "id": b["id"], "size": b["size"]} for b in books],
        },
        "books": {"ready": n_books > 0, "count": n_books},
        "guides": {
            "count": len(guides),
            "pages": [{"title": p.stem.replace("-", " ").title(), "path": f"/vault/{p.relative_to(VAULT_DIR)}"} for p in guides],
        },
        "downloads": {
            "present": dl.get("present", 0),
            "pending": dl.get("pending", 0),
            "current": dl.get("current", ""),
            "current_bytes": cur,
            "current_total": tot,
            "percent": pct,
            "free_gib": dl.get("free_gib", 0),
            "paused": bool(dl.get("paused", False)),
            "updated": dl.get("updated", ""),
        },
    }


# ---------------------------------------------------------------------------
#  search — library + guides + bookshelf, one box
# ---------------------------------------------------------------------------
def _public_link(raw: str) -> str:
    """Turn an absolute link from the library into a same-site path."""
    try:
        p = urllib.parse.urlsplit(raw)
        if p.scheme and p.netloc:
            return urllib.parse.urlunsplit(("", "", p.path or "/", p.query, p.fragment))
    except ValueError:
        pass
    return raw


def _parse_feed(raw: bytes) -> list[dict]:
    """The library answers in a simple XML feed; take title/link/snippet from it."""
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return []
    hits = []
    for node in root.iter():
        if node.tag.lower() not in ("item", "entry"):
            continue
        title = link = snippet = ""
        for child in node:
            tag = child.tag.lower()
            if tag in ("title", "name") and not title:
                title = (child.text or "").strip()
            elif tag == "link" and not link:
                link = (child.text or "").strip() or (child.get("href") or "")
            elif tag in ("description", "summary", "snippet") and not snippet:
                snippet = re.sub(r"<[^>]+>", " ", child.text or "").strip()
        if title and link:
            snippet = html.unescape(re.sub(r"<[^>]+>", " ", snippet))
            snippet = re.sub(r"\s+", " ", snippet)[:220]
            hits.append({"title": html.unescape(title), "link": _public_link(link),
                         "snippet": snippet})
    return hits


def _kiwix_one(book_id: str, pattern: str) -> list[dict]:
    query = urllib.parse.urlencode({"content": book_id, "pattern": pattern, "format": "xml"})
    req = urllib.request.Request(f"{KIWIX_INTERNAL}/search?{query}",
                                 headers={"User-Agent": "ark-portal/1.0"})
    with urllib.request.urlopen(req, timeout=9) as resp:
        return _parse_feed(resp.read(3_000_000))[:4]


def search_library(pattern: str) -> list[dict]:
    """Ask every collection at once; a slow or missing one never blocks the page."""
    books = library_books()
    if not books:
        return []
    by_id = {b["id"]: b for b in books}
    hits: list[dict] = []
    seen: set[tuple[str, str]] = set()
    with ThreadPoolExecutor(max_workers=6) as pool:
        jobs = {pool.submit(_kiwix_one, b["id"], pattern): b for b in books}
        try:
            for future in as_completed(jobs, timeout=12):
                book = jobs[future]
                try:
                    found = future.result()
                except Exception:
                    continue
                for hit in found:
                    # The link names the collection that owns the article; trust
                    # that over the guess we made when we sent the question.
                    m = re.search(r"/content/([^/]+)/", hit["link"])
                    owner = by_id.get(m.group(1)) if m else None
                    hit["source"] = (owner or book)["title"]
                    key = (hit["link"], hit["title"])
                    if key in seen:
                        continue
                    seen.add(key)
                    hits.append(hit)
        except Exception:
            pass          # deadline reached: render whatever came back
    hits.sort(key=lambda h: (h["source"], h["title"]))
    return hits[:24]


def search_guides(pattern: str) -> list[dict]:
    """Plain text search across the manual pages — works with no index at all."""
    needle = pattern.lower()
    found = []
    for path in guide_pages():
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", raw, flags=re.S | re.I)
        title_m = re.search(r"<h1[^>]*>(.*?)</h1>", text, flags=re.S | re.I)
        title = re.sub(r"<[^>]+>", "", title_m.group(1)).strip() if title_m else path.stem.replace("-", " ").title()
        plain = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text))
        plain = html.unescape(plain)
        title = html.unescape(title)
        pos = plain.lower().find(needle)
        in_title = title.lower().find(needle) >= 0
        if in_title and pos < 0:
            pos = 0
        if pos < 0:
            continue
        start = max(0, pos - 90)
        if start:
            # start on a word boundary so the snippet does not open mid-word
            space = plain.find(" ", start)
            if 0 <= space < pos:
                start = space + 1
        snippet = plain[start: pos + 170].strip()
        found.append({
            "title": title,
            "link": "/vault/" + str(path.relative_to(VAULT_DIR)),
            "snippet": ("… " if start else "") + snippet + " …",
            "source": "Manuals & Guides",
            # a page whose title matches is almost always the one you meant
            "rank": 0 if in_title else 1,
        })
    found.sort(key=lambda h: (h["rank"], h["title"]))
    for hit in found:
        hit.pop("rank", None)
    return found[:8]



def search_books(pattern: str) -> list[dict]:
    if not CALIBRE_DB.is_file():
        return []
    like = f"%{pattern}%"
    try:
        conn = sqlite3.connect(f"file:{CALIBRE_DB}?mode=ro", uri=True, timeout=5)
        try:
            rows = conn.execute(
                """SELECT b.id, b.title,
                          (SELECT a.name FROM authors a
                             JOIN books_authors_link l ON l.author = a.id
                            WHERE l.book = b.id LIMIT 1) AS author
                     FROM books b
                    WHERE b.title LIKE ? OR EXISTS (
                          SELECT 1 FROM authors a JOIN books_authors_link l ON l.author = a.id
                           WHERE l.book = b.id AND a.name LIKE ?)
                    ORDER BY b.title COLLATE NOCASE LIMIT 8""",
                (like, like),
            ).fetchall()
        finally:
            conn.close()
    except Exception:
        return []
    return [{"title": r[1], "link": f"/books/book/{r[0]}", "snippet": r[2] or "Book on the shelf",
             "source": "Books"} for r in rows]


@app.get("/search", response_class=HTMLResponse)
@app.get("/api/search", response_class=HTMLResponse)
def search(q: str = ""):
    pattern = (q or "").strip()[:120]
    form = f"""
  <form action="/search" method="get" role="search">
    <label for="q">What do you need to look up?</label>
    <input id="q" name="q" type="search" value="{esc(pattern)}"
           placeholder="water filter, broken pipe, fever…" autocomplete="off" enterkeyhint="search">
    <button class="go-btn" type="submit">Search everything</button>
  </form>"""

    if not pattern:
        return page("Search", form + """
  <h2>How to get a good answer</h2>
  <p class="hint">Type one or two words — “bleach water”, “burn”, “radiator leak”.
     Short and specific beats a whole sentence.</p>""", here="/search",
                    tagline="One box that looks in the encyclopedias, the manuals and the bookshelf.")

    library = search_library(pattern)
    guides = search_guides(pattern)
    books = search_books(pattern)
    total = len(library) + len(guides) + len(books)

    def block(title: str, items: list[dict]) -> str:
        if not items:
            return ""
        rows = []
        for i in items:
            sub = f'<span class="sub">{esc(i["source"])}</span>'
            if i.get("snippet"):
                sub += f'<span class="sub">{esc(i["snippet"])}</span>'
            rows.append(f'<li><a href="{esc(i["link"])}">{esc(i["title"])}</a>{sub}</li>')
        return f'<h2>{esc(title)}</h2><ul class="clean">{"".join(rows)}</ul>'

    parts = [form]
    if total:
        word = "answer" if total == 1 else "answers"
        parts.append(f'<p class="status"><span class="dot" aria-hidden="true"></span>'
                     f'<span><b>{total}</b> {word} for “{esc(pattern)}”. Tap one to read it.</span></p>')
    else:
        parts.append(f"""<p class="status"><span class="dot" aria-hidden="true"></span>
      <span>Nothing matched <b>“{esc(pattern)}”</b> yet.</span></p>
  <p class="note">Try a shorter word, or open a collection and use the search box inside it.</p>
  <div class="nav"><a href="/kiwix/">Open the Offline Library</a><a href="/vault/">Open the manuals</a></div>""")
    parts.append(block("From the library", library))
    parts.append(block("From the manuals", guides))
    parts.append(block("From the bookshelf", books))
    return page("Search", "\n".join(parts), here="/search",
                tagline=f"Results for “{pattern}”." if pattern else "")


# ---------------------------------------------------------------------------
#  message board
# ---------------------------------------------------------------------------
@app.get("/board", response_class=HTMLResponse)
def board(ok: str = ""):
    with ark_db.read() as conn:
        rows = conn.execute(
            "SELECT id, ts, name, body FROM notes ORDER BY id DESC LIMIT 100"
        ).fetchall()
    if rows:
        items = "".join(
            f'<li><b>{esc(r["name"])}</b>'
            f'<span class="sub">{esc(ago(r["ts"]))}</span>'
            f'<span class="sub" style="color:var(--ink);white-space:pre-wrap">{esc(r["body"])}</span></li>'
            for r in rows
        )
        listing = f'<h2>What people wrote ({len(rows)})</h2><ul class="clean">{items}</ul>'
    else:
        listing = ('<h2>Nothing here yet</h2>'
                   '<p class="hint">Be the first: write what is broken, what was fixed, '
                   'or who needs help. Everybody on the network can read it.</p>')

    thanks = ('<p class="status"><span class="dot" aria-hidden="true"></span>'
              '<span><b>Saved.</b> Your message is on the board for everyone to see.</span></p>'
              if ok else "")
    body = f"""{thanks}
  <form action="/board" method="post">
    <label for="name">Your name (or leave blank)</label>
    <input id="name" name="name" type="text" maxlength="{MAX_NAME}" placeholder="e.g. Sam" autocomplete="off">
    <label for="body">Your message</label>
    <textarea id="body" name="body" maxlength="{MAX_NOTE}" required
              placeholder="The tap in the back garden is leaking. I have spare washers."></textarea>
    <button class="go-btn" type="submit">Put it on the board</button>
  </form>
  <p class="note">Anyone on this Wi-Fi can read and post here. Keep it useful, keep it kind.</p>
  {listing}"""
    return page("Message Board", body, here="/board",
                tagline="Notes for the next person — what broke, what got fixed, who needs a hand.")


@app.post("/board", response_class=HTMLResponse)
def board_post(name: str = Form(""), body: str = Form("")):
    text = (body or "").strip()
    who = (name or "").strip() or "Anonymous"
    if len(text) < 2:
        return error_page("The message was empty.", "Write a line or two, then press the button again.", 400)
    if len(text) > MAX_NOTE:
        return error_page("That message is too long.",
                          f"Please keep it under {MAX_NOTE} characters — split it into two notes.", 400)
    with ark_db.tx() as conn:
        conn.execute("INSERT INTO notes (ts, name, body) VALUES (?, ?, ?)",
                     (now_iso(), who[:MAX_NAME], text))
    return RedirectResponse("/board?ok=1", status_code=303)



# ---------------------------------------------------------------------------
#  file box
# ---------------------------------------------------------------------------
@app.get("/files", response_class=HTMLResponse)
def files(ok: str = "", err: str = ""):
    with ark_db.read() as conn:
        rows = conn.execute(
            "SELECT id, ts, name, size, note FROM files ORDER BY id DESC LIMIT 200"
        ).fetchall()
    if rows:
        items = "".join(
            f'<li><a href="/drop/{esc(urllib.parse.quote(r["name"]))}">{esc(r["name"])}</a>'
            f'<span class="sub">{esc(human_size(r["size"]))} · added {esc(ago(r["ts"]))}</span>'
            + (f'<span class="sub">{esc(r["note"])}</span>' if r["note"] else "")
            + "</li>"
            for r in rows
        )
        listing = f'<h2>Files people shared ({len(rows)})</h2><ul class="clean">{items}</ul>'
    else:
        listing = ('<h2>The box is empty</h2>'
                   '<p class="hint">Add the first file — a photo of a broken part, a recipe, a PDF '
                   'you want everyone to have.</p>')

    banner = ""
    if ok:
        banner = ('<p class="status"><span class="dot" aria-hidden="true"></span>'
                  '<span><b>Uploaded.</b> Everyone on this network can download it now.</span></p>')
    elif err:
        banner = ('<p class="status"><span class="dot" aria-hidden="true"></span>'
                  f'<span><b>Not uploaded.</b> {esc(err)}</span></p>')
    body = f"""{banner}
  <form action="/files" method="post" enctype="multipart/form-data">
    <label for="file">Choose a file</label>
    <input id="file" name="file" type="file" required>
    <label for="note">A short note (or leave blank)</label>
    <input id="note" name="note" type="text" maxlength="140" placeholder="Spare gasket — fits the big tap">
    <button class="go-btn" type="submit">Put it in the box</button>
  </form>
  <p class="note">Anyone on this Wi-Fi can download what you add. Do not put private things here.
     Files up to 2 GB.</p>
  {listing}"""
    return page("File Box", body, here="/files",
                tagline="Swap files with everyone on the network — no account, no email.")


@app.post("/files", response_class=HTMLResponse)
async def files_post(file: UploadFile = File(None), note: str = Form("")):
    if file is None or not file.filename:
        return RedirectResponse("/files?err=No%20file%20was%20chosen", status_code=303)
    name = safe_name(file.filename)
    target = DROP_DIR / name
    if target.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M")
        target = DROP_DIR / f"{stamp}-{name}"
    written = 0
    tmp = target.with_suffix(target.suffix + ".part")
    try:
        with tmp.open("wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_UPLOAD:
                    raise ValueError("too big")
                out.write(chunk)
        tmp.replace(target)
    except ValueError:
        tmp.unlink(missing_ok=True)
        return RedirectResponse("/files?err=That%20file%20is%20too%20big%20(limit%202%20GB)",
                                status_code=303)
    except OSError:
        tmp.unlink(missing_ok=True)
        return RedirectResponse("/files?err=The%20box%20could%20not%20store%20it", status_code=303)

    if written == 0:
        target.unlink(missing_ok=True)
        return RedirectResponse("/files?err=That%20file%20was%20empty", status_code=303)

    with ark_db.tx() as conn:
        conn.execute("INSERT INTO files (ts, name, size, note) VALUES (?, ?, ?, ?)",
                     (now_iso(), target.name, written, (note or "").strip()[:140]))
    return RedirectResponse("/files?ok=1", status_code=303)

