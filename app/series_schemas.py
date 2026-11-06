"""Request and response bodies of the recurring-meeting routes (`app/api_series.py`) and the two
small shapes the meeting page embeds (`SeriesRef`, `QuestionOut`)."""

from pydantic import BaseModel, Field

from app.api_schemas import Body, MeetingRef, Platform, SeriesRef, TaskOut, UtcDatetime


class SeriesCard(BaseModel):
    """One Recurring card of the Meetings screen. The counts cover only what the caller may
    read. `next_*` are null when no calendar event ahead is known (hide the chip)."""

    id: int
    title: str
    cadence: str
    platform: Platform | None
    next_starts_at: UtcDatetime | None
    next_text: str | None
    unfinished_tasks: int
    open_questions: int


class SeriesList(BaseModel):
    # The zone `cadence` and `next_text` are written in: the caller's saved zone, else UTC.
    timezone: str
    series: list[SeriesCard]


class AttendeeOut(BaseModel):
    # Plain text. Names come from speaker labels; an address exists only for a teammate and is
    # not sent.
    name: str
    is_teammate: bool


class CarriedQuestion(BaseModel):
    id: int
    text: str
    # The meeting it was asked in, null when the caller may not read it (cannot happen in this
    # list today; kept so a future shared view does not change the shape).
    meeting: MeetingRef | None
    can_update: bool


class AgendaItemOut(BaseModel):
    id: int
    text: str
    can_remove: bool


class PastMeeting(BaseModel):
    id: int
    title: str
    created_at: UtcDatetime
    duration_seconds: int | None
    tasks_count: int


class SeriesPage(BaseModel):
    id: int
    title: str
    cadence: str
    platform: Platform | None
    timezone: str
    next_starts_at: UtcDatetime | None
    next_text: str | None
    # The caller may rename the series (its creator, or an owner or admin).
    can_rename: bool
    # Teammates who attended the last three meetings, other than the caller, who the agenda would
    # go to. 0 disables "Send agenda to attendees" (it would answer 409 `no_attendees`).
    agenda_recipients: int
    unfinished: list[TaskOut]
    # "Mon 2 Nov": the last meeting's date; `finished` is what was completed on or after it.
    finished_since_text: str | None
    finished: list[TaskOut]
    questions: list[CarriedQuestion]
    agenda: list[AgendaItemOut]
    past_meetings: list[PastMeeting]
    attendees: list[AttendeeOut]


class SeriesCreate(Body):
    title: str = Field(min_length=1, max_length=200)


class SeriesPatch(Body):
    title: str = Field(min_length=1, max_length=200)


class MeetingSeriesIn(Body):
    # null takes the meeting out of its recurring meeting.
    series_id: int | None = Field(ge=1, le=2**53)


class MeetingSeriesOut(BaseModel):
    series: SeriesRef | None


class AgendaItemIn(Body):
    text: str = Field(min_length=1, max_length=200)


class AgendaSent(BaseModel):
    # Who it is going to (plain-text names); the mail goes out within a minute.
    count: int
    names: list[str]


class QuestionPatch(Body):
    carry: bool | None = None
    resolved: bool | None = None


class ReminderPreferencesIn(Body):
    notify_reminders: bool


class ReminderPreferencesOut(BaseModel):
    notify_reminders: bool
