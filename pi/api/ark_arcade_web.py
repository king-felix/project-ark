"""Project ARK — the pages of ARK Arcade.

A guest reads the shelf and plays without giving a name.  A named player signs
in here, with their own cookie, on their own paths, and can keep game states on
the box.  Nothing in this module can reach ``/admin``; player names and
administrator accounts are different tables and different sessions.

The player itself is EmulatorJS, served from this box (``/arcade/emulator/``).
There is no CDN in any page and no external font: unplug the internet and the
shelf still plays.  The only JavaScript on a critical path is the player; the
shelf and every game card are plain HTML.
"""

from __future__ import annotations

import json
import mimetypes
import urllib.parse
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               RedirectResponse, Response)

import ark_arcade as arc
import ark_db
import ark_rommeta as rommeta
import ark_security as sec

router = APIRouter()

SAFE_EMULATOR_SUFFIXES = {".js", ".css", ".json", ".data", ".wasm", ".txt", ".md"}


# ---------------------------------------------------------------------------
#  small things every page needs
# ---------------------------------------------------------------------------
def page(title: str, body: str, *, profile_link: str = "", flash: str = "") -> HTMLResponse:
    template = (arc.template_dir() / "page.html").read_text(encoding="utf-8")
    return HTMLResponse(
        template.replace("{{TITLE}}", arc.esc(title))
                .replace("{{PROFILE_LINK}}", profile_link)
                .replace("{{FLASH}}", flash)
                .replace("{{BODY}}", body)
    )


def _profile_link(request: Request, conn) -> str:
    player = _player(request, conn, touch=False)
    if player:
        return f'<a href="/arcade/player">{arc.esc(player["display_name"] or player["username"])}</a>'
    return '<a href="/arcade/player">My saves</a>'


def _token(request: Request) -> str:
    return request.cookies.get(arc.COOKIE_NAME, "") or ""


def _player(request: Request, conn, *, touch: bool = True) -> Optional[Dict[str, Any]]:
    if not _player_origin(request):
        return None
    token = _token(request)
    if not token:
        return None
    if not arc.players_open(conn):
        # The owner has switched names off.  The saves stay on the card; the
        # session simply cannot be used until they are switched back on.
        return None
    player = arc.player_from_token(conn, token)
    if player and touch:
        arc.player_touch(conn, int(player["session_id"]))
    return player


def _client_ip(request: Request) -> str:
    return (request.client.host if request.client else "")[:80]


def _player_origin(request: Request) -> bool:
    """Passwords and saves only cross the origin nginx marks as the secure one.

    The public portal on :80 sends ``X-ARK-Player-Origin`` empty, so a header a
    visitor invents cannot reach the API through it.  The HTTPS player origin
    (:8445) sets it to ``1``.  ``testclient`` is the in-process test transport;
    it never exists on the box.
    """
    return (request.headers.get("x-ark-player-origin", "") == "1"
            or _client_ip(request) == "testclient")


def _secure_player_url(request: Request) -> str:
    raw = request.headers.get("host") or "ark.lan"
    if raw.startswith("["):                       # IPv6 literal, brackets kept
        host = raw.split("]", 1)[0] + "]"
    else:
        host = raw.split(":", 1)[0]
    path = request.url.path or "/arcade/player"
    return f"https://{host}:8445{path}"


def _to_secure(request: Request) -> RedirectResponse:
    return RedirectResponse(_secure_player_url(request), status_code=307)


def _flash(request: Request) -> str:
    err = request.query_params.get("err", "")
    ok = request.query_params.get("ok", "")
    if err:
        return f'<p class="arcade-flash bad" role="alert">{arc.esc(err[:300])}</p>'
    if ok:
        return f'<p class="arcade-flash" role="status">{arc.esc(ok[:300])}</p>'
    return ""


def _safe_next(raw: str) -> str:
    """Only a page on this shelf, never somewhere else."""
    if raw.startswith("/arcade/") and "\\" not in raw and "\n" not in raw and "\r" not in raw:
        return raw
    return "/arcade/"


def _set_player_cookie(response: Response, token: str) -> None:
    response.set_cookie(arc.COOKIE_NAME, token, max_age=arc.ABSOLUTE_TIMEOUT,
                        httponly=True, secure=True, samesite="lax", path="/")


def _clear_player_cookie(response: Response) -> None:
    response.delete_cookie(arc.COOKIE_NAME, path="/")



# ---------------------------------------------------------------------------
#  the shelf
# ---------------------------------------------------------------------------
def _game_card(game: Dict[str, Any]) -> str:
    system = arc.SYSTEMS.get(game["system"], {}).get("label", game["system"])
    facts = [system]
    if game["author"]:
        facts.append(game["author"])
    if game["license"]:
        facts.append(game["license"])
    # What the file itself says about the game, when this box has a list that
    # knows these bytes: the year, the maker, the kind of game it is.
    for extra in rommeta.game_facts(game, limit=3):
        if extra not in facts:
            facts.append(extra)
    star = ('<p class="arcade-favourite">Kept at the top</p>'
            if game.get("pref_favourite") else "")
    return f"""
    <li class="arcade-card">
      <h2><a href="/arcade/g/{arc.esc(game['slug'])}">{arc.esc(game['title'])}</a></h2>
      {star}
      <p>{arc.esc(game['description'] or 'Ready to play.')}</p>
      <p class="arcade-facts">{arc.esc(' · '.join(facts))}</p>
      <div class="arcade-play"><a href="/arcade/g/{arc.esc(game['slug'])}">Tap here to play</a></div>
    </li>"""


@router.get("/arcade/", response_class=HTMLResponse)
@router.get("/arcade", response_class=HTMLResponse, include_in_schema=False)
def library(request: Request) -> HTMLResponse:
    with ark_db.read() as conn:
        games = arc.published_games(conn)
        link = _profile_link(request, conn)
        player = _player(request, conn)
        named = player is not None
        if named:
            games = arc.published_games(conn, int(player["id"]))
    if games:
        body = (f"<h1>Games</h1>"
                f"<p class=\"arcade-lede\">Tap a game and it starts here in this browser. "
                f"Nothing to install, nothing to sign up for.</p>"
                f"<ul class=\"arcade-grid\">{''.join(_game_card(g) for g in games)}</ul>")
    else:
        body = ("<h1>Games</h1>"
                "<div class=\"arcade-empty\"><h2>No games on the shelf yet</h2>"
                "<p>The first games have not been put on this box yet. The rest of the "
                "box works normally; ask whoever looks after it to add them.</p></div>")
    if named:
        body += ('<p class="arcade-note">You are playing with a name, so your game saves '
                 'are kept on this box. <a href="/arcade/player">See my saves</a>.</p>')
    else:
        body += ('<p class="arcade-note">You are playing as a guest. Saves stay in this '
                 'browser on this device. <a href="/arcade/player">Use a name</a> to keep '
                 'them on the box instead.</p>')
    return page("Games", body, profile_link=link, flash=_flash(request))


def _unauthorised() -> JSONResponse:
    return JSONResponse({"ok": False, "error": "Sign in with your name first."}, status_code=401)


def _wrong_csrf() -> JSONResponse:
    return JSONResponse({"ok": False, "error": "That request did not come from this page. Reload and try again."},
                        status_code=403)


# ---------------------------------------------------------------------------
#  one game
# ---------------------------------------------------------------------------
def _prefs_panel(game: Dict[str, Any], prefs: Dict[str, Any], csrf: str) -> str:
    """Two choices, as a plain form — no JavaScript needed to keep them."""
    def checked(value: bool) -> str:
        return " checked" if value else ""

    return f"""
    <section class="arcade-prefs">
      <h2>How you like this game</h2>
      <form method="post" action="/arcade/player/prefs/{int(game['id'])}">
        <input type="hidden" name="csrf" value="{arc.esc(csrf)}">
        <input type="hidden" name="next" value="/arcade/g/{arc.esc(game['slug'])}">
        <label><input type="checkbox" name="favourite" value="1"{checked(prefs['favourite'])}>
          Keep this game at the top of the shelf</label>
        <label><input type="checkbox" name="autosave" value="1"{checked(prefs['autosave'])}>
          Save my game for me as I play, and start from that save next time</label>
        <button type="submit">Keep my choices</button>
      </form>
    </section>"""


def _save_panel(game: Dict[str, Any], player: Optional[Dict[str, Any]],
                rows: Dict[int, Dict[str, Any]], csrf: str,
                prefs: Optional[Dict[str, Any]] = None) -> str:
    if not player:
        return ('<p class="arcade-note">Playing as a guest: the player keeps a save in this '
                'browser. Sign in with a name to keep a save on the box and find it on '
                'another device.</p>')
    items = []
    for slot in (1, 2, 3):
        row = rows.get(slot)
        revision = int(row["revision"]) if row else 0
        when = (f'kept {arc.esc(row["updated_at"])}' if row else "empty")
        size = (f' · {row["size"] // 1024} KB' if row else "")
        items.append(f"""
        <tr>
          <th scope="row">Save {slot}</th>
          <td>{when}{size}</td>
          <td>
            <button type="button" class="small" data-act="save" data-slot="{slot}" data-rev="{revision}">Save here</button>
            <button type="button" class="small" data-act="load" data-slot="{slot}" data-rev="{revision}"{' disabled' if not row else ''}>Load</button>
          </td>
        </tr>""")
    cfg = json.dumps({"gameId": int(game["id"]), "csrf": csrf,
                      "base": f"/arcade/api/saves/{int(game['id'])}/state/",
                      "autosave": bool((prefs or {}).get("autosave"))}).replace("</", "<\\/")
    return f"""
    <section class="arcade-saves" id="arcade-savebox">
      <h2>Saves for {arc.esc(player['display_name'] or player['username'])}</h2>
      <p class="arcade-note">These are kept on this box under your name. A save is a
      snapshot of the game as it is right now.</p>
      <table class="arcade-table"><thead><tr><th>Slot</th><th>Kept</th><th>What to do</th></tr></thead>
      <tbody>{''.join(items)}</tbody></table>
      <p class="arcade-status" id="arcade-save-status" role="status"></p>
      <p class="arcade-note"><a href="/arcade/player">All my saves and my name</a></p>
      <script>{_save_script(cfg)}</script>
    </section>"""


def _save_script(cfg_json: str) -> str:
    return """
(function () {
  var cfg = __CFG__;
  var box = document.getElementById('arcade-savebox');
  var status = document.getElementById('arcade-save-status');
  function say(message, bad) { status.textContent = message; status.className = 'arcade-status' + (bad ? ' bad' : ''); }
  function ready() {
    return window.EJS_emulator && window.EJS_emulator.gameManager;
  }
  box.addEventListener('click', async function (event) {
    var button = event.target.closest('button[data-act]');
    if (!button) { return; }
    if (!ready()) { say('The game is still starting. Try again in a moment.', true); return; }
    var slot = button.getAttribute('data-slot');
    var act = button.getAttribute('data-act');
    try {
      if (act === 'save') {
        var state = window.EJS_emulator.gameManager.getState();
        if (!state || !state.length) { say('This game did not give a save to keep.', true); return; }
        var put = await fetch(cfg.base + slot, {
          method: 'PUT', credentials: 'same-origin',
          headers: {'X-ARK-CSRF': cfg.csrf, 'X-ARK-Revision': button.getAttribute('data-rev') || '0',
                    'Content-Type': 'application/octet-stream'},
          body: new Blob([state])
        });
        var putData = await put.json().catch(function () { return {}; });
        if (!put.ok) { say(putData.error || 'That save was not kept.', true); return; }
        button.setAttribute('data-rev', String(putData.revision));
        document.querySelector('button[data-act="load"][data-slot="' + slot + '"]').disabled = false;
        say('Saved to slot ' + slot + '.');
      } else {
        var got = await fetch(cfg.base + slot, {credentials: 'same-origin'});
        if (!got.ok) {
          var err = await got.json().catch(function () { return {}; });
          say(err.error || 'That save could not be read.', true); return;
        }
        window.EJS_emulator.gameManager.loadState(new Uint8Array(await got.arrayBuffer()));
        say('Loaded slot ' + slot + '.');
      }
    } catch (err) {
      say('The save did not go through. Try again.', true);
    }
  });
  if (cfg.autosave) {
    var autoRevision = 0;
    async function autoSave() {
      if (!ready()) { return; }
      var state = window.EJS_emulator.gameManager.getState();
      if (!state || !state.length) { return; }
      var put = await fetch(cfg.base + '0', {
        method: 'PUT', credentials: 'same-origin',
        headers: {'X-ARK-CSRF': cfg.csrf, 'X-ARK-Revision': String(autoRevision),
                  'Content-Type': 'application/octet-stream'},
        body: new Blob([state])
      });
      var data = await put.json().catch(function () { return {}; });
      if (put.ok) { autoRevision = Number(data.revision) || autoRevision + 1; }
    }
    async function autoLoad() {
      if (!ready()) { setTimeout(autoLoad, 1500); return; }
      var got = await fetch(cfg.base + '0', {credentials: 'same-origin'}).catch(function () { return null; });
      if (!got || !got.ok) { return; }
      autoRevision = Number(got.headers.get('X-ARK-Revision')) || 0;
      window.EJS_emulator.gameManager.loadState(new Uint8Array(await got.arrayBuffer()));
    }
    setTimeout(autoLoad, 3000);
    setInterval(autoSave, 60000);
  }
})();
""".replace("__CFG__", cfg_json)


@router.get("/arcade/g/{slug}", response_class=HTMLResponse)
def game_page(request: Request, slug: str) -> Response:
    with ark_db.read() as conn:
        game = arc.game_by_slug(conn, slug)
        if not game or not game["published"] or game["hidden"]:
            return page("Game not found",
                        "<h1>That game is not here</h1>"
                        "<p class=\"arcade-lede\">It may have been put away. "
                        "<a href=\"/arcade/\">See the games that are here</a>.</p>",
                        profile_link='<a href="/arcade/player">My saves</a>')
        player = _player(request, conn)
        rows = {}
        csrf = ""
        prefs = None
        if player:
            csrf = player["csrf"]
            rows = {int(r["slot"]): r for r in arc.saves_for(conn, int(player["id"]), int(game["id"]))
                    if r["kind"] == "state"}
            prefs = arc.player_prefs_set(conn, int(player["id"]), int(game["id"]), played=True)
        link = _profile_link(request, conn)
    system = arc.SYSTEMS.get(game["system"], {})
    facts = [system.get("label", game["system"])]
    if game["author"]:
        facts.append(game["author"])
    if game["license"]:
        facts.append(game["license"])
    for extra in rommeta.game_facts(game, limit=4):
        if extra not in facts:
            facts.append(extra)
    # Two plain sentences about where the name came from, printed only when they
    # are true: a card named from its file keeps its file's name and says nothing.
    recognised = rommeta.recognised(game)
    badge = ('<p class="arcade-note">This box recognised the game from the file '
             'itself, not from its name.</p>' if recognised else "")
    listed = rommeta.also_listed_as(game)
    listed_html = f'<p class="arcade-note">{arc.esc(listed)}</p>' if listed else ""
    config = {
        "player": "#arcade-stage",
        "core": game["core"],
        "gameUrl": f"/arcade/g/{game['slug']}/rom",
        "pathtodata": "/arcade/emulator/",
        "gameName": game["slug"],
        "startOnLoaded": True,
        "backgroundColor": "#000000",
        "volume": 0.5,
        "disableAutoLang": True,
    }
    def _js(value: Any) -> str:
        return json.dumps(value).replace("</", "<\\/")

    boot = "\n".join(f"window.EJS_{key} = {_js(value)};" for key, value in config.items())
    aspect = system.get("aspect") or "4 / 3"
    credits = (f'<p class="arcade-credits">{arc.esc(game["notices"])}</p>'
               if game["notices"] else "")
    body = f"""
    <h1>{arc.esc(game['title'])}</h1>
    <p class="arcade-lede">{arc.esc(game['description'] or 'Ready to play.')}</p>
    <p class="arcade-facts">{arc.esc(' · '.join(facts))}</p>
    {badge}
    {listed_html}
    {credits}
    <div id="arcade-frame" style="aspect-ratio: {arc.esc(aspect)}">
      <div id="arcade-stage" aria-label="{arc.esc(game['title'])} game player"></div>
    </div>
    <p class="arcade-note">Use the arrow keys or the on-screen pad. The game runs on this
    box; nothing is downloaded while you play. <a href="/arcade/static/notices.txt">About the games</a></p>
    {_prefs_panel(game, prefs, csrf) if prefs else ''}
    {_save_panel(game, player, rows, csrf, prefs)}
    <script>{boot}</script>
    <script src="/arcade/emulator/loader.js"></script>"""
    return page(game["title"], body, profile_link=link, flash=_flash(request))


@router.api_route("/arcade/g/{slug}/rom", methods=["GET", "HEAD"])
def game_rom(slug: str) -> Response:
    """One game's file.

    ``HEAD`` is answered as well as ``GET``: the player asks for the size with a
    HEAD before it downloads anything (it caches games in the browser and needs
    the length to tell a complete copy from a stale one).  A GET-only route
    answers 405 to that question, which is the answer a browser cannot use.
    """
    with ark_db.read() as conn:
        game = arc.game_by_slug(conn, slug)
        if not game or not game["published"] or game["hidden"]:
            return Response("Not here", status_code=404)
        path = arc.games_dir() / game["filename"]
    if not game["filename"] or game["filename"] != Path(game["filename"]).name:
        return Response("That game's file is missing", status_code=404)
    if not path.is_file():
        return Response("That game's file is missing", status_code=404)
    return FileResponse(path, media_type="application/octet-stream",
                        headers={"Content-Disposition": f'inline; filename="{arc.esc(game["slug"])}.rom"',
                                 "Cache-Control": "private, max-age=3600",
                                 "X-Content-Type-Options": "nosniff"})


def _inside(base: Path, raw: str) -> Optional[Path]:
    try:
        target = (base / raw).resolve()
        target.relative_to(base.resolve())
    except (OSError, ValueError):
        return None
    return target if target.is_file() else None


@router.api_route("/arcade/emulator/{path:path}", methods=["GET", "HEAD"])
def emulator_asset(path: str) -> Response:
    target = _inside(arc.emulator_dir(), path)
    if not target or target.suffix.lower() not in SAFE_EMULATOR_SUFFIXES:
        return Response("Not here", status_code=404)
    media = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    return FileResponse(target, media_type=media,
                        headers={"Cache-Control": "public, max-age=86400",
                                 "X-Content-Type-Options": "nosniff"})


@router.api_route("/arcade/static/{name}", methods=["GET", "HEAD"])
def arcade_static(name: str) -> Response:
    if name not in ("ark-arcade.css", "notices.txt"):
        return Response("Not here", status_code=404)
    target = arc.static_dir() / name
    if not target.is_file():
        return Response("Not here", status_code=404)
    media = "text/css; charset=utf-8" if name.endswith(".css") else "text/plain; charset=utf-8"
    return FileResponse(target, media_type=media,
                        headers={"Cache-Control": "public, max-age=3600"})


@router.api_route("/arcade/notices.txt", methods=["GET", "HEAD"], include_in_schema=False)
def notices() -> Response:
    target = arc.static_dir() / "notices.txt"
    if not target.is_file():
        return Response("Not here", status_code=404)
    return FileResponse(target, media_type="text/plain; charset=utf-8")



# ---------------------------------------------------------------------------
#  a player's name
# ---------------------------------------------------------------------------
def _back(raw: str, *, err: str = "", ok: str = "") -> RedirectResponse:
    target = _safe_next(raw)
    query = urllib.parse.urlencode({k: v for k, v in (("err", err), ("ok", ok)) if v})
    url = f"{target}{'&' if '?' in target else '?'}{query}" if query else target
    return RedirectResponse(url, status_code=303)


@router.get("/arcade/player", response_class=HTMLResponse)
def player_page(request: Request) -> Response:
    if not _player_origin(request):
        return _to_secure(request)
    with ark_db.read() as conn:
        player = _player(request, conn)
        open_names = arc.players_open(conn)
        link = _profile_link(request, conn)
        if not player:
            body = f"""
            <h1>My saves</h1>
            <p class="arcade-lede">Play as a guest, or use a name so your saves are kept
            on this box and follow you to another device.</p>
            <h2>Sign in</h2>
            <form class="arcade-form" method="post" action="/arcade/player/sign-in">
              <input type="hidden" name="next" value="/arcade/player">
              <label>Your name <input name="username" autocomplete="username" required maxlength="60"></label>
              <label>Your password <input name="password" type="password" autocomplete="current-password" required></label>
              <button type="submit">Sign in</button>
            </form>"""
            if open_names:
                body += """
            <h2>Make a name</h2>
            <form class="arcade-form" method="post" action="/arcade/player/new">
              <input type="hidden" name="next" value="/arcade/player">
              <label>Choose a name <input name="username" autocomplete="username" required minlength="2" maxlength="60"></label>
              <label>Choose a password <input name="password" type="password" autocomplete="new-password" required minlength="12"></label>
              <label>Same password again <input name="confirm" type="password" autocomplete="new-password" required minlength="12"></label>
              <button type="submit">Make my name</button>
            </form>"""
            else:
                body += f'<p class="arcade-note">{arc.esc(arc.PLAYERS_CLOSED)}</p>'
            body += ('<p class="arcade-note">A name is just for your games. It is not an '
                     'administrator account and it cannot change anything on this box.</p>')
            return page("My saves", body, profile_link=link, flash=_flash(request))

        rows = arc.saves_for(conn, int(player["id"]))
        csrf = player["csrf"]
        if rows:
            items = "".join(
                f"""<tr>
                  <td>{arc.esc(r['game_title'])}</td>
                  <td>{'Save ' + str(int(r['slot'])) if r['kind'] == 'state' else 'In-game battery'}</td>
                  <td>{int(r['size']) // 1024} KB<br><span class="arcade-facts">{arc.esc(r['updated_at'])}</span></td>
                  <td><form method="post" action="/arcade/player/saves/{int(r['id'])}/delete">
                    <input type="hidden" name="csrf" value="{arc.esc(csrf)}">
                    <button class="small arcade-danger" type="submit">Delete for ever</button>
                  </form></td>
                </tr>""" for r in rows)
            shelf = (f"<table class=\"arcade-table\"><thead><tr><th>Game</th><th>Save</th>"
                     f"<th>Kept</th><th></th></tr></thead><tbody>{items}</tbody></table>")
        else:
            shelf = ('<div class="arcade-empty"><h2>No saves yet</h2>'
                     '<p>Open a game and use one of its save slots. It will appear here.</p></div>')
        body = f"""
        <h1>{arc.esc(player['display_name'] or player['username'])}</h1>
        <p class="arcade-lede">Your saves are kept on this box under this name. You can
        take a copy with you and put it on another ARK.</p>
        {shelf}
        <p><a class="ark-btn" href="/arcade/player/export.zip">Download all my saves</a></p>
        <p class="arcade-note">Keep that file somewhere other than this box's own card.
        A copy on the same card does not survive that card dying.</p>
        <form class="arcade-form" method="post" action="/arcade/player/import.zip" enctype="multipart/form-data">
          <input type="hidden" name="csrf" value="{arc.esc(csrf)}">
          <label>Put a saved file back <input type="file" name="archive" accept=".zip,application/zip" required></label>
          <button type="submit">Put these saves back</button>
        </form>
        <form method="post" action="/arcade/player/sign-out">
          <input type="hidden" name="csrf" value="{arc.esc(csrf)}">
          <button type="submit">Sign out</button>
        </form>"""
        return page("My saves", body, profile_link=link, flash=_flash(request))



@router.post("/arcade/player/sign-in")
def player_sign_in(request: Request, username: str = Form(""), password: str = Form(""),
                   next: str = Form("/arcade/")) -> RedirectResponse:
    if not _player_origin(request):
        return _to_secure(request)
    with ark_db.read() as conn:
        player = arc.player_authenticate(conn, username, password, ip=_client_ip(request))
        if not player:
            return _back(next, err="That name and password did not match. Please try again.")
        session = arc.player_issue_session(conn, int(player["id"]), ip=_client_ip(request))
    response = _back(next)
    _set_player_cookie(response, session["token"])
    return response


@router.post("/arcade/player/new")
def player_new(request: Request, username: str = Form(""), password: str = Form(""),
               confirm: str = Form(""), next: str = Form("/arcade/")) -> RedirectResponse:
    if not _player_origin(request):
        return _to_secure(request)
    with ark_db.read() as conn:
        try:
            player = arc.player_register(conn, username, password, confirm)
        except ValueError as exc:
            return _back(next, err=str(exc))
        session = arc.player_issue_session(conn, int(player["id"]), ip=_client_ip(request))
    response = _back(next, ok="Your name is ready. Your saves will be kept on this box.")
    _set_player_cookie(response, session["token"])
    return response


@router.post("/arcade/player/sign-out")
def player_sign_out(request: Request, csrf: str = Form("")) -> RedirectResponse:
    if not _player_origin(request):
        return _to_secure(request)
    with ark_db.read() as conn:
        player = arc.player_from_token(conn, _token(request))
        if player:
            if not sec.csrf_ok(player["csrf"], csrf):
                return _back("/arcade/player", err="That request did not come from this page.")
            arc.player_sign_out(conn, _token(request))
    response = _back("/arcade/", ok="You are signed out. You can still play as a guest.")
    _clear_player_cookie(response)
    return response


@router.post("/arcade/player/prefs/{game_id}")
def player_prefs_save(request: Request, game_id: int, csrf: str = Form(""),
                      autosave: str = Form(""), favourite: str = Form(""),
                      next: str = Form("/arcade/")) -> RedirectResponse:
    if not _player_origin(request):
        return _to_secure(request)
    with ark_db.read() as conn:
        player = _player(request, conn)
        if not player:
            return _back(next, err="Sign in with your name first.")
        if not sec.csrf_ok(player["csrf"], csrf):
            return _back(next, err="That request did not come from this page.")
        try:
            arc.player_prefs_set(
                conn, int(player["id"]), int(game_id),
                autosave=autosave not in ("", "0", "off", "false"),
                favourite=favourite not in ("", "0", "off", "false"),
            )
        except arc.SaveRefused as exc:
            return _back("/arcade/", err=str(exc))
    return _back(next, ok="Your choices were kept.")


@router.post("/arcade/player/saves/{save_id}/delete")
def player_delete_save(request: Request, save_id: int, csrf: str = Form("")) -> RedirectResponse:
    if not _player_origin(request):
        return _to_secure(request)
    with ark_db.read() as conn:
        player = _player(request, conn, touch=False)
        if not player:
            return _back("/arcade/player", err="Sign in with your name first.")
        if not sec.csrf_ok(player["csrf"], csrf):
            return _back("/arcade/player", err="That request did not come from this page.")
        if not arc.player_save_delete(conn, int(player["id"]), int(save_id)):
            return _back("/arcade/player", err="That save was not there.")
    return _back("/arcade/player", ok="That save was deleted.")


@router.get("/arcade/player/export.zip")
def player_export(request: Request) -> Response:
    if not _player_origin(request):
        return _to_secure(request)
    with ark_db.read() as conn:
        player = _player(request, conn)
        if not player:
            return _back("/arcade/player", err="Sign in with your name first.")
        data = arc.player_export_bytes(conn, int(player["id"]))
        name = sec.normalise_username(player["username"]).replace("/", "-") or "player"
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="ark-saves-{name}.zip"',
                             "Cache-Control": "no-store"})



# ---------------------------------------------------------------------------
#  saves over the wire — the page's own small API
# ---------------------------------------------------------------------------
@router.get("/arcade/api/saves/{game_id}")
def api_saves(request: Request, game_id: int) -> JSONResponse:
    with ark_db.read() as conn:
        player = _player(request, conn)
        if not player:
            return _unauthorised()
        rows = arc.saves_for(conn, int(player["id"]), int(game_id))
    return JSONResponse({"ok": True, "saves": [
        {k: v for k, v in r.items() if k not in ("filename",)} for r in rows
    ]})


@router.get("/arcade/api/saves/{game_id}/{kind}/{slot}")
def api_save_get(request: Request, game_id: int, kind: str, slot: int) -> Response:
    with ark_db.read() as conn:
        player = _player(request, conn)
        if not player:
            return _unauthorised()
        try:
            row = arc.player_save_get(conn, int(player["id"]), int(game_id), kind, int(slot))
        except arc.SaveConflict as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=409)
        except arc.SaveRefused as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    if not row:
        return JSONResponse({"ok": False, "error": "There is no save in that slot yet."},
                            status_code=404)
    return Response(row["data"], media_type="application/octet-stream",
                    headers={"Content-Disposition": f'attachment; filename="{row["filename"]}"',
                             "Cache-Control": "no-store",
                             "X-ARK-Revision": str(int(row["revision"])),
                             "X-Content-Type-Options": "nosniff"})


@router.put("/arcade/api/saves/{game_id}/{kind}/{slot}")
async def api_save_put(request: Request, game_id: int, kind: str, slot: int) -> JSONResponse:
    with ark_db.read() as conn:
        player = _player(request, conn)
        if not player:
            return _unauthorised()
        supplied = request.headers.get("x-ark-csrf", "")
        if not sec.csrf_ok(player["csrf"], supplied):
            return _wrong_csrf()
        length = request.headers.get("content-length", "")
        if length.isdigit() and int(length) > arc.MAX_SAVE:
            return JSONResponse({"ok": False, "error": "That save is larger than this box keeps (16 MB)."},
                                status_code=413)
        data = await request.body()
        if len(data) > arc.MAX_SAVE:
            return JSONResponse({"ok": False, "error": "That save is larger than this box keeps (16 MB)."},
                                status_code=413)
        revision_raw = request.headers.get("x-ark-revision", "0")
        try:
            revision = int(revision_raw) if revision_raw else 0
        except ValueError:
            return JSONResponse({"ok": False, "error": "That save page was not understood. Reload the game."},
                                status_code=400)
        try:
            saved = arc.player_save_put(conn, int(player["id"]), int(game_id), kind, int(slot),
                                        data, expected_revision=revision)
        except arc.SaveConflict as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=409)
        except arc.SaveRefused as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    return JSONResponse({"ok": True, "save": {k: v for k, v in saved.items() if k != "filename"},
                         **{k: saved[k] for k in ("revision", "sha256", "size", "updated_at")}})


@router.delete("/arcade/api/saves/{save_id}")
def api_save_delete(request: Request, save_id: int) -> JSONResponse:
    with ark_db.read() as conn:
        player = _player(request, conn)
        if not player:
            return _unauthorised()
        if not sec.csrf_ok(player["csrf"], request.headers.get("x-ark-csrf", "")):
            return _wrong_csrf()
        if not arc.player_save_delete(conn, int(player["id"]), int(save_id)):
            return JSONResponse({"ok": False, "error": "That save was not there."}, status_code=404)
    return JSONResponse({"ok": True})



@router.get("/arcade/api/prefs/{game_id}")
def api_prefs_get(request: Request, game_id: int) -> JSONResponse:
    with ark_db.read() as conn:
        player = _player(request, conn)
        if not player:
            return _unauthorised()
        game = arc.game_by_id(conn, int(game_id))
        if not game or not game["published"] or game["hidden"]:
            return JSONResponse({"ok": False, "error": "That game is not on the shelf."},
                                status_code=404)
        prefs = arc.player_prefs_get(conn, int(player["id"]), int(game_id))
    return JSONResponse({"ok": True, "prefs": prefs})


@router.put("/arcade/api/prefs/{game_id}")
async def api_prefs_put(request: Request, game_id: int) -> JSONResponse:
    with ark_db.read() as conn:
        player = _player(request, conn)
        if not player:
            return _unauthorised()
        if not sec.csrf_ok(player["csrf"], request.headers.get("x-ark-csrf", "")):
            return _wrong_csrf()
        try:
            wanted = await request.json()
        except (ValueError, UnicodeDecodeError):
            return JSONResponse({"ok": False, "error": "That page sent something this box did not understand."},
                                status_code=400)
        if not isinstance(wanted, dict):
            return JSONResponse({"ok": False, "error": "That page sent something this box did not understand."},
                                status_code=400)
        choices = {}
        for key in ("autosave", "favourite"):
            if key in wanted:
                if not isinstance(wanted[key], bool):
                    return JSONResponse({"ok": False, "error": "That choice was not yes or no."},
                                        status_code=400)
                choices[key] = wanted[key]
        try:
            prefs = arc.player_prefs_set(conn, int(player["id"]), int(game_id), **choices)
        except arc.SaveRefused as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)
    return JSONResponse({"ok": True, "prefs": prefs})


@router.post("/arcade/player/import.zip")
async def player_import(request: Request, csrf: str = Form(""),
                        archive: UploadFile = File(...)) -> RedirectResponse:
    if not _player_origin(request):
        return _to_secure(request)
    with ark_db.read() as conn:
        player = _player(request, conn)
        if not player:
            return _back("/arcade/player", err="Sign in with your name first.")
        if not sec.csrf_ok(player["csrf"], csrf):
            return _back("/arcade/player", err="That request did not come from this page.")
        data = await archive.read(arc.MAX_IMPORT + 1)
        if len(data) > arc.MAX_IMPORT:
            return _back("/arcade/player", err="That file is larger than this box accepts for saves.")
        try:
            result = arc.player_import_bytes(conn, int(player["id"]), data)
        except arc.SaveRefused as exc:
            return _back("/arcade/player", err=str(exc))
    if result["errors"]:
        return _back("/arcade/player",
                     err=f'{result["imported"]} save(s) put back; {len(result["errors"])} could not be: '
                         + result["errors"][0])
    return _back("/arcade/player", ok=f'{result["imported"]} save(s) put back.')
