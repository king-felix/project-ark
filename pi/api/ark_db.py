"""Project ARK — the one small database, and the migrations that keep it current.

The board, the file index and (since schema 1) the administrators, their
sessions and the audit trail all live in a single SQLite file.  Everything in
here is deliberately dull:

* short-lived connections, one per request, always closed;
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

ARK_ROOT = Path(os.environ.get("ARK_ROOT", "/srv/ark"))
DB_PATH = Path(os.environ.get("ARK_DB", str(ARK_ROOT / "api" / "data" / "ark.db")))
MARKER_PATH = Path(os.environ.get("ARK_MARKER", str(DB_PATH.parent / "ark.installed")))

SCHEMA_VERSION = 1

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
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    """A fresh connection. Callers must close it (``tx`` and ``read`` do)."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=15, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=8000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


@contextmanager
def tx(immediate: bool = True):
    """A write transaction that always closes the connection.

    ``immediate=True`` takes the write lock up front: two concurrent requests
    can then not both decide that they are allowed to disable the last
    remaining administrator.
    """
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass  # nothing was open any more; the connection is closed below
        raise
    finally:
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
