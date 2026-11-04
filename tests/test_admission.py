"""Admission: who may receive bytes, how many, and who else was promised them first.

The fault these tests pin is a *race*, not a missing check.  Two uploads could each
be told the same free bytes were theirs; a body with no ``Content-Length`` was not
checked at all; the framework wrote a request body to the card before any route ran;
and a promise nobody refreshed was deleted after fifteen minutes whether the worker
that made it was dead or still encoding.

Every test here uses a real database and, where the point is concurrency, real
processes.  Capacity is bounded by the policy's own simulated card
(``ARK_SPACE_FAKE``), because a test that fills the real one is a test nobody should
run.
"""

from __future__ import annotations

import os
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

API = Path(__file__).resolve().parents[1] / "pi" / "api"


# ---------------------------------------------------------------------------
#  helpers
# ---------------------------------------------------------------------------
def fake_card(monkeypatch, *, total=100 * 1024 ** 3, usable=10 * 1024 ** 2):
    """A small, empty card the policy measures instead of this machine's disk.

    ``usable`` is what is left *after* the reserve (7 % of ``total``), which is the
    number every admission question is really about.
    """
    import ark_space
    reserve = ark_space.reserve_for(total)
    monkeypatch.setenv("ARK_SPACE_FAKE", "1")
    monkeypatch.setenv("ARK_SPACE_FAKE_TOTAL", str(total))
    monkeypatch.setenv("ARK_SPACE_FAKE_FREE", str(reserve + usable))
    return reserve


def rows(ark):
    with ark.db.read() as conn:
        return [dict(row) for row in
                conn.execute("SELECT * FROM space_reservations ORDER BY id")]


def promised(ark) -> int:
    with ark.db.read() as conn:
        return int(conn.execute(
            "SELECT COALESCE(SUM(MAX(0, bytes - written)), 0) "
            "FROM space_reservations").fetchone()[0])


def child_env(**extra) -> dict:
    env = {key: value for key, value in os.environ.items()
           if key not in ("PYTEST_CURRENT_TEST",)}
    env["PYTHONPATH"] = str(API)
    env["ARK_API_DIR"] = str(API)
    env.update({key: str(value) for key, value in extra.items()})
    return env


def run_child(code: str, *, timeout: float = 30, env=None):
    return subprocess.run([sys.executable, "-c", code], capture_output=True,
                          text=True, timeout=timeout, env=env or child_env())


def spawn(code: str, *, env=None):
    return subprocess.Popen([sys.executable, "-c", code], env=env or child_env(),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def multipart(data: bytes, name: str = "x.txt", field: str = "file",
              boundary: str = "ARKBOUND") -> bytes:
    return (f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{field}"; filename="{name}"\r\n'
            f"Content-Type: application/octet-stream\r\n\r\n").encode() + data + \
           f"\r\n--{boundary}--\r\n".encode()


# ---------------------------------------------------------------------------
#  1. a promise belongs to a process, not to a clock
# ---------------------------------------------------------------------------
def test_a_live_worker_keeps_its_promise_however_old_the_heartbeat_is(ark, monkeypatch):
    """The encode that has been running for an hour is not abandoned.

    This is the rule the old ``STALE_SECONDS`` cutoff broke: a worker that was
    still working, but had not refreshed within fifteen minutes, lost the space it
    was writing into.  Liveness now comes from the pid; the clock only decides for
    rows written before the pid was recorded.
    """
    import ark_space
    fake_card(monkeypatch, usable=10 * 1024 ** 2)
    with ark.db.tx() as conn:
        ident = ark_space.reserve("/srv/ark", 4 * 1024 ** 2, owner="long-encode",
                                  conn=conn)
        conn.execute("UPDATE space_reservations SET heartbeat = ? WHERE id = ?",
                     (time.time() - 6 * 3600, ident))
    with ark.db.tx() as conn:
        assert ark_space.reconcile(conn=conn) == 0
        assert ark_space.committed(conn, ark_space.device_key("/srv/ark")) == 4 * 1024 ** 2


def test_a_killed_worker_s_promise_is_released(ark, monkeypatch):
    import ark_space
    fake_card(monkeypatch, usable=10 * 1024 ** 2)
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait()
    with ark.db.tx() as conn:
        ark_space.reserve("/srv/ark", 4 * 1024 ** 2, owner="killed", conn=conn)
        conn.execute("UPDATE space_reservations SET pid = ?, boot = ?",
                     (gone.pid, ark_space.current_boot()))
    with ark.db.tx() as conn:
        assert ark_space.reconcile(conn=conn) == 1
        assert ark_space.committed(conn, ark_space.device_key("/srv/ark")) == 0


def test_a_promise_from_a_previous_boot_is_released_even_if_its_pid_is_alive(ark, monkeypatch):
    """After a power cut the pids start again; the boot id is what proves it.

    A pid on its own is not enough: pid 1 exists in every boot, and a reused pid
    would otherwise hold a dead job's space for ever.
    """
    import ark_space
    fake_card(monkeypatch, usable=10 * 1024 ** 2)
    if not ark_space.current_boot():
        pytest.skip("this platform does not report a boot id")
    with ark.db.tx() as conn:
        ark_space.reserve("/srv/ark", 4 * 1024 ** 2, owner="old-boot", conn=conn)
        conn.execute("UPDATE space_reservations SET pid = ?, boot = ?",
                     (os.getpid(), "a-boot-that-is-not-this-one"))
    with ark.db.tx() as conn:
        assert ark_space.reconcile(conn=conn) == 1


def test_a_clock_that_jumped_backwards_does_not_release_a_promise(ark, monkeypatch):
    import ark_space
    fake_card(monkeypatch, usable=10 * 1024 ** 2)
    with ark.db.tx() as conn:
        ark_space.reserve("/srv/ark", 4 * 1024 ** 2, owner="future", conn=conn)
        conn.execute("UPDATE space_reservations SET pid = 0, boot = '', heartbeat = ?",
                     (time.time() + 3600,))       # written "in the future"
    with ark.db.tx() as conn:
        assert ark_space.reconcile(conn=conn) == 0


def test_a_legacy_row_with_no_pid_is_kept_for_a_day(ark, monkeypatch):
    """A row written before migration 8 has nothing else to go on — so be generous."""
    import ark_space
    fake_card(monkeypatch, usable=10 * 1024 ** 2)
    with ark.db.tx() as conn:
        ark_space.reserve("/srv/ark", 4 * 1024 ** 2, owner="legacy", conn=conn)
        conn.execute("UPDATE space_reservations SET pid = 0, boot = '', heartbeat = ?",
                     (time.time() - 2 * 3600,))
    with ark.db.tx() as conn:
        assert ark_space.reconcile(conn=conn) == 0
        conn.execute("UPDATE space_reservations SET heartbeat = ?",
                     (time.time() - (ark_space.LEGACY_GRACE + 60),))
    with ark.db.tx() as conn:
        assert ark_space.reconcile(conn=conn) == 1


# ---------------------------------------------------------------------------
#  2. an unreadable ledger is not an empty one
# ---------------------------------------------------------------------------
def _break_the_ledger(monkeypatch, ark_space):
    def broken(conn, device, exclude=None):
        raise sqlite3.OperationalError("no such table: space_reservations")
    monkeypatch.setattr(ark_space, "committed", broken)


def test_a_bulk_write_is_refused_when_the_ledger_cannot_be_read(ark, monkeypatch):
    """Silently assuming "nobody promised anything" is how a card fills up.

    The refusal is the *readable* kind — a sentence, not a traceback — and it is a
    different class from "the card is full", so a page can say which problem it is.
    """
    import ark_space
    fake_card(monkeypatch, usable=10 * 1024 ** 2)
    _break_the_ledger(monkeypatch, ark_space)
    assert ark_space.committed_for("/srv/ark") is None
    assert ark_space.slack("/srv/ark") is None
    with pytest.raises(ark_space.LedgerUnreadable) as refused:
        ark_space.require("/srv/ark", 1024 ** 2)
    assert "could not read its own record" in str(refused.value)
    with pytest.raises(ark_space.LedgerUnreadable):
        ark_space.reserve("/srv/ark", 1024 ** 2, owner="bulk")
    with pytest.raises(ark_space.LedgerUnreadable):
        with ark_space.hold("/srv/ark", 1024 ** 2, owner="bulk"):
            pass


def test_an_essential_write_is_still_admitted_when_the_ledger_is_broken(ark, monkeypatch):
    """Freeing space and saving the records must survive a broken ledger.

    Refusing a recovery copy because the box cannot read its own promises would
    make one fault into two — a card that is full *and* unrepairable.
    """
    import ark_space
    fake_card(monkeypatch, usable=10 * 1024 ** 2)
    _break_the_ledger(monkeypatch, ark_space)
    with ark_space.hold("/srv/ark", 1024 ** 2, owner="recovery", essential=True) as receipt:
        assert receipt.id
    assert ark_space.require("/srv/ark", 1024 ** 2, essential=True) is None


def test_a_page_says_not_measured_rather_than_zero(ark, monkeypatch):
    import ark_space
    fake_card(monkeypatch, usable=10 * 1024 ** 2)
    _break_the_ledger(monkeypatch, ark_space)
    room = ark_space.account("/srv/ark")
    assert room["committed_bytes"] is None
    assert room["usable_bytes"] is None
    assert room["ledger"] == "unreadable"
    assert "could not read" in ark_space.describe("/srv/ark")


def test_the_health_page_prints_not_measured_for_an_unreadable_ledger(ark, monkeypatch):
    import ark_health
    import ark_space
    fake_card(monkeypatch, usable=10 * 1024 ** 2)
    _break_the_ledger(monkeypatch, ark_space)
    section = ark_health.storage_section()
    assert section["facts"]["usable_bytes"] is None
    assert ark_health.human_size(None) == "not measured yet"


# ---------------------------------------------------------------------------
#  3. real processes, one card
# ---------------------------------------------------------------------------
CHILD_RESERVE = """
import sys
import ark_space
try:
    ark_space.reserve("/srv/ark", {need}, owner="{owner}")
except ark_space.Refused:
    print("refused")
else:
    print("promised")
"""


def test_six_processes_cannot_be_promised_the_same_bytes(ark, monkeypatch):
    """The race the whole module exists for: one card, six workers, ten mebibytes.

    Each asks for four; exactly two can be answered yes.  The rows are written in
    one ``BEGIN IMMEDIATE`` transaction per process, so the answer does not depend
    on how the scheduler interleaved them.
    """
    import ark_space
    fake_card(monkeypatch, usable=10 * 1024 ** 2)
    need = 4 * 1024 ** 2
    children = [spawn(CHILD_RESERVE.format(need=need, owner=f"job-{index}"))
                for index in range(6)]
    answers = []
    for child in children:
        out, err = child.communicate(timeout=60)
        assert child.returncode == 0, err
        answers.append(out.strip())
    assert answers.count("promised") == 2, answers
    assert answers.count("refused") == 4, answers
    assert promised(ark) == 8 * 1024 ** 2


def test_a_process_that_dies_holds_nothing(ark, monkeypatch):
    """A promise whose worker was killed is released by the next reconcile."""
    import ark_space
    fake_card(monkeypatch, usable=10 * 1024 ** 2)
    child = spawn("import time, ark_space;"
                  "ark_space.reserve('/srv/ark', %d, owner='working');"
                  "print('ready', flush=True);"
                  "time.sleep(60)" % (4 * 1024 ** 2))
    assert child.stdout.readline().strip() == "ready"
    with ark.db.read() as conn:
        assert int(conn.execute("SELECT COUNT(*) FROM space_reservations").fetchone()[0]) == 1
    child.kill()
    child.wait(timeout=10)
    with ark.db.tx() as conn:
        assert ark_space.reconcile(conn=conn) == 1
    assert promised(ark) == 0


# ---------------------------------------------------------------------------
#  4. the actual multipart path
# ---------------------------------------------------------------------------
def test_a_declared_body_is_refused_before_a_byte_is_read(public, monkeypatch):
    import ark_space
    fake_card(monkeypatch, usable=0)
    answer = public.client.post("/files", files={"file": ("x.txt", b"x" * 4096)},
                                follow_redirects=False)
    assert answer.status_code == 507
    assert "nothing was stored" in answer.text.lower()
    assert list((public.ark.root / "drop" / "files").iterdir()) == []
    assert rows(public.ark) == []


def test_the_peak_counts_the_framework_copy_and_the_route_s_copy(public, monkeypatch):
    """A file that would fit once is refused when it must fit twice.

    Starlette spools the body to ``TMPDIR``; the route then copies it into
    ``drop/tmp``.  Both exist at once, so a card with room for one copy has room
    for neither — the number the old check used was the file's size.
    """
    import ark_space
    body = 2 * 1024 ** 2
    fake_card(monkeypatch, usable=3 * 1024 ** 2)      # one copy: yes. two: no.
    answer = public.client.post("/files", files={"file": ("x.bin", b"x" * body)},
                                follow_redirects=False)
    assert answer.status_code == 507, answer.text
    assert rows(public.ark) == []


def test_a_file_that_fits_twice_is_accepted_and_leaves_no_promise(public, monkeypatch):
    import ark_space
    fake_card(monkeypatch, usable=8 * 1024 ** 2)
    answer = public.client.post("/files", files={"file": ("x.bin", b"x" * (1024 ** 2))},
                                follow_redirects=False)
    assert answer.status_code == 303
    assert "ok=1" in answer.headers["location"]
    stored = [p for p in (public.ark.root / "drop" / "files").iterdir() if p.is_file()]
    assert len(stored) == 1 and stored[0].stat().st_size == 1024 ** 2
    # The promise is given back when the response ends; the bytes are real.
    assert rows(public.ark) == []


def test_a_chunked_body_is_admitted_in_bounded_steps(public, monkeypatch):
    """No Content-Length is not an excuse to skip the question.

    The promise grows a step at a time as the bytes arrive, so the body can never
    write past what has been promised — and a body that keeps arriving is refused
    the moment the next step cannot be promised.
    """
    import ark_space
    fake_card(monkeypatch, usable=8 * 1024 ** 2)
    body = multipart(b"y" * (2 * 1024 ** 2), name="chunked.bin")

    def pieces():
        for start in range(0, len(body), 64 * 1024):
            yield body[start:start + 64 * 1024]

    answer = public.client.post(
        "/files", content=pieces(),
        headers={"content-type": "multipart/form-data; boundary=ARKBOUND"},
        follow_redirects=False)
    assert answer.status_code == 303, answer.text
    assert rows(public.ark) == []


def test_a_chunked_body_bigger_than_the_card_is_stopped(public, monkeypatch):
    import ark_space
    fake_card(monkeypatch, usable=3 * 1024 ** 2)
    body = multipart(b"z" * (6 * 1024 ** 2), name="huge.bin")

    def pieces():
        for start in range(0, len(body), 256 * 1024):
            yield body[start:start + 256 * 1024]

    answer = public.client.post(
        "/files", content=pieces(),
        headers={"content-type": "multipart/form-data; boundary=ARKBOUND"},
        follow_redirects=False)
    assert answer.status_code in (413, 507), answer.text
    assert "nothing was stored" in answer.text.lower()
    assert list((public.ark.root / "drop" / "files").iterdir()) == []
    assert rows(public.ark) == []


def test_an_address_with_no_door_is_small_not_unbounded():
    import ark_admission
    door = ark_admission.door_for("/some/new/page")
    assert door.limit == ark_admission.SMALL_BODY
    assert door.admit is False
    assert ark_admission.door_for("/files").limit >= 8 * 1024 ** 3
    assert ark_admission.door_for("/arcade/api/saves/7/state/1").limit == \
        ark_admission.SAVE_MAX + ark_admission.FRAMING


def test_a_route_adopts_the_request_s_promise_instead_of_promising_twice(ark, monkeypatch):
    """One receipt, two stages: the body and the copy made from it.

    If the spool opened its own reservation, a two-gigabyte upload would promise
    four gigabytes of *extra* room on top of the four the request already promised.
    """
    import ark_admission
    import ark_space
    fake_card(monkeypatch, usable=64 * 1024 ** 2)
    door = ark_admission.door_for("/files")
    admission = ark_admission.Admission(door, owner="test")
    token = ark_space.bind(admission)
    try:
        admission.begin(4 * 1024 ** 2)
        before = rows(ark)
        assert len(before) == 1
        assert before[0]["bytes"] == 4 * 1024 ** 2 * door.copies + ark_admission.FRAMING
        with ark_space.cover(public_drop(ark), 1024 ** 2, owner="spool", key="file") as receipt:
            assert receipt.id == before[0]["id"]
        assert [row["id"] for row in rows(ark)] == [before[0]["id"]]
        assert rows(ark)[0]["bytes"] == before[0]["bytes"]
    finally:
        ark_space.unbind(token)
        admission.release()
    assert rows(ark) == []


def public_drop(ark) -> Path:
    return ark.root / "drop" / "files"


# ---------------------------------------------------------------------------
#  5. cancellation and a client that walks away
# ---------------------------------------------------------------------------
def test_a_client_that_walks_away_leaves_no_promise(ark, monkeypatch):
    """A phone that loses Wi-Fi mid-upload must not hold the card until a reboot.

    A real uvicorn, a real socket, half a multipart body, then the socket closed.
    The reservation is released by the middleware's ``finally`` when the task is
    cancelled — not by a timer.
    """
    import ark_space
    fake_card(monkeypatch, usable=64 * 1024 ** 2)
    port = free_port()
    child = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1",
         "--port", str(port), "--log-level", "error", "--no-server-header"],
        cwd=str(API), env=child_env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True)
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/api/health", timeout=1) as answer:
                    if answer.status == 200:
                        break
            except (urllib.error.URLError, OSError):
                time.sleep(0.2)
        else:
            raise AssertionError("the test server did not start: "
                                 + (child.stderr.read() if child.stderr else ""))

        payload = multipart(b"q" * (4 * 1024 ** 2), name="half.bin")
        with socket.create_connection(("127.0.0.1", port), timeout=10) as wire:
            wire.sendall(
                b"POST /files HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                b"Content-Type: multipart/form-data; boundary=ARKBOUND\r\n"
                b"Content-Length: " + str(len(payload)).encode() + b"\r\n\r\n")
            wire.sendall(payload[: 256 * 1024])
            time.sleep(0.5)                      # let admission promise the peak
            assert rows(ark), "admission should have promised room before the body"
            wire.close()

        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if not rows(ark):
                break
            time.sleep(0.25)
        assert rows(ark) == []
        assert list((ark.root / "drop" / "files").iterdir()) == []
    finally:
        child.terminate()
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:     # pragma: no cover - a stuck child
            child.kill()


# ---------------------------------------------------------------------------
#  6. somebody else fills the card
# ---------------------------------------------------------------------------
def test_another_writer_filling_the_card_stops_the_upload(public, monkeypatch):
    """The real filesystem is asked again during receipt, not trusted from the start.

    A body that was admitted against 8 MiB of room is stopped when 7 of those
    mebibytes disappear while it is arriving.  The bytes that arrived are refused
    with a sentence, nothing is published, and the promise is given back.
    """
    import ark_admission
    import ark_space
    fake_card(monkeypatch, usable=8 * 1024 ** 2)
    real_facts = ark_space.facts
    calls = {"n": 0}

    def shrinking(path=None):
        facts = real_facts(path)
        calls["n"] += 1
        if calls["n"] > 2 and facts["free_bytes"] is not None:
            # Somebody outside the box has written most of the card away.
            reserve = int(facts["reserve_bytes"] or 0)
            return {**facts, "free_bytes": reserve + 4096,
                    "usable_bytes": 4096}
        return facts

    monkeypatch.setattr(ark_space, "facts", shrinking)
    body = multipart(b"w" * (6 * 1024 ** 2), name="contended.bin")

    def pieces():
        for start in range(0, len(body), 256 * 1024):
            yield body[start:start + 256 * 1024]

    answer = public.client.post(
        "/files", content=pieces(),
        headers={"content-type": "multipart/form-data; boundary=ARKBOUND"},
        follow_redirects=False)
    assert answer.status_code in (413, 507), answer.text
    assert "filled up" in answer.text.lower() or "not enough room" in answer.text.lower()
    assert list((public.ark.root / "drop" / "files").iterdir()) == []
    assert rows(public.ark) == []


def test_a_spool_stops_when_the_card_fills_mid_file(ark, monkeypatch):
    """Outside a request too: the promise is grown, not assumed, and stops clean."""
    import ark_space
    import ark_storage
    fake_card(monkeypatch, usable=16 * 1024 ** 2)
    remaining = iter([b"a" * (1024 * 1024)] * 64)

    def read(_size):
        return next(remaining, b"")

    with pytest.raises(ark_storage.Refused):
        ark_storage.spool(read, "too-big.bin")
    assert list((ark.root / "drop" / "files").iterdir()) == []
    assert [p for p in (ark.root / "drop" / "tmp").iterdir()] == []
    assert rows(ark) == []


# ---------------------------------------------------------------------------
#  7. controls that must keep working
# ---------------------------------------------------------------------------
def test_deleting_a_file_still_works_when_the_card_is_full(public, client, monkeypatch):
    """A full card must not take away the one action that makes room."""
    import ark_space
    from helpers import BIG_PASSWORD, admin_cookie, claim_box, csrf_of, get, post

    cookie = claim_box(client)
    answer = public.client.post("/files", files={"file": ("keep.txt", b"kept")},
                                follow_redirects=False)
    assert answer.status_code == 303
    with public.ark.db.read() as conn:
        file_id = int(conn.execute("SELECT id FROM files ORDER BY id DESC").fetchone()[0])

    fake_card(monkeypatch, usable=0)
    page = get(client, "/admin/community", cookie=f"ark_admin={cookie}")
    token = csrf_of(page.text)
    removed = post(client, f"/admin/files/{file_id}/delete",
                   {"current_password": BIG_PASSWORD, "csrf": token},
                   cookie=f"ark_admin={cookie}")
    assert removed.status_code == 303
    assert list((public.ark.root / "drop" / "files").iterdir()) == []


def test_the_recovery_copy_is_kept_even_when_admission_would_refuse(ark, monkeypatch):
    """Recovery is essential: it competes for the last bytes rather than refusing.

    Every byte the card can spare is promised to another job, so a bulk write is
    refused — but the records still have room to be copied.  That is the state a
    box must never be stuck in: full *and* unable to save its own records.
    """
    import ark_recovery
    import ark_space
    usable = 64 * 1024 ** 2
    fake_card(monkeypatch, usable=usable)
    with ark.db.tx() as conn:
        ark_space.reserve("/srv/ark", usable, owner="another-job", conn=conn)
        conn.execute("INSERT INTO settings (key, value) VALUES ('seed', '1')"
                     " ON CONFLICT(key) DO UPDATE SET value = excluded.value")
    with pytest.raises(ark_space.Refused):
        ark_space.require("/srv/ark", 1024 ** 2)      # bulk: nothing left
    result = ark_recovery.capture(ark.root / "api" / "data" / "ark.db",
                                  root=ark.root, keep=2)
    assert result.status == "captured", result.said
    assert result.path is not None and (result.path / "ark.db").is_file()
    # The copy is real, and the other job's promise was not disturbed.
    with ark.db.read() as conn:
        assert int(conn.execute("SELECT COUNT(*) FROM space_reservations"
                                " WHERE owner = 'another-job'").fetchone()[0]) == 1


# ---------------------------------------------------------------------------
#  8. the ledger's own arithmetic
# ---------------------------------------------------------------------------
def test_a_promise_is_the_peak_on_disk_not_a_sum_of_every_stage(ark, monkeypatch):
    """``reserved`` is what the card must hold at once, so stages are not added blindly.

    A stage's peak is "what other stages have on the card now" plus "what this
    stage will hold".  The body's 30 MiB is still on disk while the copy is made,
    so a 5 MiB copy fits inside the 30 already promised; a 25 MiB copy does not and
    raises the promise to 35.
    """
    import ark_space
    fake_card(monkeypatch, usable=100 * 1024 ** 2)
    with ark_space.hold("/srv/ark", 10 * 1024 ** 2, owner="job") as receipt:
        receipt.peak("body", 30 * 1024 ** 2)
        assert receipt.reserved == 30 * 1024 ** 2
        receipt.spent("body", 10 * 1024 ** 2)
        receipt.peak("copy", 5 * 1024 ** 2)
        assert receipt.reserved == 30 * 1024 ** 2      # 10 on disk + 5 fits
        receipt.peak("copy", 25 * 1024 ** 2)
        assert receipt.reserved == 35 * 1024 ** 2      # 10 on disk + 25 is the peak
    assert promised(ark) == 0


def test_bytes_written_are_not_promised_twice(ark, monkeypatch):
    """``written`` comes out of the promise exactly once; statvfs has the rest."""
    import ark_space
    fake_card(monkeypatch, usable=10 * 1024 ** 2)
    with ark_space.hold("/srv/ark", 4 * 1024 ** 2, owner="job") as receipt:
        assert promised(ark) == 4 * 1024 ** 2
        receipt.spent("main", 3 * 1024 ** 2)
        assert promised(ark) == 1 * 1024 ** 2
        receipt.spent("main", 3 * 1024 ** 2)      # repeated: no change
        assert promised(ark) == 1 * 1024 ** 2


def test_a_released_promise_cannot_be_written_into(ark, monkeypatch):
    import ark_space
    fake_card(monkeypatch, usable=10 * 1024 ** 2)
    receipt = ark_space.open_receipt("/srv/ark", owner="job")
    receipt.need(1024 ** 2)
    receipt.release()
    with pytest.raises(ark_space.Refused):
        receipt.need(2 * 1024 ** 2)


def test_the_spool_adopts_the_request_promise_end_to_end(public, monkeypatch):
    """The route's own copy joins the request's promise, not a second one.

    Starlette runs a plain ``def`` route in a worker thread.  ``contextvars`` are
    copied into that thread by anyio, so the spool sees the admission the
    middleware bound.  If it did not, every upload would promise its bytes twice:
    once for the body and once for the copy — and the second promise is the one
    that fails on a card that was big enough all along.
    """
    import ark_space
    calls = []
    real = ark_space.hold

    def spy(*args, **kwargs):
        calls.append(args)
        return real(*args, **kwargs)

    monkeypatch.setattr(ark_space, "hold", spy)
    answer = public.client.post("/files", files={"file": ("a.txt", b"x" * 1000)},
                                follow_redirects=False)
    assert answer.status_code == 303, answer.text
    assert calls == [], "the spool opened its own reservation instead of adopting the request's"


def test_only_a_bounded_number_of_large_bodies_are_received_at_once(ark, monkeypatch):
    """A hundred phones must not each promise the card a hundred times over.

    The middleware holds a slot for the whole time a large body is arriving; a
    second one that cannot get a slot within a moment is told to come back rather
    than queued for ever.  This drives the real middleware over a bare ASGI app,
    so the semaphore, the wait and the 503 are the shipped ones.
    """
    import asyncio

    import ark_admission

    fake_card(monkeypatch, usable=256 * 1024 ** 2)
    monkeypatch.setattr(ark_admission, "SLOTS", 1)
    monkeypatch.setattr(ark_admission, "SLOT_WAIT", 0.2)

    async def scenario():
        started = asyncio.Event()
        release_a = asyncio.Event()

        async def echo(scope, receive, send):
            name = dict(scope["headers"])[b"x-name"].decode()
            while True:
                message = await receive()
                if message["type"] != "http.request" or not message.get("more_body"):
                    break
                if name == "a" and not started.is_set():
                    started.set()
                    await release_a.wait()
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"content-length", b"2")]})
            await send({"type": "http.response.body", "body": b"ok"})

        app = ark_admission.BodyAdmissionMiddleware(echo)
        body = b"x" * 100

        def scope(name):
            return {"type": "http", "method": "POST", "path": "/files",
                    "headers": [(b"x-name", name.encode()),
                                (b"content-length", str(len(body)).encode())]}

        async def call(name):
            sent = []
            queue = [{"type": "http.request", "body": body, "more_body": True},
                     {"type": "http.request", "body": b"", "more_body": False}]

            async def receive():
                return queue.pop(0) if queue else {"type": "http.request",
                                                   "body": b"", "more_body": False}

            async def send(message):
                sent.append(message)

            await app(scope(name), receive, send)
            return next(m["status"] for m in sent if m["type"] == "http.response.start")

        first = asyncio.create_task(call("a"))
        await started.wait()
        second = await call("b")
        release_a.set()
        return await first, second

    status_a, status_b = asyncio.run(scenario())
    assert status_a == 200
    assert status_b == 503, "a second large body should be told to come back"


def test_an_upload_during_a_maintenance_window_is_busy_not_broken(public, monkeypatch):
    """The records being put back is a 503 with a sentence, not a 500.

    A page that reported damage because the daily repair pass held the gate would
    be worse than the wait: nothing is wrong, and the box says so.
    """
    import ark_maintenance
    import ark_space
    fake_card(monkeypatch, usable=64 * 1024 ** 2)

    def busy(*_args, **_kwargs):
        raise ark_maintenance.Busy("the records are being replaced")

    monkeypatch.setattr(ark_space, "open_receipt", busy)
    answer = public.client.post("/files", files={"file": ("x.txt", b"hello")},
                                follow_redirects=False)
    assert answer.status_code == 503
    assert "putting its records back" in answer.text.lower()
    assert list((public.ark.root / "drop" / "files").iterdir()) == []
