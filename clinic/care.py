"""Treatment planning and append-only clinical documentation."""

from __future__ import annotations

from flask import current_app

from .catalog import audit, now
from .db import atomic, get_db
from .validation import conflict, integer, missing, text, invalid


PLAN_STATES = {"draft", "active", "completed", "cancelled"}
ITEM_STATES = {"planned", "scheduled", "completed", "cancelled"}


def create_plan(data: dict) -> dict:
    patient_id = integer(data.get("patient_id"), "patient_id", minimum=1)
    title = text(data.get("title"), "title", maximum=180)
    db = get_db()
    with atomic(db):
        patient = db.execute("SELECT active FROM patients WHERE id=?", (patient_id,)).fetchone()
        if patient is None:
            raise missing("Patient")
        if not patient["active"]:
            raise conflict("A treatment plan requires an active patient.")
        plan_id = int(db.execute("INSERT INTO treatment_plans (patient_id,title,created_at) VALUES (?,?,?)", (patient_id, title, now())).lastrowid)
        audit("created", "treatment_plan", plan_id, status="draft", related_id=patient_id)
    return get_plan(plan_id)


def get_plan(plan_id: int) -> dict:
    row = get_db().execute("SELECT * FROM treatment_plans WHERE id=?", (plan_id,)).fetchone()
    if row is None:
        raise missing("Treatment plan")
    result = dict(row)
    result["items"] = [dict(item) for item in get_db().execute("SELECT * FROM treatment_items WHERE plan_id=? ORDER BY sequence,id", (plan_id,))]
    return result


def list_plans(patient_id: int | None = None, status: str | None = None, limit: int = 50, offset: int = 0) -> dict:
    clauses, values = [], []
    if patient_id is not None:
        clauses.append("patient_id=?")
        values.append(patient_id)
    if status is not None:
        if status not in PLAN_STATES:
            raise invalid("'status' is not a recognized treatment-plan state.")
        clauses.append("status=?")
        values.append(status)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    db = get_db()
    total = int(db.execute("SELECT COUNT(*) FROM treatment_plans" + where, values).fetchone()[0])
    rows = db.execute("SELECT id FROM treatment_plans" + where + " ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?", (*values, limit, offset)).fetchall()
    return {"items": [get_plan(row["id"]) for row in rows], "total": total, "limit": limit, "offset": offset}


def transition_plan(plan_id: int, data: dict) -> dict:
    target = text(data.get("status"), "status", maximum=20)
    if target not in PLAN_STATES:
        raise invalid("'status' is not a recognized treatment-plan state.")
    db = get_db()
    allowed = {"draft": {"active", "cancelled"}, "active": {"completed", "cancelled"}, "completed": set(), "cancelled": set()}
    with atomic(db):
        plan = db.execute("SELECT * FROM treatment_plans WHERE id=?", (plan_id,)).fetchone()
        if plan is None:
            raise missing("Treatment plan")
        if target not in allowed[plan["status"]]:
            raise conflict(f"A {plan['status']} plan cannot transition to {target}.", "invalid_transition")
        items = db.execute("SELECT status FROM treatment_items WHERE plan_id=?", (plan_id,)).fetchall()
        if target == "active" and not items:
            raise conflict("A plan needs at least one treatment item before activation.")
        if target == "completed" and any(item["status"] not in {"completed", "cancelled"} for item in items):
            raise conflict("Every treatment item must be completed or cancelled before closing the plan.")
        if target == "cancelled":
            if any(item["status"] == "completed" for item in items):
                raise conflict("A plan with completed care cannot be cancelled.")
            db.execute("UPDATE treatment_items SET status='cancelled' WHERE plan_id=? AND status IN ('planned','scheduled')", (plan_id,))
        db.execute("UPDATE treatment_plans SET status=?,closed_at=? WHERE id=?", (target, now() if target in {"completed", "cancelled"} else None, plan_id))
        audit("transitioned", "treatment_plan", plan_id, from_status=plan["status"], to_status=target)
    return get_plan(plan_id)


def add_item(plan_id: int, data: dict) -> dict:
    service_id = integer(data.get("service_id"), "service_id", minimum=1)
    sequence = integer(data.get("sequence"), "sequence", minimum=1, maximum=999)
    tooth = text(data.get("tooth_reference"), "tooth_reference", required=False, maximum=32) or None
    notes = text(data.get("notes", ""), "notes", required=False, maximum=800)
    db = get_db()
    with atomic(db):
        plan = db.execute("SELECT status FROM treatment_plans WHERE id=?", (plan_id,)).fetchone()
        if plan is None:
            raise missing("Treatment plan")
        if plan["status"] != "draft":
            raise conflict("Treatment items can only be added to a draft plan.")
        service = db.execute("SELECT active,price_cents,name FROM services WHERE id=?", (service_id,)).fetchone()
        if service is None or not service["active"]:
            raise invalid("An active service is required for a treatment item.")
        item_id = int(db.execute("INSERT INTO treatment_items (plan_id,service_id,sequence,tooth_reference,notes,estimated_price_cents,service_name_snapshot) VALUES (?,?,?,?,?,?,?)", (plan_id, service_id, sequence, tooth, notes, service["price_cents"], service["name"])).lastrowid)
        audit("created", "treatment_item", item_id, related_id=plan_id, status="planned")
    return dict(db.execute("SELECT * FROM treatment_items WHERE id=?", (item_id,)).fetchone())


def reorder_items(plan_id: int, data: dict) -> dict:
    order = data.get("item_ids")
    if not isinstance(order, list) or not order:
        raise invalid("'item_ids' must be a non-empty ordered list.")
    ids = [integer(value, "item_ids[]", minimum=1) for value in order]
    if len(ids) != len(set(ids)):
        raise invalid("'item_ids' cannot contain duplicates.")
    db = get_db()
    with atomic(db):
        plan = db.execute("SELECT status FROM treatment_plans WHERE id=?", (plan_id,)).fetchone()
        if plan is None:
            raise missing("Treatment plan")
        if plan["status"] != "draft":
            raise conflict("Treatment order can only be changed while the plan is a draft.")
        existing = {row[0] for row in db.execute("SELECT id FROM treatment_items WHERE plan_id=?", (plan_id,))}
        if set(ids) != existing:
            raise invalid("The ordered identifiers must include every item in this plan exactly once.")
        for position, item_id in enumerate(ids, start=1):
            db.execute("UPDATE treatment_items SET sequence=? WHERE id=?", (1000 + position, item_id))
        for position, item_id in enumerate(ids, start=1):
            db.execute("UPDATE treatment_items SET sequence=? WHERE id=?", (position, item_id))
        audit("reordered", "treatment_plan", plan_id, count=len(ids))
    return get_plan(plan_id)


def schedule_item(item_id: int, data: dict) -> dict:
    appointment_id = integer(data.get("appointment_id"), "appointment_id", minimum=1)
    db = get_db()
    with atomic(db):
        item = db.execute("SELECT i.*,p.patient_id AS plan_patient_id,p.status AS plan_status FROM treatment_items i JOIN treatment_plans p ON p.id=i.plan_id WHERE i.id=?", (item_id,)).fetchone()
        if item is None:
            raise missing("Treatment item")
        if item["plan_status"] != "active" or item["status"] != "planned":
            raise conflict("Only a planned item in an active plan can be scheduled.")
        appointment = db.execute("SELECT patient_id,service_id,status,legacy_imported FROM appointments WHERE id=?", (appointment_id,)).fetchone()
        if appointment is None:
            raise missing("Appointment")
        if appointment["patient_id"] != item["plan_patient_id"] or appointment["service_id"] != item["service_id"]:
            raise conflict("The appointment must match the plan patient and item service.")
        if appointment["status"] not in {"booked", "confirmed"} or appointment["legacy_imported"]:
            raise conflict("The appointment must be an explicitly scheduled active visit.")
        db.execute("UPDATE treatment_items SET appointment_id=?,status='scheduled' WHERE id=?", (appointment_id, item_id))
        audit("scheduled", "treatment_item", item_id, related_id=appointment_id, status="scheduled")
    return dict(db.execute("SELECT * FROM treatment_items WHERE id=?", (item_id,)).fetchone())


def cancel_item(item_id: int) -> dict:
    db = get_db()
    with atomic(db):
        item = db.execute("SELECT i.*,p.status AS plan_status FROM treatment_items i JOIN treatment_plans p ON p.id=i.plan_id WHERE i.id=?", (item_id,)).fetchone()
        if item is None:
            raise missing("Treatment item")
        if item["plan_status"] != "draft" or item["status"] != "planned":
            raise conflict("Only a planned item in a draft plan can be removed.")
        db.execute("UPDATE treatment_items SET status='cancelled' WHERE id=?", (item_id,))
        audit("cancelled", "treatment_item", item_id, status="cancelled")
    return dict(db.execute("SELECT * FROM treatment_items WHERE id=?", (item_id,)).fetchone())


def add_note(data: dict) -> dict:
    patient_id = integer(data.get("patient_id"), "patient_id", minimum=1)
    appointment_id = None if data.get("appointment_id") in (None, "") else integer(data["appointment_id"], "appointment_id", minimum=1)
    amends_note_id = None if data.get("amends_note_id") in (None, "") else integer(data["amends_note_id"], "amends_note_id", minimum=1)
    body = text(data.get("body"), "body", maximum=5000)
    db = get_db()
    with atomic(db):
        if db.execute("SELECT 1 FROM patients WHERE id=?", (patient_id,)).fetchone() is None:
            raise missing("Patient")
        if appointment_id is not None:
            visit = db.execute("SELECT patient_id FROM appointments WHERE id=?", (appointment_id,)).fetchone()
            if visit is None:
                raise missing("Appointment")
            if visit["patient_id"] != patient_id:
                raise conflict("The appointment belongs to a different patient.")
        if amends_note_id is not None:
            original = db.execute("SELECT patient_id FROM clinical_notes WHERE id=?", (amends_note_id,)).fetchone()
            if original is None:
                raise missing("Clinical note")
            if original["patient_id"] != patient_id:
                raise conflict("An amendment must belong to the original note's patient.")
        author_id = int(current_app.config.get("NOTE_AUTHOR_OVERRIDE") or __import__("flask").g.account["id"])
        note_id = int(db.execute("INSERT INTO clinical_notes (patient_id,appointment_id,body,author_id,created_at,amends_note_id) VALUES (?,?,?,?,?,?)", (patient_id, appointment_id, body, author_id, now(), amends_note_id)).lastrowid)
        audit("created", "clinical_note", note_id, related_id=patient_id)
    return get_note(note_id)


def get_note(note_id: int) -> dict:
    row = get_db().execute("SELECT id,patient_id,appointment_id,body,author_id,created_at,amends_note_id FROM clinical_notes WHERE id=?", (note_id,)).fetchone()
    if row is None:
        raise missing("Clinical note")
    return dict(row)


def list_notes(patient_id: int | None = None, appointment_id: int | None = None, limit: int = 50, offset: int = 0) -> dict:
    clauses, values = [], []
    if patient_id is not None:
        clauses.append("patient_id=?")
        values.append(patient_id)
    if appointment_id is not None:
        clauses.append("appointment_id=?")
        values.append(appointment_id)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    db = get_db()
    total = int(db.execute("SELECT COUNT(*) FROM clinical_notes" + where, values).fetchone()[0])
    rows = db.execute("SELECT id FROM clinical_notes" + where + " ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?", (*values, limit, offset)).fetchall()
    return {"items": [get_note(row["id"]) for row in rows], "total": total, "limit": limit, "offset": offset}
