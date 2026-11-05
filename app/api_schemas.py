"""Request and response bodies of the JSON API (`app/api.py`).

The frontend generates its TypeScript types from these (see `scripts/export_openapi.py`).
"""

from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field


def _utc(value: datetime) -> datetime:
    return value.astimezone(UTC)  # serialised as ISO 8601 ending in "Z"


UtcDatetime = Annotated[datetime, AfterValidator(_utc)]

Status = Literal["bot_sent", "processing", "ready", "failed"]
Step = Literal["waiting", "audio", "transcribing", "speakers", "notes", "done"]
ExtractionStatus = Literal["pending", "running", "done", "failed", "skipped"]
Tone = Literal["success", "danger", "info", "warning", "neutral"]
Platform = Literal["google_meet", "zoom"]
RetryBlock = Literal["not_failed", "no_audio", "no_speech", "audio_deleted"]


class Body(BaseModel):
    """Request bodies: no unknown fields, no coercion (`"3"` is not a template id)."""

    model_config = ConfigDict(extra="forbid", strict=True)


# ---- errors ----


class FieldError(BaseModel):
    field: str
    message: str


class ErrorBody(BaseModel):
    code: str
    message: str
    fields: list[FieldError] | None = None  # only on `validation_error`


class ErrorResponse(BaseModel):
    error: ErrorBody


# ---- session ----


class UserOut(BaseModel):
    email: str
    name: str | None
    is_admin: bool
    email_verified: bool
    # The IANA zone the person chose on Settings; null until they do (times are then UTC in mail,
    # and the browser's own zone on the pages).
    time_zone: str | None = None


class UsageOut(BaseModel):
    # Seconds of recorded audio counted against the account, including meetings still running.
    used_seconds: int
    # The most the account may record in total; null when there is no cap (an admin).
    cap_seconds: int | None


class BadgesOut(BaseModel):
    # Review items the user has not checked yet: the number on the Review item in the sidebar.
    review_open: int


class SessionState(BaseModel):
    authenticated: bool
    csrf_token: str
    # All four null while logged out.
    user: UserOut | None
    usage: UsageOut | None
    # Notifications the user has not read yet: the number on the bell.
    unread_notifications: int | None
    # Counts for the sidebar.
    badges: BadgesOut | None


class Login(Body):
    email: str
    password: str


# ---- public account routes ----

PilotStateName = Literal["open", "full", "closed"]


class SignUp(Body):
    name: str = Field(max_length=2000)
    email: str = Field(max_length=2000)
    password: str = Field(max_length=2000)
    accept_terms: bool  # must be true: sign-up refuses with 422 `terms_required` otherwise
    invite_token: str | None = Field(default=None, max_length=2000)


class EmailIn(Body):
    email: str = Field(max_length=2000)


class VerifyIn(Body):
    token: str = Field(max_length=2000)
    # The password chosen at sign-up: a verification link confirms only that one.
    password: str = Field(max_length=2000)


class ResetIn(Body):
    token: str = Field(max_length=2000)
    password: str = Field(max_length=2000)


class Accepted(BaseModel):
    """The one answer sign-up, resend and forgot-password give, whatever the address."""

    status: Literal["check_your_email"]


class Verified(BaseModel):
    status: Literal["verified"]


class PasswordChanged(BaseModel):
    status: Literal["password_changed"]


class PilotOut(BaseModel):
    state: PilotStateName
    # The Google email of a visitor who was turned away by `pilot_full` (a 15-minute cookie), so
    # the waitlist form can be pre-filled; otherwise null.
    prefill_email: str | None
    # Whether "Continue with Google" exists on this server (the OAuth client is configured).
    google_signin: bool


class WaitlistIn(Body):
    email: str = Field(max_length=2000)
    name: str | None = Field(default=None, max_length=2000)


class WaitlistAccepted(BaseModel):
    status: Literal["on_the_list"]


# ---- meetings ----


class NewMeeting(Body):
    url: str
    template_id: int


class Regenerate(Body):
    template_id: int


class MeetingSummary(BaseModel):
    id: int
    title: str
    platform: Platform
    native_id: str
    status: Status
    step: Step
    status_text: str
    status_tone: Tone
    template_id: int
    # The name of the template the notes were written with: its name now, or the name it had when
    # it was deleted (a deleted template is no longer in GET /api/templates).
    template_name: str
    created_at: UtcDatetime


TranslationLanguage = Literal["fr", "es"]
TranslationStatus = Literal["translating", "ready", "failed"]


class TranslationBrief(BaseModel):
    """What the meeting page needs to know about a translation without fetching it."""

    language: TranslationLanguage
    status: TranslationStatus
    # True for a finished translation of text that has since changed (the notes were rewritten):
    # offer "Translate again".
    stale: bool


class MeetingDetail(MeetingSummary):
    # Set (never null) when status is "failed", null otherwise.
    failure_reason: str | None
    # Why a failed meeting cannot be retried; null when it can, and null unless failed.
    retry_block: RetryBlock | None
    retry_block_message: str | None
    can_cancel: bool
    # True while the bot is in the call (waiting to be let in does not count; use cancel):
    # `POST /end` stops it and the notes are written from what it recorded.
    can_end: bool
    can_retry: bool
    can_regenerate: bool
    join_attempts: int
    max_join_attempts: int
    bot_left_early: bool
    speakers_incomplete: bool
    # Seconds until the page should ask again; null once the meeting is ready or failed, except
    # while a failed join counts down its wait before it can be sent again.
    refresh_after_seconds: int | None
    # True while the meeting is bot_sent or processing and the background worker has not checked
    # in for over 20 minutes, so nothing will move until it is running again. False otherwise.
    worker_stale: bool
    # While Google has refused the first bot and the worker is pausing before it sends the second:
    # about when that happens (the status is still "bot_sent"). Null otherwise.
    join_retry_at: UtcDatetime | None
    # Where reading the notes into decisions, open questions and review items stands; null unless
    # the meeting is ready. `pending` and `running` are one state for the screen (a few seconds,
    # or a few minutes if the worker is busy); `failed` means it gave up after three tries (the
    # lists read from the notes are still there, only the "heard as" hints are missing, and
    # rewriting the notes starts it again); `skipped` is an older meeting that only has decisions.
    extraction_status: ExtractionStatus | None
    # Only when status is "ready", else null. `notes_html` is `notes` rendered by the server's
    # escaping markdown renderer: show it as HTML, and copy `notes`.
    notes: str | None
    notes_html: str | None
    # The speaker-labelled transcript when there is one, else the plain one, else null.
    transcript: str | None
    # The translations this meeting has (any state), at most one per language; empty unless ready.
    translations: list[TranslationBrief]


class MeetingList(BaseModel):
    meetings: list[MeetingSummary]
    has_more: bool
    next_before_id: int | None
    # True when the background worker has not checked in for over 20 minutes.
    worker_stale: bool
    # Seconds until the list should ask again: set while any listed meeting is bot_sent or
    # processing (its status text can change), null otherwise. The same cadence as the meeting.
    refresh_after_seconds: int | None


# ---- templates ----


class TemplateIn(Body):
    name: str
    instructions: str


class TemplateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    instructions: str


class TemplateList(BaseModel):
    templates: list[TemplateOut]


# ---- vocabulary ----


class NewTerm(Body):
    term: str


class TermOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    term: str


class TermList(BaseModel):
    terms: list[TermOut]


# ---- email preferences ----


class EmailPreferencesIn(Body):
    """`notify_email` must be a JSON boolean (the string "false" is refused) and nothing else may
    be sent: this body cannot change any other setting."""

    notify_email: bool


class EmailPreferencesOut(BaseModel):
    """`notify_email`: whether the owner of a meeting is emailed when its notes are ready. On
    unless it was turned off. Sign-in mail (verification, password reset) is not affected."""

    notify_email: bool


# ---- notifications ----

Severity = Literal["info", "warning", "error"]


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    severity: Severity
    title: str
    body: str
    meeting_id: int | None
    created_at: UtcDatetime
    read_at: UtcDatetime | None


class NotificationList(BaseModel):
    notifications: list[NotificationOut]
    has_more: bool
    next_before_id: int | None


class UnreadCount(BaseModel):
    unread_notifications: int


# ---- admin ----

AdminStatus = Literal["active", "suspended"]
MAX_CAP_SECONDS = 1000 * 3600  # an admin override or default of more than 1000 hours is a typo


class AdminUser(BaseModel):
    id: int
    email: str
    name: str | None
    is_admin: bool
    status: Literal["active", "suspended"]
    email_verified: bool
    has_password: bool
    has_google: bool
    created_at: UtcDatetime
    last_login_at: UtcDatetime | None
    used_seconds: int
    # What the user may record in total (their override, else the default); null = no cap.
    cap_seconds: int | None
    # The admin's per-user override alone; null = the default applies.
    hours_cap_override: int | None


class AdminUserList(BaseModel):
    users: list[AdminUser]
    has_more: bool
    next_before_id: int | None


class AdminUserPatch(Body):
    """Only the fields present are changed. `hours_cap_seconds: null` clears the override."""

    status: AdminStatus | None = None
    hours_cap_seconds: int | None = Field(default=None, ge=0, le=MAX_CAP_SECONDS)


class AdminDeleting(BaseModel):
    status: Literal["deleting"]


class AdminResetLink(BaseModel):
    # Shown once and never stored in the clear. Valid for 30 minutes, single use.
    reset_url: str


class AdminWaitlistEntry(BaseModel):
    id: int
    email: str
    name: str | None
    status: Literal["waiting", "invited", "joined", "removed"]
    created_at: UtcDatetime
    invited_at: UtcDatetime | None


class AdminWaitlist(BaseModel):
    entries: list[AdminWaitlistEntry]
    has_more: bool
    next_before_id: int | None


class AdminInvite(BaseModel):
    # True when the invitation was queued as an email. When it is false (no mail provider) the
    # admin must hand `invite_url` over; it is null when the mail was queued.
    emailed: bool
    invite_url: str | None
    expires_at: UtcDatetime


class AdminPilot(BaseModel):
    signups_open: bool
    max_accounts: int | None
    default_hours_cap_seconds: int | None
    password_signup_enabled: bool
    # Accounts that hold a slot now (non-admin, not deleted, plus live invitations).
    slots_taken: int
    state: PilotStateName


class AdminPilotIn(Body):
    signups_open: bool
    max_accounts: int | None = Field(ge=0, le=10_000)
    default_hours_cap_seconds: int | None = Field(ge=0, le=MAX_CAP_SECONDS)
    password_signup_enabled: bool


class AdminMailFailure(BaseModel):
    id: int
    to_email: str
    template: str
    attempts: int
    last_error: str | None
    created_at: UtcDatetime


class AdminMailFailures(BaseModel):
    failures: list[AdminMailFailure]


# ---- decisions and review ----


class MeetingRef(BaseModel):
    id: int
    title: str
    platform: Platform
    created_at: UtcDatetime


class DecisionOut(BaseModel):
    id: int
    text: str


class DecisionGroup(BaseModel):
    meeting: MeetingRef
    decisions: list[DecisionOut]


class DecisionList(BaseModel):
    # One group per meeting, newest meeting first; decisions in the notes' order.
    groups: list[DecisionGroup]
    has_more: bool
    # Pass as `before_id` for the next page of meetings.
    next_before_id: int | None


class ReviewItemOut(BaseModel):
    id: int
    # The "Please check" bullet, as the notes say it.
    text: str
    # Set only when the bullet is about a name or term the transcript has and that may have been
    # misheard: the word as it was transcribed. Show the "Correct spelling" box only then.
    heard_as: str | None
    checked_at: UtcDatetime | None
    meeting: MeetingRef


class ReviewList(BaseModel):
    items: list[ReviewItemOut]
    # How many items are open in all (the sidebar badge), whatever `state` was asked for.
    open_count: int
    # True when more than 200 matched and only the newest 200 are listed.
    truncated: bool


class ReviewTermIn(Body):
    term: str


class ReviewTermOut(BaseModel):
    item: ReviewItemOut
    term: TermOut


# ---- translation and settings ----


class TranslationIn(Body):
    # `fr` or `es`. Anything else is 422 `language_unsupported` (English is the original).
    language: str


class TranslationOut(BaseModel):
    language: TranslationLanguage
    status: TranslationStatus
    stale: bool
    # Only when status is "ready", else null. `notes` is Markdown whose headings come from a fixed
    # dictionary; `notes_html` is it rendered with links and raw HTML off (show it as HTML, copy
    # `notes`). `transcript` has the original's lines and `[Name]:` labels, as plain text to show
    # escaped; it is null when the meeting has no transcript.
    notes: str | None
    notes_html: str | None
    transcript: str | None
    # Only when status is "failed": a short code and a plain sentence to show.
    failure_code: str | None
    failure_message: str | None
    updated_at: UtcDatetime


class LanguagePreferencesOut(BaseModel):
    # `en` means the original (no translation); `fr` or `es` opens meetings in that language
    # when a translation exists.
    default_language: Literal["en", "fr", "es"]


class TimeZoneOut(BaseModel):
    # The stored IANA zone name, or "UTC" when none was ever chosen.
    time_zone: str
    # True once the person chose one (or used their device); false while "UTC" is only the default.
    is_set: bool


class TimeZoneIn(Body):
    # An IANA zone name such as "Africa/Lagos"; anything the server does not know is 422
    # `time_zone_unsupported`.
    time_zone: str


class LanguagePreferencesIn(Body):
    # `en`, `fr` or `es`; anything else is 422 `language_unsupported`.
    default_language: str
