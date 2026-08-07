# Procedure — How the System Works and How to Use It

A practical walkthrough of the whole system: what the pieces are, what actually
happens during a call, and how to run it. Read this first if you're new to the
repo. Vendor rationale lives in [ARCHITECTURE.md](ARCHITECTURE.md); the API and
schema contract in [DESIGN.md](DESIGN.md); conventions in [AGENTS.md](AGENTS.md).

---

## 1. What this system does

It is an AI call center. A caller talks to an AI agent over voice; the agent
listens, thinks, and speaks back in real time. Every call is recorded as a
database row with a full transcript, and after campaign calls an LLM tags the
outcome. A web dashboard manages contacts and campaigns and shows the results.

There are two ways audio reaches the agent:

| Transport | Status | Used for |
|---|---|---|
| **Browser web-call (WebRTC)** | **Live and verified** | The entire demo — inbound calls and campaign calls |
| **Twilio PSTN (Media Streams)** | Written, credentials verified, **never live-tested** | Real phone calls, once integration day happens |

Both feed the **same pipeline**. Only call setup differs. That is the central
design idea: swapping transports does not change the agent, the persistence,
or the dashboard.

---

## 2. The four moving parts

```
frontend/   Next.js 16 dashboard (:3000) — contacts, campaigns, calls, call widget
backend/    FastAPI (:8000) — REST API, WebRTC signalling, Twilio webhooks
agent/      Pipecat pipeline — the actual voice agent (STT → LLM → TTS)
Postgres    Docker container on host port 5433 — all state
```

The backend and the agent run **in the same Python process**. `agent/` is a
separate installable package, but the FastAPI app imports it directly
(`app/services/call_session.py` → `agent.pipeline`). There is no separate
worker or queue — a call is an asyncio task inside the API process.

---

## 3. The voice pipeline (the heart of it)

Built in `agent/pipeline.py::build_task`. Pipecat wires processors into a
chain; audio frames flow left to right, and the aggregators feed conversation
history back to the LLM:

```
transport.input()          raw mic/phone audio in
  → DeepgramSTTService     speech → text          (Nova, streaming)
  → user aggregator        appends caller turn to LLM context
  → GoogleLLMService       text → reply           (gemini-3.5-flash-lite)
  → DeepgramTTSService     reply → speech         (aura-2-thalia-en)
  → transport.output()     audio back to caller
  → assistant aggregator   appends agent turn to LLM context
```

Details that matter:

- **One Deepgram key covers both STT and TTS.** Same vendor, one credential.
- **VAD and turn-taking** come from `SileroVADAnalyzer` plus Pipecat's local
  Smart Turn v3 model (`default_transport_params()`). This is what decides when
  the caller has finished speaking.
- **Barge-in works** because `PipelineParams(allow_interruptions=True)`. If the
  caller speaks while the agent is talking, the agent stops.
- **The agent speaks first.** `LLMContext` is seeded with a system prompt plus a
  fake user message `"Please greet me now."`, and the transport's
  `on_client_connected` handler queues an `LLMRunFrame()` to trigger it.
- **Prompts are voice-shaped.** `agent/prompts/base.py` forces one-to-three
  sentence replies, no markdown, no emojis, one question at a time — because
  everything gets read aloud by TTS.
- **The prompt varies per call.** Inbound gets `INBOUND_ROLE`; outbound gets
  `OUTBOUND_ROLE` with the contact name, campaign goal, and campaign script
  interpolated in. This is how a campaign changes agent behavior — no code
  change, just campaign fields in the database.

### How transcripts get captured

`agent/transcript.py::TranscriptObserver` watches frames as they move through
the pipeline:

- **Caller turns** come from `TranscriptionFrame`s emitted by the STT service.
- **Agent turns** come from `TTSTextFrame`s, which are *buffered* and only
  flushed on `BotStoppedSpeakingFrame` / `EndFrame` / `CancelFrame`. That
  buffering is deliberate: if the caller interrupts mid-sentence, only the words
  the agent actually spoke get recorded, not the full intended reply.
- Frames are observed once per pipeline hop, so the observer dedupes by
  `frame.id`.

---

## 4. What happens during a browser call, step by step

This is the demo path. Follow it once and the rest of the system makes sense.

1. **User clicks "Call agent."** `frontend/components/call-widget.tsx` creates a
   `PipecatClient` with a `SmallWebRTCTransport` and requests the mic.
2. **Offer goes to the backend.** The client POSTs an SDP offer to
   `POST /api/webrtc/offer`, with `request_data` carrying
   `{direction, contact_id?, campaign_id?}`. ICE candidates trickle in via
   `PATCH /api/webrtc/offer`.
3. **Backend refuses early if unconfigured.** `routers/webrtc.py:44` returns
   **503** if `DEEPGRAM_API_KEY` or `GEMINI_API_KEY` is missing. This is the
   first thing that breaks when `.env` is wrong.
4. **A call row is created.** `CallSession.start()` inserts into `calls` with
   `status='in_progress'`, `started_at=now()`, `from_number='web-call'`. If it's
   a campaign call, the contact is flipped to `calling` in `campaign_contacts`.
5. **The config is assembled.** `build_config()` loads the contact name and
   campaign goal/script from the DB and builds the system prompt and greeting.
6. **The pipeline is built and run.** `build_session_task()` constructs the
   chain above with the `SmallWebRTCTransport`, then `run_call_pipeline()` runs
   it as a background asyncio task.
7. **Conversation happens.** Each completed turn is written to
   `transcript_turns` via `asyncio.to_thread` — DB writes are pushed off the
   event loop so they never stall audio.
8. **The call ends.** On disconnect the task is cancelled, then
   `CallSession.finish()` sets `status`, `ended_at`, and computes
   `duration_seconds`.
9. **Disposition (campaign calls only).** `services/disposition.py::classify_call`
   sends the transcript to Gemini with `response_mime_type="application/json"`
   and `temperature=0`, expecting
   `{"disposition": ..., "summary": ...}`. The disposition must be one of
   `interested | not_interested | callback | voicemail | failed` or it's
   rejected. On any failure the call is left with a null disposition rather
   than a wrong one. Empty transcript → `failed` / "No conversation was recorded."
10. **The queue advances.** `dialer.advance_after_call` marks the contact
    `done` or `failed`; when no `pending` contacts remain, the campaign flips to
    `completed`.

---

## 5. How campaigns work (simulated dialing)

The dialer in `services/dialer.py` **does not place phone calls today.** It is a
state machine over `campaign_contacts`:

```
start_campaign      status → running; any stale 'calling' rows reset to 'pending'
next_pending_contact  first contact with status='pending'
mark_calling        pending → calling   (when the call starts)
advance_after_call  calling → done|failed; campaign → completed when queue empties
stop_campaign       running → stopped   (takes effect after the current call)
```

The campaign page shows the next pending contact with an **"Answer as
&lt;contact&gt;"** button. You click it and roleplay that contact in a browser
web-call. Everything downstream — prompt injection, transcript, disposition,
queue advance — is real. Only the PSTN leg is simulated.

Dialing is strictly **sequential**: one call at a time, no retries, no
scheduling, no answer-machine detection.

**To make it dial real phones** you call
`telephony.originate_call(contact.phone, contact_id, campaign_id)` from
`start_campaign` / `advance_after_call`, and advance on the Twilio `completed`
status callback instead of on web-call end. See
[TWILIO_INTEGRATION.md](TWILIO_INTEGRATION.md) §5.

---

## 6. The Twilio path (built, not yet live)

Dormant code, activated by environment variables alone. `twilio_enabled()`
returns true once `TWILIO_ACCOUNT_SID` and `TWILIO_AUTH_TOKEN` are set;
origination additionally requires `PUBLIC_BASE_URL`.

**Inbound:**
```
caller dials the Twilio number
  → Twilio POSTs /twilio/inbound
  → backend returns TwiML: <Connect><Stream url="wss://<PUBLIC_BASE_URL>/twilio/media?direction=inbound"/>
  → Twilio opens the WebSocket and streams audio
  → /twilio/media reads the "start" message for streamSid/callSid,
    wraps the socket in FastAPIWebsocketTransport + TwilioFrameSerializer,
    and runs the same pipeline
```

**Outbound:** `originate_call()` POSTs to the Twilio REST API with
`Url=<PUBLIC_BASE_URL>/twilio/outbound-answer` — Twilio fetches that TwiML when
the callee answers, which returns the same `<Connect><Stream>` pointing at
`/twilio/media` with `direction=outbound` plus contact/campaign IDs in the query
string.

**Status:** `/twilio/status` receives progress callbacks and marks
`busy | no-answer | failed | canceled` calls appropriately.

Note the URL scheme conversion: `PUBLIC_BASE_URL` must be `https://`, because
`_stream_twiml()` rewrites it to `wss://`. An `http://` value produces a broken
stream URL and silent audio.

---

## 7. Data model in one glance

```
contacts ──┬─< campaign_contacts >─┬── campaigns
           │   (status per pair)   │   (goal, script_prompt, status)
           │                       │
           └──────< calls >────────┘
                      │
                      └──< transcript_turns  (role: agent|caller, ordered by ts)
```

- UUID primary keys; phone numbers stored E.164.
- `campaign_contacts` has a composite PK `(campaign_id, contact_id)`.
- `calls.contact_id` is nullable — unknown inbound callers have no contact.
- `calls.twilio_sid` is only populated on real Twilio calls.
- Schema changes go through Alembic only. Never edit an applied migration.

Full column list: [DESIGN.md](DESIGN.md).

---

## 8. Running it

### One-time setup

```bash
# 1. Database
docker compose up -d db                    # Postgres 16 on host port 5433

# 2. Backend + agent  (Python 3.12 — see note below)
cd backend
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]" -e ../agent        # BOTH packages are required

# 3. Environment
cp .env.example .env                       # at repo root, then fill in keys

# 4. Migrations
alembic upgrade head

# 5. Frontend
cd ../frontend && npm install
```

> **Use Python 3.12, not 3.13+.** Pipecat depends on `audioop`, which was
> removed from the standard library in Python 3.13.

> **Install both packages.** `pip install -e .` alone gives you a backend that
> imports `agent.pipeline` and crashes. The `-e ../agent` is not optional.

### Daily run

```bash
docker compose up -d db
cd backend && source .venv/bin/activate && uvicorn app.main:app --reload --port 8000
cd frontend && npm run dev                 # http://localhost:3000
```

Health check: `curl localhost:8000/health` → `{"status":"ok"}`

### Required credentials

| Variable | Needed for | Without it |
|---|---|---|
| `DEEPGRAM_API_KEY` | STT **and** TTS | `/api/webrtc/offer` returns 503 — no calls at all |
| `GEMINI_API_KEY` | Agent replies + dispositions | same 503 |
| `DATABASE_URL` | Everything | backend won't start |
| `TWILIO_ACCOUNT_SID` / `_AUTH_TOKEN` / `_PHONE_NUMBER` | Real phone calls | Twilio routes stay dormant; demo unaffected |
| `PUBLIC_BASE_URL` | Twilio webhooks (ngrok `https://` URL) | `originate_call` returns 503 |

All environment variables are read in `backend/app/config.py` and nowhere else.

### Checks

```bash
cd backend && pytest                       # 16 tests
cd backend && ruff check . && ruff format --check .
cd frontend && npm run lint && npx tsc --noEmit
```

---

## 9. Using the dashboard

1. **Dashboard** (`/`) — stat cards from `/api/stats`, recent calls, and the
   **Call agent** widget for an ad-hoc inbound call.
2. **Contacts** (`/contacts`) — add contacts individually or import CSV with
   header `name,phone,notes`. Phones must be E.164 (`+447…`) and are unique.
3. **Campaigns** (`/campaigns`) — create with a name, a **goal**, a **script
   prompt**, and selected contacts. Goal and script are injected into the agent's
   system prompt, so they directly control what the agent says.
4. **Run a campaign** — open it, **Start campaign**, then click
   **Answer as &lt;contact&gt;** on the next-call card and roleplay that person.
   Disposition and summary appear automatically when the call ends, and the
   queue advances.
5. **Calls** (`/calls`) — filterable log; open a row for the metadata header and
   the chat-style transcript.

A scripted client walkthrough is in [DEMO.md](DEMO.md).

### Reset demo data (destructive)

This permanently deletes all calls, transcripts, campaigns, and contacts:

```bash
docker compose exec db psql -U acc -d callcentre \
  -c "TRUNCATE transcript_turns, calls, campaign_contacts, campaigns, contacts CASCADE;"
```

---

## 10. When something breaks

| Symptom | Likely cause |
|---|---|
| `503 Voice agent not configured` | `DEEPGRAM_API_KEY` or `GEMINI_API_KEY` missing from `.env`; backend not restarted after editing it |
| `401` from Deepgram | Key invalid or revoked — verify at console.deepgram.com |
| `404 ... model is no longer available` | The pinned Gemini model was retired for your account; update `GEMINI_MODEL` in `agent/pipeline.py` **and** the model string in `services/disposition.py` |
| `429 RESOURCE_EXHAUSTED` | Gemini free-tier rate limit; wait or switch model |
| `ModuleNotFoundError: agent` | The `agent` package wasn't installed — rerun with `-e ../agent` |
| `ModuleNotFoundError: audioop` | Python 3.13+; rebuild the venv on 3.12 |
| Call connects but no audio | Browser mic permission denied, or output device muted; Chrome is the tested browser |
| Disposition stays empty | Gemini call failed — check backend logs; the code writes null rather than guessing |
| Twilio call silent | `PUBLIC_BASE_URL` not `https://`, or the ngrok URL changed and webhooks now point nowhere |

Backend logs are the primary diagnostic — the pipeline logs every processor link
at startup and every connect/disconnect per call.

---

## 11. Known limits

- Twilio path is **live-untested**; expect fixes on integration day.
- Campaign dialing is simulated — a human answers each call in the browser.
- Single machine, single process. Laptop sleep kills live calls.
- No auth on the dashboard or API.
- Sequential dialing only; no retries, scheduling, or answer-machine detection.
- Transcripts only — no call audio is stored.
- English only.

Roadmap and phase gates: [PLAN.md](PLAN.md). Production hardening:
[ARCHITECTURE.md](ARCHITECTURE.md) § Scaling Past the Demo.
