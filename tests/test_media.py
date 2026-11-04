"""The Media shelf — what a file really is, what may be promised, and what is served.

The films these tests use are generated on the spot by FFmpeg (a few kilobytes
each), never downloaded: a test that needs the internet is a test that fails on
the box this project exists for.  If FFmpeg is not installed the tests that need
it are skipped with a reason, and everything that is pure logic still runs.
"""

from __future__ import annotations

import json
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
    # Admission refuses before the body is read: a page that says nothing was
    # stored, not a redirect to a form with an error on it.
    assert answer.status_code == 507
    said = answer.text.lower()
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
def test_a_film_that_needs_repacking_is_prepared_once_and_the_original_is_released(public, ark,
                                                                                  films):
    import ark_media
    _send_video(public, films["slow"], name="Reel Two.mp4")
    media_id = _media_rows(ark)[0]["id"]
    original = ark.root / "drop" / "files" / _media_rows(ark)[0]["stored"]
    assert original.is_file()
    _publish(ark, media_id)
    _prepare_all(ark)
    row = _media_rows(ark)[0]
    assert row["plan"] == ark_media.REMUX
    assert row["play_stored"].endswith(".mp4")
    assert (ark.root / "media" / "play" / row["play_stored"]).is_file()
    assert row["stored"] == "", "the upload is released once the copy is ready"
    assert not original.exists(), "the original is removed to save room"
    with ark.db.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 0
    assert row["poster"] and (ark.root / "media" / "thumbs" / row["poster"]).is_file()
    assert ark_media.playable(row) is not None

    stream = public.client.get(f"/media/{media_id}/stream")
    assert stream.status_code == 200
    # The playable copy is a real, self-contained MP4 that starts at the beginning.
    assert stream.headers["content-type"] == "video/mp4"
    assert int(stream.headers["content-length"]) > 1000
    assert public.client.get(f"/media/{media_id}/download").status_code == 404

    page = public.client.get(f"/media/{media_id}")
    assert "<video" in page.text
    assert "removed the original upload" in page.text
    assert f"/media/{media_id}/download" not in page.text


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
    assert row["stored"] == "", "a prepared film holds only its playing copy"
    made = ark.root / "media" / "play" / row["play_stored"]
    poster = ark.root / "media" / "thumbs" / row["poster"]
    assert made.is_file() and poster.is_file()
    with ark.db.tx() as conn:
        result = ark_media.delete(conn, media_id)
    assert result["removed"] == 1
    assert result["freed"] > 0, "the space actually removed is reported"
    assert not made.exists()
    assert not poster.exists()
    assert _media_rows(ark) == []
    with ark.db.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM media_jobs").fetchone()[0] == 0
    with ark.db.tx() as conn:
        assert ark_media.delete(conn, media_id)["removed"] == 0      # idempotent


# ---------------------------------------------------------------------------
#  keeping the card lean: the upload goes once the copy can stand alone
# ---------------------------------------------------------------------------
@needs_ffmpeg
def test_a_film_that_plays_as_it_is_keeps_its_only_copy(public, ark, films):
    """A direct film has no playing copy; its original *is* the film."""
    import ark_media
    _send_video_row(ark, films["fast"], "Straight to the shelf.mp4")
    media_id = _media_rows(ark)[0]["id"]
    _publish(ark, media_id)
    _prepare_all(ark)
    row = _media_rows(ark)[0]
    assert row["plan"] == ark_media.DIRECT
    assert row["stored"] and (ark.root / "drop" / "files" / row["stored"]).is_file()
    assert row["play_stored"] == ""
    assert public.client.get(f"/media/{media_id}/download").status_code == 200


@needs_ffmpeg
def test_the_file_box_share_keeps_its_original(ark, films):
    """An adopted film's bytes are also somebody's shared file; deleting them
    would change who can download it, so the shelf only adds the copy."""
    import ark_media
    name = "shared-old.avi"
    (ark.root / "drop" / "files" / name).write_bytes(films["old"].read_bytes())
    with ark.db.tx() as conn:
        cur = conn.execute(
            "INSERT INTO files (ts, name, stored, display, size, note, sha256, hidden)"
            " VALUES ('2026-01-01T00:00:00+00:00', ?, ?, 'Shared Old.avi', ?, '', '', 0)",
            (name, name, films["old"].stat().st_size))
        file_id = int(cur.lastrowid)
        row = conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
        media_id = ark_media.adopt(conn, row)
    _publish(ark, media_id)
    _prepare_all(ark)
    row = _media_rows(ark)[0]
    assert row["plan"] == ark_media.CONVERT and row["play_stored"]
    assert row["stored"] == name, "the File Box still offers these bytes"
    assert (ark.root / "drop" / "files" / name).is_file()
    with ark.db.read() as conn:
        assert conn.execute("SELECT hidden FROM files WHERE id = ?",
                            (file_id,)).fetchone()[0] == 0


@needs_ffmpeg
def test_an_original_is_not_released_before_a_playing_copy_exists(ark, films):
    import ark_media
    _send_video_row(ark, films["old"], "Not yet.avi")
    media_id = _media_rows(ark)[0]["id"]
    with ark.db.tx() as conn:
        result = ark_media.release_original(conn, media_id)
    assert result == {"removed": 0, "freed": 0, "reason": "no copy"}
    assert _media_rows(ark)[0]["stored"]


@needs_ffmpeg
def test_the_sweep_releases_only_films_that_already_have_their_copy(ark, films):
    import ark_media
    drop = ark.root / "drop" / "files"
    (drop / "ready.mkv").write_bytes(films["slow"].read_bytes())
    (drop / "waiting.mkv").write_bytes(films["mkv"].read_bytes())
    (ark.root / "media" / "play" / "m1.mp4").write_bytes(b"\x00" * 1024)
    ready_bytes = (drop / "ready.mkv").stat().st_size
    with ark.db.tx() as conn:
        def film(stored, size, play):
            cur = conn.execute(
                "INSERT INTO files (ts, name, stored, display, size, note, sha256, hidden)"
                " VALUES ('2026-01-01T00:00:00+00:00', ?, ?, ?, ?, '', '', 1)",
                (stored, stored, stored, size))
            conn.execute(
                "INSERT INTO media (ts, file_id, title, filename, stored, size, state, plan,"
                " play_stored) VALUES ('2026-01-01T00:00:00+00:00', ?, ?, ?, ?, ?, 'published',"
                " 'convert', ?)", (int(cur.lastrowid), stored, stored, stored, size, play))
        film("ready.mkv", 5000, "m1.mp4")
        film("waiting.mkv", 3000, "")
        result = ark_media.release_ready_originals(conn)
    assert result["removed"] == 1
    assert result["freed"] == ready_bytes
    assert not (drop / "ready.mkv").exists()
    assert (drop / "waiting.mkv").is_file()
    assert sorted(r["stored"] for r in _media_rows(ark)) == ["", "waiting.mkv"]
    with ark.db.tx() as conn:
        again = ark_media.release_ready_originals(conn)
    assert again["removed"] == 0, "the sweep is idempotent"


@needs_ffmpeg
def test_preparing_again_a_film_whose_upload_was_released_says_it_is_ready(public, ark,
                                                                           films):
    import ark_media
    import ark_media_jobs
    _send_video_row(ark, films["old"], "Source Gone.avi")
    media_id = _media_rows(ark)[0]["id"]
    _publish(ark, media_id)
    _prepare_all(ark)
    assert _media_rows(ark)[0]["stored"] == ""
    with ark.db.tx() as conn:
        ark_media.enqueue(conn, media_id, "prepare")
    assert ark_media_jobs.work_once(2) is True
    with ark.db.read() as conn:
        job = conn.execute("SELECT state, detail FROM media_jobs ORDER BY id DESC"
                           " LIMIT 1").fetchone()
    assert job["state"] == "ready"
    assert "removed" in job["detail"]
    page = public.client.get(f"/media/{media_id}")
    assert "<video" in page.text


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
def test_the_owner_page_says_when_the_original_was_removed(client, ark, films):
    cookie = claim_box(client)
    _send_video_row(ark, films["old"], "Trimmed.avi")
    media_id = _media_rows(ark)[0]["id"]
    _publish(ark, media_id)
    _prepare_all(ark)
    page = get(client, "/admin/media?state=published", cookie=f"ark_admin={cookie}")
    assert "original removed" in page.text
    assert "Make a playing copy" not in page.text
    assert "Prepare again" not in page.text
    assert "Take a picture there" not in page.text, "nothing to take a picture from"
    token = csrf_of(page.text)
    answer = post(client, f"/admin/media/{media_id}/prepare", {"csrf": token},
                  cookie=f"ark_admin={cookie}")
    assert answer.status_code == 303
    assert "nothing left to prepare" in urllib.parse.unquote(answer.headers["location"])
    with ark.db.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM media_jobs WHERE state = 'queued'"
                            ).fetchone()[0] == 0


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


def test_a_playlist_cannot_make_the_box_fetch_the_internet(ark, tmp_path):
    """A file whose bytes are a list of URLs is not a film, and is not fetched."""
    import ark_media
    playlist = tmp_path / "looks-like-a-film.mp4"
    playlist.write_text("#EXTM3U\n#EXT-X-VERSION:3\n#EXTINF:10,\n"
                        "http://192.0.2.1/secret.ts\n", encoding="utf-8")
    facts = ark_media.probe(playlist)
    assert facts["ok"] is False or ark_media.plan_for(facts) == ark_media.UNSUPPORTED
    assert "hls" not in str(facts.get("container")).lower()
    # And the tools themselves are only allowed to open local files.
    import ark_media_jobs
    cmd = ark_media_jobs.build_command("ffmpeg", playlist, tmp_path / "out.mp4",
                                       {"vcodec": "h264", "acodec": "aac",
                                        "has_audio": True}, ark_media.CONVERT,
                                       1024 * 1024)
    assert cmd[cmd.index("-protocol_whitelist") + 1] == "file"


def test_the_conversion_tool_is_asked_to_report_its_progress():
    """Without this flag ffmpeg writes nothing to stdout, and Stop/timeout die."""
    import ark_media
    import ark_media_jobs
    cmd = ark_media_jobs.build_command(
        "ffmpeg", __file__, "/tmp/out.mp4",
        {"vcodec": "mpeg4", "acodec": "mp3", "has_audio": True},
        ark_media.CONVERT, 1024 * 1024)
    assert "-progress" in cmd and cmd[cmd.index("-progress") + 1] == "pipe:1"
    assert "-nostats" in cmd


@needs_ffmpeg
def test_stopping_a_running_conversion_really_stops_it(ark, tmp_path):
    """The button must stop the tool, not just write a row."""
    import threading
    import time as clock

    import ark_media
    import ark_media_jobs

    long_film = tmp_path / "long.avi"          # MPEG-4 Part 2: a real re-encode
    if not FFMPEG:
        pytest.skip("ffmpeg is not installed on this machine")
    _run(["-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=25:duration=60",
          "-f", "lavfi", "-i", "sine=frequency=440:duration=60",
          "-c:v", "mpeg4", "-q:v", "3", "-c:a", "mp3", "-shortest", str(long_film)])
    with open(long_film, "rb") as handle:
        accepted = ark_media.accept(handle.read, "long.avi", state=ark_media.STATE_PENDING)
    media_id = accepted["id"]
    with ark.db.read() as conn:
        assert ark_media.plan_for(json.loads(_media_rows(ark)[0]["probe"])) == ark_media.CONVERT
        job_id = conn.execute("SELECT id FROM media_jobs WHERE media_id = ?",
                              (media_id,)).fetchone()["id"]

    done = {}

    def runner():
        done["result"] = ark_media_jobs.work_once(1)

    thread = threading.Thread(target=runner)
    thread.start()
    deadline = clock.monotonic() + 15
    while clock.monotonic() < deadline:
        with ark.db.read() as conn:
            job = conn.execute("SELECT state FROM media_jobs WHERE id = ?",
                               (job_id,)).fetchone()
        if job and job["state"] == "running":
            break
        clock.sleep(0.05)
    clock.sleep(0.4)
    with ark.db.tx() as conn:
        ark_media.cancel(conn, media_id)
    thread.join(timeout=30)
    assert not thread.is_alive(), "the worker did not stop"
    with ark.db.read() as conn:
        job = conn.execute("SELECT state FROM media_jobs WHERE id = ?",
                           (job_id,)).fetchone()
    assert job["state"] == "cancelled"
    row = _media_rows(ark)[0]
    assert row["play_stored"] == "", "a stopped conversion must not publish a copy"
    assert list((ark.root / "media" / "play").iterdir()) == []


# ---------------------------------------------------------------------------
#  what the reviewer found: promises that were not kept
# ---------------------------------------------------------------------------
def test_a_ten_bit_film_is_converted_not_repacked():
    """A Hi10P MKV repacked byte-for-byte is a film no browser can decode."""
    import ark_media
    facts = {"ok": True, "has_video": True, "container": "mkv", "vcodec": "h264",
             "acodec": "aac", "pix_fmt": "yuv420p10le", "has_audio": True,
             "faststart": False}
    assert ark_media.plan_for(facts, "anime.mkv") == ark_media.CONVERT
    eight_bit = dict(facts, pix_fmt="yuv420p")
    assert ark_media.plan_for(eight_bit, "anime.mkv") == ark_media.REMUX


def test_a_webm_a_browser_can_play_is_not_converted():
    """ffprobe calls a .webm "matroska,webm"; the suffix says which box it is."""
    import ark_media
    facts = {"ok": True, "has_video": True, "container": "mkv", "vcodec": "vp9",
             "acodec": "opus", "pix_fmt": "yuv420p", "has_audio": True,
             "faststart": False}
    assert ark_media.plan_for(facts, "clip.webm") == ark_media.DIRECT
    # The same streams in an .mkv are not promised to a browser.
    assert ark_media.plan_for(facts, "clip.mkv") == ark_media.REMUX or \
        ark_media.plan_for(facts, "clip.mkv") == ark_media.CONVERT
    import ark_media_web
    assert ark_media_web.play_type({"play_stored": "", "filename": "clip.webm"}) == "video/webm"


def test_the_audio_verdict_describes_the_track_that_is_actually_copied():
    """Judged on the default track, encoded from the first — that is how a
    commentary ends up as a film's sound."""
    import ark_media
    probed = {"streams": [
        {"codec_type": "video", "codec_name": "h264", "pix_fmt": "yuv420p",
         "width": 320, "height": 240, "disposition": {"default": 1}},
        {"codec_type": "audio", "codec_name": "ac3", "disposition": {"default": 0}},
        {"codec_type": "audio", "codec_name": "aac", "disposition": {"default": 1}},
    ]}
    assert ark_media._stream(probed, "audio")["codec_name"] == "ac3"
    facts = {"ok": True, "has_video": True, "container": "mkv", "vcodec": "h264",
             "acodec": "ac3", "pix_fmt": "yuv420p", "has_audio": True}
    assert ark_media.plan_for(facts, "film.mkv") == ark_media.CONVERT


def test_two_captions_in_one_language_do_not_overwrite_each_other(ark):
    import ark_media
    with ark.db.tx() as conn:
        for name, text in (("talk.en.srt", b"1\n00:00:01,000 --> 00:00:02,000\nOne.\n"),
                           ("talk.en.vtt", b"WEBVTT\n\n00:00:03.000 --> 00:00:04.000\nTwo.\n")):
            ark_media.add_captions(conn, 7, name, text)
    folder = ark.root / "media" / "subs" / "7"
    files = sorted(p.name for p in folder.iterdir())
    assert files == ["en-2.vtt", "en.vtt"]
    with ark.db.read() as conn:
        rows = [dict(r) for r in ark_media.captions(conn, 7)]
    assert {r["stored"] for r in rows} == {"7/en.vtt", "7/en-2.vtt"}
    assert all(r["lang"] == "en" for r in rows)


def test_a_language_is_a_code_not_a_word_from_the_title():
    import ark_media
    assert ark_media.subtitle_language("The.Matrix.720p.srt") == "en"
    assert ark_media.subtitle_language("Movie.Spanish.srt") == "en"
    assert ark_media.subtitle_language("Film.fr.srt") == "fr"
    assert ark_media.subtitle_language("film.pt-BR.srt") == "pt"


def test_a_category_appears_only_when_a_readable_film_uses_it(ark):
    import ark_media
    facts = {"ok": True, "has_video": True, "container": "mp4", "vcodec": "h264",
             "pix_fmt": "yuv420p", "faststart": True}
    with ark.db.tx() as conn:
        ark_media.add_row(conn, file_id=0, stored="a", filename="a.mp4", size=1,
                          sha256="", facts=facts, state=ark_media.STATE_PENDING,
                          category="Rehearsal")
        assert ark_media.categories(conn) == []
        ark_media.add_row(conn, file_id=0, stored="b", filename="b.mp4", size=1,
                          sha256="", facts=facts, state=ark_media.STATE_PUBLISHED,
                          category="Repairs")
        assert ark_media.categories(conn) == ["Repairs"]


def test_a_declared_upload_that_cannot_fit_is_refused_before_it_is_written(public, ark,
                                                                         monkeypatch):
    """The body is never read: the promise is refused before the first byte.

    507 (not 413) is the honest status — the file is a legal size, the card is
    full — and it comes from admission, not from the route's own spool.
    """
    import ark_space
    from helpers import tiny_card

    tiny_card(monkeypatch, ark_space, total=100 * 1024 ** 2, free=100 * 1024 ** 2)
    answer = public.client.post("/files", files={"file": ("x.txt", b"x" * 100, "text/plain")},
                                follow_redirects=False)
    assert answer.status_code == 507
    assert "not enough room" in answer.text.lower()
    assert list((ark.root / "drop" / "files").iterdir()) == []


@needs_ffmpeg
def test_the_file_box_cannot_delete_a_film_s_original(client, ark, films):
    cookie = claim_box(client)
    _send_video_row(ark, films["fast"], "Keeper.mp4")
    with ark.db.read() as conn:
        carrier = conn.execute("SELECT * FROM files").fetchone()
    page = get(client, "/admin/community", cookie=f"ark_admin={cookie}")
    from helpers import BIG_PASSWORD
    token = csrf_of(page.text)
    answer = post(client, f"/admin/files/{carrier['id']}/delete",
                  {"current_password": BIG_PASSWORD, "csrf": token},
                  cookie=f"ark_admin={cookie}")
    assert answer.status_code == 303
    assert "Media page" in urllib.parse.unquote(answer.headers["location"])
    assert (ark.root / "drop" / "files" / carrier["stored"]).is_file()
    assert _media_rows(ark), "the film is still on the shelf"


# ---------------------------------------------------------------------------
#  film 4 on the live box, 3 Nov 2026: prepared, killed, and still "getting ready"
# ---------------------------------------------------------------------------
def _shelf_row(ark, *, plan, play_stored="", jobs=()):
    """One published film on the shelf, with the jobs it ran, oldest first."""
    import ark_media
    name = "1854edd0ad312c06-Red Riding Hoodwinked.mkv"
    (ark.root / "drop" / "files" / name).write_bytes(b"\x00" * 4096)
    with ark.db.tx() as conn:
        media_id = conn.execute(
            "INSERT INTO media (ts, title, filename, stored, size, state, plan,"
            " play_stored, vcodec, container, duration)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (ark_media.now_iso(), "Red Riding Hoodwinked", "Red Riding Hoodwinked.mkv",
             name, 4096, ark_media.STATE_PUBLISHED, plan, play_stored, "hevc", "mkv",
             416.541)).lastrowid
        for kind, state, detail in jobs:
            conn.execute(
                "INSERT INTO media_jobs (media_id, kind, state, ts, detail)"
                " VALUES (?, ?, ?, ?, ?)",
                (media_id, kind, state, ark_media.now_iso(), detail))
    return media_id


KILLED = ("Playing copy could not be made: That took longer than this box will spend"
          " on it.")


def test_a_preparation_that_failed_is_not_advertised_as_getting_ready(public, ark):
    """It said "Getting ready" for twelve hours after the job had already died.

    The film's own page fetched its row without the latest job's state, so a
    failed preparation was indistinguishable from one that had never started —
    and the branch that prints the failure could never run.
    """
    media_id = _shelf_row(ark, plan="convert", jobs=[("prepare", "failed", KILLED)])

    page = public.client.get(f"/media/{media_id}")
    assert page.status_code == 200
    assert "Getting ready" not in page.text
    assert "could not be prepared" in page.text
    assert "Ask the owner to try again" in page.text
    assert "took longer than this box" not in page.text, \
        "the box's own wording belongs on the owner's page"

    shelf = public.client.get("/media/")
    assert "Could not be prepared" in shelf.text


def test_a_picture_taken_later_does_not_erase_a_failed_preparation(public, ark):
    """A poster is a different job; its success says nothing about playing."""
    media_id = _shelf_row(
        ark, plan="convert",
        jobs=[("prepare", "failed", KILLED), ("thumbnail", "ready", "Picture taken.")])

    page = public.client.get(f"/media/{media_id}")
    assert "could not be prepared" in page.text
    assert "Picture taken." not in page.text


def test_a_film_nobody_is_working_on_does_not_say_getting_ready(public, ark):
    """Nothing queued and nothing running is not "getting ready" — it is waiting."""
    media_id = _shelf_row(ark, plan="convert")

    page = public.client.get(f"/media/{media_id}")
    assert page.status_code == 200
    assert "Getting ready" not in page.text
    assert "Ask the owner to prepare it" in page.text
    assert f"/media/{media_id}/download" in page.text, "the original is still offered"


def test_a_film_that_plays_is_ready_even_if_a_later_attempt_failed(public, ark):
    """'Prepare again' on a working film must not label the player a failure."""
    media_id = _shelf_row(ark, plan="convert", play_stored="m4.mp4",
                          jobs=[("prepare", "failed", KILLED)])
    (ark.root / "media" / "play" / "m4.mp4").write_bytes(b"\x00" * 1024)

    page = public.client.get(f"/media/{media_id}")
    assert page.status_code == 200
    assert "<video" in page.text
    assert "could not be prepared" not in page.text


def test_a_long_film_gets_the_time_the_box_really_needs():
    """Measured on the box, quota included: 24 s of 1480x1080 10-bit HEVC took
    127 s to convert.  A two-hour film therefore needs ~10.6 hours, and the
    ceiling must not cut it short (film 4 needed 37 minutes and was given 23).
    """
    import ark_media_jobs

    per_second = 127.0 / 24.0
    assert ark_media_jobs.MAX_TIMEOUT > per_second * 2 * 3600, "a 2 h film must fit"
    assert (ark_media_jobs._deadline({"duration": 2 * 3600},
                                     ark_media_jobs.CONVERT_FACTOR)
            > per_second * 2 * 3600)
    # The film that died: 416.541 s, ~37 minutes at the measured rate.
    allowed = ark_media_jobs._deadline({"duration": 416.541},
                                       ark_media_jobs.CONVERT_FACTOR)
    assert allowed > per_second * 416.541 * 1.3


def test_the_deadline_is_still_a_ceiling_not_a_promise():
    """A broken file may not pin the card for a day, whatever it claims to be."""
    import ark_media_jobs

    assert (ark_media_jobs._deadline({"duration": 10 * 3600},
                                     ark_media_jobs.CONVERT_FACTOR)
            == ark_media_jobs.MAX_TIMEOUT)
    assert ark_media_jobs.MAX_TIMEOUT <= 12 * 3600
    assert (ark_media_jobs._deadline({"duration": None},
                                      ark_media_jobs.CONVERT_FACTOR)
            == ark_media_jobs.MIN_TIMEOUT)


# ---------------------------------------------------------------------------
#  the playing copy: 720p, and the Pi's own decoder
# ---------------------------------------------------------------------------
def test_a_film_above_720p_is_prepared_at_720p_and_a_smaller_one_is_left_alone():
    """The short side decides, so a film and a phone clip both come out 720p."""
    import ark_media
    import ark_media_jobs

    def scale_for(width, height):
        cmd = ark_media_jobs.build_command(
            "ffmpeg", "in.mkv", "/tmp/out.mp4",
            {"vcodec": "hevc", "acodec": "aac", "has_audio": True,
             "width": width, "height": height},
            ark_media.CONVERT, 1024 * 1024)
        return cmd[cmd.index("-vf") + 1] if "-vf" in cmd else ""

    assert scale_for(1480, 1080) == "scale=-2:720"
    assert scale_for(1920, 1080) == "scale=-2:720"
    assert scale_for(1280, 720) == "", "720p is left alone"
    assert scale_for(640, 480) == "", "nothing is upscaled"
    assert scale_for(1080, 1920) == "scale=720:-2", "a portrait clip keeps its height"


def test_the_hardware_decoder_is_asked_for_only_when_the_device_is_there(monkeypatch,
                                                                        tmp_path):
    """A missing node is not an error: the box converts films without one."""
    import ark_media_jobs

    monkeypatch.delenv("ARK_MEDIA_HWACCEL", raising=False)
    monkeypatch.setattr(ark_media_jobs, "HWACCEL_DEVICE", str(tmp_path / "renderD128"))
    assert ark_media_jobs.hwaccel_args() == []

    (tmp_path / "renderD128").write_bytes(b"")
    assert ark_media_jobs.hwaccel_args() == ["-hwaccel", "drm"]

    monkeypatch.setenv("ARK_MEDIA_HWACCEL", "0")
    assert ark_media_jobs.hwaccel_args() == [], "the owner's switch wins"
    monkeypatch.setenv("ARK_MEDIA_HWACCEL", "1")
    assert ark_media_jobs.hwaccel_args() == ["-hwaccel", "drm"]


@needs_ffmpeg
def test_a_hardware_decoder_that_refuses_a_file_is_tried_again_on_the_cpu(ark,
                                                                         monkeypatch):
    """The device exists but will not decode this source: the film still plays."""
    import ark_media
    import ark_media_jobs

    media_id = _shelf_row(ark, plan="convert")
    with ark.db.read() as conn:
        row = ark_media.item(conn, media_id)

    seen = []

    def fake_run(cmd, *, job_id=0, deadline=0.0, on_line=None):
        seen.append(list(cmd))
        if len(seen) == 1:
            raise RuntimeError("Invalid data found when processing input")
        with open(cmd[-1], "wb") as handle:
            handle.write(b"x" * 2048)

    monkeypatch.setattr(ark_media_jobs, "hwaccel_args",
                        lambda tool=None: ["-hwaccel", "drm"])
    monkeypatch.setattr(ark_media_jobs, "_run", fake_run)
    monkeypatch.setattr(ark_media_jobs, "_short_output", lambda path, facts: "")

    made = ark_media_jobs.make_derivative(row, job_id=0, plan="convert")
    assert made.get("ok") is True, made
    assert len(seen) == 2, "one attempt with the decoder, one without"
    assert "-hwaccel" in seen[0]
    assert seen[0].index("-hwaccel") < seen[0].index("-i"), "ffmpeg wants it before -i"
    assert "-hwaccel" not in seen[1]
    with ark.db.read() as conn:
        assert ark_media.item(conn, media_id)["play_stored"] == f"m{media_id}.mp4"


@needs_ffmpeg
def test_a_conversion_that_failed_halfway_is_not_started_over(ark, monkeypatch):
    """Only a refusal before the first byte may be retried: the rest is real."""
    import ark_media
    import ark_media_jobs

    media_id = _shelf_row(ark, plan="convert")
    with ark.db.read() as conn:
        row = ark_media.item(conn, media_id)

    seen = []

    def fake_run(cmd, *, job_id=0, deadline=0.0, on_line=None):
        seen.append(list(cmd))
        if on_line is not None:
            on_line("total_size=1048576")          # the tool was writing
        raise RuntimeError("The encoder died with 20 minutes of work done")

    monkeypatch.setattr(ark_media_jobs, "hwaccel_args",
                        lambda tool=None: ["-hwaccel", "drm"])
    monkeypatch.setattr(ark_media_jobs, "_run", fake_run)

    made = ark_media_jobs.make_derivative(row, job_id=0, plan="convert")
    assert made.get("ok") is False
    assert "died" in made.get("error", "")
    assert len(seen) == 1, "a failure halfway through is not a hardware problem"


@needs_ffmpeg
def test_a_transient_database_error_does_not_throw_the_encode_away(ark, monkeypatch):
    """The daily self-heal or an owner's console must not kill a 16-minute encode.

    On the live box a reader that checkpointed the WAL at 18:11 killed job 5 with
    ``attempt to write a readonly database`` — after the film had been encoding
    for six minutes. Progress is telemetry; the film is not.
    """
    import sqlite3

    import ark_media
    import ark_media_jobs

    media_id = _shelf_row(ark, plan="convert")
    with ark.db.read() as conn:
        row = ark_media.item(conn, media_id)

    def fake_run(cmd, *, job_id=0, deadline=0.0, on_line=None):
        on_line("total_size=1048576")
        on_line("out_time_us=1000000")
        with open(cmd[-1], "wb") as handle:
            handle.write(b"x" * 2048)

    def broken(*args, **kwargs):
        raise sqlite3.OperationalError("attempt to write a readonly database")

    monkeypatch.setattr(ark_media_jobs, "hwaccel_args", lambda tool=None: [])
    monkeypatch.setattr(ark_media_jobs, "_run", fake_run)
    monkeypatch.setattr(ark_media_jobs, "_short_output", lambda path, facts: "")
    monkeypatch.setattr(ark_media, "heartbeat", broken)

    made = ark_media_jobs.make_derivative(row, job_id=0, plan="convert")
    assert made.get("ok") is True, made
    with ark.db.read() as conn:
        assert ark_media.item(conn, media_id)["play_stored"] == f"m{media_id}.mp4"
