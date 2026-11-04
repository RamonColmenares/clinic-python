from __future__ import annotations

import re

from werkzeug.security import check_password_hash

from clinic.db import get_db


def test_account_management_hashes_passwords_and_protects_last_active_manager(client, app, credentials, sign_in):
    sign_in(client, credentials)
    created = client.post("/api/accounts", json={"username": "dummy-new-user", "display_name": "DUMMY New User", "email": "new-user@example.test", "role": "USER", "password": "DUMMY long account password"})
    assert created.status_code == 201, created.get_json()
    account = created.get_json()
    assert account["role"] == "USER"
    assert "password_hash" not in account
    assert client.get("/api/accounts?active=true").get_json()["total"] == 3
    assert client.post("/api/accounts", json={"username": "bad!name", "password": "DUMMY long account password"}).status_code == 400
    assert client.post("/api/accounts", json={"username": "dummy-short", "password": "short"}).status_code == 400

    with app.app_context():
        stored = get_db().execute("SELECT password_hash FROM users WHERE username='dummy-new-user'").fetchone()[0]
        assert stored != "DUMMY long account password"
        assert check_password_hash(stored, "DUMMY long account password")

    self_id = client.get("/api/session").get_json()["account"]["id"]
    demote_last_admin = client.patch(f"/api/accounts/{self_id}", json={"role": "USER"})
    assert demote_last_admin.status_code == 409
    assert demote_last_admin.get_json()["error"] == "last_active_admin"
    last_admin = client.patch(f"/api/accounts/{self_id}", json={"active": False})
    assert last_admin.status_code == 409
    assert last_admin.get_json()["error"] == "self_deactivation"

    second_admin = client.post("/api/accounts", json={"username": "dummy-second-admin", "display_name": "DUMMY Second Admin", "role": "ADMIN", "password": "DUMMY another long password"}).get_json()
    demoted = client.patch(f"/api/accounts/{self_id}", json={"role": "USER"})
    assert demoted.status_code == 200
    assert demoted.get_json()["role"] == "USER"
    assert client.get("/api/accounts").status_code == 403

    client.post("/logout")
    page = client.get("/login")
    token = re.search(rb'name="csrf_token" value="([^"]+)"', page.data).group(1).decode()
    client.post("/login", data={"username": "dummy-second-admin", "password": "DUMMY another long password", "csrf_token": token})
    client.environ_base["HTTP_X_CSRF_TOKEN"] = client.get("/api/session").get_json()["csrf_token"]
    assert client.get("/api/accounts").status_code == 200
    assert client.patch(f"/api/accounts/{self_id}", json={"active": False}).status_code == 200
    with client.application.app_context():
        admin_hash = get_db().execute("SELECT password_hash FROM users WHERE username=?", ("dummy-second-admin",)).fetchone()[0]
        assert check_password_hash(admin_hash, "DUMMY another long password")


def test_password_reset_revokes_existing_sessions_and_deactivation_revokes_access(app, credentials, sign_in):
    manager = app.test_client()
    sign_in(manager, credentials)
    user = app.test_client()
    sign_in(user, credentials, "user")
    accounts = manager.get("/api/accounts?limit=200").get_json()["items"]
    user_record = next(item for item in accounts if item["username"] == "dummy-user")
    reset = manager.patch(f"/api/accounts/{user_record['id']}", json={"password": "DUMMY replacement password"})
    assert reset.status_code == 200
    assert user.get("/api/patients").status_code == 401

    page = user.get("/login")
    token = re.search(rb'name="csrf_token" value="([^"]+)"', page.data).group(1).decode()
    old_password = user.post("/login", data={"username": "dummy-user", "password": credentials["user"][1], "csrf_token": token})
    assert old_password.status_code == 200
    assert b"incorrect" in old_password.data
    page = user.get("/login")
    token = re.search(rb'name="csrf_token" value="([^"]+)"', page.data).group(1).decode()
    signed_in = user.post("/login", data={"username": "dummy-user", "password": "DUMMY replacement password", "csrf_token": token})
    assert signed_in.status_code == 302
    session = user.get("/api/session").get_json()
    user.environ_base["HTTP_X_CSRF_TOKEN"] = session["csrf_token"]
    assert user.get("/api/patients").status_code == 200

    deactivated = manager.patch(f"/api/accounts/{user_record['id']}", json={"active": False})
    assert deactivated.status_code == 200
    assert user.get("/api/patients").status_code == 401
    assert manager.get("/api/accounts?active=false").get_json()["total"] == 1
