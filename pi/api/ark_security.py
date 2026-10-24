"""Project ARK — password hashing, sessions, CSRF and login throttling.

Nothing in here invents cryptography.  Passwords are hashed with **scrypt**
from the Python standard library (OpenSSL's implementation, which is what ships
on Raspberry Pi OS); sessions are opaque 256-bit random tokens of which only a
SHA-256 digest is stored; comparison is constant-time.

The parameters below are tuned for a Pi 4: n=2^15, r=8, p=1 needs 32 MiB and
roughly a fifth of a second per hash.  That is slow enough to make guessing
expensive and fast enough that a person signing in does not notice.  The
stored hash records its own parameters, so they can be raised later without
invalidating existing accounts.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from dataclasses import dataclass

# ---------------------------------------------------------------------------
#  constants
# ---------------------------------------------------------------------------
BOOTSTRAP_USERNAME = "admin"
BOOTSTRAP_PASSWORD = "ChangeMe-ARK!"

PASSWORD_MIN = 12
# A bounded maximum, documented in the UI: we reject rather than truncate, so a
# passphrase is never silently cut short.
PASSWORD_MAX = 1024

SCRYPT_N = 1 << 15
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32
SCRYPT_MAXMEM = 96 * 1024 * 1024
SCRYPT_PREFIX = "scrypt"

IDLE_TIMEOUT = 30 * 60          # 30 minutes without a request
ABSOLUTE_TIMEOUT = 8 * 3600     # 8 hours from sign-in, however busy
PREAUTH_LIFETIME = 10 * 60      # the login form's own short-lived session
CLOCK_SLACK = 120               # tolerate small NTP corrections, not jumps

COOKIE_NAME = "ark_admin"
PREAUTH_COOKIE = "ark_admin_form"

FAIL_WINDOW = 15 * 60
FREE_ATTEMPTS = 5
BACKOFF_BASE = 60
BACKOFF_MAX = 15 * 60

TEMP_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"

# Cached lazily: None = not looked for yet, False = unavailable, else the class.
_FALLBACK_SCRYPT = None


class CryptUnavailable(RuntimeError):
    """Raised when the platform has no scrypt (a broken/odd Python build)."""


class PolicyError(ValueError):
    """A new password did not meet the documented policy."""


# ---------------------------------------------------------------------------
#  password hashing
# ---------------------------------------------------------------------------
def _scrypt(password: str, salt: bytes, n: int, r: int, p: int, dklen: int) -> bytes:
    """scrypt, from the standard library where it exists.

    Raspberry Pi OS ships a Python linked against OpenSSL 3, so the appliance
    always takes the first branch.  The second branch exists only because Apple's
    command-line Python links LibreSSL, which has no scrypt — without it the
    test suite could not run on the developer's Mac at all.  Both produce the
    same bytes for the same inputs (checked against the Pi; see the test suite).
    """
    if hasattr(hashlib, "scrypt"):
        return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p,
                              dklen=dklen, maxmem=SCRYPT_MAXMEM)
    global _FALLBACK_SCRYPT
    if _FALLBACK_SCRYPT is None:
        try:
            from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
        except ImportError:
            _FALLBACK_SCRYPT = False
        else:
            _FALLBACK_SCRYPT = Scrypt
    if _FALLBACK_SCRYPT is False:
        raise CryptUnavailable(
            "this Python has no hashlib.scrypt and no cryptography package — "
            "ARK will not fall back to a weaker scheme"
        )
    return _FALLBACK_SCRYPT(salt=salt, length=dklen, n=n, r=r, p=p).derive(
        password.encode("utf-8"))


def hash_password(password: str) -> str:
    """``scrypt$n$r$p$salt$hash`` — self-describing, so parameters can move."""
    if not isinstance(password, str) or not password:
        raise PolicyError("A password is required.")
    salt = os.urandom(16)
    dk = _scrypt(password, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P, SCRYPT_DKLEN)
    return "$".join((
        SCRYPT_PREFIX, str(SCRYPT_N), str(SCRYPT_R), str(SCRYPT_P),
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(dk).decode("ascii"),
    ))


def verify_password(password: str, stored: str) -> bool:
    """Constant-time check. A malformed or oversized input is simply 'no'."""
    if not isinstance(password, str) or not stored or not isinstance(stored, str):
        return False
    if len(password) > PASSWORD_MAX:
        return False
    try:
        scheme, n_s, r_s, p_s, salt_b64, hash_b64 = stored.split("$")
        if scheme != SCRYPT_PREFIX:
            return False
        n, r, p = int(n_s), int(r_s), int(p_s)
        # Bound the work a corrupt or hostile stored value could ask for.
        if not (1 << 12) <= n <= (1 << 17) or not 1 <= r <= 16 or not 1 <= p <= 4:
            return False
        salt = base64.b64decode(salt_b64, validate=True)
        expected = base64.b64decode(hash_b64, validate=True)
    except (ValueError, TypeError):
        return False
    try:
        actual = _scrypt(password, salt, n, r, p, len(expected))
    except (CryptUnavailable, ValueError):
        return False
    return hmac.compare_digest(actual, expected)


def needs_rehash(stored: str) -> bool:
    parts = (stored or "").split("$")
    if len(parts) != 6 or parts[0] != SCRYPT_PREFIX:
        return True
    return parts[1:4] != [str(SCRYPT_N), str(SCRYPT_R), str(SCRYPT_P)]


def check_new_password(password: str, *, username: str = "",
                       current_hash: str = "") -> None:
    """The documented policy: length only, no composition zoo.

    Raises PolicyError with a sentence a non-technical owner can act on.
    """
    if not isinstance(password, str):
        raise PolicyError("A password is required.")
    if len(password) > PASSWORD_MAX:
        raise PolicyError(f"That is longer than {PASSWORD_MAX} characters — "
                          "please choose something shorter.")
    if password != password.strip():
        raise PolicyError("Please do not begin or end the password with a space.")
    if len(password) < PASSWORD_MIN:
        raise PolicyError(f"Please use at least {PASSWORD_MIN} characters. "
                          "A short sentence is easy to remember and hard to guess.")
    if password == BOOTSTRAP_PASSWORD:
        raise PolicyError("That is the temporary password everyone knows. "
                          "Please choose your own.")
    if username and password.casefold() == username.casefold():
        raise PolicyError("The password must not be the same as the username.")
    if current_hash and verify_password(password, current_hash):
        raise PolicyError("That is your current password — please pick a new one.")


def generate_temp_password(length: int = 20) -> str:
    """A one-time password with no look-alike characters (no 0/O, 1/l/I)."""
    return "".join(secrets.choice(TEMP_ALPHABET) for _ in range(length))


# ---------------------------------------------------------------------------
#  tokens
# ---------------------------------------------------------------------------
def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8", "replace")).hexdigest()


def new_csrf() -> str:
    return secrets.token_urlsafe(24)


def csrf_ok(expected: str, supplied: str) -> bool:
    if not expected or not supplied or len(supplied) > 200:
        return False
    return hmac.compare_digest(expected, supplied)


def normalise_username(raw: str) -> str:
    """Case-folded, trimmed — the database also enforces uniqueness this way."""
    return (raw or "").strip().casefold()


def username_problem(raw: str) -> str:
    name = normalise_username(raw)
    if len(name) < 3:
        return "Use at least 3 characters."
    if len(name) > 32:
        return "Use at most 32 characters."
    allowed = set("abcdefghijklmnopqrstuvwxyz0123456789._-")
    if name[0] in "._-" or not set(name) <= allowed:
        return "Use letters, numbers, dot, dash or underscore — starting with a letter or number."
    return ""


# ---------------------------------------------------------------------------
#  sessions
# ---------------------------------------------------------------------------
@dataclass
class IssuedSession:
    token: str
    csrf: str
    sid: int


def create_session(conn: sqlite3.Connection, *, admin_id: int | None, kind: str,
                   epoch: int, ip: str = "", lifetime: int | None = None) -> IssuedSession:
    """Mint an opaque token; only its digest is written down."""
    token = new_token()
    csrf = new_csrf()
    now = time.time()
    ttl = lifetime if lifetime is not None else (ABSOLUTE_TIMEOUT if kind == "admin"
                                                 else PREAUTH_LIFETIME)
    cur = conn.execute(
        "INSERT INTO admin_sessions (admin_id, token_sha, csrf, kind, created, last_seen,"
        " abs_expires, epoch, ip) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (admin_id, token_digest(token), csrf, kind, now, now, now + ttl, epoch, ip[:64]),
    )
    return IssuedSession(token=token, csrf=csrf, sid=int(cur.lastrowid))


def load_session(conn: sqlite3.Connection, token: str, epoch: int) -> sqlite3.Row | None:
    """Return the live session row, or None. Expired/stale rows are removed.

    Runs on a writable connection: expiry is enforced by deleting, so a token
    that is past its life never lingers as a usable key.
    """
    if not token or len(token) > 200:
        return None
    row = conn.execute("SELECT * FROM admin_sessions WHERE token_sha = ?",
                       (token_digest(token),)).fetchone()
    if row is None:
        return None
    now = time.time()
    stale = (
        int(row["epoch"]) != int(epoch)                  # issued before this boot
        or now < float(row["created"]) - CLOCK_SLACK      # clock moved backwards
        or now > float(row["abs_expires"])                # 8-hour ceiling
        or now > float(row["last_seen"]) + IDLE_TIMEOUT   # 30-minute idle
    )
    if stale:
        conn.execute("DELETE FROM admin_sessions WHERE id = ?", (row["id"],))
        return None
    return row


def touch_session(conn: sqlite3.Connection, sid: int, last_seen: float) -> None:
    now = time.time()
    if now - float(last_seen) < 60:      # do not write on every single request
        return
    conn.execute("UPDATE admin_sessions SET last_seen = ? WHERE id = ?", (now, sid))


def drop_session(conn: sqlite3.Connection, sid: int) -> None:
    conn.execute("DELETE FROM admin_sessions WHERE id = ?", (sid,))


def drop_admin_sessions(conn: sqlite3.Connection, admin_id: int, keep: int | None = None) -> int:
    if keep is None:
        cur = conn.execute("DELETE FROM admin_sessions WHERE admin_id = ?", (admin_id,))
    else:
        cur = conn.execute("DELETE FROM admin_sessions WHERE admin_id = ? AND id <> ?",
                           (admin_id, keep))
    return cur.rowcount or 0


# ---------------------------------------------------------------------------
#  login throttling
# ---------------------------------------------------------------------------
def login_backoff(conn: sqlite3.Connection, username: str, source: str,
                  now: float | None = None) -> int:
    """Seconds this attempt must wait. 0 means 'go ahead'.

    Counted per account *and* per source, so neither one noisy client nor one
    targeted account can be hammered — and a successful sign-in clears the
    count, so nobody stays locked out for long.
    """
    now = time.time() if now is None else now
    name = normalise_username(username)
    rows = conn.execute(
        "SELECT ts, username, source FROM login_attempts WHERE ok = 0 AND ts >= ?",
        (now - FAIL_WINDOW,),
    ).fetchall()
    per_user = [r for r in rows if r["username"] == name]
    per_src = [r for r in rows if r["source"] == source]
    worst = max(len(per_user), len(per_src))
    if worst < FREE_ATTEMPTS:
        return 0
    delay = min(BACKOFF_MAX, BACKOFF_BASE * (2 ** (worst - FREE_ATTEMPTS)))
    newest = max((float(r["ts"]) for r in (per_user or per_src)), default=0.0)
    return max(0, int(round(newest + delay - now)))


def record_login(conn: sqlite3.Connection, username: str, source: str, ok: bool,
                 now: float | None = None) -> None:
    now = time.time() if now is None else now
    conn.execute("INSERT INTO login_attempts (ts, username, source, ok) VALUES (?, ?, ?, ?)",
                 (now, normalise_username(username), source[:64], 1 if ok else 0))
    if ok:
        conn.execute("DELETE FROM login_attempts WHERE ok = 0 AND username = ?",
                     (normalise_username(username),))
    conn.execute("DELETE FROM login_attempts WHERE ts < ?", (now - 7 * 86400,))


def peer_is_trusted(host: str) -> bool:
    """Is this request coming from the local reverse proxy (not a stranger)?"""
    allowed = os.environ.get("ARK_TRUSTED_PROXIES", "127.0.0.1,::1")
    return (host or "") in {h.strip() for h in allowed.split(",") if h.strip()}
