"""SQLite setup and per-request connections."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from flask import current_app, g

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    email TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('USER', 'ADMIN'))
);

CREATE TABLE IF NOT EXISTS domicilios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    calle TEXT NOT NULL DEFAULT '',
    numero INTEGER,
    localidad TEXT NOT NULL DEFAULT '',
    provincia TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS pacientes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL,
    apellido TEXT NOT NULL,
    email TEXT NOT NULL DEFAULT '',
    dni INTEGER,
    fecha_ingreso TEXT,
    domicilio_id INTEGER UNIQUE,
    FOREIGN KEY (domicilio_id) REFERENCES domicilios(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS odontologos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL,
    apellido TEXT NOT NULL,
    matricula TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS turnos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paciente_id INTEGER NOT NULL,
    odontologo_id INTEGER NOT NULL,
    fecha TEXT NOT NULL,
    FOREIGN KEY (paciente_id) REFERENCES pacientes(id) ON DELETE RESTRICT,
    FOREIGN KEY (odontologo_id) REFERENCES odontologos(id) ON DELETE RESTRICT
);
"""


def connect_db(database_path: str) -> sqlite3.Connection:
    if database_path != ":memory:":
        Path(database_path).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path, detect_types=sqlite3.PARSE_DECLTYPES)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def init_db(app) -> None:
    connection = connect_db(app.config["DATABASE_PATH"])
    try:
        connection.executescript(SCHEMA)
        connection.commit()
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
