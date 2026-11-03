"""The Media shelf — what a file really is, what may be promised, and what is served.

The films these tests use are generated on the spot by FFmpeg (a few kilobytes
each), never downloaded: a test that needs the internet is a test that fails on
the box this project exists for.  If FFmpeg is not installed the tests that need
it are skipped with a reason, and everything that is pure logic still runs.
"""

from __future__ import annotations

import shutil
import subprocess
import urllib.parse

import pytest

from helpers import claim_box, csrf_of, get, post

FFMPEG = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(not FFMPEG, reason="ffmpeg is not installed on this machine")


# ---------------------------------------------------------------------------
#  little films, made once
# ---------------------------------------------------------------------------
def _run(args) -> None:
    done = subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *args],
                          capture_output=True, check=False)
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")


@pytest.fixture(scope="session")
def films(tmp_path_factory):
    if not FFMPEG:
        pytest.skip("ffmpeg is not installed on this machine")
    folder = tmp_path_factory.mktemp("films")
    picture = ["-f", "lavfi", "-i", "testsrc2=size=320x240:rate=15:duration=4"]
    sound = ["-f", "lavfi", "-i", "sine=frequency=440:duration=4"]
    fast = folder / "fast.mp4"
    _run([*picture, *sound, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
          "-movflags", "+faststart", "-shortest", str(fast)])
    slow = folder / "slow.mp4"
    _run([*picture, *sound, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
          "-shortest", str(slow)])                      # moov at the end: needs a repack
    mkv = folder / "reel.mkv"
    _run([*picture, *sound, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
          "-shortest", str(mkv)])
    old = folder / "old.avi"
    _run([*picture, *sound, "-c:v", "mpeg4", "-c:a", "mp3", "-shortest", str(old)])
    portrait = folder / "portrait.mp4"
    _run(["-f", "lavfi", "-i", "testsrc2=size=240x320:rate=15:duration=3",
          "-f", "lavfi", "-i", "sine=frequency=300:duration=3",
          "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(portrait)])
    return {"fast": fast, "slow": slow, "mkv": mkv, "old": old, "portrait": portrait}


# ---------------------------------------------------------------------------
#  helpers
# ---------------------------------------------------------------------------
def _send_video(public, path, *, name=None, note=""):
    data = path.read_bytes() if hasattr(path, "read_bytes") else path
    return public.client.post("/files", files={"file": (name or "film.mp4", data, "video/mp4")},
                              data={"note": note}, follow_redirects=False)


def _media_rows(ark):
    with ark.db.read() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM media ORDER BY id")]


def _publish(ark, media_id):
    with ark.db.tx() as conn:
        conn.execute("UPDATE media SET state = 'published' WHERE id = ?", (media_id,))


def _prepare_all(ark):
    import ark_media_jobs
    for _ in range(40):
        if not ark_media_jobs.work_once(1):
            break


def _drop_files(ark):
    return sorted(p.name for p in (ark.root / "drop" / "files").iterdir())


# ---------------------------------------------------------------------------
#  what a file is
# ---------------------------------------------------------------------------
@needs_ffmpeg
def test_the_extension_only_decides_that_a_file_should_be_looked_at(ark, films):
    import ark_media
    direct = ark_media.probe(films["fast"])
    assert direct["ok"] and direct["vcodec"] == "h264" and direct["faststart"] is True
    assert ark_media.plan_for(direct, films["fast"]) == ark_media.DIRECT

    slow = ark_media.probe(films["slow"])
    assert ark_media.plan_for(slow, films["slow"]) == ark_media.REMUX

    mkv = ark_media.probe(films["mkv"])
    assert mkv["container"] == "mkv"
    assert ark_media.plan_for(mkv, films["mkv"]) == ark_media.REMUX

    old = ark_media.probe(films["old"])
    assert ark_media.plan_for(old, films["old"]) == ark_media.CONVERT


@needs_ffmpeg
def test_a_renamed_archive_is_not_a_film(ark, tmp_path):
    import ark_media
    fake = tmp_path / "holiday.mp4"
    fake.write_bytes(b"PK\x03\x04 this is a zip file, not a film" * 100)
    facts = ark_media.probe(fake)
    assert facts["ok"] is False
    assert "could not be read" in facts["error"].lower()
    assert ark_media.plan_for(facts) == ark_media.UNSUPPORTED


def test_the_extension_is_hidden_from_the_title():
    import ark_media
    assert ark_media.title_from_filename("The.Hunger.Games.2012.1080p.mkv") == \
        "The Hunger Games 2012 1080p"
    assert ark_media.title_from_filename("Repair_the_pump.mp4") == "Repair the pump"
    assert ark_media.title_from_filename("") == "Untitled"
    assert ark_media.human_duration(None) == "length not known"
    assert ark_media.human_duration(3725) == "1:02:05"


# ---------------------------------------------------------------------------
#  the upload doors
# ---------------------------------------------------------------------------
@needs_ffmpeg
def test_a_film_shared_in_the_file_box_waits_for_the_owner(public, ark, films):
    answer = _send_video(public, films["fast"], name="A Walk In The Woods.mp4")
    assert answer.status_code == 303
    assert answer.headers["location"].startswith("/media/?sent=")
    rows = _media_rows(ark)
    assert len(rows) == 1
    assert rows[0]["state"] == "pending"
    assert rows[0]["title"] == "A Walk In The Woods"
    with ark.db.read() as conn:
        carrier = conn.execute("SELECT * FROM files").fetchone()
    assert carrier["hidden"] == 1, "a waiting film must not be downloadable from the File Box"
    # ...and the File Box listing does not show it either.
    assert "A Walk In The Woods" not in public.client.get("/files").text


@needs_ffmpeg
def test_a_broken_file_is_refused_and_leaves_nothing_behind(public, ark):
    answer = public.client.post("/files", files={
        "file": ("broken.mp4", b"not a film at all" * 1000, "video/mp4")},
        follow_redirects=False)
    assert answer.status_code == 303
    said = urllib.parse.unquote(answer.headers["location"])
    assert "/files?err=" in said
    assert "could not be read" in said.lower()
    assert _media_rows(ark) == []
    assert _drop_files(ark) == []
    assert list((ark.root / "drop" / "tmp").iterdir()) == []


@needs_ffmpeg
def test_an_ordinary_file_still_goes_to_the_file_box(public, ark):
    answer = public.client.post("/files", files={"file": ("notes.txt", b"hello", "text/plain")},
                                follow_redirects=False)
    assert answer.headers["location"] == "/files?ok=1"
    assert _media_rows(ark) == []


@needs_ffmpeg
def test_the_box_refuses_a_film_when_the_card_is_inside_its_reserve(public, ark, films,
                                                                  monkeypatch):
    import ark_space
    monkeypatch.setattr(ark_space, "facts", lambda path=None: {
        "path": "/srv/ark", "device": 7, "total_bytes": 100 * 1024 ** 3, "free_bytes": 0,
        "reserve_bytes": 7 * 1024 ** 3, "usable_bytes": 0, "percent": 7.0})
    answer = _send_video(public, films["fast"])
    said = urllib.parse.unquote(answer.headers["location"]).lower()
    assert "nearly full" in said or "not enough room" in said
    assert _media_rows(ark) == []
    assert _drop_files(ark) == []


# ---------------------------------------------------------------------------
#  what a reader may see
# ---------------------------------------------------------------------------
@needs_ffmpeg
def test_an_unpublished_film_is_not_reachable_by_guessing_its_number(public, ark, films):
    _send_video(public, films["fast"])
    media_id = _media_rows(ark)[0]["id"]
    for path in (f"/media/{media_id}", f"/media/{media_id}/stream",
                 f"/media/{media_id}/poster", f"/media/{media_id}/download",
                 f"/media/{media_id}/captions/en"):
        assert public.client.get(path).status_code == 404, path
    listing = public.client.get("/media/")
    assert listing.status_code == 200
    assert "A Walk In The Woods" not in listing.text
    assert "The shelf is empty" in listing.text


@needs_ffmpeg
def test_a_published_film_plays_as_it_is_and_in_pieces(public, ark, films):
    _send_video(public, films["fast"], name="fast.mp4")
    media_id = _media_rows(ark)[0]["id"]
    with ark.db.tx() as conn:
        import ark_media
        ark_media.enqueue(conn, media_id, "prepare")
        conn.execute("UPDATE media SET state = 'published' WHERE id = ?", (media_id,))
    _prepare_all(ark)
    row = _media_rows(ark)[0]
    import ark_media
    assert row["plan"] == ark_media.DIRECT
    assert row["play_stored"] == "", "a direct film is not copied"

    page = public.client.get(f"/media/{media_id}")
    assert page.status_code == 200
    assert "<video" in page.text and f"/media/{media_id}/stream" in page.text
    assert "Download the original" in page.text

    size = (ark.root / "drop" / "files" / row["stored"]).stat().st_size
    whole = public.client.get(f"/media/{media_id}/stream")
    assert whole.status_code == 200
    assert whole.headers["accept-ranges"] == "bytes"
    assert whole.headers["content-length"] == str(size)
    assert whole.headers["content-type"].startswith("video/")

    piece = public.client.get(f"/media/{media_id}/stream", headers={"Range": "bytes=10-109"})
    assert piece.status_code == 206
    assert piece.headers["content-range"] == f"bytes 10-109/{size}"
    assert len(piece.content) == 100

    tail = public.client.get(f"/media/{media_id}/stream", headers={"Range": "bytes=-50"})
    assert tail.status_code == 206
    assert tail.headers["content-range"] == f"bytes {size - 50}-{size - 1}/{size}"

    beyond = public.client.get(f"/media/{media_id}/stream", headers={"Range": "bytes=99999999-"})
    assert beyond.status_code == 416
    assert beyond.headers["content-range"] == f"bytes */{size}"

    head = public.client.head(f"/media/{media_id}/stream")
    assert head.status_code == 200
    assert head.headers["content-length"] == str(size)
    assert head.content == b""


@needs_ffmpeg
def test_a_film_that_needs_repacking_is_prepared_once_and_the_original_is_kept(public, ark,
                                                                             films):
    _send_video(public, films["slow"], name="Reel Two.mp4")
    media_id = _media_rows(ark)[0]["id"]
    _publish(ark, media_id)
    _prepare_all(ark)
    row = _media_rows(ark)[0]
    import ark_media
    assert row["plan"] == ark_media.REMUX
    assert row["play_stored"].endswith(".mp4")
    assert (ark.root / "media" / "play" / row["play_stored"]).is_file()
    assert (ark.root / "drop" / "files" / row["stored"]).is_file(), "the original is kept"
    assert row["poster"] and (ark.root / "media" / "thumbs" / row["poster"]).is_file()

    stream = public.client.get(f"/media/{media_id}/stream")
    assert stream.status_code == 200
    # The playable copy is a real, self-contained MP4 that starts at the beginning.
    assert stream.headers["content-type"] == "video/mp4"
    assert int(stream.headers["content-length"]) > 1000


@needs_ffmpeg
def test_a_codec_a_browser_cannot_read_is_converted(public, ark, films):
    _send_video(public, films["old"], name="Old Reel.avi")
    media_id = _media_rows(ark)[0]["id"]
    _publish(ark, media_id)
    _prepare_all(ark)
    row = _media_rows(ark)[0]
    import ark_media
    assert row["plan"] == ark_media.CONVERT
    made = ark_media.play_dir() / row["play_stored"]
    assert made.is_file()
    facts = ark_media.probe(made)
    assert facts["vcodec"] == "h264"
    assert facts["acodec"] in ("aac", "mp3")


def test_the_poster_falls_back_to_a_neutral_card_when_there_is_none(public, ark):
    with ark.db.tx() as conn:
        cur = conn.execute(
            "INSERT INTO files (ts, name, stored, display, size, note, sha256, hidden)"
            " VALUES ('2026-01-01T00:00:00+00:00','x','x','x',1,'', '', 1)")
        conn.execute(
            "INSERT INTO media (ts, file_id, title, filename, stored, size, state, plan)"
            " VALUES ('2026-01-01T00:00:00+00:00', ?, 'No Picture', 'x.mp4', 'x', 1,"
            " 'published', 'direct')", (int(cur.lastrowid),))
    media_id = _media_rows(ark)[0]["id"]
    answer = public.client.get(f"/media/{media_id}/poster")
    assert answer.status_code == 200
    assert answer.headers["content-type"].startswith("image/svg+xml")
    assert b"Picture not ready" in answer.content


# ---------------------------------------------------------------------------
#  captions
# ---------------------------------------------------------------------------
def test_srt_becomes_webvtt_without_losing_a_line():
    import ark_media
    srt = ("1\n00:00:01,500 --> 00:00:04,000\nHello there.\n\n"
           "2\n00:01:02,250 --> 00:01:05,000\nSecond line.\n")
    vtt = ark_media.srt_to_vtt(srt)
    assert vtt.startswith("WEBVTT")
    assert "00:00:01.500 --> 00:00:04.000" in vtt
    assert "00:01:02.250 --> 00:01:05.000" in vtt
    assert "Hello there." in vtt
    assert "\n1\n" not in vtt, "the cue numbers are not part of WebVTT"


@needs_ffmpeg
def test_captions_travel_with_their_film_and_nobody_elses(public, ark, films):
    _send_video(public, films["fast"], name="Talkie.mp4")
    media_id = _media_rows(ark)[0]["id"]
    with ark.db.tx() as conn:
        import ark_media
        ark_media.add_captions(conn, media_id, "Talkie.en.srt",
                               b"1\n00:00:01,000 --> 00:00:02,000\nHi.\n")
        conn.execute("UPDATE media SET state = 'published' WHERE id = ?", (media_id,))
        other = conn.execute(
            "INSERT INTO media (ts, file_id, title, filename, stored, size, state, plan)"
            " VALUES ('2026-01-01T00:00:00+00:00', 0, 'Other', 'o.mp4', 'o', 1,"
            " 'published', 'direct')").lastrowid
    answer = public.client.get(f"/media/{media_id}/captions/en")
    assert answer.status_code == 200
    assert answer.headers["content-type"].startswith("text/vtt")
    assert "Hi." in answer.text
    assert public.client.get(f"/media/{other}/captions/en").status_code == 404
    page = public.client.get(f"/media/{media_id}")
    assert "English captions" in page.text
    assert "<track" in page.text


# ---------------------------------------------------------------------------
#  the work queue
# ---------------------------------------------------------------------------
@needs_ffmpeg
def test_an_interrupted_job_is_put_back_in_the_queue(ark, films):
    import ark_media
    _send_video_row(ark, films["fast"])
    media_id = _media_rows(ark)[0]["id"]
    with ark.db.tx() as conn:
        ark_media.claim(conn, 1)          # a worker took it and then the box went off
    with ark.db.tx() as conn:
        recovered = ark_media.recover(conn)
    assert recovered["requeued"] == 1
    with ark.db.read() as conn:
        job = conn.execute("SELECT * FROM media_jobs WHERE media_id = ?",
                           (media_id,)).fetchone()
    assert job["state"] == "queued"
    assert "switched off" in job["detail"]


@needs_ffmpeg
def test_a_job_that_stopped_three_times_is_not_retried_for_ever(ark, films):
    import ark_media
    _send_video_row(ark, films["fast"])
    with ark.db.tx() as conn:
        conn.execute("UPDATE media_jobs SET state = 'running', attempts = 3")
    with ark.db.tx() as conn:
        ark_media.recover(conn)
    with ark.db.read() as conn:
        job = conn.execute("SELECT state, detail FROM media_jobs").fetchone()
    assert job["state"] == "failed"
    assert "three times" in job["detail"]


@needs_ffmpeg
def test_stopping_a_job_is_recorded_and_the_original_is_untouched(ark, films):
    import ark_media
    _send_video_row(ark, films["slow"])
    media_id = _media_rows(ark)[0]["id"]
    with ark.db.tx() as conn:
        assert ark_media.cancel(conn, media_id) == 1
    with ark.db.read() as conn:
        job = conn.execute("SELECT state FROM media_jobs").fetchone()
    assert job["state"] == "cancelled"
    assert (ark.root / "drop" / "files" / _media_rows(ark)[0]["stored"]).is_file()
    # A cancelled job is not picked up by the worker.
    import ark_media_jobs
    assert ark_media_jobs.work_once(1) is False


# ---------------------------------------------------------------------------
#  deleting
# ---------------------------------------------------------------------------
@needs_ffmpeg
def test_deleting_a_film_removes_its_picture_its_copy_and_its_bytes(ark, films):
    import ark_media
    _send_video_row(ark, films["slow"])
    media_id = _media_rows(ark)[0]["id"]
    _publish(ark, media_id)
    _prepare_all(ark)
    row = _media_rows(ark)[0]
    original = ark.root / "drop" / "files" / row["stored"]
    made = ark.root / "media" / "play" / row["play_stored"]
    poster = ark.root / "media" / "thumbs" / row["poster"]
    assert original.is_file() and made.is_file() and poster.is_file()
    with ark.db.tx() as conn:
        result = ark_media.delete(conn, media_id)
    assert result["removed"] == 1
    assert result["freed"] > 0, "the space actually removed is reported"
    assert not original.exists()
    assert not made.exists()
    assert not poster.exists()
    assert _media_rows(ark) == []
    with ark.db.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM media_jobs").fetchone()[0] == 0
    with ark.db.tx() as conn:
        assert ark_media.delete(conn, media_id)["removed"] == 0      # idempotent


# ---------------------------------------------------------------------------
#  the owner's page
# ---------------------------------------------------------------------------
@needs_ffmpeg
def test_the_owner_page_lists_films_and_can_publish_one(client, ark, films):
    import ark_media
    cookie = claim_box(client)
    page = get(client, "/admin/media", cookie=f"ark_admin={cookie}")
    assert page.status_code == 200
    assert "The shelf right now" in page.text
    assert "may still be added" in page.text.lower()

    with ark.db.tx() as conn:
        row = conn.execute(
            "INSERT INTO files (ts, name, stored, display, size, note, sha256, hidden)"
            " VALUES ('2026-01-01T00:00:00+00:00','f.mp4','f.mp4','f.mp4',1,'', '', 1)")
        media_id = ark_media.add_row(
            conn, file_id=int(row.lastrowid), stored="f.mp4", filename="f.mp4", size=1,
            sha256="", facts={"ok": True, "has_video": True, "container": "mp4",
                              "vcodec": "h264", "acodec": "aac", "pix_fmt": "yuv420p",
                              "faststart": True, "width": 320, "height": 240,
                              "duration": 4.0}, state=ark_media.STATE_PENDING)
    listing = get(client, "/admin/media", cookie=f"ark_admin={cookie}")
    assert "f.mp4" in listing.text

    token = csrf_of(listing.text)
    published = post(client, f"/admin/media/{media_id}/state",
                     {"value": "published", "csrf": token}, cookie=f"ark_admin={cookie}")
    assert published.status_code == 303
    assert _media_rows(ark)[0]["state"] == "published"
    with ark.db.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE action = 'media-show'"
                            ).fetchone()[0] == 1


def test_the_owner_page_refuses_a_form_without_the_token(client, ark):
    cookie = claim_box(client)
    answer = post(client, "/admin/media/1/state", {"value": "published", "csrf": "wrong"},
                  cookie=f"ark_admin={cookie}")
    assert answer.status_code == 403
    assert "Nothing was changed" in answer.text


@needs_ffmpeg
def test_an_owner_can_put_a_film_from_the_file_box_on_the_shelf_without_copying_it(client,
                                                                                  ark, films):
    cookie = claim_box(client)
    public = get(client, "/admin/upload", cookie=f"ark_admin={cookie}")
    with ark.db.tx() as conn:
        cur = conn.execute(
            "INSERT INTO files (ts, name, stored, display, size, note, sha256, hidden)"
            " VALUES ('2026-01-01T00:00:00+00:00','shared.mp4','shared.mp4','Shared.mp4',"
            " 12, '', '', 0)")
        file_id = int(cur.lastrowid)
    (ark.root / "drop" / "files" / "shared.mp4").write_bytes(films["fast"].read_bytes())
    token = csrf_of(public.text)
    answer = post(client, "/admin/media/adopt", {"file_id": str(file_id), "csrf": token},
                  cookie=f"ark_admin={cookie}")
    assert answer.status_code == 303
    rows = _media_rows(ark)
    assert len(rows) == 1 and rows[0]["file_id"] == file_id
    assert rows[0]["stored"] == "shared.mp4"
    assert len(_drop_files(ark)) == 1, "adopting must not make a second copy"
    with ark.db.read() as conn:
        assert conn.execute("SELECT hidden FROM files WHERE id = ?",
                            (file_id,)).fetchone()[0] == 0


@needs_ffmpeg
def test_a_deleted_film_from_the_owner_page_says_what_went(client, ark, films):
    import ark_media
    cookie = claim_box(client)
    _send_video_row(ark, films["fast"])
    media_id = _media_rows(ark)[0]["id"]
    page = get(client, "/admin/media?state=pending", cookie=f"ark_admin={cookie}")
    token = csrf_of(page.text)
    refused = post(client, f"/admin/media/{media_id}/delete", {"csrf": token, "confirm": ""},
                   cookie=f"ark_admin={cookie}")
    assert refused.status_code == 303
    assert _media_rows(ark), "an unticked 'I am sure' must not delete anything"
    done = post(client, f"/admin/media/{media_id}/delete", {"csrf": token, "confirm": "yes"},
                cookie=f"ark_admin={cookie}")
    assert done.status_code == 303
    assert _media_rows(ark) == []


# ---------------------------------------------------------------------------
#  the catalogue
# ---------------------------------------------------------------------------
def test_search_sort_and_pages_never_show_more_than_one_screen(ark):
    import ark_media
    with ark.db.tx() as conn:
        for number in range(30):
            ark_media.add_row(
                conn, file_id=0, stored=f"s{number}", filename=f"Film {number:02d}.mp4",
                size=number * 100, sha256="",
                facts={"ok": True, "has_video": True, "container": "mp4", "vcodec": "h264",
                       "acodec": "aac", "pix_fmt": "yuv420p", "faststart": True,
                       "width": 320, "height": 240, "duration": 60 + number},
                state=ark_media.STATE_PUBLISHED)
    with ark.db.read() as conn:
        page = ark_media.listing(conn, page=1)
        assert len(page["rows"]) == ark_media.PAGE
        assert page["total"] == 30 and page["pages"] == 2 and page["has_next"] is True
        second = ark_media.listing(conn, page=2)
        assert len(second["rows"]) == 30 - ark_media.PAGE
        found = ark_media.listing(conn, q="Film 07")
        assert found["total"] == 1
        biggest = ark_media.listing(conn, sort="largest")
        assert biggest["rows"][0]["size"] == 2900
        oldest = ark_media.listing(conn, sort="title")
        assert oldest["rows"][0]["title"] == "Film 00"


def test_a_search_wildcard_is_a_character_not_a_pattern(ark):
    """A person typing % or _ means those characters, not "match anything"."""
    import ark_media

    facts = {"ok": True, "has_video": True, "container": "mp4", "vcodec": "h264",
             "faststart": True, "pix_fmt": "yuv420p"}
    with ark.db.tx() as conn:
        for name in ("a%b.mp4", "axb.mp4", "a_b.mp4", "axb again.mp4"):
            ark_media.add_row(conn, file_id=0, stored=name, filename=name, size=1,
                              sha256="", facts=facts, state=ark_media.STATE_PUBLISHED)
    with ark.db.read() as conn:
        assert ark_media.listing(conn, q="a%b")["total"] == 1
        assert ark_media.listing(conn, q="a_b")["total"] == 1
        assert ark_media.listing(conn, q="axb")["total"] == 2


# ---------------------------------------------------------------------------
#  one helper that needs the database but not the web page
# ---------------------------------------------------------------------------
def _send_video_row(ark, path, name="film.mp4"):
    import ark_media
    with open(path, "rb") as handle:
        ark_media.accept(handle.read, name, state=ark_media.STATE_PENDING,
                         added_by="test")


# ---------------------------------------------------------------------------
#  the owner's batch door recognises a film too
# ---------------------------------------------------------------------------
@needs_ffmpeg
def test_the_owners_batch_door_puts_a_film_on_the_shelf(client, ark, films):
    from helpers import LAN

    cookie = claim_box(client)
    page = get(client, "/admin/upload", cookie=f"ark_admin={cookie}")
    token = csrf_of(page.text)
    answer = client.post(
        "/admin/upload",
        files={"files": ("Holiday 2026.mp4", films["fast"].read_bytes(), "video/mp4")},
        data={"csrf": token},
        headers={**LAN, "cookie": f"ark_admin={cookie}"},
        follow_redirects=False)
    assert answer.status_code == 200, answer.text
    assert "on the Media shelf now" in answer.text
    rows = _media_rows(ark)
    assert len(rows) == 1
    assert rows[0]["state"] == "published"
    assert rows[0]["title"] == "Holiday 2026"
    assert rows[0]["added_by"] == "admin"
    # ...and it is a real shelf item, not a game and not a book.
    with ark.db.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 0


@needs_ffmpeg
def test_the_owner_can_rename_a_film_and_keep_the_file_name(client, ark, films):
    cookie = claim_box(client)
    _send_video_row(ark, films["fast"], "IMGP0421.mp4")
    media_id = _media_rows(ark)[0]["id"]
    page = get(client, "/admin/media", cookie=f"ark_admin={cookie}")
    assert "More for this film" in page.text
    assert "The name on the shelf" in page.text
    token = csrf_of(page.text)
    renamed = post(client, f"/admin/media/{media_id}/title",
                   {"title": "  The Water Filter Project  ", "csrf": token},
                   cookie=f"ark_admin={cookie}")
    assert renamed.status_code == 303
    row = _media_rows(ark)[0]
    assert row["title"] == "The Water Filter Project"
    assert row["filename"] == "IMGP0421.mp4", "the original file name is never lost"


@needs_ffmpeg
def test_a_picture_can_be_asked_for_at_a_time_the_owner_chooses(client, ark, films):
    cookie = claim_box(client)
    _send_video_row(ark, films["fast"], "clip.mp4")
    media_id = _media_rows(ark)[0]["id"]
    page = get(client, "/admin/media", cookie=f"ark_admin={cookie}")
    token = csrf_of(page.text)
    answer = post(client, f"/admin/media/{media_id}/picture",
                  {"at": "2.5", "csrf": token}, cookie=f"ark_admin={cookie}")
    assert answer.status_code == 303
    with ark.db.read() as conn:
        job = conn.execute("SELECT kind, detail FROM media_jobs ORDER BY id DESC LIMIT 1"
                           ).fetchone()
    assert job["kind"] == "thumbnail"
    assert job["detail"] == "at=2.5"
    import ark_media_jobs
    for _ in range(10):
        if not ark_media_jobs.work_once(1):
            break
    row = _media_rows(ark)[0]
    assert row["poster_at"] == pytest.approx(2.5, abs=0.05)
