# AGENTS.md

Guidance for AI agents and contributors working in this repo.

## Golden rule

**Vendor and framework choices are locked in [ARCHITECTURE.md](ARCHITECTURE.md).** Do not swap Twilio, Pipecat, Deepgram, Gemini, FastAPI, Postgres, or Next.js — or introduce parallel alternatives — unless the owner explicitly reopens the decision.

## Repo map

```
backend/           FastAPI app
  app/main.py      app factory + router registration
  app/config.py    pydantic-settings; all env vars read here, nowhere else
  app/db.py        engine/session
  app/models.py    SQLAlchemy models
  app/routers/     one file per resource (contacts, campaigns, calls, stats,
                   webrtc, twilio_webhooks)
  app/routers/     …also knowledge (agent profile, FAQs, documents, search)
  app/services/    business logic (keep routers thin)
                   call_session · dialer · disposition · telephony
                   campaign_runner (background dial supervisor) · twilio_auth
                   embeddings · knowledge (retrieval) · knowledge_ingest
agent/             Pipecat voice pipeline
  pipeline.py      STT→LLM→TTS pipeline factory (shared inbound/outbound)
  faq_gate.py      FAQ fast path + RAG injection processor
  transcript.py    frame observer → transcript turns
  prompts/         system prompt templates
frontend/          Next.js App Router + shadcn/ui
  app/             routes: / , /campaigns , /contacts , /calls , /knowledge
  components/      shared UI (sidebar, call-widget, etc.)
  lib/api.ts       all API calls live here
  lib/dispositions.ts  shared disposition labels/colours
docker-compose.yml Postgres (pgvector image) only; app processes run on host
```

How it all fits together: [PROCEDURE.md](PROCEDURE.md). Roadmap and phase gates: [PLAN.md](PLAN.md). API/schema/screens: [DESIGN.md](DESIGN.md).

## Install

```bash
cd backend
python3.12 -m venv .venv && source .venv/bin/activate   # 3.13+ breaks Pipecat (audioop removed)
pip install -e ".[dev]" -e ../agent                     # both packages, or `import agent` fails
alembic upgrade head
```

## Run locally

```bash
docker compose up -d db
cd backend && source .venv/bin/activate && uvicorn app.main:app --reload --port 8000
cd frontend && npm run dev        # :3000
curl localhost:8000/health
```

## Test / lint

```bash
cd backend && pytest              # tests in backend/tests/
cd backend && ruff check . && ruff format --check .
cd frontend && npm run lint
cd frontend && npx tsc --noEmit
```

## Conventions

- **Python:** ruff (lint + format), type hints on public functions, pydantic models for API I/O. Env vars only via `app/config.py`.
- **TypeScript:** eslint + strict TS. Use shadcn/ui components before hand-rolling UI. API calls in `frontend/lib/api.ts`, not inline in components.
- **DB:** schema changes only via Alembic migrations; never edit applied migrations.
- Phone numbers stored E.164. UUIDs for PKs.
- **Gemini model name is pinned in two files** — `agent/pipeline.py` (`GEMINI_MODEL`) and `app/services/disposition.py`. Change both together.
- **Run one uvicorn worker.** `services/campaign_runner.py` starts a dial supervisor per process; N workers means N supervisors.
- **Background tasks open their own `SessionLocal()`** — never pass a request-scoped `Depends(get_db)` session into one. Tests bind them via the `shared_session` fixture.
- **Never let the test suite reach Twilio or the embeddings API.** `conftest.py`'s autouse `no_real_dialing` and `fake_embeddings` fixtures pin both off — the first replaces `telephony._post_twilio` with a landmine, the second repoints the embeddings URL at a respx mock. `.env` holds working credentials for both, so keep new tests behind them.
- **The embedding dimension lives in three places and they must agree**: `app/models.py` `EMBEDDING_DIM` (the `vector(N)` column), the same literal in the knowledge-base migration, and `EMBEDDING_DIM` in `.env`. Startup refuses to boot if the setting and the model disagree. Changing the embedding model means a migration and a full reindex.
- **The knowledge base is fail-open on the call path.** `knowledge.lookup_turn` and `FaqGate` swallow every error and fall through to the LLM. Keep it that way: a slow or broken KB must never stall live audio.
- Commits: conventional-ish (`feat:`, `fix:`, `docs:`, `chore:`).

## Do not touch

- `.env` (real secrets; `.env.example` is the template to update instead)
- Applied Alembic migration files
- Do not start a later phase before the current one is reviewed (see PLAN.md gates)
