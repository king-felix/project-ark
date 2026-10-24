"""The public half: reading, the board, the file box — and what is still public."""

from __future__ import annotations


def test_public_pages_answer(public):
    client = public.client
    assert client.get("/board").status_code == 200
    status = client.get("/api/status")
    assert status.status_code == 200
    payload = status.json()
    assert set(payload) >= {"library", "books", "guides", "downloads"}
    # The home page itself is static HTML served by nginx; the API has no route
    # for it, which is why /status is the honest thing to probe here.
    assert client.get("/search?q=water").status_code == 200


def test_invalid_board_post_returns_400_not_500(public):
    """Regression: error_page() used to wrap an HTMLResponse in another one and
    raise AttributeError, so a typo in the form produced an empty-bodied 500."""
    response = public.client.post("/board", data={"name": "Sam", "body": "x"},
                                  follow_redirects=False)
    assert response.status_code == 400
    assert "empty" in response.text.lower()
    assert "Something went wrong" in response.text


def test_overlong_board_post_is_refused_politely(public):
    response = public.client.post("/board", data={"name": "Sam", "body": "x" * 5000},
                                  follow_redirects=False)
    assert response.status_code == 400
    assert "too long" in response.text.lower()


def test_board_stores_and_escapes_what_people_write(public):
    posted = public.client.post("/board", data={
        "name": "<script>alert(1)</script>",
        "body": "The tap leaks <img src=x onerror=alert(2)>"}, follow_redirects=False)
    assert posted.status_code == 303

    listing = public.client.get("/board")
    assert listing.status_code == 200
    assert "<script>alert(1)</script>" not in listing.text
    assert "&lt;script&gt;" in listing.text
    assert "The tap leaks" in listing.text


def test_file_box_lists_what_is_shared(public):
    page = public.client.get("/files")
    assert page.status_code == 200
    assert "File Box" in page.text


def test_the_public_portal_cannot_reach_administration(public):
    """nginx never proxies /admin on :80 — and the API refuses it without TLS."""
    for path in ("/admin", "/admin/login", "/admin/accounts"):
        response = public.client.get(path)
        assert response.status_code == 403, path
        assert "Secure address" in response.text


def test_administration_is_never_in_the_public_navigation(public):
    assert "/admin" not in public.client.get("/").text
    assert "/admin" not in public.client.get("/board").text
