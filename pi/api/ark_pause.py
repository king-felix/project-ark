"""The owner's one pause for heavy background work.

The Ark is a small computer in a room.  Sometimes the people in that room want
the machine quiet — a hot afternoon, a battery being saved for the evening, a
film being watched without an encoder taking the processor — and the honest way
to offer that is one switch that says exactly what it stops.

**One authority: a file.**  ``<data>/background-paused`` is the whole state.  It
is written atomically by this module (the web application is its only writer),
it survives a restart because it is on the card, and a shell script or a systemd
condition can read it without starting Python or opening the database.  The
database records *who* asked and when in the audit log; it is not a second copy
of the answer, because two answers are how a switch comes to disagree with
itself.

**What the pause is allowed to stop** (the documented policy, in one place):

* preparing films — new jobs are not claimed; a film already being converted is
  allowed to finish, because an FFmpeg encode cannot resume from the frame it
  reached and stopping it would throw the work away;
* book imports, game discovery and collection downloads — deferred, not
  cancelled; a download stops at a resumable piece and keeps its ``.part``;
* answers from the helper — a question is refused with a sentence rather than
  starting the engine.

**What it never stops**: reading, listening, watching what is already prepared,
playing games, opening maps, the message board, the File Box, deletion,
integrity checks, recovery, backups, and every essential write.  A pause is not
a shutdown and it is not a claim that the box is broken.

The pause is *not* inferred from the internet being absent: an offline box with
no connection is the normal state of this project, and entertainment is part of
what it is for.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import ark_atomic
import ark_db

MARKER_NAME = "background-paused"
KICK_NAME = "background-kick"

#: What the owner reads on the page, and what a script prints.
PAUSED_SENTENCE = (
    "The owner has paused heavy background work.  Reading, watching, games, maps "
    "and messages keep working; new preparation is waiting."
)
RUNNING_SENTENCE = "Heavy background work is allowed."


def data_dir() -> Path:
    """The records folder — beside the database, on the card."""
    return Path(ark_db.DB_PATH).parent


def marker_path() -> Path:
    """The one file that answers 'is background work paused?'."""
    override = os.environ.get("ARK_PAUSE_MARKER")
    return Path(override) if override else data_dir() / MARKER_NAME


def kick_path() -> Path:
    """A file touched on resume so an event-driven watcher runs again.

    ``ark-book-import.path`` watches it as well as the drop folders.  Without
    this, a book dropped while the pause was on would sit unread until somebody
    dropped another one — the pause would have silently eaten a trigger.
    """
    override = os.environ.get("ARK_PAUSE_KICK")
    return Path(override) if override else data_dir() / KICK_NAME


def paused() -> bool:
    """Is the owner's pause in force?  One ``stat``, no database, no Python state."""
    try:
        return marker_path().exists()
    except OSError:
        # A marker path that cannot be examined is not permission to start heavy
        # work.  Failing closed here costs a deferred job, not a wrong answer.
        return True


def requested() -> dict:
    """What the file says, with the time it was asked — for a page, never a guess."""
    path = marker_path()
    try:
        stat = path.stat()
    except OSError:
        return {"paused": False, "marker": str(path), "since": "", "age_seconds": None}
    return {
        "paused": True,
        "marker": str(path),
        "since": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(
            timespec="seconds"),
        "age_seconds": max(0, int(datetime.now(timezone.utc).timestamp() - stat.st_mtime)),
    }


def set_paused(on: bool, *, note: str = "") -> dict:
    """Turn the pause on or off; returns what is now true.

    Turning it *off* also touches the kick file, so a book that arrived while the
    pause was on is looked at again without asking the owner to drop it twice.
    """
    path = marker_path()
    if on:
        text = PAUSED_SENTENCE + ("\n" + note.strip() + "\n" if note.strip() else "\n")
        ark_atomic.save_text(path, text, mode=0o644, keep_backup=False)
    else:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            # If the marker cannot be removed, the pause is still on.  Saying so
            # is better than reporting a resume that did not happen.
            return {**requested(), "changed": False}
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            ark_atomic.save_text(kick_path(), stamp + "\n", mode=0o644, keep_backup=False)
        except OSError:
            pass                      # the resume itself is what matters
    return {**requested(), "changed": True}
