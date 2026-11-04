"""Clinic Desk application factory."""

from __future__ import annotations

import os
from pathlib import Path

from flask import Flask, jsonify, request

from .auth import load_or_create_secret
from .db import close_db, init_db


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=True, instance_path=os.environ.get("INSTANCE_PATH") or None)
    provided_key = (test_config or {}).get("SECRET_KEY")
    secret_key = provided_key or load_or_create_secret(app.instance_path)
    app.config.from_mapping(
        DATABASE_PATH=os.environ.get("DATABASE_PATH", str(Path(app.instance_path) / "clinic.sqlite3")),
        SECRET_KEY=secret_key,
        CLINIC_TIMEZONE=os.environ.get("CLINIC_TIMEZONE", "UTC"),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=False,
        MAX_CONTENT_LENGTH=64 * 1024,
    )
    if test_config:
        app.config.update(test_config)
    if app.config["CLINIC_TIMEZONE"] != "UTC":
        raise RuntimeError("This offline baseline stores clinic wall times in UTC; other zones are not enabled.")

    Path(app.instance_path).mkdir(parents=True, exist_ok=True, mode=0o700)
    init_db(app)

    from .auth import bp as auth_bp
    from .routes import bp as api_bp
    from .ui import bp as ui_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(ui_bp)
    app.teardown_appcontext(close_db)

    @app.after_request
    def secure_local_response(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Content-Security-Policy", "default-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'")
        return response

    @app.errorhandler(404)
    def not_found(_error):
        if request.path == "/api" or request.path.startswith("/api/"):
            return jsonify(error="not_found", message="The requested API resource was not found."), 404
        return "Not found", 404

    @app.errorhandler(405)
    def method_not_allowed(_error):
        if request.path == "/api" or request.path.startswith("/api/"):
            return jsonify(error="method_not_allowed", message="That method is not supported for this resource."), 405
        return "Method not allowed", 405

    return app
