# Quarter Barber Agent

AI agent that manages appointment booking via WhatsApp for [Quarter Barber, Gentleman's](https://www.instagram.com/quarterbarber) — a real barbershop. Google Calendar is the single source of truth; no parallel booking system.

**Status**: in development. Currently piloting with a small group of regular customers on a secondary WhatsApp number before considering migration to the shop's main line.


## What it does

- Books, cancels, and reschedules appointments through natural conversation on WhatsApp
- Checks real-time availability directly against Google Calendar
- Answers pricing and service questions grounded in the shop's official price list (RAG)
- Identifies barbers via Google Calendar `colorId`, respecting each barber's schedule and service capabilities


## Why this project

Built to demonstrate end-to-end AI system design — from requirements gathering with a real stakeholder, through architecture decisions, to a deployed service used by real customers. Full requirements and architecture documentation: [`docs/SPEC.md`](docs/SPEC.md).

Built as a follow-up to [`personal-agent`](https://github.com/migueelruiiz/personal-agent), where I implemented an agent loop, tool calling, RAG, and sandboxed code execution from scratch to understand the architecture before building something production-facing.


## Stack

LLM (Groq/Llama for dev, Claude Haiku for production) · FastAPI · Google Calendar API · Twilio (WhatsApp) · ChromaDB + sentence-transformers (RAG)

---

Developed with Claude Code as a development assistant, under my own architecture and design decisions — see [`docs/SPEC.md`](docs/SPEC.md) for the full requirements and engineering process.
