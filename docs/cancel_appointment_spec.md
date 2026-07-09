# `cancel_appointment` — Design Spec

Target file: `src/tools/cancel_appointment.py`
Depends on: `src/calendar/queries.py` (new `delete_event` function), `config.py` (`CALENDAR_ID`)

## New dependency: event-delete capability

No function in `src/calendar/queries.py` currently deletes an event. Add:

```python
from googleapiclient.errors import HttpError

def delete_event(calendar_id: str, event_id: str) -> None:
    """Delete an event by ID. Raises HttpError for anything other than
    404/410 — callers are expected to handle those explicitly rather than
    this function swallowing them."""
```

Uses `service.events().delete(calendarId=..., eventId=...).execute()`.

## Signature

```python
def cancel_appointment(event_id: str) -> dict:
```

`event_id` must already be known to the caller — obtained from a prior
`find_appointments` call in the same conversation turn sequence. This
tool performs no lookup and no confirmation of its own: confirmation is a
behavioral/prompt-level responsibility (the agent must have the client
explicitly confirm appointment details before calling this), consistent
with why `book_appointment` doesn't accept a `confirmed` flag either.

## Behavior

1. Call `delete_event(CALENDAR_ID, event_id)`.
2. On success: return `{"success": True}`.
3. On `HttpError` with status 404 or 410 (event already deleted, or the
   ID never existed — Calendar API returns 410 Gone for previously-deleted
   IDs): return `{"success": False, "reason": "not_found"}`. Do not raise
   — the agent must be able to tell the client gracefully ("that
   appointment no longer exists") instead of crashing.
4. Any other `HttpError` (e.g. 500, 403): re-raise. Not a business-logic
   outcome to swallow silently — R-4/R-5 require the agent to say it's
   unable to resolve the request, not fabricate a fake success.

## Relationship to R-7

R-7 (double-booking re-verification) does not apply here — it protects
against two clients racing for the same *slot* at booking time. There is
no equivalent race condition to guard against on deletion.

## Explicitly out of scope

- No lookup logic — `event_id` must already be known by the caller.
- No re-verification before delete.
- Natural-language messaging to the client — structured return only.
