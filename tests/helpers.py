"""Small helpers so the tests read like the story they are telling."""

from __future__ import annotations

import re
from types import SimpleNamespace

# The management listener (nginx :8443) sets these; the API only believes them
# from a loopback peer, which in tests is the TestClient itself.
HTTPS = {"x-forwarded-proto": "https"}
LAN = {**HTTPS, "x-real-ip": "192.168.1.50"}
SETUP = {**LAN, "x-ark-setup-channel": "loopback"}

BIG_PASSWORD = "correct horse battery staple 42"
SECOND_PASSWORD = "a much better passphrase 7"


def csrf_of(text: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', text)
    return match.group(1) if match else ""


def get(client, path: str, *, headers=None, cookie: str = "", cookies: dict | None = None):
    h = dict(LAN if headers is None else headers)
    if cookie:
        h["cookie"] = cookie
    return client.get(path, headers=h, cookies=cookies, follow_redirects=False)


def post(client, path: str, data: dict, *, headers=None, cookie: str = "",
         cookies: dict | None = None):
    h = dict(LAN if headers is None else headers)
    if cookie:
        h["cookie"] = cookie
    return client.post(path, data=data, headers=h, cookies=cookies,
                       follow_redirects=False)


def admin_cookie(response) -> str:
    """The session cookie value, read straight off the Set-Cookie header."""
    raw = response.headers.get("set-cookie", "")
    for part in raw.split(","):
        bits = part.strip().split(";")[0]
        if bits.startswith("ark_admin="):
            return bits.split("=", 1)[1]
    return ""


def form_cookie(response, name: str) -> str:
    raw = response.headers.get("set-cookie", "")
    for part in raw.split(","):
        bits = part.strip().split(";")[0]
        if bits.startswith(name + "="):
            return bits.split("=", 1)[1]
    return ""


def sign_in(client, username: str, password: str, *, headers=None) -> SimpleNamespace:
    """Walk the form flow exactly as a person would, without JavaScript."""
    headers = headers or SETUP
    form = get(client, "/admin/login", headers=headers)
    token = form_cookie(form, "ark_admin_form")
    page = post(client, "/admin/login",
                {"username": username, "password": password, "csrf": csrf_of(form.text)},
                headers=headers, cookie=f"ark_admin_form={token}")
    return SimpleNamespace(response=page, status=page.status_code,
                           cookie=admin_cookie(page), text=page.text)


def claim_box(client, *, password: str = BIG_PASSWORD, headers=None) -> str:
    """Bootstrap sign-in + forced password change. Returns the new session cookie."""
    headers = headers or SETUP
    first = sign_in(client, "admin", "ChangeMe-ARK!", headers=headers)
    assert first.status == 303, first.text[:400]
    setup_page = get(client, "/admin/setup", headers=headers, cookie=f"ark_admin={first.cookie}")
    done = post(client, "/admin/setup",
                {"new_password": password, "confirm": password,
                 "csrf": csrf_of(setup_page.text)},
                headers=headers, cookie=f"ark_admin={first.cookie}")
    assert done.status_code == 303, done.text[:400]
    return admin_cookie(done)


def add_admin(client, cookie: str, *, your_password: str, username: str,
              headers=None) -> tuple[str, str]:
    """Create another administrator; returns (response text, temporary password)."""
    headers = headers or LAN
    page = get(client, "/admin/accounts", headers=headers, cookie=f"ark_admin={cookie}")
    made = post(client, "/admin/accounts/create",
                {"username": username, "display_name": username.title(),
                 "current_password": your_password, "csrf": csrf_of(page.text)},
                headers=headers, cookie=f"ark_admin={cookie}")
    match = re.search(r'<pre class="code temp">([^<]+)</pre>', made.text)
    return made.text, (match.group(1) if match else "")
