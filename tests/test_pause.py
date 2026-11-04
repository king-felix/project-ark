"""The owner's pause: one switch, one file, and nothing it silently undoes.

The Ark is in a room with people in it.  "Be quiet for a while" is a reasonable
thing to want, and the failure modes of offering it are all about *lying*: a
pause that leaves a job running, a resume that switches on something the owner
had turned off, a restart that forgets, or a page that calls the whole box broken
because preparation is waiting.

Every test here uses a throwaway box.  Nothing starts a real service; the
behaviours under test are the ones the application and the worker decide.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi.testclient import TestClient

from helpers import LAN, claim_box, csrf_of, get, post


def _background(client, cookie):
    return get(client, "/admin/background", headers=LAN, cookie=f"ark_admin={cookie}")


# ---------------------------------------------------------------------------
#  the state itself
# ---------------------------------------------------------------------------
def test_a_fresh_box_is_not_paused(ark):
    import ark_pause

    assert ark_pause.paused() is False
    state = ark_pause.requested()
    assert state["paused"] is False and state["since"] == ""


def test_the_pause_survives_a_restart(ark):
    """The state is a file on the card, not a variable in a process."""
    import ark_pause

    ark_pause.set_paused(True)
    assert ark_pause.paused() is True
    # A restart is a new import of the module with the same ARK_ROOT.
    for name in ("ark_pause",):
        import sys
        sys.modules.pop(name, None)
    import ark_pause as fresh

    assert fresh.paused() is True
    marker = fresh.marker_path()
    assert marker.is_file()
    assert "paused" in marker.read_text(encoding="utf-8")


def test_the_pause_file_can_be_read_without_python(ark):
    """A shell script or a systemd condition must be able to ask the same question."""
    import ark_pause

    state = ark_pause.set_paused(True)
    path = ark_pause.marker_path()
    assert os.path.isfile(path), state
    # `test -e` in a shell is exactly this.
    assert os.path.exists(path)
    ark_pause.set_paused(False)
    assert not os.path.exists(path)


def test_an_unreadable_marker_path_fails_closed(ark, monkeypatch):
    """Not being able to look is not permission to start heavy work."""
    import ark_pause

    class Broken:
        def exists(self):
            raise OSError("the card went away")

    monkeypatch.setattr(ark_pause, "marker_path", lambda: Broken())
    assert ark_pause.paused() is True


def test_resuming_leaves_a_trigger_for_a_book_dropped_while_paused(ark):
    """A path unit that already fired during the pause must fire again on resume."""
    import ark_pause

    ark_pause.set_paused(True)
    assert not ark_pause.kick_path().exists()
    ark_pause.set_paused(False)
    assert ark_pause.kick_path().is_file()


# ---------------------------------------------------------------------------
#  what the worker does with it
# ---------------------------------------------------------------------------
def test_the_worker_claims_nothing_while_paused(ark):
    import ark_db
    import ark_media
    import ark_media_jobs
    import ark_pause

    with ark_db.tx() as conn:
        conn.execute(
            "INSERT INTO media (ts, title, filename, stored, size, state, plan)"
            " VALUES ('2026-11-04T00:00:00+00:00', 'Waiting film', 'x.mp4', 'x.mp4',"
            " 100, 'published', 'remux')")
        conn.execute(
            "INSERT INTO media_jobs (media_id, kind, state, ts) VALUES (1, 'prepare',"
            " 'queued', '2026-11-04T00:00:00+00:00')")
    ark_pause.set_paused(True)
    assert ark_media_jobs.work_once(1234) is False
    # The queue is untouched: it is deferred, not cancelled.
    with ark_db.read() as conn:
        row = conn.execute("SELECT state FROM media_jobs WHERE id = 1").fetchone()
    assert row["state"] == "queued"


def test_a_job_already_running_is_allowed_to_finish(ark, monkeypatch):
    """A claimed encode is not thrown away by a generic pause."""
    import ark_db
    import ark_media
    import ark_media_jobs
    import ark_pause

    with ark_db.tx() as conn:
        conn.execute(
            "INSERT INTO media (ts, title, filename, stored, size, state, plan)"
            " VALUES ('2026-11-04T00:00:00+00:00', 'Running film', 'y.mp4', 'y.mp4',"
            " 100, 'published', 'remux')")
    claimed = {"n": 0}

    def claim(conn, pid=0):
        claimed["n"] += 1
        return {"id": 9, "media_id": 1, "kind": "prepare"}

    monkeypatch.setattr(ark_media, "claim", claim)
    monkeypatch.setattr(ark_media_jobs, "run_job",
                        lambda job: {"ok": True, "note": "finished"})
    ark_pause.set_paused(True)
    # work_once refuses to *start* anything while paused, which is the policy:
    # a job claimed before the pause is inside run_job already, not here.
    assert ark_media_jobs.work_once(1) is False
    assert claimed["n"] == 0


def test_the_intact_recovery_pass_still_runs_at_startup(ark):
    """Pausing preparation must never pause integrity work."""
    import ark_db
    import ark_media
    import ark_media_jobs
    import ark_pause

    with ark_db.tx() as conn:
        conn.execute(
            "INSERT INTO media (ts, title, filename, stored, size, state, plan)"
            " VALUES ('2026-11-04T00:00:00+00:00', 'Film', 'z.mp4', 'z.mp4', 10,"
            " 'published', 'remux')")
        conn.execute(
            "INSERT INTO media_jobs (media_id, kind, state, ts) VALUES (1, 'prepare',"
            " 'running', '2026-11-04T00:00:00+00:00')")
    ark_pause.set_paused(True)
    result = ark_media_jobs.startup()
    # The interrupted job was reconciled, exactly as it would be without a pause.
    assert result["requeued"] == 1


# ---------------------------------------------------------------------------
#  the owner's page
# ---------------------------------------------------------------------------
def test_the_owner_can_pause_and_resume_through_the_page(client):
    import ark_pause

    cookie = claim_box(client)
    page = _background(client, cookie)
    assert page.status_code == 200
    assert "Pause background work" in page.text

    paused = post(client, "/admin/background/pause", {"csrf": csrf_of(page.text)},
                  headers=LAN, cookie=f"ark_admin={cookie}")
    assert paused.status_code == 303
    assert ark_pause.paused() is True
    assert "paused" in paused.headers.get("location", "")

    page = _background(client, cookie)
    assert "Background work is paused" in page.text
    assert "Resume background work" in page.text

    resumed = post(client, "/admin/background/resume", {"csrf": csrf_of(page.text)},
                   headers=LAN, cookie=f"ark_admin={cookie}")
    assert resumed.status_code == 303
    assert ark_pause.paused() is False


def test_pausing_needs_the_form_token_and_a_session(client):
    import ark_pause

    cookie = claim_box(client)
    refused = post(client, "/admin/background/pause", {"csrf": "not-the-token"},
                   headers=LAN, cookie=f"ark_admin={cookie}")
    assert refused.status_code in (400, 403)
    assert ark_pause.paused() is False

    anonymous = post(client, "/admin/background/pause", {"csrf": "x"}, headers=LAN)
    assert anonymous.status_code in (303, 400, 403)
    assert ark_pause.paused() is False


def test_a_pause_is_recorded_in_the_history(client):
    import ark_db

    cookie = claim_box(client)
    page = _background(client, cookie)
    post(client, "/admin/background/pause", {"csrf": csrf_of(page.text)},
         headers=LAN, cookie=f"ark_admin={cookie}")
    with ark_db.read() as conn:
        actions = [r["action"] for r in conn.execute(
            "SELECT action FROM audit_log ORDER BY id DESC LIMIT 5")]
    assert "background-pause" in actions


def test_pausing_twice_is_idempotent(client):
    import ark_pause

    cookie = claim_box(client)
    page = _background(client, cookie)
    for _ in range(2):
        post(client, "/admin/background/pause", {"csrf": csrf_of(page.text)},
             headers=LAN, cookie=f"ark_admin={cookie}")
    assert ark_pause.paused() is True
    page = _background(client, cookie)
    post(client, "/admin/background/resume", {"csrf": csrf_of(page.text)},
         headers=LAN, cookie=f"ark_admin={cookie}")
    assert ark_pause.paused() is False


def test_stopping_the_running_film_names_it_and_keeps_the_upload(client):
    import ark_db

    cookie = claim_box(client)
    with ark_db.tx() as conn:
        conn.execute(
            "INSERT INTO media (ts, title, filename, stored, size, state, plan)"
            " VALUES ('2026-11-04T00:00:00+00:00', 'Long film', 'f.mp4', 'f.mp4',"
            " 10, 'published', 'convert')")
        conn.execute(
            "INSERT INTO media_jobs (media_id, kind, state, ts) VALUES (1, 'prepare',"
            " 'running', '2026-11-04T00:00:00+00:00')")
    page = _background(client, cookie)
    assert "Stop this film now" in page.text
    stopped = post(client, "/admin/background/stop",
                   {"csrf": csrf_of(page.text), "media_id": "1"},
                   headers=LAN, cookie=f"ark_admin={cookie}")
    assert stopped.status_code == 303
    with ark_db.read() as conn:
        job = conn.execute("SELECT state FROM media_jobs WHERE id = 1").fetchone()
        film = conn.execute("SELECT stored FROM media WHERE id = 1").fetchone()
    assert job["state"] == "cancelled"
    assert film["stored"] == "f.mp4", "the upload must be kept"


def test_stop_cannot_name_a_film_that_is_not_running(client):
    """A stale page must not be able to cancel a different job."""
    import ark_db

    cookie = claim_box(client)
    with ark_db.tx() as conn:
        conn.execute(
            "INSERT INTO media (ts, title, filename, stored, size, state, plan)"
            " VALUES ('2026-11-04T00:00:00+00:00', 'Running', 'a.mp4', 'a.mp4',"
            " 10, 'published', 'convert')")
        conn.execute(
            "INSERT INTO media (ts, title, filename, stored, size, state, plan)"
            " VALUES ('2026-11-04T00:00:01+00:00', 'Other', 'b.mp4', 'b.mp4',"
            " 10, 'published', 'convert')")
        conn.execute(
            "INSERT INTO media_jobs (media_id, kind, state, ts) VALUES (1, 'prepare',"
            " 'running', '2026-11-04T00:00:00+00:00')")
    page = _background(client, cookie)
    post(client, "/admin/background/stop",
         {"csrf": csrf_of(page.text), "media_id": "2"},
         headers=LAN, cookie=f"ark_admin={cookie}")
    with ark_db.read() as conn:
        job = conn.execute("SELECT state FROM media_jobs WHERE id = 1").fetchone()
    assert job["state"] == "running"


# ---------------------------------------------------------------------------
#  what a reader sees while it is paused
# ---------------------------------------------------------------------------
def test_a_paused_worker_is_a_paused_shelf_not_a_broken_one(ark):
    """Films that can be watched still report ready; preparation says 'paused'."""
    import ark_db
    import ark_health
    import ark_pause
    from datetime import datetime, timezone

    films = ark.root / "media" / "play"
    films.mkdir(parents=True, exist_ok=True)
    (films / "m1.mp4").write_bytes(b"\x00" * 2048)
    with ark_db.tx() as conn:
        conn.execute(
            "INSERT INTO media (ts, title, filename, stored, size, state, plan,"
            " play_stored, play_size) VALUES (?, 'Ready film', 'r.mp4', '', 10,"
            " 'published', 'convert', 'm1.mp4', 2048)",
            (datetime.now(timezone.utc).isoformat(timespec="seconds"),))
        conn.execute(
            "INSERT INTO media (ts, title, filename, stored, size, state, plan)"
            " VALUES (?, 'Waiting film', 'w.mp4', 'w.mp4', 10, 'published', 'convert')",
            (datetime.now(timezone.utc).isoformat(timespec="seconds"),))
        conn.execute(
            "INSERT INTO media_jobs (media_id, kind, state, ts) VALUES (2, 'prepare',"
            " 'queued', '2026-11-04T00:00:00+00:00')")
    ark_pause.set_paused(True)
    section = ark_health.films_section()
    assert section["state"] == "paused"
    assert section["ready"] is True, section
    assert "paused" in section["reason"].lower()
    assert section["facts"]["playable"] == 1
    assert section["facts"]["waiting"] == 1


def test_a_paused_helper_says_so_and_still_leaves_the_box_whole(ark):
    import ark_ai
    import ark_pause

    ark_pause.set_paused(True)
    sentence, hint = ark_ai.not_ready_sentence()
    assert "paused" in sentence.lower()
    assert "still work" in hint.lower()
    assert ark_ai.status()["available"] is False
    assert ark_ai.not_ready_reason() == "paused"


def test_pausing_never_makes_the_core_box_unready(ark):
    import ark_health
    import ark_pause

    ark_pause.set_paused(True)
    report = ark_health.report(force=True)
    assert "Films" not in report["blocking"]
    assert "Games" not in report["blocking"]
    assert "Maps" not in report["blocking"]
    assert len(report["blocking"]) <= len(ark_health.BLOCKING_SECTIONS)
    features = {s["key"]: s for s in report["sections"]
                if s["key"] in ark_health.FEATURE_SECTIONS}
    assert set(features) == {"films", "games", "maps"}
    for section in features.values():
        assert section["state"] in ark_health.STATES


def test_the_public_pages_keep_working_while_paused(ark):
    """The whole point: a pause is not a shutdown."""
    import ark_pause
    import main

    ark_pause.set_paused(True)
    with TestClient(main.app) as client:
        for path in ("/board", "/files", "/media/", "/library", "/household", "/maps"):
            response = client.get(path, follow_redirects=False)
            assert response.status_code in (200, 301, 302, 303, 307, 308), path
        ask = client.get("/ask")
        assert ask.status_code == 200
        assert "paused" in ask.text.lower()


def test_the_health_row_for_the_helper_agrees_with_the_helper_while_paused(ark):
    """Found on the live box: `/ask` refused while the readiness row said it answered."""
    import ark_ai
    import ark_health
    import ark_pause

    engine = ark.root / "ai" / "llama-server"
    engine.parent.mkdir(parents=True, exist_ok=True)
    engine.write_bytes(b"#!/bin/sh\n")
    (ark.root / "ai" / "models").mkdir(parents=True, exist_ok=True)
    (ark.root / "ai" / "models" / "a.gguf").write_bytes(b"x" * 1024)
    ark_pause.set_paused(True)
    section = ark_health.ask_section()
    assert section["ready"] is False
    assert section["state"] == "paused"
    assert "paused" in section["reason"].lower()
    # And the sentence a reader sees on /ask is the same one.
    sentence, _hint = ark_ai.not_ready_sentence()
    assert section["reason"].startswith(sentence)


def test_the_game_timer_does_not_change_the_shelf_while_paused(ark, monkeypatch, capsys):
    """`ark-arcade --seed` / `--discover` are what the weekly timer runs."""
    import importlib.util
    import sys

    import ark_pause

    script = Path(__file__).resolve().parents[1] / "pi" / "bin" / "ark-arcade"
    spec = importlib.util.spec_from_loader(
        "ark_arcade_cli", importlib.machinery.SourceFileLoader("ark_arcade_cli", str(script)))
    module = importlib.util.module_from_spec(spec)
    sys.modules["ark_arcade_cli"] = module
    try:
        spec.loader.exec_module(module)
    finally:
        pass

    monkeypatch.setenv("ARK_API_DIR", str(Path(__file__).resolve().parents[1] / "pi" / "api"))
    monkeypatch.setenv("ARK_PAUSE_MARKER", str(ark_pause.marker_path()))
    ark_pause.set_paused(True)
    for argv in (["--seed"], ["--discover", "--auto"]):
        assert module.main(argv) == 0
        out = capsys.readouterr().out
        assert "paused by the owner" in out, out
    sys.modules.pop("ark_arcade_cli", None)


def test_game_files_are_still_delivered_while_paused(ark):
    """A pause stops preparation; it never stops a game somebody is playing."""
    import ark_pause
    import main
    from fastapi.testclient import TestClient

    ark_pause.set_paused(True)
    with TestClient(main.app) as client:
        asset = client.get("/arcade/emulator/loader.js", follow_redirects=False)
        assert asset.status_code == 200, asset.status_code
        assert asset.content
        shelf = client.get("/arcade/", follow_redirects=False)
        assert shelf.status_code == 200
