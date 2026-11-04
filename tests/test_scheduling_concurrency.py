from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier


def test_immediate_transactions_serialize_resource_conflicts_and_allow_independent_bookings(app, credentials, sign_in, practice):
    setup_client = app.test_client()
    sign_in(setup_client, credentials)
    ids = practice(setup_client, second_dentist=True, second_room=True)
    first_client = app.test_client()
    second_client = app.test_client()
    sign_in(first_client, credentials)
    sign_in(second_client, credentials)

    def submit(client, barrier, payload):
        barrier.wait(timeout=5)
        response = client.post("/api/appointments", json=payload)
        return response.status_code, response.get_json()

    conflicting = [
        {"patient_id": ids["patient"]["id"], "dentist_id": ids["dentist"]["id"], "service_id": ids["service"]["id"], "room_id": ids["room"]["id"], "start_at": "2026-11-09T09:00"},
        {"patient_id": ids["second_patient"]["id"], "dentist_id": ids["dentist"]["id"], "service_id": ids["service"]["id"], "room_id": ids["room"]["id"], "start_at": "2026-11-09T09:00"},
    ]
    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(submit, first_client, barrier, conflicting[0])
        second = pool.submit(submit, second_client, barrier, conflicting[1])
        results = [first.result(timeout=12), second.result(timeout=12)]
    assert sorted(status for status, _ in results) == [201, 409]
    assert sum(1 for status, body in results if status == 409 and body["error"] == "practitioner_conflict") == 1

    independent = [
        {"patient_id": ids["patient"]["id"], "dentist_id": ids["dentist"]["id"], "service_id": ids["service"]["id"], "room_id": ids["room"]["id"], "start_at": "2026-11-09T10:00"},
        {"patient_id": ids["second_patient"]["id"], "dentist_id": ids["dentist_two"]["id"], "service_id": ids["service"]["id"], "room_id": ids["room_two"]["id"], "start_at": "2026-11-09T10:00"},
    ]
    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(submit, first_client, barrier, independent[0])
        second = pool.submit(submit, second_client, barrier, independent[1])
        results = [first.result(timeout=12), second.result(timeout=12)]
    assert [status for status, _ in results] == [201, 201]

    with app.app_context():
        from clinic.db import get_db

        db = get_db()
        assert db.execute("SELECT COUNT(*) FROM appointments").fetchone()[0] == 3
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
