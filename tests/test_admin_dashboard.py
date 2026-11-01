"""The owner's front page: four figures, a way in, and a way out.

What these tests hold:

* the overview measures what a person opens it for — how long the box has been
  awake, how much room is left on the card, what a reader can open, what is on
  the board — and prints *not measured yet* in words when it could not ask,
  never a zero (§8.7's `None` rule);
* the number a person is looking for is reachable: the two things an owner comes
  to do have a button of their own, and the navigation says which page is being
  read without relying on colour;
* signing out is something that changes, so it is a POST with the form token —
  and it really ends the session, which a link cannot do safely;
* there is a way back to what a reader sees, built from the name the person
  actually typed;
* every address on the box that answers on that name is a link, and the one door
  that only answers on the box itself is printed as words instead;
* the public pages point at the owner's pages, in words a person can read.
"""

from __future__ import annotations

from pathlib import Path

from helpers import LAN, claim_box, csrf_of, get, post

REPO = Path(__file__).resolve().parents[1]
PASSWORD = "correct horse battery staple 42"


def _page(client, cookie: str, path: str = "/admin", headers=None):
    return get(client, path, cookie=f"ark_admin={cookie}", headers=headers)


def _claimed(ark, client) -> str:
    return claim_box(client, password=PASSWORD)


# ---------------------------------------------------------------------------
#  the four figures
# ---------------------------------------------------------------------------
def test_the_overview_shows_what_an_owner_opens_it_for(ark, client):
    cookie = _claimed(ark, client)
    page = _page(client, cookie)
    assert page.status_code == 200
    assert '<ul class="stats">' in page.text
    assert "How long the box has been on" in page.text
    assert "Room left on the card" in page.text
    # Both ways in are on the page, as something a thumb can hit.
    assert '<a class="go-btn small" href="/admin/upload">' in page.text
    assert '<a class="go-btn small" href="/admin/arcade">' in page.text


def test_the_room_left_is_the_filesystems_own_answer(ark):
    import ark_health

    room = ark_health.card_room()
    assert room["free_bytes"] and room["total_bytes"]
    # `free` is what can still be written, so it can never exceed the total, and
    # it is not asked to be `total - used` (ext4 keeps a slice back for root).
    assert 0 < room["free_bytes"] <= room["total_bytes"]
    assert room["used_bytes"] <= room["total_bytes"]


def test_a_page_that_could_not_measure_says_so_and_never_says_zero(ark, client, monkeypatch):
    import ark_health

    cookie = _claimed(ark, client)
    monkeypatch.setattr(ark_health, "card_room",
                        lambda: {"free_bytes": None, "used_bytes": None, "total_bytes": None})
    page = _page(client, cookie)
    assert page.status_code == 200
    assert "not measured yet" in page.text
    assert "0 B free" not in page.text
    assert "0% is free" not in page.text


def test_uptime_is_read_from_what_the_machine_says(ark, monkeypatch, tmp_path):
    import ark_health

    said = tmp_path / "uptime"
    said.write_text("90061.42 1234.00\n", encoding="utf-8")
    monkeypatch.setenv("ARK_UPTIME_FILE", str(said))
    assert ark_health.uptime_seconds() == 90061
    assert ark_health.uptime_words(90061) == "1 day, 1 hour"
    assert ark_health.uptime_words(0) == "less than a minute"
    assert ark_health.uptime_words(3600) == "1 hour"
    assert ark_health.uptime_words(59 * 60) == "59 minutes"


def test_a_machine_that_will_not_say_how_long_it_has_been_on_is_not_a_zero(ark, monkeypatch, tmp_path):
    import ark_health

    monkeypatch.setenv("ARK_UPTIME_FILE", str(tmp_path / "nothing-here"))
    assert ark_health.uptime_seconds() is None
    assert ark_health.uptime_words(None) == "not measured yet"


# ---------------------------------------------------------------------------
#  the navigation
# ---------------------------------------------------------------------------
def test_the_navigation_marks_the_page_being_read(ark, client):
    cookie = _claimed(ark, client)
    overview = _page(client, cookie)
    assert '<a href="/admin" aria-current="page">Overview</a>' in overview.text
    # One page is marked, not every page: a reader must not be told they are
    # standing in four places at once.
    assert overview.text.count('aria-current="page"') == 1

    health = _page(client, cookie, "/admin/health")
    assert '<a href="/admin/health" aria-current="page">What is working</a>' in health.text
    assert '<a href="/admin" aria-current="page">' not in health.text


def test_a_page_deeper_inside_a_section_belongs_to_that_section():
    import ark_admin

    assert ark_admin.nav_here("/admin/accounts/create") == "/admin/accounts"
    assert ark_admin.nav_here("/admin/") == "/admin"
    # The sign-in form is not one of these pages, and marking anything for it
    # would point a person at a page they cannot open yet.
    assert ark_admin.nav_here("/admin/login") == ""
    assert ark_admin.nav_here("/admin/network") == "/admin/network"


def test_every_navigation_item_is_a_page_the_application_really_serves():
    import ark_admin

    served = {route.path.rstrip("/") or "/"
              for route in ark_admin.router.routes
              if "GET" in getattr(route, "methods", set())}
    for href, label in ark_admin.ADMIN_NAV:
        assert (href.rstrip("/") or "/") in served, f"{label} ({href}) is a dead link"


def test_every_page_in_the_navigation_is_also_named_in_the_address_list():
    """One page is on offer; the Addresses page says what it is for.

    A page in the navigation and not in ``ark_doors`` is a door a person can open
    and cannot look up — the drift this pair exists to prevent.
    """
    import ark_admin
    import ark_doors

    listed = set(ark_doors.admin_paths())
    missing = sorted(href for href, _label in ark_admin.ADMIN_NAV if href not in listed)
    assert not missing, \
        f"these pages are in the navigation but not on the Addresses page: {missing}"


# ---------------------------------------------------------------------------
#  signing out
# ---------------------------------------------------------------------------
def test_signing_out_is_a_post_with_the_form_token(ark, client):
    cookie = _claimed(ark, client)
    page = _page(client, cookie)
    assert 'action="/admin/logout" method="post"' in page.text
    assert '<button class="go-btn small" type="submit">Sign out</button>' in page.text
    # Never a link: a GET that ends a session can be fired by a picture tag.
    response = get(client, "/admin/logout", cookie=f"ark_admin={cookie}")
    assert response.status_code == 405


def test_signing_out_really_ends_the_session(ark, client):
    cookie = _claimed(ark, client)
    page = _page(client, cookie)
    done = post(client, "/admin/logout", {"csrf": csrf_of(page.text)},
                cookie=f"ark_admin={cookie}")
    assert done.status_code == 303
    assert done.headers["location"].startswith("/admin/login")
    # The cookie is taken away, not merely ignored: a browser that keeps it
    # would find the session already gone on the next page.
    assert 'ark_admin=""' in done.headers.get("set-cookie", "") or \
        "Max-Age=0" in done.headers.get("set-cookie", "")
    after = _page(client, cookie)
    assert after.status_code == 303
    assert after.headers["location"].startswith("/admin/login")
    # And a second press is not an error — it means the person is already out.
    again = post(client, "/admin/logout", {"csrf": csrf_of(page.text)},
                 cookie=f"ark_admin={cookie}")
    assert again.status_code == 303
    assert again.headers["location"].startswith("/admin/login")


def test_a_sign_out_without_the_form_token_leaves_the_session_alone(ark, client):
    """A forged request must gain nothing — signing out is not a GET, and not a
    POST without the page's own token either."""
    cookie = _claimed(ark, client)
    refused = post(client, "/admin/logout", {"csrf": "not-the-token"},
                   cookie=f"ark_admin={cookie}")
    assert refused.status_code == 403
    assert "Nothing was changed" in refused.text
    assert _page(client, cookie).status_code == 200


def test_the_way_back_to_the_reader_pages_uses_the_name_you_typed(ark, client):
    cookie = _claimed(ark, client)
    page = _page(client, cookie, headers={**LAN, "host": "ark.lan"})
    assert '<a href="http://ark.lan/">Open the pages a reader sees</a>' in page.text


# ---------------------------------------------------------------------------
#  every address, in one place
# ---------------------------------------------------------------------------
def test_the_addresses_page_makes_every_reachable_door_a_link(ark, client):
    cookie = _claimed(ark, client)
    page = _page(client, cookie, "/admin/doors", headers={**LAN, "host": "ark.local"})
    assert page.status_code == 200
    # The address a person is most likely to want, built from the name they used.
    assert ('<a href="https://ark.local:8443/admin">https://ark.local:8443/admin</a>'
            in page.text)
    assert '<a href="http://ark.local/">http://ark.local/</a>' in page.text
    # The setup door answers on the box itself and nowhere else, so tapping it
    # from a phone would try the phone: printed in full, never a link.
    assert "https://localhost:8444/admin" in page.text
    assert 'href="https://localhost:8444/admin"' not in page.text


def test_the_address_list_links_nothing_it_did_not_build_the_name_for():
    import ark_admin

    assert ark_admin._door_link("https://{host}:8443/admin", "ark.lan") == \
        '<a href="https://ark.lan:8443/admin">https://ark.lan:8443/admin</a>'
    assert ark_admin._door_link("https://localhost:8444/admin", "ark.lan") == \
        "https://localhost:8444/admin"
    assert ark_admin._door_link("ssh pi@{host}", "ark.lan") == "ssh pi@ark.lan"


def test_the_addresses_page_is_reached_from_the_navigation(ark, client):
    cookie = _claimed(ark, client)
    assert '<a href="/admin/doors">Addresses</a>' in _page(client, cookie).text


# ---------------------------------------------------------------------------
#  and the public pages point the other way
# ---------------------------------------------------------------------------
def test_the_public_pages_say_where_the_owner_pages_are(ark, public):
    """One sentence, on every page a person may read, naming one address."""
    board = public.client.get("/board")
    assert board.status_code == 200
    assert "https://ark.local:8443/admin" in board.text
    assert "they ask for a password" in board.text


def test_the_front_page_the_box_serves_has_the_same_sentence():
    """The front page is a static file nginx serves, so it cannot be forgotten."""
    text = (REPO / "pi/portal/index.html").read_text(encoding="utf-8")
    assert "https://ark.local:8443/admin" in text
    assert "they ask for a password" in text


def test_the_public_footer_still_explains_itself_in_plain_words():
    """No product name, no program name, no word a person has to look up (§5)."""
    text = (REPO / "pi/portal/index.html").read_text(encoding="utf-8")
    footer = text.split("<footer>", 1)[1].split("</footer>", 1)[0]
    for banned in ("Kiwix", "ZIM", "Calibre", "Calibre-Web", "OPDS", "FastAPI",
                   "nginx", "systemd", "SQLite", "APK", "localhost"):
        assert banned not in footer, f"the footer says {banned}"
    assert "password" in footer
