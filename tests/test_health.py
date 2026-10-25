"""Stage 4 — readiness tells the truth, and progress is measured.

Every test builds a throwaway box (the ``ark``/``public`` fixture) and asks the
same module the pages ask.  A loopback ``http.server`` stands in for the reader
and the shelf, so "answering" and "refused" are two states a test can actually
produce — that is the whole difference between a green dot that means something
and one that cannot be wrong.
"""

from __future__ import annotations

import http.server
import json
import os
import shutil
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from html import escape as html_escape

import pytest

from helpers import BIG_PASSWORD, LAN, claim_box, get


# ---------------------------------------------------------------------------
#  a stand-in for the library reader and the bookshelf
# ---------------------------------------------------------------------------
class _Answering(http.server.BaseHTTPRequestHandler):
    def do_GET(self):                                   # noqa: N802 — stdlib name
        body = b"ok"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):                       # tests should stay quiet
        pass


class Reader:
    """A loopback address that answers while the test is inside the ``with``."""

    def __init__(self):
        self.server = http.server.HTTPServer(("127.0.0.1", 0), _Answering)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


# ---------------------------------------------------------------------------
#  building a box by hand
# ---------------------------------------------------------------------------
def _library(root, titles=("Wikipedia",)):
    """An index with those titles and a .zim each — what a finished box holds."""
    books = "".join(
        f'<book id="{t.lower()}" path="{t.lower()}.zim" title="{t}" size="1024"/>'
        for t in titles)
    (root / "kiwix" / "library.xml").write_text(
        f"<?xml version='1.0'?><library>{books}</library>", encoding="utf-8")
    for title in titles:
        (root / "kiwix" / f"{title.lower()}.zim").write_bytes(b"x" * 1024)


def _status(root, *, minutes_ago: float = 0, **fields):
    """Write the downloader's own report, with a timestamp the test controls."""
    state = root / "kiwix" / "state"
    state.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    payload = {"updated": stamp.isoformat(timespec="seconds")}
    payload.update(fields)
    (state / "status.json").write_text(json.dumps(payload), encoding="utf-8")


def _section(report, key):
    return next(s for s in report["sections"] if s["key"] == key)



# ---------------------------------------------------------------------------
#  one part of the box, and the reason behind it
# ---------------------------------------------------------------------------
def test_an_empty_box_names_what_is_missing_and_what_to_do(ark):
    import ark_health

    report = ark_health.report()
    library = _section(report, "library")
    assert library["ready"] is False
    assert library["content"] is False        # nothing on the card yet, not broken
    assert "No collection has arrived yet" in library["reason"]
    assert library["action"], "a part that is not ready must say what to do"
    assert report["ok"] is False
    assert "Offline Library" in report["blocking"]


def test_a_collection_is_only_ready_when_the_reader_answers(ark, monkeypatch):
    import ark_health

    _library(ark.root)
    silent = ark_health.library_section()
    assert silent["ready"] is False
    assert silent["content"] is True              # there is something to serve...
    assert "not answering" in silent["reason"]    # ...but nothing is serving it
    assert silent["facts"]["titles"] == 1

    with Reader() as reader:
        monkeypatch.setenv("KIWIX_INTERNAL", reader.base + "/kiwix")
        ark_health.forget_probes()
        answering = ark_health.library_section()
    assert answering["ready"] is True
    assert "answering" in answering["reason"]
    assert answering["action"] == ""


def test_an_index_without_books_is_not_ready(ark):
    import ark_health

    (ark.root / "kiwix" / "library.xml").write_text("<library></library>", encoding="utf-8")
    section = ark_health.library_section()
    assert section["ready"] is False
    assert section["content"] is False
    assert "empty" in section["reason"]


def test_the_bookshelf_needs_a_catalogue_and_a_page_that_answers(ark, monkeypatch):
    import ark_health

    assert "has not been set up" in ark_health.books_section()["reason"]

    shelf = ark.root / "calibre-library"
    shelf.mkdir()
    (shelf / "metadata.db").write_bytes(b"")         # a file, not yet a catalogue
    empty = ark_health.books_section()
    assert empty["ready"] is False and empty["content"] is False

    conn = sqlite3.connect(shelf / "metadata.db")
    conn.execute("CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT)")
    conn.execute("INSERT INTO books (title) VALUES ('Where There Is No Doctor')")
    conn.commit()
    conn.close()

    silent = ark_health.books_section()
    assert silent["ready"] is False and silent["content"] is True
    assert "not answering" in silent["reason"]

    with Reader() as reader:
        monkeypatch.setenv("CALIBRE_INTERNAL", reader.base)
        ark_health.forget_probes()
        assert ark_health.books_section()["ready"] is True


def test_a_full_card_is_named_as_the_reason(ark, monkeypatch):
    import ark_health
    import ark_storage


# ---------------------------------------------------------------------------
#  downloads: measured bytes, never a number invented
# ---------------------------------------------------------------------------
def test_progress_counts_finished_bytes_and_the_half_written_one(ark):
    import ark_health

    _status(ark.root, bytes_total=1000, bytes_on_disk=200, current="Wikipedia",
            current_bytes=100, current_total=400, current_rate_bps=50)
    p = ark_health.progress()
    assert p["reported"] is True
    assert p["current_percent"] == 25
    assert p["overall_bytes"] == 300          # 200 finished + 100 in flight
    assert p["overall_percent"] == 30
    assert p["remaining_bytes"] == 700
    assert p["rate_bps"] == 50
    assert p["eta_seconds"] == 14             # 700 bytes at 50 B/s


def test_an_unknown_total_is_not_reported_as_zero(ark):
    import ark_health

    p = ark_health.progress({})               # the queue has not reported at all
    assert p["reported"] is False
    for key in ("percent", "current_percent", "overall_percent",
                "remaining_bytes", "eta_seconds"):
        assert p[key] is None, key


def test_a_download_never_makes_the_box_unready(ark):
    import ark_health

    _status(ark.root, pending=4, present=1, bytes_total=1000, bytes_on_disk=200,
            current="Wikipedia", current_bytes=100, current_total=400,
            current_rate_bps=50, free_gib=20)
    busy = _section(ark_health.report(), "downloads")
    assert busy["ready"] is True
    assert busy["facts"]["percent"] == 25

    # A queue that has gone quiet is a note for the owner, never a reason to say
    # the box cannot be used: everything already on it still reads normally.
    _status(ark.root, minutes_ago=10, pending=4, current="Wikipedia",
            current_bytes=100, current_total=400)
    late = _section(ark_health.report(), "downloads")
    assert late["ready"] is False
    assert late["facts"]["stalled"] is True
    assert "Incoming collections" not in ark_health.report()["blocking"]


# ---------------------------------------------------------------------------
#  the HTTP answers a script and a phone both read
# ---------------------------------------------------------------------------
def test_ready_answers_503_with_a_reason_for_every_part(public):
    response = public.client.get("/api/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["ok"] is False
    assert body["sections"], "every part must be listed, not only the broken ones"
    for section in body["sections"]:
        assert {"key", "label", "ready", "reason", "action", "content", "facts"} <= set(section)
        assert section["reason"], f"{section['key']} has no reason"
        assert isinstance(section["ready"], bool)
    library = next(s for s in body["sections"] if s["key"] == "library")
    assert "No collection has arrived yet" in library["reason"]
    assert body["blocking"], "a 503 without a named reason is the old lie again"


def test_health_is_liveness_only_and_says_so(public):
    response = public.client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["live"] is True
    assert body["scope"] == "backend-process"
    assert "/api/ready" in body["note"]
    assert body["ready"] is False          # the real verdict rides along, not hidden


def test_status_carries_the_same_reasons_as_ready(public):
    ready_body = public.client.get("/api/ready").json()
    status = public.client.get("/api/status").json()
    by_key = {s["key"]: s for s in ready_body["sections"]}

    assert status["ok"] == ready_body["ok"]
    assert status["library"]["reason"] == by_key["library"]["reason"]
    assert status["books"]["reason"] == by_key["books"]["reason"]
    assert status["guides"]["reason"] == by_key["manuals"]["reason"]
    assert status["download_reason"] == by_key["downloads"]["reason"]
    for key in ("percent", "overall_percent", "rate_bps", "eta_seconds",
                "remaining_bytes", "stalled", "reported", "updated_age_seconds"):
        assert key in status["downloads"], key


# ---------------------------------------------------------------------------
#  the owner's page
# ---------------------------------------------------------------------------
def test_the_owner_page_shows_every_part_and_its_fix(client):
    import ark_health

    cookie = claim_box(client, password=BIG_PASSWORD)
    page = get(client, "/admin/health", headers=LAN, cookie=f"ark_admin={cookie}")
    assert page.status_code == 200
    for section in ark_health.report()["sections"]:
        assert html_escape(section["label"]) in page.text, section["label"]
    assert "Not ready" in page.text           # a fresh box is honestly not ready
    assert "What to do:" in page.text         # and every gap comes with the next step
    assert "Nothing on this page has been changed" in page.text
    for jargon in ("Kiwix", "ZIM", "systemd", "Calibre"):
        assert jargon not in page.text, jargon


def test_the_overview_links_to_the_owner_page(client):
    cookie = claim_box(client, password=BIG_PASSWORD)
    page = get(client, "/admin", headers=LAN, cookie=f"ark_admin={cookie}")
    assert page.status_code == 200


# ---------------------------------------------------------------------------
#  where the room went — measured folder by folder, never estimated
# ---------------------------------------------------------------------------
def test_the_breakdown_measures_each_folder(ark):
    import ark_health

    (ark.root / "kiwix" / "wiki.zim").write_bytes(b"z" * 4096)
    (ark.root / "vault" / "repair.html").write_bytes(b"v" * 1000)
    (ark.root / "drop" / "files" / "notes.txt").write_bytes(b"n" * 500)
    (ark.root / "calibre-library").mkdir()
    (ark.root / "calibre-library" / "a book.epub").write_bytes(b"b" * 2048)

    section = _section(ark_health.report(), "storage")
    sizes = {p["label"]: p["bytes"] for p in section["facts"]["breakdown"]}
    assert sizes["Offline Library"] == 4096
    assert sizes["Manuals & Guides"] == 1000
    assert sizes["File Box"] == 500
    assert sizes["Books"] == 2048
    assert section["facts"]["breakdown_bytes"] == sum(sizes.values())
    assert section["facts"]["card_total_bytes"] >= section["facts"]["free_bytes"]


def test_an_empty_folder_is_left_out_rather_than_reported_as_zero(ark):
    import ark_health

    section = _section(ark_health.report(), "storage")
    labels = [p["label"] for p in section["facts"]["breakdown"]]
    assert "Offline Library" not in labels          # nothing measured, nothing claimed
    assert all(p["bytes"] > 0 for p in section["facts"]["breakdown"])


def test_the_owner_page_says_where_the_room_went(client, ark):
    (ark.root / "kiwix" / "wiki.zim").write_bytes(b"z" * 4096)
    cookie = claim_box(client, password=BIG_PASSWORD)
    page = get(client, "/admin/health", headers=LAN, cookie=f"ark_admin={cookie}")
    assert page.status_code == 200
    assert "Offline Library" in page.text
    assert "4.0 kB" in page.text             # the measured size, not a guess
    assert "Measured:" in page.text
    for jargon in ("Kiwix", "ZIM", "systemd", "Calibre"):
        assert jargon not in page.text, jargon
    assert "/admin/health" in page.text
    assert "What is working" in page.text


# ---------------------------------------------------------------------------
#  the File Box: a record and a file are two different facts
# ---------------------------------------------------------------------------
def _shared(ark, *, stored: str, on_disk: bool = True) -> None:
    """A shared file the way ``ark_storage.spool()`` records one."""
    with ark.db.tx() as conn:
        conn.execute(
            "INSERT INTO files (ts, name, stored, display, size, sha256) "
            "VALUES (?,?,?,?,?,?)",
            ("2026-10-25T00:00:00+00:00", stored, stored, stored, 11, "0" * 64))
    if on_disk:
        (ark.root / "drop" / "files" / stored).write_bytes(b"payload....")


def test_the_file_box_counts_the_files_a_reader_can_open(ark):
    """A row whose file has gone is not a file the owner can say is shared.

    ``ark-restore --no-files`` puts the records back on a card whose File Box is
    empty, and a folder tidied by hand leaves rows behind, so the difference is
    supported rather than corrupt.  What must not happen is the owner's page
    counting the rows and claiming a file the portal cannot offer.
    """
    import ark_health

    _shared(ark, stored="aaaa1111-it-is-here.txt")
    _shared(ark, stored="bbbb2222-its-file-went.txt", on_disk=False)

    section = _section(ark_health.report(), "files")
    assert section["ready"] is True
    assert section["facts"]["files"] == 1          # what a reader can open
    assert section["facts"]["files_missing"] == 1  # said out loud, never hidden
    assert "1 file shared" in section["reason"]


def test_a_file_box_with_nothing_openable_says_so_plainly(ark):
    """Every row is a leftover: the page names it and says what to do about it."""
    import ark_health

    _shared(ark, stored="cccc3333-gone.txt", on_disk=False)

    section = _section(ark_health.report(), "files")
    assert section["ready"] is False
    assert section["content"] is False             # empty, not broken
    assert section["facts"]["files"] == 0
    assert section["facts"]["files_missing"] == 1
    assert "Nothing here can be opened" in section["reason"]
    assert "1 record is left over" in section["reason"]
    assert "delete the leftover record" in section["action"]
    for jargon in ("row", "record not found", "404", "orphan", "SQLite"):
        assert jargon not in section["reason"], jargon


def test_an_unreadable_folder_is_not_an_empty_one(ark, monkeypatch):
    """If the folder cannot be listed, the count is unknown — never zero."""
    import ark_health
    import ark_storage

    _shared(ark, stored="dddd4444-present.txt")
    _shared(ark, stored="eeee5555-unknown.txt", on_disk=False)
    monkeypatch.setattr(ark_storage, "present_names", lambda root=None: None)

    section = _section(ark_health.report(), "files")
    assert section["ready"] is True
    assert section["facts"]["files"] == 2          # the rows, unfiltered
    assert section["facts"]["files_missing"] is None
    assert "cannot be opened" not in section["reason"]


def test_the_file_box_refresh_agrees_with_the_listing(public):
    """The portal's row count and the owner page's count are the same number."""
    ark = public.ark
    _shared(ark, stored="ffff6666-here.txt")

    page = get(public.client, "/files")
    assert page.status_code == 200
    assert "here.txt" in page.text

    import ark_health

    section = _section(ark_health.report(), "files")
    assert section["facts"]["files"] == 1


# ---------------------------------------------------------------------------
#  is there a copy that could put the records back?
# ---------------------------------------------------------------------------
def _copy_into(root, *, hours_ago: float, messages: int = 12, shared: int = 4):
    """A finished copy, written the way ark-backup writes one."""
    stamp = (datetime.now(timezone.utc) - timedelta(hours=hours_ago))
    bundle = root / "backup" / f"ark-backup-{stamp.strftime('%Y%m%d-%H%M%S')}"
    bundle.mkdir(parents=True)
    (bundle / "manifest.json").write_text(json.dumps({
        "format": 1, "created": stamp.isoformat(timespec="seconds"),
        "counts": {"messages": messages, "shared_files": shared},
    }), encoding="utf-8")
    return bundle


def test_a_box_that_has_never_been_copied_says_so(ark):
    import ark_health

    report = ark_health.report()
    section = _section(report, "backup")
    assert section["ready"] is False
    assert section["content"] is False          # nothing to copy yet, not broken
    assert "No copy" in section["reason"]
    assert "ark-backup" in section["action"]
    # A missing copy must never make the box look unreadable.
    assert _section(report, "backup")["label"] not in report["blocking"]


def test_a_copy_is_reported_with_its_age_and_what_it_holds(ark):
    import ark_health

    _copy_into(ark.root, hours_ago=3)
    section = _section(ark_health.report(), "backup")
    assert section["ready"] is True
    assert section["facts"]["copies"] == 1
    assert "3 hours ago" in section["reason"]
    assert "12 messages" in section["reason"]
    assert "4 shared files" in section["reason"]

    # An old copy is not a failure of the box, but the owner must be told that
    # everything since then lives on one card.
    for stale in (ark.root / "backup").glob("ark-backup-*"):
        shutil.rmtree(stale)
    _copy_into(ark.root, hours_ago=24 * 9)
    late = _section(ark_health.report(), "backup")
    assert late["ready"] is False
    assert "9 days ago" in late["reason"]
    assert "nowhere else" in late["reason"]
    assert late["action"]


def test_a_half_written_copy_is_not_counted_as_one(ark):
    import ark_health

    left = ark.root / "backup" / "ark-backup-20200101-000000.partial"
    left.mkdir(parents=True)
    (left / "database").mkdir()
    (left / "database" / "ark.db").write_bytes(b"half")
    assert ark_health.copies() == []
    assert _section(ark_health.report(), "backup")["ready"] is False


def test_a_copy_the_pages_cannot_open_is_reported_not_denied(ark):
    """A copy that exists must never be reported as no copy at all.

    ``sudo ark-backup`` publishes as root; the pages that read the folder run as
    ``pi``.  That combination made **Latest copy** say "no copy has been made yet"
    beside a bundle that was sitting right there.  The tool now hands each bundle
    to the folder's owner (``tests/test_backup.py`` proves it), and this is the
    other half: if a copy is there and still cannot be opened, the page says so.
    """
    import ark_health

    bundle = _copy_into(ark.root, hours_ago=2)
    bundle.chmod(0o000)
    try:
        if os.access(str(bundle / "manifest.json"), os.R_OK):
            pytest.skip("this user can read anything — 000 permissions prove nothing")
        assert ark_health.copies() == []
        assert ark_health.copies_unreadable() == [bundle.name]

        section = _section(ark_health.report(), "backup")
        assert section["ready"] is False
        assert "not allowed to open it" in section["reason"]
        assert "No copy" not in section["reason"]
        assert section["facts"]["copies"] == 0
        assert section["facts"]["unreadable"] == 1
        assert "ark-backup --list" in section["action"]
        assert section["label"] not in ark_health.report()["blocking"]
    finally:
        bundle.chmod(0o755)                # so the fixture can tidy up
