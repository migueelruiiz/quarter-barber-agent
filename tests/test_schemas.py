"""
Unit tests for src/agent/schemas.py.

No I/O boundary here -- these are pure regression tests against config.py
(similar in spirit to tests/test_config.py) plus structural checks on the
Groq tool-schema conversion.
"""

import typing

import pytest
from pydantic import ValidationError

import config
from src.agent import schemas


# ---------------------------------------------------------------------------
# ServiceName / BarberName built dynamically from config.py
# ---------------------------------------------------------------------------

def test_service_name_literal_matches_config_services_keys_exactly():
    values = typing.get_args(schemas.ServiceName)
    assert set(values) == set(config.SERVICES.keys())


def test_barber_name_literal_matches_config_barbers_keys_exactly():
    values = typing.get_args(schemas.BarberName)
    assert set(values) == set(config.BARBERS.keys())


def test_check_availability_args_rejects_unknown_service():
    with pytest.raises(ValidationError):
        schemas.CheckAvailabilityArgs(service="not_a_real_service")


def test_check_availability_args_rejects_unknown_barber():
    with pytest.raises(ValidationError):
        schemas.CheckAvailabilityArgs(service="corte", barber="not_a_real_barber")


def test_check_availability_args_valid_service_uses_defaults():
    args = schemas.CheckAvailabilityArgs(service="corte")
    assert args.date is None
    assert args.time_of_day is None
    assert args.barber is None
    assert args.max_results == 3


# ---------------------------------------------------------------------------
# Field sets mirror each tool's exact parameter signature
# ---------------------------------------------------------------------------

def test_book_appointment_args_mirrors_tool_signature_fields_minus_client_phone():
    # client_phone is deliberately excluded from the model-facing schema --
    # it's injected server-side from session_id at dispatch time, never
    # supplied or guessed by the model. See src/agent/loop.py:dispatch_tool.
    assert set(schemas.BookAppointmentArgs.model_fields) == {
        "service", "start", "color_id", "client_name",
    }


def test_find_appointments_args_mirrors_tool_signature_fields_minus_client_phone():
    assert set(schemas.FindAppointmentsArgs.model_fields) == {
        "client_name", "date",
    }


def test_reschedule_appointment_args_mirrors_tool_signature_fields():
    assert set(schemas.RescheduleAppointmentArgs.model_fields) == {
        "event_id", "new_start", "duration_minutes", "color_id",
    }


def test_cancel_appointment_args_mirrors_tool_signature_fields():
    assert set(schemas.CancelAppointmentArgs.model_fields) == {"event_id"}


def test_find_appointments_args_requires_client_name_field():
    # client_name is still required (mirroring the tool's positional
    # signature minus client_phone), but the tool itself allows it to be an
    # empty string -- client_phone (always non-empty, injected server-side)
    # is enough on its own to satisfy the tool's "at least one" requirement.
    with pytest.raises(ValidationError):
        schemas.FindAppointmentsArgs()

    args = schemas.FindAppointmentsArgs(client_name="")
    assert args.client_name == ""


# ---------------------------------------------------------------------------
# event_id dependency encoded in the description, not in code (spec.md)
# ---------------------------------------------------------------------------

def test_cancel_appointment_event_id_description_references_find_appointments():
    description = schemas.CancelAppointmentArgs.model_fields["event_id"].description
    assert "find_appointments" in description


def test_reschedule_appointment_event_id_description_references_find_appointments():
    description = schemas.RescheduleAppointmentArgs.model_fields["event_id"].description
    assert "find_appointments" in description


# ---------------------------------------------------------------------------
# Groq tool-schema conversion
# ---------------------------------------------------------------------------

def test_tool_schemas_cover_all_five_tools_exactly_once():
    names = [entry["function"]["name"] for entry in schemas.TOOL_SCHEMAS]
    assert sorted(names) == sorted([
        "check_availability",
        "book_appointment",
        "find_appointments",
        "cancel_appointment",
        "reschedule_appointment",
    ])
    assert len(names) == len(set(names))


@pytest.mark.parametrize("entry", schemas.TOOL_SCHEMAS, ids=lambda e: e["function"]["name"])
def test_each_tool_schema_has_valid_groq_function_envelope(entry):
    assert entry["type"] == "function"
    function = entry["function"]
    assert isinstance(function["name"], str) and function["name"]
    assert isinstance(function["description"], str) and function["description"]

    parameters = function["parameters"]
    assert parameters["type"] == "object"
    assert "properties" in parameters
