"""
Unit tests for src/calendar/queries.py.

get_calendar_service (the only I/O boundary -- real Google Calendar API
calls) is monkeypatched with a fake service that records the request body
passed to service.events().patch(...), so these run offline and
deterministically.

Currently only covers patch_event's colorId body-shape regression (Fix 1 in
docs/reschedule_appointment_findings.md) -- other queries.py functions are
exercised indirectly via the tool-level tests (test_book_appointment.py,
test_check_availability.py, etc.), which is the established pattern in this
codebase.
"""

from datetime import datetime

import config
from src.calendar import queries

START = datetime(2026, 8, 3, 10, 0, tzinfo=config.TIMEZONE)
END = datetime(2026, 8, 3, 10, 30, tzinfo=config.TIMEZONE)


class _FakeEvents:
    def __init__(self, calls, response):
        self._calls = calls
        self._response = response

    def patch(self, calendarId, eventId, body):
        self._calls.append({"calendarId": calendarId, "eventId": eventId, "body": body})
        return self

    def execute(self):
        return self._response


class _FakeService:
    def __init__(self, calls, response):
        self._events = _FakeEvents(calls, response)

    def events(self):
        return self._events


def _patch_calendar_service(monkeypatch, response=None):
    calls = []
    if response is None:
        response = {"id": "evt-123", "status": "confirmed"}
    monkeypatch.setattr(
        queries, "get_calendar_service", lambda: _FakeService(calls, response)
    )
    return calls


def test_patch_event_body_includes_explicit_null_colorid_for_juan(monkeypatch):
    calls = _patch_calendar_service(monkeypatch)

    queries.patch_event("cal-id", "evt-123", None, START, END)

    assert len(calls) == 1
    body = calls[0]["body"]
    assert "colorId" in body
    assert body["colorId"] is None


def test_patch_event_body_includes_non_null_colorid(monkeypatch):
    calls = _patch_calendar_service(monkeypatch)

    queries.patch_event("cal-id", "evt-123", "9", START, END)

    assert len(calls) == 1
    body = calls[0]["body"]
    assert body["colorId"] == "9"


def test_patch_event_returns_raw_response(monkeypatch):
    _patch_calendar_service(
        monkeypatch, response={"id": "evt-123", "status": "cancelled"}
    )

    result = queries.patch_event("cal-id", "evt-123", "9", START, END)

    assert result == {"id": "evt-123", "status": "cancelled"}
