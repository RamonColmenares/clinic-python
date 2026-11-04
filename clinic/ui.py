"""English browser workspaces backed by the same JSON domain API."""

from __future__ import annotations

from flask import Blueprint, abort, g, render_template

bp = Blueprint("ui", __name__)

PAGES = {
    "/patients": ("patients", "Patients"),
    "/dentists": ("dentists", "Practitioners"),
    "/appointments": ("appointments", "Appointments"),
    "/catalog": ("catalog", "Services and rooms"),
    "/schedule": ("schedule", "Working schedule"),
    "/care": ("care", "Treatment plans and notes"),
    "/waitlist": ("waitlist", "Waiting list"),
    "/reminders": ("reminders", "Reminder drafts"),
    "/reports": ("reports", "Reports"),
    "/audit": ("audit", "Activity log"),
    "/accounts": ("accounts", "Local accounts"),
}


def _render(page_name: str, title: str):
    return render_template(
        "workspace.html", page_name=page_name, page_title=title,
        username=g.account["username"], display_name=g.account["display_name"],
        role=g.account["role"], csrf_token=__import__("flask").session.get("csrf_token", ""),
    )


@bp.get("/")
def home():
    return _render("home", "Practice dashboard")


@bp.get("/<path:path>")
def page(path: str):
    route = "/" + path
    entry = PAGES.get(route)
    if entry is None:
        abort(404)
    page_name, title = entry
    if page_name in {"care", "audit", "reports", "catalog", "schedule", "accounts"} and g.account["role"] != "ADMIN":
        abort(403)
    return _render(page_name, title)
