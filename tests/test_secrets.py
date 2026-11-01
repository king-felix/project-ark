"""A published tree must not carry a household's own details, and this checks it.

The leak that mattered here was never a password: it was a Wi-Fi network's **name**,
the box's address on somebody's home LAN and the workstation account in a path —
``/Users/<ACCOUNT>/Survival-Pi`` — all of which identify a house. This tree is
published, so all of it has to be a placeholder in the paperwork (the legend at the
top of ``docs/ADMIN.md``).

``scripts/ark-secrets-check.sh`` catches the four shapes that actually leaked. It is
**shape**-based on purpose: a list of the values would have to live in git, which is
the very thing it prevents. It also never prints what it found — a guard that quotes
a secret into a terminal log has leaked it again.

Three defects these tests hold were found by running it rather than reading it: a
named path that did not exist printed ``no such file`` and then exited **0** (an
``exit`` inside a function feeding a process substitution only leaves the subshell,
so a typo'd path read nothing and reported success); ``--staged`` announced it was
reading "the tracked tree"; and the script flagged **its own** header comment, which
names the shape it describes, the moment it became a tracked file.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CHECKER = REPO / "scripts" / "ark-secrets-check.sh"

# Distinctive enough that "the output must not contain it" is a real assertion.
ACCOUNT = "fixtureaccount"
SSID = "Rosebank-Road-5G"
PSK = "hunter2hunter2"
# A made-up address has to be a *private* one, or the checker would rightly let it
# through and the test below would prove nothing: the documentation ranges are on
# its allow-list. So this line carries the marker, which is the one way a line may
# excuse itself — in writing, where a reviewer reads it.
HOME_IP = "192.168.44.77"  # ark-secrets-ok: a made-up address in a made-up file

DIRTY = f"""\
WIFI_SSID={SSID}
psk={PSK}
home={HOME_IP}
path=/Users/{ACCOUNT}/Survival-Pi
"""

# Everything a private address may legitimately be: the Ark's own hotspot, the
# gateway most home routers answer on, loopback, "every address", and the three
# RFC 5737 documentation ranges the fixtures use.
ALLOWED = """\
hotspot=10.42.0.1
gateway=192.168.1.1
loopback=127.0.0.1
everywhere=0.0.0.0
doc_a=192.0.2.50
doc_b=198.51.100.7
doc_c=203.0.113.9
"""


def _run(*args: str, root: Path | None = None) -> subprocess.CompletedProcess:
    """Run the shipped checker. ``root`` points it at a throwaway copy of itself."""
    script = (root / "scripts" / "ark-secrets-check.sh") if root else CHECKER
    return subprocess.run(["bash", str(script), *args], capture_output=True,
                          text=True, timeout=120, cwd=str(root or REPO))


def _both(done: subprocess.CompletedProcess) -> str:
    return _plain(done.stdout) + _plain(done.stderr)


def _plain(text: str) -> str:
    """The report is coloured for a person; a test should read the words."""
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


# ---------------------------------------------------------------------------
#  The shipped tree
# ---------------------------------------------------------------------------
def test_the_shipped_tree_carries_no_household_detail():
    """The guard itself: this is the assertion that keeps the tree honest as it grows."""
    done = _run()
    assert done.returncode == 0, _both(done)
    assert "no household detail found" in done.stdout


def test_the_checker_can_read_its_own_documentation():
    """It names the shapes it looks for, so it used to flag its own header comment the
    moment it became a tracked file — an example in prose is not a leak, but a guard
    that fails on itself is a guard everybody switches off."""
    done = _run(str(CHECKER))
    assert done.returncode == 0, _both(done)
    assert "clean: 1 file(s) read" in done.stdout


# ---------------------------------------------------------------------------
#  Each shape, and what it must never print
# ---------------------------------------------------------------------------
def test_each_shape_is_caught_and_no_value_is_ever_printed(tmp_path):
    (tmp_path / "leak.conf").write_text(DIRTY + ALLOWED, encoding="utf-8")
    done = _run(str(tmp_path))

    assert done.returncode == 1
    output = _both(done)
    assert "a workstation account in a path" in output
    assert "a passphrase written as a literal" in output
    assert "a network name written as a literal" in output
    assert "a private address" in output
    # The point of the whole design: the report says *where*, never *what*.
    for value in (ACCOUNT, SSID, PSK, HOME_IP):
        assert value not in output, value


def test_the_arks_own_addresses_and_the_documentation_ranges_are_not_a_leak(tmp_path):
    (tmp_path / "addresses.conf").write_text(ALLOWED, encoding="utf-8")
    done = _run(str(tmp_path))
    assert done.returncode == 0, _both(done)


def test_a_quoted_or_indirect_name_is_not_a_literal(tmp_path):
    """`ssid="$ssid"`, `ssid=<NAME>`, a `printf` format and a case arm are all how a
    script *passes* a name around; only a name written out is a leak."""
    (tmp_path / "forms.sh").write_text(
        'ssid="$WIFI_SSID"\n'
        "ssid=<HOUSEHOLD-SSID>\n"
        "printf 'ssid=%s' \"$name\"\n"
        "  --ssid=*) shift ;;\n"
        'psk="$PSK"\n',
        encoding="utf-8",
    )
    done = _run(str(tmp_path))
    assert done.returncode == 0, _both(done)


def test_a_marked_line_is_excused_and_its_neighbour_is_not(tmp_path):
    """Some fixtures need a made-up network name — the owner's Network page writes one
    into a request file, which has the exact shape of the thing this catches. The
    honest answer is a marked line a reviewer reads, and the marker is read on the
    line's own text so it can never excuse the line below it."""
    (tmp_path / "fixture.conf").write_text(
        f"ssid={SSID}-a   # ark-secrets-ok: a made-up request this test writes\n"
        f"ssid={SSID}-b\n",
        encoding="utf-8",
    )
    done = _run(str(tmp_path))
    assert done.returncode == 1
    assert "1 place(s)" in _both(done)
    assert "fixture.conf:1 " not in _plain(done.stdout)
    assert "fixture.conf:2 " in _plain(done.stdout)


def test_a_binary_file_is_not_read(tmp_path):
    """The vendored player is megabytes that would match anything; `grep -I` answers
    "no match" for a file holding a NUL byte, and that is how it is skipped."""
    (tmp_path / "player.min.js").write_bytes(b"\x00\x01WIFI_SSID=" + SSID.encode() + b"\x00")
    done = _run(str(tmp_path))
    assert done.returncode == 0, _both(done)
    assert "clean: 1 file(s) read" in done.stdout


def test_a_path_that_is_not_there_is_a_usage_error_and_not_a_pass():
    """A typo must stop the run. It used to answer `no such file`, read nothing and
    exit 0 — the one answer a checker must never give."""
    done = _run("/no/such/file/here")
    assert done.returncode == 2
    assert "no such file" in _both(done)
    assert "clean" not in done.stdout


def test_the_help_writes_the_usage_and_changes_nothing():
    done = _run("--help")
    assert done.returncode == 0
    assert "ark-secrets-check.sh --staged" in done.stdout
    assert "Exit 0 clean, 1 something found, 2 usage" in done.stdout


# ---------------------------------------------------------------------------
#  What a commit would add
# ---------------------------------------------------------------------------
def _git() -> str:
    git = shutil.which("git")
    if git is None:  # pragma: no cover - git is needed to ask the question at all
        pytest.skip("git is needed to ask what a commit would add")
    return git


def _repo(tmp_path: Path) -> Path:
    """A throwaway tree with a copy of the shipped checker: the script decides what to
    read from its own location, so it must sit in a `scripts/` folder of its own."""
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    shutil.copy2(CHECKER, root / "scripts" / "ark-secrets-check.sh")
    subprocess.run([_git(), "init", "-q", str(root)], check=True, capture_output=True)
    return root


def _add(root: Path, *names: str) -> None:
    subprocess.run([_git(), "-C", str(root), "add", "--", *names],
                   check=True, capture_output=True)


def test_staged_reads_the_index_and_not_the_disk(tmp_path):
    """A half-finished edit is not what a commit carries, and `--staged` says which
    question it is asking — it used to announce "reading the tracked tree"."""
    root = _repo(tmp_path)
    (root / "notes.txt").write_text("nothing to see here\n", encoding="utf-8")
    _add(root, "notes.txt")

    clean = _run("--staged", root=root)
    assert clean.returncode == 0, _both(clean)
    assert "reading what a commit would add" in clean.stdout

    (root / "notes.txt").write_text(f"psk={PSK}\n", encoding="utf-8")
    unsaved = _run("--staged", root=root)
    assert unsaved.returncode == 0, _both(unsaved)

    _add(root, "notes.txt")
    staged = _run("--staged", root=root)
    assert staged.returncode == 1
    assert PSK not in _both(staged)

    # And the direction that matters most: the secret is in the INDEX while the
    # working copy has been tidied, so a check that read the disk would wave the
    # commit through with the secret inside it.
    (root / "notes.txt").write_text("tidied\n", encoding="utf-8")
    index_only = _run("--staged", root=root)
    assert index_only.returncode == 1
    assert "notes.txt:1" in _plain(index_only.stdout)


def test_staged_needs_a_repository(tmp_path):
    root = tmp_path / "plain"
    (root / "scripts").mkdir(parents=True)
    shutil.copy2(CHECKER, root / "scripts" / "ark-secrets-check.sh")
    done = _run("--staged", root=root)
    assert done.returncode == 2
    assert "needs a git repository" in _both(done)


# ---------------------------------------------------------------------------
#  The pre-commit hook
# ---------------------------------------------------------------------------
def test_install_hook_writes_a_hook_that_allows_clean_and_refuses_a_leak(tmp_path):
    """A guard nobody runs is a comment. The hook is what makes the check happen at the
    only moment it can still prevent something, and it must not block honest work."""
    root = _repo(tmp_path)

    installed = _run("--install-hook", root=root)
    assert installed.returncode == 0, _both(installed)
    hook = root / ".git" / "hooks" / "pre-commit"
    assert hook.is_file() and hook.stat().st_mode & 0o111

    (root / "notes.txt").write_text("an honest line\n", encoding="utf-8")
    _add(root, "notes.txt")
    allowed = subprocess.run(["sh", str(hook)], capture_output=True, text=True,
                             timeout=120, cwd=str(root))
    assert allowed.returncode == 0, _both(allowed)

    (root / "notes.txt").write_text(f"WIFI_SSID={SSID}\n", encoding="utf-8")
    _add(root, "notes.txt")
    refused = subprocess.run(["sh", str(hook)], capture_output=True, text=True,
                             timeout=120, cwd=str(root))
    assert refused.returncode == 1
    assert SSID not in _both(refused)

