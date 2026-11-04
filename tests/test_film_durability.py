"""Durable film publication: the upload leaves only when the copy is safe.

Measured on the live box on 3 November 2026: "the copy is renamed into place and
the row written, then the upload is deleted".  A rename is durable in its folder
only after that folder is synced, and a row only after SQLite has written it.  A
power cut in that window is the one case where the Ark could throw away the only
whole copy of a film it has.

These tests hold the order in place without needing a power cut: they watch what
the code calls, and what a released film looks like afterwards.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest


def _row(ark, **values):
    import ark_db
    fields = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "title": "A film", "filename": "f.mp4", "stored": "s" * 32 + "-f.mp4",
              "size": 4096, "state": "published", "plan": "remux",
              "play_stored": "m1.mp4", "play_size": 2048}
    fields.update(values)
    columns = ", ".join(fields)
    marks = ", ".join("?" for _ in fields)
    with ark_db.tx() as conn:
        cursor = conn.execute(f"INSERT INTO media ({columns}) VALUES ({marks})",
                              tuple(fields.values()))
        return int(cursor.lastrowid)


def _copy(ark, name="m1.mp4", data=b"P" * 2048):
    folder = ark.root / "media" / "play"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(data)
    return folder / name


def _upload(ark, name="s" * 32 + "-f.mp4", data=b"U" * 4096):
    folder = ark.root / "drop" / "files"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(data)
    return folder / name


def test_an_original_is_kept_when_the_copy_is_short(ark):
    """A copy a cut left truncated is not a copy somebody can watch."""
    import ark_db
    import ark_media

    _upload(ark)
    copy = _copy(ark)
    media_id = _row(ark)                       # records play_size 2048
    copy.write_bytes(b"P" * 1024)              # the card only kept half of it
    with ark_db.tx() as conn:
        result = ark_media.release_original(conn, media_id)
    assert result["removed"] == 0
    assert result["reason"] == "no copy"
    assert (ark.root / "drop" / "files" / ("s" * 32 + "-f.mp4")).is_file()


def test_an_original_is_kept_when_the_record_and_the_file_disagree(ark):
    import ark_db
    import ark_media

    _upload(ark)
    _copy(ark, data=b"P" * 1024)
    media_id = _row(ark, play_size=2048)
    with ark_db.tx() as conn:
        result = ark_media.release_original(conn, media_id)
    assert result["removed"] == 0


def test_a_matching_copy_lets_the_upload_go(ark):
    import ark_db
    import ark_media

    upload = _upload(ark)
    _copy(ark)
    media_id = _row(ark)
    with ark_db.tx() as conn:
        result = ark_media.release_original(conn, media_id)
    assert result["removed"] == 1
    assert result["freed"] == 4096
    assert not upload.exists()
    with ark_db.read() as conn:
        row = conn.execute("SELECT stored, play_stored FROM media WHERE id = ?",
                           (media_id,)).fetchone()
    assert row["stored"] == "" and row["play_stored"] == "m1.mp4"


def test_a_direct_film_never_loses_its_only_copy(ark):
    import ark_db
    import ark_media

    upload = _upload(ark)
    _copy(ark)
    media_id = _row(ark, plan="direct", play_stored="", play_size=0)
    with ark_db.tx() as conn:
        result = ark_media.release_original(conn, media_id)
    assert result["removed"] == 0 and result["reason"] == "kept"
    assert upload.is_file()


def test_the_sweep_uses_the_same_rule(ark):
    """Startup cleanup must not be a second, looser path to the same deletion."""
    import ark_db
    import ark_media

    _upload(ark)
    copy = _copy(ark, data=b"P" * 512)
    _row(ark, play_size=2048)
    with ark_db.tx() as conn:
        result = ark_media.release_ready_originals(conn)
    assert result["removed"] == 0
    assert (ark.root / "drop" / "files" / ("s" * 32 + "-f.mp4")).is_file()
    assert copy.is_file()


def test_the_publication_order_syncs_before_it_releases(ark, monkeypatch):
    """The copy's bytes and folder are durable *before* the upload is deleted."""
    import ark_media
    import ark_media_jobs

    order = []

    real_release = ark_media.release_original
    real_fsync_dir = ark_media_jobs.ark_atomic.fsync_dir
    real_fsync_file = ark_media_jobs._fsync_file

    def spy_file(path):
        order.append("fsync-file")
        return real_fsync_file(path)

    def spy_dir(path):
        order.append("fsync-dir")
        return real_fsync_dir(path)

    def spy_release(conn, media_id):
        order.append("release")
        return real_release(conn, media_id)

    monkeypatch.setattr(ark_media_jobs, "_fsync_file", spy_file)
    monkeypatch.setattr(ark_media_jobs.ark_atomic, "fsync_dir", spy_dir)
    monkeypatch.setattr(ark_media, "release_original", spy_release)

    # Drive only the tail of make_derivative: a fake tool that writes the output.
    source = ark.root / "drop" / "files" / ("s" * 32 + "-f.mp4")
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"U" * 4096)
    media_id = _row(ark)

    import ark_db
    with ark_db.read() as conn:
        row = ark_media.item(conn, media_id)

    monkeypatch.setattr(ark_media_jobs, "_facts", lambda r: {"duration": 1.0,
                                                             "vcodec": "h264",
                                                             "pix_fmt": "yuv420p",
                                                             "has_audio": False,
                                                             "width": 320, "height": 240})
    monkeypatch.setattr(ark_media_jobs, "_short_output", lambda path, facts: "")
    monkeypatch.setattr(ark_media_jobs, "build_command",
                        lambda *a, **k: ["cp", str(source), str(a[2])])
    monkeypatch.setattr(ark_media_jobs, "hwaccel_args", lambda tool=None: [])

    result = ark_media_jobs.make_derivative(row, job_id=0)
    assert result["ok"] is True, result
    assert order[:2] == ["fsync-file", "fsync-dir"], order
    assert "release" in order
    assert order.index("fsync-dir") < order.index("release")
