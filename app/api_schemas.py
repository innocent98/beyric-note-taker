"""Request and response bodies of the JSON API (`app/api.py`).

The frontend generates its TypeScript types from these (see `scripts/export_openapi.py`).
"""

from datetime import UTC, date, datetime
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
    # Open tasks the user can see that are still marked New: the number on the Tasks item.
    tasks_new: int


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
    # How many tasks this meeting has that the caller may see ("8 tasks" on the meetings list).
    tasks_count: int


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
    # True for the owner of a meeting that is ready or failed: `DELETE /api/meetings/{id}` is 409
    # `meeting_active` while the bot is in the call or the notes are being written.
    can_delete: bool
    # Whether the meeting is shared with the owner's team (the "Share with team" switch). Only the
    # owner may change it: `can_share` is false for a teammate reading a shared meeting.
    shared_with_team: bool
    can_share: bool
    # Who recorded it, for a teammate reading a shared meeting ("Shared by Dan"); null for the
    # owner's own view.
    shared_by: str | None
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
    # Length of the recording in seconds (what the hours cap counts); null until the recording
    # has been prepared, and for a meeting that never recorded.
    duration_seconds: int | None
    # The people named in the speaker-labelled transcript, in the order they first speak. Empty
    # when the transcript has no speaker names.
    participants: list[str]
    # For the page's "Decisions and review" card: this meeting's decisions that are still shown on
    # the Decisions screen, and its review items not yet checked. Both 0 unless the meeting is
    # ready; read `extraction_status` before trusting a 0 while it is pending or running.
    decisions_count: int
    review_open_count: int
    # The meeting's action items as tasks, in the order of the notes. Empty until the extraction
    # has run (read `extraction_status`) and when the owner has tasks switched off.
    tasks: list["TaskOut"]


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


# ---- tasks ----

TaskStatus = Literal["todo", "doing", "done"]
TaskSource = Literal["ai", "manual"]


class PersonOut(BaseModel):
    """A member of the caller's team who can be given a task."""

    user_id: int
    name: str
    is_you: bool


class TaskOut(BaseModel):
    id: int
    # Plain text, at most 200 characters. Never render it as a link or as HTML.
    title: str
    status: TaskStatus
    # The teammate it is assigned to; null is "Needs owner".
    assignee: PersonOut | None
    # The name as said in the meeting, kept when nobody on the team matched it. The drawer's
    # "was named as the owner in the meeting, but is not on your team" line reads this.
    spoken_owner: str | None
    due_date: date | None
    # The words about timing as said ("before Friday"), also when no date could be worked out.
    due_phrase: str | None
    # The words from the transcript that show the task, at most 300 characters, plain text.
    # Null when none was found in the transcript (see `unverified`) and for a manual task.
    quote: str | None
    is_new: bool
    # The quote the AI gave was not found in the transcript, so none is shown: worth a look.
    unverified: bool
    source: TaskSource
    # The meeting's date in its owner's time zone; the day it was made for a manual task.
    assigned_on: date | None
    done_at: UtcDatetime | None
    created_at: UtcDatetime
    # True when the task came from a meeting. `meeting` is set only when the caller may read that
    # meeting: someone given a task from a private meeting sees the task and its quote, nothing
    # else (spec 8.2), so for them `from_meeting` is true and `meeting` is null.
    from_meeting: bool
    meeting: MeetingRef | None
    # The project and milestone it is filed under (ids of `GET /api/projects` and
    # `GET /api/milestones`), null for none.
    project_id: int | None
    milestone_id: int | None
    # The sprint it is planned into (ids of `GET /api/sprints`), null is the Backlog.
    sprint_id: int | None
    # Moved on from a sprint that closed with it unfinished ("Carried over").
    carried: bool
    # Filing was a guess (one keyword hit, or a tie): show "Check project" until someone confirms
    # (`PATCH` with `check_project: false`) or picks a project.
    check_project: bool
    # What the caller may do with it, so the screen shows only what will work.
    can_update: bool  # status, due date, project, milestone
    can_edit: bool  # the title
    can_assign: bool  # give it to someone else, or to nobody
    can_claim: bool  # take a task that has no owner yet
    can_delete: bool


class AttentionOut(BaseModel):
    # Open tasks with no owner.
    needs_owner: int
    # Open tasks whose quote could not be found in the transcript.
    unverified: int
    # Open tasks filed into a project by a guess ("1 needs its project checked").
    check_project: int


class TaskList(BaseModel):
    tasks: list[TaskOut]
    # Who the caller can give a task to (the active members of their team).
    people: list[PersonOut]
    # Counted over every open task the caller can see, not only this page or filter.
    attention: AttentionOut
    has_more: bool
    next_before_id: int | None


class MeetingTasks(BaseModel):
    tasks: list[TaskOut]
    # How many different people the tasks are assigned to ("8 tasks created, assigned to 3").
    assigned_people: int
    extraction_status: ExtractionStatus | None


class TaskCreate(Body):
    # Empty is allowed: the drawer opens a blank task and fills the title in. At most 200.
    title: str = Field("", max_length=200)
    assignee_user_id: int | None = None
    # YYYY-MM-DD.
    due_date: date | None = Field(None, strict=False)
    status: TaskStatus = "todo"
    # A project of the caller's team (the Tasks screen files a new task under the open tab).
    project_id: int | None = None
    # An open sprint of the team's series for that project; null is the Backlog. Left out, the
    # sprint follows the due date: none is the Backlog, one inside or before the current sprint
    # is the current sprint, a later one is the next.
    sprint_id: int | None = Field(None, ge=1, le=9223372036854775807)


class TaskPatch(Body):
    """A partial change: send only the fields that change. A field you leave out is untouched;
    `assignee_user_id` and `due_date` set to null clear them."""

    title: str | None = Field(None, max_length=200)
    status: TaskStatus | None = None
    assignee_user_id: int | None = None
    due_date: date | None = Field(None, strict=False)
    # A project of the task's team, or null for none; choosing one clears `check_project`.
    project_id: int | None = None
    # A confirmed milestone of the task's team, or null for none.
    milestone_id: int | None = None
    # `false` is "this project is right"; the server sets it, `true` is refused.
    check_project: bool | None = None
    # An open sprint of the task's team (its series for the task's project), or null for the
    # Backlog. Owners and admins, and the maker of a manual task, only.
    sprint_id: int | None = Field(None, ge=1, le=9223372036854775807)


class TasksSettingsOut(BaseModel):
    # "Create tasks from action items": off means a meeting's action items stay only in its notes.
    auto_create_tasks: bool
    # "Email assignees": off means nobody is emailed about the tasks of this person's meetings.
    email_assignees: bool


class TasksSettingsIn(Body):
    auto_create_tasks: bool
    email_assignees: bool


class ExtractQueued(BaseModel):
    extraction_status: ExtractionStatus


TeamRole = Literal["owner", "admin", "member"]
SprintLength = Literal["week5", "week7", "week14", "month1", "month3", "month6", "custom"]
ProjectSprintLength = Literal["week5", "week7", "week14", "month1", "month3", "month6"]
InviteRole = Literal["admin", "member"]
InviteStatus = Literal["pending", "expired"]
InviteOutcome = Literal["sent", "resent", "already_member", "already_invited", "invalid_email"]


class TeamOut(BaseModel):
    id: int
    name: str
    # The team default for "Share with team": new meetings of the team's owner and admins are
    # shared when their notes are first written. Off by default; members' meetings are never
    # shared by it.
    share_new_meetings: bool
    my_role: TeamRole
    # Sprint settings (W9, Settings > Sprints; owners and admins change them): the length key,
    # the number of days when it is `custom`, and "Monday to Friday" for `week5`.
    sprint_length: SprintLength
    sprint_custom_days: int
    sprint_align_weekdays: bool
    member_count: int
    # Live (not expired) invitations; they count toward the pilot's accounts.
    pending_invites: int
    # Owner or admin: invite, remove members, edit the team and everyone's aliases.
    can_manage: bool
    # Owner only: change roles.
    can_change_roles: bool


class TeamPatch(Body):
    name: str | None = Field(None, min_length=1, max_length=100)
    share_new_meetings: bool | None = None
    sprint_length: SprintLength | None = None
    sprint_custom_days: int | None = Field(None, ge=1, le=365)
    # `true` is refused unless the length is `week5`.
    sprint_align_weekdays: bool | None = None


class TeamMemberOut(BaseModel):
    user_id: int
    # Plain text: the account name, or the part of the email before the @.
    name: str
    email: str
    role: TeamRole
    is_you: bool
    # How the person is named aloud in meetings, for matching task owners ("Matched in meetings
    # as"). Plain text.
    aliases: list[str]
    open_tasks: int
    joined_at: UtcDatetime
    can_change_role: bool
    can_edit_aliases: bool
    # True for removing someone else, and for "Leave team" on your own row.
    can_remove: bool


class TeamInviteOut(BaseModel):
    id: int
    email: str
    name: str | None
    role: InviteRole
    # `expired`: the link no longer works; Resend sends a new one.
    status: InviteStatus
    invited_by: str | None
    last_sent_at: UtcDatetime | None
    expires_at: UtcDatetime


class TeamMembers(BaseModel):
    members: list[TeamMemberOut]
    invites: list[TeamInviteOut]


class TeamMemberPatch(Body):
    """Change a role (owner only) and/or the aliases (an owner or admin, or the person themselves).
    Aliases replace the whole list."""

    role: InviteRole | None = None
    aliases: list[str] | None = Field(None, max_length=20)


class Invitee(Body):
    email: str = Field(max_length=254)
    # What you call them; becomes one of their aliases when they join. Optional.
    name: str | None = Field(None, max_length=100)


class InvitesIn(Body):
    invitees: list[Invitee] = Field(min_length=1, max_length=10)
    role: InviteRole = "member"


class InviteResult(BaseModel):
    email: str
    status: InviteOutcome
    invite: TeamInviteOut | None


class InvitesCreated(BaseModel):
    results: list[InviteResult]


class InvitePreview(BaseModel):
    """What an invitation link says, for the page that opens it (public: the token is the key)."""

    # The address the invitation was sent to: the account must use it.
    email: str
    # What the inviter calls them, if they said.
    name: str | None
    role: InviteRole
    team_name: str
    # Plain text; null when the inviter's name is not plainly a name.
    inviter_name: str | None
    # True when an account already has this address: sign in and accept. False: sign up with the
    # link (`invite_token` on `POST /api/signup`), or continue with Google.
    has_account: bool
    expires_at: UtcDatetime


class AcceptInviteIn(Body):
    token: str = Field(max_length=2000)


MeetingDetail.model_rebuild()


# ---- projects and milestones (W4) ----


class ProjectIn(Body):
    # Empty is allowed: "New project" opens a blank one and the name is typed in (autosave).
    name: str = Field("", max_length=100)
    # One of the six swatches, `#RRGGBB`; left out, the next in turn.
    color: str | None = Field(None, pattern=r"^#[0-9A-Fa-f]{6}$")
    keywords: list[str] = Field(default_factory=list, max_length=20)


class ProjectPatch(Body):
    """A partial change: send only what changes (the screen saves one field at a time)."""

    name: str | None = Field(None, max_length=100)
    color: str | None = Field(None, pattern=r"^#[0-9A-Fa-f]{6}$")
    # Replaces the list; at most 20, each at most 40 characters, duplicates ignored.
    keywords: list[str] | None = Field(None, max_length=20)
    # The project's own sprint length; null follows the team's. Owners and admins only.
    sprint_length: ProjectSprintLength | None = None


class MilestoneLine(BaseModel):
    """The one milestone line a project shows."""

    id: int
    name: str
    due_date: date


class ProjectOut(BaseModel):
    id: int
    # Plain text; empty until typed ("Untitled project").
    name: str
    color: str
    keywords: list[str]
    # Open tasks in it that the caller may see.
    open_tasks: int
    # The project's own sprint length, null when it follows the team's.
    sprint_length: ProjectSprintLength | None
    # Open tasks it has in the current sprint and in the Backlog (the caller's view).
    in_current_sprint: int
    in_backlog: int
    # The next milestone still ahead, else the latest that passed; null is "None yet".
    milestone: MilestoneLine | None
    created_at: UtcDatetime


class ProjectList(BaseModel):
    projects: list[ProjectOut]
    # True for an owner or admin of the team: the only people who may create, change or delete.
    can_manage: bool


class MilestoneIn(Body):
    name: str = Field(min_length=1, max_length=200)
    due_date: date = Field(strict=False)
    project_id: int | None = None


class MilestonePatch(Body):
    name: str | None = Field(None, min_length=1, max_length=200)
    due_date: date | None = Field(None, strict=False)
    project_id: int | None = None


class MilestoneConfirm(Body):
    # Left out, the project the milestone was filed under when it was proposed stays.
    project_id: int | None = None


class MilestoneOut(BaseModel):
    id: int
    name: str
    due_date: date
    project_id: int | None
    # The meeting it was proposed from, when the caller may read that meeting.
    source_meeting: MeetingRef | None
    # Plain text, at most 300 characters: the words said in the meeting (proposals only).
    quote: str | None
    # Null is a proposal: not a milestone yet, nothing can be filed under it.
    confirmed_at: UtcDatetime | None
    # Tasks filed under it that the caller may see, and how many are done (confirmed ones).
    task_count: int
    done_count: int
    can_confirm: bool
    can_edit: bool


class MilestoneList(BaseModel):
    milestones: list[MilestoneOut]


class MeetingMilestones(BaseModel):
    """What a meeting's page shows: dates proposed from it ("Set milestone: <name>, <date>?") and
    the ones already confirmed."""

    milestones: list[MilestoneOut]


# ---- sprints (W9) ----

SprintState = Literal["closed", "current", "next"]


class SprintOut(BaseModel):
    id: int
    number: int
    # "Sprint 12".
    name: str
    start_date: date
    end_date: date
    state: SprintState
    closed_at: UtcDatetime | None
    # Closed by hand before its end date.
    closed_early: bool
    # Tasks in it the caller may see, and how many of those are done ("3 of 8 done"). For the
    # current and next sprint of "all projects" they cover every series' current and next sprint.
    tasks_total: int
    tasks_done: int


class SprintList(BaseModel):
    # The series the list is for: a project that has its own sprint length, else the team's.
    project_id: int | None
    own_series: bool
    # Today in the viewer's time zone, for "4 days left" and "Starts Mon 9 Nov".
    today: date
    # The length this series runs on, as the Settings list names it.
    sprint_length: SprintLength
    sprint_custom_days: int
    sprint_align_weekdays: bool
    # Up to 20 closed sprints (newest last), then the current and the next.
    sprints: list[SprintOut]
    current_id: int
    next_id: int
    can_manage: bool


class SprintClosed(BaseModel):
    closed: SprintOut
    current: SprintOut
    # Unfinished tasks the caller may see that moved on ("3 unfinished tasks moved to Sprint 13").
    moved: int
