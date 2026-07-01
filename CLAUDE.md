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
Google Calendar API / ChromaDB RAG / Session memory
```

**Tools to implement:**
- `check_availability` — query Google Calendar for free 30-min (or other time if the service requires it) slots within each barber's configured working hours (see barbers_config — pending stakeholder confirmation)
- `book_appointment` — create event in Google Calendar
- `cancel_appointment` — cancel existing event
- `reschedule_appointment` — cancel + create, or move existing event
- `search_business_info` — RAG over business documents (price list, services)

---

## Project structure

```
quarter-barber-agent/
├── docs/
│   └── SPEC.md               ← full requirements and architecture
├── src/
│   ├── agent/                ← ReAct loop, agent class
│   ├── tools/                ← one file per tool
│   ├── calendar/             ← Google Calendar API integration
│   ├── whatsapp/             ← Twilio integration
│   ├── rag/                  ← ChromaDB + sentence-transformers
│   └── memory/               ← session memory (per phone number)
├── knowledge_base/           ← RAG source documents (price list, etc.)
├── tests/
├── api.py                    ← FastAPI entrypoint
├── .env                      ← never commit this
└── requirements.txt
```

---

## Stack

| Component | Technology |
|---|---|
| LLM | Groq / Llama 3.3 70b for dev → Claude Haiku for production |
| Backend | FastAPI + Uvicorn |
| Calendar | Google Calendar API |
| WhatsApp | Twilio (WhatsApp Business API) |
| RAG | ChromaDB + sentence-transformers |
| Hosting | Render or Railway |
| Memory | Persistent per session (`session_id` = phone number) |

---

## Key decisions and constraints

**Session management is required** Multiple real customers talk to the agent simultaneously — each conversation is independent, identified by phone number.

**Google Calendar is the only source of truth.** Never create a parallel database of appointments. All reads and writes go through the Calendar API.

**Barbers are identified by `colorId`** on Google Calendar events. The mapping between colorId and barber name lives in structured config (`barbers_config`), not in the prompt or RAG. If a wrong colorId is used, a real booking goes to the wrong barber — this must be config, not free text.

**Never fabricate information.** If a tool fails or returns no data, the agent must say so explicitly. It must never guess prices, availability, or barber schedules. This is a real business with real customers.

**Slot re-verification before write:** always check availability immediately before creating a calendar event to prevent double-booking race conditions.

**RAG is for business information only** (prices, services, address, policies). Appointment logic (availability, booking) always goes through the Calendar API — never through RAG or model knowledge.

---

## Open questions (pending stakeholder confirmation)

- [ ] Is there one shared Google Calendar account for all 4 barbers, or one per barber?
- [ ] Exact `colorId` mapping per barber (must be verified empirically via Calendar API, not assumed)
- [ ] Working hours per barber (individual schedules may differ)
- [ ] Service durations per service type (corte, barba, corte + barba, color, mechas, etc.)
- [ ] Fallback rule for barber assignment when client has no preference and no history

---

## Repository

Do not create any commit. The repository will be managed by Miguel 

---

## Prior learning context

Miguel built `personal-agent` (https://github.com/migueelruiiz/personal-agent) before this project — a from-scratch agent with ReAct loop, tool calling, RAG, sandboxed code execution, and FastAPI, using Groq + Llama. That project was for learning the architecture. This one is the real deployment. Reuse patterns from there where applicable, but this is a separate codebase.

**Known issue from personal-agent worth noting:** Llama 3.3 70b on Groq sometimes generates tool calls in a legacy XML format (`<function=name{...}>`) instead of standard JSON, causing `400 tool_use_failed` errors. A parser fallback was implemented there. Be aware this may appear here too.