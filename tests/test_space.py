"""The card's arithmetic — one policy, and promises that other jobs can see.

Every check here runs against a throwaway database and a *pretended* filesystem:
the numbers a real card reports would make the test depend on the machine it runs
on, and the point of ``ark_space`` is that the arithmetic is the same everywhere.
"""

from __future__ import annotations

import subprocess
import sys
import time

import pytest


# ---------------------------------------------------------------------------
#  the policy itself
# ---------------------------------------------------------------------------
def _fixed(total, free, monkeypatch, space, percent=None):
    """Make one filesystem answer with known numbers."""
    if percent is not None:
        monkeypatch.setenv("ARK_RESERVE_PERCENT", str(percent))
    monkeypatch.setattr(space, "facts", lambda path=None: {
        "path": "/srv/ark", "device": 7, "total_bytes": total, "free_bytes": free,
        "reserve_bytes": space.reserve_for(total), "usable_bytes": max(
            0, free - space.reserve_for(total)), "percent": space.reserve_percent()})
    monkeypatch.setattr(space, "device_key", lambda path=None: 7)


def test_the_reserve_is_seven_percent_of_the_card_that_holds_the_bytes(ark, monkeypatch):
    import ark_space
    total = 200 * 1024 ** 3
    assert ark_space.reserve_for(total) == int(total * 0.07)


def test_a_small_card_still_keeps_two_gibibytes(ark, monkeypatch):
    import ark_space
    monkeypatch.setenv("ARK_SPACE_CONFIG", "/nonexistent/ark.env")
    assert ark_space.reserve_for(10 * 1024 ** 3) == 2 * 1024 ** 3


def test_the_percentage_is_the_owners_and_a_typo_falls_back(ark, monkeypatch):
    import ark_space
    monkeypatch.setenv("ARK_SPACE_CONFIG", "/nonexistent/ark.env")
    monkeypatch.setenv("ARK_RESERVE_PERCENT", "12")
    assert ark_space.reserve_percent() == 12.0
    monkeypatch.setenv("ARK_RESERVE_PERCENT", "not a number")
    assert ark_space.reserve_percent() == ark_space.DEFAULT_PERCENT
    monkeypatch.setenv("ARK_RESERVE_PERCENT", "99")
    assert ark_space.reserve_percent() == ark_space.DEFAULT_PERCENT


def test_the_env_file_is_read_when_the_environment_is_silent(ark, monkeypatch, tmp_path):
    import ark_space
    monkeypatch.delenv("ARK_RESERVE_PERCENT", raising=False)
    conf = tmp_path / "ark.env"
    conf.write_text('HOTSPOT_SSID="x"\nARK_RESERVE_PERCENT="9"\n', encoding="utf-8")
    monkeypatch.setenv("ARK_SPACE_CONFIG", str(conf))
    assert ark_space.reserve_percent() == 9.0


def test_free_space_is_what_an_ordinary_account_may_write(ark, monkeypatch):
    """``f_bavail``, never ``f_bfree``: the slice kept for root is not our room."""
    import ark_space
    seen = {}

    class Stats:
        f_blocks = 1000
        f_frsize = 1024
        f_bavail = 400          # the honest number
        f_bfree = 500           # ...including blocks pi may not use

    def fake(path):
        seen["called"] = True
        return Stats()

    monkeypatch.setattr(ark_space.os, "statvfs", fake)
    facts = ark_space.facts("/srv/ark")
    assert seen["called"] is True
    assert facts["free_bytes"] == 400 * 1024
    assert facts["total_bytes"] == 1000 * 1024


# ---------------------------------------------------------------------------
#  promises shared between processes
# ---------------------------------------------------------------------------
def test_a_known_size_operation_is_refused_before_it_crosses_the_reserve(ark, monkeypatch):
    import ark_space
    _fixed(100 * 1024 ** 3, 10 * 1024 ** 3, monkeypatch, ark_space)
    # 7 % of 100 GiB is 7 GiB, so 3 GiB is usable and 4 GiB is not.
    ark_space.require("/srv/ark", 3 * 1024 ** 3)
    with pytest.raises(ark_space.Refused) as refused:
        ark_space.require("/srv/ark", 4 * 1024 ** 3)
    assert "not enough room" in str(refused.value).lower()


def test_two_jobs_cannot_both_be_promised_the_same_bytes(ark, monkeypatch):
    import ark_space
    _fixed(100 * 1024 ** 3, 10 * 1024 ** 3, monkeypatch, ark_space)
    first = ark_space.reserve("/srv/ark", 2 * 1024 ** 3, owner="job-1", label="film")
    with pytest.raises(ark_space.Refused):
        # 1 GiB was left; the second job wants 2.
        ark_space.reserve("/srv/ark", 2 * 1024 ** 3, owner="job-2", label="film")
    with ark_db_tx(ark) as conn:
        assert ark_space.committed(conn, 7) == 2 * 1024 ** 3
        ark_space.release(conn, first)
        assert ark_space.committed(conn, 7) == 0


def test_bytes_already_written_are_not_counted_twice(ark, monkeypatch):
    import ark_space
    _fixed(100 * 1024 ** 3, 10 * 1024 ** 3, monkeypatch, ark_space)
    with ark_db_tx(ark) as conn:
        one = ark_space.reserve("/srv/ark", 2 * 1024 ** 3, owner="job", conn=conn)
        assert ark_space.committed(conn, 7) == 2 * 1024 ** 3
        ark_space.touch(conn, one, 1536 * 1024 ** 2)     # 1.5 GiB is on disk now
        assert ark_space.committed(conn, 7) == 512 * 1024 ** 2
        ark_space.touch(conn, one, 1024)                 # never goes backwards
        assert ark_space.committed(conn, 7) == 512 * 1024 ** 2


def test_a_hold_is_released_even_when_the_work_fails(ark, monkeypatch):
    import ark_space
    _fixed(100 * 1024 ** 3, 10 * 1024 ** 3, monkeypatch, ark_space)
    with pytest.raises(RuntimeError):
        with ark_space.hold("/srv/ark", 1024 ** 3, owner="job-x") as receipt:
            assert receipt.id
            raise RuntimeError("the tool died")
    with ark_db_tx(ark) as conn:
        assert ark_space.committed(conn, 7) == 0


def test_a_promise_whose_process_is_gone_is_released(ark, monkeypatch):
    """The promise belongs to a process, not to a heartbeat.

    A killed worker's row goes at the next reconcile; a *live* worker's row stays
    however quiet it has been (proved in ``tests/test_admission.py``).  This is
    the rule that replaced "delete anything older than fifteen minutes", which
    either leaked a power cut's promises or threw away a real hour-long encode.
    """
    import ark_space
    _fixed(100 * 1024 ** 3, 10 * 1024 ** 3, monkeypatch, ark_space)
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait()
    with ark_db_tx(ark) as conn:
        ark_space.reserve("/srv/ark", 1024 ** 3, owner="dead-job", conn=conn)
        conn.execute("UPDATE space_reservations SET pid = ?, boot = ?, heartbeat = ?",
                     (gone.pid, ark_space.current_boot(), time.time() - 9999))
    with ark_db_tx(ark) as conn:
        assert ark_space.reconcile(conn=conn) == 1
        assert ark_space.committed(conn, 7) == 0


def test_an_operation_that_does_not_fit_says_how_much_it_needs(ark, monkeypatch):
    import ark_space
    _fixed(100 * 1024 ** 3, 5 * 1024 ** 3, monkeypatch, ark_space)
    with pytest.raises(ark_space.Refused) as refused:
        ark_space.reserve("/srv/ark", 3 * 1024 ** 3, owner="job")
    text = str(refused.value)
    assert "needs 3.0 GB" in text
    assert "Delete something" in text


def test_the_breach_is_reported_rather_than_raised(ark, monkeypatch):
    import ark_space
    _fixed(100 * 1024 ** 3, 1024, monkeypatch, ark_space)
    room = ark_space.account("/srv/ark")
    assert room["breached"] is True
    assert room["usable_bytes"] == 0


# ---------------------------------------------------------------------------
#  the shell door
# ---------------------------------------------------------------------------
def test_the_command_says_yes_quietly_and_no_with_a_reason(ark, monkeypatch, capsys):
    import importlib.util
    from importlib.machinery import SourceFileLoader
    from pathlib import Path
    repo = Path(__file__).resolve().parents[1]
    loader = SourceFileLoader("ark_space_cli", str(repo / "pi/bin/ark-space"))
    spec = importlib.util.spec_from_loader("ark_space_cli", loader)
    cli = importlib.util.module_from_spec(spec)
    loader.exec_module(cli)
    import ark_space
    _fixed(100 * 1024 ** 3, 10 * 1024 ** 3, monkeypatch, ark_space)
    assert cli.main(["--check", "--need", str(1024 ** 2), "--path", "/srv/ark"]) == 0
    assert capsys.readouterr().out == ""
    assert cli.main(["--check", "--need", str(9 * 1024 ** 3)]) == 3
    assert "not enough room" in capsys.readouterr().err.lower()


def ark_db_tx(ark):
    return ark.db.tx()
