"""
Pydantic tool schemas for the ReAct agent loop.

Implements docs/react_loop_spec.md ("Tool schemas"). One BaseModel per tool
in src/tools/*.py, each mirroring that tool's exact parameter signature.
These models are used both to generate the Groq-format tool schema (via
model_json_schema()) and to validate arguments the model returns before any
tool function is actually called -- see src/agent/loop.py:dispatch_tool.

ServiceName/BarberName are built dynamically from config.SERVICES/
config.BARBERS at import time, never hardcoded -- config.py is the single
source of truth for these closed sets (see config.py's own docstring), and
hardcoding them here would silently desync the moment a service or barber
is added or renamed.
"""

from datetime import date as date_cls
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from config import BARBERS, SERVICES

ServiceName = Literal[tuple(SERVICES.keys())]
BarberName = Literal[tuple(BARBERS.keys())]


class CheckAvailabilityArgs(BaseModel):
    service: ServiceName = Field(description="Service the client wants to book.")
    date: date_cls | None = Field(
        default=None,
        description="Specific calendar date to search. Omit to search forward "
        "from today across multiple days until max_results slots are found.",
    )
    time_of_day: Literal["morning", "afternoon"] | None = Field(
        default=None,
        description="Restrict results to morning or afternoon slots. Omit to "
        "consider the whole working day.",
    )
    barber: BarberName | None = Field(
        default=None,
        description="Restrict the search to a single barber. Omit to consider "
        "any barber eligible for the service, best-available-per-slot.",
    )
    max_results: int = Field(
        default=3,
        description="Maximum number of candidate slots to return, in chronological order.",
    )


class BookAppointmentArgs(BaseModel):
    service: ServiceName = Field(description="Service being booked.")
    start: datetime = Field(
        description="Start time of the slot to book, exactly as returned by a "
        "prior check_availability call. Must be ISO 8601 with a 'T' separator "
        "and UTC offset, e.g. 2026-07-15T10:00:00+02:00 -- do not use a space "
        "instead of 'T'."
    )
    color_id: str | None = Field(
        description="Barber's colorId for the chosen slot, exactly as returned "
        "by the prior check_availability call for this slot. Null for Juan -- "
        "never invent or substitute a literal color value."
    )
    client_name: str = Field(description="Client's full name, as given by the client.")


class FindAppointmentsArgs(BaseModel):
    client_name: str = Field(
        description="Client's name to search by. May be an empty string if the "
        "name isn't known -- the client's phone number is already available "
        "from the current session and is used automatically, so it is never "
        "a field you need to fill in."
    )
    date: date_cls | None = Field(
        default=None,
        description="Restrict the search to a single calendar date. Omit to "
        "search the next 90 days.",
    )


class CancelAppointmentArgs(BaseModel):
    event_id: str = Field(
        description="Identifier of the appointment to cancel. Must come from a "
        "prior find_appointments call in this conversation -- never invent or "
        "guess one."
    )


class RescheduleAppointmentArgs(BaseModel):
    event_id: str = Field(
        description="Identifier of the appointment to reschedule. Must come "
        "from a prior find_appointments call in this conversation -- never "
        "invent or guess one."
    )
    new_start: datetime = Field(
        description="New start time for the appointment. Must be ISO 8601 "
        "with a 'T' separator and UTC offset, e.g. 2026-07-15T10:00:00+02:00 "
        "-- do not use a space instead of 'T'."
    )
    duration_minutes: int = Field(
        description="Actual duration of the existing appointment (end minus "
        "start), from the event already returned by a prior find_appointments "
        "call -- never re-derived from a service name."
    )
    color_id: str | None = Field(
        description="colorId for the new slot, from a fresh check_availability "
        "call for the new date/time -- never inferred from the original "
        "event's colorId. Null for Juan."
    )


def _to_groq_tool_schema(name: str, description: str, model: type[BaseModel]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": model.model_json_schema(),
        },
    }


TOOL_SCHEMAS = [
    _to_groq_tool_schema(
        "check_availability",
        "Find free appointment slots for a service, optionally filtered by "
        "date, time of day, and barber. Read-only, no side effects.",
        CheckAvailabilityArgs,
    ),
    _to_groq_tool_schema(
        "book_appointment",
        "Book an appointment for a client at a specific slot and barber "
        "already confirmed via check_availability. Writes to the calendar.",
        BookAppointmentArgs,
    ),
    _to_groq_tool_schema(
        "find_appointments",
        "Locate a client's existing future appointment(s) by phone and/or "
        "name. Read-only, no side effects. Required before cancelling or "
        "rescheduling an appointment.",
        FindAppointmentsArgs,
    ),
    _to_groq_tool_schema(
        "cancel_appointment",
        "Cancel an existing appointment. Writes to the calendar.",
        CancelAppointmentArgs,
    ),
    _to_groq_tool_schema(
        "reschedule_appointment",
        "Move an existing appointment to a new start time and/or barber, "
        "preserving its original duration. Writes to the calendar.",
        RescheduleAppointmentArgs,
    ),
]
