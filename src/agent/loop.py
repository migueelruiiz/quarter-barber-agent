"""
ReAct agent loop for Quarter Barber Agent.

Implements docs/react_loop_spec.md. Dev-only: targets Groq's
openai/gpt-oss-120b, which supports native OpenAI-compatible tool calling
but not parallel tool calls (confirmed via Groq's docs, see spec) -- the
loop is strictly sequential (reason -> at most one tool call -> tool result
-> reason -> ...). Production is planned to run on Claude Haiku instead
(see README.md / CLAUDE.md); migrating this loop is out of scope here.

`_call_model` is the sole Groq I/O boundary, monkeypatched in tests so they
run offline and deterministically -- same convention as the I/O-boundary
monkeypatching already used for the Calendar API in tests/test_*.py.
"""

import json
import logging
import os
from datetime import datetime, timedelta

from dotenv import load_dotenv
from groq import APIError, BadRequestError, Groq
from pydantic import ValidationError

import config
from src.agent.schemas import (
    BookAppointmentArgs,
    CancelAppointmentArgs,
    CheckAvailabilityArgs,
    FindAppointmentsArgs,
    RescheduleAppointmentArgs,
    TOOL_SCHEMAS,
)
from src.agent.confirmations import (
    render_booking_confirmation,
    render_reschedule_confirmation,
)
from src.memory.session_store import get_session, save_session
from src.tools.book_appointment import book_appointment
from src.tools.cancel_appointment import cancel_appointment
from src.tools.check_availability import check_availability
from src.tools.find_appointments import find_appointments
from src.tools.reschedule_appointment import reschedule_appointment

load_dotenv()

logger = logging.getLogger(__name__)

MODEL = "openai/gpt-oss-120b"
MAX_ITERATIONS = 8

# Generous cap for a WhatsApp-length reply -- bounds how long a runaway
# degenerate generation can run before finishing, on top of the output
# sanity check in _is_degenerate_output below. Comfortably above a full
# booking confirmation with several fields.
MAX_RESPONSE_TOKENS = 700

# Thresholds for _is_degenerate_output: a real customer-facing reply should
# never legitimately exceed MAX_CONTENT_LENGTH characters, and should never
# legitimately be mostly punctuation/whitespace (the observed failure mode
# below MIN_ALPHA_RATIO). Deliberately conservative -- a false positive
# (rejecting a legitimately long/dense message) is far cheaper than a false
# negative (a garbled reply reaching a real customer).
MAX_CONTENT_LENGTH = 2000
MIN_ALPHA_RATIO = 0.5

FALLBACK_MESSAGE = (
    "Lo siento, no he sido capaz de completar tu solicitud. Por favor, "
    "contáctanos por teléfono para que podamos ayudarte y finalizar la consulta."
)

TOOL_REGISTRY = {
    "check_availability": (CheckAvailabilityArgs, check_availability),
    "book_appointment": (BookAppointmentArgs, book_appointment),
    "find_appointments": (FindAppointmentsArgs, find_appointments),
    "cancel_appointment": (CancelAppointmentArgs, cancel_appointment),
    "reschedule_appointment": (RescheduleAppointmentArgs, reschedule_appointment),
}

# After either of these tools succeeds, the customer-facing reply is
# rendered deterministically by src/agent/confirmations.py, never drafted
# by the model -- see docs/loop_confirmation_bugs_findings.md (bugs 1-3,
# all specific to this exact turn: the confirmation immediately after a
# successful write).
_TEMPLATED_CONFIRMATION_TOOLS = {"book_appointment", "reschedule_appointment"}

_BEHAVIORAL_INSTRUCTIONS = """
You are the WhatsApp booking assistant for Quarter Barber, Gentleman's
(Calle Abtao Nº 4, Madrid).

Rules you must always follow:
- Always reply to the client in Spanish, regardless of the language the
  client writes in. Every customer-facing message must be in Spanish.
- Never fabricate information. Prices, availability, and barber schedules
  must only come from the price menu above or from a tool call. If a tool
  fails or returns no data, say so explicitly to the client instead of
  guessing.
- Everyday Spanish phrasing maps to service categories even when the
  client doesn't use the exact menu term: "teñir/teñirme/tinte" -> a
  color-family service; "decolorar/decolorarme/mechas" -> the
  decoloracion family; "afeitar/afeitarme" -> afeitado; "cortarme el
  pelo" -> corte. Use these mappings directly instead of asking the
  client to restate their request in exact menu terms.
- When asking the client which service they want for booking purposes,
  only offer the 6 booking categories (corte, barba, corte_barba,
  decoloracion, decoloracion_corte, decoloracion_corte_barba). The
  finer-grained price variants in the price menu above (corte fade/
  infantil/jubilado, afeitado/arreglo de barba, colores fantasia/mechas/
  color) exist for price transcription only -- surface them solely when
  the client asks about price, never as separate bookable options.
- Never reveal an appointment's end time to the client -- only the start
  time is ever presented in natural language. The end time returned by
  check_availability/book_appointment is for internal use only.
- Once the service and a date/time preference are known, call
  check_availability immediately -- do not ask for the client's name before
  searching for a slot. Only ask for client_name once the client has picked
  a specific slot, immediately before calling book_appointment.
- Before asking the client for their name, check whether they already gave
  it earlier in this same conversation -- if so, reuse it instead of asking
  again from scratch; only confirm it briefly (e.g. "¿Sigue siendo
  [nombre]?") if there's a genuine reason to doubt it still applies (e.g.
  booking for someone else this time), not as a routine double-check.
  Unlike the phone number, the client's name is never known with certainty
  by the system (shared numbers, booking on someone else's behalf, etc.) --
  it must still be read from the conversation, not assumed or injected --
  but if it's already there, use it.
- Never ask the client for their phone number, and never invent or guess
  one under any circumstance. It is already known from the WhatsApp session
  and is attached automatically to any booking or search. The only phone
  number you may ever state back to the client is the exact value given to
  you at the end of this prompt.
- When no barber preference is given, barber assignment (seniority order:
  Dylan, Yuri, Rafa, Juan, filtered by who is eligible for the requested
  service) is handled automatically by check_availability -- never pick or
  override a barber yourself.
- If the client's request falls outside configured working hours or
  standard rules and cannot be resolved through the available tools, do
  not attempt a workaround -- offer to have the business call them instead.
- Always confirm the appointment details (service, barber, date, time)
  with the client before calling book_appointment, cancel_appointment, or
  reschedule_appointment. These tools write directly to the real calendar.
- event_id for cancel_appointment and reschedule_appointment must come
  from a prior find_appointments call in this conversation -- never invent
  or guess one. event_id (or any other internal identifier returned by a
  tool) must never appear in any customer-facing message -- it is for
  internal use only (passing it to cancel_appointment/reschedule_appointment),
  never for display.
- color_id for book_appointment and reschedule_appointment must be taken
  directly from the color_id field of the specific slot the client chose in
  the most recent check_availability result -- never omit it and never
  guess a value.
- Never calculate weekday offsets yourself (e.g. "el jueves que viene",
  "la semana que viene", "pasado mañana"). A lookup table of the next 14
  days with their correct date and weekday name is provided at the end of
  this prompt -- always resolve relative date/weekday expressions against
  that table, never by mental arithmetic.
- check_availability's max_results defaults to 3 and time_of_day only
  supports "morning"/"afternoon" -- it has no concept of "after 18:00" or
  "later". When the client asks for a time constraint not already covered
  by the slots you've already shown (e.g. "a partir de las 18h", "más
  tarde", "por la noche"), call check_availability again with a higher
  max_results (e.g. 10) and/or the appropriate time_of_day, then check the
  actual returned start times against what the client asked for. Never
  conclude there is no availability based on an earlier, narrower result
  set that wasn't queried with the client's actual constraint in mind.
- When presenting available slots, always frame them as a subset of
  options, not an exhaustive list -- e.g. "estos son algunos huecos
  disponibles" / "tengo estos horarios, entre otros", never a closed
  "estos son los horarios" that could read as complete. This applies to
  every slot listing in the conversation, not just after a broadened
  search.
- A tool result with "reason": "tool_error" earlier in this conversation
  reflects the system's state only at that past moment -- it is not a
  standing fact about the system now. If the client's current message
  needs tool data (availability, booking, cancellation, etc.), always
  make the appropriate tool call again this turn; never answer from an
  earlier tool_error alone, even if the client is repeating the same
  request that failed before.
""".strip()

_STATIC_SYSTEM_PROMPT = (
    f"Price menu (EUR):\n{config.render_price_menu()}\n\n{_BEHAVIORAL_INSTRUCTIONS}"
)

_DATE_LOOKUP_DAYS = 14


def _upcoming_dates_table(today) -> str:
    """Deterministic date -> Spanish weekday name lookup for the next
    _DATE_LOOKUP_DAYS days (today included), computed with plain date
    arithmetic. Exists so the model never has to calculate a weekday offset
    itself -- LLMs are unreliable at mental calendar math (see the bug this
    fixes: "la semana que viene, el jueves" resolved to a Saturday and was
    then mislabeled "jueves" in the reply). A lookup table turns that into a
    trivial lookup instead of arithmetic."""
    lines = []
    for offset in range(_DATE_LOOKUP_DAYS):
        day = today + timedelta(days=offset)
        lines.append(f"{day.isoformat()}: {config.SPANISH_WEEKDAYS[day.weekday()]}")
    return "\n".join(lines)


def build_system_prompt(client_phone: str) -> str:
    """Static content (price menu + behavioral instructions) first, current
    Europe/Madrid date/time, a 14-day date/weekday lookup table, and the
    customer's phone number appended last, in that order. Recalculated on
    every call so a long-running or resumed session never reasons with a
    stale date -- see spec.md "Current date/time injection" for why this
    ordering matters for Groq's automatic prompt caching (only the shared
    prefix is cached).

    client_phone is never a tool parameter the model can fill in (see
    schemas.py -- book_appointment/find_appointments deliberately omit it),
    but the model still needs the real value in context to state it back
    accurately for turns it still drafts itself (e.g. a cancellation
    confirmation), rather than hallucinating one. Booking/reschedule
    confirmations no longer rely on this -- see src/agent/confirmations.py,
    which renders client_phone from the same session_id directly, without
    going through the model at all."""
    now = datetime.now(config.TIMEZONE)
    weekday_es = config.SPANISH_WEEKDAYS[now.weekday()]
    date_line = f"Current date and time (Europe/Madrid): {now.strftime('%Y-%m-%d %H:%M')} ({weekday_es})."
    dates_table = (
        "Next 14 days -- date: weekday name (Europe/Madrid). Always resolve "
        "relative date/weekday expressions against this table, never by "
        f"calculating them yourself:\n{_upcoming_dates_table(now.date())}"
    )
    phone_line = (
        f"Customer's phone number (already known by the system -- never ask "
        f"for it): {client_phone}. You may state it back accurately when "
        f"confirming a booking, cancellation, or reschedule."
    )
    return f"{_STATIC_SYSTEM_PROMPT}\n\n{date_line}\n{dates_table}\n{phone_line}"


# Tools whose underlying function takes client_phone but whose model-facing
# schema deliberately excludes it (see schemas.py) -- the value is injected
# here, server-side, from the session's phone number, never from the model.
_NEEDS_CLIENT_PHONE = {"book_appointment", "find_appointments"}


def dispatch_tool(name: str, arguments: str, client_phone: str) -> dict | list:
    """Validate `arguments` (a JSON string) against the registered tool's
    Pydantic model and call the real tool function with the validated
    fields. Never raises: invalid input and unexpected exceptions escaping
    the tool function are both turned into a structured error dict, since
    this result is fed straight back to the model as the tool response."""
    entry = TOOL_REGISTRY.get(name)
    if entry is None:
        return {"success": False, "reason": "unknown_tool", "detail": f"No such tool: {name!r}"}

    model, func = entry

    try:
        parsed = json.loads(arguments)
    except json.JSONDecodeError as e:
        return {"success": False, "reason": "invalid_arguments", "detail": str(e)}

    try:
        validated = model.model_validate(parsed)
    except ValidationError as e:
        return {"success": False, "reason": "invalid_arguments", "detail": str(e)}

    kwargs = validated.model_dump()
    if name in _NEEDS_CLIENT_PHONE:
        kwargs["client_phone"] = client_phone

    try:
        return func(**kwargs)
    except Exception as e:
        return {"success": False, "reason": "tool_error", "detail": str(e)}


def _render_templated_confirmation(
    tool_name: str, arguments: str, result: dict, client_phone: str
) -> str:
    """Build the deterministic confirmation text for a successful
    book_appointment/reschedule_appointment call -- see
    src/agent/confirmations.py. Re-parses `arguments` the same way
    dispatch_tool already did to get at the model-supplied fields
    confirmations.py needs (client_name/service/color_id) that aren't part
    of the tool's own return value. Safe to re-parse: dispatch_tool having
    returned a successful result already proves this same JSON string
    passed Pydantic validation once."""
    parsed = json.loads(arguments)
    if tool_name == "book_appointment":
        args = BookAppointmentArgs.model_validate(parsed)
        return render_booking_confirmation(
            client_name=args.client_name,
            service=args.service,
            start=result["start"],
            color_id=args.color_id,
            client_phone=client_phone,
        )
    args = RescheduleAppointmentArgs.model_validate(parsed)
    return render_reschedule_confirmation(
        new_start=result["start"],
        color_id=args.color_id,
        client_phone=client_phone,
    )


def _get_client() -> Groq:
    return Groq(api_key=os.environ["GROQ_API_KEY"])


def _call_model(messages: list[dict]):
    return _get_client().chat.completions.create(
        model=MODEL,
        messages=messages,
        tools=TOOL_SCHEMAS,
        max_tokens=MAX_RESPONSE_TOKENS,
    )


def _is_degenerate_output(content: str | None) -> bool:
    """Catches the known class of degenerate/repetitive LLM generation
    (observed once in real dev testing: hundreds of lines of repeated
    punctuation/whitespace/short fragments instead of a coherent reply)
    that no amount of prompting fully prevents. Deliberately simple --
    not a text-quality classifier, just two cheap, conservative signals."""
    if not content:
        return False
    if len(content) > MAX_CONTENT_LENGTH:
        return True
    alpha_ratio = sum(1 for c in content if c.isalpha()) / len(content)
    return alpha_ratio < MIN_ALPHA_RATIO


def _tool_schema_validation_detail(error: BadRequestError) -> str | None:
    """Returns the validation-error text from a BadRequestError's response
    body if it's a Groq tool_use_failed error (recoverable via a single
    retry -- see run_agent_turn), or None if the body doesn't match that
    shape (some other 400 -- e.g. a request-size or auth-adjacent error --
    not necessarily recoverable the same way, so left to the generic
    APIError fallback path instead).

    Confirmed empirically (Groq docs + real dev failures) that the SDK
    exposes this reliably: `error.body` is the JSON-decoded response body
    whenever the response parsed as JSON (raw text otherwise, None if there
    was no response at all -- see groq._base_client._make_status_error_from_
    response). A tool_use_failed body has the shape
    {"error": {"code": "tool_use_failed", "message": "<validation detail,
    e.g. \"missing properties: 'color_id'\">", "failed_generation": "..."}}.
    The `message` field is exactly the human-readable validation detail we
    want to hand back to the model."""
    body = error.body
    if not isinstance(body, dict):
        return None
    inner = body.get("error")
    if not isinstance(inner, dict) or inner.get("code") != "tool_use_failed":
        return None
    return inner.get("message") or "tool call rejected by schema validation"


def run_agent_turn(session_id: str, user_message: str) -> str:
    messages = get_session(session_id)
    messages.append({"role": "user", "content": user_message})

    for _ in range(MAX_ITERATIONS):
        system_prompt = build_system_prompt(client_phone=session_id)
        request_messages = [{"role": "system", "content": system_prompt}, *messages]

        try:
            response = _call_model(request_messages)
        except BadRequestError as e:
            # A schema-validation rejection (Groq's tool_use_failed) is
            # different from other 400s: the model's tool call was
            # malformed, not its reasoning -- e.g. a missing required field
            # or a non-ISO-8601 datetime (see CLAUDE.md, recurring
            # intermittently despite prompt guidance, consistent with LLM
            # output being non-deterministic). Retrying once with the raw
            # validation detail usually fixes it, so it doesn't deserve the
            # same "give up" treatment as a real API failure -- and the
            # retry is folded into this same iteration so it doesn't cost
            # the turn a MAX_ITERATIONS slot.
            detail = _tool_schema_validation_detail(e)
            if detail is None:
                logger.warning("Groq API call failed, aborting turn: %s", e)
                save_session(session_id, messages)
                return FALLBACK_MESSAGE

            logger.warning(
                "Tool call rejected by schema validation, retrying once: %s", detail
            )
            retry_messages = request_messages + [
                {
                    "role": "user",
                    "content": (
                        "Tu llamada a herramienta anterior fue rechazada por "
                        f"validación de esquema: {detail}. Corrige los "
                        "argumentos y vuelve a intentar la llamada."
                    ),
                }
            ]
            try:
                response = _call_model(retry_messages)
            except APIError as retry_e:
                logger.warning(
                    "Retry after schema validation failure also failed, "
                    "aborting turn: %s",
                    retry_e,
                )
                save_session(session_id, messages)
                return FALLBACK_MESSAGE
        except APIError as e:
            # Covers every other Groq API failure mode (RateLimitError,
            # transient 5xx, connection errors, etc. -- APIError is the base
            # class for all of them). Same outcome as the MAX_ITERATIONS
            # exhausted path: never lose the customer's message, never let
            # an unhandled exception surface to the caller.
            logger.warning("Groq API call failed, aborting turn: %s", e)
            save_session(session_id, messages)
            return FALLBACK_MESSAGE

        # Ground-truth prompt size straight from Groq on every real call --
        # cheap, and the single most useful signal for spotting an unusually
        # large request before it fails (e.g. a 413). Mocked responses in
        # tests have no `.usage`, so this is a no-op there.
        usage = getattr(response, "usage", None)
        if usage is not None:
            prompt_details = getattr(usage, "prompt_tokens_details", None)
            cached_tokens = getattr(prompt_details, "cached_tokens", None)
            logger.info(
                "Groq usage: prompt_tokens=%s completion_tokens=%s "
                "total_tokens=%s cached_tokens=%s",
                usage.prompt_tokens,
                usage.completion_tokens,
                usage.total_tokens,
                cached_tokens,
            )

        choice = response.choices[0]

        # Diagnostic only (DEBUG level, silent by default): reproduced and
        # root-caused in docs/loop_confirmation_bugs_findings.md -- the
        # model occasionally emits a garbled/self-correcting draft directly
        # inside `content` itself (message.reasoning is separate and
        # unaffected, confirmed via a live capture). Structurally
        # eliminated for the book_appointment/reschedule_appointment
        # confirmation turn by the templated bypass below; this log stays
        # for every other still-model-drafted turn (cancellation
        # confirmations, informational replies, etc.), where the same
        # underlying model behavior could in principle still occur.
        logger.debug(
            "groq response: finish_reason=%s has_tool_calls=%s content=%r",
            choice.finish_reason,
            bool(choice.message.tool_calls),
            choice.message.content,
        )

        if choice.finish_reason != "tool_calls":
            content = choice.message.content
            if _is_degenerate_output(content):
                # Same outcome as the MAX_ITERATIONS/APIError paths above:
                # never let a bad turn's output be committed to history --
                # save the session as it stands (customer message + any
                # tool calls/results already accumulated this turn) and
                # answer with the fallback instead of the garbage content.
                logger.warning(
                    "Degenerate model output detected, discarding and "
                    "returning fallback message. content=%r",
                    (content or "")[:500],
                )
                save_session(session_id, messages)
                return FALLBACK_MESSAGE
            messages.append({"role": "assistant", "content": content})
            save_session(session_id, messages)
            return content

        tool_call = choice.message.tool_calls[0]
        result = dispatch_tool(
            tool_call.function.name, tool_call.function.arguments, client_phone=session_id
        )

        messages.append(
            {
                "role": "assistant",
                "content": choice.message.content,
                "tool_calls": [
                    {
                        "id": tool_call.id,
                        "type": "function",
                        "function": {
                            "name": tool_call.function.name,
                            "arguments": tool_call.function.arguments,
                        },
                    }
                ],
            }
        )
        messages.append(
            {
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": json.dumps(result, default=str),
            }
        )

        if (
            tool_call.function.name in _TEMPLATED_CONFIRMATION_TOOLS
            and isinstance(result, dict)
            and result.get("success") is True
        ):
            try:
                confirmation = _render_templated_confirmation(
                    tool_call.function.name,
                    tool_call.function.arguments,
                    result,
                    client_phone=session_id,
                )
            except Exception as e:
                # Defense in depth only -- e.g. a color_id that passed
                # schema validation (a plain str | None, not checked
                # against config.BARBERS) but doesn't match any real
                # barber. Should not happen if the model followed the
                # color_id instruction, but a template-rendering failure
                # must never crash the turn -- same fallback outcome as
                # every other failure mode in this loop.
                logger.warning(
                    "Templated confirmation rendering failed for %s, "
                    "falling back: %s",
                    tool_call.function.name,
                    e,
                )
                save_session(session_id, messages)
                return FALLBACK_MESSAGE

            messages.append({"role": "assistant", "content": confirmation})
            save_session(session_id, messages)
            return confirmation

    save_session(session_id, messages)
    return FALLBACK_MESSAGE
