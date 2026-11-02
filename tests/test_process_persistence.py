from __future__ import annotations

import http.cookiejar
import json
import os
import secrets
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager

from werkzeug.security import check_password_hash

from clinic import create_app
from clinic.auth import create_account


PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


@contextmanager
def run_server(database_path, secret_key, log_path):
    port = free_port()
    environment = os.environ.copy()
    environment.update(
        {
            "DATABASE_PATH": str(database_path),
            "SECRET_KEY": secret_key,
            "FLASK_DEBUG": "0",
        }
    )
    log_file = open(log_path, "w", encoding="utf-8")
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "flask",
            "--app",
            "clinic:create_app",
            "run",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--no-reload",
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        stdout=log_file,
        stderr=subprocess.STDOUT,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if process.poll() is not None:
                log_file.flush()
                raise AssertionError(f"Flask server stopped during startup:\n{open(log_path, encoding='utf-8').read()}")
            try:
                with urllib.request.urlopen(f"{base_url}/health", timeout=0.5) as response:
                    if response.status == 200:
                        break
            except (OSError, urllib.error.URLError):
                time.sleep(0.1)
        else:
            raise AssertionError(f"Flask server did not become ready:\n{open(log_path, encoding='utf-8').read()}")
        yield base_url
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        log_file.close()


class HttpSession:
    def __init__(self, base_url):
        self.base_url = base_url
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
        )

    def request(self, method, path, payload=None, form=None):
        headers = {}
        if form is not None:
            data = urllib.parse.urlencode(form).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        elif payload is not None:
            data = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        else:
            data = None
        request = urllib.request.Request(
            self.base_url + path, data=data, headers=headers, method=method
        )
        try:
            with self.opener.open(request, timeout=5) as response:
                raw = response.read()
                status = response.status
        except urllib.error.HTTPError as error:
            raw = error.read()
            status = error.code
        try:
            body = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            body = raw.decode("utf-8", errors="replace")
        return status, body


def test_http_crud_and_sqlite_state_survive_server_restart(tmp_path):
    database_path = tmp_path / "persisted-clinic.sqlite3"
    secret_key = secrets.token_urlsafe(48)
    password = secrets.token_urlsafe(24)
    username = "dummy-admin"
    bootstrap_app = create_app(
        {"TESTING": True, "DATABASE_PATH": str(database_path), "SECRET_KEY": secret_key}
    )
    with bootstrap_app.app_context():
        create_account(username, password, "ADMIN", "DUMMY Local Admin", "admin@example.test")
    with sqlite3.connect(database_path) as connection:
        hashed_password = connection.execute(
            "SELECT password_hash FROM users WHERE username = ?", (username,)
        ).fetchone()[0]
    assert hashed_password != password
    assert check_password_hash(hashed_password, password)

    first_log = tmp_path / "server-first.log"
    with run_server(database_path, secret_key, first_log) as base_url:
        http = HttpSession(base_url)
        assert http.request("GET", "/pacientes/listar")[0] == 401
        status, _ = http.request(
            "POST", "/login", form={"username": username, "password": password}
        )
        assert status == 200

        status, dentist = http.request(
            "POST", "/odontologos/", payload={"nombre": "DUMMY", "apellido": "Dentist", "matricula": "TEST-HTTP"}
        )
        assert status == 200
        status, patient = http.request(
            "POST",
            "/pacientes/",
            payload={
                "nombre": "DUMMY",
                "apellido": "Patient",
                "email": "patient@example.test",
                "fechaIngreso": "2026-11-07",
                "domicilio": {"calle": "Example Street", "numero": 20, "localidad": "Testville", "provincia": "Test Province"},
            },
        )
        assert status == 200
        status, appointment = http.request(
            "POST",
            "/turnos",
            payload={"paciente": {"id": patient["id"]}, "odontologo": {"id": dentist["id"]}, "fecha": "2026-11-08"},
        )
        assert status == 200
        assert http.request("GET", "/turnos")[1][0]["id"] == appointment["id"]

    second_log = tmp_path / "server-second.log"
    with run_server(database_path, secret_key, second_log) as base_url:
        http_after_restart = HttpSession(base_url)
        assert http_after_restart.request("GET", "/turnos")[0] == 401
        status, _ = http_after_restart.request(
            "POST", "/login", form={"username": username, "password": password}
        )
        assert status == 200
        dentists = http_after_restart.request("GET", "/odontologos/listar")[1]
        patients = http_after_restart.request("GET", "/pacientes/listar")[1]
        appointments = http_after_restart.request("GET", "/turnos")[1]
        assert dentists[0]["id"] == dentist["id"]
        assert patients[0]["domicilio"]["calle"] == "Example Street"
        assert appointments[0]["id"] == appointment["id"]
        assert appointments[0]["fecha"] == "2026-11-08"

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM turnos").fetchone()[0] == 1
