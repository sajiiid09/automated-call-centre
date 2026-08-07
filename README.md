# AI Automated Call Center (MVP)

An AI-powered call center: inbound and outbound voice calls handled by an AI agent, plus a web dashboard for managing campaigns, contacts, and reviewing call transcripts and dispositions.

**Status:** Demo-ready. Voice calls run in the browser (WebRTC) through the real Deepgram + Gemini pipeline, live-verified end to end. Real Twilio PSTN dialing — inbound, outbound, and sequential campaign dialing — is **implemented and unit-tested**, and switches on by configuration alone; **no real phone call has been placed yet**, so it is not live-verified. Remaining steps: [TWILIO_INTEGRATION.md](TWILIO_INTEGRATION.md).

Real dialing is **fail-closed**: nothing is dialed until `OUTBOUND_ALLOWLIST` names the permitted numbers.

**New here?** Start with [PROCEDURE.md](PROCEDURE.md) — how the system works and how to use it. Demo script: [DEMO.md](DEMO.md). Roadmap: [PLAN.md](PLAN.md).

## Stack

| Layer | Tech |
|---|---|
| Telephony | Twilio (Media Streams over WebSocket), UK number — implemented, not yet live-verified |
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
| `TWILIO_AUTH_TOKEN` | Twilio auth token + webhook signature checks | real phone calls |
| `TWILIO_PHONE_NUMBER` | Purchased Twilio number (E.164, e.g. +44…) | real phone calls |
| `PUBLIC_BASE_URL` | ngrok **https** URL for Twilio webhooks/TwiML | real phone calls |
| `OUTBOUND_ALLOWLIST` | E.164 numbers that may be dialed — **fail-closed** | real phone calls |

Optional: `DIALER_MODE` (`auto`/`simulated`/`twilio`), `MAX_OUTBOUND_CALLS_PER_DAY` (20), `DIAL_POLL_SECONDS`, `DIAL_RING_TIMEOUT_SECONDS`, `DIAL_STALE_CALL_SECONDS`.

Without `DEEPGRAM_API_KEY` and `GEMINI_API_KEY`, `POST /api/webrtc/offer` returns **503** and no call can start. Restart the backend after editing `.env`.

**Simulated vs real dialing is decided by configuration.** With `DIALER_MODE=auto` (the default), campaigns dial real phones only when Twilio credentials, a phone number, and an `https://` `PUBLIC_BASE_URL` are all present; otherwise a human answers each campaign call in the browser. The demo therefore works unchanged on any machine.

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

- **Twilio has never handled a real call.** Credentials verified and the code is tested, but live verification is outstanding.
- **Single worker only** — the campaign dial supervisor is one task per process.
- **Twilio trial:** outbound calls only to verified numbers; calls start with a trial announcement. Upgrading to pay-as-you-go (~$20) removes both.
- **UK numbers require a Twilio regulatory bundle** (address/ID proof) which can take days to approve — submit early; use a US number as interim fallback.
- No dashboard auth (local demo only). English only. Single-machine deployment.
