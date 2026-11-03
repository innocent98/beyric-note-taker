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
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

STATUSES = ("bot_sent", "processing", "ready", "failed")
STEPS = ("waiting", "audio", "transcribing", "speakers", "notes", "done")
MEETING_SOURCES = ("manual", "calendar", "instant_meet")
USER_STATUSES = ("active", "suspended", "deleted")
TOKEN_PURPOSES = ("verify_email", "reset_password", "waitlist_invite")
OAUTH_PURPOSES = ("signin", "calendar", "meet_scope")
WAITLIST_STATUSES = ("waiting", "invited", "joined", "removed")
SEVERITIES = ("info", "warning", "error")
MAIL_STATUSES = ("pending", "sent", "failed")
AUTO_JOIN_RULES = ("all", "organizer", "none")
INSTANT_MEET_MODES = ("events", "poll", "off")


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
    __table_args__ = (CheckConstraint(_in("status", MAIL_STATUSES), name="ck_mail_outbox_status"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    to_email: Mapped[str] = mapped_column(String(254))
    template: Mapped[str] = mapped_column(String(64))
    # Holds the link while pending; replaced by {} once sent.
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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


class Meeting(Base):
    __tablename__ = "meetings"
    __table_args__ = (
        CheckConstraint(_in("status", STATUSES), name="ck_meetings_status"),
        CheckConstraint(_in("step", STEPS), name="ck_meetings_step"),
        CheckConstraint(_in("source", MEETING_SOURCES), name="ck_meetings_source"),
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
    source: Mapped[str] = mapped_column(String(16), default="manual", server_default="manual")
    # The calendar event's title; null for a pasted link.
    title: Mapped[str | None] = mapped_column(Text)
    # Length of the recording the pipeline prepared, in seconds; the unit of the hours cap.
    recorded_seconds: Mapped[int | None] = mapped_column(Integer)
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
