"""Project ARK — ARK Arcade: the shelf, the player's name, and the saves.

There are two kinds of person on this box and they must never be the same
thing:

* a **guest** taps *Games* and plays.  Nothing is asked of them.  Their saves
  belong to that browser on that device (the player keeps them in the browser's
  own storage).
* a **named player** signs in with a name and a password of their own and gets
  their saves kept here, under ``/srv/ark/arcade/saves``, so the same saves
  follow them to another device.

Player names are not administrator accounts.  They live in their own tables,
with their own cookie and their own origin, and no path in this module can
reach ``/admin``.  The two session tables are deleted separately.

A save is a file that arrives from a browser, which makes it untrusted input.
It is therefore bounded (``MAX_SAVE``), named by ARK rather than by the browser,
spooled and fsynced in ``/srv/ark/arcade/tmp``, fingerprinted with SHA-256, and
published with ``os.replace`` while the database row and the file are made to
agree.  The game's own label is only ever used for display.
"""

from __future__ import annotations

import hashlib
import html
import os
import re
import sqlite3
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import ark_db
import ark_security as sec

# ---------------------------------------------------------------------------
#  constants
# ---------------------------------------------------------------------------
PACKAGE_DIR = Path(__file__).resolve().parent

MAX_SAVE = 16 * 1024 * 1024           # one save file; bigger than any honest save
SAVE_LIST_LIMIT = 100                 # per player, per game
PLAYERS_CLOSED = "Player names are switched off on this box right now. You can still play as a guest."
COOKIE_NAME = "ark_player"
IDLE_TIMEOUT = 60 * 60                 # a player's session is a game, not a shift
ABSOLUTE_TIMEOUT = 7 * 24 * 3600       # a week, then sign in again
LOGIN_WINDOW = 15 * 60
LOGIN_FREE = 5
LOGIN_MAX_BACKOFF = 15 * 60

#: Only these reach a core.  A suffix is a claim made by a file, so the file's
#: actual header is checked too (see ``rom_facts``), but a name outside this
#: list never gets that far.
SYSTEMS: Dict[str, Dict[str, Any]] = {
    "gb":  {"core": "gambatte",       "label": "Game Boy",          "ext": (".gb",),
            "aspect": "10 / 9"},
    "gbc": {"core": "gambatte",       "label": "Game Boy Color",    "ext": (".gbc",),
            "aspect": "10 / 9"},
    "nes": {"core": "fceumm",         "label": "Nintendo NES",      "ext": (".nes",),
            "aspect": "16 / 15"},
    "snes": {"core": "snes9x",        "label": "Super Nintendo",    "ext": (".sfc", ".smc"),
             "aspect": "4 / 3"},
    "gba": {"core": "mgba",           "label": "Game Boy Advance",  "ext": (".gba",),
            "aspect": "3 / 2"},
    "genesis": {"core": "genesis_plus_gx", "label": "Sega Genesis", "ext": (".md", ".gen", ".bin"),
                "aspect": "10 / 7"},
}

#: Licence names this box recognises when it describes a game.  Nothing is
#: refused because of this list — since 25 Oct 2026 a licence is provenance, kept
#: and shown, not a gate (see ``license_problem``).  It stays so the shelf can say
#: whether a licence looks familiar instead of guessing.
LICENSES_OK = (
    "mit", "zlib", "apache", "bsd", "isc", "cc0", "cc-by", "cc-by-sa",
    "gpl", "lgpl", "mpl", "unlicense", "wtfpl", "artistic", "public-domain",
    "public domain",
)

PROVIDER_LABEL = "Homebrew Hub (gbdev)"
PROVIDER_HOME = "https://hh3.gbdev.io/"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")



# ---------------------------------------------------------------------------
#  where things live — read at call time, never bound at import
# ---------------------------------------------------------------------------
def data_root() -> Path:
    default = Path(os.environ.get("ARK_ROOT", "/srv/ark")) / "arcade"
    return Path(os.environ.get("ARK_ARCADE_DATA", str(default)))


def src_root() -> Path:
    """Where the shelf's own files live (templates, player, seed list).

    On a checkout that is ``pi/arcade`` beside ``pi/api``; on the box the unit
    points ``ARK_ARCADE_SRC`` at ``/srv/ark/src/arcade``.  The card's data is a
    different folder (``data_root``) and the two must never be confused.
    """
    chosen = os.environ.get("ARK_ARCADE_SRC")
    if chosen:
        return Path(chosen)
    installed = Path("/srv/ark/src/arcade")
    if installed.is_dir():
        return installed
    return PACKAGE_DIR.parent / "arcade"


def games_dir() -> Path:
    return data_root() / "games"


def saves_root() -> Path:
    return data_root() / "saves"


def temp_root() -> Path:
    return data_root() / "tmp"


def emulator_dir() -> Path:
    return Path(os.environ.get("ARK_EMULATOR_DIR", str(src_root() / "emulatorjs")))


def static_dir() -> Path:
    return Path(os.environ.get("ARK_ARCADE_STATIC", str(src_root() / "static")))


def template_dir() -> Path:
    return Path(os.environ.get("ARK_ARCADE_TEMPLATES", str(src_root() / "templates")))


def seed_file() -> Path:
    return Path(os.environ.get("ARK_ARCADE_SEED", str(src_root() / "catalog" / "seed.json")))


def ensure_dirs() -> None:
    for d in (games_dir(), saves_root(), temp_root()):
        d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(saves_root(), 0o700)     # a save belongs to one name
        os.chmod(temp_root(), 0o700)
    except OSError:
        pass                              # a read-only card fails later, loudly, on write


# ---------------------------------------------------------------------------
#  small, boring guards
# ---------------------------------------------------------------------------
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


def slug_ok(slug: str) -> bool:
    return bool(_SLUG_RE.match(slug or ""))


def safe_slug(raw: str) -> str:
    s = (raw or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:64]


def stored_rom_name(slug: str, ext: str) -> str:
    """The name on this box is ours; the source filename is only metadata."""
    ext = ext.lower()
    if ext not in (".gb", ".gbc", ".nes", ".sfc", ".smc", ".gba", ".md", ".gen", ".bin"):
        raise ValueError("that is not a game file this box plays")
    return f"{safe_slug(slug)}{ext}"


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def rom_facts(path: Path, system: str) -> Dict[str, Any]:
    """A cheap honest look at a game file.

    The extension is a label, not proof.  A Game Boy file carries its own
    checksum at 0x14D; a NES file starts with ``NES\\x1a``.  Refusing a file
    that is not even shaped like the console is what stops a rename from
    turning a web page into a "game".
    """
    facts: Dict[str, Any] = {"ok": False, "reason": "", "header": "", "size": 0}
    try:
        size = path.stat().st_size
    except OSError:
        facts["reason"] = "The file did not arrive."
        return facts
    facts["size"] = size
    if size <= 0:
        facts["reason"] = "That file is empty."
        return facts
    if size > 512 * 1024 * 1024:
        facts["reason"] = "That file is far too big to be a game for this box."
        return facts
    with open(path, "rb") as fh:
        head = fh.read(4096)
    if system in ("gb", "gbc"):
        if len(head) < 0x150:
            facts["reason"] = "That is not a Game Boy game — it is too short."
            return facts
        csum = 0
        for b in head[0x134:0x14D]:
            csum = (csum - b - 1) & 0xFF
        if csum != head[0x14D]:
            facts["reason"] = "That file says it is a Game Boy game but its own checksum disagrees."
            return facts
        facts["header"] = head[0x134:0x144].split(b"\0")[0].decode("latin-1", "replace")
    elif system == "nes":
        if not head.startswith(b"NES\x1a"):
            facts["reason"] = "That is not a NES game — the file does not begin with a NES header."
            return facts
        facts["header"] = "NES"
    else:
        if not head:
            facts["reason"] = "That file is empty."
            return facts
        facts["header"] = system
    facts["ok"] = True
    return facts


def license_problem(license_text: str) -> str:
    """The licence a source states is recorded, never used to refuse a game.

    Until 25 Oct 2026 this was the first gate: NC/ND licences and entries that
    stated none were turned away.  The owner's decision is that the shelf is
    built from what is good to play, and the licence text travels with the game
    as provenance — it is stored on the row and shown on the game's own page.
    This function stays because the pages and the tests ask it a question; the
    answer is now always "no problem", so nothing is refused for a licence.
    """
    return ""


# ---------------------------------------------------------------------------
#  the shelf, as the pages see it
# ---------------------------------------------------------------------------
def _row(row: sqlite3.Row) -> Dict[str, Any]:
    return {k: row[k] for k in row.keys()}


def published_games(conn: sqlite3.Connection,
                    player_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """The shelf.  A named player sees their favourites first; guests see A–Z.

    The ordering is a preference, never a filter: hiding is the owner's decision
    and no player's choice can take a game off the shelf.
    """
    if player_id is None:
        rows = conn.execute(
            "SELECT * FROM games WHERE published = 1 AND hidden = 0 ORDER BY title COLLATE NOCASE"
        ).fetchall()
        return [_row(r) for r in rows]
    rows = conn.execute(
        "SELECT g.*, p.favourite AS pref_favourite FROM games g "
        "LEFT JOIN player_game_prefs p ON p.game_id = g.id AND p.player_id = ? "
        "WHERE g.published = 1 AND g.hidden = 0 "
        "ORDER BY COALESCE(p.favourite, 0) DESC, g.title COLLATE NOCASE",
        (int(player_id),),
    ).fetchall()
    out = []
    for row in rows:
        game = _row(row)
        game["pref_favourite"] = bool(game.pop("pref_favourite", 0))
        out.append(game)
    return out


def all_games(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    rows = conn.execute("SELECT * FROM games ORDER BY title COLLATE NOCASE").fetchall()
    return [_row(r) for r in rows]


def game_by_slug(conn: sqlite3.Connection, slug: str) -> Optional[Dict[str, Any]]:
    row = conn.execute("SELECT * FROM games WHERE slug = ?", (slug,)).fetchone()
    return _row(row) if row else None


def game_by_sha(conn: sqlite3.Connection, digest: str,
                not_slug: str = "") -> Optional[Dict[str, Any]]:
    """The game already on the shelf with these exact bytes, if there is one.

    Two files with one fingerprint are one game.  A copy that arrives again —
    carried twice on a stick, or dropped in under a second name — must not
    become a second entry (or a second revision) on the shelf.
    """
    if not digest:
        return None
    row = conn.execute(
        "SELECT * FROM games WHERE sha256 = ? AND slug != ? ORDER BY id LIMIT 1",
        (digest, not_slug)).fetchone()
    return _row(row) if row else None


def game_by_id(conn: sqlite3.Connection, game_id: int) -> Optional[Dict[str, Any]]:
    row = conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()
    return _row(row) if row else None


def rom_path(conn: sqlite3.Connection, slug: str) -> Optional[Path]:
    game = game_by_slug(conn, slug)
    if not game:
        return None
    name = str(game["filename"] or "")
    if not name or name != Path(name).name or "/" in name or "\\" in name:
        return None
    path = games_dir() / name
    return path if path.is_file() else None


def player_count(conn: sqlite3.Connection) -> int:
    try:
        return int(conn.execute("SELECT COUNT(*) AS n FROM players").fetchone()["n"])
    except sqlite3.DatabaseError:
        return 0


def last_job(conn: sqlite3.Connection) -> Optional[Dict[str, Any]]:
    try:
        row = conn.execute(
            "SELECT * FROM acquisition_jobs ORDER BY id DESC LIMIT 1"
        ).fetchone()
    except sqlite3.DatabaseError:
        return None
    return _row(row) if row else None


def pending_candidates(conn: sqlite3.Connection) -> int:
    try:
        return int(conn.execute(
            "SELECT COUNT(*) AS n FROM acquisition_candidates WHERE status = 'pending'"
        ).fetchone()["n"])
    except sqlite3.DatabaseError:
        return 0



# ---------------------------------------------------------------------------
#  signing in — player names, not administrators
# ---------------------------------------------------------------------------
def players_open(conn: sqlite3.Connection) -> bool:
    return (ark_db.get_setting(conn, "arcade_players", "1") or "1") != "0"


def new_player_save_dir(player_id: int, game_id: int) -> Path:
    path = saves_root() / str(int(player_id)) / str(int(game_id))
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(path, 0o700)
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    return path


def player_register(conn: sqlite3.Connection, username: str, password: str,
                    confirm: str = "") -> Dict[str, Any]:
    """Create a name.  Raises ``ValueError`` with a sentence a person can read."""
    if not players_open(conn):
        raise ValueError(PLAYERS_CLOSED)
    problem = sec.username_problem(username)
    if problem:
        raise ValueError(problem)
    if password != confirm:
        raise ValueError("The two passwords do not match.")
    try:
        sec.check_new_password(password, username=username)
    except sec.PolicyError as exc:
        raise ValueError(str(exc)) from exc
    name = sec.normalise_username(username)
    stamp = now_iso()
    try:
        with ark_db.tx(conn):
            conn.execute(
                "INSERT INTO players (username, display_name, pass_hash, enabled, created_at) "
                "VALUES (?, ?, ?, 1, ?)",
                (name, (username or "").strip()[:60], sec.hash_password(password), stamp),
            )
            pid = int(conn.execute("SELECT id FROM players WHERE username = ?", (name,)).fetchone()[0])
    except sqlite3.IntegrityError:
        raise ValueError("That name is already taken. Please choose another one.")
    return {"id": pid, "username": name}


def login_backoff(conn: sqlite3.Connection, ip: str, name: str) -> int:
    """Seconds a caller should wait before this name may be tried again."""
    since = (datetime.fromtimestamp(time.time() - LOGIN_WINDOW, timezone.utc)
             .replace(microsecond=0).isoformat().replace("+00:00", "Z"))
    try:
        rows = conn.execute(
            "SELECT ok FROM arcade_login_attempts WHERE ts >= ? AND (source = ? OR target = ?) "
            "ORDER BY id DESC LIMIT 100", (since, (ip or "")[:80], (name or "")[:60])
        ).fetchall()
    except sqlite3.DatabaseError:
        return 0
    fails = 0
    for r in rows:
        if r["ok"]:
            break
        fails += 1
    if fails < LOGIN_FREE:
        return 0
    return min(LOGIN_MAX_BACKOFF, 60 * (2 ** max(0, fails - LOGIN_FREE)))



def player_authenticate(conn: sqlite3.Connection, username: str, password: str,
                        ip: str = "") -> Optional[Dict[str, Any]]:
    """Check a name and a password, with a deliberate pause after repeated misses."""
    if not players_open(conn):
        return None
    name = sec.normalise_username(username)
    wait = login_backoff(conn, ip, name)
    if wait > 0:
        time.sleep(min(wait, 2.0))   # never turn a person into a hanging request
    row = conn.execute("SELECT * FROM players WHERE username = ?", (name,)).fetchone()
    # Same work for an unknown name as for a real one (see sec.DUMMY_HASH).
    candidate = row["pass_hash"] if row is not None else sec.DUMMY_HASH
    valid = sec.verify_password(password, candidate or sec.DUMMY_HASH)
    ok = bool(row and row["enabled"] and valid)
    with ark_db.tx(conn):
        conn.execute(
            "INSERT INTO arcade_login_attempts (ts, source, target, ok) VALUES (?, ?, ?, ?)",
            (now_iso(), (ip or "")[:80], name[:60], 1 if ok else 0),
        )
        conn.execute("DELETE FROM arcade_login_attempts WHERE ts < ?",
                     (datetime.fromtimestamp(time.time() - 7 * 86400, timezone.utc)
                      .replace(microsecond=0).isoformat().replace("+00:00", "Z"),))
    if not ok:
        return None
    return _row(row)


def player_issue_session(conn: sqlite3.Connection, player_id: int, ip: str = "") -> Dict[str, str]:
    token = sec.new_token()
    csrf = sec.new_csrf()
    stamp = now_iso()
    with ark_db.tx(conn):
        # One name, one live browser.  A new sign-in on another device is the
        # person saying "I am there now", and the old browser must not keep
        # writing saves behind their back.
        conn.execute("DELETE FROM player_sessions WHERE player_id = ?", (int(player_id),))
        conn.execute(
            "INSERT INTO player_sessions (player_id, token_hash, csrf, created_at, last_seen, ip) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (int(player_id), sec.token_digest(token), csrf, stamp, stamp, (ip or "")[:80]),
        )
        conn.execute("UPDATE players SET last_login_at = ? WHERE id = ?", (stamp, int(player_id)))
    return {"token": token, "csrf": csrf}


def player_from_token(conn: sqlite3.Connection, token: str) -> Optional[Dict[str, Any]]:
    if not token or len(token) > 200:
        return None
    row = conn.execute(
        "SELECT s.id AS session_id, s.csrf, s.created_at, s.last_seen, "
        "       p.id, p.username, p.display_name, p.enabled "
        "FROM player_sessions s JOIN players p ON p.id = s.player_id "
        "WHERE s.token_hash = ?", (sec.token_digest(token),)
    ).fetchone()
    if not row or not row["enabled"]:
        return None
    try:
        created = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
        seen = datetime.fromisoformat(row["last_seen"].replace("Z", "+00:00"))
    except ValueError:
        return None
    now = datetime.now(timezone.utc)
    if (now - seen).total_seconds() > IDLE_TIMEOUT:
        return None
    if (now - created).total_seconds() > ABSOLUTE_TIMEOUT:
        return None
    return _row(row)


def player_touch(conn: sqlite3.Connection, session_id: int) -> None:
    with ark_db.tx(conn):
        conn.execute("UPDATE player_sessions SET last_seen = ? WHERE id = ?",
                     (now_iso(), int(session_id)))


def player_sign_out(conn: sqlite3.Connection, token: str) -> None:
    if not token:
        return
    with ark_db.tx(conn):
        conn.execute("DELETE FROM player_sessions WHERE token_hash = ?",
                     (sec.token_digest(token),))


# ---------------------------------------------------------------------------
#  saves — bounded, fingerprinted, and written atomically
# ---------------------------------------------------------------------------
SAVE_KINDS = {"battery": ".srm", "state": ".state"}


class SaveConflict(RuntimeError):
    """The save on the box moved on while this browser was still playing."""


class SaveRefused(ValueError):
    """The file offered is not a save this box will keep."""


def save_filename(kind: str, slot: int) -> str:
    if kind not in SAVE_KINDS:
        raise SaveRefused("That is not a kind of save this box keeps.")
    if not isinstance(slot, int) or slot < 0 or slot > 9:
        raise SaveRefused("That save slot does not exist.")
    return "battery.srm" if kind == "battery" else f"state-{slot:02d}.state"


def _atomic_write(destination: Path, data: bytes, mode: int = 0o600) -> None:
    ensure_dirs()
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix="save-", suffix=".part", dir=str(temp_root()))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp_name, mode)
        os.replace(tmp_name, destination)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def save_path(player_id: int, game_id: int, kind: str, slot: int) -> Path:
    return new_player_save_dir(player_id, game_id) / save_filename(kind, slot)


def saves_for(conn: sqlite3.Connection, player_id: int,
              game_id: Optional[int] = None) -> List[Dict[str, Any]]:
    sql = ("SELECT s.*, g.slug AS game_slug, g.title AS game_title FROM player_saves s "
           "JOIN games g ON g.id = s.game_id WHERE s.player_id = ?")
    args: List[Any] = [int(player_id)]
    if game_id is not None:
        sql += " AND s.game_id = ?"
        args.append(int(game_id))
    sql += " ORDER BY g.title COLLATE NOCASE, s.kind, s.slot"
    rows = conn.execute(sql, args).fetchall()
    return [_row(r) for r in rows]


# ---------------------------------------------------------------------------
#  a player's choices about a game — preferences, not saves
# ---------------------------------------------------------------------------
PREFS_DEFAULT: Dict[str, Any] = {
    "autosave": False, "favourite": False, "last_played": "",
}


def player_prefs_get(conn: sqlite3.Connection, player_id: int,
                     game_id: int) -> Dict[str, Any]:
    """What this name chose for this game; defaults when nothing was chosen."""
    row = conn.execute(
        "SELECT autosave, favourite, last_played FROM player_game_prefs "
        "WHERE player_id = ? AND game_id = ?",
        (int(player_id), int(game_id)),
    ).fetchone()
    if not row:
        return dict(PREFS_DEFAULT)
    return {"autosave": bool(row["autosave"]), "favourite": bool(row["favourite"]),
            "last_played": row["last_played"] or ""}


def player_prefs_set(conn: sqlite3.Connection, player_id: int, game_id: int, *,
                     autosave: Optional[bool] = None, favourite: Optional[bool] = None,
                     played: bool = False) -> Dict[str, Any]:
    """Change only the choices given; a missing one keeps its stored value.

    ``played`` stamps the time instead of rewriting what the player chose, so
    opening a game never quietly flips a switch.  The row is created only once
    something is actually chosen or the game is opened.
    """
    game = game_by_id(conn, int(game_id))
    if not game or not game["published"] or game["hidden"]:
        raise SaveRefused("That game is not on the shelf.")
    current = player_prefs_get(conn, player_id, game_id)
    if autosave is None:
        autosave = current["autosave"]
    if favourite is None:
        favourite = current["favourite"]
    stamp = now_iso()
    last_played = stamp if played else current["last_played"]
    with ark_db.tx(conn):
        conn.execute(
            "INSERT INTO player_game_prefs (player_id, game_id, autosave, favourite, "
            "last_played, updated_at) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(player_id, game_id) DO UPDATE SET "
            "  autosave = excluded.autosave, favourite = excluded.favourite, "
            "  last_played = excluded.last_played, updated_at = excluded.updated_at",
            (int(player_id), int(game["id"]), int(bool(autosave)), int(bool(favourite)),
             last_played, stamp),
        )
    return {"autosave": bool(autosave), "favourite": bool(favourite),
            "last_played": last_played}


def player_prefs_all(conn: sqlite3.Connection, player_id: int) -> Dict[int, Dict[str, Any]]:
    """Every choice this name has made, keyed by game, for one page render."""
    try:
        rows = conn.execute(
            "SELECT game_id, autosave, favourite, last_played FROM player_game_prefs "
            "WHERE player_id = ?", (int(player_id),)
        ).fetchall()
    except sqlite3.DatabaseError:
        return {}
    return {int(r["game_id"]): {"autosave": bool(r["autosave"]),
                                "favourite": bool(r["favourite"]),
                                "last_played": r["last_played"] or ""}
            for r in rows}


def _check_game(conn: sqlite3.Connection, game_id: int) -> Dict[str, Any]:
    game = game_by_id(conn, int(game_id))
    if not game:
        raise SaveRefused("That game is not on this box.")
    name = str(game["filename"] or "")
    if not name or name != Path(name).name or "/" in name or "\\" in name:
        raise SaveRefused("That game's file name is not one this box wrote.")
    if not (games_dir() / name).is_file():
        raise SaveRefused("That game's file is missing from this box.")
    return game


def player_save_put(conn: sqlite3.Connection, player_id: int, game_id: int, kind: str,
                    slot: int, data: bytes, expected_revision: Optional[int] = None,
                    expected_sha: str = "") -> Dict[str, Any]:
    """Replace one save, refusing stale writes and never touching another owner."""
    game = _check_game(conn, game_id)
    filename = save_filename(kind, slot)
    if not data:
        raise SaveRefused("That save is empty — there is nothing to keep.")
    if len(data) > MAX_SAVE:
        raise SaveRefused("That save is larger than this box keeps (16 MB).")
    row = conn.execute(
        "SELECT * FROM player_saves WHERE player_id = ? AND game_id = ? AND kind = ? AND slot = ?",
        (int(player_id), int(game["id"]), kind, int(slot)),
    ).fetchone()
    current_revision = int(row["revision"]) if row else 0
    if expected_revision is not None and int(expected_revision) != current_revision:
        raise SaveConflict(
            f"This save changed somewhere else (the box has version {current_revision}, "
            f"this page had {int(expected_revision)}). Reload the game, then choose which one to keep."
        )
    if expected_sha and row and row["sha256"] and expected_sha != row["sha256"]:
        raise SaveConflict("This save changed somewhere else. Reload the game before saving.")
    digest = sha256_bytes(data)
    path = save_path(player_id, game["id"], kind, slot)
    previous = None
    if row and path.is_file():
        try:
            previous = path.read_bytes()
        except OSError:
            previous = None
    _atomic_write(path, data)
    stamp = now_iso()
    try:
        with ark_db.tx(conn):
            conn.execute(
                "INSERT INTO player_saves (player_id, game_id, kind, slot, filename, size, sha256, revision, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?) "
                "ON CONFLICT(player_id, game_id, kind, slot) DO UPDATE SET "
                "  filename = excluded.filename, size = excluded.size, sha256 = excluded.sha256, "
                "  revision = player_saves.revision + 1, updated_at = excluded.updated_at",
                (int(player_id), int(game["id"]), kind, int(slot), filename, len(data), digest, stamp),
            )
    except sqlite3.DatabaseError:
        # The database row is the truth.  If it could not record the save, the
        # disk must go back to what the row still describes (or to nothing).
        if previous is not None:
            try:
                _atomic_write(path, previous)
            except OSError:
                pass
        else:
            try:
                path.unlink()
            except OSError:
                pass
        raise
    return {
        "game_id": int(game["id"]), "slug": game["slug"], "kind": kind, "slot": int(slot),
        "filename": filename, "size": len(data), "sha256": digest,
        "revision": current_revision + 1, "updated_at": stamp,
    }



def player_save_get(conn: sqlite3.Connection, player_id: int, game_id: int,
                    kind: str, slot: int) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        "SELECT * FROM player_saves WHERE player_id = ? AND game_id = ? AND kind = ? AND slot = ?",
        (int(player_id), int(game_id), kind, int(slot)),
    ).fetchone()
    if not row:
        return None
    path = save_path(player_id, game_id, kind, slot)
    if not path.is_file():
        return None
    data = path.read_bytes()
    if sha256_bytes(data) != row["sha256"]:
        raise SaveConflict("The save file on this box does not match its record. It was not handed out.")
    out = _row(row)
    out["data"] = data
    out["game"] = game_by_id(conn, int(game_id))
    return out


def player_save_delete(conn: sqlite3.Connection, player_id: int, save_id: int) -> bool:
    row = conn.execute(
        "SELECT * FROM player_saves WHERE id = ? AND player_id = ?",
        (int(save_id), int(player_id)),
    ).fetchone()
    if not row:
        return False
    with ark_db.tx(conn):
        conn.execute("DELETE FROM player_saves WHERE id = ? AND player_id = ?",
                     (int(save_id), int(player_id)))
    try:
        save_path(player_id, row["game_id"], row["kind"], row["slot"]).unlink()
    except OSError:
        pass
    return True


def player_export_bytes(conn: sqlite3.Connection, player_id: int) -> bytes:
    """One small zip a person can keep, made with nothing but the stdlib."""
    import io

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for row in saves_for(conn, player_id):
            path = save_path(player_id, row["game_id"], row["kind"], row["slot"])
            if not path.is_file():
                continue
            arc = f"saves/{row['game_slug']}/{row['filename']}"
            zf.writestr(arc, path.read_bytes())
        zf.writestr("PLAYERS-README.txt",
                    "These are the saves for this ARK player name, one folder per game.\n"
                    "Keep this file somewhere other than the box's own card: a copy on\n"
                    "the same card does not survive that card dying.\n")
    return buffer.getvalue()


MAX_IMPORT = 32 * 1024 * 1024          # a zip of saves; bigger is not a person


def player_import_bytes(conn: sqlite3.Connection, player_id: int,
                        data: bytes) -> Dict[str, Any]:
    """Put saves back from the zip this box handed out — and only saves.

    The archive is untrusted.  Nothing in it is extracted to a path: every entry
    must look like ``saves/<game>/<save name>``, the game must already be on the
    shelf, and the file is written through ``player_save_put`` like any other
    save.  A path that climbs, a file that is not a save, and an unknown game are
    skipped and named in the answer; the good entries are kept.  The answer says
    exactly how many arrived.
    """
    import io

    if not data:
        raise SaveRefused("That file is empty.")
    if len(data) > MAX_IMPORT:
        raise SaveRefused("That file is larger than this box accepts for saves.")
    imported, skipped, errors = 0, [], []
    expanded = 0
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, OSError) as exc:
        raise SaveRefused("That is not a save file this box made.") from exc
    with archive:
        infos = archive.infolist()
        if len(infos) > 500:
            raise SaveRefused("That file holds too many things to be a set of saves.")
        total = sum(max(0, i.file_size) for i in infos)
        if total > 2 * MAX_IMPORT:
            raise SaveRefused("That file would expand to more than this box accepts.")
        for info in infos:
            name = info.filename.replace("\\", "/")
            if info.is_dir() or name == "PLAYERS-README.txt":
                continue
            parts = name.split("/")
            if len(parts) != 3 or parts[0] != "saves":
                skipped.append(name[:80])
                continue
            slug = safe_slug(parts[1])
            game = game_by_slug(conn, slug) if slug else None
            if not game or not (games_dir() / game["filename"]).is_file():
                skipped.append(name[:80])
                continue
            filename = parts[2]
            if filename == "battery.srm":
                kind, slot = "battery", 0
            else:
                match = re.fullmatch(r"state-(\d{2})\.state", filename)
                if not match:
                    skipped.append(name[:80])
                    continue
                kind, slot = "state", int(match.group(1))
            try:
                with archive.open(info) as fh:
                    raw = fh.read(MAX_SAVE + 1)
                if len(raw) > MAX_SAVE:
                    errors.append(f"{name[:60]}: that save is larger than this box keeps")
                    continue
                expanded += len(raw)
                if expanded > 2 * MAX_IMPORT:
                    raise SaveRefused("That file expands to more than this box accepts for saves.")
                player_save_put(conn, player_id, int(game["id"]), kind, slot, raw,
                                expected_revision=None)
                imported += 1
            except (SaveRefused, SaveConflict, OSError, zipfile.BadZipFile) as exc:
                errors.append(f"{name[:60]}: {exc}")
    return {"imported": imported, "skipped": skipped, "errors": errors}
