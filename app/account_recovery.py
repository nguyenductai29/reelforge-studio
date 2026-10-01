"""Break-glass account recovery, on the server only: ``python -m app.account_recovery``.

For the case the web interface cannot solve: the only administrator forgot their password while email does not
work, or lost both their authenticator and their recovery codes. Whoever runs this already controls the server
(shell access as the service account, ``instance/bootstrap.json`` and the master key), so it asks for nothing else.

    python -m app.account_recovery reset-password --email admin@example.com   # prompts twice, never echoes
    python -m app.account_recovery reset-2fa --email admin@example.com

Both sign the account out everywhere, are recorded in the audit log (``via: server_command``) and email the
account owner when email works (the scheduler worker delivers it). ``--password-stdin`` reads the new password
from standard input instead of prompting, for scripts. Nothing is printed but the outcome.
"""
import argparse
from datetime import datetime, timezone
import getpass
import sys

from sqlalchemy import select, update


def _user(db, email: str):
    from sqlalchemy.orm import undefer

    from app.models import User

    return db.scalar(select(User).options(undefer("*")).where(User.email == email.strip().lower()))


def _notify(db, user, template: str, params: dict) -> None:
    from app import mailer

    mailer.enqueue(db, to=user.email, template=template, locale=user.locale, params=params, user_id=user.id)


def reset_password(email: str, password: str) -> int:
    from app import accounts, audit
    from app.db import Session
    from app.models import AccountToken
    from app.passwords import hashed_password

    with Session.begin() as db:
        user = _user(db, email)
        if user is None:
            print("No account with that email.", file=sys.stderr)
            return 1
        problem = accounts.password_problem(password, user.email)
        if problem:
            print("Use at least 12 characters, different from the email.", file=sys.stderr)
            return 1
        moment = datetime.now(timezone.utc)
        user.password_hash, user.password_changed_at = hashed_password(password), moment
        db.execute(update(AccountToken).where(AccountToken.user_id == user.id, AccountToken.purpose == "password_reset",
                                              AccountToken.used_at.is_(None)).values(used_at=moment))
        revoked = accounts.revoke_sessions(db, user.id)
        audit.record(db, "admin.user_password_reset", target_type="user", target_id=user.id,
                     details={"via": "server_command", "sessions_revoked": revoked})
        from app import mailer

        _notify(db, user, "password_changed", {"time": moment, "link": mailer.link("/")})
    print(f"Password replaced; {revoked} session(s) signed out.")
    return 0


def reset_two_factor(email: str) -> int:
    from app import accounts, audit
    from app.db import Session

    with Session.begin() as db:
        user = _user(db, email)
        if user is None:
            print("No account with that email.", file=sys.stderr)
            return 1
        if not accounts.two_factor_enabled(user):
            print("Two-factor authentication is already off for this account.")
            return 0
        accounts.disable_totp(db, user)
        revoked = accounts.revoke_sessions(db, user.id)
        audit.record(db, "admin.user_two_factor_reset", target_type="user", target_id=user.id,
                     details={"via": "server_command", "sessions_revoked": revoked})
        _notify(db, user, "security_2fa_disabled", {"time": datetime.now(timezone.utc)})
    print(f"Two-factor authentication turned off; {revoked} session(s) signed out. Turn it on again after signing in.")
    return 0


def _read_password(from_stdin: bool) -> str | None:
    if from_stdin:
        return sys.stdin.readline().rstrip("\r\n")
    first = getpass.getpass("New password: ")
    if first != getpass.getpass("Repeat it: "):
        print("The passwords do not match.", file=sys.stderr)
        return None
    return first


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Recover an account from the server (break-glass)")
    commands = parser.add_subparsers(dest="command", required=True)
    password_parser = commands.add_parser("reset-password", help="Set a new password; every session is signed out")
    password_parser.add_argument("--email", required=True)
    password_parser.add_argument("--password-stdin", action="store_true", help="Read the password from stdin")
    two_factor_parser = commands.add_parser("reset-2fa", help="Turn two-factor authentication off for an account")
    two_factor_parser.add_argument("--email", required=True)
    args = parser.parse_args(argv)

    from app.runtime_env import start_process

    start_process("account_recovery")
    if args.command == "reset-password":
        password = _read_password(args.password_stdin)
        return 1 if password is None else reset_password(args.email, password)
    return reset_two_factor(args.email)


if __name__ == "__main__":
    raise SystemExit(main())
