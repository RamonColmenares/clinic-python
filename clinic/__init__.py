"""Clinic Turnos application factory."""

from __future__ import annotations

import os
import secrets
from pathlib import Path

from flask import Flask

from .db import close_db, init_db


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        DATABASE_PATH=os.environ.get(
            "DATABASE_PATH", str(Path(app.instance_path) / "clinic.sqlite3")
        ),
        SECRET_KEY=os.environ.get("SECRET_KEY") or secrets.token_hex(32),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
    )
    if test_config:
        app.config.update(test_config)

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    init_db(app)

    from .auth import bp as auth_bp
    from .routes import bp as api_bp
    from .ui import bp as ui_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(ui_bp)
    app.teardown_appcontext(close_db)

    return app
