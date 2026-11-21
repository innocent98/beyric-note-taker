import re
import uuid
from datetime import UTC, date, datetime
from functools import lru_cache

from pydantic import AliasChoices, Field, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# `TRANSCRIPT_SOURCE=skribby` is allowed only with a Skribby model on this list: the notes then
# take their speaker names straight from Skribby's own transcript, so the model must do speaker
# diarization (Skribby's billing page lists it for ElevenLabs, not for Groq Whisper, which is the
# default `SKRIBBY_TRANSCRIPTION_MODEL`). `elevenlabs/scribe-v2` is the only one: it won the model
# bake-off on voice separation (meeting 34, 2026-11-13). `soniox/stt-async-v5` was removed (Skribby
# marks it deprecated and nothing used it). Add a model here only after reading a real answer
# from it.
TRANSCRIPT_SKRIBBY_MODELS = ("elevenlabs/scribe-v2",)
# The default `BOT_MAX_LOCAL` under Skribby: below the 25 concurrent bots our Skribby plan allows.
SKRIBBY_BOT_CEILING = 20
# One address: no spaces, commas, angle brackets or quotes; a dotted domain. Deliberately plain (no
# display names, no comments, no quoted local parts): this is a public address an operator types in.
_LABEL = r"[a-z0-9]([a-z0-9-]*[a-z0-9])?"
_PLAIN_EMAIL = re.compile(rf"[a-z0-9][a-z0-9._%+-]*@{_LABEL}(\.{_LABEL})+")


def parse_instant(value: str, *, name: str = "the timestamp") -> datetime:
    """An ISO 8601 timestamp WITH its time zone (`2026-11-22T09:30:00Z` or `...+01:00`), as UTC.
    Refused, never guessed: a missing zone (a Lagos clock read as UTC is an hour out), a date with
    no time, and anything that is not a timestamp. The error names `name` and never the value."""
    text = value.strip()
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        raise ValueError(
            f"{name} must be an ISO 8601 timestamp with a time zone, "
            "for example 2026-11-22T09:30:00Z"
        ) from None
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError(
            f"{name} has no time zone: write it with Z (UTC) or an offset such as +01:00, "
            "for example 2026-11-22T09:30:00Z"
        )
    if "T" not in text.upper() and " " not in text:
        raise ValueError(f"{name} must include a time, for example 2026-11-22T09:30:00Z")
    return moment.astimezone(UTC)


class Settings(BaseSettings):
    # `hide_input_in_errors`: a start-up validation error must never print the settings it was given
    # (they hold the secrets below; their `repr` is hidden too, `Field(repr=False)`).
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)

    @field_validator("transcript_source", mode="before")
    @classmethod
    def _blank_transcript_source_is_the_default(cls, value):
        """An empty `TRANSCRIPT_SOURCE=` line means "not set": our own transcription."""
        if isinstance(value, str) and not value.strip():
            return "own"
        return value

    @field_validator("transcript_source")
    @classmethod
    def _known_transcript_source(cls, value: str) -> str:
        name = value.strip().lower()
        if name not in ("own", "skribby"):
            raise ValueError(f"TRANSCRIPT_SOURCE is {value!r}; it must be own or skribby")
        return name

    @field_validator("billing_country_routes", mode="before")
    @classmethod
    def _blank_billing_routes_are_the_default(cls, value):
        """An empty `BILLING_COUNTRY_ROUTES=` line means "not set": the default table."""
        if isinstance(value, str) and not value.strip():
            from app.billing.routing import DEFAULT_ROUTES

            return DEFAULT_ROUTES
        return value

    @field_validator("billing_country_routes")
    @classmethod
    def _billing_routes_parse(cls, value: str) -> str:
        from app.billing.routing import parse_routes

        try:
            parse_routes(value)
        except ValueError as exc:
            raise ValueError(f"BILLING_COUNTRY_ROUTES is {value!r}: {exc}") from None
        return value

    @field_validator("organizer_bot_wait_seconds")
    @classmethod
    def _organizer_wait_is_short(cls, value: int) -> int:
        """A wait over ten minutes could eat the 15 minutes a meeting can still be joined; it
        stops startup instead of quietly shortening recordings."""
        if not 0 <= value <= 600:
            raise ValueError(f"ORGANIZER_BOT_WAIT_SECONDS is {value}; it must be 0 to 600")
        return value

    @field_validator("hours_mode", mode="before")
    @classmethod
    def _blank_hours_mode_is_off(cls, value):
        """An empty `HOURS_MODE=` line means "not set": the old cap rule only."""
        if isinstance(value, str) and not value.strip():
            return "off"
        return value

    @field_validator("hours_mode")
    @classmethod
    def _known_hours_mode(cls, value: str) -> str:
        name = value.strip().lower()
        if name not in ("off", "shadow", "enforce"):
            raise ValueError(f"HOURS_MODE is {value!r}; it must be off, shadow or enforce")
        return name

    @field_validator(
        "trial_seconds",
        "trial_days",
        "trial_daily_budget_seconds",
        "organizer_bot_wait_seconds",
        mode="before",
    )
    @classmethod
    def _blank_trial_setting_is_unset(cls, value, info: ValidationInfo):
        """An empty `TRIAL_SECONDS=` (or any of the S10 lines) means "not set": the default."""
        if isinstance(value, str) and not value.strip():
            return cls.model_fields[info.field_name].default
        return value

    @model_validator(mode="after")
    def _trial_settings_make_sense(self):
        """A budget under one trial would stop every trial without saying why, so it stops the
        process at startup instead."""
        budget = self.trial_daily_budget_seconds
        if budget and self.trial_seconds and budget < self.trial_seconds:
            raise ValueError(
                "TRIAL_DAILY_BUDGET_SECONDS is smaller than TRIAL_SECONDS: no trial could ever "
                "be given. Raise it, or set it to 0 for no daily cap"
            )
        return self

    @field_validator("feature_gates_mode", mode="before")
    @classmethod
    def _blank_feature_gates_mode_is_off(cls, value):
        """An empty `FEATURE_GATES_MODE=` line means "not set": every feature stays open."""
        if isinstance(value, str) and not value.strip():
            return "off"
        return value

    @field_validator("feature_gates_mode")
    @classmethod
    def _known_feature_gates_mode(cls, value: str) -> str:
        name = value.strip().lower()
        if name not in ("off", "shadow", "enforce"):
            raise ValueError(f"FEATURE_GATES_MODE is {value!r}; it must be off, shadow or enforce")
        return name

    @field_validator("transcript_shadow_percent", mode="before")
    @classmethod
    def _blank_shadow_percent_is_zero(cls, value):
        if isinstance(value, str) and not value.strip():
            return 0
        return value

    @field_validator("transcript_shadow_percent")
    @classmethod
    def _shadow_percent_in_range(cls, value: int) -> int:
        if not 0 <= value <= 100:
            raise ValueError(f"TRANSCRIPT_SHADOW_PERCENT is {value}; it must be from 0 to 100")
        return value

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

    @field_validator("bot_invite_email")
    @classmethod
    def _bot_invite_email_is_one_address(cls, value: str) -> str:
        """Empty is fine (the tip stays hidden). Otherwise exactly one plain address, lower-cased:
        the value is shown to people to type into a guest list, so a list, a display name
        (`Bot <a@b.c>`) or a stray space is refused at start, naming the setting."""
        value = value.strip().lower()
        if value and not _PLAIN_EMAIL.fullmatch(value):
            raise ValueError("BOT_INVITE_EMAIL must be one email address, such as note@example.com")
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

    @field_validator("billing_live_since")
    @classmethod
    def _billing_live_since_is_a_timestamp(cls, value: str) -> str:
        """Garbage is refused at startup even while it would be ignored (`BILLING_LIVE` false):
        a typo found on the day billing goes live is a typo that blocks the go-live."""
        if value.strip():
            parse_instant(value, name="BILLING_LIVE_SINCE")
        return value

    @model_validator(mode="after")
    def _billing_live_since_is_not_in_the_future(self):
        """While billing is live, a cutover still ahead would hide every live payment made before
        it, so it is refused. (Not checked with `BILLING_LIVE` false: there it has no effect.)"""
        if self.billing_live and self.billing_live_since.strip():
            cutover = parse_instant(self.billing_live_since, name="BILLING_LIVE_SINCE")
            if cutover > datetime.now(UTC):
                raise ValueError(
                    "BILLING_LIVE_SINCE is in the future: set it to the moment the closeout "
                    "was run, which has already passed"
                )
        return self

    @model_validator(mode="after")
    def _paystack_keys_match_billing_live(self):
        """Test and live keys are never mixed (billing spec 5.7): with `BILLING_LIVE` false only
        `sk_test_` / `pk_test_` keys are accepted, with it true only `sk_live_` / `pk_live_`. A
        wrong prefix stops the process at startup, naming the setting and never printing the key."""
        mode = "live" if self.billing_live else "test"
        for setting, prefix in (("paystack_secret_key", "sk"), ("paystack_public_key", "pk")):
            value = getattr(self, setting).strip()
            if value and not value.startswith(f"{prefix}_{mode}_"):
                raise ValueError(
                    f"{setting.upper()} is not a {mode}-mode key (it must start with "
                    f"{prefix}_{mode}_) while BILLING_LIVE is {str(self.billing_live).lower()}"
                )
        secret, webhook = self.paystack_secret_key.strip(), self.paystack_webhook_secret
        if webhook and not webhook.strip():
            raise ValueError("PAYSTACK_WEBHOOK_SECRET is blank; remove the line or set the secret")
        if webhook.strip() and not secret:
            raise ValueError(
                "PAYSTACK_WEBHOOK_SECRET is set but PAYSTACK_SECRET_KEY is not: a webhook could be "
                "checked and then never settled"
            )
        return self

    @model_validator(mode="after")
    def _flutterwave_key_kind(self):
        """Test and live keys are never mixed (billing spec 5.7), for Flutterwave. What its
        authentication page states is that test keys always carry `_TEST` (its example is a public
        key; the secret key's exact form is not shown, nor what a live key looks like). So the check
        rests on that one fact and nothing else: with `BILLING_LIVE` false a key without `_TEST` is
        refused (it is not a test key), with it true a key with `_TEST` is refused. A wrong key
        stops the process at startup, naming the setting and never printing the key."""
        for setting in ("flutterwave_secret_key", "flutterwave_public_key"):
            value = getattr(self, setting).strip()
            if not value:
                continue
            if self.billing_live and "_TEST" in value:
                raise ValueError(
                    f"{setting.upper()} is a Flutterwave TEST key (it contains _TEST) "
                    "while BILLING_LIVE is true"
                )
            if not self.billing_live and "_TEST" not in value:
                raise ValueError(
                    f"{setting.upper()} is not a Flutterwave TEST key (test keys contain _TEST) "
                    "while BILLING_LIVE is false"
                )
        secret_hash, secret = self.flutterwave_secret_hash, self.flutterwave_secret_key.strip()
        if secret_hash and not secret_hash.strip():
            raise ValueError("FLUTTERWAVE_SECRET_HASH is blank; remove the line or set the hash")
        if secret_hash.strip() and not secret:
            raise ValueError(
                "FLUTTERWAVE_SECRET_HASH is set but FLUTTERWAVE_SECRET_KEY is not: a webhook "
                "could be checked and then never settled"
            )
        return self

    @model_validator(mode="after")
    def _skribby_transcript_needs_a_diarizing_model(self):
        if (
            self.transcript_source == "skribby"
            and self.skribby_transcription_model.strip() not in TRANSCRIPT_SKRIBBY_MODELS
        ):
            known = ", ".join(TRANSCRIPT_SKRIBBY_MODELS)
            raise ValueError(
                f"TRANSCRIPT_SOURCE is 'skribby' but SKRIBBY_TRANSCRIPTION_MODEL is "
                f"{self.skribby_transcription_model!r}; it must be one of: {known}"
            )
        return self

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/notetaker"

    # Skribby, the one bot provider (`app/bot_provider.py`, `app/skribby.py`). The key is never
    # logged or shown: `repr=False` keeps it out of the settings' own repr. Without it no bot can
    # be sent: the web, worker and scheduler entry points refuse to start (`skribby.
    # require_configured`), and the provider refuses with `BotProviderNotConfigured` (retryable).
    # It is not checked while the settings are built, so tools and tests that never send a bot
    # need no key.
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
    # Whether a person's own note taker name (`users.bot_name`) is what the HOST sees on a signed-in
    # Google Meet bot. Live evidence 2026-11-14 (the owner's real Meet call): the signed-in bot
    # showed as the Google account's own name ("Beyric Note Taker") in the waiting list, the
    # participant list and as the sender of the chat message, NOT as `SKRIBBY_BOT_NAME`, so Skribby
    # ignores `bot_name` for the signed-in Meet bot. OFF (the default, and what the evidence says):
    # the "Admit '...'" line and the chat notice name the account's own name on Meet, and Settings
    # says so. Turn it on only if a later live Meet call shows the custom name. Set
    # `SKRIBBY_ACCOUNT_NAME` to the account's real name so those lines are true.
    bot_name_applies_when_signed_in: bool = False
    # Extra hosts a recording may be fetched from, comma separated (a name, or any subdomain of it).
    # Skribby's own domain and Amazon's storage are always allowed; anything else is refused until
    # an operator has seen where the recordings really are (`scripts/skribby_live_check.py` prints
    # the host). Optional.
    skribby_download_hosts: str = ""
    # Skribby transcribes the call too; its text is only used to know who spoke when (our own
    # transcription still writes the transcript), unless `transcript_source` is `skribby`, when
    # this model's text IS the transcript (`elevenlabs/scribe-v2`, 0.20 USD an hour, has speaker
    # diarization). `groq/whisper-large-v3-turbo` is on the Free plan and the cheapest (0.04 USD an
    # hour); `none` turns it off and with it the speaker names.
    skribby_transcription_model: str = "groq/whisper-large-v3-turbo"
    # What the notes are made from. `own` (the default, and today's behaviour): our OpenAI
    # transcription of the audio. `skribby`: the bot provider's own transcript, for meetings a
    # Skribby bot ran, falling back to ours when it is missing or too short
    # (`app/transcript_source.py`). Anything else stops the process at startup.
    transcript_source: str = "own"
    # The per-person hours rule (billing spec, stage S1). `off` (default): only the old cap rule
    # runs. `shadow`: it still decides, and each recording start also asks `app/entitlements.py`
    # and logs one line when the two disagree (`hours_shadow`). `enforce` is accepted and does
    # nothing yet (stage S2 makes the new rule decide). Anything else stops the process at startup.
    hours_mode: str = "off"
    # The feature gates (billing spec 2.3, stage S3): Ask Beyric, new translations and rewriting
    # notes with another template need a live plan or trial (`app/feature_gates.py`). `off`
    # (default): every feature stays open and nothing extra is read. `shadow`: they stay open and
    # one line is logged each time the gate would have refused. `enforce`: 403 `plan_inactive`.
    # Meant to be switched on only at go-live (S11). Anything else stops the process at startup.
    feature_gates_mode: str = "off"
    # Payments (billing spec section 5, stage S4). `false` (default): the webhook route
    # `POST /api/billing/webhooks/{provider}` answers 404 and nothing is stored. No payment provider
    # is built yet, so `true` still accepts nothing until Paystack (S6) or Flutterwave (S7) lands.
    billing_enabled: bool = False
    # Which currency and provider each billing country pays with (billing spec 5.2, stage S5):
    # `COUNTRY=CURRENCY:provider` or `COUNTRY=off` entries, `*` for every country with no entry of
    # its own. Default: Nigeria to naira through Paystack, any other country to dollars through
    # Flutterwave. A country with an entry never falls through to `*`. To switch a provider off
    # write `off` for its countries (`NG=off,*=USD:flutterwave`); deleting the `NG` entry would
    # send Nigeria to the `*` route. Only paystack (NGN) and flutterwave (USD) are accepted.
    billing_country_routes: str = "NG=NGN:paystack,*=USD:flutterwave"
    # The accounts that may use the billing routes while `BILLING_ENABLED` is true (S5): a
    # comma-separated list of emails. CLOSED by default: empty means no account may (spec section 8:
    # "for allow-listed accounts only"); `*` in the list means every account. An account not allowed
    # gets the same 404 as billing being off. The pilot of the pay screens is this list.
    billing_allowed_emails: str = ""
    # Which kind of provider key this process may hold (billing spec 5.7, stage S6). `false`
    # (default): TEST keys only (`sk_test_...`); no real money can move. `true`: LIVE keys only
    # (`sk_live_...`). A key of the other kind stops the process at startup naming the setting; the
    # key itself is never printed. Switch on only at go-live (S11).
    billing_live: bool = False
    # The go-live cutover `T` (an ISO 8601 timestamp WITH its zone, `2026-11-22T09:30:00Z`; empty =
    # none). While `BILLING_LIVE` is true, a payment created before it was made with test keys and
    # is hidden from a person's lists and not counted as their first plan payment
    # (`app/billing/cutover.py`). Set it to the same value the one-time closeout script was run
    # with (`scripts/billing_close_test_data.py --before`). With `BILLING_LIVE` false it is
    # ignored. A value that is not a timestamp with a zone, or that is in the future while
    # `BILLING_LIVE` is true, stops the process at startup.
    billing_live_since: str = ""
    # Where a provider sends the payer back after the hosted checkout (stage S8). Empty: the
    # app's own `/billing/return` page (`PUBLIC_BASE_URL` + `/billing/return`). The page only shows
    # the payment's state; nothing is granted there, so a wrong address costs nothing.
    billing_return_url: str = ""
    # Invoices (stage S9, billing spec section 6). The seller's details are read from here and
    # snapshotted onto each invoice when it is issued, so a later edit never rewrites an old one.
    # NOTHING here is a real registration or tax number: they are empty until Adebayo supplies
    # them, and an empty one is left off the invoice (the invoice says nothing, it never invents).
    # `BILLING_VAT_RATE_BP` is the VAT INCLUDED in the price charged, in basis points (750 = 7.5%);
    # default 0: no VAT line. An added-on VAT (a higher charge) is not built: it would change every
    # amount a provider is asked for, and the accountant has not answered (spec section 6).
    billing_seller_name: str = "Beyrichq LTD"
    billing_seller_address: str = ""
    billing_seller_registration_no: str = ""
    billing_seller_tax_id: str = ""
    billing_seller_email: str = ""
    billing_vat_rate_bp: int = Field(default=0, ge=0, le=5000)
    # Paystack, naira (stage S6). All three stay empty until Adebayo puts them in the server's
    # `.env`: with no secret key set, every call the adapter could make raises before it leaves the
    # process. `repr=False` keeps them out of the settings' own repr; no log line or error ever
    # carries one. The secret key calls the API and (while no webhook secret is set) signs the
    # legacy webhook header; the webhook signing secret (`whsec_...`, dashboard: Developers > API
    # configuration) signs the `Paystack-Signature` header. The public key is read by nothing yet
    # (hosted checkout needs only the secret key); it is accepted so a later screen can use it.
    paystack_secret_key: str = Field(default="", repr=False)
    paystack_public_key: str = Field(default="", repr=False)
    paystack_webhook_secret: str = Field(default="", repr=False)
    # Flutterwave, dollars (stage S7), from the server `.env` only. Empty by default: with no secret
    # key every call the adapter could make raises before it leaves the process. The secret key
    # calls the API; the secret hash is the value set in the Flutterwave dashboard that comes back
    # in the `verif-hash` header of every webhook (empty refuses every webhook); the public key is
    # kept for the day a page needs it (the adapter does not read it). Like Paystack's, a key of
    # the wrong kind for `BILLING_LIVE` stops the process at startup (`_flutterwave_key_kind`).
    flutterwave_secret_key: str = Field(default="", repr=False)
    flutterwave_public_key: str = Field(default="", repr=False)
    flutterwave_secret_hash: str = Field(default="", repr=False)
    # Whether a diarization tag that carries no `speaker_name` may be named by elimination from
    # Skribby's `potential_speaker_names` (the list minus the bot's names minus the names other tags
    # have; `app/skribby.py::_tag_names`). ON (the default, today's behaviour, kept while the saved
    # evidence is collected; `skribby_segment_evidence`). OFF: a tag is named only by the
    # `speaker_name` its own segments carry, and any other tag stays "Unknown speaker". The
    # elimination may have named a tag after someone who is not in the call (meeting 29,
    # 2026-11-10); the evidence will show whether it did. The same switch covers the sole
    # candidate rule (one name on every segment of the tag, nobody else has it; 2026-11-16).
    skribby_name_by_elimination: bool = True
    # Whether a diarization tag still unnamed after Skribby's own name, elimination and the sole
    # candidate may be named after the participant Meet's own speaking indicator shows speaking most
    # over its segments (`app/meet_timeline.py`, `app/skribby.py::_name_by_timeline`;
    # docs/sop/2026-11-17-name-by-meet-timeline.md). ON by default: it only acts on a clear margin,
    # after a clock check, and in a meeting where the timeline already agrees with the names Skribby
    # gave; the saved evidence records `how: meet_timeline` for every name it gave. OFF: no extra
    # request to Skribby and no name from the timeline. Independent of the elimination switch. A
    # value that is not a boolean stops startup, like the other switches.
    name_by_meet_timeline: bool = True
    # Whether a Skribby segment moves to the person Meet's speaking timeline shows speaking over
    # it, when exactly one person was lit and that person is not the one its tag says
    # (`app/speaker_correction.py`; docs/sop/2026-11-19-line-level-speaker-correction.md). ON by
    # default; this is the one emergency switch, not a rollout dial. It acts only with
    # TRANSCRIPT_SOURCE=skribby, only on a meeting whose speaker events were kept (Teams, Zoom and
    # a failed fetch change nothing), only after the same clock check the timeline naming uses,
    # and it never stops a meeting. OFF: every line keeps its tag's name, as before it existed; the
    # rebuild script then rebuilds without it as well. A value that is not a boolean stops startup.
    speaker_line_correction: bool = True
    # Several accounts hold the same calendar event with the same meeting link: the ORGANIZER's bot
    # records (billing spec 2.5, stage S12; docs/sop/2026-11-19-organizer-bot-preferred.md). A
    # guest's join runner waits `organizer_bot_wait_seconds` after its run time before placing,
    # but only while another account that is the organizer is known to hold the event and can
    # place (it has an account, a live join, a good calendar, hours or a plan); a lone guest, an
    # organizer with no account, no join, no hours or no plan never waits, so the first placed bot
    # records as before. ON by default; this is the one emergency switch, not a rollout dial. OFF:
    # the first placed bot wins, as before it existed. A value that is not a boolean stops startup.
    prefer_organizer_bot: bool = True
    # How long a guest's bot waits for the organizer's (a scheduling delay on the runner's 10 s
    # tick, never a sleep). 0 to 600; anything else stops startup. Meaningless while
    # PREFER_ORGANIZER_BOT is false.
    organizer_bot_wait_seconds: int = 120
    # In `skribby` mode: the share of meetings (0 to 100, chosen by meeting id) for which our own
    # transcription is ALSO run, only to compare numbers with Skribby's and then thrown away
    # (`transcript_shadow_metrics`). Costs OpenAI transcription for that share. 0 = never.
    transcript_shadow_percent: int = 0
    # The address of the signed-in Skribby bot's Google account (`SKRIBBY_ACCOUNT_ID`). On Google
    # Meet a bot whose address is on the event's guest list skips the waiting room, so the app can
    # tell people to invite it ("Add note@... to your event to skip the wait"). Public, not a
    # secret. Empty = no such tip. Shown only when it is true, see `bot_invite_email_shown`.
    bot_invite_email: str = ""
    # The line the bot posts in the meeting chat when it joins ("Hi, I'm <bot>. <owner> added me to
    # record this meeting ..."): Skribby only (`app/chat_notice.py`). OFF until one real meeting on
    # each platform has shown it works; a Skribby refusal of the parameter is retried without it.
    bot_chat_notice_enabled: bool = False

    @property
    def shown_bot_name(self) -> str:
        """The name the host sees in the meeting for the bot, for the owner's "Admit '...'" line:
        the signed-in account's own name when the bot signs in, else the guest name."""
        if self.skribby_account_id:
            return self.skribby_account_name.strip() or self.skribby_bot_name
        return self.skribby_bot_name

    @property
    def bot_invite_email_shown(self) -> str | None:
        """The address people may be told to invite, or None. It is told only when it is true: the
        bot is signed in (`skribby_account_id`) as that address. A guest bot (no account) is not on
        anyone's guest list, so there is nothing to invite."""
        if self.skribby_account_id and self.bot_invite_email:
            return self.bot_invite_email
        return None

    poll_interval_seconds: int = 30
    # How long the bot may sit in a call with no one else speaking before it leaves on its own
    # (Skribby's `last_person_detection`, in whole minutes, rounded up). It is a silence rule, not a
    # headcount, so a short window can cut a quiet meeting short. 0 = send nothing and keep
    # Skribby's default.
    bot_left_alone_seconds: int = 300
    max_poll_hours: int = 6
    # How many bots may be on meetings at once across all users (`app/placement.py`). The bots run
    # on Skribby's servers (our plan there allows 25 at once; Skribby's billing page has a "Request
    # an increase"), so this is only a high safety ceiling, default 20 (`SKRIBBY_BOT_CEILING`);
    # `USER_BOT_LIMIT` is the real per-person control. Set it in `.env` to override. 0 sends no new
    # bots (a drain). `MAX_ACTIVE_BOTS`, the name this setting had before placement existed, is
    # still read, so an old .env keeps working.
    bot_max_local: int = Field(
        default=SKRIBBY_BOT_CEILING,
        ge=0,
        validation_alias=AliasChoices("bot_max_local", "max_active_bots"),
    )
    # How many bots one user may have on meetings at once (spec section 9.4, step 7). It is the
    # real per-person control (the capacity ceiling above is high). 0 stops every user.
    user_bot_limit: int = Field(default=2, ge=0)
    # `HOURS_MODE=enforce`: while a person has a meeting running or just placed, a second one starts
    # only if their hours cover a reserve for each of them: this many minutes per meeting (the
    # calendar event's length instead, when known). A first meeting is not affected. 0 turns the
    # reserve off. See `app/entitlements.py` (`check_join`).
    hours_reserve_minutes: int = Field(default=30, ge=0, le=360)
    # The one-time trial (billing stage S10, spec 3.8). `TRIAL_SECONDS` is the size of the trial
    # grant: 0 (default) turns the trial off, 10800 is 3 hours. A person gets it once, at the first
    # confirmed sign-in, and it ends at the hours used or `TRIAL_DAYS` days after it is given,
    # whichever comes first, in any `HOURS_MODE` (`app/trial.py`). `TRIAL_DAILY_BUDGET_
    # SECONDS` caps the trial seconds given in any 24 hours (0 = no cap): the brake on how many
    # trials start while sign-up is open; people past it wait and get theirs when the window frees.
    trial_seconds: int = Field(default=0, ge=0, le=100 * 3600)
    trial_days: int = Field(default=14, ge=1, le=365)
    trial_daily_budget_seconds: int = Field(default=0, ge=0)
    # Whether the bot joins Microsoft Teams meetings (a pasted link, a calendar event): on by
    # default (Skribby joins Teams as a guest). `TEAMS_JOIN_ENABLED=false` switches it off. Off, a
    # Teams event reads "Microsoft Teams meetings cannot be joined yet" and a pasted Teams link is
    # refused.
    teams_join_enabled: bool = True

    openai_api_key: str = Field(default="", repr=False)
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
    owner_password: str = Field(default="", repr=False)
    session_secret: str = Field(default="", repr=False)
    cookie_secure: bool = True
    # The secret nginx sends in X-Edge-Secret. The app believes X-Forwarded-For only on a request
    # that carries it (app/edge.py). Empty = not configured: the app warns at startup and treats
    # every client as the socket peer (the proxy), so all visitors share one login limit.
    edge_shared_secret: str = Field(default="", repr=False)

    # The public address of the site: mail links and the Google redirect URIs are built from it.
    public_base_url: str = "https://notes.beyrictech.com"
    # "resend", "console" (development only) or "" = resend when `resend_api_key` is set, the
    # console outside production, and no mailer at all in production without a key (mail then
    # stays in the outbox and password sign-up stays off).
    mail_provider: str = ""
    resend_api_key: str = Field(default="", repr=False)
    # The OAuth client for "Sign in with Google". Both set = the feature exists; either empty =
    # the routes answer 404 and `GET /api/pilot` says `google_signin: false`.
    google_client_id: str = ""
    google_client_secret: str = Field(default="", repr=False)
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
    token_enc_key: str = Field(default="", repr=False)
    token_enc_key_previous: str = Field(default="", repr=False)
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
    microsoft_client_secret: str = Field(default="", repr=False)
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

    # Multi-team (docs/superpowers/specs/2026-11-13-multi-team-design.md). Off by default: while it
    # is off a person is on one team at a time exactly as before, and accepting an invitation
    # dissolves their team of one or is refused with `already_on_team`. On: a person keeps their
    # personal team and may belong to several others. Switch it on only after the frontend that
    # understands several teams is live (stage 4). Restart web, worker and scheduler after changing.
    multi_team_enabled: bool = False

    # Recurring meetings follow the meetings (docs/superpowers/specs/2026-11-14-series-follow-
    # meetings.md). Off by default: a series is read and written exactly as before. On (and only
    # with MULTI_TEAM_ENABLED on too): a series row belongs to one team scope, and a meeting moves
    # to the row of its own scope when it is shared, unshared, moved, or its recorder leaves. Switch
    # it on only AFTER `scripts/backfill_series_follow.py --apply` has run. After changing it, run
    # `docker compose up -d --force-recreate web worker scheduler` (a plain restart does not re-read
    # `.env`).
    series_follow_meetings: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
