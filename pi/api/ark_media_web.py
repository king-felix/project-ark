"""Project ARK — the public shelf: films a reader can browse, and a player that works.

Two rules shape every page here:

* **A reader may only see what the owner has published.**  ``state = published``
  is checked on the page, on the picture, on the captions and on the bytes
  themselves, so a guessed number is a 404 like any other.
* **The bytes are served in pieces.**  A phone that seeks to the middle of a
  two-hour film asks for a *range*, and answering that with the whole file is
  how a "player" becomes a download that appears to hang.  ``ranged()`` is the
  one place ranges are parsed and answered — with 206, ``Content-Range``,
  ``Accept-Ranges``, ``HEAD`` and a correct 416 for a range that does not exist.

The player is the browser's own ``<video>`` element: it already does seeking,
fullscreen, volume, captions and picture-in-picture on every phone and laptop,
and it costs no download.  The small script on the page adds only what the
element does not have — a speed control, a picture-in-picture button, and a
resume position kept in *this browser* (never on the box, never shared).
"""

from __future__ import annotations

import html
import os
import re
import urllib.parse
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response, StreamingResponse

import ark_db
import ark_media

CHUNK = 256 * 1024
_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")


def esc(value) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


# ---------------------------------------------------------------------------
#  ranges — the one implementation
# ---------------------------------------------------------------------------
class BadRange(Exception):
    """A Range header that cannot be honoured: answered 416, never ignored."""


def parse_range(value: str, size: int) -> Optional[tuple]:
    """``bytes=a-b`` → an inclusive ``(start, end)``; ``None`` means "send it all".

    Only a single range is honoured.  A multi-part range is legal HTTP and a
    nuisance in practice: every browser that matters asks for one range, and a
    416 with the real size is a truthful answer to anything stranger than that.
    """
    if not value:
        return None
    text = value.strip()
    match = _RANGE.match(text)
    if not match:
        raise BadRange(text)
    first, last = match.group(1), match.group(2)
    if first == "" and last == "":
        raise BadRange(text)
    if first == "":
        length = int(last)
        if length <= 0:
            raise BadRange(text)
        start = max(0, size - length)
        end = size - 1
    else:
        start = int(first)
        end = int(last) if last else size - 1
    if start >= size or start > end or size <= 0:
        raise BadRange(text)
    return start, min(end, size - 1)


def ranged(request: Request, path: Path, media_type: str, *,
           filename: Optional[str] = None, cache: str = "public, max-age=300",
           private: bool = False) -> Response:
    """Answer a GET or HEAD for a file, honouring ``Range`` correctly.

    ``Content-Length`` is always the length of *this* response, ``Content-Range``
    is always inclusive, and a file that changed under a seek is served whole
    rather than sliced across two versions.
    """
    try:
        info = os.stat(str(path))
        size = info.st_size
    except OSError:
        return Response("Not here", status_code=404)
    etag = f'"{int(info.st_mtime)}-{size}"'
    headers = {
        "Accept-Ranges": "bytes",
        "ETag": etag,
        "Cache-Control": ("private, max-age=0" if private else cache),
        "X-Content-Type-Options": "nosniff",
    }
    if filename:
        headers["Content-Disposition"] = _attachment(filename)
    if request.headers.get("if-none-match") == etag and not request.headers.get("range"):
        return Response(status_code=304, headers=headers)

    requested = request.headers.get("range", "")
    if request.headers.get("if-range") and request.headers["if-range"] not in (etag,):
        requested = ""
    try:
        span = parse_range(requested, size)
    except (BadRange, ValueError):
        headers["Content-Range"] = f"bytes */{size}"
        return Response("That part of the file does not exist.", status_code=416,
                        headers=headers)
    start, end = span if span else (0, max(0, size - 1))
    length = (end - start + 1) if size else 0
    headers["Content-Length"] = str(length)
    status = 206 if span else 200
    if span:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    if request.method == "HEAD":
        return Response(status_code=status, media_type=media_type, headers=headers)

    def body():
        with open(path, "rb") as handle:
            handle.seek(start)
            left = length
            while left > 0:
                piece = handle.read(min(CHUNK, left))
                if not piece:
                    break
                left -= len(piece)
                yield piece

    return StreamingResponse(body(), status_code=status, media_type=media_type,
                             headers=headers)


def _attachment(display: str) -> str:
    plain = "".join(ch if 32 <= ord(ch) < 127 and ch not in '\\";' else "_"
                    for ch in (display or ""))[:120].strip() or "video"
    return ("attachment; filename=\"" + plain + "\"; filename*=UTF-8''"
            + urllib.parse.quote(display or "video", safe=""))


# ---------------------------------------------------------------------------
#  what a card shows
# ---------------------------------------------------------------------------
def poster_url(item) -> str:
    return f"/media/{int(item['id'])}/poster"


def stream_url(item) -> str:
    return f"/media/{int(item['id'])}/stream"


def play_type(item) -> str:
    """The type of the file that will actually be sent, not of the original.

    A direct WebM is served as itself; everything else the box serves is an MP4
    (the original, or the copy it made).  Getting this wrong makes a player show
    "cannot play" for a file it can.
    """
    stored = str(item["play_stored"] or "")
    if stored:
        return "video/webm" if stored.endswith(".webm") else "video/mp4"
    return "video/webm" if str(item["filename"] or "").lower().endswith(".webm") else "video/mp4"


def _meta_line(item, status: str) -> str:
    bits = [ark_media.human_duration(item["duration"])]
    size = ark_media.footprint(item)
    if size:
        bits.append(ark_media.human_size(size))
    bits.append(ark_media.status_words(status))
    return " · ".join(bits)


def card(item, status: str) -> str:
    """One film, as a link with a picture.  Text is escaped; nothing is fetched."""
    return f"""
  <a class="film" href="/media/{int(item['id'])}">
    <img class="poster" src="{esc(poster_url(item))}" alt="" loading="lazy" width="360" height="203">
    <span class="film-body">
      <span class="film-title">{esc(item['title'])}</span>
      <span class="film-meta">{esc(_meta_line(item, status))}</span>
    </span>
  </a>"""


def fallback_poster() -> bytes:
    """A neutral card for a film whose picture could not be taken.

    Inline SVG on purpose: no font, no image file, no network, and it renders on
    a phone that has never had a working connection to anything else.
    """
    return b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 360 203" role="img"
 aria-label="No picture for this film">
 <rect width="360" height="203" fill="#111113"/>
 <rect x="0.5" y="0.5" width="359" height="202" fill="none" stroke="#26262a"/>
 <circle cx="180" cy="94" r="34" fill="#0a1a10" stroke="#1f4a31"/>
 <path d="M170 78 L198 94 L170 110 Z" fill="#8ee6ac"/>
 <text x="180" y="152" text-anchor="middle" fill="#b6b6be"
   font-family="system-ui,sans-serif" font-size="15">Picture not ready</text>
</svg>"""


# ---------------------------------------------------------------------------
#  the pages
# ---------------------------------------------------------------------------
SORTS = (("recent", "Newest first"), ("title", "Title A–Z"), ("longest", "Longest first"),
         ("largest", "Largest first"), ("oldest", "Oldest first"))
READY = (("", "Everything"), ("ready", "Ready to play"), ("working", "Getting ready"),
         ("needs", "Needs preparing"))
PAGE_SIZE = ark_media.PAGE


def _query(**values) -> str:
    clean = {k: v for k, v in values.items() if v not in (None, "", 0, "1")}
    if clean.get("page") in (1, "1"):
        clean.pop("page", None)
    return urllib.parse.urlencode(clean)


def _filters(values: dict) -> str:
    """The filter row as plain links — no script, one tap each."""
    out = []
    for key, label in READY:
        current = values.get("ready", "")
        href = "/media/?" + _query(q=values.get("q", ""), sort=values.get("sort", ""),
                                   category=values.get("category", ""), ready=key)
        on = ' aria-current="true"' if current == key else ""
        out.append(f'<a class="chip" href="{esc(href)}"{on}>{esc(label)}</a>')
    for category in values.get("categories") or []:
        key = category
        current = values.get("category", "")
        href = "/media/?" + _query(q=values.get("q", ""), sort=values.get("sort", ""),
                                   ready=values.get("ready", ""), category=key)
        on = ' aria-current="true"' if current == key else ""
        out.append(f'<a class="chip" href="{esc(href)}"{on}>{esc(category)}</a>')
    return '<nav class="chips" aria-label="Show">' + "".join(out) + "</nav>"


def _sort_form(values: dict) -> str:
    options = "".join(
        f'<option value="{esc(key)}"' + (" selected" if values.get("sort", "recent") == key else "")
        + f'>{esc(label)}</option>' for key, label in SORTS)
    hidden = "".join(
        f'<input type="hidden" name="{esc(name)}" value="{esc(value)}">'
        for name, value in (("q", values.get("q", "")), ("ready", values.get("ready", "")),
                            ("category", values.get("category", "")))
        if value)
    return f"""
  <form class="sortbar" method="get" action="/media/">
    {hidden}
    <label for="sort">Order</label>
    <select id="sort" name="sort" onchange="this.form.submit()">{options}</select>
    <button class="smallbtn" type="submit">Sort</button>
  </form>"""


def _pager(result: dict, values: dict) -> str:
    if result["pages"] <= 1:
        return ""
    here = result["page"]
    previous = ("/media/?" + _query(q=values.get("q", ""), sort=values.get("sort", ""),
                                    ready=values.get("ready", ""),
                                    category=values.get("category", ""),
                                    page=here - 1)) if result["has_prev"] else ""
    following = ("/media/?" + _query(q=values.get("q", ""), sort=values.get("sort", ""),
                                     ready=values.get("ready", ""),
                                     category=values.get("category", ""),
                                     page=here + 1)) if result["has_next"] else ""
    left = f'<a class="chip" href="{esc(previous)}">Previous</a>' if previous else ""
    right = f'<a class="chip" href="{esc(following)}">Next</a>' if following else ""
    return (f'<nav class="pager" aria-label="Pages">{left}'
            f'<span class="pageno">Page {here} of {result["pages"]}</span>{right}</nav>')


def build_router(page) -> APIRouter:
    """Wire the shelf onto the portal's own page shell.

    ``page`` is ``main.page`` — passed in rather than imported so the public
    backend does not import itself.
    """
    router = APIRouter(include_in_schema=False)

    def _published(media_id: int):
        with ark_db.read() as conn:
            row = ark_media.item(conn, media_id)
            if row is None or str(row["state"]) != ark_media.STATE_PUBLISHED:
                return None, None
            job = ark_media.running_job(conn, media_id)
            last = ark_media.last_job(conn, media_id, kind="prepare")
            subs = ark_media.captions(conn, media_id)
        return row, {"job": job, "last": last, "subs": subs}

    @router.get("/media", response_class=HTMLResponse, include_in_schema=False)
    @router.get("/media/", response_class=HTMLResponse)
    def library(request: Request, q: str = "", sort: str = "recent", ready: str = "",
                category: str = "", page_no: int = Query(1, alias="page")):
        # `page` is the template function's name, so the query parameter arrives
        # under an alias rather than shadowing it.
        values = {"q": q.strip()[:120], "sort": sort if sort in ark_media.SORTS else "recent",
                  "ready": ready if ready in dict(READY) else "", "category": category[:60]}
        with ark_db.read() as conn:
            result = ark_media.listing(conn, q=values["q"], state=ark_media.STATE_PUBLISHED,
                                       ready=values["ready"], category=values["category"],
                                       sort=values["sort"], page=page_no, per_page=PAGE_SIZE)
            values["categories"] = ark_media.categories(conn)
            total_all = ark_media.count(conn, state=ark_media.STATE_PUBLISHED)
            jobs = conn.execute("SELECT COUNT(*) FROM media_jobs WHERE state IN"
                                " ('queued','running')").fetchone()[0]
            rows = list(result["rows"])

        cards = []
        for row in rows:
            cards.append(card(row, ark_media.status_of(row, ark_media.has_bytes(row))))

        banner = ""
        if request.query_params.get("sent"):
            banner = ('<p class="status"><span class="dot" aria-hidden="true"></span>'
                      '<span><b>Thank you — it has been sent for a look.</b> A film somebody '
                      'shares is checked by the owner before it appears here.</span></p>')
        elif request.query_params.get("err"):
            banner = ('<p class="status"><span class="dot no" aria-hidden="true"></span>'
                      f'<span><b>Not added.</b> {esc(request.query_params["err"][:300])}</span></p>')
        elif request.query_params.get("ok"):
            banner = ('<p class="status"><span class="dot" aria-hidden="true"></span>'
                      f'<span>{esc(request.query_params["ok"][:300])}</span></p>')

        if cards:
            grid = '<div class="films">' + "".join(cards) + "</div>"
        elif values["q"] or values["ready"] or values["category"]:
            grid = ('<h2>Nothing matched</h2><p class="hint">Try a shorter word, or show '
                    'everything.</p>')
        elif total_all:
            grid = ('<h2>Nothing here yet</h2><p class="hint">Films the owner adds appear '
                    'here once they are ready to play.</p>')
        else:
            grid = ('<h2>The shelf is empty</h2><p class="hint">Films kept on this box will '
                    'appear here — nothing to sign in to, nothing to download first.</p>')

        search = f"""
  <form class="searchbar" method="get" action="/media/">
    <label for="q">Find a film</label>
    <input id="q" name="q" type="search" value="{esc(values['q'])}" placeholder="A word from its name">
    <input type="hidden" name="sort" value="{esc(values['sort'])}">
    <button class="go-btn" type="submit">Search</button>
  </form>"""

        # The browser-local row is drawn by a few lines of script from this
        # device's own storage; with no script it simply is not there.
        resume = """
  <section id="continue" hidden>
    <h2>Carry on watching</h2>
    <p class="hint">Kept in this browser only — not on the box, and not shared with anyone.</p>
    <div class="films" id="continue-films"></div>
    <button class="smallbtn" type="button" id="forget-history">Clear this list</button>
  </section>"""

        body = f"""{banner}{search}
  {_filters(values)}
  {_sort_form(values)}
  <p class="hint">{result['total']} film{'s' if result['total'] != 1 else ''} on the shelf"""
        if jobs:
            body += f" · {jobs} being prepared"
        body += f"""</p>
  {grid}
  {_pager(result, values)}
  {resume}
  <p class="note">Some films need a moment of preparation before they can play on a phone.
     If one says <b>Needs preparing</b>, ask the owner — once it is ready the box keeps
     only the copy that plays here.</p>
<script>
(function () {{
  var KEY = "ark.media.recent";
  function read() {{ try {{ return JSON.parse(localStorage.getItem(KEY) || "[]"); }}
                    catch (e) {{ return []; }} }}
  function write(list) {{ try {{ localStorage.setItem(KEY, JSON.stringify(list)); }} catch (e) {{}} }}
  var box = document.getElementById("continue");
  var list = read().filter(function (x) {{ return x && x.id && x.left > 5; }});
  if (box && list.length) {{
    var holder = document.getElementById("continue-films");
    list.slice(0, 8).forEach(function (one) {{
      var a = document.createElement("a");
      a.className = "film"; a.href = "/media/" + encodeURIComponent(one.id);
      var img = document.createElement("img");
      img.className = "poster"; img.loading = "lazy"; img.alt = "";
      img.src = "/media/" + encodeURIComponent(one.id) + "/poster";
      var body = document.createElement("span"); body.className = "film-body";
      var title = document.createElement("span"); title.className = "film-title";
      title.textContent = one.title || ("Film " + one.id);
      var meta = document.createElement("span"); meta.className = "film-meta";
      meta.textContent = "Stopped at " + clock(one.pos) + " of " + clock(one.dur);
      body.appendChild(title); body.appendChild(meta);
      a.appendChild(img); a.appendChild(body); holder.appendChild(a);
    }});
    box.hidden = false;
  }}
  function clock(s) {{
    s = Math.max(0, Math.round(s || 0));
    var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
    return (h ? h + ":" + String(m).padStart(2, "0") : m) + ":" + String(x).padStart(2, "0");
  }}
  var forget = document.getElementById("forget-history");
  if (forget) forget.addEventListener("click", function () {{
    write([]); if (box) box.hidden = true;
  }});
}})();
</script>"""
        return page("Media", body, here="/media/",
                    tagline="Films kept on this box — tap one to watch, no sign-in.")

    @router.get("/media/{media_id}", response_class=HTMLResponse)
    def watch(request: Request, media_id: int):
        row, extra = _published(media_id)
        if row is None:
            return page("Not here", '<h2>That film is not on the shelf</h2>'
                        '<p class="hint">It may have been removed, or it may be waiting for '
                        'the owner to look at it.</p><p><a href="/media/">Back to Media</a></p>',
                        here="/media/", status=404)
        original = ark_media.original_present(row)
        last = extra["last"]
        # The film's own page does not come through `listing()`, so the latest
        # job has to be handed over explicitly — otherwise a failed preparation
        # reads as "never prepared" and the page promises work nobody is doing.
        status = ark_media.status_of(row, ark_media.has_bytes(row),
                                     str(last["state"] or "") if last is not None else "")
        playable = ark_media.playable(row)
        job = extra["job"]

        if playable is not None:
            tracks = "".join(
                f'<track kind="subtitles" srclang="{esc(s["lang"])}" label="{esc(s["label"])}"'
                f' src="/media/{int(row["id"])}/captions/{esc(s["lang"])}">'
                for s in extra["subs"])
            player = f"""
  <div class="stage">
    <video id="player" controls playsinline preload="metadata"
           poster="{esc(poster_url(row))}">
      <source src="{esc(stream_url(row))}" type="{esc(play_type(row))}">
      {tracks}
      <p>This browser cannot play the film here.
         <a href="/media/{int(row['id'])}/stream" download>Download it instead</a>.</p>
    </video>
  </div>
  <div class="playerbar">
    <a class="smallbtn" href="/media/">Back to Media</a>
    <label class="sr-only" for="speed">Speed</label>
    <select id="speed" class="smallbtn">
      <option value="0.75">0.75×</option><option value="1" selected>1×</option>
      <option value="1.25">1.25×</option><option value="1.5">1.5×</option><option value="2">2×</option>
    </select>
    <button class="smallbtn" type="button" id="pip" hidden>Small window</button>
    <button class="smallbtn" type="button" id="restart" hidden>Start over</button>
  </div>
  <p class="note" id="playback-error" hidden></p>"""
        else:
            detail = ""
            if status == "missing":
                waiting = "The file is not here"
                detail = " The film's file is not on the card any more."
            elif job is not None:
                waiting = "Getting ready"
                detail = (" The box is working on it now." if str(job["state"]) == "running"
                          else " It is next in the queue.")
            elif status == "failed":
                waiting = "This film could not be prepared"
                # The detail the box wrote down is for the owner's page: it can
                # be an FFmpeg line or a Python exception, and this page is read
                # by somebody who has never heard of either.
                detail = (" The original file is kept. Ask the owner to try again —"
                          " the reason is on their page.")
            else:
                waiting = "Not ready yet"
                detail = " Ask the owner to prepare it before it can play."
            player = f"""
  <p class="status"><span class="dot wait" aria-hidden="true"></span>
    <span><b>{esc(waiting)}.</b>{detail}</span></p>
  <p><a class="smallbtn" href="/media/">Back to Media</a></p>"""

        download = ""
        if original:
            download = (f'<p><a class="go-link" href="/media/{int(row["id"])}/download">'
                        'Download the original</a></p>')

        subs = ""
        if extra["subs"]:
            links = " ".join(
                f'<a class="chip" href="/media/{int(row["id"])}/captions/{esc(s["lang"])}">'
                f'{esc(s["label"])} captions</a>' for s in extra["subs"])
            subs = f'<h2>Captions</h2><p>{links}</p>'

        facts = [
            ("Original file", str(row["filename"] or "")),
            ("Length", ark_media.human_duration(row["duration"])),
            ("Size on this box", ark_media.human_size(ark_media.footprint(row))),
            ("Picture", f'{int(row["width"])}×{int(row["height"])} · '
                        f'{str(row["vcodec"] or "not known")}'),
            ("Sound", str(row["acodec"] or "none")),
            ("Added", str(row["ts"] or "")[:10]),
            ("On this box", ark_media.status_words(status)),
        ]
        detail_rows = "".join(
            f'<li><b>{esc(label)}</b> <span class="sub">{esc(value)}</span></li>'
            for label, value in facts if value)
        note = ""
        if (str(row["plan"]) in (ark_media.REMUX, ark_media.CONVERT)
                and str(row["play_stored"] or "")):
            if not original:
                note = ('<p class="note">The box made this film into a copy that plays '
                        'here and removed the original upload to save room. What you are '
                        'watching is that copy.</p>')
            else:
                note = ('<p class="note">The box made a copy of this film that plays here. '
                        'The original upload is still on the card.</p>')

        body = f"""{player}
  <h2>About this film</h2>
  <ul class="clean facts">{detail_rows}</ul>
  {note}
  {subs}
  {download}
<script>
(function () {{
  var video = document.getElementById("player");
  if (!video) return;
  var id = {int(row['id'])};
  var KEY = "ark.media.pos." + id, RECENT = "ark.media.recent";
  var speed = document.getElementById("speed");
  var pip = document.getElementById("pip");
  var restart = document.getElementById("restart");
  var problem = document.getElementById("playback-error");
  function read(key, fallback) {{
    try {{ var v = JSON.parse(localStorage.getItem(key)); return v === null ? fallback : v; }}
    catch (e) {{ return fallback; }}
  }}
  function save(key, value) {{ try {{ localStorage.setItem(key, JSON.stringify(value)); }} catch (e) {{}} }}
  function clock(s) {{
    s = Math.max(0, Math.round(s || 0));
    var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60;
    return (h ? h + ":" + String(m).padStart(2, "0") : m) + ":" + String(x).padStart(2, "0");
  }}
  function remember(at) {{
    if (!isFinite(video.duration) || video.duration < 30) return;
    if (at === undefined) at = video.currentTime;
    if (!(at > 1)) return;
    var list = read(RECENT, []).filter(function (x) {{ return x && x.id !== id; }});
    list.unshift({{ id: id, title: {json_string(str(row['title']))}, pos: at,
                  dur: video.duration, left: video.duration - at }});
    save(RECENT, list.slice(0, 12));
  }}
  // Leaving the page must not erase a place somebody chose: a film that was only
  // just opened is at 0, and writing 0 over the position saved an hour ago is
  // how "resume" quietly stops working.
  function leaving() {{
    if (video.currentTime > 1) {{ save(KEY, video.currentTime); remember(); }}
  }}
  // The metadata may already be here: a script at the end of the body runs after
  // the <video> has had its chance to fetch the header, and an event that fired
  // a moment ago never fires again.  Ask once, listen otherwise.
  function resume() {{
    var saved = read(KEY, null);
    if (saved && saved > 15 && saved < video.duration - 5) {{
      restart.hidden = false;
      restart.textContent = "Start over (resume at " + clock(saved) + ")";
      video.currentTime = saved;
    }}
  }}
  if (video.readyState >= 1) resume();
  else video.addEventListener("loadedmetadata", resume);
  var last = 0;
  video.addEventListener("timeupdate", function () {{
    if (video.currentTime - last > 5) {{ last = video.currentTime; save(KEY, video.currentTime); }}
  }});
  video.addEventListener("pause", function () {{ save(KEY, video.currentTime); remember(); }});
  // A seek is a place somebody chose to be; a tab closing is the commonest way to
  // leave a film.  Both are written down, so "resume" is not a promise that only
  // works when a film happens to be five seconds longer than the last tick.
  video.addEventListener("seeked", function () {{ save(KEY, video.currentTime); }});
  window.addEventListener("pagehide", leaving);
  document.addEventListener("visibilitychange", function () {{
    if (document.visibilityState === "hidden") leaving();
  }});
  video.addEventListener("ended", function () {{ save(KEY, 0); remember(); }});
  video.addEventListener("error", function () {{
    problem.hidden = false;
    problem.textContent = "This browser could not play the film. Try the Download button below, or another browser.";
  }});
  if (restart) restart.addEventListener("click", function () {{
    video.currentTime = 0; save(KEY, 0); video.play();
  }});
  if (speed) speed.addEventListener("change", function () {{ video.playbackRate = Number(speed.value); }});
  if (pip && document.pictureInPictureEnabled) {{
    pip.hidden = false;
    pip.addEventListener("click", function () {{
      if (document.pictureInPictureElement) document.exitPictureInPicture();
      else video.requestPictureInPicture().catch(function () {{}});
    }});
  }}
}})();
</script>"""
        return page(str(row["title"]), body, here="/media/")

    @router.api_route("/media/{media_id}/poster", methods=["GET", "HEAD"])
    def poster(request: Request, media_id: int):
        row, _extra = _published(media_id)
        if row is None:
            return Response("Not here", status_code=404)
        path = ark_media.poster_path(row)
        if path is not None:
            return ranged(request, path, "image/jpeg", cache="public, max-age=3600")
        return Response(fallback_poster(), media_type="image/svg+xml",
                        headers={"Cache-Control": "public, max-age=300"})

    @router.api_route("/media/{media_id}/stream", methods=["GET", "HEAD"])
    def stream(request: Request, media_id: int):
        row, _extra = _published(media_id)
        if row is None:
            return Response("Not here", status_code=404)
        path = ark_media.playable(row)
        if path is None:
            return Response("That film is not ready to play yet.", status_code=409)
        return ranged(request, path, play_type(row))

    @router.api_route("/media/{media_id}/download", methods=["GET", "HEAD"])
    def download(request: Request, media_id: int):
        row, _extra = _published(media_id)
        if row is None:
            return Response("Not here", status_code=404)
        path = ark_media.original_path(row)
        if not path.is_file():
            return Response("Not here", status_code=404)
        guess = _guess_type(str(row["filename"] or ""))
        return ranged(request, path, guess, filename=str(row["filename"] or "video"),
                      cache="private, max-age=0")

    @router.api_route("/media/{media_id}/captions/{lang}", methods=["GET", "HEAD"])
    def captions(media_id: int, lang: str):
        row, extra = _published(media_id)
        if row is None:
            return Response("Not here", status_code=404)
        wanted = str(lang or "").lower()
        for sub in extra["subs"]:
            if str(sub["lang"]).lower() == wanted:
                path = ark_media.caption_path(sub)
                if path is None:
                    break
                try:
                    text = path.read_text(encoding="utf-8")
                except OSError:
                    break
                return Response(text, media_type="text/vtt; charset=utf-8",
                                headers={"Cache-Control": "public, max-age=300"})
        return Response("Not here", status_code=404)

    @router.get("/media/{media_id}/file", include_in_schema=False)
    def old_file(media_id: int):
        # A guessed, unpublished address must not become a download door.
        return RedirectResponse(f"/media/{int(media_id)}", status_code=307)

    return router


def _guess_type(name: str) -> str:
    import mimetypes
    return mimetypes.guess_type(name)[0] or "application/octet-stream"


def json_string(value: str) -> str:
    """A Python string as a JavaScript literal, safely — never a template hole."""
    import json
    return json.dumps(str(value or ""), ensure_ascii=False).replace("</", "<\\/")
