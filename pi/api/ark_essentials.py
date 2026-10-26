"""The essentials pack: the shelf's most important pages, zipped up to carry away.

A copy of our own manuals, guides and quick card that opens on any computer with no
network, no box and no internet: unzip it, double-click ``index.html``.  It exists
because the box is one card in one house — a pack on a USB stick in a bag survives
the card that made it.

What it is, in three rules:

* **deterministic** — members are sorted, timestamps are fixed, so two builds of the
  same shelf are the same bytes and a difference is a real difference;
* **proved** — every member's SHA-256 and size go into ``MANIFEST.txt`` inside the
  pack, and :func:`verify` recomputes them, so a pack can be shown whole before it is
  trusted or printed;
* **readable where it lands** — the manuals link to ``/vault/…`` on the box, which is
  meaningless on a stick, so every such link is made relative (:func:`_rewrite`) and
  the pack carries its own ``index.html``.

Nothing here writes into the box's live folders: a build writes one ``.partial`` file
and then renames it, so a pack is either the previous one or the new one.
"""

from __future__ import annotations

import hashlib
import os
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

PACK_NAME = "ark-essentials.zip"
MANIFEST = "MANIFEST.txt"
START = "START-HERE.txt"
INDEX = "index.html"
FORMAT = 1

# The vault holds apps and fetched downloads too; those are not paper for a bag.
SKIP_DIRS = {"apps", "downloads", "pdf", ".git"}
KEEP = {".html", ".css", ".txt"}
MAX_MEMBER = 8 * 1024 * 1024      # a manual is kilobytes; anything this big is not one

# 1980-01-01 is the earliest a zip can express, and a fixed stamp is what makes two
# builds of the same shelf byte-identical.
_STAMP = (1980, 1, 1, 0, 0, 0)

_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


_ATTR = re.compile(r'(?P<attr>href|src)="(?P<value>[^"]*)"')


def _prefix(arcname: str) -> str:
    """How many folders deep a member sits: ``manuals/water.html`` needs ``../``."""
    return "../" * arcname.count("/")


def _rewrite(text: str, arcname: str) -> tuple:
    """Make absolute on-box links work inside the pack.  Returns (text, changes).

    On the box a manual says ``href="/vault/manual.css"`` and ``href="/"``.  On a stick
    the pack's own root *is* the vault root, so ``/vault/`` is dropped and every link is
    lifted by as many folders as the member sits deep (``manuals/water.html`` → ``../``).
    """
    up = _prefix(arcname)
    changes = 0

    def one(match):
        nonlocal changes
        attr, value = match.group("attr"), match.group("value")
        if value.startswith("/vault/"):
            changes += 1
            return f'{attr}="{up}{value[len("/vault/"):]}"'
        if value == "/vault":
            changes += 1
            return f'{attr}="{up}{INDEX}"'
        if value == "/":
            changes += 1
            return f'{attr}="{up}{INDEX}"'
        return match.group(0)

    return _ATTR.sub(one, text), changes


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def collect(vault) -> list:
    """Every page worth carrying, as sorted [(name in the pack, path on the card)]."""
    root = Path(vault)
    if not root.is_dir():
        return []
    found = []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if not path.is_file() or path.suffix.lower() not in KEEP:
            continue
        found.append((rel.as_posix(), path))
    return found


def pack_path(out_dir) -> Path:
    return Path(out_dir) / PACK_NAME


def pack_dir() -> Path:
    """Where the box keeps the pack it serves — one file, beside its records.

    Read at call time, like every other path in this project, so a test can point
    ``ARK_ESSENTIALS_DIR`` (or ``ARK_ROOT``) anywhere without touching the live card.
    """
    chosen = os.environ.get("ARK_ESSENTIALS_DIR")
    if chosen:
        return Path(chosen)
    return Path(os.environ.get("ARK_ROOT", "/srv/ark")) / "essentials"


def _title(text: str, fallback: str) -> str:
    found = _TITLE.search(text)
    if not found:
        return fallback
    return " ".join(found.group(1).split())[:120] or fallback


def _index_html(pages: list, built: str) -> str:
    """The pack's own front page — plain HTML, no fonts, no script, no network."""
    items = "".join(
        f'<li><a href="{name}">{title}</a><span class="d">{name}</span></li>\n'
        for name, title in pages)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>The Ark — essentials, on a stick</title>
<style>
body{{background:#000;color:#f3f3f4;font:17px/1.55 system-ui,sans-serif;margin:0;padding:24px 16px 64px}}
main{{max-width:760px;margin:0 auto}}
a{{color:#8ee6ac}}
ul{{list-style:none;padding:0}}
li{{border-bottom:1px solid #26262a;padding:14px 0}}
.d{{display:block;color:#b6b6be;font-size:.85rem;margin-top:2px}}
.note{{color:#b6b6be;font-size:.95rem}}
</style>
</head>
<body>
<main>
<h1>The Ark — essentials</h1>
<p class="note">Made on {built}. This copy works with no internet and no box:
every page here is a file on the stick.</p>
<ul>
{items}</ul>
<p class="note">Print the quick card and the first-aid page while a printer works.
The rest reads fine on any phone or computer.</p>
</main>
</body>
</html>
"""


def _start_text(built: str, pages: list, missing: list) -> str:
    lines = [
        "THE ARK — WHAT IS IN THIS FOLDER",
        "",
        f"Made on {built}. Nothing here needs the internet, a password or the box.",
        "",
        "HOW TO USE IT",
        "",
        "1. Unzip this folder somewhere on a computer, a laptop or a phone.",
        "2. Open the file called index.html — that is the front page.",
        "3. Tap or click what you want to read. Everything opens in your browser,",
        "   straight off the stick.",
        "",
        "WHAT IS INSIDE",
        "",
    ]
    lines += [f"  {name}  —  {title}" for name, title in pages]
    if missing:
        lines += ["", "NOT IN THIS COPY"]
        lines += [f"  {name} — it was not there when this copy was made." for name in missing]
    lines += [
        "",
        "PRINT THESE FIRST",
        "",
        "  quick-card.html  — one sheet, the things worth having on the wall.",
        "  manuals/first-aid.html — read it once while nothing is wrong.",
        "",
        "A copy on a stick is worth keeping somewhere other than the house it is for.",
        "",
    ]
    return "\n".join(lines)


def _manifest_text(built: str, rows: list, missing: list) -> str:
    lines = [f"ark-essentials {FORMAT}", f"built: {built}",
             f"missing: {', '.join(missing) if missing else 'nothing'}", ""]
    for name, size, digest in rows:
        lines.append(f"{digest}  {size:>9}  {name}")
    return "\n".join(lines) + "\n"


def _parse_manifest(text: str) -> tuple:
    built, missing, rows = "", [], []
    for line in text.splitlines():
        if line.startswith("built: "):
            built = line[7:].strip()
        elif line.startswith("missing: "):
            tail = line[9:].strip()
            missing = [] if tail in ("", "nothing") else [
                part.strip() for part in tail.split(",") if part.strip()]
        elif len(line) > 70 and "  " in line:
            digest, size, name = line.split(None, 2)
            try:
                rows.append((name.strip(), int(size), digest))
            except ValueError:      # a manifest somebody typed into: ignored, never fatal
                continue
    return built, missing, rows


def verify(pack) -> tuple:
    """Recompute every fingerprint in a pack.  Returns (whole, [what is wrong])."""
    path = Path(pack)
    if not path.is_file():
        return False, [f"there is no pack at {path}"]
    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        return False, [f"it cannot be opened: {exc}"]
    problems = []
    with archive:
        broken = archive.testzip()
        if broken:
            problems.append(f"{broken} is damaged inside the pack")
        try:
            text = archive.read(MANIFEST).decode("utf-8", "replace")
        except KeyError:
            return False, problems + [f"it has no {MANIFEST}, so nothing can be checked"]
        _built, _missing, rows = _parse_manifest(text)
        if not rows:
            problems.append(f"{MANIFEST} lists nothing")
        names = set(archive.namelist()) - {MANIFEST}
        listed = {name for name, _size, _digest in rows}
        for extra in sorted(names - listed):
            problems.append(f"{extra} is in the pack but not in the list")
        for gone in sorted(listed - names):
            problems.append(f"{gone} is listed but not in the pack")
        for name, size, digest in rows:
            if name not in names:
                continue
            data = archive.read(name)
            if len(data) != size:
                problems.append(f"{name} is {len(data)} bytes, not {size}")
            if _sha256(data) != digest:
                problems.append(f"{name} does not match its fingerprint")
    return (not problems), problems


def names(path) -> list:
    """What is inside one pack, in the order a person reads a list."""
    try:
        with zipfile.ZipFile(path) as archive:
            return sorted(archive.namelist())
    except (zipfile.BadZipFile, OSError):
        return []


def status(out_dir) -> dict:
    """What is on the card right now: present, size, when, how many, what is missing."""
    path = pack_path(out_dir)
    if not path.is_file():
        return {"present": False, "path": str(path)}
    blob = path.read_bytes()
    built, missing, rows = "", [], []
    try:
        with zipfile.ZipFile(path) as archive:
            built, missing, rows = _parse_manifest(
                archive.read(MANIFEST).decode("utf-8", "replace"))
    except (zipfile.BadZipFile, KeyError, OSError):
        return {"present": True, "path": str(path), "bytes": len(blob),
                "sha256": _sha256(blob), "built": "", "members": 0,
                "missing": [], "readable": False}
    return {"present": True, "path": str(path), "bytes": len(blob),
            "sha256": _sha256(blob), "built": built, "members": len(rows) + 1,
            "missing": missing, "readable": True}

def build(out_dir, *, vault, sheet=None, when=None) -> dict:

    """Write one pack and return what it holds.  Raises when there is nothing to pack."""
    found = collect(vault)
    if not found:
        raise FileNotFoundError(f"no pages to pack under {vault}")
    stamp = when or datetime.now(timezone.utc)
    built = stamp.strftime("%Y-%m-%d %H:%M")
    payload, pages, skipped = [], [], []
    for name, path in found:
        data = path.read_bytes()
        if len(data) > MAX_MEMBER:
            skipped.append(name)
            continue
        if path.suffix.lower() in (".html", ".css"):
            text, _changes = _rewrite(data.decode("utf-8", "replace"), name)
            data = text.encode("utf-8")
            if path.suffix.lower() == ".html":
                pages.append((name, _title(text, name)))
        payload.append((name, data))
    missing = []
    if sheet and Path(sheet).is_file():
        payload.append(("RECOVERY-SHEET.txt", Path(sheet).read_bytes()))
    else:
        missing.append("RECOVERY-SHEET.txt")
    payload.append((INDEX, _index_html(sorted(pages), built).encode("utf-8")))
    payload.append((START, _start_text(built, sorted(pages), missing).encode("utf-8")))
    payload.sort()

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    part = out / (PACK_NAME + ".partial")
    rows = [(name, len(data), _sha256(data)) for name, data in payload]
    with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in payload:
            info = zipfile.ZipInfo(name, date_time=_STAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, data)
        info = zipfile.ZipInfo(MANIFEST, date_time=_STAMP)
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o644 << 16
        archive.writestr(info, _manifest_text(built, rows, missing).encode("utf-8"))
    with open(part, "rb+") as handle:
        os.fsync(handle.fileno())
    target = pack_path(out)
    os.replace(part, target)
    blob = target.read_bytes()
    return {"path": str(target), "bytes": len(blob), "sha256": _sha256(blob),
            "built": built, "members": len(payload) + 1,
            "names": [name for name, _data in payload] + [MANIFEST],
            "skipped": skipped, "missing": missing}

