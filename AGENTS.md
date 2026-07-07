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
  app/models.py    SQLAlchemy models (Phase 2+)
  app/routers/     one file per resource (contacts, campaigns, calls, twilio_webhooks)
  app/services/    business logic (keep routers thin)
agent/             Pipecat voice pipeline (Phase 3+)
  pipeline.py      STT→LLM→TTS pipeline factory (shared inbound/outbound)
  prompts/         system prompt templates
frontend/          Next.js App Router + shadcn/ui
  app/             routes: / , /campaigns , /contacts , /calls
  components/      shared UI (sidebar, etc.)
docker-compose.yml Postgres only; app processes run on host for the demo
```

Roadmap and phase gates: [PLAN.md](PLAN.md). API/schema/screens: [DESIGN.md](DESIGN.md).

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
- **DB:** schema changes only via Alembic migrations (Phase 2+); never edit applied migrations.
- Phone numbers stored E.164. UUIDs for PKs.
- Commits: conventional-ish (`feat:`, `fix:`, `docs:`, `chore:`).

## Do not touch

- `.env` (real secrets; `.env.example` is the template to update instead)
- Applied Alembic migration files
- Do not start a later phase before the current one is reviewed (see PLAN.md gates)
