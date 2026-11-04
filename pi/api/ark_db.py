"""Project ARK — the one small database, and the migrations that keep it current.

The board, the file index and (since schema 1) the administrators, their
sessions and the audit trail all live in a single SQLite file.  Everything in
here is deliberately dull:

* short-lived connections, one per request, always closed;
* every connection holds the maintenance gate for its whole life, so no
  replacement can happen while a handle is open (``ark_maintenance``);
* WAL so a reader never blocks the download/import jobs;
* numbered migrations applied inside one transaction, so a crash leaves the
  previous version untouched;
* ``BEGIN IMMEDIATE`` for anything that must not interleave (the "last
  administrator" rule depends on it).

The one thing that is *not* in the database on purpose: the install marker
file.  If the database disappears, an empty file next to it still proves this
box was configured once — so ARK must ask for recovery instead of quietly
recreating the well-known bootstrap account.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import ark_maintenance

try:
    # Optional on purpose: a box caught mid-deploy may briefly have this file
    # missing.  The gate still protects a known replacement; this extra marker
    # protects the window after a crash, when the gate is already free.
    import ark_db_replace
except ImportError:  # pragma: no cover - a deployment catch-up, not a normal box
    ark_db_replace = None  # type: ignore[assignment]

ARK_ROOT = Path(os.environ.get("ARK_ROOT", "/srv/ark"))
DB_PATH = Path(os.environ.get("ARK_DB", str(ARK_ROOT / "api" / "data" / "ark.db")))
MARKER_PATH = Path(os.environ.get("ARK_MARKER", str(DB_PATH.parent / "ark.installed")))

SCHEMA_VERSION = 7

# ---------------------------------------------------------------------------
#  migrations — append, never edit an entry that has already shipped
# ---------------------------------------------------------------------------
MIGRATIONS: list[tuple[int, list[str]]] = [
    (
        1,
        [
            # -- the public tables, unchanged, so the file is self-describing ---
            """CREATE TABLE IF NOT EXISTS notes (
                   id    INTEGER PRIMARY KEY AUTOINCREMENT,
                   ts    TEXT NOT NULL,
                   name  TEXT NOT NULL,
                   body  TEXT NOT NULL
               )""",
            """CREATE TABLE IF NOT EXISTS files (
                   id    INTEGER PRIMARY KEY AUTOINCREMENT,
                   ts    TEXT NOT NULL,
                   name  TEXT NOT NULL,
                   size  INTEGER NOT NULL,
                   note  TEXT NOT NULL DEFAULT ''
               )""",
            # -- administrators -------------------------------------------------
            """CREATE TABLE IF NOT EXISTS admins (
                   id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                   username            TEXT NOT NULL COLLATE NOCASE UNIQUE,
                   display_name        TEXT NOT NULL DEFAULT '',
                   pass_hash           TEXT NOT NULL,
                   role                TEXT NOT NULL DEFAULT 'admin',
                   enabled             INTEGER NOT NULL DEFAULT 1,
                   must_change         INTEGER NOT NULL DEFAULT 0,
                   is_bootstrap        INTEGER NOT NULL DEFAULT 0,
                   created_at          TEXT NOT NULL,
                   created_by          TEXT NOT NULL DEFAULT '',
                   password_changed_at TEXT NOT NULL DEFAULT '',
                   last_login_at       TEXT NOT NULL DEFAULT ''
               )""",
            # -- sessions (opaque token, only the digest is stored) -------------
            """CREATE TABLE IF NOT EXISTS admin_sessions (
                   id          INTEGER PRIMARY KEY AUTOINCREMENT,
                   admin_id    INTEGER REFERENCES admins(id) ON DELETE CASCADE,
                   token_sha   TEXT NOT NULL UNIQUE,
                   csrf        TEXT NOT NULL,
                   kind        TEXT NOT NULL,
                   created     REAL NOT NULL,
                   last_seen   REAL NOT NULL,
                   abs_expires REAL NOT NULL,
                   epoch       INTEGER NOT NULL,
                   ip          TEXT NOT NULL DEFAULT ''
               )""",
            """CREATE INDEX IF NOT EXISTS admin_sessions_admin ON admin_sessions(admin_id)""",
            # -- login throttling ----------------------------------------------
            """CREATE TABLE IF NOT EXISTS login_attempts (
                   id       INTEGER PRIMARY KEY AUTOINCREMENT,
                   ts       REAL NOT NULL,
                   username TEXT NOT NULL,
                   source   TEXT NOT NULL,
                   ok       INTEGER NOT NULL
               )""",
            """CREATE INDEX IF NOT EXISTS login_attempts_ts ON login_attempts(ts)""",
            # -- audit trail ----------------------------------------------------
            """CREATE TABLE IF NOT EXISTS audit_log (
                   id         INTEGER PRIMARY KEY AUTOINCREMENT,
                   ts         TEXT NOT NULL,
                   actor_id   INTEGER,
                   actor_name TEXT NOT NULL DEFAULT '',
                   action     TEXT NOT NULL,
                   target     TEXT NOT NULL DEFAULT '',
                   result     TEXT NOT NULL DEFAULT 'ok',
                   detail     TEXT NOT NULL DEFAULT ''
               )""",
            # -- small key/value store (settings, session epoch) ----------------
            """CREATE TABLE IF NOT EXISTS settings (
                   key   TEXT PRIMARY KEY,
                   value TEXT NOT NULL
               )""",
        ],
    ),
    (
        2,
        [
            # -- the board gains three reversible states ------------------------
            # Nothing here deletes: a hidden message can be restored, a pinned
            # warning stays on top, and "sorted out" leaves the answer in place
            # for the next person who has the same problem.
            "ALTER TABLE notes ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE notes ADD COLUMN resolved INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE notes ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0",
            # -- a shared file keeps its own place on disk ----------------------
            # 'name' used to be both the label and the file name, which is how
            # three uploads in one minute overwrote each other.  'stored' is the
            # name on disk (random, unique); 'display' is only what a person
            # reads; 'name' stays for older rows and older code.
            "ALTER TABLE files ADD COLUMN stored TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE files ADD COLUMN display TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE files ADD COLUMN sha256 TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE files ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0",
            "UPDATE files SET stored = name WHERE stored = ''",
            "UPDATE files SET display = name WHERE display = ''",
            "CREATE INDEX IF NOT EXISTS notes_order ON notes(hidden, resolved, pinned, id)",
            "CREATE INDEX IF NOT EXISTS files_order ON files(hidden, id)",
            # -- the owner's switches, open out of the box ---------------------
            # A box that has never been set up must still be usable by the
            # people standing next to it; closing the doors is a deliberate act.
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('public_board_post', '1')",
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('public_uploads', '1')",
        ],
    ),
    (
        3,
        [
            # -- the game shelf -------------------------------------------------
            # A game is one file with a fingerprint.  A new version is a new
            # game_revisions row, never an edit to the old one, so an old save
            # can still say which version of the game it belongs to.
            """CREATE TABLE IF NOT EXISTS games (
                   id            INTEGER PRIMARY KEY AUTOINCREMENT,
                   slug          TEXT NOT NULL UNIQUE,
                   title         TEXT NOT NULL,
                   system        TEXT NOT NULL,
                   core          TEXT NOT NULL,
                   filename      TEXT NOT NULL,
                   size          INTEGER NOT NULL DEFAULT 0,
                   sha256        TEXT NOT NULL,
                   revision      INTEGER NOT NULL DEFAULT 1,
                   license       TEXT NOT NULL DEFAULT '',
                   license_evidence TEXT NOT NULL DEFAULT '',
                   author        TEXT NOT NULL DEFAULT '',
                   homepage      TEXT NOT NULL DEFAULT '',
                   description   TEXT NOT NULL DEFAULT '',
                   cover         TEXT NOT NULL DEFAULT '',
                   provider      TEXT NOT NULL DEFAULT '',
                   entry_id      TEXT NOT NULL DEFAULT '',
                   installed_at  TEXT NOT NULL,
                   updated_at    TEXT NOT NULL,
                   published     INTEGER NOT NULL DEFAULT 1,
                   hidden        INTEGER NOT NULL DEFAULT 0
               )""",
            """CREATE TABLE IF NOT EXISTS game_revisions (
                   id          INTEGER PRIMARY KEY AUTOINCREMENT,
                   game_id     INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
                   revision    INTEGER NOT NULL,
                   filename    TEXT NOT NULL,
                   size        INTEGER NOT NULL DEFAULT 0,
                   sha256      TEXT NOT NULL,
                   installed_at TEXT NOT NULL,
                   note        TEXT NOT NULL DEFAULT '',
                   UNIQUE (game_id, revision)
               )""",
            # -- where a game came from, and what may still be offered ----------
            """CREATE TABLE IF NOT EXISTS providers (
                   id           TEXT PRIMARY KEY,
                   label        TEXT NOT NULL,
                   kind         TEXT NOT NULL DEFAULT 'gbdev-database',
                   home         TEXT NOT NULL DEFAULT '',
                   enabled      INTEGER NOT NULL DEFAULT 1,
                   last_run_at  TEXT NOT NULL DEFAULT '',
                   last_result  TEXT NOT NULL DEFAULT ''
               )""",
            """CREATE TABLE IF NOT EXISTS acquisition_candidates (
                   id           INTEGER PRIMARY KEY AUTOINCREMENT,
                   provider     TEXT NOT NULL,
                   entry_id     TEXT NOT NULL,
                   title        TEXT NOT NULL,
                   system       TEXT NOT NULL,
                   filename     TEXT NOT NULL,
                   download_url TEXT NOT NULL,
                   license      TEXT NOT NULL DEFAULT '',
                   license_evidence TEXT NOT NULL DEFAULT '',
                   author       TEXT NOT NULL DEFAULT '',
                   homepage     TEXT NOT NULL DEFAULT '',
                   sha256       TEXT NOT NULL DEFAULT '',
                   size         INTEGER NOT NULL DEFAULT 0,
                   status       TEXT NOT NULL DEFAULT 'pending',
                   reason       TEXT NOT NULL DEFAULT '',
                   game_id      INTEGER REFERENCES games(id) ON DELETE SET NULL,
                   created_at   TEXT NOT NULL,
                   updated_at   TEXT NOT NULL,
                   UNIQUE (provider, entry_id)
               )""",
            """CREATE TABLE IF NOT EXISTS acquisition_jobs (
                   id          INTEGER PRIMARY KEY AUTOINCREMENT,
                   kind        TEXT NOT NULL,
                   status      TEXT NOT NULL DEFAULT 'running',
                   started_at  TEXT NOT NULL,
                   finished_at TEXT NOT NULL DEFAULT '',
                   detail      TEXT NOT NULL DEFAULT ''
               )""",
            # -- player names: real accounts, never administrators --------------
            # The two session tables are separate on purpose.  Signing out of
            # the owner's area must not end a child's game, and a player name
            # can never reach /admin.
            """CREATE TABLE IF NOT EXISTS players (
                   id            INTEGER PRIMARY KEY AUTOINCREMENT,
                   username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
                   display_name  TEXT NOT NULL DEFAULT '',
                   pass_hash     TEXT NOT NULL,
                   enabled       INTEGER NOT NULL DEFAULT 1,
                   created_at    TEXT NOT NULL,
                   last_login_at TEXT NOT NULL DEFAULT ''
               )""",
            """CREATE TABLE IF NOT EXISTS player_sessions (
                   id         INTEGER PRIMARY KEY AUTOINCREMENT,
                   player_id  INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
                   token_hash TEXT NOT NULL UNIQUE,
                   csrf       TEXT NOT NULL,
                   created_at TEXT NOT NULL,
                   last_seen  TEXT NOT NULL,
                   ip         TEXT NOT NULL DEFAULT ''
               )""",
            """CREATE TABLE IF NOT EXISTS player_saves (
                   id          INTEGER PRIMARY KEY AUTOINCREMENT,
                   player_id   INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
                   game_id     INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
                   kind        TEXT NOT NULL,
                   slot        INTEGER NOT NULL DEFAULT 0,
                   filename    TEXT NOT NULL,
                   size        INTEGER NOT NULL DEFAULT 0,
                   sha256      TEXT NOT NULL,
                   revision    INTEGER NOT NULL DEFAULT 1,
                   updated_at  TEXT NOT NULL,
                   UNIQUE (player_id, game_id, kind, slot)
               )""",
            """CREATE TABLE IF NOT EXISTS arcade_login_attempts (
                   id      INTEGER PRIMARY KEY AUTOINCREMENT,
                   ts      TEXT NOT NULL,
                   source  TEXT NOT NULL DEFAULT '',
                   target  TEXT NOT NULL DEFAULT '',
                   ok      INTEGER NOT NULL
               )""",
            "CREATE INDEX IF NOT EXISTS arcade_login_ts ON arcade_login_attempts(ts)",
            "CREATE INDEX IF NOT EXISTS games_order ON games(hidden, title)",
            "CREATE INDEX IF NOT EXISTS saves_owner ON player_saves(player_id, game_id)",
            # Player names are open on a fresh box, like the board and the File
            # Box.  Closing them is a deliberate act by the owner.
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('arcade_players', '1')",
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('arcade_auto_install', '0')",
        ],
    ),
    (
        4,
        [
            # -- why a game may be here, in words the owner can check -----------
            # A licence name is not evidence that the file may be copied.  The
            # language is part of eligibility too: the household reads English,
            # and a Japanese-only game must not arrive silently.  Both facts are
            # recorded beside the game rather than inferred at render time.
            "ALTER TABLE games ADD COLUMN language TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE games ADD COLUMN language_evidence TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE games ADD COLUMN rights_evidence TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE games ADD COLUMN provenance TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE games ADD COLUMN release TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE games ADD COLUMN notices TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE acquisition_candidates ADD COLUMN language TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE acquisition_candidates ADD COLUMN language_evidence TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE acquisition_candidates ADD COLUMN rights_evidence TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE acquisition_candidates ADD COLUMN provenance TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE acquisition_candidates ADD COLUMN release TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE acquisition_candidates ADD COLUMN notices TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE game_revisions ADD COLUMN release TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE game_revisions ADD COLUMN provenance TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE game_revisions ADD COLUMN notices TEXT NOT NULL DEFAULT ''",
            # -- a player's choices belong to the pair, not to one browser ------
            # These are preferences, not saves: they survive signing in on
            # another device and are cheap enough to write on every toggle.
            """CREATE TABLE IF NOT EXISTS player_game_prefs (
                   player_id  INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
                   game_id    INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
                   autosave   INTEGER NOT NULL DEFAULT 0,
                   favourite  INTEGER NOT NULL DEFAULT 0,
                   last_played TEXT NOT NULL DEFAULT '',
                   updated_at TEXT NOT NULL,
                   PRIMARY KEY (player_id, game_id)
               )""",
            "CREATE INDEX IF NOT EXISTS prefs_favourite ON player_game_prefs(player_id, favourite, updated_at)",
            # Existing rows keep their exact behaviour: unknown language, and a
            # licence that was recorded but never proved, read as 'needs review'.
            "UPDATE acquisition_candidates SET status = 'review' "
            " WHERE status = 'pending' AND (language = '' OR rights_evidence = '')",
        ],
    ),
    (
        5,
        [
            # -- what the file *is*, from the box's own offline catalogue ------
            # A cartridge's name, publisher and year are facts about the bytes,
            # not about the page: they are read from the fingerprints and stored
            # beside the game so a card with no internet can still say what it
            # is holding.  Every column defaults to '' so a row written before
            # this migration keeps reading exactly as it did (the ``stored``
            # rule of §8.7, one table over).
            "ALTER TABLE games ADD COLUMN crc32 TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE games ADD COLUMN md5 TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE games ADD COLUMN sha1 TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE games ADD COLUMN rom_title TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE games ADD COLUMN rom_region TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE games ADD COLUMN rom_year TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE games ADD COLUMN rom_developer TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE games ADD COLUMN rom_publisher TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE games ADD COLUMN rom_genre TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE games ADD COLUMN rom_players TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE games ADD COLUMN rom_franchise TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE games ADD COLUMN rom_esrb TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE games ADD COLUMN rom_source TEXT NOT NULL DEFAULT ''",
            # sha1 / md5 / crc32 when a fingerprint chose this row, and 'name'
            # when nobody had listed the file and its file name had to do.
            "ALTER TABLE games ADD COLUMN rom_confidence TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE game_revisions ADD COLUMN crc32 TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE game_revisions ADD COLUMN md5 TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE game_revisions ADD COLUMN sha1 TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE game_revisions ADD COLUMN rom_title TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE game_revisions ADD COLUMN rom_confidence TEXT NOT NULL DEFAULT ''",
            # One file, one row: the same bytes offered twice must be found
            # without reading every game's name.
            "CREATE INDEX IF NOT EXISTS games_sha1 ON games(sha1) WHERE sha1 <> ''",
            "CREATE INDEX IF NOT EXISTS games_crc32 ON games(crc32) WHERE crc32 <> ''",
        ],
    ),
    (
        6,
        [
            # A save used to be one file per slot, overwritten in place before the
            # row was committed: a cut between the two left new bytes under the old
            # row's fingerprint, and every later read was refused.  A save is now
            # stored as an immutable, content-addressed file and the row names it,
            # so the old bytes stay readable until the new row is on the card
            # (see ark_arcade.player_save_put).  An empty ``blob`` means the row
            # predates this layout: the file is still at its old name.
            "ALTER TABLE player_saves ADD COLUMN blob TEXT NOT NULL DEFAULT ''",
        ],
    ),
    (
        7,
        [
            # -- the media shelf ------------------------------------------------
            # A video is one row of facts about one stored file.  ``file_id`` and
            # ``stored`` name the bytes in ``drop/files`` — the shelf never keeps a
            # second copy of an original — and ``play_stored`` names the one
            # derivative (a remux or a conversion) the player may actually serve.
            # ``duration`` is nullable on purpose: a file whose length nobody could
            # read is *unknown*, never zero (the `None` rule of §8.8).
            """CREATE TABLE IF NOT EXISTS media (
                   id          INTEGER PRIMARY KEY AUTOINCREMENT,
                   ts          TEXT NOT NULL,
                   file_id     INTEGER NOT NULL DEFAULT 0,
                   title       TEXT NOT NULL DEFAULT '',
                   filename    TEXT NOT NULL DEFAULT '',
                   stored      TEXT NOT NULL DEFAULT '',
                   size        INTEGER NOT NULL DEFAULT 0,
                   sha256      TEXT NOT NULL DEFAULT '',
                   state       TEXT NOT NULL DEFAULT 'pending',
                   category    TEXT NOT NULL DEFAULT '',
                   container   TEXT NOT NULL DEFAULT '',
                   vcodec      TEXT NOT NULL DEFAULT '',
                   acodec      TEXT NOT NULL DEFAULT '',
                   width       INTEGER NOT NULL DEFAULT 0,
                   height      INTEGER NOT NULL DEFAULT 0,
                   duration    REAL,
                   rotation    INTEGER NOT NULL DEFAULT 0,
                   plan        TEXT NOT NULL DEFAULT 'unknown',
                   play_stored TEXT NOT NULL DEFAULT '',
                   play_size   INTEGER NOT NULL DEFAULT 0,
                   poster      TEXT NOT NULL DEFAULT '',
                   poster_at   REAL,
                   probe       TEXT NOT NULL DEFAULT '',
                   added_by    TEXT NOT NULL DEFAULT '',
                   note        TEXT NOT NULL DEFAULT ''
               )""",
            "CREATE INDEX IF NOT EXISTS media_state ON media(state, ts)",
            "CREATE INDEX IF NOT EXISTS media_file ON media(file_id)",
            # -- work the box still owes the shelf ----------------------------
            # A job is durable: it survives a restart, it says how many times it
            # has honestly tried, and the worker claims one row at a time so two
            # workers can never process the same film.
            """CREATE TABLE IF NOT EXISTS media_jobs (
                   id        INTEGER PRIMARY KEY AUTOINCREMENT,
                   media_id  INTEGER NOT NULL,
                   kind      TEXT NOT NULL,
                   state     TEXT NOT NULL DEFAULT 'queued',
                   attempts  INTEGER NOT NULL DEFAULT 0,
                   ts        TEXT NOT NULL,
                   started   TEXT NOT NULL DEFAULT '',
                   finished  TEXT NOT NULL DEFAULT '',
                   heartbeat REAL NOT NULL DEFAULT 0,
                   pid       INTEGER NOT NULL DEFAULT 0,
                   detail    TEXT NOT NULL DEFAULT '',
                   progress  REAL NOT NULL DEFAULT 0
               )""",
            "CREATE INDEX IF NOT EXISTS media_jobs_state ON media_jobs(state, id)",
            "CREATE INDEX IF NOT EXISTS media_jobs_media ON media_jobs(media_id)",
            # -- captions that travel with a film -----------------------------
            """CREATE TABLE IF NOT EXISTS media_subs (
                   id       INTEGER PRIMARY KEY AUTOINCREMENT,
                   media_id INTEGER NOT NULL,
                   lang     TEXT NOT NULL DEFAULT 'en',
                   label    TEXT NOT NULL DEFAULT '',
                   stored   TEXT NOT NULL DEFAULT '',
                   size     INTEGER NOT NULL DEFAULT 0,
                   source   TEXT NOT NULL DEFAULT ''
               )""",
            "CREATE INDEX IF NOT EXISTS media_subs_media ON media_subs(media_id)",
            # -- space promised to a job that has not finished writing yet ----
            # One row per reservation, shared by every process.  ``written`` is
            # subtracted from ``bytes`` when admission asks how much is still
            # owed, so a byte is never counted twice: statvfs already sees the
            # bytes on the card.
            """CREATE TABLE IF NOT EXISTS space_reservations (
                   id        INTEGER PRIMARY KEY AUTOINCREMENT,
                   ts        REAL NOT NULL,
                   heartbeat REAL NOT NULL,
                   owner     TEXT NOT NULL,
                   device    INTEGER NOT NULL DEFAULT 0,
                   path      TEXT NOT NULL DEFAULT '',
                   label     TEXT NOT NULL DEFAULT '',
                   bytes     INTEGER NOT NULL DEFAULT 0,
                   written   INTEGER NOT NULL DEFAULT 0
               )""",
            "CREATE INDEX IF NOT EXISTS space_reservations_device"
            " ON space_reservations(device)",
        ],
    ),
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class _GatedConnection(sqlite3.Connection):
    """A connection whose lifetime *is* the shared maintenance hold.

    The hold is taken before SQLite opens the file (``connect`` below) and given
    back in ``close``.  A caller that forgets to close therefore keeps the box
    from replacing the records — which is the honest answer, and is why the
    hold is bound to the handle rather than to a function call.  ``__del__`` is
    the belt: an abandoned handle releases the gate when it is collected.
    """

    def close(self) -> None:
        try:
            super().close()
        finally:
            self._release_gate()

    def _release_gate(self) -> None:
        ticket = getattr(self, "_ark_gate", None)
        if ticket is not None:
            self._ark_gate = None
            ticket.release()

    def __del__(self):  # pragma: no cover - the interpreter decides when this runs
        try:
            self._release_gate()
        except Exception:
            pass


def connect() -> sqlite3.Connection:
    """A fresh connection. Callers must close it (``tx`` and ``read`` do).

    The maintenance gate is taken **before** the file is opened: a replacement
    can never land between "the gate is clear" and "this handle exists".  A box
    that is putting its records back answers ``ark_maintenance.Busy`` after a
    short wait, rather than hanging a page or reading a file mid-swap.
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    ticket = ark_maintenance.admit(DB_PATH)
    try:
        if ark_db_replace is not None and ark_db_replace.pending(DB_PATH):
            raise ark_maintenance.Busy(
                "The box is finishing putting its records back. Try again in a "
                "moment.")
        conn = sqlite3.connect(str(DB_PATH), timeout=15, isolation_level=None,
                               factory=_GatedConnection)
    except BaseException:
        ticket.release()
        raise
    conn._ark_gate = ticket
    try:
        conn.row_factory = sqlite3.Row
        # WAL is a property of the *file*, not of this connection, so asking for it
        # again is a write that must wait for whatever writer is busy — up to the
        # connect timeout on every fresh connection.  A reader opened while a film is
        # being published would therefore stall for seconds before it could answer a
        # question about free space.  Ask first; write only when the file is not in
        # WAL yet (a brand-new database, or a restore that replaced it).
        if str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower() != "wal":
            conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=8000")
        conn.execute("PRAGMA foreign_keys=ON")
    except BaseException:
        conn.close()                    # releases the gate with the handle
        raise
    # ``synchronous=FULL`` on **every** connection, not just the one the repair
    # pass opened.  With ``NORMAL`` SQLite reports a commit once the bytes are in
    # the kernel's cache, so a power cut can lose the last thing the box told a
    # person it had kept — a note that was posted, a save that was confirmed.  The
    # cost is one fsync per write transaction on this card; the portal's writes are
    # a handful a minute, and correctness here is the whole point of the box.
    # ``ark_self_heal.harden()`` sets the same pair on the databases it owns, but a
    # pragma is per connection: this line is what the living box actually uses.
    try:
        conn.execute("PRAGMA synchronous=FULL")
    except BaseException:
        conn.close()
        raise
    return conn


def defer_until_commit(conn: sqlite3.Connection, action) -> bool:
    """Run ``action`` only after the transaction this connection is in commits.

    A save is a row *and* a blob.  The row update may be nested inside a caller's
    transaction (an import, a route that writes more than one thing), so removing
    the blob it replaced inside that block would take the bytes away before the
    row that stopped naming them is durable — and a capture between the two sees
    the old row naming a file that is gone.  Actions registered here run after the
    outermost ``COMMIT``, and are discarded on ``ROLLBACK``.  An action that
    raises is ignored: a leftover blob is collected later, a lost one is not.

    Answers ``True`` when the action was registered; ``False`` when this
    connection is not in a transaction (the caller may act now); ``None`` when it
    is in one but cannot hold the list (the caller must leave the bytes alone).
    """
    if not conn.in_transaction:
        return False
    actions = getattr(conn, "_ark_after_commit", None)
    if actions is None:
        actions = []
        try:
            conn._ark_after_commit = actions
        except AttributeError:               # pragma: no cover - exotic connection
            return None
    actions.append(action)
    return True


def _run_after_commit(conn: sqlite3.Connection) -> None:
    actions = getattr(conn, "_ark_after_commit", None)
    if not actions:
        return
    try:
        conn._ark_after_commit = []
    except AttributeError:                   # pragma: no cover - exotic connection
        pass
    for action in actions:
        try:
            action()
        except Exception:                    # noqa: BLE001 — cleanup never fails a commit
            pass


def _drop_after_commit(conn: sqlite3.Connection) -> None:
    try:
        conn._ark_after_commit = []
    except AttributeError:                   # pragma: no cover - exotic connection
        pass


@contextmanager
def tx(immediate: bool = True, conn: sqlite3.Connection | None = None):
    """A write transaction that always closes the connections it opened.

    ``immediate=True`` takes the write lock up front: two concurrent requests
    can then not both decide that they are allowed to disable the last
    remaining administrator.

    ``conn`` may be a connection the caller already holds.  When that connection
    is already inside a transaction this is a nested call and does nothing but
    yield it — one writer, one commit, which is what the game shelf needs when a
    save row and a file must be published together.  A caller that passes a
    plain read connection gets a real transaction on it, still closed by this
    function only if this function opened it.

    The outermost commit also runs any after-commit actions registered with
    :func:`defer_until_commit` — file cleanup that must not happen before the row
    that made the file unreferenced is durable.  A rollback discards them.
    """
    if isinstance(immediate, sqlite3.Connection):   # tx(conn) — the older call shape
        conn, immediate = immediate, True
    own = conn is None
    if own:
        conn = connect()
    started = not conn.in_transaction
    try:
        if started:
            conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
        yield conn
        if started:
            conn.execute("COMMIT")
            # The row is durable now; only here may a save's replaced blob (or a
            # file a delete made unreferenced) be removed.
            _run_after_commit(conn)
    except BaseException:
        if started:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass  # nothing was open any more; the connection is closed below
            # The row changes were undone; the files they would have freed are
            # still named by the records that survived.
            _drop_after_commit(conn)
        raise
    finally:
        if own:
            conn.close()


@contextmanager
def read():
    """A read-only-ish connection, closed on the way out."""
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------------
#  settings
# ---------------------------------------------------------------------------
def get_setting(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def session_epoch(conn: sqlite3.Connection) -> int:
    return int(get_setting(conn, "session_epoch", "1") or "1")


def bump_session_epoch(conn: sqlite3.Connection) -> int:
    """Invalidate every session issued before this boot.

    Sessions do not survive a service restart: after a reboot or an upgrade a
    person signs in again.  That is the honest behaviour when the wall clock
    may have jumped, and it means a stolen cookie cannot outlive a restart.
    """
    nxt = session_epoch(conn) + 1
    set_setting(conn, "session_epoch", str(nxt))
    conn.execute("DELETE FROM admin_sessions")
    # A player's name and save files are not administrator credentials, but the
    # session itself is still a bearer token.  It does not survive a restart
    # either; the save files do, which is the whole point of having a name.
    conn.execute("DELETE FROM player_sessions")
    return nxt


# ---------------------------------------------------------------------------
#  audit
# ---------------------------------------------------------------------------
def audit(conn, *, action: str, actor_id=None, actor_name: str = "",
          target: str = "", result: str = "ok", detail: str = "") -> None:
    """Record what an administrator did — never a password or a token."""
    conn.execute(
        "INSERT INTO audit_log (ts, actor_id, actor_name, action, target, result, detail) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (now_iso(), actor_id, actor_name[:80], action[:60], target[:120],
         result[:20], detail[:300]),
    )


# ---------------------------------------------------------------------------
#  install marker
# ---------------------------------------------------------------------------
def marker_present() -> bool:
    return MARKER_PATH.exists()


def write_marker() -> None:
    """Atomically prove 'this box has been configured' — outside the database."""
    MARKER_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(MARKER_PATH.parent), prefix=".ark-installed-")
    try:
        os.write(fd, b"configured\n")
        os.fsync(fd)
        os.close(fd)
        os.chmod(tmp, 0o600)
        os.replace(tmp, MARKER_PATH)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass  # already renamed into place


# ---------------------------------------------------------------------------
#  migrate
# ---------------------------------------------------------------------------
def migrate(conn: sqlite3.Connection | None = None) -> int:
    """Bring the database up to SCHEMA_VERSION. Returns the resulting version."""
    if conn is not None:
        return _migrate(conn)
    with tx() as own:
        return _migrate(own)


def _migrate(conn: sqlite3.Connection) -> int:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS schema_version (
               version    INTEGER PRIMARY KEY,
               applied_at TEXT NOT NULL
           )"""
    )
    row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    current = int(row[0] or 0)
    for version, statements in MIGRATIONS:
        if version <= current:
            continue
        for sql in statements:
            conn.execute(sql)
        conn.execute("INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                     (version, now_iso()))
        current = version
    return current
