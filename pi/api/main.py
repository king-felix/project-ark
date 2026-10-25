"""Project ARK — the small backend behind the portal.

Serves what a nearby reader needs, all anonymously:

  GET  /api/status   what is on the box, as JSON (used by the home page strip)
  GET  /api/ready    truthful readiness — 200 when every section a reader needs works
  GET  /api/health   liveness only ("the backend answered"), never a verdict
  GET  /search       one search box across the library, the books and the guides
  GET  /board        message board (read + post)
  GET  /files        shared file box (read + upload)
  GET  /arcade/      game shelf (guest play; an optional name keeps saves)

Reading needs no account, on purpose: this is a box in a room, not a website.
Everything is rendered server-side so the critical paths need zero JavaScript,
except the game player itself. Named-player saves are served only through the
separate TLS origin (:8445); the public shelf is guest-only.

The owner's side lives in ``ark_admin`` under ``/admin``.  It is mounted here
but never reachable from the portal: nginx answers 404 for it on :80 and only
proxies it on the HTTPS management listener — so an administrator password
cannot travel over plain HTTP even by accident.
"""

from __future__ import annotations

import html
import mimetypes
import os
import re
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Form, Response, UploadFile, File
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               RedirectResponse)

import ark_admin
import ark_arcade
import ark_arcade_install
import ark_arcade_web
import ark_db
import ark_health
import ark_storage
from ark_health import (book_count, guide_pages, human_size, library_books,
                        library_bytes, now_iso)

# Paths are read from ark_health at the moment of use, never frozen at import:
# one place decides where the library, the shelf and the guides live, so a
# change of location cannot leave half the portal pointing at the old folder.

MAX_UPLOAD = ark_storage.MAX_FILE          # the same number nginx is configured with
MAX_NOTE = 4000
MAX_NAME = 60

# Page sizes. Small enough that a phone does not download a year of notes to
# read the newest one, large enough that scrolling feels normal.
BOARD_PAGE = 20
FILES_PAGE = 50

# Search is bounded in three places: how many collections are asked, how many
# questions are in flight, and how long the page waits for answers.
SEARCH_WORKERS = 6
MAX_COLLECTIONS = 12
SEARCH_DEADLINE = 6.0

app = FastAPI(title="Project ARK", docs_url=None, redoc_url=None, openapi_url=None)


# ---------------------------------------------------------------------------
#  storage
# ---------------------------------------------------------------------------
def init_db() -> None:
    """Schema, migrations, the one-time administrator account, fresh sessions.

    Called once per process, at import: a restart therefore never resurrects a
    default login, never resets a password, and always starts from "signed out".
    """
    ark_storage.ensure_dirs()
    ark_arcade.ensure_dirs()
    ark_db.migrate()
    ark_admin.ensure_initialised()
    with ark_db.tx() as conn:
        ark_db.bump_session_epoch(conn)
    # A half-written upload from a crash or a closed browser is invisible to
    # everyone; delete the stale ones so the card does not slowly fill with them.
    try:
        ark_storage.reconcile_temp()
        ark_arcade_install.sweep_temp()
    except OSError:
        pass          # housekeeping must never stop the portal from starting


init_db()

# The administrator area lives under /admin on this same backend, but nginx only
# exposes it on the HTTPS management listener — never on the public portal.
app.include_router(ark_admin.router)
# The games shelf is public on purpose.  Its player pages carry their own cookie
# and their own tables; nothing here can reach /admin.
app.include_router(ark_arcade_web.router)


# ---------------------------------------------------------------------------
#  tiny helpers
# ---------------------------------------------------------------------------
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


def setting(key: str, default: str = "1") -> str:
    """Read one of the owner's switches; a missing row means 'open'."""
    with ark_db.read() as conn:
        return ark_db.get_setting(conn, key, default)


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
#  Reading the index, counting books and pages, and measuring the download queue
#  all live in ark_health now, so the pages and the verdict cannot disagree.  A
#  section is "ready" there only when its content is present *and* the thing
#  that serves it answered — see ark_health.report().


@app.get("/api/health")
def health():
    """Is the backend alive?  Only that — and it says so out loud.

    This used to answer ``{"ok": true}`` whatever the box was doing, which made
    it useless as evidence.  It is still always 200 whenever this code runs (a
    monitor needs a liveness check that does not page on a full card), but it no
    longer pretends to be a verdict: the readiness summary rides along and the
    honest answer lives at ``/api/ready``.
    """
    report = ark_health.report()
    return {
        "live": True,
        "scope": "backend-process",
        "note": "The backend answered. This is not a health verdict — see /api/ready.",
        "ready": report["ok"],
        "blocking": report["blocking"],
        "time": now_iso(),
    }


@app.get("/api/ready")
def ready():
    """Truthful readiness: 200 only when every section a reader came for works.

    503 otherwise, with the same body — each section carries ``reason`` (a
    sentence a person can read) and ``action`` (empty when nothing is wrong), so
    a script and a human get the same answer out of one call.
    """
    report = ark_health.report()
    return JSONResponse(report, status_code=200 if report["ok"] else 503)


@app.get("/api/status")
def status():
    books = library_books()
    guides = guide_pages()
    n_books = book_count()
    report = ark_health.report()
    sections = {s["key"]: s for s in report["sections"]}
    dl = ark_health.progress()
    return {
        "time": now_iso(),
        "ok": report["ok"],
        "blocking": report["blocking"],
        # Every section, with the reason it is or is not usable.  This is the
        # same list the owner's "What is working" page renders.
        "sections": report["sections"],
        "library": {
            "ready": sections["library"]["ready"],
            "reason": sections["library"]["reason"],
            "titles": len(books),
            "bytes": library_bytes(),
            "items": [{"title": b["title"], "id": b["id"], "size": b["size"]} for b in books],
        },
        "books": {
            "ready": sections["books"]["ready"],
            "reason": sections["books"]["reason"],
            "count": n_books,
        },
        "guides": {
            "ready": sections["manuals"]["ready"],
            "reason": sections["manuals"]["reason"],
            "count": len(guides),
            "pages": [{"title": p.stem.replace("-", " ").title(), "path": f"/vault/{p.relative_to(ark_health.vault_dir())}"} for p in guides],
        },
        # Byte-level truth from the downloader's own report: what is finished,
        # what is in flight, how fast, and how much is left.  `percent` is the
        # current collection; `overall_percent` is the whole queue.
        "downloads": {
            key: dl[key] for key in
            ("present", "pending", "total", "current", "current_bytes", "current_total",
             "percent", "current_percent", "overall_percent", "overall_bytes",
             "remaining_bytes", "rate_bps", "eta_seconds", "bytes_total",
             "bytes_on_disk", "free_gib", "paused", "updated", "updated_age_seconds",
             "stalled", "reported")
        },
        "download_reason": sections["downloads"]["reason"],
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
    req = urllib.request.Request(f"{ark_health.kiwix_base()}/search?{query}",
                                 headers={"User-Agent": "ark-portal/1.0"})
    with urllib.request.urlopen(req, timeout=9) as resp:
        return _parse_feed(resp.read(3_000_000))[:4]


def _search_pool() -> ThreadPoolExecutor:
    """One pool for the whole process, created on first use.

    Creating a pool per request meant a per-request ``shutdown()``, and that
    joins every worker — so the 12-second deadline expired and the page then
    waited anyway, up to several minutes with twenty collections.  Holding the
    pool open means the wait is the only thing that can delay the page.
    """
    global _SEARCH_EXECUTOR
    if _SEARCH_EXECUTOR is None:
        _SEARCH_EXECUTOR = ThreadPoolExecutor(max_workers=SEARCH_WORKERS,
                                              thread_name_prefix="ark-search")
    return _SEARCH_EXECUTOR


_SEARCH_EXECUTOR: ThreadPoolExecutor | None = None


def search_library(pattern: str) -> list[dict]:
    """Ask a bounded set of collections, and never wait longer than the budget.

    Whatever has arrived by the deadline is rendered; whatever has not is
    abandoned (not joined, not waited for) and its late answer is discarded.
    """
    books = library_books()[:MAX_COLLECTIONS]
    if not books:
        return []
    by_id = {b["id"]: b for b in books}
    hits: list[dict] = []
    seen: set[tuple[str, str]] = set()
    pool = _search_pool()
    jobs = {pool.submit(_kiwix_one, b["id"], pattern): b for b in books}
    done, pending = wait(list(jobs), timeout=SEARCH_DEADLINE)
    for future in pending:
        future.cancel()           # a running worker is left to finish and be ignored
    for future in done:
        book = jobs[future]
        try:
            found = future.result()
        except Exception:
            continue              # one broken collection is not the page's problem
        for hit in found:
            # The link names the collection that owns the article; trust that
            # over the guess we made when we sent the question.
            m = re.search(r"/content/([^/]+)/", hit["link"])
            owner = by_id.get(m.group(1)) if m else None
            hit["source"] = (owner or book)["title"]
            key = (hit["link"], hit["title"])
            if key in seen:
                continue
            seen.add(key)
            hits.append(hit)
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
            "link": "/vault/" + str(path.relative_to(ark_health.vault_dir())),
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
    catalogue = ark_health.calibre_db()
    if not catalogue.is_file():
        return []
    like = f"%{pattern}%"
    try:
        conn = sqlite3.connect(f"file:{catalogue}?mode=ro", uri=True, timeout=5)
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
def board(ok: str = "", p: int = 1):
    page_no = max(1, int(p or 1))
    offset = (page_no - 1) * BOARD_PAGE
    with ark_db.read() as conn:
        total = int(conn.execute("SELECT COUNT(*) FROM notes WHERE hidden = 0").fetchone()[0])
        rows = conn.execute(
            "SELECT id, ts, name, body, pinned, resolved FROM notes WHERE hidden = 0"
            " ORDER BY (CASE WHEN pinned = 1 AND resolved = 0 THEN 0 ELSE 1 END),"
            "          resolved ASC, id DESC LIMIT ? OFFSET ?",
            (BOARD_PAGE, offset),
        ).fetchall()
        posting_open = ark_db.get_setting(conn, "public_board_post", "1") != "0"

    if rows:
        items = "".join(_note_item(r) for r in rows)
        listing = f'<h2>What people wrote ({total})</h2><ol class="clean">{items}</ol>'
        listing += _pager(page_no, offset + len(rows), total)
    else:
        listing = ('<h2>Nothing here yet</h2>'
                   '<p class="hint">Be the first: write what is broken, what was fixed, '
                   'or who needs help. Everybody on the network can read it.</p>'
                   if page_no == 1 else
                   '<h2>That is all of it</h2>'
                   '<p class="hint">There are no older messages. Go back to the newest.</p>'
                   '<div class="nav"><a href="/board">Newest messages</a></div>')

    thanks = ('<p class="status"><span class="dot" aria-hidden="true"></span>'
              '<span><b>Saved.</b> Your message is on the board for everyone to see.</span></p>'
              if ok else "")
    form = f"""
  <form action="/board" method="post">
    <label for="name">Your name (or leave blank)</label>
    <input id="name" name="name" type="text" maxlength="{MAX_NAME}" placeholder="e.g. Sam" autocomplete="off">
    <label for="body">Your message</label>
    <textarea id="body" name="body" maxlength="{MAX_NOTE}" required
              placeholder="The tap in the back garden is leaking. I have spare washers."></textarea>
    <button class="go-btn" type="submit">Put it on the board</button>
  </form>
  <p class="note">Anyone on this Wi-Fi can read and post here. Keep it useful, keep it kind.</p>"""
    if not posting_open:
        form = ('<p class="status"><span class="dot" aria-hidden="true"></span>'
                '<span><b>New messages are switched off.</b> The owner has paused posting for '
                'now. You can still read everything below.</span></p>')

    body = f"""{thanks}
  {form}
  {listing}"""
    return page("Message Board", body, here="/board",
                tagline="Notes for the next person — what broke, what got fixed, who needs a hand.")


def _note_item(row) -> str:
    """One message. Pinned and sorted markers are labels, not different pages."""
    badges = ""
    if row["pinned"]:
        badges += '<span class="badge">Pinned</span>'
    if row["resolved"]:
        badges += '<span class="badge done">Sorted out</span>'
    return (f'<li><b>{esc(row["name"])}</b>{badges}'
            f'<span class="sub">{esc(ago(row["ts"]))}</span>'
            f'<span class="sub" style="color:var(--ink);white-space:pre-wrap">'
            f'{esc(row["body"])}</span></li>')


def _pager(page_no: int, shown: int, total: int) -> str:
    links = []
    if page_no > 1:
        links.append(f'<a href="/board?p={page_no - 1}">Newer messages</a>')
    if shown < total:
        links.append(f'<a href="/board?p={page_no + 1}">Older messages</a>')
    return f'<div class="nav">{"".join(links)}</div>' if links else ""


@app.post("/board", response_class=HTMLResponse)
def board_post(name: str = Form(""), body: str = Form("")):
    if setting("public_board_post") == "0":
        return error_page("New messages are switched off.",
                          "The owner has paused posting. Reading still works — nothing you "
                          "wrote was lost, but nothing was added either.", 403)
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
        visible = conn.execute(
            "SELECT id, ts, name, display, stored, size, note FROM files"
            " WHERE hidden = 0 ORDER BY id DESC"
        ).fetchall()
        uploads_open = ark_db.get_setting(conn, "public_uploads", "1") != "0"
    # A row can outlive its file without anything being broken — `ark-restore
    # --no-files` puts the records back on a card whose File Box is empty, and a
    # folder cleaned by hand does the same — so the listing offers only what is
    # really there.  The count follows the list for the same reason.
    rows = _present(visible)
    total = len(rows)
    rows = rows[:FILES_PAGE]
    if rows:
        items = "".join(
            f'<li><a href="/drop/{esc(urllib.parse.quote(_stored_name(r), safe=""))}">'
            f'{esc(r["display"] or r["name"])}</a>'
            f'<span class="sub">{esc(human_size(r["size"]))} · added {esc(ago(r["ts"]))}</span>'
            + (f'<span class="sub">{esc(r["note"])}</span>' if r["note"] else "")
            + "</li>"
            for r in rows
        )
        heading = f'Files people shared ({total})'
        listing = f'<h2>{esc(heading)}</h2><ul class="clean">{items}</ul>'
        if total > len(rows):
            listing += ('<p class="note">Showing the newest '
                        f'{len(rows)} of {total}. Older files are still here — ask the owner '
                        'if you need one of them.</p>')
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
    if uploads_open:
        form = f"""
  <form action="/files" method="post" enctype="multipart/form-data">
    <label for="file">Choose a file</label>
    <input id="file" name="file" type="file" required>
    <label for="note">A short note (or leave blank)</label>
    <input id="note" name="note" type="text" maxlength="140" placeholder="Spare gasket — fits the big tap">
    <button class="go-btn" type="submit">Put it in the box</button>
  </form>
  <p class="note">Anyone on this Wi-Fi can download what you add. Do not put private things here.
     Files up to {MAX_UPLOAD // (1024 ** 3)} GB.</p>"""
    else:
        form = ('<p class="status"><span class="dot" aria-hidden="true"></span>'
                '<span><b>New uploads are switched off.</b> The owner has paused sharing for '
                'now. Everything already in the box can still be downloaded.</span></p>')
    body = f"""{banner}{form}
  {listing}"""
    return page("File Box", body, here="/files",
                tagline="Swap files with everyone on the network — no account, no email.")


def _stored_name(row) -> str:
    """The name on disk. Older rows only have 'name', which was the disk name."""
    return row["stored"] or row["name"]


def _present(rows) -> list:
    """Only the rows whose file is on the card.

    A row without a file is not a corruption: `ark-restore --no-files` puts the
    records back while the File Box stays empty, and a folder tidied by hand
    leaves rows behind.  The download route already answers 404 for those, but a
    link that 404s is a dead end for a reader who has never met a 404 — so the
    listing, and the number above it, count only what can really be opened.

    An unreadable folder is not an empty one: if the listing cannot be read, the
    rows are shown unfiltered rather than telling somebody nothing was ever
    shared.
    """
    folder = ark_storage.drop_dir()
    try:
        names = {entry.name for entry in os.scandir(folder) if entry.is_file()}
    except OSError:
        return list(rows)
    return [row for row in rows if _stored_name(row) in names]


def _download_header(display: str) -> str:
    """A Content-Disposition value that cannot carry a newline into the wire.

    The display name is whatever a reader typed, so it is user input.  The
    ASCII fallback is deliberately dull; the real name travels RFC 5987-encoded.
    """
    plain = "".join(ch if 32 <= ord(ch) < 127 and ch not in '\\";' else "_"
                    for ch in (display or ""))[:120].strip() or "download"
    return ("attachment; filename=\"" + plain + "\"; filename*=UTF-8''"
            + urllib.parse.quote(display or "download", safe=""))


@app.get("/drop/{name}")
def drop_download(name: str) -> Response:
    """Serve a shared file only while its row says it is visible.

    nginx used to alias this folder directly, which meant a file the owner had
    hidden (or one whose row never existed) was still one guessed URL away.
    The database is now the gate: no visible row, no bytes.
    """
    bare = (name or "").strip()
    if (not bare or bare != Path(bare).name or "/" in bare or "\\" in bare
            or "\x00" in bare or bare in (".", "..")):
        return Response("Not here", status_code=404)
    with ark_db.read() as conn:
        row = conn.execute(
            "SELECT * FROM files WHERE hidden = 0 AND (stored = ? OR (stored = '' AND name = ?))"
            " ORDER BY id DESC LIMIT 1", (bare, bare)).fetchone()
    if row is None:
        return Response("Not here", status_code=404)
    folder = ark_storage.drop_dir()
    try:
        target = (folder / bare).resolve()
        target.relative_to(folder.resolve())
    except (OSError, ValueError):
        return Response("Not here", status_code=404)
    if not target.is_file():
        return Response("Not here", status_code=404)
    display = str(row["display"] or row["name"] or bare)
    media = mimetypes.guess_type(display)[0] or "application/octet-stream"
    return FileResponse(target, media_type=media,
                        headers={"Content-Disposition": _download_header(display),
                                 "Cache-Control": "public, max-age=3600",
                                 "X-Content-Type-Options": "nosniff"})


@app.post("/files", response_class=HTMLResponse)
def files_post(file: UploadFile = File(None), note: str = Form("")):
    if setting("public_uploads") == "0":
        return error_page("New uploads are switched off.",
                          "The owner has paused sharing for now. Everything already in the "
                          "box can still be downloaded. Nothing was stored.", 403)
    if file is None or not file.filename:
        return RedirectResponse("/files?err=No%20file%20was%20chosen", status_code=303)

    # One write path for every upload: a hidden temp file, the cap enforced
    # while it streams, then an atomic move that can never replace another
    # person's file of the same name.
    try:
        stored = ark_storage.spool(file.file.read, file.filename)
    except ark_storage.Refused as exc:
        return RedirectResponse("/files?err=" + urllib.parse.quote(str(exc)), status_code=303)
    finally:
        file.file.close()

    with ark_db.tx() as conn:
        conn.execute(
            "INSERT INTO files (ts, name, stored, display, size, note, sha256)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (now_iso(), stored.display, stored.stored, stored.display, stored.size,
             (note or "").strip()[:140], stored.sha256),
        )
    return RedirectResponse("/files?ok=1", status_code=303)

