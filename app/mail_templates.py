"""What each mail says, in the designer's layout (`app/mail_layout.py`).

Two kinds of template, which is also how the sender treats them:

- **Link templates** (sign-up, verification, reset, waitlist invite): the payload holds the link,
  because the link carries a raw token. These are account mail: not counted against the daily
  caps, not stopped by an unsubscribe (only by a bounce or a complaint), and scrubbed from the
  outbox within the hour (`app/mail.py`).
- **Notes templates** (the notes-ready mail here; the share, tasks and reminder mails of later
  phases): the payload holds **ids only** (`{"meeting_id": 7}`), checked by `enqueue`. The text
  is read from the database when the mail is SENT, so notes and transcripts are never copied
  into `mail_outbox`, and a meeting deleted or an owner leaving between queueing and sending
  stops the mail instead of mailing what should be gone. A builder returns a `Message` or a
  `Skip` (a reason slug the row keeps; never a reason that carries text from the meeting).

A later phase adds a notes template with `@notes_template("notes_shared")` and the guards
below (`ready_meeting`), not its own copy of the checks.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import export, timezones, titles, tokens
from app.config import Settings
from app.mail_layout import (
    Block,
    Bullets,
    Button,
    Divider,
    Heading,
    Label,
    Meta,
    Para,
    Quote,
    Rows,
    render_html,
    render_text,
)
from app.mail_message import Attachment, Message, clean_line
from app.models import (
    Meeting,
    MeetingShare,
    MeetingShareRecipient,
    MeetingTranslation,
    Task,
    TeamMember,
    User,
    UserSettings,
)
from app.notes_document import (
    ListBlock,
    Paragraph,
    Section,
    build_notes_document,
    export_filename,
)

SIGNATURE = "Beyric Note Taker, a product of Beyrichq LTD"


@dataclass(frozen=True)
class Skip:
    """The mail should not go, and that is not an error. `reason` is a short slug."""

    reason: str


# ---- link templates ----

# A name goes into the greeting only when it is plainly a name: letters, then letters, spaces,
# apostrophes and hyphens, at most 60. The waitlist form takes a name from anyone, and mail goes
# to whatever address they typed, so a name is never allowed to carry a line break, a link or
# any text of its own into an email from us.
PLAIN_NAME = re.compile(r"[^\W\d_](?:[^\W\d_]|[ '’-]){0,59}")


def _with_greeting(payload: dict, sentence: str) -> str:
    """`sentence` after "Hi <name>, " when the payload's name is plainly a name."""
    name = payload.get("name")
    if isinstance(name, str) and PLAIN_NAME.fullmatch(name.strip()):
        return f"Hi {name.strip()}, {sentence[:1].lower()}{sentence[1:]}"
    return sentence


def _link_mail(
    subject: str,
    heading: str,
    body: str,
    button: str,
    note: str,
    payload: dict,
    to: str,
) -> Message:
    # Spacing as drawn: 10 under the heading, 24 under the sentence, and the note 24 under the
    # button (the button's own 4 px margin collapses into it, so the note carries all 24).
    blocks: list[Block] = [
        Heading(heading, after=10),
        Para(_with_greeting(payload, body), after=24),
        Button(button, payload["link"]),
        Para(f"{note}", muted=True, before=24, after=0),
    ]
    return Message(
        to=to,
        subject=subject,
        text=render_text(blocks, SIGNATURE),
        html=render_html(subject, blocks, SIGNATURE),
    )


def _verify(payload: dict, to: str, settings: Settings) -> Message:
    return _link_mail(
        "Confirm your email for Beyric Note Taker",
        "Confirm your email address",
        "Confirm this is your email to finish setting up your Beyric Note Taker account.",
        "Confirm email",
        "This link expires in 24 hours. If you did not create an account, you can ignore this "
        "email.",
        payload,
        to,
    )


def _reset(payload: dict, to: str, settings: Settings) -> Message:
    return _link_mail(
        "Reset your Beyric Note Taker password",
        "Reset your password",
        f"We received a request to reset the password for {to}. Use the button below to choose "
        "a new one.",
        "Choose a new password",
        "This link expires in 30 minutes. If you did not ask for this, your password will stay "
        "the same.",
        payload,
        to,
    )


def _already(payload: dict, to: str, settings: Settings) -> Message:
    return _link_mail(
        "You already have a Beyric Note Taker account",
        "You already have an account",
        "Someone, probably you, tried to create an account with this email address, but it "
        "already has one. Sign in with the button below, or use Forgot password on the sign-in "
        "page if you do not remember it.",
        "Sign in",
        "If this was not you, you can ignore this email.",
        payload,
        to,
    )


def _invite(payload: dict, to: str, settings: Settings) -> Message:
    return _link_mail(
        "A place is waiting for you on Beyric Note Taker",
        "A place is waiting for you",
        "A place has opened up in the Beyric Note Taker pilot and it is reserved for this "
        "email address for 7 days. Use the button below to create your account.",
        "Create your account",
        "If you were not expecting this, you can ignore this email.",
        payload,
        to,
    )


def _team_invite(payload: dict, to: str, settings: Settings) -> Message:
    """The designer's "Team invite" (`Emails.dc.html`): a heading naming the inviter, one sentence,
    one button, the expiry line. The inviter's name is used only when it is plainly a name (it
    goes in the subject); otherwise the mail says "a teammate". Account-style mail: the payload
    holds the link, which carries a raw token, so it is scrubbed once sent."""
    inviter = payload.get("inviter")
    plain = isinstance(inviter, str) and PLAIN_NAME.fullmatch(inviter.strip())
    heading = (
        f"{inviter.strip()} invited you to join their team"
        if plain
        else "You have been invited to join a team"
    )
    blocks: list[Block] = [
        Heading(heading),
        Para(
            "Join the team on Beyric Note Taker to see shared meeting notes and the tasks "
            "assigned to you after each call."
        ),
        Button("Accept invite", payload["link"]),
        Para(
            "This invite expires in 7 days. If you were not expecting it, you can ignore this "
            "email.",
            muted=True,
        ),
    ]
    return Message(
        to=to,
        subject=heading,
        text=render_text(blocks, SIGNATURE),
        html=render_html(heading, blocks, SIGNATURE),
    )


def _alert(payload: dict, to: str, settings: Settings) -> Message:
    """An alert from `app/alerts.py`: the words the app wrote, no link. The text is in the payload,
    so it is account-style mail (scrubbed once sent, failed and scrubbed after an hour unsent: an
    alert that old is no use). Every string is escaped by the layout and the subject is one line."""
    title = payload["title"]
    body = payload["body"]
    if not isinstance(title, str) or not isinstance(body, str):
        raise ValueError("an alert's title and body are text")
    subject = clean_line(title, 150) or "Beyric Note Taker alert"
    lines = [clean_line(line, 600) for line in body.splitlines()][:20]
    blocks: list[Block] = [
        Heading(subject),
        *(Para(line) for line in lines if line),
        Para("This is an automatic message from Beyric Note Taker.", muted=True),
    ]
    return Message(
        to=to,
        subject=subject,
        text=render_text(blocks, SIGNATURE),
        html=render_html(subject, blocks, SIGNATURE),
    )


NOT_JOINED = "meeting_not_joined"
# The page the designer's "Send it again" button opens: the Meetings page ("Send it again from the
# Meetings page", `Beyric Note Taker.dc.html`), which is the app's home, `/` (there is no
# `/meetings` page; a visitor who is not signed in sees the landing page and its Log in link). A
# failed meeting has its Send it again there, and a meeting the scheduler never got into has its
# Send now (C15b). A plain link into the signed-in app: no token, no query, and nothing happens by
# opening it (a mail scanner or a forwarded copy cannot send a bot). None draws no button.
SEND_AGAIN_PATH: str | None = "/"
# The designer's words, shared with the bell entry that goes with the mail (`app/pipeline.py`).
NOT_JOINED_TITLE = "The note taker could not join your meeting"
NOT_JOINED_REASON = (
    "This usually happens when the host does not let the note taker in from the waiting room."
)


def not_joined_payload(
    session: Session, user_id: int, platform: str, native_id: str, at: datetime
) -> dict:
    """What `_not_joined` reads: the meeting as the alerts name it ("Google Meet btp-tkio-gtb",
    "Zoom 88205679674", "Microsoft Teams ...": platform and code only) and the moment the note
    taker was refused, written in the owner's stored time zone ("11:58 GMT+1"; UTC when none is
    stored or the stored name is not one the server knows). The caller passes the moment the news
    arrived, never a later deadline. It reads the database, so a caller builds it inside the guard
    that keeps a mail problem away from what it is really doing."""
    zone = session.scalar(select(UserSettings.time_zone).where(UserSettings.user_id == user_id))
    local, label = timezones.localise(at, zone)
    return {
        "meeting": titles.alert_name(None, platform, native_id),
        "at": f"{local:%H:%M} {label}",
    }


# Templates whose payload is their own fields, not the alert's title and body: queued without
# that payload (a caller bug) they would fail as unknown at send time, so `alerts` sends the plain
# alert instead and logs it.
OWN_PAYLOAD_TEMPLATES = frozenset({NOT_JOINED})


def _not_joined(payload: dict, to: str, settings: Settings) -> Message:
    """The designer's "Could not join" (`Emails.dc.html:137-149`): sent when a bot was sent to a
    meeting and not admitted. Account-style (the payload is text, scrubbed once sent). Queued by
    `app/pipeline.py` (`_tell_not_admitted`) through `alerts.notify_user`, which has already
    checked the account's email switch and the suppression list, so it follows the same opt-out
    as the alert it replaces; the footer's Email preferences link goes to the switch."""
    meeting, at = payload["meeting"], payload["at"]
    if not isinstance(meeting, str) or not isinstance(at, str):
        raise ValueError("a not-joined mail's meeting and time are text")
    meeting, at = clean_line(meeting, 120), clean_line(at, 40)
    base = settings.public_base_url.rstrip("/")
    subject = "Meeting not recorded"  # the bell's title, as the alert mail it replaces had
    blocks: list[Block] = [
        Heading(NOT_JOINED_TITLE, after=10),
        Para(
            f"We tried to join {meeting} at {at} but were not admitted. {NOT_JOINED_REASON}",
            after=16,
            strong=meeting,
        ),
        # 24 above the button as drawn; with no button the card's own padding closes it
        Para(
            "If the meeting is still running, you can send it again.",
            after=24 if SEND_AGAIN_PATH is not None else 0,
        ),
    ]
    if SEND_AGAIN_PATH is not None:
        blocks.append(Button("Send it again", f"{base}{SEND_AGAIN_PATH}"))
    footer = "You received this because you sent the note taker to this meeting."
    prefs = ("Email preferences", f"{base}/settings#email-preferences")
    return Message(
        to=to,
        subject=subject,
        text=render_text(blocks, footer, prefs),
        html=render_html(subject, blocks, footer, prefs),
    )


CALENDAR_DISCONNECTED = "calendar_disconnected"


def _calendar_disconnected(payload: dict, to: str, settings: Settings) -> Message:
    """LEAD-DESIGNED (2026-11-05), not in the designer's `Emails.dc.html`: built from its "Could not
    join" mail (heading, two paragraphs, one button, the footer with Email preferences) and the
    words the Settings card already uses. Queued by `app/calendar_states.py` once per disconnect
    (and only for `needs_reconnect`, never `degraded`), after the account's own email switch and the
    suppression list were checked. Only the payload's `provider` is read (the footer names the
    calendar); nothing else in it is shown."""
    base = settings.public_base_url.rstrip("/")
    subject = "Your calendar is disconnected"
    blocks: list[Block] = [
        Heading(subject),
        Para(
            "Beyric can no longer read your calendar, so the note taker has stopped "
            "joining meetings from it."
        ),
        Para("Connect again and it carries on with your upcoming meetings."),
        Button("Reconnect", f"{base}/settings"),
    ]
    calendar = "Microsoft Outlook" if payload.get("provider") == "microsoft" else "Google Calendar"
    footer = f"You received this because your {calendar} is connected to Beyric Note Taker."
    prefs = ("Email preferences", f"{base}/settings#email-preferences")
    return Message(
        to=to,
        subject=subject,
        text=render_text(blocks, footer, prefs),
        html=render_html(subject, blocks, footer, prefs),
    )


LINK_TEMPLATES: dict[str, Callable[[dict, str, Settings], Message]] = {
    "alert": _alert,
    NOT_JOINED: _not_joined,
    CALENDAR_DISCONNECTED: _calendar_disconnected,
    "verify_email": _verify,
    "reset_password": _reset,
    "already_registered": _already,
    "waitlist_invite": _invite,
    "team_invite": _team_invite,
}


def render_link(template: str, payload: dict, to: str, settings: Settings) -> Message:
    """The mail for a link template. Raises KeyError for an unknown template or a payload
    without its link."""
    return LINK_TEMPLATES[template](payload, to, settings)


# ---- notes templates (ids only in the payload) ----

NotesBuilder = Callable[[Session, dict, str, Settings, datetime], Message | Skip]
NOTES_TEMPLATES: dict[str, NotesBuilder] = {}


def notes_template(name: str) -> Callable[[NotesBuilder], NotesBuilder]:
    def register(builder: NotesBuilder) -> NotesBuilder:
        NOTES_TEMPLATES[name] = builder
        return builder

    return register


def check_id_payload(payload: dict) -> None:
    """ValueError unless every value is an integer id: a notes template never carries text."""
    for key, value in payload.items():
        if not isinstance(key, str) or isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("a notes mail payload holds ids only")


def prepare(
    session: Session, template: str, payload: dict, to: str, settings: Settings, now: datetime
) -> Message | Skip:
    """The mail for one outbox row, or a `Skip`. KeyError: unknown template (or a link payload
    without its link). ValueError: a message that could not be built safely."""
    if template in NOTES_TEMPLATES:
        return NOTES_TEMPLATES[template](session, payload, to, settings, now)
    return render_link(template, payload, to, settings)


def ready_meeting(session: Session, meeting_id: object) -> tuple[Meeting, User] | Skip:
    """The meeting and its owner, if the notes may still be mailed: the meeting exists, is ready
    with notes, and its owner is active (not suspended, not being deleted)."""
    if isinstance(meeting_id, bool) or not isinstance(meeting_id, int):
        return Skip("meeting_gone")
    meeting = session.get(Meeting, meeting_id)
    if meeting is None:
        return Skip("meeting_gone")
    owner = session.get(User, meeting.owner_user_id)
    if owner is None or owner.status != "active":
        return Skip("owner_inactive")
    if meeting.status != "ready" or not meeting.notes:
        return Skip("not_ready")
    return meeting, owner


def _platform_label(meeting: Meeting) -> str:
    return "Zoom" if meeting.platform == "zoom" else "Google Meet"


def meeting_name(meeting: Meeting) -> str:
    """The calendar title, else "Zoom 88205679674": the name `services.meeting_title` gives,
    cleaned for a mail (short, no web address in it, one line), without importing services
    (services imports the pipeline, which queues this mail)."""
    return clean_line(titles.mail_title(meeting), 80)


def owner_time_zone(session: Session, owner: User) -> str | None:
    return session.scalar(select(UserSettings.time_zone).where(UserSettings.user_id == owner.id))


def when_line(meeting: Meeting, time_zone: str | None = None) -> str:
    """ "05 Nov 2026, 14:30 GMT+1": the meeting's start in the owner's zone, labelled with it
    ("UTC" while the owner has not chosen one)."""
    when, label = timezones.localise(meeting.created_at, time_zone)
    return f"{when.day:02d} {when:%b %Y, %H:%M} {label}"


# ---- abridged notes: the part of the notes a mail carries ----

# Spec section 10, ambiguity 10: mails carry these, abridged; downloads and attachments carry all.
MAIL_SECTIONS = ("summary", "decisions", "action items", "open questions")
MAX_PARAGRAPH_CHARS = 600
MAX_ITEMS = 6
MAX_ITEM_CHARS = 200
MAX_HEADING_CHARS = 60


def clip(text: str, limit: int) -> str:
    """`text` cut to `limit` characters at the end of a sentence if one falls in the second half
    of the allowance, else at a word, with an ellipsis."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    ends = [cut.rfind(mark) for mark in (". ", "! ", "? ")]
    best = max(ends)
    if best >= limit // 2:
        return cut[: best + 1]
    return cut[: cut.rfind(" ") if " " in cut else limit].rstrip(" ,;:") + "…"


def _runs_text(runs) -> str:
    return "".join(run.text for run in runs)


def _section_text(section: Section) -> tuple[list[str], list[str]]:
    """The paragraphs and the list items of a section, as plain text."""
    paragraphs: list[str] = []
    items: list[str] = []
    for block in section.blocks:
        if isinstance(block, Paragraph):
            paragraphs.append(_runs_text(block.runs))
        elif isinstance(block, ListBlock):
            for item in block.items:
                text = " ".join(
                    _runs_text(inner.runs) for inner in item if isinstance(inner, Paragraph)
                )
                if text.strip():
                    items.append(text)
    return [p for p in paragraphs if p.strip()], items


def abridged_blocks(sections: list[Section], names: tuple[str, ...] = MAIL_SECTIONS) -> list[Block]:
    """Blocks for the sections a mail carries (`names`: the headings, lower-cased, in the notes'
    language). A notes template written by the owner may use other headings: then the first
    section stands in, so a mail is never empty of notes."""
    wanted = [s for s in sections if s.heading and clean_line(s.heading, 80).lower() in names]
    if not wanted:
        wanted = [s for s in sections if _section_text(s) != ([], [])][:1]
    blocks: list[Block] = []
    for section in wanted:
        paragraphs, items = _section_text(section)
        heading = clean_line(section.heading or "Notes", MAX_HEADING_CHARS)
        blocks.append(Label(heading))
        if items:
            shown = tuple(clip(titles.mail_line(i, 400), MAX_ITEM_CHARS) for i in items[:MAX_ITEMS])
            if len(items) > MAX_ITEMS:
                shown += (f"and {len(items) - MAX_ITEMS} more in the full notes",)
            blocks.append(Bullets(shown))
        elif paragraphs:
            joined = titles.mail_line(" ".join(paragraphs), MAX_PARAGRAPH_CHARS * 2)
            blocks.append(Para(clip(joined, MAX_PARAGRAPH_CHARS)))
        else:
            blocks.append(Para("None noted.", muted=True))
    return blocks


# ---- "Notes ready" ----

# `pipeline.REMOVAL_REASONS` (a test keeps the two equal): Vexa says the bot was removed or ran
# out of time, so the notes may stop before the meeting did.
LEFT_EARLY_REASONS = frozenset({"evicted", "max_bot_time_exceeded"})


def notes_ready_wanted(session: Session, owner: User) -> bool:
    """The owner's setting: `user_settings.notify_email`, on when the user has no row yet."""
    value = session.scalar(
        select(UserSettings.notify_email).where(UserSettings.user_id == owner.id)
    )
    return True if value is None else bool(value)


@notes_template("notes_ready")
def _notes_ready(
    session: Session, payload: dict, to: str, settings: Settings, now: datetime
) -> Message | Skip:
    found = ready_meeting(session, payload["meeting_id"])
    if isinstance(found, Skip):
        return found
    meeting, owner = found
    if to != owner.email.lower():
        return Skip("recipient_changed")  # the address on the row is not the owner's any more
    if not notes_ready_wanted(session, owner):
        return Skip("opted_out")
    doc = build_notes_document(
        title=meeting_name(meeting),
        platform_label=_platform_label(meeting),
        started_at=meeting.created_at,
        recorded_seconds=meeting.recorded_seconds,
        notes=meeting.notes or "",
        transcript=None,
        generated_at=now,
    )
    name = meeting_name(meeting)
    blocks: list[Block] = [
        Meta(f"{name} · {when_line(meeting, owner_time_zone(session, owner))}"),
        Heading("Your meeting notes are ready"),
        *abridged_blocks(doc.sections),
        Button("View full notes", f"{settings.public_base_url.rstrip('/')}/meetings/{meeting.id}"),
    ]
    if meeting.completion_reason in LEFT_EARLY_REASONS:
        blocks.append(
            Para("The note taker left early, so these notes may be incomplete.", muted=True)
        )
    footer = "You received this because you sent the note taker to this meeting."
    # The same link in every mail: no person, token or meeting in it (the page needs sign-in).
    prefs = (
        "Email preferences",
        f"{settings.public_base_url.rstrip('/')}/settings#email-preferences",
    )
    subject = clean_line(f"Your notes are ready: {name}", 120)
    return Message(
        to=to,
        subject=subject,
        text=render_text(blocks, footer, prefs),
        html=render_html(subject, blocks, footer, prefs),
    )


# ---- "New tasks assigned" ----

MAX_TASKS_IN_MAIL = 10
MAX_TASK_CHARS = 120


def due_note(due: date | None, today: date) -> str:
    """ "Due today", "Due tomorrow", "Due Fri 6 Nov", or nothing when there is no date."""
    if due is None:
        return ""
    if due == today:
        return "Due today"
    if (due - today).days == 1:
        return "Due tomorrow"
    return f"Due {due:%a} {due.day} {due:%b}"


@notes_template("tasks_assigned")
def _tasks_assigned(
    session: Session, payload: dict, to: str, settings: Settings, now: datetime
) -> Message | Skip:
    found = ready_meeting(session, payload["meeting_id"])
    if isinstance(found, Skip):
        return found
    meeting, owner = found
    recipient_id = payload.get("user_id")
    recipient = session.get(User, recipient_id) if isinstance(recipient_id, int) else None
    if recipient is None or recipient.status != "active":
        return Skip("recipient_inactive")
    if to != recipient.email.lower():
        return Skip("recipient_changed")
    if recipient.id == owner.id:
        return Skip("own_tasks")  # nobody is told about the tasks of their own meeting
    owner_settings = session.scalar(
        select(UserSettings.email_assignees).where(UserSettings.user_id == owner.id)
    )
    if owner_settings is False:
        return Skip("assignees_not_emailed")
    if not notes_ready_wanted(session, recipient):  # their email preference
        return Skip("opted_out")
    team_id = session.scalar(select(TeamMember.team_id).where(TeamMember.user_id == recipient.id))
    tasks = list(
        session.scalars(
            select(Task)
            .where(
                Task.meeting_id == meeting.id,
                Task.team_id == team_id,
                Task.assignee_user_id == recipient.id,
                Task.source == "ai",
                Task.deleted_at.is_(None),
                Task.suggested.is_(False),  # a suggestion is mailed when it is accepted
                Task.status == "todo",
                Task.is_new.is_(True),  # backfilled (quiet) tasks are never New
            )
            .order_by(Task.position, Task.id)
        )
    )
    if not tasks:
        return Skip("no_tasks")
    zone = owner_time_zone(session, owner)
    meeting_day = timezones.localise(meeting.created_at, zone)[0].date()
    today = timezones.localise(now, zone)[0].date()
    shared = meeting.shared_team_id is not None and meeting.shared_team_id == team_id
    owner_name = clean_line(owner.name or owner.email.split("@")[0], 60)
    # An assignee of a private meeting's task is not told its title (spec 8.2), only whose it was.
    name = meeting_name(meeting) if shared else f"A meeting of {owner_name}"
    by = f" · recorded by {owner_name}" if shared else ""
    count = len(tasks)
    shown = tasks[:MAX_TASKS_IN_MAIL]
    rows = tuple(
        (titles.mail_line(t.title, MAX_TASK_CHARS) or "Untitled task", due_note(t.due_date, today))
        for t in shown
    )
    base = settings.public_base_url.rstrip("/")
    heading = "You have 1 new task" if count == 1 else f"You have {count} new tasks"
    when = "today's meeting" if meeting_day == today else "the meeting"
    noun, verb = ("this action item", "was") if count == 1 else ("these action items", "were")
    full_name = (recipient.name or "").strip()
    first = full_name.split()[0] if PLAIN_NAME.fullmatch(full_name) else ""
    lead = f"Hi {first}, {noun}" if first else noun.capitalize()
    intro = f"{lead} from {when} {verb} assigned to you."
    blocks: list[Block] = [
        Meta(f"{name}{by} · {meeting_day.day:02d} {meeting_day:%b %Y}"),
        Heading(heading),
        Para(intro),
        Rows(rows),
    ]
    if count > len(shown):
        blocks.append(Para(f"and {count - len(shown)} more in your tasks", muted=True))
    blocks += [
        Button("Open my tasks", f"{base}/tasks"),
        Para("Not yours? Open the task to reassign it.", muted=True),
    ]
    footer = "You received this because you are on the Beyric team."
    prefs = ("Email preferences", f"{base}/settings#email-preferences")
    subject = clean_line(f"{heading}: {name}", 120)
    return Message(
        to=to,
        subject=subject,
        text=render_text(blocks, footer, prefs),
        html=render_html(subject, blocks, footer, prefs),
    )


# ---- "Notes shared with you" (W6, spec section 10) ----

OPT_OUT_TTL = timedelta(days=180)
SHARE_ATTACHMENT_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def _mail_section_names(language: str) -> tuple[str, ...]:
    """The four sections a mail carries, as they are headed in `language`, lower-cased."""
    from app import translation  # translation imports services, which imports this module

    table = translation.HEADINGS[language]
    return tuple(
        table[h].lower() for h in ("Summary", "Decisions", "Action items", "Open questions")
    )


def _share_attachments(
    share: MeetingShare, meeting: Meeting, notes: str, words: str, now: datetime
) -> tuple[Attachment, ...]:
    """The files of a share, built now from the same text the mail shows: the notes as PDF or
    Word (with the transcript inside when ticked), or, with no notes file, the transcript alone
    as plain text. Nothing is stored."""
    title = meeting_name(meeting)
    with_transcript = share.include_transcript and bool(words.strip())
    if share.attach in SHARE_ATTACHMENT_TYPES:
        doc = build_notes_document(
            title=title,
            platform_label=_platform_label(meeting),
            started_at=meeting.created_at,
            recorded_seconds=meeting.recorded_seconds,
            notes=notes,
            transcript=words if with_transcript else None,
            generated_at=now,
        )
        content = export.build_file(doc, share.attach)
        return (
            Attachment(
                export_filename(title, share.attach), content, SHARE_ATTACHMENT_TYPES[share.attach]
            ),
        )
    if with_transcript:
        return (
            Attachment(export_filename(f"{title} transcript", "txt"), words.encode(), "text/plain"),
        )
    return ()


@notes_template("notes_shared")
def _notes_shared(
    session: Session, payload: dict, to: str, settings: Settings, now: datetime
) -> Message | Skip:
    """The mail of one recipient of one share. Everything is checked again here, at SEND time: the
    share and the recipient exist and the share is live, the meeting is ready, the sender is still
    an active, confirmed account, and a translated share still has a current translation. The
    notes are read now; the outbox row holds three ids. Outside recipients get no link to the
    notes (D7); a teammate who may read the meeting gets one to the signed-in page."""
    from app import translation  # translation imports services, which imports this module

    share = session.get(MeetingShare, payload["share_id"])
    recipient = session.get(MeetingShareRecipient, payload["recipient_id"])
    if share is None or recipient is None or recipient.share_id != share.id:
        return Skip("share_gone")
    if share.revoked_at is not None or share.status != "queued":
        return Skip("share_revoked")
    if to != recipient.email:
        return Skip("recipient_changed")
    found = ready_meeting(session, share.meeting_id)
    if isinstance(found, Skip):
        return found
    meeting, owner = found
    if owner.id != share.sender_user_id:
        return Skip("sender_changed")
    if owner.email_verified_at is None:
        return Skip("sender_unverified")
    language = share.language
    original = meeting.notes or ""
    words_original = meeting.speaker_transcript or meeting.transcript or ""
    notes, words = original, words_original
    if language != "en":
        row = session.scalars(
            select(MeetingTranslation).where(
                MeetingTranslation.meeting_id == meeting.id,
                MeetingTranslation.language == language,
            )
        ).first()
        if row is None or row.status != "ready" or translation.is_stale(row, meeting):
            return Skip("translation_missing")
        notes, words = row.notes_md or "", row.transcript or ""
    name = meeting_name(meeting)
    sender_name = titles.mail_line(owner.name or owner.email.split("@")[0], 60)
    sender_first = sender_name.split()[0] if PLAIN_NAME.fullmatch(sender_name) else ""
    zone = owner_time_zone(session, owner)
    meta = f"{name} · {when_line(meeting, zone)}"
    if meeting.recorded_seconds:
        meta += f" · {max(1, round(meeting.recorded_seconds / 60))} min"
    heading = (
        f"{sender_first} shared meeting notes with you"
        if sender_first
        else "Meeting notes were shared with you"
    )
    doc = build_notes_document(
        title=name,
        platform_label=_platform_label(meeting),
        started_at=meeting.created_at,
        recorded_seconds=meeting.recorded_seconds,
        notes=notes,
        transcript=None,
        generated_at=now,
    )
    # As drawn: the heading first (6 px under it), then the meeting's line with 20 under it.
    blocks: list[Block] = [Heading(heading, after=6), Meta(meta, after=20)]
    if share.message:
        blocks.append(Quote(share.message))
    blocks += abridged_blocks(doc.sections, _mail_section_names(language))
    if language != "en":
        # Spec section 2, row 11: no link to the original, so the original follows the translation.
        original_doc = build_notes_document(
            title=name,
            platform_label=_platform_label(meeting),
            started_at=meeting.created_at,
            recorded_seconds=meeting.recorded_seconds,
            notes=original,
            transcript=None,
            generated_at=now,
        )
        blocks += [
            Divider(),
            Label("Original notes (English)"),
            *abridged_blocks(original_doc.sections),
        ]
    base = settings.public_base_url.rstrip("/")
    member = session.get(User, recipient.user_id) if recipient.user_id else None
    member_team = (
        session.scalar(select(TeamMember.team_id).where(TeamMember.user_id == member.id))
        if member is not None and member.status == "active"
        else None
    )
    if member_team is not None and meeting.shared_team_id == member_team:
        blocks.append(Button("Open notes", f"{base}/meetings/{meeting.id}"))
    attachments = _share_attachments(share, meeting, notes, words, now)
    if attachments:
        has_transcript = share.include_transcript and bool(words.strip())
        blocks.append(
            Para(
                "The full transcript is attached." if has_transcript else "The notes are attached.",
                muted=True,
            )
        )
    raw = tokens.issue(
        session,
        purpose="mail_optout",
        email=to,
        user_id=None,
        now=now,
        ttl=OPT_OUT_TTL,
    )
    footer = f"Sent by {owner.email} using Beyric Note Taker."  # as drawn: the address only
    opt_out = (
        "Don't want these? Stop mail from Beyric Note Taker",
        f"{base}/unsubscribe?token={raw}",
    )
    subject = clean_line(f"{sender_name} shared meeting notes: {name}", 120)
    return Message(
        to=to,
        subject=subject,
        text=render_text(blocks, footer, opt_out),
        html=render_html(subject, blocks, footer, opt_out),
        attachments=attachments,
        reply_to=owner.email,
    )


# The agenda and reminder mails of recurring meetings register themselves here (W8). Last, because
# that module imports the helpers above.
from app import series_mail  # noqa: E402,F401
