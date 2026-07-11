"""
Unit tests for src/tools/reschedule_appointment.py.

check_slot_available and patch_event (the only I/O boundaries -- real
Google Calendar API calls) are monkeypatched in every test so these run
offline and deterministically.
"""

from datetime import datetime, timedelta

import httplib2
import pytest
from googleapiclient.errors import HttpError

import config
from src.tools import reschedule_appointment as ra

NEW_START = datetime(2026, 8, 3, 10, 30, tzinfo=config.TIMEZONE)


def _http_error(status: int) -> HttpError:
    resp = httplib2.Response({"status": status})
    error = HttpError(resp, b"error body", uri=None)
    assert error.resp.status == status
    return error


def _patch_slot_available(monkeypatch, available: bool):
    calls = []

    def fake_check_slot_available(
        calendar_id, color_id, start, end, exclude_event_id=None
    ):
        calls.append((calendar_id, color_id, start, end, exclude_event_id))
        return available

    monkeypatch.setattr(ra, "check_slot_available", fake_check_slot_available)
    return calls


def _patch_patch_event(monkeypatch, side_effect=None, event_id="evt-123", status="confirmed"):
    calls = []

    def fake_patch_event(calendar_id, event_id_, color_id, start, end):
        calls.append((calendar_id, event_id_, color_id, start, end))
        if side_effect is not None:
            raise side_effect
        return {"id": event_id, "status": status}

    monkeypatch.setattr(ra, "patch_event", fake_patch_event)
    return calls


def test_successful_reschedule_returns_expected_dict_shape(monkeypatch):
    _patch_slot_available(monkeypatch, True)
    patch_calls = _patch_patch_event(monkeypatch)

    result = ra.reschedule_appointment(
        event_id="evt-123",
        new_start=NEW_START,
        duration_minutes=30,
        color_id="9",
    )

    assert result == {
        "success": True,
        "event_id": "evt-123",
        "start": NEW_START,
        "end": NEW_START + timedelta(minutes=30),
    }
    assert len(patch_calls) == 1


def test_patch_event_called_once_with_correct_arguments(monkeypatch):
    _patch_slot_available(monkeypatch, True)
    patch_calls = _patch_patch_event(monkeypatch)

    ra.reschedule_appointment(
        event_id="evt-123",
        new_start=NEW_START,
        duration_minutes=30,
        color_id="9",
    )

    assert len(patch_calls) == 1
    calendar_id, event_id, color_id, start, end = patch_calls[0]
    assert calendar_id == ra.CALENDAR_ID
    assert event_id == "evt-123"
    assert color_id == "9"
    assert start == NEW_START
    assert end == NEW_START + timedelta(minutes=30)


def test_slot_taken_returns_failure_without_calling_patch_event(monkeypatch):
    _patch_slot_available(monkeypatch, False)
    patch_calls = _patch_patch_event(monkeypatch)

    result = ra.reschedule_appointment(
        event_id="evt-123",
        new_start=NEW_START,
        duration_minutes=30,
        color_id="9",
    )

    assert result == {"success": False, "reason": "slot_taken"}
    assert patch_calls == []


def test_self_overlap_excludes_own_event_id_from_availability_check(monkeypatch):
    slot_calls = _patch_slot_available(monkeypatch, True)
    _patch_patch_event(monkeypatch)

    ra.reschedule_appointment(
        event_id="evt-123",
        new_start=NEW_START,
        duration_minutes=60,
        color_id="9",
    )

    assert len(slot_calls) == 1
    _, _, _, _, exclude_event_id = slot_calls[0]
    assert exclude_event_id == "evt-123"


def test_404_on_patch_maps_to_not_found_without_raising(monkeypatch):
    _patch_slot_available(monkeypatch, True)
    _patch_patch_event(monkeypatch, side_effect=_http_error(404))

    result = ra.reschedule_appointment(
        event_id="evt-already-gone",
        new_start=NEW_START,
        duration_minutes=30,
        color_id="9",
    )

    assert result == {"success": False, "reason": "not_found"}


def test_410_on_patch_maps_to_not_found_without_raising(monkeypatch):
    _patch_slot_available(monkeypatch, True)
    _patch_patch_event(monkeypatch, side_effect=_http_error(410))

    result = ra.reschedule_appointment(
        event_id="evt-previously-deleted",
        new_start=NEW_START,
        duration_minutes=30,
        color_id="9",
    )

    assert result == {"success": False, "reason": "not_found"}


def test_cancelled_status_in_patch_response_maps_to_not_found(monkeypatch):
    _patch_slot_available(monkeypatch, True)
    patch_calls = _patch_patch_event(monkeypatch, status="cancelled")

    result = ra.reschedule_appointment(
        event_id="evt-already-cancelled",
        new_start=NEW_START,
        duration_minutes=30,
        color_id="9",
    )

    assert result == {"success": False, "reason": "not_found"}
    # distinct path from the HttpError 404/410 cases -- patch_event was
    # still called and returned normally, no exception raised.
    assert len(patch_calls) == 1


def test_other_http_error_on_patch_propagates_unhandled(monkeypatch):
    _patch_slot_available(monkeypatch, True)
    _patch_patch_event(monkeypatch, side_effect=_http_error(500))

    with pytest.raises(HttpError) as exc_info:
        ra.reschedule_appointment(
            event_id="evt-123",
            new_start=NEW_START,
            duration_minutes=30,
            color_id="9",
        )

    assert exc_info.value.resp.status == 500


def test_juan_null_color_id_passed_through_explicitly(monkeypatch):
    _patch_slot_available(monkeypatch, True)
    patch_calls = _patch_patch_event(monkeypatch)

    ra.reschedule_appointment(
        event_id="evt-123",
        new_start=NEW_START,
        duration_minutes=30,
        color_id=None,
    )

    _, _, color_id, _, _ = patch_calls[0]
    assert color_id is None


def test_duration_taken_as_is_independent_of_services(monkeypatch):
    _patch_slot_available(monkeypatch, True)
    patch_calls = _patch_patch_event(monkeypatch)

    # 45 minutes doesn't correspond to any single SERVICES duration -- this
    # is deliberate: duration_minutes must be used verbatim, never
    # re-derived from a service name.
    result = ra.reschedule_appointment(
        event_id="evt-123",
        new_start=NEW_START,
        duration_minutes=45,
        color_id="9",
    )

    assert result["end"] - result["start"] == timedelta(minutes=45)
    _, _, _, start, end = patch_calls[0]
    assert end - start == timedelta(minutes=45)
