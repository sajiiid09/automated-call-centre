from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.services import campaign_runner, embeddings

    campaign_runner.start()
    yield
    await campaign_runner.stop()
    await embeddings.close()


def _check_embedding_dim() -> None:
    """Fail at startup, not 200 chunks into an upload.

    The vector() column width is baked into the schema by the migration; the
    setting only describes what the embeddings API returns. If they disagree
    every insert dies with an opaque Postgres error, so say so up front.
    """
    from app.config import settings
    from app.models import EMBEDDING_DIM

    if settings.embedding_dim != EMBEDDING_DIM:
        raise RuntimeError(
            f"EMBEDDING_DIM={settings.embedding_dim} but the vector() columns are "
            f"{EMBEDDING_DIM}-wide. Set EMBEDDING_DIM={EMBEDDING_DIM}, or change "
            f"app.models.EMBEDDING_DIM and write a migration to match."
        )


def create_app() -> FastAPI:
    _check_embedding_dim()
    app = FastAPI(title="AI Call Centre API", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    from app.routers import (
        calls,
        campaigns,
        contacts,
        knowledge,
        stats,
        twilio_webhooks,
        webrtc,
    )

    app.include_router(contacts.router)
    app.include_router(campaigns.router)
    app.include_router(calls.router)
    app.include_router(webrtc.router)
    app.include_router(stats.router)
    app.include_router(twilio_webhooks.router)
    app.include_router(knowledge.router)

    return app


app = create_app()
