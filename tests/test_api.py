from __future__ import annotations


def test_patient_search_pagination_validation_and_catalog_roundtrips(client, credentials, sign_in, practice):
    sign_in(client, credentials)
    ids = practice(client)
    response = client.get("/api/patients?search=Patient&limit=1&offset=1")
    assert response.status_code == 200
    assert response.get_json()["total"] == 2
    assert len(response.get_json()["items"]) == 1
    assert client.get("/api/patients?search=%25").get_json()["total"] == 0
    assert client.get("/api/patients?limit=201").status_code == 400
    assert client.get("/api/patients?offset=-1").status_code == 400

    patient = client.get(f"/api/patients/{ids['patient']['id']}").get_json()
    assert patient["address"]["street"] == "DUMMY Main Street"
    assert patient["patient_number"] == "DUMMY-001"
    assert client.patch(f"/api/patients/{patient['id']}", json={"address": None, "email": "updated@example.test"}).status_code == 200
    assert client.get(f"/api/patients/{patient['id']}").get_json()["address"] is None
    duplicate = client.patch(f"/api/patients/{ids['second_patient']['id']}", json={"patient_number": "DUMMY-001"})
    assert duplicate.status_code == 409
    assert client.patch(f"/api/patients/{patient['id']}", json={"patient_number": "bad", "extra": True}).status_code == 400

    dentist = client.get(f"/api/dentists/{ids['dentist']['id']}").get_json()
    assert dentist["license_number"] == "DUMMY-DR-1"
    assert client.get(f"/api/dentists/{dentist['id']}/services").get_json()["service_ids"] == [ids["service"]["id"]]
    assert client.put(f"/api/dentists/{dentist['id']}/services", json={"service_ids": [ids["service"]["id"], ids["service"]["id"]]}).status_code == 400
    assert client.get(f"/api/services/{ids['service']['id']}").get_json()["price_cents"] == 7500
    assert client.patch(f"/api/services/{ids['service']['id']}", json={"price_cents": -1}).status_code == 400
    assert client.get(f"/api/rooms/{ids['room']['id']}").get_json()["active"] is True


def test_availability_booking_conflicts_back_to_back_and_time_off(client, credentials, sign_in, practice, book_visit):
    sign_in(client, credentials)
    ids = practice(client, second_dentist=True, second_room=True)
    params = {"date": ids["date"], "dentist_id": ids["dentist"]["id"], "service_id": ids["service"]["id"], "room_id": ids["room"]["id"], "patient_id": ids["patient"]["id"]}
    slots = client.get("/api/availability", query_string=params).get_json()["slots"]
    assert slots[0]["start_at"] == "2026-11-09T09:00"
    assert len(slots) == 11

    first = book_visit(client, ids, start="2026-11-09T09:00")
    assert first.status_code == 201, first.get_json()
    visit = first.get_json()
    assert visit["end_at"] == "2026-11-09T09:30"
    assert visit["price_cents"] == 7500
    assert client.get("/api/availability", query_string=params).get_json()["slots"][0]["start_at"] == "2026-11-09T09:30"

    dentist_overlap = book_visit(client, ids, start="2026-11-09T09:15", patient_key="second_patient", room_key="room_two")
    assert dentist_overlap.status_code == 409
    patient_overlap = book_visit(client, ids, start="2026-11-09T09:15", dentist_key="dentist_two", room_key="room_two")
    assert patient_overlap.status_code == 409
    room_overlap = book_visit(client, ids, start="2026-11-09T09:00", dentist_key="dentist_two", patient_key="second_patient")
    assert room_overlap.status_code == 409

    appointment_id = visit["id"]
    leave_during_visit = client.post("/api/time-off", json={"dentist_id": ids["dentist"]["id"], "start_at": "2026-11-09T09:15", "end_at": "2026-11-09T09:45", "reason": "DUMMY overlapping visit"})
    assert leave_during_visit.status_code == 409
    assert client.post(f"/api/appointments/{appointment_id}/transitions", json={"status": "cancelled", "version": 1, "reason": "DUMMY changed plans"}).status_code == 200
    leave = client.post("/api/time-off", json={"dentist_id": ids["dentist"]["id"], "start_at": "2026-11-09T09:30", "end_at": "2026-11-09T10:00", "reason": "DUMMY leave"})
    assert leave.status_code == 201
    available = client.get("/api/availability", query_string=params).get_json()["slots"]
    assert not any(slot["start_at"] == "2026-11-09T09:30" for slot in available)
    overlap_leave = client.post("/api/time-off", json={"dentist_id": ids["dentist"]["id"], "start_at": "2026-11-09T09:45", "end_at": "2026-11-09T10:15", "reason": "DUMMY duplicate leave"})
    assert overlap_leave.status_code == 409
    assert client.post("/api/appointments", json={
        "patient_id": ids["patient"]["id"], "dentist_id": ids["dentist"]["id"], "service_id": ids["service"]["id"], "room_id": ids["room"]["id"], "start_at": "2026-11-09T09:30",
    }).status_code == 409


def test_reschedule_optimistic_version_lifecycle_and_history(client, credentials, sign_in, practice, book_visit, app):
    sign_in(client, credentials)
    ids = practice(client)
    created = book_visit(client, ids)
    visit = created.get_json()
    moved = client.patch(f"/api/appointments/{visit['id']}", json={"version": 1, "start_at": "2026-11-09T10:00"})
    assert moved.status_code == 200
    assert moved.get_json()["version"] == 2
    stale = client.patch(f"/api/appointments/{visit['id']}", json={"version": 1, "start_at": "2026-11-09T10:30"})
    assert stale.status_code == 409
    assert stale.get_json()["error"] == "stale_version"
    assert client.get(f"/api/appointments/{visit['id']}").get_json()["start_at"] == "2026-11-09T10:00"

    confirmed = client.post(f"/api/appointments/{visit['id']}/transitions", json={"status": "confirmed", "version": 2})
    assert confirmed.status_code == 200
    assert client.post(f"/api/appointments/{visit['id']}/transitions", json={"status": "booked", "version": 3}).status_code == 409
    with app.app_context():
        app.config["CLOCK"] = lambda: __import__("datetime").datetime(2026, 11, 9, 10, 1, tzinfo=__import__("datetime").timezone.utc)
    checked_in = client.post(f"/api/appointments/{visit['id']}/transitions", json={"status": "checked_in", "version": 3})
    assert checked_in.status_code == 200
    with client.application.test_client() as no_session:
        assert no_session.get(f"/api/appointments/{visit['id']}/events").status_code == 401
    completed = client.post(f"/api/appointments/{visit['id']}/transitions", json={"status": "completed", "version": 4})
    assert completed.status_code == 200
    history = client.get(f"/api/appointments/{visit['id']}/events").get_json()["items"]
    assert [event["to_status"] for event in history] == ["booked", "booked", "confirmed", "checked_in", "completed"]
    assert client.post(f"/api/appointments/{visit['id']}/transitions", json={"status": "cancelled", "version": 5, "reason": "DUMMY"}).status_code == 409


def test_catalog_archive_and_qualification_future_booking_guards(client, credentials, sign_in, practice, book_visit):
    sign_in(client, credentials)
    ids = practice(client)
    visit = book_visit(client, ids).get_json()
    assert client.patch(f"/api/dentists/{ids['dentist']['id']}", json={"active": False}).status_code == 409
    assert client.patch(f"/api/services/{ids['service']['id']}", json={"active": False}).status_code == 409
    assert client.patch(f"/api/rooms/{ids['room']['id']}", json={"active": False}).status_code == 409
    remove = client.put(f"/api/dentists/{ids['dentist']['id']}/services", json={"service_ids": []})
    assert remove.status_code == 409
    cancelled = client.post(f"/api/appointments/{visit['id']}/transitions", json={"status": "cancelled", "version": 1, "reason": "DUMMY"})
    assert cancelled.status_code == 200
    assert client.patch(f"/api/dentists/{ids['dentist']['id']}", json={"active": False}).status_code == 200
    assert client.get("/api/dentists?active=false").get_json()["total"] == 1


def test_input_errors_never_expose_sql_and_past_appointments_are_rejected(client, credentials, sign_in, practice):
    sign_in(client, credentials)
    ids = practice(client)
    missing_patient = client.post("/api/appointments", json={"patient_id": 9999, "dentist_id": ids["dentist"]["id"], "service_id": ids["service"]["id"], "room_id": ids["room"]["id"], "start_at": "2026-11-09T09:00"})
    assert missing_patient.status_code == 400
    assert "Traceback" not in missing_patient.get_data(as_text=True)
    past = client.post("/api/appointments", json={"patient_id": ids["patient"]["id"], "dentist_id": ids["dentist"]["id"], "service_id": ids["service"]["id"], "room_id": ids["room"]["id"], "start_at": "2026-11-06T09:00"})
    assert past.status_code == 409


def test_no_show_requires_start_time_and_terminal_visits_cannot_be_rescheduled(client, credentials, sign_in, practice, book_visit, app):
    sign_in(client, credentials)
    ids = practice(client)
    visit = book_visit(client, ids).get_json()
    before_start = client.post(f"/api/appointments/{visit['id']}/transitions", json={"status": "no_show", "version": 1})
    assert before_start.status_code == 409
    assert before_start.get_json()["error"] == "visit_not_started"
    with app.app_context():
        app.config["CLOCK"] = lambda: __import__("datetime").datetime(2026, 11, 9, 9, 1, tzinfo=__import__("datetime").timezone.utc)
    marked = client.post(f"/api/appointments/{visit['id']}/transitions", json={"status": "no_show", "version": 1})
    assert marked.status_code == 200
    assert marked.get_json()["status"] == "no_show"
    assert client.patch(f"/api/appointments/{visit['id']}", json={"version": 2, "start_at": "2026-11-09T10:00"}).status_code == 409
