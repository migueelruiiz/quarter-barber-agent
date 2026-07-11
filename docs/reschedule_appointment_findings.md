# `reschedule_appointment` — Integration Test Findings

Source: manual integration test against the real `quarter-barber-dev` calendar
(2026-07-10/11), steps 4 and 6 of the test plan. All calendar events created
during this investigation were deleted afterward; the calendar was confirmed
clean before and after (`API Null Color Test A/B` on 2026-07-12 predate this
session and were left untouched).

This is investigation only — no changes were made to `reschedule_appointment.py`
or `queries.py`.

---

## Bug 1 — `colorId` not cleared when rescheduling to Juan

**Root cause (already known):** `events().patch()` only updates keys present
in the request body — omitted keys are left untouched, unlike `insert()`
where an omitted key simply means "no value" for a new resource. `patch_event`
currently omits the `colorId` key entirely when `color_id is None`, copying
the convention from `create_event` (correct for insert, wrong for patch/update).
Effect: reassigning an appointment to Juan (`color_id=None`) reports success
but the event keeps its old barber's `colorId`, so it's still misread as
belonging to the old barber by every `colorId`-based lookup
(`check_availability`, `find_appointments`).

### Empirical findings

Created a real event with `colorId: "10"` (Rafa), then sent a raw `patch`
request with `colorId` **explicitly set to `null`** in the body (bypassing
`patch_event`'s current omit-when-`None` logic):

```json
{
  "start": {"dateTime": "2026-07-11T09:00:00+02:00"},
  "end": {"dateTime": "2026-07-11T09:30:00+02:00"},
  "colorId": null
}
```

Raw `patch` response (`colorId` key absent):

```json
{
  "kind": "calendar#event",
  "id": "7v1n7ibqcb11esk3ga1146beok",
  "status": "confirmed",
  "summary": "Bug1 Investigation - Rafa",
  "start": {"dateTime": "2026-07-11T09:00:00+02:00", "timeZone": "America/New_York"},
  "end": {"dateTime": "2026-07-11T09:30:00+02:00", "timeZone": "America/New_York"},
  "sequence": 0
}
```

A follow-up independent `events().get()` on the same event confirmed the
stored state matches the patch response exactly: no `colorId` key present.

**Conclusion:** explicitly sending `"colorId": null` in the patch body genuinely
clears the field — the stored event ends up with no `colorId` key at all,
which is exactly the null-colorId convention already used for Juan everywhere
else in this codebase (`create_event`, `check_slot_available`, `BARBERS["juan"]`).
The API does not reject `null` or store anything unexpected. No fallback
mechanism (e.g. a separate "unset field" API) is needed — the direct approach
works as hoped, so there's nothing further to investigate here.

### Proposed fix

In `patch_event`, always include `colorId` in the body — never omit it:

```python
body = {
    "start": {"dateTime": start.isoformat()},
    "end": {"dateTime": end.isoformat()},
    "colorId": color_id,
}
```

(i.e. drop the `if color_id is not None:` guard that currently exists only
for `create_event`'s insert-time convention; it should not be copied to
`patch_event`.)

**Tradeoffs:** none of substance — this is strictly more correct than the
current behavior and requires no extra API call. The only behavior change is
that rescheduling *to* Juan will now actually clear `colorId`, which is the
intended behavior per spec. Existing non-Juan reschedules are unaffected
(`color_id` is already a non-`None` string in those cases, so the body
already included the key).

---

## Bug 2 — patching a cancelled event succeeds silently instead of `not_found`

**Root cause (already known):** `cancel_appointment`/`delete_event` doesn't
purge the event immediately — Calendar marks it `status: "cancelled"` and
keeps it retrievable via `events().get()` and, as this investigation confirms,
patchable via `events().patch()`. Cancelled events are excluded from
`list_events` results by default (`singleEvents=True` with default
`showDeleted=False`), so this is not a double-booking risk — but
`reschedule_appointment` assumed (by analogy with `delete_event`, which does
raise 404/410 on a second delete) that `patch_event` would raise `HttpError`
404/410 on an already-cancelled event. It doesn't. The `patch` succeeds,
genuinely mutates `start`/`end`/`colorId` on the cancelled resource, and
`reschedule_appointment` returns `{"success": True, ...}` for an appointment
the client no longer has.

### Empirical findings

Two real events were created and patched, to compare the raw `patch` response
shape directly (not a separate `get()` call):

**Case A — normal, non-cancelled event, patched to a new time:**

```json
{
  "kind": "calendar#event",
  "id": "qa18j9u58fvn3g57afte0ddb34",
  "status": "confirmed",
  "summary": "Bug2 Investigation - Normal",
  "colorId": "9",
  "start": {"dateTime": "2026-07-13T09:00:00+02:00", "timeZone": "America/New_York"},
  "end": {"dateTime": "2026-07-13T09:30:00+02:00", "timeZone": "America/New_York"},
  "sequence": 1
}
```

**Case B — event cancelled via `delete_event`, then patched to a new time:**

```json
{
  "kind": "calendar#event",
  "id": "4825difs53hftpijfjpeboco9c",
  "status": "cancelled",
  "summary": "Bug2 Investigation - Cancelled",
  "colorId": "9",
  "start": {"dateTime": "2026-07-13T09:30:00+02:00", "timeZone": "America/New_York"},
  "end": {"dateTime": "2026-07-13T10:00:00+02:00", "timeZone": "America/New_York"},
  "sequence": 2
}
```

Side by side:

| | Case A (normal) | Case B (cancelled) |
|---|---|---|
| `status` in patch response | present, `"confirmed"` | present, `"cancelled"` |
| `start`/`end`/`colorId` mutated | yes | yes (silently, on a dead event) |
| `HttpError` raised | no | no |

**Conclusion:** the `patch` call's own response — no separate `events().get()`
lookup — already includes `status`, and it reliably distinguishes the two
cases (`"confirmed"` vs `"cancelled"`). `reschedule_appointment` can detect
this case for free from the return value `patch_event` already produces; no
extra API call is required.

(Note as an aside, not part of this bug: attempting to delete an
already-cancelled event a second time — as cleanup for Case B did — correctly
raises `HttpError` 410, consistent with the existing documented
`cancel_appointment` behavior in `CLAUDE.md`. Only `patch` on a cancelled
event is the surprising case; `delete` on one is not.)

### Proposed fix

After calling `patch_event` in `reschedule_appointment`, check the returned
resource's `status`:

```python
patched = patch_event(CALENDAR_ID, event_id, color_id, new_start, new_end)
if patched.get("status") == "cancelled":
    return {"success": False, "reason": "not_found"}
```

This replaces (or supplements) the current assumption that a cancelled event
is only detectable via a raised `HttpError` 404/410 on `patch`.

**Tradeoffs:**
- **Zero extra API calls** — the check uses data already returned by the
  `patch_event` call that has to happen anyway. This is strictly cheaper than
  the alternative of doing a defensive `events().get()` before every patch
  (which would cost one extra API call on *every* reschedule, including the
  common non-cancelled case, just to guard against a rare edge case).
- The 404/410 `HttpError` handling in `reschedule_appointment` should stay as
  a separate, additional path — it's still the right behavior for a
  never-existed `event_id` (confirmed in step 7 of the integration test:
  raw HTTP 404). This fix adds a second, distinct failure path for the
  cancelled-but-still-resolvable case; it doesn't replace the existing one.

---

## Open questions for Miguel

None — both empirical questions posed for this investigation resolved
cleanly with straightforward fixes and no unexpected API behavior beyond the
two bugs themselves.
