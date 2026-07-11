from datetime import datetime

from .client import get_calendar_service


def get_colors() -> dict:
    """Return the full colorId mapping from the Calendar API."""
    service = get_calendar_service()
    return service.colors().get().execute()


def list_events(calendar_id: str, start: datetime, end: datetime) -> list[dict]:
    """List all events in a calendar within [start, end).

    start and end must be timezone-aware datetimes so the RFC3339
    timestamps sent to the API include an offset.
    """
    service = get_calendar_service()
    response = (
        service.events()
        .list(
            calendarId=calendar_id,
            timeMin=start.isoformat(),
            timeMax=end.isoformat(),
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )
    return [
        {
            "id": item.get("id"),
            "summary": item.get("summary"),
            "start": item.get("start"),
            "end": item.get("end"),
            "colorId": item.get("colorId"),
        }
        for item in response.get("items", [])
    ]


def check_slot_available(
    calendar_id: str,
    color_id: str,
    start: datetime,
    end: datetime,
    exclude_event_id: str | None = None,
) -> bool:
    """Return True if no event assigned to color_id overlaps [start, end).

    exclude_event_id, when provided, is skipped when checking for overlap --
    used by reschedule_appointment so the appointment's own pre-patch event
    isn't misread as a conflict against its own new slot.
    """
    events = list_events(calendar_id, start, end)
    return not any(
        e["colorId"] == color_id and e["id"] != exclude_event_id for e in events
    )


def create_event(
    calendar_id: str,
    color_id: str | None,
    summary: str,
    start: datetime,
    end: datetime,
) -> dict:
    """Insert an event into the calendar. Returns the created event resource
    (includes at least the event id).

    start and end must be timezone-aware datetimes. Do not rely on the
    timeZone field sent here for anything read back afterward — the API
    returns dateTime in the account's own default timezone regardless (see
    check_availability._parse_event_dt).
    """
    service = get_calendar_service()
    body = {
        "summary": summary,
        "start": {"dateTime": start.isoformat()},
        "end": {"dateTime": end.isoformat()},
    }
    if color_id is not None:
        body["colorId"] = color_id
    return service.events().insert(calendarId=calendar_id, body=body).execute()


def patch_event(
    calendar_id: str,
    event_id: str,
    color_id: str | None,
    start: datetime,
    end: datetime,
) -> dict:
    """Update start, end, and colorId on an existing event. summary is never
    touched. Unlike create_event, colorId is always included in the body,
    explicitly null when color_id is None -- patch() only updates keys
    present in the body, so omitting colorId (create_event's convention,
    correct for insert) would leave the event's OLD colorId untouched
    instead of clearing it when rescheduling to Juan. Confirmed empirically
    against quarter-barber-dev; see docs/reschedule_appointment_findings.md.
    Raises HttpError for anything other than 404/410 -- callers are
    expected to handle those explicitly rather than this function
    swallowing them.

    Do not rely on the timeZone field sent here for anything read back
    afterward -- same API quirk as create_event.
    """
    service = get_calendar_service()
    body = {
        "start": {"dateTime": start.isoformat()},
        "end": {"dateTime": end.isoformat()},
        "colorId": color_id,
    }
    return (
        service.events()
        .patch(calendarId=calendar_id, eventId=event_id, body=body)
        .execute()
    )


def delete_event(calendar_id: str, event_id: str) -> None:
    """Delete an event by ID. Raises HttpError for anything other than
    404/410 — callers are expected to handle those explicitly rather than
    this function swallowing them."""
    service = get_calendar_service()
    service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
