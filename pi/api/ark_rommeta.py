"""What a game's own bytes say it is.

``ark_romdb`` answers one question — which catalogue row has these fingerprints?
This module turns that answer into the facts a shelf row keeps and the words a
page prints.  It is the only place that decides **which** fingerprint is stored
for a file, and the only place that phrases a match in words a reader who has
never heard of a checksum can follow.

Three rules, in the order they matter:

1. A name the owner typed is never overwritten.  Pre-filling only fills blanks.
2. A catalogue fact is stored beside the game, so the page keeps working when
   the catalogue file is absent (a card rebuilt by hand, a bundle restored).
3. Nobody has listed the file is a normal answer, not an error: the shelf then
   says it was named after its file, and that is the whole of the claim.
"""

import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

import ark_romdb as romdb

#: The columns a catalogue fact lands in.  Kept as one tuple so the migration,
#: the writer and the card cannot drift apart.
FACT_COLUMNS = (
    "rom_title", "rom_region", "rom_year", "rom_developer", "rom_publisher",
    "rom_genre", "rom_players", "rom_franchise", "rom_esrb", "rom_source",
    "rom_confidence",
)

#: The fingerprints the shelf remembers, strongest first.
FINGERPRINTS = ("sha1", "md5", "crc32")

#: The confidence values that mean "this was recognised from the file itself".
BY_CONTENTS = ("sha1", "md5", "crc32")


def playable(system: Any) -> bool:
    """Can this box actually run that console?"""
    return str(system or "") in romdb.PLAYABLE


def system_of(found: Optional[Dict[str, Any]], hint: str = "") -> str:
    """The console a match proves, else the one the file name hints at."""
    said = str((found or {}).get("system") or "")
    if playable(said):
        return said
    return str(hint or "") if playable(hint) else ""



def view_for(ident: Dict[str, Any], label: str = "") -> Dict[str, Any]:
    """The header view that matched, else the file exactly as it arrived.

    A Nintendo file's dump often carries a 16-byte header that the catalogue does
    not list, so the digest that matched belongs to the *stripped* view.  Storing
    that one is what lets the same cartridge be recognised again on a card where
    the file arrived with a header.
    """
    views = [v for v in (ident.get("candidates") or []) if isinstance(v, dict)]
    if not views:
        return {"label": "", "crc32": "", "md5": "", "sha1": ""}
    for view in views:
        if label and view.get("label") == label:
            return view
    return views[0]


def canonical(ident: Dict[str, Any], found: Optional[Dict[str, Any]] = None
              ) -> Dict[str, Any]:
    """Which fingerprints to keep for this file, and which view they describe."""
    found = found or {}
    view = view_for(ident, str(found.get("hash_label") or ""))
    return {
        "label": str(view.get("label") or ""),
        "sha1": str(view.get("sha1") or "").lower(),
        "md5": str(view.get("md5") or "").lower(),
        "crc32": str(view.get("crc32") or "").lower(),
        "matched_by": str(found.get("matched_by") or ""),
    }


def facts_from(found: Optional[Dict[str, Any]], confidence: str,
               fingerprint: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    """The catalogue row, as the columns a shelf row keeps."""
    found = found or {}
    fp = fingerprint or {}
    facts = {
        "rom_title": str(found.get("title") or ""),
        "rom_region": str(found.get("region") or ""),
        "rom_year": str(found.get("year") or ""),
        "rom_developer": str(found.get("developer") or ""),
        "rom_publisher": str(found.get("publisher") or ""),
        "rom_genre": str(found.get("genre") or ""),
        "rom_players": str(found.get("players") or ""),
        "rom_franchise": str(found.get("franchise") or ""),
        "rom_esrb": str(found.get("esrb") or ""),
        "rom_source": str(found.get("source") or "")[:80],
        "rom_confidence": str(confidence or ""),
        # The fingerprint of the view the catalogue described, never of the
        # wrapper: a second copy of the same cartridge must find this row.
        "sha1": str(fp.get("sha1") or ""),
        "md5": str(fp.get("md5") or ""),
        "crc32": str(fp.get("crc32") or ""),
    }
    if not facts["rom_title"] and found.get("name"):
        facts["rom_title"] = romdb.clean_title(str(found["name"]))
    if not facts["rom_region"] and found.get("name"):
        facts["rom_region"] = romdb.region_of(str(found["name"]))
    return facts


def _where(found: Dict[str, Any]) -> str:
    """'Super Nintendo, 1992, Nintendo' — the short version of a match."""
    bits: List[str] = []
    label = romdb.system_label(str(found.get("system") or ""))
    if label:
        bits.append(label)
    if found.get("year"):
        bits.append(str(found["year"]))
    if found.get("publisher") or found.get("developer"):
        bits.append(str(found.get("publisher") or found.get("developer")))
    return ", ".join(bits)


def sentence(known: bool, title: str, where: str, *, can_play: bool = True,
             problem: str = "") -> str:
    """One line about what was recognised, in words that need no glossary.

    No algorithm names, no catalogue names and no word a reader would have to look
    up: the owner reads this on a phone, and a guest never sees it at all.
    """
    if problem:
        return problem[0].upper() + problem[1:] + "."
    if not known:
        return "Nobody has listed this file; the name on it is all this box has."
    if not can_play:
        return 'These bytes are "%s" (%s) — and this box cannot play that kind of game.' % (
            title, where or "a console it does not run")
    return 'This looks like "%s"%s.' % (title, " (%s)" % where if where else "")


def prefill(path: Optional[Path] = None, *, filename: str = "", system: str = "",
            conn: Optional[sqlite3.Connection] = None,
            ident: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Read a game file and say what it should be called, without writing anything.

    ``ident`` may be an :func:`ark_romdb.identify_path` answer already in hand
    (a caller that hashed the file for its own reasons), in which case the file is
    not read again.  ``conn`` is the catalogue; ``None`` simply means nothing is
    known and the file name is used, which is the answer on a card with no
    catalogue at all.
    """
    if ident is None:
        if path is None:
            ident = {"filename": filename, "candidates": [], "system_hint": "",
                     "problem": "there is no file to read"}
        else:
            ident = romdb.identify_path(Path(path), filename or "")
    else:
        ident = dict(ident)
    if filename and not ident.get("filename"):
        ident["filename"] = filename

    found: Dict[str, Any] = {}
    if ident.get("candidates"):
        found = romdb.match(ident, conn) or {}
    confidence = str(found.get("matched_by") or "") if found else ""
    fingerprint = canonical(ident, found)
    facts = facts_from(found, confidence, fingerprint)

    hint = str(ident.get("system_hint") or "")
    found_system = str(found.get("system") or "")
    chosen = system_of(found, hint) or system_of(None, system)
    shown = ident.get("filename") or filename or ""
    if found:
        proposed = facts["rom_title"] or romdb.title_from_filename(shown)
        where = _where(found)
    else:
        proposed = romdb.title_from_filename(shown)
        where = ""
    can_play = bool(chosen) or not found_system
    return {
        "known": bool(found),
        "confidence": confidence,
        "playable": playable(chosen),
        "system": chosen,
        "system_label": romdb.system_label(chosen),
        "found_system": found_system,
        "found_system_label": romdb.system_label(found_system or hint),
        "title": proposed,
        "where": where,
        "found": found,
        "ident": ident,
        "view": fingerprint.get("label", ""),
        "fingerprints": {key: fingerprint.get(key, "") for key in FINGERPRINTS},
        "facts": facts,
        "problem": str(ident.get("problem") or ""),
        "sentence": sentence(bool(found), proposed, where,
                             can_play=can_play or playable(found_system),
                             problem=str(ident.get("problem") or "")),
    }


def _line(facts: Dict[str, str]) -> str:
    """A description a reader understands when the owner typed none."""
    genre = str(facts.get("rom_genre") or "").strip()
    year = str(facts.get("rom_year") or "").strip()
    if genre and year:
        return "%s game from %s" % (genre, year)
    if genre:
        return "%s game" % genre
    if year:
        return "From %s" % year
    return ""


def apply(candidate: Dict[str, Any],
          pre: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Fill the blanks in a candidate from what the file itself says.

    Never overwrites a word the owner typed — not a title, not a console, not a
    description.  A fingerprint is not a word, so the fingerprints are always the
    ones this box measured, and a matching revision is stored with them.
    """
    out = dict(candidate or {})
    if not pre:
        return out
    facts = pre.get("facts") or {}
    for key, value in facts.items():
        text = str(value or "").strip()
        if text and not str(out.get(key) or "").strip():
            out[key] = text
    if not str(out.get("title") or "").strip() and pre.get("title"):
        out["title"] = str(pre["title"]).strip()
    if not str(out.get("system") or "").strip() and pre.get("system"):
        out["system"] = str(pre["system"]).strip()
    if not str(out.get("release") or "").strip() and facts.get("rom_year"):
        out["release"] = str(facts["rom_year"])
    if not str(out.get("author") or "").strip():
        who = facts.get("rom_developer") or facts.get("rom_publisher")
        if who:
            out["author"] = str(who)
    if not str(out.get("description") or "").strip():
        out["description"] = _line(facts)
    if not str(out.get("provenance") or "").strip() and pre.get("known"):
        out["provenance"] = "recognised-from-contents"
    return out


# ---------------------------------------------------------------------------
#  what a page prints
# ---------------------------------------------------------------------------

def recognised(game: Dict[str, Any]) -> bool:
    """Was this game named from its contents, rather than from its file name?"""
    return str((game or {}).get("rom_confidence") or "") in BY_CONTENTS


def confidence_words(value: Any) -> str:
    """The owner's words for how sure the box is, never an algorithm's name."""
    text = str(value or "").strip().lower()
    if text in BY_CONTENTS:
        return "the file itself"
    if text == "name":
        return "its file name"
    return "nothing yet"


def game_facts(game: Dict[str, Any], limit: int = 4) -> List[str]:
    """Short facts for a card: the year, who made it, and what kind of game it is."""
    game = game or {}
    out: List[str] = []

    def add(value: Any) -> None:
        text = str(value or "").strip()
        if text and text not in out:
            out.append(text)

    add(game.get("rom_year"))
    add(game.get("rom_publisher") or game.get("rom_developer"))
    add(game.get("rom_genre"))
    players = str(game.get("rom_players") or "").strip()
    if players.isdigit() and int(players) > 1:
        add("%s players" % players)
    add(game.get("rom_region"))
    add(game.get("rom_esrb"))
    return out[:limit] if limit > 0 else out


def also_listed_as(game: Dict[str, Any]) -> str:
    """The catalogue's own spelling, when it differs from the title on the card."""
    game = game or {}
    listed = str(game.get("rom_title") or "").strip()
    title = str(game.get("title") or "").strip()
    if not listed:
        return ""
    if listed.casefold() == title.casefold():
        return ""
    return 'The box\'s own list calls this "%s".' % listed


def summary(pre: Optional[Dict[str, Any]]) -> str:
    """The pre-fill answer as one line, for a console or an owner's page."""
    pre = pre or {}
    line = str(pre.get("sentence") or "").strip()
    if not pre.get("known"):
        return line or "Nothing on this box has listed that file."
    return "%s Recognised by %s." % (line, confidence_words(pre.get("confidence")))
