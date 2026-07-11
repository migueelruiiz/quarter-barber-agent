# `reschedule_appointment` — Design Spec

Target file: `src/tools/reschedule_appointment.py`
Depends on: `src/calendar/queries.py` (`check_slot_available` — extended, `patch_event` — new), `config.py` (`CALENDAR_ID`)

## New dependency: event-patch capability

No function in `src/calendar/queries.py` currently updates an existing event in place. Add:

```python
def patch_event(
    calendar_id: str,
    event_id: str,
    color_id: str | None,
    start: datetime,
    end: datetime,
) -> dict:
    """Update start, end, and colorId on an existing event. Raises HttpError
    for anything other than 404/410 — callers are expected to handle those
    explicitly rather than this function swallowing them."""
```

Uses `service.events().patch(calendarId=..., eventId=..., body={...}).execute()`. `summary` is never touched — the client name/phone pair on the original event stays exactly as booked. Do not set the `timeZone` field on the request body as authoritative for anything read back — same confirmed API quirk as `book_appointment`.

## Extended dependency: `check_slot_available` gains `exclude_event_id`

```python
def check_slot_available(
    calendar_id: str,
    color_id: str | None,
    start: datetime,
    end: datetime,
    exclude_event_id: str | None = None,
) -> bool:
```

New parameter, optional, defaults to `None` — fully backward-compatible with `book_appointment`'s existing call site. When provided, an event whose `id` matches `exclude_event_id` is ignored when checking for overlap.

Without this, R-7 re-verification for a reschedule would always find the appointment's own original event still occupying its original slot (the `patch` hasn't happened yet at verification time) and incorrectly report `slot_taken` whenever the new time range overlaps the old one with the same barber — the single most common reschedule case (e.g. moving a 60-minute appointment from 10:00 to 10:30 with the same barber).

## Signature

```python
def reschedule_appointment(
    event_id: str,
    new_start: datetime,
    duration_minutes: int,
    color_id: str | None,
) -> dict:
```

Notes on parameters:
- No `service` parameter. Per the owner's confirmation, the service booked is irrelevant to both the barber (a 30-minute block is interchangeable between `corte`/`barba`) and the client (only the appointment time matters — same reasoning already documented in `find_appointments_spec.md`). Re-deriving duration from `SERVICES[service]` would also silently discard any manual shortening the barber applied to the original event (see `CLAUDE.md`, "Bleaching duration").
- `duration_minutes` is computed by the caller as `(original_end - original_start)` from the event already returned by a prior `find_appointments` call in the same conversation turn sequence — never re-fetched inside this tool. This preserves whatever actual duration the event currently has, manually adjusted or not.
- `color_id` is always required, and is always the result of a fresh `check_availability` call for the new date/time — never inferred from the original event's `colorId`. A reschedule may or may not change barber; both cases are structurally identical to this tool, since the caller is responsible for having re-verified real availability for the new slot (R-11) regardless of whether the barber changes. `color_id` matches `None` explicitly for Juan, same convention as `book_appointment`.
- No `end` parameter — always computed internally as `new_start + timedelta(minutes=duration_minutes)`.

## Behavior

1. **Resolve new end time.**
   ```
   new_end = new_start + timedelta(minutes=duration_minutes)
   ```

2. **R-7 re-verification, self-excluding.** Immediately before writing, call
   `check_slot_available(CALENDAR_ID, color_id, new_start, new_end, exclude_event_id=event_id)`.
   - If `False`: return `{"success": False, "reason": "slot_taken"}`. Do not
     raise, do not touch the original event. Same expected-outcome handling
     as `book_appointment`'s race condition — the agent re-runs
     `check_availability` and offers alternatives.
   - If `True`: proceed to step 3.

3. **Patch the event.**
   ```
   patch_event(CALENDAR_ID, event_id, color_id, new_start, new_end)
   ```
   `summary` is left untouched by `patch_event` — no `client_name`/`client_phone` input needed anywhere in this tool.

4. **404/410 handling.** If `patch_event` raises `HttpError` with status
   404 or 410 (event never existed): return `{"success": False, "reason": "not_found"}`.
   Any other `HttpError` propagates unhandled — same convention as
   `cancel_appointment`.

   An already-cancelled event does *not* raise `HttpError` on patch — it
   succeeds and returns a resource with `status: "cancelled"`. This is
   checked separately, directly on the successful `patch_event` return
   value (`patched.get("status") == "cancelled"` → `not_found`), with no
   extra API call. See `docs/reschedule_appointment_findings.md` for the
   empirical basis.

5. **Return value on success:**
   ```python
   {
       "success": True,
       "event_id": event_id,
       "start": new_start,
       "end": new_end,
   }
   ```

## Relationship to R-7

Same protection as `book_appointment` — guards against a near-simultaneous booking race on the *new* slot. The `exclude_event_id` extension exists solely to prevent the appointment's own pre-patch state from being misread as a conflict.

## Explicitly out of scope

- Barber eligibility / schedule / fallback validation for the new slot — already done by the `check_availability` call the caller made before invoking this tool.
- Client confirmation before executing — behavioral/prompt-level responsibility, consistent with `book_appointment` and `cancel_appointment` (no `confirmed` parameter on any write tool).
- Locating `event_id` — must already be known to the caller, obtained from a prior `find_appointments` call.
- Any change to the event's `summary` (client name/phone) — untouched by design.
- Any change to which "service" was booked — never stored, never referenced.

## Test cases to cover

- Successful reschedule → `{"success": True, ...}` with correct `start`/`end`, `patch_event` called once with the correct arguments.
- `slot_taken` (new range genuinely conflicts with a different event) → `{"success": False, "reason": "slot_taken"}`, `patch_event` never called.
- Self-overlap case: same barber, new range overlaps the event's own original range → must NOT be reported as `slot_taken` (regression test for `exclude_event_id`).
- `HttpError` 404 on patch → `{"success": False, "reason": "not_found"}`.
- `HttpError` 410 on patch → same as above.
- Any other `HttpError` (e.g. 500) → propagates unhandled.
- `color_id=None` (Juan) passed through explicitly to `patch_event`, not silently dropped or defaulted.
- Duration is taken as-is from `duration_minutes`, independent of any `SERVICES` value — no re-derivation from a service name anywhere in the tool.
