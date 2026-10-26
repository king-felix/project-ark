"""Stage 7 — the page somebody opens when they are frightened, and the cards by the door.

``test_household.py`` proves what the page *holds*; this file proves what a browser
*sees*, and the one thing the box must never do on that page: invent something.

Three separate promises are checked here:

* **the review never guesses** — an emergency topic nobody has read says so, a date it
  cannot read counts as due rather than as zero, an unknown topic is refused, and a
  corrupt row is an empty record rather than a 500;
* **the public page keeps working with nothing plugged in** — the household's own words,
  the pages really on the card, and one link that carries them away (and a friendly
  notice, not a broken link, when no pack has been made);
* **the cards print** — inline SVG squares, no image file, no font, no script, no
  network, and the address in plain letters underneath for a dead phone.
"""

from __future__ import annotations

import datetime
import html

import pytest

REVIEW_KEY = "emergency_review"


@pytest.fixture()
def review(ark):
    """The module, imported after the throwaway box exists (it binds ``ark_db``)."""
    import ark_review
    return ark_review


@pytest.fixture()
def owner(client):
    """A claimed box and a signed-in owner."""
    from helpers import claim_box
    return claim_box(client)


def _vault(ark):
    """The folder the box keeps its manuals in, made ready for a page or two."""
    import ark_health
    root = ark_health.vault_dir()
    (root / "manuals").mkdir(parents=True, exist_ok=True)
    (root / "guides").mkdir(parents=True, exist_ok=True)
    return root


def _page(root, name: str, title: str = "A page") -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"<!doctype html><html><head><title>{title}</title></head>"
                    f"<body><p>{title}</p></body></html>", encoding="utf-8")


def _raw(key: str) -> str:
    import ark_db
    with ark_db.read() as conn:
        return ark_db.get_setting(conn, key, "")


def _put(key: str, value: str) -> None:
    import ark_db
    with ark_db.tx() as conn:
        ark_db.set_setting(conn, key, value)



# ---------------------------------------------------------------------------
#  the review: a record of the look, never a judgement about the words
# ---------------------------------------------------------------------------
def test_a_topic_nobody_has_read_says_so_and_is_due(ark, review):
    rows = review.summary(_vault(ark))
    assert [row["key"] for row in rows] == [topic[0] for topic in review.TOPICS]
    for row in rows:
        assert row["checked"] == ""
        assert row["days"] is None            # not measured, never 0
        assert row["due"] is True             # a page nobody read is the one to read
        assert row["words"] == "Nobody has read this yet."
        assert row["covered"] is False        # nothing on the card, and it says so


def test_a_page_on_the_card_is_listed_and_a_missing_one_is_named(ark, review):
    root = _vault(ark)
    _page(root, "manuals/first-aid.html", "First aid")
    rows = {row["key"]: row for row in review.summary(root)}
    assert rows["breathing"]["covered"] is True
    assert rows["breathing"]["pages"] == ["manuals/first-aid.html"]
    assert rows["breathing"]["missing"] == []
    assert rows["water"]["covered"] is False
    assert rows["water"]["missing"] == ["manuals/water.html"]


def test_every_topic_names_a_page_that_is_really_on_the_card(review):
    """A topic may not point at a file the box does not carry.

    The list in ``ark_review.TOPICS`` is the claim the household page makes about
    what this box covers.  A typo there is invisible at run time — the page simply
    says *"no page for this yet"* about a page that has been on the card all along
    — so the claim is checked against the shipped vault instead of trusted.
    """
    from pathlib import Path
    vault = Path(__file__).resolve().parents[1] / "pi" / "vault"
    for key, name, asks, pages in review.TOPICS:
        assert asks.endswith("."), f"{key} does not say what the page must answer"
        assert pages, f"{key} names no page at all"
        for page in pages:
            assert (vault / page).is_file(), f"{key} names {page}, which is not in pi/vault"


def test_reading_a_page_today_records_who_and_what_needed_changing(ark, review):
    kept = review.record("water", who="Sam", note="  the   number  changed ")
    assert kept["on"] == datetime.date.today().isoformat()
    assert kept["by"] == "Sam"
    assert kept["note"] == "the number changed"
    row = {r["key"]: r for r in review.summary(_vault(ark))}["water"]
    assert row["checked"] == kept["on"]
    assert row["days"] == 0
    assert row["due"] is False
    assert row["words"] == "Somebody here read this today."


def test_a_topic_this_box_does_not_keep_is_refused_not_invented(ark, review):
    with pytest.raises(review.UnknownTopic):
        review.record("lightning", who="Sam")
    with pytest.raises(review.UnknownTopic):
        review.forget("lightning")
    assert _raw(REVIEW_KEY) == ""


def test_a_date_that_has_not_happened_yet_is_refused(ark, review):
    tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
    with pytest.raises(ValueError):
        review.record("water", when=tomorrow)
    assert _raw(REVIEW_KEY) == ""


def test_a_date_nobody_can_read_counts_as_due_never_as_today(review):
    assert review.days_since("") is None
    assert review.days_since("the other day") is None
    assert review.days_since("2026-10-01", today=datetime.date(2026, 10, 25)) == 24
    # A clock that went backwards is "today", not a negative age.
    assert review.days_since("2026-10-25", today=datetime.date(2026, 10, 1)) == 0


def test_a_corrupt_row_is_an_empty_record_and_survives_being_written_again(ark, review):
    _put(REVIEW_KEY, "not json at all")
    assert review.load() == {"checked": {}, "updated": ""}
    assert {r["words"] for r in review.summary(_vault(ark))} == {"Nobody has read this yet."}
    review.record("water")
    assert list(review.load()["checked"]) == ["water"]


def test_a_row_with_a_topic_this_box_dropped_keeps_the_rest(ark, review):
    _put(REVIEW_KEY, ('{"checked": {"water": {"on": "2026-10-01", "by": "Sam"},'
                      ' "lightning": {"on": "2026-10-02"},'
                      ' "fire": {"on": "not a date"}}, "updated": "x"}'))
    stored = review.load()
    assert list(stored["checked"]) == ["water"]     # the unknown key and the bad date go
    assert stored["updated"] == "x"


def test_taking_a_look_back_leaves_every_other_topic_alone(ark, review):
    review.record("water", who="Sam")
    review.record("fire", who="Ada")
    review.forget("water")
    assert list(review.load()["checked"]) == ["fire"]
    assert review.status(_vault(ark))["read"] == 1


def test_the_status_counts_what_is_there_and_never_decides_readiness(ark, review):
    root = _vault(ark)
    _page(root, "manuals/first-aid.html")
    review.record("breathing")
    status = review.status(root)
    assert status["total"] == len(review.TOPICS)
    assert status["covered"] == 1
    assert status["read"] == 1
    assert status["due"] == len(review.TOPICS) - 1
    assert "manuals/water.html" in status["missing_pages"]



# ---------------------------------------------------------------------------
#  the public page: what the household wrote, and the pages really on the card
# ---------------------------------------------------------------------------
def test_the_household_page_is_reachable_from_every_page_and_says_nothing_it_was_not_told(public):
    other = public.client.get("/board")
    assert other.status_code == 200
    assert '<a href="/household">Household</a>' in other.text   # in the navigation

    page = public.client.get("/household")
    assert page.status_code == 200
    assert '<a href="/household" aria-current="page">Household</a>' in page.text
    assert "Nobody has written this page yet" in page.text
    assert "No emergency pages are on the box yet." in page.text
    assert "There is no copy to carry yet." in page.text
    # A blank box offers no download rather than a link that would 404.
    assert 'href="/essentials.zip"' not in page.text


def test_the_page_shows_what_a_person_typed_escaped_and_in_its_own_order(ark, public):
    import ark_household
    ark_household.save({"people": "Ada and <b>Bo</b>", "water": "under the sink"})
    text = public.client.get("/household").text
    assert "Ada and &lt;b&gt;Bo&lt;/b&gt;" in text
    assert "<b>Bo</b>" not in text
    assert text.index("Who lives here") < text.index("Where the water stops")
    assert "Last written here" in text


def test_the_page_lists_the_emergency_pages_that_are_really_there(ark, public):
    root = _vault(ark)
    _page(root, "manuals/water.html", "Water")
    text = public.client.get("/household").text
    assert '<a href="/vault/manuals/water.html">Water you can drink</a>' in text
    assert "No page on this box yet for:" in text
    assert "Fire in the house" in text          # named as missing, not invented
    assert '<a href="/vault/manuals/fire.html">' not in text


def test_the_switch_turns_the_page_off_and_leaves_every_other_page_alone(ark, public):
    _put("public_household", "0")
    for path in ("/household", "/cards"):
        off = public.client.get(path)
        assert off.status_code == 404, path
        assert "switched off" in off.text
    for path in ("/board", "/files", "/search"):
        assert public.client.get(path).status_code == 200, path


# ---------------------------------------------------------------------------
#  the cards: a square a dead phone camera can read, and the address in letters
# ---------------------------------------------------------------------------
CARDS = (("http://ark.lan", "On the box's own Wi-Fi"),
         ("http://ark.local", "On a home network"),
         ("http://10.42.0.1", "If the name does not work"))


def test_the_cards_are_inline_pictures_with_the_address_written_out(public):
    page = public.client.get("/cards")
    assert page.status_code == 200
    for address, when in CARDS:
        assert f"<h2>{html.escape(when)}</h2>" in page.text
        assert f'<p class="wall-address">{address}</p>' in page.text
    assert page.text.count('<article class="wall-card">') == 3
    assert page.text.count("<svg") == 3


def test_a_card_needs_no_file_a_font_a_script_or_a_network(public):
    text = public.client.get("/cards").text
    for pattern in ('<img', 'src="http', 'href="http', "url(http", "<script",
                    "@import", "@font-face", "javascript:"):
        assert pattern not in text, pattern
    # The one stylesheet is the box's own, and there is no other link out.
    assert '<link rel="stylesheet" href="/assets/ark.css">' in text


def test_a_square_is_a_named_picture_so_a_screen_reader_can_read_it(public):
    text = public.client.get("/cards").text
    assert 'role="img"' in text
    assert 'aria-label="http://ark.lan"' in text


# ---------------------------------------------------------------------------
#  the one file to carry away
# ---------------------------------------------------------------------------
def test_a_pack_that_was_never_made_is_a_sentence_not_a_broken_link(public):
    answer = public.client.get("/essentials.zip")
    assert answer.status_code == 404
    assert "There is no copy to download yet." in answer.text


def test_a_pack_that_was_made_is_offered_as_one_file_with_no_path_from_the_request(ark, public):
    import ark_essentials
    root = _vault(ark)
    _page(root, "manuals/water.html", "Water")
    made = ark_essentials.build(ark_essentials.pack_dir(), vault=root)
    answer = public.client.get("/essentials.zip")
    assert answer.status_code == 200
    assert answer.headers["content-type"] == "application/zip"
    assert "ark-essentials.zip" in answer.headers["content-disposition"]
    assert answer.headers["x-content-type-options"] == "nosniff"
    assert len(answer.content) == made["bytes"]
    assert answer.content[:2] == b"PK"                     # really a zip, byte for byte
    # The page links to it because a copy really is there.
    assert 'href="/essentials.zip"' in public.client.get("/household").text


def test_a_reader_may_ask_how_big_it_is_before_downloading_it(ark, public):
    import ark_essentials
    root = _vault(ark)
    _page(root, "manuals/water.html", "Water")
    made = ark_essentials.build(ark_essentials.pack_dir(), vault=root)
    head = public.client.head("/essentials.zip")
    assert head.status_code == 200
    assert int(head.headers["content-length"]) == made["bytes"]
    assert head.content == b""


def test_a_damaged_pack_is_not_offered(ark, public):
    import ark_essentials
    root = _vault(ark)
    _page(root, "manuals/water.html", "Water")
    ark_essentials.build(ark_essentials.pack_dir(), vault=root)
    ark_essentials.pack_path(ark_essentials.pack_dir()).write_bytes(b"not a zip")
    text = public.client.get("/household").text
    assert "looks damaged, so it is not offered" in text
    assert 'href="/essentials.zip"' not in text


# ---------------------------------------------------------------------------
#  the owner's half
# ---------------------------------------------------------------------------
def test_the_owners_page_asks_for_a_sign_in_like_every_other(ark, client):
    from helpers import get
    answer = get(client, "/admin/household")
    assert answer.status_code == 303
    assert "/admin/login" in answer.headers["location"]


def test_the_owners_page_says_nobody_has_read_anything_yet(client, owner):
    from helpers import get
    page = get(client, "/admin/household", cookie=f"ark_admin={owner}")
    assert page.status_code == 200
    assert "Nobody has read this yet." in page.text
    assert page.text.count("Somebody read this today") == 8     # one button per topic
    assert "There is no copy to carry yet." in page.text
    assert "0 of 9 answered" in page.text


def test_saving_an_answer_puts_it_on_both_pages_and_in_the_history(public, client, owner):
    from helpers import csrf_of, get, post
    page = get(client, "/admin/household", cookie=f"ark_admin={owner}")
    saved = post(client, "/admin/household/save",
                 {"people": "Ada and Bo", "water": "under the sink",
                  "csrf": csrf_of(page.text)}, cookie=f"ark_admin={owner}")
    assert saved.status_code == 303
    assert saved.headers["location"].startswith("/admin/household")
    assert "Ada and Bo" in public.client.get("/household").text
    history = get(client, "/admin/audit", cookie=f"ark_admin={owner}")
    assert "household-save" in history.text


def test_an_answer_too_long_is_refused_with_the_limit_named(client, owner):
    from helpers import csrf_of, get, post
    page = get(client, "/admin/household", cookie=f"ark_admin={owner}")
    answer = post(client, "/admin/household/save",
                  {"power": "x" * 5000, "csrf": csrf_of(page.text)},
                  cookie=f"ark_admin={owner}")
    assert answer.status_code == 400
    assert "Not saved." in answer.text
    assert "300" in answer.text                       # the limit, said out loud
    assert "Ada" not in answer.text
    unchanged = get(client, "/admin/household", cookie=f"ark_admin={owner}")
    assert "0 of 9 answered" in unchanged.text


def test_the_switch_turns_the_page_off_and_back_on(public, client, owner):
    from helpers import csrf_of, get, post
    page = get(client, "/admin/household", cookie=f"ark_admin={owner}")
    off = post(client, "/admin/household/switch", {"csrf": csrf_of(page.text)},
               cookie=f"ark_admin={owner}")
    assert off.status_code == 303
    assert public.client.get("/household").status_code == 404
    off_page = get(client, "/admin/household", cookie=f"ark_admin={owner}")
    on = post(client, "/admin/household/switch",
              {"household": "1", "csrf": csrf_of(off_page.text)},
              cookie=f"ark_admin={owner}")
    assert on.status_code == 303
    assert public.client.get("/household").status_code == 200



def test_a_person_says_they_read_a_page_and_it_shows_on_the_public_page(public, client, owner, ark):
    from helpers import csrf_of, get, post
    _page(_vault(ark), "manuals/water.html", "Water")
    page = get(client, "/admin/household", cookie=f"ark_admin={owner}")
    read = post(client, "/admin/household/read/water",
                {"note": "the number had changed", "csrf": csrf_of(page.text)},
                cookie=f"ark_admin={owner}")
    assert read.status_code == 303
    shown = get(client, "/admin/household", cookie=f"ark_admin={owner}")
    assert "Somebody here read this today." in shown.text
    assert "the number had changed" in shown.text
    assert "Take that back" in shown.text
    public_text = public.client.get("/household").text
    assert "Somebody here read this today." in public_text
    assert "the number had changed" not in public_text      # a note is the owner's own


def test_a_topic_this_box_does_not_keep_is_refused_at_the_door(client, owner):
    from helpers import csrf_of, get, post
    page = get(client, "/admin/household", cookie=f"ark_admin={owner}")
    answer = post(client, "/admin/household/read/lightning", {"csrf": csrf_of(page.text)},
                  cookie=f"ark_admin={owner}")
    assert answer.status_code == 400
    assert "not a page this box keeps" in answer.text
    assert _raw(REVIEW_KEY) == ""


def test_taking_a_look_back_is_recorded_and_audited(client, owner):
    from helpers import csrf_of, get, post
    page = get(client, "/admin/household", cookie=f"ark_admin={owner}")
    post(client, "/admin/household/read/fire", {"csrf": csrf_of(page.text)},
         cookie=f"ark_admin={owner}")
    again = get(client, "/admin/household", cookie=f"ark_admin={owner}")
    back = post(client, "/admin/household/forget/fire", {"csrf": csrf_of(again.text)},
                cookie=f"ark_admin={owner}")
    assert back.status_code == 303
    assert "Nobody has read this yet." in get(
        client, "/admin/household", cookie=f"ark_admin={owner}").text
    history = get(client, "/admin/audit", cookie=f"ark_admin={owner}")
    assert "household-read" in history.text
    assert "household-forget" in history.text


def test_making_the_copy_needs_something_to_copy_and_says_so(client, owner):
    from helpers import csrf_of, get, post
    page = get(client, "/admin/household", cookie=f"ark_admin={owner}")
    answer = post(client, "/admin/household/pack", {"csrf": csrf_of(page.text)},
                  cookie=f"ark_admin={owner}")
    assert answer.status_code == 400
    assert "No copy was made." in answer.text
    assert "Nothing else has changed." in answer.text


def test_making_the_copy_writes_it_proves_it_and_then_offers_it(public, client, owner, ark):
    from helpers import csrf_of, get, post
    import ark_essentials
    root = _vault(ark)
    _page(root, "manuals/water.html", "Water")
    page = get(client, "/admin/household", cookie=f"ark_admin={owner}")
    made = post(client, "/admin/household/pack", {"csrf": csrf_of(page.text)},
                cookie=f"ark_admin={owner}")
    assert made.status_code == 303
    pack = ark_essentials.pack_path(ark_essentials.pack_dir())
    assert pack.is_file()
    whole, problems = ark_essentials.verify(pack)
    assert whole, problems
    assert "Archive" not in made.text                      # no jargon in the answer
    after = get(client, "/admin/household", cookie=f"ark_admin={owner}")
    assert "A copy is on the box:" in after.text
    assert 'href="/essentials.zip"' in after.text
    assert public.client.get("/essentials.zip").status_code == 200
    history = get(client, "/admin/audit", cookie=f"ark_admin={owner}")
    assert "household-pack" in history.text


def test_a_damaged_copy_is_reported_as_damaged_rather_than_offered(client, owner, ark, monkeypatch):
    from helpers import csrf_of, get, post
    import ark_essentials
    root = _vault(ark)
    _page(root, "manuals/water.html", "Water")
    monkeypatch.setattr(ark_essentials, "verify", lambda path: (False, ["two pages differ"]))
    page = get(client, "/admin/household", cookie=f"ark_admin={owner}")
    answer = post(client, "/admin/household/pack", {"csrf": csrf_of(page.text)},
                  cookie=f"ark_admin={owner}")
    assert answer.status_code == 400
    assert "does not read back whole" in answer.text
    assert "two pages differ" in answer.text


def test_none_of_this_needs_a_password_from_a_reader(public):
    """The household page is the page somebody opens when the box is all they have."""
    for path in ("/household", "/cards"):
        answer = public.client.get(path)
        assert answer.status_code == 200, path
        assert "sign in" not in answer.text.lower(), path
