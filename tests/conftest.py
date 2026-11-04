from __future__ import annotations

import re
import secrets
from datetime import datetime, timezone

import pytest

from clinic import create_app
from clinic.auth import create_account


@pytest.fixture
def app(tmp_path):
    application = create_app({
        "TESTING": True,
        "DATABASE_PATH": str(tmp_path / "clinic-test.sqlite3"),
        "SECRET_KEY": secrets.token_urlsafe(48),
        "CLOCK": lambda: datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc),
    })
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
        page = client.get("/login")
        match = re.search(rb'name="csrf_token" value="([^"]+)"', page.data)
        assert match, "The sign-in page must render its CSRF token."
        username, password = credentials[role]
        response = client.post("/login", data={"username": username, "password": password, "csrf_token": match.group(1).decode()})
        assert response.status_code == 302
        session = client.get("/api/session").get_json()
        client.environ_base["HTTP_X_CSRF_TOKEN"] = session["csrf_token"]
        return response

    return login


@pytest.fixture
def practice():
    def create(client, *, second_dentist=False, second_room=False):
        patient_response = client.post("/api/patients", json={
            "first_name": "DUMMY", "last_name": "Patient One", "email": "patient@example.test",
            "patient_number": "DUMMY-001", "registered_on": "2026-11-07",
            "reminder_consent": True,
            "address": {"street": "DUMMY Main Street", "number": 10, "city": "DUMMY City", "region": "DUMMY Region"},
        })
        assert patient_response.status_code == 201, patient_response.get_json()
        second_patient_response = client.post("/api/patients", json={
            "first_name": "DUMMY", "last_name": "Patient Two", "email": "patient-two@example.test",
            "patient_number": "DUMMY-002", "reminder_consent": False,
        })
        assert second_patient_response.status_code == 201, second_patient_response.get_json()
        dentist_response = client.post("/api/dentists", json={"first_name": "DUMMY", "last_name": "Practitioner One", "license_number": "DUMMY-DR-1"})
        assert dentist_response.status_code == 201, dentist_response.get_json()
        service_response = client.post("/api/services", json={"name": "DUMMY Checkup", "duration_minutes": 30, "price_cents": 7500})
        assert service_response.status_code == 201, service_response.get_json()
        room_response = client.post("/api/rooms", json={"name": "DUMMY Room 1"})
        assert room_response.status_code == 201, room_response.get_json()
        first = {
            "patient": patient_response.get_json(), "second_patient": second_patient_response.get_json(),
            "dentist": dentist_response.get_json(), "service": service_response.get_json(), "room": room_response.get_json(),
        }
        qualification = client.put(f"/api/dentists/{first['dentist']['id']}/services", json={"service_ids": [first["service"]["id"]]})
        assert qualification.status_code == 200, qualification.get_json()
        day = "2026-11-09"
        shift = client.post("/api/shifts", json={"dentist_id": first["dentist"]["id"], "weekday": 0, "start_time": "09:00", "end_time": "12:00"})
        assert shift.status_code == 201, shift.get_json()
        first["shift"] = shift.get_json()
        first["date"] = day
        if second_dentist:
            dentist_two = client.post("/api/dentists", json={"first_name": "DUMMY", "last_name": "Practitioner Two", "license_number": "DUMMY-DR-2"}).get_json()
            client.put(f"/api/dentists/{dentist_two['id']}/services", json={"service_ids": [first["service"]["id"]]})
            client.post("/api/shifts", json={"dentist_id": dentist_two["id"], "weekday": 0, "start_time": "09:00", "end_time": "12:00"})
            first["dentist_two"] = dentist_two
        if second_room:
            first["room_two"] = client.post("/api/rooms", json={"name": "DUMMY Room 2"}).get_json()
        return first

    return create


@pytest.fixture
def book_visit():
    def book(client, practice, *, start="2026-11-09T09:00", patient_key="patient", dentist_key="dentist", room_key="room", reason="DUMMY visit"):
        return client.post("/api/appointments", json={
            "patient_id": practice[patient_key]["id"], "dentist_id": practice[dentist_key]["id"],
            "service_id": practice["service"]["id"], "room_id": practice[room_key]["id"],
            "start_at": start, "reason": reason,
        })
    return book
