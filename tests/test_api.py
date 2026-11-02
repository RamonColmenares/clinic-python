from __future__ import annotations


def dentist_payload():
    return {"nombre": "DUMMY", "apellido": "Dentist", "matricula": "TEST-001"}


def patient_payload():
    return {
        "nombre": "DUMMY",
        "apellido": "Patient",
        "email": "patient@example.test",
        "dni": 12345678,
        "fechaIngreso": "2026-11-07",
        "domicilio": {
            "calle": "Example Street",
            "numero": 10,
            "localidad": "Testville",
            "provincia": "Test Province",
        },
    }


def test_patient_dentist_and_appointment_crud(client, credentials, sign_in):
    sign_in(client, credentials)

    dentist = client.post("/odontologos/", json=dentist_payload()).get_json()
    assert dentist["id"] > 0
    assert client.get(f"/odontologos/{dentist['id']}").get_json()["matricula"] == "TEST-001"

    patient = client.post("/pacientes/", json=patient_payload()).get_json()
    assert patient["id"] > 0
    assert patient["domicilio"]["calle"] == "Example Street"
    assert client.get(f"/pacientes/{patient['id']}").get_json()["fechaIngreso"] == "2026-11-07"

    appointment = client.post(
        "/turnos/",
        json={
            "paciente": {"id": patient["id"]},
            "odontologo": {"id": dentist["id"]},
            "fecha": "2026-11-08",
        },
    )
    assert appointment.status_code == 200
    appointment_record = appointment.get_json()
    assert appointment_record["paciente"]["nombre"] == "DUMMY"
    assert appointment_record["odontologo"]["matricula"] == "TEST-001"
    appointment_id = appointment_record["id"]

    assert len(client.get("/pacientes/listar").get_json()) == 1
    assert len(client.get("/odontologos/listar").get_json()) == 1
    assert client.get("/turnos").get_json()[0]["fecha"] == "2026-11-08"
    assert client.get(f"/turnos/{appointment_id}").status_code == 200

    updated_patient = patient_payload() | {"id": patient["id"], "nombre": "DUMMY Updated"}
    assert client.put("/pacientes", json=updated_patient).get_json()["nombre"] == "DUMMY Updated"
    updated_dentist = dentist | {"nombre": "DUMMY Updated"}
    assert client.put("/odontologos", json=updated_dentist).get_json()["nombre"] == "DUMMY Updated"
    updated_appointment = {
        "id": appointment_id,
        "paciente": {"id": patient["id"]},
        "odontologo": {"id": dentist["id"]},
        "fecha": "2026-11-09",
    }
    assert client.put("/turnos", json=updated_appointment).get_json()["fecha"] == "2026-11-09"

    invalid_reference = client.post(
        "/turnos",
        json={"paciente": {"id": 99999}, "odontologo": {"id": dentist["id"]}, "fecha": "2026-11-09"},
    )
    assert invalid_reference.status_code == 400
    assert "Patient does not exist" in invalid_reference.get_json()["error"]
    invalid_date = client.post(
        "/turnos",
        json={"paciente": {"id": patient["id"]}, "odontologo": {"id": dentist["id"]}, "fecha": "yesterday"},
    )
    assert invalid_date.status_code == 400
    assert client.put("/odontologos", json=dentist_payload() | {"id": 99999}).status_code == 404

    assert client.delete(f"/pacientes/{patient['id']}").status_code == 409
    assert client.delete(f"/odontologos/{dentist['id']}").status_code == 409
    assert client.delete(f"/turnos/{appointment_id}").status_code == 200
    assert client.delete(f"/pacientes/{patient['id']}").status_code == 200
    assert client.delete(f"/odontologos/{dentist['id']}").status_code == 200
    assert client.get(f"/turnos/{appointment_id}").status_code == 404
    assert client.get(f"/pacientes/{patient['id']}").status_code == 404


def test_invalid_payloads_return_client_errors(client, credentials, sign_in):
    sign_in(client, credentials)
    assert client.post("/odontologos", data="not-json", content_type="text/plain").status_code == 400
    assert client.post("/pacientes", json={"nombre": "DUMMY"}).status_code == 400
    assert client.delete("/turnos/404").status_code == 404
