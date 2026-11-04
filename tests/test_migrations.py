from __future__ import annotations

import sqlite3
import secrets
from datetime import datetime, timezone

import pytest
from werkzeug.security import check_password_hash, generate_password_hash

from clinic import create_app
from clinic.db import get_db


LEGACY_SCHEMA = """
CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL UNIQUE, display_name TEXT NOT NULL, email TEXT NOT NULL DEFAULT '', password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK (role IN ('USER','ADMIN')));
CREATE TABLE domicilios (id INTEGER PRIMARY KEY AUTOINCREMENT, calle TEXT NOT NULL DEFAULT '', numero INTEGER, localidad TEXT NOT NULL DEFAULT '', provincia TEXT NOT NULL DEFAULT '');
CREATE TABLE pacientes (id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL, apellido TEXT NOT NULL, email TEXT NOT NULL DEFAULT '', dni INTEGER, fecha_ingreso TEXT, domicilio_id INTEGER UNIQUE, FOREIGN KEY (domicilio_id) REFERENCES domicilios(id) ON DELETE SET NULL);
CREATE TABLE odontologos (id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL, apellido TEXT NOT NULL, matricula TEXT NOT NULL DEFAULT '');
CREATE TABLE turnos (id INTEGER PRIMARY KEY AUTOINCREMENT, paciente_id INTEGER NOT NULL, odontologo_id INTEGER NOT NULL, fecha TEXT NOT NULL, FOREIGN KEY (paciente_id) REFERENCES pacientes(id) ON DELETE RESTRICT, FOREIGN KEY (odontologo_id) REFERENCES odontologos(id) ON DELETE RESTRICT);
"""


def build_legacy_database(path, *, orphan_appointment=False):
    original_hash = generate_password_hash("DUMMY legacy password with enough length")
    with sqlite3.connect(path) as db:
        db.executescript(LEGACY_SCHEMA)
        db.execute("INSERT INTO users VALUES (5, 'dummy-legacy-admin', 'DUMMY Legacy Admin', 'admin@example.test', ?, 'ADMIN')", (original_hash,))
        db.execute("INSERT INTO domicilios VALUES (7, 'DUMMY Legacy Street', 12, 'DUMMY Legacy City', 'DUMMY Region')")
        db.execute("INSERT INTO pacientes VALUES (9, 'DUMMY', 'Legacy Patient', 'legacy@example.test', 87654321, '2025-12-03', 7)")
        db.execute("INSERT INTO odontologos VALUES (11, 'DUMMY', 'Legacy Dentist One', 'DUMMY-LICENSE')")
        db.execute("INSERT INTO odontologos VALUES (12, 'DUMMY', 'Legacy Dentist Two', 'DUMMY-LICENSE')")
        db.execute("INSERT INTO odontologos VALUES (13, 'DUMMY', 'Legacy Dentist Three', '')")
        if orphan_appointment:
            db.execute("PRAGMA foreign_keys=OFF")
            db.execute("INSERT INTO turnos VALUES (20, 999, 11, '2026-11-15')")
        else:
            db.execute("INSERT INTO turnos VALUES (20, 9, 11, '2026-11-15')")
        db.commit()
    return original_hash


def test_legacy_import_preserves_ids_password_hash_and_relationships_across_reopens(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    password_hash = build_legacy_database(path)
    config = {"TESTING": True, "DATABASE_PATH": str(path), "SECRET_KEY": secrets.token_urlsafe(48), "CLOCK": lambda: datetime(2026, 11, 7, 12, tzinfo=timezone.utc)}
    first_app = create_app(config)
    with first_app.app_context():
        db = get_db()
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
        assert dict(db.execute("SELECT id,username,display_name,email,password_hash,role FROM users").fetchone()) == {
            "id": 5, "username": "dummy-legacy-admin", "display_name": "DUMMY Legacy Admin", "email": "admin@example.test", "password_hash": password_hash, "role": "ADMIN",
        }
        assert check_password_hash(db.execute("SELECT password_hash FROM users WHERE id=5").fetchone()[0], "DUMMY legacy password with enough length")
        patient = dict(db.execute("SELECT id,patient_number,address_id,registered_on FROM patients").fetchone())
        assert patient == {"id": 9, "patient_number": "87654321", "address_id": 7, "registered_on": "2025-12-03"}
        assert [tuple(row) for row in db.execute("SELECT id,license_number FROM dentists ORDER BY id")] == [(11, "DUMMY-LICENSE"), (12, "DUMMY-LICENSE"), (13, None)]
        legacy = dict(db.execute("SELECT id,patient_id,dentist_id,start_at,end_at,status,legacy_imported,service_id,room_id FROM appointments").fetchone())
        assert legacy == {"id": 20, "patient_id": 9, "dentist_id": 11, "start_at": "2026-11-15T09:00", "end_at": "2026-11-15T09:30", "status": "booked", "legacy_imported": 1, "service_id": None, "room_id": None}
        assert db.execute("SELECT COUNT(*) FROM appointment_events WHERE appointment_id=20").fetchone()[0] == 1
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []

    for _ in range(2):
        reopened = create_app(config)
        with reopened.app_context():
            db = get_db()
            assert db.execute("SELECT COUNT(*) FROM patients WHERE id=9").fetchone()[0] == 1
            assert db.execute("SELECT COUNT(*) FROM appointments WHERE id=20").fetchone()[0] == 1
            assert db.execute("SELECT COUNT(*) FROM appointment_events WHERE appointment_id=20").fetchone()[0] == 1
            assert db.execute("SELECT password_hash FROM users WHERE id=5").fetchone()[0] == password_hash

    client = first_app.test_client()
    login_page = client.get("/login")
    import re

    token = re.search(rb'name="csrf_token" value="([^"]+)"', login_page.data).group(1).decode()
    logged_in = client.post("/login", data={"username": "dummy-legacy-admin", "password": "DUMMY legacy password with enough length", "csrf_token": token})
    assert logged_in.status_code == 302
    client.environ_base["HTTP_X_CSRF_TOKEN"] = client.get("/api/session").get_json()["csrf_token"]
    legacy_response = client.get("/api/appointments/20")
    assert legacy_response.status_code == 200
    assert legacy_response.get_json()["legacy_imported"] is True
    assert client.post("/api/appointments/20/transitions", json={"status": "confirmed", "version": 1}).status_code == 409

    service = client.post("/api/services", json={"name": "DUMMY Legacy Service", "duration_minutes": 30, "price_cents": 2000}).get_json()
    room = client.post("/api/rooms", json={"name": "DUMMY Replacement Room"}).get_json()
    client.put("/api/dentists/11/services", json={"service_ids": [service["id"]]})
    client.post("/api/shifts", json={"dentist_id": 11, "weekday": 6, "start_time": "09:00", "end_time": "11:00"})
    before_reschedule = client.get("/api/reports/utilization?from=2026-11-15&to=2026-11-15").get_json()
    assert before_reschedule["practitioners"][0]["appointment_minutes"] == 0
    conflict = client.post("/api/appointments", json={"patient_id": 9, "dentist_id": 11, "service_id": service["id"], "room_id": room["id"], "start_at": "2026-11-15T09:00"})
    assert conflict.status_code == 409
    assert conflict.get_json()["error"] == "legacy_date_reserved"
    moved = client.patch("/api/appointments/20", json={"version": 1, "start_at": "2026-11-15T09:30", "service_id": service["id"], "room_id": room["id"]})
    assert moved.status_code == 200, moved.get_json()
    assert moved.get_json()["legacy_imported"] is False
    assert client.post("/api/appointments/20/transitions", json={"status": "confirmed", "version": 2}).status_code == 200
    revenue = client.get("/api/reports/revenue?from=2026-11-15&to=2026-11-15").get_json()
    assert revenue["total_cents"] == 0


def test_failed_legacy_migration_rolls_back_schema_and_records(tmp_path):
    path = tmp_path / "broken-legacy.sqlite3"
    build_legacy_database(path, orphan_appointment=True)
    with pytest.raises(RuntimeError, match="foreign-key check"):
        create_app({"TESTING": True, "DATABASE_PATH": str(path), "SECRET_KEY": secrets.token_urlsafe(48)})
    with sqlite3.connect(path) as db:
        table_names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"users", "domicilios", "pacientes", "odontologos", "turnos"}.issubset(table_names)
        assert "patients" not in table_names
        assert db.execute("SELECT COUNT(*) FROM turnos WHERE id=20 AND paciente_id=999").fetchone()[0] == 1
        assert db.execute("PRAGMA user_version").fetchone()[0] == 0
