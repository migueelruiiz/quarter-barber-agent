# ReAct Agent Loop — Design Spec

Target files: `src/agent/loop.py`, `src/agent/schemas.py`, `src/memory/session_store.py`
Depends on: `config.py` (`SERVICES`, `BARBERS`), `src/tools/*` (all 5 tools), Groq API (`openai/gpt-oss-120b`)

## Scope

This is a new component, not a port of `personal-agent`'s ReAct loop. `personal-agent`
targeted `llama-3.3-70b-versatile` on Groq, which required a fallback parser for
malformed tool-call output (legacy XML format instead of JSON). `openai/gpt-oss-120b`
supports native OpenAI-compatible tool calling and does not need that fallback —
confirmed against Groq's own docs (`console.groq.com/docs/tool-use`, "Supported
Models" table: `openai/gpt-oss-120b` → Yes for tool use, No for parallel tool use).

No parallel tool calls: the model returns at most one tool call per turn. The loop
is strictly sequential (reason → one tool call → tool result → reason → ...), with
no branch for handling multiple simultaneous tool calls in a single model response.

If a booking request logically requires multiple appointments (e.g. two people,
two services), the agent must handle them as separate sequential tool calls across
turns — never assume or request parallel execution from the model.

## LLM client

- Model: `openai/gpt-oss-120b` via Groq.
- Tool calling: native (`tools` parameter, OpenAI-compatible format), not
  Structured Outputs (`response_format: json_schema`) — the two are mutually
  exclusive per Groq's docs ("Streaming and tool use are not currently supported
  with Structured Outputs").
- `MAX_ITERATIONS = 8`. Caps the number of loop iterations (one model call +
  optional tool call = one iteration) before forcing termination. Chosen for a
  domain where a real booking flow rarely exceeds 2-3 sequential tool calls
  (e.g. `check_availability` → `book_appointment`), with margin for the customer
  changing their mind mid-flow (re-querying availability, correcting a parameter).
  If the cap is reached without a final (non-tool-call) response, the loop must
  return a fallback message directing the customer to a phone call (R-17) —
  never leave the request unanswered.

## Loop algorithm

```
messages = [system_prompt(), *session_history, user_message]

for _ in range(MAX_ITERATIONS):
    response = groq_client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=messages,
        tools=TOOL_SCHEMAS,
    )
    choice = response.choices[0]

    if choice.finish_reason != "tool_calls":
        return choice.message.content  # final answer to the customer

    tool_call = choice.message.tool_calls[0]  # exactly one, by construction
    result = dispatch_tool(tool_call.function.name, tool_call.function.arguments)

    messages.append(choice.message)
    messages.append({
        "role": "tool",
        "tool_call_id": tool_call.id,
        "content": json.dumps(result, default=str),
    })

# MAX_ITERATIONS exhausted without a final answer
return FALLBACK_MESSAGE  # R-17: offer a phone call
```

`system_prompt()` is regenerated on every loop iteration, not cached across the
whole conversation — see "Current date/time injection" below for why.

## Tool schemas

One Pydantic `BaseModel` per tool (5 total: `CheckAvailabilityArgs`,
`BookAppointmentArgs`, `FindAppointmentsArgs`, `CancelAppointmentArgs`,
`RescheduleAppointmentArgs`), living in `src/agent/schemas.py`. Each model:

- Mirrors the exact parameter signature of its corresponding function in
  `src/tools/*.py`.
- Is converted to the Groq tool-schema format via `.model_json_schema()`, wrapped
  in the `{"type": "function", "function": {...}}` envelope Groq expects.
- Is used to **validate** the arguments the model returns (`tool_call.function.arguments`,
  a JSON string) before calling the real tool function — never trust the raw
  parsed JSON directly, regardless of how well-formed native tool calling
  generally is.

**Enum fields must never hardcode literal values.** `service` and `barber`
parameters are closed sets already defined in `config.py` (`SERVICES.keys()`,
`BARBERS.keys()`). Hardcoding them as `Literal["corte", "barba", ...]` inside a
`BaseModel` duplicates data that must only live in `config.py` (per its own
docstring) and silently desyncs the moment a service or barber is added or
renamed. Build the `Literal` dynamically at import time instead:

```python
from typing import Literal
from config import SERVICES, BARBERS

ServiceName = Literal[tuple(SERVICES.keys())]
BarberName = Literal[tuple(BARBERS.keys())]
```

Each `BaseModel` references `ServiceName`/`BarberName`, not a copied string list.

**`event_id` dependency (`find_appointments` → `cancel_appointment` /
`reschedule_appointment`) is enforced via the tool `description`, not in code.**
Both `cancel_appointment` and `reschedule_appointment` already require an
`event_id` obtained from a prior `find_appointments` call — this is existing,
documented behavior (see each tool's spec: *"must already be known to the
caller, obtained from a prior find_appointments call"*). The loop does not add a
stateful check verifying `find_appointments` was actually called earlier in the
conversation — that would be code complexity for a risk that's already handled
gracefully: a hallucinated or stale `event_id` simply returns `{"success": False,
"reason": "not_found"}` (404/410 handling already implemented in both tools),
never a data-integrity issue. The dependency is instead made explicit in the
`description` field of the `cancel_appointment`/`reschedule_appointment` tool
schemas in `src/agent/schemas.py`, e.g.: *"event_id must come from a prior
find_appointments call in this conversation — never invent or guess one."* This
is the field the model actually reads to decide when and how to call a tool, so
it's the correct place to encode the dependency, consistent with how the tools
themselves already trust prompt-level/behavioral enforcement over code-level
enforcement for non-integrity-risking constraints (e.g. no `confirmed` flag on
any write tool).

**`client_phone` is deliberately excluded from every tool schema exposed to
the model.** `book_appointment` and `find_appointments` are the only two tool
functions that take `client_phone`, but `session_id` *is* the customer's
phone number (Twilio's `From` field in production) — the system already
knows it with certainty before the conversation even starts. The model must
never be asked to supply or infer a customer's phone number: that would be
redundant at best and, once this goes live, a real risk (a mistyped or
mis-transcribed digit silently attaching the wrong number to a booking).
Instead, `dispatch_tool` injects `client_phone=session_id` server-side for
exactly these two tools, after Pydantic validation, before calling the real
tool function — see "Tool dispatch" below.

## Tool dispatch

A single mapping from tool name (string, as sent by the model) to
`(pydantic_model, python_function)`, used by `dispatch_tool`:

```python
TOOL_REGISTRY = {
    "check_availability": (CheckAvailabilityArgs, check_availability),
    "book_appointment": (BookAppointmentArgs, book_appointment),
    "find_appointments": (FindAppointmentsArgs, find_appointments),
    "cancel_appointment": (CancelAppointmentArgs, cancel_appointment),
    "reschedule_appointment": (RescheduleAppointmentArgs, reschedule_appointment),
}
```

`dispatch_tool` parses `arguments` (JSON string) into the corresponding Pydantic
model, then calls the real function with the validated fields. Any exception
raised by the tool function itself (not argument validation — actual execution
failures) must be caught here and turned into a structured error result passed
back to the model as the tool response, never left to crash the loop. This
follows the same pattern already used inside the tools for `HttpError` handling
(`cancel_appointment`, `reschedule_appointment`) — the loop must not duplicate
or bypass that handling, only catch what escapes it.

## Current date/time injection

The system prompt includes the current date, time, and weekday in
`Europe/Madrid`, recalculated on **every loop iteration** (not once per session).
Cost is negligible (a single `datetime.now(TIMEZONE)` call and one line of text);
the alternative (fixing it once at session start) risks the model reasoning with
a stale date if a session is resumed later or a conversation crosses midnight —
same category of bug as the timezone-normalization issue already found and fixed
in `check_availability`.

**Ordering within the system prompt matters for Groq's automatic prompt caching.**
Groq caches the longest exact-match prefix shared with a recent request (confirmed
active for `openai/gpt-oss-120b`, 50% discount on cached input tokens, zero
config). Caching only holds up to the *first* point of difference between two
prompts. The date/time line must therefore be placed at the **end** of the system
prompt, after the static content (price menu from `config.render_price_menu()`,
behavioral instructions) — never at the start or interleaved with it. This keeps
the static prefix cacheable across every turn of a conversation (and across
different customers' conversations, since that content never changes) while
still recalculating the one line that must never go stale. Placing the dynamic
content first would break caching for the entire prompt on every single call.

## Session memory

Target file: `src/memory/session_store.py`

- Storage: SQLite, on a persistent volume (Railway Hobby plan — chosen over
  Render for this project specifically because Railway's Hobby tier includes
  persistent volume storage without cold-start/sleep behavior on inactivity,
  relevant for a WhatsApp webhook where response latency after idle periods
  matters).
- Schema: one table, columns `session_id` (text, = phone number), `messages`
  (text, JSON-serialized list of the full message history — no summarization),
  `updated_at` (timestamp).
- `session_id` = the customer's phone number as received from Twilio's `From`
  field. No separate session-ID generation.
- Full message history is stored verbatim (every `user`/`assistant`/`tool`
  message), not a summarized state (e.g. "preferred barber"). Booking
  conversations are short enough (few turns, well under the 131K token context
  window) that summarization would add complexity and a class of bugs
  (stale/incorrect summaries) without a real capacity constraint to justify it.

### Why session memory exists at all

Not for cross-conversation preference persistence (e.g. remembering a customer's
preferred barber for a *future, unrelated* booking) — that is deliberately out of
scope, matching how the business already operates in person (the barber is
specified per booking, every time). Session memory exists because the FastAPI
backend is stateless between HTTP requests: each WhatsApp message arrives as an
independent Twilio webhook call, with no in-process memory of earlier messages
in the same booking conversation. Session memory is what allows a multi-turn
booking (service → barber → time → confirmation, potentially across several
separate webhook calls) to be completed at all.

### Session expiration

- **Timeout: 90 minutes of inactivity.** On a new incoming message for a given
  `session_id`, if `updated_at` is older than 90 minutes, the stored history is
  discarded and the conversation starts fresh (empty history) rather than being
  loaded. Chosen over a shorter window (e.g. 30 min) because the more likely
  real-world interruption is the customer getting pulled away mid-conversation
  (a call, a distraction) and returning within an hour or so — a same-day,
  different-topic re-contact within the window is considered less likely and an
  acceptable tradeoff.
- **Expiration is silent — no proactive notification.** Detecting expiration at
  the exact moment it happens (to message the customer that their session
  lapsed) would require an active background job (cron/scheduled task) polling
  sessions independently of incoming requests, since the current design only
  checks `updated_at` reactively when a new message arrives. That adds new
  infrastructure (something running outside the request/response cycle) and an
  outbound, business-initiated message likely falls under Meta's WhatsApp
  Business API template-message rules outside the 24h customer-service window
  (relevant to R-14, still pending verification). Deferred as a future
  improvement, not part of v1 — in practice, expirations are expected to be rare
  given typical response latency.
- No distinction between an abandoned booking and a completed one for expiration
  purposes — both simply age out after 90 minutes. No explicit "close the
  session" trigger (e.g. detecting a thank-you message) is implemented; it would
  be unreliable (a "thanks, also—" message would need to *not* close the
  session) and unnecessary given passive expiration already covers both cases
  uniformly.

## Explicitly out of scope for this spec

- Twilio/WhatsApp webhook integration (`src/whatsapp/`) — separate milestone.
- Rate limiting per phone number (R-6) — separate concern from the loop itself.
- Cross-session customer preference memory — deliberately not implemented (see
  "Why session memory exists at all").
- Proactive session-expiration notifications — deferred, see above.
- Analytics/reporting ingestion — unrelated to the loop.
- **Migration to Claude for production.** Per `README.md`, production is planned
  to run on Claude Haiku, not Groq — this spec and `loop.py` target Groq/
  `openai/gpt-oss-120b` for dev only. Claude's tool-calling format is not
  identical to Groq/OpenAI's (different request/response shape for tool
  definitions and tool-call results), so migrating is not a drop-in API-key
  swap. When that migration is scheduled, `loop.py`'s Groq client call will
  need either a translation layer or a client-specific rewrite — not addressed
  here.
  - **Concurrent messages from the same session.** This spec assumes serialized
  access to a session's history (get_session → mutate → save_session as a
  single sequence per run_agent_turn call). If two messages from the same
  customer arrive close enough together that two run_agent_turn calls run
  concurrently for the same session_id (realistic once this is behind a
  Twilio webhook — the dev CLI script can't reproduce this, since input()
  is strictly sequential), the second save_session call would overwrite the
  first's update (lost-update race). Not addressed here — must be resolved
  when the FastAPI/Twilio integration layer is built (e.g. a per-session_id
  lock or queue), not deferred further than that milestone.