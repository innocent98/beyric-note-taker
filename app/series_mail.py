"""The two mails of a recurring meeting (spec 7): the agenda a teammate sends, and the reminders
before the meeting. Both are NOTES templates (`app/mail_templates.py`): the outbox row holds ids
only and the text is read when the mail is sent, so no meeting word is ever copied into
`mail_outbox`.

**Who gets them.** Only active members of the series' team who attended one of the last three
meetings (their name in the speaker labels, matched like a task owner, or the account whose bot
recorded) and have not switched series mail off (`user_settings.notify_reminders`). Never an
outside address, never a stored calendar attendee address (decision D16). Recipients are found
when the mail is queued AND checked again when it is sent: a person who left the team, turned the
mail off or was suspended in between is skipped.

**What they say, per recipient.** Assembled by code from stored rows, nothing generated. Each
recipient sees only what THEY may read: the last meeting's summary and decisions only when they
can read that meeting, the carried questions and unfinished tasks only from meetings and tasks
they can see (`visibility.can_read_clause`, `tasks.visible`). A teammate who attended a private
meeting of someone else gets the agenda lines (team content) and nothing from that meeting.
Text from meetings and typed agenda lines is cut and cleaned (`titles.mail_line`: a web address
becomes "[link removed]"), put in the layout's escaped blocks, and never in a link or a subject
except the series title (cleaned to one line).

**Abuse limits (the agenda is sent by a person).** At most 10 recipients a send, 5 sends and 20
recipients per sender per day, one at a time per sender (a row lock), and a series needs
something to say. Reminders are sent by the scheduler: once per person, occurrence and kind (a
unique key), only while the team's reminders are on, and never for an occurrence that has moved,
been cancelled or started.
"""

import logging
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import mail, series, timezones, titles
from app.config import Settings
from app.mail_layout import (
    Block,
    Bullets,
    Button,
    Heading,
    Label,
    Meta,
    Para,
    Rows,
    check_url,
    render_html,
    render_text,
)
from app.mail_message import Message, clean_line
from app.mail_templates import (
    PLAIN_NAME,
    Skip,
    clip,
    due_note,
    notes_template,
)
from app.models import (
    CalendarEvent,
    Decision,
    Meeting,
    Series,
    SeriesAgendaSend,
    SeriesReminder,
    Team,
    TeamMember,
    User,
    UserSettings,
)
from app.notes_document import build_notes_document

logger = logging.getLogger(__name__)

SEND_MAX_RECIPIENTS = 10
SEND_DAILY_SENDS = 5
SEND_DAILY_RECIPIENTS = 20
MAX_LISTED = 6  # decisions, questions and agenda lines in a mail
MAX_TASK_ROWS = 5
SUMMARY_CHARS = 300
REMINDER_BATCH = 200
# The window each reminder may go in, as minutes before the start: the one-hour mail from 60 to
# 35 minutes before, the 30-minute mail from 30 to 5. A scheduler that was down misses a mail
# rather than send a stale one.
WINDOWS = {"1h": (60, 35), "30m": (30, 5)}
JOIN_HOSTS = ("meet.google.com", "zoom.us")


class AgendaRefused(series.SeriesRefused):
    pass


# ---- sending the agenda ----


def _agenda_content(session: Session, viewer: User, found: Series) -> tuple[list, list, int]:
    """(agenda lines, carried questions, unfinished tasks) the viewer may see."""
    lines = series.agenda_items(session, found)
    questions = series.carried_questions(session, viewer, found)
    unfinished = series.unfinished_tasks(session, viewer, found)
    return lines, questions, len(unfinished)


def send_agenda(session: Session, sender: User, found: Series, now: datetime) -> list:
    """Queue the agenda mail for the attendees of the last three meetings (not the sender), in the
    caller's transaction. Returns the people it is going to. SeriesRefused: 409 `no_attendees`,
    409 `nothing_to_send`, 429 `agenda_rate_limited`."""
    # One send at a time per sender, so two presses cannot both pass the daily caps.
    session.execute(select(User.id).where(User.id == sender.id).with_for_update())
    since = now - timedelta(days=1)
    sends = list(
        session.scalars(
            select(SeriesAgendaSend.recipient_count).where(
                SeriesAgendaSend.sender_user_id == sender.id, SeriesAgendaSend.created_at >= since
            )
        )
    )
    people = [a for a in series.recipients(session, found) if a.user_id != sender.id]
    people = people[:SEND_MAX_RECIPIENTS]
    if not people:
        raise AgendaRefused(
            409,
            "no_attendees",
            "There is nobody on your team to send it to yet. The agenda goes to teammates who "
            "were in the last three meetings.",
        )
    if len(sends) >= SEND_DAILY_SENDS or sum(sends) + len(people) > SEND_DAILY_RECIPIENTS:
        raise AgendaRefused(
            429,
            "agenda_rate_limited",
            "You have sent as many agendas as fit in a day. Try again tomorrow.",
        )
    lines, questions, unfinished = _agenda_content(session, sender, found)
    if not (lines or questions or unfinished):
        raise AgendaRefused(
            409, "nothing_to_send", "Add an agenda item first. There is nothing to send yet."
        )
    send = SeriesAgendaSend(
        series_id=found.id, sender_user_id=sender.id, recipient_count=len(people), created_at=now
    )
    session.add(send)
    session.flush()
    queued = []
    for person in people:
        user = session.get(User, person.user_id)
        try:
            with session.begin_nested():
                mail.enqueue(
                    session, user.email, "series_agenda", {"send_id": send.id, "user_id": user.id}
                )
            queued.append(person)
        except IntegrityError:
            continue
    send.recipient_count = len(queued)
    return queued


# ---- shared pieces of the two mails ----


def _first_name(user: User) -> str:
    name = (user.name or "").strip()
    return name.split()[0] if PLAIN_NAME.fullmatch(name) else ""


def _zone(session: Session, user: User) -> str | None:
    return session.scalar(select(UserSettings.time_zone).where(UserSettings.user_id == user.id))


def _wants(session: Session, user: User) -> bool:
    value = session.scalar(
        select(UserSettings.notify_reminders).where(UserSettings.user_id == user.id)
    )
    return value is not False


def _recipient(
    session: Session, user_id: object, to: str, found: Series | None
) -> tuple[User, Series] | Skip:
    """The person a series mail is for, if it may still go: they exist and are active, the address
    is theirs, they are still on the series' team, and they have not switched it off."""
    if found is None:
        return Skip("series_gone")
    user = session.get(User, user_id) if isinstance(user_id, int) else None
    if user is None or user.status != "active":
        return Skip("recipient_inactive")
    if to != user.email.lower():
        return Skip("recipient_changed")
    member = session.scalars(select(TeamMember).where(TeamMember.user_id == user.id)).first()
    if member is None or member.team_id != found.team_id:
        return Skip("left_team")
    if not _wants(session, user):
        return Skip("opted_out")
    return user, found


def _title(found: Series) -> str:
    return clean_line(found.title or series.UNTITLED, 80)


def _tasks_rows(session: Session, user: User, found: Series, today) -> tuple[tuple, int]:
    rows = series.unfinished_tasks(session, user, found)
    people = {
        u.id: u
        for u in session.scalars(
            select(User).where(
                User.id.in_({t.assignee_user_id for t in rows if t.assignee_user_id})
            )
        )
    }
    shown = []
    for task in rows[:MAX_TASK_ROWS]:
        assignee = people.get(task.assignee_user_id)
        who = (
            titles.clean_title(assignee.name, 60) or assignee.email.split("@")[0]
            if assignee
            else "Needs owner"
        )
        late = task.due_date is not None and task.due_date < today
        due = (
            f"Was due {task.due_date:%a} {task.due_date.day} {task.due_date:%b}"
            if late
            else due_note(task.due_date, today) or "No due date"
        )
        label = titles.mail_line(task.title, 120) or "Untitled task"
        shown.append((f"{label} · {who}", due))
    return tuple(shown), len(rows)


def _link(path: str, settings: Settings) -> str:
    return f"{settings.public_base_url.rstrip('/')}{path}"


def _send_message(
    to: str, subject: str, blocks: list[Block], footer: str, prefs: tuple[str, str]
) -> Message:
    subject = clean_line(subject, 120)
    return Message(
        to=to,
        subject=subject,
        text=render_text(blocks, footer, prefs),
        html=render_html(subject, blocks, footer, prefs),
    )


def _prefs(settings: Settings) -> tuple[str, str]:
    return ("Reminder settings", _link("/settings#reminders", settings))


# ---- the agenda mail ----


@notes_template("series_agenda")
def _series_agenda(
    session: Session, payload: dict, to: str, settings: Settings, now: datetime
) -> Message | Skip:
    send = session.get(SeriesAgendaSend, payload.get("send_id"))
    if send is None:
        return Skip("send_gone")
    found = session.get(Series, send.series_id)
    ready = _recipient(session, payload.get("user_id"), to, found)
    if isinstance(ready, Skip):
        return ready
    user, found = ready
    sender = session.get(User, send.sender_user_id) if send.sender_user_id else None
    if sender is None or sender.status != "active":
        return Skip("sender_gone")
    sender_member = session.scalars(
        select(TeamMember).where(TeamMember.user_id == sender.id)
    ).first()
    if sender_member is None or sender_member.team_id != found.team_id:
        return Skip("sender_left_team")
    lines, questions, unfinished = _agenda_content(session, user, found)
    if not (lines or questions or unfinished):
        return Skip("nothing_to_send")
    zone = _zone(session, user)
    today = timezones.localise(now, zone)[0].date()
    name = _title(found)
    sender_name = clean_line(sender.name or sender.email.split("@")[0], 60)
    items = [titles.mail_line(i.text, 200) for i in lines]
    items += [titles.mail_line(q.text, 200) for q, _meeting in questions]
    items = [i for i in items if i][: MAX_LISTED * 2]
    blocks: list[Block] = [
        Meta(f"{name} · sent by {sender_name}"),
        Heading("Agenda for the next meeting"),
    ]
    first = _first_name(user)
    blocks.append(
        Para(
            f"Hi {first}, here is what the team wants to cover."
            if first
            else "Here is what the team wants to cover."
        )
    )
    if items:
        blocks += [Label("On the agenda"), Bullets(tuple(items))]
    if unfinished:
        rows, total = _tasks_rows(session, user, found, today)
        blocks += [Label("Still open from last time"), Rows(rows)]
        if total > len(rows):
            blocks.append(Para(f"and {total - len(rows)} more in the series", muted=True))
    blocks.append(Button("View the agenda", _link(f"/series/{found.id}", settings)))
    footer = f"You received this because you attend {name}, and {sender_name} sent it."
    return _send_message(to, f"Agenda: {name}", blocks, footer, _prefs(settings))


# ---- reminders ----


def _timings(team: Team) -> tuple[str, ...]:
    return ("1h", "30m") if team.reminder_timing == "both" else (team.reminder_timing,)


def queue_due_reminders(session: Session, now: datetime) -> int:
    """Queue the reminders that are due now, once each. Teams with reminders off are not looked
    at. The occurrences come from the events the calendar sync stored; a series needs a recurring
    id to have any. Returns how many mails were queued."""
    horizon = now + timedelta(minutes=max(w[0] for w in WINDOWS.values()) + 1)
    queued = 0
    team_rows = session.scalars(select(Team).where(Team.reminders_on.is_(True))).all()
    for team in team_rows:
        member_ids = select(TeamMember.user_id).where(TeamMember.team_id == team.id)
        events = list(
            session.scalars(
                select(CalendarEvent)
                .join(Series, Series.calendar_recurring_id == CalendarEvent.recurring_event_id)
                .where(
                    Series.team_id == team.id,
                    CalendarEvent.user_id.in_(member_ids),
                    CalendarEvent.status != "cancelled",
                    CalendarEvent.all_day.is_(False),
                    CalendarEvent.starts_at > now,
                    CalendarEvent.starts_at <= horizon,
                )
                .order_by(CalendarEvent.starts_at, CalendarEvent.id)
                .limit(REMINDER_BATCH)
            )
        )
        seen: set[tuple[str, datetime]] = set()
        for event in events:
            key = (event.recurring_event_id, event.starts_at)
            if key in seen:
                continue
            seen.add(key)
            found = session.scalars(
                select(Series).where(
                    Series.team_id == team.id,
                    Series.calendar_recurring_id == event.recurring_event_id,
                )
            ).first()
            if found is None:
                continue
            for kind in _timings(team):
                opens, closes = WINDOWS[kind]
                if not (event.starts_at - timedelta(minutes=opens) <= now):
                    continue
                if not (now < event.starts_at - timedelta(minutes=closes)):
                    continue
                queued += _queue_for(session, found, event, kind)
    session.commit()
    return queued


def _queue_for(session: Session, found: Series, event: CalendarEvent, kind: str) -> int:
    """One reminder row and one mail per recipient. A series with no past meeting and no agenda
    has nothing to say and sends nothing."""
    if not series.meetings_of(session, found, None, limit=1) and not series.agenda_items(
        session, found
    ):
        return 0
    queued = 0
    for person in series.recipients(session, found):
        user = session.get(User, person.user_id)
        try:
            with session.begin_nested():
                reminder = SeriesReminder(
                    series_id=found.id,
                    user_id=user.id,
                    kind=kind,
                    starts_at=event.starts_at,
                    calendar_event_id=event.id,
                )
                session.add(reminder)
                session.flush()
                mail.enqueue(session, user.email, "series_reminder", {"reminder_id": reminder.id})
            queued += 1
        except IntegrityError:
            continue  # already queued for this person and occurrence
    return queued


def _when(starts: datetime, ends: datetime | None, zone: str | None) -> str:
    local, label = timezones.localise(starts, zone)
    text = f"{local:%a} {local.day} {local:%b}, {local:%H:%M}"
    if ends is not None and ends > starts:
        text += f" to {timezones.localise(ends, zone)[0]:%H:%M}"
    return f"{text} {label}"


def _join_url(event: CalendarEvent | None) -> str | None:
    """The event's meeting link if it is one we know how to open: https, on Meet or Zoom. Anything
    else (a link someone put in an invite) gets no button."""
    if event is None or not event.meeting_url:
        return None
    try:
        url = check_url(event.meeting_url.strip())
        parts = urlsplit(url)
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not any(
        host == allowed or host.endswith("." + allowed) for allowed in JOIN_HOSTS
    ):
        return None
    return url


def _summary_line(meeting: Meeting, now: datetime) -> str:
    doc = build_notes_document(
        title="",
        platform_label="",
        started_at=meeting.created_at,
        recorded_seconds=meeting.recorded_seconds,
        notes=meeting.notes or "",
        transcript=None,
        generated_at=now,
    )
    from app.mail_templates import _section_text

    for section in doc.sections:
        if (section.heading or "").strip().lower() == "summary":
            paragraphs, items = _section_text(section)
            text = " ".join(paragraphs or items)
            return clip(titles.mail_line(text, SUMMARY_CHARS * 2), SUMMARY_CHARS)
    return ""


@notes_template("series_reminder")
def _series_reminder(
    session: Session, payload: dict, to: str, settings: Settings, now: datetime
) -> Message | Skip:
    reminder = session.get(SeriesReminder, payload.get("reminder_id"))
    if reminder is None:
        return Skip("reminder_gone")
    found = session.get(Series, reminder.series_id)
    ready = _recipient(session, reminder.user_id, to, found)
    if isinstance(ready, Skip):
        return ready
    user, found = ready
    team = session.get(Team, found.team_id)
    if team is None or not team.reminders_on or reminder.kind not in _timings(team):
        return Skip("reminders_off")
    if now >= reminder.starts_at:
        return Skip("started")
    event = (
        session.get(CalendarEvent, reminder.calendar_event_id)
        if reminder.calendar_event_id
        else None
    )
    if event is not None and (event.status == "cancelled" or event.starts_at != reminder.starts_at):
        return Skip("moved_or_cancelled")  # the sync saw the occurrence change after we queued
    zone = _zone(session, user)
    today = timezones.localise(now, zone)[0].date()
    name = _title(found)
    one_hour = reminder.kind == "1h"
    pill = "Starts in 1 hour" if one_hour else "Starts in 30 minutes"
    platform = {"google_meet": "Google Meet", "zoom": "Zoom"}.get(found.platform or "", "")
    ends = event.ends_at if event is not None else None
    when = _when(reminder.starts_at, ends, zone)
    blocks: list[Block] = [
        Meta(pill),
        Heading(name),
        Para(f"{when} · {platform}" if platform else when, muted=True),
    ]
    buttons = []
    join = _join_url(event)
    if join:
        buttons.append(Button("Join meeting", join))
    if one_hour:
        buttons.append(Button("View agenda", _link(f"/series/{found.id}", settings)))
    blocks += buttons
    last = next(iter(series.meetings_of(session, found, user, limit=1)), None)
    lines = series.agenda_items(session, found)
    questions = series.carried_questions(session, user, found)
    rows, total = _tasks_rows(session, user, found, today)
    if one_hour:
        if last is not None:
            local_day = timezones.localise(last.created_at, zone)[0]
            blocks.append(Label(f"Last time, {local_day:%a} {local_day.day} {local_day:%b}"))
            summary = _summary_line(last, now)
            if summary:
                blocks.append(Para(summary))
            decisions = [
                titles.mail_line(d.text, 200)
                for d in session.scalars(
                    select(Decision)
                    .where(Decision.meeting_id == last.id, Decision.hidden_at.is_(None))
                    .order_by(Decision.position)
                    .limit(MAX_LISTED)
                )
            ]
            decisions = [d for d in decisions if d]
            if decisions:
                blocks += [Label("Decisions made"), Bullets(tuple(decisions))]
        shown = [titles.mail_line(q.text, 200) for q, _m in questions[:MAX_LISTED]]
        shown = [q for q in shown if q]
        if shown:
            blocks += [Label("Open questions to settle"), Bullets(tuple(shown))]
        if lines:
            blocks += [
                Label("Also on the agenda"),
                Bullets(tuple(titles.mail_line(i.text, 200) for i in lines[:MAX_LISTED])),
            ]
        if rows:
            blocks += [Label("Still open from last time"), Rows(rows)]
            if total > len(rows):
                blocks.append(Para(f"and {total - len(rows)} more in the series", muted=True))
    else:
        agenda = [titles.mail_line(q.text, 200) for q, _m in questions[:MAX_LISTED]]
        agenda += [titles.mail_line(i.text, 200) for i in lines[:MAX_LISTED]]
        if total:
            agenda.append(f"Check on {total} unfinished task{'s' if total != 1 else ''}.")
        agenda = [a for a in agenda if a]
        if agenda:
            blocks += [Label("On the agenda"), Bullets(tuple(agenda))]
    if len(blocks) <= 3 + len(buttons):
        return Skip("nothing_to_say")
    footer = f"You are getting this because you attend {name}."
    return _send_message(to, f"{pill}: {name}", blocks, footer, _prefs(settings))
