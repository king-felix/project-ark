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
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

import ark_db
import ark_health
import ark_security as sec
import ark_storage

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
                initial = sec.hash_password(sec.BOOTSTRAP_PASSWORD)
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
    ctx = Ctx(conn=conn, epoch=epoch, session=row, csrf=row["csrf"] if row else "")
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


def shell(title: str, body: str, *, ctx: Ctx | None = None, status: int = 200,
          tagline: str = "") -> HTMLResponse:
    nav = ""
    if ctx is not None and ctx.admin is not None and not ctx.admin["must_change"]:
        links = [("/admin", "Overview"), ("/admin/health", "What is working"),
                 ("/admin/community", "Community"),
                 ("/admin/accounts", "Administrators"),
                 ("/admin/password", "My password"), ("/admin/audit", "History")]
        nav = '<div class="nav">' + "".join(
            f'<a href="{esc(href)}">{esc(label)}</a>' for href, label in links
        ) + "</div>"
        who = f'<p class="hint">Signed in as <b>{esc(ctx.admin["username"])}</b></p>'
    else:
        who = ""
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
<div class="wrap">
  <header>
    <span class="logo">Ark administrator</span>
    <h1>{esc(title)}</h1>
    {sub}
  </header>
  {nav}
  {who}
{body}
  <footer><p><b>Private area.</b> Public readers never see this page or these settings.</p></footer>
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
    return shell("Finish setup over a direct connection", f"""
  <p class="status"><span class="dot" aria-hidden="true"></span><span><b>This box has not been
     claimed yet.</b> To stop the first stranger on the Wi-Fi becoming its administrator, the
     first sign-in only works over a private connection to the box itself.</span></p>
  <h2>On your own computer</h2>
  <p class="hint">Run this, then open the address below in the same computer's browser:</p>
  <pre class="code">{esc(SETUP_HINT)}</pre>
  <p class="note">Sign in as <b>{esc(sec.BOOTSTRAP_USERNAME)}</b> with the temporary password
     <b>{esc(sec.BOOTSTRAP_PASSWORD)}</b>. You will be asked to choose your own password
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
        good = bool(row) and bool(row["enabled"]) and sec.verify_password(password,
                                                                         row["pass_hash"])
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
    if (blocked := _gate_tls(request)) is not None:
        return blocked
    with ark_db.tx() as conn:
        ctx = _load_ctx(conn, request, ark_db.session_epoch(conn))
        if ctx.session is not None and sec.csrf_ok(ctx.csrf, csrf):
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
        files = int(conn.execute("SELECT COUNT(*) FROM files").fetchone()[0])
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
    body = f"""
  {_notice("good", f"<b>You are the administrator.</b> {active} enabled "
                   f"account{'s' if active != 1 else ''} on this box.")}
  <div class="grid">
    <div class="card"><b>{len(admins)}</b><span class="sub">administrators</span></div>
    <div class="card"><b>{notes}</b><span class="sub">messages on the board</span></div>
    <div class="card"><b>{files}</b><span class="sub">shared files</span></div>
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


def _health_row(section: dict) -> str:
    """One part of the box: its state, what that means, and what to do."""
    state = ("<span class=\"state\">Ready</span>" if section["ready"]
             else "<span class=\"state no\">Not ready</span>")
    fix = (f'<span class="fix">What to do: {esc(section["action"])}</span>'
           if section["action"] else "")
    return (f'<li><b>{esc(section["label"])}</b> {state}'
            f'<span class="sub">{esc(section["reason"])}</span>{fix}</li>')


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
    switches = (
        '<form method="post" action="/admin/community/switches">\n'
        f'    {_csrf_field(ctx.csrf)}\n'
        f'    {board_switch}\n'
        f'    {uploads_switch}\n'
        '    <button class="go-btn" type="submit">Save these switches</button>\n'
        '  </form>\n'
        '  <p class="note">These are the only two things that decide who can write here. '
        'There is no sign-in for readers, by design — the box is in the room with them.</p>')

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
                       csrf: str = Form("")):
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
                           ("public_uploads", "1" if uploads else "0")):
            ark_db.set_setting(conn, key, value)
        ark_db.audit(conn, action="community-switches", actor_id=me["id"],
                     actor_name=me["username"], result="ok",
                     detail=f"posting={'on' if board else 'off'}, "
                            f"uploads={'on' if uploads else 'off'}")
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

