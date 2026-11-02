# Clinic Turnos

Clinic Turnos is a local web application for managing patients, dentists, and appointments. It is a Python migration of an existing Spring Boot clinic project. It keeps the Spanish JSON field names and route shapes used by that application's browser screens.

The app uses Flask and SQLite. It runs on one machine and has no dependency on a hosted database, external API, Docker, or CDN. All people and account data created by the test suite are synthetic. The repository includes no patient records or pre-created accounts.

## Requirements

- Python 3.11 or newer
- `uv` for the reproducible setup command below, or `pip` to install from `pyproject.toml`

## Install and verify

From the repository root, run:

```sh
uv sync --locked --extra dev
uv run --locked pytest
```

The tests use Flask's request client against the application and temporary SQLite files. They cover sign-in, role checks, the patient/dentist/appointment CRUD routes, relationship validation, and data surviving a fresh app instance. No tests are skipped.

To start after installation:

```sh
uv run flask --app clinic run --host 127.0.0.1 --port 5000
```

Open <http://127.0.0.1:5000>. The database is created in `instance/clinic.sqlite3` by default. Override its location with `DATABASE_PATH`.

## Create an account

There are no demo or default passwords. In another terminal, create an account interactively; the password is entered without echo and stored as a one-way hash:

```sh
uv run clinic-admin create-user --username clinic-admin --role ADMIN
```

Use `--role USER` for a read-only patient/dentist account that can manage appointments. The command asks for a display name, optional email, and password of at least 12 characters. The same username cannot be created twice.

For deployments beyond a local single-user setup, set `SECRET_KEY` to a securely generated value in the process environment. If it is omitted, a fresh random in-memory key is generated each time the app starts; existing database records remain, but browser sessions expire on restart. Do not commit `.env` files or secret values.

## Browser behavior

- `/` is the authenticated home page, with links to the patient, dentist, and appointment screens.
- `/pacientes.html` and `/odontologos.html` list records. `ADMIN` accounts can create, edit, and delete patients and dentists. `USER` accounts can view those lists.
- `/turnos.html` lists appointments. Both roles can create, edit, and delete appointments.
- The `alta-*.html` pages provide the corresponding create forms. UI assets are local and do not load third-party scripts or styles.
- Login uses a session cookie. `/logout` clears that session.

## HTTP API

All API routes require a signed-in account. Responses use JSON. Invalid input returns `400`, missing records return `404`, forbidden role access returns `403`, and attempts to delete a patient or dentist still referenced by an appointment return `409`.

| Method | Path | Access | Purpose |
| --- | --- | --- | --- |
| GET | `/pacientes/listar` | USER, ADMIN | List patients, with nested `domicilio` |
| POST | `/pacientes` | ADMIN | Create patient and optional address |
| GET | `/pacientes/{id}` | ADMIN | Read one patient |
| PUT | `/pacientes` | ADMIN | Update patient and optional address |
| DELETE | `/pacientes/{id}` | ADMIN | Delete patient without appointments |
| GET | `/odontologos/listar` | USER, ADMIN | List dentists |
| POST | `/odontologos` | ADMIN | Create dentist |
| GET | `/odontologos/{id}` | ADMIN | Read one dentist |
| PUT | `/odontologos` | ADMIN | Update dentist |
| DELETE | `/odontologos/{id}` | ADMIN | Delete dentist without appointments |
| GET | `/turnos` | USER, ADMIN | List appointments with nested patient and dentist |
| POST | `/turnos` | USER, ADMIN | Create appointment using `paciente.id`, `odontologo.id`, and `fecha` |
| GET | `/turnos/{id}` | USER, ADMIN | Read one appointment |
| PUT | `/turnos` | USER, ADMIN | Update appointment relationships or date |
| DELETE | `/turnos/{id}` | USER, ADMIN | Delete appointment |

Example appointment request:

```json
{
  "paciente": {"id": 1},
  "odontologo": {"id": 1},
  "fecha": "2026-11-07"
}
```

Patient fields are `nombre`, `apellido`, `email`, `dni`, `fechaIngreso`, and `domicilio` (`calle`, `numero`, `localidad`, `provincia`). Dentist fields are `nombre`, `apellido`, and `matricula`. Dates use ISO `YYYY-MM-DD` format.

## Current limits

This is a local clinic CRUD application, not a hosted multi-clinic service. It does not include account recovery, public account registration, CSRF tokens, or a production deployment configuration. It does not include records from the source project's local database or tests.

## License

MIT. See [LICENSE](LICENSE).
