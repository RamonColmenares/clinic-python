from __future__ import annotations

from clinic.db import get_db


def test_authentication_and_role_permissions(client, credentials, sign_in):
    assert client.get("/pacientes/listar").status_code == 401
    assert client.get("/turnos").status_code == 401
    assert client.get("/").status_code == 302
    assert client.get("/login").status_code == 200

    with client.application.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM users").fetchone()[0] == 2

    sign_in(client, credentials)
    dentist = client.post(
        "/odontologos", json={"nombre": "DUMMY", "apellido": "Dentist", "matricula": "TEST"}
    ).get_json()
    patient = client.post(
        "/pacientes",
        json={"nombre": "DUMMY", "apellido": "Patient", "domicilio": {"calle": "Test Road"}},
    ).get_json()

    client.post("/logout")
    sign_in(client, credentials, "user")
    assert client.get("/odontologos/listar").status_code == 200
    assert client.get("/pacientes/listar").status_code == 200
    assert client.get("/odontologos.html").status_code == 200
    assert client.get("/pacientes.html").status_code == 200
    assert client.get("/turnos.html").status_code == 200
    assert client.get(f"/odontologos/{dentist['id']}").status_code == 403
    assert client.get(f"/pacientes/{patient['id']}").status_code == 403
    assert client.post(
        "/odontologos", json={"nombre": "Other", "apellido": "Person", "matricula": "X"}
    ).status_code == 403
    assert client.put("/pacientes", json=patient | {"nombre": "Not Allowed"}).status_code == 403
    assert client.delete(f"/odontologos/{dentist['id']}").status_code == 403

    appointment = client.post(
        "/turnos",
        json={
            "paciente": {"id": patient["id"]},
            "odontologo": {"id": dentist["id"]},
            "fecha": "2026-11-10",
        },
    )
    assert appointment.status_code == 200
    assert client.put(
        "/turnos",
        json={"id": appointment.get_json()["id"], "paciente": {"id": patient["id"]}, "odontologo": {"id": dentist["id"]}, "fecha": "2026-11-11"},
    ).status_code == 200


def test_browser_pages_include_local_functional_assets(client, credentials, sign_in):
    sign_in(client, credentials)
    home = client.get("/")
    assert home.status_code == 200
    assert b"href=\"/odontologos.html\"" in home.data
    assert b"href=\"/pacientes.html\"" in home.data
    assert b"href=\"/turnos.html\"" in home.data

    for path, marker in (
        ("/odontologos.html", b"data-page=\"dentists\""),
        ("/alta-odontologos.html", b"data-entity=\"dentist\""),
        ("/pacientes.html", b"data-page=\"patients\""),
        ("/alta-paciente.html", b"data-entity=\"patient\""),
        ("/turnos.html", b"data-page=\"appointments\""),
        ("/alta-turno.html", b"data-entity=\"appointment\""),
    ):
        page_response = client.get(path)
        assert page_response.status_code == 200
        assert marker in page_response.data
        assert b"maxcdn.bootstrapcdn.com" not in page_response.data
        assert b"ajax.googleapis.com" not in page_response.data

    script = client.get("/static/clinic.js")
    assert script.status_code == 200
    for endpoint in (b"/odontologos/listar", b"/pacientes/listar", b"/turnos"):
        assert endpoint in script.data
