"""
Unit tests for src/agent/loop.py.

`_call_model` is the sole Groq I/O boundary and is monkeypatched in every
test, same convention as monkeypatching the Calendar API boundary in the
existing tool tests -- these never hit the real Groq API. Tool execution
is isolated by monkeypatching entries directly in TOOL_REGISTRY (rather
than the underlying Calendar-backed tool functions), so dispatch/loop
behavior is tested independently of any real tool's I/O.
"""

import json
from datetime import datetime, timedelta

import httpx
import pytest
from groq import APIConnectionError, BadRequestError, RateLimitError

import config
from src.agent import loop
from src.agent.schemas import CheckAvailabilityArgs


def _groq_api_error(cls, status_code: int, message: str):
    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    response = httpx.Response(status_code, request=request)
    return cls(message, response=response, body=None)


def _tool_use_failed_error(detail: str) -> BadRequestError:
    """Builds a BadRequestError shaped like Groq's real tool_use_failed
    response body -- {"error": {"code": "tool_use_failed", "message": ...,
    "failed_generation": ...}} -- confirmed via Groq's docs and real dev
    failures (see loop._tool_schema_validation_detail)."""
    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    response = httpx.Response(400, request=request)
    body = {
        "error": {
            "message": detail,
            "type": "invalid_request_error",
            "code": "tool_use_failed",
            "failed_generation": "<function=reschedule_appointment{...}>",
        }
    }
    return BadRequestError(f"Error code: 400 - {body}", response=response, body=body)


# ---------------------------------------------------------------------------
# Fake Groq response objects
# ---------------------------------------------------------------------------

class _FakeFunction:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _FakeToolCall:
    def __init__(self, call_id, name, arguments):
        self.id = call_id
        self.function = _FakeFunction(name, arguments)


class _FakeMessage:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _FakeChoice:
    def __init__(self, finish_reason, message):
        self.finish_reason = finish_reason
        self.message = message


class _FakeResponse:
    def __init__(self, finish_reason, message):
        self.choices = [_FakeChoice(finish_reason, message)]


def _tool_call_response(name, arguments, call_id="call-1"):
    return _FakeResponse(
        "tool_calls",
        _FakeMessage(content=None, tool_calls=[_FakeToolCall(call_id, name, arguments)]),
    )


def _final_response(content):
    return _FakeResponse("stop", _FakeMessage(content=content, tool_calls=None))


# ---------------------------------------------------------------------------
# dispatch_tool
# ---------------------------------------------------------------------------

def test_dispatch_tool_routes_to_correct_function_with_validated_arguments(monkeypatch):
    calls = []

    def fake_check_availability(**kwargs):
        calls.append(kwargs)
        return []

    monkeypatch.setitem(
        loop.TOOL_REGISTRY, "check_availability", (CheckAvailabilityArgs, fake_check_availability)
    )

    result = loop.dispatch_tool(
        "check_availability",
        json.dumps({"service": "corte", "barber": "dylan"}),
        client_phone="600111222",
    )

    assert result == []
    assert calls == [
        {
            "service": "corte",
            "date": None,
            "time_of_day": None,
            "barber": "dylan",
            "max_results": 3,
        }
    ]


def test_dispatch_tool_injects_client_phone_for_book_appointment(monkeypatch):
    calls = []

    def fake_book_appointment(**kwargs):
        calls.append(kwargs)
        return {"success": True}

    monkeypatch.setitem(
        loop.TOOL_REGISTRY, "book_appointment", (loop.BookAppointmentArgs, fake_book_appointment)
    )

    loop.dispatch_tool(
        "book_appointment",
        json.dumps(
            {
                "service": "corte",
                "start": "2026-07-15T10:00:00+02:00",
                "color_id": "9",
                "client_name": "Juan Perez",
            }
        ),
        client_phone="600111222",
    )

    assert calls[0]["client_phone"] == "600111222"


def test_dispatch_tool_injects_client_phone_for_find_appointments(monkeypatch):
    calls = []

    def fake_find_appointments(**kwargs):
        calls.append(kwargs)
        return []

    monkeypatch.setitem(
        loop.TOOL_REGISTRY, "find_appointments", (loop.FindAppointmentsArgs, fake_find_appointments)
    )

    loop.dispatch_tool(
        "find_appointments", json.dumps({"client_name": ""}), client_phone="600111222"
    )

    assert calls[0]["client_phone"] == "600111222"


def test_dispatch_tool_does_not_inject_client_phone_for_other_tools(monkeypatch):
    calls = []

    def fake_check_availability(**kwargs):
        calls.append(kwargs)
        return []

    monkeypatch.setitem(
        loop.TOOL_REGISTRY, "check_availability", (CheckAvailabilityArgs, fake_check_availability)
    )

    loop.dispatch_tool(
        "check_availability", json.dumps({"service": "corte"}), client_phone="600111222"
    )

    assert "client_phone" not in calls[0]


def test_dispatch_tool_returns_structured_error_on_invalid_json():
    result = loop.dispatch_tool("check_availability", "{not valid json", client_phone="600111222")

    assert result == {
        "success": False,
        "reason": "invalid_arguments",
        "detail": result["detail"],
    }


def test_dispatch_tool_returns_structured_error_on_schema_validation_failure():
    result = loop.dispatch_tool(
        "check_availability", json.dumps({"service": "not_a_real_service"}), client_phone="600111222"
    )

    assert result["success"] is False
    assert result["reason"] == "invalid_arguments"


def test_dispatch_tool_returns_structured_error_on_unknown_tool_name():
    result = loop.dispatch_tool("not_a_real_tool", json.dumps({}), client_phone="600111222")

    assert result == {
        "success": False,
        "reason": "unknown_tool",
        "detail": "No such tool: 'not_a_real_tool'",
    }


def test_dispatch_tool_catches_unexpected_exception_from_tool_function(monkeypatch):
    def fake_check_availability(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setitem(
        loop.TOOL_REGISTRY, "check_availability", (CheckAvailabilityArgs, fake_check_availability)
    )

    result = loop.dispatch_tool(
        "check_availability", json.dumps({"service": "corte"}), client_phone="600111222"
    )

    assert result == {"success": False, "reason": "tool_error", "detail": "boom"}


def test_dispatch_tool_never_raises_for_any_failure_mode(monkeypatch):
    def fake_check_availability(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setitem(
        loop.TOOL_REGISTRY, "check_availability", (CheckAvailabilityArgs, fake_check_availability)
    )

    # None of these should propagate an exception out of dispatch_tool.
    loop.dispatch_tool("not_a_real_tool", "{}", client_phone="600111222")
    loop.dispatch_tool("check_availability", "not json", client_phone="600111222")
    loop.dispatch_tool("check_availability", json.dumps({"service": "nope"}), client_phone="600111222")
    loop.dispatch_tool("check_availability", json.dumps({"service": "corte"}), client_phone="600111222")


# ---------------------------------------------------------------------------
# run_agent_turn
# ---------------------------------------------------------------------------

def test_run_agent_turn_completes_after_one_tool_call_and_final_answer(monkeypatch):
    monkeypatch.setattr(loop, "get_session", lambda session_id: [])
    saved = {}
    monkeypatch.setattr(
        loop, "save_session", lambda session_id, messages: saved.update(history=messages)
    )

    responses = [
        _tool_call_response("check_availability", json.dumps({"service": "corte"})),
        _final_response("Here are your options."),
    ]
    monkeypatch.setattr(loop, "_call_model", lambda messages: responses.pop(0))
    monkeypatch.setitem(
        loop.TOOL_REGISTRY, "check_availability", (CheckAvailabilityArgs, lambda **kwargs: [])
    )

    result = loop.run_agent_turn("session-1", "Quiero una cita")

    assert result == "Here are your options."
    assert saved["history"][-1] == {"role": "assistant", "content": "Here are your options."}
    assert responses == []


def test_run_agent_turn_returns_fallback_when_max_iterations_exhausted(monkeypatch):
    monkeypatch.setattr(loop, "get_session", lambda session_id: [])
    monkeypatch.setattr(loop, "save_session", lambda session_id, messages: None)

    call_count = {"n": 0}

    def always_tool_calls(messages):
        call_count["n"] += 1
        return _tool_call_response("check_availability", json.dumps({"service": "corte"}))

    monkeypatch.setattr(loop, "_call_model", always_tool_calls)
    monkeypatch.setitem(
        loop.TOOL_REGISTRY, "check_availability", (CheckAvailabilityArgs, lambda **kwargs: [])
    )

    result = loop.run_agent_turn("session-2", "Quiero una cita")

    assert result == loop.FALLBACK_MESSAGE
    assert call_count["n"] == loop.MAX_ITERATIONS


def test_run_agent_turn_stops_immediately_on_non_tool_call_response(monkeypatch):
    monkeypatch.setattr(loop, "get_session", lambda session_id: [])
    saved = {}
    monkeypatch.setattr(
        loop, "save_session", lambda session_id, messages: saved.update(history=messages)
    )

    calls = []

    def fake_call_model(messages):
        calls.append(messages)
        return _final_response("Hola, en que puedo ayudarte?")

    monkeypatch.setattr(loop, "_call_model", fake_call_model)

    result = loop.run_agent_turn("session-3", "Hola")

    assert result == "Hola, en que puedo ayudarte?"
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# run_agent_turn -- Groq API errors (groq.APIError and subclasses) must
# never crash the turn: same outcome as MAX_ITERATIONS exhaustion --
# session saved as-is, FALLBACK_MESSAGE returned, no unhandled exception.
# ---------------------------------------------------------------------------

def test_run_agent_turn_returns_fallback_on_bad_request_error(monkeypatch):
    # Mirrors the real failure: Groq rejects a malformed tool-call
    # generation (e.g. a non-ISO-8601 datetime) with a 400 before this code
    # ever sees a response.
    monkeypatch.setattr(loop, "get_session", lambda session_id: [])
    saved = {}
    monkeypatch.setattr(
        loop, "save_session", lambda session_id, messages: saved.update(history=messages)
    )

    def raise_bad_request(messages):
        raise _groq_api_error(BadRequestError, 400, "tool_use_failed")

    monkeypatch.setattr(loop, "_call_model", raise_bad_request)

    result = loop.run_agent_turn("session-4", "Ponme una cita a las 18:30")

    assert result == loop.FALLBACK_MESSAGE
    # The customer's message must not be lost.
    assert saved["history"][-1] == {"role": "user", "content": "Ponme una cita a las 18:30"}


def test_run_agent_turn_returns_fallback_on_rate_limit_error(monkeypatch):
    monkeypatch.setattr(loop, "get_session", lambda session_id: [])
    monkeypatch.setattr(loop, "save_session", lambda session_id, messages: None)

    def raise_rate_limit(messages):
        raise _groq_api_error(RateLimitError, 429, "rate limit exceeded")

    monkeypatch.setattr(loop, "_call_model", raise_rate_limit)

    result = loop.run_agent_turn("session-5", "Hola")

    assert result == loop.FALLBACK_MESSAGE


def test_run_agent_turn_returns_fallback_on_generic_api_error(monkeypatch):
    # Any groq.APIError subclass must be handled the same way -- the catch
    # is on the base class, not an enumerated list of specific errors.
    # APIConnectionError is a network-level APIError (no HTTP response at
    # all), deliberately different from the status-level errors above.
    monkeypatch.setattr(loop, "get_session", lambda session_id: [])
    monkeypatch.setattr(loop, "save_session", lambda session_id, messages: None)

    def raise_connection_error(messages):
        request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
        raise APIConnectionError(request=request)

    monkeypatch.setattr(loop, "_call_model", raise_connection_error)

    result = loop.run_agent_turn("session-6", "Hola")

    assert result == loop.FALLBACK_MESSAGE


# ---------------------------------------------------------------------------
# run_agent_turn -- single retry on tool-call schema validation failure
# ---------------------------------------------------------------------------

def test_run_agent_turn_retries_once_on_tool_schema_validation_failure(monkeypatch):
    # Regression test: reschedule_appointment failed Groq's own schema
    # validation (missing color_id) -- previously this abandoned the turn
    # with FALLBACK_MESSAGE even though the fix is usually just "ask the
    # model to retry with corrected arguments."
    monkeypatch.setattr(loop, "get_session", lambda session_id: [])
    saved = {}
    monkeypatch.setattr(
        loop, "save_session", lambda session_id, messages: saved.update(history=messages)
    )

    calls = []

    def fake_call_model(messages):
        calls.append(messages)
        if len(calls) == 1:
            raise _tool_use_failed_error("missing properties: 'color_id'")
        return _final_response("Tu cita ha sido reprogramada correctamente.")

    monkeypatch.setattr(loop, "_call_model", fake_call_model)

    result = loop.run_agent_turn("session-9", "Cambia mi cita a las 18:00")

    assert result == "Tu cita ha sido reprogramada correctamente."
    assert len(calls) == 2

    # The retry-prompt message, including the raw validation detail so the
    # model can see exactly what was wrong, must reach the model on retry.
    retry_messages = calls[1]
    assert any(
        "missing properties: 'color_id'" in (m.get("content") or "") for m in retry_messages
    )
    # The retry is the same request plus the retry-prompt appended -- not a
    # fresh, unrelated request.
    assert calls[0] == retry_messages[: len(calls[0])]


def test_run_agent_turn_returns_fallback_when_retry_also_fails_schema_validation(monkeypatch):
    monkeypatch.setattr(loop, "get_session", lambda session_id: [])
    saved = {}
    monkeypatch.setattr(
        loop, "save_session", lambda session_id, messages: saved.update(history=messages)
    )

    calls = []

    def fake_call_model(messages):
        calls.append(messages)
        raise _tool_use_failed_error("missing properties: 'color_id'")

    monkeypatch.setattr(loop, "_call_model", fake_call_model)

    result = loop.run_agent_turn("session-10", "Cambia mi cita a las 18:00")

    assert result == loop.FALLBACK_MESSAGE
    # Exactly one retry -- not an infinite loop, and not counted as fresh
    # MAX_ITERATIONS iterations (which would have allowed many more calls).
    assert len(calls) == 2


def test_run_agent_turn_does_not_retry_non_schema_bad_request_error(monkeypatch):
    # A 400 that isn't shaped like Groq's tool_use_failed body (e.g. no body
    # at all) must fall straight through to the existing generic-APIError
    # fallback path, with no retry attempted.
    monkeypatch.setattr(loop, "get_session", lambda session_id: [])
    monkeypatch.setattr(loop, "save_session", lambda session_id, messages: None)

    calls = []

    def fake_call_model(messages):
        calls.append(messages)
        raise _groq_api_error(BadRequestError, 400, "some other bad request")

    monkeypatch.setattr(loop, "_call_model", fake_call_model)

    result = loop.run_agent_turn("session-11", "Hola")

    assert result == loop.FALLBACK_MESSAGE
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# run_agent_turn -- degenerate output safety net
# ---------------------------------------------------------------------------

def test_run_agent_turn_returns_fallback_on_degenerate_output(monkeypatch):
    # Regression test: a real cancellation flow completed correctly, but the
    # confirmation reply generated immediately after degenerated into
    # hundreds of lines of repeated punctuation/whitespace/short fragments.
    monkeypatch.setattr(loop, "get_session", lambda session_id: [])
    saved = {}
    monkeypatch.setattr(
        loop, "save_session", lambda session_id, messages: saved.update(history=messages)
    )

    degenerate = "... ** Sorry Oops! ... ** ... \n\n" * 100
    monkeypatch.setattr(loop, "_call_model", lambda messages: _final_response(degenerate))

    result = loop.run_agent_turn("session-7", "Cancela mi cita")

    assert result == loop.FALLBACK_MESSAGE
    # The degenerate content must never be committed to conversation history.
    assert not any(m.get("content") == degenerate for m in saved["history"])


def test_run_agent_turn_does_not_flag_coherent_booking_confirmation(monkeypatch):
    # A legitimately long, coherent reply (full booking confirmation with
    # several fields) must not be falsely flagged as degenerate.
    monkeypatch.setattr(loop, "get_session", lambda session_id: [])
    saved = {}
    monkeypatch.setattr(
        loop, "save_session", lambda session_id, messages: saved.update(history=messages)
    )

    confirmation = (
        "¡Perfecto! Tu cita ha sido confirmada con estos detalles:\n"
        "- Servicio: Corte de pelo y arreglo de barba\n"
        "- Barbero: Dylan\n"
        "- Fecha: jueves, 16 de julio de 2026\n"
        "- Hora: 18:00\n"
        "- Precio: 25 euros\n\n"
        "Te esperamos en Calle Abtao Nº 4, Madrid. Si necesitas cambiar o "
        "cancelar tu cita, no dudes en escribirnos por este mismo medio. "
        "¡Gracias por confiar en Quarter Barber, Gentleman's!"
    )
    monkeypatch.setattr(loop, "_call_model", lambda messages: _final_response(confirmation))

    result = loop.run_agent_turn("session-8", "Confirma mi cita")

    assert result == confirmation
    assert saved["history"][-1] == {"role": "assistant", "content": confirmation}


# ---------------------------------------------------------------------------
# run_agent_turn -- templated confirmation bypass
# (docs/loop_confirmation_bugs_findings.md, bugs 1-3)
# ---------------------------------------------------------------------------

def test_run_agent_turn_uses_template_for_successful_book_appointment(monkeypatch):
    # The model's own drafted text for this turn is deliberately corrupted
    # (mirrors the real bug transcript) to prove it's never used -- the
    # loop must short-circuit before ever making this second _call_model
    # call, not just override its output afterward.
    monkeypatch.setattr(loop, "get_session", lambda session_id: [])
    saved = {}
    monkeypatch.setattr(
        loop, "save_session", lambda session_id, messages: saved.update(history=messages)
    )

    start = datetime(2026, 7, 17, 11, 0, tzinfo=config.TIMEZONE)
    end = start + timedelta(minutes=30)
    corrupted = "​\xa0The answer is corrupted; need to correct.¡Esto no debería verse!"

    responses = [
        _tool_call_response(
            "book_appointment",
            json.dumps(
                {
                    "service": "barba",
                    "start": start.isoformat(),
                    "color_id": "9",
                    "client_name": "Felipe",
                }
            ),
        ),
        _final_response(corrupted),
    ]
    monkeypatch.setattr(loop, "_call_model", lambda messages: responses.pop(0))
    monkeypatch.setitem(
        loop.TOOL_REGISTRY,
        "book_appointment",
        (
            loop.BookAppointmentArgs,
            lambda **kwargs: {
                "success": True,
                "event_id": "evt123",
                "start": start,
                "end": end,
            },
        ),
    )

    result = loop.run_agent_turn("600111222", "Quiero arreglo de barba mañana")

    assert corrupted not in result
    assert "Servicio: Barba" in result
    assert "Barbero: Dylan" in result
    assert "17 de julio de 2026 (viernes)" in result
    assert "11:00" in result
    assert "600 111 222" in result
    # The second queued response (the corrupted one) must never be
    # consumed -- proves the model was never called for this turn at all.
    assert len(responses) == 1
    assert saved["history"][-1] == {"role": "assistant", "content": result}


def test_run_agent_turn_uses_template_for_successful_reschedule_appointment(monkeypatch):
    monkeypatch.setattr(loop, "get_session", lambda session_id: [])
    saved = {}
    monkeypatch.setattr(
        loop, "save_session", lambda session_id, messages: saved.update(history=messages)
    )

    new_start = datetime(2026, 7, 21, 10, 30, tzinfo=config.TIMEZONE)
    new_end = new_start + timedelta(minutes=30)
    corrupted = "martes 21 de julio de 2026 (miércoles) -- corrupted"

    responses = [
        _tool_call_response(
            "reschedule_appointment",
            json.dumps(
                {
                    "event_id": "evt123",
                    "new_start": new_start.isoformat(),
                    "duration_minutes": 30,
                    "color_id": "6",
                }
            ),
        ),
        _final_response(corrupted),
    ]
    monkeypatch.setattr(loop, "_call_model", lambda messages: responses.pop(0))
    monkeypatch.setitem(
        loop.TOOL_REGISTRY,
        "reschedule_appointment",
        (
            loop.RescheduleAppointmentArgs,
            lambda **kwargs: {
                "success": True,
                "event_id": "evt123",
                "start": new_start,
                "end": new_end,
            },
        ),
    )

    result = loop.run_agent_turn("658553891", "Cambia mi cita al martes")

    assert corrupted not in result
    assert "Barbero: Yuri" in result
    assert "21 de julio de 2026 (martes)" in result
    assert len(responses) == 1
    assert saved["history"][-1] == {"role": "assistant", "content": result}


def test_run_agent_turn_does_not_template_on_slot_taken_failure(monkeypatch):
    # Only a *successful* book_appointment/reschedule_appointment bypasses
    # the model -- a failure (e.g. a race condition caught by R-7
    # re-verification) must still be handed back to the model to draft a
    # natural-language response, exactly as before this change.
    monkeypatch.setattr(loop, "get_session", lambda session_id: [])
    monkeypatch.setattr(loop, "save_session", lambda session_id, messages: None)

    responses = [
        _tool_call_response(
            "book_appointment",
            json.dumps(
                {
                    "service": "barba",
                    "start": "2026-07-17T11:00:00+02:00",
                    "color_id": "9",
                    "client_name": "Felipe",
                }
            ),
        ),
        _final_response("Lo siento, ese horario ya no está disponible."),
    ]
    monkeypatch.setattr(loop, "_call_model", lambda messages: responses.pop(0))
    monkeypatch.setitem(
        loop.TOOL_REGISTRY,
        "book_appointment",
        (loop.BookAppointmentArgs, lambda **kwargs: {"success": False, "reason": "slot_taken"}),
    )

    result = loop.run_agent_turn("600111222", "Quiero arreglo de barba mañana")

    assert result == "Lo siento, ese horario ya no está disponible."
    assert responses == []


def test_is_degenerate_output_flags_excessive_length():
    assert loop._is_degenerate_output("a" * (loop.MAX_CONTENT_LENGTH + 1)) is True


def test_is_degenerate_output_does_not_flag_none_or_empty():
    assert loop._is_degenerate_output(None) is False
    assert loop._is_degenerate_output("") is False


# ---------------------------------------------------------------------------
# build_system_prompt
# ---------------------------------------------------------------------------

def test_system_prompt_places_price_menu_before_date_time_line():
    prompt = loop.build_system_prompt(client_phone="600111222")

    price_index = prompt.index("Price menu")
    date_index = prompt.index("Current date and time")
    assert price_index < date_index


def test_system_prompt_recalculates_date_line_on_every_call(monkeypatch):
    real_now = datetime.now(config.TIMEZONE)

    class _FrozenDatetime(datetime):
        _now_value = real_now

        @classmethod
        def now(cls, tz=None):
            return cls._now_value

    monkeypatch.setattr(loop, "datetime", _FrozenDatetime)

    _FrozenDatetime._now_value = real_now
    first = loop.build_system_prompt(client_phone="600111222")

    _FrozenDatetime._now_value = real_now + timedelta(days=1)
    second = loop.build_system_prompt(client_phone="600111222")

    assert first != second
    # The static prefix (price menu + behavioral instructions) is unaffected.
    assert first.split("Current date and time")[0] == second.split("Current date and time")[0]


def test_date_lookup_table_has_correct_spanish_weekdays_for_known_date(monkeypatch):
    # 2026-07-13 is a confirmed Monday -- regression test for the bug where
    # the model resolved "la semana que viene, el jueves" to a Saturday.
    fixed_now = datetime(2026, 7, 13, 10, 0, tzinfo=config.TIMEZONE)

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now

    monkeypatch.setattr(loop, "datetime", _FrozenDatetime)

    prompt = loop.build_system_prompt(client_phone="600111222")

    assert "2026-07-13: lunes" in prompt
    assert "2026-07-14: martes" in prompt
    assert "2026-07-15: miércoles" in prompt
    assert "2026-07-16: jueves" in prompt
    assert "2026-07-17: viernes" in prompt
    assert "2026-07-18: sábado" in prompt
    assert "2026-07-19: domingo" in prompt


def test_date_lookup_table_covers_14_days_starting_today():
    table = loop._upcoming_dates_table(datetime(2026, 7, 13).date())
    lines = table.splitlines()

    assert len(lines) == loop._DATE_LOOKUP_DAYS == 14
    assert lines[0] == "2026-07-13: lunes"
    assert lines[-1] == "2026-07-26: domingo"


def test_system_prompt_places_date_time_line_before_dates_table():
    prompt = loop.build_system_prompt(client_phone="600111222")

    date_index = prompt.index("Current date and time")
    table_index = prompt.index("Next 14 days")
    assert date_index < table_index


def test_behavioral_instructions_forbid_leaking_event_id():
    assert "event_id" in loop._BEHAVIORAL_INSTRUCTIONS
    assert "customer-facing" in loop._BEHAVIORAL_INSTRUCTIONS


def test_system_prompt_includes_client_phone_after_date_line():
    prompt = loop.build_system_prompt(client_phone="600111222")

    date_index = prompt.index("Current date and time")
    phone_index = prompt.index("600111222")
    assert date_index < phone_index


def test_system_prompt_client_phone_varies_by_call():
    first = loop.build_system_prompt(client_phone="600111222")
    second = loop.build_system_prompt(client_phone="611222333")

    assert "600111222" in first and "611222333" not in first
    assert "611222333" in second and "600111222" not in second
