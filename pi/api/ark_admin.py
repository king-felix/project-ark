"""Project ARK — the central administrator login and everything behind it.

Design rules this file follows, and why:

* **Two doors, one lock.** The public portal never proxies ``/admin``. The only
  way in is the HTTPS management listener (:8443), and while the box is still
  unclaimed the *only* door is the loopback listener (:8444) that an owner
  reaches through an SSH tunnel.  A caller-supplied ``X-Forwarded-For`` never
  decides anything: the peer address must be nginx on this machine.
* **Nothing privileged without a session, a CSRF token and TLS.** Every
  state-changing route re-checks all three server-side, not just the screen.
* **A claimed box stays claimed.** The bootstrap account is created once, and
  only when there is no install marker.  A wiped database on a configured box
  is a recovery job, not an invitation to re-create the known password.
* **No JavaScript required** — the pages are plain forms and links.
"""

from __future__ import annotations

import html
import sqlite3
import time
import urllib.error
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response

import ark_db
import ark_doors
import ark_essentials
import ark_health
import ark_household
import ark_ingest
import ark_media
import ark_media_jobs
import ark_media_web
import ark_review
import ark_security as sec
import ark_space
import ark_storage
import ark_arcade
import ark_arcade_install
import ark_network
import ark_romdb
import ark_rommeta

router = APIRouter(prefix="/admin", include_in_schema=False)

SETUP_HINT = (
    "ssh -N -L 8444:127.0.0.1:8444 pi@ark.local   then open "
    "https://localhost:8444/admin in this computer's browser"
)

# Set when this Python has no working scrypt.  The public library keeps
# working; this area explains the problem instead of pretending to sign anyone in.
CRYPTO_ERROR = ""

SECURITY_HEADERS = {
    "Cache-Control": "no-store, max-age=0",
    "Pragma": "no-cache",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": ("default-src 'none'; style-src 'self'; img-src 'self'; "
                                "form-action 'self'; base-uri 'none'; frame-ancestors 'none'"),
}


# ---------------------------------------------------------------------------
#  bootstrap state
# ---------------------------------------------------------------------------
def _admin_count(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM admins").fetchone()[0])


def bootstrap_unclaimed(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT must_change, enabled FROM admins WHERE is_bootstrap = 1 LIMIT 1"
    ).fetchone()
    return bool(row and row["must_change"] and row["enabled"])


def state(conn: sqlite3.Connection) -> str:
    """fresh | setup-pending | ready | recovery-required"""
    if _admin_count(conn) == 0:
        return "recovery-required" if ark_db.marker_present() else "fresh"
    return "setup-pending" if bootstrap_unclaimed(conn) else "ready"


def ensure_initialised() -> str:
    """Create the one-time bootstrap administrator, exactly once, ever."""
    global CRYPTO_ERROR
    ark_db.migrate()
    with ark_db.tx() as conn:
        if _admin_count(conn) or ark_db.marker_present():
            state_now = state(conn)
            n_admins = _admin_count(conn)
        else:
            try:
                initial = sec.bootstrap_password()
                if not initial:
                    raise sec.CryptUnavailable(
                        "no writable place for the first-run password")
                initial = sec.hash_password(initial)
            except sec.CryptUnavailable as exc:
                # Public reading must not die because this Python cannot hash
                # passwords; the administrator area reports it instead.
                CRYPTO_ERROR = str(exc)
                return "unavailable"
            try:
                conn.execute(
                    "INSERT INTO admins (username, display_name, pass_hash, role, enabled,"
                    " must_change, is_bootstrap, created_at, created_by)"
                    " VALUES (?, ?, ?, 'admin', 1, 1, 1, ?, 'installer')",
                    (sec.BOOTSTRAP_USERNAME, "Administrator", initial, ark_db.now_iso()),
                )
                ark_db.audit(conn, action="bootstrap-account-created", actor_name="installer",
                             target=sec.BOOTSTRAP_USERNAME,
                             detail="temporary password must be changed at first sign-in")
            except sqlite3.IntegrityError:
                pass        # another process won the race; that is fine
            state_now, n_admins = "setup-pending", 1
    if n_admins and not ark_db.marker_present():
        ark_db.write_marker()
    return state_now


# ---------------------------------------------------------------------------
#  request plumbing
# ---------------------------------------------------------------------------
@dataclass
class Ctx:
    conn: sqlite3.Connection
    epoch: int
    session: sqlite3.Row | None = None
    admin: sqlite3.Row | None = None
    csrf: str = ""
    host: str = ""           # the name the reader typed, for the way back out
    here: str = ""           # the path being answered, for *you are here*


def _peer(request: Request) -> str:
    return request.client.host if request.client else ""


def over_tls(request: Request) -> bool:
    """Only the local nginx may assert the scheme, and it sets it from $scheme."""
    return (sec.peer_is_trusted(_peer(request))
            and request.headers.get("x-forwarded-proto", "").lower() == "https")


def setup_channel(request: Request) -> bool:
    """True only on the loopback listener that exists for unclaimed boxes."""
    return (sec.peer_is_trusted(_peer(request))
            and request.headers.get("x-ark-setup-channel", "").lower() == "loopback")


def client_source(request: Request) -> str:
    """The real client address, as recorded by nginx (never the raw header)."""
    peer = _peer(request)
    if sec.peer_is_trusted(peer):
        return (request.headers.get("x-real-ip") or peer).strip()[:64]
    return peer[:64]


def _resp(body: str, status: int = 200, extra: dict | None = None) -> HTMLResponse:
    headers = dict(SECURITY_HEADERS)
    if extra:
        headers.update(extra)
    return HTMLResponse(body, status_code=status, headers=headers)


def _redirect(url: str, status: int = 303) -> RedirectResponse:
    return RedirectResponse(url, status_code=status, headers=dict(SECURITY_HEADERS))


def _set_cookie(response: Response, name: str, value: str, max_age: int) -> None:
    response.set_cookie(name, value, max_age=max_age, path="/admin", httponly=True,
                        secure=True, samesite="strict")


def _clear_cookie(response: Response, name: str) -> None:
    response.delete_cookie(name, path="/admin", httponly=True, secure=True,
                           samesite="strict")


def _load_ctx(conn: sqlite3.Connection, request: Request, epoch: int,
              cookie: str = sec.COOKIE_NAME) -> Ctx:
    token = request.cookies.get(cookie, "")
    row = sec.load_session(conn, token, epoch) if token else None
    ctx = Ctx(conn=conn, epoch=epoch, session=row, csrf=row["csrf"] if row else "",
              host=request.url.hostname or "", here=request.url.path)
    if row is not None and row["admin_id"] is not None:
        ctx.admin = conn.execute("SELECT * FROM admins WHERE id = ?",
                                 (row["admin_id"],)).fetchone()
        if ctx.admin is None or not ctx.admin["enabled"]:
            sec.drop_session(conn, row["id"])       # disabled mid-session
            ctx.session = ctx.admin = None
            ctx.csrf = ""
        else:
            sec.touch_session(conn, row["id"], row["last_seen"])
    return ctx


# ---------------------------------------------------------------------------
#  page furniture
# ---------------------------------------------------------------------------
def esc(value) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def ago(iso: str) -> str:
    if not iso:
        return "never"
    try:
        then = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return esc(iso)
    secs = (datetime.now(timezone.utc) - then).total_seconds()
    if secs < 90:
        return "just now"
    for limit, div, word in ((3600, 60, "minute"), (86400, 3600, "hour"),
                             (2592000, 86400, "day"), (31536000, 2592000, "month")):
        if secs < limit:
            n = int(secs // div)
            return f"{n} {word}{'' if n == 1 else 's'} ago"
    return f"{int(secs // 31536000)} years ago"


#: The administrator's pages, in the order the navigation offers them.  The
#: navigation, the *you are here* mark and the address list are built from this
#: one tuple, so a page added here is a page reachable everywhere it should be.
ADMIN_NAV = (
    ("/admin", "Overview"),
    ("/admin/health", "What is working"),
    ("/admin/upload", "Add files"),
    ("/admin/media", "Media"),
    ("/admin/network", "Network"),
    ("/admin/household", "Household"),
    ("/admin/arcade", "Games"),
    ("/admin/community", "Community"),
    ("/admin/accounts", "Administrators"),
    ("/admin/password", "My password"),
    ("/admin/audit", "History"),
    ("/admin/doors", "Addresses"),
)


def nav_here(path: str) -> str:
    """Which navigation item the reader is standing on.

    A route deeper inside a section (``/admin/accounts/create``) belongs to that
    section's item.  Nothing is marked when nothing matches: the sign-in form is
    not one of these pages, and marking one would point a person at a page they
    cannot open yet.
    """
    path = (path or "").rstrip("/") or "/"
    for href, _label in ADMIN_NAV:
        if path == href:
            return href
    best = ""
    for href, _label in ADMIN_NAV:
        if href != "/admin" and path.startswith(href + "/") and len(href) > len(best):
            best = href
    return best


def _admin_nav(ctx: Ctx) -> str:
    """Every page on offer, with the one being read marked for a screen reader."""
    here = nav_here(ctx.here)
    links = "".join(
        f'<a href="{esc(href)}"' + (' aria-current="page"' if href == here else "") +
        f'>{esc(label)}</a>' for href, label in ADMIN_NAV)
    return f'<nav class="nav" aria-label="Administrator pages">{links}</nav>'


def _sign_out(ctx: Ctx) -> str:
    """A way out, as a form: signing out changes something, so it is a POST."""
    return (f'<form class="signout" action="/admin/logout" method="post">'
            f'{_csrf_field(ctx.csrf)}'
            '<button class="go-btn small" type="submit">Sign out</button></form>')


def shell(title: str, body: str, *, ctx: Ctx | None = None, status: int = 200,
          tagline: str = "") -> HTMLResponse:
    nav = who = ""
    if ctx is not None and ctx.admin is not None and not ctx.admin["must_change"]:
        nav = _admin_nav(ctx)
        who = (f'<p class="hint">Signed in as <b>{esc(ctx.admin["username"])}</b></p>'
               + _sign_out(ctx))
    # The way back to what a reader sees.  The owner's pages sit on one name and a
    # password; the portal sits on the same name over plain HTTP, so the link is
    # built from the name this person actually typed.
    host = (ctx.host if ctx is not None and ctx.host else "ark.lan")
    back = (f'<p><a href="http://{esc(host)}/">Open the pages a reader sees</a>'
            ' — the sign-in does not follow you there.</p>')
    sub = f'<p class="tagline">{esc(tagline)}</p>' if tagline else ""
    doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark">
<meta name="theme-color" content="#000000">
<meta name="robots" content="noindex,nofollow">
<title>{esc(title)} — Ark administrator</title>
<link rel="stylesheet" href="/assets/ark.css">
<link rel="stylesheet" href="/assets/ark-admin.css">
</head>
<body class="page-admin">
<a class="skip" href="#main">Skip to the main content</a>
<div class="wrap">
  <header>
    <span class="logo">Ark administrator</span>
    <h1>{esc(title)}</h1>
    {sub}
  </header>
  {nav}
  {who}
<main id="main">
{body}
</main>
  <footer><p><b>Private area.</b> Public readers never see this page or these settings.</p>
  {back}</footer>
</div>
</body>
</html>"""
    return _resp(doc, status)


# ---------------------------------------------------------------------------
#  gates — every door checks these server-side
# ---------------------------------------------------------------------------
def _gate_tls(request: Request) -> Response | None:
    if over_tls(request):
        return None
    return shell("Secure address required", """
  <p class="status"><span class="dot" aria-hidden="true"></span><span><b>Use the secure address.</b>
     An administrator password is never sent over plain HTTP, so this address stays closed.</span></p>
  <p class="note">Open <b>https://ark.lan:8443/admin</b> instead. Your browser will ask once to
     trust the box's own certificate — see “Trusting the certificate” in the printed guide.</p>""",
                 status=403)


def _gate_setup(request: Request, conn: sqlite3.Connection) -> Response | None:
    """The box's first claim is only possible through the loopback listener."""
    if not bootstrap_unclaimed(conn) or setup_channel(request):
        return None
    temporary = sec.bootstrap_password(create=False)
    if temporary:
        where = (f"<b>{esc(temporary)}</b>")
    else:
        where = (f"the one written in <code>{esc(str(sec.bootstrap_password_path()))}</code>")
    return shell("Finish setup over a direct connection", f"""
  <p class="status"><span class="dot" aria-hidden="true"></span><span><b>This box has not been
     claimed yet.</b> To stop the first stranger on the Wi-Fi becoming its administrator, the
     first sign-in only works over a private connection to the box itself.</span></p>
  <h2>On your own computer</h2>
  <p class="hint">Run this, then open the address below in the same computer's browser:</p>
  <pre class="code">{esc(SETUP_HINT)}</pre>
  <p class="note">Sign in as <b>{esc(sec.BOOTSTRAP_USERNAME)}</b> with the temporary password
     {where}. You will be asked to choose your own password
     immediately — that temporary one stops working straight away.</p>""", status=403)


def _gate_recovery(conn: sqlite3.Connection) -> Response | None:
    if CRYPTO_ERROR:
        return shell("Sign-in unavailable on this box", f"""
  <p class="status"><span class="dot" aria-hidden="true"></span><span><b>This box cannot check
     passwords.</b> Its Python build has no <code>scrypt</code>, and ARK will not fall back to a
     weaker scheme.</span></p>
  <p class="note">Public reading is unaffected. To fix sign-in, install a Python built against
     OpenSSL 1.1 or newer, then restart the portal. Details: {esc(CRYPTO_ERROR)}</p>""",
                     status=503)
    if _admin_count(conn) > 0:
        return None
    return shell("Recovery needed", """
  <p class="status"><span class="dot" aria-hidden="true"></span><span><b>No administrator account
     exists.</b> This box was set up before, so ARK will not quietly re-create the well-known
     temporary login.</span></p>
  <h2>Restore access from the box itself</h2>
  <p class="hint">Over SSH (or the local console) run:</p>
  <pre class="code">sudo ark-admin-recover --username admin</pre>
  <p class="note">That prints a one-time password for the account you name and requires it to be
     changed at the next sign-in. It is deliberately not available from the web interface.</p>""",
                 status=503)


def _notice(kind: str, text: str) -> str:
    return (f'<p class="status {esc(kind)}"><span class="dot" aria-hidden="true"></span>'
            f'<span>{text}</span></p>')


def _login_form(csrf: str, *, username: str = "", error: str = "") -> str:
    banner = _notice("bad", f"<b>Not signed in.</b> {esc(error)}") if error else ""
    return f"""{banner}
  <form action="/admin/login" method="post" autocomplete="off">
    <input type="hidden" name="csrf" value="{esc(csrf)}">
    <label for="username">Administrator name</label>
    <input id="username" name="username" type="text" value="{esc(username)}"
           maxlength="32" autocapitalize="none" autocorrect="off" required>
    <label for="password">Password</label>
    <input id="password" name="password" type="password" maxlength="{sec.PASSWORD_MAX}"
           autocomplete="current-password" required>
    <button class="go-btn" type="submit">Sign in</button>
  </form>
  <p class="note">Thirty minutes without activity signs you out; so does eight hours, or a restart
     of the box.</p>"""


# ---------------------------------------------------------------------------
#  sign in
# ---------------------------------------------------------------------------
@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    if (blocked := _gate_tls(request)) is not None:
        return blocked
    with ark_db.tx() as conn:
        if (blocked := _gate_recovery(conn)) is not None:
            return blocked
        if (blocked := _gate_setup(request, conn)) is not None:
            return blocked
        epoch = ark_db.session_epoch(conn)
        ctx = _load_ctx(conn, request, epoch, cookie=sec.PREAUTH_COOKIE)
        if ctx.session is not None:
            return shell("Sign in", _login_form(ctx.csrf),
                         tagline="Only the owner of this box needs this page.")
        issued = sec.create_session(conn, admin_id=None, kind="preauth",
                                    epoch=epoch, ip=client_source(request))
        response = shell("Sign in", _login_form(issued.csrf),
                         tagline="Only the owner of this box needs this page.")
        _set_cookie(response, sec.PREAUTH_COOKIE, issued.token, sec.PREAUTH_LIFETIME)
        return response


@router.post("/login", response_class=HTMLResponse)
def login_submit(request: Request, username: str = Form(""), password: str = Form(""),
                 csrf: str = Form("")):
    if (blocked := _gate_tls(request)) is not None:
        return blocked
    source = client_source(request)
    generic = "That name and password do not match an administrator account."
    with ark_db.tx() as conn:
        if (blocked := _gate_recovery(conn)) is not None:
            return blocked
        if (blocked := _gate_setup(request, conn)) is not None:
            return blocked
        epoch = ark_db.session_epoch(conn)
        ctx = _load_ctx(conn, request, epoch, cookie=sec.PREAUTH_COOKIE)

        if ctx.session is not None and not sec.csrf_ok(ctx.csrf, csrf):
            return shell("Sign in", _login_form(ctx.csrf, username=username,
                         error="That form expired. Please try again."), status=400)
        if ctx.session is None:
            # No form session (expired, or a stale page): start a fresh one.
            issued = sec.create_session(conn, admin_id=None, kind="preauth",
                                        epoch=epoch, ip=source)
            response = shell("Sign in", _login_form(issued.csrf, username=username,
                             error="That form expired. Please try again."), status=400)
            _set_cookie(response, sec.PREAUTH_COOKIE, issued.token, sec.PREAUTH_LIFETIME)
            return response

        wait = sec.login_backoff(conn, username, source)
        if wait > 0:
            ark_db.audit(conn, action="sign-in", actor_name=username, result="throttled",
                         target=source, detail=f"waiting {wait}s")
            response = shell("Too many attempts", _notice(
                "bad", f"<b>Please wait about {max(1, wait // 60)} minute(s).</b> "
                       "Too many wrong passwords were tried for this name."), status=429)
            response.headers["Retry-After"] = str(wait)
            return response

        name = sec.normalise_username(username)
        row = conn.execute("SELECT * FROM admins WHERE username = ?", (name,)).fetchone()
        # Always do the expensive check, even for a name that does not exist and
        # for a disabled account.  Short-circuiting here would let an attacker
        # discover administrator names by timing the answer.
        candidate = row["pass_hash"] if row is not None else sec.DUMMY_HASH
        valid = sec.verify_password(password, candidate or sec.DUMMY_HASH)
        good = bool(row) and bool(row["enabled"]) and valid
        if not good:
            sec.record_login(conn, name, source, ok=False)
            ark_db.audit(conn, action="sign-in", actor_name=name, result="denied",
                         target=source, detail=generic)
            return shell("Sign in", _login_form(ctx.csrf, username=username, error=generic),
                         status=401)

        # Success. Hashes move forward here if the cost parameters were raised.
        if sec.needs_rehash(row["pass_hash"]):
            conn.execute("UPDATE admins SET pass_hash = ? WHERE id = ?",
                         (sec.hash_password(password), row["id"]))
        conn.execute("UPDATE admins SET last_login_at = ? WHERE id = ?",
                     (ark_db.now_iso(), row["id"]))
        sec.record_login(conn, name, source, ok=True)
        sec.drop_session(conn, ctx.session["id"])
        issued = sec.create_session(conn, admin_id=row["id"], kind="admin",
                                    epoch=epoch, ip=source)
        ark_db.audit(conn, action="sign-in", actor_id=row["id"], actor_name=row["username"],
                     target=source, result="ok")
        response = _redirect("/admin/setup" if row["must_change"] else "/admin")
        _set_cookie(response, sec.COOKIE_NAME, issued.token, sec.ABSOLUTE_TIMEOUT)
        _clear_cookie(response, sec.PREAUTH_COOKIE)
        return response


# ---------------------------------------------------------------------------
#  first sign-in: choose a real password
# ---------------------------------------------------------------------------
def _setup_form(csrf: str, admin: sqlite3.Row, *, error: str = "") -> str:
    banner = _notice("bad", f"<b>Not saved.</b> {esc(error)}") if error else ""
    return f"""{banner}
  <p class="hint">Signed in as <b>{esc(admin["username"])}</b> with a temporary password.
     Choose your own now — the temporary one stops working immediately.</p>
  <form action="/admin/setup" method="post" autocomplete="off">
    <input type="hidden" name="csrf" value="{esc(csrf)}">
    <label for="new_password">Your new password</label>
    <input id="new_password" name="new_password" type="password" required
           minlength="{sec.PASSWORD_MIN}" maxlength="{sec.PASSWORD_MAX}"
           autocomplete="new-password" aria-describedby="pw-help">
    <p class="note" id="pw-help">At least {sec.PASSWORD_MIN} characters. A short sentence is
       perfect. Up to {sec.PASSWORD_MAX} characters are stored in full — nothing is cut short.</p>
    <label for="confirm">Type it again</label>
    <input id="confirm" name="confirm" type="password" required
           maxlength="{sec.PASSWORD_MAX}" autocomplete="new-password">
    <button class="go-btn" type="submit">Save my password</button>
  </form>"""


def _setup_allowed(request: Request, ctx: Ctx) -> bool:
    """A later administrator may finish setup over the LAN; the box's first
    claim may not — that one needs the private loopback channel."""
    return setup_channel(request) or not ctx.admin["is_bootstrap"]


@router.get("/setup", response_class=HTMLResponse)
def setup_form(request: Request):
    if (blocked := _gate_tls(request)) is not None:
        return blocked
    with ark_db.tx() as conn:
        ctx = _load_ctx(conn, request, ark_db.session_epoch(conn))
        if ctx.admin is None:
            return _redirect("/admin/login")
        if not ctx.admin["must_change"]:
            return _redirect("/admin")
        if not _setup_allowed(request, ctx):
            return shell("Private connection needed", f"""
  <p class="status"><span class="dot" aria-hidden="true"></span><span><b>The box's first
     administrator can only be set up over a private connection.</b></span></p>
  <pre class="code">{esc(SETUP_HINT)}</pre>""", status=403)
        return shell("Choose your own password", _setup_form(ctx.csrf, ctx.admin),
                     ctx=ctx, tagline="This is the last step before the box is yours alone.")


@router.post("/setup", response_class=HTMLResponse)
def setup_submit(request: Request, new_password: str = Form(""), confirm: str = Form(""),
                 csrf: str = Form("")):
    if (blocked := _gate_tls(request)) is not None:
        return blocked
    with ark_db.tx() as conn:
        epoch = ark_db.session_epoch(conn)
        ctx = _load_ctx(conn, request, epoch)
        if ctx.admin is None:
            return _redirect("/admin/login")
        admin = ctx.admin
        if not admin["must_change"]:
            return _redirect("/admin")
        if not _setup_allowed(request, ctx):
            return shell("Private connection needed", f"""
  <p class="status"><span class="dot" aria-hidden="true"></span><span><b>Use the private
     connection.</b></span></p><pre class="code">{esc(SETUP_HINT)}</pre>""", status=403)
        if not sec.csrf_ok(ctx.csrf, csrf):
            return shell("Choose your own password", _setup_form(ctx.csrf, admin,
                         error="That form expired. Please reload the page."), status=400)
        if new_password != confirm:
            return shell("Choose your own password", _setup_form(ctx.csrf, admin,
                         error="The two passwords do not match."), status=400)
        try:
            sec.check_new_password(new_password, username=admin["username"],
                                   current_hash=admin["pass_hash"])
        except sec.PolicyError as exc:
            return shell("Choose your own password", _setup_form(ctx.csrf, admin,
                         error=str(exc)), status=400)
        # Atomic: only the first completed attempt may change the account.
        cur = conn.execute(
            "UPDATE admins SET pass_hash = ?, must_change = 0, password_changed_at = ?"
            " WHERE id = ? AND must_change = 1",
            (sec.hash_password(new_password), ark_db.now_iso(), admin["id"]),
        )
        if cur.rowcount != 1:
            sec.drop_admin_sessions(conn, admin["id"])
            ark_db.audit(conn, action="password-setup", actor_id=admin["id"],
                         actor_name=admin["username"], result="conflict",
                         detail="another attempt completed first")
            return shell("Already set up", _notice(
                "bad", "<b>Someone completed this already.</b> Please sign in with the new "
                       "password.") + '<p class="nav"><a href="/admin/login">Go to sign-in</a></p>',
                status=409)
        sec.drop_admin_sessions(conn, admin["id"])
        issued = sec.create_session(conn, admin_id=admin["id"], kind="admin",
                                    epoch=epoch, ip=client_source(request))
        ark_db.audit(conn, action="password-setup", actor_id=admin["id"],
                     actor_name=admin["username"], result="ok",
                     detail="temporary password replaced")
        response = _redirect("/admin")
        _set_cookie(response, sec.COOKIE_NAME, issued.token, sec.ABSOLUTE_TIMEOUT)
        return response


# ---------------------------------------------------------------------------
#  shared checks for the signed-in area
# ---------------------------------------------------------------------------
def _need_session(ctx: Ctx, request: Request) -> Response | None:
    """Authenticated, fully set up, or refused — POSTs get an explicit 403."""
    if ctx.admin is None:
        if request.method in ("GET", "HEAD"):
            return _redirect("/admin/login")
        return shell("Not signed in", _notice(
            "bad", "<b>Nothing was changed.</b> Please sign in first."), status=403)
    if ctx.admin["must_change"]:
        if request.method in ("GET", "HEAD"):
            return _redirect("/admin/setup")
        return shell("Finish setting up first", _notice(
            "bad", "<b>Nothing was changed.</b> This account still has a temporary password. "
                   "Choose your own password first."), status=403)
    return None


def _need_csrf(ctx: Ctx, supplied: str) -> Response | None:
    if sec.csrf_ok(ctx.csrf, supplied):
        return None
    return shell("That form expired", _notice(
        "bad", "<b>Nothing was changed.</b> The page was open too long or came from "
               "somewhere else. Please go back, reload, and try again."), status=403)


def _reauth(admin: sqlite3.Row, password: str) -> bool:
    """Current password, checked on the server, for high-impact actions."""
    return bool(password) and sec.verify_password(password, admin["pass_hash"])


def _csrf_field(csrf: str) -> str:
    return f'<input type="hidden" name="csrf" value="{esc(csrf)}">'


@router.post("/logout", response_class=HTMLResponse)
def logout(request: Request, csrf: str = Form("")):
    """The way out, as a POST with the form token.

    It changes something — the session is ended on the server, not merely
    forgotten by the browser — so it is a POST and it is token-checked.  A GET
    that ends a session can be fired by an image tag on somebody else's page; a
    POST with a token cannot.  A forged request therefore gains nothing: the
    session is left exactly as it was and the person stays signed in.
    """
    if (blocked := _gate_tls(request)) is not None:
        return blocked
    with ark_db.tx() as conn:
        ctx = _load_ctx(conn, request, ark_db.session_epoch(conn))
        if ctx.session is None:
            # Already signed out (a second press, or a page left open): the honest
            # answer is the sign-in form, not "that form expired".
            response = _redirect("/admin/login")
            _clear_cookie(response, sec.COOKIE_NAME)
            _clear_cookie(response, sec.PREAUTH_COOKIE)
            return response
        if (blocked := _need_csrf(ctx, csrf)) is not None:
            return blocked
        sec.drop_session(conn, ctx.session["id"])
        if ctx.admin is not None:
            ark_db.audit(conn, action="sign-out", actor_id=ctx.admin["id"],
                         actor_name=ctx.admin["username"], result="ok")
    response = _redirect("/admin/login")
    _clear_cookie(response, sec.COOKIE_NAME)
    _clear_cookie(response, sec.PREAUTH_COOKIE)
    return response


# ---------------------------------------------------------------------------
#  overview
# ---------------------------------------------------------------------------
def _stat(value: str, label: str, *, note: str = "", link: str = "",
          link_label: str = "") -> str:
    """One figure on the dashboard: the number, what it counts, and the way in.

    A figure the box could not measure arrives here as *not measured yet* and is
    printed as words — never as a zero, which would be a claim (the `None` rule).
    """
    footnote = f'<span class="sub">{esc(note)}</span>' if note else ""
    way_in = (f'<a class="more" href="{esc(link)}">{esc(link_label)}</a>'
              if link and link_label else "")
    return (f'<li><b>{esc(value)}</b><span class="sub">{esc(label)}</span>'
            f'{footnote}{way_in}</li>')


def _dashboard(health: dict, messages: int) -> str:
    """The four figures the owner opens this page for.

    Each one is measured here and now: how long the box has been awake, how much
    room is left on the card, what a reader can open in the File Box, and what is
    written on the board.  Everything else on this page points at the page that
    explains it.
    """
    room = ark_health.card_room()
    free, used, total = room["free_bytes"], room["used_bytes"], room["total_bytes"]
    if free is None or total is None:
        room_stat = _stat(
            "not measured yet", "Room left on the card",
            note="The card did not answer.  Nothing already on it has been touched.")
    else:
        # Rounded down, never up: 99.6% free must not be printed as "100% is
        # free" on a card that has something on it.
        share = int(100.0 * free / total) if total else 0
        room_stat = _stat(
            f"{ark_health.human_size(free)} free",
            "Room left on the card — the library, the books, the games and the File Box "
            "all share it",
            note=f"{ark_health.human_size(used)} of {ark_health.human_size(total)} is in "
                 f"use, so {share}% is free.",
            link="/admin/health", link_label="Where the room went")

    awake = ark_health.uptime_seconds()
    if awake is None:
        uptime_stat = _stat(
            "not measured yet", "How long the box has been on",
            note="This page could not ask the machine how long it has been awake.")
    else:
        uptime_stat = _stat(
            ark_health.uptime_words(awake), "How long the box has been on",
            note="It counts from the last time the box was switched on or restarted — a "
                 "power cut starts it again.")

    facts = next((s["facts"] for s in health["sections"] if s["key"] == "files"), {})
    openable = facts.get("files")
    if openable is None:
        files_stat = _stat(
            "not measured yet", "Files anyone can open in the File Box",
            note="The shared folder could not be read, so there is no number to give.")
    else:
        gone = facts.get("files_missing")
        if gone is None:
            note = ("The folder could not be listed, so this counts the records, not the "
                    "files behind them.")
        elif gone:
            note = (f"{gone} record{'s' if gone != 1 else ''} in the list "
                    f"{'have' if gone != 1 else 'has'} no file behind "
                    f"{'them' if gone != 1 else 'it'} any more.")
        else:
            note = ""
        files_stat = _stat(
            f"{openable} file{'' if openable == 1 else 's'}",
            "Anyone can open these in the File Box", note=note,
            link="/admin/community", link_label="Look after shared files")

    notes_stat = _stat(
        f"{messages} message{'' if messages == 1 else 's'}", "On the Message Board",
        note="Newest last, and every note can be hidden or deleted here.",
        link="/admin/community", link_label="Community settings")
    return uptime_stat + room_stat + files_stat + notes_stat


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def overview(request: Request):
    if (blocked := _gate_tls(request)) is not None:
        return blocked
    with ark_db.tx() as conn:
        if (blocked := _gate_recovery(conn)) is not None:
            return blocked
        ctx = _load_ctx(conn, request, ark_db.session_epoch(conn))
        if (blocked := _need_session(ctx, request)) is not None:
            return blocked
        admins = conn.execute(
            "SELECT username, display_name, enabled, must_change, last_login_at"
            " FROM admins ORDER BY id"
        ).fetchall()
        notes = int(conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0])
        events = conn.execute("SELECT ts, actor_name, action, result FROM audit_log"
                              " ORDER BY id DESC LIMIT 5").fetchall()
        active = sum(1 for a in admins if a["enabled"] and not a["must_change"])
    # The overview shows the verdict; /admin/health shows every part and why.
    health = ark_health.report()
    if health["ok"]:
        health_block = f"""
  <h2>Is it working?</h2>
  {_notice("good", "<b>Everything a visitor needs is working.</b> "
                   f"{health['ready']} of {health['total']} parts answered just now.")}
  <p class="note"><a href="/admin/health">See what was checked</a></p>"""
    else:
        names = ", ".join(health["blocking"]) or "some parts"
        count = len(health["blocking"]) or (health["total"] - health["ready"])
        health_block = f"""
  <h2>Is it working?</h2>
  {_notice("bad", f"<b>{count} part{'s' if count != 1 else ''} need attention:</b> "
                  f"{esc(names)}.")}
  <p class="note"><a href="/admin/health">See what is wrong and what to do</a></p>"""

    rows = "".join(
        f"<tr><td>{esc(a['username'])}</td><td>{esc(a['display_name'] or '—')}</td>"
        f"<td>{'yes' if a['enabled'] else 'no'}</td>"
        f"<td>{'must change' if a['must_change'] else 'set'}</td>"
        f"<td>{esc(ago(a['last_login_at']))}</td></tr>"
        for a in admins
    )
    recent = "".join(
        f"<li>{esc(e['actor_name'] or 'system')} — {esc(e['action'])} "
        f"<span class=\"sub\">{esc(ago(e['ts']))} · {esc(e['result'])}</span></li>"
        for e in events
    ) or '<li>No recorded activity yet.</li>'
    stats = _dashboard(health, notes)
    body = f"""
  {_notice("good", f"<b>You are the administrator.</b> {active} enabled "
                   f"account{'s' if active != 1 else ''} on this box.")}
  <h2>This box right now</h2>
  <p class="note">Measured when you opened this page — not remembered from yesterday.</p>
  <ul class="stats">{stats}</ul>
  <h2>Put something on the box</h2>
  <div class="grid two">
    <div class="card"><b>Books and games</b>
      <span class="sub">Choose several files at once and press one button.</span>
      <a class="go-btn small" href="/admin/upload">Add books and games</a></div>
    <div class="card"><b>One game of your own</b>
      <span class="sub">The box reads the file and shows you its words first.</span>
      <a class="go-btn small" href="/admin/arcade">Add one game</a></div>
  </div>
  <h2>Administrators</h2>
  <table class="plain"><thead><tr><th>Name</th><th>Shown as</th><th>Enabled</th>
    <th>Password</th><th>Last sign-in</th></tr></thead><tbody>{rows}</tbody></table>
  <p class="nav"><a href="/admin/accounts">Manage administrators</a></p>
  <h2>Recent activity</h2>
  <ul class="clean">{recent}</ul>
  {health_block}"""
    return shell("Overview", body, ctx=ctx,
                 tagline="The owner's side of the box.")


# ---------------------------------------------------------------------------
#  what is working
# ---------------------------------------------------------------------------
def _checked_at(iso: str) -> str:
    try:
        then = datetime.fromisoformat(iso)
        return then.astimezone().strftime("%H:%M")
    except Exception:
        return "just now"


def _storage_breakdown(section: dict) -> str:
    """Where the room went, measured folder by folder.

    Only the storage row gets this: everywhere else a size list would be noise.
    An empty card renders nothing at all rather than five zeroes — a figure the
    box did not measure must never appear as a claim.
    """
    parts = section["facts"].get("breakdown") or []
    if not parts:
        return ""
    rows = "".join(
        f'<li>{esc(p["label"])}<span>{esc(ark_health.human_size(p["bytes"]))}</span></li>'
        for p in parts)
    total = section["facts"].get("breakdown_bytes") or 0
    card_used = section["facts"].get("card_used_bytes")
    card = (f"  The card holds {esc(ark_health.human_size(card_used))} in all, the "
            "operating system included.") if card_used else ""
    return (f'<ul class="breakdown">{rows}</ul>'
            f'<span class="sub">Measured: {esc(ark_health.human_size(total))} in these '
            f'folders.{card}</span>')


def _health_row(section: dict) -> str:
    """One part of the box: its state, what that means, and what to do."""
    state = ("<span class=\"state\">Ready</span>" if section["ready"]
             else "<span class=\"state no\">Not ready</span>")
    fix = (f'<span class="fix">What to do: {esc(section["action"])}</span>'
           if section["action"] else "")
    extra = _storage_breakdown(section) if section["key"] == "storage" else ""
    return (f'<li><b>{esc(section["label"])}</b> {state}'
            f'<span class="sub">{esc(section["reason"])}</span>{fix}{extra}</li>')


@router.get("/health", response_class=HTMLResponse)
def health_page(request: Request):
    """Every part of the box, the reason it is or is not usable, and what to do.

    Nothing here changes anything: it looks, and it tells the truth about what it
    saw.  The probes are asked fresh (no cached answer) because the owner opened
    this page to find out what is wrong *now*.
    """
    if (blocked := _gate_tls(request)) is not None:
        return blocked
    with ark_db.tx() as conn:
        if (blocked := _gate_recovery(conn)) is not None:
            return blocked
        ctx = _load_ctx(conn, request, ark_db.session_epoch(conn))
        if (blocked := _need_session(ctx, request)) is not None:
            return blocked

    ark_health.forget_probes()
    report = ark_health.report()
    rows = "".join(_health_row(s) for s in report["sections"])

    if report["ok"]:
        headline = _notice(
            "good", "<b>Everything a visitor needs is working.</b> "
                    f"{report['ready']} of {report['total']} parts answered.")
    else:
        count = len(report["blocking"])
        names = esc(", ".join(report["blocking"])) or "some parts"
        headline = _notice(
            "bad", f"<b>{count} part{'s' if count != 1 else ''} need"
                   f"{'s' if count == 1 else ''} attention:</b> {names}.")
    waiting = [s for s in report["sections"] if not s["ready"]]
    waiting_line = (""
                    if not waiting else
                    '<p class="note">The parts marked <b>Not ready</b> are listed above with '
                    'a plain reason. Nothing on this page has been changed.</p>')
    body = f"""
  {headline}
  <h2>Checked at {esc(_checked_at(report['checked']))}</h2>
  <ul class="clean health">{rows}</ul>
  {waiting_line}
  <p class="note">Reload this page to check again. It only looks — it never starts,
     stops or deletes anything. Downloading is ordinary work in progress, so a
     waiting download does not count as something being wrong.</p>"""
    return shell("What is working", body, ctx=ctx,
                 tagline="Every part of the box, and the reason behind each answer.")


# ---------------------------------------------------------------------------
#  change my own password
# ---------------------------------------------------------------------------
@router.get("/password", response_class=HTMLResponse)
def password_form(request: Request):
    if (blocked := _gate_tls(request)) is not None:
        return blocked
    with ark_db.tx() as conn:
        ctx = _load_ctx(conn, request, ark_db.session_epoch(conn))
        if (blocked := _need_session(ctx, request)) is not None:
            return blocked
        return shell("My password", _password_form(ctx.csrf),
                     ctx=ctx, tagline="Changing it signs out every other browser.")


def _password_form(csrf: str, *, error: str = "") -> str:
    banner = _notice("bad", f"<b>Not changed.</b> {esc(error)}") if error else ""
    return f"""{banner}
  <form action="/admin/password" method="post" autocomplete="off">
    {_csrf_field(csrf)}
    <label for="current_password">Your current password</label>
    <input id="current_password" name="current_password" type="password" required
           maxlength="{sec.PASSWORD_MAX}" autocomplete="current-password">
    <label for="new_password">New password</label>
    <input id="new_password" name="new_password" type="password" required
           minlength="{sec.PASSWORD_MIN}" maxlength="{sec.PASSWORD_MAX}"
           autocomplete="new-password" aria-describedby="pw-help">
    <p class="note" id="pw-help">At least {sec.PASSWORD_MIN} characters; up to
       {sec.PASSWORD_MAX} are kept in full.</p>
    <label for="confirm">New password again</label>
    <input id="confirm" name="confirm" type="password" required
           maxlength="{sec.PASSWORD_MAX}" autocomplete="new-password">
    <button class="go-btn" type="submit">Change my password</button>
  </form>"""


@router.post("/password", response_class=HTMLResponse)
def password_submit(request: Request, current_password: str = Form(""),
                    new_password: str = Form(""), confirm: str = Form(""),
                    csrf: str = Form("")):
    if (blocked := _gate_tls(request)) is not None:
        return blocked
    with ark_db.tx() as conn:
        epoch = ark_db.session_epoch(conn)
        ctx = _load_ctx(conn, request, epoch)
        if (blocked := _need_session(ctx, request)) is not None:
            return blocked
        if (blocked := _need_csrf(ctx, csrf)) is not None:
            return blocked
        admin = ctx.admin
        if not _reauth(admin, current_password):
            ark_db.audit(conn, action="password-change", actor_id=admin["id"],
                         actor_name=admin["username"], result="denied",
                         detail="wrong current password")
            return shell("My password", _password_form(
                ctx.csrf, error="That is not your current password."), ctx=ctx, status=403)
        if new_password != confirm:
            return shell("My password", _password_form(
                ctx.csrf, error="The two new passwords do not match."), ctx=ctx, status=400)
        try:
            sec.check_new_password(new_password, username=admin["username"],
                                   current_hash=admin["pass_hash"])
        except sec.PolicyError as exc:
            return shell("My password", _password_form(ctx.csrf, error=str(exc)),
                         ctx=ctx, status=400)
        conn.execute("UPDATE admins SET pass_hash = ?, must_change = 0,"
                     " password_changed_at = ? WHERE id = ?",
                     (sec.hash_password(new_password), ark_db.now_iso(), admin["id"]))
        sec.drop_admin_sessions(conn, admin["id"])
        issued = sec.create_session(conn, admin_id=admin["id"], kind="admin",
                                    epoch=epoch, ip=client_source(request))
        ark_db.audit(conn, action="password-change", actor_id=admin["id"],
                     actor_name=admin["username"], result="ok",
                     detail="all other sessions revoked")
        response = _redirect("/admin")
        _set_cookie(response, sec.COOKIE_NAME, issued.token, sec.ABSOLUTE_TIMEOUT)
        return response


# ---------------------------------------------------------------------------
#  administrators
# ---------------------------------------------------------------------------
def _temp_password_page(title: str, username: str, temp: str, *, ctx: Ctx,
                        done: str) -> HTMLResponse:
    """Shown exactly once: the temporary password exists only in this response."""
    body = f"""
  {_notice("good", f"<b>{esc(done)}</b>")}
  <h2>Temporary password for {esc(username)}</h2>
  <pre class="code temp">{esc(temp)}</pre>
  <p class="note"><b>Write it down now.</b> It is not stored in readable form and cannot be shown
     again. Give it to that person directly, not by email or chat. They must choose their own
     password the first time they sign in.</p>
  <p class="nav"><a href="/admin/accounts">Back to administrators</a></p>"""
    return shell(title, body, ctx=ctx)


def _target(conn: sqlite3.Connection, admin_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM admins WHERE id = ?", (admin_id,)).fetchone()


def _account_row(a: sqlite3.Row, csrf: str) -> str:
    toggle_to = "disable" if a["enabled"] else "enable"
    toggle_label = "Switch this account off" if a["enabled"] else "Switch this account back on"
    return f"""<tr>
  <td><b>{esc(a["username"])}</b><span class="sub">{esc(a["display_name"] or "—")}</span></td>
  <td>{"yes" if a["enabled"] else "<b>no</b>"}</td>
  <td>{"must change" if a["must_change"] else "set"}<span class="sub">{esc(ago(a["last_login_at"]))}</span></td>
  <td><details><summary>Manage</summary>
    <form action="/admin/accounts/{a["id"]}/reset" method="post" autocomplete="off">
      {_csrf_field(csrf)}
      <label for="pw-{a["id"]}">Your password</label>
      <input id="pw-{a["id"]}" name="current_password" type="password" required
             maxlength="{sec.PASSWORD_MAX}" autocomplete="current-password">
      <button class="go-btn small" type="submit">Give a new temporary password</button>
    </form>
    <form action="/admin/accounts/{a["id"]}/{toggle_to}" method="post" autocomplete="off">
      {_csrf_field(csrf)}
      <label for="pw2-{a["id"]}">Your password</label>
      <input id="pw2-{a["id"]}" name="current_password" type="password" required
             maxlength="{sec.PASSWORD_MAX}" autocomplete="current-password">
      <button class="go-btn small" type="submit">{esc(toggle_label)}</button>
    </form>
  </details></td>
</tr>"""


@router.get("/accounts", response_class=HTMLResponse)
def accounts(request: Request):
    if (blocked := _gate_tls(request)) is not None:
        return blocked
    with ark_db.tx() as conn:
        ctx = _load_ctx(conn, request, ark_db.session_epoch(conn))
        if (blocked := _need_session(ctx, request)) is not None:
            return blocked
        rows = conn.execute("SELECT * FROM admins ORDER BY id").fetchall()
        table = "".join(_account_row(a, ctx.csrf) for a in rows)
        body = f"""
  <h2>Administrators ({len(rows)})</h2>
  <table class="plain"><thead><tr><th>Name</th><th>Enabled</th><th>Password</th>
    <th>Actions</th></tr></thead><tbody>{table}</tbody></table>
  <h2>Add an administrator</h2>
  <form action="/admin/accounts/create" method="post" autocomplete="off">
    {_csrf_field(ctx.csrf)}
    <label for="username">Name to sign in with</label>
    <input id="username" name="username" type="text" required maxlength="32"
           autocapitalize="none" autocorrect="off" placeholder="e.g. sam">
    <label for="display_name">Shown on this page as (optional)</label>
    <input id="display_name" name="display_name" type="text" maxlength="60" placeholder="e.g. Sam">
    <label for="current_password">Your password, to confirm it is you</label>
    <input id="current_password" name="current_password" type="password" required
           maxlength="{sec.PASSWORD_MAX}" autocomplete="current-password">
    <button class="go-btn" type="submit">Create the account</button>
  </form>
  <p class="note">The new person gets a one-time password shown once. They choose their own the
     first time they sign in. Every account can do everything — there is one role, on purpose.</p>"""
        return shell("Administrators", body, ctx=ctx,
                     tagline="Everyone here can change everything on this box.")



@router.post("/accounts/create", response_class=HTMLResponse)
def account_create(request: Request, username: str = Form(""), display_name: str = Form(""),
                   current_password: str = Form(""), csrf: str = Form("")):
    if (blocked := _gate_tls(request)) is not None:
        return blocked
    with ark_db.tx() as conn:
        ctx = _load_ctx(conn, request, ark_db.session_epoch(conn))
        if (blocked := _need_session(ctx, request)) is not None:
            return blocked
        if (blocked := _need_csrf(ctx, csrf)) is not None:
            return blocked
        me = ctx.admin
        if not _reauth(me, current_password):
            ark_db.audit(conn, action="account-create", actor_id=me["id"],
                         actor_name=me["username"], result="denied",
                         detail="wrong current password")
            return shell("Administrators", _notice(
                "bad", "<b>Nobody was added.</b> Your own password did not match."),
                ctx=ctx, status=403)
        problem = sec.username_problem(username)
        if problem:
            return shell("Administrators", _notice(
                "bad", f"<b>Nobody was added.</b> {esc(problem)}"), ctx=ctx, status=400)
        name = sec.normalise_username(username)
        if conn.execute("SELECT 1 FROM admins WHERE username = ?", (name,)).fetchone():
            return shell("Administrators", _notice(
                "bad", "<b>Nobody was added.</b> That name is already taken — even a switched-off "
                       "account keeps its name, so history stays readable."), ctx=ctx, status=409)
        temp = sec.generate_temp_password()
        conn.execute(
            "INSERT INTO admins (username, display_name, pass_hash, role, enabled, must_change,"
            " is_bootstrap, created_at, created_by) VALUES (?, ?, ?, 'admin', 1, 1, 0, ?, ?)",
            (name, (display_name or "").strip()[:60], sec.hash_password(temp),
             ark_db.now_iso(), me["username"]),
        )
        ark_db.audit(conn, action="account-create", actor_id=me["id"], actor_name=me["username"],
                     target=name, result="ok", detail="temporary password issued")
        return _temp_password_page("Account created", name, temp, ctx=ctx,
                                   done=f"{name} can now sign in.")



@router.post("/accounts/{admin_id}/reset", response_class=HTMLResponse)
def account_reset(request: Request, admin_id: int, current_password: str = Form(""),
                  csrf: str = Form("")):
    if (blocked := _gate_tls(request)) is not None:
        return blocked
    with ark_db.tx() as conn:
        ctx = _load_ctx(conn, request, ark_db.session_epoch(conn))
        if (blocked := _need_session(ctx, request)) is not None:
            return blocked
        if (blocked := _need_csrf(ctx, csrf)) is not None:
            return blocked
        me = ctx.admin
        if not _reauth(me, current_password):
            ark_db.audit(conn, action="account-reset", actor_id=me["id"],
                         actor_name=me["username"], result="denied",
                         detail="wrong current password")
            return shell("Administrators", _notice(
                "bad", "<b>Nothing was changed.</b> Your own password did not match."),
                ctx=ctx, status=403)
        target = _target(conn, admin_id)
        if target is None:
            return shell("No such account", _notice("bad", "<b>That account is gone.</b>"),
                         ctx=ctx, status=404)
        temp = sec.generate_temp_password()
        conn.execute("UPDATE admins SET pass_hash = ?, must_change = 1 WHERE id = ?",
                     (sec.hash_password(temp), target["id"]))
        revoked = sec.drop_admin_sessions(conn, target["id"])
        ark_db.audit(conn, action="account-reset", actor_id=me["id"], actor_name=me["username"],
                     target=target["username"], result="ok",
                     detail=f"temporary password issued, {revoked} session(s) ended")
        return _temp_password_page("Password reset", target["username"], temp, ctx=ctx,
                                   done=f"{target['username']} must choose a new password next.")


def _set_enabled(request: Request, admin_id: int, enable: bool, current_password: str,
                 csrf: str) -> Response:
    if (blocked := _gate_tls(request)) is not None:
        return blocked
    action = "account-enable" if enable else "account-disable"
    with ark_db.tx() as conn:                     # BEGIN IMMEDIATE: serialised on purpose
        ctx = _load_ctx(conn, request, ark_db.session_epoch(conn))
        if (blocked := _need_session(ctx, request)) is not None:
            return blocked
        if (blocked := _need_csrf(ctx, csrf)) is not None:
            return blocked
        me = ctx.admin
        if not _reauth(me, current_password):
            ark_db.audit(conn, action=action, actor_id=me["id"], actor_name=me["username"],
                         result="denied", detail="wrong current password")
            return shell("Administrators", _notice(
                "bad", "<b>Nothing was changed.</b> Your own password did not match."),
                ctx=ctx, status=403)
        target = _target(conn, admin_id)
        if target is None:
            return shell("No such account", _notice("bad", "<b>That account is gone.</b>"),
                         ctx=ctx, status=404)
        if not enable:
            others = int(conn.execute(
                "SELECT COUNT(*) FROM admins WHERE enabled = 1 AND must_change = 0 AND id <> ?",
                (target["id"],)).fetchone()[0])
            if others == 0:
                ark_db.audit(conn, action=action, actor_id=me["id"], actor_name=me["username"],
                             target=target["username"], result="refused",
                             detail="last usable administrator")
                return shell("Administrators", _notice(
                    "bad", "<b>That is the last administrator who can sign in.</b> Create or "
                           "reset another one first — otherwise nobody could ever get back in."),
                    ctx=ctx, status=409)
        conn.execute("UPDATE admins SET enabled = ? WHERE id = ?",
                     (1 if enable else 0, target["id"]))
        if not enable:
            sec.drop_admin_sessions(conn, target["id"])
        ark_db.audit(conn, action=action, actor_id=me["id"], actor_name=me["username"],
                     target=target["username"], result="ok")
        return _redirect("/admin/accounts")


@router.post("/accounts/{admin_id}/enable", response_class=HTMLResponse)
def account_enable(request: Request, admin_id: int, current_password: str = Form(""),
                   csrf: str = Form("")):
    return _set_enabled(request, admin_id, True, current_password, csrf)


@router.post("/accounts/{admin_id}/disable", response_class=HTMLResponse)
def account_disable(request: Request, admin_id: int, current_password: str = Form(""),
                    csrf: str = Form("")):
    return _set_enabled(request, admin_id, False, current_password, csrf)



# ---------------------------------------------------------------------------
#  audit history
# ---------------------------------------------------------------------------
@router.get("/audit", response_class=HTMLResponse)
def audit_page(request: Request):
    if (blocked := _gate_tls(request)) is not None:
        return blocked
    with ark_db.tx() as conn:
        ctx = _load_ctx(conn, request, ark_db.session_epoch(conn))
        if (blocked := _need_session(ctx, request)) is not None:
            return blocked
        rows = conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT 200").fetchall()
    items = "".join(
        f"<tr><td>{esc(r['ts'])}</td><td>{esc(r['actor_name'] or 'system')}</td>"
        f"<td>{esc(r['action'])}</td><td>{esc(r['target'] or '—')}</td>"
        f"<td>{esc(r['result'])}</td><td>{esc(r['detail'] or '')}</td></tr>"
        for r in rows
    ) or '<tr><td colspan="6">Nothing recorded yet.</td></tr>'
    body = f"""
  <h2>What has happened ({len(rows)} shown)</h2>
  <table class="plain"><thead><tr><th>When (UTC)</th><th>Who</th><th>What</th><th>About</th>
    <th>Outcome</th><th>Note</th></tr></thead><tbody>{items}</tbody></table>
  <p class="note">Times come from the box's own clock and are shown in UTC. If the box has been
     offline its clock may drift until it next sees a time source — treat the moments as
     approximate. Passwords and session tokens never appear here.</p>"""
    return shell("History", body, ctx=ctx, tagline="Who changed what, for the record.")


# ---------------------------------------------------------------------------
#  community — the board and the shared files, seen from the owner's side
# ---------------------------------------------------------------------------
BOARD_ACTIONS = {
    "pin": ("pinned", 1), "unpin": ("pinned", 0),
    "resolve": ("resolved", 1), "reopen": ("resolved", 0),
    "hide": ("hidden", 1), "restore": ("hidden", 0),
}
FILE_ACTIONS = {"hide": ("hidden", 1), "restore": ("hidden", 0)}


def _switch(on: bool, name: str, label: str, hint: str) -> str:
    checked = " checked" if on else ""
    return f"""<label class="switch">
      <input type="checkbox" name="{esc(name)}" value="1"{checked}>
      <span>{esc(label)}</span>
    </label>
    <p class="note">{esc(hint)}</p>"""


@router.get("/community", response_class=HTMLResponse)
def community(request: Request):
    if (blocked := _gate_tls(request)) is not None:
        return blocked
    with ark_db.tx() as conn:
        ctx = _load_ctx(conn, request, ark_db.session_epoch(conn))
        if (blocked := _need_session(ctx, request)) is not None:
            return blocked
        notes = conn.execute(
            "SELECT id, ts, name, body, pinned, resolved, hidden FROM notes"
            " ORDER BY id DESC LIMIT 50").fetchall()
        files = conn.execute(
            "SELECT id, ts, name, display, stored, size, hidden FROM files"
            " ORDER BY id DESC LIMIT 50").fetchall()
        board_open = ark_db.get_setting(conn, "public_board_post", "1") != "0"
        uploads_open = ark_db.get_setting(conn, "public_uploads", "1") != "0"
        ask_open = ark_db.get_setting(conn, "public_ask", "1") != "0"
        hidden_notes = int(conn.execute(
            "SELECT COUNT(*) FROM notes WHERE hidden = 1").fetchone()[0])

    board_switch = _switch(
        board_open, "board", "Anyone nearby can post a message",
        "Leave this on for a shared box. Switch it off if the board is being misused: "
        "reading still works, and nothing already written is lost.")
    uploads_switch = _switch(
        uploads_open, "uploads", "Anyone nearby can add a file",
        "Leave this on for a normal box. Switch it off while you catch up: everything "
        "already in the box stays downloadable.")
    ask_switch = _switch(
        ask_open, "ask", "Anyone nearby can ask the helper a question",
        "Leave this on for a normal box. The helper answers only from what is on this "
        "card and never keeps what anybody asked; switching it off leaves every other "
        "page exactly as it is.")
    switches = (
        '<form method="post" action="/admin/community/switches">\n'
        f'    {_csrf_field(ctx.csrf)}\n'
        f'    {board_switch}\n'
        f'    {uploads_switch}\n'
        f'    {ask_switch}\n'
        '    <button class="go-btn" type="submit">Save these switches</button>\n'
        '  </form>\n'
        '  <p class="note">These are the three things that decide what a visitor can do '
        'here. There is no sign-in for readers, by design — the box is in the room with '
        'them.</p>')

    note_items = "".join(_community_note_row(r, ctx.csrf) for r in notes) or (
        '<li>Nothing has been written yet.</li>')
    file_rows = "".join(_community_file_row(r, ctx.csrf) for r in files) or (
        '<tr><td colspan="4">Nobody has shared a file yet.</td></tr>')
    hidden_note = (f'<p class="note">{hidden_notes} hidden message'
                   f'{"" if hidden_notes == 1 else "s"} — they stay in the history and can be '
                   f'shown again at any time.</p>' if hidden_notes else "")

    body = f"""
  <h2>Who can write</h2>
  {switches}
  <h2>Messages (newest first, 50 shown)</h2>
  {hidden_note}
  <ul class="clean">{note_items}</ul>
  <h2>Shared files (newest first, 50 shown)</h2>
  <table class="plain"><thead><tr><th>File</th><th>Size</th><th>Added</th>
    <th>Actions</th></tr></thead><tbody>{file_rows}</tbody></table>
  <p class="note">Hiding or deleting a file is immediate and permanent for readers. Deleting "
     "needs your own password because there is no way back from it.</p>"""
    return shell("Community", body, ctx=ctx,
                 tagline="The board and the shared files — what people wrote, and what you can do about it.")


@router.post("/community/switches", response_class=HTMLResponse)
def community_switches(request: Request, board: str = Form(""), uploads: str = Form(""),
                       ask: str = Form(""), csrf: str = Form("")):
    """A checkbox is ticked when the door is open. Absent means closed."""
    if (blocked := _gate_tls(request)) is not None:
        return blocked
    with ark_db.tx() as conn:
        ctx = _load_ctx(conn, request, ark_db.session_epoch(conn))
        if (blocked := _need_session(ctx, request)) is not None:
            return blocked
        if (blocked := _need_csrf(ctx, csrf)) is not None:
            return blocked
        me = ctx.admin
        for key, value in (("public_board_post", "1" if board else "0"),
                           ("public_uploads", "1" if uploads else "0"),
                           ("public_ask", "1" if ask else "0")):
            ark_db.set_setting(conn, key, value)
        ark_db.audit(conn, action="community-switches", actor_id=me["id"],
                     actor_name=me["username"], result="ok",
                     detail=f"posting={'on' if board else 'off'}, "
                            f"uploads={'on' if uploads else 'off'}, "
                            f"helper={'on' if ask else 'off'}")
    return _redirect("/admin/community")


def _committed(request: Request, conn, csrf: str):
    """Load the owner's session and check the form's token. Returns (ctx, blocked)."""
    ctx = _load_ctx(conn, request, ark_db.session_epoch(conn))
    if (blocked := _need_session(ctx, request)) is not None:
        return ctx, blocked
    if (blocked := _need_csrf(ctx, csrf)) is not None:
        return ctx, blocked
    return ctx, None


@router.post("/board/{note_id}/state", response_class=HTMLResponse)
def board_state(request: Request, note_id: int, action: str = Form(""), csrf: str = Form("")):
    """Pin, unpin, resolve, reopen, hide or restore one message.

    Nothing here deletes anything: every action has an opposite, and every
    action leaves a line in the history. The column name comes from the fixed
    table above, never from the request.
    """
    if (blocked := _gate_tls(request)) is not None:
        return blocked
    with ark_db.tx() as conn:
        ctx, blocked = _committed(request, conn, csrf)
        if blocked is not None:
            return blocked
        change = BOARD_ACTIONS.get(action)
        if change is None:
            return shell("Community", _notice(
                "bad", "<b>Nothing was changed.</b> That is not an action this box offers."),
                ctx=ctx, status=400)
        if conn.execute("SELECT 1 FROM notes WHERE id = ?", (note_id,)).fetchone() is None:
            return shell("Community", _notice(
                "bad", "<b>Nothing was changed.</b> That message is no longer here."),
                ctx=ctx, status=404)
        column, value = change
        conn.execute(f"UPDATE notes SET {column} = ? WHERE id = ?", (value, note_id))
        ark_db.audit(conn, action=f"board-{action}", actor_id=ctx.admin["id"],
                     actor_name=ctx.admin["username"], target=str(note_id), result="ok")
    return _redirect("/admin/community")


@router.post("/files/{file_id}/state", response_class=HTMLResponse)
def file_state(request: Request, file_id: int, action: str = Form(""), csrf: str = Form("")):
    """Hide a shared file from readers, or put it back. The file stays on the card."""
    if (blocked := _gate_tls(request)) is not None:
        return blocked
    with ark_db.tx() as conn:
        ctx, blocked = _committed(request, conn, csrf)
        if blocked is not None:
            return blocked
        change = FILE_ACTIONS.get(action)
        if change is None:
            return shell("Community", _notice(
                "bad", "<b>Nothing was changed.</b> That is not an action this box offers."),
                ctx=ctx, status=400)
        if conn.execute("SELECT 1 FROM files WHERE id = ?", (file_id,)).fetchone() is None:
            return shell("Community", _notice(
                "bad", "<b>Nothing was changed.</b> That file is no longer listed."),
                ctx=ctx, status=404)
        column, value = change
        conn.execute(f"UPDATE files SET {column} = ? WHERE id = ?", (value, file_id))
        ark_db.audit(conn, action=f"file-{action}", actor_id=ctx.admin["id"],
                     actor_name=ctx.admin["username"], target=str(file_id), result="ok")
    return _redirect("/admin/community")


def human_size(n) -> str:
    """The same wording the public page uses, so a figure never looks different."""
    n = float(n or 0)
    for unit in ("B", "kB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def quote_name(stored: str) -> str:
    from urllib.parse import quote
    return quote(stored or "", safe="")


def _community_note_row(row, csrf: str) -> str:
    state = []
    if row["pinned"]:
        state.append("pinned")
    if row["resolved"]:
        state.append("sorted out")
    if row["hidden"]:
        state.append("hidden")
    badge = f'<span class="sub">{esc(", ".join(state))}</span>' if state else ""

    def button(action: str, label: str) -> str:
        return (f'<button class="small" type="submit" name="action" value="{esc(action)}">'
                f'{esc(label)}</button>')

    actions = [
        button("unpin", "Unpin") if row["pinned"] else button("pin", "Pin to top"),
        button("reopen", "Still open") if row["resolved"] else button("resolve", "Sorted out"),
        button("restore", "Show again") if row["hidden"] else button("hide", "Hide"),
    ]
    form = (f'<form class="inline" method="post" action="/admin/board/{int(row["id"])}/state">'
            f'{_csrf_field(csrf)}{"".join(actions)}</form>')
    return (f'<li><b>{esc(row["name"])}</b> {badge}'
            f'<span class="sub">{esc(ago(row["ts"]))}</span>'
            f'<span class="sub" style="white-space:pre-wrap">{esc(row["body"])}</span>'
            f'{form}</li>')


def _community_file_row(row, csrf: str) -> str:
    hidden = " · hidden" if row["hidden"] else ""
    toggle = "restore" if row["hidden"] else "hide"
    label = "Show again" if row["hidden"] else "Hide"
    return f"""<tr>
      <td><a href="/drop/{esc(quote_name(row['stored'] or row['name']))}">{esc(row['display'] or row['name'])}</a></td>
      <td>{esc(human_size(row['size']))}</td>
      <td>{esc(ago(row['ts']))}{esc(hidden)}</td>
      <td>
        <form class="inline" method="post" action="/admin/files/{int(row['id'])}/state">
          {_csrf_field(csrf)}
          <button class="small" type="submit" name="action" value="{toggle}">{esc(label)}</button>
        </form>
        <form class="inline" method="post" action="/admin/files/{int(row['id'])}/delete">
          {_csrf_field(csrf)}
          <input type="password" name="current_password" placeholder="Your password" required>
          <button class="small danger" type="submit">Delete for ever</button>
        </form>
      </td>
    </tr>"""


@router.post("/files/{file_id}/delete", response_class=HTMLResponse)
def file_delete(request: Request, file_id: int, current_password: str = Form(""),
                csrf: str = Form("")):
    """Remove a file and its link for good — the one action with no way back."""
    if (blocked := _gate_tls(request)) is not None:
        return blocked
    with ark_db.tx() as conn:
        ctx, blocked = _committed(request, conn, csrf)
        if blocked is not None:
            return blocked
        me = ctx.admin
        if not _reauth(me, current_password):
            ark_db.audit(conn, action="file-delete", actor_id=me["id"],
                         actor_name=me["username"], target=str(file_id),
                         result="denied", detail="wrong current password")
            return shell("Community", _notice(
                "bad", "<b>Nothing was deleted.</b> Your own password did not match."),
                ctx=ctx, status=403)
        row = conn.execute("SELECT id, name, display, stored FROM files WHERE id = ?",
                           (file_id,)).fetchone()
        if row is None:
            return shell("Community", _notice(
                "bad", "<b>Nothing was deleted.</b> That file is no longer listed."),
                ctx=ctx, status=404)
        # The bytes may be a film's only copy: the Media shelf keeps its original
        # in the File Box, so deleting the row here would leave a film that can
        # neither play nor be played from its derivative.
        kept = conn.execute("SELECT COUNT(*) FROM media WHERE stored = ?",
                            (str(row["stored"] or row["name"]),)).fetchone()[0]
        if kept:
            return _redirect("/admin/community?err=" + urllib.parse.quote(
                "That file is a film on the Media shelf. Delete it from the Media page, "
                "so its picture and playing copy go with it. Nothing was deleted."))
        removed = _remove_from_card(row["stored"] or row["name"])
        conn.execute("DELETE FROM files WHERE id = ?", (file_id,))
        ark_db.audit(conn, action="file-delete", actor_id=me["id"], actor_name=me["username"],
                     target=row["display"] or row["name"], result="ok",
                     detail="file removed from the card" if removed else
                            "record removed; the file was already gone")
    return _redirect("/admin/community")


def _remove_from_card(name: str) -> bool:
    """Delete one file from the shared folder, and only from that folder."""
    folder = ark_storage.drop_dir()
    target = folder / name
    if target.parent != folder or target.name in ("", ".", ".."):
        return False          # never follow a name out of the folder
    try:
        target.unlink()
        return True
    except OSError:
        return False          # already gone, or the card went read-only


# ---------------------------------------------------------------------------
#  games — the shelf, player names, and what the box may fetch
# ---------------------------------------------------------------------------
def _arcade_candidate_row(c: dict, csrf: str) -> str:
    install = (f'''<form method="post" action="/admin/arcade/suggestions/{int(c['id'])}/install">
              {_csrf_field(csrf)}
              <button class="go-btn small" type="submit">Check and add</button>
            </form>''' if c["status"] == "pending" else "")
    return f"""<tr>
          <td>{esc(c['title'])}<br><span class="hint">{esc(c['license'] or 'no licence stated')}</span></td>
          <td>{esc(ark_arcade.SYSTEMS.get(c['system'], {}).get('label', c['system']))}</td>
          <td>{esc(c['status'])}<br><span class="hint">{esc(c['reason'] or 'ready to check')}</span></td>
          <td>
            {install}
            <form method="post" action="/admin/arcade/suggestions/{int(c['id'])}/state">
              {_csrf_field(csrf)}
              <input type="hidden" name="action" value="{'pending' if c['status'] == 'refused' else 'refused'}">
              <button class="go-btn small" type="submit">{'Allow again' if c['status'] == 'refused' else 'Put away'}</button>
            </form>
          </td>
        </tr>"""
# ---------------------------------------------------------------------------
#  the household page — the owner's half
# ---------------------------------------------------------------------------
def _household_form(ctx, values) -> str:
    """The nine answers, in the order the page asks for them."""
    fields = []
    for key, label, hint, limit, lines in ark_household.ENTRIES:
        value = esc((values or {}).get(key, ""))
        if lines:
            control = (f'<textarea id="{esc(key)}" name="{esc(key)}" '
                       f'maxlength="{int(limit)}">{value}</textarea>')
        else:
            control = (f'<input id="{esc(key)}" name="{esc(key)}" type="text" '
                       f'maxlength="{int(limit)}" value="{value}">')
        fields.append(f'    <label for="{esc(key)}">{esc(label)}</label>\n'
                      f'    <p class="note">{esc(hint)}</p>\n    {control}')
    return ('<form method="post" action="/admin/household/save">\n    '
            + _csrf_field(ctx.csrf) + "\n" + "\n".join(fields)
            + '\n    <button class="go-btn" type="submit">Save this page</button>\n  </form>')


def _review_rows(ctx, rows) -> str:
    """One emergency topic per line: the pages, the last look, and what to do about it."""
    items = []
    for row in rows:
        pages = ", ".join(
            f'<a href="/vault/{esc(page)}">{esc(page)}</a>' for page in row["pages"]
        ) or "no page on the box yet"
        actions = (
            f'<form method="post" action="/admin/household/read/{esc(row["key"])}">\n'
            f'      {_csrf_field(ctx.csrf)}\n'
            f'      <label for="note-{esc(row["key"])}">What needed changing? '
            f'(or leave blank)</label>\n'
            f'      <input id="note-{esc(row["key"])}" name="note" type="text" maxlength="300">\n'
            f'      <button class="go-btn" type="submit">Somebody read this today</button>\n'
            f'    </form>')
        if row["checked"]:
            actions += (
                f'\n    <form method="post" action="/admin/household/forget/{esc(row["key"])}">\n'
                f'      {_csrf_field(ctx.csrf)}\n'
                f'      <button class="go-btn" type="submit">Take that back</button>\n'
                f'    </form>')
        items.append(
            f'<li><b>{esc(row["name"])}</b>'
            f'<span class="sub">{esc(row["asks"])}</span>'
            f'<span class="sub">Pages: {pages}</span>'
            f'<span class="sub">{esc(row["words"])}'
            + (f' — {esc(row["note"])}' if row["note"] else "")
            + f'</span>\n    {actions}</li>')
    return '<ul class="clean">' + "".join(items) + '</ul>'


def _pack_owner_block(pack, csrf: str) -> str:
    """What is on the card, and the one button that makes a new copy."""
    if pack.get("present") and pack.get("readable"):
        made = str(pack.get("built") or "").strip()
        state = (f'A copy is on the box: {esc(ark_health.human_size(pack.get("bytes")))} · '
                 f'{int(pack.get("members") or 0)} pages'
                 + (f' · made {esc(made)}' if made else "") + '.')
        link = ('<p><a class="go-link" href="/essentials.zip">Download this copy</a></p>')
    elif pack.get("present"):
        state = ("A copy is on the box but it cannot be read back, so it is not offered. "
                 "Make a new one.")
        link = ""
    else:
        state = "There is no copy to carry yet."
        link = ""
    return (f'<p>{state}</p>{link}\n'
            '  <form method="post" action="/admin/household/pack">\n'
            f'    {_csrf_field(csrf)}\n'
            '    <button class="go-btn" type="submit">Make a new copy now</button>\n'
            '  </form>')


@router.get("/household", response_class=HTMLResponse)
def household(request: Request):
    """The owner's half: the nine answers, the switch, the review, and the copy."""
    if (blocked := _gate_tls(request)) is not None:
        return blocked
    with ark_db.tx() as conn:
        ctx = _load_ctx(conn, request, ark_db.session_epoch(conn))
        if (blocked := _need_session(ctx, request)) is not None:
            return blocked
        page_open = ark_db.get_setting(conn, "public_household", "1") != "0"

    values = ark_household.load()
    updated = ark_household.updated()
    answered, total = ark_household.progress(values)
    rows = ark_review.summary(ark_health.vault_dir())
    pack = ark_essentials.status(ark_essentials.pack_dir())
    switch = _switch(
        page_open, "household", "Anyone nearby can read the household page",
        "Leave this on. It is the page somebody opens when something has gone wrong, "
        "and switching it off changes nothing else on the box.")
    stamp = (f'<p class="note">Last saved {esc(updated)}.</p>' if updated else "")
    soon = ark_review.CHECK_EVERY_DAYS
    body = f"""
  <h2>What this household wrote down ({answered} of {total} answered)</h2>
  <p class="note">Short answers, in your own words. Nothing here is shown to anyone
     except people on this box's own Wi-Fi.</p>
  {_household_form(ctx, values)}
  {stamp}
  <h2>Who can read it</h2>
  <form method="post" action="/admin/household/switch">
    {_csrf_field(ctx.csrf)}
    {switch}
    <button class="go-btn" type="submit">Save this switch</button>
  </form>
  <h2>The emergency pages — when did a person last read one?</h2>
  <p class="note">A page nobody has read for {int(soon)} days is a page to read again:
     words go stale and a neighbour's number changes. The box cannot tell whether a page is
     right — only a person can, so this page records the look, never the judgement.</p>
  {_review_rows(ctx, rows)}
  <h2>The pages to carry away</h2>
  <p class="note">One file holding the manuals, the guides and the recovery sheet, so they
     still open on a computer with no box and no internet. A copy left on this card does
     not survive this card — put one on a stick or a phone as well.</p>
  {_pack_owner_block(pack, ctx.csrf)}
"""
    return shell("Household", body, ctx=ctx,
                 tagline="The page a household fills in itself — and the emergency pages "
                         "behind it.")


@router.post("/household/save", response_class=HTMLResponse)
def household_save(request: Request,
                   people: str = Form(""), meeting: str = Form(""),
                   contact: str = Form(""), water: str = Form(""),
                   power: str = Form(""), gas: str = Form(""),
                   firstaid: str = Form(""), animals: str = Form(""),
                   notes: str = Form(""), csrf: str = Form("")):
    """Store the nine answers.  Too long is refused with the limit named, never trimmed."""
    if (blocked := _gate_tls(request)) is not None:
        return blocked
    with ark_db.tx() as conn:
        ctx, blocked = _committed(request, conn, csrf)
        if blocked is not None:
            return blocked
        actor_id, who = ctx.admin["id"], ctx.admin["username"]
    given = {"people": people, "meeting": meeting, "contact": contact,
             "water": water, "power": power, "gas": gas, "firstaid": firstaid,
             "animals": animals, "notes": notes}
    try:
        kept = ark_household.save(given, who=who)
    except ark_household.TooLong as exc:
        return shell("Household", f"""
  <p class="status"><span class="dot no" aria-hidden="true"></span>
     <span><b>Not saved.</b> {esc(exc)}</span></p>
  <p class="note">Nothing has been changed — shorten that answer and save again.</p>
  <div class="nav"><a href="/admin/household">Back to the page</a></div>
""", ctx=ctx, status=400)
    answered = len(ark_household.filled(kept))
    with ark_db.tx() as conn:
        ark_db.audit(conn, action="household-save", actor_id=actor_id,
                     actor_name=who, result="ok",
                     detail=f"{answered} of {len(ark_household.ENTRIES)} answered")
    return _redirect("/admin/household?ok=saved")


@router.post("/household/switch", response_class=HTMLResponse)
def household_switch(request: Request, household: str = Form(""), csrf: str = Form("")):
    """A ticked box means anybody nearby can read the page; absent means only the owner."""
    if (blocked := _gate_tls(request)) is not None:
        return blocked
    with ark_db.tx() as conn:
        ctx, blocked = _committed(request, conn, csrf)
        if blocked is not None:
            return blocked
        me = ctx.admin
        ark_db.set_setting(conn, "public_household", "1" if household else "0")
        ark_db.audit(conn, action="household-switch", actor_id=me["id"],
                     actor_name=me["username"], result="ok",
                     detail=f"public household page={'on' if household else 'off'}")
    return _redirect("/admin/household")


@router.post("/household/read/{topic}", response_class=HTMLResponse)
def household_read(request: Request, topic: str, note: str = Form(""),
                   csrf: str = Form("")):
    """A person says they read one emergency page today, and maybe what was wrong."""
    if (blocked := _gate_tls(request)) is not None:
        return blocked
    with ark_db.tx() as conn:
        ctx, blocked = _committed(request, conn, csrf)
        if blocked is not None:
            return blocked
        actor_id, who = ctx.admin["id"], ctx.admin["username"]
    try:
        kept = ark_review.record(topic, who=who, note=note)
    except ark_review.UnknownTopic:
        return shell("Household", _notice(
            "bad", "<b>Nothing was changed.</b> That is not a page this box keeps."),
            ctx=ctx, status=400)
    except ValueError as exc:
        return shell("Household", _notice(
            "bad", f"<b>Nothing was changed.</b> {esc(exc)}"), ctx=ctx, status=400)
    with ark_db.tx() as conn:
        ark_db.audit(conn, action="household-read", actor_id=actor_id, actor_name=who,
                     result="ok",
                     detail=f"{topic} read {kept['on']}"
                            + (f" — {kept['note'][:160]}" if kept["note"] else ""))
    return _redirect("/admin/household?ok=read")


@router.post("/household/forget/{topic}", response_class=HTMLResponse)
def household_forget(request: Request, topic: str, csrf: str = Form("")):
    """Take one 'somebody read this' back, so the page asks for a fresh look again."""
    if (blocked := _gate_tls(request)) is not None:
        return blocked
    with ark_db.tx() as conn:
        ctx, blocked = _committed(request, conn, csrf)
        if blocked is not None:
            return blocked
        actor_id, who = ctx.admin["id"], ctx.admin["username"]
    try:
        ark_review.forget(topic)
    except ark_review.UnknownTopic:
        return shell("Household", _notice(
            "bad", "<b>Nothing was changed.</b> That is not a page this box keeps."),
            ctx=ctx, status=400)
    with ark_db.tx() as conn:
        ark_db.audit(conn, action="household-forget", actor_id=actor_id, actor_name=who,
                     result="ok", detail=f"{topic}: that look is taken back")
    return _redirect("/admin/household?ok=forgot")


@router.post("/household/pack", response_class=HTMLResponse)
def household_pack(request: Request, csrf: str = Form("")):
    """Write the one file the household can carry away, then prove it reads back whole."""
    if (blocked := _gate_tls(request)) is not None:
        return blocked
    with ark_db.tx() as conn:
        ctx, blocked = _committed(request, conn, csrf)
        if blocked is not None:
            return blocked
        actor_id, who = ctx.admin["id"], ctx.admin["username"]

    vault = ark_health.vault_dir()
    sheet = ark_health.ark_root() / "RECOVERY-SHEET.txt"
    try:
        made = ark_essentials.build(ark_essentials.pack_dir(), vault=vault, sheet=sheet)
    except (FileNotFoundError, OSError) as exc:
        with ark_db.tx() as conn:
            ark_db.audit(conn, action="household-pack", actor_id=actor_id,
                         actor_name=who, result="failed", detail=str(exc)[:200])
        return shell("Household", f"""
  <p class="status"><span class="dot no" aria-hidden="true"></span>
     <span><b>No copy was made.</b> {esc(exc)}</span></p>
  <p class="note">The copy is made from the pages under <b>Manuals &amp; Guides</b>, so there
     has to be at least one page there first. Nothing else has changed.</p>
  <div class="nav"><a href="/admin/household">Back to the page</a></div>
""", ctx=ctx, status=400)

    whole, problems = ark_essentials.verify(made["path"])
    detail = (f"{int(made['members'])} pages, {int(made['bytes'])} bytes, "
              f"sha256 {str(made['sha256'])[:16]}")
    with ark_db.tx() as conn:
        ark_db.audit(conn, action="household-pack", actor_id=actor_id, actor_name=who,
                     result="ok" if whole else "damaged", detail=detail)
    if not whole:
        return shell("Household", f"""
  <p class="status"><span class="dot no" aria-hidden="true"></span>
     <span><b>The copy was made but it does not read back whole.</b>
     {'; '.join(esc(one) for one in problems)}</span></p>
  <p class="note">It is on the card, and it is not offered for download. Make it again; if
     it happens twice the card is worth checking.</p>
  <div class="nav"><a href="/admin/household">Back to the page</a></div>
""", ctx=ctx, status=400)
    return _redirect("/admin/household?ok=pack")


# ---------------------------------------------------------------------------
#  the owner's network — one network saved, or one restart on it
# ---------------------------------------------------------------------------
NETWORK_MINUTES = 30

STANDING_BOOT = {
    "hotspot": "A cold start broadcasts the box's own Wi-Fi and stays there.",
    "auto": "A cold start tries a saved network first, then broadcasts.",
    "home": "A cold start tries your own network first, then broadcasts.",
    "lan": "A cold start tries your own network for administrative work, then broadcasts.",
}


def _minutes_from(value) -> int:
    """The minutes field as a whole number.  A refusal, never a guess."""
    text = str(value or "").strip()
    if not text:
        return NETWORK_MINUTES
    try:
        return int(text)
    except ValueError:
        raise ark_network.NetworkRefused(
            "The number of minutes has to be a whole number, like 30.") from None


def _what_was_asked(text: str) -> str:
    """The sentence that says what the box now expects, out of what a helper said.

    ``ark-mode request-lan`` prints the request first and a reassurance second, so
    a banner that showed only the last line would answer a question nobody asked.
    Anything else a helper said falls through to its last line, as before.
    """
    for line in (text or "").splitlines():
        line = line.strip()
        if line.lower().startswith("asked:"):
            return line[:300]
    return ark_network.last_words(text)


def _network_choice(ctx, networks) -> str:
    """The form that asks for ONE restart on a saved network."""
    if networks:
        options = "".join(
            f'<option value="{esc(n["profile"])}">{esc(n["ssid"] or n["profile"])}</option>'
            for n in networks)
        picker = ('    <label for="network">Which saved network?</label>\n'
                  f'    <select id="network" name="network">{options}</select>')
    else:
        picker = ('    <p class="note">No network of yours is saved yet, so this asks for '
                  'the one the box already knows. Add a network below to name a different '
                  'one.</p>')
    restart = ('    <label class="switch">\n'
               '      <input type="checkbox" name="restart" value="1">\n'
               '      <span>Restart the box now, without waiting</span>\n'
               '    </label>\n'
               '    <p class="note">The restart happens a few seconds after this page has '
               'answered, so this message is not lost under it.</p>')
    return f"""<form method="post" action="/admin/network/lan">
    {_csrf_field(ctx.csrf)}
{picker}
    <label for="minutes">How long should the request wait for a restart?</label>
    <input id="minutes" name="minutes" type="number" inputmode="numeric" min="1"
           max="{int(ark_network.LAN_MINUTES_MAX)}" value="{int(NETWORK_MINUTES)}">
    <p class="note">In minutes, up to {int(ark_network.LAN_MINUTES_MAX)}. After that the
       request lapses by itself and a restart broadcasts as usual.</p>
{restart}
    <button class="go-btn" type="submit">Ask for one restart on my network</button>
  </form>"""


def _network_add_form(ctx) -> str:
    """Saving a network.  The password is never written back onto the page."""
    return f"""<form method="post" action="/admin/network/add" autocomplete="off">
    {_csrf_field(ctx.csrf)}
    <label for="ssid">The name the phone shows for that Wi-Fi</label>
    <input id="ssid" name="ssid" type="text" maxlength="{int(ark_network.SSID_MAX)}"
           autocomplete="off" spellcheck="false" required>
    <label for="password">Its password</label>
    <input id="password" name="password" type="password"
           maxlength="{int(ark_network.PSK_MAX)}" autocomplete="new-password" required>
    <p class="note">At least {int(ark_network.PSK_MIN)} characters, exactly as it is written
       on the router. It goes straight to the box and is never shown again, kept on this
       page, or written into the records.</p>
    <button class="go-btn" type="submit">Save this network</button>
  </form>"""


def _network_saved(networks) -> str:
    """The networks the box already knows.  Unreadable is not empty (§8.7)."""
    if networks is None:
        return ('<p class="note">The box could not read its list of saved networks just now. '
                'Nothing is wrong with the Wi-Fi because of that — you can still ask for a '
                'restart, and a saved network is still usable.</p>')
    if not networks:
        return ('<p class="note">No networks of your own are saved on this box yet. While that '
                'is true the box only ever broadcasts its own.</p>')
    items = "".join(
        f'<li>{esc(n["ssid"] or n["profile"])}'
        f'<span class="sub">saved as &ldquo;{esc(n["profile"])}&rdquo;</span></li>'
        for n in networks)
    return f'<ul class="clean">{items}</ul>'


def _network_body(ctx, *, flash: str = "") -> str:
    """The whole page, so a refusal and a success render the same thing."""
    state = ark_network.request_state()
    networks = ark_network.saved_networks()
    if state.get("state") == "waiting":
        waiting = (_notice("good", esc(ark_network.request_words(state))) + "\n  "
                   '<form method="post" action="/admin/network/cancel">\n'
                   f'    {_csrf_field(ctx.csrf)}\n'
                   '    <button class="go-btn" type="submit">Take that request back</button>\n'
                   '  </form>')
    else:
        waiting = f'<p class="note">{esc(ark_network.request_words(state))}</p>'
    standing = STANDING_BOOT.get(
        ark_network.boot_mode(),
        "A cold start broadcasts the box's own Wi-Fi and stays there.")
    return f"""
  {flash}
  <h2>Where the box is now</h2>
  <p>{esc(ark_network.mode_words())}</p>
  <p class="note">{esc(standing)}</p>
  <h2>Restart on my own network for a while</h2>
  <p class="note">This is a request, not a change of habit. The next restart joins the
     network you name — once — and the start after that broadcasts as usual. If the
     network is not there, the box broadcasts instead, so this can never leave it
     somewhere you cannot reach it.</p>
  {waiting}
  {_network_choice(ctx, networks)}
  <h2>Add a Wi-Fi network</h2>
  <p class="note">Saving a network does not make the box join it. It only means the box
     can be asked to restart on it, here or later.</p>
  {_network_add_form(ctx)}
  <h2>Networks already saved</h2>
  {_network_saved(networks)}
"""


@router.get("/network", response_class=HTMLResponse)
def network_page(request: Request):
    """Add a network, and ask for one restart on a saved one."""
    if (blocked := _gate_tls(request)) is not None:
        return blocked
    with ark_db.tx() as conn:
        ctx = _load_ctx(conn, request, ark_db.session_epoch(conn))
        if (blocked := _need_session(ctx, request)) is not None:
            return blocked
    flash = ""
    if request.query_params.get("ok"):
        flash = _notice("good", esc(request.query_params["ok"][:300]))
    elif request.query_params.get("err"):
        flash = _notice("bad", esc(request.query_params["err"][:300]))
    return shell("Network", _network_body(ctx, flash=flash), ctx=ctx,
                 tagline="Where the box can be reached — and how to ask it for one "
                         "restart on your own Wi-Fi.")


@router.post("/network/add", response_class=HTMLResponse)
def network_add(request: Request, ssid: str = Form(""), password: str = Form(""),
                csrf: str = Form("")):
    """Save one Wi-Fi network, so a restart can be asked for it later."""
    if (blocked := _gate_tls(request)) is not None:
        return blocked
    with ark_db.tx() as conn:
        ctx, blocked = _committed(request, conn, csrf)
        if blocked is not None:
            return blocked
        actor_id, who = ctx.admin["id"], ctx.admin["username"]
    try:
        kept = ark_network.add_network(ssid, password)
    except ark_network.NetworkRefused as exc:
        return shell("Network", _network_body(ctx, flash=_notice(
            "bad", f"<b>Nothing was saved.</b> {esc(exc)}")), ctx=ctx, status=400)
    said = ark_network.last_words(kept.get("words", "")) or f'Saved "{kept["ssid"]}".'
    with ark_db.tx() as conn:
        ark_db.audit(conn, action="network-add", actor_id=actor_id, actor_name=who,
                     result="ok", detail=f'saved a network called "{kept["ssid"]}"')
    return _redirect("/admin/network?ok=" + urllib.parse.quote(said, safe=""))


@router.post("/network/lan", response_class=HTMLResponse)
def network_lan(request: Request, network: str = Form(""), minutes: str = Form(""),
                restart: str = Form(""), csrf: str = Form("")):
    """Ask for ONE restart on a saved network — at the next start, or now."""
    if (blocked := _gate_tls(request)) is not None:
        return blocked
    with ark_db.tx() as conn:
        ctx, blocked = _committed(request, conn, csrf)
        if blocked is not None:
            return blocked
        actor_id, who = ctx.admin["id"], ctx.admin["username"]
    try:
        said = ark_network.request_lan(network, minutes=_minutes_from(minutes),
                                       by=who, restart=bool(restart))
    except ark_network.NetworkRefused as exc:
        # `--restart` writes the request *first* and asks for the reboot second, so a
        # refusal on that path usually means one thing only: the request is on the box
        # and the restart is not.  Saying "Nothing was asked." there is answered by the
        # page's own Waiting block, with its "Take that request back" button, two lines
        # below — the banner denying what the page is offering to undo.  Three things
        # must all be true to say otherwise: the person asked for the restart, the
        # helper was really reached (`exc.ran` — a name with a space in it, or minutes
        # of 0, is refused before anything runs and cannot have written a request), and
        # the box's own clock still calls the request waiting.  So: the form says what
        # was asked, `exc.ran` says whether the helper could have written it, and the
        # request file itself says whether it is there.  Believe the box.
        kept = (bool(restart) and getattr(exc, "ran", False)
                and ark_network.request_state().get("state") == "waiting")
        return shell("Network", _network_body(ctx, flash=_notice(
            "bad", f"<b>{'The request is kept' if kept else 'Nothing was asked'}.</b> "
                   f"{esc(exc)}")), ctx=ctx, status=400)
    state = ark_network.request_state()
    asked = state.get("network") or network or ark_network.default_network()
    with ark_db.tx() as conn:
        ark_db.audit(conn, action="network-request-lan", actor_id=actor_id, actor_name=who,
                     result="ok",
                     detail=(f'asked for one restart on "{asked}" within '
                             f'{_minutes_from(minutes)} minutes'
                             f'{" — and restarted the box now" if restart else ""}'))
    words = _what_was_asked(said) or "The request is waiting for the next restart."
    return _redirect("/admin/network?ok=" + urllib.parse.quote(words, safe=""))


@router.post("/network/cancel", response_class=HTMLResponse)
def network_cancel(request: Request, csrf: str = Form("")):
    """Take a waiting request back.  The box itself was never changed by it."""
    if (blocked := _gate_tls(request)) is not None:
        return blocked
    with ark_db.tx() as conn:
        ctx, blocked = _committed(request, conn, csrf)
        if blocked is not None:
            return blocked
        actor_id, who = ctx.admin["id"], ctx.admin["username"]
    try:
        said = ark_network.cancel_request()
    except ark_network.NetworkRefused as exc:
        return shell("Network", _network_body(ctx, flash=_notice(
            "bad", f"<b>Nothing was changed.</b> {esc(exc)}")), ctx=ctx, status=400)
    with ark_db.tx() as conn:
        ark_db.audit(conn, action="network-cancel", actor_id=actor_id, actor_name=who,
                     result="ok", detail="took the restart request back")
    words = ark_network.last_words(said) or "Nothing is waiting any more."
    return _redirect("/admin/network?ok=" + urllib.parse.quote(words, safe=""))


@router.get("/arcade", response_class=HTMLResponse)
def arcade_page(request: Request):
    if (blocked := _gate_tls(request)) is not None:
        return blocked
    with ark_db.tx() as conn:
        ctx = _load_ctx(conn, request, ark_db.session_epoch(conn))
        if (blocked := _need_session(ctx, request)) is not None:
            return blocked
        games = ark_arcade.all_games(conn)
        candidates = ark_arcade_install.candidate_rows(conn)
        names_open = ark_arcade.players_open(conn)
        add_open = ark_arcade.uploads_open(conn)
        auto = (ark_db.get_setting(conn, "arcade_auto_install", "0") or "0") == "1"
        last = ark_arcade.last_job(conn)

    game_rows = "".join(
        f"""<tr>
          <td>{esc(g['title'])}<br><span class="hint">{esc(g['author'] or 'maker not stated')}</span></td>
          <td>{esc(ark_arcade.SYSTEMS.get(g['system'], {}).get('label', g['system']))}</td>
          <td>{esc(g['license'] or 'not stated')}</td>
          <td>{'Hidden' if g['hidden'] else ('On the shelf' if g['published'] else 'Not shown')}</td>
          <td><form method="post" action="/admin/arcade/{int(g['id'])}/state">
            {_csrf_field(ctx.csrf)}
            <input type="hidden" name="action" value="{'show' if g['hidden'] else 'hide'}">
            <button class="go-btn small" type="submit">{'Show on shelf' if g['hidden'] else 'Hide from shelf'}</button>
          </form></td>
        </tr>""" for g in games) or '<tr><td colspan="5">No games on the shelf yet.</td></tr>'

    wanted = [c for c in candidates if c["status"] in ("pending", "refused")]
    candidate_rows = "".join(_arcade_candidate_row(c, ctx.csrf) for c in wanted) or (
        '<tr><td colspan="4">No suggestions waiting.</td></tr>')

    last_line = (f'<p class="hint">Last check: {esc(last["finished_at"] or last["started_at"])} — '
                 f'{esc(last["detail"])}</p>' if last else
                 '<p class="hint">The box has not checked for new games yet.</p>')
    names_switch = _switch(
        names_open, "players", "Anyone can use a name to keep saves",
        "A name is only for games: it cannot sign in to this area or change the box. "
        "Leave it on unless names are being misused; guests can always play without one.")
    add_switch = _switch(
        add_open, "public_add", "Anyone can add a game of their own",
        "Leave this on so a visitor can put a game file of their own on the shelf from the "
        "games page, without signing in. A visitor can only add a game, never replace one: "
        "a name already on the shelf is refused. Switch it off and the shelf keeps playing.")
    auto_switch = _switch(
        auto, "auto", "Add newly found games without asking me",
        "Leave this off to see each suggestion first. Even when it is on, a game is only "
        "kept if every check passes; the licence its source states is written on the "
        "game's page as provenance, never used to refuse it.")
    flash = ""
    if request.query_params.get("ok"):
        flash = _notice("good", esc(request.query_params["ok"][:300]))
    elif request.query_params.get("err"):
        flash = _notice("bad", esc(request.query_params["err"][:300]))
    body = f"""
  {flash}
  <h2>A game you already have</h2>
  <p class="note">Pick a game file that is already on this computer or phone. The box
  reads the file itself and shows you the words it would put on the games page before
  anything is added.</p>
  <form method="post" action="/admin/arcade/upload" enctype="multipart/form-data">
    {_csrf_field(ctx.csrf)}
    <p><input type="file" name="file" required></p>
    <button class="go-btn" type="submit">Look at this file</button>
  </form>
  <p class="note">One file at a time, and no bigger than 64 MB. A file the box cannot
  play is refused with the reason, and the shelf is left exactly as it was.</p>

  <h2>Games on the shelf</h2>
  <table class="plain"><thead><tr><th>Game</th><th>Kind</th><th>Licence</th>
    <th>State</th><th>What to do</th></tr></thead><tbody>{game_rows}</tbody></table>
  <p class="note">Hiding a game takes it off the games page and stops its file being
  handed out. Nothing is deleted, so showing it again is always possible.</p>

  <h2>Player names and new games</h2>
  <form method="post" action="/admin/arcade/switches">
    {_csrf_field(ctx.csrf)}
    {names_switch}
    {add_switch}
    {auto_switch}
    <button class="go-btn" type="submit">Save these switches</button>
  </form>
  <p class="note">The box asks the curated homebrew source for new games once a week.
  Every suggestion records what licence the source stated; nothing is fetched from
  anywhere else.</p>
  {last_line}

  <h2>Suggested games</h2>
  <table class="plain"><thead><tr><th>Game</th><th>Kind</th><th>What the box says</th>
    <th>What to do</th></tr></thead><tbody>{candidate_rows}</tbody></table>
  <p class="note">“Check and add” downloads the game, proves its fingerprint and shape,
  and only then puts it on the shelf. A failure changes nothing.</p>"""
    return shell("Games", body, ctx=ctx,
                 tagline="The game shelf, who may keep saves, and every suggestion the box has recorded.")


def _own_game_form(ctx, token: str, filename: str, pre: dict, filled: dict,
                   problem: str = "") -> HTMLResponse:
    """The words the box would put on the games page, before anything is added."""
    options = "".join(
        f'<option value="{esc(key)}"{" selected" if key == filled.get("system") else ""}>'
        f'{esc(info["label"])}</option>'
        for key, info in sorted(ark_arcade.SYSTEMS.items(),
                                key=lambda item: item[1]["label"]))
    notice = _notice("bad", "<b>" + esc(problem) + "</b>") if problem else ""
    facts = " · ".join(ark_rommeta.game_facts(pre.get("facts") or {}, limit=6))
    facts_line = f'<p class="hint">{esc(facts)}</p>' if facts else ""
    return shell("Games", f"""
  {notice}
  <h2>Does this look right?</h2>
  <p><b>{esc(pre.get("sentence") or "")}</b></p>
  {facts_line}
  <p class="hint">The file you picked is <b>{esc(filename)}</b>. Nothing has been added yet.</p>
  <form method="post" action="/admin/arcade/upload/add">
    {_csrf_field(ctx.csrf)}
    <input type="hidden" name="token" value="{esc(token)}">
    <input type="hidden" name="filename" value="{esc(filename)}">
    <p><label>Name on the games page<br>
      <input type="text" name="title" value="{esc(filled.get('title') or '')}" maxlength="120" required></label></p>
    <p><label>Which console<br><select name="system" required>{options}</select></label></p>
    <p><label>One line under the name<br>
      <input type="text" name="description" value="{esc(filled.get('description') or '')}" maxlength="300"></label></p>
    <p><label>Who made it<br>
      <input type="text" name="author" value="{esc(filled.get('author') or '')}" maxlength="80"></label></p>
    <p><label>Year<br>
      <input type="text" name="release" value="{esc(filled.get('release') or '')}" maxlength="40"></label></p>
    <p><label>What its licence says (shown on the game's page)<br>
      <input type="text" name="license_text" value="{esc(filled.get('license') or '')}" maxlength="80"></label></p>
    <button class="go-btn" type="submit">Add this game</button>
  </form>
  <form method="post" action="/admin/arcade/upload/discard">
    {_csrf_field(ctx.csrf)}
    <input type="hidden" name="token" value="{esc(token)}">
    <button class="go-btn small" type="submit">Throw it away</button>
  </form>
  <p class="note">Adding a game copies it onto this box and shows it on the games page.
  Nothing else on the card is touched, and a file the checks refuse changes nothing.</p>""",
                 ctx=ctx, tagline="One game you already have, checked before it is added.")


@router.post("/arcade/upload", response_class=HTMLResponse)
def arcade_upload(request: Request, file: UploadFile = File(...), csrf: str = Form("")):
    """Take one file an owner picked, and say what this box makes of it.

    The bytes go to the arcade's own temp folder under a name this box chose —
    never into ``games/`` — and the file only becomes a game when the owner
    confirms the words on the next screen.
    """
    if (blocked := _gate_tls(request)) is not None:
        return blocked
    with ark_db.read() as conn:
        ctx, blocked = _committed(request, conn, csrf)
        if blocked is not None:
            return blocked
        me = ctx.admin
        shown = str(file.filename or "").replace("\\", "/")
        shown = shown.rsplit("/", 1)[-1].strip()[:120]
        try:
            spooled = ark_arcade_install.spool_upload(file.file, filename=shown)
        except (ark_arcade_install.InstallRefused, OSError) as exc:
            with ark_db.tx() as conn:
                ark_db.audit(conn, action="arcade-upload", actor_id=me["id"],
                             actor_name=me["username"], target=shown or "a file",
                             result="failed", detail=str(exc)[:300])
            return shell("Games", _notice("bad", "<b>Nothing was added.</b> " + esc(str(exc))),
                         ctx=ctx, status=400)
        pre = ark_rommeta.prefill(spooled, filename=shown)
        if not pre.get("playable"):
            # A file whose kind this box cannot play is refused before the owner
            # is asked to fill in a form: the shelf could only ever be broken by it.
            with ark_db.tx() as conn:
                ark_db.audit(conn, action="arcade-upload", actor_id=me["id"],
                             actor_name=me["username"], target=shown or spooled.name,
                             result="failed", detail="not a kind of game this box can play")
            ark_arcade_install.discard_upload(spooled)
            return shell("Games", _notice(
                "bad", "<b>Nothing was added.</b> This box cannot play that kind of file"
                       " — it has no game from a console on this box inside it."),
                ctx=ctx, status=400)
        twin = ark_romdb.game_by_identity(conn, pre.get("ident") or {})
        with ark_db.tx() as conn:
            ark_db.audit(conn, action="arcade-upload", actor_id=me["id"],
                         actor_name=me["username"], target=shown or spooled.name,
                         result="ok", detail="looked at, not added yet")
    if twin:
        ark_arcade_install.discard_upload(spooled)
        return shell("Games", _notice(
            "bad", "<b>That game is already on the shelf</b> as "
                   f"“{esc(twin['title'])}”, so there is nothing to add."),
            ctx=ctx, tagline="One game you already have, checked before it is added.")
    filled = ark_rommeta.apply({}, pre)
    return _own_game_form(ctx, spooled.name, shown or spooled.name, pre, filled)


@router.post("/arcade/upload/add", response_class=HTMLResponse)
def arcade_upload_add(request: Request, token: str = Form(""), filename: str = Form(""),
                      title: str = Form(""), system: str = Form(""),
                      description: str = Form(""), author: str = Form(""),
                      release: str = Form(""), license_text: str = Form(""),
                      csrf: str = Form("")):
    """Publish the waiting file under the words the owner confirmed."""
    if (blocked := _gate_tls(request)) is not None:
        return blocked
    with ark_db.read() as conn:
        ctx, blocked = _committed(request, conn, csrf)
        if blocked is not None:
            return blocked
        me = ctx.admin
        spooled = ark_arcade_install.upload_path(token)
        if spooled is None:
            return shell("Games", _notice(
                "bad", "<b>Nothing was added.</b> That file is no longer waiting — "
                       "pick it again."), ctx=ctx, status=404)
        label = filename or spooled.name
        facts = {"slug": "", "title": title, "system": system,
                 "license_text": license_text, "release": release, "author": author,
                 "description": description, "filename": label}
        pre = ark_rommeta.prefill(spooled, filename=label)
        try:
            result = ark_arcade_install.import_owner_file(conn, spooled, **facts)
        except (ark_arcade_install.InstallRefused, OSError, sqlite3.DatabaseError) as exc:
            with ark_db.tx() as conn:
                ark_db.audit(conn, action="arcade-upload", actor_id=me["id"],
                             actor_name=me["username"], target=label,
                             result="failed", detail=str(exc)[:300])
            return _own_game_form(ctx, token, label, pre, ark_rommeta.apply(facts, pre),
                                  problem="That one was not added: " + str(exc))
        with ark_db.tx() as conn:
            ark_db.audit(conn, action="arcade-upload", actor_id=me["id"],
                         actor_name=me["username"], target=result.get("slug") or title,
                         result="ok",
                         detail=(f"added revision {result['revision']}"
                                 if result.get("installed")
                                 else result.get("reason", "already here")))
    ark_arcade_install.discard_upload(spooled)
    said = (f"{title or result.get('slug')} was checked and added."
            if result.get("installed")
            else f"{title or result.get('slug')} is already on the shelf.")
    return _redirect("/admin/arcade?" + urllib.parse.urlencode({"ok": said}))


@router.post("/arcade/upload/discard", response_class=HTMLResponse)
def arcade_upload_discard(request: Request, token: str = Form(""), csrf: str = Form("")):
    """Throw away a file the owner decided not to add. Nothing was ever published."""
    if (blocked := _gate_tls(request)) is not None:
        return blocked
    with ark_db.tx() as conn:
        ctx, blocked = _committed(request, conn, csrf)
        if blocked is not None:
            return blocked
        me = ctx.admin
        spooled = ark_arcade_install.upload_path(token)
        removed = ark_arcade_install.discard_upload(spooled)
        ark_db.audit(conn, action="arcade-upload", actor_id=me["id"],
                     actor_name=me["username"], target=(spooled.name if spooled else token[:40]),
                     result="ok" if removed else "failed",
                     detail="thrown away before it was added" if removed
                            else "that file was already gone")
    return _redirect("/admin/arcade?" + urllib.parse.urlencode(
        {"ok": "That file was thrown away. Nothing was added."}))


@router.post("/arcade/switches", response_class=HTMLResponse)
def arcade_switches(request: Request, players: str = Form(""), auto: str = Form(""),
                    public_add: str = Form(""), csrf: str = Form("")):
    if (blocked := _gate_tls(request)) is not None:
        return blocked
    with ark_db.tx() as conn:
        ctx, blocked = _committed(request, conn, csrf)
        if blocked is not None:
            return blocked
        me = ctx.admin
        ark_db.set_setting(conn, "arcade_players", "1" if players else "0")
        ark_db.set_setting(conn, "arcade_auto_install", "1" if auto else "0")
        ark_db.set_setting(conn, "public_arcade_upload", "1" if public_add else "0")
        ark_db.audit(conn, action="arcade-switches", actor_id=me["id"],
                     actor_name=me["username"], result="ok",
                     detail=f"player names={'on' if players else 'off'}, "
                            f"anyone may add a game={'on' if public_add else 'off'}, "
                            f"auto install={'on' if auto else 'off'}")
    return _redirect("/admin/arcade")


@router.post("/arcade/{game_id}/state", response_class=HTMLResponse)
def arcade_game_state(request: Request, game_id: int, action: str = Form(""),
                      csrf: str = Form("")):
    if (blocked := _gate_tls(request)) is not None:
        return blocked
    if action not in ("hide", "show"):
        return shell("Games", _notice("bad", "<b>Nothing was changed.</b> That is not an action."),
                     status=400)
    with ark_db.tx() as conn:
        ctx, blocked = _committed(request, conn, csrf)
        if blocked is not None:
            return blocked
        me = ctx.admin
        game = ark_arcade.game_by_id(conn, int(game_id))
        if game is None:
            return shell("Games", _notice("bad", "<b>Nothing was changed.</b> That game is not here."),
                         ctx=ctx, status=404)
        conn.execute("UPDATE games SET hidden = ?, updated_at = ? WHERE id = ?",
                     (1 if action == "hide" else 0, ark_arcade.now_iso(), int(game_id)))
        ark_db.audit(conn, action=f"game-{action}", actor_id=me["id"], actor_name=me["username"],
                     target=game["slug"], result="ok",
                     detail="taken off the games page" if action == "hide" else "shown again")
    return _redirect("/admin/arcade")


@router.post("/arcade/suggestions/{candidate_id}/install", response_class=HTMLResponse)
def arcade_suggestion_install(request: Request, candidate_id: int, csrf: str = Form("")):
    """Download and prove one suggestion, then publish it — never while holding a lock."""
    if (blocked := _gate_tls(request)) is not None:
        return blocked
    with ark_db.tx() as conn:
        ctx, blocked = _committed(request, conn, csrf)
        if blocked is not None:
            return blocked
        me = ctx.admin
        row = ark_arcade_install.candidate_row(conn, int(candidate_id))
        if row is None:
            return shell("Games", _notice("bad", "<b>Nothing was added.</b> That suggestion is gone."),
                         ctx=ctx, status=404)
        title = row["title"]
    try:
        with ark_db.read() as conn:
            result = ark_arcade_install.install_approved(conn, int(candidate_id))
    except (ark_arcade_install.InstallRefused, urllib.error.URLError,
            TimeoutError, OSError, sqlite3.DatabaseError) as exc:
        with ark_db.tx() as conn:
            ark_db.audit(conn, action="arcade-suggestion-install", actor_id=me["id"],
                         actor_name=me["username"], target=title, result="failed",
                         detail=str(exc)[:300])
        return _redirect("/admin/arcade?" + urllib.parse.urlencode(
            {"err": f"{title} was not added: {exc}"}))
    with ark_db.tx() as conn:
        detail = (f"installed {result['slug']} revision {result['revision']}"
                  if result.get("installed") else result.get("reason", "already here"))
        ark_db.audit(conn, action="arcade-suggestion-install", actor_id=me["id"],
                     actor_name=me["username"], target=title, result="ok",
                     detail=detail)
    return _redirect("/admin/arcade?" + urllib.parse.urlencode(
        {"ok": f"{title} was checked and added." if result.get("installed")
               else f"{title} is already on the shelf."}))


@router.post("/arcade/suggestions/{candidate_id}/state", response_class=HTMLResponse)
def arcade_suggestion_state(request: Request, candidate_id: int, action: str = Form(""),
                            csrf: str = Form("")):
    if (blocked := _gate_tls(request)) is not None:
        return blocked
    if action not in ("pending", "refused"):
        return shell("Games", _notice("bad", "<b>Nothing was changed.</b> That is not an action."),
                     status=400)
    with ark_db.tx() as conn:
        ctx, blocked = _committed(request, conn, csrf)
        if blocked is not None:
            return blocked
        me = ctx.admin
        row = ark_arcade_install.candidate_row(conn, int(candidate_id))
        if row is None:
            return shell("Games", _notice("bad", "<b>Nothing was changed.</b> That suggestion is gone."),
                         ctx=ctx, status=404)
        ok = ark_arcade_install.set_candidate_status(conn, int(candidate_id), action,
                                                      "put away by the owner" if action == "refused" else "")
        ark_db.audit(conn, action="arcade-suggestion-state", actor_id=me["id"],
                     actor_name=me["username"], target=row["title"], result="ok" if ok else "denied",
                     detail=f"now {action}" if ok else "already installed; nothing changed")
    return _redirect("/admin/arcade")


# ---------------------------------------------------------------------------
#  books and games in one go — and every address on the box
# ---------------------------------------------------------------------------
def _picked_name(raw: str) -> str:
    """The name a person sees for a file they picked: never a path, never empty."""
    shown = str(raw or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    shown = "".join(ch for ch in shown if ch.isprintable())
    return shown[:120] or "a file"


def _upload_rows(rows) -> str:
    """What happened to each picked file, in the order it was picked."""
    if not rows:
        return ""
    marks = {"ok": "✓", "wait": "…", "bad": "×"}
    body = "".join(
        f"""<tr>
          <td>{esc(r["name"])}<span class="sub">{esc(r["kind_word"])}</span></td>
          <td>{marks.get(r["state"], "×")} {esc(r["said"])}</td>
        </tr>""" for r in rows)
    return f"""<h2>What happened</h2>
  <table class="plain"><thead><tr><th>File</th><th>What the box did</th></tr></thead>
    <tbody>{body}</tbody></table>"""


def _upload_body(ctx, rows=None, flash: str = "") -> str:
    """The owner's one door for several files: pick them, press once, read the list."""
    kinds = ark_ingest.kinds()
    book_list = " ".join(kinds[ark_ingest.KIND_BOOK])
    game_list = " ".join(kinds[ark_ingest.KIND_GAME])
    film_list = " ".join(kinds[ark_ingest.KIND_MEDIA])
    return f"""
  {flash}
  <h2>Books, games and films, all at once</h2>
  <p class="note">Choose one file or twenty. The box decides what each one is by its
  kind, and puts it where it belongs: <b>a book</b> goes into the Book Library,
  <b>a game</b> goes onto the games page, <b>a film</b> goes onto the Media shelf.
  Nothing else on the card is touched.</p>
  <form method="post" action="/admin/upload" enctype="multipart/form-data">
    {_csrf_field(ctx.csrf)}
    <label for="files">The files</label>
    <input id="files" name="files" type="file" multiple required>
    <button class="go-btn" type="submit">Put these files on the box</button>
  </form>
  <p class="note">One press carries up to <b>{ark_ingest.MAX_FILES} files</b> and at most
  <b>{human_size(ark_ingest.MAX_BATCH)}</b> in total. A book may be up to
  {human_size(ark_ingest.MAX_BOOK)} and a game up to
  {human_size(ark_ingest.MAX_GAME)}; send the rest in a second go.</p>
  <h2>What this page reads</h2>
  <table class="plain"><thead><tr><th>Kind of file</th><th>What happens to it</th></tr></thead>
    <tbody>
      <tr><td><b>Books</b><span class="sub">{esc(book_list)}</span></td>
          <td>Added to the Book Library and readable in the browser. A book the
          library already holds is not added twice.</td></tr>
      <tr><td><b>Games</b><span class="sub">{esc(game_list)}</span></td>
          <td>Checked, then put on the games page. A file that is not a game the box
          can play is refused, and the shelf is left exactly as it was.</td></tr>
      <tr><td><b>Films</b><span class="sub">{esc(film_list)}</span></td>
          <td>Read properly by the box's video tools and put on the Media shelf, with a
          picture. A film that needs a playing copy is prepared in the background; the
          original is kept. Its own page has captions and a delete button.</td></tr>
    </tbody></table>
  <p class="hint">Anything else is refused by name: this page will not guess at a file.
  A game takes a few seconds to check; a batch of books is handed to the box's own book
  importer, so this page may say “still being added” and mean it — the Book Library page
  shows the answer.</p>
  {_upload_rows(rows)}"""



@router.get("/upload", response_class=HTMLResponse)
def upload_page(request: Request):
    """The form, and nothing else: no file is read until it is chosen."""
    if (blocked := _gate_tls(request)) is not None:
        return blocked
    with ark_db.read() as conn:
        ctx = _load_ctx(conn, request, ark_db.session_epoch(conn))
        if (blocked := _need_session(ctx, request)) is not None:
            return blocked
    return shell("Add files", _upload_body(ctx), ctx=ctx,
                 tagline="Several books or games at once, each put where it belongs.")


@router.post("/upload", response_class=HTMLResponse)
def upload_files(request: Request, files: Optional[List[UploadFile]] = File(None),
                 csrf: str = Form("")):
    """Take every file in one press and route each one to its own normal path.

    A route that reads a file must not be ``async def``: this is a plain function so
    Starlette runs it in its thread pool instead of stalling the event loop for as
    long as several files take to arrive.

    A **book** is written into the folder the book importer already watches — this
    page starts nothing and needs no root — and a **game** goes through the arcade's
    own ``spool_upload`` → ``import_owner_file`` gate.  Nothing here is a second
    write path, and a refusal changes nothing.
    """
    if (blocked := _gate_tls(request)) is not None:
        return blocked
    with ark_db.read() as conn:
        ctx, blocked = _committed(request, conn, csrf)
        if blocked is not None:
            return blocked
        me = ctx.admin

    picked = [item for item in (files or []) if item is not None]
    if not picked:
        return shell("Add files", _upload_body(ctx, flash=_notice(
            "bad", "<b>Nothing was added.</b> No file was chosen.")), ctx=ctx, status=400)
    if len(picked) > ark_ingest.MAX_FILES:
        return shell("Add files", _upload_body(ctx, flash=_notice(
            "bad", f"<b>Nothing was added.</b> That is {len(picked)} files, and one "
                   f"press carries {ark_ingest.MAX_FILES}. Send them in two goes.")),
            ctx=ctx, status=400)

    rows: List[dict] = []
    waiting: List[tuple] = []          # (row, Book) — the importer has not answered yet
    total = 0
    for item in picked:
        shown = _picked_name(item.filename)
        kind = ark_ingest.classify(shown)
        if not kind:
            rows.append({"name": shown, "kind_word": "not a kind this page reads",
                         "state": "bad",
                         "said": "the box does not know what to do with that kind of file"})
            continue
        is_book = kind == ark_ingest.KIND_BOOK
        is_media = kind == ark_ingest.KIND_MEDIA
        kind_word = "book" if is_book else ("film" if is_media else "game")
        room = ark_ingest.MAX_BATCH - total
        if not is_media and room <= 0:
            rows.append({"name": shown, "kind_word": kind_word, "state": "bad",
                         "said": "this press is already as large as one upload may be"})
            continue
        try:
            if is_media:
                # A film does not share the batch ceiling: it has its own, larger
                # cap and is bounded by the card's reserve instead.  It goes on the
                # shelf at once because this page is the owner's own door.
                film = ark_media.accept(item.file.read, shown,
                                        state=ark_media.STATE_PUBLISHED,
                                        added_by=str(me["username"]),
                                        limit=ark_ingest.MAX_MEDIA)
                rows.append({"name": shown, "kind_word": kind_word, "state": "ok",
                             "said": "on the Media shelf now"})
                continue
            if is_book:
                book = ark_ingest.spool_book(item.file.read, shown,
                                             limit=min(ark_ingest.MAX_BOOK, room))
                total += book.size
                if ark_ingest.imported(book.sha256):
                    ark_ingest.discard(book.path)
                    rows.append({"name": shown, "kind_word": kind_word, "state": "ok",
                                 "said": "already in the Book Library — not added twice"})
                else:
                    row = {"name": shown, "kind_word": kind_word, "state": "wait",
                           "said": "handed to the Book Library"}
                    rows.append(row)
                    waiting.append((row, book))
                continue
            spooled = ark_arcade_install.spool_upload(
                item.file, filename=shown, limit=min(ark_ingest.MAX_GAME, room))
            total += spooled.stat().st_size
            pre = ark_rommeta.prefill(spooled, filename=shown)
            if not pre.get("playable"):
                ark_arcade_install.discard_upload(spooled)
                rows.append({"name": shown, "kind_word": kind_word, "state": "bad",
                             "said": "the box could not tell which console that file is "
                                     "for, so it did not guess — a game whose console it "
                                     "cannot name is added from a console with "
                                     "ark-arcade --import, where the console is named"})
                continue
            filled = ark_rommeta.apply({}, pre)
            facts = {key: filled.get(key, "") for key in
                     ("title", "system", "author", "release", "description")}
            facts["filename"] = shown
            try:
                with ark_db.tx() as conn:
                    result = ark_arcade_install.import_owner_file(conn, spooled, **facts)
            finally:
                ark_arcade_install.discard_upload(spooled)
            title = filled.get("title") or result.get("slug") or shown
            if result.get("installed"):
                revision = int(result.get("revision") or 1)
                said = (f"on the games page now as “{title}”" if revision <= 1 else
                        f"replaced the copy already on the shelf as “{title}”, "
                        f"now revision {revision}")
            else:
                said = result.get("reason") or "already on the shelf"
            rows.append({"name": shown, "kind_word": kind_word, "state": "ok",
                         "said": said})
        except ark_storage.Refused as exc:
            rows.append({"name": shown, "kind_word": kind_word, "state": "bad",
                         "said": str(exc)})
        except (ark_arcade_install.InstallRefused, OSError,
                sqlite3.DatabaseError) as exc:
            rows.append({"name": shown, "kind_word": kind_word, "state": "bad",
                         "said": str(exc) or "the box could not add that one"})

    if waiting:
        found = ark_ingest.wait_for_library([book.sha256 for _row, book in waiting])
        for row, book in waiting:
            if book.sha256 in found:
                row["state"] = "ok"
                row["said"] = "in the Book Library now"
            else:
                row["state"] = "wait"
                row["said"] = ("the box has it and is still adding it — open Books in a "
                               "moment and it will be there")

    with ark_db.tx() as conn:
        for row in rows:
            ark_db.audit(conn, action="upload", actor_id=me["id"],
                         actor_name=me["username"], target=row["name"],
                         result="failed" if row["state"] == "bad" else "ok",
                         detail=f"{row['kind_word']}: {row['said']}")

    done = sum(1 for r in rows if r["state"] == "ok")
    bad = sum(1 for r in rows if r["state"] == "bad")
    late = sum(1 for r in rows if r["state"] == "wait")
    pieces = [f"<b>{done + late} of {len(rows)} file{'' if len(rows) == 1 else 's'} "
              "went on the box.</b>"]
    if late:
        pieces.append(f"{late} of those is still being added — open Books in a moment.")
    if bad:
        pieces.append(f"{bad} refused — the reason is beside each one.")
    kind = "good" if done and not bad else ("bad" if bad else "")
    # 400 only when nothing at all was taken on: a book still being added was
    # accepted, and calling that a client error would be a lie in a probe's mouth.
    return shell("Add files", _upload_body(ctx, rows=rows, flash=_notice(kind, " ".join(pieces))),
                 ctx=ctx, status=200 if (done or late) else 400,
                 tagline="Several books or games at once, each put where it belongs.")




def _door_link(address: str, host: str) -> str:
    """The address as something a person can tap, or as words when it cannot be.

    Every door that answers on the name this person is using is a link: the point
    of this page is that nobody has to type a port number again.  The setup door is
    printed in full but never linked — it answers on the box itself and nowhere
    else, so tapping it from a phone would try the phone.
    """
    shown = address.replace("{host}", host)
    if "{host}" not in address or not shown.startswith(("http://", "https://")):
        return esc(shown)
    return f'<a href="{esc(shown)}">{esc(shown)}</a>'


@router.get("/doors", response_class=HTMLResponse)
def doors_page(request: Request):
    """Every address on this box, who may open it, and what it is for.

    Generated from ``ark_doors.DOORS`` — one list the tests hold to the application,
    so a page added to this area and not named here fails a test instead of becoming
    a door nobody can find.
    """
    if (blocked := _gate_tls(request)) is not None:
        return blocked
    with ark_db.read() as conn:
        ctx = _load_ctx(conn, request, ark_db.session_epoch(conn))
        if (blocked := _need_session(ctx, request)) is not None:
            return blocked
    host = ctx.host or "ark.local"
    parts = []
    for title, rows in ark_doors.groups():
        body = "".join(
            f"""<tr>
              <td><b>{esc(door["name"])}</b><span class="sub">{_door_link(door["address"], host)}</span></td>
              <td>{esc(door["who"])}</td>
              <td>{esc(door["what"].replace("{host}", host))}</td>
            </tr>""" for door in rows)
        parts.append(f"""<h2>{esc(title)}</h2>
  <table class="plain"><thead><tr><th>Address</th><th>Who</th><th>What it is for</th></tr></thead>
    <tbody>{body}</tbody></table>""")
    intro = f"""<p class="note">The box answers to more than one name. On its own Wi-Fi it
  answers every name, so <b>ark.lan</b> always works there; on a home network use
  <b>ark.local</b>, which the box answers wherever it is. The addresses below are
  written for the name you are using now (<b>{esc(host)}</b>) — substitute the other
  one and they work just the same.</p>
  <p class="hint">The private pages are on the secure address, because a password must
  never travel unencrypted. The box's certificate is its own, so your browser will ask
  once to trust it.</p>"""
    return shell("Addresses", intro + "".join(parts), ctx=ctx,
                 tagline="Every address on this box, who may open it, and what it is for.")


# ---------------------------------------------------------------------------
#  MEDIA — the films on this box
#
#  A film is the one thing here that can be huge, slow to prepare and impossible
#  to play, so this page is built around three honest sentences: what the file
#  really is, what the box had to do to make it playable, and how much room is
#  left before the box refuses more.  Nothing on this page silently cleans up:
#  removing a film is a choice with a name on it in the history.
# ---------------------------------------------------------------------------
MEDIA_SORTS = (("recent", "Newest first"), ("title", "Title A–Z"),
               ("largest", "Largest first"), ("longest", "Longest first"))
#: The page opens on *everything*: a film waiting for a look is the one the owner
#: most needs to see, and a filter that hides it by default is how it gets missed.
MEDIA_STATES = (("", "Everything"), ("published", "On the shelf"),
                ("pending", "Waiting for a look"), ("hidden", "Hidden"))
#: How many films one screen shows.  A shelf of a thousand films is paged, not
#: downloaded: each card is one picture and two lines of text.
MEDIA_PER_PAGE = 20


def _media_plan_words(row) -> str:
    return ark_media.plan_words(str(row["plan"] or ""))


def _media_job_words(row) -> str:
    keys = row.keys()
    job = str(row["job_state"] or "") if "job_state" in keys else ""
    detail = str(row["job_detail"] or "") if "job_detail" in keys else ""
    if job == "queued":
        return "Waiting to be prepared."
    if job == "running":
        try:
            percent = int(float(row["job_progress"] or 0))
        except (TypeError, ValueError, KeyError):
            percent = 0
        return (f"Being prepared now — about {percent}% done." if percent > 0
                else "Being prepared now.")
    if job == "failed":
        return "Could not be prepared" + (f": {detail}" if detail else ".")
    return ""


def _media_row(row, csrf: str) -> str:
    """One film on the owner's page: what it is, and one button per decision."""
    item_id = int(row["id"])
    present = ark_media.original_path(row).is_file()
    status = ark_media.status_of(row, present)
    title = row["title"] or ark_media.title_from_filename(str(row["filename"] or ""))
    state = str(row["state"] or "")
    picture = (f'<img class="thumb" src="/admin/media/{item_id}/poster" alt=""'
               ' loading="lazy" width="160" height="90">' if present else
               '<span class="thumb none" aria-hidden="true">no file</span>')
    facts = [ark_media.human_duration(row["duration"]), ark_media.human_size(row["size"])]
    if row["play_size"]:
        facts.append("playing copy " + ark_media.human_size(row["play_size"]))
    # What the *player* would use, not what the original needed: once a playing
    # copy exists, "needs a quick repack" beside "ready to play" reads like a
    # contradiction, and the owner would be right.
    facts.append("playing copy ready" if row["play_stored"] else _media_plan_words(row))
    if row["category"]:
        facts.append(str(row["category"]))
    words = " · ".join(facts)
    job_words = _media_job_words(row)
    buttons = []
    if state == ark_media.STATE_PUBLISHED:
        buttons.append(_media_button(item_id, "state", "hidden", csrf, "Take off the shelf"))
    else:
        buttons.append(_media_button(item_id, "state", "published", csrf, "Show on the shelf"))
    if present:
        needs_copy = (str(row["plan"]) in (ark_media.REMUX, ark_media.CONVERT)
                      and not str(row["play_stored"] or ""))
        buttons.append(_media_button(
            item_id, "prepare", "", csrf,
            "Make a playing copy" if needs_copy else "Prepare again"))
        buttons.append(_media_button(item_id, "picture", "", csrf, "New picture"))
    if str(row["job_state"] or "") in ("queued", "running"):
        buttons.append(_media_button(item_id, "stop", "", csrf, "Stop preparing"))
    buttons.append(_media_delete_form(item_id, csrf, title))
    watch = (f'<a class="smallbtn" href="/media/{item_id}">Watch</a>'
             if state == ark_media.STATE_PUBLISHED and ark_media.playable(row) else "")
    more = f"""
      <details class="more">
        <summary>More for this film</summary>
        <form method="post" action="/admin/media/{item_id}/title">
          {_csrf_field(csrf)}
          <label for="title-{item_id}">The name on the shelf (the file keeps its own)</label>
          <input id="title-{item_id}" name="title" type="text" maxlength="160"
                 value="{esc(title)}">
          <button class="smallbtn" type="submit">Save the name</button>
        </form>
        <form method="post" action="/admin/media/{item_id}/picture">
          {_csrf_field(csrf)}
          <label for="at-{item_id}">Take the picture at (seconds into the film — blank lets the box choose)</label>
          <input id="at-{item_id}" name="at" type="text" inputmode="decimal" placeholder="65" value="">
          <button class="smallbtn" type="submit">Take a picture there</button>
        </form>
        <form method="post" action="/admin/media/{item_id}/captions" enctype="multipart/form-data">
          {_csrf_field(csrf)}
          <label for="sub-{item_id}">Add captions (.vtt or .srt)</label>
          <input id="sub-{item_id}" name="file" type="file" accept=".vtt,.srt">
          <button class="smallbtn" type="submit">Add these captions</button>
        </form>
      </details>"""
    return f"""
  <li class="filmrow">
    <label class="tick" title="Tick to delete several at once">
      <input type="checkbox" name="ids" value="{item_id}" form="bulk-delete">
      <span class="sr-only">Choose {esc(title)}</span>
    </label>
    <div class="filmrow-main">
      {picture}
      <div>
        <b>{esc(title)}</b>
        <span class="sub">{esc(words)}</span>
        <span class="sub">{esc(ark_media.status_words(status))}{(' — ' + esc(job_words)) if job_words else ''}</span>
        <span class="sub">File: {esc(row['filename'])}</span>
        <span class="sub">Picture {int(row['width'])}×{int(row['height'])} · {esc(row['vcodec'] or 'unknown')} / {esc(row['acodec'] or 'no sound')}</span>
      </div>
    </div>
    <div class="filmrow-actions">{watch}{''.join(buttons)}</div>
    <div class="filmrow-more">{more}</div>
  </li>"""


def _media_delete_form(item_id: int, csrf: str, title: str) -> str:
    """Deleting one film asks for the same certainty the bulk form does.

    The button used to POST without `confirm=yes`, so the route refused every
    time and the only working delete was the bulk one — a control that can never
    succeed is worse than no control.
    """
    return f"""
      <details class="danger-zone">
        <summary>Delete…</summary>
        <form method="post" action="/admin/media/{item_id}/delete">
          {_csrf_field(csrf)}
          <label class="tick"><input type="checkbox" name="confirm" value="yes" required>
            I am sure — delete “{esc(title)}” for ever</label>
          <button class="smallbtn danger" type="submit">Delete for ever</button>
        </form>
      </details>"""


def _media_button(item_id: int, action: str, value: str, csrf: str, label: str,
                  danger: bool = False) -> str:
    return f"""
      <form method="post" action="/admin/media/{item_id}/{action}">
        {_csrf_field(csrf)}
        <input type="hidden" name="value" value="{esc(value)}">
        <button class="smallbtn{' danger' if danger else ''}" type="submit">{esc(label)}</button>
      </form>"""


def _media_storage_block(room: dict) -> str:
    """The card's arithmetic, in the owner's words — never a quota for one person."""
    if room["free_bytes"] is None:
        return _notice("bad", "<b>Space on the card could not be measured.</b> "
                              "Nothing can be promised until it can.")
    rows = [
        ("The card holds", ark_media.human_size(room["total_bytes"])),
        ("Free right now", ark_media.human_size(room["free_bytes"])),
        ("Kept back for the box", ark_media.human_size(room["reserve_bytes"])),
        ("Promised to jobs running now", ark_media.human_size(room["committed_bytes"])),
        ("May still be added", ark_media.human_size(room["usable_bytes"])),
    ]
    body = "".join(f"<tr><th>{esc(k)}</th><td>{esc(v)}</td></tr>" for k, v in rows)
    breach = ""
    if room.get("breached"):
        breach = _notice("bad", "<b>The card is already inside the space this box keeps "
                                "back.</b> New films and files are refused until room is "
                                "made. Watching, deleting and the owner's pages still work.")
    return f"""{breach}
  <table class="plain"><tbody>{body}</tbody></table>
  <p class="note">The box keeps <b>{esc(str(room['percent']))}%</b> of the card, or 2 GB if
  that is more, so the portal keeps working when the card is nearly full. This is the
  box's own limit, <b>not a quota for one person</b> — and it cannot stop something
  outside the box from filling the card.</p>"""


def _media_upload_form(ctx, flash: str = "") -> str:
    return f"""
  {flash}
  <h2>Put films on the shelf</h2>
  <p class="note">Choose films — several at once is fine. Each one is looked at properly
  before the shelf promises it will play: the box reads the picture and sound inside,
  not just the name. A film you add here appears straight away; a film somebody shares
  waits for you to look at it.</p>
  <form method="post" action="/admin/media/upload" enctype="multipart/form-data">
    {_csrf_field(ctx.csrf)}
    <label for="films">The films</label>
    <input id="films" name="files" type="file" multiple required
           accept="video/*,.mkv,.avi,.ts,.m2ts,.wmv,.flv,.ogv,.m4v,.3gp">
    <label for="caption_files">Captions (optional — a .vtt or .srt with the same name)</label>
    <input id="caption_files" name="captions" type="file" multiple accept=".vtt,.srt">
    <label for="category">A shelf name (optional)</label>
    <input id="category" name="category" type="text" maxlength="40" placeholder="Repair films">
    <button class="go-btn" type="submit">Put these films on the shelf</button>
  </form>
  <p class="note">Up to <b>{ark_media.human_size(ark_media.MAX_MEDIA)}</b> per film, and never
  more than the card can spare. A film that needs converting is queued and done in the
  background, one at a time, so the box keeps answering.</p>"""


def _media_body(ctx, *, result=None, values=None, flash: str = "", room=None,
                largest=None, uploads=None) -> str:
    values = values or {"q": "", "state": "", "sort": "recent"}
    rows = list(result["rows"]) if result else []
    filters = []
    for key, label in MEDIA_STATES:
        href = "/admin/media?" + urllib.parse.urlencode(
            {k: v for k, v in (("q", values["q"]), ("sort", values["sort"]), ("state", key))
             if v})
        on = ' aria-current="page"' if values["state"] == key else ""
        filters.append(f'<a class="chip" href="{esc(href)}"{on}>{esc(label)}</a>')
    sorts = "".join(
        f'<option value="{esc(key)}"' + (" selected" if values["sort"] == key else "")
        + f'>{esc(label)}</option>' for key, label in MEDIA_SORTS)
    listing = ""
    if rows:
        listing = ('<ul class="clean films-admin">'
                   + "".join(_media_row(row, ctx.csrf) for row in rows) + "</ul>")
        listing += f"""
  <form id="bulk-delete" method="post" action="/admin/media/delete" class="bulk">
    {_csrf_field(ctx.csrf)}
    <label class="tick"><input type="checkbox" name="confirm" value="yes" required>
      I am sure — delete the ticked films for ever</label>
    <button class="smallbtn danger" type="submit">Delete the ticked films</button>
    <p class="note">Deleting removes the film's playing copy, its picture and its captions,
    and the original file as well unless the File Box or another film still uses it.
    A film somebody is watching right now frees its space when they close it.</p>
  </form>"""
    elif result is not None:
        listing = ('<h2>Nothing here</h2><p class="hint">No film matches that. '
                   'Try “Everything”, or a shorter word.</p>')
    pager = ""
    if result and result["pages"] > 1:
        bits = []
        for number in range(1, result["pages"] + 1):
            if number in (1, result["pages"]) or abs(number - result["page"]) <= 2:
                href = "/admin/media?" + urllib.parse.urlencode(
                    {k: v for k, v in (("q", values["q"]), ("sort", values["sort"]),
                                       ("state", values["state"]), ("page", number)) if v})
                on = ' aria-current="page"' if number == result["page"] else ""
                bits.append(f'<a class="chip" href="{esc(href)}"{on}>{number}</a>')
            elif bits and bits[-1] != "…":
                bits.append("…")
        pager = '<nav class="pager" aria-label="Pages">' + "".join(bits) + "</nav>"

    biggest = ""
    if largest:
        items = "".join(
            f'<li>{esc(r["title"])}<span class="sub">'
            f'{esc(ark_media.human_size(r["size"]))} · {esc(ark_media.human_duration(r["duration"]))}'
            f' · {esc(str(r["state"]))}</span></li>' for r in largest)
        biggest = (f'<h2>Largest films</h2><ul class="clean">{items}</ul>'
                   '<p class="hint">Delete one of these to reclaim the most room in one go.</p>')

    upload_block = uploads or ""
    body = f"""
  {upload_block}
  <h2>The shelf right now</h2>
  {_media_storage_block(room or ark_space.account(ark_media.media_root()))}
  <h2>Films</h2>
  <form class="searchbar" method="get" action="/admin/media">
    <label for="q">Find a film</label>
    <input id="q" name="q" type="search" value="{esc(values['q'])}" placeholder="Title or file name">
    <input type="hidden" name="state" value="{esc(values['state'])}">
    <label for="sort">Order</label>
    <select id="sort" name="sort">{sorts}</select>
    <button class="go-btn" type="submit">Show these</button>
  </form>
  <nav class="chips" aria-label="Which films">{''.join(filters)}</nav>
  <p class="hint">{result['total'] if result else 0} film{'s' if (result or {}).get('total') != 1 else ''} in this view.</p>
  {listing}
  {pager}
  {biggest}
  <h2>What this box can play</h2>
  <table class="plain"><thead><tr><th>What the file is</th><th>What happens</th></tr></thead>
    <tbody>
      <tr><td><b>Plays as it is</b></td><td>The browser opens the original file. Nothing is copied.</td></tr>
      <tr><td><b>Needs a quick repack</b></td><td>The same picture and sound are put in a
        box a phone opens. It costs seconds and no quality.</td></tr>
      <tr><td><b>Needs converting</b></td><td>The picture has to be rebuilt in a shape a
        browser can decode. The original is kept, and the copy is made in the background,
        one film at a time. A long film can take hours on this small computer.</td></tr>
    </tbody></table>
  <p class="hint">A film is never published just because it is a film. Films from the
  File Box wait here until you show them; films you add are on the shelf at once.</p>"""
    return body


def _media_values(request: Request) -> dict:
    q = (request.query_params.get("q") or "").strip()[:120]
    state = request.query_params.get("state", "")
    if state not in dict(MEDIA_STATES):
        state = ""
    sort = request.query_params.get("sort", "recent")
    if sort not in dict(MEDIA_SORTS):
        sort = "recent"
    try:
        page = max(1, int(request.query_params.get("page", "1")))
    except ValueError:
        page = 1
    return {"q": q, "state": state, "sort": sort, "page": page}


def _media_flash(request: Request) -> str:
    if request.query_params.get("ok"):
        return _notice("good", esc(request.query_params["ok"][:300]))
    if request.query_params.get("err"):
        return _notice("bad", esc(request.query_params["err"][:300]))
    return ""


@router.get("/media", response_class=HTMLResponse)
def media_page(request: Request):
    """The owner's shelf: what is on it, what it is, and how much room is left."""
    if (blocked := _gate_tls(request)) is not None:
        return blocked
    values = _media_values(request)
    with ark_db.read() as conn:
        ctx = _load_ctx(conn, request, ark_db.session_epoch(conn))
        if (blocked := _need_session(ctx, request)) is not None:
            return blocked
        result = ark_media.listing(conn, q=values["q"], state=values["state"],
                                   sort=values["sort"], page=values["page"],
                                   per_page=MEDIA_PER_PAGE)
        room = ark_space.account(ark_media.media_root(), conn=conn)
        largest = ark_media.largest(conn, 8)
        shared = _media_shared_files(conn)
    body = _media_body(ctx, result=result, values=values, flash=_media_flash(request),
                       room=room, largest=largest, uploads=_media_upload_form(ctx))
    body += _media_shared_block(ctx, shared)
    return shell("Media", body, ctx=ctx,
                 tagline="Films on this box — what they are, and what they need.")


def _media_shared_files(conn) -> list:
    """Videos already in the File Box that are not on the shelf yet.

    Adopting one adds no bytes: the shelf row points at the file the File Box
    already holds.  It is offered because a person who shared a film before this
    page existed should not have to upload it twice.
    """
    rows = conn.execute(
        "SELECT * FROM files WHERE hidden = 0 ORDER BY id DESC LIMIT 200").fetchall()
    known = {str(r[0]) for r in conn.execute(
        "SELECT DISTINCT stored FROM media").fetchall()}
    found = []
    for row in rows:
        stored = str(row["stored"] or row["name"] or "")
        if stored in known:
            continue
        if ark_media.is_video_name(str(row["display"] or row["name"] or "")):
            found.append(row)
    return found


def _media_shared_block(ctx, rows) -> str:
    if not rows:
        return ""
    items = "".join(
        f"""<li>{esc(r['display'] or r['name'])}
          <span class="sub">{esc(ark_media.human_size(r['size']))} · already in the File Box</span>
          <form method="post" action="/admin/media/adopt">
            {_csrf_field(ctx.csrf)}
            <input type="hidden" name="file_id" value="{int(r['id'])}">
            <button class="smallbtn" type="submit">Put it on the shelf too</button>
          </form></li>""" for r in rows[:20])
    return f"""<h2>Films already in the File Box</h2>
  <p class="hint">Putting one on the shelf does not copy it and does not change who can
  download it — it adds the shelf's picture, player and details to the file that is
  already there.</p>
  <ul class="clean">{items}</ul>"""


def _media_check(ctx, conn, csrf: str, request: Request):
    return _committed(request, conn, csrf)


@router.post("/media/upload", response_class=HTMLResponse)
def media_upload(request: Request, files: Optional[List[UploadFile]] = File(None),
                 captions: Optional[List[UploadFile]] = File(None),
                 category: str = Form(""), csrf: str = Form("")):
    """Films from the owner: trusted, so they are on the shelf as soon as they pass the probe."""
    if (blocked := _gate_tls(request)) is not None:
        return blocked
    with ark_db.read() as conn:
        ctx, blocked = _media_check(ctx=None, conn=conn, csrf=csrf, request=request)
        if blocked is not None:
            return blocked
        me = ctx.admin
    picked = [item for item in (files or []) if item is not None and item.filename]
    if not picked:
        return _redirect("/admin/media?err=" + urllib.parse.quote(
            "No film was chosen, so nothing was added."))
    added: List[dict] = []
    refused: List[dict] = []
    for item in picked:
        shown = _picked_name(item.filename)
        if not ark_media.is_video_name(shown):
            refused.append({"name": shown, "said": "that is not a kind of film this box reads"})
            continue
        try:
            result = ark_media.accept(item.file.read, shown, state=ark_media.STATE_PUBLISHED,
                                      added_by=str(me["username"]),
                                      category=(category or "").strip()[:40],
                                      limit=ark_media.MAX_MEDIA)
            added.append({"name": shown, "id": int(result["id"]),
                          "stem": Path(shown).stem.lower()})
        except ark_storage.Refused as exc:
            refused.append({"name": shown, "said": str(exc)})
        except (sqlite3.DatabaseError, OSError) as exc:
            refused.append({"name": shown, "said": str(exc) or "the box could not add that one"})
        finally:
            item.file.close()

    kept = 0
    for caption in (captions or []):
        if caption is None or not caption.filename:
            continue
        shown = _picked_name(caption.filename)
        stem = Path(shown).stem.lower()
        # A caption belongs to a film only when the names match.  Guessing is how
        # the wrong subtitles end up on the wrong film.
        match = [one for one in added if one["stem"] == stem]
        payload = caption.file.read(2 * 1024 * 1024 + 1)
        caption.file.close()
        if len(match) != 1:
            refused.append({"name": shown, "said": "no film in this upload has that name"})
            continue
        try:
            with ark_db.tx() as conn:
                ark_media.add_captions(conn, match[0]["id"], shown, payload,
                                       source=str(me["username"]))
            kept += 1
        except (ark_storage.Refused, sqlite3.DatabaseError, OSError) as exc:
            refused.append({"name": shown, "said": str(exc)})

    with ark_db.tx() as conn:
        for one in added:
            ark_db.audit(conn, action="media-add", actor_id=me["id"],
                         actor_name=me["username"], target=one["name"], result="ok",
                         detail=f"film {one['id']}")
        for one in refused:
            ark_db.audit(conn, action="media-add", actor_id=me["id"],
                         actor_name=me["username"], target=one["name"], result="failed",
                         detail=one["said"][:200])
    pieces = []
    if added:
        pieces.append(f"{len(added)} film{'' if len(added) == 1 else 's'} on the shelf")
    if kept:
        pieces.append(f"{kept} caption file{'' if kept == 1 else 's'} added")
    if refused:
        pieces.append("refused: " + "; ".join(f"{one['name']} — {one['said']}"
                                              for one in refused[:4]))
    if not pieces:
        return _redirect("/admin/media?err=" + urllib.parse.quote("Nothing was added."))
    word = "ok=" if added or kept else "err="
    return _redirect("/admin/media?" + word + urllib.parse.quote(". ".join(pieces)))


@router.post("/media/adopt", response_class=HTMLResponse)
def media_adopt(request: Request, file_id: int = Form(0), csrf: str = Form("")):
    """Put a File Box video on the shelf without copying a byte."""
    if (blocked := _gate_tls(request)) is not None:
        return blocked
    with ark_db.read() as conn:
        ctx, blocked = _media_check(ctx=None, conn=conn, csrf=csrf, request=request)
        if blocked is not None:
            return blocked
        row = conn.execute("SELECT * FROM files WHERE id = ? AND hidden = 0",
                           (int(file_id),)).fetchone()
    if row is None:
        return _redirect("/admin/media?err=" + urllib.parse.quote(
            "That file is not in the File Box any more."))
    # ffprobe is a subprocess: it must never run while the database's one write
    # lock is held, or every other writer on the box waits for it.
    facts = ark_media.probe(ark_storage.drop_dir() / str(row["stored"] or row["name"]))
    with ark_db.tx() as conn:
        ctx, blocked = _media_check(ctx=None, conn=conn, csrf=csrf, request=request)
        if blocked is not None:
            return blocked
        try:
            media_id = ark_media.adopt(conn, row, added_by=str(ctx.admin["username"]),
                                       facts=facts)
        except ark_storage.Refused as exc:
            return _redirect("/admin/media?err=" + urllib.parse.quote(str(exc)))
        ark_db.audit(conn, action="media-adopt", actor_id=ctx.admin["id"],
                     actor_name=ctx.admin["username"], target=str(row["display"]),
                     result="ok", detail=f"film {media_id}")
    return _redirect("/admin/media?ok=" + urllib.parse.quote(
        "Added to the shelf; the file itself was not copied."))


@router.post("/media/{item_id}/state", response_class=HTMLResponse)
def media_state(request: Request, item_id: int, value: str = Form(""),
                csrf: str = Form("")):
    if (blocked := _gate_tls(request)) is not None:
        return blocked
    want = value if value in (ark_media.STATE_PUBLISHED, ark_media.STATE_HIDDEN) else ""
    if not want:
        return _redirect("/admin/media?err=" + urllib.parse.quote("That is not a choice."))
    with ark_db.tx() as conn:
        ctx, blocked = _media_check(ctx=None, conn=conn, csrf=csrf, request=request)
        if blocked is not None:
            return blocked
        if ark_media.item(conn, item_id) is None:
            return _redirect("/admin/media?err=" + urllib.parse.quote("That film is gone."))
        conn.execute("UPDATE media SET state = ? WHERE id = ?", (want, int(item_id)))
        ark_db.audit(conn, action="media-show" if want == ark_media.STATE_PUBLISHED
                     else "media-hide", actor_id=ctx.admin["id"],
                     actor_name=ctx.admin["username"], target=str(item_id), result="ok")
    said = ("On the shelf now." if want == ark_media.STATE_PUBLISHED
            else "Taken off the shelf. The file is still on the card.")
    return _redirect("/admin/media?ok=" + urllib.parse.quote(said))


@router.post("/media/{item_id}/prepare", response_class=HTMLResponse)
def media_prepare(request: Request, item_id: int, csrf: str = Form("")):
    """Look at the file again and do whatever it still needs."""
    if (blocked := _gate_tls(request)) is not None:
        return blocked
    with ark_db.tx() as conn:
        ctx, blocked = _media_check(ctx=None, conn=conn, csrf=csrf, request=request)
        if blocked is not None:
            return blocked
        if ark_media.item(conn, item_id) is None:
            return _redirect("/admin/media?err=" + urllib.parse.quote("That film is gone."))
        ark_media.enqueue(conn, item_id, "prepare")
        ark_db.audit(conn, action="media-prepare", actor_id=ctx.admin["id"],
                     actor_name=ctx.admin["username"], target=str(item_id), result="ok")
    return _redirect("/admin/media?ok=" + urllib.parse.quote(
        "Queued. The box prepares one film at a time."))


@router.post("/media/{item_id}/picture", response_class=HTMLResponse)
def media_picture(request: Request, item_id: int, at: str = Form(""),
                  csrf: str = Form("")):
    """Take a new picture — at a time the owner typed, when they typed one."""
    if (blocked := _gate_tls(request)) is not None:
        return blocked
    stamp = str(at or "").strip()
    if stamp:
        try:
            seconds = max(0.0, min(24 * 3600.0, float(stamp)))
        except ValueError:
            return _redirect("/admin/media?err=" + urllib.parse.quote(
                "A time is a number of seconds, like 65 or 12.5."))
    with ark_db.tx() as conn:
        ctx, blocked = _media_check(ctx=None, conn=conn, csrf=csrf, request=request)
        if blocked is not None:
            return blocked
        if ark_media.item(conn, item_id) is None:
            return _redirect("/admin/media?err=" + urllib.parse.quote("That film is gone."))
        if stamp:
            job = conn.execute(
                "INSERT INTO media_jobs (media_id, kind, state, ts, detail)"
                " VALUES (?, 'thumbnail', 'queued', ?, ?)",
                (int(item_id), ark_media.now_iso(), f"at={seconds}")).lastrowid
        else:
            ark_media.enqueue(conn, item_id, "thumbnail")
        ark_db.audit(conn, action="media-picture", actor_id=ctx.admin["id"],
                     actor_name=ctx.admin["username"], target=str(item_id), result="ok",
                     detail=f"at {seconds}" if stamp else "chosen by the box")
    return _redirect("/admin/media?ok=" + urllib.parse.quote("A new picture is queued."))


@router.post("/media/{item_id}/stop", response_class=HTMLResponse)
def media_stop(request: Request, item_id: int, csrf: str = Form("")):
    if (blocked := _gate_tls(request)) is not None:
        return blocked
    with ark_db.tx() as conn:
        ctx, blocked = _media_check(ctx=None, conn=conn, csrf=csrf, request=request)
        if blocked is not None:
            return blocked
        stopped = ark_media.cancel(conn, item_id)
        ark_db.audit(conn, action="media-stop", actor_id=ctx.admin["id"],
                     actor_name=ctx.admin["username"], target=str(item_id), result="ok",
                     detail=f"{stopped} job(s)")
    return _redirect("/admin/media?ok=" + urllib.parse.quote(
        "Stopped. The original is untouched; a half-made copy was thrown away."))


@router.post("/media/{item_id}/title", response_class=HTMLResponse)
def media_title(request: Request, item_id: int, title: str = Form(""),
                csrf: str = Form("")):
    """A name of the owner's choosing; the original file name is never lost."""
    if (blocked := _gate_tls(request)) is not None:
        return blocked
    clean = " ".join(str(title or "").split())[:160]
    with ark_db.tx() as conn:
        ctx, blocked = _media_check(ctx=None, conn=conn, csrf=csrf, request=request)
        if blocked is not None:
            return blocked
        row = ark_media.item(conn, item_id)
        if row is None:
            return _redirect("/admin/media?err=" + urllib.parse.quote("That film is gone."))
        conn.execute("UPDATE media SET title = ? WHERE id = ?",
                     (clean or ark_media.title_from_filename(str(row["filename"] or "")),
                      int(item_id)))
        ark_db.audit(conn, action="media-title", actor_id=ctx.admin["id"],
                     actor_name=ctx.admin["username"], target=str(item_id), result="ok",
                     detail=clean[:120])
    return _redirect("/admin/media?ok=" + urllib.parse.quote("The name is saved."))


@router.post("/media/{item_id}/captions", response_class=HTMLResponse)
def media_captions(request: Request, item_id: int, file: UploadFile = File(None),
                   csrf: str = Form("")):
    if (blocked := _gate_tls(request)) is not None:
        return blocked
    with ark_db.tx() as conn:
        ctx, blocked = _media_check(ctx=None, conn=conn, csrf=csrf, request=request)
        if blocked is not None:
            return blocked
        if ark_media.item(conn, item_id) is None:
            return _redirect("/admin/media?err=" + urllib.parse.quote("That film is gone."))
        if file is None or not file.filename:
            return _redirect("/admin/media?err=" + urllib.parse.quote(
                "No caption file was chosen."))
        shown = _picked_name(file.filename)
        payload = file.file.read(2 * 1024 * 1024 + 1)
        file.file.close()
        try:
            ark_media.add_captions(conn, item_id, shown, payload,
                                   source=str(ctx.admin["username"]))
        except ark_storage.Refused as exc:
            return _redirect("/admin/media?err=" + urllib.parse.quote(str(exc)))
        ark_db.audit(conn, action="media-captions", actor_id=ctx.admin["id"],
                     actor_name=ctx.admin["username"], target=str(item_id), result="ok",
                     detail=shown)
    return _redirect("/admin/media?ok=" + urllib.parse.quote("Captions added."))


@router.post("/media/{item_id}/delete", response_class=HTMLResponse)
def media_delete(request: Request, item_id: int, confirm: str = Form(""),
                 csrf: str = Form("")):
    if (blocked := _gate_tls(request)) is not None:
        return blocked
    if confirm != "yes":
        return _redirect("/admin/media?err=" + urllib.parse.quote(
            "Nothing was deleted — the box was not told it was sure."))
    with ark_db.tx() as conn:
        ctx, blocked = _media_check(ctx=None, conn=conn, csrf=csrf, request=request)
        if blocked is not None:
            return blocked
        result = ark_media.delete(conn, item_id)
        ark_db.audit(conn, action="media-delete", actor_id=ctx.admin["id"],
                     actor_name=ctx.admin["username"], target=str(item_id), result="ok",
                     detail=f"freed about {result['freed']} bytes")
    return _redirect("/admin/media?ok=" + urllib.parse.quote(
        f"Deleted. About {ark_media.human_size(result['freed'])} was removed from the card."))


@router.post("/media/delete", response_class=HTMLResponse)
def media_delete_many(request: Request, ids: Optional[List[int]] = Form(None),
                      confirm: str = Form(""), csrf: str = Form("")):
    """Delete several films at once — only when the box was told it was sure."""
    if (blocked := _gate_tls(request)) is not None:
        return blocked
    if confirm != "yes":
        return _redirect("/admin/media?err=" + urllib.parse.quote(
            "Nothing was deleted — the box was not told it was sure."))
    chosen = [int(one) for one in (ids or [])][:200]
    if not chosen:
        return _redirect("/admin/media?err=" + urllib.parse.quote("No film was ticked."))
    with ark_db.tx() as conn:
        ctx, blocked = _media_check(ctx=None, conn=conn, csrf=csrf, request=request)
        if blocked is not None:
            return blocked
        result = ark_media.delete_many(conn, chosen)
        ark_db.audit(conn, action="media-delete", actor_id=ctx.admin["id"],
                     actor_name=ctx.admin["username"], target=f"{result['removed']} films",
                     result="ok", detail=f"freed about {result['freed']} bytes")
    return _redirect("/admin/media?ok=" + urllib.parse.quote(
        f"{result['removed']} deleted, about {ark_media.human_size(result['freed'])} removed. "
        "A file somebody is watching right now only becomes free space when they close it."))


@router.api_route("/media/{item_id}/poster", methods=["GET", "HEAD"])
def media_admin_poster(request: Request, item_id: int):
    """The owner may see a film's picture before it is published."""
    if (blocked := _gate_tls(request)) is not None:
        return blocked
    with ark_db.read() as conn:
        ctx = _load_ctx(conn, request, ark_db.session_epoch(conn))
        if (blocked := _need_session(ctx, request)) is not None:
            return blocked
        row = ark_media.item(conn, item_id)
    if row is None:
        return Response("Not here", status_code=404)
    path = ark_media.poster_path(row)
    if path is None:
        return Response(ark_media_web.fallback_poster(), media_type="image/svg+xml")
    return ark_media_web.ranged(request, path, "image/jpeg", private=True)


@router.api_route("/media/{item_id}/stream", methods=["GET", "HEAD"])
def media_admin_stream(request: Request, item_id: int):
    """The owner may watch an unpublished film — the public door stays closed."""
    if (blocked := _gate_tls(request)) is not None:
        return blocked
    with ark_db.read() as conn:
        ctx = _load_ctx(conn, request, ark_db.session_epoch(conn))
        if (blocked := _need_session(ctx, request)) is not None:
            return blocked
        row = ark_media.item(conn, item_id)
    if row is None:
        return Response("Not here", status_code=404)
    path = ark_media.playable(row)
    if path is None:
        return Response("That film is not ready to play yet.", status_code=409)
    return ark_media_web.ranged(request, path, ark_media_web.play_type(row), private=True)
