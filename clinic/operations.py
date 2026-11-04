"""Wait-list offers, local reminder drafts, and persisted operational reports."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from .catalog import audit, now
from .db import atomic, get_db
from .scheduling import appointment_record, availability, create_appointment
from .validation import conflict, integer, invalid, iso_date, iso_time, local_datetime, missing, text


def create_waitlist_entry(data: dict) -> dict:
    patient_id = integer(data.get("patient_id"), "patient_id", minimum=1)
    service_id = integer(data.get("service_id"), "service_id", minimum=1)
    dentist_id = None if data.get("dentist_id") in (None, "") else integer(data["dentist_id"], "dentist_id", minimum=1)
    earliest = iso_date(data.get("earliest_date"), "earliest_date")
    latest = iso_date(data.get("latest_date"), "latest_date")
    if earliest > latest:
        raise invalid("'latest_date' must be on or after 'earliest_date'.")
    preferred_start = iso_time(data.get("preferred_start"), "preferred_start")
    preferred_end = iso_time(data.get("preferred_end"), "preferred_end")
    if preferred_start >= preferred_end:
        raise invalid("The preferred time window must end after it starts.")
    db = get_db()
    with atomic(db):
        patient = db.execute("SELECT active FROM patients WHERE id=?", (patient_id,)).fetchone()
        service = db.execute("SELECT active FROM services WHERE id=?", (service_id,)).fetchone()
        if patient is None or not patient["active"]:
            raise invalid("An active patient is required for the waiting list.")
        if service is None or not service["active"]:
            raise invalid("An active service is required for the waiting list.")
        if dentist_id is not None:
            dentist = db.execute("SELECT active FROM dentists WHERE id=?", (dentist_id,)).fetchone()
            if dentist is None or not dentist["active"]:
                raise invalid("An active practitioner is required for the waiting list.")
            if db.execute("SELECT 1 FROM dentist_services WHERE dentist_id=? AND service_id=?", (dentist_id, service_id)).fetchone() is None:
                raise conflict("The practitioner is not qualified for the requested service.")
        entry_id = int(db.execute(
            """INSERT INTO waitlist_entries (patient_id,service_id,dentist_id,earliest_date,latest_date,
               preferred_start,preferred_end,status,created_at) VALUES (?,?,?,?,?,?,?,'waiting',?)""",
            (patient_id, service_id, dentist_id, earliest, latest, preferred_start, preferred_end, now()),
        ).lastrowid)
        audit("created", "waitlist_entry", entry_id, status="waiting", related_id=patient_id)
    return waitlist_entry(entry_id)


def waitlist_entry(entry_id: int) -> dict:
    row = get_db().execute(
        """SELECT w.*, p.first_name AS patient_first_name,p.last_name AS patient_last_name,
                  s.name AS service_name,d.first_name AS dentist_first_name,d.last_name AS dentist_last_name
           FROM waitlist_entries w JOIN patients p ON p.id=w.patient_id JOIN services s ON s.id=w.service_id
           LEFT JOIN dentists d ON d.id=w.dentist_id WHERE w.id=?""",
        (entry_id,),
    ).fetchone()
    if row is None:
        raise missing("Waiting-list entry")
    result = dict(row)
    result["patient_name"] = f"{row['patient_first_name']} {row['patient_last_name']}"
    result["service_name"] = row["service_name"]
    result["dentist_name"] = None if row["dentist_id"] is None else f"{row['dentist_first_name']} {row['dentist_last_name']}"
    return result


def list_waitlist(status: str | None = "waiting", limit: int = 50, offset: int = 0) -> dict:
    if status not in {None, "waiting", "booked", "withdrawn"}:
        raise invalid("'status' is not a recognized waiting-list state.")
    db = get_db()
    where, values = (" WHERE status=?", [status]) if status else ("", [])
    total = int(db.execute("SELECT COUNT(*) FROM waitlist_entries" + where, values).fetchone()[0])
    rows = db.execute("SELECT id FROM waitlist_entries" + where + " ORDER BY created_at,id LIMIT ? OFFSET ?", (*values, limit, offset)).fetchall()
    return {"items": [waitlist_entry(row["id"]) for row in rows], "total": total, "limit": limit, "offset": offset}


def waitlist_slots(entry_id: int, *, from_value: str | None = None, days: int = 14) -> dict:
    """Suggest currently valid slots that match a waiting entry's preferences."""
    days = integer(days, "days", minimum=1, maximum=14)
    db = get_db()
    entry = db.execute("SELECT * FROM waitlist_entries WHERE id=?", (entry_id,)).fetchone()
    if entry is None:
        raise missing("Waiting-list entry")
    if entry["status"] != "waiting":
        raise conflict("Only waiting entries can search for slots.", "invalid_transition")
    if from_value is None:
        from_value = max(entry["earliest_date"], now()[:10])
    first = date.fromisoformat(iso_date(from_value, "from"))
    if first.isoformat() < entry["earliest_date"]:
        first = date.fromisoformat(entry["earliest_date"])
    if first.isoformat() > entry["latest_date"]:
        raise invalid("'from' is after this waiting entry's date window.")
    final = min(first + timedelta(days=days - 1), date.fromisoformat(entry["latest_date"]))
    if entry["dentist_id"] is None:
        dentists = [row[0] for row in db.execute(
            """SELECT d.id FROM dentists d JOIN dentist_services ds ON ds.dentist_id=d.id
               WHERE d.active=1 AND ds.service_id=? ORDER BY d.last_name,d.first_name,d.id""",
            (entry["service_id"],),
        )]
    else:
        dentists = [entry["dentist_id"]]
    items = []
    day = first
    while day <= final:
        for dentist_id in dentists:
            available = availability(day.isoformat(), dentist_id, entry["service_id"], patient_id=entry["patient_id"])
            for slot in available["slots"]:
                start_time = slot["start_at"][11:]
                if entry["preferred_start"] <= start_time < entry["preferred_end"]:
                    items.append({**slot, "dentist_id": dentist_id, "service_id": entry["service_id"], "patient_id": entry["patient_id"]})
        day += timedelta(days=1)
    items.sort(key=lambda slot: (slot["start_at"], slot["dentist_id"], slot["room_id"]))
    return {"entry_id": entry_id, "from": first.isoformat(), "to": final.isoformat(), "items": items, "total": len(items)}


def withdraw_waitlist_entry(entry_id: int) -> dict:
    db = get_db()
    with atomic(db):
        row = db.execute("SELECT status FROM waitlist_entries WHERE id=?", (entry_id,)).fetchone()
        if row is None:
            raise missing("Waiting-list entry")
        if row["status"] != "waiting":
            raise conflict("Only a waiting entry can be withdrawn.", "invalid_transition")
        db.execute("UPDATE waitlist_entries SET status='withdrawn' WHERE id=?", (entry_id,))
        audit("withdrawn", "waitlist_entry", entry_id, from_status="waiting", to_status="withdrawn")
    return waitlist_entry(entry_id)


def book_waitlist_entry(entry_id: int, data: dict) -> dict:
    dentist_id = integer(data.get("dentist_id"), "dentist_id", minimum=1)
    room_id = integer(data.get("room_id"), "room_id", minimum=1)
    start = local_datetime(data.get("start_at"), "start_at")
    db = get_db()
    with atomic(db):
        entry = db.execute("SELECT * FROM waitlist_entries WHERE id=?", (entry_id,)).fetchone()
        if entry is None:
            raise missing("Waiting-list entry")
        if entry["status"] != "waiting":
            raise conflict("This waiting-list entry is no longer waiting.", "invalid_transition")
        start_moment = datetime.strptime(start, "%Y-%m-%dT%H:%M")
        if not entry["earliest_date"] <= start_moment.date().isoformat() <= entry["latest_date"]:
            raise conflict("The requested date is outside the waiting-list window.")
        if not entry["preferred_start"] <= start_moment.strftime("%H:%M") < entry["preferred_end"]:
            raise conflict("The requested time is outside the preferred time window.")
        if entry["dentist_id"] is not None and entry["dentist_id"] != dentist_id:
            raise conflict("The requested practitioner does not match this entry.")
        appointment = create_appointment({
            "patient_id": entry["patient_id"], "dentist_id": dentist_id,
            "service_id": entry["service_id"], "room_id": room_id,
            "start_at": start, "reason": "Booked from the waiting list.",
        })
        db.execute("UPDATE waitlist_entries SET status='booked',offered_appointment_id=? WHERE id=?", (appointment["id"], entry_id))
        audit("booked", "waitlist_entry", entry_id, from_status="waiting", to_status="booked", related_id=appointment["id"])
    return {"entry": waitlist_entry(entry_id), "appointment": appointment_record(appointment["id"])}


def generate_reminders(data: dict) -> dict:
    as_of = local_datetime(data.get("as_of"), "as_of")
    db = get_db()
    generated = []
    with atomic(db):
        appointments = db.execute(
            """SELECT a.id,a.start_at,a.patient_id,a.service_id,a.version,a.schedule_revision,p.email,p.reminder_consent,p.first_name,p.last_name,
                      d.first_name AS dentist_first_name,d.last_name AS dentist_last_name,s.name AS service_name
               FROM appointments a JOIN patients p ON p.id=a.patient_id
               JOIN dentists d ON d.id=a.dentist_id LEFT JOIN services s ON s.id=a.service_id
               WHERE a.status IN ('booked','confirmed') AND a.legacy_imported=0 AND p.active=1
                 AND p.reminder_consent=1 AND p.email<>'' AND a.start_at>? ORDER BY a.start_at,a.id""",
            (as_of,),
        ).fetchall()
        for visit in appointments:
            start = datetime.strptime(visit["start_at"], "%Y-%m-%dT%H:%M")
            for kind, delta in (("day_before", timedelta(days=1)), ("two_hours_before", timedelta(hours=2))):
                due = start - delta
                due_at = due.strftime("%Y-%m-%dT%H:%M")
                if due_at > as_of:
                    continue
                message = f"DUMMY reminder preview: appointment on {visit['start_at']} for {visit['service_name'] or 'clinic visit'} with {visit['dentist_first_name']} {visit['dentist_last_name']}. This is a local draft; no message was sent."
                cursor = db.execute(
                    """INSERT OR IGNORE INTO reminders
                       (appointment_id,kind,due_at,status,recipient,body,created_at,appointment_version,schedule_revision)
                       VALUES (?,?,?,'pending',?,?,?,?,?)""",
                    (visit["id"], kind, due_at, visit["email"], message, now(), visit["version"], visit["schedule_revision"]),
                )
                if cursor.rowcount == 1:
                    reminder_id = int(cursor.lastrowid)
                    generated.append(reminder_id)
                    audit("drafted", "reminder", reminder_id, status="pending", related_id=visit["id"])
    return {"created": len(generated), "items": [reminder_record(reminder_id) for reminder_id in generated]}


def reminder_record(reminder_id: int) -> dict:
    row = get_db().execute("SELECT * FROM reminders WHERE id=?", (reminder_id,)).fetchone()
    if row is None:
        raise missing("Reminder")
    return dict(row)


def list_reminders(status: str | None = "pending", limit: int = 50, offset: int = 0) -> dict:
    if status not in {None, "pending", "acknowledged", "cancelled"}:
        raise invalid("'status' is not a recognized reminder state.")
    db = get_db()
    where, values = (" WHERE status=?", [status]) if status else ("", [])
    total = int(db.execute("SELECT COUNT(*) FROM reminders" + where, values).fetchone()[0])
    rows = db.execute("SELECT id FROM reminders" + where + " ORDER BY due_at,id LIMIT ? OFFSET ?", (*values, limit, offset)).fetchall()
    return {"items": [reminder_record(row["id"]) for row in rows], "total": total, "limit": limit, "offset": offset}


def acknowledge_reminder(reminder_id: int) -> dict:
    db = get_db()
    with atomic(db):
        reminder = db.execute("SELECT status FROM reminders WHERE id=?", (reminder_id,)).fetchone()
        if reminder is None:
            raise missing("Reminder")
        if reminder["status"] != "pending":
            raise conflict("Only a pending local draft can be acknowledged.", "invalid_transition")
        db.execute("UPDATE reminders SET status='acknowledged',acknowledged_at=? WHERE id=?", (now(), reminder_id))
        audit("acknowledged", "reminder", reminder_id, from_status="pending", to_status="acknowledged")
    return reminder_record(reminder_id)


def daily_report(day_value: str) -> dict:
    day_value = iso_date(day_value, "date")
    day_end = (date.fromisoformat(day_value) + timedelta(days=1)).isoformat()
    db = get_db()
    rows = db.execute(
        """SELECT status,COUNT(*) AS count FROM appointments WHERE start_at>=? AND start_at<? GROUP BY status""",
        (day_value + "T00:00", day_end + "T00:00"),
    ).fetchall()
    counts = {status: 0 for status in ("booked", "confirmed", "checked_in", "completed", "cancelled", "no_show")}
    counts.update({row["status"]: int(row["count"]) for row in rows})
    ids = db.execute("SELECT id FROM appointments WHERE start_at>=? AND start_at<? ORDER BY start_at,id", (day_value + "T00:00", day_end + "T00:00")).fetchall()
    agenda = [appointment_record(row["id"]) for row in ids]
    return {
        "date": day_value, "counts": counts,
        "queue": [item for item in agenda if item["status"] == "checked_in"],
        "agenda": agenda,
    }


def _date_range(start_value: str, end_value: str) -> tuple[date, date]:
    first = date.fromisoformat(iso_date(start_value, "from"))
    last = date.fromisoformat(iso_date(end_value, "to"))
    if first > last:
        raise invalid("'to' must be on or after 'from'.")
    if (last - first).days > 90:
        raise invalid("Reports are limited to a 91-day window.")
    return first, last


def _minutes_between(start_at: str, end_at: str, start: datetime, end: datetime) -> int:
    first = max(datetime.strptime(start_at, "%Y-%m-%dT%H:%M"), start)
    last = min(datetime.strptime(end_at, "%Y-%m-%dT%H:%M"), end)
    return max(0, int((last - first).total_seconds() // 60))


def _clinic_staffed_capacity(db, first: date, last: date) -> int:
    """Union practitioner opening intervals per day for a one-room denominator."""
    capacity = 0
    dentists = db.execute("SELECT id FROM dentists WHERE active=1").fetchall()
    day = first
    while day <= last:
        intervals: list[tuple[datetime, datetime]] = []
        for dentist in dentists:
            shifts = db.execute("SELECT start_time,end_time FROM weekly_shifts WHERE dentist_id=? AND weekday=?", (dentist["id"], day.weekday())).fetchall()
            for shift in shifts:
                shift_start = datetime.combine(day, datetime.strptime(shift["start_time"], "%H:%M").time())
                shift_end = datetime.combine(day, datetime.strptime(shift["end_time"], "%H:%M").time())
                off_rows = db.execute("SELECT start_at,end_at FROM time_off WHERE dentist_id=? AND start_at<? AND end_at>? ORDER BY start_at", (dentist["id"], shift_end.strftime("%Y-%m-%dT%H:%M"), shift_start.strftime("%Y-%m-%dT%H:%M"))).fetchall()
                cursor = shift_start
                for off in off_rows:
                    off_start = max(shift_start, datetime.strptime(off["start_at"], "%Y-%m-%dT%H:%M"))
                    off_end = min(shift_end, datetime.strptime(off["end_at"], "%Y-%m-%dT%H:%M"))
                    if off_start > cursor:
                        intervals.append((cursor, off_start))
                    cursor = max(cursor, off_end)
                if cursor < shift_end:
                    intervals.append((cursor, shift_end))
        intervals.sort()
        if intervals:
            merged_start, merged_end = intervals[0]
            for current_start, current_end in intervals[1:]:
                if current_start <= merged_end:
                    merged_end = max(merged_end, current_end)
                else:
                    capacity += int((merged_end - merged_start).total_seconds() // 60)
                    merged_start, merged_end = current_start, current_end
            capacity += int((merged_end - merged_start).total_seconds() // 60)
        day += timedelta(days=1)
    return capacity


def utilization_report(from_value: str, to_value: str) -> dict:
    first, last = _date_range(from_value, to_value)
    window_start = datetime.combine(first, datetime.min.time())
    window_end = datetime.combine(last + timedelta(days=1), datetime.min.time())
    db = get_db()
    dentist_rows = db.execute("SELECT id,first_name,last_name FROM dentists WHERE active=1 ORDER BY last_name,first_name,id").fetchall()
    practitioner_reports = []
    staffed_capacity = 0
    by_dentist: dict[int, dict] = {}
    for dentist in dentist_rows:
        capacity = 0
        day = first
        while day <= last:
            shifts = db.execute("SELECT start_time,end_time FROM weekly_shifts WHERE dentist_id=? AND weekday=?", (dentist["id"], day.weekday())).fetchall()
            for shift in shifts:
                shift_start = datetime.combine(day, datetime.strptime(shift["start_time"], "%H:%M").time())
                shift_end = datetime.combine(day, datetime.strptime(shift["end_time"], "%H:%M").time())
                raw = int((shift_end - shift_start).total_seconds() // 60)
                off = db.execute("SELECT start_at,end_at FROM time_off WHERE dentist_id=? AND start_at<? AND end_at>?", (dentist["id"], shift_end.strftime("%Y-%m-%dT%H:%M"), shift_start.strftime("%Y-%m-%dT%H:%M"))).fetchall()
                off_minutes = sum(_minutes_between(row["start_at"], row["end_at"], shift_start, shift_end) for row in off)
                capacity += max(0, raw - min(raw, off_minutes))
            day += timedelta(days=1)
        booked_rows = db.execute(
            """SELECT start_at,end_at,status FROM appointments WHERE dentist_id=? AND legacy_imported=0
               AND status IN ('booked','confirmed','checked_in','completed') AND start_at<? AND end_at>?""",
            (dentist["id"], window_end.strftime("%Y-%m-%dT%H:%M"), window_start.strftime("%Y-%m-%dT%H:%M")),
        ).fetchall()
        minutes = sum(_minutes_between(row["start_at"], row["end_at"], window_start, window_end) for row in booked_rows)
        staffed_capacity += capacity
        entry = {"dentist_id": dentist["id"], "dentist_name": f"{dentist['first_name']} {dentist['last_name']}", "scheduled_capacity_minutes": capacity, "appointment_minutes": minutes, "utilization_percent": round(minutes * 100 / capacity, 1) if capacity else 0.0}
        practitioner_reports.append(entry)
        by_dentist[dentist["id"]] = entry

    clinic_room_capacity = _clinic_staffed_capacity(db, first, last)
    room_rows = db.execute("SELECT id,name FROM rooms WHERE active=1 ORDER BY name,id").fetchall()
    rooms = []
    for room in room_rows:
        rows = db.execute(
            """SELECT start_at,end_at FROM appointments WHERE room_id=? AND legacy_imported=0
               AND status IN ('booked','confirmed','checked_in','completed') AND start_at<? AND end_at>?""",
            (room["id"], window_end.strftime("%Y-%m-%dT%H:%M"), window_start.strftime("%Y-%m-%dT%H:%M")),
        ).fetchall()
        minutes = sum(_minutes_between(row["start_at"], row["end_at"], window_start, window_end) for row in rows)
        rooms.append({"room_id": room["id"], "room_name": room["name"], "appointment_minutes": minutes, "staffed_open_minutes": clinic_room_capacity, "utilization_percent": round(minutes * 100 / clinic_room_capacity, 1) if clinic_room_capacity else 0.0})
    return {"from": first.isoformat(), "to": last.isoformat(), "practitioners": practitioner_reports, "rooms": rooms, "room_capacity_note": "Each room uses the union of active practitioner working hours after time off as its staffed opening window; separate room opening hours are not modeled."}


def revenue_report(from_value: str, to_value: str) -> dict:
    first, last = _date_range(from_value, to_value)
    db = get_db()
    rows = db.execute(
        """SELECT a.service_id,COALESCE(NULLIF(a.service_name_snapshot,''),s.name,'Legacy appointment') AS service_name,
                  COUNT(*) AS completed_visits,SUM(a.price_cents) AS amount_cents
           FROM appointments a LEFT JOIN services s ON s.id=a.service_id
           WHERE a.status='completed' AND a.legacy_imported=0 AND a.start_at>=? AND a.start_at<?
           GROUP BY a.service_id,service_name ORDER BY service_name,service_id""",
        (first.isoformat() + "T00:00", (last + timedelta(days=1)).isoformat() + "T00:00"),
    ).fetchall()
    items = [{"service_id": row["service_id"], "service_name": row["service_name"], "completed_visits": int(row["completed_visits"]), "amount_cents": int(row["amount_cents"] or 0)} for row in rows]
    return {"from": first.isoformat(), "to": last.isoformat(), "currency": "USD", "estimate_only": True, "items": items, "total_cents": sum(item["amount_cents"] for item in items), "note": "This estimate sums stored appointment price snapshots. It is not accounting or payment data."}


def list_audit(action: str | None = None, entity_type: str | None = None, limit: int = 50, offset: int = 0) -> dict:
    clauses, values = [], []
    if action:
        clauses.append("action=?")
        values.append(text(action, "action", maximum=60))
    if entity_type:
        clauses.append("entity_type=?")
        values.append(text(entity_type, "entity_type", maximum=60))
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    db = get_db()
    total = int(db.execute("SELECT COUNT(*) FROM audit_events" + where, values).fetchone()[0])
    rows = db.execute("SELECT * FROM audit_events" + where + " ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?", (*values, limit, offset)).fetchall()
    items = []
    for row in rows:
        items.append({"id": row["id"], "actor_id": row["actor_id"], "action": row["action"], "entity_type": row["entity_type"], "entity_id": row["entity_id"], "created_at": row["created_at"], "metadata": json.loads(row["metadata_json"])})
    return {"items": items, "total": total, "limit": limit, "offset": offset}
