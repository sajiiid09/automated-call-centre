from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All environment variables are read here and nowhere else."""

    model_config = SettingsConfigDict(env_file="../.env", extra="ignore")

    database_url: str = "postgresql+psycopg://acc:acc@localhost:5433/callcentre"

    # Phase 3+
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_phone_number: str = ""
    deepgram_api_key: str = ""
    gemini_api_key: str = ""
    public_base_url: str = ""

    # Phase 6 — real PSTN dialing
    # auto: real dialing when Twilio is fully configured, otherwise simulated.
    dialer_mode: str = "auto"  # auto | simulated | twilio
    # Fail-closed: real dialing refuses to run while this is empty.
    outbound_allowlist: str = ""  # comma-separated E.164
    dialer_supervisor_enabled: bool = True
    dial_poll_seconds: int = 3
    dial_ring_timeout_seconds: int = 30
    dial_stale_call_seconds: int = 300
    max_outbound_calls_per_day: int = 20

    # Phase 7 — knowledge base (RAG + FAQ fast path)
    # NOTE: deliberately *not* named `embedding_model`. pydantic-settings matches
    # env keys case-insensitively, and `.env` already carries a lowercase
    # `embedding_model=<a URL>` line — that name would bind a URL into what
    # reads like a model name.
    embedding_api_url: str = ""
    embedding_api_key: str = ""
    embedding_model_name: str = ""
    # Must equal app.models.EMBEDDING_DIM; the column type is DDL shape, so the
    # model holds the literal and this only guards vectors at runtime.
    embedding_dim: int = 768
    embedding_timeout_seconds: float = 10.0

    # Call-path budget. The whole KB lookup is capped at this; on timeout the
    # turn falls through to the LLM rather than stalling the audio pipeline.
    kb_turn_timeout_seconds: float = 0.8
    kb_max_upload_bytes: int = 5 * 1024 * 1024

    # Defaults for a freshly created agent_profile row; the live values are
    # read from that row, not from here.
    faq_threshold: float = 0.82
    rag_top_k: int = 4
    rag_min_score: float = 0.25


settings = Settings()
