"""
Unit tests for src/tools/check_availability.py.

`list_events` (the only I/O boundary — real Google Calendar API calls) is
monkeypatched in every test so these run offline and deterministically,
against a fixed Monday that is a working day for every barber (see
config.BARBERS day_off values: dylan=Tue, yuri=Thu, rafa=Wed, juan=Sat).
"""

from datetime import date, datetime, time, timedelta, timezone

import config
from src.tools import check_availability as ca

MONDAY = date(2026, 8, 3)
assert MONDAY.weekday() == 0


def _event(day: date, start_hhmm: str, end_hhmm: str, color_id):
    def _dt(hhmm):
        hour, minute = hhmm.split(":")
        return datetime.combine(day, time(int(hour), int(minute)), tzinfo=ca.TIMEZONE)

    return {
        "id": "evt",
        "summary": "booked",
        "start": {"dateTime": _dt(start_hhmm).isoformat()},
        "end": {"dateTime": _dt(end_hhmm).isoformat()},
        "colorId": color_id,
    }


def _event_foreign_offset(day: date, start_hhmm: str, end_hhmm: str, color_id):
    """Same instants as `_event`, but serialized with a fixed -04:00 offset
    instead of Europe/Madrid's own offset — mirrors what the real Calendar
    API actually returns when the account's default timezone differs from
    Madrid's (confirmed against the real quarter-barber-dev calendar, whose
    account default is America/New_York, regardless of the `timeZone` field
    sent on insert)."""

    def _dt(hhmm):
        hour, minute = hhmm.split(":")
        madrid_dt = datetime.combine(day, time(int(hour), int(minute)), tzinfo=ca.TIMEZONE)
        return madrid_dt.astimezone(timezone(timedelta(hours=-4)))

    return {
        "id": "evt-foreign-offset",
        "summary": "booked (foreign offset)",
        "start": {"dateTime": _dt(start_hhmm).isoformat()},
        "end": {"dateTime": _dt(end_hhmm).isoformat()},
        "colorId": color_id,
    }


def _patch_events(monkeypatch, events):
    calls = []

    def fake_list_events(calendar_id, start, end):
        calls.append((calendar_id, start, end))
        return events

    monkeypatch.setattr(ca, "list_events", fake_list_events)
    return calls


# ---------------------------------------------------------------------------
# 30-minute granularity
# ---------------------------------------------------------------------------

def test_slots_are_generated_every_30_minutes(monkeypatch):
    _patch_events(monkeypatch, [])

    slots = ca.check_availability(
        service="corte", date=MONDAY, barber="dylan", max_results=100
    )

    starts = [s["start"] for s in slots]
    assert starts[0] == datetime.combine(MONDAY, time(10, 0), tzinfo=ca.TIMEZONE)
    for earlier, later in zip(starts, starts[1:]):
        assert later - earlier == timedelta(minutes=30)


# ---------------------------------------------------------------------------
# Hard cutoff at closing time
# ---------------------------------------------------------------------------

def test_no_slot_extends_past_closing_time(monkeypatch):
    _patch_events(monkeypatch, [])

    # corte_barba is 60 minutes; working hours close at 20:00.
    slots = ca.check_availability(
        service="corte_barba", date=MONDAY, barber="dylan", max_results=100
    )

    close = datetime.combine(MONDAY, time(20, 0), tzinfo=ca.TIMEZONE)
    for slot in slots:
        assert slot["end"] <= close
    # last valid start is 19:00 (19:00-20:00); 19:30 would end at 20:30.
    assert slots[-1]["start"] == datetime.combine(MONDAY, time(19, 0), tzinfo=ca.TIMEZONE)
    assert datetime.combine(MONDAY, time(19, 30), tzinfo=ca.TIMEZONE) not in [
        s["start"] for s in slots
    ]


# ---------------------------------------------------------------------------
# No buffer between back-to-back events
# ---------------------------------------------------------------------------

def test_no_buffer_required_between_events(monkeypatch):
    events = [_event(MONDAY, "12:00", "13:00", "9")]  # dylan's color_id
    _patch_events(monkeypatch, events)

    slots = ca.check_availability(
        service="corte", date=MONDAY, barber="dylan", max_results=100
    )
    starts = {s["start"] for s in slots}

    # ends exactly when the event starts -> allowed
    assert datetime.combine(MONDAY, time(11, 30), tzinfo=ca.TIMEZONE) in starts
    # starts exactly when the event ends -> allowed, no buffer
    assert datetime.combine(MONDAY, time(13, 0), tzinfo=ca.TIMEZONE) in starts
    # inside the busy event -> excluded
    assert datetime.combine(MONDAY, time(12, 30), tzinfo=ca.TIMEZONE) not in starts


# ---------------------------------------------------------------------------
# Juan's colorId=None matched explicitly
# ---------------------------------------------------------------------------

def test_juan_null_color_id_blocks_his_own_slots_only(monkeypatch):
    events = [
        _event(MONDAY, "11:00", "11:30", None),  # Juan's own event (colorId null)
        _event(MONDAY, "12:00", "12:30", "9"),  # Dylan's event, must not affect Juan
    ]
    _patch_events(monkeypatch, events)

    slots = ca.check_availability(
        service="corte", date=MONDAY, barber="juan", max_results=100
    )
    starts = {s["start"] for s in slots}

    assert datetime.combine(MONDAY, time(11, 0), tzinfo=ca.TIMEZONE) not in starts
    assert datetime.combine(MONDAY, time(12, 0), tzinfo=ca.TIMEZONE) in starts


# ---------------------------------------------------------------------------
# barber specified but unavailable -> empty list, no substitution
# ---------------------------------------------------------------------------

def test_specified_barber_fully_booked_returns_empty_no_substitution(monkeypatch):
    events = [_event(MONDAY, "10:00", "20:00", "10")]  # rafa's color_id, whole day
    _patch_events(monkeypatch, events)

    slots = ca.check_availability(
        service="corte", date=MONDAY, barber="rafa", max_results=100
    )

    assert slots == []


# ---------------------------------------------------------------------------
# barber=None mixes barbers per slot, following seniority fallback
# ---------------------------------------------------------------------------

def test_no_barber_specified_assigns_best_available_per_slot(monkeypatch):
    events = [
        _event(MONDAY, "10:00", "11:00", "9"),  # dylan busy 10:00-11:00
        _event(MONDAY, "10:30", "11:00", "6"),  # yuri busy 10:30-11:00
    ]
    _patch_events(monkeypatch, events)

    slots = ca.check_availability(service="corte", date=MONDAY, barber=None, max_results=3)

    assert [s["start"] for s in slots] == [
        datetime.combine(MONDAY, time(10, 0), tzinfo=ca.TIMEZONE),
        datetime.combine(MONDAY, time(10, 30), tzinfo=ca.TIMEZONE),
        datetime.combine(MONDAY, time(11, 0), tzinfo=ca.TIMEZONE),
    ]
    # dylan busy at 10:00 -> next in SENIORITY_ORDER (yuri) free at 10:00
    assert slots[0]["barber"] == "yuri"
    # dylan and yuri both busy at 10:30 -> falls to rafa
    assert slots[1]["barber"] == "rafa"
    # dylan free again at 11:00 -> back to dylan (first in seniority order)
    assert slots[2]["barber"] == "dylan"


# ---------------------------------------------------------------------------
# Bleaching uses max documented duration (R-16)
# ---------------------------------------------------------------------------

def test_bleaching_service_reserves_max_documented_duration(monkeypatch):
    _patch_events(monkeypatch, [])

    slots = ca.check_availability(
        service="decoloracion", date=MONDAY, barber="dylan", max_results=1
    )

    assert slots[0]["end"] - slots[0]["start"] == timedelta(
        minutes=config.SERVICES["decoloracion"]["duration_minutes"]
    )
    assert config.SERVICES["decoloracion"]["duration_minutes"] == 120


# ---------------------------------------------------------------------------
# 50-day safety cap
# ---------------------------------------------------------------------------

def test_search_stops_at_50_day_cap_when_never_available(monkeypatch):
    # rafa's whole working day is always booked -> no slot is ever found.
    call_log = []

    def fake_list_events(calendar_id, start, end):
        call_log.append(start)
        return [_event(start.date(), "00:00", "23:59", "10")]

    monkeypatch.setattr(ca, "list_events", fake_list_events)

    today = ca._now().date()
    expected_calls = sum(
        1
        for i in range(ca.SEARCH_DAY_CAP)
        if (today + timedelta(days=i)).weekday() != config.BARBERS["rafa"]["day_off"]
        and config.WORKING_HOURS[(today + timedelta(days=i)).weekday()] is not None
    )

    slots = ca.check_availability(service="corte", date=None, barber="rafa", max_results=3)

    assert slots == []
    assert len(call_log) == expected_calls


# ---------------------------------------------------------------------------
# Same-day minimum lead time (30 minutes from now)
# ---------------------------------------------------------------------------

def test_same_day_lead_time_pushes_first_slot_to_next_aligned_time(monkeypatch):
    _patch_events(monkeypatch, [])
    # now=10:25 -> now+30min=10:55 -> next :00/:30-aligned slot is 11:00.
    monkeypatch.setattr(ca, "_now", lambda: datetime.combine(MONDAY, time(10, 25), tzinfo=ca.TIMEZONE))

    slots = ca.check_availability(
        service="corte", date=MONDAY, barber="dylan", max_results=100
    )

    starts = [s["start"] for s in slots]
    assert starts[0] == datetime.combine(MONDAY, time(11, 0), tzinfo=ca.TIMEZONE)
    assert datetime.combine(MONDAY, time(10, 30), tzinfo=ca.TIMEZONE) not in starts


def test_lead_time_does_not_affect_future_days(monkeypatch):
    _patch_events(monkeypatch, [])
    future_day = MONDAY + timedelta(days=1)  # Tuesday; rafa is not off that day
    monkeypatch.setattr(ca, "_now", lambda: datetime.combine(MONDAY, time(10, 25), tzinfo=ca.TIMEZONE))

    slots = ca.check_availability(
        service="corte", date=future_day, barber="rafa", max_results=1
    )

    assert slots[0]["start"] == datetime.combine(future_day, time(10, 0), tzinfo=ca.TIMEZONE)


# ---------------------------------------------------------------------------
# Busy-interval boundaries with a foreign UTC offset are normalized to Madrid
# ---------------------------------------------------------------------------

def test_foreign_offset_event_boundary_normalized_to_madrid(monkeypatch):
    # Regression test: the real Calendar API returns dateTime in the
    # account's own default timezone (observed: America/New_York, -04:00)
    # regardless of the timeZone field sent on insert. A slot immediately
    # following such an event inherits its cursor in _free_gaps, so without
    # normalization the returned "start" would carry the foreign offset
    # instead of Europe/Madrid -- correct in absolute instant, but wrong if
    # ever formatted with strftime/.hour for the customer.
    events = [_event_foreign_offset(MONDAY, "12:00", "13:00", "9")]  # dylan, Madrid 12:00-13:00
    _patch_events(monkeypatch, events)

    slots = ca.check_availability(
        service="corte", date=MONDAY, barber="dylan", max_results=100
    )

    next_slot = next(
        s for s in slots
        if s["start"] == datetime.combine(MONDAY, time(13, 0), tzinfo=ca.TIMEZONE)
    )
    assert next_slot["start"].tzinfo == ca.TIMEZONE
    assert next_slot["start"].utcoffset() == timedelta(hours=2)
    assert next_slot["start"].hour == 13  # not 07/09, which the foreign offset would show
