# Architecture

## System Diagram

```mermaid
flowchart LR
    Browser((Browser mic\ndemo caller))
    Caller((Phone caller\nwhen Twilio live))
    Twilio[Twilio adapter\nreal dialing when configured]
    Agent[Pipecat voice agent\nVAD · turn-taking · barge-in]
    DG_STT[Deepgram Nova\nSTT]
    Gemini[Gemini 3.5 Flash Lite\nLLM]
    DG_TTS[Deepgram Aura\nTTS]
    API[FastAPI backend\nREST + webhooks + WebRTC signalling]
    DB[(PostgreSQL)]
    FE[Next.js dashboard :3000]

    Browser <-->|WebRTC audio\nSmallWebRTC| Agent
    Caller <-.->|PSTN| Twilio
    Twilio <-.->|WebSocket audio\nMedia Streams| Agent
    Agent --> DG_STT --> Gemini --> DG_TTS --> Agent
    Agent -->|call rows, transcript turns| API
    API <--> DB
    FE -->|REST /api| API
```

Two transports feed **one shared pipeline** (`agent/pipeline.py`):

- **Browser web-call (live now):** dashboard call widget POSTs an SDP offer to `/api/webrtc/offer`; Pipecat's SmallWebRTC transport carries mic/speaker audio. This powers the whole demo without Twilio.
- **Twilio (implemented, not yet live-verified):** `/twilio/inbound` returns TwiML `<Connect><Stream>` into the `/twilio/media` WebSocket; outbound via Twilio REST origination, driven by a background campaign supervisor. Selected by configuration — see [TWILIO_INTEGRATION.md](TWILIO_INTEGRATION.md).

**Mode is a configuration decision, not a code path choice.** `telephony.dialing_mode()` returns `twilio` only when credentials, a number, and an `https://` public URL are all present; otherwise the system runs simulated browser dialing. That keeps the demo working everywhere and makes enabling real calls a `.env` change.

End-to-end mechanics of a call, and how to run the system: [PROCEDURE.md](PROCEDURE.md).

## Data Flow

**Inbound (demo):** user clicks "Call agent" → WebRTC session → pipeline (audio → Deepgram STT → Gemini → Deepgram TTS → audio) → call row + transcript turns persisted → dashboard reads via REST.

**Outbound campaigns (demo):** Start campaign → sequential dialer surfaces next pending contact → user answers as the contact in a web-call carrying the campaign script → after each call Gemini tags a disposition + summary and the queue advances; campaign completes when the queue empties.

**With Twilio:** same pipeline; only call setup changes (PSTN webhook / REST origination instead of browser offer).

## Technology Choices & Rationale

Decisions below are **locked**. Do not swap vendors or frameworks unless the owner explicitly reopens the decision here.

| Choice | Why | Alternatives considered |
|---|---|---|
| **Twilio** | Best docs, easy number provisioning, mature Media Streams WebSocket API, trial credit. Fastest to a working demo. | Telnyx (cheaper, weaker docs); self-hosted SIP/Asterisk (no per-min cost but weeks of ops work); Vonage (fewer AI-voice examples). |
| **Pipecat** | Open-source Python framework purpose-built for voice agents: VAD, interruptions, turn-taking handled. Any STT/LLM/TTS pluggable. Self-hosted → no per-minute platform fee and a clean production path. | LiveKit Agents (great at scale, heavier infra); Vapi/Retell (demo in hours but $0.05–0.15/min platform fee + lock-in); OpenAI Realtime (lowest latency, ~$0.30+/min, vendor-locked voices). |
| **Deepgram STT (Nova)** | Streaming-native, fast, $200 free credit covers the whole demo. | Whisper (not streaming-native); Google Chirp; AssemblyAI. |
| **Deepgram TTS (Aura)** | Same vendor/key as STT, low latency, free credit. Voice quality is adequate for demo. | Cartesia Sonic and ElevenLabs sound better — **planned upgrade path**; swap is a one-line Pipecat service change. |
| **Gemini Flash Lite** | Free-tier API key, fast + cheap, good enough conversational quality. Currently pinned to `gemini-3.5-flash-lite` (see note below). | Claude Haiku (better quality, paid); GPT-4.1-mini (paid). |
| **One shared pipeline** | Same agent runtime for inbound/outbound; only call setup differs. Less code, consistent behavior. Prompt/config varies per campaign. | Separate pipelines — only justified if behaviors diverge heavily. |
| **FastAPI + Postgres** | Python matches Pipecat (one language, one repo). Postgres production-grade from day one — no SQLite migration later. | Node backend (second runtime); SQLite (migration friction); Supabase (vendor coupling, still need FastAPI for webhooks). |
| **Next.js + shadcn/ui** | Polished dashboard fast, huge ecosystem. | Vite SPA (fine, less convention); SvelteKit; FastAPI+HTMX (weak demo polish). |
| **pgvector in the existing Postgres** | The knowledge base needs vector search, and the corpus is small. Reusing Postgres keeps one datastore, one backup, one migration path — a dedicated vector DB would be a second system to run for a few thousand rows. Image changed from `postgres:16-alpine` to `pgvector/pgvector:pg16`. | Pinecone/Qdrant/Weaviate (another service and another SDK); in-process numpy (no index, no persistence, per-worker RAM); Postgres full-text search (no semantic paraphrase matching, which is the whole point). |
| **OpenAI-compatible embeddings endpoint** | Self-hosted behind the owner's own URL, so transcripts and documents never leave their infrastructure, and the interface is the de-facto standard — swapping models is a `.env` change plus a reindex. | OpenAI/Voyage/Cohere hosted embeddings (another vendor with call content); Gemini embeddings (would couple retrieval to the LLM vendor). |
| **Local + ngrok** | Zero hosting cost, fastest iteration; Twilio just needs a public HTTPS/WSS URL. | VPS (~$6/mo, always-on); Railway/Render free tier (WebSocket + cold-start risk for live audio). |
| **No auth** | Local-only demo. | Basic JWT login is the first thing to add if the dashboard gets a public URL. |
| **English only** | Deepgram and Gemini are multilingual; adding a language later is configuration (STT language, TTS voice, prompt), not rearchitecture. | — |

### Gemini model pinning

The Gemini **model version** is not a locked vendor decision — Google retires
Flash Lite versions for new accounts without warning, surfacing as
`404 ... no longer available to new users`. When that happens, bump the version;
the vendor stays Gemini. The model name is pinned in **two** places and both
must move together:

- `agent/pipeline.py` → `GEMINI_MODEL` (conversation)
- `backend/app/services/disposition.py` → `model=` (post-call classification)

## Knowledge base: how a caller turn is answered

```mermaid
flowchart TD
    STT[Caller utterance from STT] --> Gate{FaqGate}
    Gate -->|backchannel: yes / ok / thanks| LLM
    Gate --> Embed[Embed once, ~150ms]
    Embed --> Search[pgvector cosine: FAQs + document chunks]
    Search --> Score{FAQ score ≥ threshold?}
    Score -->|yes| Speak[Speak the stored answer verbatim]
    Score -->|no| Inject[Inject top chunks as a marked system message]
    Inject --> LLM[Gemini]
    LLM --> TTS
    Speak --> TTS[Deepgram Aura]
```

The fast path is a real bypass, not a shortcut through the prompt:
`GoogleLLMService` runs inference on `LLMContextFrame` and nothing else, so
declining to forward that one frame skips the generation entirely. The canned
answer goes out as a `TTSSpeakFrame`, which (via `append_to_context`) still
lands in the LLM context as the assistant's turn — so a follow-up question is
answered with the FAQ already in history.

The same mechanism gives the agent a deterministic greeting: the opening line
is spoken from `agent_profile.greeting_template` instead of being improvised,
removing a full LLM round trip from time-to-first-audio.

Everything on this path is **fail-open**. The lookup is capped at
`KB_TURN_TIMEOUT_SECONDS`; on timeout, error, or caller barge-in the turn falls
through to the LLM exactly as it would have before the knowledge base existed.

## Known Limitations (accepted for demo)

- **Twilio is not live-verified.** Credentials, phone number, and account are verified against the API and the adapter is unit-tested, but no real PSTN call has been placed; expect ~1 hour of verification.
- Campaign dialing is **simulated** unless Twilio is fully configured: user answers each call in the browser as the contact.
- **Single worker.** The dial supervisor runs one task per process; `uvicorn --workers N` would duplicate it. Multi-worker would need a `pg_try_advisory_lock` around the loop.
- Real dialing is **fail-closed** behind `OUTBOUND_ALLOWLIST` plus a daily cap — deliberate friction, since the alternative default is a live public URL with valid credentials and an unreviewed contact list.
- No answer-machine detection, so voicemail is recorded as `completed`.
- Twilio **trial** restrictions: outbound only to verified numbers, trial announcement plays. ~$20 upgrade removes both.
- UK number requires regulatory bundle approval (days). US number as fallback.
- Single machine, no redundancy; a laptop sleep kills live calls.
- No auth on dashboard or API.
- Gemini free-tier rate limits (fine at demo call volume, not at scale).
- Sequential outbound dialer — one call at a time.
- No call recording audio storage (transcripts only).
- **Knowledge base has no OCR.** A scanned PDF has no text layer and is rejected with an explicit error rather than indexed as empty.
- **Embeddings are a single point of failure for retrieval.** If that endpoint is down the agent still answers, just without company knowledge — it falls back to "I'll pass that to the team".
- **Changing embedding model requires a migration and a full reindex**, because the vector column width is fixed in the schema.

## Scaling Past the Demo

1. **TTS upgrade:** Deepgram Aura → Cartesia or ElevenLabs (config swap in the Pipecat pipeline).
2. **Hosting:** Docker Compose on a VPS first; then split agent workers from API, run agents on machines near Twilio edge, autoscale on concurrent calls (LiveKit or k8s if call volume demands).
3. **Database:** managed Postgres (RDS/Supabase/Neon); add read replicas only if reporting load requires.
4. **Auth:** JWT login → multi-tenant orgs with roles.
5. **LLM:** paid Gemini tier or Claude Haiku for quality; per-campaign model choice.
6. **Compliance:** call recording consent, DNC list checks, recording storage (S3) — required before real outbound campaigns in production.
7. **Concurrency:** parallel outbound dialing with answer-machine detection (Twilio AMD).
