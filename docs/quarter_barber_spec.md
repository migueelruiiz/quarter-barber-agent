# Quarter Barber Agent — Specification Document

**Proyecto**: AI agent to manage appointments through WhatsApp

**Client/Stakeholder**: Quarter Barber, Gentleman's (Calle Abtao Nº 4)

**Repo**: `https://github.com/migueelruiiz/quarter-barber-agent`

---

## 1. Introduction

### 1.1. Stakeholder and actors involved

| Rol | Who | Objective |
|---|---|---|
| Stakeholder | Quarter Barber's owner | Improve the appointment management without adding operative effort  |
| Final users (employees) | 4 barbers | Still using Google Calendar without new friction. |
| Final users | Reduced group of usual users | Make/cancel appointments without calling or waiting for an answer. |
| Developer / Technical Product owner | Miguel Ruiz | Show design, development and deployment end-to-end skills for a AI system |

### 1.2. Problem analysis

Currently the appointment management is done through call or WhatsApp directly to the business number, with manual annotation in Google Calendar. This method consumes the employees' time in repetitive tasks (confirm availability, annotation, manage cancelations, answering quick questions) that could be automatized without changing the tool they use now.

### 1.3. Objetives (v1 — Pilot)

- Allow to a defined group of customers, make, cancel and reprogram appointments through WhatsApp without the intervention of the employees. 
- Answer automatically to frequent questions about prices and services, based on official information.
- Maintain Google Calendar as the only source of truth - no parallel system with possible desynchronization.
- Do not affect at any moment to the current business' canal and number during the pilot

### 1.4. Out of scope (after v1.0)

- Migration to the current business number
- Public website
- Multi-local support
- Advance analytics dashboard

### 1.5. Stakeholder Configuration — Status

All v1 configuration inputs originally required from the stakeholder have been 
collected (service durations, barber working days, business hours, barber 
service eligibility, and the assignment fallback rule — see Section 2 and 
`config.py`). One item remains open:

- **colorId per barber**: named colors were provided by the owner, but per 
  this project's data-integrity principle, the actual colorId values must be 
  confirmed via the Calendar `colors.get` API and manual inspection of 
  existing events before being hardcoded into `config.py`.

---

## 2. Requirements

| ID | Category | Requirement |
|---|---|---|
| R-1 | Infrastructure | The system shall run as a cloud service, requiring no local installation or dedicated hardware |
| R-2 | Infrastructure | The system shall run on an independent WhatsApp number (Twilio), with no risk to the business's current phone number |
| R-3 | Reliability | The system shall maintain an independent conversation session per client, identified by phone number |
| R-4 | Reliability | The system shall never fabricate information (prices, availability, or business data) not present in verified sources |
| R-5 | Reliability | The system shall explicitly say to the customer that it's not able to resolve the request |
| R-6 | Reliability | The system shall apply basic abuse protection (rate limiting per phone), given the exposure to external users |
| R-7 | Reliability | The system shall double-verify slot availability immediately before creating the calendar event, to prevent double booking from near-simultaneous requests |
| R-8 | Information | The system shall answer pricing and service questions from a verified business knowledge base injected into the model context as a cached prefix, never from model memory. Source of truth for pricing: the business's official Instagram photo (unstructured; no other documentation exists), manually transcribed into `config.py` — there is no automated sync between the Instagram source and the config. |
| R-9 | Logic | The system shall be able to create, cancel, and reschedule appointments directly in Google Calendar |
| R-10 | Logic | The system shall only offer appointment slots that fall within the requesting barber's configured working schedule, accounting for barber-specific working days and hours |
| R-11 | Logic | The system shall check real-time availability against the shared Google Calendar before offering or confirming any appointment slot |
| R-12 | Logic | The system shall determine appointment duration based on the requested service type, rather than assuming a fixed slot length |
| R-13 | Logic | The system shall identify each barber via Google Calendar colorId value, and shall only assign appointments to barbers whose configured service list includes the requested service |
| R-14 | Compliance | The system shall comply with Meta/WhatsApp policies for AI agents in force at deplotment time |
| R-15 | Logic | The system shall assign a barber according to the following precedence: explicit client preference (named barber) → client's most recent barber if conversation history indicates a returning client → deterministic fallback by seniority order (Dylan, Yuri, Rafa, Juan), filtered to barbers eligible for the requested service |
| R-16 | Logic | The system shall reserve the maximum documented duration, rather than the typical duration, when booking services that include a bleaching treatment |
| R-17 | Reliability | The system shall decline to process appointment requests falling outside configured operating hours or standard scheduling rules, offering a phone call with the business as the resolution path |
| R-18 | Infrastructure | The system shall support barber schedule overrides (day-off changes) at the configuration level, requiring manual verification against existing appointments before taking effect |

---

## 3. Technical Aspects 

### 3.1. Arquitecture summary

```
Customer (WhatsApp)
        ↓
Twilio (WhatsApp Business API)
        ↓
Backend (FastAPI, cloud — Render/Railway)
        ↓
Agente (loop ReAct + tools)
        ↓
┌─────────────────────────────────────┐
│ Google Calendar API (unique source  │
│   of truth for appointments)        │
│ config.py — cached system prompt    │
│   prefix (business info, no RAG)    │
│ Memory per session (1 per           │
│    telephone number)                │
└─────────────────────────────────────┘
```

### 3.2. Technical stack

| Component | Tecnology |
|---|---|
| LLM Model | openai/gpt-oss-120b dev → Claude Haiku production |
| Backend | FastAPI + Uvicorn |
| Calendar | Google Calendar API |
| WhatsApp | Twilio (WhatsApp Business API) |
| Hosting | Render o Railway |
| Memory | Session-persistent (`session_id` = telephone) |

### 3.3. Agent Toolkit

| Tool | Function |
|---|---|
| `check_availability` | Check availability from a unique Google Calendar |
| `find_appointments` | Locate a client's existing future appointment(s) by phone and/or name, for cancel/reschedule flows |
| `book_appointment` | Create events in Google Calendar (customer, service, barber(indicated by color)) |
| `cancel_appointment` | Cancel existing events, free up spots |
| `reschedule_appointment` | Cancel + create events, or move an existing event |

### 3.4. Memory and sessions

**`session_id` is necessary** - each actual customer needs an independent conversation, identified by a telephone number. Each session maintain its own historic records.

### 3.5. Knowledge base

All structured business data (services, prices, durations, barber eligibility per service, colorId-to-barber mapping, working hours) is maintained in `src/config.py` as the single source of truth. This config is consumed by both the booking logic (durations, eligibility, colorId) and the analytics layer (prices). The agent's system prompt receives a string projection generated from this config at load time as a cached prefix. No embedding model or vector database is required at this scale; if the knowledge base grows substantially post-v1.0, retrieval-augmented generation can be introduced at that point.

### 3.6. Analitic layer for the owner

Each appointment goes through the agent and is registered in Google Calendar with structured data so it can be created without asking for anything else to the business:

- Recurring ingestion script: reads Google Calendar API -> load data into a database for analysis (this is the proyect's data engineering component)
- Metrics: stimated income, most demanded services, cancelation rate, peak hours, etc.
- Simple initial output: weekly report (email or pdf) - without complex dashboard in v1.0.

### 3.7. Security and reliability

Because of talking with real owned customers:

- Rate limiting per telephone number.
- Explicit politic about not making up information (prices, availability) - fails management pattern (detect an actual fail vs. making up a plausible answer).
- Explicit fallback: if the agent can't solve something, it must indicate that a human will contact or it'll offer a phone call as an alternative solution - never stuck in a loop with a real customer. 

### 3.8. WhatsApp

1. **Development**: Twilio Sandbox (free, only the developer and the owner testing it)
2. **Production, software pilot**: real Twilio number, after Meta business verification (2-7 days, requires legal business information). With no friction for final customer - works normal as WhatsApp.
3. **Future, after v1.0**: migration to the current number to the business API if the owner decides it.

**Important 

### 3.9. Account management

- **Google Cloud**: developer's account (free, "testing" mode, up to 100 authorized users - enough for the pilot). The owner just needs to authorize the acces to the Calendar through OAuth. 
Development and testing will be performed against a developer-owned Google Calendar to avoid any risk to live barbershop data. The owner's calendar will only be accessed via OAuth once the integration layer is fully validated.
- **Twilio**: developer's account for developing and sandbox. The Meta business verification for production number will require sensitive information when the moment arrives.
- **Render/Railway**: developer's account, with no relation to the business. 

### 3.10. Repository structure

Flat structure (no domain-based subfolders), because of YAGNI principle - it'll be restructured whenever a second service exists.

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
