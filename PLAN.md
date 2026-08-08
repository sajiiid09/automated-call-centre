# Phased Roadmap

Each phase ships one working, demoable module. **Gate: owner reviews and approves before the next phase starts.** No big-bang build.

## Phase 1 — Docs + scaffold + running frontend  ✅ done

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

## Phase 2 — Data layer + dashboard CRUD  ✅ done

**Goal:** Manage contacts and campaigns end-to-end in the browser.

**Scope:** SQLAlchemy models + Alembic migrations (schema in DESIGN.md); contacts + campaigns REST endpoints; CSV contact import; frontend pages wired to API (contact table + add dialog + import, campaign create form + list).

**Out of scope:** anything call-related, campaign start/stop.

**Done when:** create/search/import contacts and create a campaign with selected contacts, all from the browser, persisted in Postgres.

## Phase 3 — Voice agent (browser web-call; Twilio inbound pending keys)  ✅ done

**Goal:** Phone the Twilio number, converse with the AI agent, read the transcript in the dashboard.

**Scope:** Pipecat pipeline (Deepgram Nova STT → Gemini Flash Lite → Deepgram Aura TTS); Twilio Media Streams WebSocket bridge; `/twilio/inbound` + `/twilio/status` webhooks via ngrok; `calls` + `transcript_turns` persisted; Calls page + transcript view live.

**Out of scope:** outbound, dispositions, per-campaign prompts.

**Done when:** a real inbound call holds a coherent conversation with interruption handling, and its transcript appears in the dashboard.

## Phase 4 — Outbound campaigns + dispositions (simulated dialer)  ✅ done

**Goal:** Launch a campaign from the dashboard; agent calls contacts and records outcomes.

**Scope:** Twilio REST call origination reusing the same pipeline; sequential dialer (one call at a time); campaign start/stop; post-call LLM disposition tagging (`interested | not_interested | callback | voicemail | failed`) + one-line summary; campaign progress in dashboard.

**Out of scope:** parallel dialing, answer-machine detection, retries/scheduling.

**Done when:** starting a campaign dials a verified number, the agent follows the campaign script, and disposition + transcript show in the dashboard.

## Phase 5 — Demo polish  ✅ done

**Goal:** Clean full client demo run-through.

**Scope:** per-campaign prompt tuning (goal/script fields drive agent behavior); barge-in tested; failure handling (failed calls marked, dialer continues); dashboard stat cards (`/api/stats`); demo runbook (script of what to show, reset steps).

**Out of scope:** production items listed in ARCHITECTURE.md "Scaling Past the Demo".

**Done when:** end-to-end demo (inbound call → outbound campaign → review transcripts/dispositions) runs clean twice in a row.

## Phase 6 — Twilio integration  🟡 in progress

**Goal:** Real PSTN calls through the already-built pipeline.

**Scope:** fill Twilio env vars, point number webhooks at ngrok, live-verify the dormant adapter (`/twilio/inbound`, `/twilio/media`, `/twilio/status`, REST origination), switch campaign dialer from simulated to real dialing. Checklist: [TWILIO_INTEGRATION.md](TWILIO_INTEGRATION.md).

**Progress — code complete, live verification outstanding:**
- ✅ Twilio credentials in `.env`, verified against the API (account active, trial tier)
- ✅ Number `+447888862925` confirmed owned, voice-capable
- ✅ Deepgram + Gemini live-verified (STT/TTS round trip, LLM, disposition tagging)
- ✅ Campaign dialer switched to real origination — background supervisor (`services/campaign_runner.py`) claims one contact at a time and places PSTN calls
- ✅ Call row now created at origination, so unanswered calls (busy/no-answer/failed) advance the queue instead of stranding it
- ✅ `StatusCallbackEvent` sent as repeated params — previously only `completed` was ever delivered
- ✅ Real `from_number`/`to_number` persisted instead of the literal `web-call`
- ✅ Fail-closed safety: `OUTBOUND_ALLOWLIST`, daily cap, start confirmation, stop hangs up the live leg
- ✅ `X-Twilio-Signature` validation on all Twilio routes
- ✅ Simulated browser dialing preserved — mode switches on configuration alone, so DEMO.md is unaffected
- ⬜ `PUBLIC_BASE_URL` / ngrok tunnel *(human)*
- ⬜ `OUTBOUND_ALLOWLIST` populated *(human)*
- ⬜ Twilio console webhooks pointed at the backend *(human)*
- ⬜ Live inbound / outbound / campaign calls verified *(human)*

Remaining steps: [TWILIO_INTEGRATION.md](TWILIO_INTEGRATION.md).

**Done when:** inbound call to the Twilio number converses with the agent and a campaign dials a real (verified) phone with disposition recorded.

---

## Phase 7 — Knowledge base (inbound CX)  🟡 code complete

**Goal:** The agent knows who it works for and can answer real company questions — common ones instantly, the rest from indexed documents.

**Scope:** pgvector-backed knowledge base, FAQ fast path that bypasses the LLM, RAG over uploaded documents, a deterministic greeting, dashboard `/knowledge`, and inbound dispositions.

**Progress:**
- ✅ Postgres image swapped to `pgvector/pgvector:pg16`; volume recreated (it was empty)
- ✅ Schema: `agent_profile`, `kb_documents`, `kb_chunks`, `faqs` + HNSW cosine indexes (migration `c4a91e6b7d02`)
- ✅ Async embeddings client against the OpenAI-compatible endpoint — batching, index re-sorting, dimension validation, no retry on the call path
- ✅ Ingestion: PDF/TXT/MD → paragraph-greedy chunks with overlap → embedded in a background task, `pending → processing → ready|failed`
- ✅ Retrieval: one embedding serves both the FAQ and chunk queries; backchannel short-circuit; per-process vector cache; hard `KB_TURN_TIMEOUT_SECONDS` budget, fail-open
- ✅ `agent/faq_gate.py` — on a match the stored answer is spoken verbatim and the `LLMContextFrame` is dropped, which bypasses Gemini entirely; on a miss the top chunks are injected as a marked system message. Barge-in guarded by an epoch counter
- ✅ Deterministic greeting from `agent_profile.greeting_template` — `CallConfig.greeting` was dead code and is now the actual first utterance, saving a full LLM round trip
- ✅ Inbound dispositions: `finish()` classifies every call, vocabulary branches on direction; campaign advancement unchanged
- ✅ Dashboard `/knowledge` (identity, FAQs, documents, test search) + shared disposition badges
- ✅ 138 backend tests green, including a machine-checkable proof that a hit emits one `TTSSpeakFrame` and zero `LLMContextFrame`
- ⬜ `EMBEDDING_API_KEY` / `EMBEDDING_MODEL_NAME` / `EMBEDDING_DIM` in `.env` *(human)*
- ⬜ End-to-end verification against the real embeddings endpoint *(human + agent)*
- ⬜ Company content loaded: FAQs and documents *(human)*

**Done when:** a browser web-call greets by company name, answers a seeded FAQ verbatim with no Gemini generation in the logs, answers a document-only question from retrieved text, and lands an inbound disposition.
