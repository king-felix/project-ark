"""Search must answer inside its budget, even when a collection hangs.

The old code used ``as_completed(..., timeout=12)`` inside a ``with
ThreadPoolExecutor(...)`` block: leaving the block calls ``shutdown(wait=True)``,
so the deadline expired and then the request waited anyway for every worker —
twenty collections at nine seconds each is far past any budget a person will
accept.  The pool is now held open and shared, the wait is bounded, and nothing
unfinished is joined on the way out.
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
    monkeypatch.setattr(main, "library_books", lambda: _books(40))
    asked: list[str] = []

    def fake(book_id: str, pattern: str) -> list[dict]:
        asked.append(book_id)
        return []

    monkeypatch.setattr(main, "_kiwix_one", fake)
    monkeypatch.setattr(main, "SEARCH_DEADLINE", 5.0)
    main.search_library("water")
    assert len(asked) == main.MAX_COLLECTIONS < 40


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

    def boom(book_id: str, pattern: str):
        raise OSError("connection refused")

    monkeypatch.setattr(main, "_kiwix_one", boom)
    page = public.client.get("/search?q=water")
    assert page.status_code == 200, "one dead collection must not take the page down"
    # The person still gets an answer, in plain words, with somewhere to go next.
    assert "Nothing matched" in page.text
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
