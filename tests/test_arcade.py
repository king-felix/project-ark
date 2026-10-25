"""ARK Arcade: the shelf is public, a name is not an administrator.

These tests pin the boundary the feature was built around:

* a guest plays and is asked for nothing — and cannot reach a named player's
  saves;
* a named player's saves belong to that name alone, survive a stale page, and
  can be taken away and put back;
* every page and both files the player needs come from this box, so the shelf
  still works with the internet unplugged;
* a catalogue entry cannot turn a download into an arbitrary URL, an archive
  into a path, or a renamed web page into a game.
"""

from __future__ import annotations

import hashlib
import io
import re
import zipfile

from helpers import LAN, claim_box, csrf_of, get, post

PASSWORD = "correct horse battery staple 42"
SECOND_PASSWORD = "a much better passphrase 7"


def _game(ark, slug: str = "stars", *, hidden: int = 0, published: int = 1,
          description: str = "A test game.", notices: str = "") -> int:
    data = ("game bytes for " + slug).encode()
    games = ark.root / "arcade" / "games"
    games.mkdir(parents=True, exist_ok=True)
    (games / f"{slug}.gb").write_bytes(data)
    with ark.db.tx() as conn:
        cur = conn.execute(
            "INSERT INTO games (slug, title, system, core, filename, size, sha256, revision, "
            "license, license_evidence, author, description, notices, provider, entry_id, "
            "installed_at, updated_at, published, hidden) VALUES (?, ?, 'gb', 'gambatte', ?, ?, ?, 1, "
            "'MIT', 'test entry: license MIT', 'Test Maker', ?, ?, 'test', ?, ?, ?, ?, ?)",
            (slug, slug.title(), f"{slug}.gb", len(data), hashlib.sha256(data).hexdigest(),
             description, notices, slug,
             "2026-10-24T00:00:00Z", "2026-10-24T00:00:00Z", published, hidden),
        )
        return int(cur.lastrowid)


def _register(client, name: str, password: str = PASSWORD):
    response = client.post("/arcade/player/new", data={
        "username": name, "password": password, "confirm": password,
        "next": "/arcade/player"}, follow_redirects=False)
    assert response.status_code == 303, response.text
    token = ""
    for part in response.headers.get("set-cookie", "").split(","):
        bits = part.strip().split(";")[0]
        if bits.startswith("ark_player="):
            token = bits.split("=", 1)[1]
    assert token
    page = client.get("/arcade/player", cookies={"ark_player": token})
    match = re.search(r'name="csrf" value="([^"]+)"', page.text)
    assert match
    return token, match.group(1)


# ---------------------------------------------------------------------------
#  the guest
# ---------------------------------------------------------------------------
def test_the_shelf_answers_without_a_name(public):
    page = public.client.get("/arcade/")
    assert page.status_code == 200
    assert "Games" in page.text
    assert "guest" in page.text.lower()


def test_an_empty_shelf_says_so_without_breaking_the_portal(public):
    page = public.client.get("/arcade/")
    assert page.status_code == 200
    assert "No games on the shelf yet" in page.text


def test_a_guest_cannot_reach_a_players_saves(public):
    for method, path in (("get", "/arcade/api/saves/1"),
                         ("get", "/arcade/api/saves/1/state/1"),
                         ("put", "/arcade/api/saves/1/state/1"),
                         ("delete", "/arcade/api/saves/1")):
        response = getattr(public.client, method)(path)
        assert response.status_code == 401, (method, path)


def test_the_game_page_is_entirely_local_and_has_no_guest_save_panel(public):
    _game(public.ark)
    page = public.client.get("/arcade/g/stars")
    assert page.status_code == 200
    assert 'window.EJS_pathtodata = "/arcade/emulator/";' in page.text
    assert 'src="/arcade/emulator/loader.js"' in page.text
    assert "http://" not in page.text and "https://" not in page.text
    assert "Playing as a guest" in page.text
    assert "/admin" not in page.text


def test_what_a_person_wrote_about_a_game_reaches_the_page_as_text(public):
    """The line and the credits an owner typed are shown — escaped, never markup."""
    _game(public.ark, notices="Made by <b>the neighbours</b> in 2026")
    page = public.client.get("/arcade/g/stars")
    assert page.status_code == 200
    assert "A test game." in page.text
    assert "Made by &lt;b&gt;the neighbours&lt;/b&gt; in 2026" in page.text
    assert "<b>the neighbours</b>" not in page.text


def test_the_player_and_its_files_come_from_this_box(public):
    loader = public.client.get("/arcade/emulator/loader.js")
    assert loader.status_code == 200
    assert "EJS" in loader.text
    assert public.client.get("/arcade/emulator/version.json").status_code == 200
    assert public.client.get("/arcade/static/ark-arcade.css").status_code == 200
    assert public.client.get("/arcade/static/notices.txt").status_code == 200


def test_the_emulator_route_cannot_walk_out_of_its_folder(public):
    assert public.client.get("/arcade/emulator/../api/main.py").status_code == 404
    assert public.client.get("/arcade/emulator/%2e%2e/%2e%2e/api/main.py").status_code == 404


def test_a_hidden_game_has_no_page_and_no_file(public):
    _game(public.ark, hidden=1)
    page = public.client.get("/arcade/g/stars")
    assert page.status_code == 200
    assert "That game is not here" in page.text
    assert public.client.get("/arcade/g/stars/rom").status_code == 404


# ---------------------------------------------------------------------------
#  the named player
# ---------------------------------------------------------------------------
def test_a_name_signs_in_and_the_game_page_offers_saves(public):
    _game(public.ark)
    token, csrf = _register(public.client, "Ada")
    page = public.client.get("/arcade/g/stars", cookies={"ark_player": token})
    assert page.status_code == 200
    assert "Saves for Ada" in page.text
    assert token and csrf


def test_a_save_round_trip_and_a_stale_page_is_refused(public):
    game_id = _game(public.ark)
    token, csrf = _register(public.client, "Ada")
    cookies = {"ark_player": token}
    url = f"/arcade/api/saves/{game_id}/state/1"
    headers = {"x-ark-csrf": csrf, "x-ark-revision": "0"}

    kept = public.client.put(url, content=b"first save", headers=headers, cookies=cookies)
    assert kept.status_code == 200, kept.text
    assert kept.json()["revision"] == 1
    stored = next((public.ark.root / "arcade" / "saves").rglob("state-01.state"))
    assert (stored.stat().st_mode & 0o777) == 0o600
    assert (stored.parent.stat().st_mode & 0o777) == 0o700

    listing = public.client.get(f"/arcade/api/saves/{game_id}", cookies=cookies)
    assert listing.status_code == 200
    assert listing.json()["saves"][0]["size"] == len(b"first save")

    got = public.client.get(url, cookies=cookies)
    assert got.status_code == 200
    assert got.content == b"first save"

    stale = public.client.put(url, content=b"from an old page", headers=headers, cookies=cookies)
    assert stale.status_code == 409
    assert "changed somewhere else" in stale.json()["error"]

    newer = public.client.put(url, content=b"second save",
                              headers={"x-ark-csrf": csrf, "x-ark-revision": "1"}, cookies=cookies)
    assert newer.status_code == 200
    assert newer.json()["revision"] == 2
    assert public.client.get(url, cookies=cookies).content == b"second save"

    save_id = listing.json()["saves"][0]["id"]
    deleted = public.client.delete(f"/arcade/api/saves/{save_id}",
                                   headers={"x-ark-csrf": csrf}, cookies=cookies)
    assert deleted.status_code == 200
    assert public.client.get(url, cookies=cookies).status_code == 404


def test_a_save_that_is_too_large_is_refused_before_it_is_written(public):
    import ark_arcade as arc
    game_id = _game(public.ark)
    token, csrf = _register(public.client, "Ada")
    cookies = {"ark_player": token}
    url = f"/arcade/api/saves/{game_id}/state/1"

    too_big = public.client.put(url, content=b"x" * (arc.MAX_SAVE + 1),
                                cookies=cookies,
                                headers={"x-ark-csrf": csrf, "x-ark-revision": "0"})
    assert too_big.status_code == 413
    assert "16 MB" in too_big.json()["error"]

    # Nothing was published, and nothing is left waiting in the save folder.
    assert public.client.get(url, cookies=cookies).status_code == 404
    saves = public.ark.root / "arcade" / "saves"
    assert not [p for p in saves.rglob("*") if p.is_file()]


def test_a_save_without_the_page_token_is_refused(public):
    game_id = _game(public.ark)
    token, _ = _register(public.client, "Ada")
    response = public.client.put(f"/arcade/api/saves/{game_id}/state/1",
                                 content=b"not from the page",
                                 headers={"x-ark-csrf": "wrong"}, cookies={"ark_player": token})
    assert response.status_code == 403


def test_a_player_can_keep_two_choices_for_each_game(public):
    game_id = _game(public.ark)
    token, csrf = _register(public.client, "Ada")
    cookies = {"ark_player": token}
    url = f"/arcade/api/prefs/{game_id}"

    fresh = public.client.get(url, cookies=cookies)
    assert fresh.status_code == 200, fresh.text
    assert fresh.json()["prefs"]["autosave"] is False
    assert fresh.json()["prefs"]["favourite"] is False

    wanted = {"x-ark-csrf": csrf, "content-type": "application/json"}
    kept = public.client.put(url, json={"autosave": True, "favourite": True},
                             headers=wanted, cookies=cookies)
    assert kept.status_code == 200, kept.text
    assert kept.json()["prefs"]["autosave"] is True
    assert kept.json()["prefs"]["favourite"] is True

    again = public.client.get(url, cookies=cookies)
    assert again.json()["prefs"]["autosave"] is True
    assert again.json()["prefs"]["favourite"] is True

    # A page that did not come from this shelf may not change them.
    invented = public.client.put(url, json={"autosave": False},
                                 headers={"x-ark-csrf": "wrong"}, cookies=cookies)
    assert invented.status_code == 403
    assert public.client.get(url, cookies=cookies).json()["prefs"]["autosave"] is True


def test_a_pending_save_choice_is_per_name_and_per_game(public):
    stars = _game(public.ark)
    blocks = _game(public.ark, slug="blocks")
    token, csrf = _register(public.client, "Ada")
    other, other_csrf = _register(public.client, "Grace")
    headers = {"x-ark-csrf": csrf}

    assert public.client.put(f"/arcade/api/prefs/{stars}", json={"autosave": True},
                             headers=headers, cookies={"ark_player": token}).status_code == 200
    mine = public.client.get(f"/arcade/api/prefs/{stars}", cookies={"ark_player": token}).json()
    assert mine["prefs"]["autosave"] is True
    assert public.client.get(f"/arcade/api/prefs/{blocks}",
                             cookies={"ark_player": token}).json()["prefs"]["autosave"] is False
    theirs = public.client.get(f"/arcade/api/prefs/{stars}",
                               cookies={"ark_player": other}).json()
    assert theirs["prefs"]["autosave"] is False
    assert public.client.put(f"/arcade/api/prefs/{stars}", json={"autosave": False},
                             headers={"x-ark-csrf": other_csrf},
                             cookies={"ark_player": other}).status_code == 200
    assert public.client.get(f"/arcade/api/prefs/{stars}",
                             cookies={"ark_player": token}).json()["prefs"]["autosave"] is True


def test_a_guest_cannot_keep_choices_and_a_hidden_game_has_none(public):
    game_id = _game(public.ark)
    assert public.client.get(f"/arcade/api/prefs/{game_id}").status_code == 401
    assert public.client.put(f"/arcade/api/prefs/{game_id}", json={"autosave": True},
                             headers={"x-ark-csrf": "x"}).status_code == 401
    token, csrf = _register(public.client, "Ada")
    with public.ark.db.tx() as conn:
        conn.execute("UPDATE games SET hidden = 1 WHERE id = ?", (game_id,))
    response = public.client.put(f"/arcade/api/prefs/{game_id}", json={"autosave": True},
                                 headers={"x-ark-csrf": csrf}, cookies={"ark_player": token})
    assert response.status_code == 404
    assert public.client.get(f"/arcade/api/prefs/{game_id}", cookies={"ark_player": token}).status_code == 404


def test_the_choices_are_in_the_page_and_keep_the_game_at_the_top(public):
    stars = _game(public.ark)
    blocks = _game(public.ark, slug="blocks")
    token, csrf = _register(public.client, "Ada")
    cookies = {"ark_player": token}

    page = public.client.get("/arcade/g/stars", cookies=cookies)
    assert page.status_code == 200
    assert "How you like this game" in page.text
    assert f'action="/arcade/player/prefs/{stars}"' in page.text
    assert "last_played" not in page.text

    kept = public.client.post(f"/arcade/player/prefs/{stars}",
                              data={"csrf": csrf, "favourite": "1", "autosave": "1",
                                    "next": "/arcade/g/stars"},
                              cookies=cookies, follow_redirects=False)
    assert kept.status_code == 303
    assert kept.headers["location"].startswith("/arcade/g/stars")
    again = public.client.get("/arcade/g/stars", cookies=cookies)
    assert "checked" in again.text

    shelf = public.client.get("/arcade/", cookies=cookies)
    assert shelf.status_code == 200
    assert shelf.text.index("/arcade/g/stars") < shelf.text.index("/arcade/g/blocks")
    assert "Kept at the top" in shelf.text


def test_choosing_one_thing_keeps_the_other_and_opening_a_game_is_not_a_choice(public):
    game_id = _game(public.ark)
    token, csrf = _register(public.client, "Ada")
    cookies = {"ark_player": token}
    url = f"/arcade/api/prefs/{game_id}"
    headers = {"x-ark-csrf": csrf}

    assert public.client.put(url, json={"autosave": True},
                             headers=headers, cookies=cookies).status_code == 200

    # Opening the game records that it was played.  It must not flip either
    # switch, and the page must not show the timestamp as a third choice.
    page = public.client.get("/arcade/g/stars", cookies=cookies)
    assert page.status_code == 200
    first = public.client.get(url, cookies=cookies).json()["prefs"]
    assert first["autosave"] is True
    assert first["favourite"] is False
    assert first["last_played"]

    # A request that names only the favourite keeps the stored autosave and
    # the visit time: a missing choice is not "off".
    assert public.client.put(url, json={"favourite": True},
                             headers=headers, cookies=cookies).status_code == 200
    second = public.client.get(url, cookies=cookies).json()["prefs"]
    assert second["autosave"] is True
    assert second["favourite"] is True
    assert second["last_played"] == first["last_played"]


def test_the_page_shows_the_switch_that_is_actually_stored(public):
    game_id = _game(public.ark)
    token, csrf = _register(public.client, "Ada")
    cookies = {"ark_player": token}
    assert public.client.put(f"/arcade/api/prefs/{game_id}",
                             json={"autosave": True, "favourite": True},
                             headers={"x-ark-csrf": csrf}, cookies=cookies).status_code == 200
    page = public.client.get("/arcade/g/stars", cookies=cookies)
    assert page.status_code == 200
    autosave = re.search(r'name="autosave" value="1"([^>]*)>', page.text)
    favourite = re.search(r'name="favourite" value="1"([^>]*)>', page.text)
    assert autosave and "checked" in autosave.group(1)
    assert favourite and "checked" in favourite.group(1)


def test_one_players_save_is_invisible_to_another(public):
    game_id = _game(public.ark)
    ada, ada_csrf = _register(public.client, "Ada")
    url = f"/arcade/api/saves/{game_id}/state/1"
    assert public.client.put(url, content=b"Ada's game", cookies={"ark_player": ada},
                             headers={"x-ark-csrf": ada_csrf, "x-ark-revision": "0"}).status_code == 200
    grace, grace_csrf = _register(public.client, "Grace", SECOND_PASSWORD)
    assert public.client.get(url, cookies={"ark_player": grace}).status_code == 404
    assert public.client.delete("/arcade/api/saves/1", cookies={"ark_player": grace},
                                headers={"x-ark-csrf": grace_csrf}).status_code == 404


def test_a_players_zip_can_be_put_back(public):
    game_id = _game(public.ark)
    token, csrf = _register(public.client, "Ada")
    cookies = {"ark_player": token}
    public.client.put(f"/arcade/api/saves/{game_id}/state/2", content=b"a state to keep",
                      cookies=cookies, headers={"x-ark-csrf": csrf, "x-ark-revision": "0"})

    exported = public.client.get("/arcade/player/export.zip", cookies=cookies)
    assert exported.status_code == 200
    with zipfile.ZipFile(io.BytesIO(exported.content)) as zf:
        assert "saves/stars/state-02.state" in zf.namelist()

    grace, grace_csrf = _register(public.client, "Grace", SECOND_PASSWORD)
    put_back = public.client.post(
        "/arcade/player/import.zip",
        data={"csrf": grace_csrf},
        files={"archive": ("saves.zip", exported.content, "application/zip")},
        cookies={"ark_player": grace}, follow_redirects=False)
    assert put_back.status_code == 303
    assert "ok=" in put_back.headers["location"]
    got = public.client.get(f"/arcade/api/saves/{game_id}/state/2", cookies={"ark_player": grace})
    assert got.status_code == 200
    assert got.content == b"a state to keep"


def test_an_import_cannot_walk_out_of_the_save_folder(public):
    import ark_arcade as arc
    game_id = _game(public.ark)
    _register(public.client, "Ada")
    with public.ark.db.read() as conn:
        player = conn.execute("SELECT id FROM players WHERE username = 'ada'").fetchone()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("saves/stars/../../escape.state", b"nope")
        zf.writestr("saves/stars/state-01.state", b"a real save")
        zf.writestr("not-a-save.txt", b"nope")
    with public.ark.db.read() as conn:
        result = arc.player_import_bytes(conn, int(player["id"]), buffer.getvalue())
    assert result["imported"] == 1
    assert result["skipped"]
    assert not (public.ark.root / "arcade" / "escape.state").exists()
    assert (public.ark.root / "arcade" / "saves" / str(player["id"]) / str(game_id)
            / "state-01.state").is_file()


def test_a_player_name_is_not_an_administrator(public):
    token, _ = _register(public.client, "Ada")
    admin = public.client.get("/admin/login",
                              headers={"x-forwarded-proto": "https", "x-real-ip": "192.168.1.50",
                                       "x-ark-setup-channel": "loopback"},
                              cookies={"ark_player": token})
    # Either the management door refuses a plain portal peer outright, or it
    # shows the administrator sign-in.  What it must never do is treat the
    # player's cookie as a signed-in administrator.
    assert admin.status_code in (200, 403)
    assert "ark_admin=" not in admin.headers.get("set-cookie", "")
    assert "Good to see you" not in admin.text


# ---------------------------------------------------------------------------
#  the owner's side
# ---------------------------------------------------------------------------
def test_the_owner_can_see_the_shelf_and_close_player_names(ark, client, public):
    game_id = _game(ark)
    token, save_csrf = _register(public.client, "Ada")
    cookie = claim_box(client)
    page = get(client, "/admin/arcade", headers=LAN, cookie=f"ark_admin={cookie}")
    assert page.status_code == 200
    assert "Games on the shelf" in page.text
    assert "Stars" in page.text
    assert "Anyone can use a name to keep saves" in page.text

    saved = post(client, "/admin/arcade/switches",
                 {"csrf": csrf_of(page.text)}, headers=LAN, cookie=f"ark_admin={cookie}")
    assert saved.status_code == 303
    with ark.db.read() as conn:
        assert ark.db.get_setting(conn, "arcade_players") == "0"
        assert ark.db.get_setting(conn, "arcade_auto_install") == "0"
    # With names closed, an existing session cannot save, and a new name is refused.
    closed_save = public.client.put(f"/arcade/api/saves/{game_id}/state/1",
                                    content=b"while closed",
                                    headers={"x-ark-csrf": save_csrf, "x-ark-revision": "0"},
                                    cookies={"ark_player": token})
    assert closed_save.status_code == 401
    assert public.client.get("/arcade/").status_code == 200
    refused = public.client.post("/arcade/player/new", data={
        "username": "Ada", "password": PASSWORD, "confirm": PASSWORD},
        follow_redirects=False)
    assert refused.status_code == 303
    assert "switched+off" in refused.headers["location"].lower()


def test_the_owner_can_put_a_suggestion_away_without_a_download(ark, client):
    with ark.db.tx() as conn:
        conn.execute(
            "INSERT INTO acquisition_candidates (provider, entry_id, title, system, filename, "
            "download_url, license, license_evidence, status, reason, created_at, updated_at) "
            "VALUES ('homebrew-hub', 'waternet', 'Waternet', 'gbc', 'waternet.gbc', ?, 'MIT', "
            "'test entry: MIT', 'pending', '', '2026-10-24T00:00:00Z', '2026-10-24T00:00:00Z')",
            ("https://raw.githubusercontent.com/gbdev/database/master/entries/waternet/waternet.gbc",))
        candidate_id = int(conn.execute("SELECT id FROM acquisition_candidates").fetchone()[0])
    cookie = claim_box(client)
    page = get(client, "/admin/arcade", headers=LAN, cookie=f"ark_admin={cookie}")
    assert page.status_code == 200
    assert "Waternet" in page.text
    put_away = post(client, f"/admin/arcade/suggestions/{candidate_id}/state",
                    {"action": "refused", "csrf": csrf_of(page.text)},
                    headers=LAN, cookie=f"ark_admin={cookie}")
    assert put_away.status_code == 303
    with ark.db.read() as conn:
        row = conn.execute("SELECT status, reason FROM acquisition_candidates WHERE id = ?",
                           (candidate_id,)).fetchone()
    assert row["status"] == "refused"
    assert "owner" in row["reason"]


def test_the_owner_can_add_one_suggestion_without_a_download(ark, client, monkeypatch):
    """The route that runs the real gate, with only the network call stubbed."""
    import ark_arcade_install as install
    with ark.db.tx() as conn:
        conn.execute(
            "INSERT INTO acquisition_candidates (provider, entry_id, title, system, filename, "
            "download_url, license, license_evidence, status, reason, created_at, updated_at) "
            "VALUES ('homebrew-hub', 'waternet', 'Waternet', 'gbc', 'waternet.gbc', ?, 'MIT', "
            "'test entry: MIT', 'pending', '', '2026-10-24T00:00:00Z', '2026-10-24T00:00:00Z')",
            ("https://raw.githubusercontent.com/gbdev/database/master/entries/waternet/waternet.gbc",))
        candidate_id = int(conn.execute("SELECT id FROM acquisition_candidates").fetchone()[0])
    monkeypatch.setattr(install, "install_approved",
                        lambda conn, cid: {"slug": "waternet", "installed": True,
                                           "revision": 1, "sha256": "0" * 64, "size": 1})
    cookie = claim_box(client)
    page = get(client, "/admin/arcade", headers=LAN, cookie=f"ark_admin={cookie}")
    added = post(client, f"/admin/arcade/suggestions/{candidate_id}/install",
                 {"csrf": csrf_of(page.text)}, headers=LAN, cookie=f"ark_admin={cookie}")
    assert added.status_code == 303
    assert "ok=" in added.headers["location"]
    with ark.db.read() as conn:
        audit = conn.execute("SELECT action, result, detail FROM audit_log "
                             "WHERE action = 'arcade-suggestion-install'").fetchone()
    assert audit["result"] == "ok"
    assert "revision 1" in audit["detail"]


def test_the_owner_can_take_a_game_off_the_shelf_and_put_it_back(ark, client, public):
    game_id = _game(ark)
    cookie = claim_box(client)
    page = get(client, "/admin/arcade", headers=LAN, cookie=f"ark_admin={cookie}")
    hidden = post(client, f"/admin/arcade/{game_id}/state",
                  {"action": "hide", "csrf": csrf_of(page.text)},
                  headers=LAN, cookie=f"ark_admin={cookie}")
    assert hidden.status_code == 303
    with ark.db.read() as conn:
        assert conn.execute("SELECT hidden FROM games WHERE id = ?",
                            (game_id,)).fetchone()[0] == 1
    assert "Stars" not in public.client.get("/arcade/").text

    page = get(client, "/admin/arcade", headers=LAN, cookie=f"ark_admin={cookie}")
    shown = post(client, f"/admin/arcade/{game_id}/state",
                 {"action": "show", "csrf": csrf_of(page.text)},
                 headers=LAN, cookie=f"ark_admin={cookie}")
    assert shown.status_code == 303
    assert "Stars" in public.client.get("/arcade/").text


# ---------------------------------------------------------------------------
#  the question the player asks before it downloads anything
# ---------------------------------------------------------------------------
def test_a_game_file_answers_a_size_question_as_well_as_a_download(public):
    """The player asks with HEAD first, then downloads.

    It caches games in the browser and compares the length it is told with the
    copy it already has, so a route that answers only GET returns 405 to the
    question the browser actually asked — and a browser cannot use a 405.
    """
    _game(public.ark)
    got = public.client.get("/arcade/g/stars/rom")
    assert got.status_code == 200
    head = public.client.head("/arcade/g/stars/rom")
    assert head.status_code == 200, "HEAD must be answered, not refused"
    assert head.content == b""
    assert int(head.headers["content-length"]) == int(got.headers["content-length"]) == len(got.content)


def test_a_hidden_or_absent_game_answers_a_size_question_the_same_way(public):
    """A name question must not leak a hidden game's file either."""
    _game(public.ark, hidden=1)
    assert public.client.head("/arcade/g/stars/rom").status_code == 404
    assert public.client.head("/arcade/g/nothing/rom").status_code == 404


def test_the_player_is_sized_on_a_frame_the_emulator_cannot_overwrite(public):
    """The player writes its own ``style`` attribute on its own element.

    Sizing the stage inline therefore accomplishes nothing — it is erased as
    the player starts — so the shape of the screen is set on a frame around it,
    and the console decides that shape.
    """
    _game(public.ark)
    page = public.client.get("/arcade/g/stars")
    assert '<div id="arcade-frame" style="aspect-ratio: 10 / 9">' in page.text, \
        "a Game Boy screen is nearly square"
    assert '<div id="arcade-stage" aria-label=' in page.text, \
        "the stage carries no inline style the player would erase"


# ---------------------------------------------------------------------------
#  one file the owner picked (the only door that accepts a body on :8443)
# ---------------------------------------------------------------------------

def _gb_bytes(seed: bytes = b"ARK TEST") -> bytes:
    data = bytearray(0x150)
    data[0x134:0x144] = seed.ljust(16, b"\0")
    csum = 0
    for byte in data[0x134:0x14D]:
        csum = (csum - byte - 1) & 0xFF
    data[0x14D] = csum
    return bytes(data)


def _upload(client, cookie: str, csrf: str, name: str, data: bytes):
    return client.post("/admin/arcade/upload",
                       files={"file": (name, data, "application/octet-stream")},
                       data={"csrf": csrf}, headers=LAN,
                       cookies={"ark_admin": cookie}, follow_redirects=False)


def test_the_owner_can_add_a_file_and_watches_it_become_a_game(ark, client):
    """Look at the file, confirm the words, and only then is it on the shelf."""
    cookie = claim_box(client)
    page = get(client, "/admin/arcade", headers=LAN, cookie=f"ark_admin={cookie}")
    csrf = csrf_of(page.text)

    data = _gb_bytes()
    looked = _upload(client, cookie, csrf, "Pocket Quest (Europe).gb", data)
    assert looked.status_code == 200, looked.text
    assert "Does this look right?" in looked.text
    token = re.search(r'name="token" value="([^"]+)"', looked.text).group(1)
    # The waiting file is under a name the box chose, and nothing is on the shelf.
    assert re.fullmatch(r"upload-[0-9a-f]{32}\.gb", token), token
    assert not any((ark.root / "arcade" / "games").iterdir())
    # The name is pre-filled from the file name, and the form says so plainly.
    assert 'value="Pocket Quest"' in looked.text

    added = client.post("/admin/arcade/upload/add",
                        data={"csrf": csrf, "token": token,
                              "filename": "Pocket Quest (Europe).gb",
                              "title": "Pocket Quest", "system": "gb",
                              "description": "", "author": "", "release": "",
                              "license_text": "MIT"},
                        headers=LAN, cookies={"ark_admin": cookie}, follow_redirects=False)
    assert added.status_code == 303
    assert "ok=" in added.headers["location"]

    with ark.db.read() as conn:
        game = conn.execute("SELECT * FROM games WHERE slug = 'pocket-quest'").fetchone()
        audit = conn.execute("SELECT result, detail FROM audit_log "
                             "WHERE action = 'arcade-upload' ORDER BY id DESC").fetchone()
    assert game is not None and game["title"] == "Pocket Quest"
    assert game["sha1"] == hashlib.sha1(data).hexdigest()
    assert audit["result"] == "ok" and "revision 1" in audit["detail"]
    # The waiting copy is gone and exactly one file was published.
    tmp = ark.root / "arcade" / "tmp"
    assert not tmp.exists() or not any(tmp.iterdir())
    assert len(list((ark.root / "arcade" / "games").iterdir())) == 1


def test_the_same_cartridge_offered_twice_is_named_not_added_again(ark, client):
    cookie = claim_box(client)
    page = get(client, "/admin/arcade", headers=LAN, cookie=f"ark_admin={cookie}")
    csrf = csrf_of(page.text)
    data = _gb_bytes(b"TWICE")

    first = _upload(client, cookie, csrf, "first.gb", data)
    token = re.search(r'name="token" value="([^"]+)"', first.text).group(1)
    client.post("/admin/arcade/upload/add",
                data={"csrf": csrf, "token": token, "filename": "first.gb",
                      "title": "First", "system": "gb", "license_text": "MIT"},
                headers=LAN, cookies={"ark_admin": cookie}, follow_redirects=False)

    again = _upload(client, cookie, csrf, "another name entirely.gb", data)
    assert again.status_code == 200
    assert "already on the shelf" in again.text
    assert "First" in again.text
    assert len(list((ark.root / "arcade" / "games").iterdir())) == 1


def test_a_file_the_box_cannot_play_is_refused_and_changes_nothing(ark, client):
    cookie = claim_box(client)
    page = get(client, "/admin/arcade", headers=LAN, cookie=f"ark_admin={cookie}")
    csrf = csrf_of(page.text)

    refused = _upload(client, cookie, csrf, "notes.txt", b"just some words\n")
    assert refused.status_code == 400
    assert "Nothing was added" in refused.text
    games = ark.root / "arcade" / "games"
    assert not games.exists() or not any(games.iterdir())
    # Nothing is left waiting either: a refusal does not keep bytes on the card.
    assert not (ark.root / "arcade" / "tmp").exists() or not any(
        (ark.root / "arcade" / "tmp").iterdir())


def test_an_upload_without_a_sign_in_writes_nothing(ark, client, public):
    """The upload door is on the sign-in origin; a stranger cannot use it."""
    cookie = claim_box(client)
    page = get(client, "/admin/arcade", headers=LAN, cookie=f"ark_admin={cookie}")
    csrf = csrf_of(page.text)
    refused = public.client.post(
        "/admin/arcade/upload",
        files={"file": ("x.gb", _gb_bytes(), "application/octet-stream")},
        data={"csrf": csrf}, follow_redirects=False)
    assert refused.status_code in (401, 403, 404)
    assert not (ark.root / "arcade" / "tmp").exists() or not any(
        (ark.root / "arcade" / "tmp").iterdir())

