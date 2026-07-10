"""
book_appointment tool.

Implements docs/book_appointment_spec.md. Never trusts a caller-supplied end
time — it is always recomputed from SERVICES[service]["duration_minutes"].
Barber eligibility/schedule/fallback is not re-validated here; that already
happened in check_availability, whose result the caller is acting on.
color_id is the only barber-identifying field this tool needs, and matches
None explicitly for Juan (never hardcode a literal color for him).

R-7 re-verification: check_slot_available is called again immediately
before writing, to close the race window between check_availability and
this call (near-simultaneous booking by two customers).

client_phone is normalized to the bare Spanish national number (stripping
a +34/34 prefix) before being written into the summary -- see
src/tools/_phone.py.
"""

from datetime import datetime, timedelta

from config import CALENDAR_ID, SERVICES
from src.calendar.queries import check_slot_available, create_event
from src.tools._phone import normalize_spanish_phone


def book_appointment(
    service: str,
    start: datetime,
    color_id: str | None,
    client_name: str,
    client_phone: str,
) -> dict:
    if service not in SERVICES:
        raise ValueError(f"Unknown service: {service!r}")

    duration = timedelta(minutes=SERVICES[service]["duration_minutes"])
    end = start + duration

    if not check_slot_available(CALENDAR_ID, color_id, start, end):
        return {"success": False, "reason": "slot_taken"}

    summary = f"{client_name} - {normalize_spanish_phone(client_phone)}"
    event = create_event(CALENDAR_ID, color_id, summary, start, end)

    return {
        "success": True,
        "event_id": event.get("id"),
        "start": start,
        "end": end,
    }
