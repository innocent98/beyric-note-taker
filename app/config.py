import re
import uuid
from datetime import date
from functools import lru_cache

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @field_validator("bot_provider", mode="before")
    @classmethod
    def _blank_bot_provider_is_the_default(cls, value):
        """An empty `BOT_PROVIDER=` line (a commented-out value left as a bare key) means "not
        set", the same as leaving it out, so it cannot stop every process at startup."""
        if isinstance(value, str) and not value.strip():
            return "vexa"
        return value

    @field_validator("bot_provider")
    @classmethod
    def _known_bot_provider(cls, value: str) -> str:
        from app.bot_provider import PROVIDER_FACTORIES

        name = value.strip().lower()
        if name not in PROVIDER_FACTORIES:
            known = ", ".join(sorted(PROVIDER_FACTORIES))
            raise ValueError(f"BOT_PROVIDER is {value!r}; it must be one of: {known}")
        return name

    @field_validator("skribby_region")
    @classmethod
    def _known_skribby_region(cls, value: str) -> str:
        region = value.strip().lower() or "eu"
        if region not in ("eu", "jp"):
            raise ValueError(f"SKRIBBY_REGION is {value!r}; it must be eu or jp")
        return region

    @field_validator("skribby_account_id")
    @classmethod
    def _skribby_account_id_is_a_uuid(cls, value: str) -> str:
        value = value.strip()
        if value:
            try:
                value = str(uuid.UUID(value))
            except ValueError:
                raise ValueError("SKRIBBY_ACCOUNT_ID must be the account's id (a UUID)") from None
        return value

    @field_validator("microsoft_authority")
    @classmethod
    def _known_microsoft_authority(cls, value: str) -> str:
        """Only the two audiences the registration can serve are accepted (Outlook spec 3.2): a
        stray value would send people to an endpoint we never designed for."""
        authority = value.strip().lower() or "consumers"
        if authority not in ("consumers", "common"):
            raise ValueError(f"MICROSOFT_AUTHORITY is {value!r}; it must be consumers or common")
        return authority

    @field_validator("microsoft_allowed_tenants")
    @classmethod
    def _microsoft_tenants_are_guids(cls, value: str) -> str:
        """A comma separated list of directory (tenant) ids, lower-cased. Anything that is not a
        GUID is refused at start: a typo here would silently admit or block a whole organisation."""
        ids = []
        for part in value.split(","):
            part = part.strip().lower()
            if not part:
                continue
            if not re.fullmatch(r"[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}", part):
                raise ValueError(
                    "MICROSOFT_ALLOWED_TENANTS must be tenant ids (GUIDs), comma separated"
                )
            ids.append(part)
        return ",".join(dict.fromkeys(ids))

    @field_validator("microsoft_secret_expires_on")
    @classmethod
    def _microsoft_secret_expiry_is_a_date(cls, value: str) -> str:
        value = value.strip()
        if value:
            try:
                date.fromisoformat(value)
            except ValueError:
                raise ValueError("MICROSOFT_SECRET_EXPIRES_ON must be a date, YYYY-MM-DD") from None
        return value

    @property
    def microsoft_allowed_tenant_ids(self) -> frozenset[str]:
        """The tenants a Microsoft connection may come from; empty means any."""
        return frozenset(t for t in self.microsoft_allowed_tenants.split(",") if t)

    @model_validator(mode="after")
    def _skribby_needs_its_key(self):
        """A process told to use Skribby with no key would fail at the first meeting; stop it at
        startup instead, naming the setting (never printing any key)."""
        if self.bot_provider == "skribby" and not self.skribby_api_key.strip():
            raise ValueError("BOT_PROVIDER is 'skribby' but SKRIBBY_API_KEY is not set")
        return self

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/notetaker"

    # Which bot provider puts the bot in meetings (`app/bot_provider.py`): `vexa` (the default, the
    # self-hosted Vexa) or `skribby` (the hosted Skribby, `app/skribby.py`). An unknown value, or
    # `skribby` without its key, stops the process at startup instead of failing at the first
    # meeting.
    bot_provider: str = "vexa"
    # Skribby (only read when `bot_provider` is `skribby`, or when a meeting that Skribby ran is
    # still being worked on). The key is never logged or shown: `repr=False` keeps it out of the
    # settings' own repr.
    skribby_api_key: str = Field(default="", repr=False)
    # The id of an Authenticated Account made in Skribby's dashboard (the Google account the bot
    # signs in as). Empty = the bot joins as a guest named `skribby_bot_name`.
    skribby_account_id: str = ""
    # What the signed-in bot is called in a meeting (the Google account's own name; Skribby ignores
    # `bot_name` once it is signed in). Used only in the owner's "Admit '...'" line. Optional.
    skribby_account_name: str = ""
    # The two Skribby regions keep their data apart: `eu` (default) or `jp`.
    skribby_region: str = "eu"
    # What a guest bot is called in a meeting. Skribby's naming guide says names that sound like a
    # recorder ("Bot", "Notetaker", "Recording", "AI") raise Google's bot detection, and a name
    # that sounds like a person or a service desk does not.
    skribby_bot_name: str = "Beyric Meeting Support"
    # Extra hosts a recording may be fetched from, comma separated (a name, or any subdomain of it).
    # Skribby's own domain and Amazon's storage are always allowed; anything else is refused until
    # an operator has seen where the recordings really are (`scripts/skribby_live_check.py` prints
    # the host). Optional.
    skribby_download_hosts: str = ""
    # Skribby transcribes the call too; its text is only used to know who spoke when (our own
    # transcription still writes the transcript). `groq/whisper-large-v3-turbo` is on the Free
    # plan and the cheapest (0.04 USD an hour); `none` turns it off and with it the speaker names.
    skribby_transcription_model: str = "groq/whisper-large-v3-turbo"
    vexa_base_url: str = "http://localhost:8056"
    vexa_api_key: str = ""
    bot_name: str = "Beyric Note Taker (recording)"

    def bot_name_for(self, provider: str | None) -> str:
        """The name the host sees in the meeting for the provider that ran a meeting, for the
        owner's "Admit '...'" line (so a meeting Vexa runs still names Vexa's bot after the setting
        moved to Skribby)."""
        if (provider or self.bot_provider) == "skribby":
            if self.skribby_account_id:
                return self.skribby_account_name.strip() or self.skribby_bot_name
            return self.skribby_bot_name
        return self.bot_name

    @property
    def shown_bot_name(self) -> str:
        return self.bot_name_for(self.bot_provider)

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
    # The most output tokens the extraction call may ask for on its retry after a cut-off.
    extract_max_output_tokens: int = 32000
    # The call that translates notes and transcripts (app/translation.py). Its own setting so the
    # quality eval (tests/eval/translation_eval.py) can move it without touching the others.
    translate_model: str = "gpt-6-luna"
    # The call that answers a question on the Ask Beyric page (app/ask.py), and how many such
    # model-calling questions one person may ask in a rolling 24 hours. Both optional.
    ask_model: str = "gpt-6-luna"
    ask_daily_limit: int = Field(default=30, ge=0)
    # The call that finds topics repeated across a series' meetings (app/topics.py): one per series
    # when its last meeting's notes are read. Optional.
    topics_model: str = "gpt-6-luna"

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
    # admins are told. Password-reset and invitation mail may use all of it; verification mail (the
    # form can make the site send it to anyone), notes mail (and later share and reminder mail)
    # may not use the last `mail_hourly_reserve`; admin alerts are never held.
    # 0 or less = NO budget (unlimited; it does not stop mail, and a warning is logged at start).
    # Optional.
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

    # The Microsoft (Outlook) calendar, Outlook spec 9.2. All optional while the flag is false.
    # The OAuth client of the Azure app registration; both set = the client exists. The flag is the
    # operator's switch for the whole feature: the routes answer 404 `calendar_unavailable` and the
    # UI draws nothing until it is true (and the Teams gate of spec section 7 is met).
    microsoft_client_id: str = ""
    microsoft_client_secret: str = ""
    microsoft_calendar_enabled: bool = False
    # Which accounts our authorize endpoint accepts: `consumers` (personal Outlook.com, Hotmail,
    # Live: the pilot) or `common` (work and school as well, once publisher verification is done).
    microsoft_authority: str = "consumers"
    # Optional, comma separated tenant ids a connection must come from (empty = any).
    microsoft_allowed_tenants: str = ""
    # When the client secret in Azure expires, YYYY-MM-DD: drives the 30-day-before reminder.
    microsoft_secret_expires_on: str = ""
    # True once Microsoft's publisher verification is done; until then the connect call may tell
    # the frontend to explain the "unverified" screen (only if M0 shows Microsoft shows one).
    microsoft_publisher_verified: bool = False
    # Off by default. When on, a WORK connection also reads the organisation's verified domains
    # and an unanswered invite from an organiser in them joins like a colleague's. That trusts the
    # organiser's address as Exchange Online reports it (spec 8.1 #8: confirm with the M0 run, S4).
    microsoft_organizer_domain_trust: bool = False

    # The scheduler's two loops (spec section 9.7): dispatch every 10 s (which is also how often
    # a standby retries the leader lock), the sync loop wakes every 60 s. Not required.
    scheduler_dispatch_seconds: int = 10
    scheduler_sync_seconds: int = 60

    # How long before a calendar meeting starts the bot is sent (spec 0a Q2: a bot needs about a
    # minute to open the browser and ask to join). Not required; P0.8 measures the real number.
    autojoin_lead_seconds: int = 60
    # Auto-join is ON by default (decision 2026-11-04: people connect their calendar and leave, so
    # it must work with no switch). This is the operator's emergency stop, not a user setting: set
    # it to false and the scheduler stops sending bots from calendars (rows wait; nothing else
    # changes) and records since when, which the app shows. Restart `scheduler` after changing it.
    autojoin_enabled: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
