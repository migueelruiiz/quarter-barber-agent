# CLAUDE.md — Quarter Barber Agent

Context file for Claude Code sessions. Read this before making any changes.

---

## What this project is

AI agent that manages appointment booking via WhatsApp for **Quarter Barber, Gentleman's** (Calle Abtao Nº 4, Madrid) — a real barbershop owned by a friend. This is a real deployment, not a demo.

Google Calendar is the **single source of truth** for appointments. No parallel booking system exists — the agent reads and writes directly to the barbershop's existing calendar.

This is Miguel's main portfolio project to apply for AI/ML Engineer and Software Engineer positions in NYC.

---

## Architecture overview

```
Customer (WhatsApp)
        ↓
Twilio (WhatsApp Business API)
        ↓
FastAPI backend (cloud — Render or Railway)
        ↓
Agent (ReAct loop + tools)
        ↓
Google Calendar API / Session memory
```

**Tools to implement:**
- `check_availability` — query Google Calendar for free 30-min (or other time if the service requires it) slots within each barber's configured working hours (see barbers_config in config.py)
- `book_appointment` — create event in Google Calendar
- `cancel_appointment` — cancel existing event
- `reschedule_appointment` — cancel + create, or move existing event

---

## Project structure

```
quarter-barber-agent/
├── docs/
│   └── quarter_barber_spec.md     ← this document
├── src/
│   ├── agent/                     ← ReAct loop, agent class
│   ├── calendar/                  ← Google Calendar API integration
│   ├── memory/                    ← session memory (per phone number)
│   ├── tools/                     ← one file per tool
│   └── whatsapp/                  ← Twilio integration
├── tests/
├── .env
├── .gitignore
├── config.py                      ← SERVICES, BARBERS dicts + render functions
├── CLAUDE.md
├── credentials.json
├── README.md
├── requirements.txt
├── token.json
└── api.py
```

**Import convention:** always use full path imports from `src.calendar` to avoid conflict with Python's built-in `calendar` stdlib module. Example: `from src.calendar.queries import list_events`.

---

## Stack

| Component | Technology |
|---|---|
| LLM | openai/gpt-oss-120b for dev → Claude Haiku for production |
| Backend | FastAPI + Uvicorn |
| Calendar | Google Calendar API |
| WhatsApp | Twilio (WhatsApp Business API) |
| Hosting | Render or Railway |
| Memory | Persistent per session (`session_id` = phone number) |

---

## Key decisions and constraints

**Session management is required** Multiple real customers talk to the agent simultaneously — each conversation is independent, identified by phone number.

**Google Calendar is the only source of truth.** Never create a parallel database of appointments. All reads and writes go through the Calendar API.

**Barbers are identified by `colorId`** on Google Calendar events. The mapping between colorId and barber name lives in structured config (`barbers_config`), not in the prompt. If a wrong colorId is used, a real booking goes to the wrong barber — this must be config, not free text.

**Never fabricate information.** If a tool fails or returns no data, the agent must say so explicitly. It must never guess prices, availability, or barber schedules. This is a real business with real customers.

**Slot re-verification before write:** always check availability immediately before creating a calendar event to prevent double-booking race conditions.

**Business information delivery:** prices, services, address, and policies are injected into the system prompt as a cached context prefix generated from `src/config.py` at load time. Appointment logic (availability, booking) always goes through the Calendar API — never from model memory.

**Price presentation — no profile inference:** when a customer asks about a price, the agent presents the full set of listed variants for that category (e.g. all haircut price variants, mirroring the business's own price list layout) rather than asking the customer's age or profile to select one. This matches the business's existing in-person, honor-based pricing and avoids the agent making an unverifiable assumption about the customer. Combo prices (e.g. corte + barba) are never a stored value — they are the sum of the customer's selected component prices, computed at response time; see config.py PRICE_MENU for the confirmed no-discount policy.

**Google Calendar colorId:** barber colors come from the `event` color map returned by `colors.get()`, not the `calendar` map. colorId values are strings (e.g. "7", "10", "11").

**Null colorId handling:** events with colorId null inherit the calendar's default color visually, but the API returns null — not the default colorId. The default barber is Juan (peacock blue is the calendar's default color, meaning his events are expected to carry colorId == null rather than an explicit value). This still requires empirical verification via colors.get + manual event inspection before being hardcoded — a named color from the owner is not equivalent to a confirmed API value. check_slot_available must match null explicitly when querying availability for the default barber.

**Known limitation — expanded color palette invisible to API v3 (confirmed July 2026):** Google Calendar's June 2026 rollout expanding event colors from 11 to 24 defaults plus up to 200 custom RGB colors is not exposed through the public Calendar API v3, at least for the account types tested (personal Gmail). Confirmed empirically: an event colored with a new-palette color (outside the classic 11) has no `colorId` key and no other new key in its raw JSON response, with or without the undocumented `eventLabelVersion=1` parameter — it is indistinguishable via API from a true default/no-color event.

Operational constraint: barbers must only use one of the 11 classic colors when assigning their event color — the four current assignments (Rafa=Basil/10, Yuri=Tangerine/6, Dylan=Blueberry/9, Juan=default/null) are all within this safe set. If any barber's color is ever reassigned to a color from the new palette, their events will silently be read as belonging to the default barber (Juan), risking double-booking. This must be communicated to the owner as a process constraint, not just a technical footnote — no code-side detection is possible given current API behavior.

**Bleaching duration:** book the maximum documented duration for any service combination including a bleaching treatment, not the typical duration — actual duration is hair-dependent and the calendar event is the only record of barber availability (R-16). The barber can shorten the event manually afterward; this is reflected automatically in the next availability check, no agent logic needed.

**Bleaching eligibility:** only Dylan and Juan are configured as eligible. No separate priority rule is needed to prefer Dylan — filtering the general seniority fallback order (Dylan, Yuri, Rafa, Juan) by service eligibility already yields Dylan first.

**Out-of-hours exceptions:** requests outside configured operating hours or standard rules are not resolved automatically; the agent offers a phone call with the business instead (R-17).

**Barber day-off swaps:** requires checking no existing appointments conflict with the barber's desired new day off before applying the change. Manual config change by the developer for v1; a future owner-facing dashboard may expose this directly (R-18).

---

## Development environment

**Calendar under development:** all development and testing is done against a personal Google Calendar (developer-owned), not the barbershop's calendar. The production calendar is only accessed for a final read-only smoke test once the code is validated, followed by write operations with explicit care.

**Dev OAuth credentials already configured:** `credentials.json` and `token.json` exist in the project root and are valid for the `quarter-barber-dev` calendar. Claude Code sessions must reuse them and must never regenerate credentials or re-run the OAuth consent flow unless explicitly told the token is invalid or expired.

**OAuth authorization from the owner** is required before any access to the production calendar. This is a manual step deferred until the codebase is stable.

---

## Configuration status

Resolved by the stakeholder (see `quarter_barber_spec.md` Section 2):
- [x] One shared Google Calendar account and calendar for all 4 barbers
- [x] Working hours and each barber's individual day off
- [x] Service durations per service type
- [x] Barber assignment fallback rule (seniority order)
- [x] Bleaching service eligibility and duration handling

Still open:
- [ ] Exact `colorId` mapping per barber — must be verified via `colors.get` + 
  manual event inspection, not assumed from named colors

Planned (engineering task, not stakeholder-dependent):
- [ ] Barber day-off swap mechanism (config-level override + conflict check)

---

## Repository

Do not create any commit. The repository will be managed by Miguel 

---

## Prior learning context

Miguel built `personal-agent` (https://github.com/migueelruiiz/personal-agent) before this project — a from-scratch agent with ReAct loop, tool calling, RAG, sandboxed code execution, and FastAPI, using Groq + Llama. That project was for learning the architecture. This one is the real deployment. Reuse patterns from there where applicable, but this is a separate codebase.

**Known issue from personal-agent worth noting:** Llama 3.3 70b on Groq sometimes generates tool calls in a legacy XML format (`<function=name{...}>`) instead of standard JSON, causing `400 tool_use_failed` errors. A parser fallback was implemented there. Be aware this may appear here too.