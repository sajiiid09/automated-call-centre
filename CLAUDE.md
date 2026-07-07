# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

AI automated call center MVP: inbound/outbound voice calls handled by an AI agent (Twilio → Pipecat → Deepgram STT/TTS + Gemini), FastAPI + Postgres backend, Next.js + shadcn/ui dashboard. Built in gated phases — **do not start a later phase before the current one is reviewed** (see PLAN.md).

**Vendor/framework choices are locked in ARCHITECTURE.md** — never swap providers or frameworks unless the owner explicitly reopens the decision there. Full conventions and repo map: AGENTS.md. API contract, DB schema, and screens: DESIGN.md.

## Commands

```bash
docker compose up -d db                                  # Postgres 16
cd backend && source .venv/bin/activate
uvicorn app.main:app --reload --port 8000                # backend (health: /health)
pytest                                                   # backend tests
pytest tests/test_x.py::test_name                        # single test
ruff check . && ruff format --check .                    # backend lint/format
cd frontend && npm run dev                               # dashboard :3001
npm run lint && npx tsc --noEmit                         # frontend checks
ngrok http 8000                                          # public URL for Twilio webhooks (Phase 3+)
```

## Architecture in one paragraph

One shared Pipecat pipeline (`agent/pipeline.py`: Deepgram Nova STT → Gemini Flash Lite → Deepgram Aura TTS) is fed by two transports: browser web-calls over WebRTC (`POST /api/webrtc/offer`, the demo path — dashboard call widget) and a dormant, live-untested Twilio adapter (`/twilio/inbound` TwiML → `/twilio/media` Media Streams WebSocket, REST origination for outbound) that activates when Twilio env vars are set. Campaigns run a sequential simulated dialer: the next pending contact is answered as a web-call from the campaign page, and Gemini tags a disposition after each call (`services/dialer.py`, `services/disposition.py`). The pipeline persists call rows and transcript turns through the FastAPI backend (`backend/app/`, routers thin, logic in `services/`) into Postgres; the Next.js dashboard consumes `/api/*`. All env vars are read only in `backend/app/config.py`. DB schema changes only via Alembic migrations (never edit applied ones). Phone numbers E.164; UUID PKs.
