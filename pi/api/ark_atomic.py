"""A write that survives the plug being pulled out.

The Ark lives on a card in a house where the power goes away without warning, so
every file it keeps as *state* — ``ark_status.json``, the import ledger, the
network watchdog's counters, the household answers — is written the same way, in
this one place:

1. write the new bytes into a temp file **in the same folder** (same filesystem,
   so the rename is atomic), ``fsync`` it;
2. keep the previous file as ``<name>.bak`` — the copy you fall back to when the
   new bytes turn out to be unreadable after a bad cut;
3. ``os.replace()`` the temp file over the real one, which either happened or did
   not, and can never leave a half-written file;
4. ``fsync`` the folder, so the rename itself is on the card and not only in the
   kernel's cache;
5. ``os.sync()`` when the caller asks for it (a power cut two seconds later must
   not lose the file).

``read_json()`` is the other half: it answers the value **and** what was wrong
with it, so a page can say "this file was broken and has been set aside" instead
of printing a traceback or, worse, an empty page that looks fine.

Nothing here decides *what* to write.  The one-writer rules in the project rules
still hold: shared files go through ``ark_storage.spool()``, games through
``publish_candidate()``, saves through ``player_save_put()``.  This module is for
the small files the box keeps about itself.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Optional, Tuple

#: What a previous version of a file is kept as.
BACKUP_SUFFIX = ".bak"

#: The temp file that carries a new version into place.  The leading dot keeps it
#: out of listings, and ``.tmp`` is what the self-heal sweeper looks for.
TEMP_PREFIX = "."
TEMP_SUFFIX = ".tmp"


def fsync_dir(folder: Any) -> None:
    """Make a rename durable by syncing the folder that holds it.

    Silently does nothing where this cannot be done (some filesystems refuse to
    open a folder for reading); losing the durability of the rename is better
    than losing the write.
    """
    try:
        fd = os.open(str(folder), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def sync() -> None:
    """Flush everything the kernel is holding, best effort."""
    try:
        os.sync()
    except (AttributeError, OSError):        # pragma: no cover - platform
        pass


def save_bytes(path: Any, data: bytes, *, mode: Optional[int] = None,
               keep_backup: bool = True) -> Path:
    """Write ``data`` to ``path`` so a cut leaves the old file or the new one.

    ``mode`` defaults to the mode the file already had (``0644`` for a new one),
    and a file that already exists keeps its owner when this runs as root — a
    root-run step must not quietly leave a file the ``pi`` pages cannot read.
    """
    target = Path(path)
    folder = target.parent
    folder.mkdir(parents=True, exist_ok=True)
    try:
        old_stat: Optional[os.stat_result] = target.stat()
    except OSError:
        old_stat = None
    old_mode = stat.S_IMODE(old_stat.st_mode) if old_stat is not None else None
    fd, tmp_name = tempfile.mkstemp(prefix=TEMP_PREFIX + target.name + ".",
                                    suffix=TEMP_SUFFIX, dir=str(folder))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_name, mode if mode is not None else (old_mode or 0o644))
        if old_stat is not None and hasattr(os, "geteuid") and os.geteuid() == 0:
            try:
                os.chown(tmp_name, old_stat.st_uid, old_stat.st_gid)
            except OSError:                  # pragma: no cover - odd filesystems
                pass
        if keep_backup and old_stat is not None:
            try:
                shutil.copy2(str(target), str(target) + BACKUP_SUFFIX)
            except OSError:
                # The backup is a courtesy, not the promise: the rename below is
                # what makes the write safe, and it must happen regardless.
                pass
        os.replace(tmp_name, str(target))
        tmp_name = ""
        fsync_dir(folder)
        return target
    finally:
        if tmp_name:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass


def save_text(path: Any, text: str, *, encoding: str = "utf-8",
              mode: Optional[int] = None, keep_backup: bool = True) -> Path:
    return save_bytes(path, str(text).encode(encoding), mode=mode,
                      keep_backup=keep_backup)


def save_json(path: Any, value: Any, *, indent: int = 2,
              mode: Optional[int] = None, keep_backup: bool = True) -> Path:
    """Write JSON the same way.  A trailing newline keeps every diff readable."""
    text = json.dumps(value, indent=indent, ensure_ascii=False, sort_keys=False)
    return save_text(path, text + "\n", mode=mode, keep_backup=keep_backup)


def read_json(path: Any) -> Tuple[Optional[Any], str]:
    """Return ``(value, problem)`` — ``problem`` is ``""`` when the file was fine.

    A missing file is ``(None, "missing")``, which is a normal state for every one
    of these files (a box that has never lost its link has no state file).  A
    broken file is ``(None, "<why>")`` and never an exception: the caller decides
    whether to heal, report or carry on without it.
    """
    target = Path(path)
    try:
        raw = target.read_bytes()
    except FileNotFoundError:
        return None, "missing"
    except OSError as exc:
        return None, "cannot be read (%s)" % exc.strerror
    if not raw.strip():
        return None, "is empty"
    try:
        return json.loads(raw.decode("utf-8")), ""
    except UnicodeDecodeError:
        return None, "is not text"
    except ValueError as exc:
        return None, "is not valid JSON (%s)" % exc


def good(path: Any) -> bool:
    """Is that file a JSON file this box can read?"""
    return read_json(path)[1] == ""


def restore_backup(path: Any) -> bool:
    """Put the ``.bak`` copy back, atomically, after checking it is readable JSON.

    A backup that is itself broken must never be copied over anything, so the
    copy is parsed before it is accepted.
    """
    target = Path(path)
    backup = Path(str(target) + BACKUP_SUFFIX)
    if not backup.is_file():
        return False
    try:
        data = backup.read_bytes()
    except OSError:
        return False
    try:
        json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return False
    save_bytes(target, data, keep_backup=False)
    return True


def save_clock() -> bool:
    """Ask ``fake-hwclock`` to remember the time, so the next cold boot has one.

    A Pi has no battery-backed clock.  With ``fake-hwclock`` installed, the time
    this box last knew is written to disk and restored at boot, which is what
    keeps log timestamps and "how long since" honest after a power cut.
    """
    tool = shutil.which("fake-hwclock")
    if not tool:
        return False
    try:
        subprocess.run([tool, "save"], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=10, check=False)
        return True
    except (OSError, subprocess.SubprocessError):   # pragma: no cover - platform
        return False

