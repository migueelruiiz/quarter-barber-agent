# `check_availability` — Design Spec

Target file: `src/tools/check_availability.py`
Depends on: `src/calendar/queries.py` (`list_events`), `config.py` (`SERVICES`, `WORKING_HOURS`, `BARBERS`, `SENIORITY_ORDER`)

## Signature

```python
def check_availability(
    service: str,
    date: date | None = None,
    time_of_day: str | None = None,   # "morning" | "afternoon" | None
    barber: str | None = None,
    max_results: int = 3,
) -> list[dict]:
```

Only `service` is required. All other parameters are independent and optional —
the caller (agent) may supply them in any combination, in any order, across
multiple turns of conversation. This function is stateless: it has no memory
of prior calls.

## Return value

```python
[
    {
        "start": datetime(...),   # tz-aware
        "end": datetime(...),     # tz-aware, = start + service duration
        "barber": "Dylan",
        "color_id": "9",          # or None for Juan
    },
    ...
]
```

- `end` is included for direct use by `book_appointment` (avoids recomputation)
  but must never be communicated to the customer — only `start` is presented
  in natural language.
- Empty list means no slot was found within the search bounds. Never raise,
  never fabricate a slot. The agent decides the next step (broaden search,
  or R-17 fallback to phone call).

## Core algorithm — free-slot calculation within a single day

For a given day and a given barber:

1. `list_events(calendar_id, day_start, day_end)` — one call for the whole day.
2. Filter events where `colorId == barber.color_id` (match `None` explicitly
   for Juan — do not skip/ignore null).
3. Sort matching events by start time.
4. Walk the gaps: `WORKING_HOURS.open` → first event, event → event, last
   event → `WORKING_HOURS.close`.
5. Within each gap, generate candidate start times every 30 minutes
   (`:00`/`:30` only — fixed granularity, independent of service duration):

```
for each free gap (gap_start, gap_end) in the day:
    candidate = gap_start
    while candidate + service_duration <= min(gap_end, WORKING_HOURS.close):
        yield candidate
        candidate += timedelta(minutes=30)
```

- No buffer between appointments — an event may start exactly when the
  previous one ends (cleanup/checkout time is already baked into service
  duration in `config.py`).
- Hard cutoff at `WORKING_HOURS.close` — a slot is invalid if it would end
  after closing. No exceptions; this is a strict rule (R-17 applies to
  anything outside standard scheduling).
- Service duration comes from `SERVICES`; use the maximum documented duration
  when the service includes bleaching (R-16).

## Barber resolution

Two distinct branches — must not be conflated:

**`barber` specified:**
Only search that barber's calendar. If no slot is found, return an empty
list. Never silently substitute another barber. If the customer later
changes their mind ("anyone is fine" / names a different barber), that is a
new call to `check_availability` with updated parameters — not something
this function handles internally.

**`barber=None`:**
Best-available-barber-per-slot (not a single fixed barber for the whole
search). For each 30-minute candidate slot, check eligible barbers in
`SENIORITY_ORDER` (filtered to barbers eligible for `service`, per R-13/R-15)
and assign the first one free at that exact slot. Different slots in the
result may end up assigned to different barbers — this is expected and
correct, since the customer without a barber preference primarily cares
about date/time, not consistency of barber.

## Search range when `date=None`

Search forward day by day (applying the free-slot algorithm above to each
day) until `max_results` slots are accumulated, or a **50-day safety cap**
is reached. The cap is a technical safety bound, not a business rule — it
should realistically never be hit. If reached without accumulating results,
return whatever was found (possibly empty).

`time_of_day` ("morning"/"afternoon"), when given, restricts which portion
of each day's working hours is considered — no fixed clock-time boundary is
specified here; use whatever midday split is already implied by
`WORKING_HOURS`/config, or confirm with Miguel if not already defined.

## Result ordering / truncation

Chronological order, truncated to `max_results`. No redistribution across
time-of-day or across barbers for "variety" — deliberately out of scope to
avoid unnecessary complexity.

## Relationship to R-7 (double-booking prevention)

`check_availability` is NOT used for the pre-write re-verification. R-7 is
about a race condition on a single, already-chosen slot (chatbot vs. phone/
in-person booking happening near-simultaneously) — it does not need
candidate generation or barber fallback.

Use the existing `check_slot_available(calendar_id, color_id, start, end)`
from `queries.py` as-is, called immediately before `events().insert()` in
`book_appointment`. No new variant needed.

## Explicitly out of scope for this function

- Deciding what to ask the customer next, or in what order — that's the
  agent/ReAct loop's responsibility.
- Presenting results in natural language.
- Any conversational state / memory of prior calls.
