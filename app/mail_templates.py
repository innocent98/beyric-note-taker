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
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import timezones, titles
from app.config import Settings
from app.mail_layout import (
    Block,
    Bullets,
    Button,
    Heading,
    Label,
    Meta,
    Para,
    render_html,
    render_text,
)
from app.mail_message import Message, clean_line
from app.models import Meeting, User, UserSettings
from app.notes_document import ListBlock, Paragraph, Section, build_notes_document

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
    blocks: list[Block] = [
        Heading(heading),
        Para(_with_greeting(payload, body)),
        Button(button, payload["link"]),
        Para(f"{note}", muted=True),
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
        "Confirm this is your email to finish creating your Beyric Note Taker account.",
        "Confirm email",
        "The link works once and expires in 24 hours. If you did not create an account, "
        "you can ignore this email.",
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
        "The link works once and expires in 30 minutes. If you did not ask for this, ignore "
        "this email: your password has not changed.",
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


LINK_TEMPLATES: dict[str, Callable[[dict, str, Settings], Message]] = {
    "alert": _alert,
    "verify_email": _verify,
    "reset_password": _reset,
    "already_registered": _already,
    "waitlist_invite": _invite,
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


def abridged_blocks(sections: list[Section]) -> list[Block]:
    """Blocks for the sections a mail carries. A notes template written by the owner may use
    other headings: then the first section stands in, so a mail is never empty of notes."""
    wanted = [
        s for s in sections if s.heading and clean_line(s.heading, 80).lower() in MAIL_SECTIONS
    ]
    if not wanted:
        wanted = [s for s in sections if _section_text(s) != ([], [])][:1]
    blocks: list[Block] = []
    for section in wanted:
        paragraphs, items = _section_text(section)
        heading = clean_line(section.heading or "Notes", MAX_HEADING_CHARS)
        blocks.append(Label(heading))
        if items:
            shown = tuple(clip(i, MAX_ITEM_CHARS) for i in items[:MAX_ITEMS])
            if len(items) > MAX_ITEMS:
                shown += (f"and {len(items) - MAX_ITEMS} more in the full notes",)
            blocks.append(Bullets(shown))
        elif paragraphs:
            blocks.append(Para(clip(" ".join(paragraphs), MAX_PARAGRAPH_CHARS)))
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
