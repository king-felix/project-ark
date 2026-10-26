"""The message board: what a reader sees, and what the owner may do about it.

Moderation is deliberately reversible: hiding a message stops it appearing on the
reading page, pinning keeps a warning at the top, and marking something "sorted
out" leaves the answer in place for the next person.  Nothing is deleted by the
moderation actions, and every action is written to the audit history.
"""

from __future__ import annotations

from helpers import BIG_PASSWORD, LAN, claim_box, csrf_of, get, post


def _note(client, body: str, name: str = "Sam") -> None:
    answer = client.post("/board", data={"name": name, "body": body}, follow_redirects=False)
    assert answer.status_code == 303, answer.text


def _note_rows(ark) -> list:
    with ark.db.read() as conn:
        return conn.execute("SELECT * FROM notes ORDER BY id").fetchall()


def _file_rows(ark) -> list:
    with ark.db.read() as conn:
        return conn.execute("SELECT * FROM files ORDER BY id").fetchall()


def _community(client, cookie: str):
    page = get(client, "/admin/community", headers=LAN, cookie=f"ark_admin={cookie}")
    assert page.status_code == 200, page.text[:400]
    return page, csrf_of(page.text)


def _act(client, cookie: str, path: str, data: dict):
    _page, csrf = _community(client, cookie)
    return post(client, path, {**data, "csrf": csrf}, headers=LAN,
                cookie=f"ark_admin={cookie}")


# ---------------------------------------------------------------------------
#  ordering, pinning, hiding
# ---------------------------------------------------------------------------
def test_board_pages_come_newest_first(public):
    for n in range(3):
        _note(public.client, f"message {n}")
    page = public.client.get("/board")
    assert page.text.index("message 2") < page.text.index("message 1") < page.text.index("message 0")


def test_a_pinned_warning_stays_at_the_top(public, ark):
    _note(public.client, "old note")
    _note(public.client, "boil water for one minute")
    cookie = claim_box(public.client, password=BIG_PASSWORD)
    pinned = _act(public.client, cookie, "/admin/board/2/state", {"action": "pin"})
    assert pinned.status_code == 303
    page = public.client.get("/board")
    assert page.text.index("boil water") < page.text.index("old note")


def test_marking_something_sorted_moves_it_down_but_keeps_it(public):
    _note(public.client, "the tap is leaking")
    cookie = claim_box(public.client, password=BIG_PASSWORD)
    answer = _act(public.client, cookie, "/admin/board/1/state", {"action": "resolve"})
    assert answer.status_code == 303
    page = public.client.get("/board")
    assert "the tap is leaking" in page.text
    assert "Sorted out" in page.text


def test_a_hidden_message_disappears_from_the_reading_page(public, ark):
    _note(public.client, "buy my miracle tonic")
    cookie = claim_box(public.client, password=BIG_PASSWORD)
    answer = _act(public.client, cookie, "/admin/board/1/state", {"action": "hide"})
    assert answer.status_code == 303
    assert "miracle tonic" not in public.client.get("/board").text
    # ...but the row is still there, so the owner can put it back
    assert _note_rows(ark)[0]["hidden"] == 1
    restored = _act(public.client, cookie, "/admin/board/1/state", {"action": "restore"})
    assert restored.status_code == 303
    assert "miracle tonic" in public.client.get("/board").text


def test_the_board_paginates(public):
    for n in range(30):
        _note(public.client, f"note number {n:02d}")
    first = public.client.get("/board")
    assert first.status_code == 200
    assert "note number 29" in first.text
    assert "note number 00" not in first.text
    second = public.client.get("/board?p=2")
    assert second.status_code == 200
    assert "note number 00" in second.text
# ---------------------------------------------------------------------------
#  the owner's switches
# ---------------------------------------------------------------------------
def test_writing_is_open_until_the_owner_closes_it(public, ark):
    with ark.db.read() as conn:
        assert ark.db.get_setting(conn, "public_board_post", "1") == "1"
        assert ark.db.get_setting(conn, "public_uploads", "1") == "1"
    _note(public.client, "still allowed out of the box")
    upload = public.client.post("/files", files={"file": ("a.txt", b"a", "text/plain")},
                                follow_redirects=False)
    assert upload.status_code == 303 and "ok=1" in upload.headers["location"]


def test_the_owner_can_close_anonymous_posting(public, ark):
    cookie = claim_box(public.client, password=BIG_PASSWORD)
    _page, csrf = _community(public.client, cookie)
    closed = post(public.client, "/admin/community/switches",
                  {"csrf": csrf, "uploads": "1"}, headers=LAN, cookie=f"ark_admin={cookie}")
    assert closed.status_code == 303

    refused = public.client.post("/board", data={"name": "Sam", "body": "hello"},
                                 follow_redirects=False)
    assert refused.status_code == 403
    assert "switched off" in refused.text.lower()
    with ark.db.read() as conn:
        assert ark.db.get_setting(conn, "public_board_post", "1") == "0"
        assert ark.db.get_setting(conn, "public_uploads", "1") == "1"

    upload = public.client.post("/files", files={"file": ("a.txt", b"a", "text/plain")},
                                follow_redirects=False)
    assert upload.status_code == 303 and "ok=1" in upload.headers["location"]

    # ...and opening it again takes one click, not a rebuild
    reopened = post(public.client, "/admin/community/switches",
                    {"csrf": csrf, "board": "1", "uploads": "1"}, headers=LAN,
                    cookie=f"ark_admin={cookie}")
    assert reopened.status_code == 303
    _note(public.client, "allowed again")


def test_the_owner_can_close_anonymous_uploading(public):
    cookie = claim_box(public.client, password=BIG_PASSWORD)
    _page, csrf = _community(public.client, cookie)
    post(public.client, "/admin/community/switches", {"csrf": csrf, "board": "1"},
         headers=LAN, cookie=f"ark_admin={cookie}")
    refused = public.client.post("/files", files={"file": ("a.txt", b"a", "text/plain")},
                                 follow_redirects=False)
    assert refused.status_code == 403
    assert "switched off" in refused.text.lower()


# ---------------------------------------------------------------------------
#  the helper's switch (the same three checkboxes, one page)
# ---------------------------------------------------------------------------
def test_the_owner_can_close_the_helper_without_closing_anything_else(public, ark):
    cookie = claim_box(public.client, password=BIG_PASSWORD)
    _page, csrf = _community(public.client, cookie)
    closed = post(public.client, "/admin/community/switches",
                  {"csrf": csrf, "board": "1", "uploads": "1"}, headers=LAN,
                  cookie=f"ark_admin={cookie}")
    assert closed.status_code == 303

    page = public.client.get("/ask")
    assert page.status_code == 403
    assert "switched off" in page.text.lower()
    assert "questions other people ask" not in page.text.lower()
    with ark.db.read() as conn:
        assert ark.db.get_setting(conn, "public_ask", "1") == "0"
        # ...and nothing else moved
        assert ark.db.get_setting(conn, "public_board_post", "1") == "1"
        assert ark.db.get_setting(conn, "public_uploads", "1") == "1"

    _note(public.client, "the board is still open")
    opened = post(public.client, "/admin/community/switches",
                  {"csrf": csrf, "board": "1", "uploads": "1", "ask": "1"},
                  headers=LAN, cookie=f"ark_admin={cookie}")
    assert opened.status_code == 303
    again = public.client.get("/ask")
    assert again.status_code == 200
    assert "questions other people ask" in again.text.lower()


# ---------------------------------------------------------------------------
#  the door itself
# ---------------------------------------------------------------------------
def test_moderation_without_a_session_is_refused(client, ark):
    answer = post(client, "/admin/board/1/state", {"action": "hide", "csrf": "x"})
    assert answer.status_code == 403
    answer = post(client, "/admin/community/switches", {"csrf": "x"})
    assert answer.status_code == 403
    with ark.db.read() as conn:
        assert ark.db.get_setting(conn, "public_board_post", "1") == "1"


def test_moderation_without_csrf_is_refused(public):
    cookie = claim_box(public.client, password=BIG_PASSWORD)
    _note(public.client, "hello")
    answer = post(public.client, "/admin/board/1/state", {"action": "hide"},
                  headers=LAN, cookie=f"ark_admin={cookie}")
    assert answer.status_code == 403
    assert "Nothing was changed" in answer.text
    assert "hello" in public.client.get("/board").text


def test_an_unknown_action_changes_nothing(public, ark):
    _note(public.client, "hello")
    cookie = claim_box(public.client, password=BIG_PASSWORD)
    answer = _act(public.client, cookie, "/admin/board/1/state", {"action": "levitate"})
    assert answer.status_code == 400
    assert _note_rows(ark)[0]["hidden"] == 0


def test_every_moderation_action_is_audited(public, ark):
    _note(public.client, "hello")
    cookie = claim_box(public.client, password=BIG_PASSWORD)
    _act(public.client, cookie, "/admin/board/1/state", {"action": "hide"})
    with ark.db.read() as conn:
        rows = conn.execute("SELECT action, target, result FROM audit_log"
                            " WHERE action LIKE 'board-%'").fetchall()
    assert (rows[0]["action"], rows[0]["target"], rows[0]["result"]) == ("board-hide", "1", "ok")


# ---------------------------------------------------------------------------
#  shared files from the owner's side
# ---------------------------------------------------------------------------
def test_the_owner_can_hide_and_restore_a_shared_file(public, ark):
    public.client.post("/files", files={"file": ("a.txt", b"secret notes", "text/plain")},
                       follow_redirects=False)
    cookie = claim_box(public.client, password=BIG_PASSWORD)
    hidden = _act(public.client, cookie, "/admin/files/1/state", {"action": "hide"})
    assert hidden.status_code == 303
    assert "a.txt" not in public.client.get("/files").text
    shown = _act(public.client, cookie, "/admin/files/1/state", {"action": "restore"})
    assert shown.status_code == 303
    assert "a.txt" in public.client.get("/files").text


def test_deleting_a_shared_file_needs_the_owners_own_password(public, ark):
    public.client.post("/files", files={"file": ("junk.txt", b"junk", "text/plain")},
                       follow_redirects=False)
    cookie = claim_box(public.client, password=BIG_PASSWORD)
    stored = _file_rows(ark)[0]["stored"]

    refused = _act(public.client, cookie, "/admin/files/1/delete", {"current_password": "wrong"})
    assert refused.status_code == 403
    assert (ark.root / "drop" / "files" / stored).is_file()

    removed = _act(public.client, cookie, "/admin/files/1/delete",
                   {"current_password": BIG_PASSWORD})
    assert removed.status_code == 303
    assert not (ark.root / "drop" / "files" / stored).exists()
    assert _file_rows(ark) == []


def test_the_deleter_cannot_be_pointed_outside_the_shared_folder(public):
    """Delete removes one file from drop/files — never anything else on the card."""
    from ark_admin import _remove_from_card

    ark = public.ark
    outside = ark.root / "keep-this.txt"
    outside.write_bytes(b"do not touch")
    assert _remove_from_card("../../keep-this.txt") is False
    assert outside.exists()
