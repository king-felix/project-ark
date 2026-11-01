"""The owner's own door for several files at once, and the list of every address.

What these tests hold:

* one press carries books, games, or both — and each one lands where it already
  belonged: a book in the folder the book importer watches, a game on the shelf
  through the arcade's own publish gate;
* nothing is written by a refused file, an unknown kind, an empty one, a batch
  too large for one press, or a stranger without a sign-in and a form token;
* a book is "in the library" only when the importer's own record says so;
* the page that lists every address is generated from one list, and that list
  knows every administrator page the application really serves;
* the proxy allows exactly the batch the application accepts, and no more.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

from helpers import LAN, claim_box, csrf_of, get

REPO = Path(__file__).resolve().parents[1]
PASSWORD = "correct horse battery staple 42"


def _gb_bytes(seed: bytes = b"ARK TEST") -> bytes:
    """A real little Game Boy cartridge header, so the shape check passes."""
    data = bytearray(0x150)
    data[0x134:0x144] = seed.ljust(16, b"\0")
    csum = 0
    for byte in data[0x134:0x14D]:
        csum = (csum - byte - 1) & 0xFF
    data[0x14D] = csum
    return bytes(data)


def _epub_bytes(text: str = "a book") -> bytes:
    """Nothing reads the inside of a book here; the importer does, later."""
    return b"PK\x03\x04" + text.encode() + b"\n"


def _page(client, cookie: str, path: str = "/admin/upload"):
    return get(client, path, cookie=f"ark_admin={cookie}")


def _token(client, cookie: str, path: str = "/admin/upload") -> str:
    return csrf_of(_page(client, cookie, path).text)


def _send(client, cookie: str, csrf: str, files):
    """One press, several files — exactly what the browser's form sends."""
    return client.post("/admin/upload",
                       files=[("files", (name, data, "application/octet-stream"))
                              for name, data in files],
                       data={"csrf": csrf}, headers=LAN,
                       cookies={"ark_admin": cookie}, follow_redirects=False)


def _incoming(ark) -> Path:
    return ark.root / "calibre-incoming"


def _incoming_files(ark):
    """What a person would see in the staging folder: finished, visible files."""
    folder = _incoming(ark)
    if not folder.is_dir():
        return []
    return sorted(p.name for p in folder.iterdir()
                  if p.is_file() and not p.name.startswith("."))


def _hidden_incoming(ark):
    """Half-written books, the importer's ledger — everything a person does not see."""
    folder = _incoming(ark)
    if not folder.is_dir():
        return []
    return sorted(p.name for p in folder.iterdir() if p.name.startswith("."))


def _shelf(ark):
    folder = ark.root / "arcade" / "games"
    if not folder.is_dir():
        return []
    return sorted(p.name for p in folder.iterdir() if p.is_file())


def _no_book_wait(monkeypatch):
    """No importer runs in a test: ask its record once and move on.

    ``wait_for_library`` reads the importer's ledger in a loop for up to
    ``IMPORT_WAIT`` seconds.  With no importer on this machine that loop would
    simply burn the clock, so the tests that only care about *where a book landed*
    shorten it to nothing — and the test that cares about the waiting sentence sets
    it to zero on purpose.
    """
    import ark_ingest

    monkeypatch.setattr(ark_ingest, "IMPORT_WAIT", 0.0)


# ---------------------------------------------------------------------------
#  the page
# ---------------------------------------------------------------------------
def test_the_page_offers_one_button_and_takes_several_files(client, ark):
    cookie = claim_box(client)
    page = _page(client, cookie)
    assert page.status_code == 200
    assert 'name="files"' in page.text
    assert "multiple" in page.text                  # several at once, not one
    assert 'type="file"' in page.text
    assert "Put these files on the box" in page.text
    assert "enctype=\"multipart/form-data\"" in page.text
    # It says, in words, what it will read and what it does with each kind.
    assert ".epub" in page.text and ".gb" in page.text
    assert "Book Library" in page.text and "games page" in page.text


def test_the_page_is_behind_the_sign_in(client):
    refused = get(client, "/admin/upload")
    assert refused.status_code == 303
    assert refused.headers.get("location") == "/admin/login"


# ---------------------------------------------------------------------------
#  a book
# ---------------------------------------------------------------------------
def test_a_book_lands_in_the_importers_own_folder(client, ark, monkeypatch):
    """A book goes where the book importer already watches — no second path."""
    _no_book_wait(monkeypatch)
    cookie = claim_box(client)
    before = (ark.root / "drop" / "files")
    sent = _send(client, cookie, _token(client, cookie),
                 [("My Book.epub", _epub_bytes())])
    assert sent.status_code == 200, sent.text
    assert "1 of 1 file went on the box" in sent.text
    assert "still being added" in sent.text

    names = _incoming_files(ark)
    assert len(names) == 1, names
    stored = _incoming(ark) / names[0]
    assert stored.name.startswith("My Book-"), stored.name
    assert stored.suffix == ".epub"
    assert oct(stored.stat().st_mode & 0o777) == "0o644"
    assert stored.read_bytes() == _epub_bytes()
    # Nothing else was touched: not the File Box, not a half-written book.
    assert not any(before.iterdir()) if before.is_dir() else True
    assert _hidden_incoming(ark) == []       # no .part, no temp, no ledger invented


def test_a_book_the_library_already_holds_is_not_added_twice(client, ark):
    """The importer's own record decides — the box does not guess from the folder."""
    cookie = claim_box(client)
    data = _epub_bytes("a book it already has")
    digest = hashlib.sha256(data).hexdigest()
    _incoming(ark).mkdir(parents=True, exist_ok=True)
    (_incoming(ark) / ".imported.json").write_text(
        json.dumps({digest: {"name": "that book.epub", "at": "2026-10-24T00:00:00Z"}}),
        encoding="utf-8")

    sent = _send(client, cookie, _token(client, cookie), [("that book.epub", data)])
    assert sent.status_code == 200, sent.text
    assert "already in the Book Library" in sent.text
    # Our copy is thrown away again: a book the library holds is not left to be
    # imported a second time, and the ledger is the only reason we know.
    assert _incoming_files(ark) == []


def test_the_page_says_in_the_library_only_when_the_importer_has_recorded_it(client, ark,
                                                                            monkeypatch):
    """The importer is what answers, not "the file is not there any more"."""
    import ark_ingest

    cookie = claim_box(client)
    seen = {}

    def already_recorded(digests, seconds=None):
        seen["digests"] = list(digests)
        return set(digests)           # the importer's ledger now names all of them

    monkeypatch.setattr(ark_ingest, "wait_for_library", already_recorded)
    sent = _send(client, cookie, _token(client, cookie), [("A Story.epub", _epub_bytes())])
    assert sent.status_code == 200, sent.text
    assert "in the Book Library now" in sent.text
    assert seen["digests"] == [hashlib.sha256(_epub_bytes()).hexdigest()]


def test_a_book_the_importer_has_not_answered_for_is_left_where_it_is(client, ark,
                                                                     monkeypatch):
    """No impatient answer: the box says it still has the book, and keeps it."""
    import ark_ingest

    cookie = claim_box(client)
    monkeypatch.setattr(ark_ingest, "IMPORT_WAIT", 0.0)
    sent = _send(client, cookie, _token(client, cookie), [("Slow.epub", _epub_bytes("slow"))])
    assert sent.status_code == 200, sent.text
    assert "still adding it" in sent.text
    # The file stays: the importer will take it, and nothing was thrown away
    # because this page ran out of patience.
    assert len(_incoming_files(ark)) == 1


# ---------------------------------------------------------------------------
#  a game
# ---------------------------------------------------------------------------
def test_a_game_lands_on_the_shelf_through_the_arcade_gate(client, ark):
    cookie = claim_box(client)
    sent = _send(client, cookie, _token(client, cookie),
                 [("Pocket Quest.gb", _gb_bytes(b"POCKET"))])
    assert sent.status_code == 200, sent.text
    assert "on the games page now as" in sent.text

    shelf = _shelf(ark)
    assert len(shelf) == 1, shelf
    assert shelf[0].endswith(".gb")
    with ark.db.read() as conn:
        row = conn.execute("SELECT title, system, sha256, revision, published "
                           "FROM games").fetchone()
    assert row["system"] == "gb" and row["published"] == 1 and row["revision"] == 1
    assert row["sha256"] == hashlib.sha256(_gb_bytes(b"POCKET")).hexdigest()
    # The waiting copy is gone: a published game is not left in arcade/tmp.
    tmp = ark.root / "arcade" / "tmp"
    assert not tmp.exists() or not any(tmp.iterdir())


def test_one_press_carries_both_kinds_and_keeps_them_apart(client, ark, monkeypatch):
    _no_book_wait(monkeypatch)
    cookie = claim_box(client)
    sent = _send(client, cookie, _token(client, cookie), [
        ("A Book.epub", _epub_bytes("one")),
        ("A Game.gb", _gb_bytes(b"BOTH")),
        ("A Manual.pdf", b"%PDF-1.4 not really"),
    ])
    assert sent.status_code == 200, sent.text
    assert "3 of 3 files went on the box" in sent.text
    assert len(_incoming_files(ark)) == 2          # the book and the manual
    assert len(_shelf(ark)) == 1                   # the game, and only the game


def test_the_same_cartridge_offered_twice_is_named_not_added_again(client, ark):
    cookie = claim_box(client)
    csrf = _token(client, cookie)
    first = _send(client, cookie, csrf, [("Twice.gb", _gb_bytes(b"TWICE"))])
    assert "on the games page now" in first.text
    second = _send(client, cookie, csrf, [("Twice.gb", _gb_bytes(b"TWICE"))])
    assert second.status_code == 200, second.text
    assert "already on the shelf" in second.text
    assert len(_shelf(ark)) == 1



# ---------------------------------------------------------------------------
#  refusals write nothing
# ---------------------------------------------------------------------------
def test_a_kind_the_box_does_not_read_is_refused_by_name(client, ark):
    cookie = claim_box(client)
    sent = _send(client, cookie, _token(client, cookie),
                 [("holiday.exe", b"MZ\x90\x00 a program, not a book")])
    assert sent.status_code == 400, sent.text
    assert "does not know what to do with that kind of file" in sent.text
    assert "holiday.exe" in sent.text              # named, not silently dropped
    assert _incoming_files(ark) == [] and _shelf(ark) == []


def test_an_empty_file_is_refused_and_leaves_nothing_behind(client, ark):
    cookie = claim_box(client)
    sent = _send(client, cookie, _token(client, cookie),
                 [("Empty.epub", b""), ("Empty.gb", b"")])
    assert sent.status_code == 400, sent.text
    assert "nothing to add" in sent.text
    assert _incoming_files(ark) == [] and _shelf(ark) == []
    tmp = ark.root / "arcade" / "tmp"
    assert not tmp.exists() or not any(tmp.iterdir())


def test_a_file_that_is_not_really_a_game_is_refused_and_the_shelf_is_unchanged(client, ark):
    cookie = claim_box(client)
    sent = _send(client, cookie, _token(client, cookie),
                 [("notes.gb", b"just some words, no cartridge header here\n")])
    assert sent.status_code == 400, sent.text
    assert "notes.gb" in sent.text
    assert _shelf(ark) == []
    with ark.db.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 0


def test_a_file_whose_console_the_box_cannot_name_is_refused_not_guessed(client, ark):
    """``.bin`` is shared by two consoles, and the page has no way to ask which.

    Guessing would put a Genesis game on the shelf under the wrong console, so the box
    says what it could not work out and names the one door where a person can say.
    """
    cookie = claim_box(client)
    sent = _send(client, cookie, _token(client, cookie),
                 [("Mystery.bin", b"\x00" * 4096)])
    assert sent.status_code == 400, sent.text
    assert "could not tell which console" in sent.text
    assert "ark-arcade --import" in sent.text
    assert _shelf(ark) == []
    tmp = ark.root / "arcade" / "tmp"
    assert not tmp.exists() or not any(tmp.iterdir())


def test_more_files_than_one_press_carries_is_refused_before_anything_is_written(client, ark):
    import ark_ingest

    cookie = claim_box(client)
    too_many = [(f"Book {n}.epub", _epub_bytes(str(n)))
                for n in range(ark_ingest.MAX_FILES + 1)]
    sent = _send(client, cookie, _token(client, cookie), too_many)
    assert sent.status_code == 400, sent.text
    assert f"press carries {ark_ingest.MAX_FILES}" in sent.text
    assert _incoming_files(ark) == []


def test_a_press_larger_than_one_upload_may_be_stops_and_leaves_no_partial_file(
        client, ark, monkeypatch):
    """A batch has a ceiling too, and the file that crosses it is not left behind."""
    import ark_ingest

    cookie = claim_box(client)
    monkeypatch.setattr(ark_ingest, "MAX_BATCH", 64)
    monkeypatch.setattr(ark_ingest, "MIN_FREE_RESERVE", 0)
    sent = _send(client, cookie, _token(client, cookie),
                 [("Big.epub", b"x" * 200)])
    assert sent.status_code == 400, sent.text
    assert "larger than this page will take" in sent.text
    assert _incoming_files(ark) == []              # neither a file nor a .part


def test_a_name_in_the_form_cannot_reach_a_folder_outside_the_staging_area(client, ark,
                                                                          monkeypatch):
    _no_book_wait(monkeypatch)
    cookie = claim_box(client)
    sent = _send(client, cookie, _token(client, cookie),
                 [("../../escape.epub", _epub_bytes("escape"))])
    assert sent.status_code == 200, sent.text
    assert len(_incoming_files(ark)) == 1
    stored = _incoming(ark) / _incoming_files(ark)[0]
    assert stored.parent == _incoming(ark)         # exactly one folder, never up
    assert "escape" in stored.name and ".." not in stored.name


# ---------------------------------------------------------------------------
#  the door itself
# ---------------------------------------------------------------------------
def test_the_upload_door_needs_a_sign_in_and_writes_nothing(client, ark):
    sent = client.post("/admin/upload",
                       files=[("files", ("x.gb", _gb_bytes(), "application/octet-stream"))],
                       data={"csrf": "not-a-token"}, headers=LAN, follow_redirects=False)
    assert sent.status_code == 403
    assert _shelf(ark) == [] and _incoming_files(ark) == []


def test_the_upload_door_needs_the_form_token(client, ark):
    cookie = claim_box(client)
    sent = client.post("/admin/upload",
                       files=[("files", ("x.gb", _gb_bytes(), "application/octet-stream"))],
                       data={"csrf": ""}, headers=LAN,
                       cookies={"ark_admin": cookie}, follow_redirects=False)
    assert sent.status_code == 403
    assert "Nothing was changed" in sent.text
    assert _shelf(ark) == [] and _incoming_files(ark) == []


def test_the_upload_route_is_never_async():
    """Reading several files blocks; an ``async def`` here would stall the event loop."""
    import inspect

    import ark_admin

    assert not inspect.iscoroutinefunction(ark_admin.upload_files)
    assert not inspect.iscoroutinefunction(ark_admin.upload_page)



# ---------------------------------------------------------------------------
#  one list of kinds, kept honest against the two things that really read files
# ---------------------------------------------------------------------------
def test_every_kind_the_book_importer_reads_is_a_kind_this_page_reads():
    """Two lists, one truth: the shipped importer is the authority on books.

    The page must not import the importer (it runs under a different Python), so the
    list is copied — and this test reads the script that really imports books and
    fails the moment the two disagree.  A book kind Calibre takes but this page does
    not know would be a file the owner cannot add; the other way round would be a
    file handed over that the importer then ignores, and sits in the folder for ever.
    """
    import ast

    text = (REPO / "pi" / "bin" / "ark-import-books").read_text(encoding="utf-8")
    found = None
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "BOOK_EXT" for t in node.targets):
            found = {item.value for item in node.value.elts}
    assert found, "ark-import-books no longer defines BOOK_EXT"

    import ark_ingest

    assert set(ark_ingest.BOOK_EXT) == found


def test_every_console_the_shelf_can_play_is_a_kind_this_page_carries():
    """A console added to the arcade is offered here without a second list."""
    import ark_arcade
    import ark_ingest

    wanted = {ext for info in ark_arcade.SYSTEMS.values() for ext in info["ext"]}
    assert set(ark_ingest.GAME_EXT) == wanted
    # And the two families never overlap: a file has exactly one home.
    assert not (set(ark_ingest.BOOK_EXT) & set(ark_ingest.GAME_EXT))


def test_the_caps_are_the_ones_the_rest_of_the_box_already_uses():
    import ark_arcade_install
    import ark_ingest
    import ark_storage

    assert ark_ingest.MAX_GAME == ark_arcade_install.MAX_DOWNLOAD
    assert ark_ingest.MIN_FREE_RESERVE == ark_storage.MIN_FREE_RESERVE
    assert ark_ingest.MAX_BATCH <= ark_storage.MAX_FILE


# ---------------------------------------------------------------------------
#  the list of every address
# ---------------------------------------------------------------------------
def test_the_addresses_page_is_behind_the_sign_in(client):
    refused = get(client, "/admin/doors")
    assert refused.status_code == 303
    assert refused.headers.get("location") == "/admin/login"


def test_the_addresses_page_names_the_doors_in_plain_words(client):
    cookie = claim_box(client)
    page = _page(client, cookie, "/admin/doors")
    assert page.status_code == 200
    # The path is what matters; the host is whatever the reader used (this test
    # client calls itself ``testserver``), and it must not leave a brace behind.
    for wanted in ("/admin/upload", "/admin/arcade", "/admin/doors",
                   "/files", "/arcade/add", ":8445/arcade/player",
                   "/api/ready", "/recovery.txt"):
        assert wanted in page.text, wanted
    assert "{host}" not in page.text
    assert "http://testserver/files" in page.text          # the reader's own name
    assert "Book Library" in page.text and "File Box" in page.text


def test_every_administrator_page_is_named_in_the_addresses():
    """A page added to this area and not listed is a door nobody can find."""
    sys.path.insert(0, str(REPO / "pi" / "api"))
    try:
        import ark_doors
    finally:
        sys.path.pop(0)
    listed = set(ark_doors.admin_paths())
    # Every GET page this router really serves, read from the shipped module.
    source = (REPO / "pi" / "api" / "ark_admin.py").read_text(encoding="utf-8")
    served = set(re.findall(r'@router\.get\("([^"]*)"', source))
    served = {"/admin" if path in ("", "/") else "/admin" + path for path in served}
    missing = sorted(served - listed - set(ark_doors.STEPS))
    assert not missing, f"these administrator pages are not in the address list: {missing}"


def test_the_address_list_really_describes_every_door():
    sys.path.insert(0, str(REPO / "pi" / "api"))
    try:
        import ark_doors
    finally:
        sys.path.pop(0)
    assert len(ark_doors.DOORS) >= 25
    for door in ark_doors.DOORS:
        assert door["address"].startswith(("http://", "https://")), door
        assert door["name"] and door["who"] and door["what"], door
        # Every group the data uses is a group the page prints, in a fixed order.
        assert door["group"] in ark_doors.GROUP_ORDER, door["group"]



# ---------------------------------------------------------------------------
#  the proxy allows exactly what the application accepts
# ---------------------------------------------------------------------------
def test_the_batch_door_allows_the_batch_the_page_accepts_and_no_more():
    """One exact path takes a 512 MiB body; the rest of /admin stays tiny.

    520m is ``ark_ingest.MAX_BATCH`` (512 MiB) plus multipart framing, so the proxy
    never refuses a batch the page would take — and never lets a body through that
    the application has no use for.  The body is streamed and, like every other
    listener on this box, parks on the card rather than in the tmpfs /tmp became.
    """
    import ark_ingest

    assert ark_ingest.MAX_BATCH == 512 * 1024 * 1024
    text = (REPO / "pi" / "nginx" / "ark-admin.conf").read_text(encoding="utf-8")
    door = re.search(r"location\s*=\s*/admin/upload\s*\{(.*?)\n\s*\}", text, re.S)
    assert door, "ark-admin.conf has no exact-match door for /admin/upload"
    body = door.group(1)
    assert "client_max_body_size 520m;" in body, body
    assert "proxy_request_buffering off;" in body, body
    assert "proxy_read_timeout 300s;" in body, body

    # The prefix underneath keeps the server's small cap: a form is not an upload.
    server = re.search(r"server \{(.*?)\n\}", text, re.S)
    assert server and "client_max_body_size 128k;" in server.group(1)
    prefix = re.search(r"location \^~ /admin \{(.*?)\n\s*\}", text, re.S)
    assert prefix and "client_max_body_size" not in prefix.group(1)
    # And the parking place is the card, not RAM.
    assert "client_body_temp_path /srv/ark/tmp/nginx" in text


def test_the_readme_and_the_address_list_agree_about_the_batch_page():
    """Paperwork and the product in one commit — the list is the page's source."""
    admin_doc = (REPO / "docs" / "ADMIN.md").read_text(encoding="utf-8")
    assert "/admin/upload" in admin_doc


def test_a_half_written_book_is_swept_and_never_counted_as_a_book(ark):
    """The temp name is not decoration: the repair pass has to be able to see it.

    A cut in the middle of a 400 MB book leaves ``.upload-<hex>.epub.part`` in the
    staging folder.  The leading dot and the ``.part`` suffix are what keep the book
    importer from ever reading it; this rule is what keeps it from living there for
    ever.  Without the rule the file would be invisible to every sweep — the folder
    was not in ``SWEEP_RULES`` at all — and the only trace of the book would be a
    hidden file nobody could explain.
    """
    import ark_self_heal

    folder = ark.root / "calibre-incoming"
    folder.mkdir(parents=True, exist_ok=True)
    half = folder / ".upload-0123456789abcdef0123456789abcdef.epub.part"
    half.write_bytes(b"half a book")
    whole = folder / "A Book-deadbeef.epub"
    whole.write_bytes(b"a whole book")
    stale = __import__("time").time() - (ark_self_heal.STALE_UPLOAD_HOURS + 1) * 3600
    __import__("os").utime(str(half), (stale, stale))

    gone = ark_self_heal.sweep(ark.root)      # judged against the clock, as on the box

    assert "calibre-incoming/.upload-0123456789abcdef0123456789abcdef.epub.part" in gone
    assert not half.exists()
    # A finished book is somebody's book, not a temp file: the sweep never takes it.
    assert whole.exists()


