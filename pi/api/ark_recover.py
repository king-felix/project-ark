"""Offline recovery for an ARK administrator account.

This is the last way in when every password is lost. It runs only through the
root-owned wrapper ``/usr/local/sbin/ark-admin-recover`` (``sudo ark-admin-recover``),
never through the web application: no route imports it, and it is not reachable
from any listener. It resets **one named account** to a freshly generated
temporary password and requires that password to be changed at the next sign-in.

It deliberately has no option to "reset everything" and no universal default.
"""

from __future__ import annotations

import argparse
import sys

import ark_db
import ark_security as sec


def _usage_lines(conn) -> str:
    rows = conn.execute(
        "SELECT username, enabled, must_change, last_login_at FROM admins ORDER BY id"
    ).fetchall()
    if not rows:
        return "   (no administrator accounts exist)"
    out = []
    for row in rows:
        state = "enabled" if row["enabled"] else "switched off"
        if row["must_change"]:
            state += ", must change password"
        out.append(f"   {row['username']:<16} {state:<32} last sign-in: "
                   f"{row['last_login_at'] or 'never'}")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ark-admin-recover",
        description="Give one administrator a temporary password (root only).")
    parser.add_argument("--username", help="the account to recover")
    parser.add_argument("--list", action="store_true", help="list the accounts and stop")
    args = parser.parse_args(argv)

    ark_db.migrate()

    if args.list or not args.username:
        with ark_db.read() as conn:
            print("Administrators on this box:")
            print(_usage_lines(conn))
        if not args.username:
            print("\nChoose one:  sudo ark-admin-recover --username <name>")
        return 0

    name = sec.normalise_username(args.username)
    temp = sec.generate_temp_password()
    with ark_db.tx() as conn:
        row = conn.execute("SELECT * FROM admins WHERE username = ?", (name,)).fetchone()
        if row is None:
            print(f"No administrator called {name!r}. Accounts on this box:", file=sys.stderr)
            print(_usage_lines(conn), file=sys.stderr)
            return 2
        conn.execute(
            "UPDATE admins SET pass_hash = ?, must_change = 1, enabled = 1 WHERE id = ?",
            (sec.hash_password(temp), row["id"]),
        )
        revoked = sec.drop_admin_sessions(conn, row["id"])
        ark_db.audit(conn, action="recovery-console", actor_id=None,
                     actor_name="root-console", target=row["username"], result="ok",
                     detail=f"temporary password issued, {revoked} session(s) ended")

    print(f"\nTemporary password for {name}:  {temp}\n")
    print("It works once. The box will ask for a new password as soon as you sign in.")
    print("Sign in at https://ark.lan:8443/admin — over an SSH tunnel if the box is")
    print("still unclaimed:")
    print("   ssh -N -L 8444:127.0.0.1:8444 pi@ark.local    then https://localhost:8444/admin\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
