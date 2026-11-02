"""JSON endpoints for patients, dentists, and appointments."""

from __future__ import annotations

import sqlite3
from datetime import date
from functools import wraps

from flask import Blueprint, jsonify, request

from .auth import require_roles
from .db import get_db

bp = Blueprint("api", __name__)


class InvalidInput(ValueError):
    """A request body does not match the API contract."""


def json_body() -> dict:
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise InvalidInput("Request body must be a JSON object.")
    return body


def required_text(body: dict, field: str) -> str:
    value = body.get(field)
    if not isinstance(value, str) or not value.strip():
        raise InvalidInput(f"'{field}' is required and must be text.")
    return value.strip()


def optional_text(body: dict, field: str, default: str = "") -> str:
    value = body.get(field, default)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise InvalidInput(f"'{field}' must be text.")
    return value.strip()


def optional_integer(body: dict, field: str) -> int | None:
    value = body.get(field)
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise InvalidInput(f"'{field}' must be an integer.")
    try:
        return int(value)
    except (TypeError, ValueError) as error:
        raise InvalidInput(f"'{field}' must be an integer.") from error


def required_id(value, field: str) -> int:
    if value is None or isinstance(value, bool):
        raise InvalidInput(f"'{field}' is required and must be an integer.")
    try:
        result = int(value)
    except (TypeError, ValueError) as error:
        raise InvalidInput(f"'{field}' is required and must be an integer.") from error
    if result < 1:
        raise InvalidInput(f"'{field}' must be a positive integer.")
    return result


def required_date(body: dict, field: str = "fecha") -> str:
    value = body.get(field)
    if not isinstance(value, str):
        raise InvalidInput(f"'{field}' is required in YYYY-MM-DD format.")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise InvalidInput(f"'{field}' is required in YYYY-MM-DD format.") from error
    if parsed.isoformat() != value:
        raise InvalidInput(f"'{field}' is required in YYYY-MM-DD format.")
    return value


def handle_input_errors(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        try:
            return view(*args, **kwargs)
        except InvalidInput as error:
            return jsonify(error=str(error)), 400
        except sqlite3.IntegrityError:
            get_db().rollback()
            return jsonify(error="The record conflicts with existing relationships."), 409

    return wrapped


def patient_record(row) -> dict:
    if row is None:
        return None
    return {
        "id": row["id"],
        "nombre": row["nombre"],
        "apellido": row["apellido"],
        "email": row["email"],
        "dni": row["dni"],
        "fechaIngreso": row["fecha_ingreso"],
        "domicilio": None
        if row["domicilio_id"] is None
        else {
            "id": row["domicilio_id"],
            "calle": row["calle"],
            "numero": row["numero"],
            "localidad": row["localidad"],
            "provincia": row["provincia"],
        },
    }


def dentist_record(row) -> dict:
    return {
        "id": row["id"],
        "nombre": row["nombre"],
        "apellido": row["apellido"],
        "matricula": row["matricula"],
    }


def find_patient(patient_id: int):
    return get_db().execute(
        """
        SELECT p.id, p.nombre, p.apellido, p.email, p.dni, p.fecha_ingreso,
               p.domicilio_id, d.calle, d.numero, d.localidad, d.provincia
        FROM pacientes AS p LEFT JOIN domicilios AS d ON d.id = p.domicilio_id
        WHERE p.id = ?
        """,
        (patient_id,),
    ).fetchone()


def find_dentist(dentist_id: int):
    return get_db().execute(
        "SELECT id, nombre, apellido, matricula FROM odontologos WHERE id = ?",
        (dentist_id,),
    ).fetchone()


def appointment_record(row) -> dict:
    return {
        "id": row["id"],
        "fecha": row["fecha"],
        "paciente": {
            "id": row["paciente_id"],
            "nombre": row["paciente_nombre"],
            "apellido": row["paciente_apellido"],
            "email": row["paciente_email"],
            "dni": row["paciente_dni"],
            "fechaIngreso": row["paciente_fecha_ingreso"],
            "domicilio": None
            if row["paciente_domicilio_id"] is None
            else {
                "id": row["paciente_domicilio_id"],
                "calle": row["calle"],
                "numero": row["numero"],
                "localidad": row["localidad"],
                "provincia": row["provincia"],
            },
        },
        "odontologo": {
            "id": row["odontologo_id"],
            "nombre": row["odontologo_nombre"],
            "apellido": row["odontologo_apellido"],
            "matricula": row["matricula"],
        },
    }


def find_appointment(appointment_id: int):
    return get_db().execute(
        """
        SELECT t.id, t.fecha, p.id AS paciente_id, p.nombre AS paciente_nombre,
               p.apellido AS paciente_apellido, p.email AS paciente_email,
               p.dni AS paciente_dni, p.fecha_ingreso AS paciente_fecha_ingreso,
               p.domicilio_id AS paciente_domicilio_id, d.calle, d.numero,
               d.localidad, d.provincia, o.id AS odontologo_id,
               o.nombre AS odontologo_nombre, o.apellido AS odontologo_apellido,
               o.matricula
        FROM turnos AS t
        JOIN pacientes AS p ON p.id = t.paciente_id
        LEFT JOIN domicilios AS d ON d.id = p.domicilio_id
        JOIN odontologos AS o ON o.id = t.odontologo_id
        WHERE t.id = ?
        """,
        (appointment_id,),
    ).fetchone()


def address_values(value) -> tuple[str, int | None, str, str] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise InvalidInput("'domicilio' must be an object or null.")
    address = {
        "calle": value.get("calle", ""),
        "numero": value.get("numero"),
        "localidad": value.get("localidad", ""),
        "provincia": value.get("provincia", ""),
    }
    for field in ("calle", "localidad", "provincia"):
        if address[field] is None:
            address[field] = ""
        if not isinstance(address[field], str):
            raise InvalidInput(f"'domicilio.{field}' must be text.")
        address[field] = address[field].strip()
    number = address["numero"]
    if number in (None, ""):
        number = None
    elif isinstance(number, bool):
        raise InvalidInput("'domicilio.numero' must be an integer.")
    else:
        try:
            number = int(number)
        except (TypeError, ValueError) as error:
            raise InvalidInput("'domicilio.numero' must be an integer.") from error
    return address["calle"], number, address["localidad"], address["provincia"]


def save_address(values, existing_id: int | None = None) -> int | None:
    db = get_db()
    if values is None:
        if existing_id is not None:
            db.execute("DELETE FROM domicilios WHERE id = ?", (existing_id,))
        return None
    if existing_id is None:
        cursor = db.execute(
            "INSERT INTO domicilios (calle, numero, localidad, provincia) VALUES (?, ?, ?, ?)",
            values,
        )
        return int(cursor.lastrowid)
    db.execute(
        "UPDATE domicilios SET calle = ?, numero = ?, localidad = ?, provincia = ? WHERE id = ?",
        (*values, existing_id),
    )
    return existing_id


@bp.get("/pacientes/listar")
@require_roles("USER", "ADMIN")
def list_patients():
    rows = get_db().execute(
        """
        SELECT p.id, p.nombre, p.apellido, p.email, p.dni, p.fecha_ingreso,
               p.domicilio_id, d.calle, d.numero, d.localidad, d.provincia
        FROM pacientes AS p LEFT JOIN domicilios AS d ON d.id = p.domicilio_id
        ORDER BY p.id
        """
    ).fetchall()
    return jsonify([patient_record(row) for row in rows])


@bp.route("/pacientes", methods=["POST"], strict_slashes=False)
@require_roles("ADMIN")
@handle_input_errors
def create_patient():
    body = json_body()
    name = required_text(body, "nombre")
    surname = required_text(body, "apellido")
    email = optional_text(body, "email")
    dni = optional_integer(body, "dni")
    entered = body.get("fechaIngreso")
    if entered is not None:
        entered = required_date({"fechaIngreso": entered}, "fechaIngreso")
    address_id = save_address(address_values(body.get("domicilio")))
    db = get_db()
    cursor = db.execute(
        """INSERT INTO pacientes (nombre, apellido, email, dni, fecha_ingreso, domicilio_id)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (name, surname, email, dni, entered, address_id),
    )
    db.commit()
    return jsonify(patient_record(find_patient(int(cursor.lastrowid))))


@bp.get("/pacientes/<int:patient_id>")
@require_roles("ADMIN")
def get_patient(patient_id: int):
    row = find_patient(patient_id)
    if row is None:
        return jsonify(error="Patient not found."), 404
    return jsonify(patient_record(row))


@bp.route("/pacientes", methods=["PUT"], strict_slashes=False)
@require_roles("ADMIN")
@handle_input_errors
def update_patient():
    body = json_body()
    patient_id = required_id(body.get("id"), "id")
    existing = find_patient(patient_id)
    if existing is None:
        return jsonify(error="Patient not found."), 404
    name = required_text(body, "nombre")
    surname = required_text(body, "apellido")
    email = optional_text(body, "email")
    dni = optional_integer(body, "dni")
    entered = body.get("fechaIngreso")
    if entered is not None:
        entered = required_date({"fechaIngreso": entered}, "fechaIngreso")
    address_id = existing["domicilio_id"]
    if "domicilio" in body:
        address_id = save_address(address_values(body.get("domicilio")), address_id)
    db = get_db()
    db.execute(
        """UPDATE pacientes SET nombre = ?, apellido = ?, email = ?, dni = ?,
           fecha_ingreso = ?, domicilio_id = ? WHERE id = ?""",
        (name, surname, email, dni, entered, address_id, patient_id),
    )
    db.commit()
    return jsonify(patient_record(find_patient(patient_id)))


@bp.delete("/pacientes/<int:patient_id>")
@require_roles("ADMIN")
@handle_input_errors
def delete_patient(patient_id: int):
    db = get_db()
    existing = db.execute(
        "SELECT domicilio_id FROM pacientes WHERE id = ?", (patient_id,)
    ).fetchone()
    if existing is None:
        return jsonify(error="Patient not found."), 404
    db.execute("DELETE FROM pacientes WHERE id = ?", (patient_id,))
    if existing["domicilio_id"] is not None:
        db.execute("DELETE FROM domicilios WHERE id = ?", (existing["domicilio_id"],))
    db.commit()
    return jsonify(message="Se elimino el paciente sin problemas")


@bp.get("/odontologos/listar")
@require_roles("USER", "ADMIN")
def list_dentists():
    rows = get_db().execute(
        "SELECT id, nombre, apellido, matricula FROM odontologos ORDER BY id"
    ).fetchall()
    return jsonify([dentist_record(row) for row in rows])


@bp.route("/odontologos", methods=["POST"], strict_slashes=False)
@require_roles("ADMIN")
@handle_input_errors
def create_dentist():
    body = json_body()
    values = (
        required_text(body, "nombre"),
        required_text(body, "apellido"),
        optional_text(body, "matricula"),
    )
    db = get_db()
    cursor = db.execute(
        "INSERT INTO odontologos (nombre, apellido, matricula) VALUES (?, ?, ?)",
        values,
    )
    db.commit()
    return jsonify(dentist_record(find_dentist(int(cursor.lastrowid))))


@bp.get("/odontologos/<int:dentist_id>")
@require_roles("ADMIN")
def get_dentist(dentist_id: int):
    row = find_dentist(dentist_id)
    if row is None:
        return jsonify(error="Dentist not found."), 404
    return jsonify(dentist_record(row))


@bp.route("/odontologos", methods=["PUT"], strict_slashes=False)
@require_roles("ADMIN")
@handle_input_errors
def update_dentist():
    body = json_body()
    dentist_id = required_id(body.get("id"), "id")
    if find_dentist(dentist_id) is None:
        return jsonify(error="Dentist not found."), 404
    values = (
        required_text(body, "nombre"),
        required_text(body, "apellido"),
        optional_text(body, "matricula"),
        dentist_id,
    )
    db = get_db()
    db.execute(
        "UPDATE odontologos SET nombre = ?, apellido = ?, matricula = ? WHERE id = ?",
        values,
    )
    db.commit()
    return jsonify(dentist_record(find_dentist(dentist_id)))


@bp.delete("/odontologos/<int:dentist_id>")
@require_roles("ADMIN")
@handle_input_errors
def delete_dentist(dentist_id: int):
    db = get_db()
    cursor = db.execute("DELETE FROM odontologos WHERE id = ?", (dentist_id,))
    if cursor.rowcount == 0:
        return jsonify(error="Dentist not found."), 404
    db.commit()
    return jsonify(message="Se elimino el odontologo sin problemas")


def appointment_ids(body: dict) -> tuple[int, int]:
    patient = body.get("paciente")
    dentist = body.get("odontologo")
    if not isinstance(patient, dict) or not isinstance(dentist, dict):
        raise InvalidInput("'paciente' and 'odontologo' must include an id.")
    patient_id = required_id(patient.get("id"), "paciente.id")
    dentist_id = required_id(dentist.get("id"), "odontologo.id")
    db = get_db()
    if db.execute("SELECT 1 FROM pacientes WHERE id = ?", (patient_id,)).fetchone() is None:
        raise InvalidInput("Patient does not exist.")
    if db.execute("SELECT 1 FROM odontologos WHERE id = ?", (dentist_id,)).fetchone() is None:
        raise InvalidInput("Dentist does not exist.")
    return patient_id, dentist_id


@bp.get("/turnos")
@require_roles("USER", "ADMIN")
def list_appointments():
    rows = get_db().execute(
        """
        SELECT t.id FROM turnos AS t ORDER BY t.fecha, t.id
        """
    ).fetchall()
    return jsonify([appointment_record(find_appointment(row["id"])) for row in rows])


@bp.route("/turnos", methods=["POST"], strict_slashes=False)
@require_roles("USER", "ADMIN")
@handle_input_errors
def create_appointment():
    body = json_body()
    patient_id, dentist_id = appointment_ids(body)
    appointment_date = required_date(body)
    db = get_db()
    cursor = db.execute(
        "INSERT INTO turnos (paciente_id, odontologo_id, fecha) VALUES (?, ?, ?)",
        (patient_id, dentist_id, appointment_date),
    )
    db.commit()
    return jsonify(appointment_record(find_appointment(int(cursor.lastrowid))))


@bp.get("/turnos/<int:appointment_id>")
@require_roles("USER", "ADMIN")
def get_appointment(appointment_id: int):
    row = find_appointment(appointment_id)
    if row is None:
        return jsonify(error="Appointment not found."), 404
    return jsonify(appointment_record(row))


@bp.route("/turnos", methods=["PUT"], strict_slashes=False)
@require_roles("USER", "ADMIN")
@handle_input_errors
def update_appointment():
    body = json_body()
    appointment_id = required_id(body.get("id"), "id")
    if find_appointment(appointment_id) is None:
        return jsonify(error="Appointment not found."), 404
    patient_id, dentist_id = appointment_ids(body)
    appointment_date = required_date(body)
    db = get_db()
    db.execute(
        "UPDATE turnos SET paciente_id = ?, odontologo_id = ?, fecha = ? WHERE id = ?",
        (patient_id, dentist_id, appointment_date, appointment_id),
    )
    db.commit()
    return jsonify(appointment_record(find_appointment(appointment_id)))


@bp.delete("/turnos/<int:appointment_id>")
@require_roles("USER", "ADMIN")
@handle_input_errors
def delete_appointment(appointment_id: int):
    db = get_db()
    cursor = db.execute("DELETE FROM turnos WHERE id = ?", (appointment_id,))
    if cursor.rowcount == 0:
        return jsonify(error="Appointment not found."), 404
    db.commit()
    return jsonify(message="Se elimino el Turno sin problemas")
