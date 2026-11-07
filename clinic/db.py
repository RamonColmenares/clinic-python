"""SQLite connections, schema migrations, and transaction boundaries."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from flask import current_app, g


SCHEMA = """
CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    email TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('USER', 'ADMIN')),
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    session_version INTEGER NOT NULL DEFAULT 1 CHECK (session_version > 0)
);
CREATE TABLE addresses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    street TEXT NOT NULL DEFAULT '',
    number INTEGER CHECK (number IS NULL OR number >= 0),
    city TEXT NOT NULL DEFAULT '',
    region TEXT NOT NULL DEFAULT ''
);
CREATE TABLE patients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    email TEXT NOT NULL DEFAULT '',
    patient_number TEXT,
    registered_on TEXT,
    address_id INTEGER UNIQUE REFERENCES addresses(id) ON DELETE SET NULL,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    reminder_consent INTEGER NOT NULL DEFAULT 0 CHECK (reminder_consent IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE dentists (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    license_number TEXT,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE services (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    duration_minutes INTEGER NOT NULL CHECK (duration_minutes BETWEEN 5 AND 480),
    price_cents INTEGER NOT NULL CHECK (price_cents >= 0),
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE dentist_services (
    dentist_id INTEGER NOT NULL REFERENCES dentists(id) ON DELETE RESTRICT,
    service_id INTEGER NOT NULL REFERENCES services(id) ON DELETE RESTRICT,
    PRIMARY KEY (dentist_id, service_id)
);
CREATE TABLE rooms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE weekly_shifts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dentist_id INTEGER NOT NULL REFERENCES dentists(id) ON DELETE RESTRICT,
    weekday INTEGER NOT NULL CHECK (weekday BETWEEN 0 AND 6),
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    CHECK (start_time < end_time),
    UNIQUE (dentist_id, weekday, start_time, end_time)
);
CREATE TABLE time_off (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dentist_id INTEGER NOT NULL REFERENCES dentists(id) ON DELETE RESTRICT,
    start_at TEXT NOT NULL,
    end_at TEXT NOT NULL,
    reason TEXT NOT NULL,
    CHECK (start_at < end_at)
);
CREATE TABLE appointments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER NOT NULL REFERENCES patients(id) ON DELETE RESTRICT,
    dentist_id INTEGER NOT NULL REFERENCES dentists(id) ON DELETE RESTRICT,
    service_id INTEGER REFERENCES services(id) ON DELETE RESTRICT,
    room_id INTEGER REFERENCES rooms(id) ON DELETE RESTRICT,
    start_at TEXT NOT NULL,
    end_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('booked', 'confirmed', 'checked_in', 'completed', 'cancelled', 'no_show')),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    schedule_revision INTEGER NOT NULL DEFAULT 1 CHECK (schedule_revision > 0),
    reason TEXT NOT NULL DEFAULT '',
    cancellation_reason TEXT,
    legacy_imported INTEGER NOT NULL DEFAULT 0 CHECK (legacy_imported IN (0, 1)),
    duration_minutes INTEGER NOT NULL CHECK (duration_minutes > 0),
    price_cents INTEGER NOT NULL CHECK (price_cents >= 0),
    service_name_snapshot TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (start_at < end_at)
);
CREATE INDEX appointments_dentist_interval ON appointments(dentist_id, start_at, end_at);
CREATE INDEX appointments_patient_interval ON appointments(patient_id, start_at, end_at);
CREATE INDEX appointments_room_interval ON appointments(room_id, start_at, end_at);
CREATE INDEX appointments_day_status ON appointments(start_at, status);
CREATE TABLE appointment_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    appointment_id INTEGER NOT NULL REFERENCES appointments(id) ON DELETE RESTRICT,
    from_status TEXT,
    to_status TEXT NOT NULL,
    actor_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT ''
);
CREATE TABLE treatment_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER NOT NULL REFERENCES patients(id) ON DELETE RESTRICT,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'active', 'completed', 'cancelled')),
    created_at TEXT NOT NULL,
    closed_at TEXT
);
CREATE TABLE treatment_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id INTEGER NOT NULL REFERENCES treatment_plans(id) ON DELETE RESTRICT,
    service_id INTEGER NOT NULL REFERENCES services(id) ON DELETE RESTRICT,
    sequence INTEGER NOT NULL CHECK (sequence > 0),
    tooth_reference TEXT,
    notes TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'planned' CHECK (status IN ('planned', 'scheduled', 'completed', 'cancelled')),
    appointment_id INTEGER UNIQUE REFERENCES appointments(id) ON DELETE RESTRICT,
    estimated_price_cents INTEGER NOT NULL CHECK (estimated_price_cents >= 0),
    service_name_snapshot TEXT NOT NULL DEFAULT '',
    UNIQUE (plan_id, sequence)
);
CREATE TABLE clinical_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER NOT NULL REFERENCES patients(id) ON DELETE RESTRICT,
    appointment_id INTEGER REFERENCES appointments(id) ON DELETE RESTRICT,
    body TEXT NOT NULL,
    author_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    amends_note_id INTEGER REFERENCES clinical_notes(id) ON DELETE RESTRICT
);
CREATE TABLE waitlist_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER NOT NULL REFERENCES patients(id) ON DELETE RESTRICT,
    service_id INTEGER NOT NULL REFERENCES services(id) ON DELETE RESTRICT,
    dentist_id INTEGER REFERENCES dentists(id) ON DELETE RESTRICT,
    earliest_date TEXT NOT NULL,
    latest_date TEXT NOT NULL,
    preferred_start TEXT NOT NULL,
    preferred_end TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'waiting' CHECK (status IN ('waiting', 'booked', 'withdrawn')),
    offered_appointment_id INTEGER UNIQUE REFERENCES appointments(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL,
    CHECK (earliest_date <= latest_date),
    CHECK (preferred_start < preferred_end)
);
CREATE TABLE reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    appointment_id INTEGER NOT NULL REFERENCES appointments(id) ON DELETE RESTRICT,
    kind TEXT NOT NULL CHECK (kind IN ('day_before', 'two_hours_before')),
    due_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'acknowledged', 'cancelled')),
    recipient TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL,
    acknowledged_at TEXT,
    appointment_version INTEGER NOT NULL,
    schedule_revision INTEGER NOT NULL
);
CREATE UNIQUE INDEX reminder_active_revision ON reminders(appointment_id, kind, schedule_revision) WHERE status IN ('pending', 'acknowledged');
CREATE TABLE audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id INTEGER,
    created_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX audit_events_created ON audit_events(created_at, id);
"""


def connect_db(database_path: str, *, enable_wal: bool = True) -> sqlite3.Connection:
    if database_path != ":memory:":
        Path(database_path).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path, timeout=10, isolation_level="DEFERRED")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 10000")
    if enable_wal:
        connection.execute("PRAGMA journal_mode = WAL")
    return connection


def init_db(app) -> None:
    """Initialize empty storage; preserve supported databases and reject other schemas."""
    connection = connect_db(app.config["DATABASE_PATH"], enable_wal=False)
    try:
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version == 2:
            connection.execute("PRAGMA journal_mode = WAL")
            return
        if version != 0:
            raise RuntimeError(f"Database version {version} is unsupported; no conversion was applied.")
        present = connection.execute(
            "SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' LIMIT 1"
        ).fetchone()
        if present is not None:
            raise RuntimeError("Unversioned database schema is unsupported; no conversion was applied.")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("BEGIN IMMEDIATE")
        try:
            for statement in SCHEMA.split(";"):
                if statement.strip():
                    connection.execute(statement)
            if connection.execute("PRAGMA foreign_key_check").fetchall():
                raise RuntimeError("Database initialization failed its foreign-key check.")
            connection.execute("PRAGMA user_version = 2")
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
    finally:
        connection.close()


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = connect_db(current_app.config["DATABASE_PATH"])
    return g.db


def close_db(_error: BaseException | None = None) -> None:
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


@contextmanager
def atomic(connection: sqlite3.Connection | None = None, *, immediate: bool = True) -> Iterator[sqlite3.Connection]:
    """Run a unit of work with rollback semantics and an immediate writer lock."""
    connection = connection or get_db()
    if connection.in_transaction:
        savepoint = f"nested_{id(connection):x}"
        connection.execute(f"SAVEPOINT {savepoint}")
        try:
            yield connection
            connection.execute(f"RELEASE SAVEPOINT {savepoint}")
        except BaseException:
            connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            raise
        return
    connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
    try:
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
