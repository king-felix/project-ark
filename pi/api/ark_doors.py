"""Project ARK — every address on this box, in one list.

The owner's page at ``/admin/doors`` is generated from this tuple, and so is the
test that holds it to the application: a route added to the administrator's pages
and not named here is a door nobody can find, and an address named here that the
box does not answer is a dead link in the paperwork.

**Why it is a page and not prose in a document.**  Every previous list of
addresses in this repo drifted (``README.md``, ``docs/ADMIN.md`` and
``docs/USING-THE-ARK.md`` have each been wrong at some point about a door).  The
list lives beside the code, the page renders it, and ``tests/test_admin_upload.py``
fails when a new administrator page is missing from it.

Words here obey the same rule as every other page a person reads: no product
names, and every line says what happens if you open it.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

#: Addresses are shown exactly as they are typed.  ``{host}`` is the box's own
#: name on the network the reader is on (``ark.lan`` on the hotspot, ``ark.local``
#: on a home network), which is why the page prints that rule above the table
#: instead of pretending one name works everywhere.
DOORS: Tuple[Dict[str, str], ...] = (
    # ---- the way in -------------------------------------------------------
    {"group": "The way in", "address": "https://{host}:8443/admin",
     "name": "Sign in",
     "who": "the owner",
     "what": "The private door. Everything below that says “behind the sign-in” is "
             "reached from here, and nothing on this door is visible without it."},
    {"group": "The way in", "address": "https://localhost:8444/admin",
     "name": "First setup",
     "who": "the owner, over an SSH tunnel",
     "what": "Only for a box that has never been claimed. It answers on the box "
             "itself and nowhere else, so the first stranger on the Wi-Fi cannot "
             "take it."},

    # ---- everything a reader may open -------------------------------------
    {"group": "Open to anyone on the box's Wi-Fi", "address": "http://{host}/",
     "name": "Front page",
     "who": "anyone",
     "what": "The page a person starts from: every part of the box, what is on it, "
             "and how much room is left."},
    {"group": "Open to anyone on the box's Wi-Fi", "address": "http://{host}/search",
     "name": "One search box",
     "who": "anyone",
     "what": "Looks in the Offline Library, the guides and the books at once."},
    {"group": "Open to anyone on the box's Wi-Fi", "address": "http://{host}/board",
     "name": "Message Board",
     "who": "anyone",
     "what": "Notes the household leaves for each other."},
    {"group": "Open to anyone on the box's Wi-Fi", "address": "http://{host}/files",
     "name": "File Box",
     "who": "anyone",
     "what": "Share a file and take one away. A shared file's own link is "
             "http://{host}/drop/<name>."},
    {"group": "Open to anyone on the box's Wi-Fi", "address": "http://{host}/household",
     "name": "Household page",
     "who": "anyone",
     "what": "The page for the day something goes wrong: eight situations, each "
             "with the page on this box that answers it."},
    {"group": "Open to anyone on the box's Wi-Fi", "address": "http://{host}/cards",
     "name": "Wall cards",
     "who": "anyone",
     "what": "Three cards to print, each with one of the box's addresses drawn as "
             "a square a phone camera can read."},
    {"group": "Open to anyone on the box's Wi-Fi", "address": "http://{host}/essentials.zip",
     "name": "The pack to carry away",
     "who": "anyone",
     "what": "The manuals and guides, the quick card and the recovery sheet in one "
             "file, to open on a device that is not this box."},
    {"group": "Open to anyone on the box's Wi-Fi", "address": "http://{host}/recovery.txt",
     "name": "Recovery sheet",
     "who": "anyone",
     "what": "The printed page for a box that will not start."},
    {"group": "Open to anyone on the box's Wi-Fi", "address": "http://{host}/ask",
     "name": "Ask the Ark",
     "who": "anyone",
     "what": "Ask a question in your own words. The answer is written on this box, "
             "with no internet involved."},
    {"group": "Open to anyone on the box's Wi-Fi", "address": "http://{host}/arcade/",
     "name": "Games",
     "who": "anyone",
     "what": "The game shelf: pick a game and play it in the browser."},
    {"group": "Open to anyone on the box's Wi-Fi", "address": "http://{host}/arcade/add",
     "name": "Add a game",
     "who": "anyone",
     "what": "A visitor can put a game of their own on the shelf. A visitor can add "
             "a game and never replace one."},
    {"group": "Open to anyone on the box's Wi-Fi", "address": "http://{host}/media/",
     "name": "Media",
     "who": "anyone",
     "what": "The film shelf: what the owner has published, with pictures, a player "
             "and a download. A film somebody shares waits for the owner first."},
    {"group": "Open to anyone on the box's Wi-Fi", "address": "http://{host}/books/",
     "name": "Books",
     "who": "anyone",
     "what": "The Book Library: read in the browser, or download a copy."},
    {"group": "Open to anyone on the box's Wi-Fi", "address": "http://{host}/kiwix/",
     "name": "Offline Library",
     "who": "anyone",
     "what": "The collections themselves — the reading half of the box."},

    # ---- the owner's pages ------------------------------------------------
    {"group": "Behind the sign-in", "address": "https://{host}:8443/admin",
     "name": "Overview",
     "who": "the owner",
     "what": "What is on the box and what needs looking at, in one screen."},
    {"group": "Behind the sign-in", "address": "https://{host}:8443/admin/upload",
     "name": "Add books and games",
     "who": "the owner",
     "what": "Choose several files at once and press one button: books go into the "
             "Book Library, games go onto the game shelf."},
    {"group": "Behind the sign-in", "address": "https://{host}:8443/admin/media",
     "name": "Media",
     "who": "the owner",
     "what": "The films on the box: what each file really is, what it needs to play, "
             "its picture, and how much room is left before the box refuses more."},
    {"group": "Behind the sign-in", "address": "https://{host}:8443/admin/arcade",
     "name": "Games",
     "who": "the owner",
     "what": "The shelf, what licence each game states, one game at a time with its "
             "words checked first, and who may keep saves."},
    {"group": "Behind the sign-in", "address": "https://{host}:8443/admin/community",
     "name": "Community",
     "who": "the owner",
     "what": "What may be written on the Message Board and into the File Box, and "
             "what has been written."},
    {"group": "Behind the sign-in", "address": "https://{host}:8443/admin/household",
     "name": "Household",
     "who": "the owner",
     "what": "The nine answers only a person can write, and the pack to carry away."},
    {"group": "Behind the sign-in", "address": "https://{host}:8443/admin/network",
     "name": "Network",
     "who": "the owner",
     "what": "Where the box is now, a Wi-Fi saved for later, and one restart on "
             "your own network."},
    {"group": "Behind the sign-in", "address": "https://{host}:8443/admin/health",
     "name": "What is working",
     "who": "the owner",
     "what": "Every part of the box, whether it is working, and what to do when it "
             "is not."},
    {"group": "Behind the sign-in", "address": "https://{host}:8443/admin/doors",
     "name": "Addresses",
     "who": "the owner",
     "what": "This page: every address on the box, who may open it, and what it is "
             "for."},
    {"group": "Behind the sign-in", "address": "https://{host}:8443/admin/accounts",
     "name": "Administrators",
     "who": "the owner",
     "what": "Who else may sign in, and stopping a name without deleting it."},
    {"group": "Behind the sign-in", "address": "https://{host}:8443/admin/password",
     "name": "My password",
     "who": "the owner",
     "what": "Changing your own password."},
    {"group": "Behind the sign-in", "address": "https://{host}:8443/admin/audit",
     "name": "History",
     "who": "the owner",
     "what": "What was done on this box, by whom, and when."},

    # ---- a name for saving a game ----------------------------------------
    {"group": "A name for keeping saves", "address": "https://{host}:8445/arcade/player",
     "name": "My saves",
     "who": "anyone with a name",
     "what": "Signing in as a player — only for keeping a game's progress and "
             "carrying it to another device. A player name cannot reach any of the "
             "pages above."},

    # ---- for a monitor rather than a person ------------------------------
    {"group": "For a monitor rather than a person", "address": "http://{host}/api/ready",
     "name": "Is the box ready?",
     "who": "a monitoring script",
     "what": "200 when every part a reader needs is working, 503 when something is "
             "not, with the reason written in the answer. This is the honest one."},
    {"group": "For a monitor rather than a person", "address": "http://{host}/api/status",
     "name": "What is on the box",
     "who": "a monitoring script",
     "what": "The same facts the front page uses, as data."},
    {"group": "For a monitor rather than a person", "address": "http://{host}/api/health",
     "name": "Is the program running?",
     "who": "a monitoring script",
     "what": "Says only that the web program answered. It is not a verdict on the "
             "box — read /api/ready for that."},
)

#: The two form pages *inside* the way in — the sign-in form and the first-run
#: password change.  They are reached from the doors above and are deliberately not
#: listed as addresses of their own (nobody types ``/admin/login``), but naming them
#: here is what lets a test tell "reached from the way in" from "nobody can find it".
STEPS = ("/admin/login", "/admin/setup")

#: The order groups are printed in, so the page reads like a walk through the box.
GROUP_ORDER = (
    "The way in",
    "Open to anyone on the box's Wi-Fi",
    "Behind the sign-in",
    "A name for keeping saves",
    "For a monitor rather than a person",
)


def groups() -> List[Tuple[str, List[Dict[str, str]]]]:
    """The doors, grouped and in a stable order.  The page renders exactly this."""
    out: List[Tuple[str, List[Dict[str, str]]]] = []
    for name in GROUP_ORDER:
        rows = [dict(door) for door in DOORS if door["group"] == name]
        if rows:
            out.append((name, rows))
    return out


def paths() -> List[str]:
    """Every address, with the host placeholder left in.  Used by the tests."""
    return [door["address"] for door in DOORS]


def admin_paths() -> List[str]:
    """The paths this box's own administrator pages answer on, as a reader types them."""
    found = []
    for door in DOORS:
        address = door["address"]
        if "/admin" not in address:
            continue
        tail = address.split("/admin", 1)[1]
        found.append("/admin" + tail)
    return found

