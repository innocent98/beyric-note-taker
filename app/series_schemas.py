"""Request and response bodies of the recurring-meeting routes (`app/api_series.py`) and the two
small shapes the meeting page embeds (`SeriesRef`, `QuestionOut`)."""

from typing import Literal

from pydantic import BaseModel, Field

from app.api_schemas import Body, MeetingRef, Platform, SeriesRef, TaskOut, TeamRef, UtcDatetime


class ProjectLink(BaseModel):
    """The project a recurring meeting is linked to (its tasks are filed there)."""

    id: int
    # Plain text; empty until typed (show "Untitled project").
    name: str


class SeriesTeam(TeamRef):
    """The row's team. `is_personal` is true for someone's personal space: label it "Personal".
    What a personal row lists and hides (no next meeting, no "Keeps coming up", nobody to send the
    agenda to) holds only while `SeriesPage.scoped` is true."""

    is_personal: bool


class SeriesCard(BaseModel):
    """One Recurring card of the Meetings screen. The counts cover only what the caller may
    read. `next_*` are null when no calendar event ahead is known (hide the chip)."""

    id: int
    title: str
    # The team this row belongs to (a row is one team's, or a person's personal space's): send its
    # id as `X-Team-Id` for the task drawer. Always present.
    team: SeriesTeam
    cadence: str
    platform: Platform | None
    # Null on a personal-space row (`SERIES_FOLLOW_MEETINGS`): hide the "next meeting" chip.
    next_starts_at: UtcDatetime | None
    next_text: str | None
    unfinished_tasks: int
    open_questions: int
    # The project its tasks are filed into; null when not linked (`PUT /api/series/{id}/project`).
    project: ProjectLink | None


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


class TopicOut(BaseModel):
    """One "Keeps coming up" row. Only topics the caller may read at least two meetings of are
    sent, and `meetings` are only the ones they may read (so "N meetings" and the dates never
    count a teammate's private one)."""

    id: int
    # Plain text, at most 60 characters ("Pricing model").
    label: str
    # The sentence "Suggested agenda" offers for it, plain text, at most 100 characters.
    agenda: str
    reason: Literal["no_decision", "no_owner", "not_final"]
    # The sub-line, written by the server from `reason` and the number of readable meetings:
    # "Discussed in 3 meetings without a decision".
    line: str
    # "Mon 26 Oct, Fri 30 Oct and Mon 2 Nov 2026", in the caller's zone, oldest first.
    dates_text: str
    meetings: list[MeetingRef]


class AgendaSuggestion(BaseModel):
    """One "Suggested agenda" row: `text` is what "Add to agenda" posts to the agenda (and what
    "Added" is matched against, by exact text, in the live agenda list)."""

    kind: Literal["topic", "overdue", "questions", "suggested"]
    text: str


class SeriesTeamCard(BaseModel):
    """A team's card of the same recurring meeting, named on a Personal page (`team_card`)."""

    id: int
    team: SeriesTeam


class SeriesPage(BaseModel):
    id: int
    title: str
    # The team this row belongs to; see `SeriesCard.team`.
    team: SeriesTeam
    # Only on a Personal card from the calendar, and only while `scoped`: a card of the SAME
    # recurring meeting in an ordinary team of the caller's that the caller may see, else null. A
    # meeting of the event that goes to that team's card is grouped there, so the agenda lines typed
    # on THIS page are not used by it: draw the hint. LEAD-DESIGNED, not in the designer's files.
    team_card: SeriesTeamCard | None
    # True while `SERIES_FOLLOW_MEETINGS` is on: the page lists only the meetings of its team scope,
    # and a personal row has no next meeting. False: the row is the old one (a personal team's row
    # can then hold shared meetings and has a next date), so draw the page as it was.
    scoped: bool
    cadence: str
    platform: Platform | None
    timezone: str
    next_starts_at: UtcDatetime | None
    next_text: str | None
    # "Opens Sprint 13" under "Next meeting", when the next meeting is the one that opens that
    # sprint for the caller: the rule of the one-hour reminder mail's "Sprint 13 opens". null (no
    # line) otherwise. Nothing is ever sent for "Closes ...": no rule for it exists yet.
    sprint_note: str | None
    # The caller may rename the series (its creator, or an owner or admin).
    can_rename: bool
    # The caller may remove this recurring meeting (`DELETE /api/series/{id}`): an owner or
    # admin of its team; for a personal row, its owner; and the person who made the row only
    # while it is completely empty (no meeting, no waiting line, no project, no mail to go).
    # false: do not draw the button. LEAD-DESIGNED, not in the designer's files.
    can_remove: bool
    # Teammates who attended the last three meetings, other than the caller, who the agenda would
    # go to. 0 disables "Send agenda to attendees" (it would answer 409 `no_attendees`).
    agenda_recipients: int
    unfinished: list[TaskOut]
    # "Mon 2 Nov": the last meeting's date; `finished` is what was completed on or after it.
    finished_since_text: str | None
    finished: list[TaskOut]
    questions: list[CarriedQuestion]
    agenda: list[AgendaItemOut]
    # "Keeps coming up", read from storage (no model call on a page load). Empty = hide the section.
    topics: list[TopicOut]
    # "Suggested agenda", built from the topics above, the overdue tasks, the carried questions and
    # the suggested tasks of this series. Empty = hide the section.
    suggested_agenda: list[AgendaSuggestion]
    past_meetings: list[PastMeeting]
    attendees: list[AttendeeOut]


class SeriesRemoved(BaseModel):
    """What `DELETE /api/series/{id}` did, for the toast. Counts only."""

    # How many of the row's meetings the caller may read, which are now ordinary meetings, not
    # grouped. They are all still there.
    meetings_kept: int
    # The waiting "Other agenda items" lines that went with the row.
    agenda_lines_removed: int
    # true: the row came from a calendar recurring event and the event is now remembered as removed
    # for this team (or this personal space): it will not be made again, and later meetings of it
    # are not grouped there. false: a recurring meeting made by hand has no event to remember.
    remembered: bool


class RemovedRecurring(BaseModel):
    """One row of `GET /api/series/removed`: a calendar recurring meeting that was removed from a
    scope and can be brought back. LEAD-DESIGNED, not in the designer's files."""

    # The removal mark's id, which `POST /api/series/removed/{id}/bring-back` takes (not a series).
    id: int
    # What the CALLER'S OWN calendar calls the event, else "Recurring meeting". Never a teammate's.
    title: str
    # The scope it was removed from.
    team: SeriesTeam
    removed_at: UtcDatetime
    # The name of the owner, admin or person who removed it; null once they have left the team.
    removed_by: str | None


class RemovedList(BaseModel):
    removed: list[RemovedRecurring]


class BroughtBack(BaseModel):
    """What `POST /api/series/removed/{id}/bring-back` did: the scope whose mark was cleared. No
    card is made by the call itself (a Personal card is made by the next hourly calendar sync; a
    team's by the next time an owner or admin shares a meeting of it, or their recording is shared
    automatically)."""

    team: SeriesTeam


class SeriesCreate(Body):
    title: str = Field(min_length=1, max_length=200)


class SeriesPatch(Body):
    title: str = Field(min_length=1, max_length=200)


class SeriesProjectIn(Body):
    # null unlinks the recurring meeting from its project.
    project_id: int | None = Field(ge=1, le=2**53)


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
