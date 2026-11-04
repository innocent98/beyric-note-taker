from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

STATUSES = ("bot_sent", "processing", "ready", "failed")
STEPS = ("waiting", "audio", "transcribing", "speakers", "notes", "done")
MEETING_SOURCES = ("manual", "calendar", "instant_meet")
USER_STATUSES = ("active", "suspended", "deleted")
TOKEN_PURPOSES = ("verify_email", "reset_password", "waitlist_invite")
OAUTH_PURPOSES = ("signin", "calendar", "meet_scope")
WAITLIST_STATUSES = ("waiting", "invited", "joined", "removed")
SEVERITIES = ("info", "warning", "error")
MAIL_STATUSES = ("pending", "sent", "failed", "skipped")
SUPPRESSION_REASONS = ("bounce", "complaint", "unsubscribed")
AUTO_JOIN_RULES = ("all", "organizer", "none")
INSTANT_MEET_MODES = ("events", "poll", "off")
CONNECTION_PROVIDERS = ("google", "ics")
CONNECTION_STATUSES = ("connected", "degraded", "needs_reconnect", "disconnected")
RECONNECT_REASONS = ("expired", "revoked", "error")
MEET_SUBSCRIPTION_STATES = ("none", "active", "needs_reconnect", "error")
EVENT_PLATFORMS = ("google_meet", "zoom", "teams")
EVENT_STATUSES = ("confirmed", "tentative", "cancelled")
EVENT_RESPONSES = ("accepted", "declined", "tentative", "needsAction")
JOIN_SOURCES = ("calendar", "instant_meet")
JOIN_STATES = (
    "scheduled",
    "dispatching",
    "joined",
    "completed",
    "skipped",
    "cancelled",
    "no_capacity",
    "failed",
)
# A join in one of these states holds its meeting code: only one bot per code at a time.
LIVE_JOIN_STATES = ("dispatching", "no_capacity", "joined")
AI_RUN_KINDS = ("extract", "translate")
AI_RUN_STATUSES = ("ok", "failed")
# `pending` waits for the worker (or for a retry), `running` is being worked on, `done` is finished,
# `failed` gave up after the retries, `skipped` means only the decisions were read from the notes
# and the model was not asked (an old meeting, per the backfill rules). Null = notes not written.
EXTRACTION_STATUSES = ("pending", "running", "done", "failed", "skipped")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} in ({', '.join(repr(v) for v in values)})"


class Base(DeclarativeBase):
    pass


class User(Base):
    """An account. Emails are stored lower-cased (the check keeps it so), which makes the plain
    unique constraint a unique-on-lower(email) one."""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(_in("status", USER_STATUSES), name="ck_users_status"),
        CheckConstraint("email = lower(email)", name="ck_users_email_lower"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    name: Mapped[str | None] = mapped_column(String(200))
    password_hash: Mapped[str | None] = mapped_column(Text)  # null for a Google-only account
    google_sub: Mapped[str | None] = mapped_column(String(64), unique=True)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default=sa.false())
    status: Mapped[str] = mapped_column(String(16), default="active", server_default="active")
    # Null = use `app_state.default_hours_cap_seconds`; an admin's override otherwise.
    hours_cap_seconds: Mapped[int | None] = mapped_column(Integer)
    # Bumped by logout and password change: revokes this user's sessions only.
    session_epoch: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    terms_accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    terms_version: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # When the admin deleted the account (status `deleted`); the purge gives up a day after it.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthToken(Base):
    """A single-use token for email verification, password reset or a waitlist invite. Only the
    hash is stored."""

    __tablename__ = "auth_tokens"
    __table_args__ = (
        CheckConstraint(_in("purpose", TOKEN_PURPOSES), name="ck_auth_tokens_purpose"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE")
    )  # null for a waitlist invite
    email: Mapped[str] = mapped_column(String(254))
    purpose: Mapped[str] = mapped_column(String(32))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # A verification link confirms the password of the sign-up that made it (never a later
    # one's): the hash is kept here until the link is used and the person types it again.
    pending_password_hash: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OAuthFlow(Base):
    """State of one Google OAuth round trip; short-lived and swept."""

    __tablename__ = "oauth_flows"
    __table_args__ = (
        CheckConstraint(_in("purpose", OAUTH_PURPOSES), name="ck_oauth_flows_purpose"),
    )

    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    purpose: Mapped[str] = mapped_column(String(16))
    user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE")
    )
    code_verifier: Mapped[str] = mapped_column(String(128))
    nonce: Mapped[str] = mapped_column(String(64))
    return_to: Mapped[str | None] = mapped_column(String(500))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class WaitlistEntry(Base):
    __tablename__ = "waitlist_entries"
    __table_args__ = (
        CheckConstraint(_in("status", WAITLIST_STATUSES), name="ck_waitlist_entries_status"),
        CheckConstraint("email = lower(email)", name="ck_waitlist_entries_email_lower"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    name: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(16), default="waiting", server_default="waiting")
    invited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    invite_token_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("auth_tokens.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        CheckConstraint(_in("severity", SEVERITIES), name="ck_notifications_severity"),
        UniqueConstraint("user_id", "dedupe_key", name="uq_notifications_user_dedupe"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(64))
    severity: Mapped[str] = mapped_column(String(16), default="info", server_default="info")
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, default="", server_default="")
    meeting_id: Mapped[int | None] = mapped_column(ForeignKey("meetings.id", ondelete="SET NULL"))
    dedupe_key: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MailOutbox(Base):
    __tablename__ = "mail_outbox"
    __table_args__ = (
        CheckConstraint(_in("status", MAIL_STATUSES), name="ck_mail_outbox_status"),
        # One notes-ready mail per meeting, however many workers or retries (the payload of a
        # notes row keeps its ids when the row is done).
        Index(
            "uq_mail_outbox_notes_ready_meeting",
            sa.text("(payload->>'meeting_id')"),
            unique=True,
            postgresql_where=sa.text("template = 'notes_ready'"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    to_email: Mapped[str] = mapped_column(String(254))
    template: Mapped[str] = mapped_column(String(64))
    # A link template holds its link here while pending; a notes template holds ids only (the
    # notes are read at send time). Replaced by {} once the row is done.
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MailSuppression(Base):
    """An address no mail goes to (a bounce, a complaint, an unsubscribe), kept as the sha256 of
    the lower-cased address only (`usage.email_hash`). Not removed when an account is deleted:
    forgetting a complaint would let the next sign-up mail the address again."""

    __tablename__ = "mail_suppressions"
    __table_args__ = (
        CheckConstraint(_in("reason", SUPPRESSION_REASONS), name="ck_mail_suppressions_reason"),
    )

    email_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    reason: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Template(Base):
    __tablename__ = "templates"
    __table_args__ = (UniqueConstraint("owner_user_id", "name", name="uq_templates_owner_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(100))
    instructions: Mapped[str] = mapped_column(Text)


class VocabularyTerm(Base):
    __tablename__ = "vocabulary_terms"
    __table_args__ = (UniqueConstraint("owner_user_id", "term", name="uq_vocabulary_owner_term"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    term: Mapped[str] = mapped_column(String(200))


class UserSettings(Base):
    """One row per user, created at sign-up with these defaults."""

    __tablename__ = "user_settings"
    __table_args__ = (
        CheckConstraint(_in("auto_join_rule", AUTO_JOIN_RULES), name="ck_user_settings_rule"),
        CheckConstraint(
            "instant_meet_mode_override is null or "
            + _in("instant_meet_mode_override", INSTANT_MEET_MODES),
            name="ck_user_settings_instant_mode",
        ),
    )

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    auto_join_rule: Mapped[str] = mapped_column(String(16), default="all", server_default="all")
    calendar_template_id: Mapped[int | None] = mapped_column(
        ForeignKey("templates.id", ondelete="SET NULL")
    )
    join_instant_meets: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=sa.false()
    )
    instant_meet_mode_override: Mapped[str | None] = mapped_column(String(16))
    notify_email: Mapped[bool] = mapped_column(Boolean, default=True, server_default=sa.true())


class CalendarConnection(Base):
    """A user's connected calendar source: their Google connection (the calendar and Meet features
    are each derived from `granted_scopes`, so Meet detection works without the Calendar scope) or,
    from provider `ics`, a pasted calendar link. Access tokens are never stored; the refresh token
    is AES-GCM ciphertext (`app/vault.py`). One row per user and provider."""

    __tablename__ = "calendar_connections"
    __table_args__ = (
        CheckConstraint(
            _in("provider", CONNECTION_PROVIDERS), name="ck_calendar_connections_provider"
        ),
        CheckConstraint(_in("status", CONNECTION_STATUSES), name="ck_calendar_connections_status"),
        CheckConstraint(
            "needs_reconnect_reason is null or " + _in("needs_reconnect_reason", RECONNECT_REASONS),
            name="ck_calendar_connections_reason",
        ),
        CheckConstraint(
            _in("meet_subscription_state", MEET_SUBSCRIPTION_STATES),
            name="ck_calendar_connections_meet_state",
        ),
        UniqueConstraint("user_id", "provider", name="uq_calendar_connections_user_provider"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(String(16), default="google", server_default="google")
    google_account_email: Mapped[str | None] = mapped_column(String(254))
    google_sub: Mapped[str | None] = mapped_column(String(64))
    # The ID token's `hd` claim: present for Workspace accounts, absent for personal Gmail.
    google_hosted_domain: Mapped[str | None] = mapped_column(String(253))
    status: Mapped[str] = mapped_column(String(16), default="connected", server_default="connected")
    needs_reconnect_reason: Mapped[str | None] = mapped_column(String(16))
    granted_scopes: Mapped[str | None] = mapped_column(Text)
    refresh_token_enc: Mapped[bytes | None] = mapped_column(LargeBinary)  # nonce || ciphertext
    key_version: Mapped[int | None] = mapped_column(Integer)
    connected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # Null asks the sync loop for a first sync.
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # When the whole window was last listed (not just the changes since the sync token); a full
    # listing is due every 6 hours. Null = never: the next sync is a full one.
    last_full_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sync_token: Mapped[str | None] = mapped_column(Text)
    # connected_at + 7 days, set only while the OAuth app is in Testing.
    expected_expiry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    meet_subscription_name: Mapped[str | None] = mapped_column(Text)
    meet_subscription_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Apart from `status`: Meet detection can fail while the calendar is fine.
    meet_subscription_state: Mapped[str] = mapped_column(
        String(16), default="none", server_default="none"
    )


class CalendarEvent(Base):
    """One calendar event as the sync last saw it. Holds the attendee count, never the addresses,
    and no description or location. `meeting_url` can carry a Zoom passcode: never log it. Rows are
    deleted two days after the event ends and on disconnect."""

    __tablename__ = "calendar_events"
    __table_args__ = (
        CheckConstraint(
            "platform is null or " + _in("platform", EVENT_PLATFORMS),
            name="ck_calendar_events_platform",
        ),
        CheckConstraint(_in("status", EVENT_STATUSES), name="ck_calendar_events_status"),
        CheckConstraint(
            "self_response is null or " + _in("self_response", EVENT_RESPONSES),
            name="ck_calendar_events_response",
        ),
        UniqueConstraint("connection_id", "source_event_id", name="uq_calendar_events_source"),
        Index("ix_calendar_events_user_starts", "user_id", "starts_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"))
    connection_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("calendar_connections.id", ondelete="CASCADE")
    )
    # The source's own id (Google's event id, an ICS UID plus RECURRENCE-ID).
    source_event_id: Mapped[str] = mapped_column(String(1024))
    recurring_event_id: Mapped[str | None] = mapped_column(String(1024))
    title: Mapped[str] = mapped_column(Text, default="", server_default="")
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    platform: Mapped[str | None] = mapped_column(String(16))
    meeting_url: Mapped[str | None] = mapped_column(Text)
    native_id: Mapped[str | None] = mapped_column(String(200))
    organizer_self: Mapped[bool | None] = mapped_column(Boolean)
    # The organiser is in the user's own Workspace domain (the address itself is never kept).
    organizer_same_org: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=sa.false()
    )
    attendee_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    self_response: Mapped[str | None] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="confirmed", server_default="confirmed")
    # Kept so a rule change can re-run `will_join` from the stored row alone.
    all_day: Mapped[bool] = mapped_column(Boolean, default=False, server_default=sa.false())
    event_type: Mapped[str] = mapped_column(String(32), default="default", server_default="default")
    # The user's switch for this meeting: null follows the rule, true joins, false does not.
    override: Mapped[bool | None] = mapped_column(Boolean)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ScheduledJoin(Base):
    """A bot that will be, is being, or was sent to a meeting by the scheduler. One row per
    calendar event per user, revived and reused rather than duplicated. A row exists only for
    events that will be joined. `meeting_id` is the only link to the meeting; from `joined` on the
    meeting is the truth."""

    __tablename__ = "scheduled_joins"
    __table_args__ = (
        CheckConstraint(_in("source", JOIN_SOURCES), name="ck_scheduled_joins_source"),
        CheckConstraint(_in("platform", EVENT_PLATFORMS), name="ck_scheduled_joins_platform"),
        CheckConstraint(_in("state", JOIN_STATES), name="ck_scheduled_joins_state"),
        UniqueConstraint("user_id", "calendar_event_id", name="uq_scheduled_joins_user_event"),
        Index("ix_scheduled_joins_state_run_at", "state", "run_at"),
        # One live join per user per meeting code. Per user on purpose: a global index would let one
        # account block another's join by registering the same link first. Two users in one
        # meeting each get a join until C12's dispatcher decides how sharing works.
        Index(
            "uq_scheduled_joins_live_code",
            "user_id",
            "platform",
            "native_id",
            unique=True,
            postgresql_where=sa.text(_in("state", LIVE_JOIN_STATES)),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"))
    source: Mapped[str] = mapped_column(String(16), default="calendar", server_default="calendar")
    # Null for an instant-Meet join. The event row goes two days after the meeting; the join stays.
    calendar_event_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("calendar_events.id", ondelete="SET NULL")
    )
    platform: Mapped[str] = mapped_column(String(16))
    native_id: Mapped[str] = mapped_column(String(200))
    meeting_url: Mapped[str] = mapped_column(Text)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(String(16), default="scheduled", server_default="scheduled")
    # Why a join was skipped or cancelled; free text on purpose, the runner owns the vocabulary.
    skip_reason: Mapped[str | None] = mapped_column(String(32))
    template_id: Mapped[int | None] = mapped_column(ForeignKey("templates.id", ondelete="SET NULL"))
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    meeting_id: Mapped[int | None] = mapped_column(ForeignKey("meetings.id", ondelete="SET NULL"))
    last_error: Mapped[str | None] = mapped_column(Text)
    # When the single-event re-check ran, so it runs once per join.
    rechecked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Meeting(Base):
    __tablename__ = "meetings"
    __table_args__ = (
        CheckConstraint(_in("status", STATUSES), name="ck_meetings_status"),
        CheckConstraint(_in("step", STEPS), name="ck_meetings_step"),
        CheckConstraint(_in("source", MEETING_SOURCES), name="ck_meetings_source"),
        CheckConstraint(
            "extraction_status is null or " + _in("extraction_status", EXTRACTION_STATUSES),
            name="ck_meetings_extraction_status",
        ),
        Index("ix_meetings_owner_user_id", "owner_user_id", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    url: Mapped[str] = mapped_column(Text)
    platform: Mapped[str] = mapped_column(String(32))
    native_id: Mapped[str] = mapped_column(String(200))
    template_id: Mapped[int] = mapped_column(ForeignKey("templates.id"))
    vexa_meeting_id: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(16), default="bot_sent", server_default="bot_sent")
    step: Mapped[str] = mapped_column(String(16), default="waiting", server_default="waiting")
    failure_reason: Mapped[str | None] = mapped_column(Text)
    transcript: Mapped[str | None] = mapped_column(Text)
    speaker_transcript: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    started_polling_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    audio_deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Vexa's id for the recording, kept when first seen so cleanup can delete Vexa's copy.
    vexa_recording_id: Mapped[int | None] = mapped_column(BigInteger)
    # True when some part of the transcript could not be given speaker names.
    speakers_incomplete: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=sa.false()
    )
    completion_reason: Mapped[str | None] = mapped_column(String(64))
    # How many bots have been sent for this meeting; Zoom sometimes turns one away.
    join_attempts: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    # When a poll first saw Vexa report the meeting as over.
    terminal_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # When a poll last saw the bot in the live state kept in `completion_reason`; null when there
    # is none. A state older than a couple of poll intervals is not shown as current.
    bot_state_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(16), default="manual", server_default="manual")
    # The calendar event's title; null for a pasted link.
    title: Mapped[str | None] = mapped_column(Text)
    # Length of the recording the pipeline prepared, in seconds; the unit of the hours cap.
    recorded_seconds: Mapped[int | None] = mapped_column(Integer)
    # Where the extraction of decisions, open questions and review items from the notes stands
    # (app/workspace.py). It never fails the meeting: the notes are the deliverable.
    extraction_status: Mapped[str | None] = mapped_column(String(16))
    extraction_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    extraction_attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The prompt version of the last finished extraction (app/ai_versions.py).
    extraction_version: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UsageEntry(Base):
    """Recorded seconds counted against a user's hours. Kept apart from `meetings` so deleting a
    meeting does not give hours back, and keyed by `email_hash` too so deleting an account and
    signing up again does not reset them."""

    __tablename__ = "usage_entries"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    email_hash: Mapped[str] = mapped_column(
        String(64), index=True
    )  # sha256 of the lower-cased email
    meeting_id: Mapped[int | None] = mapped_column(
        ForeignKey("meetings.id", ondelete="SET NULL"), unique=True
    )
    seconds: Mapped[int] = mapped_column(Integer)
    counted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Decision(Base):
    """One bullet of a meeting's "Decisions" section. Read from the notes by code (no model call).
    `fingerprint` is the identity across a re-run, so a decision the owner hid stays hidden."""

    __tablename__ = "decisions"
    __table_args__ = (
        UniqueConstraint("meeting_id", "fingerprint", name="uq_decisions_meeting_fp"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    meeting_id: Mapped[int] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), index=True
    )
    text: Mapped[str] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer)
    fingerprint: Mapped[str] = mapped_column(String(64))
    hidden_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OpenQuestion(Base):
    """One bullet of a meeting's "Open questions" section."""

    __tablename__ = "open_questions"
    __table_args__ = (
        UniqueConstraint("meeting_id", "fingerprint", name="uq_open_questions_meeting_fp"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    meeting_id: Mapped[int] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), index=True
    )
    text: Mapped[str] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer)
    fingerprint: Mapped[str] = mapped_column(String(64))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReviewItem(Base):
    """One bullet of a meeting's "Please check" section, for the Review screen. Owner only, like
    vocabulary: `owner_user_id` is the meeting's owner. `heard_as` is set only when the extraction
    call says the bullet is about a possibly misheard name or term and that word is in the
    transcript."""

    __tablename__ = "review_items"
    __table_args__ = (
        UniqueConstraint("meeting_id", "fingerprint", name="uq_review_items_meeting_fp"),
        Index("ix_review_items_owner_checked", "owner_user_id", "checked_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    meeting_id: Mapped[int] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), index=True
    )
    owner_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE")
    )
    text: Mapped[str] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer)
    fingerprint: Mapped[str] = mapped_column(String(64))
    heard_as: Mapped[str | None] = mapped_column(String(100))
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AiRun(Base):
    """One model call made for the AI workspace, kept so the real cost per meeting hour can be
    read off. Like `usage_entries` it outlives the meeting (`meeting_id` is nulled, not cascaded),
    and `error` is the exception class name only: exception text can hold transcript words."""

    __tablename__ = "ai_runs"
    __table_args__ = (
        CheckConstraint(_in("kind", AI_RUN_KINDS), name="ck_ai_runs_kind"),
        CheckConstraint(_in("status", AI_RUN_STATUSES), name="ck_ai_runs_status"),
        Index("ix_ai_runs_meeting_id", "meeting_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    meeting_id: Mapped[int | None] = mapped_column(ForeignKey("meetings.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(16))
    model: Mapped[str] = mapped_column(String(100))
    prompt_version: Mapped[str | None] = mapped_column(String(32))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="ok", server_default="ok")
    error: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AppState(Base):
    """One row (id 1) of app-wide state. `session_epoch` is bumped to sign everyone out."""

    __tablename__ = "app_state"
    __table_args__ = (CheckConstraint("id = 1", name="ck_app_state_single_row"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    session_epoch: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Set by the worker on every pass; the home page warns when it goes stale.
    worker_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Pilot settings. Nobody can sign up until the admin opens the pilot. The two nullable ints
    # have a server default only, so an explicit None means "no limit".
    signups_open: Mapped[bool] = mapped_column(Boolean, default=False, server_default=sa.false())
    max_accounts: Mapped[int | None] = mapped_column(Integer, server_default="10")
    # Seconds of recorded audio per account for the whole pilot; null = no cap.
    default_hours_cap_seconds: Mapped[int | None] = mapped_column(Integer, server_default="18000")
    password_signup_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=sa.false()
    )
    scheduler_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


@sa.event.listens_for(Session, "before_flush")
def _complete_the_join_with_its_meeting(session: Session, flush_context, instances) -> None:
    """A calendar join that is `joined` becomes `completed` in the SAME transaction as its meeting
    leaving `bot_sent` (spec 7.7, plan C12), whichever code moves the meeting: the worker (the bot
    left, or the join failed), Cancel, End, a purge. Done here, at the one place every status
    change passes, so no path can forget it and no crash can leave the two disagreeing. It frees
    the meeting code: a recurring meeting's next event may then join (`uq_scheduled_joins_live_code`
    holds a code while its join is live)."""
    leaving = [
        obj.id
        for obj in session.dirty
        if isinstance(obj, Meeting)
        and obj.id is not None
        and obj.status != "bot_sent"
        and "bot_sent" in sa.inspect(obj).attrs.status.history.deleted
    ]
    if leaving:
        session.execute(
            sa.update(ScheduledJoin)
            .where(ScheduledJoin.meeting_id.in_(leaving), ScheduledJoin.state == "joined")
            .values(state="completed")
            .execution_options(synchronize_session="fetch")
        )
