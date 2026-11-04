"""A deadline that depends on the child speaking is not a deadline.

The reviewed defect, reproduced with a real subprocess: a child that slept 0.4 s
returned **success** against a 0.05 s deadline, because ``_run`` only checked the
clock inside ``for line in proc.stdout`` and a silent child never entered the
loop.  These tests use ``sys.executable`` so they exercise a real process, a real
pipe, a real kill and a real reap — not a mock that agrees with the code.
"""

from __future__ import annotations

import os
import sys
import textwrap
import time

import pytest


def _child(tmp_path, body: str):
    script = tmp_path / "child.py"
    script.write_text("import os, sys, time\n" + textwrap.dedent(body), encoding="utf-8")
    return [sys.executable, str(script)]


def _pid_file(tmp_path):
    return tmp_path / "child.pid"


def _gone(pid_file) -> bool:
    pid = int(pid_file.read_text().strip())
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:                       # pragma: no cover - not ours
        return False
    return False


def test_a_silent_child_is_stopped_at_its_deadline(ark, tmp_path):
    import ark_media_jobs

    pid_file = _pid_file(tmp_path)
    cmd = _child(tmp_path, f"""
        open({str(pid_file)!r}, "w").write(str(os.getpid()))
        time.sleep(10)
    """)
    started = time.monotonic()
    with pytest.raises(RuntimeError) as caught:
        ark_media_jobs._run(cmd, deadline=0.15)
    elapsed = time.monotonic() - started
    assert elapsed < 2.0, f"the deadline was not enforced ({elapsed:.2f}s)"
    assert "longer" in str(caught.value).lower()
    assert _gone(pid_file), "the child was killed but not reaped"


def test_a_partial_line_does_not_hide_the_deadline(ark, tmp_path):
    import ark_media_jobs

    pid_file = _pid_file(tmp_path)
    cmd = _child(tmp_path, f"""
        open({str(pid_file)!r}, "w").write(str(os.getpid()))
        sys.stdout.write("total_size=12")   # deliberately no newline
        sys.stdout.flush()
        time.sleep(10)
    """)
    started = time.monotonic()
    with pytest.raises(RuntimeError):
        ark_media_jobs._run(cmd, deadline=0.15)
    assert time.monotonic() - started < 2.0
    assert _gone(pid_file)


def test_continuous_output_cannot_outrun_the_deadline(ark, tmp_path):
    import ark_media_jobs

    pid_file = _pid_file(tmp_path)
    cmd = _child(tmp_path, f"""
        open({str(pid_file)!r}, "w").write(str(os.getpid()))
        while True:
            sys.stdout.write("out_time_us=1\\n")
            sys.stdout.flush()
    """)
    lines = []
    started = time.monotonic()
    with pytest.raises(RuntimeError):
        ark_media_jobs._run(cmd, deadline=0.3, on_line=lines.append)
    elapsed = time.monotonic() - started
    assert elapsed < 3.0, f"continuous output starved the clock ({elapsed:.2f}s)"
    assert lines, "progress lines should still have been delivered"
    assert _gone(pid_file)


def test_stderr_growth_is_bounded_and_still_names_the_fault(ark, tmp_path):
    import ark_media_jobs

    cmd = _child(tmp_path, """
        for _ in range(4000):
            sys.stderr.write("a noisy warning that must not fill memory\\n")
        sys.stderr.write("the actual refusal is on this line\\n")
        raise SystemExit(3)
    """)
    with pytest.raises(RuntimeError) as caught:
        ark_media_jobs._run(cmd, deadline=10)
    message = str(caught.value)
    assert "the actual refusal is on this line" in message
    assert len(message) < 500, "the complaint must be a tail, not the whole log"


def test_a_clean_child_that_says_nothing_succeeds(ark, tmp_path):
    import ark_media_jobs

    cmd = _child(tmp_path, """
        time.sleep(0.05)
        raise SystemExit(0)
    """)
    ark_media_jobs._run(cmd, deadline=5)


def test_progress_lines_are_still_streamed(ark, tmp_path):
    import ark_media_jobs

    cmd = _child(tmp_path, """
        for value in (1, 2, 3):
            sys.stdout.write(f"total_size={value}\\n")
            sys.stdout.flush()
            time.sleep(0.02)
    """)
    seen = []
    ark_media_jobs._run(cmd, deadline=5, on_line=seen.append)
    assert seen == ["total_size=1", "total_size=2", "total_size=3"]


def test_a_cancelled_job_stops_a_silent_child(ark, tmp_path, monkeypatch):
    """Stop is enforced by the loop, not by the next progress line."""
    import ark_media_jobs

    monkeypatch.setattr(ark_media_jobs, "CANCEL_CHECK_SECONDS", 0.02)
    answers = iter([False, True])
    monkeypatch.setattr(ark_media_jobs, "_cancelled", lambda job_id: next(answers, True))
    pid_file = _pid_file(tmp_path)
    cmd = _child(tmp_path, f"""
        open({str(pid_file)!r}, "w").write(str(os.getpid()))
        time.sleep(10)
    """)
    started = time.monotonic()
    with pytest.raises(ark_media_jobs.Stopped):
        ark_media_jobs._run(cmd, job_id=7, deadline=30)
    assert time.monotonic() - started < 2.0
    assert _gone(pid_file)
