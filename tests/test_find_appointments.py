"""
Unit tests for src/tools/find_appointments.py.

`list_events` (the only I/O boundary -- real Google Calendar API calls) is
monkeypatched in every test so these run offline and deterministically.
"""

from datetime import date, datetime, time, timedelta

import pytest

from src.tools import find_appointments as fa

MONDAY = date(2026, 8, 3)


def _event(day, start_hhmm, end_hhmm, color_id, summary, event_id="evt"):
    def _dt(hhmm):
        hour, minute = hhmm.split(":")
        return datetime.combine(day, time(int(hour), int(minute)), tzinfo=fa.TIMEZONE)

    return {
        "id": event_id,
        "summary": summary,
        "start": {"dateTime": _dt(start_hhmm).isoformat()},
        "end": {"dateTime": _dt(end_hhmm).isoformat()},
        "colorId": color_id,
    }


def _patch_events(monkeypatch, events):
    calls = []

    def fake_list_events(calendar_id, start, end):
        calls.append((calendar_id, start, end))
        return events

    monkeypatch.setattr(fa, "list_events", fake_list_events)
    return calls


# ---------------------------------------------------------------------------
# Phone match
# ---------------------------------------------------------------------------

def test_phone_match_against_agent_created_event(monkeypatch):
    events = [_event(MONDAY, "10:00", "10:30", "9", "Juan Perez - +34 600 111 222", "evt-1")]
    _patch_events(monkeypatch, events)

    results = fa.find_appointments(client_phone="600111222", client_name="", date=MONDAY)

    assert len(results) == 1
    assert results[0]["event_id"] == "evt-1"
    assert results[0]["barber"] == "dylan"  # colorId "9"


# ---------------------------------------------------------------------------
# Name match against free-text, barber-annotated summary
# ---------------------------------------------------------------------------

def test_name_token_match_against_free_text_summary(monkeypatch):
    events = [_event(MONDAY, "17:00", "17:30", "6", "Juanito corte 17h", "evt-2")]
    _patch_events(monkeypatch, events)

    results = fa.find_appointments(client_phone="", client_name="Juan Pérez", date=MONDAY)

    assert len(results) == 1
    assert results[0]["event_id"] == "evt-2"


def test_free_text_summary_matches_only_by_name_not_phone(monkeypatch):
    events = [_event(MONDAY, "17:00", "17:30", "6", "Juanito corte 17h", "evt-3")]
    _patch_events(monkeypatch, events)

    # client_phone deliberately doesn't appear anywhere in the summary --
    # only the name token match should surface this event.
    results = fa.find_appointments(client_phone="699999999", client_name="Juan Pérez", date=MONDAY)

    assert len(results) == 1
    assert results[0]["event_id"] == "evt-3"


def test_accented_client_name_matches_unaccented_summary(monkeypatch):
    events = [_event(MONDAY, "10:00", "10:30", "9", "Cliente Ramirez cita", "evt-4")]
    _patch_events(monkeypatch, events)

    results = fa.find_appointments(client_phone="", client_name="Ramírez", date=MONDAY)

    assert len(results) == 1
    assert results[0]["event_id"] == "evt-4"


def test_short_name_tokens_do_not_cause_false_positive(monkeypatch):
    events = [_event(MONDAY, "10:00", "10:30", "9", "Al pelo barberia cita", "evt-5")]
    _patch_events(monkeypatch, events)

    # "Al" normalizes to a 2-char token, below the >=3 threshold -- must not
    # match even though "al" is literally a substring of the summary.
    results = fa.find_appointments(client_phone="", client_name="Al", date=MONDAY)

    assert results == []


# ---------------------------------------------------------------------------
# No match -> empty list, never an error
# ---------------------------------------------------------------------------

def test_no_match_returns_empty_list(monkeypatch):
    events = [_event(MONDAY, "10:00", "10:30", "9", "Maria Lopez - 611222333", "evt-6")]
    _patch_events(monkeypatch, events)

    results = fa.find_appointments(client_phone="699000000", client_name="Pedro", date=MONDAY)

    assert results == []


# ---------------------------------------------------------------------------
# date filter narrows the search window to a single day
# ---------------------------------------------------------------------------

def test_date_filter_narrows_window_to_single_day(monkeypatch):
    calls = _patch_events(monkeypatch, [])

    fa.find_appointments(client_phone="600111222", client_name="", date=MONDAY)

    assert len(calls) == 1
    _, start, end = calls[0]
    assert start == datetime.combine(MONDAY, time.min, tzinfo=fa.TIMEZONE)
    assert end == datetime.combine(MONDAY, time.max, tzinfo=fa.TIMEZONE)


# ---------------------------------------------------------------------------
# Deduplication when both phone and name independently match
# ---------------------------------------------------------------------------

def test_dedup_when_phone_and_name_both_match_same_event(monkeypatch):
    events = [_event(MONDAY, "10:00", "10:30", "9", "Juan Perez - 600111222", "evt-7")]
    _patch_events(monkeypatch, events)

    results = fa.find_appointments(client_phone="600111222", client_name="Juan Perez", date=MONDAY)

    assert len(results) == 1
    assert results[0]["event_id"] == "evt-7"


# ---------------------------------------------------------------------------
# 90-day forward window (date=None)
# ---------------------------------------------------------------------------

def test_events_outside_90_day_window_excluded_by_boundary(monkeypatch):
    fixed_now = datetime(2026, 8, 3, 9, 0, tzinfo=fa.TIMEZONE)
    monkeypatch.setattr(fa, "_now", lambda: fixed_now)

    inside = _event(
        fixed_now.date() + timedelta(days=89), "10:00", "10:30", "9",
        "Ana Ruiz - 600111222", "evt-inside",
    )
    outside = _event(
        fixed_now.date() + timedelta(days=91), "10:00", "10:30", "9",
        "Ana Ruiz - 600111222", "evt-outside",
    )

    calls = []

    def fake_list_events(calendar_id, start, end):
        calls.append((calendar_id, start, end))
        return [
            e for e in (inside, outside)
            if start <= datetime.fromisoformat(e["start"]["dateTime"]) < end
        ]

    monkeypatch.setattr(fa, "list_events", fake_list_events)

    results = fa.find_appointments(client_phone="600111222", client_name="", date=None)

    assert [r["event_id"] for r in results] == ["evt-inside"]
    _, window_start, window_end = calls[0]
    assert window_start == fixed_now
    assert window_end == fixed_now + timedelta(days=90)


# ---------------------------------------------------------------------------
# colorId with no matching barber -> barber: None, event still included
# ---------------------------------------------------------------------------

def test_unknown_color_id_returns_barber_none(monkeypatch):
    events = [_event(MONDAY, "10:00", "10:30", "99", "Carlos Ruiz - 600111222", "evt-8")]
    _patch_events(monkeypatch, events)

    results = fa.find_appointments(client_phone="600111222", client_name="", date=MONDAY)

    assert len(results) == 1
    assert results[0]["barber"] is None


# ---------------------------------------------------------------------------
# ValueError when both client_phone and client_name are empty
# ---------------------------------------------------------------------------

def test_value_error_when_both_phone_and_name_empty():
    with pytest.raises(ValueError):
        fa.find_appointments(client_phone="", client_name="")
