"""
Unit tests for src/tools/book_appointment.py.

check_slot_available and create_event (the only I/O boundaries — real
Google Calendar API calls) are monkeypatched in every test so these run
offline and deterministically.
"""

from datetime import datetime, timedelta

import pytest

import config
from src.tools import book_appointment as ba

START = datetime(2026, 8, 3, 10, 0, tzinfo=config.TIMEZONE)


def _patch_slot_available(monkeypatch, available: bool):
    calls = []

    def fake_check_slot_available(calendar_id, color_id, start, end):
        calls.append((calendar_id, color_id, start, end))
        return available

    monkeypatch.setattr(ba, "check_slot_available", fake_check_slot_available)
    return calls


def _patch_create_event(monkeypatch, event_id="evt-123"):
    calls = []

    def fake_create_event(calendar_id, color_id, summary, start, end):
        calls.append((calendar_id, color_id, summary, start, end))
        return {"id": event_id}

    monkeypatch.setattr(ba, "create_event", fake_create_event)
    return calls


def test_successful_booking_returns_expected_dict_shape(monkeypatch):
    _patch_slot_available(monkeypatch, True)
    create_calls = _patch_create_event(monkeypatch, event_id="evt-123")

    result = ba.book_appointment(
        service="corte",
        start=START,
        color_id="9",
        client_name="Juan Perez",
        client_phone="+34600000000",
    )

    assert result == {
        "success": True,
        "event_id": "evt-123",
        "start": START,
        "end": START + timedelta(minutes=30),
    }
    assert len(create_calls) == 1


def test_slot_taken_returns_failure_without_calling_create_event(monkeypatch):
    _patch_slot_available(monkeypatch, False)
    create_calls = _patch_create_event(monkeypatch)

    result = ba.book_appointment(
        service="corte",
        start=START,
        color_id="9",
        client_name="Juan Perez",
        client_phone="+34600000000",
    )

    assert result == {"success": False, "reason": "slot_taken"}
    assert create_calls == []


def test_event_summary_is_name_dash_phone(monkeypatch):
    _patch_slot_available(monkeypatch, True)
    create_calls = _patch_create_event(monkeypatch)

    ba.book_appointment(
        service="corte",
        start=START,
        color_id="9",
        client_name="Juan Perez",
        client_phone="+34600000000",
    )

    _, _, summary, _, _ = create_calls[0]
    assert summary == "Juan Perez - 600000000"


@pytest.mark.parametrize(
    "client_phone",
    ["+34600111222", "34600111222", "600 111 222", "600111222"],
)
def test_spanish_phone_variants_normalize_to_same_summary(monkeypatch, client_phone):
    _patch_slot_available(monkeypatch, True)
    create_calls = _patch_create_event(monkeypatch)

    ba.book_appointment(
        service="corte",
        start=START,
        color_id="9",
        client_name="Juan Perez",
        client_phone=client_phone,
    )

    _, _, summary, _, _ = create_calls[0]
    assert summary == "Juan Perez - 600111222"


def test_non_spanish_shaped_phone_left_unmodified(monkeypatch):
    _patch_slot_available(monkeypatch, True)
    create_calls = _patch_create_event(monkeypatch)

    # US number: 11 digits but doesn't start with "34" -> not treated as
    # Spanish, no prefix is stripped.
    ba.book_appointment(
        service="corte",
        start=START,
        color_id="9",
        client_name="Juan Perez",
        client_phone="+1 415 555 0100",
    )

    _, _, summary, _, _ = create_calls[0]
    assert summary == "Juan Perez - 14155550100"


def test_end_time_derived_from_multi_duration_service(monkeypatch):
    _patch_slot_available(monkeypatch, True)
    create_calls = _patch_create_event(monkeypatch)

    result = ba.book_appointment(
        service="decoloracion_corte_barba",
        start=START,
        color_id="9",
        client_name="Juan Perez",
        client_phone="+34600000000",
    )

    expected_duration = timedelta(
        minutes=config.SERVICES["decoloracion_corte_barba"]["duration_minutes"]
    )
    assert expected_duration == timedelta(minutes=180)
    assert result["end"] - result["start"] == expected_duration

    _, _, _, _, end = create_calls[0]
    assert end == START + expected_duration


def test_unknown_service_fails_before_any_api_call(monkeypatch):
    slot_calls = _patch_slot_available(monkeypatch, True)
    create_calls = _patch_create_event(monkeypatch)

    with pytest.raises(ValueError):
        ba.book_appointment(
            service="not_a_real_service",
            start=START,
            color_id="9",
            client_name="Juan Perez",
            client_phone="+34600000000",
        )

    assert slot_calls == []
    assert create_calls == []


def test_juan_null_color_id_passed_through_explicitly(monkeypatch):
    _patch_slot_available(monkeypatch, True)
    create_calls = _patch_create_event(monkeypatch)

    ba.book_appointment(
        service="corte",
        start=START,
        color_id=None,
        client_name="Juan Perez",
        client_phone="+34600000000",
    )

    _, color_id, _, _, _ = create_calls[0]
    assert color_id is None
