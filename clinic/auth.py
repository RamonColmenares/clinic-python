"""Persistent local account sessions, CSRF protection, and role guards."""

from __future__ import annotations

import os
import secrets
from functools import wraps
from pathlib import Path
from urllib.parse import unquote, urlsplit

from flask import Blueprint, current_app, g, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from .db import atomic, get_db
from .validation import DomainError

bp = Blueprint("auth", __name__)


def is_api_path(path: str) -> bool:
    return path == "/api" or path.startswith("/api/")


def csrf_token() -> str:
    token = session.get("csrf_token")
    if not isinstance(token, str) or len(token) < 32:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


def valid_next_path(value: str | None) -> bool:
    if not value:
        return False
    decoded = unquote(value)
    if "\\" in decoded or "\x00" in decoded:
        return False
    parsed = urlsplit(decoded)
    return parsed.scheme == "" and parsed.netloc == "" and decoded.startswith("/") and not decoded.startswith("//")


@bp.before_app_request
def load_account_and_protect_routes():
    csrf_token()
    user_id = session.get("user_id")
    g.account = None
    if user_id is not None:
        g.account = get_db().execute(
            "SELECT id,username,display_name,email,role,session_version FROM users WHERE id=? AND active=1",
            (user_id,),
        ).fetchone()
        if g.account is None or session.get("session_version") != g.account["session_version"]:
            session.clear()
            csrf_token()
            g.account = None

    if request.endpoint in {"auth.login", "auth.health", "static"}:
        if request.method == "POST" and request.endpoint == "auth.login":
            supplied = request.form.get("csrf_token", "")
            if not secrets.compare_digest(supplied, session.get("csrf_token", "")):
                return jsonify(error="csrf_failed", message="The form expired. Reload it and try again."), 400
        return None
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and g.account is not None:
        supplied = request.headers.get("X-CSRF-Token", "") or request.form.get("csrf_token", "")
        if not secrets.compare_digest(supplied, session.get("csrf_token", "")):
            return jsonify(error="csrf_failed", message="A valid CSRF token is required."), 400
    if g.account is not None:
        return None
    if is_api_path(request.path):
        return jsonify(error="authentication_required", message="Authentication is required."), 401
    return redirect(url_for("auth.login", next=request.full_path.rstrip("?") if request.query_string else request.path))


def require_roles(*roles: str):
    def decorate(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if g.account is None:
                return jsonify(error="authentication_required", message="Authentication is required."), 401
            if g.account["role"] not in roles:
                return jsonify(error="forbidden", message="This account is not allowed to perform this action."), 403
            return view(*args, **kwargs)

        return wrapped

    return decorate


@bp.get("/health")
def health():
    return jsonify(status="ok")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.account is not None and request.method == "GET":
        return redirect(url_for("ui.home"))
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        account = get_db().execute(
            "SELECT id,username,password_hash,session_version FROM users WHERE username=? AND active=1", (username,)
        ).fetchone()
        if account is None or not check_password_hash(account["password_hash"], password):
            error = "The username or password is incorrect."
        else:
            session.clear()
            session["user_id"] = account["id"]
            session["session_version"] = account["session_version"]
            csrf_token()
            destination = request.args.get("next", "")
            if not valid_next_path(destination):
                destination = url_for("ui.home")
            return redirect(destination)
    return render_template("login.html", error=error, csrf_token=csrf_token())


@bp.post("/logout")
def logout():
    # The pre-request guard has already checked authentication and CSRF.
    session.clear()
    return redirect(url_for("auth.login"))


def create_account(username: str, password: str, role: str, display_name: str, email: str = "") -> int:
    """Create a persistent local account using a salted one-way password hash."""
    normalized_role = role.upper()
    if normalized_role not in {"USER", "ADMIN"}:
        raise ValueError("role must be USER or ADMIN")
    if not username.strip() or not password or len(password) < 12:
        raise ValueError("username and a password of at least 12 characters are required")
    if len(username) > 80 or len(display_name) > 120 or len(email) > 254:
        raise ValueError("account fields exceed their supported length")
    connection = get_db()
    with atomic(connection):
        cursor = connection.execute(
            "INSERT INTO users (username,display_name,email,password_hash,role) VALUES (?,?,?,?,?)",
            (username.strip(), display_name.strip() or username.strip(), email.strip(), generate_password_hash(password), normalized_role),
        )
        return int(cursor.lastrowid)


def load_or_create_secret(instance_path: str) -> str:
    """Use the configured key or persist one private random key for local restarts."""
    configured = os.environ.get("SECRET_KEY")
    if configured:
        return configured
    location = Path(instance_path) / "session-secret"
    Path(instance_path).mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        descriptor = os.open(location, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        value = location.read_text(encoding="ascii").strip()
        if len(value) < 48:
            raise RuntimeError("The local session-secret file is invalid; remove it to generate a new key.")
        try:
            location.chmod(0o600)
        except OSError:
            pass
        return value
    value = secrets.token_urlsafe(48)
    with os.fdopen(descriptor, "w", encoding="ascii") as stream:
        stream.write(value + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    return value
