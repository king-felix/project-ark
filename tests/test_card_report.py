"""The read-only card report: five numbers that are not the same number.

After a cloning migration the card, the partition, the filesystem and the free
space genuinely disagree, and "how big is my card?" has four defensible answers.
``pi/bin/ark-card-report`` exists to say which is which, and to say plainly when
the extra room is not usable yet.

Nothing here needs a card: the physical and partition sizes come from a
disposable sysfs tree, and the arithmetic is exercised directly through
``summarise()`` with every input passed in.

The rule that makes this worth testing at all is the project's ``None`` rule:
an unmeasured size is printed as "not measured yet", never as 0.  A report that
said "0 bytes" on a card it could not read would be worse than no report.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "pi" / "bin" / "ark-card-report"
STORAGE_STEP = REPO / "pi" / "steps" / "20-storage.sh"
API_DIR = REPO / "pi" / "api"

GiB = 1024 ** 3
GB = 1000 ** 3


def load_report():
    loader = SourceFileLoader("ark_card_report_under_test", str(SCRIPT))
    spec = importlib.util.spec_from_loader("ark_card_report_under_test", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def report():
    return load_report()


# ---------------------------------------------------------------------------
#  the arithmetic
# ---------------------------------------------------------------------------
def test_a_fresh_install_uses_the_whole_card(report):
    result = report.summarise(
        card=512 * GB, part=512 * GB, fs_total=int(476 * GiB),
        fs_free=int(400 * GiB), reserve=int(33 * GiB))
    assert result["whole_card"] is True
    assert result["verdict"] == "the whole card is in use"


def test_a_cloned_512_gb_card_that_was_never_expanded_is_named_as_such(report):
    """The exact state after `clone-card.sh`: new card, old partition table."""
    result = report.summarise(
        card=512_107_000_000, part=256_086_000_000, fs_total=int(238 * GiB),
        fs_free=int(180 * GiB), reserve=int(16 * GiB))
    assert result["whole_card"] is False
    assert "has not been expanded" in result["verdict"]
    assert "old card" in result["detail"]


def test_a_partition_that_the_filesystem_has_not_caught_up_with_is_named(report):
    result = report.summarise(
        card=512_107_000_000, part=512_107_000_000, fs_total=int(238 * GiB),
        fs_free=int(180 * GiB), reserve=int(16 * GiB))
    assert result["whole_card"] is False
    assert "filesystem has not been grown" in result["verdict"]


def test_the_reserve_is_subtracted_from_free_space_and_never_goes_negative(report):
    tight = report.summarise(card=128 * GB, part=128 * GB, fs_total=100 * GiB,
                             fs_free=1 * GiB, reserve=7 * GiB)
    assert tight["usable_bytes"] == 0
    roomy = report.summarise(card=512 * GB, part=512 * GB, fs_total=476 * GiB,
                             fs_free=300 * GiB, reserve=33 * GiB)
    assert roomy["usable_bytes"] == 267 * GiB


@pytest.mark.parametrize("card,part,fs_total", [
    (None, 256 * GB, 238 * GiB),
    (512 * GB, None, 238 * GiB),
    (512 * GB, 256 * GB, None),
])
def test_an_unmeasured_layer_is_not_rendered_as_zero(report, card, part, fs_total):
    result = report.summarise(card=card, part=part, fs_total=fs_total,
                              fs_free=None, reserve=None)
    assert result["verdict"] == "not measured yet"
    assert result["whole_card"] is False
    assert result["usable_bytes"] is None
    assert report._size(None) == "not measured yet"
    assert report._size(0) == "0 B"          # a measured zero is still a zero


def test_the_report_uses_the_one_reserve_policy(report):
    """Not a second 7 %: the same function every write path asks."""
    import ark_space
    total = 476 * GiB
    reserve, percent = report.reserve_for(total)
    assert reserve == ark_space.reserve_for(total)
    assert percent == ark_space.reserve_percent()


# ---------------------------------------------------------------------------
#  the wiring: real script, fake sysfs, no card
# ---------------------------------------------------------------------------
def sysfs(tmp_path, card_sectors, part_sectors, part="mmcblk0p2"):
    root = tmp_path / "sys-block"
    disk = "mmcblk0"
    (root / disk / part).mkdir(parents=True)
    (root / disk / "size").write_text(str(card_sectors), encoding="ascii")
    (root / disk / part / "size").write_text(str(part_sectors), encoding="ascii")
    return root, disk, part


def run_report(tmp_path, root, disk, part, *args):
    env = dict(os.environ)
    env["ARK_SYS_BLOCK"] = str(root)
    env["ARK_CARD_SOURCE"] = f"/dev/{part}"
    env["ARK_CARD_DEVICE"] = disk
    env["ARK_CARD_MOUNT"] = str(tmp_path)
    env["ARK_API_DIR"] = str(API_DIR)
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True, env=env, timeout=40)


def test_a_cloned_card_reports_both_sizes_and_exits_four(tmp_path):
    root, disk, part = sysfs(tmp_path, 1_000_215_216, 500_000_000)   # 512 GB / 256 GB
    done = run_report(tmp_path, root, disk, part)
    assert done.returncode == 4, done.stderr
    assert "512.1 GB" in done.stdout
    assert "256.0 GB" in done.stdout
    assert "has not been expanded" in done.stdout
    assert "growpart" in done.stdout                # the procedure, printed only


def test_the_filesystem_figure_is_measured_not_copied_from_the_partition(tmp_path):
    """Three different numbers, three different sources — that is the point.

    If the report echoed the partition size as "the filesystem", a card that
    had been re-partitioned but never re-grown would look finished.
    """
    root, disk, part = sysfs(tmp_path, 1_000_215_216, 1_000_000_000)
    data = json.loads(run_report(tmp_path, root, disk, part, "--json").stdout)
    st = os.statvfs(str(tmp_path))
    assert data["partition_bytes"] == 1_000_000_000 * 512
    assert data["filesystem_bytes"] == st.f_blocks * st.f_frsize
    assert data["filesystem_bytes"] != data["partition_bytes"]


def test_json_carries_every_number_for_a_program(tmp_path):
    root, disk, part = sysfs(tmp_path, 1_000_215_216, 500_000_000)
    done = run_report(tmp_path, root, disk, part, "--json")
    data = json.loads(done.stdout)
    assert data["card_bytes"] == 1_000_215_216 * 512
    assert data["partition_bytes"] == 500_000_000 * 512
    assert data["card_device"] == "mmcblk0"
    assert data["partition_device"] == "mmcblk0p2"
    assert data["reserve_percent"] == pytest.approx(7.0)
    measured = os.statvfs(str(tmp_path))
    assert data["filesystem_bytes"] == measured.f_blocks * measured.f_frsize
    assert data["free_bytes"] == measured.f_bavail * measured.f_frsize
    for key in ("reserve_bytes", "usable_bytes", "verdict", "whole_card"):
        assert key in data


def test_an_unreadable_machine_exits_five_and_says_not_measured(tmp_path):
    empty = tmp_path / "empty-sys"
    empty.mkdir()
    done = run_report(tmp_path, empty, "mmcblk0", "mmcblk0p2")
    assert done.returncode == 5, done.stdout
    assert "not measured yet" in done.stdout


def test_the_partition_is_found_under_its_disk_in_sysfs(tmp_path):
    """Whole disks sit at <root>/<name>/size; partitions one level deeper."""
    root, disk, part = sysfs(tmp_path, 1_000_215_216, 500_000_000)
    assert (root / disk / part / "size").is_file()
    done = run_report(tmp_path, root, disk, part, "--json")
    assert json.loads(done.stdout)["partition_bytes"] == 500_000_000 * 512


# ---------------------------------------------------------------------------
#  it is a report, not a repair
# ---------------------------------------------------------------------------
def test_the_report_can_never_write_or_resize_anything():
    """The expansion is advice in the text, never a command it runs.

    A read-only check that quietly ran resize2fs would be the worst possible
    thing to hand somebody during a migration, so this pins the absence of
    every writing call in the source.
    """
    source = SCRIPT.read_text(encoding="utf-8")
    for forbidden in ('"growpart"', '"resize2fs"', 'open(', 'write_text', 'write_bytes',
                      'os.mkdir', 'os.makedirs', 'os.remove', 'os.rename',
                      'shutil.', 'unlink('):
        assert forbidden not in source, f"{forbidden} has no place in a read-only report"
    assert "growpart" in source and "resize2fs" in source, "the procedure must still be printed"


def test_the_storage_phase_installs_the_report():
    text = STORAGE_STEP.read_text(encoding="utf-8")
    assert 'bin/ark-card-report" /usr/local/sbin/ark-card-report 755' in text
