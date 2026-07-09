"""
Unit tests for src/tools/cancel_appointment.py.

delete_event (the only I/O boundary -- real Google Calendar API calls) is
monkeypatched in every test so these run offline and deterministically.
"""

import httplib2
import pytest
from googleapiclient.errors import HttpError

from src.tools import cancel_appointment as ca


def _http_error(status: int) -> HttpError:
    resp = httplib2.Response({"status": status})
    error = HttpError(resp, b"error body", uri=None)
    assert error.resp.status == status
    return error


def _patch_delete_event(monkeypatch, side_effect=None):
    calls = []

    def fake_delete_event(calendar_id, event_id):
        calls.append((calendar_id, event_id))
        if side_effect is not None:
            raise side_effect

    monkeypatch.setattr(ca, "delete_event", fake_delete_event)
    return calls


def test_successful_cancellation_returns_success_true(monkeypatch):
    calls = _patch_delete_event(monkeypatch)

    result = ca.cancel_appointment(event_id="evt-123")

    assert result == {"success": True}
    assert calls == [(ca.CALENDAR_ID, "evt-123")]


def test_404_maps_to_not_found_without_raising(monkeypatch):
    _patch_delete_event(monkeypatch, side_effect=_http_error(404))

    result = ca.cancel_appointment(event_id="evt-already-gone")

    assert result == {"success": False, "reason": "not_found"}


def test_410_maps_to_not_found_without_raising(monkeypatch):
    _patch_delete_event(monkeypatch, side_effect=_http_error(410))

    result = ca.cancel_appointment(event_id="evt-previously-deleted")

    assert result == {"success": False, "reason": "not_found"}


def test_other_http_error_propagates_unhandled(monkeypatch):
    _patch_delete_event(monkeypatch, side_effect=_http_error(500))

    with pytest.raises(HttpError) as exc_info:
        ca.cancel_appointment(event_id="evt-123")

    assert exc_info.value.resp.status == 500
