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
  app/services/    business logic (keep routers thin)
                   call_session · dialer · disposition · telephony
                   campaign_runner (background dial supervisor) · twilio_auth
agent/             Pipecat voice pipeline
  pipeline.py      STT→LLM→TTS pipeline factory (shared inbound/outbound)
  transcript.py    frame observer → transcript turns
  prompts/         system prompt templates
frontend/          Next.js App Router + shadcn/ui
  app/             routes: / , /campaigns , /contacts , /calls
  components/      shared UI (sidebar, call-widget, etc.)
  lib/api.ts       all API calls live here
docker-compose.yml Postgres only; app processes run on host for the demo
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
- **Never let the test suite reach Twilio.** `conftest.py`'s autouse `no_real_dialing` fixture pins the mode off and replaces `telephony._post_twilio` with a landmine; keep new tests behind it.
- Commits: conventional-ish (`feat:`, `fix:`, `docs:`, `chore:`).

## Do not touch

- `.env` (real secrets; `.env.example` is the template to update instead)
- Applied Alembic migration files
- Do not start a later phase before the current one is reviewed (see PLAN.md gates)
