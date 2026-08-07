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
| **Browser web-call (WebRTC)** | **Live and verified** | The demo — inbound calls and simulated campaign calls |
| **Twilio PSTN (Media Streams)** | Implemented and unit-tested, **not yet live-verified** | Real phone calls |

Both feed the **same pipeline**. Only call setup differs. That is the central
design idea: swapping transports does not change the agent, the persistence,
or the dashboard.

**Which one runs is decided by configuration, not code** (see §5). Without a
public HTTPS URL the system stays in simulated mode and the browser demo works
exactly as before.

---

## 2. The four moving parts

```
frontend/   Next.js 16 dashboard (:3000) — contacts, campaigns, calls, call widget
backend/    FastAPI (:8000) — REST API, WebRTC signalling, Twilio webhooks, dial supervisor
agent/      Pipecat pipeline — the actual voice agent (STT → LLM → TTS)
Postgres    Docker container on host port 5433 — all state
```

The backend and the agent run **in the same Python process**. `agent/` is a
separate installable package, but the FastAPI app imports it directly
(`app/services/call_session.py` → `agent.pipeline`). There is no external
worker or queue — a call is an asyncio task inside the API process, and in real
dialing mode the campaign supervisor is another asyncio task in that same
process (§5).

> **Run a single worker.** The supervisor is one task per process; `uvicorn
> --workers N` would start N of them. The database claim is safe under
> concurrency, but the polling and stale-call reap would duplicate work.

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

## 5. How campaigns work

Campaigns run in one of two modes, decided by `telephony.dialing_mode()`:

| `DIALER_MODE` | Behavior |
|---|---|
| `auto` (default) | Real dialing **iff** Twilio credentials, a phone number, and an `https://` `PUBLIC_BASE_URL` are all present. Otherwise simulated. |
| `simulated` | Kill switch — always browser web-calls, even with Twilio configured. |
| `twilio` | Force real dialing. |

The `https://` requirement is not pedantry: the TwiML rewrites that URL to
`wss://` for the media stream, so an `http://` value produces silent audio with
no error anywhere.

### The shared state machine

`services/dialer.py` is a state machine over `campaign_contacts`, used by both
modes:

```
start_campaign        status → running; contacts stuck in 'calling' re-queued,
                      but only if their call is actually over
claim_next_contact    atomically take the next pending contact → 'calling'
advance_after_call    calling → done|failed; campaign → completed when drained
stop_campaign         running → stopped
```

`claim_next_contact` locks the campaign row (`SELECT … FOR UPDATE`) and checks
for an in-flight call inside that lock. That is what keeps dialing sequential
and stops two supervisors racing. `advance_after_call` only acts on a contact
still in `calling`, which makes it **idempotent** — a real call can be reported
terminal by the status callback, the pipeline, and the stale reap.

### Simulated mode

The campaign page shows the next pending contact with an **"Answer as
&lt;contact&gt;"** button. You click it and roleplay that contact in a browser
web-call. Everything downstream — prompt injection, transcript, disposition,
queue advance — is real. Only the PSTN leg is simulated. This is what
[DEMO.md](DEMO.md) uses.

### Real mode — the dial supervisor

`services/campaign_runner.py` is the **only server-initiated actor** in the
backend; everything else reacts to an incoming request. It starts from the
FastAPI lifespan and, every `DIAL_POLL_SECONDS` (3), for each running campaign:

1. **Reaps stale attempts** — a contact in `calling` whose call is terminal, or
   older than `DIAL_STALE_CALL_SECONDS` (300), is force-advanced. This is what
   makes a mid-campaign process restart self-healing: the media stream died with
   the process, so nothing else would ever release that contact.
2. **Claims** one contact atomically.
3. **Checks the guardrails** (allowlist, daily cap). A blocked contact is marked
   `failed` rather than skipped — skipping would leave the campaign unable to
   ever reach `completed`.
4. **Creates the `calls` row** with `status="initiated"` and the real numbers.
5. **Originates** the call and stores the returned SID.

If origination throws — a trial account rejecting an unverified number is the
common case — the contact is failed and the queue moves on. One bad number must
never wedge a campaign.

Polling rather than chaining each call off the previous one is deliberate: a
dropped webhook, an ngrok restart, or a process crash would break a chain
permanently, and nothing would repair it.

Dialing is strictly **sequential**: one call at a time, no retries, no
scheduling, no answer-machine detection.

---

## 6. The Twilio path (implemented, not yet live-verified)

**Inbound:**
```
caller dials the Twilio number
  → Twilio POSTs /twilio/inbound  (signature verified)
  → backend returns TwiML: <Connect><Stream url="wss://…/twilio/media?direction=inbound&from_number=…"/>
  → Twilio opens the WebSocket and streams audio
  → /twilio/media reads the "start" message for streamSid/callSid,
    wraps the socket in FastAPIWebsocketTransport + TwilioFrameSerializer,
    and runs the same pipeline
```
The caller's number is carried into the stream URL, so inbound rows record real
E.164 numbers and can be matched back to a known contact.

**Outbound:** the row is created **first**, then `originate_call()` POSTs to the
Twilio REST API with our `call_id` threaded into both callback URLs. Twilio
fetches `/twilio/outbound-answer` when the callee picks up, which returns the
same `<Connect><Stream>` pointing at `/twilio/media`.

### Why the row is created before the call exists

The `calls` row used to be created inside `/twilio/media`, which only runs when
somebody **answers**. A call that went to busy, rang out, or failed therefore
had no row and no SID — so its status callback matched nothing, the failure was
dropped silently, and the campaign contact sat in `calling` forever.

Now: `create_outbound_call_row` writes the row at origination
(`status="initiated"`), and `/twilio/media` **adopts** it via `adopt_call_row`,
which attaches the SID, flips it to `in_progress`, and restarts `started_at` so
`duration_seconds` measures talk time rather than talk + ring.

### Status callbacks own the queue

| Twilio `CallStatus` | `calls.status` | Advances queue |
|---|---|---|
| `initiated`, `queued` | `initiated` | no |
| `ringing` | `ringing` | no |
| `in-progress` | left alone (media stream owns it) | no |
| `completed` | `completed` | **yes** |
| `busy`, `no-answer`, `canceled`, `failed` | `failed` / `no_answer` | **yes** |

For a call nobody answered there is no transcript, so the disposition is written
directly (`"Twilio reported no-answer"`) instead of spending a Gemini call to
reach the same conclusion.

On PSTN the status callback — not the pipeline ending — advances the queue,
because it is the only signal that exists when nobody picks up. `CallSession`
knows this via its `is_twilio` flag.

Lookups are keyed on **our** `call_id` from the query string, with the SID as a
fallback: Twilio's first callback can arrive before the REST response has even
returned the SID to us.

### Webhook authentication

All four Twilio routes verify `X-Twilio-Signature` (HMAC-SHA1 over the URL plus
sorted POST params, using the auth token). The Media Streams WebSocket carries
no headers, so its URL includes a per-call HMAC token that only our own TwiML
can produce. Both checks are skipped in simulated mode, so tests and the browser
demo are unaffected.

Without this, anyone who found the ngrok URL could post fake status callbacks to
corrupt campaign state, or open the media socket and burn Deepgram and Gemini
credit.

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
| `TWILIO_ACCOUNT_SID` / `_AUTH_TOKEN` / `_PHONE_NUMBER` | Real phone calls | stays in simulated mode; demo unaffected |
| `PUBLIC_BASE_URL` | Twilio webhooks — must be `https://` | stays in simulated mode |
| `OUTBOUND_ALLOWLIST` | **Required** for any real call | every real call is refused (fail-closed) |

Optional dialing knobs, all with working defaults: `DIALER_MODE` (`auto`),
`MAX_OUTBOUND_CALLS_PER_DAY` (20), `DIAL_POLL_SECONDS` (3),
`DIAL_RING_TIMEOUT_SECONDS` (30), `DIAL_STALE_CALL_SECONDS` (300),
`DIALER_SUPERVISOR_ENABLED` (true).

All environment variables are read in `backend/app/config.py` and nowhere else.

### Safety before you dial anything real

Real calls cost money and reach real people, so the guardrails are fail-closed:

- **`OUTBOUND_ALLOWLIST` must list every number that may be dialed.** Empty means
  nothing dials, which is the default state.
- **A daily cap** (`MAX_OUTBOUND_CALLS_PER_DAY`) bounds the blast radius of a bug.
- **Starting a real campaign requires confirmation** — the API rejects it with 409
  unless `confirm_real` is set, and the dashboard shows a dialog listing the numbers.
- **Stop hangs up the live leg**, so nobody is left talking to the agent.
- `DIALER_MODE=simulated` is a hard kill switch.

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
| Campaign won't dial, `dialing_mode` is `simulated` | a prerequisite is missing — check the `https://` prefix first |
| `503 OUTBOUND_ALLOWLIST is empty` | working as designed; add the number you intend to call |
| `503 Daily outbound cap reached` | raise `MAX_OUTBOUND_CALLS_PER_DAY` |
| `409` on campaign start | real mode needs `confirm_real` — use the dashboard's confirm dialog |
| Contact stuck in `calling` | the reap clears it after `DIAL_STALE_CALL_SECONDS`; check logs for the underlying failure |
| `403` on a Twilio webhook | signature check failed — usually `PUBLIC_BASE_URL` not matching the URL Twilio actually called |

Backend logs are the primary diagnostic — the pipeline logs every processor link
at startup and every connect/disconnect per call.

---

## 11. Known limits

- Twilio path is implemented and unit-tested but **not yet live-verified** —
  see [TWILIO_INTEGRATION.md](TWILIO_INTEGRATION.md).
- Campaign dialing is simulated unless Twilio is fully configured.
- Single machine, **single worker**. Laptop sleep kills live calls.
- No auth on the dashboard or API (the Twilio webhooks are signature-verified).
- Sequential dialing only; no retries, scheduling, or answer-machine detection.
  Without AMD, voicemail is reported as `completed`.
- Transcripts only — no call audio is stored.
- English only.

Roadmap and phase gates: [PLAN.md](PLAN.md). Production hardening:
[ARCHITECTURE.md](ARCHITECTURE.md) § Scaling Past the Demo.
