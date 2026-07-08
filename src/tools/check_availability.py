"""
check_availability tool.

Implements docs/check_availability_spec.md. Stateless: given a service and
optional filters (date, time_of_day, barber), returns free slots computed
directly from Google Calendar via `list_events` — never from model memory.

Assumptions not defined anywhere in config.py / CLAUDE.md, picked here and
flagged rather than silently decided:

- time_of_day morning/afternoon split: config.py/WORKING_HOURS defines no
  midday boundary. Using a fixed 14:00 clock-time boundary, independent of
  each day's actual open/close (which varies, e.g. Saturday closes at
  14:00 itself).

Same-day lead time: candidate slots for the current day that start less
than 30 minutes from now are excluded, resuming at the next 30-minute-
aligned slot at or after (now + 30min). This only applies to the current
day — future days are unaffected. See `_apply_lead_time`.
"""

from datetime import date as date_cls
from datetime import datetime, time, timedelta

from config import BARBERS, CALENDAR_ID, SENIORITY_ORDER, SERVICES, TIMEZONE, WORKING_HOURS
from src.calendar.queries import list_events

AFTERNOON_BOUNDARY = time(14, 0)
SLOT_GRANULARITY = timedelta(minutes=30)
SEARCH_DAY_CAP = 50
MIN_LEAD_TIME = timedelta(minutes=30)


def check_availability(
    service: str,
    date: date_cls | None = None,
    time_of_day: str | None = None,
    barber: str | None = None,
    max_results: int = 3,
) -> list[dict]:
    if service not in SERVICES:
        raise ValueError(f"Unknown service: {service!r}")
    if barber is not None and barber not in BARBERS:
        raise ValueError(f"Unknown barber: {barber!r}")
    if time_of_day not in (None, "morning", "afternoon"):
        raise ValueError(f"Unknown time_of_day: {time_of_day!r}")

    duration = timedelta(minutes=SERVICES[service]["duration_minutes"])

    if date is not None:
        return _slots_for_day(date, service, duration, time_of_day, barber)[:max_results]

    results: list[dict] = []
    current = _now().date()
    for _ in range(SEARCH_DAY_CAP):
        results.extend(_slots_for_day(current, service, duration, time_of_day, barber))
        if len(results) >= max_results:
            break
        current += timedelta(days=1)

    return results[:max_results]


def _slots_for_day(
    day: date_cls,
    service: str,
    duration: timedelta,
    time_of_day: str | None,
    barber: str | None,
) -> list[dict]:
    hours = WORKING_HOURS.get(day.weekday())
    if hours is None:
        return []

    day_open = datetime.combine(day, _parse_time(hours[0]), tzinfo=TIMEZONE)
    day_close = datetime.combine(day, _parse_time(hours[1]), tzinfo=TIMEZONE)

    if time_of_day == "morning":
        day_close = min(day_close, datetime.combine(day, AFTERNOON_BOUNDARY, tzinfo=TIMEZONE))
    elif time_of_day == "afternoon":
        day_open = max(day_open, datetime.combine(day, AFTERNOON_BOUNDARY, tzinfo=TIMEZONE))

    if day_open >= day_close:
        return []

    if barber is not None:
        slots = _slots_for_single_barber(day, day_open, day_close, duration, barber)
    else:
        slots = _slots_best_barber_per_gap(day, day_open, day_close, duration, service)

    return _apply_lead_time(day, day_open, slots)


def _slots_for_single_barber(
    day: date_cls,
    day_open: datetime,
    day_close: datetime,
    duration: timedelta,
    barber: str,
) -> list[dict]:
    barber_data = BARBERS[barber]
    if day.weekday() == barber_data["day_off"]:
        return []

    color_id = barber_data["color_id"]
    gaps = _free_gaps(
        day_open, day_close, _busy_intervals(day, day_open, day_close, color_id, barber)
    )
    candidates = _candidates_from_gaps(gaps, duration, day_close)

    return [
        {"start": c, "end": c + duration, "barber": barber, "color_id": color_id}
        for c in candidates
    ]


def _slots_best_barber_per_gap(
    day: date_cls,
    day_open: datetime,
    day_close: datetime,
    duration: timedelta,
    service: str,
) -> list[dict]:
    eligible = [
        b
        for b in SENIORITY_ORDER
        if service in BARBERS[b]["eligible_services"] and day.weekday() != BARBERS[b]["day_off"]
    ]

    free_slots_by_barber = {}
    for b in eligible:
        color_id = BARBERS[b]["color_id"]
        gaps = _free_gaps(
            day_open, day_close, _busy_intervals(day, day_open, day_close, color_id, b)
        )
        free_slots_by_barber[b] = set(_candidates_from_gaps(gaps, duration, day_close))

    results = []
    candidate = day_open
    while candidate + duration <= day_close:
        for b in eligible:
            if candidate in free_slots_by_barber[b]:
                results.append(
                    {
                        "start": candidate,
                        "end": candidate + duration,
                        "barber": b,
                        "color_id": BARBERS[b]["color_id"],
                    }
                )
                break
        candidate += SLOT_GRANULARITY

    return results


def _now() -> datetime:
    return datetime.now(TIMEZONE)


def _apply_lead_time(day: date_cls, day_open: datetime, slots: list[dict]) -> list[dict]:
    now = _now()
    if day != now.date():
        return slots

    earliest = now + MIN_LEAD_TIME
    if earliest <= day_open:
        return slots

    delta = earliest - day_open
    remainder = delta % SLOT_GRANULARITY
    if remainder:
        earliest += SLOT_GRANULARITY - remainder

    return [s for s in slots if s["start"] >= earliest]


def _busy_intervals(
    day: date_cls,
    day_open: datetime,
    day_close: datetime,
    color_id: str | None,
    barber: str,
) -> list[tuple[datetime, datetime]]:
    events = list_events(CALENDAR_ID, day_open, day_close)
    intervals = [
        (_parse_event_dt(e["start"]), _parse_event_dt(e["end"]))
        for e in events
        if e["colorId"] == color_id
    ]

    lunch_break = BARBERS[barber]["lunch_break"]
    if lunch_break is not None:
        lunch_start = max(datetime.combine(day, lunch_break[0], tzinfo=TIMEZONE), day_open)
        lunch_end = min(datetime.combine(day, lunch_break[1], tzinfo=TIMEZONE), day_close)
        if lunch_start < lunch_end:
            intervals.append((lunch_start, lunch_end))

    intervals.sort(key=lambda interval: interval[0])
    return intervals


def _free_gaps(
    day_open: datetime, day_close: datetime, busy: list[tuple[datetime, datetime]]
) -> list[tuple[datetime, datetime]]:
    gaps = []
    cursor = day_open
    for start, end in busy:
        if start > cursor:
            gaps.append((cursor, start))
        if end > cursor:
            cursor = end
    if cursor < day_close:
        gaps.append((cursor, day_close))
    return gaps


def _candidates_from_gaps(
    gaps: list[tuple[datetime, datetime]], duration: timedelta, day_close: datetime
) -> list[datetime]:
    candidates = []
    for gap_start, gap_end in gaps:
        candidate = gap_start
        limit = min(gap_end, day_close)
        while candidate + duration <= limit:
            candidates.append(candidate)
            candidate += SLOT_GRANULARITY
    return candidates


def _parse_time(hhmm: str) -> time:
    hour, minute = hhmm.split(":")
    return time(int(hour), int(minute))


def _parse_event_dt(side: dict) -> datetime:
    # The Calendar API returns dateTime in the account's own default
    # timezone regardless of the timeZone field sent on insert (confirmed
    # empirically against a real account defaulting to America/New_York).
    # Normalize immediately so every downstream gap/cursor/candidate is
    # consistently Europe/Madrid rather than inheriting a foreign offset.
    return datetime.fromisoformat(side["dateTime"]).astimezone(TIMEZONE)
