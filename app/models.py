from datetime import date, datetime

import sqlalchemy as sa
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

STATUSES = ("bot_sent", "processing", "ready", "failed")
STEPS = ("waiting", "audio", "transcribing", "speakers", "notes", "done")
MEETING_SOURCES = ("manual", "calendar", "instant_meet")
USER_STATUSES = ("active", "suspended", "deleted")
TEAM_ROLES = ("owner", "admin", "member")
INVITE_ROLES = ("admin", "member")
TOKEN_PURPOSES = ("verify_email", "reset_password", "waitlist_invite", "mail_optout")
SHARE_LANGUAGES = ("en", "fr", "es")
SHARE_ATTACH = ("none", "pdf", "docx")
# A share waits for its translation, then its mails are queued; `failed` means they never were.
SHARE_STATUSES = ("waiting_translation", "queued", "failed", "revoked")
SHARE_RECIPIENT_STATUSES = ("pending", "sent", "not_sent", "failed")
OAUTH_PURPOSES = ("signin", "calendar", "meet_scope")
WAITLIST_STATUSES = ("waiting", "invited", "joined", "removed")
SEVERITIES = ("info", "warning", "error")
MAIL_STATUSES = ("pending", "sent", "failed", "skipped")
SUPPRESSION_REASONS = ("bounce", "complaint", "unsubscribed")
AUTO_JOIN_RULES = ("all", "organizer", "none")
INSTANT_MEET_MODES = ("events", "poll", "off")
CONNECTION_PROVIDERS = ("google", "ics", "microsoft")
OAUTH_FLOW_PROVIDERS = ("google", "microsoft")
ACCOUNT_KINDS = ("personal", "work")
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
# `shadow` is a transcription made only to compare with the provider's transcript
# (`TRANSCRIPT_SHADOW_PERCENT`): a real OpenAI cost, so it counts in cost totals, but it has no
# user_id (the owner did not cause it) and is never a meeting's transcript.
AI_RUN_KINDS = (
    "extract",
    "translate",
    "notes",
    "label",
    "transcribe",
    "ask",
    "topics",
    "shadow",
    "vocabulary",
)
AI_RUN_STATUSES = ("ok", "failed", "pending")
# `pending` waits for the worker (or for a retry), `running` is being worked on, `done` is finished,
# `failed` gave up after the retries, `skipped` means only the decisions were read from the notes
# and the model was not asked (an old meeting, per the backfill rules). Null = notes not written.
EXTRACTION_STATUSES = ("pending", "running", "done", "failed", "skipped")
# English is the original, so only these two are translated into (spec section 11, D8).
NOTE_LANGUAGES = ("en", "fr", "es")
TRANSLATION_STATUSES = ("translating", "ready", "failed")
TASK_STATUSES = ("todo", "doing", "done")
TASK_SOURCES = ("ai", "manual")
TASK_CONFIDENCES = ("high", "medium", "low")
# `replaced` and `rejected` are only ever set by a person (the Decisions record).
DECISION_STATUSES = ("approved", "proposed", "replaced", "rejected")
# When the reminder mails go before a recurring meeting (the Settings choice): one hour before,
# 30 minutes before, or both (the designer draws two mails).
REMINDER_TIMINGS = ("1h", "30m", "both")
REMINDER_KINDS = ("1h", "30m")

# The sprint lengths of the Settings list (W9). A project may override the team's with any of
# them except `custom`: the designer's per-project select has no Custom entry.
SPRINT_LENGTHS = ("week5", "week7", "week14", "month1", "month3", "month6", "custom")
PROJECT_SPRINT_LENGTHS = SPRINT_LENGTHS[:-1]


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
        # The sign-up flood defence counts unconfirmed rows by where they were made from.
        Index(
            "ix_users_signup_origin_unconfirmed",
            "signup_origin",
            postgresql_where=sa.text("email_verified_at is null"),
        ),
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
    # Set when the account was erased but its row had to stay because a team owns a meeting this
    # person recorded (`app/deletion.py`): name, email and credentials are gone, the row is hollow.
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # A keyed hash of the client address an unconfirmed account was made from
    # (`ratelimit.origin_key`), so the sign-up flood defence can tell one address's rows from
    # another's. Set at creation and never changed by a later submission; null for older rows.
    signup_origin: Mapped[str | None] = mapped_column(String(32))


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
    """State of one OAuth round trip (Google or Microsoft); short-lived and swept."""

    __tablename__ = "oauth_flows"
    __table_args__ = (
        CheckConstraint(_in("purpose", OAUTH_PURPOSES), name="ck_oauth_flows_purpose"),
        CheckConstraint(_in("provider", OAUTH_FLOW_PROVIDERS), name="ck_oauth_flows_provider"),
    )

    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    purpose: Mapped[str] = mapped_column(String(16))
    # Which provider's consent screen this round trip went to: a state can only finish at that
    # provider's callback.
    provider: Mapped[str] = mapped_column(String(16), default="google", server_default="google")
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
    meeting_id: Mapped[int | None] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"))
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
        # One "New tasks assigned" mail per assignee per meeting (spec 6.1), however many passes.
        Index(
            "uq_mail_outbox_notes_shared",
            sa.text("(payload->>'share_id')"),
            sa.text("(payload->>'recipient_id')"),
            unique=True,
            postgresql_where=sa.text("template = 'notes_shared'"),
        ),
        Index(
            "uq_mail_outbox_tasks_assigned",
            sa.text("(payload->>'meeting_id')"),
            sa.text("(payload->>'user_id')"),
            unique=True,
            postgresql_where=sa.text("template = 'tasks_assigned'"),
        ),
        # W8: one agenda mail per person per send, one reminder mail per reminder row.
        Index(
            "uq_mail_outbox_series_agenda",
            sa.text("(payload->>'send_id')"),
            sa.text("(payload->>'user_id')"),
            unique=True,
            postgresql_where=sa.text("template = 'series_agenda'"),
        ),
        Index(
            "uq_mail_outbox_series_reminder",
            sa.text("(payload->>'reminder_id')"),
            unique=True,
            postgresql_where=sa.text("template = 'series_reminder'"),
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
    """A person's notes template. Deleting one only marks it (`deleted_at`): meetings keep the id
    of the template their notes were written with, so the row must stay. A marked row is never
    listed, chosen or counted; its name may be used again (the unique index is over live rows)."""

    __tablename__ = "templates"
    __table_args__ = (
        Index(
            "uq_templates_owner_name",
            "owner_user_id",
            "name",
            unique=True,
            postgresql_where=sa.text("deleted_at is null"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(100))
    instructions: Mapped[str] = mapped_column(Text)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class VocabularyTerm(Base):
    __tablename__ = "vocabulary_terms"
    __table_args__ = (UniqueConstraint("owner_user_id", "term", name="uq_vocabulary_owner_term"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    term: Mapped[str] = mapped_column(String(200))


class Team(Base):
    """A small group of accounts that may share meetings (spec 8.1). Every account has one from
    sign-up (a team of one), so every query is team-aware from day one. Sprint and reminder
    settings arrive with the phases that use them (W3, W8, W9), as additive columns.

    `is_personal` marks a person's own permanent space (multi-team design, 2026-11-13): at most one
    per owner (the partial unique index), never left, never joined, nobody invited into it."""

    __tablename__ = "teams"
    __table_args__ = (
        CheckConstraint(_in("reminder_timing", REMINDER_TIMINGS), name="ck_teams_reminder_timing"),
        CheckConstraint(_in("sprint_length", SPRINT_LENGTHS), name="ck_teams_sprint_length"),
        CheckConstraint("sprint_custom_days between 1 and 365", name="ck_teams_sprint_custom_days"),
        Index(
            "uq_teams_one_personal_per_owner",
            "owner_user_id",
            unique=True,
            postgresql_where=sa.text("is_personal"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), default="My team", server_default="My team")
    owner_user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), index=True)
    is_personal: Mapped[bool] = mapped_column(Boolean, default=False, server_default=sa.false())
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", server_default="UTC")
    # The team default for "Share with team" (spec 8.2): off. Read when a meeting's notes are first
    # written (`teams.apply_share_default`), never when the bot joins, and never copied anywhere.
    share_new_meetings: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=sa.false()
    )
    # W8: "Remind attendees before recurring meetings" (Settings, owners and admins). Off until a
    # manager switches it on: the mail uses calendar data for a purpose the privacy policy names
    # only once Google verification is done (spec 7, "Google data constraint").
    reminders_on: Mapped[bool] = mapped_column(Boolean, default=False, server_default=sa.false())
    reminder_timing: Mapped[str] = mapped_column(String(8), default="both", server_default="both")

    # W9 (spec 6.4): how long a sprint is for the whole team, the number of days when it is
    # `custom` (1 to 365), and whether 1-week-5-day sprints run Monday to Friday.
    sprint_length: Mapped[str] = mapped_column(String(8), default="week5", server_default="week5")
    sprint_custom_days: Mapped[int] = mapped_column(Integer, default=10, server_default="10")
    sprint_align_weekdays: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=sa.true()
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TeamInvite(Base):
    """An invitation to join a team (spec 4, 8.1). Only the hash of the link's token is stored.
    Live = neither accepted nor revoked, and not past `expires_at`. A live row for an address with
    no account holds one of the pilot's places (`signup.slots_taken`). `claimed_user_id` is the
    account made from the link whose email is not confirmed yet: it joins when it is."""

    __tablename__ = "team_invites"
    __table_args__ = (
        CheckConstraint(_in("role", INVITE_ROLES), name="ck_team_invites_role"),
        CheckConstraint("email = lower(email)", name="ck_team_invites_email_lower"),
        Index("ix_team_invites_email", "email"),
        Index(
            "uq_team_invites_live",
            "team_id",
            "email",
            unique=True,
            postgresql_where=sa.text("accepted_at is null and revoked_at is null"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    team_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("teams.id", ondelete="CASCADE"))
    email: Mapped[str] = mapped_column(String(254))
    # What the inviter calls them: seeds their aliases when they join (spec 2, row 4).
    name: Mapped[str | None] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(String(8), default="member", server_default="member")
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    invited_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    claimed_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TeamMember(Base):
    """A user's membership of one team. A person may have several (multi-team design): one row per
    team, a role in each, unique on (team, user). `MULTI_TEAM_ENABLED` decides whether the app lets
    a second one be made; the schema no longer stops it."""

    __tablename__ = "team_members"
    __table_args__ = (
        CheckConstraint(_in("role", TEAM_ROLES), name="ck_team_members_role"),
        UniqueConstraint("team_id", "user_id", name="uq_team_members_team_user"),
        Index("ix_team_members_team_id", "team_id"),
        Index("ix_team_members_user_id", "user_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    team_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("teams.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(8), default="member", server_default="member")
    # How the person is named aloud in meetings, for matching task owners (W2/W3).
    aliases: Mapped[list[str]] = mapped_column(
        ARRAY(Text), default=list, server_default=sa.text("'{}'::text[]")
    )
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


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
        CheckConstraint(
            _in("default_language", NOTE_LANGUAGES), name="ck_user_settings_default_language"
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
    # The language notes and transcripts open in when a translation exists; `en` is the original.
    default_language: Mapped[str] = mapped_column(String(2), default="en", server_default="en")
    # The person's own IANA time zone ("Africa/Lagos"); null = UTC, as every time was before.
    time_zone: Mapped[str | None] = mapped_column(String(64))
    # Settings > Tasks (spec 12): whether action items of this person's meetings become tasks, and
    # whether the people they are assigned to are emailed about them.
    auto_create_tasks: Mapped[bool] = mapped_column(Boolean, default=True, server_default=sa.true())
    email_assignees: Mapped[bool] = mapped_column(Boolean, default=True, server_default=sa.true())
    # "Ask before adding unclear tasks": an action item the model rates medium or low waits under
    # Suggested tasks instead of becoming a task. Off: every action item becomes a task.
    ask_before_unclear: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=sa.true()
    )
    # W8: whether this person is emailed the agenda a teammate sends and the reminders before a
    # recurring meeting they attend (Settings > Email).
    notify_reminders: Mapped[bool] = mapped_column(Boolean, default=True, server_default=sa.true())
    # The one team this person's new meetings are shared to when their notes are first written
    # (multi-team design 6.3); null = none chosen, and the team default (`teams.
    # share_new_meetings`, owners and admins) applies as before.
    auto_share_team_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("teams.id", ondelete="SET NULL")
    )


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
            "account_kind is null or " + _in("account_kind", ACCOUNT_KINDS),
            name="ck_calendar_connections_account_kind",
        ),
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
    # The connected account, whichever provider: a label (the address shown on Settings), the
    # provider's stable subject id (Google `sub`; Microsoft `oid`), and the organisation domain
    # (Google's `hd` claim: present for Workspace accounts, absent for personal Gmail).
    account_email: Mapped[str | None] = mapped_column(String(254))
    account_subject: Mapped[str | None] = mapped_column(String(64))
    account_domain: Mapped[str | None] = mapped_column(String(253))
    # Microsoft only (null for Google and ICS): the directory (tenant) id, personal or work, and the
    # organisation's verified domains (space separated), used for the same-organisation test.
    tenant_id: Mapped[str | None] = mapped_column(String(36))
    account_kind: Mapped[str | None] = mapped_column(String(8))
    tenant_domains: Mapped[str | None] = mapped_column(Text)
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
    # When the current run of failed syncs began (null while the last sync worked). The calendar
    # is `degraded` once this is 30 minutes old (app/calendar_states.py).
    error_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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
        Index(
            "ix_meetings_series_id",
            "series_id",
            postgresql_where=sa.text("series_id is not null"),
        ),
        Index(
            "ix_meetings_shared_team_id",
            "shared_team_id",
            postgresql_where=sa.text("shared_team_id is not null"),
        ),
        Index(
            "ix_meetings_owner_team_id",
            "owner_team_id",
            postgresql_where=sa.text("owner_team_id is not null"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    url: Mapped[str] = mapped_column(Text)
    platform: Mapped[str] = mapped_column(String(32))
    native_id: Mapped[str] = mapped_column(String(200))
    template_id: Mapped[int] = mapped_column(ForeignKey("templates.id"))
    # The provider's id for the bot (`vexa_meeting_id` keeps its name; it holds a Skribby bot's
    # UUID as text too) and which provider ran it. Old rows are `vexa`.
    vexa_meeting_id: Mapped[str | None] = mapped_column(String(100))
    bot_provider: Mapped[str] = mapped_column(String(32), default="vexa", server_default="vexa")
    # When a hosted provider's copy (recording, transcript, bot record) was deleted at the provider;
    # null until then, and always null for Vexa (its copy follows the retention rule).
    provider_copy_deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Failed tries to delete it and when the next may be made (backoff); after
    # `pipeline.COPY_MAX_ATTEMPTS` the retry job leaves the row to the retention pass.
    provider_copy_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    provider_copy_next_try_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The hosted provider's region the bot was sent to (`eu`, `jp`); null for Vexa. Skribby's two
    # regions are separate accounts: a bot sent to one is not found in the other.
    bot_region: Mapped[str | None] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(16), default="bot_sent", server_default="bot_sent")
    step: Mapped[str] = mapped_column(String(16), default="waiting", server_default="waiting")
    failure_reason: Mapped[str | None] = mapped_column(Text)
    transcript: Mapped[str | None] = mapped_column(Text)
    # What `transcript` was made from: `own` (our OpenAI transcription of the audio) or `skribby`
    # (the bot provider's transcript, `TRANSCRIPT_SOURCE=skribby`). Null before the transcript is
    # saved and for meetings made before this column existed (they were all `own`).
    transcript_source: Mapped[str | None] = mapped_column(String(16))
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
    # True for a meeting read by the backfill or by an on-demand "extract tasks": its tasks are not
    # marked New and nobody is mailed about them. Cleared when the notes are rewritten.
    extraction_quiet: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=sa.false()
    )
    # The one thing that makes a meeting visible to anyone but its owner (spec 4, 8.2): the team
    # it is shared with. Null = private, which is the default and what every row starts as. Only
    # `app/visibility.py` turns this into who may read; nothing else may compare it.
    shared_team_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("teams.id", ondelete="SET NULL")
    )
    # Stage 2 of the multi-team design: the team that OWNS the meeting (moved to it), as opposed to
    # one it is merely shared with. Null for every row and unused until stage 2 fills it. The
    # read rule in `app/visibility.py` matches it; the write and recorder rules for a moved meeting
    # are stage 2 work (see that module's docstring).
    owner_team_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("teams.id", ondelete="SET NULL")
    )
    # The recurring meeting this one belongs to (W8, `app/series.py`). Null = not in one. Only
    # `app/series.py` sets it, and a reader must also check the series belongs to their team.
    series_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("series.id", ondelete="SET NULL")
    )
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
    """One bullet of a meeting's "Decisions" section. Read from the notes by code; the extraction
    adds what the model said about it and the code checked (`decided_by`, `quote`, `proposed`, the
    related tasks). `fingerprint` is the identity across a re-run, so a decision the owner hid stays
    hidden and a status, revisit date or replaced-by a person set is never overwritten.

    `status`: `approved` (default), `proposed` (from the model), `replaced` / `rejected` (by hand
    only). `replaced_by_id` is the decision that replaced this one."""

    __tablename__ = "decisions"
    __table_args__ = (
        UniqueConstraint("meeting_id", "fingerprint", name="uq_decisions_meeting_fp"),
        CheckConstraint(_in("status", DECISION_STATUSES), name="ck_decisions_status"),
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
    status: Mapped[str] = mapped_column(String(10), default="approved", server_default="approved")
    # A speaker of the transcript and their words, kept only when the code found them there.
    decided_by: Mapped[str | None] = mapped_column(String(100))
    quote: Mapped[str | None] = mapped_column(String(300))
    revisit_on: Mapped[date | None] = mapped_column(Date)
    replaced_by_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("decisions.id", ondelete="SET NULL"), index=True
    )
    # A person touched status, revisit date or replaced-by: the extraction leaves them be.
    # (`status` itself is the source of truth; this only tells "proposed by the model" apart from
    # "set to proposed by a person".)
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DecisionTask(Base):
    """A task that carries out a decision (the Related tasks of the Decisions record)."""

    __tablename__ = "decision_tasks"

    decision_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("decisions.id", ondelete="CASCADE"), primary_key=True
    )
    task_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True, index=True
    )


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
    # W8: who marked it resolved (null when nobody did, or the account has gone).
    resolved_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    # W8: "On next agenda". True = the question is carried to the next meeting of the series until
    # someone resolves it. Only a question of a series meeting is ever carried.
    carry: Mapped[bool] = mapped_column(Boolean, default=True, server_default=sa.true())
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


class Task(Base):
    """A thing someone has to do: an action item read from a meeting's notes (`source` `ai`) or a
    task a person typed in (`manual`, no meeting). Every task belongs to a team, which is the
    meeting owner's team for an AI task. The notes bullet is the record; the model only helps with
    the owner, the due date and the quote, and everything it said is checked (app/extraction.py).

    `ai_fingerprint` is the identity across a re-run (spec 3.3). A task a person touched
    (`edited_at` set, or `status` not `todo`) is never overwritten or removed by a re-run. A deleted
    AI task is kept as a tombstone (`deleted_at` set, text cleared) so the same bullet does not
    bring it back."""

    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint(_in("status", TASK_STATUSES), name="ck_tasks_status"),
        CheckConstraint(_in("source", TASK_SOURCES), name="ck_tasks_source"),
        CheckConstraint(
            "confidence is null or " + _in("confidence", TASK_CONFIDENCES),
            name="ck_tasks_confidence",
        ),
        Index(
            "uq_tasks_meeting_fingerprint",
            "meeting_id",
            "ai_fingerprint",
            unique=True,
            postgresql_where=sa.text("meeting_id is not null and ai_fingerprint is not null"),
        ),
        Index("ix_tasks_team_id", "team_id", "id"),
        Index("ix_tasks_assignee_user_id", "assignee_user_id"),
        Index("ix_tasks_project_id", "project_id"),
        Index("ix_tasks_milestone_id", "milestone_id"),
        Index("ix_tasks_sprint_id", "sprint_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    team_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("teams.id", ondelete="CASCADE"))
    meeting_id: Mapped[int | None] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(200), default="", server_default="")
    assignee_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    # The name as said in the meeting, kept when no teammate matched it ("Needs owner").
    spoken_owner: Mapped[str | None] = mapped_column(String(100))
    due_date: Mapped[date | None] = mapped_column(Date)
    due_phrase: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(8), default="todo", server_default="todo")
    quote: Mapped[str | None] = mapped_column(String(300))
    is_new: Mapped[bool] = mapped_column(Boolean, default=False, server_default=sa.false())
    # The model's quote was not found in the transcript: shown without a quote, counted as
    # needing attention.
    unverified: Mapped[bool] = mapped_column(Boolean, default=False, server_default=sa.false())
    source: Mapped[str] = mapped_column(String(8), default="manual", server_default="manual")
    ai_fingerprint: Mapped[str | None] = mapped_column(String(64))
    # Position of the bullet in the notes, so a meeting's tasks list in the order of its notes.
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The meeting's date in its owner's zone; today for a manual task.
    assigned_on: Mapped[date | None] = mapped_column(Date)
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # W4. A project or milestone that goes away leaves the task with none (the task stays).
    project_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("projects.id", ondelete="SET NULL")
    )
    milestone_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("milestones.id", ondelete="SET NULL")
    )
    # Filing was a guess (one keyword hit or a tie): shown as "Check project" until a person
    # confirms or changes the project.
    check_project: Mapped[bool] = mapped_column(Boolean, default=False, server_default=sa.false())
    # W9. The sprint it is planned into; null is the Backlog. A sprint that goes away (its project
    # was deleted) leaves the task in the Backlog.
    sprint_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("sprints.id", ondelete="SET NULL")
    )
    # Moved on from a sprint that closed with the task unfinished ("Carried over").
    carried: Mapped[bool] = mapped_column(Boolean, default=False, server_default=sa.false())
    # Suggested tasks (spec 2026-11-08, section 2): an action item the model was unsure about waits
    # for a person to accept it (`suggested` false) or dismiss it (the tombstone). While it waits it
    # is counted nowhere and mailed to nobody. `confidence` is the model's `high`, `medium` or `low`
    # (null: made before extract-v2, or by hand); `reason` is its plain words on why a medium or low
    # item is unclear.
    suggested: Mapped[bool] = mapped_column(Boolean, default=False, server_default=sa.false())
    confidence: Mapped[str | None] = mapped_column(String(8))
    reason: Mapped[str | None] = mapped_column(String(200))


class Project(Base):
    """A project of a team (spec 4, 6.3): a name, a colour and the keywords tasks are filed by.
    Visible to the whole team; the counts inside it follow what the viewer may see. Deleting one
    leaves its tasks with no project (`tasks.project_id` is SET NULL) and removes its milestones.
    `sprint_length` is the project's own sprint length (W9); null follows the team's. The
    recurring meetings link waits for W8."""

    __tablename__ = "projects"
    __table_args__ = (
        Index("ix_projects_team_id", "team_id", "id"),
        CheckConstraint(
            "sprint_length is null or " + _in("sprint_length", PROJECT_SPRINT_LENGTHS),
            name="ck_projects_sprint_length",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    team_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("teams.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(100), default="", server_default="")
    color: Mapped[str] = mapped_column(String(7))
    keywords: Mapped[list[str]] = mapped_column(
        ARRAY(Text), default=list, server_default=sa.text("'{}'::text[]")
    )
    sprint_length: Mapped[str | None] = mapped_column(String(8))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Sprint(Base):
    """One sprint of a team (spec 6.4). A team has one series, and a project with its own sprint
    length has another (`project_id` set). Each series keeps exactly two open sprints: the current
    one (the earlier) and the next. Closed ones stay as history. Dates are days in the team's
    zone; the sprint's name is "Sprint {number}", numbers counting up inside the series."""

    __tablename__ = "sprints"
    __table_args__ = (
        Index("ix_sprints_team_id", "team_id", "project_id"),
        Index(
            "uq_sprints_series_number",
            "team_id",
            sa.text("coalesce(project_id, 0)"),
            "number",
            unique=True,
        ),
        CheckConstraint("end_date >= start_date", name="ck_sprints_dates"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    team_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("teams.id", ondelete="CASCADE"))
    project_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("projects.id", ondelete="CASCADE")
    )
    number: Mapped[int] = mapped_column(Integer)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_early: Mapped[bool] = mapped_column(Boolean, default=False, server_default=sa.false())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Milestone(Base):
    """A dated goal. The extraction call may propose one from a meeting (`confirmed_at` null, with
    the words said in `quote`); it only counts once someone confirms it (spec D14). The source
    meeting is kept as a plain reference: deleting the meeting deletes its proposals
    (`services.delete_meeting`) and leaves a confirmed milestone."""

    __tablename__ = "milestones"
    __table_args__ = (
        Index("ix_milestones_team_id", "team_id", "id"),
        Index("ix_milestones_source_meeting_id", "source_meeting_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    team_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("teams.id", ondelete="CASCADE"))
    project_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("projects.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String(200))
    due_date: Mapped[date] = mapped_column(Date)
    source_meeting_id: Mapped[int | None] = mapped_column(
        ForeignKey("meetings.id", ondelete="SET NULL")
    )
    quote: Mapped[str | None] = mapped_column(String(300))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Series(Base):
    """A recurring meeting of a team (spec 7). It comes from the calendar's recurring-event id
    (`calendar_recurring_id`, found when a calendar meeting's notes are written, or by the hourly
    sync of joined events) or from a person attaching a meeting by hand (then it has none). The
    cadence and the next date are computed, never stored. Visible to the team (title and
    settings); every list and count inside it is filtered by what the viewer may read.

    `project_id` is the W4 link (a series files its tasks into one project); it has no foreign key
    here because projects arrive in their own migration, and W4 writes it."""

    __tablename__ = "series"
    __table_args__ = (
        Index(
            "uq_series_team_recurring",
            "team_id",
            "calendar_recurring_id",
            unique=True,
            postgresql_where=sa.text("calendar_recurring_id is not null"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    team_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("teams.id", ondelete="CASCADE"), index=True
    )
    owner_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    title: Mapped[str] = mapped_column(String(200), default="", server_default="")
    calendar_recurring_id: Mapped[str | None] = mapped_column(String(1024))
    platform: Mapped[str | None] = mapped_column(String(16))
    project_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # The worker's repeat-topics job (`app/topics.py`): a hash of what the last successful call was
    # given (an unchanged series costs no call), when it last looked (null = look again now: new
    # notes, a meeting joined or left) and how many calls failed since the input last changed.
    topics_signature: Mapped[str | None] = mapped_column(String(64))
    topics_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    topics_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


TOPIC_REASONS = ("no_decision", "no_owner", "not_final")


class SeriesTopic(Base):
    """A topic that came up in two or more meetings of a series with no decision on it ("Keeps
    coming up"). Stored by the worker's one model call per series and replaced as a set; the page
    reads these rows and never calls the model. `label` and `agenda` are text the model wrote from
    the meetings in `meeting_ids`, so a read shows a topic only to a viewer who may read at least
    two of those meetings (`series.topics_for`)."""

    __tablename__ = "series_topics"
    __table_args__ = (
        CheckConstraint(_in("reason", TOPIC_REASONS), name="ck_series_topics_reason"),
        Index("ix_series_topics_series_id", "series_id", "position"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    series_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("series.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    label: Mapped[str] = mapped_column(String(60))
    agenda: Mapped[str] = mapped_column(String(100))
    reason: Mapped[str] = mapped_column(String(12))
    meeting_ids: Mapped[list[int]] = mapped_column(JSONB)
    # Every meeting the model was shown when it wrote this topic (the cited ones are `meeting_ids`):
    # a viewer who cannot read all of them (one was unshared since) is not shown the topic.
    source_meeting_ids: Mapped[list[int]] = mapped_column(JSONB, default=list, server_default="[]")
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class SeriesAgendaItem(Base):
    """One typed line of a series' "Other agenda items". Consumed (kept, no longer shown) when the
    next meeting of the series is recorded: `consumed_at` is the truth, `consumed_by_meeting_id`
    only says which meeting (it goes null if that meeting is deleted; the line stays consumed)."""

    __tablename__ = "series_agenda_items"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    series_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("series.id", ondelete="CASCADE"), index=True
    )
    text: Mapped[str] = mapped_column(String(200))
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    consumed_by_meeting_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("meetings.id", ondelete="SET NULL")
    )
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SeriesAgendaSend(Base):
    """One press of "Send agenda to attendees": the id the mail rows carry and what the daily caps
    count. Holds no text; the agenda is read when each mail is sent."""

    __tablename__ = "series_agenda_sends"
    __table_args__ = (Index("ix_series_agenda_sends_sender", "sender_user_id", "created_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    series_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("series.id", ondelete="CASCADE"), index=True
    )
    sender_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    recipient_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SeriesReminder(Base):
    """One reminder mail owed to one person for one occurrence of a series. The unique key is what
    makes it once, however many ticks or workers see the same event. Holds ids and times only; the
    mail is written when it is sent."""

    __tablename__ = "series_reminders"
    __table_args__ = (
        CheckConstraint(_in("kind", REMINDER_KINDS), name="ck_series_reminders_kind"),
        UniqueConstraint(
            "series_id", "user_id", "kind", "starts_at", name="uq_series_reminders_occurrence"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    series_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("series.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(4))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # The occurrence the "Join meeting" button reads its link from at send time; the event row is
    # removed two days after it ends, which only drops the button.
    calendar_event_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("calendar_events.id", ondelete="SET NULL")
    )
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
        Index("ix_ai_runs_user_id_created_at", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    meeting_id: Mapped[int | None] = mapped_column(ForeignKey("meetings.id", ondelete="SET NULL"))
    # Whose call it was, for the per-account daily limit on translations: the meeting may be
    # deleted later (`meeting_id` is then nulled), the account's count must not drop with it.
    user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    kind: Mapped[str] = mapped_column(String(16))
    model: Mapped[str] = mapped_column(String(100))
    prompt_version: Mapped[str | None] = mapped_column(String(32))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    # Transcription is priced per audio minute, not per token: the seconds of audio sent.
    audio_seconds: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="ok", server_default="ok")
    error: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MeetingTranslation(Base):
    """The notes and transcript of one meeting in one other language, as a separate view: the
    original is never changed. `source_hash` is the hash of the notes and transcript that were
    translated, so a rewrite of the notes shows up as stale. `failure_code` is a short code, never
    meeting words. `started_at` and `attempts` are the worker's claim (see `app/translation.py`)."""

    __tablename__ = "meeting_translations"
    __table_args__ = (
        UniqueConstraint("meeting_id", "language", name="uq_meeting_translations_meeting_lang"),
        CheckConstraint(_in("status", TRANSLATION_STATUSES), name="ck_meeting_translations_status"),
        CheckConstraint(_in("language", NOTE_LANGUAGES), name="ck_meeting_translations_language"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    meeting_id: Mapped[int] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"))
    language: Mapped[str] = mapped_column(String(2))
    status: Mapped[str] = mapped_column(
        String(16), default="translating", server_default="translating"
    )
    notes_md: Mapped[str | None] = mapped_column(Text)
    transcript: Mapped[str | None] = mapped_column(Text)
    source_hash: Mapped[str] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(100))
    failure_code: Mapped[str | None] = mapped_column(String(32))
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MeetingShare(Base):
    """One "Share notes by email" request (spec section 10). Holds ids and the sender's own words
    only: the notes are read from the meeting when each mail is SENT, never copied here or into
    `mail_outbox`. Deleted with the meeting."""

    __tablename__ = "meeting_shares"
    __table_args__ = (
        CheckConstraint(_in("status", SHARE_STATUSES), name="ck_meeting_shares_status"),
        CheckConstraint(_in("language", SHARE_LANGUAGES), name="ck_meeting_shares_language"),
        CheckConstraint(_in("attach", SHARE_ATTACH), name="ck_meeting_shares_attach"),
        Index("ix_meeting_shares_meeting_id", "meeting_id", "id"),
        Index("ix_meeting_shares_sender_created", "sender_user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    meeting_id: Mapped[int] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"))
    sender_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE")
    )
    language: Mapped[str] = mapped_column(String(2))
    attach: Mapped[str] = mapped_column(String(8), default="none", server_default="none")
    include_transcript: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=sa.false()
    )
    # The sender's optional note: plain text, at most 500 characters, shown escaped and unlinked.
    message: Mapped[str] = mapped_column(Text, default="", server_default="")
    status: Mapped[str] = mapped_column(String(24))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MeetingShareRecipient(Base):
    """One person a share goes to. `user_id` is set when the address is an active member of the
    sender's team at share time. `status` is what the sender may be told: `not_sent` covers every
    reason a mail did not go (a suppressed address included, never named)."""

    __tablename__ = "meeting_share_recipients"
    __table_args__ = (
        CheckConstraint(
            _in("status", SHARE_RECIPIENT_STATUSES), name="ck_meeting_share_recipients_status"
        ),
        CheckConstraint("email = lower(email)", name="ck_meeting_share_recipients_email_lower"),
        UniqueConstraint("share_id", "email", name="uq_meeting_share_recipients_share_email"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    share_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("meeting_shares.id", ondelete="CASCADE")
    )
    email: Mapped[str] = mapped_column(String(254))
    name: Mapped[str | None] = mapped_column(String(100))
    user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ShareUsage(Base):
    """How many recipients an account asked to mail, and when: the daily limit's ledger. Kept apart
    from `meeting_shares` so deleting a meeting (which deletes its shares) does not give the day's
    capacity back. Holds no address and no meeting; goes with the account."""

    __tablename__ = "share_usage"
    __table_args__ = (Index("ix_share_usage_user_created", "user_id", "created_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"))
    recipients: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RateHit(Base):
    """One hit on a login or sign-up limit (`app/ratelimit.py`): the bucket's name, a keyed hash of
    the key (never an address or an email), and when (seconds since the epoch)."""

    __tablename__ = "rate_hits"
    __table_args__ = (Index("ix_rate_hits_bucket_key_at", "bucket", "key", "at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    bucket: Mapped[str] = mapped_column(String(32))
    key: Mapped[str] = mapped_column(String(32))
    at: Mapped[float] = mapped_column(sa.Float(precision=53))


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
    # The join runner (app/join_runner.py): when it first led (joins due before then are closed
    # silently, never joined retroactively), and since when the operator's switch has been off
    # (joins whose deadline passed meanwhile are closed silently when it comes back on).
    autojoin_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    autojoin_paused_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


@sa.event.listens_for(Session, "before_flush")
def _complete_the_join_with_its_meeting(session: Session, flush_context, instances) -> None:
    """A calendar join that is `joined` becomes `completed` in the SAME transaction as its meeting
    leaving `bot_sent` (spec 7.7, plan C12), whichever code moves the meeting: the worker (the bot
    left, or the join failed), Cancel, End, a purge. Done here, at the one place every status
    change passes, so no path can forget it and no crash can leave the two disagreeing. It frees
    the meeting code: a recurring meeting's next event may then join (`uq_scheduled_joins_live_code`
    holds a code while its join is live)."""
    # No look at the old value: an attribute that was never loaded has no history, and the update
    # below only touches a `joined` join, so it is a harmless no-op when the meeting was not live.
    leaving = [
        obj.id
        for obj in session.dirty
        if isinstance(obj, Meeting)
        and obj.id is not None
        and obj.status != "bot_sent"
        and sa.inspect(obj).attrs.status.history.has_changes()
    ]
    if leaving:
        session.execute(
            sa.update(ScheduledJoin)
            .where(ScheduledJoin.meeting_id.in_(leaving), ScheduledJoin.state == "joined")
            .values(state="completed")
            .execution_options(synchronize_session="fetch")
        )


INTEGRATION_KINDS = ("slack_webhook", "linear")


class Integration(Base):
    """A team's connection to an outside service (W10). `config_enc` is the vault blob of the
    secret (a Slack incoming-webhook URL); it is never returned by the API and never logged.
    `status` is `connected` or `failing`; `last_error` is a short fixed sentence, never text from
    the other service. One row per team and kind."""

    __tablename__ = "integrations"
    __table_args__ = (
        CheckConstraint(_in("kind", INTEGRATION_KINDS), name="ck_integrations_kind"),
        CheckConstraint("status in ('connected', 'failing')", name="ck_integrations_status"),
        UniqueConstraint("team_id", "kind", name="uq_integrations_team_kind"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    team_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("teams.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(24))
    config_enc: Mapped[bytes] = mapped_column(LargeBinary)  # nonce || ciphertext
    key_version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(12), default="connected", server_default="connected")
    # A non-secret name to show ("Engineering" for a Linear team); never the key.
    label: Mapped[str | None] = mapped_column(String(100))
    last_error: Mapped[str | None] = mapped_column(String(200))
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    connected_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IntegrationPost(Base):
    """One outbound post to an integration, written BEFORE the call is made: the unique index
    makes "once per meeting" true even if the worker retries, and counting a team's rows in the
    last hour is the rate cap. `meeting_id` is null for the test post made when connecting."""

    __tablename__ = "integration_posts"
    __table_args__ = (
        Index("ix_integration_posts_team_created", "team_id", "created_at"),
        Index(
            "uq_integration_posts_meeting",
            "team_id",
            "kind",
            "meeting_id",
            unique=True,
            postgresql_where=sa.text("meeting_id is not null"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    team_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("teams.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(24))
    meeting_id: Mapped[int | None] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TrackerIssue(Base):
    """The issue a task became in the team's tracker (one way, created once). Written before the
    call with `external_id` null as the claim, so two workers never create the same issue; a failed
    call deletes the claim."""

    __tablename__ = "tracker_issues"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    task_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("tasks.id", ondelete="CASCADE"), unique=True
    )
    team_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("teams.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(24))
    external_id: Mapped[str | None] = mapped_column(String(64))
    identifier: Mapped[str | None] = mapped_column(String(30))
    url: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_tracker_issues_team_created", "team_id", "created_at"),)


class TranscriptShadowMetric(Base):
    """Numbers from one meeting where, in `TRANSCRIPT_SOURCE=skribby`, our own transcription was
    ALSO run (a sampled check, `TRANSCRIPT_SHADOW_PERCENT`) and then thrown away, so the two can be
    compared over many meetings. Numbers only: never transcript text, a name or an email. The
    meeting is the one link, and the row goes with the meeting (a deleted meeting or account leaves
    nothing here)."""

    __tablename__ = "transcript_shadow_metrics"
    __table_args__ = (UniqueConstraint("meeting_id", name="uq_transcript_shadow_metrics_meeting"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    meeting_id: Mapped[int] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"))
    skribby_words: Mapped[int] = mapped_column(Integer)
    own_words: Mapped[int] = mapped_column(Integer)
    shared_words: Mapped[int] = mapped_column(Integer)
    # shared_words over the larger of the two counts, 0 to 1 (1 = the same words).
    shared_share: Mapped[float] = mapped_column(Float)
    skribby_speakers: Mapped[int] = mapped_column(Integer)
    unnamed_segments: Mapped[int] = mapped_column(Integer)
    segments: Mapped[int] = mapped_column(Integer)
    audio_seconds: Mapped[int] = mapped_column(Integer)
    # Which one the notes were made from: `skribby`, or `own` when Skribby's was not used.
    notes_source: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SkribbySegmentEvidence(Base):
    """What Skribby sent for one meeting, kept 7 days so a bad speaker labelling can be diagnosed
    (our own copy of Skribby's is deleted minutes after the meeting). `items` is each transcript
    item as it came (tag, `speaker_name`, the names in `potential_speaker_names`, start, end,
    text); `tags` the name the app derived for each tag and why (given, elimination, unknown).
    It holds the words people said: never logged, never in an API answer or a page. Deleted by the
    retention job after 7 days, with the meeting (`ON DELETE CASCADE`) and with the account."""

    __tablename__ = "skribby_segment_evidence"
    __table_args__ = (
        UniqueConstraint("meeting_id", name="uq_skribby_segment_evidence_meeting"),
        Index("ix_skribby_segment_evidence_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    meeting_id: Mapped[int] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"))
    items: Mapped[list] = mapped_column(JSONB)
    tags: Mapped[dict] = mapped_column(JSONB)
    # Whether SKRIBBY_NAME_BY_ELIMINATION was on when the names were derived.
    by_elimination: Mapped[bool] = mapped_column(Boolean)
    # Who was speaking when, from the meeting platform itself (Skribby's `with-speaker-events`
    # answer, as returned, capped at 400 KB; `app/skribby.py::speaker_events_record`). Null when it
    # could not be fetched. Same retention and deletion as the row.
    events: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
