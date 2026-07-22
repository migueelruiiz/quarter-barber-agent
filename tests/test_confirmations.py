"""
Unit tests for src/agent/confirmations.py.

These are pure functions (no I/O, no model calls) -- tests call them
directly with fixed inputs, no monkeypatching needed.
"""

import inspect
from datetime import datetime

import config
from src.agent.confirmations import (
    render_booking_confirmation,
    render_reschedule_confirmation,
)

MADRID = config.TIMEZONE


# ---------------------------------------------------------------------------
# render_booking_confirmation
# ---------------------------------------------------------------------------

def test_booking_confirmation_uses_correct_weekday_for_known_date():
    # 2026-07-17 is a confirmed Friday -- regression test for the weekday
    # bug in docs/loop_confirmation_bugs_findings.md (Bug 2), where the
    # model stated "domingo" for this same date.
    start = datetime(2026, 7, 17, 11, 0, tzinfo=MADRID)

    text = render_booking_confirmation(
        client_name="Felipe",
        service="barba",
        start=start,
        color_id="9",
        client_phone="658553891",
    )

    assert "17 de julio de 2026 (viernes)" in text
    assert "11:00" in text


def test_booking_confirmation_formats_service_and_barber_for_two_services():
    start = datetime(2026, 7, 20, 10, 30, tzinfo=MADRID)

    barba = render_booking_confirmation(
        client_name="Ana",
        service="barba",
        start=start,
        color_id="9",  # dylan
        client_phone="600111222",
    )
    assert "Servicio: Barba" in barba
    assert "Barbero: Dylan" in barba

    corte_barba = render_booking_confirmation(
        client_name="Ana",
        service="corte_barba",
        start=start,
        color_id="6",  # yuri
        client_phone="600111222",
    )
    assert "Servicio: Corte Barba" in corte_barba
    assert "Barbero: Yuri" in corte_barba


def test_booking_confirmation_never_includes_price():
    text = render_booking_confirmation(
        client_name="Felipe",
        service="barba",
        start=datetime(2026, 7, 17, 11, 0, tzinfo=MADRID),
        color_id="9",
        client_phone="658553891",
    )

    # Deliberate: `service` here is a coarse SERVICES category with no
    # uniform price across its PRICE_MENU variants -- see module docstring
    # in src/agent/confirmations.py. Price must never be rendered here.
    assert "€" not in text
    assert "Precio" not in text


def test_booking_confirmation_never_includes_end_time_or_internal_ids():
    # client_phone deliberately has no "9" digit, so the raw color_id
    # value ("9") can be checked for leakage without colliding with an
    # unrelated digit in the phone number display.
    text = render_booking_confirmation(
        client_name="Felipe",
        service="barba",
        start=datetime(2026, 7, 17, 11, 0, tzinfo=MADRID),
        color_id="9",
        client_phone="600111222",
    )

    # Structural guarantee: the function signature has no way to receive
    # an end time or an event_id at all.
    params = inspect.signature(render_booking_confirmation).parameters
    assert "end" not in params
    assert "event_id" not in params

    assert "11:30" not in text  # would-be end time for a 30-minute service
    for leaked in ("event_id", "eventId", "color_id", "colorId", "9"):
        assert leaked not in text


def test_booking_confirmation_juan_null_color_id_renders_without_crashing():
    text = render_booking_confirmation(
        client_name="Felipe",
        service="corte",
        start=datetime(2026, 7, 17, 11, 0, tzinfo=MADRID),
        color_id=None,
        client_phone="658553891",
    )

    assert "Barbero: Juan" in text
    assert "None" not in text


def test_booking_confirmation_normalizes_phone_display():
    text = render_booking_confirmation(
        client_name="Felipe",
        service="corte",
        start=datetime(2026, 7, 17, 11, 0, tzinfo=MADRID),
        color_id=None,
        client_phone="+34658553891",
    )

    assert "+34" not in text
    assert "658 553 891" in text


# ---------------------------------------------------------------------------
# render_reschedule_confirmation
# ---------------------------------------------------------------------------

def test_reschedule_confirmation_uses_correct_weekday_for_known_date():
    # 2026-07-21 is a confirmed Tuesday -- regression test for the
    # self-contradictory "martes ... (miércoles)" bug in
    # docs/loop_confirmation_bugs_findings.md (Bug 2).
    new_start = datetime(2026, 7, 21, 10, 30, tzinfo=MADRID)

    text = render_reschedule_confirmation(
        new_start=new_start,
        color_id="6",  # yuri
        client_phone="658553891",
    )

    assert "21 de julio de 2026 (martes)" in text
    assert "Barbero: Yuri" in text


def test_reschedule_confirmation_formats_barber_for_two_barbers():
    new_start = datetime(2026, 7, 22, 15, 0, tzinfo=MADRID)

    rafa = render_reschedule_confirmation(
        new_start=new_start, color_id="10", client_phone="600111222"
    )
    assert "Barbero: Rafa" in rafa

    juan = render_reschedule_confirmation(
        new_start=new_start, color_id=None, client_phone="600111222"
    )
    assert "Barbero: Juan" in juan
    assert "None" not in juan


def test_reschedule_confirmation_never_includes_price_service_or_name():
    # RescheduleAppointmentArgs carries neither client_name nor service --
    # see docs/reschedule_appointment_spec.md and the module docstring in
    # src/agent/confirmations.py. Structural guarantee: the function
    # signature has no parameter for any of them.
    params = inspect.signature(render_reschedule_confirmation).parameters
    assert "client_name" not in params
    assert "service" not in params
    assert "price" not in params

    text = render_reschedule_confirmation(
        new_start=datetime(2026, 7, 21, 10, 30, tzinfo=MADRID),
        color_id="6",
        client_phone="658553891",
    )
    assert "€" not in text


def test_reschedule_confirmation_never_includes_end_time_or_internal_ids():
    text = render_reschedule_confirmation(
        new_start=datetime(2026, 7, 21, 10, 30, tzinfo=MADRID),
        color_id="6",
        client_phone="658553891",
    )

    params = inspect.signature(render_reschedule_confirmation).parameters
    assert "end" not in params
    assert "event_id" not in params

    assert "11:00" not in text  # would-be end time for a 30-minute service
    for leaked in ("event_id", "eventId", "color_id", "colorId"):
        assert leaked not in text
