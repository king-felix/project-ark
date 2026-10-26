"""Two numbers that must never disagree again, and one listener that must stay local.

Both were recorded defects: nginx accepted a 4 GiB request body while the
backend refused anything over 2 GiB (so a 3 GiB upload was read from the network
and then thrown away), and ``kiwix-serve`` listened on every address, so the
library could be reached without going through the proxy at all.

The last group is the other half of the same idea: the book shelf is a reading
door.  Its routes were enumerated from the installed Calibre-Web 0.6.27 url_map,
so "management is not reachable from the portal" is a checked fact and not a
hope.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
NGINX = (REPO / "pi" / "nginx" / "ark.conf").read_text(encoding="utf-8")
KIWIX = (REPO / "pi" / "systemd" / "ark-kiwix.service").read_text(encoding="utf-8")
PORTAL_PHASE = (REPO / "pi" / "steps" / "60-portal.sh").read_text(encoding="utf-8")
MAIN_APP = (REPO / "pi" / "api" / "main.py").read_text(encoding="utf-8")
EMULATOR = REPO / "pi" / "arcade" / "emulatorjs"

UNITS = {"k": 1024, "m": 1024 ** 2, "g": 1024 ** 3}


def _nginx_body_limit() -> int:
    match = re.search(r"^\s*client_max_body_size\s+(\d+)([kKmMgG])\s*;", NGINX, re.M)
    assert match, "client_max_body_size is missing from the portal server block"
    return int(match.group(1)) * UNITS[match.group(2).lower()]


def test_nginx_allows_the_app_limit_plus_framing_only():
    import ark_storage

    limit = _nginx_body_limit()
    assert limit >= ark_storage.MAX_FILE, "nginx would refuse a file the backend accepts"
    assert limit <= ark_storage.MAX_FILE + 16 * 1024 ** 2, (
        "nginx must not accept bodies the backend will only throw away"
    )


def test_ssh_stays_key_and_password_without_root_and_secrets_stay_out_of_git():
    """The first-boot convenience must not quietly weaken SSH, and no secret ships."""
    firstrun = (REPO / "staging" / "firstrun.sh.tmpl").read_text(encoding="utf-8")
    assert "PasswordAuthentication yes" in firstrun
    assert "PubkeyAuthentication yes" in firstrun
    assert "PermitRootLogin no" in firstrun
    assert "/home/pi/.ssh/authorized_keys" in firstrun

    ignore = (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()
    for secret in ("staging/wifi.env", "staging/firstrun.sh",
                   "staging/pi-password.txt", "staging/*.nmconnection"):
        assert secret in ignore, secret


def test_the_library_engine_is_only_reachable_through_the_proxy():
    assert "--address=127.0.0.1" in KIWIX, "kiwix-serve must not listen on every interface"
    assert "--port=8081" in KIWIX


def test_the_admin_area_is_not_proxied_on_the_public_port():
    assert "location ^~ /admin { return 404; }" in NGINX


def _backend_pages() -> list[str]:
    """The pages ``location /`` must not be allowed to answer for."""
    for line in NGINX.splitlines():
        stripped = line.strip()
        if stripped.startswith("location ~ ^/(") and "search" in stripped:
            inner = stripped.split("^/(", 1)[1].rsplit(")$", 1)[0]
            return inner.split("|")
    raise AssertionError("ark.conf has no proxy location for the backend-built pages")


# ---------------------------------------------------------------------------
#  the portal is for someone who has never heard of any of this
# ---------------------------------------------------------------------------
def test_the_portal_stays_dark_large_and_self_contained():
    """OLED-dark, 16 px or better, 44 px tap targets, and nothing fetched from outside.

    These are the promises the household layer rests on: the pages are read on a
    phone, in the dark, often by somebody who is frightened, and they are read with
    the internet unplugged.  A webfont or a CDN stylesheet would break the second
    half of that silently — the page still renders, just wrong, or not at all.
    """
    css = (REPO / "pi" / "portal" / "assets" / "ark.css").read_text(encoding="utf-8")
    assert "--bg:#000" in css, "the portal must stay true black for an OLED phone"
    size = re.search(r"body\{[^}]*font:\s*\d+\s+(\d+)px", css, re.S)
    assert size, "the body font size is no longer a plain pixel value"
    assert int(size.group(1)) >= 16, "16 px is the floor a phone can read in the dark"
    assert "min-height:44px" in css or "min-height:48px" in css, \
        "every tap target needs 44 px of height"

    for name in ("index.html", "help.html"):
        page = (REPO / "pi" / "portal" / name).read_text(encoding="utf-8")
        # the box's own three names are not "outside" — the page offers them as links
        # and prints them as text ("open http://ark.lan in your normal browser")
        own = page.replace("http://ark.lan", "").replace("http://ark.local", "").replace(
            "http://10.42.0.1", "")
        for loaded in (r"src\s*=\s*[\"']?(?:https?:)?//",
                       r"href\s*=\s*[\"']?(?:https?:)?//",
                       r"url\(\s*[\"']?(?:https?:)?//",
                       r"@import"):
            assert not re.search(loaded, own, re.I), \
                f"{name} loads something from outside the box ({loaded})"


def test_a_keyboard_can_skip_the_front_page_to_what_matters():
    """Both static pages offer the skip link, and it points at something real.

    A page whose skip link points at an ``id`` that is not there is worse than no
    skip link: the keyboard lands nowhere and the reader concludes the page is
    broken.  The rule is one link, first in the tab order, aimed at a ``<main>``
    that exists — the same shell the backend pages use (``main.page()``).
    """
    css = (REPO / "pi" / "portal" / "assets" / "ark.css").read_text(encoding="utf-8")
    assert ".skip{" in css and ".skip:focus{left:0}" in css, \
        "the skip link must be off-screen until it is focused"
    assert "main{display:block}" in css

    for name in ("index.html", "help.html"):
        page = (REPO / "pi" / "portal" / name).read_text(encoding="utf-8")
        body = page.split("<body>", 1)[1]
        first = body.lstrip().splitlines()[0].strip()
        assert first == '<a class="skip" href="#main">Skip to the main content</a>', \
            f"{name} does not start its body with the skip link"
        assert '<main id="main">' in page, f"{name} has no target for the skip link"
        assert page.count("<main") == 1 and page.count("</main>") == 1, \
            f"{name} must have exactly one main region"


def test_every_page_the_backend_builds_is_named_in_the_proxy_block():
    """A page that is not named still answers **200** — with the front page.

    ``location /`` ends in ``try_files $uri $uri/ /index.html``, so ``/household``
    and ``/cards`` would both return the portal's own front page with a healthy
    status code if the proxy block did not name them: the door looks open and the
    page is wrong.  The same trap made ``/essentials.zip`` a download link that
    handed back HTML.  ``60-portal.sh`` asks what the body says, and this keeps the
    three names in the shipped configuration.
    """
    pages = _backend_pages()
    for name in ("search", "board", "files", "household", "cards"):
        assert name in pages, f"/{name} would fall through to index.html"
    assert r"essentials\.zip" in pages, "/essentials.zip would answer with index.html"

    # a page nobody can reach from the front page is a page nobody uses
    front = (REPO / "pi" / "portal" / "index.html").read_text(encoding="utf-8")
    for door in ('href="/household"', 'href="/cards"'):
        assert door in front, f"the front page does not offer {door}"

    # and the installer proves the two pages by what they say, not by the code alone;
    # each needle is a sentence only that page carries, so the front page (which
    # `location /` would hand back) cannot pass for it
    for needle in ("The page for the day something goes wrong",
                   "Three addresses, three squares"):
        assert needle in PORTAL_PHASE, (
            f"60-portal.sh no longer proves the page that says {needle!r}")
        assert needle in MAIN_APP, f"no page says {needle!r} any more"


# ---------------------------------------------------------------------------
#  the offline player: every byte it will ask for is here, and is the byte named
# ---------------------------------------------------------------------------
def _manifest() -> dict[str, str]:
    wanted = {}
    for line in (EMULATOR / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, _, rel = line.partition("  ")
        assert rel and len(digest) == 64, f"malformed SHA256SUMS line: {line!r}"
        wanted[rel] = digest
    return wanted


def test_every_vendored_player_file_matches_its_checksum():
    manifest = _manifest()
    actual = {p.relative_to(EMULATOR).as_posix()
              for p in EMULATOR.rglob("*") if p.is_file() and p.name != "SHA256SUMS"}
    assert actual == set(manifest), (
        "SHA256SUMS must list every vendored file and nothing else: "
        f"unlisted={sorted(actual - set(manifest))} missing={sorted(set(manifest) - actual)}")
    for rel, digest in manifest.items():
        assert hashlib.sha256((EMULATOR / rel).read_bytes()).hexdigest() == digest, rel


def test_the_player_has_every_add_on_it_loads_at_run_time():
    """The decompressors are fetched only when a game archive is opened.

    They are not imported by the page and nothing else references them, so their
    absence is invisible until a phone with no internet tries to play.  Every
    ``compression/…`` the minified player mentions must exist and be listed.
    """
    player = (EMULATOR / "emulator.min.js").read_text(encoding="utf-8", errors="replace")
    referenced = set(re.findall(r"compression/[A-Za-z0-9_.-]+", player))
    assert "compression/extractzip.js" in referenced
    assert "compression/libunrar.wasm" in referenced
    manifest = _manifest()
    for rel in referenced:
        assert (EMULATOR / rel).is_file(), rel
        assert rel in manifest, f"{rel} is served but not covered by SHA256SUMS"


def test_the_installer_proves_the_player_before_serving_it():
    assert "SHA256SUMS" in PORTAL_PHASE
    assert "sha256sum --quiet -c SHA256SUMS" in PORTAL_PHASE
    for rel in ("compression/extractzip.js", "compression/extract7z.js",
                "compression/libunrar.js", "compression/libunrar.wasm"):
        assert rel in PORTAL_PHASE, rel


# ---------------------------------------------------------------------------
#  the book shelf is a reading door, not a management console
#
#  R3 was recorded as NOT STARTED: Calibre-Web's own management routes were
#  reachable through the portal.  The answer is not a list of the routes we
#  remembered — it is a list read out of the installed package's url_map
#  (``calibreweb/cps/*.py``, Calibre-Web 0.6.27, 231 rules, read on the box
#  2026-10-25) and kept here, so a future Calibre upgrade that adds a route is
#  a failing test rather than a quiet hole.
# ---------------------------------------------------------------------------
def _locations() -> list[tuple[str, str, str]]:
    """Every multi-line ``location`` block in the shipped config: (kind, rule, body)."""
    lines = NGINX.splitlines()
    found: list[tuple[str, str, str]] = []
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped.startswith("location ") and stripped.endswith("{"):
            head = stripped[len("location "):-1].strip()
            if head.startswith("^~"):
                kind, rule = "prefix-priority", head[2:].strip()
            elif head.startswith("~"):
                kind, rule = "regex", head[1:].strip()
            else:
                kind, rule = "prefix", head
            body: list[str] = []
            i += 1
            while i < len(lines) and lines[i].strip() != "}":
                body.append(lines[i])
                i += 1
            found.append((kind, rule, "\n".join(body)))
        i += 1
    return found


def _books_deny() -> tuple[str, str]:
    """(pattern, body) of the /books location that answers 404."""
    for kind, rule, body in _locations():
        if kind == "regex" and rule.startswith("^/books/(?:") and "admin|" in rule:
            return rule, body
    raise AssertionError("the /books 404 door is missing from pi/nginx/ark.conf")


def _books_read_location() -> str:
    for kind, rule, body in _locations():
        if kind == "prefix" and rule == "/books":
            return body
    raise AssertionError("the /books reading door is missing from pi/nginx/ark.conf")


#: Management and account routes a visitor could reach with GET alone.  Each one
#: must answer 404 at the proxy, because a method rule cannot stop a GET.
MANAGEMENT_GET = [
    "/books/admin", "/books/admin/view", "/books/admin/config", "/books/admin/dbconfig",
    "/books/admin/usertable", "/books/admin/user/new", "/books/admin/user/3",
    "/books/admin/mailsettings", "/books/admin/resetpassword/3", "/books/admin/logfile",
    "/books/admin/logdownload/1", "/books/admin/debug", "/books/admin/alive",
    "/books/admin/scheduledtasks", "/books/admin/book/3", "/books/admin/book/convert/3",
    "/books/ajax/listusers", "/books/ajax/domainlist/1", "/books/ajax/log/1",
    "/books/ajax/pathchooser/", "/books/ajax/getlocale",
    "/books/reconnect", "/books/stats", "/books/tasks", "/books/register",
    "/books/login", "/books/logout", "/books/me", "/books/generate_auth_token/3",
    "/books/deleteauthtoken/3", "/books/import_ldap_users", "/books/link/github",
    "/books/unlink/github", "/books/link/google", "/books/unlink/google",
    "/books/watch/subscribe", "/books/watch/revoke", "/books/watch/callback",
    "/books/verify/abc123", "/books/metadata_backup", "/books/metadata/search",
    "/books/metadata/provider/gb", "/books/get_update_status", "/books/get_updater_status",
    "/books/authenticate", "/books/callback", "/books/shelf/create", "/books/shelf/edit/3",
    "/books/shelf/delete/3", "/books/shutdown",
]

#: Routes that only ever change something with a method other than GET, so
#: ``limit_except GET`` is what refuses them.
MUTATING_POST = [
    "/books/upload", "/books/delete/3", "/books/delete/3/epub",
    "/books/ajax/deletebook", "/books/ajax/mergebooks", "/books/ajax/archiveselectedbooks",
    "/books/ajax/editbooks/3", "/books/ajax/editselectedbooks", "/books/ajax/xchange",
    "/books/ajax/fullsync", "/books/ajax/fullsync/3", "/books/ajax/simulatedbchange",
    "/books/ajax/deleteuser", "/books/ajax/editlistusers/3", "/books/ajax/canceltask",
    "/books/ajax/updateThumbnails", "/books/ajax/view", "/books/ajax/table_settings",
    "/books/ajax/toggleread/3", "/books/ajax/togglearchived/3",
    "/books/ajax/bookmark/3/epub", "/books/ajax/sort_value/title/3",
    "/books/shelf/add/3/7", "/books/shelf/massadd/3", "/books/shelf/massremove/3",
    "/books/shelf/order/3", "/books/shelf/remove/3/7", "/books/send/3/epub/0",
    "/books/send/3/epub/1", "/books/admin/ajaxconfig", "/books/admin/ajaxdbconfig",
    "/books/v1/library/sync", "/books/login", "/books/register",
]

#: What a reader must still be able to do.  A route here that the 404 door
#: swallows is a broken shelf, which is how this defect would come back.
READING = [
    "/books/", "/books/page/2", "/books/table", "/books/book/3", "/books/show/3/epub",
    "/books/show/3/epub/any-name", "/books/download/3/epub", "/books/download/3/epub/x.epub",
    "/books/read/3/epub", "/books/search", "/books/category", "/books/author",
    "/books/publisher", "/books/series", "/books/ratings", "/books/formats",
    "/books/language", "/books/cover/3", "/books/cover/3/sm", "/books/series_cover/3",
    "/books/series_cover/3/sm", "/books/simpleshelf/3", "/books/shelf/3",
    "/books/shelf/3/title", "/books/shelf/3/title/2", "/books/basic",
    "/books/basic_book/3", "/books/robots.txt", "/books/static/js/main.js",
    "/books/get_tags_json", "/books/get_authors_json", "/books/get_series_json",
    "/books/get_matching_tags", "/books/ajax/listbooks", "/books/ajax/book/ab12",
    "/books/ajax/displayselectedbooks", "/books/opds", "/books/opds/new",
    "/books/opds/author", "/books/opds/search/water", "/books/opds/shelf/3",
    "/books/3B2C/300/300/None/None/image.jpg", "/books/advsearch",
]


def test_the_book_shelf_answers_404_for_every_management_route():
    pattern, body = _books_deny()
    deny = re.compile(pattern)
    assert "proxy_pass" not in body, "a refused route must not be handed to the library"
    assert "return 404" in body
    missed = [path for path in MANAGEMENT_GET if not deny.match(path)]
    assert not missed, f"reachable management routes: {missed}"


def test_the_book_shelf_refuses_every_method_that_could_change_it():
    pattern, _ = _books_deny()
    deny = re.compile(pattern)
    door = _books_read_location()
    assert "limit_except GET { deny all; }" in door, "the reading door must be GET/HEAD only"
    # A POST is refused either by name or by method; nothing is left to chance.
    unrefused = [p for p in MUTATING_POST if not deny.match(p)]
    assert unrefused, "every mutating route should be named above; this list must not empty"
    for path in unrefused:
        assert "limit_except GET" in door, path


def test_the_one_post_that_only_reads_has_its_own_door():
    """Advanced search posts a form and changes nothing — and nothing else may."""
    doors = [(rule, body) for kind, rule, body in _locations()
             if kind == "regex" and "advsearch" in rule]
    assert len(doors) == 1, f"expected one advanced-search door, found {len(doors)}"
    rule, body = doors[0]
    assert rule == "^/books/(?:advsearch)/?$"
    assert "proxy_pass http://ark_calibre;" in body
    assert "limit_except" not in body, "advanced search must keep its POST"
    assert body.count("X-Script-Name      /books;") == 1


def test_every_reading_route_survives_the_door():
    deny = re.compile(_books_deny()[0])
    broken = [path for path in READING if deny.match(path)]
    assert not broken, f"the 404 door would break reading: {broken}"


def test_the_block_cannot_break_the_importer_because_it_never_uses_the_proxy():
    """The book importer talks to 127.0.0.1:8083 itself.

    Refusing /upload and /login at the proxy is only safe while that stays true;
    if the importer ever went through nginx it would lock itself out.
    """
    importer = (REPO / "pi" / "bin" / "ark-import-books").read_text(encoding="utf-8")
    assert 'BASE_URL = "http://127.0.0.1:8083"' in importer
    assert "127.0.0.1:8083" in importer.split("def ", 1)[0]


def test_the_installer_proves_the_book_door_on_the_box():
    """A config that is only checked when someone reads it is not a door."""
    assert "/books/" in PORTAL_PHASE
    for proof in ("/books/admin", "/books/upload", "/books/login"):
        assert proof in PORTAL_PHASE, proof


# ---------------------------------------------------------------------------
#  the player's box must have a height to fill
# ---------------------------------------------------------------------------
def test_the_player_box_has_a_height_to_fill():
    """The player's canvas is ``height: 100%`` of its box.

    With no height on the box the canvas collapses to nothing, the game runs
    perfectly and invisibly behind a black rectangle, and it looks exactly like
    "the player does not load".  That is how it was found, on a phone.

    The height lives on a frame we own, never on the stage: the player writes
    ``--ejs-primary-color`` into its own element's ``style`` attribute and so
    erases anything we put there.
    """
    css = (REPO / "pi" / "arcade" / "static" / "ark-arcade.css").read_text(encoding="utf-8")

    frame = re.search(r"#arcade-frame\s*\{([^}]*)\}", css)
    assert frame, "the player needs a frame rule of its own"
    body = frame.group(1)
    assert "aspect-ratio" in body or "height:" in body, (
        "the player's box needs a definite height or an aspect ratio: " + body)

    stage = re.search(r"#arcade-stage[^{]*\{([^}]*)\}", css)
    assert stage, "#arcade-stage must be told to fill the frame"
    assert "height: 100%" in stage.group(1), (
        "the stage must fill the frame's height: " + stage.group(1))


def test_every_console_has_a_screen_shape():
    """A Game Boy screen is nearly square; a Game Boy Advance screen is wide."""
    import ark_arcade

    for system, facts in ark_arcade.SYSTEMS.items():
        assert re.fullmatch(r"\d+ / \d+", facts["aspect"]), (system, facts)


# ---------------------------------------------------------------------------
#  the door a game file arrives through
# ---------------------------------------------------------------------------
def test_a_game_a_visitor_picks_has_a_door_of_its_own_on_both_origins():
    """One exact path takes a 64 MB body; every other path under /arcade/ does not.

    A save is at most 16 MiB, so ``/arcade/`` keeps its 20m on the public portal.
    A game file needs MAX_DOWNLOAD plus multipart framing, and that needs naming
    on the origin the file is actually uploaded through — the public portal and
    the secure player origin both serve the same page.
    """
    import ark_arcade_install

    cap = ark_arcade_install.MAX_DOWNLOAD
    assert cap == 64 * 1024 * 1024, "the arcade's own cap is what nginx must allow for"
    wanted = "68m"     # 64 MB + multipart framing

    for name in ("ark.conf", "ark-players.conf"):
        text = (REPO / "pi" / "nginx" / name).read_text(encoding="utf-8")
        door = re.search(r"location\s*=\s*/arcade/add\s*\{(.*?)\n\s*\}", text, re.S)
        assert door, f"{name} has no exact-match door for /arcade/add"
        body = door.group(1)
        assert f"client_max_body_size {wanted};" in body, (name, body)
        assert "proxy_request_buffering off;" in body, (
            f"{name} must stream the body, or a phone waits for all 64 MB first")


def test_the_save_door_did_not_grow_to_a_game_size():
    """The public /arcade/ prefix stays small: a save is 16 MiB, never a game."""
    text = (REPO / "pi" / "nginx" / "ark.conf").read_text(encoding="utf-8")
    prefix = re.search(r"location /arcade/ \{(.*?)\n\s*\}", text, re.S)
    assert prefix, "the public shelf location is missing"
    assert "client_max_body_size 20m;" in prefix.group(1), prefix.group(1)


# ---------------------------------------------------------------------------
#  the library catalogue must be built where the bundles live
# ---------------------------------------------------------------------------
def test_the_library_catalogue_is_built_beside_the_bundles_it_describes():
    """kiwix-manage records each path relative to the catalogue's own folder.

    Built in /tmp and then moved into place, every path came out as
    ``/srv/ark/srv/ark/kiwix/<name>.zim``: the library listed twenty books it
    could not open, the viewer answered 200, search answered
    "Content not found" for every name, and nothing anywhere said why.
    """
    script = (REPO / "pi" / "bin" / "ark-kiwix-library").read_text(encoding="utf-8")
    assert 'mktemp "$ZIM_DIR/' in script, (
        "the index must be built inside the folder it describes, or its paths are wrong")
    assert 'mv -f "$1" "$LIB"' in script, (
        "publish by rename (publish()), so a half-built catalogue is never served")
