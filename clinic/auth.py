"""Session authentication and account creation."""

from __future__ import annotations

from functools import wraps

from flask import Blueprint, g, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from .db import get_db

bp = Blueprint("auth", __name__)


def is_api_path(path: str) -> bool:
    return path == "/pacientes" or path.startswith("/pacientes/") or path == "/odontologos" or path.startswith("/odontologos/") or path == "/turnos" or path.startswith("/turnos/")


@bp.before_app_request
def load_account_and_protect_routes():
    user_id = session.get("user_id")
    g.account = None
    if user_id is not None:
        g.account = get_db().execute(
            "SELECT id, username, display_name, email, role FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if g.account is None:
            session.clear()

    if request.endpoint in {"auth.login", "auth.health", "static"}:
        return None
    if g.account is not None:
        return None
    if is_api_path(request.path):
        return jsonify(error="Authentication required."), 401
    return redirect(url_for("auth.login", next=request.path))


def require_roles(*roles: str):
    def decorate(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if g.account is None:
                return jsonify(error="Authentication required."), 401
            if g.account["role"] not in roles:
                return jsonify(error="This account is not allowed to perform this action."), 403
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
            "SELECT id, username, password_hash FROM users WHERE username = ?",
            (username,),
        ).fetchone()
        if account is None or not check_password_hash(account["password_hash"], password):
            error = "Nombre de usuario o contraseña incorrectos."
        else:
            session.clear()
            session["user_id"] = account["id"]
            destination = request.args.get("next", "")
            if not destination.startswith("/") or destination.startswith("//"):
                destination = url_for("ui.home")
            return redirect(destination)
    return render_template("login.html", error=error)


@bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


def create_account(
    username: str,
    password: str,
    role: str,
    display_name: str,
    email: str = "",
) -> int:
    """Create an account using a one-way password hash."""
    normalized_role = role.upper()
    if normalized_role not in {"USER", "ADMIN"}:
        raise ValueError("role must be USER or ADMIN")
    if not username.strip() or not password:
        raise ValueError("username and password are required")
    connection = get_db()
    cursor = connection.execute(
        "INSERT INTO users (username, display_name, email, password_hash, role) VALUES (?, ?, ?, ?, ?)",
        (
            username.strip(),
            display_name.strip() or username.strip(),
            email.strip(),
            generate_password_hash(password),
            normalized_role,
        ),
    )
    connection.commit()
    return int(cursor.lastrowid)
