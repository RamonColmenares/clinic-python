# Clinic Desk

Clinic Desk is an offline operations workspace for a small dental practice. It manages patient and practitioner directories, services and rooms, working hours and time off, appointment scheduling, treatment plans and clinical notes, a waiting list, local reminder drafts, and operational reports.

The application uses Flask and SQLite on one machine. It does not require a hosted database, external API, Docker, CDN, email provider, payment processor, or calendar account. All examples and test fixtures use clearly marked synthetic `DUMMY` records and reserved `.test` email addresses. The project includes no real patient records or pre-created login accounts.

## Requirements and setup

- Python 3.11 or newer
- `uv` for the locked setup below, or `pip` to install the package from `pyproject.toml`

Install the runtime and developer dependencies, then run the application checks:

```sh
uv sync --locked --extra dev
uv run --locked pytest
```

Start the local server:

```sh
uv run --locked flask --app clinic:create_app run --host 127.0.0.1 --port 5000
```

Open <http://127.0.0.1:5000>. The default database is `instance/clinic.sqlite3`; set `DATABASE_PATH` to use another path. Flask listens on loopback in this command so the workspace is available only on the local machine.

The first run creates an instance directory and a random `session-secret` file with owner-only permissions. It is reused on later starts so signed-in browser sessions remain valid across a server restart. Set `SECRET_KEY` in the process environment to use a managed key instead. Keep the database, instance directory, and environment files private; they are excluded by `.gitignore`.

The clinic time zone is UTC in this baseline. API and browser date-times use minute precision as `YYYY-MM-DDTHH:MM`, without an offset. Prices are stored as integer cents. The completed-service revenue report labels its total as a USD estimate, not accounting or payment data.

## Create an account

There are no default or demo passwords. Create an account interactively; the password is entered without echo and stored as a salted one-way hash:

```sh
uv run --locked clinic-admin create-user --username clinic-admin --role ADMIN
```

Use `--role USER` for a front-desk account. `ADMIN` can manage the service catalog, practitioner qualifications and weekly schedule, treatment plans, clinical notes, and manager reports. `USER` can work with patient and practitioner directories, appointments, the waiting list, and local reminder drafts. Permissions are checked by the server on every write and read of clinical notes.

For a manual walkthrough, `uv run --locked clinic-admin generate-demo` creates a small set of clearly labeled synthetic records. It does not create an account. `clinic-admin db-check` checks the schema version, foreign-key integrity, and record counts. `clinic-admin draft-reminders --as-of YYYY-MM-DDTHH:MM` creates eligible local draft previews; it never sends a message.

## Browser workspaces

After signing in, the navigation provides these working screens:

- `/` shows today's persisted agenda, visit counts, the check-in queue, and pending reminder count.
- `/patients` searches and pages through the patient directory. Managers can add, update, and archive patients, maintain optional addresses, and record reminder consent.
- `/dentists` maintains the practitioner directory and archive state. Managers can edit records and assign service qualifications.
- `/appointments` books visits from available slots, reschedules with version checks, and advances visit status. Imported date-only visits have an explicit rescheduling notice.
- `/catalog` edits services and rooms and assigns practitioner qualifications.
- `/schedule` publishes non-overlapping weekly hours and records or removes practitioner time off.
- `/care` manages treatment plans, ordered items, links items to matching appointments, and writes append-only clinical notes and amendments. Managers only.
- `/waitlist` records date and time preferences, books a matching cancellation slot through the normal availability rules, or withdraws an entry.
- `/reminders` creates and acknowledges consent-aware local outbox drafts. Drafts are previews only; no email or text delivery is implemented.
- `/reports` shows daily operations, practitioner and room utilization, and completed-service price estimates. Managers only.
- `/audit` filters the local activity log. Activity details contain allowlisted record IDs and state changes, not raw request data or clinical note text. Managers only.
- `/accounts` creates, updates, and deactivates local USER and ADMIN accounts. Manager changes cannot remove the last active manager, and password resets invalidate existing sessions.

UI text is rendered with Jinja escaping or DOM `textContent`. Browser writes include a session-bound CSRF token. User-facing routes and JSON contracts use English names.

## JSON API

All API endpoints are under `/api`. A signed-in request to an unknown `/api/...` route still receives a JSON `401`. Responses use `{ "error": "code", "message": "..." }` for errors. Typical status codes are `400` for invalid input, `401` for a missing session, `403` for a role restriction, `404` for a missing record, and `409` for a scheduling conflict or stale appointment version.

The API is grouped into these resources:

| Resource | Main paths | Access |
| --- | --- | --- |
| Session and accounts | `GET /api/session`; `/api/accounts` | Session: USER, ADMIN; account changes: ADMIN |
| Patients | `/api/patients`, `/api/patients/{id}` | Directory: USER, ADMIN; changes and details: ADMIN |
| Practitioners and qualifications | `/api/dentists`, `/api/dentists/{id}/services` | Read: USER, ADMIN; changes: ADMIN |
| Services and rooms | `/api/services`, `/api/rooms` | Read: USER, ADMIN; changes: ADMIN |
| Working hours and time off | `/api/shifts`, `/api/time-off` | Read: USER, ADMIN; changes: ADMIN |
| Availability and visits | `/api/availability`, `/api/appointments`, `/api/appointments/{id}/transitions` | USER, ADMIN; completing a visit: ADMIN |
| Treatment plans and notes | `/api/treatment-plans`, `/api/treatment-items`, `/api/clinical-notes` | ADMIN |
| Waiting list | `/api/waitlist`, `/api/waitlist/{id}/book`, `/api/waitlist/{id}/withdraw` | USER, ADMIN |
| Reminder drafts | `/api/reminders`, `/api/reminders/generate`, `/api/reminders/{id}/acknowledge` | USER, ADMIN |
| Reports and history | `/api/reports/daily`, `/api/reports/utilization`, `/api/reports/revenue`, `/api/patient-history/{id}` | Daily operations and visit history: USER, ADMIN; utilization and revenue: ADMIN |
| Activity log | `/api/audit` | ADMIN |

Collection responses have the form `{ "items": [...], "total": 0, "limit": 50, "offset": 0 }`. The default page size is 50 and the maximum is 200. Filter and sort bounds are validated. Send the `csrf_token` returned by `GET /api/session` in the `X-CSRF-Token` header on every cookie-authenticated `POST`, `PATCH`, `PUT`, or `DELETE` request.

Example patient and appointment requests:

```json
{
  "first_name": "DUMMY",
  "last_name": "Example Patient",
  "email": "patient@example.test",
  "patient_number": "DUMMY-001",
  "reminder_consent": true
}
```

```json
{
  "patient_id": 1,
  "dentist_id": 1,
  "service_id": 1,
  "room_id": 1,
  "start_at": "2026-11-09T09:00",
  "reason": "DUMMY checkup"
}
```

Appointment duration and price are copied from the selected service when booked. Visits reserve the dentist, patient, and room for half-open time intervals, so one visit can end exactly when the next begins. Booking and rescheduling checks run in an SQLite `BEGIN IMMEDIATE` transaction. Rescheduling requires the current `version`; a stale version returns `409` without changing the visit. Appointment state changes are kept as events, and appointments with history are not deleted.

## Legacy SQLite migration

The application upgrades the previous Spanish SQLite schema on first startup and records schema version 2. It preserves account password hashes, patient and practitioner IDs, address relationships, and appointment IDs. The old national-ID field is mapped to the optional English `patient_number` field as text; new screens do not collect a national ID.

The original appointments stored only a calendar date. Each imported row receives placeholder database timestamps of `09:00` to `09:30` on that date, `legacy_imported: true`, no service or room, and a migration event. The placeholder is not presented as a real visit time, is omitted from utilization and revenue calculations, and cannot be confirmed or marked as care completed. Until it is explicitly rescheduled, the unresolved row conservatively reserves that patient's and practitioner's whole date. Rescheduling requires an active service, qualified practitioner, room, published working hours, and conflict-free time; after that, the appointment uses normal scheduling rules. Migration and foreign-key checks run in one transaction, so a failed migration leaves the original database intact.

Back up the SQLite file before opening it with a newer application version. No migration deletes the original file separately, but the schema is upgraded in place.

## Local security and privacy

Passwords are salted hashes. Session cookies are HTTP-only and same-site, login and browser/API writes use CSRF protection, and role checks are enforced server-side. SQL uses parameters; identifiers, numeric ranges, and local timestamps are validated. Audit metadata deliberately excludes request bodies, email addresses, addresses, password values, and clinical note text.

Use only synthetic patient information in public examples. This application is an offline scheduling and record-keeping aid, not a hosted multi-practice service and not a claim of clinical or regulatory compliance. It has no external email, SMS, payment, calendar, or clinical decision service.

## License

MIT. See [LICENSE](LICENSE).
