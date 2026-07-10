# `book_appointment` — Design Spec

Target file: `src/tools/book_appointment.py`
Depends on: `config.py` (`SERVICES`, `CALENDAR_ID`), `src/calendar/queries.py`
(`check_slot_available` — existing, R-7 verification)

## New dependency: event-write capability

No function in `src/calendar/queries.py` or `client.py` currently writes to
the Calendar. `book_appointment` is the first tool to do so. A new function
is needed in `queries.py`, e.g.:

```python
def create_event(
    calendar_id: str,
    color_id: str | None,
    summary: str,
    start: datetime,
    end: datetime,
) -> dict:
    """Insert an event into the calendar. Returns the created event resource
    (must include at least the event id)."""
```

Uses `service.events().insert(calendarId=..., body={...}).execute()`. Do not
set the `timeZone` field on insert as authoritative for anything read back —
per the confirmed empirical finding in `check_availability`, the API
ignores it and returns `dateTime` in the account's own default timezone
regardless.

## Signature

```python
def book_appointment(
    service: str,
    start: datetime,
    color_id: str | None,
    client_name: str,
    client_phone: str,
) -> dict:
```

Notes on parameters:
- No `end` parameter — always recomputed internally from
  `SERVICES[service]["duration_minutes"]`. Never trust a caller-supplied
  `end`; this guarantees a booked event can never be written without a
  valid, correctly-derived end time.
- No `barber` parameter. `book_appointment` does not re-validate barber
  eligibility, schedule, or seniority fallback — all of that already
  happened in `check_availability`, whose result is what the caller is
  acting on. `color_id` is the only barber-identifying field the Calendar
  API needs, and matches `None` explicitly for Juan (never hardcode a
  literal color for him).

## Behavior

1. **Resolve duration and end time.**
   ```
   if service not in SERVICES: fail before any API call
   duration = SERVICES[service]["duration_minutes"]
   end = start + duration
   ```
   If duration cannot be resolved, the function must fail here — never
   proceed to write an event with a missing or fabricated end time.

2. **R-7 re-verification.** Immediately before writing, call
   `check_slot_available(CALENDAR_ID, color_id, start, end)`.
   - If `False`: return `{"success": False, "reason": "slot_taken"}`. Do
     not raise. This is an expected, common outcome (near-simultaneous
     booking race), not an error condition — the agent is expected to
     tell the customer something like "that slot was just taken" and
     re-run `check_availability` with whatever constraints the customer
     already gave (service, barber preference if any, etc.), rather than
     surfacing a raw error.
   - If `True`: proceed to step 3.

3. **Create the event.**
   - Title (`summary`): exactly `f"{client_name} - {client_phone}"`, where
     `client_phone` has been normalized to the bare 9-digit Spanish
     national number first (stripping a `+34`/`34` prefix if present —
     see `src/tools/_phone.py`; any other length/prefix is left as
     digits-only, unmodified further). No description, no additional
     fields.
   - `color_id` passed through as given — the caller (agent) is
     responsible for having obtained a valid `color_id` from a prior
     `check_availability` call.

4. **Return value on success:**
   ```python
   {
       "success": True,
       "event_id": ...,   # needed later by cancel/reschedule
       "start": ...,
       "end": ...,
   }
   ```

## Explicitly out of scope

- Barber eligibility / schedule / fallback validation — already done by
  `check_availability`.
- Any retry or re-search logic on `slot_taken` — that's the agent's job,
  not this tool's.
- Natural-language messaging to the customer — the tool returns structured
  data only.
