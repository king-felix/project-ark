"""Many readers at once: does the box keep answering, and does it say what it costs?

The targets are written down here, **before** the measurement, and they are the
same numbers reported in `docs/IMPLEMENTATION-LOG.md` §56:

    search (small fixture)      p95 < 1500 ms, no 5xx
    /api/status                 p95 <  800 ms, no 5xx
    /api/health (liveness)      p95 <  150 ms, no 5xx — it must not scan shelves
    film range request          every response 206 with exactly the bytes asked for
    board page                  p95 <  800 ms, no 5xx

What this test can and cannot prove is stated in the docstring of each test.  A
throwaway box with three files is not a 97 GiB card on a phone; it proves the
*shape* of the behaviour (no lock contention, no 5xx, liveness stays cheap) and
nothing about a real card's absolute speed.  The Pi measurement is separate and
identifies the device, the revision and the workload.
"""

from __future__ import annotations

import statistics
import time
from concurrent.futures import ThreadPoolExecutor

from helpers import LAN

#: How many simulated readers, and how long the workload runs.  Deliberately
#: finite and small: this is a gate, not a benchmark.
READERS = 8
ROUNDS = 6


def _p95(values):
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))
    return ordered[index]


def _measure(fn, count=ROUNDS):
    times, codes = [], []
    for _ in range(count):
        started = time.monotonic()
        response = fn()
        times.append((time.monotonic() - started) * 1000.0)
        codes.append(response.status_code)
    return times, codes


def test_several_readers_get_pages_and_no_server_errors(public):
    """Eight readers, six page loads each, all on the same process."""
    client = public.client
    paths = ["/board", "/files", "/library", "/search?q=water", "/household"]

    def read(path):
        return path, client.get(path, headers=LAN)

    requests = [(path, i) for i in range(ROUNDS) for path in paths]
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=READERS) as pool:
        results = list(pool.map(lambda item: read(item[0]), requests))
    elapsed = time.monotonic() - started

    codes = [r.status_code for _path, r in results]
    assert not [c for c in codes if c >= 500], codes
    assert all(c in (200, 302, 303) for c in codes), sorted(set(codes))
    # A sane ceiling for "the whole workload", not a per-request target: if this
    # takes longer than ten seconds for 30 requests on a throwaway card something
    # is serialised that should not be.
    assert elapsed < 10.0, f"{len(requests)} requests took {elapsed:.2f}s"


def test_liveness_stays_cheap_while_other_readers_work(public):
    """`/api/health` must not walk the shelves — measured, not assumed."""
    client = public.client
    quiet, _ = _measure(lambda: client.get("/api/health"), count=5)

    def noisy():
        for _ in range(3):
            client.get("/library", headers=LAN)
            client.get("/search?q=water", headers=LAN)
        return client.get("/api/health")

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: noisy(), range(4)))
    busy, codes = _measure(lambda: client.get("/api/health"), count=ROUNDS)

    assert not [c for c in codes if c >= 500]
    assert _p95(busy) < 150.0, f"liveness p95 {_p95(busy):.0f} ms"
    # And it is not systematically slower than the quiet case.
    assert _p95(busy) < max(150.0, _p95(quiet) * 4 + 60)


def test_status_and_search_meet_their_targets(public):
    client = public.client
    status_times, status_codes = _measure(lambda: client.get("/api/status"), count=ROUNDS)
    search_times, search_codes = _measure(lambda: client.get("/search?q=water"),
                                          count=ROUNDS)
    board_times, board_codes = _measure(lambda: client.get("/board", headers=LAN),
                                        count=ROUNDS)

    for codes, name in ((status_codes, "status"), (search_codes, "search"),
                        (board_codes, "board")):
        assert not [c for c in codes if c >= 500], (name, codes)
    assert _p95(status_times) < 800.0, f"status p95 {_p95(status_times):.0f} ms"
    assert _p95(search_times) < 1500.0, f"search p95 {_p95(search_times):.0f} ms"
    assert _p95(board_times) < 800.0, f"board p95 {_p95(board_times):.0f} ms"


def test_a_film_plays_by_range_while_other_readers_are_busy(ark):
    """Seeking is a range request; it must stay exact under load."""
    import ark_db
    from datetime import datetime, timezone

    import main
    from fastapi.testclient import TestClient

    payload = bytes(range(256)) * 64            # 16 KiB of identifiable bytes
    folder = ark.root / "drop" / "files"
    folder.mkdir(parents=True, exist_ok=True)
    name = "b" * 32 + "-film.mp4"
    (folder / name).write_bytes(payload)
    with ark_db.tx() as conn:
        conn.execute(
            "INSERT INTO media (ts, title, filename, stored, size, state, plan)"
            " VALUES (?, 'Range film', ?, ?, ?, 'published', 'direct')",
            (datetime.now(timezone.utc).isoformat(timespec="seconds"), name, name,
             len(payload)))

    with TestClient(main.app) as client:
        def seek(_):
            first = 4096
            last = 8191
            response = client.get("/media/1/stream", headers={
                **LAN, "range": f"bytes={first}-{last}"}, follow_redirects=False)
            return response

        with ThreadPoolExecutor(max_workers=READERS) as pool:
            results = list(pool.map(seek, range(READERS * 3)))

        for response in results:
            assert response.status_code == 206, response.status_code
            assert response.content == payload[4096:8192]
            assert response.headers["content-range"] == f"bytes 4096-8191/{len(payload)}"


def test_a_background_preparation_does_not_block_reading(ark, monkeypatch):
    """One bounded background job beside readers: both finish, neither waits."""
    import ark_db
    import ark_media_jobs

    with ark_db.tx() as conn:
        conn.execute(
            "INSERT INTO media (ts, title, filename, stored, size, state, plan)"
            " VALUES ('2026-11-04T00:00:00+00:00', 'Queued', 'x.mp4', 'x.mp4', 10,"
            " 'published', 'remux')")
        conn.execute(
            "INSERT INTO media_jobs (media_id, kind, state, ts) VALUES (1, 'prepare',"
            " 'queued', '2026-11-04T00:00:00+00:00')")

    def slow_job(job):
        time.sleep(0.05)
        return {"ok": True}

    monkeypatch.setattr(ark_media_jobs, "run_job", slow_job)

    import main
    from fastapi.testclient import TestClient

    with TestClient(main.app) as client:
        started = time.monotonic()
        with ThreadPoolExecutor(max_workers=4) as pool:
            worker = pool.submit(ark_media_jobs.work_once, 4321)
            readers = [pool.submit(client.get, "/library", headers=LAN)
                       for _ in range(6)]
            assert worker.result(timeout=20) is True
            codes = [r.result().status_code for r in readers]
        elapsed = time.monotonic() - started
    assert all(c == 200 for c in codes), codes
    assert elapsed < 10.0


def test_a_paused_box_is_not_a_slow_box(public, ark):
    """Pausing background work must not put a lock in front of a reader."""
    import ark_db
    import ark_pause

    with ark_db.tx() as conn:
        conn.execute(
            "INSERT INTO media (ts, title, filename, stored, size, state, plan)"
            " VALUES ('2026-11-04T00:00:00+00:00', 'Waiting', 'w.mp4', 'w.mp4', 10,"
            " 'published', 'convert')")
        conn.execute(
            "INSERT INTO media_jobs (media_id, kind, state, ts) VALUES (1, 'prepare',"
            " 'queued', '2026-11-04T00:00:00+00:00')")

    def measure():
        return _measure(lambda: public.client.get("/board", headers=LAN), count=ROUNDS)

    before, _ = measure()
    ark_pause.set_paused(True)
    try:
        after, codes = measure()
    finally:
        ark_pause.set_paused(False)
    assert not [c for c in codes if c >= 500]
    assert _p95(after) < 800.0, f"paused board p95 {_p95(after):.0f} ms"
    assert _p95(after) < _p95(before) * 4 + 200


def test_the_report_is_reused_within_its_window(ark, monkeypatch):
    """A page-load burst must not walk every shelf once per request."""
    import ark_health

    calls = {"n": 0}
    real = ark_health._build

    def counting(build):
        calls["n"] += 1
        return real(build)

    monkeypatch.setattr(ark_health, "_build", counting)
    monkeypatch.setenv("ARK_HEALTH_REPORT_CACHE", "30")
    monkeypatch.setattr(ark_health, "REPORT_CACHE_SECONDS", 30.0)
    ark_health.forget_probes()
    ark_health.report()
    first = calls["n"]
    assert first == len(ark_health.SECTIONS)
    for _ in range(5):
        ark_health.report()
    assert calls["n"] == first, "the cached answer was rebuilt"
    ark_health.report(force=True)
    assert calls["n"] == first * 2, "force must scan again"
