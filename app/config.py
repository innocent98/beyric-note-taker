from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/notetaker"

    vexa_base_url: str = "http://localhost:8056"
    vexa_api_key: str = ""
    bot_name: str = "Beyric Note Taker (recording)"
    poll_interval_seconds: int = 30
    max_poll_hours: int = 6

    openai_api_key: str = ""
    transcription_model: str = "gpt-transcribe"
    notes_model: str = "gpt-6-luna"

    audio_retention_days: int = 7
    audio_dir: str = "data/audio"

    app_env: str = "production"
    owner_password: str = ""
    session_secret: str = ""
    cookie_secure: bool = True
    # The secret nginx sends in X-Edge-Secret. The app believes X-Forwarded-For only on a request
    # that carries it (app/edge.py). Empty = not configured: the app warns at startup and treats
    # every client as the socket peer (the proxy), so all visitors share one login limit.
    edge_shared_secret: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
