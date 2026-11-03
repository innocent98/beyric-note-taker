"""Request and response bodies of the JSON API (`app/api.py`).

The frontend generates its TypeScript types from these (see `scripts/export_openapi.py`).
"""

from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict


def _utc(value: datetime) -> datetime:
    return value.astimezone(UTC)  # serialised as ISO 8601 ending in "Z"


UtcDatetime = Annotated[datetime, AfterValidator(_utc)]

Status = Literal["bot_sent", "processing", "ready", "failed"]
Step = Literal["waiting", "audio", "transcribing", "speakers", "notes", "done"]
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


class UsageOut(BaseModel):
    # Seconds of recorded audio counted against the account, including meetings still running.
    used_seconds: int
    # The most the account may record in total; null when there is no cap (an admin).
    cap_seconds: int | None


class SessionState(BaseModel):
    authenticated: bool
    csrf_token: str
    # Both null while logged out.
    user: UserOut | None
    usage: UsageOut | None


class Login(Body):
    email: str
    password: str


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
    created_at: UtcDatetime


class MeetingDetail(MeetingSummary):
    # Set (never null) when status is "failed", null otherwise.
    failure_reason: str | None
    # Why a failed meeting cannot be retried; null when it can, and null unless failed.
    retry_block: RetryBlock | None
    retry_block_message: str | None
    can_cancel: bool
    # True while the bot is in the call or waiting to be let in (status_text says which):
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
    # Only when status is "ready", else null. `notes_html` is `notes` rendered by the server's
    # escaping markdown renderer: show it as HTML, and copy `notes`.
    notes: str | None
    notes_html: str | None
    # The speaker-labelled transcript when there is one, else the plain one, else null.
    transcript: str | None


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
