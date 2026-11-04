from functools import lru_cache

from pydantic import AliasChoices, Field
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
    # How many bots may be on meetings at once on the one local Vexa host, across all users
    # (`app/placement.py`). The box fits about one until measured (spec section 9); raise it only
    # after the load test. 0 sends no new bots (a drain). `MAX_ACTIVE_BOTS`, the name this setting
    # had before placement existed, is still read, so an old .env keeps working.
    bot_max_local: int = Field(
        default=1, ge=0, validation_alias=AliasChoices("bot_max_local", "max_active_bots")
    )
    # How many bots one user may have on meetings at once (spec section 9.4, step 7). It only bites
    # once `bot_max_local` is above it. 0 stops every user.
    user_bot_limit: int = Field(default=2, ge=0)
    # Teams links are recognised on calendar events, but the bot joins them only once the Teams
    # spike (P0.7) has passed and this is set: until then a Teams event reads "Can't join yet".
    teams_join_enabled: bool = False

    openai_api_key: str = ""
    transcription_model: str = "gpt-transcribe"
    notes_model: str = "gpt-6-luna"
    # The one call that reads the notes and transcript into decisions, open questions and review
    # items (app/extraction.py). Kept apart from `notes_model` so a stronger model can take over
    # this call alone if the eval gate (docs/spike/ai-extraction-eval.md) says luna is not enough.
    extract_model: str = "gpt-6-luna"
    # The call that translates notes and transcripts (app/translation.py). Its own setting so the
    # quality eval (tests/eval/translation_eval.py) can move it without touching the others.
    translate_model: str = "gpt-6-luna"

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
    mail_from: str = "Beyric Note Taker <no-reply@notes.beyrictech.com>"
    # Caps on mail that is not sign-in mail (notes ready now; shares, tasks and reminders later).
    # `mail_daily_cap`: once this many mails have gone since 00:00 UTC (the day Resend counts,
    # account mail included) notes mail waits for the next day. 80 leaves room under the Free
    # plan's 100 a day (spike ML1); raise it on a paid plan, 0 = no cap.
    # `mail_recipient_daily_cap`: notes mails one address may get in 24 hours; more are skipped.
    mail_daily_cap: int = 80
    mail_recipient_daily_cap: int = 5
    # The global budget for ALL outgoing mail, a rolling hour (spike ML1; it is what a sign-up flood
    # would otherwise spend, and Resend's daily quota with it). `mail_hourly_budget`: mails sent
    # in the last hour, of any kind; over it mail waits (pending, reason `over_budget`) and the
    # admins are told. Sign-in and account mail may use all of it, notes mail (and later share and
    # reminder mail) may not use the last `mail_hourly_reserve`, admin alerts are never held.
    # 0 = no budget. Optional.
    mail_hourly_budget: int = 30
    mail_hourly_reserve: int = 10
    # Where admin alerts (no bot slot free, a join that was not recorded) are mailed. Empty: each
    # admin's own address, unless their `notify_email` is off. Optional (`app/alerts.py`).
    admin_alert_email: str = ""

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

    # The scheduler's two loops (spec section 9.7): dispatch every 10 s (which is also how often
    # a standby retries the leader lock), the sync loop wakes every 60 s. Not required.
    scheduler_dispatch_seconds: int = 10
    scheduler_sync_seconds: int = 60

    # How long before a calendar meeting starts the bot is sent (spec 0a Q2: a bot needs about a
    # minute to open the browser and ask to join). Not required; P0.8 measures the real number.
    autojoin_lead_seconds: int = 60


@lru_cache
def get_settings() -> Settings:
    return Settings()
