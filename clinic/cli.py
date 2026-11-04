"""Operator commands for account setup, local demo data, and integrity checks."""

from __future__ import annotations

import argparse
import getpass
import sys
from datetime import date, datetime, timedelta, timezone

from . import create_app
from .auth import create_account
from . import catalog, operations
from .db import get_db


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="clinic-admin", description="Manage a local Clinic Desk instance.")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create-user", help="create a USER or ADMIN account")
    create.add_argument("--username", required=True)
    create.add_argument("--role", choices=("USER", "ADMIN"), default="USER")
    commands.add_parser("generate-demo", help="add clearly synthetic local records for a manual browser walkthrough")
    reminders = commands.add_parser("draft-reminders", help="create due reminder previews without sending messages")
    reminders.add_argument("--as-of", required=True, help="UTC local minute, YYYY-MM-DDTHH:MM")
    commands.add_parser("db-check", help="check schema version, foreign keys, and record counts")
    return parser


def _create_user(args) -> int:
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
    create_account(args.username, password, args.role, display_name, email)
    print(f"Created {args.role} account {args.username!r}.")
    return 0


def _next_weekday(after: date, weekday: int) -> date:
    distance = (weekday - after.weekday()) % 7
    return after + timedelta(days=distance or 7)


def _generate_demo() -> int:
    db = get_db()
    if db.execute("SELECT 1 FROM patients WHERE patient_number='DUMMY-DEMO-001'").fetchone():
        print("DUMMY demo records already exist; no changes were made.")
        return 0
    today = datetime.now(timezone.utc).date()
    patient = catalog.create_patient({
        "first_name": "DUMMY", "last_name": "Demo Patient", "email": "patient@example.test",
        "patient_number": "DUMMY-DEMO-001", "registered_on": today.isoformat(),
        "reminder_consent": True,
    })
    dentist = catalog.create_dentist({"first_name": "DUMMY", "last_name": "Demo Practitioner", "license_number": "DUMMY-LICENSE-001"})
    service = catalog.create_service({"name": "DUMMY Checkup", "duration_minutes": 30, "price_cents": 7500})
    room = catalog.create_room({"name": "DUMMY Room 1"})
    catalog.set_qualifications(dentist["id"], [service["id"]])
    appointment_day = _next_weekday(today, 0)
    weekday = appointment_day.weekday()
    catalog.create_shift({"dentist_id": dentist["id"], "weekday": weekday, "start_time": "09:00", "end_time": "12:00"})
    print(f"Created DUMMY patient #{patient['id']}, practitioner #{dentist['id']}, service #{service['id']}, room #{room['id']}.")
    print(f"Working hours are ready for {appointment_day.isoformat()}; sign in to book a local visit.")
    return 0


def _database_check() -> int:
    db = get_db()
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version != 2:
        print(f"Unsupported schema version: {version}", file=sys.stderr)
        return 1
    violations = db.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        print(f"Foreign key violations: {len(violations)}", file=sys.stderr)
        return 1
    counts = {}
    for table in ("users", "patients", "dentists", "services", "rooms", "appointments", "treatment_plans", "waitlist_entries", "reminders"):
        counts[table] = int(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    print("Schema v2 is valid; foreign-key checks passed.")
    for table, count in counts.items():
        print(f"{table}: {count}")
    return 0


def main() -> int:
    args = _parser().parse_args()
    app = create_app()
    with app.app_context():
        try:
            if args.command == "create-user":
                return _create_user(args)
            if args.command == "generate-demo":
                return _generate_demo()
            if args.command == "draft-reminders":
                result = operations.generate_reminders({"as_of": args.as_of})
                print(f"Created {result['created']} local reminder draft(s). No messages were sent.")
                return 0
            return _database_check()
        except Exception as error:
            get_db().rollback()
            print(f"Clinic Desk command failed: {error}", file=sys.stderr)
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
