# Phased Roadmap

Each phase ships one working, demoable module. **Gate: owner reviews and approves before the next phase starts.** No big-bang build.

## Phase 1 — Docs + scaffold + running frontend  ✅ current

**Goal:** Repo skeleton, all docs, dashboard shell running.

**Scope:**
- README, ARCHITECTURE, DESIGN, PLAN, AGENTS docs
- FastAPI backend boots with `/health`
- `docker-compose.yml` with Postgres 16
- Next.js + shadcn/ui app: sidebar nav, 4 placeholder pages (Dashboard, Campaigns, Contacts, Calls) with empty states
- `.env.example`, `.gitignore`, initial commit

**Out of scope:** any voice code, any real data, DB models.

**Done when:** `uvicorn` serves `/health`; `npm run dev` shows navigable dashboard shell; Postgres container healthy; docs complete with no TBDs.

**Also start now (external, slow):** Twilio UK regulatory bundle submission — approval takes days.

## Phase 2 — Data layer + dashboard CRUD

**Goal:** Manage contacts and campaigns end-to-end in the browser.

**Scope:** SQLAlchemy models + Alembic migrations (schema in DESIGN.md); contacts + campaigns REST endpoints; CSV contact import; frontend pages wired to API (contact table + add dialog + import, campaign create form + list).

**Out of scope:** anything call-related, campaign start/stop.

**Done when:** create/search/import contacts and create a campaign with selected contacts, all from the browser, persisted in Postgres.

## Phase 3 — Inbound voice agent

**Goal:** Phone the Twilio number, converse with the AI agent, read the transcript in the dashboard.

**Scope:** Pipecat pipeline (Deepgram Nova STT → Gemini Flash Lite → Deepgram Aura TTS); Twilio Media Streams WebSocket bridge; `/twilio/inbound` + `/twilio/status` webhooks via ngrok; `calls` + `transcript_turns` persisted; Calls page + transcript view live.

**Out of scope:** outbound, dispositions, per-campaign prompts.

**Done when:** a real inbound call holds a coherent conversation with interruption handling, and its transcript appears in the dashboard.

## Phase 4 — Outbound campaigns + dispositions

**Goal:** Launch a campaign from the dashboard; agent calls contacts and records outcomes.

**Scope:** Twilio REST call origination reusing the same pipeline; sequential dialer (one call at a time); campaign start/stop; post-call LLM disposition tagging (`interested | not_interested | callback | voicemail | failed`) + one-line summary; campaign progress in dashboard.

**Out of scope:** parallel dialing, answer-machine detection, retries/scheduling.

**Done when:** starting a campaign dials a verified number, the agent follows the campaign script, and disposition + transcript show in the dashboard.

## Phase 5 — Demo polish

**Goal:** Clean full client demo run-through.

**Scope:** per-campaign prompt tuning (goal/script fields drive agent behavior); barge-in tested; failure handling (failed calls marked, dialer continues); dashboard stat cards (`/api/stats`); demo runbook (script of what to show, reset steps).

**Out of scope:** production items listed in ARCHITECTURE.md "Scaling Past the Demo".

**Done when:** end-to-end demo (inbound call → outbound campaign → review transcripts/dispositions) runs clean twice in a row.
