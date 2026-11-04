from __future__ import annotations

import http.cookiejar
import json
import os
import re
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
from datetime import date, datetime, timedelta, timezone

from werkzeug.security import check_password_hash

from clinic import create_app
from clinic.auth import create_account


PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


@contextmanager
def run_server(database_path, instance_path, log_path):
    port = free_port()
    environment = os.environ.copy()
    environment.pop("SECRET_KEY", None)
    environment.update({"DATABASE_PATH": str(database_path), "INSTANCE_PATH": str(instance_path), "FLASK_DEBUG": "0"})
    log_file = open(log_path, "w", encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, "-m", "flask", "--app", "clinic:create_app", "run", "--host", "127.0.0.1", "--port", str(port), "--no-reload"],
        cwd=PROJECT_ROOT, env=environment, stdout=log_file, stderr=subprocess.STDOUT,
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
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.csrf_token = ""

    def request(self, method, path, payload=None, form=None):
        headers = {}
        if self.csrf_token:
            headers["X-CSRF-Token"] = self.csrf_token
        if form is not None:
            data = urllib.parse.urlencode(form).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        elif payload is not None:
            data = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        else:
            data = None
        request = urllib.request.Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=5) as response:
                raw, status = response.read(), response.status
        except urllib.error.HTTPError as error:
            raw, status = error.read(), error.code
        try:
            body = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            body = raw.decode("utf-8", errors="replace")
        if path == "/api/session" and status == 200:
            self.csrf_token = body["csrf_token"]
        return status, body


def login(http: HttpSession, username: str, password: str):
    _, page = http.request("GET", "/login")
    assert isinstance(page, str)
    token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
    status, _ = http.request("POST", "/login", form={"username": username, "password": password, "csrf_token": token})
    assert status == 200
    status, session = http.request("GET", "/api/session")
    assert status == 200
    http.csrf_token = session["csrf_token"]


def next_monday() -> str:
    current = datetime.now(timezone.utc).date()
    distance = (0 - current.weekday()) % 7 or 7
    return (current + timedelta(days=distance)).isoformat()


def test_http_workflow_and_generated_session_secret_survive_independent_server_restart(tmp_path, monkeypatch):
    database_path = tmp_path / "clinic.sqlite3"
    instance_path = tmp_path / "private-instance"
    monkeypatch.setenv("INSTANCE_PATH", str(instance_path))
    monkeypatch.delenv("SECRET_KEY", raising=False)
    username = "dummy-process-admin"
    password = secrets.token_urlsafe(24)
    bootstrap_app = create_app({"TESTING": True, "DATABASE_PATH": str(database_path)})
    with bootstrap_app.app_context():
        create_account(username, password, "ADMIN", "DUMMY Process Admin", "admin@example.test")
    secret_file = instance_path / "session-secret"
    original_secret = secret_file.read_text(encoding="ascii")
    assert len(original_secret.strip()) >= 48
    assert secret_file.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(database_path) as connection:
        stored_hash = connection.execute("SELECT password_hash FROM users WHERE username=?", (username,)).fetchone()[0]
    assert stored_hash != password
    assert check_password_hash(stored_hash, password)

    date_value = next_monday()
    weekday = date.fromisoformat(date_value).weekday()
    first_log = tmp_path / "server-first.log"
    with run_server(database_path, instance_path, first_log) as base_url:
        http = HttpSession(base_url)
        assert http.request("GET", "/api/patients")[0] == 401
        login(http, username, password)
        home_status, home = http.request("GET", "/")
        assert home_status == 200
        assert "Clinic Desk" in home and "Appointment agenda" in home
        script_status, script = http.request("GET", "/static/clinic.js")
        assert script_status == 200 and "/api/availability" in script
        status, dentist = http.request("POST", "/api/dentists", payload={"first_name": "DUMMY", "last_name": "Process Dentist", "license_number": "DUMMY-HTTP"})
        assert status == 201
        status, service = http.request("POST", "/api/services", payload={"name": "DUMMY Process Checkup", "duration_minutes": 30, "price_cents": 12345})
        assert status == 201
        status, room = http.request("POST", "/api/rooms", payload={"name": "DUMMY Process Room"})
        assert status == 201
        assert http.request("PUT", f"/api/dentists/{dentist['id']}/services", payload={"service_ids": [service["id"]]})[0] == 200
        assert http.request("POST", "/api/shifts", payload={"dentist_id": dentist["id"], "weekday": weekday, "start_time": "09:00", "end_time": "12:00"})[0] == 201
        status, patient = http.request("POST", "/api/patients", payload={"first_name": "DUMMY", "last_name": "Process Patient", "email": "process@example.test", "patient_number": "DUMMY-HTTP-001", "address": {"street": "DUMMY Process Street", "city": "DUMMY City"}})
        assert status == 201
        status, appointment = http.request("POST", "/api/appointments", payload={"patient_id": patient["id"], "dentist_id": dentist["id"], "service_id": service["id"], "room_id": room["id"], "start_at": f"{date_value}T09:00"})
        assert status == 201, appointment
        assert http.request("GET", f"/api/appointments/{appointment['id']}")[1]["start_at"] == f"{date_value}T09:00"

    assert secret_file.read_text(encoding="ascii") == original_secret
    second_log = tmp_path / "server-second.log"
    with run_server(database_path, instance_path, second_log) as base_url:
        http.base_url = base_url
        assert http.request("GET", "/api/session")[0] == 200
        assert http.request("GET", "/api/patients?search=Process")[1]["items"][0]["id"] == patient["id"]
        assert http.request("GET", "/api/dentists")[1]["items"][0]["id"] == dentist["id"]
        persisted = http.request("GET", "/api/appointments")[1]
        assert persisted["total"] == 1
        assert persisted["items"][0]["id"] == appointment["id"]
        assert persisted["items"][0]["patient_name"] == "DUMMY Process Patient"
    assert secret_file.read_text(encoding="ascii") == original_secret

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM appointments").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM patients").fetchone()[0] == 1
