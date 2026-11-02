"""Administrative CLI for creating the first login account."""

from __future__ import annotations

import argparse
import getpass
import sys

from . import create_app
from .auth import create_account
from .db import get_db


def main() -> int:
    parser = argparse.ArgumentParser(prog="clinic-admin")
    subparsers = parser.add_subparsers(dest="command", required=True)
    create_user = subparsers.add_parser("create-user", help="create a USER or ADMIN login")
    create_user.add_argument("--username", required=True)
    create_user.add_argument("--role", choices=("USER", "ADMIN"), default="USER")
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        display_name = input("Display name: ").strip() or args.username
        email = input("Email (optional): ").strip()
        password = getpass.getpass("Password: ")
        confirmation = getpass.getpass("Confirm password: ")
        if len(password) < 12:
            print("Password must contain at least 12 characters.", file=sys.stderr)
            return 2
        if password != confirmation:
            print("Passwords do not match.", file=sys.stderr)
            return 2
        try:
            create_account(args.username, password, args.role, display_name, email)
        except Exception as error:
            get_db().rollback()
            print(f"Could not create account: {error}", file=sys.stderr)
            return 1
        print(f"Created {args.role} account {args.username!r}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
