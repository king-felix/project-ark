"""The SD-card guard: capacity classes, closed failures, and a second look.

These tests drive the **real** ``scripts/lib-disk-guard.sh`` under ``bash``
against a stand-in ``diskutil`` that emits genuine plist bytes.  Nothing here
needs a card, a reader, root, or macOS: the fixture is a JSON "world" that the
fake turns into exactly the metadata a real reader would return.

What is being defended, in order of how badly it would hurt:

* **capacity is not the test.**  A device passes only if *every* independent
  fact agrees — external, physical, whole, non-internal, removable/ejectable,
  and inside a supported capacity class.
* **missing metadata is a refusal, not a shrug.**  The old guard compared
  ``Internal == yes``, so a reader that answered nothing at all looked safe.
* **a device name is not a device.**  ``/dev/disk4`` is "whatever is fourth
  today".  The identity captured when the card was chosen is re-checked, in the
  process that writes, immediately before the write.

The sizes below are the decimal byte counts ``diskutil info TotalSize`` reports
for real cards of each class — 128/256/512 GB nominal, which are always a
little over the round number.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
GUARD = REPO / "scripts" / "lib-disk-guard.sh"
FLASH = REPO / "scripts" / "01-flash.sh"
FIX_SHELL = REPO / "scripts" / "fix-pi-shell.sh"
CLONE = REPO / "scripts" / "clone-card.sh"

GB = 1000 ** 3
CARD_128 = 128_043_000_000        # a real "128 GB" microSD
CARD_256 = 256_086_000_000        # a real "256 GB" microSD
CARD_512 = 512_107_000_000        # a real "512 GB" microSD
CARD_64 = 63_864_000_000          # a real "64 GB" microSD

# ---------------------------------------------------------------------------
#  the stand-in: two subcommands, genuine plists
# ---------------------------------------------------------------------------
FAKE_DISKUTIL = '''#!{python}
"""A stand-in for macOS diskutil: `list external physical` and `info -plist`.

The world is a JSON file named by FAKE_DISKUTIL_WORLD:
    {{"disks": {{"disk4": {{...plist keys...}}}}, "external": ["disk4"]}}
A key missing from a disk dict is a key the reader did not report.
"""
import json, os, plistlib, sys

world = json.load(open(os.environ["FAKE_DISKUTIL_WORLD"]))
argv = sys.argv[1:]

if argv[:3] == ["list", "external", "physical"]:
    if world.get("list_broken"):
        print("diskutil: could not list disks", file=sys.stderr)
        raise SystemExit(1)
    for dev in world.get("external", []):
        print("/dev/%s (external, physical):" % dev)
        print("   #:                       TYPE NAME                    SIZE       IDENTIFIER")
        print("   0:     FDisk_partition_scheme                        *%s     %s" % ("X", dev))
    raise SystemExit(0)

if argv[:2] == ["info", "-plist"]:
    if world.get("info_broken"):
        print("diskutil: could not open the device", file=sys.stderr)
        raise SystemExit(1)
    dev = os.path.basename(argv[2])
    disks = world.get("disks", {{}})
    if dev not in disks:
        print("diskutil: no such disk", file=sys.stderr)
        raise SystemExit(1)
    sys.stdout.buffer.write(plistlib.dumps(disks[dev]))
    raise SystemExit(0)

print("fake diskutil: unsupported %r" % (argv,), file=sys.stderr)
raise SystemExit(2)
'''


def card(size, **over):
    """A whole external SD card as macOS describes one, with overrides.

    ``None`` removes a key entirely — that is how "the reader did not report
    this" is expressed, and it is different from an empty value.
    """
    fields = {
        "Internal": False,
        "RemovableMedia": True,
        "Removable": True,
        "Ejectable": True,
        "WholeDisk": True,
        "VirtualOrPhysical": "Physical",
        "TotalSize": size,
        "BusProtocol": "USB",
        "MediaName": "SD Card Reader",
        "DiskUUID": "A1B2-C3D4",
        "MountPoint": "",
    }
    for key, value in over.items():
        if value is None:
            fields.pop(key, None)
        else:
            fields[key] = value
    return fields


class World:
    """A disposable machine: a world file, a fake diskutil, and a runner."""

    def __init__(self, tmp_path):
        self.tmp = tmp_path
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        fake = self.bin / "diskutil"
        fake.write_text(FAKE_DISKUTIL.format(python=sys.executable), encoding="utf-8")
        fake.chmod(0o755)
        self.path = tmp_path / "world.json"

    def set(self, disks, external=None, **extra):
        world = {
            "disks": disks,
            "external": list(disks) if external is None else list(external),
        }
        world.update(extra)
        self.path.write_text(json.dumps(world), encoding="utf-8")
        return self

    def run(self, snippet, args=(), timeout=40):
        """Source the guard and run one of its functions in a fresh bash."""
        env = dict(os.environ)
        env["PATH"] = str(self.bin) + os.pathsep + env.get("PATH", "")
        env["FAKE_DISKUTIL_WORLD"] = str(self.path)
        env.pop("ARK_CARD_BANDS", None)
        command = 'source "$1"; shift; ' + snippet + ' "$@"'
        return subprocess.run(
            ["bash", "-c", command, "_", str(GUARD), *args],
            capture_output=True, text=True, env=env, timeout=timeout,
        )


@pytest.fixture()
def world(tmp_path):
    return World(tmp_path)


def assert_refused(done, *needles):
    assert done.returncode != 0, f"the guard accepted it:\n{done.stdout}\n{done.stderr}"
    assert done.stdout.strip() == "", f"a refusal must not print a device: {done.stdout!r}"
    for needle in needles:
        assert needle in done.stderr, f"missing {needle!r} in:\n{done.stderr}"


# ---------------------------------------------------------------------------
#  1. the supported capacity classes
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("size,label", [
    (CARD_128, "128"),
    (CARD_256, "256"),
    (CARD_512, "512"),
    (512 * 2 ** 30, "512"),        # a card built from 512 GiB of flash
    (549_755_813_888, "512"),
    (127_000_000_000, "128"),
    (247_000_000_000, "256"),      # a 256 GB card that reports low
    (440_000_000_000, "512"),      # the exact floor of the 512 band
])
def test_a_supported_card_class_is_accepted(world, size, label):
    done = world.set({"disk4": card(size)}).run("ark_assert_sd_card")
    assert done.returncode == 0, done.stderr
    assert done.stdout == "disk4"
    assert f"{label} GB card" in done.stderr


@pytest.mark.parametrize("size", [
    CARD_64,              # a real 64 GB card: too small for the library
    160_000_000_000,      # between the 128 and 256 bands
    350_000_000_000,      # between the 256 and 512 bands
    640_000_000_000,      # a 640 GB card: a newer class nobody has added
    1_000_000_000_000,    # a 1 TB portable drive
    2_000_000_000_000,    # a 2 TB drive
])
def test_an_unsupported_size_is_refused_and_says_what_is_supported(world, size):
    done = world.set({"disk4": card(size)}).run("ark_assert_sd_card")
    assert_refused(done, "not a supported card class", "128 GB (100", "512 GB (440")


def test_the_old_270_gb_ceiling_is_gone_but_a_ceiling_still_exists(world):
    """512 GB must pass; 640 GB must not.  A band, not an open maximum."""
    assert world.set({"disk4": card(CARD_512)}).run("ark_assert_sd_card").returncode == 0
    assert world.set({"disk4": card(640 * GB)}).run("ark_assert_sd_card").returncode != 0


def test_the_capacity_class_is_a_pure_function(world):
    """No device, no diskutil: the policy itself, at its edges."""
    world.set({})
    cases = {
        "99999999999": "",           # just under the 128 floor
        "100000000000": "128",
        "150000000000": "128",
        "150000000001": "",          # just over
        "200000000000": "256",
        "300000000000": "256",
        "439999999999": "",
        "440000000000": "512",
        "580000000000": "512",
        "580000000001": "",
        "not-a-number": "",
    }
    for value, expected in cases.items():
        done = world.run("ark_capacity_class", [value])
        assert done.stdout == expected, f"{value} -> {done.stdout!r}, wanted {expected!r}"


# ---------------------------------------------------------------------------
#  2. every independent fact, on its own
# ---------------------------------------------------------------------------
def test_an_internal_disk_is_refused_even_when_listed_as_external(world):
    """Contradictory metadata is a refusal: the list and the plist disagree."""
    done = world.set({"disk4": card(CARD_512, Internal=True)}).run("ark_assert_sd_card")
    assert_refused(done, "Internal=yes", "Refusing")


def test_the_internal_ssd_is_refused_by_name(world):
    done = world.set({"disk0": card(CARD_512)}, external=["disk0"]).run("ark_assert_sd_card")
    assert_refused(done, "forbidden list", "NEVER flash disk0")


@pytest.mark.parametrize("name", ["disk0", "disk1"])
def test_every_forbidden_identifier_is_refused(world, name):
    done = world.set({name: card(CARD_512)}, external=[name]).run("ark_assert_sd_card")
    assert_refused(done, "forbidden list")


def test_a_synthesized_container_is_refused(world):
    done = world.set({"disk3": card(CARD_512, VirtualOrPhysical="Virtual")}).run("ark_assert_sd_card")
    assert_refused(done, "synthesized/virtual")


def test_an_uninterpretable_virtualorphysical_is_refused(world):
    done = world.set({"disk4": card(CARD_512, VirtualOrPhysical="Imaginary")}).run("ark_assert_sd_card")
    assert_refused(done, "cannot interpret")


def test_a_partition_is_not_a_card(world):
    """WholeDisk=no is what a partition looks like; only whole disks are cards."""
    done = world.set({"disk4": card(CARD_512, WholeDisk=False)}).run("ark_assert_sd_card")
    assert_refused(done, "partition", "WholeDisk=no")


def test_a_fixed_disk_that_is_neither_ejectable_nor_removable_is_refused(world):
    done = world.set({"disk4": card(CARD_512, Ejectable=False, RemovableMedia=False)}).run("ark_assert_sd_card")
    assert_refused(done, "RemovableMedia=yes")


@pytest.mark.parametrize("size", [CARD_256, CARD_512])
def test_a_usb_ssd_is_refused_however_big_it_is(world, size):
    """The defect this rule exists for.

    A portable USB SSD (the drive somebody keeps their backup on) reports
    Internal=no, WholeDisk=yes, VirtualOrPhysical=Physical and **Ejectable=yes**
    -- eject is how the Finder removes it.  Only RemovableMedia separates it
    from an SD card, and a 500 GB backup drive sits inside the 512 GB class.
    """
    done = world.set({"disk4": card(size, RemovableMedia=False, Ejectable=True)}).run("ark_assert_sd_card")
    assert_refused(done, "does not report RemovableMedia=yes", "an external USB drive is Ejectable too")


def test_removable_media_that_is_not_ejectable_is_contradictory(world):
    done = world.set({"disk4": card(CARD_512, RemovableMedia=True, Ejectable=False)}).run("ark_assert_sd_card")
    assert_refused(done, "Contradictory metadata")


def test_a_real_card_is_removable_media_and_ejectable(world):
    """Both yes is the one shape a card reader reports."""
    done = world.set({"disk4": card(CARD_512)}).run("ark_assert_sd_card")
    assert done.returncode == 0, done.stderr
    assert "Removable   : yes" in done.stderr
    assert "Ejectable   : yes" in done.stderr


# ---------------------------------------------------------------------------
#  3. uncovered selection, and metadata that cannot be trusted
# ---------------------------------------------------------------------------
def test_no_card_at_all_aborts_loudly(world):
    done = world.set({}).run("ark_assert_sd_card")
    assert_refused(done, "No external physical disk found", "NOT mounted")


def test_two_cards_are_ambiguous_and_neither_is_chosen(world):
    done = world.set({"disk4": card(CARD_256), "disk5": card(CARD_512)}).run("ark_assert_sd_card")
    assert_refused(done, "ambiguous", "/dev/disk4", "/dev/disk5")


@pytest.mark.parametrize("field", ["TotalSize", "Internal", "WholeDisk", "Ejectable", "RemovableMedia"])
def test_a_missing_required_field_fails_closed(world, field):
    """The old guard read Internal==yes, so a blank answer looked safe."""
    done = world.set({"disk4": card(CARD_512, **{field: None})}).run("ark_assert_sd_card")
    assert_refused(done, f"reports no {field}")


@pytest.mark.parametrize("value", ["about 512 GB", "", "-1", "5.12e11", "512 GB (nominal)"])
def test_a_malformed_size_fails_closed(world, value):
    done = world.set({"disk4": card(CARD_512, TotalSize=value)}).run("ark_assert_sd_card")
    assert_refused(done, "TotalSize")


@pytest.mark.parametrize("value", ["512", "1"])
def test_a_numeric_but_absurd_size_is_refused_as_an_unsupported_class(world, value):
    """Well formed, and still not a card: 512 bytes is not 512 GB.

    Refused by the capacity band rather than by the format check — which is
    the point of having a band instead of a `>= 100 GB` test.
    """
    done = world.set({"disk4": card(CARD_512, TotalSize=value)}).run("ark_assert_sd_card")
    assert_refused(done, "not a supported card class")


def test_an_unreadable_plist_fails_closed(world):
    done = world.set({"disk4": card(CARD_512)}, info_broken=True).run("ark_assert_sd_card")
    assert_refused(done, "could not read diskutil metadata")


def test_a_device_that_vanishes_from_the_list_fails_closed(world):
    """Listed external once, gone the second time — the guard looks twice."""
    world.set({"disk4": card(CARD_512)}, external=[])
    done = world.run("ark_assert_sd_card")
    assert_refused(done, "No external physical disk found")


# ---------------------------------------------------------------------------
#  4. identity: a name is not a device
# ---------------------------------------------------------------------------
def test_the_identity_records_name_size_and_uuid(world):
    done = world.set({"disk4": card(CARD_512)}).run("ark_card_identity", ["disk4"])
    assert done.returncode == 0, done.stderr
    assert done.stdout == f"dev=disk4 size={CARD_512} uuid=A1B2-C3D4"


def test_the_identity_says_so_when_no_uuid_is_reported(world):
    """Honest about what the reader gave: '-' rather than an invented value."""
    done = world.set({"disk4": card(CARD_512, DiskUUID=None)}).run("ark_card_identity", ["disk4"])
    assert done.stdout == f"dev=disk4 size={CARD_512} uuid=-"


def test_revalidation_passes_when_nothing_changed(world):
    world.set({"disk4": card(CARD_512)})
    token = world.run("ark_card_identity", ["disk4"]).stdout
    done = world.run("ark_revalidate_sd_card", [token])
    assert done.returncode == 0, done.stderr
    assert done.stdout == "disk4"


def test_revalidation_refuses_a_card_swapped_for_a_different_size(world):
    world.set({"disk4": card(CARD_256)})
    token = world.run("ark_card_identity", ["disk4"]).stdout
    world.set({"disk4": card(CARD_512)})
    done = world.run("ark_revalidate_sd_card", [token])
    assert_refused(done, "the device changed since it was chosen")


def test_revalidation_catches_a_same_size_swap_by_uuid(world):
    """The case capacity cannot see: two 512 GB cards, same device number."""
    world.set({"disk4": card(CARD_512, DiskUUID="AAAA-1111")})
    token = world.run("ark_card_identity", ["disk4"]).stdout
    world.set({"disk4": card(CARD_512, DiskUUID="BBBB-2222")})
    done = world.run("ark_revalidate_sd_card", [token])
    assert_refused(done, "the device changed since it was chosen")


def test_revalidation_refuses_a_device_that_is_no_longer_external(world):
    world.set({"disk4": card(CARD_512)})
    token = world.run("ark_card_identity", ["disk4"]).stdout
    world.set({"disk4": card(CARD_512)}, external=[])
    done = world.run("ark_revalidate_sd_card", [token])
    assert_refused(done, "no longer an external physical disk")


def test_revalidation_refuses_when_a_fact_became_contradictory(world):
    world.set({"disk4": card(CARD_512)})
    token = world.run("ark_card_identity", ["disk4"]).stdout
    world.set({"disk4": card(CARD_512, Internal=True)})
    done = world.run("ark_revalidate_sd_card", [token])
    assert_refused(done, "Internal=yes")


def test_revalidation_refuses_a_malformed_token(world):
    world.set({"disk4": card(CARD_512)})
    done = world.run("ark_revalidate_sd_card", ["size=512"])
    assert_refused(done, "malformed")


def test_revalidation_with_nothing_to_compare_against_refuses(world):
    world.set({"disk4": card(CARD_512)})
    done = world.run("ark_revalidate_sd_card", [""])
    assert_refused(done, "needs the identity captured")


# ---------------------------------------------------------------------------
#  5. two cards: the cloning pair
# ---------------------------------------------------------------------------
def test_the_pair_resolver_picks_the_larger_card_as_destination(world):
    done = world.set({"disk4": card(CARD_256), "disk5": card(CARD_512)}).run("ark_assert_sd_card_pair")
    assert done.returncode == 0, done.stderr
    assert done.stdout == "src=disk4 dst=disk5"


def test_the_pair_resolver_works_when_the_destination_is_listed_first(world):
    done = world.set({"disk4": card(CARD_512), "disk5": card(CARD_256)}).run("ark_assert_sd_card_pair")
    assert done.stdout == "src=disk5 dst=disk4"


def test_two_cards_of_the_same_class_are_refused(world):
    """Nothing in the metadata can tell them apart; the guard will not guess."""
    done = world.set({"disk4": card(CARD_512), "disk5": card(CARD_512, DiskUUID="ZZZZ-9999")}) \
        .run("ark_assert_sd_card_pair")
    assert_refused(done, "same capacity", "will not guess")


def test_the_pair_resolver_needs_exactly_two(world):
    done = world.set({"disk4": card(CARD_256)}).run("ark_assert_sd_card_pair")
    assert_refused(done, "needs exactly 2")

    done = world.set({"disk4": card(CARD_128), "disk5": card(CARD_256),
                      "disk6": card(CARD_512)}).run("ark_assert_sd_card_pair")
    assert_refused(done, "needs exactly 2", "/dev/disk6")


def test_the_pair_resolver_still_checks_each_card_in_full(world):
    done = world.set({"disk4": card(CARD_256, Internal=True), "disk5": card(CARD_512)}) \
        .run("ark_assert_sd_card_pair")
    assert_refused(done, "Internal=yes")


# ---------------------------------------------------------------------------
#  6. the callers write nothing without a second look
# ---------------------------------------------------------------------------
def test_the_flash_helper_revalidates_before_it_hands_the_card_to_the_imager():
    """The check has to live in the privileged helper, before the write.

    This is a structural test on purpose: running the real flash would write a
    real card.  What it pins is the ordering that makes the check meaningful —
    revalidate first, imager second, in the same generated script.
    """
    text = FLASH.read_text(encoding="utf-8")
    assert 'source "$GUARD"' in text, "the helper does not source the guard"
    revalidate = text.index("ark_revalidate_sd_card")
    imager = text.index('"$IMAGER" --cli')
    assert revalidate < imager, "the card is handed to the imager before it is revalidated"
    assert "SD_ID=$(ark_card_identity" in text, "no identity is captured to revalidate"
    assert 'echo 4 > "$DONE"' in text, "a refused flash must report its own exit code"


def test_fixing_a_shell_on_a_card_revalidates_before_it_writes():
    text = FIX_SHELL.read_text(encoding="utf-8")
    assert "ark_card_identity" in text
    assert text.index("ark_revalidate_sd_card") < text.index('cp -f "$PIFIX"')


def test_the_clone_tool_revalidates_both_cards_inside_the_helper():
    text = CLONE.read_text(encoding="utf-8")
    assert text.count("ark_revalidate_sd_card") >= 2
    assert text.index("ark_revalidate_sd_card") < text.index('dd if="/dev/r$SRC"')


def test_the_clone_tool_refuses_to_run_without_a_person(world):
    """Never unattended: no TTY, no write, whatever the cards look like."""
    world.set({"disk4": card(CARD_256), "disk5": card(CARD_512)})
    env = dict(os.environ)
    env["PATH"] = str(world.bin) + os.pathsep + env.get("PATH", "")
    env["FAKE_DISKUTIL_WORLD"] = str(world.path)
    done = subprocess.run(
        ["bash", str(CLONE)], capture_output=True, text=True,
        env=env, stdin=subprocess.DEVNULL, timeout=40,
    )
    assert done.returncode != 0
    assert "needs a person at the keyboard" in done.stderr


def test_the_clone_tool_dry_run_names_both_cards_and_writes_nothing(world):
    world.set({"disk4": card(CARD_256), "disk5": card(CARD_512)})
    env = dict(os.environ)
    env["PATH"] = str(world.bin) + os.pathsep + env.get("PATH", "")
    env["FAKE_DISKUTIL_WORLD"] = str(world.path)
    done = subprocess.run(
        ["bash", str(CLONE), "--dry-run"], capture_output=True, text=True,
        env=env, stdin=subprocess.DEVNULL, timeout=40,
    )
    assert done.returncode == 0, done.stderr
    assert "/dev/disk4" in done.stderr and "/dev/disk5" in done.stderr
    assert "would run: dd if=/dev/rdisk4 of=/dev/rdisk5" in done.stderr
    assert "dry run" in done.stderr


def test_the_clone_tool_will_not_copy_onto_itself(world):
    """A guard against a later edit that makes src and dst the same device."""
    text = CLONE.read_text(encoding="utf-8")
    assert '[[ "$SRC" != "$DST" ]]' in text


def test_the_pair_refuses_a_card_and_a_backup_drive(world):
    """The cloning variant of the USB-SSD hazard: two "external" disks, one a drive."""
    done = world.set({
        "disk4": card(CARD_256),
        "disk5": card(CARD_512, RemovableMedia=False),      # a 500 GB backup SSD
    }).run("ark_assert_sd_card_pair")
    assert_refused(done, "RemovableMedia=yes")


# ---------------------------------------------------------------------------
#  5b. a refusal is a non-zero exit, even through a gate
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("snippet", ["ark_assert_sd_card", "ark_guard_or_exit"])
def test_a_refusal_propagates_through_a_gate(world, snippet):
    """`ark_die`'s exit only ends a subshell.

    A function that captured the device and carried on returned 0 with an empty
    string, so `if ark_guard_or_exit; then ...` printed a pass on a refusal.
    """
    done = world.set({}).run(
        'if %s >/dev/null 2>&1; then echo PASS; else echo REFUSED; fi' % snippet)
    # A refusal may end the whole shell (ark_die's exit) or return non-zero
    # through the gate.  Either is fine; what must never happen is PASS.
    assert "PASS" not in done.stdout, "%s reported success on a refusal" % snippet
    assert done.returncode != 0, "%s exited 0 on a refusal" % snippet


def test_guard_or_exit_prints_the_device_on_success(world):
    done = world.set({"disk4": card(CARD_512)}).run("ark_guard_or_exit")
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "disk4"


def test_the_standalone_guard_exits_non_zero_when_it_refuses(world):
    """The command docs/CARD-MIGRATION.md tells a human to run is also a gate."""
    world.set({})
    env = dict(os.environ)
    env["PATH"] = str(world.bin) + os.pathsep + env.get("PATH", "")
    env["FAKE_DISKUTIL_WORLD"] = str(world.path)
    done = subprocess.run(["bash", str(GUARD)], capture_output=True, text=True,
                          env=env, stdin=subprocess.DEVNULL, timeout=40)
    assert done.returncode != 0, "the standalone guard exited 0 on a refusal"


# ---------------------------------------------------------------------------
#  5c. the generated helper: escaping is a safety property
# ---------------------------------------------------------------------------
#: Both writing scripts build their privileged helper with an *unquoted*
#: heredoc, so anything not escaped is expanded by the generating shell. A
#: `$(dd ...)` that slipped through would touch a device before the operator had
#: confirmed anything, and a `$VAR` belonging to the helper would silently
#: become empty (which is how the tail comparison once lost its argument).
#: These tests generate the helper text exactly as the script does.
GEN_PRELUDE = (
    "set -Eeuo pipefail\n"
    "SRC=disk4; DST=disk5; GUARD=/tmp/guard.sh; LOG=/tmp/clone.log; "
    "DONE=/tmp/clone.done\n"
    'SRC_ID="dev=disk4 size=1 uuid=-"; DST_ID="dev=disk5 size=2 uuid=-"\n'
    "SRC_BYTES=512107000000; IMG=/tmp/img; FIRSTRUN=/tmp/fr; IMAGER=/tmp/imager\n"
    'PLAIN=/dev/disk4; SD_DEV=disk4; SD_ID="dev=disk4 size=1 uuid=-"\n'
)


def _heredoc_body(path, marker, terminator="EOF"):
    text = path.read_text(encoding="utf-8")
    start = text.index(marker)
    body = text[start + len(marker):]
    return body[:body.index("\n%s\n" % terminator)]


@pytest.mark.parametrize("script,own_vars", [
    (CLONE, ("EDGE", "s_head", "s_tail")),
    (FLASH, ("rc",)),
])
def test_the_generated_helper_is_valid_bash_and_expands_nothing_early(tmp_path, script, own_vars):
    terminator = "EOM" if script == CLONE else "EOF"
    body = _heredoc_body(script, 'cat > "$HELPER" <<EOF', terminator)
    harness = tmp_path / "gen.sh"
    harness.write_text(
        GEN_PRELUDE + 'cat > "$1" <<EOF\n' + body + '\n%s\n' % terminator,
        encoding="utf-8")
    out = tmp_path / "helper.sh"
    done = subprocess.run(["bash", str(harness), str(out)],
                          capture_output=True, text=True, timeout=40)
    assert done.returncode == 0, done.stderr
    generated = out.read_text(encoding="utf-8")

    # Nothing belonging to the helper's own runtime ran while generating it.
    for forbidden in ("tail: option", "dd: ", "No such file or directory"):
        assert forbidden not in done.stderr, "the generator executed something: " + done.stderr
    # The helper's own variables survived as variables, not as empty strings.
    for name in own_vars:
        assert "$" + name in generated, "${%s} was expanded away" % name
    # ...and it is a script bash will actually run.
    check = subprocess.run(["bash", "-n", str(out)], capture_output=True, text=True, timeout=20)
    assert check.returncode == 0, "generated helper fails bash -n: " + check.stderr
