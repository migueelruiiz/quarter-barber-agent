"""
reschedule_appointment tool.

Implements docs/reschedule_appointment_spec.md. No `service` parameter --
duration is caller-supplied via `duration_minutes`, taken as-is from the
event already returned by a prior find_appointments call, so any manual
shortening the barber applied to the original event (e.g. bleaching) is
preserved rather than re-derived from SERVICES. `summary` (client
name/phone) is never touched -- patch_event only updates start/end/colorId.

R-7 re-verification: check_slot_available is called again immediately
before writing, self-excluding the event being rescheduled via
exclude_event_id so the appointment's own pre-patch state on its original
slot isn't misread as a conflict against its new slot.

An already-cancelled event does not raise HttpError on patch -- Calendar
marks it status "cancelled" but keeps it patchable, and patch_event's
response reflects that status directly (confirmed empirically against
quarter-barber-dev; see docs/reschedule_appointment_findings.md). That
status is checked below so a reschedule attempt on a cancelled appointment
reports not_found instead of a false-positive success, with no extra API
call beyond the patch that already has to happen.
"""

from datetime import datetime, timedelta

from googleapiclient.errors import HttpError

from config import CALENDAR_ID
from src.calendar.queries import check_slot_available, patch_event

NOT_FOUND_STATUSES = (404, 410)


def reschedule_appointment(
    event_id: str,
    new_start: datetime,
    duration_minutes: int,
    color_id: str | None,
) -> dict:
    new_end = new_start + timedelta(minutes=duration_minutes)

    if not check_slot_available(
        CALENDAR_ID, color_id, new_start, new_end, exclude_event_id=event_id
    ):
        return {"success": False, "reason": "slot_taken"}

    try:
        patched = patch_event(CALENDAR_ID, event_id, color_id, new_start, new_end)
    except HttpError as e:
        if e.resp.status in NOT_FOUND_STATUSES:
            return {"success": False, "reason": "not_found"}
        raise

    if patched.get("status") == "cancelled":
        return {"success": False, "reason": "not_found"}

    return {
        "success": True,
        "event_id": event_id,
        "start": new_start,
        "end": new_end,
    }
