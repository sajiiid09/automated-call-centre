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


settings = Settings()
