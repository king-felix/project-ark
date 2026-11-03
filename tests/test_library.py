"""The library door: shelves a person can choose from, and what search may claim.

Three promises are checked here, and each of them is a way the box could lie to
somebody who is looking for something:

* **the shelf page is the card, not a wish list** — every collection the index
  lists appears exactly once, the filing comes from the catalogue rather than
  from a second list in code, and a bundle copied on by hand lands under
  *Everything else* instead of vanishing;
* **a tag in the file never decides what a reader is told** — ``_ftindex:no``
  looks like "this cannot be searched", and on this box it is not: the installed
  iFixit bundle carries it and answers 22,722 hits for "screen" in 0.22 s.  So the
  search fan-out still asks every collection, and a card only calls itself films
  because the catalogue filed it on the ``watch`` shelf;
* **the proxy really serves the page** — ``location /`` ends in
  ``try_files … /index.html``, so an unnamed route answers the front page with a
  200.  That is a page that looks fine and is not the page anybody asked for,
  and it is why ``/library`` has to be named in ``ark.conf``.

The last group is about the download: the digest the mirror publishes is the only
check that catches a mirror serving a different file of the right length.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CATALOG = REPO / "pi" / "kiwix" / "catalog.tsv"
NGINX = (REPO / "pi" / "nginx" / "ark.conf").read_text(encoding="utf-8")
MAIN_APP = (REPO / "pi" / "api" / "main.py").read_text(encoding="utf-8")
CHECKSUM = REPO / "pi" / "bin" / "ark-zim-checksum"

BANNED = ("kiwix", "zim", "calibre", "opds", "fastapi", "nginx", "systemd",
          "sqlite", "apk", "localhost", "port")


def _library_xml(ark, books):
    """Write an index in the shape kiwix-manage produces (paths are file names)."""
    rows = []
    for stem, title, size, tags in books:
        rows.append(f'<book id="{stem}" path="{stem}.zim" name="{stem}"'
                    f' title="{title}" size="{size}" tags="{tags}"/>')
    (ark.root / "kiwix" / "library.xml").write_text(
        '<?xml version="1.0"?><library>' + "".join(rows) + "</library>",
        encoding="utf-8")


def _catalogue_rows():
    out = []
    for line in CATALOG.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#") or line.startswith("name\t"):
            continue
        out.append(line.split("\t"))
    return out


# ---------------------------------------------------------------------------
#  the catalogue is the one place a collection is described
# ---------------------------------------------------------------------------
def test_every_catalogue_row_is_complete_and_filed_on_a_real_shelf():
    import ark_library

    keys = {key for key, _title, _blurb in ark_library.TOPICS}
    seen = set()
    for parts in _catalogue_rows():
        assert len(parts) == 7, f"{parts[0]}: expected 7 columns, got {len(parts)}"
        name, flavour, size, tier, label, query, topic = parts
        assert tier in ("essential", "core", "extra"), name
        assert re.fullmatch(r"-|maxi|nopic|mini", flavour), name
        float(size)                                   # a number, not a hopeful word
        assert topic in keys, f"{name}: {topic!r} is not a shelf the page draws"
        assert query.strip(), name
        assert (name, flavour) not in seen, f"{name} ({flavour}) is listed twice"
        seen.add((name, flavour))


def test_the_requested_collections_are_in_the_catalogue_on_the_right_shelves():
    """The six things asked for on 3 November 2026, each where a person would look.

    Two of them are films and say so in their label — a video shelf is opened to
    watch, and its label should not promise a page to read.
    """
    rows = {parts[0]: parts for parts in _catalogue_rows()}
    expected = {
        "urban-prepper_en_all": "watch",
        "diy.stackexchange.com_en_all": "repair",
        "mechanics.stackexchange.com_en_all": "vehicles",
        "solar.lowtechmagazine.com_mul_all": "home",
        "canadian-prepper_en_preppingfood": "watch",
        "canadian-prepper_en_bugoutconcepts": "watch",
        "trueprepper.com_en_all": "prep",
    }
    for name, topic in expected.items():
        assert name in rows, f"{name} is missing from the catalogue"
        assert rows[name][6] == topic, f"{name} is filed under {rows[name][6]!r}"
        assert rows[name][3] == "core", f"{name} must be part of the core library"
        assert len(rows[name][4]) > 3, f"{name} needs a plain-English label"
    for film in ("urban-prepper_en_all", "canadian-prepper_en_preppingfood",
                 "canadian-prepper_en_bugoutconcepts"):
        assert "video" in rows[film][4].lower(), f"{film} is films; its label must say so"


def test_the_search_ceiling_is_above_what_the_catalogue_can_put_on_the_card(public):
    """The ceiling is a guard, never a page size — and it drifted anyway.

    On 2 November 2026 it was 12 against 20 collections; on 3 November the card
    reached 27 against 20, and the seven books sorted last — one of them Wikipedia
    — were never asked anything.  The catalogue is the box's own definition of how
    many collections exist, so it is the number the ceiling has to stay above, and
    the block has to be able to name every collection the search asked.
    """
    names = {parts[0] for parts in _catalogue_rows()}
    assert public.app.MAX_COLLECTIONS >= len(names), (
        f"the card can hold {len(names)} collections but only "
        f"{public.app.MAX_COLLECTIONS} would ever be asked")
    assert public.app.LIBRARY_HITS >= public.app.MAX_COLLECTIONS, (
        "a block shorter than the ceiling cannot show every collection that answered")


def test_no_shelf_label_uses_a_word_the_reader_does_not_know():
    import ark_library

    for _key, title, blurb in ark_library.TOPICS:
        for word in re.findall(r"[a-z]+", f"{title} {blurb}".lower()):
            assert word not in BANNED, f"{word!r} is not a word for this page"


# ---------------------------------------------------------------------------
#  filing a book: one shelf, never two, never none
# ---------------------------------------------------------------------------
def test_a_file_name_becomes_the_catalogue_name_it_came_from():
    import ark_library

    assert ark_library.name_of("urban-prepper_en_all_2026-08") == "urban-prepper_en_all"
    assert ark_library.name_of("wikipedia_en_all_nopic_2026-06") == "wikipedia_en_all"
    assert ark_library.name_of("canadian-prepper_en_preppingfood_2026-10") \
        == "canadian-prepper_en_preppingfood"
    assert ark_library.name_of("gone.zim") == "gone"


def test_every_book_lands_on_exactly_one_shelf(ark, monkeypatch):
    import ark_library

    monkeypatch.setenv("ARK_CATALOG", str(CATALOG))
    books = [
        {"id": "a", "stem": "diy.stackexchange.com_en_all_2026-10", "title": "Home Improvement",
         "size": 1},
        {"id": "b", "stem": "urban-prepper_en_all_2026-08", "title": "Urban Prepper",
         "size": 1},
        {"id": "c", "stem": "something-copied-by-hand_2026-01", "title": "By hand",
         "size": 1},
        {"id": "d", "stem": "mechanics.stackexchange.com_en_all_2026-10", "title": "Mechanics",
         "size": 1},
    ]
    groups = ark_library.shelves(books)
    placed = [b["id"] for g in groups for b in g["books"]]
    assert sorted(placed) == ["a", "b", "c", "d"], "a book was lost or shown twice"
    assert [g["key"] for g in groups] == ["repair", "vehicles", "watch", "more"], \
        "shelves must keep the order a reader meets them in"
    assert [b["id"] for g in groups if g["key"] == "more" for b in g["books"]] == ["c"]


def test_an_empty_shelf_is_not_drawn(ark, monkeypatch):
    import ark_library

    monkeypatch.setenv("ARK_CATALOG", str(CATALOG))
    groups = ark_library.shelves([{"id": "x", "stem": "diy.stackexchange.com_en_all_2026-10",
                                   "title": "Home Improvement", "size": 1}])
    assert [g["key"] for g in groups] == ["repair"]


def test_a_catalogue_the_box_cannot_read_is_no_filing_not_a_broken_page(tmp_path, monkeypatch):
    """A stray non-UTF-8 byte in one label must not take the whole library page down."""
    import ark_library

    bad = tmp_path / "catalog.tsv"
    bad.write_bytes(b"name\tflavour\tsize\ttier\tlabel\tquery\ttopic\n"
                    b"diy.stackexchange.com_en_all\t-\t1.9\tcore\tCaf\xe9 fixes\tdiy\trepair\n")
    monkeypatch.setenv("ARK_CATALOG", str(bad))
    assert ark_library.read_catalogue() == {}
    groups = ark_library.shelves([{"id": "x", "stem": "diy.stackexchange.com_en_all_2026-10",
                                   "title": "Home Improvement"}])
    assert [g["key"] for g in groups] == ["more"]


def test_a_missing_catalogue_is_an_empty_map_not_a_crash(ark, monkeypatch, tmp_path):
    import ark_library

    monkeypatch.setenv("ARK_CATALOG", str(tmp_path / "not-there.tsv"))
    assert ark_library.read_catalogue() == {}
    groups = ark_library.shelves([{"id": "x", "stem": "anything_2026-01", "title": "T"}])
    assert [g["key"] for g in groups] == ["more"]


# ---------------------------------------------------------------------------
#  the page a person actually sees
# ---------------------------------------------------------------------------
def _two_shelves(ark, monkeypatch):
    monkeypatch.setenv("ARK_CATALOG", str(CATALOG))
    _library_xml(ark, [
        ("diy.stackexchange.com_en_all_2026-10", "Home Improvement Q&amp;A", 2037134,
         "_ftindex:yes;_pictures:yes"),
        ("urban-prepper_en_all_2026-08", "Urban Prepper", 2192613,
         "_videos:yes;_ftindex:no;_pictures:yes"),
    ])


def test_the_shelf_page_shows_every_collection_and_links_to_it(public, ark, monkeypatch):
    _two_shelves(ark, monkeypatch)
    body = public.client.get("/library", headers={"x-real-ip": "192.0.2.50"}).text

    assert "Home Improvement Q&amp;A" in body
    assert "Urban Prepper" in body
    assert 'href="/kiwix/content/diy.stackexchange.com_en_all_2026-10/"' in body
    assert 'href="/kiwix/content/urban-prepper_en_all_2026-08/"' in body
    assert body.count("Urban Prepper") >= 1
    assert "Films. Open the shelf and pick one" in body
    assert "Tap to watch" in body
    assert "Read in browser" in body
    # 2037134 + 2192613 KiB is 4.0 GiB — the page must say GB, not MB.
    assert "4.0 GB" in body


def test_the_index_says_kilobytes_and_the_shelf_page_says_gigabytes(ark, monkeypatch):
    """``kiwix-manage`` writes ``size`` in KiB; the page must not read it as bytes.

    Read as bytes, a card holding 86.5 GiB printed **86.5 MB** — a number that makes
    the library look like one book, on the one page whose whole job is to say what is
    there.  ``sizeBytes`` (when present) is bytes; ``size`` is KiB, always.
    """
    import ark_health

    monkeypatch.setenv("ARK_CATALOG", str(CATALOG))
    _library_xml(ark, [
        ("urban-prepper_en_all_2026-08", "Urban Prepper", 2192613, "_videos:yes;_ftindex:no"),
    ])
    book, = ark_health.library_books()
    assert book["size"] == 2192613 * 1024, "the index's kilobytes must become bytes"


def test_a_picture_guide_is_not_sold_as_a_film_shelf(public, ark, monkeypatch):
    """iFixit carries ``_ftindex:no`` too — and it *is* searchable on this box.

    Measured 3 Nov 2026: 22,722 hits for "screen" in 0.2 s against the installed
    iFixit bundle, so ``_ftindex:no`` says nothing about whether a collection can
    be searched and nothing a reader should be told.  Only the catalogue's shelf
    decides what a card calls itself.
    """
    monkeypatch.setenv("ARK_CATALOG", str(CATALOG))
    _library_xml(ark, [
        ("ifixit_en_all_2025-12", "iFixit in English", 3487007,
         "_category:iFixit;_pictures:yes;_videos:yes;_ftindex:no"),
    ])
    body = public.client.get("/library", headers={"x-real-ip": "192.0.2.50"}).text
    assert "Read in browser" in body
    assert "Films." not in body, "a picture guide must not be sold as a film shelf"
    assert "Tap to watch" not in body


def test_the_shelf_page_says_what_each_shelf_is_for(public, ark, monkeypatch):
    _two_shelves(ark, monkeypatch)
    body = public.client.get("/library", headers={"x-real-ip": "192.0.2.50"}).text
    assert ">Fix it<" in body and ">Watch and learn<" in body
    assert 'id="repair"' in body and 'href="#repair"' in body
    assert "Browse every collection" in body


def test_the_shelf_page_uses_the_plain_words_and_escapes_a_title(public, ark, monkeypatch):
    monkeypatch.setenv("ARK_CATALOG", str(CATALOG))
    _library_xml(ark, [
        ("urban-prepper_en_all_2026-08", "Prepper &lt;script&gt;alert(1)&lt;/script&gt;",
         2192613, "_videos:yes;_ftindex:no"),
    ])
    body = public.client.get("/library", headers={"x-real-ip": "192.0.2.50"}).text
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body
    for word in re.findall(r"[a-z]+", re.sub(r"<[^>]+>", " ", body).lower()):
        assert word not in BANNED, f"{word!r} reached a reader"
    assert "https://ark.local:8443/admin" in body    # the footer still tells the owner


def test_an_empty_library_is_a_friendly_page_not_an_error(public, ark, monkeypatch):
    monkeypatch.setenv("ARK_CATALOG", str(CATALOG))
    response = public.client.get("/library", headers={"x-real-ip": "192.0.2.50"})
    assert response.status_code == 200
    assert "still filling up" in response.text


# ---------------------------------------------------------------------------
#  search must not pretend it looked at the films
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
#  the proxy must really carry the page
# ---------------------------------------------------------------------------
def test_every_page_the_backend_builds_is_named_by_the_proxy():
    """``location /`` answers the front page with a 200 for anything it does not know.

    So a route that is not named here is not a 404 — it is the home page wearing
    the address that was asked for.  This test reads the decorators out of the
    app and insists that each page has a door.
    """
    routes = {r for r in re.findall(r'@app\.get\("([^"]+)"', MAIN_APP)}
    named = set(re.findall(r"location\s+(?:=|\^~|~)\s+(\S+)", NGINX))
    alt = re.search(r"location ~ \^/\(([^)]*)\)\$", NGINX)
    alternation = {"/" + p.replace("\\", "") for p in alt.group(1).split("|")} if alt else set()

    for route in sorted(routes):
        if route.startswith("/api") or route == "/":
            continue
        first = "/" + route.strip("/").split("/")[0]
        assert (route in named or first in named or first + "/" in named
                or route in alternation or first in alternation), (
            f"{route} has no door in ark.conf — it would answer the home page with a 200")


def test_the_library_page_is_not_reachable_without_the_proxy_naming_it():
    assert re.search(r"location ~ \^/\([^)]*\blibrary\b[^)]*\)\$", NGINX), (
        "/library must be named in the on-the-fly page location")


def test_every_backend_module_the_portal_imports_is_installed():
    """A new module that phase 60 does not copy is a service that will not start.

    The list of files in ``60-portal.sh`` is written by hand, so it drifts from the
    imports in ``main.py`` the moment somebody adds one.  The failure is total and
    obvious on the box (``ark-api`` inactive) but silent until a deploy; this makes
    it a failing test instead.
    """
    phase = (REPO / "pi" / "steps" / "60-portal.sh").read_text(encoding="utf-8")
    imported = set(re.findall(r"^import (ark_\w+)$", MAIN_APP, re.M))
    imported |= set(re.findall(r"^from (ark_\w+) import", MAIN_APP, re.M))
    assert imported, "main.py imports no backend modules — the regex has drifted"
    for module in sorted(imported):
        assert f"{module}.py" in phase, (
            f"{module}.py is imported by main.py but 60-portal.sh never copies it")


# ---------------------------------------------------------------------------
#  the published digest — the one check a same-length wrong file cannot pass
# ---------------------------------------------------------------------------
def _run(*args):
    return subprocess.run([sys.executable, str(CHECKSUM), *args],
                          capture_output=True, text=True, timeout=60)


def _payload(tmp_path, data=b"a library on a card\n"):
    f = tmp_path / "urban-prepper_en_all_2026-08.zim"
    f.write_bytes(data)
    return f


def _digest(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_the_metalink_digest_is_found_and_the_file_matches_it(tmp_path):
    f = _payload(tmp_path)
    want = _digest(f)
    (tmp_path / (f.name + ".meta4")).write_text(
        '<?xml version="1.0"?><metalink xmlns="urn:ietf:params:xml:ns:metalink">'
        f'<file name="{f.name}"><size>{f.stat().st_size}</size>'
        f'<hash type="md5">{"0" * 32}</hash>'
        f'<hash type="sha-256">{want}</hash>'
        '<pieces length="4194304" type="sha-1"><hash>' + "1" * 40 + "</hash></pieces>"
        "</file></metalink>", encoding="utf-8")

    ok = _run("--url", f.as_uri(), "--file", str(f))
    assert ok.returncode == 0, ok.stderr
    assert ok.stdout.strip() == want

    only = _run("--url", f.as_uri())
    assert only.returncode == 0
    assert only.stdout.strip() == want


def test_a_plain_sha256_file_is_enough_when_there_is_no_metalink(tmp_path):
    f = _payload(tmp_path)
    want = _digest(f)
    (tmp_path / (f.name + ".sha256")).write_text(f"{want}  {f.name}\n", encoding="utf-8")
    ok = _run("--url", f.as_uri(), "--file", str(f))
    assert ok.returncode == 0, ok.stderr


def test_a_file_that_is_not_the_published_one_is_refused(tmp_path):
    f = _payload(tmp_path, b"the right length, the wrong library")
    want = "0" * 64
    (tmp_path / (f.name + ".meta4")).write_text(
        '<?xml version="1.0"?><metalink xmlns="urn:ietf:params:xml:ns:metalink">'
        f'<file name="{f.name}"><hash type="sha-256">{want}</hash></file></metalink>',
        encoding="utf-8")
    bad = _run("--url", f.as_uri(), "--file", str(f))
    assert bad.returncode == 1, bad.stderr
    assert "mismatch" in bad.stderr.lower()


def test_no_published_digest_is_unknown_not_wrong(tmp_path):
    f = _payload(tmp_path)
    unknown = _run("--url", f.as_uri(), "--file", str(f))
    assert unknown.returncode == 3, unknown.stderr
    assert "no published checksum" in unknown.stderr.lower()


def test_a_file_that_cannot_be_read_is_a_different_answer_again(tmp_path):
    f = _payload(tmp_path)
    want = _digest(f)
    (tmp_path / (f.name + ".meta4")).write_text(
        '<?xml version="1.0"?><metalink xmlns="urn:ietf:params:xml:ns:metalink">'
        f'<file name="{f.name}"><hash type="sha-256">{want}</hash></file></metalink>',
        encoding="utf-8")
    missing = _run("--url", f.as_uri(), "--file", str(tmp_path / "gone.zim"))
    assert missing.returncode == 2, missing.stderr


DOWNLOAD = REPO / "pi" / "bin" / "ark-zim-download"


def test_the_downloader_checks_the_digest_before_it_publishes():
    script = DOWNLOAD.read_text(encoding="utf-8")
    body = script.split("fetch_one()", 1)[1].split("\ntrap ", 1)[0]
    assert 'verify_part "$url" "$part" "$name"' in body, (
        "fetch_one must ask verify_part before the file gets its final name")
    publish = body.index('mv -f "$part" "$ZIM_DIR/$name"')
    assert body.index('verify_part "$url" "$part" "$name"') < publish


def _verify(tmp_path, *, meta4="", helper=None, payload=b"a library on a card\n"):
    """Run the real ``verify_part`` from the real downloader against a real file.

    Sourcing the shipped script (rather than copying its logic into the test) is
    the point: an earlier version initialised its verdict to 9 and never reset it
    on success, so every good download took the "helper missing" branch, was
    published unchecked, and the log read as though the check was not installed.
    A test that greps the source cannot see that.  This one runs it.
    """
    import os
    import subprocess

    faked = tmp_path / "bin"
    faked.mkdir()
    (faked / "flock").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (faked / "flock").chmod(0o755)

    for sub in ("zim", "state", "logs"):
        (tmp_path / sub).mkdir()
    part = tmp_path / "zim" / "x.zim.part"
    part.write_bytes(payload)
    if meta4:
        (tmp_path / "zim" / "x.zim.meta4").write_text(meta4, encoding="utf-8")

    env = {**os.environ,
           "ZIM_DIR": str(tmp_path / "zim"),
           "LOG_DIR": str(tmp_path / "logs"),
           "QUEUE": str(tmp_path / "queue.tsv"),
           "PROFILE_FILE": str(tmp_path / "profile"),
           "PAUSE": str(tmp_path / "pause"),
           "CHECK": str(helper or REPO / "pi" / "bin" / "ark-zim-checksum"),
           "PATH": f"{faked}:{os.environ.get('PATH', '')}"}
    (tmp_path / "queue.tsv").write_text("# empty\n", encoding="utf-8")

    script = (f'source "{DOWNLOAD}" || exit 9\n'
              'if verify_part "$1" "$2" "x.zim"; then echo VERDICT=ok; '
              'else echo VERDICT=no; fi\n')
    run = subprocess.run(["bash", "-c", script, "t", (tmp_path / "zim" / "x.zim").as_uri(),
                          str(part)], capture_output=True, text=True, timeout=90, env=env)
    log = (tmp_path / "logs" / "zim-download.log")
    return run, (log.read_text(encoding="utf-8") if log.exists() else ""), part


def _meta4(digest: str) -> str:
    return ('<?xml version="1.0"?><metalink xmlns="urn:ietf:params:xml:ns:metalink">'
            '<file name="x.zim">'
            f'<hash type="sha-1">{"2" * 40}</hash><hash type="sha-256">{digest}</hash>'
            "</file></metalink>")


def test_a_good_download_is_published_with_the_digest_it_was_checked_against(tmp_path):
    import hashlib

    want = hashlib.sha256(b"a library on a card\n").hexdigest()
    run, log, _part = _verify(tmp_path, meta4=_meta4(want))
    assert "VERDICT=ok" in run.stdout, (run.stdout, run.stderr)
    assert "checksum ok x.zim" in log, log
    # The receipt lives beside the queue state, which the script derives from ZIM_DIR.
    receipt = (tmp_path / "zim" / "state" / "x.zim.sha256").read_text(encoding="utf-8")
    assert receipt.strip() == f"{want}  x.zim"


def test_a_mismatched_download_is_removed_and_refused(tmp_path):
    run, log, part = _verify(tmp_path, meta4=_meta4("0" * 64))
    assert "VERDICT=no" in run.stdout, (run.stdout, run.stderr)
    assert "checksum MISMATCH" in log
    assert not part.exists(), "a file that is not the published one must not survive"


def test_a_mirror_with_no_digest_keeps_the_file_and_says_unverified(tmp_path):
    run, log, part = _verify(tmp_path)
    assert "VERDICT=ok" in run.stdout, (run.stdout, run.stderr)
    assert "no published checksum" in log
    assert part.exists(), "an unpublished digest is not a reason to lose a sound file"


def test_a_missing_helper_keeps_the_file_and_says_so(tmp_path):
    run, log, part = _verify(tmp_path, helper=tmp_path / "nope" / "ark-zim-checksum")
    assert "VERDICT=ok" in run.stdout, (run.stdout, run.stderr)
    assert "is missing" in log
    assert part.exists()
