"""Patient, practitioner, service, room, qualification, and roster catalog."""

from __future__ import annotations

import json
import sqlite3

from flask import current_app, g

from .db import atomic, get_db
from .validation import conflict, integer, iso_date, iso_time, local_datetime, missing, text, boolean, invalid


ACTIVE_VISIT_SQL = "status IN ('booked', 'confirmed', 'checked_in')"


def now() -> str:
    from datetime import datetime, timezone

    clock = current_app.config.get("CLOCK")
    moment = clock() if callable(clock) else datetime.now(timezone.utc)
    return moment.astimezone(timezone.utc).replace(second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M")


def actor_id() -> int | None:
    account = getattr(g, "account", None)
    return int(account["id"]) if account is not None else None


def audit(action: str, entity_type: str, entity_id: int | None, **metadata) -> None:
    allowed = {"status", "from_status", "to_status", "version", "related_id", "count", "active"}
    clean = {key: value for key, value in metadata.items() if key in allowed}
    get_db().execute(
        "INSERT INTO audit_events (actor_id, action, entity_type, entity_id, created_at, metadata_json) VALUES (?, ?, ?, ?, ?, ?)",
        (actor_id(), action, entity_type, entity_id, now(), json.dumps(clean, sort_keys=True)),
    )


def _dict(row) -> dict | None:
    return dict(row) if row is not None else None


def _address_fields(value) -> tuple[str, int | None, str, str] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise invalid("'address' must be an object or null.")
    unknown = set(value) - {"street", "number", "city", "region"}
    if unknown:
        raise invalid(f"Unknown address field(s): {', '.join(sorted(unknown))}.")
    street = text(value.get("street", ""), "address.street", required=False, maximum=160)
    city = text(value.get("city", ""), "address.city", required=False, maximum=120)
    region = text(value.get("region", ""), "address.region", required=False, maximum=120)
    number_value = value.get("number")
    number = None if number_value in (None, "") else integer(number_value, "address.number", minimum=0, maximum=999999)
    return street, number, city, region


def _save_address(values: tuple[str, int | None, str, str] | None, address_id: int | None = None) -> int | None:
    db = get_db()
    if values is None:
        if address_id is not None:
            db.execute("DELETE FROM addresses WHERE id = ?", (address_id,))
        return None
    if address_id is None:
        return int(db.execute("INSERT INTO addresses (street, number, city, region) VALUES (?, ?, ?, ?)", values).lastrowid)
    db.execute("UPDATE addresses SET street = ?, number = ?, city = ?, region = ? WHERE id = ?", (*values, address_id))
    return address_id


def _patient_row(patient_id: int):
    return get_db().execute(
        """SELECT p.*, a.street, a.number, a.city, a.region
           FROM patients p LEFT JOIN addresses a ON a.id=p.address_id WHERE p.id=?""",
        (patient_id,),
    ).fetchone()


def patient_record(row) -> dict | None:
    if row is None:
        return None
    return {
        "id": row["id"], "first_name": row["first_name"], "last_name": row["last_name"],
        "email": row["email"], "patient_number": row["patient_number"],
        "registered_on": row["registered_on"], "active": bool(row["active"]),
        "reminder_consent": bool(row["reminder_consent"]),
        "address": None if row["address_id"] is None else {
            "id": row["address_id"], "street": row["street"], "number": row["number"],
            "city": row["city"], "region": row["region"],
        },
    }


def create_patient(data: dict) -> dict:
    first = text(data.get("first_name"), "first_name", maximum=100)
    last = text(data.get("last_name"), "last_name", maximum=100)
    email = text(data.get("email", ""), "email", required=False, maximum=254)
    number = text(data.get("patient_number"), "patient_number", required=False, maximum=80) or None
    registered = data.get("registered_on")
    if registered is not None:
        registered = iso_date(registered, "registered_on")
    consent = boolean(data.get("reminder_consent", False), "reminder_consent")
    address = _address_fields(data.get("address"))
    db = get_db()
    with atomic(db):
        if number and db.execute("SELECT 1 FROM patients WHERE patient_number=? LIMIT 1", (number,)).fetchone():
            raise conflict("That patient number is already in use.")
        address_id = _save_address(address)
        stamp = now()
        cursor = db.execute(
            """INSERT INTO patients (first_name,last_name,email,patient_number,registered_on,address_id,
               reminder_consent,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)""",
            (first, last, email, number, registered, address_id, int(consent), stamp, stamp),
        )
        patient_id = int(cursor.lastrowid)
        audit("created", "patient", patient_id, active=True)
    return patient_record(_patient_row(patient_id))


def update_patient(patient_id: int, data: dict) -> dict:
    allowed = {"first_name", "last_name", "email", "patient_number", "registered_on", "address", "active", "reminder_consent"}
    unknown = set(data) - allowed
    if unknown:
        raise invalid(f"Unknown field(s): {', '.join(sorted(unknown))}.")
    db = get_db()
    with atomic(db):
        row = _patient_row(patient_id)
        if row is None:
            raise missing("Patient")
        values = {
            "first_name": row["first_name"], "last_name": row["last_name"], "email": row["email"],
            "patient_number": row["patient_number"], "registered_on": row["registered_on"],
            "address_id": row["address_id"], "active": row["active"],
            "reminder_consent": row["reminder_consent"],
        }
        if "first_name" in data:
            values["first_name"] = text(data["first_name"], "first_name", maximum=100)
        if "last_name" in data:
            values["last_name"] = text(data["last_name"], "last_name", maximum=100)
        if "email" in data:
            values["email"] = text(data["email"], "email", required=False, maximum=254)
        if "patient_number" in data:
            values["patient_number"] = text(data["patient_number"], "patient_number", required=False, maximum=80) or None
            if values["patient_number"] and db.execute("SELECT 1 FROM patients WHERE patient_number=? AND id<>? LIMIT 1", (values["patient_number"], patient_id)).fetchone():
                raise conflict("That patient number is already in use.")
        if "registered_on" in data:
            values["registered_on"] = None if data["registered_on"] in (None, "") else iso_date(data["registered_on"], "registered_on")
        if "address" in data:
            values["address_id"] = _save_address(_address_fields(data["address"]), row["address_id"])
        if "active" in data:
            values["active"] = int(boolean(data["active"], "active"))
            if not values["active"]:
                future = db.execute(
                    f"SELECT 1 FROM appointments WHERE patient_id=? AND {ACTIVE_VISIT_SQL} AND start_at>? LIMIT 1",
                    (patient_id, now()),
                ).fetchone()
                if future:
                    raise conflict("A patient with future appointments cannot be archived.")
        if "reminder_consent" in data:
            values["reminder_consent"] = int(boolean(data["reminder_consent"], "reminder_consent"))
        if (not values["active"] or not values["reminder_consent"] or values["email"] != row["email"]) and (row["active"] or row["reminder_consent"] or values["email"] != row["email"]):
            affected = db.execute("SELECT id FROM appointments WHERE patient_id=? AND status IN ('booked','confirmed')", (patient_id,)).fetchall()
            for visit in affected:
                pending = db.execute("SELECT id FROM reminders WHERE appointment_id=? AND status='pending'", (visit["id"],)).fetchall()
                db.execute("UPDATE reminders SET status='cancelled' WHERE appointment_id=? AND status='pending'", (visit["id"],))
                for reminder in pending:
                    audit("cancelled_patient_settings_changed", "reminder", reminder["id"], from_status="pending", to_status="cancelled", related_id=visit["id"])
        db.execute(
            """UPDATE patients SET first_name=?,last_name=?,email=?,patient_number=?,registered_on=?,address_id=?,
               active=?,reminder_consent=?,updated_at=? WHERE id=?""",
            (values["first_name"], values["last_name"], values["email"], values["patient_number"], values["registered_on"], values["address_id"], values["active"], values["reminder_consent"], now(), patient_id),
        )
        audit("updated", "patient", patient_id, active=bool(values["active"]))
    return patient_record(_patient_row(patient_id))


def list_patients(search: str = "", active: bool | None = None, limit: int = 50, offset: int = 0) -> dict:
    clauses: list[str] = []
    values: list = []
    if search:
        clauses.append("(first_name LIKE ? ESCAPE '\\' OR last_name LIKE ? ESCAPE '\\' OR patient_number LIKE ? ESCAPE '\\')")
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        values.extend([pattern, pattern, pattern])
    if active is not None:
        clauses.append("active=?")
        values.append(int(active))
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    db = get_db()
    total = int(db.execute("SELECT COUNT(*) FROM patients" + where, values).fetchone()[0])
    rows = db.execute("SELECT id FROM patients" + where + " ORDER BY last_name,first_name,id LIMIT ? OFFSET ?", (*values, limit, offset)).fetchall()
    return {"items": [patient_record(_patient_row(row["id"])) for row in rows], "total": total, "limit": limit, "offset": offset}


def _dentist(dentist_id: int):
    return get_db().execute("SELECT * FROM dentists WHERE id=?", (dentist_id,)).fetchone()


def dentist_record(row) -> dict | None:
    if row is None:
        return None
    return {"id": row["id"], "first_name": row["first_name"], "last_name": row["last_name"], "license_number": row["license_number"] or "", "active": bool(row["active"])}


def create_dentist(data: dict) -> dict:
    first = text(data.get("first_name"), "first_name", maximum=100)
    last = text(data.get("last_name"), "last_name", maximum=100)
    license_number = text(data.get("license_number", ""), "license_number", required=False, maximum=80) or None
    db = get_db()
    with atomic(db):
        if license_number and db.execute("SELECT 1 FROM dentists WHERE license_number=? LIMIT 1", (license_number,)).fetchone():
            raise conflict("That license number is already in use.")
        stamp = now()
        cursor = db.execute("INSERT INTO dentists (first_name,last_name,license_number,created_at,updated_at) VALUES (?,?,?,?,?)", (first, last, license_number, stamp, stamp))
        dentist_id = int(cursor.lastrowid)
        audit("created", "dentist", dentist_id, active=True)
    return dentist_record(_dentist(dentist_id))


def update_dentist(dentist_id: int, data: dict) -> dict:
    allowed = {"first_name", "last_name", "license_number", "active"}
    unknown = set(data) - allowed
    if unknown:
        raise invalid(f"Unknown field(s): {', '.join(sorted(unknown))}.")
    db = get_db()
    with atomic(db):
        row = _dentist(dentist_id)
        if row is None:
            raise missing("Dentist")
        values = [row["first_name"], row["last_name"], row["license_number"], row["active"]]
        if "first_name" in data:
            values[0] = text(data["first_name"], "first_name", maximum=100)
        if "last_name" in data:
            values[1] = text(data["last_name"], "last_name", maximum=100)
        if "license_number" in data:
            values[2] = text(data["license_number"], "license_number", required=False, maximum=80) or None
            if values[2] and db.execute("SELECT 1 FROM dentists WHERE license_number=? AND id<>? LIMIT 1", (values[2], dentist_id)).fetchone():
                raise conflict("That license number is already in use.")
        if "active" in data:
            values[3] = int(boolean(data["active"], "active"))
            if not values[3] and db.execute(f"SELECT 1 FROM appointments WHERE dentist_id=? AND {ACTIVE_VISIT_SQL} AND start_at>? LIMIT 1", (dentist_id, now())).fetchone():
                raise conflict("A practitioner with future appointments cannot be archived.")
        db.execute("UPDATE dentists SET first_name=?,last_name=?,license_number=?,active=?,updated_at=? WHERE id=?", (*values, now(), dentist_id))
        audit("updated", "dentist", dentist_id, active=bool(values[3]))
    return dentist_record(_dentist(dentist_id))


def list_dentists(active: bool | None = None) -> list[dict]:
    sql = "SELECT * FROM dentists"
    args: tuple = ()
    if active is not None:
        sql += " WHERE active=?"
        args = (int(active),)
    return [dentist_record(row) for row in get_db().execute(sql + " ORDER BY last_name,first_name,id", args)]


def _service(service_id: int):
    return get_db().execute("SELECT * FROM services WHERE id=?", (service_id,)).fetchone()


def service_record(row) -> dict | None:
    if row is None:
        return None
    return {"id": row["id"], "name": row["name"], "duration_minutes": row["duration_minutes"], "price_cents": row["price_cents"], "active": bool(row["active"])}


def create_service(data: dict) -> dict:
    name = text(data.get("name"), "name", maximum=120)
    duration = integer(data.get("duration_minutes"), "duration_minutes", minimum=5, maximum=480)
    price = integer(data.get("price_cents"), "price_cents", minimum=0, maximum=1_000_000_000)
    db = get_db()
    with atomic(db):
        stamp = now()
        service_id = int(db.execute("INSERT INTO services (name,duration_minutes,price_cents,created_at,updated_at) VALUES (?,?,?,?,?)", (name, duration, price, stamp, stamp)).lastrowid)
        audit("created", "service", service_id, active=True)
    return service_record(_service(service_id))


def update_service(service_id: int, data: dict) -> dict:
    allowed = {"name", "duration_minutes", "price_cents", "active"}
    unknown = set(data) - allowed
    if unknown:
        raise invalid(f"Unknown field(s): {', '.join(sorted(unknown))}.")
    db = get_db()
    with atomic(db):
        row = _service(service_id)
        if row is None:
            raise missing("Service")
        name = text(data.get("name", row["name"]), "name", maximum=120)
        duration = integer(data.get("duration_minutes", row["duration_minutes"]), "duration_minutes", minimum=5, maximum=480)
        price = integer(data.get("price_cents", row["price_cents"]), "price_cents", minimum=0, maximum=1_000_000_000)
        active = int(boolean(data["active"], "active")) if "active" in data else row["active"]
        if not active and db.execute(f"SELECT 1 FROM appointments WHERE service_id=? AND {ACTIVE_VISIT_SQL} AND start_at>? LIMIT 1", (service_id, now())).fetchone():
            raise conflict("A service with future appointments cannot be archived.")
        db.execute("UPDATE services SET name=?,duration_minutes=?,price_cents=?,active=?,updated_at=? WHERE id=?", (name, duration, price, active, now(), service_id))
        audit("updated", "service", service_id, active=bool(active))
    return service_record(_service(service_id))


def list_services(active: bool | None = None) -> list[dict]:
    sql, args = "SELECT * FROM services", ()
    if active is not None:
        sql += " WHERE active=?"
        args = (int(active),)
    return [service_record(row) for row in get_db().execute(sql + " ORDER BY name,id", args)]


def _room(room_id: int):
    return get_db().execute("SELECT * FROM rooms WHERE id=?", (room_id,)).fetchone()


def room_record(row) -> dict | None:
    if row is None:
        return None
    return {"id": row["id"], "name": row["name"], "active": bool(row["active"])}


def create_room(data: dict) -> dict:
    name = text(data.get("name"), "name", maximum=80)
    db = get_db()
    with atomic(db):
        stamp = now()
        room_id = int(db.execute("INSERT INTO rooms (name,created_at,updated_at) VALUES (?,?,?)", (name, stamp, stamp)).lastrowid)
        audit("created", "room", room_id, active=True)
    return room_record(_room(room_id))


def update_room(room_id: int, data: dict) -> dict:
    unknown = set(data) - {"name", "active"}
    if unknown:
        raise invalid(f"Unknown field(s): {', '.join(sorted(unknown))}.")
    db = get_db()
    with atomic(db):
        row = _room(room_id)
        if row is None:
            raise missing("Room")
        name = text(data.get("name", row["name"]), "name", maximum=80)
        active = int(boolean(data["active"], "active")) if "active" in data else row["active"]
        if not active and db.execute(f"SELECT 1 FROM appointments WHERE room_id=? AND {ACTIVE_VISIT_SQL} AND start_at>? LIMIT 1", (room_id, now())).fetchone():
            raise conflict("A room with future appointments cannot be archived.")
        db.execute("UPDATE rooms SET name=?,active=?,updated_at=? WHERE id=?", (name, active, now(), room_id))
        audit("updated", "room", room_id, active=bool(active))
    return room_record(_room(room_id))


def list_rooms(active: bool | None = None) -> list[dict]:
    sql, args = "SELECT * FROM rooms", ()
    if active is not None:
        sql += " WHERE active=?"
        args = (int(active),)
    return [room_record(row) for row in get_db().execute(sql + " ORDER BY name,id", args)]


def set_qualifications(dentist_id: int, service_ids: list[int]) -> list[int]:
    if not isinstance(service_ids, list):
        raise invalid("'service_ids' must be a list of service identifiers.")
    ids = [integer(value, "service_ids[]", minimum=1) for value in service_ids]
    if len(ids) != len(set(ids)):
        raise invalid("'service_ids' cannot contain duplicates.")
    db = get_db()
    with atomic(db):
        dentist = db.execute("SELECT active FROM dentists WHERE id=?", (dentist_id,)).fetchone()
        if dentist is None:
            raise missing("Dentist")
        for service_id in ids:
            service = db.execute("SELECT active FROM services WHERE id=?", (service_id,)).fetchone()
            if service is None or not service["active"]:
                raise invalid("Every qualification must reference an active service.")
        current = {r[0] for r in db.execute("SELECT service_id FROM dentist_services WHERE dentist_id=?", (dentist_id,))}
        removed = current - set(ids)
        for service_id in removed:
            if db.execute(f"SELECT 1 FROM appointments WHERE dentist_id=? AND service_id=? AND {ACTIVE_VISIT_SQL} AND start_at>? LIMIT 1", (dentist_id, service_id, now())).fetchone():
                raise conflict("A qualification used by a future appointment cannot be removed.")
        db.execute("DELETE FROM dentist_services WHERE dentist_id=?", (dentist_id,))
        db.executemany("INSERT INTO dentist_services (dentist_id,service_id) VALUES (?,?)", [(dentist_id, service_id) for service_id in ids])
        audit("qualifications_replaced", "dentist", dentist_id, count=len(ids))
    return sorted(ids)


def qualifications(dentist_id: int) -> list[int]:
    if _dentist(dentist_id) is None:
        raise missing("Dentist")
    return [row[0] for row in get_db().execute("SELECT service_id FROM dentist_services WHERE dentist_id=? ORDER BY service_id", (dentist_id,))]


def list_shifts(dentist_id: int | None = None) -> list[dict]:
    sql = "SELECT * FROM weekly_shifts"
    args: tuple = ()
    if dentist_id is not None:
        sql += " WHERE dentist_id=?"
        args = (dentist_id,)
    return [dict(row) for row in get_db().execute(sql + " ORDER BY dentist_id,weekday,start_time,id", args)]


def create_shift(data: dict) -> dict:
    dentist_id = integer(data.get("dentist_id"), "dentist_id", minimum=1)
    weekday = integer(data.get("weekday"), "weekday", minimum=0, maximum=6)
    start = iso_time(data.get("start_time"), "start_time")
    end = iso_time(data.get("end_time"), "end_time")
    if start >= end:
        raise invalid("A shift must end after it starts on the same day.")
    db = get_db()
    with atomic(db):
        dentist = _dentist(dentist_id)
        if dentist is None or not dentist["active"]:
            raise invalid("An active practitioner is required for a shift.")
        overlaps = db.execute("SELECT 1 FROM weekly_shifts WHERE dentist_id=? AND weekday=? AND start_time<? AND end_time>? LIMIT 1", (dentist_id, weekday, end, start)).fetchone()
        if overlaps:
            raise conflict("Weekly shifts for a practitioner cannot overlap.")
        shift_id = int(db.execute("INSERT INTO weekly_shifts (dentist_id,weekday,start_time,end_time) VALUES (?,?,?,?)", (dentist_id, weekday, start, end)).lastrowid)
        audit("created", "weekly_shift", shift_id, related_id=dentist_id)
    return dict(db.execute("SELECT * FROM weekly_shifts WHERE id=?", (shift_id,)).fetchone())


def delete_shift(shift_id: int) -> None:
    db = get_db()
    with atomic(db):
        row = db.execute("SELECT id FROM weekly_shifts WHERE id=?", (shift_id,)).fetchone()
        if row is None:
            raise missing("Weekly shift")
        db.execute("DELETE FROM weekly_shifts WHERE id=?", (shift_id,))
        audit("deleted", "weekly_shift", shift_id)


def create_time_off(data: dict) -> dict:
    dentist_id = integer(data.get("dentist_id"), "dentist_id", minimum=1)
    start = local_datetime(data.get("start_at"), "start_at")
    end = local_datetime(data.get("end_at"), "end_at")
    from .validation import interval

    interval(start, end, maximum_minutes=60 * 24 * 31)
    reason = text(data.get("reason"), "reason", maximum=240)
    db = get_db()
    with atomic(db):
        if _dentist(dentist_id) is None:
            raise missing("Dentist")
        if db.execute("SELECT 1 FROM time_off WHERE dentist_id=? AND start_at<? AND end_at>? LIMIT 1", (dentist_id, end, start)).fetchone():
            raise conflict("Time-off intervals for one practitioner cannot overlap.")
        occupied = db.execute(f"SELECT id FROM appointments WHERE dentist_id=? AND {ACTIVE_VISIT_SQL} AND start_at<? AND end_at>? LIMIT 1", (dentist_id, end, start)).fetchone()
        if occupied:
            raise conflict("Time off overlaps an active appointment.")
        time_off_id = int(db.execute("INSERT INTO time_off (dentist_id,start_at,end_at,reason) VALUES (?,?,?,?)", (dentist_id, start, end, reason)).lastrowid)
        audit("created", "time_off", time_off_id, related_id=dentist_id)
    return dict(db.execute("SELECT id,dentist_id,start_at,end_at,reason FROM time_off WHERE id=?", (time_off_id,)).fetchone())


def list_time_off(dentist_id: int | None = None) -> list[dict]:
    sql, args = "SELECT id,dentist_id,start_at,end_at,reason FROM time_off", ()
    if dentist_id is not None:
        sql += " WHERE dentist_id=?"
        args = (dentist_id,)
    return [dict(row) for row in get_db().execute(sql + " ORDER BY start_at,id", args)]


def delete_time_off(time_off_id: int) -> None:
    db = get_db()
    with atomic(db):
        if db.execute("SELECT 1 FROM time_off WHERE id=?", (time_off_id,)).fetchone() is None:
            raise missing("Time off")
        db.execute("DELETE FROM time_off WHERE id=?", (time_off_id,))
        audit("deleted", "time_off", time_off_id)
