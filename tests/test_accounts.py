"""Adding people, switching them off, and the rule that keeps someone able to get in."""

from __future__ import annotations

import time

from helpers import (BIG_PASSWORD, LAN, SECOND_PASSWORD, add_admin, admin_cookie,
                     claim_box, csrf_of, get, post, sign_in)


def test_new_admin_gets_a_one_time_password_and_must_change_it(ark, client):
    owner = claim_box(client)
    text, temp = add_admin(client, owner, your_password=BIG_PASSWORD, username="sam")
    assert len(temp) == 20
    assert "Write it down now" in text

    # The secret is shown but never written down anywhere.
    with ark.db.read() as conn:
        leaked = conn.execute(
            "SELECT COUNT(*) FROM audit_log WHERE detail LIKE ? OR target LIKE ?",
            (f"%{temp}%", f"%{temp}%")).fetchone()[0]
        assert leaked == 0, "temporary password must never reach the audit log"

    # Sam signs in from the LAN (not the private channel) and is sent to setup.
    sam = sign_in(client, "sam", temp, headers=LAN)
    assert sam.status == 303
    assert sam.response.headers["location"] == "/admin/setup"
    page = get(client, "/admin/setup", headers=LAN, cookie=f"ark_admin={sam.cookie}")
    assert page.status_code == 200      # only the box's *first* claim needs the tunnel

    done = post(client, "/admin/setup",
                {"new_password": SECOND_PASSWORD, "confirm": SECOND_PASSWORD,
                 "csrf": csrf_of(page.text)},
                headers=LAN, cookie=f"ark_admin={sam.cookie}")
    assert done.status_code == 303
    fresh = admin_cookie(done)
    assert get(client, "/admin/accounts", headers=LAN,
               cookie=f"ark_admin={fresh}").status_code == 200


def test_username_rules_and_uniqueness(ark, client):
    owner = claim_box(client)
    page = get(client, "/admin/accounts", cookie=f"ark_admin={owner}")
    csrf = csrf_of(page.text)

    bad = post(client, "/admin/accounts/create",
               {"username": "no spaces please", "current_password": BIG_PASSWORD,
                "csrf": csrf}, cookie=f"ark_admin={owner}")
    assert bad.status_code == 400

    first = post(client, "/admin/accounts/create",
                 {"username": "Sam", "current_password": BIG_PASSWORD, "csrf": csrf},
                 cookie=f"ark_admin={owner}")
    assert "Temporary password" in first.text

    with ark.db.read() as conn:
        stored = conn.execute("SELECT username FROM admins WHERE username = 'sam'").fetchone()
    assert stored is not None, "usernames are case-folded"

    again = post(client, "/admin/accounts/create",
                 {"username": "SAM", "current_password": BIG_PASSWORD, "csrf": csrf},
                 cookie=f"ark_admin={owner}")
    assert again.status_code == 409


def test_account_creation_needs_your_own_password(ark, client):
    owner = claim_box(client)
    page = get(client, "/admin/accounts", cookie=f"ark_admin={owner}")
    made = post(client, "/admin/accounts/create",
                {"username": "sam", "current_password": "not-my-password",
                 "csrf": csrf_of(page.text)}, cookie=f"ark_admin={owner}")
    assert made.status_code == 403
    with ark.db.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM admins").fetchone()[0] == 1


def test_the_last_usable_admin_cannot_be_switched_off(ark, client):
    owner = claim_box(client)
    page = get(client, "/admin/accounts", cookie=f"ark_admin={owner}")
    refused = post(client, "/admin/accounts/1/disable",
                   {"current_password": BIG_PASSWORD, "csrf": csrf_of(page.text)},
                   cookie=f"ark_admin={owner}")
    assert refused.status_code == 409
    assert "last administrator" in refused.text
    with ark.db.read() as conn:
        assert conn.execute("SELECT enabled FROM admins WHERE id = 1").fetchone()[0] == 1


def test_a_temporary_account_does_not_count_as_the_last_admin(ark, client):
    owner = claim_box(client)
    add_admin(client, owner, your_password=BIG_PASSWORD, username="sam")
    page = get(client, "/admin/accounts", cookie=f"ark_admin={owner}")
    # sam has never chosen a password, so switching the owner off would lock the box.
    refused = post(client, "/admin/accounts/1/disable",
                   {"current_password": BIG_PASSWORD, "csrf": csrf_of(page.text)},
                   cookie=f"ark_admin={owner}")
    assert refused.status_code == 409


def test_disabling_an_admin_ends_their_sessions(ark, client):
    owner = claim_box(client)
    _text, temp = add_admin(client, owner, your_password=BIG_PASSWORD, username="sam")
    sam = sign_in(client, "sam", temp, headers=LAN)
    assert get(client, "/admin/setup", headers=LAN,
               cookie=f"ark_admin={sam.cookie}").status_code == 200

    page = get(client, "/admin/accounts", cookie=f"ark_admin={owner}")
    done = post(client, "/admin/accounts/2/disable",
                {"current_password": BIG_PASSWORD, "csrf": csrf_of(page.text)},
                cookie=f"ark_admin={owner}")
    assert done.status_code == 303

    assert get(client, "/admin/setup", headers=LAN,
               cookie=f"ark_admin={sam.cookie}").status_code == 303      # signed out
    assert sign_in(client, "sam", temp, headers=LAN).status == 401        # and locked out


def test_reset_shows_a_new_password_and_revokes_sessions(ark, client):
    owner = claim_box(client)
    _text, first_temp = add_admin(client, owner, your_password=BIG_PASSWORD, username="sam")
    sam = sign_in(client, "sam", first_temp, headers=LAN)

    page = get(client, "/admin/accounts", cookie=f"ark_admin={owner}")
    reset = post(client, "/admin/accounts/2/reset",
                 {"current_password": BIG_PASSWORD, "csrf": csrf_of(page.text)},
                 cookie=f"ark_admin={owner}")
    assert reset.status_code == 200
    assert "Temporary password" in reset.text
    assert get(client, "/admin/setup", headers=LAN,
               cookie=f"ark_admin={sam.cookie}").status_code == 303
    assert sign_in(client, "sam", first_temp, headers=LAN).status == 401


def test_changing_my_password_needs_the_old_one_and_ends_other_sessions(ark, client):
    owner = claim_box(client)
    page = get(client, "/admin/password", cookie=f"ark_admin={owner}")
    csrf = csrf_of(page.text)

    wrong = post(client, "/admin/password",
                 {"current_password": "guess", "new_password": SECOND_PASSWORD,
                  "confirm": SECOND_PASSWORD, "csrf": csrf}, cookie=f"ark_admin={owner}")
    assert wrong.status_code == 403

    ok = post(client, "/admin/password",
              {"current_password": BIG_PASSWORD, "new_password": SECOND_PASSWORD,
               "confirm": SECOND_PASSWORD, "csrf": csrf}, cookie=f"ark_admin={owner}")
    assert ok.status_code == 303
    assert get(client, "/admin", cookie=f"ark_admin={owner}").status_code == 303
    fresh = admin_cookie(ok)
    assert get(client, "/admin", cookie=f"ark_admin={fresh}").status_code == 200
    assert sign_in(client, "admin", BIG_PASSWORD).status == 401
    assert sign_in(client, "admin", SECOND_PASSWORD).status == 303



def test_wrong_passwords_are_generic_throttled_and_audited(ark, client):
    for _ in range(5):
        bad = sign_in(client, "admin", "definitely-not-it")
        assert bad.status == 401
        assert "definitely-not-it" not in bad.text
    blocked = sign_in(client, "admin", "ChangeMe-ARK!")
    assert blocked.status == 429
    assert int(blocked.response.headers["retry-after"]) > 0

    with ark.db.read() as conn:
        denied = conn.execute("SELECT COUNT(*) FROM audit_log WHERE result = 'denied'"
                              ).fetchone()[0]
        detail = " ".join(r["detail"] for r in
                          conn.execute("SELECT detail FROM audit_log").fetchall())
    assert denied >= 5
    assert "definitely-not-it" not in detail

    # Once the failures age out of the window, the correct password works again.
    with ark.db.tx() as conn:
        conn.execute("UPDATE login_attempts SET ts = ts - 100000")
    assert sign_in(client, "admin", "ChangeMe-ARK!").status == 303


def test_sessions_expire_on_idle_and_on_restart(ark, client):
    owner = claim_box(client)
    assert get(client, "/admin", cookie=f"ark_admin={owner}").status_code == 200

    with ark.db.tx() as conn:
        conn.execute("UPDATE admin_sessions SET last_seen = ?",
                     (time.time() - ark.sec.IDLE_TIMEOUT - 30,))
    assert get(client, "/admin", cookie=f"ark_admin={owner}").status_code == 303

    again = sign_in(client, "admin", BIG_PASSWORD)      # signing in again is always possible
    assert again.status == 303
    fresh = again.cookie
    assert get(client, "/admin", cookie=f"ark_admin={fresh}").status_code == 200
    with ark.db.tx() as conn:
        ark.db.bump_session_epoch(conn)          # what a restart does
    assert get(client, "/admin", cookie=f"ark_admin={fresh}").status_code == 303


def test_absolute_lifetime_wins_over_activity(ark, client):
    owner = claim_box(client)
    with ark.db.tx() as conn:
        conn.execute("UPDATE admin_sessions SET abs_expires = ?", (time.time() - 1,))
    assert get(client, "/admin", cookie=f"ark_admin={owner}").status_code == 303


def test_a_clock_that_jumps_backwards_does_not_extend_a_session(ark, client):
    owner = claim_box(client)
    with ark.db.tx() as conn:
        conn.execute("UPDATE admin_sessions SET created = ?, abs_expires = ?",
                     (time.time() + 7200, time.time() + 7200))
    assert get(client, "/admin", cookie=f"ark_admin={owner}").status_code == 303


def test_sign_out_is_immediate(ark, client):
    owner = claim_box(client)
    page = get(client, "/admin/password", cookie=f"ark_admin={owner}")
    done = post(client, "/admin/logout", {"csrf": csrf_of(page.text)},
                cookie=f"ark_admin={owner}")
    assert done.status_code == 303
    assert get(client, "/admin", cookie=f"ark_admin={owner}").status_code == 303

