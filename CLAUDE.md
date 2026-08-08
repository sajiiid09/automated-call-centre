# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

AI automated call center MVP: inbound/outbound voice calls handled by an AI agent (Twilio → Pipecat → Deepgram STT/TTS + Gemini), FastAPI + Postgres backend, Next.js + shadcn/ui dashboard. Built in gated phases — **do not start a later phase before the current one is reviewed** (see PLAN.md).

**Vendor/framework choices are locked in ARCHITECTURE.md** — never swap providers or frameworks unless the owner explicitly reopens the decision there. Full conventions and repo map: AGENTS.md. API contract, DB schema, and screens: DESIGN.md. How the system works end to end and how to run it: PROCEDURE.md.

## Commands

```bash
docker compose up -d db                                  # Postgres 16 + pgvector, host port 5433
cd backend && source .venv/bin/activate
uvicorn app.main:app --reload --port 8000                # backend (health: /health)
pytest                                                   # backend tests
pytest tests/test_x.py::test_name                        # single test
ruff check . && ruff format --check .                    # backend lint/format
cd frontend && npm run dev                               # dashboard :3000
npm run lint && npx tsc --noEmit                         # frontend checks
ngrok http 8000                                          # public URL for Twilio webhooks (Phase 6)
```

Env setup: **Python 3.12** (3.13+ removed `audioop`, which Pipecat needs) and
`pip install -e ".[dev]" -e ../agent` — both packages, or `import agent` fails.

## Architecture in one paragraph

One shared Pipecat pipeline (`agent/pipeline.py`: Deepgram Nova STT → Gemini `gemini-3.5-flash-lite` → Deepgram Aura TTS; the model name is pinned in both `agent/pipeline.py` and `services/disposition.py` and must be changed in both) is fed by two transports: browser web-calls over WebRTC (`POST /api/webrtc/offer`, the demo path — dashboard call widget) and a Twilio adapter (`/twilio/inbound` TwiML → `/twilio/media` Media Streams WebSocket, REST origination for outbound; signature-verified) that is implemented but not yet live-verified. `telephony.dialing_mode()` picks simulated vs real from configuration alone — real requires credentials, a number, and an `https://` `PUBLIC_BASE_URL`. Campaigns dial sequentially either way: simulated means the next pending contact is answered as a web-call from the campaign page; real means the background supervisor (`services/campaign_runner.py`, the only server-initiated actor, one per process) claims a contact and places a PSTN call, with terminal Twilio status callbacks advancing the queue. Outbound `calls` rows are created at origination and adopted by the media stream, so unanswered calls still advance. Real dialing is fail-closed behind `OUTBOUND_ALLOWLIST`. Gemini tags a disposition after **every** finished call (`services/disposition.py`), picking its vocabulary from `call.direction` — inbound gets CX labels (`resolved | needs_followup | complaint | enquiry | abandoned`), outbound keeps the sales ones; only campaign advancement is still gated on `is_campaign_call`. The agent's company knowledge lives in a pgvector-backed knowledge base (`services/knowledge.py`, `services/knowledge_ingest.py`, `/api/knowledge/*`, dashboard `/knowledge`): uploaded PDFs/TXT/MD are chunked and embedded through an OpenAI-compatible endpoint, and on each caller turn `agent/faq_gate.py` — a frame processor between the user aggregator and the LLM — embeds the utterance once and either speaks a matching FAQ verbatim (dropping the `LLMContextFrame`, which bypasses Gemini entirely) or injects the top document chunks as a marked system message. Identity lives in the single-row `agent_profile` table, which also supplies the deterministic greeting. Everything on that path is fail-open under `KB_TURN_TIMEOUT_SECONDS`. The pipeline persists call rows and transcript turns through the FastAPI backend (`backend/app/`, routers thin, logic in `services/`) into Postgres; the Next.js dashboard consumes `/api/*`. All env vars are read only in `backend/app/config.py`. DB schema changes only via Alembic migrations (never edit applied ones). Phone numbers E.164; UUID PKs (except `agent_profile`, a config singleton pinned to `id = 1`).
