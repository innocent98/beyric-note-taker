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
    # that carries it (app/edge.py). Empty = not configured yet: the app warns at startup and
    # falls back to `trusted_proxy_ips` below.
    edge_shared_secret: str = ""
    # TRANSITIONAL, used only while `edge_shared_secret` is empty: peer addresses whose
    # X-Forwarded-For is believed ("" = none; the Next.js container is always on top of this).
    # Forgeable through Docker's NAT, so it is ignored once the secret is set.
    trusted_proxy_ips: str = "172.28.0.1"


@lru_cache
def get_settings() -> Settings:
    return Settings()
