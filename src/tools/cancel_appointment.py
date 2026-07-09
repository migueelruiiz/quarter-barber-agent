"""
cancel_appointment tool.

Implements docs/cancel_appointment_spec.md. Performs no lookup and no
confirmation of its own -- `event_id` must already be known to the caller,
obtained from a prior find_appointments call in the same conversation turn
sequence. Confirmation is a behavioral/prompt-level responsibility (the
agent must have the client explicitly confirm appointment details before
calling this), consistent with why book_appointment doesn't accept a
`confirmed` flag either.

R-7 (double-booking re-verification) does not apply here -- it protects
against two clients racing for the same slot at booking time. There is no
equivalent race condition to guard against on deletion.
"""

from googleapiclient.errors import HttpError

from config import CALENDAR_ID
from src.calendar.queries import delete_event

NOT_FOUND_STATUSES = (404, 410)


def cancel_appointment(event_id: str) -> dict:
    try:
        delete_event(CALENDAR_ID, event_id)
    except HttpError as e:
        if e.resp.status in NOT_FOUND_STATUSES:
            return {"success": False, "reason": "not_found"}
        raise

    return {"success": True}
