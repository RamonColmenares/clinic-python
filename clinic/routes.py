"""English JSON API for the local clinic desk."""

from __future__ import annotations

import sqlite3
from datetime import date

from flask import Blueprint, current_app, g, jsonify, request

from . import accounts, care, catalog, operations, scheduling
from .auth import csrf_token, require_roles
from .db import get_db
from .validation import DomainError, integer, iso_date, page_args, require_fields, text, invalid

bp = Blueprint("api", __name__, url_prefix="/api")


def json_body() -> dict:
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise invalid("Request body must be a JSON object.")
    return body


def _active_filter() -> bool | None:
    raw = request.args.get("active")
    if raw is None:
        return None
    if raw not in {"true", "false"}:
        raise invalid("'active' must be true or false.")
    return raw == "true"


def _paged(items: list[dict], total: int | None = None) -> dict:
    limit, offset = page_args(request.args)
    return {"items": items[offset:offset + limit], "total": len(items) if total is None else total, "limit": limit, "offset": offset}


def _required_query_id(name: str) -> int:
    return integer(request.args.get(name), name, minimum=1)


def _optional_query_id(name: str) -> int | None:
    raw = request.args.get(name)
    return None if raw in (None, "") else integer(raw, name, minimum=1)


@bp.errorhandler(DomainError)
def domain_error(error: DomainError):
    return jsonify(error=error.code, message=error.message), error.status


@bp.errorhandler(sqlite3.IntegrityError)
def relationship_error(_error: sqlite3.IntegrityError):
    get_db().rollback()
    return jsonify(error="conflict", message="The record conflicts with an existing relationship or unique value."), 409


@bp.get("/session")
@require_roles("USER", "ADMIN")
def session_info():
    return jsonify(account={"id": g.account["id"], "username": g.account["username"], "display_name": g.account["display_name"], "role": g.account["role"]}, csrf_token=csrf_token(), timezone=current_app.config["CLINIC_TIMEZONE"])


@bp.get("/accounts")
@require_roles("ADMIN")
def account_directory():
    limit, offset, active = accounts.parse_account_page(request.args)
    return jsonify(accounts.list_accounts(limit, offset, active))


@bp.post("/accounts")
@require_roles("ADMIN")
def create_account():
    data = json_body()
    require_fields(data, {"username", "display_name", "email", "role", "password"}, {"username", "password"})
    return jsonify(accounts.create_managed_account(data)), 201


@bp.get("/accounts/<int:account_id>")
@require_roles("ADMIN")
def get_account(account_id: int):
    return jsonify(accounts.get_account(account_id))


@bp.patch("/accounts/<int:account_id>")
@require_roles("ADMIN")
def patch_account(account_id: int):
    return jsonify(accounts.update_managed_account(account_id, json_body()))


@bp.get("/patients")
@require_roles("USER", "ADMIN")
def patients():
    limit, offset = page_args(request.args)
    search = text(request.args.get("search", ""), "search", required=False, maximum=120)
    result = catalog.list_patients(search, _active_filter(), limit, offset)
    return jsonify(result)


@bp.post("/patients")
@require_roles("ADMIN")
def create_patient():
    data = json_body()
    require_fields(data, {"first_name", "last_name", "email", "patient_number", "registered_on", "address", "reminder_consent"}, {"first_name", "last_name"})
    return jsonify(catalog.create_patient(data)), 201


@bp.get("/patients/<int:patient_id>")
@require_roles("ADMIN")
def patient(patient_id: int):
    from .catalog import patient_record

    row = catalog._patient_row(patient_id)
    if row is None:
        raise DomainError("not_found", "Patient was not found.", 404)
    return jsonify(patient_record(row))


@bp.patch("/patients/<int:patient_id>")
@require_roles("ADMIN")
def patch_patient(patient_id: int):
    return jsonify(catalog.update_patient(patient_id, json_body()))


@bp.get("/dentists")
@require_roles("USER", "ADMIN")
def dentists():
    items = catalog.list_dentists(_active_filter())
    return jsonify(_paged(items))


@bp.post("/dentists")
@require_roles("ADMIN")
def create_dentist():
    data = json_body()
    require_fields(data, {"first_name", "last_name", "license_number"}, {"first_name", "last_name"})
    return jsonify(catalog.create_dentist(data)), 201


@bp.get("/dentists/<int:dentist_id>")
@require_roles("ADMIN")
def dentist(dentist_id: int):
    row = catalog._dentist(dentist_id)
    if row is None:
        raise DomainError("not_found", "Practitioner was not found.", 404)
    return jsonify(catalog.dentist_record(row))


@bp.patch("/dentists/<int:dentist_id>")
@require_roles("ADMIN")
def patch_dentist(dentist_id: int):
    return jsonify(catalog.update_dentist(dentist_id, json_body()))


@bp.get("/dentists/<int:dentist_id>/services")
@require_roles("USER", "ADMIN")
def dentist_services(dentist_id: int):
    return jsonify(dentist_id=dentist_id, service_ids=catalog.qualifications(dentist_id))


@bp.put("/dentists/<int:dentist_id>/services")
@require_roles("ADMIN")
def replace_dentist_services(dentist_id: int):
    data = json_body()
    require_fields(data, {"service_ids"}, {"service_ids"})
    return jsonify(dentist_id=dentist_id, service_ids=catalog.set_qualifications(dentist_id, data["service_ids"]))


@bp.get("/services")
@require_roles("USER", "ADMIN")
def services():
    return jsonify(_paged(catalog.list_services(_active_filter())))


@bp.post("/services")
@require_roles("ADMIN")
def create_service():
    data = json_body()
    require_fields(data, {"name", "duration_minutes", "price_cents"}, {"name", "duration_minutes", "price_cents"})
    return jsonify(catalog.create_service(data)), 201


@bp.get("/services/<int:service_id>")
@require_roles("USER", "ADMIN")
def service(service_id: int):
    row = catalog._service(service_id)
    if row is None:
        raise DomainError("not_found", "Service was not found.", 404)
    return jsonify(catalog.service_record(row))


@bp.patch("/services/<int:service_id>")
@require_roles("ADMIN")
def patch_service(service_id: int):
    return jsonify(catalog.update_service(service_id, json_body()))


@bp.get("/rooms")
@require_roles("USER", "ADMIN")
def rooms():
    return jsonify(_paged(catalog.list_rooms(_active_filter())))


@bp.post("/rooms")
@require_roles("ADMIN")
def create_room():
    data = json_body()
    require_fields(data, {"name"}, {"name"})
    return jsonify(catalog.create_room(data)), 201


@bp.get("/rooms/<int:room_id>")
@require_roles("USER", "ADMIN")
def room(room_id: int):
    row = catalog._room(room_id)
    if row is None:
        raise DomainError("not_found", "Room was not found.", 404)
    return jsonify(catalog.room_record(row))


@bp.patch("/rooms/<int:room_id>")
@require_roles("ADMIN")
def patch_room(room_id: int):
    return jsonify(catalog.update_room(room_id, json_body()))


@bp.get("/shifts")
@require_roles("USER", "ADMIN")
def shifts():
    dentist_id = _optional_query_id("dentist_id")
    return jsonify(_paged(catalog.list_shifts(dentist_id)))


@bp.post("/shifts")
@require_roles("ADMIN")
def create_shift():
    data = json_body()
    require_fields(data, {"dentist_id", "weekday", "start_time", "end_time"}, {"dentist_id", "weekday", "start_time", "end_time"})
    return jsonify(catalog.create_shift(data)), 201


@bp.delete("/shifts/<int:shift_id>")
@require_roles("ADMIN")
def delete_shift(shift_id: int):
    catalog.delete_shift(shift_id)
    return "", 204


@bp.get("/time-off")
@require_roles("USER", "ADMIN")
def time_off():
    return jsonify(_paged(catalog.list_time_off(_optional_query_id("dentist_id"))))


@bp.post("/time-off")
@require_roles("ADMIN")
def create_time_off():
    data = json_body()
    require_fields(data, {"dentist_id", "start_at", "end_at", "reason"}, {"dentist_id", "start_at", "end_at", "reason"})
    return jsonify(catalog.create_time_off(data)), 201


@bp.delete("/time-off/<int:time_off_id>")
@require_roles("ADMIN")
def delete_time_off(time_off_id: int):
    catalog.delete_time_off(time_off_id)
    return "", 204


@bp.get("/availability")
@require_roles("USER", "ADMIN")
def availability():
    return jsonify(scheduling.availability(
        request.args.get("date"), _required_query_id("dentist_id"), _required_query_id("service_id"),
        _optional_query_id("room_id"), _optional_query_id("patient_id"),
    ))


@bp.get("/appointments")
@require_roles("USER", "ADMIN")
def appointments():
    limit, offset = page_args(request.args)
    from_date = request.args.get("from")
    to_date = request.args.get("to")
    if from_date:
        from_date = iso_date(from_date, "from")
    if to_date:
        to_date = iso_date(to_date, "to")
    if from_date and to_date:
        if from_date > to_date:
            raise invalid("'to' must be on or after 'from'.")
        if (date.fromisoformat(to_date) - date.fromisoformat(from_date)).days > 90:
            raise invalid("Appointment list ranges are limited to 91 days.")
    dentist_id = _optional_query_id("dentist_id")
    patient_id = _optional_query_id("patient_id")
    return jsonify(scheduling.list_appointments(from_date=from_date, to_date=to_date, dentist_id=dentist_id, patient_id=patient_id, status=request.args.get("status"), limit=limit, offset=offset))


@bp.post("/appointments")
@require_roles("USER", "ADMIN")
def create_appointment():
    data = json_body()
    require_fields(data, {"patient_id", "dentist_id", "service_id", "room_id", "start_at", "reason"}, {"patient_id", "dentist_id", "service_id", "room_id", "start_at"})
    return jsonify(scheduling.create_appointment(data)), 201


@bp.get("/appointments/<int:appointment_id>")
@require_roles("USER", "ADMIN")
def appointment(appointment_id: int):
    result = scheduling.appointment_record(appointment_id)
    if result is None:
        raise DomainError("not_found", "Appointment was not found.", 404)
    return jsonify(result)


@bp.patch("/appointments/<int:appointment_id>")
@require_roles("USER", "ADMIN")
def reschedule_appointment(appointment_id: int):
    return jsonify(scheduling.reschedule_appointment(appointment_id, json_body()))


@bp.post("/appointments/<int:appointment_id>/transitions")
@require_roles("USER", "ADMIN")
def transition_appointment(appointment_id: int):
    data = json_body()
    require_fields(data, {"status", "version", "reason"}, {"status", "version"})
    if data.get("status") == "completed":
        if g.account["role"] != "ADMIN":
            raise DomainError("forbidden", "Only a practice manager can complete visits.", 403)
    return jsonify(scheduling.transition_appointment(appointment_id, data))


@bp.get("/appointments/<int:appointment_id>/events")
@require_roles("USER", "ADMIN")
def appointment_events(appointment_id: int):
    return jsonify({"items": scheduling.appointment_events(appointment_id)})


@bp.get("/treatment-plans")
@require_roles("ADMIN")
def treatment_plans():
    patient_id = _optional_query_id("patient_id")
    limit, offset = page_args(request.args)
    return jsonify(care.list_plans(patient_id, request.args.get("status"), limit, offset))


@bp.post("/treatment-plans")
@require_roles("ADMIN")
def create_treatment_plan():
    data = json_body()
    require_fields(data, {"patient_id", "title"}, {"patient_id", "title"})
    return jsonify(care.create_plan(data)), 201


@bp.get("/treatment-plans/<int:plan_id>")
@require_roles("ADMIN")
def treatment_plan(plan_id: int):
    return jsonify(care.get_plan(plan_id))


@bp.patch("/treatment-plans/<int:plan_id>")
@require_roles("ADMIN")
def transition_treatment_plan(plan_id: int):
    data = json_body()
    require_fields(data, {"status"}, {"status"})
    return jsonify(care.transition_plan(plan_id, data))


@bp.post("/treatment-plans/<int:plan_id>/items")
@require_roles("ADMIN")
def create_treatment_item(plan_id: int):
    data = json_body()
    require_fields(data, {"service_id", "sequence", "tooth_reference", "notes"}, {"service_id", "sequence"})
    return jsonify(care.add_item(plan_id, data)), 201


@bp.post("/treatment-plans/<int:plan_id>/items/reorder")
@require_roles("ADMIN")
def reorder_treatment_items(plan_id: int):
    return jsonify(care.reorder_items(plan_id, json_body()))


@bp.post("/treatment-items/<int:item_id>/schedule")
@require_roles("ADMIN")
def schedule_treatment_item(item_id: int):
    data = json_body()
    require_fields(data, {"appointment_id"}, {"appointment_id"})
    return jsonify(care.schedule_item(item_id, data))


@bp.post("/treatment-items/<int:item_id>/cancel")
@require_roles("ADMIN")
def cancel_treatment_item(item_id: int):
    return jsonify(care.cancel_item(item_id))


@bp.get("/clinical-notes")
@require_roles("ADMIN")
def clinical_notes():
    patient_id = _optional_query_id("patient_id")
    appointment_id = _optional_query_id("appointment_id")
    limit, offset = page_args(request.args)
    return jsonify(care.list_notes(patient_id, appointment_id, limit, offset))


@bp.post("/clinical-notes")
@require_roles("ADMIN")
def create_clinical_note():
    data = json_body()
    require_fields(data, {"patient_id", "appointment_id", "body", "amends_note_id"}, {"patient_id", "body"})
    return jsonify(care.add_note(data)), 201


@bp.get("/waitlist")
@require_roles("USER", "ADMIN")
def waitlist():
    limit, offset = page_args(request.args)
    return jsonify(operations.list_waitlist(request.args.get("status", "waiting"), limit, offset))


@bp.post("/waitlist")
@require_roles("USER", "ADMIN")
def create_waitlist():
    data = json_body()
    require_fields(data, {"patient_id", "service_id", "dentist_id", "earliest_date", "latest_date", "preferred_start", "preferred_end"}, {"patient_id", "service_id", "earliest_date", "latest_date", "preferred_start", "preferred_end"})
    return jsonify(operations.create_waitlist_entry(data)), 201


@bp.post("/waitlist/<int:entry_id>/withdraw")
@require_roles("USER", "ADMIN")
def withdraw_waitlist(entry_id: int):
    return jsonify(operations.withdraw_waitlist_entry(entry_id))


@bp.get("/waitlist/<int:entry_id>/slots")
@require_roles("USER", "ADMIN")
def waitlist_slots(entry_id: int):
    days = integer(request.args.get("days", "14"), "days", minimum=1, maximum=14)
    return jsonify(operations.waitlist_slots(entry_id, from_value=request.args.get("from"), days=days))


@bp.post("/waitlist/<int:entry_id>/book")
@require_roles("USER", "ADMIN")
def book_waitlist(entry_id: int):
    data = json_body()
    require_fields(data, {"dentist_id", "room_id", "start_at"}, {"dentist_id", "room_id", "start_at"})
    return jsonify(operations.book_waitlist_entry(entry_id, data)), 201


@bp.get("/reminders")
@require_roles("USER", "ADMIN")
def reminders():
    limit, offset = page_args(request.args)
    return jsonify(operations.list_reminders(request.args.get("status", "pending"), limit, offset))


@bp.get("/reminders/<int:reminder_id>")
@require_roles("USER", "ADMIN")
def reminder(reminder_id: int):
    return jsonify(operations.reminder_record(reminder_id))


@bp.post("/reminders/generate")
@require_roles("USER", "ADMIN")
def generate_reminders():
    data = json_body()
    require_fields(data, {"as_of"}, {"as_of"})
    return jsonify(operations.generate_reminders(data)), 201


@bp.post("/reminders/<int:reminder_id>/acknowledge")
@require_roles("USER", "ADMIN")
def acknowledge_reminder(reminder_id: int):
    return jsonify(operations.acknowledge_reminder(reminder_id))


@bp.get("/reports/daily")
@require_roles("USER", "ADMIN")
def daily_report():
    return jsonify(operations.daily_report(request.args.get("date")))


@bp.get("/reports/utilization")
@require_roles("ADMIN")
def utilization_report():
    return jsonify(operations.utilization_report(request.args.get("from"), request.args.get("to")))


@bp.get("/reports/revenue")
@require_roles("ADMIN")
def revenue_report():
    return jsonify(operations.revenue_report(request.args.get("from"), request.args.get("to")))


@bp.get("/patient-history/<int:patient_id>")
@require_roles("USER", "ADMIN")
def patient_history(patient_id: int):
    limit, offset = page_args(request.args)
    result = scheduling.patient_history(patient_id, limit, offset)
    if g.account["role"] == "ADMIN":
        result["clinical_notes"] = care.list_notes(patient_id, limit=limit, offset=offset)
    return jsonify(result)


@bp.get("/audit")
@require_roles("ADMIN")
def audit_events():
    limit, offset = page_args(request.args)
    return jsonify(operations.list_audit(request.args.get("action"), request.args.get("entity_type"), limit, offset))
