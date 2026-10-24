"""Schema, upgrades and the hashing contract with the appliance."""

from __future__ import annotations

import sqlite3

# Produced by Python 3.13 / OpenSSL on the actual Raspberry Pi, for the
# passphrase and zero salt below. If the developer machine's fallback scrypt
# ever disagreed with the Pi's hashlib, this test would fail — which is the
# point: a hash written on one must verify on the other.
PI_VECTOR = "scrypt$32768$8$1$AAAAAAAAAAAAAAAAAAAAAA==$HoyWZz2solBDwpngxLUHIRok87diKgir2jP8dWPluL4="
PI_PASSPHRASE = "cross-check passphrase"


def test_hashes_from_the_pi_verify_here(ark):
    assert ark.sec.verify_password(PI_PASSPHRASE, PI_VECTOR)
    assert not ark.sec.verify_password(PI_PASSPHRASE + "x", PI_VECTOR)
    assert not ark.sec.verify_password("", PI_VECTOR)


def test_hash_format_and_round_trip(ark):
    stored = ark.sec.hash_password(PI_PASSPHRASE)
    parts = stored.split("$")
    assert parts[0] == "scrypt"
    assert (parts[1], parts[2], parts[3]) == ("32768", "8", "1")
    assert ark.sec.verify_password(PI_PASSPHRASE, stored)
    assert not ark.sec.needs_rehash(stored)
    # Two hashes of the same password differ: the salt is per-password.
    assert ark.sec.hash_password(PI_PASSPHRASE) != stored


def test_malformed_or_hostile_stored_hashes_are_refused(ark):
    for broken in ("", "plaintext", "scrypt$1$1$1$AAAA$AAAA", "md5$500$8$1$AA$AA",
                   "scrypt$1048576$8$1$AAAAAAAAAAAAAAAAAAAAAA==$AAAA"):
        assert ark.sec.verify_password("anything at all", broken) is False


def test_policy_rejects_oversized_input_instead_of_truncating(ark):
    too_long = "a" * (ark.sec.PASSWORD_MAX + 1)
    try:
        ark.sec.check_new_password(too_long)
    except ark.sec.PolicyError as exc:
        assert "longer" in str(exc)
    else:                                    # pragma: no cover - the assert is the point
        raise AssertionError("oversized password was accepted")
    ark.sec.check_new_password("x" * ark.sec.PASSWORD_MAX)     # the maximum itself is fine


def test_migrations_are_idempotent(ark):
    first = ark.db.migrate()
    second = ark.db.migrate()
    assert first == second == ark.db.SCHEMA_VERSION
    with ark.db.read() as conn:
        assert conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == 1


def test_upgrade_keeps_board_and_files(ark, tmp_path):
    """A database written by the pre-admin release must survive the upgrade."""
    legacy = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(legacy))
    conn.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,"
                 " name TEXT NOT NULL, body TEXT NOT NULL)")
    conn.execute("CREATE TABLE files (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL,"
                 " name TEXT NOT NULL, size INTEGER NOT NULL, note TEXT NOT NULL DEFAULT '')")
    conn.execute("INSERT INTO notes (ts, name, body) VALUES ('2026-01-01T00:00:00+00:00',"
                 " 'Sam', 'the tap leaks')")
    conn.execute("INSERT INTO files (ts, name, size, note) VALUES ('2026-01-01T00:00:00+00:00',"
                 " 'photo.jpg', 1234, 'the tap')")
    conn.commit()
    conn.close()

    conn = sqlite3.connect(str(legacy))
    conn.row_factory = sqlite3.Row
    conn.isolation_level = None
    conn.execute("BEGIN IMMEDIATE")
    assert ark.db._migrate(conn) == ark.db.SCHEMA_VERSION
    conn.execute("COMMIT")

    notes = conn.execute("SELECT name, body FROM notes").fetchall()
    files = conn.execute("SELECT name, size FROM files").fetchall()
    assert [n["body"] for n in notes] == ["the tap leaks"]
    assert [(f["name"], f["size"]) for f in files] == [("photo.jpg", 1234)]
    # ...and the new tables are there, empty, ready for the first administrator.
    assert conn.execute("SELECT COUNT(*) FROM admins").fetchone()[0] == 0
    conn.close()


def test_settings_upsert(ark):
    with ark.db.tx() as conn:
        ark.db.set_setting(conn, "example", "one")
        ark.db.set_setting(conn, "example", "two")
        assert ark.db.get_setting(conn, "example") == "two"
        assert ark.db.get_setting(conn, "missing", "fallback") == "fallback"
