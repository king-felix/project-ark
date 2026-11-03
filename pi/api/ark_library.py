"""The shelves the portal's Library page puts the collections on.

The library already answers two questions well: *what is on the card* (the
index beside the bundles) and *what does one search find* (the fan-out in
``main``).  Neither of them helps somebody who has never heard the word
"collection" and does not know that a car-repair archive is called
"Motor Vehicle Mechanics".  This module answers the third question — *where do
I start looking* — by filing every installed collection under one plain-English
shelf.

The filing itself is **data, not prose**: the catalogue (``catalog.tsv``) carries
a ``topic`` column beside each bundle, so adding a collection, renaming a shelf
or moving one book is a one-line change in one file.  A bundle that was copied
onto the card by hand has no catalogue row; it is shown under *Everything else*
rather than being hidden, so the page can never disagree with the shelf.

Nothing here reads the network, writes a file, or decides whether a book is
readable — it groups what ``ark_health.library_books()`` already found.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

#: The shelves, in the order a reader meets them.  A shelf with no books on the
#: card is left out of the page entirely, so an empty box never shows a heading
#: with nothing under it.
TOPICS = (
    ("repair", "Fix it",
     "Broken taps, wiring, furniture and appliances — how other people fixed "
     "the same thing."),
    ("vehicles", "Vehicles",
     "Cars, trucks and motorcycles: what a noise means, and how to reach the "
     "part that makes it."),
    ("food", "Food and garden",
     "Growing it, cooking it, and keeping it through a bad season."),
    ("health", "Health and first aid",
     "What to do before help arrives — and how to look up the thing you are "
     "looking at."),
    ("home", "Water, power and shelter",
     "Keeping the house livable when the services stop."),
    ("prep", "Prepared and planning",
     "Longer reads about getting ready on purpose."),
    ("watch", "Watch and learn",
     "Films that play from the box — no internet, no account. Pick one from the "
     "list and tap it."),
    ("outdoors", "Outdoors and getting about",
     "Bicycles, camping, walking, and travelling without a signal."),
    ("radio", "Keep in touch",
     "Radios, aerials, and getting a message out of the house."),
    ("learn", "Learn and look things up",
     "The encyclopedias, the dictionary, the textbooks and the classics."),
)

#: The shelf whose collections are films.  A shelf, not a guess about the file:
#: iFixit also carries ``_ftindex:no`` and *is* searchable (22,722 hits for
#: "screen" in 0.22 s on the live box, 3 Nov 2026), so no tag in the bundle can
#: answer "is this a film".  The catalogue says which is which, once, per
#: collection.
FILM_TOPIC = "watch"

#: Where a collection with no catalogue row goes.  Never dropped.
OTHER = ("more", "Everything else",
         "On the card, but not on a shelf yet.")

_BY_KEY = {key: (title, blurb) for key, title, blurb in TOPICS}

#: ``urban-prepper_en_all_2026-08`` → ``urban-prepper_en_all``, and
#: ``wikipedia_en_all_nopic_2026-06`` → ``wikipedia_en_all`` (the catalogue keeps
#: the size word in its ``flavour`` column, not in the name).
_DATE = re.compile(r"_(?:\d{4}-\d{2})$")
_FLAVOUR = re.compile(r"_(?:nopic|maxi|mini)$")


def catalogue_file() -> Path:
    """The catalogue the installer copies in (overridable for a test)."""
    return Path(os.environ.get("ARK_CATALOG")
                or "/usr/local/share/ark/catalog.tsv")


def name_of(stem: str) -> str:
    """The catalogue id for a book's file stem, or the stem when it has none."""
    stem = (stem or "").strip()
    if stem.endswith(".zim"):
        stem = stem[:-4]
    stem = _DATE.sub("", stem)
    return _FLAVOUR.sub("", stem)


def read_catalogue(path: str | os.PathLike | None = None) -> dict:
    """``{catalogue name: topic key}`` from ``catalog.tsv``.

    A malformed or absent catalogue is an empty map, not an error: the Library
    page must still list the card's books on a box where the phase that copies
    the catalogue has not run.
    """
    target = Path(path) if path else catalogue_file()
    out: dict[str, str] = {}
    try:
        text = target.read_text(encoding="utf-8")
    except OSError:
        return out
    for raw in text.splitlines():
        line = raw.rstrip("\n")
        if not line or line.startswith("#") or line.startswith("name\t"):
            continue
        parts = line.split("\t")
        if len(parts) < 6:
            continue
        topic = parts[6].strip() if len(parts) > 6 else ""
        out[parts[0].strip()] = topic if topic in _BY_KEY else ""
    return out


def shelf_of(stem: str, catalogue: dict | None = None) -> str:
    """Which shelf one book belongs on (a topic key, or ``more``)."""
    table = read_catalogue() if catalogue is None else catalogue
    return table.get(name_of(stem)) or OTHER[0]


def shelves(books: list[dict], catalogue: dict | None = None) -> list[dict]:
    """Group the card's books onto shelves, in ``TOPICS`` order.

    Every book lands on exactly one shelf — the grouping is a partition, so the
    page can never show the same collection twice or lose one between headings.
    An empty shelf is not returned at all.
    """
    table = read_catalogue() if catalogue is None else catalogue
    buckets: dict[str, list[dict]] = {}
    for book in books:
        buckets.setdefault(shelf_of(book.get("stem") or book.get("id", ""), table),
                           []).append(book)
    out = []
    for key, title, blurb in TOPICS + (OTHER,):
        found = buckets.get(key)
        if not found:
            continue
        found = sorted(found, key=lambda b: (b.get("title") or "").lower())
        out.append({"key": key, "title": title, "blurb": blurb, "books": found})
    return out
