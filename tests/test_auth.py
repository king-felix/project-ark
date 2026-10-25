"""Who may become an administrator, and what they may do once they are one."""

from __future__ import annotations

from pathlib import Path

from helpers import (BIG_PASSWORD, LAN, SETUP, add_admin, admin_cookie,
                     claim_box, csrf_of, get, post, sign_in)


def _bootstrap_session(client) -> tuple[str, str]:
    """Sign in as the bootstrap account (private channel) -> (cookie, csrf)."""
    first = sign_in(client, "admin", "ChangeMe-ARK!")
    assert first.status == 303, first.text[:300]
    page = get(client, "/admin/setup", headers=SETUP, cookie=f"ark_admin={first.cookie}")
    assert page.status_code == 200
    return first.cookie, csrf_of(page.text)


# ---------------------------------------------------------------------------
#  first run
# ---------------------------------------------------------------------------
def test_bootstrap_account_is_created_once(ark):
    with ark.db.read() as conn:
        rows = conn.execute("SELECT * FROM admins").fetchall()
    assert len(rows) == 1
    row = rows[0]
    assert row["username"] == "admin"
    assert row["must_change"] == 1 and row["is_bootstrap"] == 1 and row["enabled"] == 1
    assert row["pass_hash"].startswith("scrypt$")
    assert ark.sec.verify_password("ChangeMe-ARK!", row["pass_hash"])
    assert ark.db.MARKER_PATH.exists(), "install marker must exist outside the database"


def test_wiped_database_is_recovery_not_a_new_default(ark):
    with ark.db.tx() as conn:
        conn.execute("DELETE FROM admins")
    # A restart must not hand the box back to anyone who knows the public default.
    assert ark.admin.ensure_initialised() == "recovery-required"
    assert ark.admin.ensure_initialised() == "recovery-required"
    with ark.db.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM admins").fetchone()[0] == 0


def test_wiped_database_explains_recovery(client, ark):
    with ark.db.tx() as conn:
        conn.execute("DELETE FROM admins")
    page = get(client, "/admin/login", headers=SETUP)
    assert page.status_code == 503
    assert "ark-admin-recover" in page.text
    assert "ChangeMe" not in page.text


def test_lan_login_refused_before_the_box_is_claimed(client):
    page = get(client, "/admin/login", headers=LAN)
    assert page.status_code == 403
    assert "ssh -N -L 8444:127.0.0.1:8444" in page.text
    assert "ChangeMe-ARK!" in page.text      # the owner must be told the temp password


def test_bootstrap_can_only_finish_over_the_private_channel(client):
    cookie, _ = _bootstrap_session(client)
    page = get(client, "/admin/setup", headers=LAN, cookie=f"ark_admin={cookie}")
    assert page.status_code == 403
    assert "Private connection" in page.text


def test_incomplete_account_cannot_reach_management(client, ark):
    cookie, _ = _bootstrap_session(client)
    # A GET is redirected to the password step...
    assert get(client, "/admin/accounts", headers=SETUP, cookie=f"ark_admin={cookie}").status_code == 303
    # ...and a direct POST is refused outright, not merely hidden in the UI.
    made = post(client, "/admin/accounts/create",
                {"username": "sneaky", "current_password": "ChangeMe-ARK!", "csrf": "x"},
                headers=SETUP, cookie=f"ark_admin={cookie}")
    assert made.status_code == 403
    with ark.db.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM admins").fetchone()[0] == 1


# ---------------------------------------------------------------------------
#  password policy and the one-shot setup
# ---------------------------------------------------------------------------
def test_short_password_is_rejected(client):
    cookie, csrf = _bootstrap_session(client)
    page = post(client, "/admin/setup",
                {"new_password": "short", "confirm": "short", "csrf": csrf},
                headers=SETUP, cookie=f"ark_admin={cookie}")
    assert page.status_code == 400
    assert "at least 12" in page.text


def test_temporary_password_cannot_be_reused(client):
    cookie, csrf = _bootstrap_session(client)
    page = post(client, "/admin/setup",
                {"new_password": "ChangeMe-ARK!", "confirm": "ChangeMe-ARK!", "csrf": csrf},
                headers=SETUP, cookie=f"ark_admin={cookie}")
    assert page.status_code == 400
    assert "temporary password" in page.text


def test_mismatched_confirmation_is_rejected(client):
    cookie, csrf = _bootstrap_session(client)
    page = post(client, "/admin/setup",
                {"new_password": BIG_PASSWORD, "confirm": BIG_PASSWORD + "x", "csrf": csrf},
                headers=SETUP, cookie=f"ark_admin={cookie}")
    assert page.status_code == 400
    assert "do not match" in page.text


def test_setup_changes_password_and_revokes_the_old_session(ark, client):
    cookie, csrf = _bootstrap_session(client)
    done = post(client, "/admin/setup",
                {"new_password": BIG_PASSWORD, "confirm": BIG_PASSWORD, "csrf": csrf},
                headers=SETUP, cookie=f"ark_admin={cookie}")
    assert done.status_code == 303
    with ark.db.read() as conn:
        row = conn.execute("SELECT must_change, pass_hash FROM admins").fetchone()
    assert row["must_change"] == 0
    assert ark.sec.verify_password(BIG_PASSWORD, row["pass_hash"])
    assert not ark.sec.verify_password("ChangeMe-ARK!", row["pass_hash"])

    # The session used to set the password is not the session left behind.
    reused = get(client, "/admin", headers=SETUP, cookie=f"ark_admin={cookie}")
    assert reused.status_code == 303 and "/admin/login" in reused.headers["location"]
    fresh = admin_cookie(done)
    assert fresh and fresh != cookie
    assert get(client, "/admin", cookie=f"ark_admin={fresh}").status_code == 200


def test_a_complete_lan_sign_in_reaches_a_protected_page(client, ark):
    """TLS liveness is not authorization: the whole form flow must work on LAN."""
    cookie = claim_box(client, password=BIG_PASSWORD)
    landing = sign_in(client, "admin", BIG_PASSWORD, headers=LAN)
    assert landing.status == 303 and landing.cookie
    page = get(client, "/admin/accounts", headers=LAN,
               cookie=f"ark_admin={landing.cookie}")
    assert page.status_code == 200
    assert "Administrators" in page.text


def test_setup_cannot_overwrite_a_completed_password(ark):
    """The guard is the WHERE clause, so a race cannot win it either."""
    with ark.db.tx() as conn:
        first = conn.execute(
            "UPDATE admins SET pass_hash = 'first', must_change = 0 WHERE id = 1"
            " AND must_change = 1").rowcount
        second = conn.execute(
            "UPDATE admins SET pass_hash = 'second', must_change = 0 WHERE id = 1"
            " AND must_change = 1").rowcount
        stored = conn.execute("SELECT pass_hash FROM admins WHERE id = 1").fetchone()[0]
    assert (first, second) == (1, 0)
    assert stored == "first"


# ---------------------------------------------------------------------------
#  transport, CSRF and headers
# ---------------------------------------------------------------------------
def test_plain_http_never_reaches_a_password_field(client):
    page = get(client, "/admin", headers={})
    assert page.status_code == 403
    assert "Secure address" in page.text
    sent = post(client, "/admin/login",
                {"username": "admin", "password": "ChangeMe-ARK!", "csrf": ""}, headers={})
    assert sent.status_code == 403
    assert "ChangeMe-ARK!" not in sent.text


def test_security_headers_on_every_admin_page(client):
    page = get(client, "/admin/login")
    assert page.headers["cache-control"].startswith("no-store")
    assert page.headers["x-frame-options"] == "DENY"
    assert page.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]


def test_post_without_csrf_changes_nothing(ark, client):
    cookie = claim_box(client)
    made = post(client, "/admin/accounts/create",
                {"username": "sam", "current_password": BIG_PASSWORD, "csrf": "not-the-token"},
                headers=SETUP, cookie=f"ark_admin={cookie}")
    assert made.status_code == 403
    assert "expired" in made.text.lower()
    with ark.db.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM admins").fetchone()[0] == 1


def test_first_run_password_is_random_private_and_stable(tmp_path, monkeypatch):
    """No well-known password may exist on a box an owner actually boots.

    With no override, the first-run password is generated once, written 0600,
    and read back untouched afterwards.  (``ARK_BOOTSTRAP_PASSWORD`` is only the
    tests' and an expert owner's shortcut.)
    """
    monkeypatch.delenv("ARK_BOOTSTRAP_PASSWORD", raising=False)
    target = tmp_path / "bootstrap-password"
    monkeypatch.setenv("ARK_BOOTSTRAP_FILE", str(target))

    import ark_security as sec

    first = sec.bootstrap_password()
    assert len(first) >= sec.PASSWORD_MIN
    assert first != "ChangeMe-ARK!"
    assert target.read_text().strip() == first
    assert target.stat().st_mode & 0o777 == 0o600
    # Reading again must not rotate it under the owner's feet.
    assert sec.bootstrap_password() == first
    # And it cannot be chosen as the permanent password.
    try:
        sec.check_new_password(first, username="admin")
    except sec.PolicyError as exc:
        assert "temporary" in str(exc)
    else:                                        # pragma: no cover
        raise AssertionError("the first-run password was accepted as permanent")


def test_no_first_run_password_is_invented_by_a_policy_check(tmp_path, monkeypatch):
    """Checking a candidate password must not create the file as a side effect."""
    monkeypatch.delenv("ARK_BOOTSTRAP_PASSWORD", raising=False)
    target = tmp_path / "not-yet" / "bootstrap-password"
    monkeypatch.setenv("ARK_BOOTSTRAP_FILE", str(target))

    import ark_security as sec

    assert sec.bootstrap_password(create=False) == ""
    assert not target.exists()


def test_the_api_never_trusts_a_forwarded_address():
    """uvicorn rewrites the peer from X-Forwarded-For unless told not to.

    Every door in ark_admin (sign-in, the private setup channel, throttling)
    decides on the peer address.  If a local caller could make uvicorn replace
    it with a header, a stranger on the LAN could claim to be 127.0.0.1 simply
    by reaching a local proxy path.  The unit must disable that rewrite.
    """
    unit = (Path(__file__).resolve().parents[1] / "pi" / "systemd" / "ark-api.service").read_text()
    assert "--no-proxy-headers" in unit
    assert "--host 127.0.0.1" in unit


def test_forged_forwarding_headers_do_not_claim_the_box(ark):
    from starlette.requests import Request

    def request(host: str, headers: dict):
        scope = {
            "type": "http", "http_version": "1.1", "method": "GET",
            "scheme": "https", "path": "/admin", "raw_path": b"/admin",
            "query_string": b"", "root_path": "",
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
            "client": (host, 44444), "server": ("127.0.0.1", 8085),
        }
        return Request(scope)

    # A stranger may send anything it likes; the peer address decides.
    assert ark.admin.setup_channel(
        request("192.168.1.9", {"x-ark-setup-channel": "loopback"})) is False
    assert ark.admin.setup_channel(
        request("127.0.0.1", {"x-ark-setup-channel": "loopback"})) is True
    assert ark.admin.over_tls(
        request("192.168.1.9", {"x-forwarded-proto": "https"})) is False
    assert ark.admin.over_tls(
        request("127.0.0.1", {"x-forwarded-proto": "http"})) is False
    assert ark.admin.over_tls(
        request("127.0.0.1", {"x-forwarded-proto": "https"})) is True

