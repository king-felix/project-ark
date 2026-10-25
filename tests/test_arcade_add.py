"""ARK Arcade: a game you already have, from the games page, with no sign-in.

The door is open to anyone on this box's Wi-Fi because the person holding the
game file is usually not the person who runs the box.  These tests pin what that
must and must not mean:

* a stranger with no account, no name and no cookie can put a game on the shelf
  through the same gate the owner's page uses;
* a stranger may **add** a game and may never **replace** one — a name already on
  the shelf is refused, so nobody can change the game somebody else is playing;
* a refusal leaves nothing on the card: no game, no waiting file, no second copy;
* the owner can close the door, and closing it never stops the shelf playing.

Nothing here starts a browser; the player's own white-screen defect is pinned in
``test_arcade.py`` (the page must wait for a real tap).
"""

from __future__ import annotations

import hashlib
import re


def _gb_bytes(seed: bytes = b"ARK ADD") -> bytes:
    """A tiny but well-formed Game Boy cartridge header, as a browser would send."""
    data = bytearray(0x150)
    data[0x134:0x144] = seed.ljust(16, b"\0")
    csum = 0
    for byte in data[0x134:0x14D]:
        csum = (csum - byte - 1) & 0xFF
    data[0x14D] = csum
    return bytes(data)


def _shelf(ark):
    return ark.root / "arcade" / "games"


def _waiting(ark):
    folder = ark.root / "arcade" / "tmp"
    return sorted(p.name for p in folder.iterdir()) if folder.exists() else []


def _shelf_files(ark):
    folder = _shelf(ark)
    return sorted(p.name for p in folder.iterdir()) if folder.exists() else []


def _look(public, name: str, data: bytes):
    """The first step a visitor takes: hand the box one file, with no cookie at all."""
    return public.client.post(
        "/arcade/add", files={"file": (name, data, "application/octet-stream")},
        follow_redirects=False)


def _token(text: str) -> str:
    match = re.search(r'name="token" value="([^"]+)"', text)
    assert match, text[:400]
    return match.group(1)


def _confirm(public, token: str, name: str, title: str, system: str = "gb"):
    return public.client.post(
        "/arcade/add/confirm",
        data={"token": token, "filename": name, "title": title, "system": system,
              "description": "A game somebody brought along.", "author": "",
              "release": "", "license_text": "MIT"},
        follow_redirects=False)


# ---------------------------------------------------------------------------
#  the door is open, and it is the same gate underneath
# ---------------------------------------------------------------------------
def test_anyone_can_add_a_game_without_a_name_or_a_password(public):
    """No cookie, no account, no sign-in — and the shelf gains exactly one game."""
    form = public.client.get("/arcade/add")
    assert form.status_code == 200
    assert "Add a game you already have" in form.text
    assert 'enctype="multipart/form-data"' in form.text
    assert "You do not need a name or a password" in form.text

    data = _gb_bytes()
    looked = _look(public, "Pocket Quest (Europe).gb", data)
    assert looked.status_code == 200, looked.text
    assert "Does this look right?" in looked.text
    assert "Nothing has" in looked.text and "been added yet" in looked.text
    assert _shelf_files(public.ark) == [], "looking at a file must not publish it"
    assert _waiting(public.ark), "the waiting file should be spooled, not published"

    added = _confirm(public, _token(looked.text), "Pocket Quest (Europe).gb", "Pocket Quest")
    assert added.status_code == 303, added.text
    assert added.headers["location"].startswith("/arcade/g/pocket-quest"), added.headers
    assert "ok=" in added.headers["location"]

    page = public.client.get("/arcade/g/pocket-quest")
    assert page.status_code == 200
    assert "Pocket Quest" in page.text
    assert "Pocket Quest" in public.client.get("/arcade/").text
    assert _shelf_files(public.ark) == ["pocket-quest.gb"], _shelf_files(public.ark)
    assert _waiting(public.ark) == [], "a confirmed file must not keep waiting"

    with public.ark.db.read() as conn:
        game = conn.execute("SELECT * FROM games WHERE slug = 'pocket-quest'").fetchone()
        seen = [row["actor_name"] for row in conn.execute(
            "SELECT actor_name FROM audit_log WHERE action = 'arcade-add' ORDER BY id")]
    assert game is not None
    assert game["sha256"] == hashlib.sha256(data).hexdigest()
    assert game["published"] == 1 and game["hidden"] == 0
    assert game["description"] == "A game somebody brought along."
    assert seen == ["a visitor", "a visitor"], seen


def test_a_visitor_may_add_a_game_but_never_replace_one(public):
    """The owner's page publishes revision 2 of a file it is given twice.

    This door must not: a stranger who named their file after a game already on
    the shelf would otherwise change the game somebody else is playing.
    """
    (_shelf(public.ark)).mkdir(parents=True, exist_ok=True)
    (_shelf(public.ark) / "stars.gb").write_bytes(b"the original game")
    with public.ark.db.tx() as conn:
        conn.execute(
            "INSERT INTO games (slug, title, system, core, filename, size, sha256, revision, "
            "license, license_evidence, author, description, notices, provider, entry_id, "
            "installed_at, updated_at, published, hidden) VALUES ('stars', 'Stars', 'gb', "
            "'gambatte', 'stars.gb', 17, 'abc', 1, 'MIT', 'test', 'Someone', 'A test game.', "
            "'', 'test', 'stars', '2026-10-24T00:00:00Z', '2026-10-24T00:00:00Z', 1, 0)")

    looked = _look(public, "stars.gb", _gb_bytes(b"NOT STARS"))
    assert looked.status_code == 200, looked.text
    again = _confirm(public, _token(looked.text), "stars.gb", "Stars")
    assert again.status_code == 200, again.text
    assert "There is already a game called" in again.text
    assert "Choose another name" in again.text
    assert _shelf_files(public.ark) == ["stars.gb"], "the shelf must be untouched"
    assert (_shelf(public.ark) / "stars.gb").read_bytes() == b"the original game"
    with public.ark.db.read() as conn:
        game = conn.execute("SELECT * FROM games WHERE slug = 'stars'").fetchone()
    assert game["revision"] == 1 and game["sha256"] == "abc"
    assert len(_waiting(public.ark)) == 1, "the waiting copy is kept so a new name can be given"


def test_the_same_cartridge_offered_twice_is_named_not_added_again(public):
    data = _gb_bytes(b"TWICE")
    first = _look(public, "first.gb", data)
    assert _confirm(public, _token(first.text), "first.gb", "First").status_code == 303
    assert _shelf_files(public.ark) == ["first.gb"]

    again = _look(public, "another name entirely.gb", data)
    assert again.status_code == 200
    assert "already on the shelf" in again.text
    assert "First" in again.text
    assert _shelf_files(public.ark) == ["first.gb"], "the same bytes are not a second game"
    assert _waiting(public.ark) == [], "a refused file does not stay on the card"


def test_a_file_the_box_cannot_play_is_refused_and_nothing_waits(public):
    refused = _look(public, "notes.txt", b"just some words, not a game\n")
    assert refused.status_code == 400, refused.text
    assert "That one was not added" in refused.text
    assert "cannot play that kind of file" in refused.text
    assert _shelf_files(public.ark) == []
    assert _waiting(public.ark) == []


def test_an_empty_file_is_refused_before_it_is_kept(public):
    refused = _look(public, "empty.gb", b"")
    assert refused.status_code == 400
    assert "empty" in refused.text.lower()
    assert _waiting(public.ark) == []


def test_a_token_that_is_not_one_of_ours_cannot_publish_anything(public):
    for bogus in ("../../etc/passwd", "upload-not-a-token.gb", "",
                  "upload-" + "0" * 32 + ".gb"):
        answer = _confirm(public, bogus, "x.gb", "Whatever")
        assert answer.status_code == 404, (bogus, answer.status_code)
        assert "no longer waiting" in answer.text
    assert _shelf_files(public.ark) == []
    assert _waiting(public.ark) == []


def test_a_visitor_can_change_their_mind_and_nothing_is_kept(public):
    looked = _look(public, "maybe.gb", _gb_bytes(b"MAYBE"))
    token = _token(looked.text)
    assert _waiting(public.ark)
    gone = public.client.post("/arcade/add/discard", data={"token": token},
                              follow_redirects=False)
    assert gone.status_code == 303
    assert gone.headers["location"].startswith("/arcade/add?ok=")
    assert "thrown" in gone.headers["location"]
    assert _waiting(public.ark) == []
    assert _shelf_files(public.ark) == []



def test_the_name_the_person_types_is_the_name_on_the_shelf(public):
    """The typed name decides the shelf, not the name the box recognised.

    Found on the box: the confirm step checked one slug (from the typed name) and
    the publisher derived another (from the recognised name), so a person who
    renamed a game was told it was "already on the shelf" about a game they had
    never added. Nothing was published — but the sentence was wrong, and a second
    homebrew game with the same recognised title could never be added.
    """
    looked = _look(public, "prefill.gb", _gb_bytes(b"PREFILL"))
    assert looked.status_code == 200
    filled = re.search(r'name="title" value="([^"]*)"', looked.text).group(1)
    assert filled, "the box should prefill a name"
    added = _confirm(public, _token(looked.text), "prefill.gb", "My Own Name")
    assert added.status_code == 303, added.text
    assert added.headers["location"].startswith("/arcade/g/my-own-name"), added.headers
    assert _shelf_files(public.ark) == ["my-own-name.gb"], _shelf_files(public.ark)
    with public.ark.db.read() as conn:
        game = conn.execute("SELECT * FROM games WHERE slug = 'my-own-name'").fetchone()
    assert game is not None and game["title"] == "My Own Name"
    assert "My Own Name" in public.client.get("/arcade/g/my-own-name").text


# ---------------------------------------------------------------------------
#  the owner's switch, and what closing it may not do
# ---------------------------------------------------------------------------
def test_the_door_can_be_closed_and_the_shelf_keeps_playing(public):
    (_shelf(public.ark)).mkdir(parents=True, exist_ok=True)
    (_shelf(public.ark) / "kept.gb").write_bytes(b"game")
    with public.ark.db.tx() as conn:
        conn.execute(
            "INSERT INTO games (slug, title, system, core, filename, size, sha256, revision, "
            "license, license_evidence, author, description, notices, provider, entry_id, "
            "installed_at, updated_at, published, hidden) VALUES ('kept', 'Kept', 'gb', "
            "'gambatte', 'kept.gb', 4, 'def', 1, 'MIT', 'test', '', 'A game that stays.', "
            "'', 'test', 'kept', '2026-10-24T00:00:00Z', '2026-10-24T00:00:00Z', 1, 0)")
        public.ark.db.set_setting(conn, "public_arcade_upload", "0")

    page = public.client.get("/arcade/add")
    assert page.status_code == 200
    assert "Adding games is switched off" in page.text
    assert "Everything on the shelf still plays" in page.text

    refused = _look(public, "late.gb", _gb_bytes(b"LATE"))
    assert refused.status_code == 403
    assert _waiting(public.ark) == []

    shelf = public.client.get("/arcade/")
    assert shelf.status_code == 200 and "Kept" in shelf.text
    assert public.client.get("/arcade/g/kept").status_code == 200
    assert public.client.get("/arcade/g/kept/rom").status_code == 200

    with public.ark.db.tx() as conn:
        public.ark.db.set_setting(conn, "public_arcade_upload", "1")
    assert "Add a game you already have" in public.client.get("/arcade/add").text


def test_the_owner_can_close_the_door_from_their_own_page(ark, client, public):
    """The switch lives where the owner looks, and it writes the setting it reads."""
    from helpers import LAN, claim_box, csrf_of, get

    cookie = claim_box(client)
    page = get(client, "/admin/arcade", headers=LAN, cookie=f"ark_admin={cookie}")
    assert page.status_code == 200
    assert "Anyone can add a game of their own" in page.text
    assert 'name="public_add"' in page.text
    client.post("/admin/arcade/switches",
                data={"csrf": csrf_of(page.text), "players": "1", "auto": ""},
                headers=LAN, cookies={"ark_admin": cookie}, follow_redirects=False)
    with ark.db.read() as conn:
        stored = ark.db.get_setting(conn, "public_arcade_upload", "1")
        audit = conn.execute("SELECT detail FROM audit_log WHERE action = 'arcade-switches' "
                             "ORDER BY id DESC").fetchone()
    assert stored == "0", "a switch that is not ticked means off"
    assert "anyone may add a game=off" in audit["detail"]
    assert "Adding games is switched off" in public.client.get("/arcade/add").text
