"""An optional shelf that breaks must not take the rest of the box with it.

The reviewed defect: importing ``main`` called ``ark_media.ensure_dirs()`` before
the shared startup had finished, so a ``media/thumbs`` path replaced by a stray
file raised ``FileExistsError`` and the **API never started** — no library, no
books, no board, no games.  These tests hold the door shut against that class of
fault: a damaged optional folder, a missing video tool, and an absent helper are
each measured by the health page and none of them stops the portal.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi.testclient import TestClient


def _break(path: Path) -> None:
    """Replace a directory with a plain file — the reviewed failure, exactly."""
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()
    path.write_text("this is not a folder", encoding="utf-8")


def _api(ark):
    """Import the real app *after* the damage, as a cold boot would."""
    import main
    return TestClient(main.app)


def test_the_api_starts_with_a_damaged_media_folder(ark):
    import ark_media

    _break(ark_media.thumbs_dir())
    with _api(ark) as client:
        assert client.get("/api/health").status_code == 200
        # The rest of the box is still a box.  The home page itself is a static
        # file served by nginx, so the API's own doors are what is proved here.
        assert client.get("/board").status_code == 200
        assert client.get("/library").status_code == 200
        # The film shelf answers with a page, not a traceback, even though the
        # folder it wants for posters is not a folder.
        shelf = client.get("/media/")
        assert shelf.status_code == 200
        assert "Films" in shelf.text or "film" in shelf.text.lower()


def test_the_api_starts_with_a_damaged_shared_folder(ark):
    import ark_storage

    _break(ark_storage.drop_dir())
    with _api(ark) as client:
        assert client.get("/api/health").status_code == 200
        page = client.get("/files")
        # A readable page that names the trouble, never an unhandled exception.
        assert page.status_code == 200
        assert "File Box" in page.text


def test_a_missing_video_tool_does_not_stop_the_portal(ark, monkeypatch):
    monkeypatch.setenv("ARK_FFMPEG", "/nonexistent/ffmpeg")
    monkeypatch.setenv("ARK_FFPROBE", "/nonexistent/ffprobe")
    with _api(ark) as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/media/").status_code == 200


def test_an_unavailable_helper_does_not_stop_the_portal(ark):
    # The shared fixture already points the helper at a dead loopback port.
    with _api(ark) as client:
        assert client.get("/api/health").status_code == 200
        page = client.get("/ask")
        assert page.status_code == 200
        assert "helper is not fitted" in page.text
        # The rest of the reading path answered on the same process.
        assert client.get("/media/").status_code == 200


def test_a_broken_optional_catalogue_is_a_sentence_not_a_crash(ark):
    """A malformed offline game catalogue is information, never a gate."""
    import ark_health

    bad = ark.root / "romdb.sqlite"
    bad.write_bytes(b"this is not a SQLite database at all")
    section = ark_health.games_section()
    # The shelf itself may be empty, but the check answers rather than raising.
    assert section["key"] == "games"
    assert isinstance(section["ready"], bool)
    assert section["reason"]


def test_direct_playback_survives_a_broken_poster_folder(ark):
    """Only preparation is unavailable — the bytes a viewer needs still play."""
    import ark_db
    import ark_media

    film = ark.root / "drop" / "files" / ("a" * 16 + "-film.mp4")
    film.parent.mkdir(parents=True, exist_ok=True)
    film.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"0" * 4096)
    with ark_db.tx() as conn:
        conn.execute(
            "INSERT INTO media (ts, title, filename, stored, size, state, plan)"
            " VALUES (?, ?, ?, ?, ?, 'published', 'direct')",
            (ark_health_now(), "A film that plays", film.name, film.name, film.stat().st_size))
    _break(ark_media.thumbs_dir())
    with _api(ark) as client:
        page = client.get("/media/1")
        assert page.status_code == 200
        stream = client.get("/media/1/stream")
        assert stream.status_code in (200, 206)
        assert stream.content[:12] == b"\x00\x00\x00\x18ftypmp42"


def ark_health_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
