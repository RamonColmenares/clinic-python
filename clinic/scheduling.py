"""Slot generation and transactional appointment operations."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from flask import current_app, g

from .catalog import ACTIVE_VISIT_SQL, audit, now
from .db import atomic, get_db
from .validation import conflict, integer, interval, invalid, iso_date, local_datetime, missing, text


ACTIVE_STATUSES = ("booked", "confirmed", "checked_in")
ALL_STATUSES = ("booked", "confirmed", "checked_in", "completed", "cancelled", "no_show")


def _actor_id() -> int | None:
    account = getattr(g, "account", None)
    return int(account["id"]) if account is not None else None


def _available_resources(patient_id: int, dentist_id: int, service_id: int, room_id: int, start: str, end: str, exclude_id: int | None = None) -> tuple[int, int]:
    db = get_db()
    patient = db.execute("SELECT active FROM patients WHERE id=?", (patient_id,)).fetchone()
    dentist = db.execute("SELECT active FROM dentists WHERE id=?", (dentist_id,)).fetchone()
    service = db.execute("SELECT active,duration_minutes,price_cents FROM services WHERE id=?", (service_id,)).fetchone()
    room = db.execute("SELECT active FROM rooms WHERE id=?", (room_id,)).fetchone()
    if patient is None or not patient["active"]:
        raise invalid("An active patient is required for a new appointment.")
    if dentist is None or not dentist["active"]:
        raise invalid("An active practitioner is required for a new appointment.")
    if service is None or not service["active"]:
        raise invalid("An active service is required for a new appointment.")
    if room is None or not room["active"]:
        raise invalid("An active room is required for a new appointment.")
    if db.execute("SELECT 1 FROM dentist_services WHERE dentist_id=? AND service_id=?", (dentist_id, service_id)).fetchone() is None:
        raise conflict("The practitioner is not qualified for this service.", "qualification_required")

    start_moment = datetime.strptime(start, "%Y-%m-%dT%H:%M")
    end_moment = datetime.strptime(end, "%Y-%m-%dT%H:%M")
    day = start_moment.date().isoformat()
    if end_moment.date() != start_moment.date():
        raise invalid("An appointment must start and end on the same local day.")
    shift = db.execute(
        "SELECT 1 FROM weekly_shifts WHERE dentist_id=? AND weekday=? AND start_time<=? AND end_time>=? LIMIT 1",
        (dentist_id, start_moment.weekday(), start_moment.strftime("%H:%M"), end_moment.strftime("%H:%M")),
    ).fetchone()
    if shift is None:
        raise conflict("The requested time is outside the practitioner's published working hours.", "outside_working_hours")
    if db.execute("SELECT 1 FROM time_off WHERE dentist_id=? AND start_at<? AND end_at>? LIMIT 1", (dentist_id, end, start)).fetchone():
        raise conflict("The practitioner is unavailable during that time.", "practitioner_unavailable")

    exclusion = " AND id<>?" if exclude_id is not None else ""
    interval_args: tuple = (end, start, exclude_id) if exclude_id is not None else (end, start)
    for resource, resource_id in (("dentist_id", dentist_id), ("patient_id", patient_id), ("room_id", room_id)):
        occupied = db.execute(
            f"SELECT 1 FROM appointments WHERE {resource}=? AND {ACTIVE_VISIT_SQL} AND legacy_imported=0 AND start_at<? AND end_at>?{exclusion} LIMIT 1",
            (resource_id, *interval_args),
        ).fetchone()
        if occupied:
            label = {"dentist_id": "practitioner", "patient_id": "patient", "room_id": "room"}[resource]
            raise conflict(f"The {label} already has an appointment during that time.", f"{label}_conflict")

    # Date-only appointments have no reliable time. They conservatively reserve
    # that patient's and practitioner's whole day until explicitly rescheduled.
    legacy_exclusion = " AND id<>?" if exclude_id is not None else ""
    for resource, resource_id in (("dentist_id", dentist_id), ("patient_id", patient_id)):
        legacy = db.execute(
            f"SELECT 1 FROM appointments WHERE {resource}=? AND status IN ('booked','confirmed','checked_in') AND legacy_imported=1 AND date(start_at)=?{legacy_exclusion} LIMIT 1",
            (resource_id, day, exclude_id) if exclude_id is not None else (resource_id, day),
        ).fetchone()
        if legacy:
            label = "practitioner" if resource == "dentist_id" else "patient"
            raise conflict(f"An unresolved legacy visit reserves this {label}'s full date.", "legacy_date_reserved")

    return int(service["duration_minutes"]), int(service["price_cents"])


def appointment_record(appointment_id: int) -> dict | None:
    row = get_db().execute(
        """SELECT a.*, p.first_name AS patient_first_name, p.last_name AS patient_last_name,
                  d.first_name AS dentist_first_name, d.last_name AS dentist_last_name,
                  s.name AS service_name, r.name AS room_name
           FROM appointments a JOIN patients p ON p.id=a.patient_id
           JOIN dentists d ON d.id=a.dentist_id
           LEFT JOIN services s ON s.id=a.service_id LEFT JOIN rooms r ON r.id=a.room_id
           WHERE a.id=?""",
        (appointment_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "id": row["id"], "patient_id": row["patient_id"], "patient_name": f"{row['patient_first_name']} {row['patient_last_name']}",
        "dentist_id": row["dentist_id"], "dentist_name": f"{row['dentist_first_name']} {row['dentist_last_name']}",
        "service_id": row["service_id"], "service_name": row["service_name_snapshot"] or row["service_name"], "room_id": row["room_id"], "room_name": row["room_name"],
        "start_at": row["start_at"], "end_at": row["end_at"], "status": row["status"], "version": row["version"], "schedule_revision": row["schedule_revision"],
        "reason": row["reason"], "cancellation_reason": row["cancellation_reason"],
        "legacy_imported": bool(row["legacy_imported"]), "duration_minutes": row["duration_minutes"], "price_cents": row["price_cents"],
        "created_at": row["created_at"], "updated_at": row["updated_at"],
    }


def _append_event(appointment_id: int, from_status: str | None, to_status: str, reason: str = "") -> None:
    get_db().execute(
        "INSERT INTO appointment_events (appointment_id,from_status,to_status,actor_id,created_at,reason) VALUES (?,?,?,?,?,?)",
        (appointment_id, from_status, to_status, _actor_id(), now(), reason),
    )


def create_appointment(data: dict) -> dict:
    patient_id = integer(data.get("patient_id"), "patient_id", minimum=1)
    dentist_id = integer(data.get("dentist_id"), "dentist_id", minimum=1)
    service_id = integer(data.get("service_id"), "service_id", minimum=1)
    room_id = integer(data.get("room_id"), "room_id", minimum=1)
    start = local_datetime(data.get("start_at"), "start_at")
    if start < now():
        raise conflict("Appointments cannot be booked in the past.", "past_appointment")
    reason = text(data.get("reason", ""), "reason", required=False, maximum=300)
    db = get_db()
    with atomic(db):
        service = db.execute("SELECT duration_minutes,price_cents FROM services WHERE id=? AND active=1", (service_id,)).fetchone()
        if service is None:
            raise invalid("An active service is required for a new appointment.")
        end_moment = datetime.strptime(start, "%Y-%m-%dT%H:%M") + timedelta(minutes=service["duration_minutes"])
        end = end_moment.strftime("%Y-%m-%dT%H:%M")
        duration, price = _available_resources(patient_id, dentist_id, service_id, room_id, start, end)
        stamp = now()
        appointment_id = int(db.execute(
            """INSERT INTO appointments (patient_id,dentist_id,service_id,room_id,start_at,end_at,status,version,
               reason,duration_minutes,price_cents,service_name_snapshot,created_at,updated_at)
               VALUES (?,?,?,?,?,?,'booked',1,?,?,?,?,?,?)""",
            (patient_id, dentist_id, service_id, room_id, start, end, reason, duration, price, db.execute("SELECT name FROM services WHERE id=?", (service_id,)).fetchone()[0], stamp, stamp),
        ).lastrowid)
        _append_event(appointment_id, None, "booked", "Appointment created.")
        audit("created", "appointment", appointment_id, status="booked", version=1)
    return appointment_record(appointment_id)


def reschedule_appointment(appointment_id: int, data: dict) -> dict:
    allowed = {"version", "start_at", "service_id", "room_id", "dentist_id", "patient_id", "reason"}
    unknown = set(data) - allowed
    if unknown:
        raise invalid(f"Unknown field(s): {', '.join(sorted(unknown))}.")
    db = get_db()
    with atomic(db):
        row = db.execute("SELECT * FROM appointments WHERE id=?", (appointment_id,)).fetchone()
        if row is None:
            raise missing("Appointment")
        if row["status"] not in {"booked", "confirmed"}:
            raise conflict("Only booked or confirmed appointments can be rescheduled.", "invalid_transition")
        version = integer(data.get("version"), "version", minimum=1)
        if version != row["version"]:
            raise conflict("The appointment changed since it was loaded. Refresh and try again.", "stale_version")
        patient_id = integer(data.get("patient_id", row["patient_id"]), "patient_id", minimum=1)
        dentist_id = integer(data.get("dentist_id", row["dentist_id"]), "dentist_id", minimum=1)
        service_id = integer(data.get("service_id", row["service_id"]), "service_id", minimum=1)
        room_id = integer(data.get("room_id", row["room_id"]), "room_id", minimum=1)
        start = local_datetime(data.get("start_at"), "start_at")
        if start < now():
            raise conflict("Appointments cannot be moved into the past.", "past_appointment")
        reason = text(data.get("reason", row["reason"]), "reason", required=False, maximum=300)
        service = db.execute("SELECT duration_minutes,price_cents FROM services WHERE id=? AND active=1", (service_id,)).fetchone()
        if service is None:
            raise invalid("An active service is required for a new appointment.")
        linked_item = db.execute("SELECT i.service_id,p.patient_id FROM treatment_items i JOIN treatment_plans p ON p.id=i.plan_id WHERE i.appointment_id=?", (appointment_id,)).fetchone()
        if linked_item and (patient_id != linked_item["patient_id"] or service_id != linked_item["service_id"]):
            raise conflict("An appointment linked to a treatment item must keep the same patient and service.", "treatment_link_conflict")
        attached_notes = db.execute("SELECT 1 FROM clinical_notes WHERE appointment_id=? LIMIT 1", (appointment_id,)).fetchone()
        if attached_notes and patient_id != row["patient_id"]:
            raise conflict("An appointment with clinical notes cannot be moved to another patient.", "clinical_history_conflict")
        end = (datetime.strptime(start, "%Y-%m-%dT%H:%M") + timedelta(minutes=service["duration_minutes"])).strftime("%Y-%m-%dT%H:%M")
        duration, price = _available_resources(patient_id, dentist_id, service_id, room_id, start, end, appointment_id)
        next_version = row["version"] + 1
        schedule_changed = (
            row["legacy_imported"]
            or patient_id != row["patient_id"]
            or dentist_id != row["dentist_id"]
            or service_id != row["service_id"]
            or room_id != row["room_id"]
            or start != row["start_at"]
            or end != row["end_at"]
        )
        next_schedule_revision = row["schedule_revision"] + int(schedule_changed)
        db.execute(
            """UPDATE appointments SET patient_id=?,dentist_id=?,service_id=?,room_id=?,start_at=?,end_at=?,
               reason=?,legacy_imported=0,duration_minutes=?,price_cents=?,service_name_snapshot=?,version=?,schedule_revision=?,updated_at=? WHERE id=? AND version=?""",
            (patient_id, dentist_id, service_id, room_id, start, end, reason, duration, price, db.execute("SELECT name FROM services WHERE id=?", (service_id,)).fetchone()[0], next_version, next_schedule_revision, now(), appointment_id, version),
        )
        if db.execute("SELECT changes()").fetchone()[0] != 1:
            raise conflict("The appointment changed since it was loaded.", "stale_version")
        if schedule_changed:
            pending_reminders = db.execute("SELECT id FROM reminders WHERE appointment_id=? AND status='pending'", (appointment_id,)).fetchall()
            db.execute("UPDATE reminders SET status='cancelled' WHERE appointment_id=? AND status='pending'", (appointment_id,))
            for reminder in pending_reminders:
                audit("cancelled_reschedule", "reminder", reminder["id"], from_status="pending", to_status="cancelled", related_id=appointment_id)
        if row["legacy_imported"]:
            reason_text = "Legacy date-only visit explicitly rescheduled; placeholder time discarded."
        else:
            reason_text = "Appointment rescheduled."
        _append_event(appointment_id, row["status"], row["status"], reason_text)
        audit("rescheduled", "appointment", appointment_id, status=row["status"], version=next_version)
    return appointment_record(appointment_id)


def transition_appointment(appointment_id: int, data: dict) -> dict:
    allowed_fields = {"status", "version", "reason"}
    unknown = set(data) - allowed_fields
    if unknown:
        raise invalid(f"Unknown field(s): {', '.join(sorted(unknown))}.")
    target = text(data.get("status"), "status", maximum=30)
    version = integer(data.get("version"), "version", minimum=1)
    reason = text(data.get("reason", ""), "reason", required=False, maximum=300)
    db = get_db()
    transitions = {
        "booked": {"confirmed", "checked_in", "cancelled", "no_show"},
        "confirmed": {"checked_in", "cancelled", "no_show"},
        "checked_in": {"completed", "cancelled"},
    }
    with atomic(db):
        row = db.execute("SELECT * FROM appointments WHERE id=?", (appointment_id,)).fetchone()
        if row is None:
            raise missing("Appointment")
        if version != row["version"]:
            raise conflict("The appointment changed since it was loaded. Refresh and try again.", "stale_version")
        if target not in transitions.get(row["status"], set()):
            raise conflict(f"A {row['status']} appointment cannot transition to {target}.", "invalid_transition")
        if row["legacy_imported"] and target in {"confirmed", "checked_in", "completed", "no_show"}:
            raise conflict("Legacy date-only visits must be explicitly rescheduled before confirmation or care transitions.", "legacy_reschedule_required")
        if target == "cancelled" and not reason:
            raise invalid("A cancellation reason is required.")
        if target == "no_show" and row["start_at"] > now():
            raise conflict("A future appointment cannot be marked as a no-show.", "visit_not_started")
        if target == "checked_in" and row["start_at"] > now():
            raise conflict("A future appointment cannot be checked in.", "visit_not_started")
        next_version = row["version"] + 1
        db.execute("UPDATE appointments SET status=?,version=?,cancellation_reason=?,updated_at=? WHERE id=? AND version=?", (target, next_version, reason if target == "cancelled" else row["cancellation_reason"], now(), appointment_id, version))
        if target in {"cancelled", "no_show"}:
            db.execute("UPDATE reminders SET status='cancelled' WHERE appointment_id=? AND status='pending'", (appointment_id,))
            linked_items = db.execute(
                "SELECT id FROM treatment_items WHERE appointment_id=? AND status='scheduled' ORDER BY id",
                (appointment_id,),
            ).fetchall()
            db.execute(
                "UPDATE treatment_items SET status='planned',appointment_id=NULL WHERE appointment_id=? AND status='scheduled'",
                (appointment_id,),
            )
            for item in linked_items:
                audit("unscheduled_after_visit_" + target, "treatment_item", item["id"], from_status="scheduled", to_status="planned", related_id=appointment_id)
        if target == "completed":
            db.execute("UPDATE treatment_items SET status='completed' WHERE appointment_id=? AND status='scheduled'", (appointment_id,))
            _complete_eligible_plans(db, appointment_id)
        _append_event(appointment_id, row["status"], target, reason)
        audit("transitioned", "appointment", appointment_id, from_status=row["status"], to_status=target, version=next_version)
    return appointment_record(appointment_id)


def _complete_eligible_plans(db, appointment_id: int) -> None:
    plans = db.execute("SELECT DISTINCT p.id FROM treatment_plans p JOIN treatment_items i ON i.plan_id=p.id WHERE i.appointment_id=? AND p.status='active'", (appointment_id,)).fetchall()
    for plan in plans:
        remaining = db.execute("SELECT 1 FROM treatment_items WHERE plan_id=? AND status NOT IN ('completed','cancelled') LIMIT 1", (plan["id"],)).fetchone()
        if remaining is None:
            db.execute("UPDATE treatment_plans SET status='completed',closed_at=? WHERE id=? AND status='active'", (now(), plan["id"]))
            audit("completed", "treatment_plan", plan["id"], status="completed")


def appointment_events(appointment_id: int) -> list[dict]:
    if get_db().execute("SELECT 1 FROM appointments WHERE id=?", (appointment_id,)).fetchone() is None:
        raise missing("Appointment")
    rows = get_db().execute("SELECT id,appointment_id,from_status,to_status,actor_id,created_at,reason FROM appointment_events WHERE appointment_id=? ORDER BY id", (appointment_id,))
    return [dict(row) for row in rows]


def list_appointments(*, from_date: str | None = None, to_date: str | None = None, dentist_id: int | None = None, patient_id: int | None = None, status: str | None = None, limit: int = 50, offset: int = 0) -> dict:
    clauses, values = [], []
    if from_date:
        clauses.append("start_at>=?")
        values.append(from_date + "T00:00")
    if to_date:
        clauses.append("start_at<?")
        values.append((datetime.strptime(to_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%dT00:00"))
    if dentist_id is not None:
        clauses.append("dentist_id=?")
        values.append(dentist_id)
    if patient_id is not None:
        clauses.append("patient_id=?")
        values.append(patient_id)
    if status:
        if status not in ALL_STATUSES:
            raise invalid("'status' is not a recognized appointment state.")
        clauses.append("status=?")
        values.append(status)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    db = get_db()
    total = int(db.execute("SELECT COUNT(*) FROM appointments" + where, values).fetchone()[0])
    ids = db.execute("SELECT id FROM appointments" + where + " ORDER BY start_at,id LIMIT ? OFFSET ?", (*values, limit, offset)).fetchall()
    return {"items": [appointment_record(row["id"]) for row in ids], "total": total, "limit": limit, "offset": offset}


def availability(day_value: str, dentist_id: int, service_id: int, room_id: int | None = None, patient_id: int | None = None) -> dict:
    day_value = iso_date(day_value, "date")
    day = date.fromisoformat(day_value)
    db = get_db()
    service = db.execute("SELECT duration_minutes FROM services WHERE id=? AND active=1", (service_id,)).fetchone()
    dentist = db.execute("SELECT active FROM dentists WHERE id=?", (dentist_id,)).fetchone()
    if service is None or dentist is None or not dentist["active"]:
        raise invalid("Availability requires an active practitioner and service.")
    if patient_id is not None:
        patient = db.execute("SELECT active FROM patients WHERE id=?", (patient_id,)).fetchone()
        if patient is None or not patient["active"]:
            raise invalid("Availability requires an active patient when a patient filter is provided.")
    if db.execute("SELECT 1 FROM dentist_services WHERE dentist_id=? AND service_id=?", (dentist_id, service_id)).fetchone() is None:
        raise conflict("The practitioner is not qualified for this service.", "qualification_required")
    rooms = [room_id] if room_id is not None else [r[0] for r in db.execute("SELECT id FROM rooms WHERE active=1 ORDER BY name,id")]
    for selected_room in rooms:
        room = db.execute("SELECT active FROM rooms WHERE id=?", (selected_room,)).fetchone()
        if room is None or not room["active"]:
            raise invalid("Availability requires an active room.")
    shifts = db.execute("SELECT start_time,end_time FROM weekly_shifts WHERE dentist_id=? AND weekday=? ORDER BY start_time", (dentist_id, day.weekday())).fetchall()
    slots = []
    duration = int(service["duration_minutes"])
    for shift in shifts:
        cursor = datetime.combine(day, time.fromisoformat(shift["start_time"]))
        shift_end = datetime.combine(day, time.fromisoformat(shift["end_time"]))
        while cursor + timedelta(minutes=duration) <= shift_end:
            start = cursor.strftime("%Y-%m-%dT%H:%M")
            end = (cursor + timedelta(minutes=duration)).strftime("%Y-%m-%dT%H:%M")
            if start < now():
                cursor += timedelta(minutes=15)
                continue
            has_time_off = db.execute("SELECT 1 FROM time_off WHERE dentist_id=? AND start_at<? AND end_at>? LIMIT 1", (dentist_id, end, start)).fetchone()
            busy = bool(has_time_off)
            if not has_time_off:
                dentist_busy = db.execute("SELECT 1 FROM appointments WHERE dentist_id=? AND status IN ('booked','confirmed','checked_in') AND legacy_imported=0 AND start_at<? AND end_at>? LIMIT 1", (dentist_id, end, start)).fetchone()
                legacy_day = db.execute("SELECT 1 FROM appointments WHERE dentist_id=? AND status IN ('booked','confirmed','checked_in') AND legacy_imported=1 AND date(start_at)=? LIMIT 1", (dentist_id, day_value)).fetchone()
                patient_busy = db.execute("SELECT 1 FROM appointments WHERE patient_id=? AND status IN ('booked','confirmed','checked_in') AND legacy_imported=0 AND start_at<? AND end_at>? LIMIT 1", (patient_id, end, start)).fetchone() if patient_id else None
                patient_legacy = db.execute("SELECT 1 FROM appointments WHERE patient_id=? AND status IN ('booked','confirmed','checked_in') AND legacy_imported=1 AND date(start_at)=? LIMIT 1", (patient_id, day_value)).fetchone() if patient_id else None
                busy = bool(dentist_busy or legacy_day or patient_busy or patient_legacy)
            for selected_room in rooms:
                room_busy = db.execute("SELECT 1 FROM appointments WHERE room_id=? AND status IN ('booked','confirmed','checked_in') AND legacy_imported=0 AND start_at<? AND end_at>? LIMIT 1", (selected_room, end, start)).fetchone()
                if not busy and room_busy is None:
                    slots.append({"start_at": start, "end_at": end, "room_id": selected_room})
            cursor += timedelta(minutes=15)
    return {"date": day_value, "dentist_id": dentist_id, "service_id": service_id, "slots": sorted(slots, key=lambda slot: (slot["start_at"], slot["room_id"]))}


def patient_history(patient_id: int, limit: int = 50, offset: int = 0) -> dict:
    patient = get_db().execute("SELECT id,first_name,last_name,active FROM patients WHERE id=?", (patient_id,)).fetchone()
    if patient is None:
        raise missing("Patient")
    db = get_db()
    total = int(db.execute("SELECT COUNT(*) FROM appointments WHERE patient_id=?", (patient_id,)).fetchone()[0])
    visits = db.execute("SELECT id FROM appointments WHERE patient_id=? ORDER BY start_at DESC,id DESC LIMIT ? OFFSET ?", (patient_id, limit, offset)).fetchall()
    visit_records = []
    for row in visits:
        record = appointment_record(row["id"])
        record["events"] = appointment_events(row["id"])
        visit_records.append(record)
    return {"patient": dict(patient), "appointments": {"items": visit_records, "total": total, "limit": limit, "offset": offset}}
