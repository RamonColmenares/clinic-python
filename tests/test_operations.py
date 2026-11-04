from __future__ import annotations

from datetime import datetime, timezone

from clinic.db import get_db


def test_treatment_plan_items_snapshots_lifecycle_and_append_only_notes(client, credentials, sign_in, practice, book_visit, app):
    sign_in(client, credentials)
    ids = practice(client)
    plan = client.post("/api/treatment-plans", json={"patient_id": ids["patient"]["id"], "title": "DUMMY two-step plan"}).get_json()
    item_one = client.post(f"/api/treatment-plans/{plan['id']}/items", json={"service_id": ids["service"]["id"], "sequence": 2, "tooth_reference": "14", "notes": "DUMMY initial note"})
    item_two = client.post(f"/api/treatment-plans/{plan['id']}/items", json={"service_id": ids["service"]["id"], "sequence": 1})
    assert item_one.status_code == 201
    assert item_two.status_code == 201
    assert item_one.get_json()["estimated_price_cents"] == 7500
    assert item_one.get_json()["service_name_snapshot"] == "DUMMY Checkup"
    assert client.post(f"/api/treatment-plans/{plan['id']}/items", json={"service_id": ids["service"]["id"], "sequence": 1}).status_code == 409
    reordered = client.post(f"/api/treatment-plans/{plan['id']}/items/reorder", json={"item_ids": [item_one.get_json()["id"], item_two.get_json()["id"]]})
    assert [item["id"] for item in reordered.get_json()["items"]] == [item_one.get_json()["id"], item_two.get_json()["id"]]
    assert [item["sequence"] for item in reordered.get_json()["items"]] == [1, 2]
    assert client.patch(f"/api/treatment-plans/{plan['id']}", json={"status": "active"}).status_code == 200
    assert client.post(f"/api/treatment-plans/{plan['id']}/items", json={"service_id": ids["service"]["id"], "sequence": 3}).status_code == 409

    first = book_visit(client, ids, start="2026-11-09T09:00").get_json()
    second = book_visit(client, ids, start="2026-11-09T09:30", patient_key="second_patient").get_json()
    third = book_visit(client, ids, start="2026-11-09T10:00").get_json()
    assert client.post(f"/api/treatment-items/{item_one.get_json()['id']}/schedule", json={"appointment_id": first["id"]}).status_code == 200
    wrong_link = client.post(f"/api/treatment-items/{item_two.get_json()['id']}/schedule", json={"appointment_id": second["id"]})
    assert wrong_link.status_code == 409
    assert client.post(f"/api/treatment-items/{item_two.get_json()['id']}/schedule", json={"appointment_id": third["id"]}).status_code == 200
    assert client.post(f"/api/treatment-items/{item_one.get_json()['id']}/schedule", json={"appointment_id": first["id"]}).status_code == 409
    changed_catalog = client.patch(f"/api/services/{ids['service']['id']}", json={"name": "DUMMY New Service Label", "price_cents": 9900}).get_json()
    assert changed_catalog["price_cents"] == 9900
    assert client.get(f"/api/appointments/{first['id']}").get_json()["price_cents"] == 7500

    note = client.post("/api/clinical-notes", json={"patient_id": ids["patient"]["id"], "appointment_id": first["id"], "body": "DUMMY clinical note one"})
    assert note.status_code == 201
    amendment = client.post("/api/clinical-notes", json={"patient_id": ids["patient"]["id"], "body": "DUMMY corrected note", "amends_note_id": note.get_json()["id"]})
    assert amendment.status_code == 201
    assert amendment.get_json()["amends_note_id"] == note.get_json()["id"]
    assert client.get(f"/api/clinical-notes?patient_id={ids['patient']['id']}").get_json()["total"] == 2
    mismatched = client.post("/api/clinical-notes", json={"patient_id": ids["second_patient"]["id"], "appointment_id": first["id"], "body": "DUMMY wrong chart"})
    assert mismatched.status_code == 409

    for appointment_id in (first["id"], third["id"]):
        assert client.post(f"/api/appointments/{appointment_id}/transitions", json={"status": "confirmed", "version": 1}).status_code == 200
    with app.app_context():
        app.config["CLOCK"] = lambda: datetime(2026, 11, 9, 10, 1, tzinfo=timezone.utc)
    for appointment_id in (first["id"], third["id"]):
        assert client.post(f"/api/appointments/{appointment_id}/transitions", json={"status": "checked_in", "version": 2}).status_code == 200
    assert client.post(f"/api/appointments/{first['id']}/transitions", json={"status": "completed", "version": 3}).status_code == 200
    assert client.get(f"/api/treatment-plans/{plan['id']}").get_json()["status"] == "active"
    assert client.patch(f"/api/treatment-plans/{plan['id']}", json={"status": "completed"}).status_code == 409
    assert client.post(f"/api/appointments/{third['id']}/transitions", json={"status": "completed", "version": 3}).status_code == 200
    completed_plan = client.get(f"/api/treatment-plans/{plan['id']}").get_json()
    assert completed_plan["status"] == "completed"
    assert [item["status"] for item in completed_plan["items"]] == ["completed", "completed"]
    history = client.get(f"/api/patient-history/{ids['patient']['id']}").get_json()
    assert history["clinical_notes"]["total"] == 2
    assert history["clinical_notes"]["items"][0]["body"] in {"DUMMY clinical note one", "DUMMY corrected note"}


def test_user_patient_history_excludes_clinical_notes(client, credentials, sign_in, practice, book_visit):
    sign_in(client, credentials)
    ids = practice(client)
    visit = book_visit(client, ids).get_json()
    assert client.post("/api/clinical-notes", json={"patient_id": ids["patient"]["id"], "appointment_id": visit["id"], "body": "DUMMY private note"}).status_code == 201
    client.post("/logout")
    sign_in(client, credentials, "user")
    history = client.get(f"/api/patient-history/{ids['patient']['id']}")
    assert history.status_code == 200
    assert "clinical_notes" not in history.get_json()
    assert client.get("/api/clinical-notes").status_code == 403


def test_reschedule_preserves_linked_treatment_and_clinical_history(client, credentials, sign_in, practice, book_visit):
    sign_in(client, credentials)
    ids = practice(client)
    plan = client.post("/api/treatment-plans", json={"patient_id": ids["patient"]["id"], "title": "DUMMY linked care"}).get_json()
    item = client.post(f"/api/treatment-plans/{plan['id']}/items", json={"service_id": ids["service"]["id"], "sequence": 1}).get_json()
    assert client.patch(f"/api/treatment-plans/{plan['id']}", json={"status": "active"}).status_code == 200
    linked = book_visit(client, ids, start="2026-11-09T09:00").get_json()
    assert client.post(f"/api/treatment-items/{item['id']}/schedule", json={"appointment_id": linked["id"]}).status_code == 200
    another_service = client.post("/api/services", json={"name": "DUMMY Second Service", "duration_minutes": 30, "price_cents": 4000}).get_json()

    changed_patient = client.patch(f"/api/appointments/{linked['id']}", json={"version": 1, "patient_id": ids["second_patient"]["id"], "start_at": linked["start_at"]})
    assert changed_patient.status_code == 409
    assert changed_patient.get_json()["error"] == "treatment_link_conflict"
    changed_service = client.patch(f"/api/appointments/{linked['id']}", json={"version": 1, "service_id": another_service["id"], "start_at": linked["start_at"]})
    assert changed_service.status_code == 409
    assert changed_service.get_json()["error"] == "treatment_link_conflict"
    unchanged = client.get(f"/api/appointments/{linked['id']}").get_json()
    assert (unchanged["patient_id"], unchanged["service_id"], unchanged["version"]) == (ids["patient"]["id"], ids["service"]["id"], 1)

    noted = book_visit(client, ids, start="2026-11-09T09:30").get_json()
    note = client.post("/api/clinical-notes", json={"patient_id": ids["patient"]["id"], "appointment_id": noted["id"], "body": "DUMMY recorded history"})
    assert note.status_code == 201
    moved_patient = client.patch(f"/api/appointments/{noted['id']}", json={"version": 1, "patient_id": ids["second_patient"]["id"], "start_at": noted["start_at"]})
    assert moved_patient.status_code == 409
    assert moved_patient.get_json()["error"] == "clinical_history_conflict"
    assert client.get(f"/api/appointments/{noted['id']}").get_json()["patient_id"] == ids["patient"]["id"]


def test_cancel_and_no_show_release_linked_treatment_items(client, credentials, sign_in, practice, book_visit, app):
    sign_in(client, credentials)
    ids = practice(client)
    plan = client.post("/api/treatment-plans", json={"patient_id": ids["patient"]["id"], "title": "DUMMY reschedulable care"}).get_json()
    item = client.post(f"/api/treatment-plans/{plan['id']}/items", json={"service_id": ids["service"]["id"], "sequence": 1}).get_json()
    assert client.patch(f"/api/treatment-plans/{plan['id']}", json={"status": "active"}).status_code == 200
    first = book_visit(client, ids, start="2026-11-09T09:00").get_json()
    assert client.post(f"/api/treatment-items/{item['id']}/schedule", json={"appointment_id": first["id"]}).status_code == 200

    cancelled = client.post(f"/api/appointments/{first['id']}/transitions", json={"status": "cancelled", "version": 1, "reason": "DUMMY patient rescheduled"})
    assert cancelled.status_code == 200
    released = client.get(f"/api/treatment-plans/{plan['id']}").get_json()["items"][0]
    assert (released["status"], released["appointment_id"]) == ("planned", None)
    second = book_visit(client, ids, start="2026-11-09T09:30").get_json()
    relinked = client.post(f"/api/treatment-items/{item['id']}/schedule", json={"appointment_id": second["id"]})
    assert relinked.status_code == 200
    assert relinked.get_json()["appointment_id"] == second["id"]

    with app.app_context():
        app.config["CLOCK"] = lambda: datetime(2026, 11, 9, 10, 1, tzinfo=timezone.utc)
    no_show = client.post(f"/api/appointments/{second['id']}/transitions", json={"status": "no_show", "version": 1})
    assert no_show.status_code == 200
    released_again = client.get(f"/api/treatment-plans/{plan['id']}").get_json()["items"][0]
    assert (released_again["status"], released_again["appointment_id"]) == ("planned", None)
    third = book_visit(client, ids, start="2026-11-09T10:30").get_json()
    relinked_again = client.post(f"/api/treatment-items/{item['id']}/schedule", json={"appointment_id": third["id"]})
    assert relinked_again.status_code == 200
    assert relinked_again.get_json()["appointment_id"] == third["id"]


def test_waitlist_slot_matching_atomic_booking_and_withdrawal(client, credentials, sign_in, practice, book_visit):
    sign_in(client, credentials)
    ids = practice(client)
    waiting = client.post("/api/waitlist", json={"patient_id": ids["patient"]["id"], "service_id": ids["service"]["id"], "earliest_date": "2026-11-09", "latest_date": "2026-11-09", "preferred_start": "09:00", "preferred_end": "10:00"})
    assert waiting.status_code == 201
    entry_id = waiting.get_json()["id"]
    matches = client.get(f"/api/waitlist/{entry_id}/slots?days=14")
    assert matches.status_code == 200
    assert matches.get_json()["items"][0]["start_at"] == "2026-11-09T09:00"
    assert matches.get_json()["items"][0]["dentist_id"] == ids["dentist"]["id"]
    assert client.get(f"/api/waitlist/{entry_id}/slots?days=15").status_code == 400

    occupied = book_visit(client, ids, start="2026-11-09T09:00", patient_key="second_patient").get_json()
    failed_offer = client.post(f"/api/waitlist/{entry_id}/book", json={"dentist_id": ids["dentist"]["id"], "room_id": ids["room"]["id"], "start_at": "2026-11-09T09:00"})
    assert failed_offer.status_code == 409
    assert client.get(f"/api/waitlist?status=waiting").get_json()["total"] == 1
    appointment_count = client.get("/api/appointments?limit=200").get_json()["total"]
    assert appointment_count == 1

    booked = client.post(f"/api/waitlist/{entry_id}/book", json={"dentist_id": ids["dentist"]["id"], "room_id": ids["room"]["id"], "start_at": "2026-11-09T09:30"})
    assert booked.status_code == 201
    assert booked.get_json()["entry"]["status"] == "booked"
    assert booked.get_json()["appointment"]["start_at"] == "2026-11-09T09:30"
    assert client.post(f"/api/waitlist/{entry_id}/book", json={"dentist_id": ids["dentist"]["id"], "room_id": ids["room"]["id"], "start_at": "2026-11-09T09:45"}).status_code == 409
    assert client.post(f"/api/appointments/{occupied['id']}/transitions", json={"status": "cancelled", "version": 1, "reason": "DUMMY cancellation"}).status_code == 200

    second_entry = client.post("/api/waitlist", json={"patient_id": ids["second_patient"]["id"], "service_id": ids["service"]["id"], "earliest_date": "2026-11-09", "latest_date": "2026-11-11", "preferred_start": "10:00", "preferred_end": "11:00"}).get_json()
    assert client.post(f"/api/waitlist/{second_entry['id']}/withdraw", json={}).status_code == 200
    assert client.post(f"/api/waitlist/{second_entry['id']}/book", json={"dentist_id": ids["dentist"]["id"], "room_id": ids["room"]["id"], "start_at": "2026-11-09T10:00"}).status_code == 409


def test_consent_reminder_idempotency_acknowledgement_and_cancellation(client, credentials, sign_in, practice, book_visit):
    sign_in(client, credentials)
    ids = practice(client)
    allowed = book_visit(client, ids, start="2026-11-09T09:00").get_json()
    denied = book_visit(client, ids, start="2026-11-09T09:30", patient_key="second_patient").get_json()
    generation = client.post("/api/reminders/generate", json={"as_of": "2026-11-08T09:00"})
    assert generation.status_code == 201
    assert generation.get_json()["created"] == 1
    assert generation.get_json()["items"][0]["kind"] == "day_before"
    assert "no message was sent" in generation.get_json()["items"][0]["body"]
    repeat = client.post("/api/reminders/generate", json={"as_of": "2026-11-08T09:00"})
    assert repeat.get_json()["created"] == 0
    two_hour = client.post("/api/reminders/generate", json={"as_of": "2026-11-09T08:00"})
    assert two_hour.get_json()["created"] == 1
    pending = client.get("/api/reminders?status=pending&limit=20").get_json()
    assert pending["total"] == 2
    reminder = pending["items"][0]
    acknowledged = client.post(f"/api/reminders/{reminder['id']}/acknowledge", json={})
    assert acknowledged.status_code == 200
    assert acknowledged.get_json()["status"] == "acknowledged"
    assert client.post(f"/api/reminders/{reminder['id']}/acknowledge", json={}).status_code == 409

    latest_pending = client.get("/api/reminders?status=pending&limit=20").get_json()["items"][0]
    cancelled = client.post(f"/api/appointments/{allowed['id']}/transitions", json={"status": "cancelled", "version": 1, "reason": "DUMMY schedule changed"})
    assert cancelled.status_code == 200
    all_reminders = client.get("/api/reminders?status=cancelled&limit=20").get_json()
    assert any(item["id"] == latest_pending["id"] for item in all_reminders["items"])
    assert client.get(f"/api/appointments/{denied['id']}").status_code == 200


def test_reminders_follow_schedule_revision_without_confirmation_duplicates(client, credentials, sign_in, practice, book_visit, app):
    sign_in(client, credentials)
    ids = practice(client)
    visit = book_visit(client, ids, start="2026-11-09T09:00").get_json()
    initial = client.post("/api/reminders/generate", json={"as_of": "2026-11-08T09:00"}).get_json()
    assert initial["created"] == 1
    original = initial["items"][0]
    assert (original["appointment_version"], original["schedule_revision"]) == (1, 1)

    confirmed = client.post(f"/api/appointments/{visit['id']}/transitions", json={"status": "confirmed", "version": 1})
    assert confirmed.status_code == 200
    assert (confirmed.get_json()["version"], confirmed.get_json()["schedule_revision"]) == (2, 1)
    detail_update = client.patch(f"/api/appointments/{visit['id']}", json={"version": 2, "start_at": visit["start_at"], "reason": "DUMMY reason only"})
    assert detail_update.status_code == 200
    assert (detail_update.get_json()["version"], detail_update.get_json()["schedule_revision"]) == (3, 1)
    assert client.get(f"/api/reminders/{original['id']}").get_json()["status"] == "pending"
    repeat_after_confirmation = client.post("/api/reminders/generate", json={"as_of": "2026-11-08T09:00"})
    assert repeat_after_confirmation.get_json()["created"] == 0

    moved = client.patch(f"/api/appointments/{visit['id']}", json={"version": 3, "start_at": "2026-11-09T09:30"})
    assert moved.status_code == 200
    assert (moved.get_json()["version"], moved.get_json()["schedule_revision"]) == (4, 2)
    assert client.get(f"/api/reminders/{original['id']}").get_json()["status"] == "cancelled"
    new_draft = client.post("/api/reminders/generate", json={"as_of": "2026-11-08T09:30"})
    assert new_draft.status_code == 201
    assert new_draft.get_json()["created"] == 1
    replacement = new_draft.get_json()["items"][0]
    assert replacement["schedule_revision"] == 2
    assert replacement["appointment_version"] == 4
    assert "2026-11-09T09:30" in replacement["body"]
    assert client.post("/api/reminders/generate", json={"as_of": "2026-11-08T09:30"}).get_json()["created"] == 0

    patient_update = client.patch(f"/api/patients/{ids['patient']['id']}", json={"reminder_consent": False})
    assert patient_update.status_code == 200
    assert client.get(f"/api/reminders/{replacement['id']}").get_json()["status"] == "cancelled"
    assert client.post("/api/reminders/generate", json={"as_of": "2026-11-08T09:30"}).get_json()["created"] == 0

    no_show_patient = client.post("/api/patients", json={"first_name": "DUMMY", "last_name": "No Show", "email": "no-show@example.test", "reminder_consent": True}).get_json()
    no_show_visit = client.post("/api/appointments", json={"patient_id": no_show_patient["id"], "dentist_id": ids["dentist"]["id"], "service_id": ids["service"]["id"], "room_id": ids["room"]["id"], "start_at": "2026-11-09T10:00"}).get_json()
    with app.app_context():
        app.config["CLOCK"] = lambda: datetime(2026, 11, 9, 10, 1, tzinfo=timezone.utc)
    assert client.post(f"/api/appointments/{no_show_visit['id']}/transitions", json={"status": "no_show", "version": 1}).status_code == 200
    client.post("/api/reminders/generate", json={"as_of": "2026-11-09T08:00"})
    with app.app_context():
        from clinic.db import get_db

        assert get_db().execute("SELECT COUNT(*) FROM reminders WHERE appointment_id=?", (no_show_visit["id"],)).fetchone()[0] == 0


def test_patient_consent_revocation_cancels_existing_local_drafts(client, credentials, sign_in, practice, book_visit):
    sign_in(client, credentials)
    ids = practice(client)
    visit = book_visit(client, ids).get_json()
    reminder = client.post("/api/reminders/generate", json={"as_of": "2026-11-08T09:00"}).get_json()["items"][0]
    patient_id = ids["patient"]["id"]
    updated = client.patch(f"/api/patients/{patient_id}", json={"reminder_consent": False})
    assert updated.status_code == 200
    assert client.get(f"/api/reminders/{reminder['id']}").get_json()["status"] == "cancelled"
    assert client.get("/api/reminders?status=cancelled").get_json()["total"] == 1
    assert client.post("/api/reminders/generate", json={"as_of": "2026-11-08T09:00"}).get_json()["created"] == 0


def test_reports_use_persisted_snapshots_and_room_union_capacity(client, credentials, sign_in, practice, book_visit, app):
    sign_in(client, credentials)
    ids = practice(client, second_dentist=True)
    visit = book_visit(client, ids, start="2026-11-09T09:00").get_json()
    client.patch(f"/api/services/{ids['service']['id']}", json={"name": "DUMMY Current Checkup", "price_cents": 99999})
    assert client.post(f"/api/appointments/{visit['id']}/transitions", json={"status": "confirmed", "version": 1}).status_code == 200
    with app.app_context():
        app.config["CLOCK"] = lambda: datetime(2026, 11, 9, 9, 1, tzinfo=timezone.utc)
    assert client.post(f"/api/appointments/{visit['id']}/transitions", json={"status": "checked_in", "version": 2}).status_code == 200
    assert client.post(f"/api/appointments/{visit['id']}/transitions", json={"status": "completed", "version": 3}).status_code == 200
    revenue = client.get("/api/reports/revenue?from=2026-11-09&to=2026-11-09").get_json()
    assert revenue["total_cents"] == 7500
    assert revenue["items"][0]["service_name"] == "DUMMY Checkup"
    assert revenue["estimate_only"] is True
    daily = client.get("/api/reports/daily?date=2026-11-09").get_json()
    assert daily["counts"]["completed"] == 1
    assert len(daily["agenda"]) == 1
    report = client.get("/api/reports/utilization?from=2026-11-09&to=2026-11-09").get_json()
    room = report["rooms"][0]
    assert room["staffed_open_minutes"] == 180
    assert room["appointment_minutes"] == 30
    assert room["utilization_percent"] == 16.7
    assert client.get("/api/reports/utilization?from=2026-01-01&to=2026-04-02").status_code == 400


def test_audit_allowlist_excludes_email_addresses_and_note_text(client, app, credentials, sign_in):
    sign_in(client, credentials)
    patient = client.post("/api/patients", json={"first_name": "DUMMY", "last_name": "Private Example", "email": "private@example.test", "address": {"street": "DUMMY Private Road"}}).get_json()
    client.post("/api/clinical-notes", json={"patient_id": patient["id"], "body": "DUMMY private clinical text"})
    result = client.get("/api/audit?entity_type=clinical_note").get_json()
    assert result["total"] == 1
    assert result["items"][0]["entity_id"] > 0
    with app.app_context():
        db = get_db()
        stored_audit = " ".join(row[0] for row in db.execute("SELECT metadata_json FROM audit_events"))
        assert "private@example.test" not in stored_audit
        assert "DUMMY Private Road" not in stored_audit
        assert "DUMMY private clinical text" not in stored_audit
    assert client.get("/api/audit?action=created&limit=1").get_json()["items"][0]["action"] == "created"
