from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


def create_app() -> FastAPI:
    app = FastAPI(title="AI Call Centre API")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3001"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    from app.routers import calls, campaigns, contacts

    app.include_router(contacts.router)
    app.include_router(campaigns.router)
    app.include_router(calls.router)

    return app


app = create_app()
