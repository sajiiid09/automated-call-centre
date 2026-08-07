# AI Automated Call Center (MVP)

An AI-powered call center: inbound and outbound voice calls handled by an AI agent, plus a web dashboard for managing campaigns, contacts, and reviewing call transcripts and dispositions.

**Status:** Demo-ready without Twilio. Voice calls run in the browser (WebRTC) through the real Deepgram + Gemini pipeline; campaigns use a simulated dialer. Deepgram (Nova STT + Aura TTS) and Gemini are live-verified end to end. Twilio credentials are valid and the adapter is written, but **no real PSTN call has been placed yet** — see [TWILIO_INTEGRATION.md](TWILIO_INTEGRATION.md) for integration day.

**New here?** Start with [PROCEDURE.md](PROCEDURE.md) — how the system works and how to use it. Demo script: [DEMO.md](DEMO.md). Roadmap: [PLAN.md](PLAN.md).

## Stack

| Layer | Tech |
|---|---|
| Telephony | Twilio (Media Streams over WebSocket), UK number — adapter written, live-untested |
| Demo voice transport | Browser web-call via WebRTC (Pipecat SmallWebRTC) |
| Voice orchestration | [Pipecat](https://github.com/pipecat-ai/pipecat) (Python 3.12) |
| STT / TTS | Deepgram — Nova STT, Aura TTS (`aura-2-thalia-en`); one key covers both |
| LLM | Google Gemini `gemini-3.5-flash-lite` (free tier) |
| Backend | FastAPI + SQLAlchemy |
| Database | PostgreSQL 16 (Docker, host port 5433) |
| Frontend | Next.js 16 + React 19 + Tailwind 4 + shadcn/ui |
| Demo deploy | Local machine + ngrok tunnel |

Rationale and alternatives: [ARCHITECTURE.md](ARCHITECTURE.md). Vendor choices are **locked** there unless explicitly reopened.

## Prerequisites

- **Python 3.12** — not 3.13+. Pipecat needs `audioop`, removed from the stdlib in 3.13.
- Node.js 20+
- Docker + Docker Compose
- [ngrok](https://ngrok.com/) account (free) — only for Twilio
- API keys: Deepgram, Google AI Studio (Gemini). Twilio only for real phone calls.

## Setup

```bash
# 1. Database
docker compose up -d db

# 2. Backend + agent
cd backend
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]" -e ../agent   # both packages: backend imports agent.pipeline
cp ../.env.example ../.env            # then fill in keys
alembic upgrade head
uvicorn app.main:app --reload --port 8000

# 3. Frontend (separate terminal)
cd frontend
npm install
npm run dev                  # http://localhost:3000

# 4. Tunnel for Twilio webhooks (Phase 6 only)
ngrok http 8000
```

Health check: `curl http://localhost:8000/health` → `{"status":"ok"}`.

Step-by-step walkthrough of what all this does: [PROCEDURE.md](PROCEDURE.md).

## Environment Variables

Defined in `.env` at repo root (copy from `.env.example`). Never commit `.env`.

| Variable | Purpose | Required for |
|---|---|---|
| `DATABASE_URL` | Postgres connection string | everything |
| `DEEPGRAM_API_KEY` | Deepgram STT **and** TTS (one key, both) | any voice call |
| `GEMINI_API_KEY` | Agent replies + post-call dispositions | any voice call |
| `TWILIO_ACCOUNT_SID` | Twilio account SID | real phone calls |
| `TWILIO_AUTH_TOKEN` | Twilio auth token | real phone calls |
| `TWILIO_PHONE_NUMBER` | Purchased Twilio number (E.164, e.g. +44…) | real phone calls |
| `PUBLIC_BASE_URL` | ngrok **https** URL for Twilio webhooks/TwiML | real phone calls |

Without `DEEPGRAM_API_KEY` and `GEMINI_API_KEY`, `POST /api/webrtc/offer` returns **503** and no call can start. Restart the backend after editing `.env`.

## Repo Layout

```
backend/    FastAPI app (REST API + WebRTC signalling + Twilio webhooks)
agent/      Pipecat voice pipeline (STT → LLM → TTS)
frontend/   Next.js dashboard
docs:       PROCEDURE.md · ARCHITECTURE.md · DESIGN.md · PLAN.md · AGENTS.md · DEMO.md · TWILIO_INTEGRATION.md
```

| Doc | What's in it |
|---|---|
| [PROCEDURE.md](PROCEDURE.md) | How the system works and how to use it — start here |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Vendor choices and rationale (**locked**) |
| [DESIGN.md](DESIGN.md) | API contract, DB schema, screens |
| [PLAN.md](PLAN.md) | Phased roadmap and gates |
| [AGENTS.md](AGENTS.md) | Conventions for contributors and AI agents |
| [DEMO.md](DEMO.md) | Client demo runbook |
| [TWILIO_INTEGRATION.md](TWILIO_INTEGRATION.md) | Integration-day checklist (Phase 6, outstanding) |

## Known Demo Constraints

- **Twilio adapter has never handled a real call.** Credentials verified, code written; live testing is Phase 6.
- **Twilio trial:** outbound calls only to verified numbers; calls start with a trial announcement. Upgrading to pay-as-you-go (~$20) removes both.
- **UK numbers require a Twilio regulatory bundle** (address/ID proof) which can take days to approve — submit early; use a US number as interim fallback.
- No dashboard auth (local demo only). English only. Single-machine deployment.
