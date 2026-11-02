from __future__ import annotations

import secrets

import pytest

from clinic import create_app
from clinic.auth import create_account


@pytest.fixture
def app(tmp_path):
    application = create_app(
        {
            "TESTING": True,
            "DATABASE_PATH": str(tmp_path / "clinic-test.sqlite3"),
            "SECRET_KEY": secrets.token_bytes(32),
        }
    )
    yield application


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def credentials(app):
    with app.app_context():
        admin_password = secrets.token_urlsafe(24)
        user_password = secrets.token_urlsafe(24)
        create_account("dummy-admin", admin_password, "ADMIN", "DUMMY Admin", "admin@example.test")
        create_account("dummy-user", user_password, "USER", "DUMMY User", "user@example.test")
    return {
        "admin": ("dummy-admin", admin_password),
        "user": ("dummy-user", user_password),
    }


@pytest.fixture
def sign_in():
    def login(client, credentials, role="admin"):
        username, password = credentials[role]
        return client.post("/login", data={"username": username, "password": password})

    return login
