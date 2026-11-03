"""Search must answer inside its budget, even when a collection hangs.

The old code used ``as_completed(..., timeout=12)`` inside a ``with
ThreadPoolExecutor(...)`` block: leaving the block calls ``shutdown(wait=True)``,
so the deadline expired and then the request waited anyway for every worker —
twenty collections at nine seconds each is far past any budget a person will
accept.  The pool is now held open and shared, the wait is bounded, and nothing
unfinished is joined on the way out.

The second half is the same fault one step further on: *which* of the answers
that arrived reach the page.  The block was sorted by collection name and cut at
``LIBRARY_HITS``, so on the card a common word filled every place from the
alphabetically-first six collections and the rest of the library could not appear
at all.  ``_spread()`` takes the best of every collection before the second of
any, and these tests pin that.
"""

from __future__ import annotations

import time

from helpers import LAN


def _books(n: int) -> list[dict]:
    return [{"id": f"b{i:02d}", "title": f"Book {i}", "size": 1} for i in range(n)]


def test_a_hanging_collection_does_not_hold_the_page(public, monkeypatch):
    main = public.app
    monkeypatch.setattr(main, "library_books", lambda: _books(20))
    calls: list[str] = []

    def fake(book_id: str, pattern: str) -> list[dict]:
        calls.append(book_id)
        if book_id == "b00":
            return [{"title": "Water filter", "link": "/kiwix/content/b00/A", "snippet": "boil"}]
        time.sleep(2.0)
        return []

    monkeypatch.setattr(main, "_kiwix_one", fake)
    monkeypatch.setattr(main, "SEARCH_DEADLINE", 0.4)

    started = time.monotonic()
    hits = main.search_library("water")
    elapsed = time.monotonic() - started

    assert elapsed < 1.5, f"search took {elapsed:.1f}s with a hanging collection"
    assert [h["title"] for h in hits] == ["Water filter"]
    assert hits[0]["source"] == "Book 0"
    assert len(calls) <= main.MAX_COLLECTIONS


def test_only_a_bounded_number_of_collections_is_asked(public, monkeypatch):
    main = public.app
    monkeypatch.setattr(main, "library_books", lambda: _books(120))
    asked: list[str] = []

    def fake(book_id: str, pattern: str) -> list[dict]:
        asked.append(book_id)
        return []

    monkeypatch.setattr(main, "_kiwix_one", fake)
    monkeypatch.setattr(main, "SEARCH_DEADLINE", 5.0)
    main.search_library("water")
    assert len(asked) == main.MAX_COLLECTIONS < 120


def test_no_answer_at_all_still_arrives_on_time(public, monkeypatch):
    main = public.app
    monkeypatch.setattr(main, "library_books", lambda: _books(20))
    monkeypatch.setattr(main, "_kiwix_one", lambda book_id, pattern: time.sleep(2.0) or [])
    monkeypatch.setattr(main, "SEARCH_DEADLINE", 0.4)
    started = time.monotonic()
    assert main.search_library("nothing matches") == []
    assert time.monotonic() - started < 1.5


def test_the_pool_is_shared_and_bounded(public):
    main = public.app
    pool = main._search_pool()
    assert pool is main._search_pool(), "one pool for the process, not one per request"
    assert pool._max_workers == main.SEARCH_WORKERS


def test_a_broken_collection_does_not_break_the_page(public, monkeypatch):
    main = public.app
    monkeypatch.setattr(main, "library_books", lambda: _books(3))

    def mostly_dead(book_id: str, pattern: str):
        if book_id == "b00":
            return []                     # one collection really answered
        raise OSError("connection refused")

    monkeypatch.setattr(main, "_kiwix_one", mostly_dead)
    page = public.client.get("/search?q=water", headers=LAN)
    assert page.status_code == 200, "one dead collection must not take the page down"
    # The person still gets an answer, in plain words, with somewhere to go next —
    # and it says the look-up stopped early instead of claiming nothing matched.
    assert "look-up stopped early" in page.text
    assert "Offline Library" in page.text


def test_the_public_search_page_answers_with_nothing_installed(public):
    page = public.client.get("/search?q=water", headers=LAN)
    assert page.status_code == 200
    assert "Search everything" in page.text


# ---------------------------------------------------------------------------
#  the name a collection is asked for by
# ---------------------------------------------------------------------------
def test_a_collection_is_asked_for_by_the_name_of_its_file(public, monkeypatch):
    """A UUID gets an empty feed, and an empty feed looks like "no results"."""
    main = public.app
    stem = "bicycles.stackexchange.com_en_all_2026-10"
    book = {"id": "2c464b36-1ea7-2646-ba6a-3b7504ec4a63", "stem": stem,
            "title": "Bicycles Q&A", "size": 1}
    monkeypatch.setattr(main, "library_books", lambda: [book])
    asked: list[str] = []

    def fake(handle: str, pattern: str) -> list[dict]:
        asked.append(handle)
        return [{"title": "Brakes", "link": f"/kiwix/content/{handle}/Brakes", "snippet": ""}]

    monkeypatch.setattr(main, "_kiwix_one", fake)

    hits = main.search_library("brake")

    assert asked == [stem], "the file name, not the library's UUID"
    assert hits[0]["source"] == "Bicycles Q&A", "a hit names its own collection"
    assert hits[0]["link"] == f"/kiwix/content/{stem}/Brakes"


def test_a_book_without_a_file_name_still_gets_asked(public, monkeypatch):
    """An index we could not read a file name from falls back to the id.

    It may answer nothing, but it must not crash the page or skip the book.
    """
    main = public.app
    monkeypatch.setattr(main, "library_books",
                        lambda: [{"id": "b00", "stem": "", "title": "Book 0", "size": 1}])
    asked: list[str] = []
    monkeypatch.setattr(main, "_kiwix_one", lambda handle, pattern: asked.append(handle) or [])
    main.search_library("water")
    assert asked == ["b00"]


# ---------------------------------------------------------------------------
#  the words a hit carries
# ---------------------------------------------------------------------------
#: A real answer, shaped the way the library really writes one: the matched word is
#: wrapped in ``<b>`` **inside** the description, which is where the old reader stopped.
FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item>
    <title>Water filter</title>
    <link>http://127.0.0.1:8081/kiwix/content/b00/A/Water_filter</link>
    <description>How do you <b>make water</b> safe while touring? Do you carry a filter?</description>
  </item>
</channel></rss>
"""


def test_a_snippet_is_the_words_of_a_hit_not_the_stop_before_the_highlight(public):
    """Every hit on the live box answered with a snippet of exactly ``"..."``.

    ``node.text`` is only the text *before the first child element*, and the library
    wraps the matched word in ``<b>`` — so a perfectly good answer was read as the
    three characters in front of the highlight and nothing else.  ``itertext()`` walks
    into the children, which is what a reader means by "the words in this element".
    """
    hits = public.app._parse_feed(FEED)

    assert len(hits) == 1
    assert hits[0]["snippet"].startswith("How do you make water safe")
    assert "carry a filter" in hits[0]["snippet"], hits[0]["snippet"]


def test_the_highlight_is_not_shown_to_the_reader_as_a_tag(public):
    hits = public.app._parse_feed(FEED)

    assert "<b>" not in hits[0]["snippet"], hits[0]["snippet"]
    assert "make water safe" in hits[0]["snippet"]


def test_a_title_with_a_highlight_in_it_keeps_its_words(public):
    raw = (b'<?xml version="1.0"?><rss><channel><item>'
           b'<title>Water <b>sterilising</b> tablets</title>'
           b'<link>/kiwix/content/b00/A/Tablets</link>'
           b'<description>Two tablets per litre.</description></item></channel></rss>')

    hits = public.app._parse_feed(raw)

    assert hits[0]["title"] == "Water sterilising tablets"


def test_a_description_that_carries_escaped_markup_is_read_as_words(public):
    """The feed escapes HTML twice; the reader must still get sentences, not tags."""
    raw = (b'<?xml version="1.0"?><rss><channel><item>'
           b'<title>Boiling water</title><link>/kiwix/content/b00/A/Boiling</link>'
           b'<description>&lt;p&gt;Bring it to a &lt;b&gt;rolling boil&lt;/b&gt; '
           b'for one minute.&lt;/p&gt;</description></item></channel></rss>')

    hits = public.app._parse_feed(raw)

    assert hits[0]["snippet"] == "Bring it to a rolling boil for one minute."


def test_the_words_a_collection_answers_with_reach_the_page(public, monkeypatch):
    """A snippet nobody can read is the same as no snippet, so prove the whole way."""
    main = public.app
    monkeypatch.setattr(main, "library_books",
                        lambda: [{"id": "b00", "stem": "b00", "title": "Bicycles Q&A",
                                  "size": 1}])
    monkeypatch.setattr(main, "_kiwix_one",
                        lambda handle, pattern: main._parse_feed(FEED))

    page = public.client.get("/search?q=water", headers=LAN)

    assert page.status_code == 200
    assert "Do you carry a filter?" in page.text, "the words a hit carries reach the person"


# ---------------------------------------------------------------------------
#  an answer, and something that only looked like one
# ---------------------------------------------------------------------------
#: What the reader replies when it is asked for a collection it does not have.
#: It is well-formed XML, so a reader that only asks "did this parse?" reads it as
#: a perfectly good answer that happened to hold no items — which is how a wrong
#: collection name came out as a confident "nothing matched" on 2 November 2026.
ERROR_DOC = b'<?xml version="1.0"?><error>Content not found</error>'


def test_a_feed_with_no_hits_is_an_empty_answer_and_not_a_failure(public):
    raw = b'<?xml version="1.0"?><rss version="2.0"><channel><title>Bicycles</title></channel></rss>'

    assert public.app._parse_feed(raw) == [], "the collection answered; it just had nothing"


def test_bytes_that_are_not_xml_are_not_an_answer(public):
    assert public.app._parse_feed(b"<html><body>502 Bad Gateway") is None


def test_the_readers_own_error_document_is_not_an_answer(public):
    assert public.app._parse_feed(ERROR_DOC) is None


def test_an_error_document_carrying_a_namespace_is_still_not_an_answer(public):
    raw = (b'<?xml version="1.0"?>'
           b'<error xmlns="http://www.w3.org/2005/Atom">Content not found</error>')

    assert public.app._parse_feed(raw) is None


def test_a_page_that_parses_but_is_not_a_feed_is_not_an_answer(public):
    """An error page from something in front of the reader parses perfectly well.

    A reader that took *any* parseable root for an answer would report a proxy's
    error page as a collection that answered and found nothing.
    """
    raw = b'<?xml version="1.0"?><html><body><h1>Gone</h1></body></html>'

    assert public.app._parse_feed(raw) is None


#: An Atom shelf: the namespace sits on every element, so the children are
#: ``{http://www.w3.org/2005/Atom}entry`` and an ``<entry>`` test never fires.  The
#: link is an attribute here, which is the other half of the same lesson.
ATOM = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Bicycles</title>
  <entry>
    <title>Brake pads</title>
    <link href="http://127.0.0.1:8081/kiwix/content/b00/A/Brake_pads"/>
    <summary>Replace them when the <b>water</b> groove is gone.</summary>
  </entry>
</feed>
"""


def test_an_atom_shelf_is_read_even_though_every_element_carries_a_namespace(public):
    """A namespace is a label, never a fact about the answer.

    Without the tag stripped, this feed parses, holds one entry, and yields **zero**
    hits — so a real answer is worded as *nothing matched*, which is the failure this
    whole file is about, one namespace away.
    """
    hits = public.app._parse_feed(ATOM)

    assert len(hits) == 1
    assert hits[0]["title"] == "Brake pads"
    assert hits[0]["link"] == "/kiwix/content/b00/A/Brake_pads", "an absolute link becomes our own"
    assert "water groove is gone" in hits[0]["snippet"], hits[0]["snippet"]



# ---------------------------------------------------------------------------
#  a snippet that ends where a person would end it
# ---------------------------------------------------------------------------
def test_a_snippet_short_enough_to_fit_is_shown_whole_with_no_ellipsis(public):
    """``" …"`` is a claim that words were left out, so it is written only when they were."""
    assert public.app.shorten("Boil it for one minute.") == "Boil it for one minute."


def test_a_long_snippet_is_cut_at_a_whole_word_and_says_so(public):
    """``text[:220]`` ended ``…do you carry wat`` on the live box on 1 November 2026.

    A word cut in half reads as a broken page, so the cut lands on the last space
    in the window, and the ellipsis says the rest of the sentence exists.
    """
    text = "gear " * 60 + "and a spare tube."
    body = public.app.shorten(text)

    assert body.endswith(" …"), body
    assert body == text[:len(body) - 2] + " …", "the beginning of the text, not a rewrite"
    assert body[:-2].split(" ")[-1] == "gear", "the last word kept is a whole word"


def test_a_snippet_prefers_a_finished_thought_when_one_lands_near_the_end(public):
    """A whole sentence reads better than a longer phrase that stops in the middle."""
    sentence = "Do you carry water in panniers or on your back? "
    text = sentence * 5 + "More words after that."

    body = public.app.shorten(text)
    word_cut = text[:text.rfind(" ", 0, public.app.SNIPPET_CHARS)]

    assert body.endswith("? …"), body
    assert body == text[:len(body) - 2] + " …"
    assert word_cut.endswith("in"), word_cut       # what the plain word cut would show
    assert body != word_cut + " …", "a finished thought wins over a longer phrase"


def test_a_comma_the_cut_left_dangling_is_not_shown(public):
    """A comma in front of an ellipsis reads as a typo, not as a pause."""
    prefix = "gear " * 41 + "cap "           # 209 characters, ending on a space
    text = prefix + "elephants, " + "and then some more words to fill the window"

    body = public.app.shorten(text)

    assert body.endswith("elephants …"), body
    assert ", …" not in body


def test_a_snippet_with_no_space_to_cut_at_is_still_bounded(public):
    """Never raise, never return more than the window, even with nothing to cut on."""
    body = public.app.shorten("x" * 400)

    assert body == "x" * public.app.SNIPPET_CHARS + " …"


def test_a_long_description_reaches_the_page_cut_at_a_word(public):
    """The cut happens on the way in, so the page never holds a half word either."""
    long_text = "Rinse the filter with clean water and leave it to dry " * 8
    raw = ('<?xml version="1.0"?><rss><channel><item>'
           '<title>Filter care</title><link>/kiwix/content/b00/A/Filter_care</link>'
           f'<description>{long_text}</description></item></channel></rss>').encode()

    body = public.app._parse_feed(raw)[0]["snippet"]

    assert body.endswith(" …"), "the reader is told the answer goes on"
    assert len(body) <= public.app.SNIPPET_CHARS + 2
    assert body[:-2].split(" ")[-1] in long_text.split(" "), "every word kept is whole"



# ---------------------------------------------------------------------------
#  what a search really heard from the library
# ---------------------------------------------------------------------------
def test_an_empty_feed_from_every_collection_is_the_only_empty_answer(public, monkeypatch):
    """``empty`` is the one state allowed to say *nothing matched*, so it is earned."""
    main = public.app
    monkeypatch.setattr(main, "library_books", lambda: _books(3))
    monkeypatch.setattr(main, "_kiwix_one", lambda handle, pattern: [])

    report = main._search_report("water")

    assert report["hits"] == []
    assert (report["asked"], report["answered"]) == (3, 3)
    assert (report["failed"], report["late"]) == (0, 0)
    assert report["available"] == 3
    assert main.library_state(report) == "empty"


def test_a_collection_that_never_answered_is_not_counted_as_an_empty_one(public, monkeypatch):
    """``None`` is the reader saying *that was not an answer* — a gap, not a no."""
    main = public.app
    monkeypatch.setattr(main, "library_books", lambda: _books(3))

    def half_silent(handle, pattern):
        return [] if handle == "b00" else None

    monkeypatch.setattr(main, "_kiwix_one", half_silent)
    report = main._search_report("water")

    assert (report["answered"], report["failed"]) == (1, 2)
    assert main.library_state(report) == "partial"


def test_a_collection_that_raised_is_not_counted_as_having_answered(public, monkeypatch):
    main = public.app
    monkeypatch.setattr(main, "library_books", lambda: _books(2))

    def boom(handle, pattern):
        raise OSError("connection refused")

    monkeypatch.setattr(main, "_kiwix_one", boom)
    report = main._search_report("water")

    assert (report["answered"], report["failed"]) == (0, 2)
    assert main.library_state(report) == "silent"


def test_the_collections_the_deadline_left_behind_are_counted_as_late(public, monkeypatch):
    """Abandoned is not empty: nobody heard from them, so nobody may say they had nothing."""
    main = public.app
    monkeypatch.setattr(main, "library_books", lambda: _books(3))
    monkeypatch.setattr(main, "SEARCH_DEADLINE", 0.2)

    def slow(handle, pattern):
        if handle != "b00":
            time.sleep(1.0)
        return []

    monkeypatch.setattr(main, "_kiwix_one", slow)
    report = main._search_report("water")

    assert report["late"] == 2
    assert (report["asked"], report["answered"]) == (3, 1)
    assert main.library_state(report) == "partial"


def test_a_hit_is_found_whatever_else_the_library_did(public, monkeypatch):
    main = public.app
    monkeypatch.setattr(main, "library_books", lambda: _books(4))

    def one_hit(handle, pattern):
        if handle == "b02":
            return [{"title": "Water filter", "link": "/kiwix/content/b02/A/Water_filter",
                     "snippet": "boil it"}]
        raise OSError("connection refused")

    monkeypatch.setattr(main, "_kiwix_one", one_hit)
    report = main._search_report("water")

    assert main.library_state(report) == "found"
    assert [h["title"] for h in report["hits"]] == ["Water filter"]


def test_a_box_holding_more_books_than_one_search_asks_is_not_an_empty_library(public, monkeypatch):
    """Twelve of twenty asked, and the page said *nothing matched* — the original fault.

    Every book that *was* asked answered, so the other counts alone say ``empty``.
    ``available`` is the only reason they are not believed.
    """
    main = public.app
    monkeypatch.setattr(main, "library_books", lambda: _books(main.MAX_COLLECTIONS + 1))
    asked: list[str] = []
    monkeypatch.setattr(main, "_kiwix_one", lambda handle, pattern: asked.append(handle) or [])

    report = main._search_report("water")

    assert len(asked) == main.MAX_COLLECTIONS
    assert report["asked"] == main.MAX_COLLECTIONS
    assert report["available"] == main.MAX_COLLECTIONS + 1
    assert report["answered"] == report["asked"], "every book that was asked did answer"
    assert main.library_state(report) == "partial", "the library was not wholly looked in"


def test_a_box_with_no_books_is_not_a_search_that_failed(public, monkeypatch):
    main = public.app
    monkeypatch.setattr(main, "library_books", lambda: [])
    monkeypatch.setattr(main, "_kiwix_one", lambda handle, pattern: [])

    report = main._search_report("water")

    assert (report["asked"], report["available"]) == (0, 0)
    assert report["hits"] == []
    assert main.library_state(report) == "no-books"



# ---------------------------------------------------------------------------
#  what the page says about it
# ---------------------------------------------------------------------------
def test_the_page_may_say_nothing_matched_only_when_every_book_answered(public, monkeypatch):
    """The one honest use of *nothing matched*: the whole library was looked in."""
    main = public.app
    monkeypatch.setattr(main, "library_books", lambda: _books(3))
    monkeypatch.setattr(main, "_kiwix_one", lambda handle, pattern: [])

    page = public.client.get("/search?q=water", headers=LAN)

    assert page.status_code == 200
    assert "All 3 collections were asked and none of them had it" in page.text
    assert "look-up stopped early" not in page.text, "nothing stopped early"


def test_the_page_names_how_many_books_answered_when_some_did_not(public, monkeypatch):
    """A gap in the search, worded as a gap — amber, with a way to try again.

    This is the sentence the live box never had: it said *Nothing matched* about a
    look-up that had only heard from one of three collections.
    """
    main = public.app
    monkeypatch.setattr(main, "library_books", lambda: _books(3))
    monkeypatch.setattr(main, "_kiwix_one",
                        lambda handle, pattern: [] if handle == "b00" else None)

    page = public.client.get("/search?q=water", headers=LAN)

    assert "look-up stopped early" in page.text
    assert "Only 1 of 3 collections answered in time" in page.text, page.text
    assert 'class="dot wait"' in page.text, "amber: not a fault, just unfinished"
    assert 'class="dot no"' not in page.text
    assert "Nothing matched" not in page.text, "the sentence this fault was made of"


def test_the_page_says_which_books_it_never_asked(public, monkeypatch):
    """A cap is not the library, so the sentence names the cap and the library both."""
    main = public.app
    monkeypatch.setattr(main, "library_books", lambda: _books(main.MAX_COLLECTIONS + 1))
    monkeypatch.setattr(main, "_kiwix_one", lambda handle, pattern: [])

    page = public.client.get("/search?q=water", headers=LAN)

    assert (f"Only the first {main.MAX_COLLECTIONS} of the box’s "
            f"{main.MAX_COLLECTIONS + 1} collections were looked in") in page.text, page.text
    assert "answered in time" not in page.text, "every book that was asked did answer"
    assert (f"All {main.MAX_COLLECTIONS} collections were asked") not in page.text


def test_a_library_that_answered_nothing_is_worded_as_a_fault_not_as_an_answer(public, monkeypatch):
    main = public.app
    monkeypatch.setattr(main, "library_books", lambda: _books(4))

    def boom(handle, pattern):
        raise OSError("connection refused")

    monkeypatch.setattr(main, "_kiwix_one", boom)
    page = public.client.get("/search?q=water", headers=LAN)

    assert "The big library could not be looked in just now" in page.text
    assert "That is not the same as “nothing found”" in page.text
    assert 'class="dot no"' in page.text, "red: nothing answered at all"
    assert "Nothing matched" not in page.text


def test_a_box_with_no_books_says_so_instead_of_blaming_the_search(public, monkeypatch):
    """An empty shelf is not a broken look-up, and it is not "nothing matched" either."""
    main = public.app
    monkeypatch.setattr(main, "library_books", lambda: [])
    monkeypatch.setattr(main, "_kiwix_one", lambda handle, pattern: [])

    page = public.client.get("/search?q=water", headers=LAN)

    assert "The Offline Library has no list of its books yet" in page.text
    assert "The manuals and the bookshelf were still searched" in page.text
    assert 'class="dot wait"' in page.text, "amber: nothing is wrong, there is nothing to read"
    assert 'class="dot no"' not in page.text, "an empty shelf is not a failed look-up"
    assert "Nothing matched" not in page.text


def test_answers_from_the_manuals_do_not_hide_a_library_that_never_answered(public, monkeypatch):
    """A tidy list must not paper over a failed look-up, so the note travels with it."""
    main = public.app
    monkeypatch.setattr(main, "library_books", lambda: _books(2))
    monkeypatch.setattr(main, "_kiwix_one", lambda handle, pattern: None)
    monkeypatch.setattr(main, "search_guides",
                        lambda pattern: [{"title": "Boiling water", "link": "/vault/water.html",
                                          "snippet": "", "source": "Manuals"}])

    page = public.client.get("/search?q=water", headers=LAN)

    assert "Boiling water" in page.text, "the manual page is still offered"
    assert "The big library could not be looked in just now" in page.text
    assert "none of the 2 collections" in page.text
    assert "everything above comes from the manuals and the bookshelf" in page.text


def test_answers_from_the_manuals_say_that_the_library_half_is_missing(public, monkeypatch):
    """The same branch one state over: answers found, and the search stopped early."""
    main = public.app
    monkeypatch.setattr(main, "library_books", lambda: _books(3))
    monkeypatch.setattr(main, "_kiwix_one",
                        lambda handle, pattern: [] if handle == "b00" else None)
    monkeypatch.setattr(main, "search_guides",
                        lambda pattern: [{"title": "Boiling water", "link": "/vault/water.html",
                                          "snippet": "", "source": "Manuals"}])

    page = public.client.get("/search?q=water", headers=LAN)

    assert "Boiling water" in page.text
    assert "Only 1 of 3 collections answered in time" in page.text
    assert "may hold more" in page.text, "why the library half is missing"
    assert "Look again" in page.text, "and what to do about it"
    assert "nothing to read" not in page.text, "the shelf was not empty — the look-up was"



def test_the_block_is_long_enough_to_show_every_collection_it_asked(public):
    """A block shorter than the library is a sample of the alphabet, not the shelf.

    ``LIBRARY_HITS`` places cannot name more collections than it has places, so the
    two numbers are tied: if a future card holds more books than the block has
    places, the block must grow with it or the search goes back to answering from
    the front of an alphabetical list.
    """
    main = public.app
    assert main.LIBRARY_HITS >= main.MAX_COLLECTIONS, \
        "a block of 24 cannot show a card of 30 collections"
    assert main.HITS_PER_COLLECTION >= 1


class _Reply:
    """Just enough of an ``http.client.HTTPResponse`` for ``_kiwix_one``."""

    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, amount: int | None = None) -> bytes:
        return self.body


def _feed(count: int, handle: str = "b00") -> bytes:
    """An answer in the shape the library really sends: an RSS channel of items."""
    items = "".join(
        f"<item><title>Water {i}</title>"
        f"<link>http://127.0.0.1:8081/content/{handle}/A/water-{i}</link>"
        f"<description>boil it</description></item>"
        for i in range(count))
    return f"<rss version='2.0'><channel>{items}</channel></rss>".encode()


def test_one_collection_may_only_fill_its_share_of_the_block(public, monkeypatch):
    """The per-collection ceiling is applied to the answer, not to the page.

    A collection that returns forty articles about *water* must not be allowed to
    crowd out the other nineteen, so the cut belongs where the bytes arrive.
    """
    main = public.app
    monkeypatch.setattr(main.urllib.request, "urlopen",
                        lambda req, timeout=None: _Reply(_feed(9)))
    found = main._kiwix_one("b00", "water")
    assert len(found) == main.HITS_PER_COLLECTION < 9


def test_a_search_shows_a_hit_from_every_collection_before_a_second_from_any(public, monkeypatch):
    """This is the defect: *water* came back from six collections out of twenty.

    Twenty collections, four answers each — eighty hits for twenty-four places.
    Every collection must be named before any collection is named twice, which is
    also why ``"Book 19"`` (last in an alphabetical sort) has to be on the page.
    """
    main = public.app
    monkeypatch.setattr(main, "library_books", lambda: _books(20))
    monkeypatch.setattr(main, "_kiwix_one", lambda handle, pattern: [
        {"title": f"Water {i}", "link": f"/kiwix/content/{handle}/A/w{i}", "snippet": ""}
        for i in range(main.HITS_PER_COLLECTION)])

    hits = main.search_library("water")

    assert len(hits) == main.LIBRARY_HITS, "the block is still exactly its own length"
    assert {h["source"] for h in hits} == {f"Book {i}" for i in range(20)}, \
        "every collection on the card is named, not just the alphabetically-first six"
    assert "Book 19" in {h["source"] for h in hits}


def test_a_library_where_only_three_collections_answered_is_not_padded(public, monkeypatch):
    """A spread may never invent a hit, and never lengthen the block.

    Three collections with something to say still show all three of their answers
    and nothing else — no empty row, no filler from a collection that had nothing.
    """
    main = public.app
    monkeypatch.setattr(main, "library_books", lambda: _books(20))
    answered = {"b00", "b05", "b19"}
    monkeypatch.setattr(main, "_kiwix_one",
                        lambda handle, pattern: [{"title": f"Water in {handle}",
                                                  "link": f"/kiwix/content/{handle}/A/x",
                                                  "snippet": ""}]
                        if handle in answered else [])

    hits = main.search_library("water")

    assert len(hits) == 3
    assert [h["source"] for h in hits] == ["Book 0", "Book 19", "Book 5"], \
        "each answer travels, and they are ordered by collection for reading"


def test_the_block_still_reads_grouped_by_collection(public, monkeypatch):
    """Spreading picks the hits; the page still shows them grouped.

    A reader decides whose opinion they are looking at by the collection heading, so
    the chosen hits are sorted back into collection order rather than left in the
    round-robin sequence that chose them.
    """
    main = public.app
    monkeypatch.setattr(main, "library_books", lambda: _books(20))
    monkeypatch.setattr(main, "_kiwix_one", lambda handle, pattern: [
        {"title": f"Water {i}", "link": f"/kiwix/content/{handle}/A/w{i}", "snippet": ""}
        for i in range(main.HITS_PER_COLLECTION)])

    keys = [(h["source"], h["title"]) for h in main.search_library("water")]

    assert keys == sorted(keys), "the block reads collection by collection"
    seen: list[str] = []
    for source, _title in keys:
        if not seen or seen[-1] != source:
            seen.append(source)
    assert len(seen) == len(set(seen)), "one collection is one group, never two"

