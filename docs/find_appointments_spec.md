# `find_appointments` — Design Spec

Target file: `src/tools/find_appointments.py`
Depends on: `src/calendar/queries.py` (`list_events`), `config.py` (`CALENDAR_ID`, `BARBERS`, `TIMEZONE`)

## Signature

```python
def find_appointments(
    client_phone: str,
    client_name: str,
    date: date | None = None,
) -> list[dict]:
```

`client_phone` and `client_name` are both optional individually, but at
least one must be non-empty (`ValueError` otherwise — see step 7 below).
Stateless, read-only, no side effects. Supports both agent-created
appointments (structured `summary`, see `book_appointment_spec.md`) and
phone-booked appointments manually annotated by a barber (free-text
`summary`, no guaranteed format) — this is the reason matching cannot rely
on a single structured field.

## Return value

```python
[
    {
        "event_id": "abc123",
        "start": datetime(...),   # tz-aware, Europe/Madrid
        "end": datetime(...),     # tz-aware, Europe/Madrid
        "barber": "dylan",        # or None if colorId matches no known barber
    },
    ...
]
```

Chronological order, ascending by `start`. Empty list means no match —
never raise for "not found", only for missing input (step 7).

## Behavior

1. Resolve the search window:
   - If `date` is given: `window_start = datetime.combine(date, time.min, tzinfo=TIMEZONE)`,
     `window_end = datetime.combine(date, time.max, tzinfo=TIMEZONE)`.
   - If `date` is `None`: `window_start = _now()`, `window_end = _now() + timedelta(days=90)`.
2. Call `list_events(CALENDAR_ID, window_start, window_end)` once — a
   single call over the full window, not a per-day loop (unlike
   `check_availability`, this isn't evaluating 30-minute granularity).
3. Normalize each event's `start`/`end` with `.astimezone(TIMEZONE)`,
   same as `check_availability._parse_event_dt` (same API quirk applies).
4. For each event, evaluate phone match and name match against its
   `summary` (see "Matching rules" below). Include the event if either
   matches.
5. Resolve `barber` via reverse lookup of `colorId` against
   `BARBERS[*]["color_id"]` (match `None` explicitly for Juan). If no
   barber has that `colorId`, set `"barber": None`.
6. Deduplicate by `event_id` (an event could theoretically satisfy both
   phone and name match), sort ascending by `start`.
7. If both `client_phone` and `client_name` are empty/falsy, raise
   `ValueError` before calling `list_events`.

## Matching rules

**Phone match**: normalize `client_phone` with the same Spanish national-
number normalization used by `book_appointment` (strip a `+34`/`34`
prefix if present — see `src/tools/_phone.py`), then strip all non-digit
characters from both the normalized `client_phone` and the event's
`summary`. Match if the digit-only client phone is a non-empty substring
of the digit-only summary. The normalization is deliberately NOT applied
to the summary side: the summary's digit string may contain unrelated
digits from free-text content (e.g. a time like "17h" in a barber's
manual annotation), so the "11 digits starting with 34" condition isn't
reliably meaningful there — applying it could strip digits out of
context. This is safe: a normalized 9-digit search string remains a valid
substring of a longer digit string regardless of what prefix or
surrounding text that longer string contains.

**Name match**: normalize both `client_name` and the event's `summary` —
lowercase, strip accents (Unicode NFKD decomposition), remove punctuation.
Split the normalized client name on whitespace into tokens. Match if any
token of length >= 3 is a substring of the normalized summary.

An event is included if it satisfies phone match OR name match (no
confidence ranking between the two — both surfaced identically, per
CLAUDE.md decision to let the client pick from the full candidate list
rather than the tool guessing).

## Explicitly out of scope

- No confirmation, no cancellation — this tool only locates and returns
  candidates. The calling agent presents the list to the client and
  obtains explicit confirmation before invoking `cancel_appointment` or
  `reschedule_appointment`.
- No service/duration is inferred or returned — irrelevant to the client
  during cancel/reschedule (see CLAUDE.md: only start time matters to the
  client in this flow).
- No barber filtering on input — a client may not remember or know who
  they were assigned; all matching events are returned regardless of
  `colorId`.
- No fuzzy/edit-distance matching — token-substring only. If insufficient
  in practice, revisit as a separate change.

## Test cases to cover

- Phone match against an agent-created event (`"{name} - {phone}"` format).
- Name-token match against a free-text barber-annotated summary (e.g.
  `client_name="Juan Pérez"`, `summary="Juanito corte 17h"`).
- No match → empty list (not an error).
- `date` filter narrows correctly to a single day.
- Deduplication when both phone and name independently match the same
  event.
- Events outside the 90-day window are not returned (mock boundary date).
- `colorId` with no matching barber in `BARBERS` → `"barber": None`,
  event still included.
- `ValueError` when both `client_phone` and `client_name` are empty.