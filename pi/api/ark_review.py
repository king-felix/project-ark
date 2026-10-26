"""The emergency pages, and when a person last read one.

The box carries pages for the day something goes wrong — breathing, water, food,
fire, power, toilets, cold — and a page nobody has read since it was written is a
page that may quietly be wrong.  This module does the only thing a small box can
honestly do about that:

* it **finds** the pages that belong to each emergency, and says which are missing;
* it **records** that a person opened one, on what day, and what they wanted to say
  about it;
* it **counts the days** since the last look, and calls a topic due after a year.

It never judges the words in a page, never refuses to serve one, and never invents a
date: a topic nobody has read says *"nobody has read this yet"*, which is the truth
and is more useful than a green tick.  Missing content is information too — the
readiness verdict in :mod:`ark_health` is unaffected, because a household with a
half-written manual is still a working box.

The record is one JSON row (``settings`` key ``emergency_review``), so it travels in
a backup with the rest of the records and survives a card being replaced.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import ark_db

# (key, plain name, what the page has to answer, the pages that carry it)
#
# The file names are the ones on the card (``/srv/ark/vault/…``).  A topic with two
# pages is covered when *either* is there; a missing page is listed, never assumed.
TOPICS = (
    ("breathing", "Breathing and bleeding",
     "What to do in the first ten minutes when somebody is badly hurt.",
     ("manuals/first-aid.html",)),
    ("water", "Water you can drink",
     "How to make water safe when the tap stops, and how much to keep.",
     ("manuals/water.html",)),
    ("food", "Food that is still good",
     "What to keep when the power has been off, and what to throw away.",
     ("manuals/food.html",)),
    ("fire", "Fire in the house",
     "What to do first, and how to put out a small fire.",
     ("manuals/fire.html",)),
    ("power", "Power out, power back",
     "Turning the house off safely, and what must never be plugged in.",
     ("manuals/power.html", "guides/electrical.html")),
    ("sanitation", "Toilets with no water",
     "What to do when nothing flushes.",
     ("manuals/sanitation.html",)),
    ("cold", "Cold, heat and shelter",
     "Keeping warm when the heating stops.",
     ("manuals/shelter.html",)),
    ("repairs", "Small repairs",
     "Leaks, doors and the jobs that can wait.",
     ("guides/plumbing.html", "guides/household-repairs.html")),
)

KEY = "emergency_review"
CHECK_EVERY_DAYS = 365          # a year is a readable interval, not a medical opinion
MAX_NOTE = 300

_BY_KEY = {key: (name, asks, pages) for key, name, asks, pages in TOPICS}


class UnknownTopic(ValueError):
    """A topic this box does not keep.  Refused, never invented."""


def looks_there(vault, name: str) -> bool:
    """Is that page really on the card?  Unreadable is 'not there', never a crash."""
    try:
        return (Path(vault) / name).is_file()
    except OSError:
        return False


def scan(vault) -> list:
    """Every emergency topic, with the pages that exist and the ones that do not."""
    out = []
    for key, name, asks, pages in TOPICS:
        here = [page for page in pages if looks_there(vault, page)]
        out.append({"key": key, "name": name, "asks": asks,
                    "pages": here, "missing": [p for p in pages if p not in here],
                    "covered": bool(here)})
    return out


def _decode(raw: str) -> dict:
    """The stored record, or an empty one.  A corrupt row is empty, never a 500."""
    if not raw:
        return {"checked": {}, "updated": ""}
    try:
        stored = json.loads(raw)
    except (TypeError, ValueError):
        return {"checked": {}, "updated": ""}
    if not isinstance(stored, dict):
        return {"checked": {}, "updated": ""}
    checked = stored.get("checked")
    if not isinstance(checked, dict):
        checked = {}
    kept = {}
    for key, entry in checked.items():
        if key not in _BY_KEY or not isinstance(entry, dict):
            continue
        on = _clean_date(entry.get("on", ""))
        if not on:
            continue
        kept[key] = {"on": on,
                     "by": _tidy(entry.get("by", ""))[:60],
                     "note": _tidy(entry.get("note", ""))[:MAX_NOTE]}
    return {"checked": kept, "updated": str(stored.get("updated") or "")}


def _tidy(raw) -> str:
    return " ".join(str(raw if raw is not None else "").split())


def _clean_date(raw) -> str:
    """``YYYY-MM-DD``, or ``""``.  Accepts a date, a datetime or a string."""
    if isinstance(raw, datetime):
        return raw.date().isoformat()
    if isinstance(raw, date):
        return raw.isoformat()
    text = str(raw or "").strip()[:10]
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return ""


def load() -> dict:
    with ark_db.read() as conn:
        return _decode(ark_db.get_setting(conn, KEY, ""))


def record(topic: str, *, when=None, who: str = "", note: str = "") -> dict:
    """Say that a person read one emergency page today, and store it.

    Refuses an unknown topic and a date that has not happened yet; everything else is
    kept as the person typed it (trimmed, never shortened).
    """
    if topic not in _BY_KEY:
        raise UnknownTopic(f"there is no emergency topic called {topic!r}")
    on = _clean_date(when) if when else date.today().isoformat()
    if not on:
        raise ValueError("that is not a date a person could have read a page on")
    if date.fromisoformat(on) > date.today():
        raise ValueError("that date has not happened yet")
    keep = {"on": on, "by": _tidy(who)[:60], "note": _tidy(note)[:MAX_NOTE]}
    stored = load()
    stored["checked"][topic] = keep
    stored["updated"] = datetime.now().astimezone().isoformat(timespec="seconds")
    with ark_db.tx() as conn:
        ark_db.set_setting(conn, KEY, json.dumps(stored, ensure_ascii=False))
    return keep


def forget(topic: str) -> None:
    """Take one topic back to 'nobody has read this yet'.  Nothing else changes."""
    if topic not in _BY_KEY:
        raise UnknownTopic(f"there is no emergency topic called {topic!r}")
    stored = load()
    if topic in stored["checked"]:
        del stored["checked"][topic]
        with ark_db.tx() as conn:
            ark_db.set_setting(conn, KEY, json.dumps(stored, ensure_ascii=False))


def days_since(on: str, *, today=None) -> "int | None":
    """How long ago that was, in whole days.  Unreadable is ``None``, never ``0``."""
    try:
        then = date.fromisoformat(_clean_date(on))
    except ValueError:
        return None
    return max(0, ((today or date.today()) - then).days)


def summary(vault, *, today=None) -> list:
    """Every topic as one row: pages, whether they are there, when it was last read."""
    stored = load()["checked"]
    rows = []
    for topic in scan(vault):
        entry = stored.get(topic["key"], {})
        age = days_since(entry.get("on", ""), today=today) if entry else None
        if not entry:
            words = "Nobody has read this yet."
        elif age == 0:
            words = "Somebody here read this today."
        else:
            words = f"Somebody here read this {age} day{'s' if age != 1 else ''} ago."
        rows.append(dict(topic, checked=entry.get("on", ""), by=entry.get("by", ""),
                         note=entry.get("note", ""), days=age,
                         due=(age is None or age >= CHECK_EVERY_DAYS), words=words))
    return rows


def status(vault, *, today=None) -> dict:
    """Counts for the owner's page and the verifier — never used for a verdict."""
    rows = summary(vault, today=today)
    return {"covered": sum(1 for r in rows if r["covered"]),
            "total": len(rows),
            "read": sum(1 for r in rows if r["checked"]),
            "due": sum(1 for r in rows if r["due"]),
            "missing_pages": sorted({p for r in rows for p in r["missing"]})}
