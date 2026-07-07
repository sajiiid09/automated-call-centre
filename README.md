# AI Automated Call Center (MVP)

An AI-powered call center: inbound and outbound voice calls handled by an AI agent, plus a web dashboard for managing campaigns, contacts, and reviewing call transcripts and dispositions.

**Status:** Demo-ready without Twilio. Voice calls run in the browser (WebRTC) through the real Deepgram + Gemini pipeline; campaigns use a simulated dialer. Twilio is the only missing piece — see [TWILIO_INTEGRATION.md](TWILIO_INTEGRATION.md) for integration day and [DEMO.md](DEMO.md) for the demo runbook. Roadmap: [PLAN.md](PLAN.md).

## Stack

| Layer | Tech |
|---|---|
| Telephony | Twilio (Media Streams over WebSocket), UK number — adapter written, dormant until keys arrive |
| Demo voice transport | Browser web-call via WebRTC (Pipecat SmallWebRTC) |
| Voice orchestration | [Pipecat](https://github.com/pipecat-ai/pipecat) (Python) |
| STT / TTS | Deepgram (Nova STT, Aura TTS) |
| LLM | Google Gemini (Flash Lite, free tier) |
| Backend | FastAPI + SQLAlchemy |
| Database | PostgreSQL 16 (Docker) |
| Frontend | Next.js + Tailwind + shadcn/ui |
| Demo deploy | Local machine + ngrok tunnel |

Rationale and alternatives: [ARCHITECTURE.md](ARCHITECTURE.md). Vendor choices are **locked** there unless explicitly reopened.

## Prerequisites

- Python 3.11+
- Node.js 20+
- Docker + Docker Compose
- [ngrok](https://ngrok.com/) account (free)
- Accounts/API keys: Twilio, Deepgram, Google AI Studio (Gemini)

## Setup

```bash
# 1. Database
docker compose up -d db

# 2. Backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e .
cp ../.env.example ../.env   # then fill in keys
uvicorn app.main:app --reload --port 8000

# 3. Frontend (separate terminal)
cd frontend
npm install
npm run dev                  # http://localhost:3001

# 4. Tunnel for Twilio webhooks (Phase 3+)
ngrok http 8000
```

Health check: `curl http://localhost:8000/health` → `{"status":"ok"}`.

## Environment Variables

Defined in `.env` at repo root (copy from `.env.example`). Never commit `.env`.

| Variable | Purpose | Needed from phase |
|---|---|---|
| `DATABASE_URL` | Postgres connection string | 2 |
| `TWILIO_ACCOUNT_SID` | Twilio account SID | 3 |
| `TWILIO_AUTH_TOKEN` | Twilio auth token | 3 |
| `TWILIO_PHONE_NUMBER` | Purchased Twilio number (E.164, e.g. +44…) | 3 |
| `DEEPGRAM_API_KEY` | Deepgram STT + TTS | 3 |
| `GEMINI_API_KEY` | Google AI Studio key | 3 |
| `PUBLIC_BASE_URL` | ngrok https URL, used in Twilio webhooks/TwiML | 3 |

## Repo Layout

```
backend/    FastAPI app (REST API + Twilio webhooks)
agent/      Pipecat voice pipeline (STT → LLM → TTS)
frontend/   Next.js dashboard
docs:       ARCHITECTURE.md · DESIGN.md · PLAN.md · AGENTS.md
```

## Known Demo Constraints

- **Twilio trial:** outbound calls only to verified numbers; calls start with a trial announcement. Upgrading to pay-as-you-go (~$20) removes both.
- **UK numbers require a Twilio regulatory bundle** (address/ID proof) which can take days to approve — submit early; use a US number as interim fallback.
- No dashboard auth (local demo only). English only. Single-machine deployment.
