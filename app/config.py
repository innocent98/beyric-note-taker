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
    # How many bots may be on meetings at once, across all users. The Vexa box fits about one
    # until measured (spec section 9); raise it only after the load test. Phase C replaces this
    # with a per-host dispatcher.
    max_active_bots: int = 1

    openai_api_key: str = ""
    transcription_model: str = "gpt-transcribe"
    notes_model: str = "gpt-6-luna"

    audio_retention_days: int = 7
    audio_dir: str = "data/audio"

    app_env: str = "production"
    # Read only by migration 0006, which creates the admin account from them. After that the
    # login uses the `users` table and OWNER_PASSWORD can be removed from .env.
    owner_email: str = ""
    owner_password: str = ""
    session_secret: str = ""
    cookie_secure: bool = True
    # The secret nginx sends in X-Edge-Secret. The app believes X-Forwarded-For only on a request
    # that carries it (app/edge.py). Empty = not configured: the app warns at startup and treats
    # every client as the socket peer (the proxy), so all visitors share one login limit.
    edge_shared_secret: str = ""

    # The public address of the site: mail links and the Google redirect URIs are built from it.
    public_base_url: str = "https://notes.beyrictech.com"
    # "resend", "console" (development only) or "" = resend when `resend_api_key` is set, the
    # console outside production, and no mailer at all in production without a key (mail then
    # stays in the outbox and password sign-up stays off).
    mail_provider: str = ""
    resend_api_key: str = ""
    # The OAuth client for "Sign in with Google". Both set = the feature exists; either empty =
    # the routes answer 404 and `GET /api/pilot` says `google_signin: false`.
    google_client_id: str = ""
    google_client_secret: str = ""
    mail_from: str = "Beyric Note Taker <no-reply@beyrictech.com>"


@lru_cache
def get_settings() -> Settings:
    return Settings()
