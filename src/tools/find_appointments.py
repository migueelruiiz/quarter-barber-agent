"""
find_appointments tool.

Implements docs/find_appointments_spec.md. Stateless, read-only: given a
client_phone and/or client_name (at least one required) and an optional
date, returns candidate calendar events matching by phone digit-substring
or name token-substring against the event summary -- never from model
memory. Supports both agent-created events (structured "Name - Phone"
summary, see book_appointment.py) and free-text phone-booked events
manually annotated by a barber, which is why matching can't rely on a
single structured field.
"""

import unicodedata
from datetime import date as date_cls
from datetime import datetime, time, timedelta

from config import BARBERS, CALENDAR_ID, TIMEZONE
from src.calendar.queries import list_events

SEARCH_WINDOW_DAYS = 90


def find_appointments(
    client_phone: str,
    client_name: str,
    date: date_cls | None = None,
) -> list[dict]:
    if not client_phone and not client_name:
        raise ValueError("At least one of client_phone or client_name must be provided")

    if date is not None:
        window_start = datetime.combine(date, time.min, tzinfo=TIMEZONE)
        window_end = datetime.combine(date, time.max, tzinfo=TIMEZONE)
    else:
        window_start = _now()
        window_end = window_start + timedelta(days=SEARCH_WINDOW_DAYS)

    events = list_events(CALENDAR_ID, window_start, window_end)
    color_to_barber = {data["color_id"]: name for name, data in BARBERS.items()}

    results = []
    seen_ids = set()
    for event in events:
        summary = event.get("summary") or ""
        if not (_phone_matches(client_phone, summary) or _name_matches(client_name, summary)):
            continue

        event_id = event["id"]
        if event_id in seen_ids:
            continue
        seen_ids.add(event_id)

        results.append(
            {
                "event_id": event_id,
                "start": _parse_event_dt(event["start"]),
                "end": _parse_event_dt(event["end"]),
                "barber": color_to_barber.get(event["colorId"]),
            }
        )

    results.sort(key=lambda r: r["start"])
    return results


def _now() -> datetime:
    return datetime.now(TIMEZONE)


def _phone_matches(client_phone: str, summary: str) -> bool:
    if not client_phone:
        return False
    digits = _digits_only(client_phone)
    return bool(digits) and digits in _digits_only(summary)


def _digits_only(text: str) -> str:
    return "".join(ch for ch in text if ch.isdigit())


def _name_matches(client_name: str, summary: str) -> bool:
    if not client_name:
        return False
    normalized_summary = _normalize_text(summary)
    tokens = [t for t in _normalize_text(client_name).split() if len(t) >= 3]
    return any(token in normalized_summary for token in tokens)


def _normalize_text(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower())
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return "".join(ch for ch in stripped if ch.isalnum() or ch.isspace())


def _parse_event_dt(side: dict) -> datetime:
    # Same API quirk as check_availability._parse_event_dt: the Calendar
    # API returns dateTime in the account's own default timezone regardless
    # of the timeZone field sent on insert. Normalize immediately.
    return datetime.fromisoformat(side["dateTime"]).astimezone(TIMEZONE)
