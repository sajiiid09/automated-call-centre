# Design

API contract, database schema, and dashboard screens. Implemented incrementally per [PLAN.md](PLAN.md) — this document is the target shape.

## REST API

Base path `/api`. JSON in/out. No auth (demo). Errors: `{"detail": "..."}` with proper status codes.

### Contacts (Phase 2)

| Method | Path | Description |
|---|---|---|
| GET | `/api/contacts` | List (query: `search`, `page`, `page_size`) |
| POST | `/api/contacts` | Create `{name, phone, notes?}` — phone E.164, unique |
| GET | `/api/contacts/{id}` | Detail incl. call history |
| PATCH | `/api/contacts/{id}` | Update |
| DELETE | `/api/contacts/{id}` | Delete |
| POST | `/api/contacts/import` | CSV upload (`name,phone,notes`); returns `{imported, skipped, errors[]}` |

### Campaigns (Phase 2, launch in Phase 4)

| Method | Path | Description |
|---|---|---|
| GET | `/api/campaigns` | List with progress counts |
| POST | `/api/campaigns` | Create `{name, goal, script_prompt, contact_ids[]}` |
| GET | `/api/campaigns/{id}` | Detail: contacts + per-contact call status/disposition |
| PATCH | `/api/campaigns/{id}` | Update (only while `draft`) |
| POST | `/api/campaigns/{id}/start` | Start sequential outbound dialing. In real dialing mode requires body `{confirm_real: true}`, else **409** |
| POST | `/api/campaigns/{id}/stop` | Stop dialing and hang up the live leg |

Campaign status: `draft → running → completed` (or `stopped`).

Campaign responses carry `dialing_mode` (`simulated`|`twilio`); each contact row carries `call_status` and `dialable` (`null` in simulated mode, `false` when the number is not allowlisted).

### Calls (Phase 3+)

| Method | Path | Description |
|---|---|---|
| GET | `/api/calls` | List (filters: `direction`, `campaign_id`, `disposition`, `from_date`) |
| GET | `/api/calls/{id}` | Detail: metadata + full transcript turns |
| POST | `/api/calls/outbound` | Ad-hoc single outbound call `{contact_id}` (test utility) |

### Web-call signalling (live — demo voice transport)

| Method | Path | Description |
|---|---|---|
| POST | `/api/webrtc/offer` | SDP offer from the dashboard call widget; `request_data` carries `{direction, contact_id?, campaign_id?}`; spawns a pipeline bound to a new `calls` row |
| PATCH | `/api/webrtc/offer` | Trickle ICE candidates `{pc_id, candidates[]}` |

### Twilio webhooks (implemented, not yet live-verified; not under `/api`)

All verify `X-Twilio-Signature` when real dialing is active.

| Method | Path | Description |
|---|---|---|
| POST | `/twilio/inbound` | Answer webhook → TwiML `<Connect><Stream url="wss://…/twilio/media">`, carrying the caller's `From`/`To` |
| POST | `/twilio/outbound-answer` | TwiML fetched when an originated call is answered; carries `call_id`/`contact_id`/`campaign_id` to the stream URL |
| WS | `/twilio/media` | Media Streams WebSocket → bridges into Pipecat pipeline; **adopts** the row created at origination |
| POST | `/twilio/status` | Status callbacks → update the `calls` row and advance the campaign queue on terminal states |

`PUBLIC_BASE_URL` must be `https://` — the TwiML rewrites it to `wss://` for the stream URL.

Outbound `calls` rows are created **at origination** (`status="initiated"`), not on answer, so busy/no-answer/failed callbacks have a row to land on and the campaign queue advances.

### Knowledge base (Phase 7)

| Method | Path | Description |
|---|---|---|
| GET | `/api/knowledge/profile` | Agent identity + retrieval settings; creates the singleton on first read |
| PATCH | `/api/knowledge/profile` | Update company name, greeting, persona, thresholds |
| GET | `/api/knowledge/faqs` | List, with `indexed` and `hit_count` |
| POST | `/api/knowledge/faqs` | 201; embeds the question inline |
| PATCH | `/api/knowledge/faqs/{id}` | Re-embeds only when `question` changes |
| DELETE | `/api/knowledge/faqs/{id}` | 204 |
| GET | `/api/knowledge/documents` | List with indexing status — poll this after upload |
| POST | `/api/knowledge/documents` | 202; multipart `file` (.pdf/.txt/.md), indexes in the background |
| POST | `/api/knowledge/documents/{id}/reindex` | 202; idempotent, re-chunks stored text |
| DELETE | `/api/knowledge/documents/{id}` | 204; chunks cascade |
| POST | `/api/knowledge/search` | `{query, top_k?}` → top FAQ **with its raw score and the active threshold**, plus matching document sections |

`POST /search` deliberately returns the best FAQ even below threshold, so
near-misses are visible and `faq_threshold` can be tuned without a phone call.

### Misc

| Method | Path | Description |
|---|---|---|
| GET | `/health` | `{"status":"ok"}` (Phase 1) |
| GET | `/api/stats` | Dashboard cards: total calls, avg duration, dispositions breakdown (Phase 5) |

## Database Schema (Postgres)

```sql
contacts
  id            uuid PK default gen_random_uuid()
  name          text not null
  phone         text not null unique          -- E.164
  notes         text
  created_at    timestamptz default now()

campaigns
  id            uuid PK
  name          text not null
  goal          text                          -- human description
  script_prompt text                          -- injected into agent system prompt
  status        text not null default 'draft' -- draft|running|stopped|completed
  created_at    timestamptz

campaign_contacts
  campaign_id   uuid FK -> campaigns
  contact_id    uuid FK -> contacts
  status        text default 'pending'        -- pending|calling|done|failed
  position      int not null default 0        -- deterministic dial order
  PK (campaign_id, contact_id)

calls
  id            uuid PK
  twilio_sid    text unique
  direction     text not null                 -- inbound|outbound
  contact_id    uuid FK -> contacts null      -- null for unknown inbound callers
  campaign_id   uuid FK -> campaigns null
  from_number   text
  to_number     text
  status        text                          -- initiated|ringing|in_progress|completed|failed|no_answer
  -- vocabulary depends on direction (Phase 7):
  --   outbound: interested|not_interested|callback|voicemail|failed
  --   inbound:  resolved|needs_followup|complaint|enquiry|abandoned
  disposition   text
  disposition_summary text                    -- LLM one-liner
  started_at    timestamptz
  ended_at      timestamptz
  duration_seconds int

transcript_turns
  id            bigserial PK
  call_id       uuid FK -> calls
  role          text not null                 -- agent|caller
  content       text not null
  ts            timestamptz default now()

-- Knowledge base (Phase 7). Requires the `vector` extension.

agent_profile                                 -- exactly one row, CHECK (id = 1)
  id                int PK default 1
  company_name      text not null
  greeting_template text not null             -- $company_name / $contact_name
  persona           text                      -- appended to the system prompt
  faq_threshold     double precision not null default 0.82
  rag_top_k         int not null default 4
  rag_min_score     double precision not null default 0.25
  updated_at        timestamptz

kb_documents
  id            uuid PK
  title         text not null
  filename      text not null
  content_type  text not null
  size_bytes    int not null default 0
  status        text not null default 'pending' -- pending|processing|ready|failed
  error         text
  chunk_count   int not null default 0
  content       text                          -- extracted text; reindex source
  created_at    timestamptz
  updated_at    timestamptz

kb_chunks
  id            uuid PK
  document_id   uuid FK -> kb_documents ON DELETE CASCADE
  ordinal       int not null
  content       text not null
  embedding     vector(1024) not null
  created_at    timestamptz
  UNIQUE (document_id, ordinal)

faqs
  id            uuid PK
  question      text not null                 -- the embedded side
  answer        text not null                 -- spoken to the caller verbatim
  enabled       boolean not null default true
  embedding     vector(1024)                  -- null = not indexed, never matches
  hit_count     int not null default 0
  created_at    timestamptz
  updated_at    timestamptz
```

Migrations: Alembic, from Phase 2. Indexes: `calls(campaign_id)`, `calls(started_at)`, `calls(campaign_id, contact_id)`, `campaign_contacts(campaign_id, position)`, `transcript_turns(call_id)`, `kb_chunks(document_id)`, plus HNSW cosine indexes on `kb_chunks(embedding)` and `faqs(embedding)`.

The `vector(N)` width is fixed by the schema. It appears as a literal in `app/models.py` (`EMBEDDING_DIM`) and in the knowledge-base migration, and must equal `EMBEDDING_DIM` in `.env` — the app refuses to start otherwise. Changing embedding model means a migration and a full reindex.

## Dashboard Screens

Layout: fixed sidebar nav (Dashboard, Campaigns, Contacts, Calls, Knowledge) + main content. shadcn/ui components.

1. **Dashboard** (`/`) — stat cards (total calls, active campaign, dispositions donut — Phase 5; empty states in Phase 1), recent calls list.
2. **Contacts** (`/contacts`) — table with search, Add Contact dialog, Import CSV button, row → contact detail with call history.
3. **Campaigns** (`/campaigns`) — list with status badge + progress (e.g. 12/40 called). New Campaign form: name, goal, script prompt, contact multi-select. Detail (`/campaigns/[id]`): contact list with per-contact status/disposition, Start/Stop buttons.
4. **Calls** (`/calls`) — filterable log table (direction, disposition, duration). Row → call detail (`/calls/[id]`): metadata header + chat-style transcript (agent/caller bubbles) + disposition.
5. **Knowledge** (`/knowledge`, Phase 7) — four stacked cards: **Agent identity** (company name, greeting template, standing instructions, FAQ match threshold); **Instant answers** (FAQ table with enable toggle and hit count, add dialog); **Documents** (upload PDF/TXT/MD, status badge polling `pending → processing → ready`, section count, reindex/delete); **Test search** (type a caller question, see the top FAQ with its score against the live threshold and the document sections the agent would be given). Test search is how the threshold gets tuned without placing a call.

Phase 1 ships all four routes as placeholder pages with correct nav and empty-state cards.
