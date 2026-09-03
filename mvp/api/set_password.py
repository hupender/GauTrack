"""Set one account's password deliberately, from the machine that runs GauTrack.

Why this exists: every other way of getting a password onto an account produces
a *random* one — the seed mints them, and an administrator's "reset password"
button issues a temporary one. That is right for handing a new officer their
first login, and wrong for the two cases a pilot actually has:

  * the DMC wants a password he has chosen and can remember;
  * a field phone must keep working across a demo-data refresh.

Run it as `make set-password U=<username>`. The password is typed at a hidden
prompt, never passed as a command-line argument (arguments are visible to every
other process on the machine via `ps`, and they land in the shell history file).

Two deliberate behaviours worth knowing:

  * Every existing session for that account is revoked. A password change that
    leaves the old sessions alive has not actually changed anything for an
    attacker who already has a cookie.
  * The change is written through the schema-owner connection, so the audit
    trigger records it like any other row change: who ran it, when, and that the
    hash changed. The password itself is never written anywhere in clear.
"""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parent))

from auth import hash_password  # noqa: E402
from config import settings  # noqa: E402

MIN_LENGTH = 12


def _mark_in_credentials_file(username: str) -> bool:
    """Strike this account's row out of `.seed_credentials.txt`.

    That file is the operator's list of what works. The moment a password is
    changed here, the value printed there is wrong, and a wrong password in a
    credentials file is worse than no password at all: it sends whoever reads it
    into a lockout after ten failed attempts. The row is kept (so the account is
    still listed with its role and ULB) but the password column is replaced.
    """
    from config import ROOT_DIR

    path = ROOT_DIR / ".seed_credentials.txt"
    if not path.exists():
        return False
    changed = False
    out = []
    for line in path.read_text().splitlines():
        parts = line.split()
        if len(parts) == 4 and parts[0] == username and not line.startswith(" "):
            line = f"{parts[0]:<12} {'(set manually)':<28} {parts[2]:<14} {parts[3]}"
            changed = True
        out.append(line)
    if changed:
        path.write_text("\n".join(out) + "\n")
        path.chmod(0o600)
    return changed


def set_password(username: str, password: str) -> str:
    engine = create_engine(
        settings.owner_database_url,
        future=True,
        connect_args=settings.db_connect_args(),
    )
    with Session(engine, future=True) as db:
        row = db.execute(
            text("SELECT id, full_name, role::text AS role FROM users WHERE username = :u"),
            {"u": username},
        ).mappings().one_or_none()
        if row is None:
            raise SystemExit(f"no account called '{username}' — run `make set-password` with a username from /admin/users")

        # Attribute the change in the audit log to this script rather than to
        # whichever user id happens to be left over on the connection.
        db.execute(text("SELECT set_config('app.ip', 'set-password-script', false)"))
        db.execute(
            text("UPDATE users SET password_hash = :h, failed_logins = 0, locked_until = NULL WHERE id = :i"),
            {"h": hash_password(password), "i": row["id"]},
        )
        # A password change must not leave old cookies working.
        revoked = db.execute(
            text("UPDATE sessions SET revoked_at = now() WHERE user_id = :i AND revoked_at IS NULL"),
            {"i": row["id"]},
        ).rowcount
        db.commit()
    return f"{row['full_name']} ({row['role']}) — {revoked} existing session(s) signed out"


def main() -> None:
    parser = argparse.ArgumentParser(description="Set one GauTrack account's password")
    parser.add_argument("username", help="the account to change, e.g. dmc")
    args = parser.parse_args()

    first = getpass.getpass(f"New password for '{args.username}': ")
    if len(first) < MIN_LENGTH:
        raise SystemExit(f"too short — use at least {MIN_LENGTH} characters")
    if first != getpass.getpass("Type it again: "):
        raise SystemExit("the two entries did not match — nothing was changed")

    print(f"[set-password] {set_password(args.username, first)}")
    print(f"[set-password] '{args.username}' can now sign in with the new password.")
    if _mark_in_credentials_file(args.username):
        print("[set-password] .seed_credentials.txt now shows '(set manually)' for this "
              "account, because the password printed there no longer works.")
    print("[set-password] This password survives `make reseed`. It is deliberately not "
          "written to any file — record it wherever you keep the others.")


if __name__ == "__main__":
    main()
