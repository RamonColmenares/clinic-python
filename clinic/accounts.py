"""Manager-controlled local accounts and session revocation."""

from __future__ import annotations

import re

from flask import g
from werkzeug.security import generate_password_hash

from .auth import create_account
from .catalog import audit
from .db import atomic, get_db
from .validation import boolean, conflict, integer, invalid, missing, page_args, text


USERNAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{2,79}\Z")
ACCOUNT_ROLES = {"ADMIN", "USER"}


def account_record(row) -> dict:
    return {
        "id": int(row["id"]),
        "username": row["username"],
        "display_name": row["display_name"],
        "email": row["email"],
        "role": row["role"],
        "active": bool(row["active"]),
    }


def list_accounts(limit: int = 50, offset: int = 0, active: bool | None = None) -> dict:
    db = get_db()
    where, values = (" WHERE active=?", [int(active)]) if active is not None else ("", [])
    total = int(db.execute("SELECT COUNT(*) FROM users" + where, values).fetchone()[0])
    rows = db.execute("SELECT id,username,display_name,email,role,active FROM users" + where + " ORDER BY username,id LIMIT ? OFFSET ?", (*values, limit, offset)).fetchall()
    return {"items": [account_record(row) for row in rows], "total": total, "limit": limit, "offset": offset}


def get_account(account_id: int) -> dict:
    row = get_db().execute("SELECT id,username,display_name,email,role,active FROM users WHERE id=?", (account_id,)).fetchone()
    if row is None:
        raise missing("Account")
    return account_record(row)


def _credentials(data: dict) -> tuple[str, str, str, str, str]:
    username = text(data.get("username"), "username", maximum=80)
    if not USERNAME_PATTERN.fullmatch(username):
        raise invalid("Username must be 3–80 characters and use letters, numbers, periods, underscores, or hyphens.")
    display_name = text(data.get("display_name", username), "display_name", maximum=120)
    email = text(data.get("email", ""), "email", required=False, maximum=254)
    role = text(data.get("role", "USER"), "role", maximum=10).upper()
    if role not in ACCOUNT_ROLES:
        raise invalid("'role' must be USER or ADMIN.")
    password = text(data.get("password"), "password", maximum=1024)
    if len(password) < 12:
        raise invalid("Password must contain at least 12 characters.")
    return username, display_name, email, role, password


def create_managed_account(data: dict) -> dict:
    username, display_name, email, role, password = _credentials(data)
    db = get_db()
    with atomic(db):
        duplicate = db.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone()
        if duplicate:
            raise conflict("That username is already in use.")
        account_id = create_account(username, password, role, display_name, email)
        audit("created", "account", account_id, active=True, status=role)
    return get_account(account_id)


def update_managed_account(account_id: int, data: dict) -> dict:
    allowed = {"display_name", "email", "role", "active", "password"}
    unknown = set(data) - allowed
    if unknown:
        raise invalid(f"Unknown field(s): {', '.join(sorted(unknown))}.")
    db = get_db()
    with atomic(db):
        row = db.execute("SELECT id,display_name,email,role,active,session_version FROM users WHERE id=?", (account_id,)).fetchone()
        if row is None:
            raise missing("Account")
        display_name = text(data.get("display_name", row["display_name"]), "display_name", maximum=120)
        email = text(data.get("email", row["email"]), "email", required=False, maximum=254)
        role = text(data.get("role", row["role"]), "role", maximum=10).upper()
        if role not in ACCOUNT_ROLES:
            raise invalid("'role' must be USER or ADMIN.")
        active = int(boolean(data["active"], "active")) if "active" in data else row["active"]
        password = data.get("password")
        password_hash = None
        if password not in (None, ""):
            password = text(password, "password", maximum=1024)
            if len(password) < 12:
                raise invalid("Password must contain at least 12 characters.")
            password_hash = generate_password_hash(password)

        current_actor = int(g.account["id"])
        if account_id == current_actor and not active:
            raise conflict("You cannot deactivate the account used for this request.", "self_deactivation")
        was_active_admin = row["role"] == "ADMIN" and bool(row["active"])
        stops_being_active_admin = was_active_admin and (role != "ADMIN" or not active)
        if stops_being_active_admin:
            remaining = db.execute("SELECT COUNT(*) FROM users WHERE active=1 AND role='ADMIN' AND id<>?", (account_id,)).fetchone()[0]
            if remaining == 0:
                raise conflict("At least one active manager account must remain.", "last_active_admin")

        next_version = row["session_version"] + (1 if password_hash else 0)
        db.execute(
            """UPDATE users SET display_name=?,email=?,role=?,active=?,
               password_hash=COALESCE(?,password_hash),session_version=? WHERE id=?""",
            (display_name, email, role, active, password_hash, next_version, account_id),
        )
        audit("updated", "account", account_id, active=bool(active), status=role)
    return get_account(account_id)


def parse_account_page(query) -> tuple[int, int, bool | None]:
    limit, offset = page_args(query)
    raw = query.get("active")
    if raw is None:
        active = None
    elif raw == "true":
        active = True
    elif raw == "false":
        active = False
    else:
        raise invalid("'active' must be true or false.")
    return limit, offset, active
