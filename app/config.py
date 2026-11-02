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
    notes_model: str = "gpt-5.4-mini"

    audio_retention_days: int = 7
    audio_dir: str = "data/audio"

    owner_password: str = ""
    session_secret: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
