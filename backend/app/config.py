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


settings = Settings()
