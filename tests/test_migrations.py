from __future__ import annotations

import secrets
import sqlite3

import pytest
from werkzeug.security import check_password_hash

from clinic import create_app
from clinic.db import get_db


def test_current_database_preserves_records_and_date_only_visits_across_reopens(
    client, app, credentials, sign_in, practice, book_visit
):
    sign_in(client, credentials)
    records = practice(client)
    response = book_visit(client, records)
    assert response.status_code == 201
    visit_id = response.get_json()["id"]
    with app.app_context():
        db = get_db()
        original_hash = db.execute("SELECT password_hash FROM users WHERE username='dummy-admin'").fetchone()[0]
        patient = dict(db.execute("SELECT * FROM patients WHERE id=?", (records["patient"]["id"],)).fetchone())
        address = dict(db.execute("SELECT * FROM addresses WHERE id=?", (patient["address_id"],)).fetchone())
        # A supported database can contain unresolved date-only rows from an earlier release.
        db.execute(
            "UPDATE appointments SET legacy_imported=1, service_id=NULL, room_id=NULL, start_at=?, end_at=? WHERE id=?",
            ("2026-11-15T09:00", "2026-11-15T09:30", visit_id),
        )
        db.commit()

    config = {name: app.config[name] for name in ("TESTING", "DATABASE_PATH", "SECRET_KEY", "CLOCK")}
    for _ in range(2):
        reopened = create_app(config)
        with reopened.app_context():
            db = get_db()
            assert db.execute("PRAGMA user_version").fetchone()[0] == 2
            assert dict(db.execute("SELECT * FROM patients WHERE id=?", (patient["id"],)).fetchone()) == patient
            assert dict(db.execute("SELECT * FROM addresses WHERE id=?", (address["id"],)).fetchone()) == address
            stored_hash = db.execute("SELECT password_hash FROM users WHERE username='dummy-admin'").fetchone()[0]
            assert stored_hash == original_hash
            assert check_password_hash(stored_hash, credentials["admin"][1])
            assert db.execute("SELECT COUNT(*) FROM appointments WHERE id=?", (visit_id,)).fetchone()[0] == 1
            assert db.execute("SELECT COUNT(*) FROM appointment_events WHERE appointment_id=?", (visit_id,)).fetchone()[0] == 1
            assert db.execute("PRAGMA foreign_key_check").fetchall() == []

    visit = client.get(f"/api/appointments/{visit_id}")
    assert visit.status_code == 200 and visit.get_json()["legacy_imported"] is True
    assert client.post(f"/api/appointments/{visit_id}/transitions", json={"status": "confirmed", "version": 1}).status_code == 409
    dentist_id = records["dentist"]["id"]
    service_id = records["service"]["id"]
    room_id = records["room"]["id"]
    assert client.post("/api/shifts", json={"dentist_id": dentist_id, "weekday": 6, "start_time": "09:00", "end_time": "11:00"}).status_code == 201
    report = client.get("/api/reports/utilization?from=2026-11-15&to=2026-11-15").get_json()
    assert report["practitioners"][0]["appointment_minutes"] == 0
    conflict = client.post("/api/appointments", json={
        "patient_id": patient["id"], "dentist_id": dentist_id, "service_id": service_id,
        "room_id": room_id, "start_at": "2026-11-15T09:00",
    })
    assert conflict.status_code == 409 and conflict.get_json()["error"] == "legacy_date_reserved"
    moved = client.patch(f"/api/appointments/{visit_id}", json={
        "version": 1, "start_at": "2026-11-15T09:30", "service_id": service_id, "room_id": room_id,
    })
    assert moved.status_code == 200, moved.get_json()
    assert moved.get_json()["legacy_imported"] is False
    assert client.post(f"/api/appointments/{visit_id}/transitions", json={"status": "confirmed", "version": 2}).status_code == 200
    assert client.get("/api/reports/revenue?from=2026-11-15&to=2026-11-15").get_json()["total_cents"] == 0


@pytest.mark.parametrize("version", [0, 99])
def test_unsupported_database_is_rejected_without_modifying_its_file(tmp_path, version):
    path = tmp_path / "unsupported.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE unsupported_records (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
        db.execute("INSERT INTO unsupported_records VALUES (1, 'DUMMY retained record')")
        db.execute(f"PRAGMA user_version = {version}")
    original_bytes = path.read_bytes()
    with pytest.raises(RuntimeError, match="unsupported"):
        create_app({"TESTING": True, "DATABASE_PATH": str(path), "SECRET_KEY": secrets.token_urlsafe(48)})
    assert path.read_bytes() == original_bytes
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT value FROM unsupported_records WHERE id=1").fetchone()[0] == "DUMMY retained record"
        assert db.execute("PRAGMA user_version").fetchone()[0] == version


def test_failed_schema_initialization_rolls_back_every_table(tmp_path, monkeypatch):
    from clinic import db as database

    path = tmp_path / "failed-initialization.sqlite3"
    monkeypatch.setattr(database, "SCHEMA", database.SCHEMA + "INSERT INTO users (username) VALUES ('dummy-invalid');")
    with pytest.raises(sqlite3.IntegrityError):
        create_app({"TESTING": True, "DATABASE_PATH": str(path), "SECRET_KEY": secrets.token_urlsafe(48)})
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall() == []
        assert db.execute("PRAGMA user_version").fetchone()[0] == 0
