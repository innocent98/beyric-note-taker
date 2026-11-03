from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/notetaker"

    vexa_base_url: str = "http://localhost:8056"
    vexa_api_key: str = ""
    bot_name: str = "Beyric Note Taker (recording)"
    poll_interval_seconds: int = 30
    # How long the bot may sit in a call with no one else speaking before it leaves on its own
    # (Vexa `automatic_leave.max_time_left_alone`). It is a silence rule, not a headcount, so a
    # short window can cut a quiet meeting short. Vexa's own default is 10 minutes. 0 = send
    # nothing and keep Vexa's default.
    bot_left_alone_seconds: int = 300
    max_poll_hours: int = 6
    # How many bots may be on meetings at once, across all users. The Vexa box fits about one
    # until measured (spec section 9); raise it only after the load test. Phase C replaces this
    # with a per-host dispatcher.
    max_active_bots: int = 1
    # Teams links are recognised on calendar events, but the bot joins them only once the Teams
    # spike (P0.7) has passed and this is set: until then a Teams event reads "Can't join yet".
    teams_join_enabled: bool = False

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
    # True while the Google Cloud project is in Testing: a refresh token then lapses after 7 days,
    # and a connection records when (`expected_expiry_at`). Off once the project is in production.
    google_oauth_testing: bool = False
    # True once Google has verified the app for its sensitive scopes. Until then a tester sees
    # "Google hasn't verified this app" on the calendar consent screen, so the connect call tells
    # the frontend to explain it first.
    google_app_verified: bool = False
    mail_from: str = "Beyric Note Taker <no-reply@beyrictech.com>"

    # Hosts a pasted calendar link may point at, comma separated: an exact name, or `.suffix`
    # (every subdomain of it). Only an admin edits this, through the environment, never a user.
    # The defaults are the hosts Google Calendar and Outlook publish private links on, not yet
    # confirmed against real feeds (spike C19).
    ics_allowed_hosts: str = "calendar.google.com,outlook.office365.com,outlook.live.com"

    # The key that encrypts stored secrets (calendar refresh tokens, a pasted calendar link): 32
    # random bytes, base64 (`openssl rand -base64 32`). It lives only in the server .env, never in
    # the database or a backup. Rotation: new key here, old key in `token_enc_key_previous`, raise
    # `token_enc_key_version` by one, re-encrypt, then drop the previous key (app/vault.py).
    token_enc_key: str = ""
    token_enc_key_previous: str = ""
    token_enc_key_version: int = 1
    # Set true where the calendar features (Google connect, pasted calendar links) are switched
    # on. In production the app then refuses to start without `token_enc_key`, as it already does
    # once `google_client_id` is set.
    calendar_enabled: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
