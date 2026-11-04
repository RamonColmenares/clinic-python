"""Shared request parsing and domain error types."""

from __future__ import annotations

import re
from datetime import date, datetime, time, timezone
from typing import Any


class DomainError(Exception):
    """An expected application failure with an HTTP status and stable code."""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def invalid(message: str, code: str = "invalid_input") -> DomainError:
    return DomainError(code, message, 400)


def missing(entity: str) -> DomainError:
    return DomainError("not_found", f"{entity} was not found.", 404)


def conflict(message: str, code: str = "conflict") -> DomainError:
    return DomainError(code, message, 409)


def forbidden(message: str = "This account is not allowed to perform this action.") -> DomainError:
    return DomainError("forbidden", message, 403)


def require_fields(body: dict[str, Any], allowed: set[str], required: set[str] = frozenset()) -> None:
    unknown = set(body) - allowed
    if unknown:
        raise invalid(f"Unknown field(s): {', '.join(sorted(unknown))}.")
    missing_fields = required - set(body)
    if missing_fields:
        raise invalid(f"Missing field(s): {', '.join(sorted(missing_fields))}.")


def text(value: Any, field: str, *, required: bool = True, maximum: int = 240) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str):
        raise invalid(f"'{field}' must be text.")
    result = value.strip()
    if required and not result:
        raise invalid(f"'{field}' is required.")
    if len(result) > maximum:
        raise invalid(f"'{field}' must contain at most {maximum} characters.")
    return result


def integer(value: Any, field: str, *, minimum: int | None = None, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise invalid(f"'{field}' must be an integer.")
    if isinstance(value, str) and not re.fullmatch(r"-?(0|[1-9]\d*)", value):
        raise invalid(f"'{field}' must be an integer.")
    try:
        result = int(value)
    except (TypeError, ValueError) as error:
        raise invalid(f"'{field}' must be an integer.") from error
    if minimum is not None and result < minimum:
        raise invalid(f"'{field}' must be at least {minimum}.")
    if maximum is not None and result > maximum:
        raise invalid(f"'{field}' must be at most {maximum}.")
    return result


def boolean(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise invalid(f"'{field}' must be true or false.")
    return value


def iso_date(value: Any, field: str, *, minimum_year: int = 2000, maximum_year: int = 2100) -> str:
    if not isinstance(value, str):
        raise invalid(f"'{field}' must use YYYY-MM-DD format.")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise invalid(f"'{field}' must use YYYY-MM-DD format.") from error
    if parsed.isoformat() != value or not minimum_year <= parsed.year <= maximum_year:
        raise invalid(f"'{field}' must use YYYY-MM-DD format in the supported year range.")
    return value


def iso_time(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise invalid(f"'{field}' must use HH:MM format.")
    try:
        parsed = time.fromisoformat(value)
    except ValueError as error:
        raise invalid(f"'{field}' must use HH:MM format.") from error
    if parsed.strftime("%H:%M") != value:
        raise invalid(f"'{field}' must use HH:MM format.")
    return value


def local_datetime(value: Any, field: str) -> str:
    """Parse configured local wall time at minute precision, without an offset."""
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", value):
        raise invalid(f"'{field}' must use YYYY-MM-DDTHH:MM format without an offset.")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M")
    except ValueError as error:
        raise invalid(f"'{field}' must be a valid local date and time.") from error
    if not 2000 <= parsed.year <= 2100:
        raise invalid(f"'{field}' is outside the supported date range.")
    return value


def minute_now(clock=None) -> str:
    value = clock() if callable(clock) else datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).replace(second=0, microsecond=0, tzinfo=None).strftime("%Y-%m-%dT%H:%M")


def interval(start: str, end: str, *, maximum_minutes: int = 480) -> int:
    first = datetime.strptime(start, "%Y-%m-%dT%H:%M")
    last = datetime.strptime(end, "%Y-%m-%dT%H:%M")
    minutes = int((last - first).total_seconds() // 60)
    if minutes <= 0:
        raise invalid("The end time must be after the start time.")
    if minutes > maximum_minutes:
        raise invalid(f"The interval cannot exceed {maximum_minutes} minutes.")
    return minutes


def page_args(query) -> tuple[int, int]:
    limit = integer(query.get("limit", "50"), "limit", minimum=1, maximum=200)
    offset = integer(query.get("offset", "0"), "offset", minimum=0, maximum=1_000_000)
    return limit, offset
