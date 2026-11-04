from __future__ import annotations

import re


def test_login_logout_roles_and_api_authentication(client, credentials, sign_in, practice):
    assert client.get("/api/session").status_code == 401
    unknown = client.get("/api/no-such-route")
    assert unknown.status_code == 401
    assert unknown.is_json
    assert client.get("/").status_code == 302

    login_page = client.get("/login")
    token = re.search(rb'name="csrf_token" value="([^"]+)"', login_page.data).group(1).decode()
    wrong_password = client.post("/login", data={"username": "dummy-admin", "password": "wrong", "csrf_token": token})
    assert wrong_password.status_code == 200
    assert b"The username or password is incorrect." in wrong_password.data

    sign_in(client, credentials)
    ids = practice(client)
    assert client.get("/api/session").get_json()["timezone"] == "UTC"
    csrf_required = client.post("/api/services", json={"name": "DUMMY Forbidden", "duration_minutes": 30, "price_cents": 0}, headers={"X-CSRF-Token": ""})
    assert csrf_required.status_code == 400
    client.post("/logout")
    sign_in(client, credentials, "user")
    assert client.get("/api/patients").status_code == 200
    assert client.get("/api/appointments").status_code == 200
    assert client.get("/api/treatment-plans").status_code == 403
    assert client.get("/api/clinical-notes").status_code == 403
    assert client.get("/api/reports/revenue?from=2026-11-09&to=2026-11-09").status_code == 403
    assert client.get("/api/audit").status_code == 403
    assert client.get("/care").status_code == 403
    assert client.get("/audit").status_code == 403
    assert client.get(f"/api/patients/{ids['patient']['id']}").status_code == 403
    assert client.get("/api/dentists").status_code == 200
    assert client.post("/api/services", json={"name": "DUMMY Forbidden", "duration_minutes": 30, "price_cents": 0}).status_code == 403
    assert b"/care" not in client.get("/patients").data

    csrf_result = client.post("/api/waitlist", json={}, headers={"X-CSRF-Token": ""})
    assert csrf_result.status_code == 400
    assert csrf_result.get_json()["error"] == "csrf_failed"
    assert client.post("/logout", headers={"X-CSRF-Token": ""}).status_code == 400
    assert client.post("/logout").status_code == 302
    assert client.get("/api/patients").status_code == 401


def test_safe_next_path_and_no_third_party_browser_dependencies(client, credentials, sign_in):
    sign_in(client, credentials)
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 200
    for path in ("/", "/patients", "/dentists", "/appointments", "/catalog", "/schedule", "/care", "/waitlist", "/reminders", "/reports", "/audit", "/accounts"):
        page = client.get(path)
        assert page.status_code == 200, path
        assert b"lang=\"en\"" in page.data
        assert b"maxcdn.bootstrapcdn.com" not in page.data
        assert b"ajax.googleapis.com" not in page.data
        assert b"csrf-token" in page.data
    script = client.get("/static/clinic.js")
    assert script.status_code == 200
    for marker in (b"/api/availability", b"/api/treatment-plans", b"/api/waitlist", b"/api/reminders/generate", b"/api/reports/revenue", b"/api/accounts"):
        assert marker in script.data
    assert b"innerHTML" not in script.data

    client.post("/logout")
    for unsafe in ("//evil.example/path", "/%5cevil.example", "https://evil.example"):
        page = client.get("/login?next=" + unsafe.replace("?", "%3F"))
        token = re.search(rb'name="csrf_token" value="([^"]+)"', page.data).group(1).decode()
        result = client.post("/login?next=" + unsafe.replace("?", "%3F"), data={"username": credentials["admin"][0], "password": credentials["admin"][1], "csrf_token": token})
        assert result.status_code == 302
        assert result.headers["Location"] == "/"
        client.environ_base["HTTP_X_CSRF_TOKEN"] = client.get("/api/session").get_json()["csrf_token"]
        client.post("/logout")
