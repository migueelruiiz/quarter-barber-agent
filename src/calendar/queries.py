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
    calendar_id: str, color_id: str, start: datetime, end: datetime
) -> bool:
    """Return True if no event assigned to color_id overlaps [start, end)."""
    events = list_events(calendar_id, start, end)
    return not any(e["colorId"] == color_id for e in events)
