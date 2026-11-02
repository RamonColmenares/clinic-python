"""Authenticated browser pages."""

from __future__ import annotations

from flask import Blueprint, abort, g, render_template

bp = Blueprint("ui", __name__)

PAGES = {
    "odontologos.html": "dentists.html",
    "pacientes.html": "patients.html",
    "turnos.html": "appointments.html",
    "alta-odontologos.html": "new-dentist.html",
    "alta-paciente.html": "new-patient.html",
    "alta-turno.html": "new-appointment.html",
}


def render_page(template: str):
    return render_template(
        template,
        username=g.account["username"],
        display_name=g.account["display_name"],
        role=g.account["role"],
    )


@bp.get("/")
def home():
    return render_page("index.html")


@bp.get("/<path:page>")
def page(page: str):
    template = PAGES.get(page)
    if template is None:
        abort(404)
    return render_page(template)
