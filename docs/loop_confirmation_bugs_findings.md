# Findings: confirmation-turn text corruption and weekday mismatches

Investigated via `testingscripts/dev_chat.py` manual testing (session
`658553891`, persisted in `testingscripts/data/sessions.db`) plus one
live, debug-instrumented reproduction run against real Groq +
`quarter-barber-dev` on 2026-07-16/17. No fix applied — findings only,
per project workflow.

Both bugs are **not regressions of the 2026-07-16 fix** (event_id leak,
degenerate-output filter, date-lookup-table content) and **not caused by
this codebase's retry logic**. Both are gaps that fix didn't cover, one
in each direction: (1) a raw-model-generation defect the degenerate-output
filter wasn't tuned to catch, and (2) a display step with no grounding
instruction at all.

---

## Bug 1 — corrupted/self-correcting text leaking into `content`

### Root cause

`openai/gpt-oss-120b`, on a minority of confirmation turns (the turn
immediately after `book_appointment`/`reschedule_appointment` returns),
emits a `message.content` string that itself contains a leading garbled
fragment (zero-width spaces `​`, non-breaking spaces `\xa0`,
asterisks, sometimes a partial/full English self-diagnosis sentence),
glued with **zero separator** directly onto the real, correct Spanish
answer. This happens **inside a single API response** — there is no
second model call and no code-side concatenation involved.

This was confirmed two independent ways:

**1. Ruled out any codebase-side retry/template source.** A repo-wide
case-insensitive grep for the literal corrupted-text fragments
(`"No internal IDs"`, `"appears corrupted"`, `"need to correct"`,
`"Provide proper confirmation"`, etc.) across every file returns nothing.
The only retry mechanism in the code (`run_agent_turn`'s
`BadRequestError`/`tool_use_failed` handling, `src/agent/loop.py:315-354`)
builds a fixed **Spanish** string about schema validation
(`"Tu llamada a herramienta anterior fue rechazada..."`), holds it in a
**local variable** (`retry_messages`) that is never appended to the
persisted `messages` history, and only fires on a 400 from the API —
never on a plain `finish_reason="stop"` response. The degenerate-output
path (`_is_degenerate_output`, line 397-411) has no retry at all — on a
flagged output it returns `FALLBACK_MESSAGE` directly, discarding the
content; it cannot produce a "corrupted + corrected" concatenation
because there is no second generation to concatenate. Confirmed
definitively by the live repro below: the corrupted turn had
`finish_reason="stop"` with **no** `BadRequestError` and no retry path
invoked anywhere in that turn.

**2. Captured the raw field split directly.** Temporary instrumentation
(`logger.debug` on `choice.message.reasoning`, reverted after this
investigation) on a live call reproduced the bug on the very first
attempt (session `repro-999999999`, turn 2 — the `book_appointment`
confirmation). The raw API response had:

```
content  = 'Perfect\xa0—\xa0t\xa0​\xa0\xa0? \n\n****\n\n\n\n¡Listo, Felipe! Tu cita para **arreglo de barba** (10€) está confirmada: ...'
reasoning = "We have a problematic final output: It seems garbled and not "
            "following guidelines. Need to correct.\n\nWe need to respond "
            "in Spanish, confirm appointment details: service (arreglo de "
            "barba, 10€), barber Dylan, date 18 de julio, time 11:00, name "
            "Felipe, phone number repro-999999999 (optional to state). "
            "Must not show event_id. Should be clear. Also note we need to "
            "use correct formatting and avoid weird characters.\n\nLet's "
            "produce a proper response."
```

`message.reasoning` is genuinely populated and genuinely distinct from
`content` — the 2026-07-16 fix's premise (Groq keeps these two fields
structurally separate) is correct and still holds at the SDK/field level.
`message.reasoning` is never read anywhere in this codebase (confirmed by
grep), so it isn't the leak source either. The leak is **inside
`content` itself**: the model's own final-channel generation includes an
aborted/garbled first pass, and — in this instance — no legible
self-correction sentence, just noise characters, immediately followed by
the real answer.

Cross-referencing this against the two original transcripts (session
`658553891`) shows the same shape, just with the noise-only prefix
replaced by (or combined with) a legible English self-diagnosis sentence
very close in wording to the reasoning example above ("The answer appears
corrupted; need to correct. Provide proper confirmation in Spanish,
include service, barber (from slot: Dylan), date and time, price
(10.00€), and phone number. No internal IDs."). Note the price format
`10.00€` (period decimal, no space) matches `config.render_price_menu()`'s
literal format (`config.py:224`, `f"{price:.2f}€"`) as it appears in
the system prompt — i.e. this text is being drawn from the model's own
context/plan, not copied from any code-constructed string (our code never
formats prices that way outside the system prompt, and the actual
customer-facing price format used moments later in the same string is the
Spanish-formatted `10,00 €`). All three observed instances have different
exact wording/noise, which is expected of stochastic model output and
would not be expected from a fixed code-side template bug.

**Conclusion:** this is a gap in the 2026-07-16 fix's coverage, not a
regression and not a retry-path leak. That fix correctly established that
`message.reasoning` is discarded/never used — true and still holds. It
did not anticipate the model occasionally producing a flawed draft
directly in `content` and self-correcting *within the same generation*,
un-separated from the corrected text. The fix's own debug comment
(`src/agent/loop.py:384-389`, now stale) explicitly says this was "not
reproduced against real Groq responses so far" — it has now been
reproduced, on the first live attempt, specifically on a confirmation
turn.

### Why `_is_degenerate_output` doesn't catch it

Computed directly against the two real corrupted messages from session
`658553891`:

| turn | length | alpha_ratio | MIN_ALPHA_RATIO | flagged? |
|---|---|---|---|---|
| booking confirmation (idx 7) | 609 | 0.573 | 0.5 | **No** |
| reschedule confirmation (idx 19) | 638 | 0.616 | 0.5 | **No** |

Both are well under `MAX_CONTENT_LENGTH` (2000) and clear
`MIN_ALPHA_RATIO` (0.5) because the tail of the string is a full,
coherent, correct answer — most of the string's characters are real
Spanish/English words. The filter was tuned for a different failure mode
(the regression test at `tests/test_loop.py:466`, hundreds of lines of
pure repeated punctuation — near-zero alpha ratio). This new shape
(short garbled prefix + long coherent correct suffix) structurally can't
trip either signal.

### Correlation with confirmation turns

All 3 observed corruptions (2 in the original session, 1 in the fresh
repro) occurred on the turn immediately after `book_appointment` or
`reschedule_appointment` returned. Zero corruptions were observed across
~15 other turns in the two sessions inspected (informational replies,
slot-offering replies, tool-call-issuing turns). Sample size is small,
but consistent with these being the highest-complexity synthesis turns —
the model must simultaneously satisfy several constraints (service,
barber, date, time, price, phone, "never reveal event_id", "never reveal
end time") from a raw tool result, which plausibly increases the odds of
an unstable first draft.

### Proposed fix approach (not applied)

Strip/repair rather than reject-and-fallback, since the correct answer is
present in the same string, just prefixed with garbage: after receiving
`content`, detect a leading corrupted segment (heuristic: content before
the first sentence that starts with a Spanish greeting/confirmation
marker the system prompt's own vocabulary uses, e.g. `¡`) and drop
everything before it, rather than discarding the whole reply via
`_is_degenerate_output`. This is more surgical than lowering
`MIN_ALPHA_RATIO` (which would risk false-negatives on the original
hundreds-of-lines-of-punctuation case if a coherent line appeared at the
very end) and avoids a full fallback (and its "call us instead" message)
for a turn that already computed the correct answer. Needs a few more
real examples to design the heuristic safely — flagging as an open risk,
not a decided design.

---

## Bug 2 — wrong weekday name in confirmations

### Root cause

The system prompt's 14-day lookup table (`_upcoming_dates_table`,
`src/agent/loop.py:169-181`) is regenerated correctly on every turn and
is never wrong — verified both by the existing regression test
(`tests/test_loop.py:558`) and by grepping the actual injected table from
the live repro's raw request logs (`2026-07-23: jueves`, `2026-07-21:
martes`, etc. — all correct). The bug is that the model doesn't reliably
consult it, in **two distinct ways**:

**Sub-mode A — confirmation-turn weekday is freely generated, not
looked up, even when the date itself is correct.** In the original
session, the booking confirmation (idx 7) states `17 de julio de 2026
(domingo)` — the date `17 de julio` is correct (matches the earlier,
correct offer message and the tool result), but 2026-07-17 is a
**viernes** per the table, not domingo. The reschedule confirmation
(idx 19) is even more telling: it states `martes 21 de julio de 2026
(miércoles)` — self-contradictory *within the same string*: it opens
with the correct word "martes" (matching its own earlier, correct offer
message a few turns prior) and then appends a wrong parenthetical
"(miércoles)" moments later. In the live repro, the booking confirmation
similarly stated `18 de julio de 2026 (domingo)` when the table says
**sábado**.

Checking the model's own `reasoning` field for that exact repro turn: it
discusses service, barber, date ("18 de julio"), time, name, phone, and
formatting — **it never mentions a weekday at all**. The parenthetical
weekday is generated with zero planning, at the surface-text level, with
no table lookup happening at any stage of that turn's reasoning. It is
not requested by any instruction — no offer/informational message
anywhere in either session uses the `(weekday)` parenthetical format; it
appears to be a stylistic flourish the model adds specifically to
confirmation messages, unprompted, and unguarded by the existing
"never calculate weekday offsets yourself" instruction — which is worded
around interpreting the *customer's* relative phrasing ("el jueves que
viene"), not around how the model should render an already-known date's
weekday name for display.

**Sub-mode B — the date itself is resolved wrong for compound relative
phrases, and the model then falsely claims table compliance.** In the
live repro, resolving "el martes de la semana que viene" (today: Friday
2026-07-17) should yield 2026-07-21 (per the table). The model's own
reasoning for the `check_availability` call instead says: *"Today is
Friday 2026-07-17. Next week Tuesday would be 2026-07-23 (Tuesday)"* —
which is free-hand arithmetic, not a table scan, and 2026-07-23 is
actually **jueves** per the table. Worse, on the very next turn (the slot
offer, not even the confirmation), the model's reasoning doubles down:
*"Next week Tuesday is 2026-07-23 (as per table)"* — explicitly claiming
table grounding for a value the table contradicts. This is a distinct
failure from sub-mode A: it's not that the table was ignored, it's that
the model did mental arithmetic, got it wrong, and then hallucinated
having verified it against the table.

Both sub-modes converge on the same underlying gap: the table is present
and correct on every turn, and the instruction to use it only explicitly
covers the *forward* direction for simple phrases (resolving what date
"tomorrow"/"el jueves que viene" means). It does not explicitly require
the *reverse* direction (given a concrete date, e.g. from a tool result
or from the customer's own phrasing, look up its weekday name for
display) or extend to compound phrases (relative week + explicit weekday
name), and nothing in the loop verifies that the value in the final
customer-facing string actually matches the table before it goes out.

**No prior fix touched this.** The only existing test on this table
(`test_date_lookup_table_has_correct_spanish_weekdays_for_known_date`,
`tests/test_loop.py:558`) only asserts the table's own text is correct
inside the system prompt — it never exercises a real (or mocked)
confirmation turn to check the table is actually *used*. No test in the
suite calls a real model for this, so this class of bug can't be caught
by the existing 123 tests regardless of how they're extended, only by
integration/live testing or a code-level guard.

### Proposed fix approach (not applied)

Don't trust the model to reverse-derive weekday text at all: after a
tool call returns a `start` datetime, compute its Spanish weekday name in
code (same `_SPANISH_WEEKDAYS` dict already used for the table) and
either (a) inject it directly into the tool result dict so the model only
ever has to copy a ready-made string, never compute one, or (b)
post-process the model's final confirmation text to replace/validate any
`(weekday)`-shaped parenthetical against the known-correct value before
sending it to the customer. Option (a) is more consistent with this
project's existing philosophy (`R-16`, `R-7` etc. — push correctness into
deterministic code, not model discipline) and avoids relying on prompt
wording to cover a case it doesn't currently cover explicitly.

---

## Incidental finding (not investigated further, out of scope)

Session `638242539`, message index 29 (a reschedule confirmation) states
`Hora: 14:00–14:30`, revealing the appointment's end time — this
violates the explicit rule at `src/agent/loop.py:87-89` ("Never reveal an
appointment's end time to the client"). Flagging for a separate pass;
not investigated here since it's a distinct code path/rule from either
bug above.

## Cleanup performed during this investigation

- Debug instrumentation added to `src/agent/loop.py` (temporary
  `reasoning=%r` addition to the existing debug log line) has been
  reverted — the file is back to its pre-investigation state.
- One real calendar event was created on `quarter-barber-dev` during the
  live repro (`85u03j1h5jsjh8evj9k9gbhmas`, "Felipe - 999999999") and has
  been cancelled. Verified via `list_events` that only the two
  pre-existing events from earlier manual testing sessions remain
  (`ked62aom1n0gub1plvafbt2f90` / "Felipe - 658553891",
  `32rq0lf2jnrppc70r0g5uj5ke3` / "Hola - 638242539") — neither created by
  this session, both left untouched.
