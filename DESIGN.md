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
| POST | `/api/campaigns/{id}/start` | Start sequential outbound dialing |
| POST | `/api/campaigns/{id}/stop` | Stop after current call |

Campaign status: `draft → running → completed` (or `stopped`).

### Calls (Phase 3+)

| Method | Path | Description |
|---|---|---|
| GET | `/api/calls` | List (filters: `direction`, `campaign_id`, `disposition`, `from_date`) |
| GET | `/api/calls/{id}` | Detail: metadata + full transcript turns |
| POST | `/api/calls/outbound` | Ad-hoc single outbound call `{contact_id}` (test utility) |

### Twilio webhooks (Phase 3+, not under `/api`)

| Method | Path | Description |
|---|---|---|
| POST | `/twilio/inbound` | Answer webhook → returns TwiML `<Connect><Stream url="wss://…/twilio/media">` |
| WS | `/twilio/media` | Media Streams WebSocket → bridges into Pipecat pipeline |
| POST | `/twilio/status` | Call status callbacks (ringing/answered/completed) → updates `calls` row |

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
  disposition   text                          -- interested|not_interested|callback|voicemail|failed (Phase 4)
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
```

Migrations: Alembic, from Phase 2. Indexes: `calls(campaign_id)`, `calls(started_at)`, `transcript_turns(call_id)`.

## Dashboard Screens

Layout: fixed sidebar nav (Dashboard, Campaigns, Contacts, Calls) + main content. shadcn/ui components.

1. **Dashboard** (`/`) — stat cards (total calls, active campaign, dispositions donut — Phase 5; empty states in Phase 1), recent calls list.
2. **Contacts** (`/contacts`) — table with search, Add Contact dialog, Import CSV button, row → contact detail with call history.
3. **Campaigns** (`/campaigns`) — list with status badge + progress (e.g. 12/40 called). New Campaign form: name, goal, script prompt, contact multi-select. Detail (`/campaigns/[id]`): contact list with per-contact status/disposition, Start/Stop buttons.
4. **Calls** (`/calls`) — filterable log table (direction, disposition, duration). Row → call detail (`/calls/[id]`): metadata header + chat-style transcript (agent/caller bubbles) + disposition.

Phase 1 ships all four routes as placeholder pages with correct nav and empty-state cards.
