"""What a household writes down: who lives here, where the water stops, who to ring.

This is the page that has to work when somebody is frightened.  It is therefore one
flat list of short answers kept in one JSON row (``settings`` key ``household``), so it
travels in a backup with the rest of the records, and every entry is a *sentence a
person typed* — the box never invents one, never reorders them, and never decides an
answer is wrong.

Rules kept here on purpose:

* a value is trimmed and its control characters are removed, never silently shortened;
* a value longer than its limit is refused with a sentence naming the limit, because a
  half-written instruction is worse than none;
* an unreadable stored row is an empty page, not a crash — a page that 500s when the
  record is corrupt is a page that is missing on the worst day.
"""

from __future__ import annotations

import json
import re

import ark_db
from ark_health import now_iso

# (key, label, what to write, maximum characters, several lines?)
ENTRIES = (
    ("people", "Who lives here",
     "Names, so anyone can see who should be home.", 300, False),
    ("meeting", "Where we meet",
     "The place everybody goes to if the house cannot be used.", 300, False),
    ("contact", "Who to ring",
     "A neighbour, family, or the number people ring in an emergency.", 300, False),
    ("water", "Where the water stops",
     "The tap or valve that turns the water off.", 300, False),
    ("power", "Where the power stops",
     "The switch on the main board that turns everything off.", 300, False),
    ("gas", "Where the gas stops",
     "The valve on the bottle or the pipe.", 300, False),
    ("firstaid", "Where the first-aid kit is",
     "And anything that has to be reached in a hurry.", 300, False),
    ("animals", "Animals and medicine",
     "Pets, and any medicine somebody must have.", 500, False),
    ("notes", "Anything else",
     "One thing per line — anything the household must not forget.", 2000, True),
)

KEY = "household"

# The addresses worth a card on the wall, with the sentence that says when each one
# works.  ``ark.lan`` answers on the box's own Wi-Fi, ``ark.local`` on a home network,
# and the number is the one that always works while the box is broadcasting.
CARDS = (
    ("http://ark.lan", "On the box's own Wi-Fi",
     "Every name works while the box is broadcasting The-Ark-Offline."),
    ("http://ark.local", "On a home network",
     "Use this one when the box is plugged into the household router."),
    ("http://10.42.0.1", "If the name does not work",
     "The number is always the box, on its own Wi-Fi."),
)

_BAD = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_BLANKS = re.compile(r"\n{3,}")


class TooLong(ValueError):
    """An answer that does not fit.  Refused, never shortened."""


def blank() -> dict:
    return {key: "" for key, *_ in ENTRIES}


def _tidy(raw, lines: bool) -> str:
    text = str(raw if raw is not None else "").replace("\r\n", "\n").replace("\r", "\n")
    text = _BAD.sub("", text)
    if not lines:
        text = " ".join(text.split())
    else:
        text = _BLANKS.sub("\n\n", "\n".join(
            line.strip() for line in text.split("\n"))).strip("\n")
    return text.strip()


def clean(values: dict) -> dict:
    """Trim, drop control characters, refuse anything over its limit, drop unknown keys."""
    out = blank()
    given = values or {}
    for key, label, _hint, limit, lines in ENTRIES:
        text = _tidy(given.get(key, ""), lines)
        if len(text) > limit:
            raise TooLong(f"{label} is longer than {limit} characters")
        out[key] = text
    return out


def load() -> dict:
    with ark_db.read() as conn:
        raw = ark_db.get_setting(conn, KEY, "")
    return _decode(raw)[0]


def updated() -> str:
    """When the household last changed anything — ``""`` if they never have."""
    with ark_db.read() as conn:
        raw = ark_db.get_setting(conn, KEY, "")
    return _decode(raw)[1]


def _decode(raw: str) -> tuple:
    if not raw:
        return blank(), ""
    try:
        stored = json.loads(raw)
    except (TypeError, ValueError):
        return blank(), ""      # unreadable is empty, never a 500
    if not isinstance(stored, dict):
        return blank(), ""
    values = stored.get("values")
    if not isinstance(values, dict):
        values = {}
    try:
        kept = clean(values)
    except TooLong:             # a row written by an older, longer limit
        kept = {k: _tidy(values.get(k, ""), lines)[:limit]
                for k, _l, _h, limit, lines in ENTRIES}
    return kept, str(stored.get("updated") or "")


def save(values: dict, *, who: str = "") -> dict:
    """Store one household's answers and return what was stored."""
    kept = clean(values)
    stamp = now_iso()
    with ark_db.tx() as conn:
        ark_db.set_setting(conn, KEY, json.dumps(
            {"values": kept, "updated": stamp, "by": who or ""}, ensure_ascii=False))
    return kept


def filled(values: dict) -> list:
    """The answers somebody wrote, in the order of the page: [(label, text)]."""
    return [(label, (values or {}).get(key, ""))
            for key, label, _hint, _limit, _lines in ENTRIES
            if (values or {}).get(key, "").strip()]


def missing(values: dict) -> list:
    """The ones still blank, with the sentence that says what belongs there."""
    return [(label, hint) for key, label, hint, _limit, _lines in ENTRIES
            if not (values or {}).get(key, "").strip()]


def progress(values: dict) -> tuple:
    """(answered, total) — what the owner's page counts, never a percentage."""
    return len(filled(values)), len(ENTRIES)
