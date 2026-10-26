"""Stage 7 — the pack a household carries away: zipped, fingerprinted, readable anywhere.

The pack exists for the day the box does not boot, so it has three jobs and this file
holds one set of tests for each:

* **it lands somewhere that works** — every ``/vault/…`` link a manual was written with
  is made relative, and the pack carries its own front page, so a stick opens with no
  box, no network and no password;
* **it can be shown whole** — ``MANIFEST.txt`` carries a size and a SHA-256 for every
  member, and ``verify()`` recomputes them, so a damaged pack is caught before anybody
  prints it and trusts it;
* **the same shelf gives the same bytes** — members are sorted and timestamps are
  fixed, so two builds differ only when the pages differ.

Nothing here needs the Pi, the internet or a zip tool: a pack is a plain file.
"""

from __future__ import annotations

import datetime
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
API = REPO / "pi" / "api"
CLI = REPO / "pi" / "bin" / "ark-essentials"
SHEET = REPO / "pi" / "RECOVERY-SHEET.txt"
WHEN = datetime.datetime(2026, 10, 25, 9, 30, tzinfo=datetime.timezone.utc)

if str(API) not in sys.path:
    sys.path.insert(0, str(API))

import ark_essentials as pack          # noqa: E402


# ---------------------------------------------------------------------------
#  a little vault on a card
# ---------------------------------------------------------------------------
def make_vault(root: Path) -> Path:
    """A shelf the shape the box has: pages, a stylesheet, and folders not for a bag."""
    (root / "manuals").mkdir(parents=True, exist_ok=True)
    (root / "guides").mkdir(parents=True, exist_ok=True)
    for name in ("apps", "downloads", "pdf", ".git"):
        (root / name).mkdir(parents=True, exist_ok=True)
        (root / name / "big.bin").write_bytes(b"\x00" * 32)
    (root / "manual.css").write_text("body{background:#000}", encoding="utf-8")
    (root / "quick-card.html").write_text(
        '<!doctype html><html><head><title>Quick card</title>'
        '<link rel="stylesheet" href="/vault/manual.css"></head>'
        '<body><a href="/">Home</a><p>Water off: under the sink</p></body></html>',
        encoding="utf-8")
    (root / "manuals" / "water.html").write_text(
        '<!doctype html><html><head><title>Water</title>'
        '<link rel="stylesheet" href="/vault/manual.css"></head><body>'
        '<a href="/vault/manuals/first-aid.html">First aid</a></body></html>',
        encoding="utf-8")
    (root / "manuals" / "first-aid.html").write_text(
        '<!doctype html><html><head><title>First aid</title></head><body>'
        '<a href="/">Back to The Ark</a></body></html>', encoding="utf-8")
    (root / "manuals" / "notes.txt").write_text("plain words", encoding="utf-8")
    (root / "manuals" / "photo.jpg").write_bytes(b"\xff\xd8\xff")   # not paper
    return root


@pytest.fixture()
def vault(tmp_path) -> Path:
    return make_vault(tmp_path / "vault")


def build(tmp_path, vault, **kwargs) -> dict:
    return pack.build(tmp_path / "out", vault=vault, when=WHEN, **kwargs)


# ---------------------------------------------------------------------------
#  what goes in
# ---------------------------------------------------------------------------
def test_only_pages_and_a_stylesheet_go_in_the_pack(vault):
    names = [name for name, _path in pack.collect(vault)]
    assert names == ["manual.css", "manuals/first-aid.html", "manuals/notes.txt",
                     "manuals/water.html", "quick-card.html"]
    for skipped in ("apps/big.bin", "downloads/big.bin", "pdf/big.bin", ".git/big.bin"):
        assert skipped not in names


def test_a_vault_that_is_not_there_is_empty_not_an_error(tmp_path):
    assert pack.collect(tmp_path / "nothing-here") == []


def test_there_is_nothing_to_pack_and_it_says_so(tmp_path):
    with pytest.raises(FileNotFoundError) as refused:
        pack.build(tmp_path / "out", vault=tmp_path / "empty")
    assert "no pages" in str(refused.value)
    assert not pack.pack_path(tmp_path / "out").exists()


def test_a_page_too_big_to_be_a_manual_is_skipped_and_named(tmp_path, vault):
    (vault / "manuals" / "huge.txt").write_text("x" * (pack.MAX_MEMBER + 1), encoding="utf-8")
    made = build(tmp_path, vault)
    assert "manuals/huge.txt" in made["skipped"]


def test_the_shelfs_own_front_page_does_not_ride_along_beside_the_packs(tmp_path, vault):
    """Phase 4 writes an ``index.html`` at the top of the vault; the pack writes its own.

    Both at once meant one zip with two members called ``index.html``: zipfile only
    warned, every unzipper kept whichever came last, and a pack could open on the
    shelf's generated listing instead of the pack's front page.  Found on the Pi on
    26 Oct 2026, on the first real pack this box ever built.
    """
    (vault / "index.html").write_text(
        '<!doctype html><html><head><title>Manuals and guides</title></head>'
        '<body><a href="/vault/manuals/water.html">Water</a></body></html>',
        encoding="utf-8")
    assert "index.html" not in [name for name, _path in pack.collect(vault)]
    # a page called index.html inside a folder is content, and still travels
    (vault / "guides" / "index.html").write_text(
        '<!doctype html><html><head><title>Guides</title></head><body>x</body></html>',
        encoding="utf-8")
    made = build(tmp_path, vault)
    assert made["names"].count("index.html") == 1
    with zipfile.ZipFile(pack.pack_path(tmp_path / "out")) as archive:
        assert len(set(archive.namelist())) == len(archive.namelist())
        front = archive.read("index.html").decode("utf-8")
    assert "The Ark — essentials" in front            # the pack's own page, not the shelf's
    assert "Manuals and guides" not in front


def test_a_pack_that_would_hold_two_files_of_one_name_is_refused(tmp_path, vault, monkeypatch):
    """A duplicate name is a bug, not a warning: nothing is written and the words say why."""
    duplicate = [
        ("manuals/water.html", vault / "manuals" / "water.html"),
        ("manuals/water.html", vault / "manuals" / "first-aid.html"),
    ]
    monkeypatch.setattr(pack, "collect", lambda _vault: duplicate)
    out = tmp_path / "out"
    with pytest.raises(ValueError) as refused:
        pack.build(out, vault=vault, when=WHEN)
    assert "two files called manuals/water.html" in str(refused.value)
    assert not pack.pack_path(out).exists()
    assert not (out / (pack.PACK_NAME + ".partial")).exists()


# ---------------------------------------------------------------------------
#  landing somewhere that works
# ---------------------------------------------------------------------------
def test_a_link_on_the_box_is_made_relative_at_every_depth():
    text = '<link href="/vault/manual.css"><a href="/">home</a>'
    assert pack._rewrite(text, "quick-card.html") == (
        '<link href="manual.css"><a href="index.html">home</a>', 2)
    assert pack._rewrite(text, "manuals/water.html") == (
        '<link href="../manual.css"><a href="../index.html">home</a>', 2)
    assert pack._rewrite(text, "a/b/c.html") == (
        '<link href="../../manual.css"><a href="../../index.html">home</a>', 2)


def test_a_page_link_to_another_page_keeps_its_folder(tmp_path, vault):
    made = build(tmp_path, vault)
    with zipfile.ZipFile(made["path"]) as archive:
        assert 'href="../manuals/first-aid.html"' in archive.read("manuals/water.html").decode()
        for name in made["names"]:
            body = archive.read(name).decode("utf-8", "replace")
            assert '="/vault' not in body, name
            assert '="/"' not in body, name


def test_the_pack_carries_its_own_front_page_and_a_page_that_says_how_to_use_it(tmp_path, vault):
    made = build(tmp_path, vault)
    with zipfile.ZipFile(made["path"]) as archive:
        index = archive.read(pack.INDEX).decode()
        start = archive.read(pack.START).decode()
    assert pack.INDEX in made["names"] and pack.START in made["names"]
    assert 'href="manuals/water.html"' in index
    assert "Quick card" in index and "First aid" in index      # titles, not file names
    assert "no internet" in index and "a password or the box" in start
    assert "Open the file called index.html" in start
    assert "First aid" in start and "manuals/water.html" in start


def test_the_recovery_sheet_rides_along_and_its_absence_is_admitted(tmp_path, vault):
    with_sheet = build(tmp_path, vault, sheet=SHEET)
    assert "RECOVERY-SHEET.txt" in with_sheet["names"]
    assert with_sheet["missing"] == []

    without = pack.build(tmp_path / "out2", vault=vault,
                         sheet=tmp_path / "not-there.txt", when=WHEN)
    assert without["missing"] == ["RECOVERY-SHEET.txt"]
    with zipfile.ZipFile(without["path"]) as archive:
        start = archive.read(pack.START).decode()
        manifest = archive.read(pack.MANIFEST).decode()
    assert "RECOVERY-SHEET.txt — it was not there when this copy was made." in start
    assert "missing: RECOVERY-SHEET.txt" in manifest


def test_nothing_in_the_pack_needs_the_network(tmp_path, vault):
    made = build(tmp_path, vault)
    with zipfile.ZipFile(made["path"]) as archive:
        for name in made["names"]:
            body = archive.read(name).decode("utf-8", "replace")
            for tag in ('href="http', 'src="http', "url(http"):
                assert tag not in body, (name, tag)

    assert "manuals/huge.txt" not in made["names"]



# ---------------------------------------------------------------------------
#  shown whole, or not trusted
# ---------------------------------------------------------------------------
def test_a_fresh_pack_is_whole_and_says_what_is_in_it(tmp_path, vault):
    made = build(tmp_path, vault)
    whole, problems = pack.verify(made["path"])
    assert whole, problems
    assert made["members"] == len(made["names"])
    assert pack.names(made["path"]) == sorted(made["names"])


def test_a_member_that_changed_is_caught_by_its_fingerprint(tmp_path, vault):
    made = build(tmp_path, vault)
    damaged = tmp_path / "damaged.zip"
    with zipfile.ZipFile(made["path"]) as source, \
            zipfile.ZipFile(damaged, "w") as target:
        for item in source.infolist():
            body = source.read(item.filename)
            if item.filename == "manuals/water.html":
                body = body.replace(b"Water", b"Wafer")     # one word, same length
            target.writestr(item, body)
    whole, problems = pack.verify(damaged)
    assert not whole
    assert any("manuals/water.html" in problem for problem in problems)
    assert any("fingerprint" in problem for problem in problems)


def test_a_pack_that_is_not_a_pack_is_refused_and_never_crashes(tmp_path, vault):
    made = build(tmp_path, vault)
    whole, problems = pack.verify(tmp_path / "no-such-file.zip")
    assert (whole, problems[0].startswith("there is no pack")) == (False, True)

    junk = tmp_path / "junk.zip"
    junk.write_bytes(b"this is not a zip file at all")
    whole, problems = pack.verify(junk)
    assert not whole and problems

    listed_but_gone = tmp_path / "gone.zip"
    with zipfile.ZipFile(made["path"]) as source, zipfile.ZipFile(listed_but_gone, "w") as target:
        for item in source.infolist():
            if item.filename != "manuals/notes.txt":
                target.writestr(item, source.read(item.filename))
    whole, problems = pack.verify(listed_but_gone)
    assert not whole
    assert any("manuals/notes.txt is listed but not in the pack" in p for p in problems)


def test_a_manifest_with_no_rows_is_not_a_pack_you_can_trust(tmp_path, vault):
    made = build(tmp_path, vault)
    empty_manifest = tmp_path / "empty-manifest.zip"
    with zipfile.ZipFile(made["path"]) as source, \
            zipfile.ZipFile(empty_manifest, "w") as target:
        for item in source.infolist():
            body = source.read(item.filename)
            if item.filename == pack.MANIFEST:
                body = b"ark-essentials 1\nbuilt: 2026-10-25 09:30\nmissing: nothing\n"
            target.writestr(item, body)
    whole, problems = pack.verify(empty_manifest)
    assert not whole
    assert any("lists nothing" in problem for problem in problems)


# ---------------------------------------------------------------------------
#  the same shelf, the same bytes
# ---------------------------------------------------------------------------
def test_two_builds_of_the_same_shelf_are_the_same_bytes(tmp_path, vault):
    first = build(tmp_path, vault)
    second = pack.build(tmp_path / "again", vault=vault, when=WHEN)
    assert first["sha256"] == second["sha256"]
    assert first["bytes"] == second["bytes"]


def test_a_changed_page_makes_a_different_pack(tmp_path, vault):
    first = build(tmp_path, vault)
    (vault / "manuals" / "notes.txt").write_text("different words", encoding="utf-8")
    second = pack.build(tmp_path / "again2", vault=vault, when=WHEN)
    assert first["sha256"] != second["sha256"]


def test_the_stamp_inside_the_pack_is_fixed_and_is_the_one_it_was_given(tmp_path, vault):
    made = build(tmp_path, vault)
    assert made["built"] == "2026-10-25 09:30"
    with zipfile.ZipFile(made["path"]) as archive:
        assert archive.getinfo("manuals/water.html").date_time == (1980, 1, 1, 0, 0, 0)
        assert "built: 2026-10-25 09:30" in archive.read(pack.MANIFEST).decode()


# ---------------------------------------------------------------------------
#  what the owner's page and the console ask about the pack
# ---------------------------------------------------------------------------
def test_before_a_build_there_is_no_pack_and_after_one_it_says_what_it_holds(tmp_path, vault):
    assert pack.status(tmp_path / "out")["present"] is False
    made = build(tmp_path, vault, sheet=SHEET)
    state = pack.status(tmp_path / "out")
    assert state["present"] is True and state["readable"] is True
    assert state["path"] == made["path"]
    assert state["bytes"] == made["bytes"] and state["sha256"] == made["sha256"]
    assert state["built"] == "2026-10-25 09:30"
    assert state["members"] == len(made["names"])
    assert state["missing"] == []


def test_a_pack_the_box_cannot_read_is_reported_not_crashed(tmp_path, vault):
    build(tmp_path, vault)
    pack.pack_path(tmp_path / "out").write_bytes(b"not a zip")
    state = pack.status(tmp_path / "out")
    assert state["present"] is True and state["readable"] is False
    assert state["members"] == 0
    assert pack.names(pack.pack_path(tmp_path / "out")) == []


# ---------------------------------------------------------------------------
#  the console tool
# ---------------------------------------------------------------------------
def _run(*args):
    return subprocess.run([sys.executable, str(CLI), *args],
                          capture_output=True, text=True, timeout=120)


def test_the_tool_builds_verifies_and_lists_a_pack(tmp_path, vault):
    out = tmp_path / "pack"
    made = _run("--build", "--from", str(vault), "--sheet", str(SHEET), "--out", str(out))
    assert made.returncode == 0, made.stderr + made.stdout
    assert "Wrote" in made.stdout and "files, made" in made.stdout

    verified = _run("--verify", "--out", str(out))
    assert verified.returncode == 0, verified.stdout
    assert "whole" in verified.stdout

    listed = _run("--list", "--out", str(out))
    assert listed.returncode == 0
    assert "manuals/first-aid.html" in listed.stdout
    assert pack.MANIFEST in listed.stdout

    state = _run("--status", "--out", str(out))
    assert state.returncode == 0
    assert "made 2026" in state.stdout or "made " in state.stdout
    assert "fingerprint" in state.stdout


def test_the_tool_refuses_to_pretend_when_it_cannot_build(tmp_path):
    out = tmp_path / "pack"
    made = _run("--build", "--from", str(tmp_path / "no-vault"), "--out", str(out))
    assert made.returncode == 1
    assert made.stdout.startswith("Not done:")
    assert not pack.pack_path(out).exists()


def test_the_tool_says_so_when_there_is_no_pack_yet(tmp_path):
    out = tmp_path / "pack"
    for flag in ("--status", "--list", "--verify"):
        answer = _run(flag, "--out", str(out))
        assert answer.returncode == 1, flag
    assert "No pack yet" in _run("--status", "--out", str(out)).stdout
    assert "there is no pack" in _run("--verify", "--out", str(out)).stdout


def test_the_tool_refuses_a_damaged_pack_with_a_sentence(tmp_path, vault):
    out = tmp_path / "pack"
    made = pack.build(out, vault=vault, sheet=SHEET, when=WHEN)
    damaged = Path(made["path"])
    with zipfile.ZipFile(damaged) as source:
        members = {item.filename: source.read(item.filename) for item in source.infolist()}
    members["manuals/water.html"] = b"a page somebody edited by hand"
    with zipfile.ZipFile(damaged, "w") as target:
        for name, body in members.items():
            target.writestr(name, body)
    answer = _run("--verify", "--out", str(out))
    assert answer.returncode == 1
    assert "manuals/water.html" in answer.stdout
    assert "Not whole" in answer.stdout

