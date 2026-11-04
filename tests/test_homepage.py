"""The front page: what it claims, what it says when it cannot check, and daylight.

The reviewed behaviour was two lies in a row.

* The static HTML said **"Ready."** in plain text, so a phone with the backend
  dead read a green dot and a promise that nothing had verified.
*  **"Search everything"** was on a box that does not search films or games.

And one honest omission: a `Today / Together` plan had nowhere to be announced
except a message somebody had to scroll to find.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from helpers import LAN

PORTAL = Path(__file__).resolve().parents[1] / "pi" / "portal"


def test_the_static_page_never_claims_to_be_ready_by_itself():
    html = (PORTAL / "index.html").read_text(encoding="utf-8")
    assert "<b>Ready.</b> Tap anything below" not in html, (
        "the front page must not promise readiness before anything checked")
    assert "Checking what is on the box" in html
    # The words that make the claim are still there — but only from the code
    # path that has just read /api/status.
    assert "return s.ok ? s.json() : null" not in html
    assert 'fetch("/api/status"' in html


def test_the_static_page_says_what_still_works_when_the_backend_is_gone():
    html = (PORTAL / "index.html").read_text(encoding="utf-8")
    assert "The box is not answering its own status check" in html
    assert "should still open" in html


def test_the_front_page_has_a_place_for_practical_help_reading_and_company():
    html = (PORTAL / "index.html").read_text(encoding="utf-8")
    for heading in ("If something is wrong", "Read, watch, play", "For everybody here"):
        assert heading in html, heading
    # Enjoyment is named as a purpose, not an afterthought.
    assert "Learning and enjoyment are not an afterthought" in html
    assert "as much as a burst pipe is" in html


def test_the_search_button_names_what_it_actually_searches():
    html = (PORTAL / "index.html").read_text(encoding="utf-8")
    assert "Search the library, guides and books" in html
    assert "Search everything" not in html
    assert "does not look inside the films or the games" in html
    # And it says where to go instead.
    assert 'href="/media/"' in html and 'href="/arcade/"' in html


def test_the_page_explains_where_progress_is_kept():
    html = (PORTAL / "index.html").read_text(encoding="utf-8")
    assert "Where your place is kept" in html
    assert "this browser, on this device" in html
    assert "the box does not keep a watch history" in html
    assert "under a player name you choose" in html


def test_a_light_presentation_exists_and_is_offered():
    css = (PORTAL / "assets" / "ark.css").read_text(encoding="utf-8")
    assert "html.light{" in css
    # The same targets and the same focus ring: a reading variant, not a second design.
    assert "html.light a:focus-visible" in css
    html = (PORTAL / "index.html").read_text(encoding="utf-8")
    assert "/theme/light" in html
    assert "easier to read in daylight" in html


def test_the_theme_route_sets_a_cookie_and_returns_the_reader(public):
    response = public.client.get("/theme/light?back=/search", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/search"
    cookie = response.headers.get("set-cookie", "")
    assert "ark_theme=light" in cookie
    assert "Max-Age=" in cookie

    back = public.client.get("/theme/dark?back=/board", follow_redirects=False)
    assert back.status_code == 303
    assert "ark_theme=dark" in back.headers.get("set-cookie", "")


def test_the_theme_route_refuses_to_send_anybody_off_the_box(public):
    """`back` is a redirect target, so it is checked, not trusted."""
    outside = public.client.get("/theme/light?back=https://example.org/",
                                follow_redirects=False)
    assert outside.status_code == 303
    assert outside.headers["location"] == "/"

    tricky = public.client.get("/theme/light?back=//example.org/", follow_redirects=False)
    assert tricky.headers["location"] == "/"

    nonsense = public.client.get("/theme/purple?back=/board", follow_redirects=False)
    assert nonsense.status_code == 303
    assert nonsense.headers["location"] == "/board"
    assert "ark_theme=" not in nonsense.headers.get("set-cookie", "")


def test_a_page_renders_in_the_chosen_colours(public):
    dark = public.client.get("/board", headers=LAN)
    assert 'class="dark"' in dark.text
    light = public.client.get("/board?theme=light", headers=LAN)
    assert 'class="light"' in light.text
    assert 'body class="theme-light"' in light.text
    # The way back to the other choice is always on the page.
    assert "/theme/dark" in light.text


# ---------------------------------------------------------------------------
#  Today / Together — the owner's pinned note, reused, never invented
# ---------------------------------------------------------------------------
def _pin(ark, body: str, *, name: str = "Sam", hours_ago: float = 1.0,
         resolved: int = 0, hidden: int = 0):
    import ark_db
    when = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    with ark_db.tx() as conn:
        cursor = conn.execute(
            "INSERT INTO notes (ts, name, body, pinned, resolved, hidden)"
            " VALUES (?, ?, ?, 1, ?, ?)",
            (when.isoformat(timespec="seconds"), name, body, resolved, hidden))
        return int(cursor.lastrowid)


def test_there_is_no_panel_when_nothing_is_pinned(public):
    body = public.client.get("/api/today").json()
    assert body == {"present": False}


def test_a_pinned_note_is_the_plan(public, ark):
    _pin(ark, "Film at eight in the front room. Bring something to sit on.", name="Sam")
    body = public.client.get("/api/today").json()
    assert body["present"] is True
    assert body["body"].startswith("Film at eight")
    assert body["name"] == "Sam"
    assert body["stale"] is False
    assert body["when"]


def test_a_hidden_or_sorted_plan_is_not_shown(public, ark):
    _pin(ark, "A cancelled evening", hidden=1)
    assert public.client.get("/api/today").json() == {"present": False}
    _pin(ark, "An evening that already happened", resolved=1)
    assert public.client.get("/api/today").json() == {"present": False}


def test_an_old_plan_is_shown_with_its_age(public, ark):
    """Honest about a stale plan: the box cannot know it was cancelled."""
    _pin(ark, "Games night on Tuesday", hours_ago=24 * 4)
    body = public.client.get("/api/today").json()
    assert body["present"] is True
    assert body["stale"] is True
    assert body["age_hours"] >= 36


def test_the_panel_never_reveals_household_answers(public, ark):
    """The plan comes from a public note, not from the household page."""
    import ark_db
    with ark_db.tx() as conn:
        ark_db.set_setting(conn, "household_water", "the stop tap is under the sink")
    _pin(ark, "Supper at seven")
    body = public.client.get("/api/today").json()
    assert "stop tap" not in str(body)


def test_the_front_page_hides_the_panel_by_default():
    html = (PORTAL / "index.html").read_text(encoding="utf-8")
    assert 'id="today"' in html
    assert "hidden aria-labelledby=\"today-heading\"" in html.replace(" ", " ")
    assert 'fetch("/api/today"' in html
    # A plan that is not today's must not read as though it were.
    assert "it may already have happened" in html
