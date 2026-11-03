"""Owner actions shared by the Jinja pages (`app/web.py`) and the JSON API (`app/api.py`).

Each action does the work and commits, or raises `Refused` with the HTTP status, a stable code
and the message the owner reads. The callers only decide how to show that: a page with a notice,
or a JSON error. Keeping the rules here means the two can never disagree.
"""

import logging
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, Request
from markdown_it import MarkdownIt
from markupsafe import Markup
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.convertors import Convertor, register_url_convertor

from app import auth, edge
from app.models import AppState, Meeting, Template, VocabularyTerm
from app.notes import MAX_TERM_CHARS, NotesFailed, generate_notes
from app.pipeline import REASON_CANCELLED, REASON_NO_SPEECH
from app.vexa import UnsupportedMeetingUrl, VexaClient, VexaError, parse_meeting_url

logger = logging.getLogger(__name__)

MAX_URL_CHARS = 2000
MAX_ID_DIGITS = 10
MAX_ID = 2**31 - 1  # ids are Postgres integers; anything larger cannot exist
MAX_TEMPLATE_NAME_CHARS = 100
MAX_INSTRUCTION_CHARS = 4000
BAD_TERM_CHARS = ("<", ">", "\r", "\n")  # the same characters app/transcribe.py drops
ACTIVE = ("bot_sent", "processing")
# One worker pass runs a whole recording through transcription and notes, and the heartbeat is
# only stamped between passes, so an hour-long meeting can keep it quiet for several minutes.
WORKER_STALE_AFTER = timedelta(minutes=20)
NEW_MEETING_LOCK = 6001  # serialises "is a bot active?" + "send a bot" across requests

STEP_TEXT = {
    "waiting": "Getting started",
    "audio": "Downloading the audio",
    "transcribing": "Writing the transcript",
    "speakers": "Working out who said what",
    "notes": "Writing the notes",
}
STATUS_TONES = {
    "ready": "success",
    "failed": "danger",
    "processing": "info",
    "bot_sent": "warning",
}


class Refused(Exception):
    """An action the owner asked for that cannot go ahead. `message` is safe to show as is."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


# ---- dependencies the tests replace ----


def get_vexa() -> VexaClient:
    return VexaClient()


def get_openai_client():
    return None  # generate_notes builds its own client from settings


# ---- login ----


OWNER_ACCOUNT = "owner"  # the login form has no username yet; accounts will pass the email


def log_in(request: Request, session: Session, password: str, account: str = OWNER_ACCOUNT) -> None:
    """Check the password and give the request a fresh logged-in session."""
    settings = request.app.state.settings
    limiter: auth.LoginLimiter = request.app.state.limiter
    client = edge.client_address(request, settings)
    known_device = auth.device_is_known(
        request.app.state.device_key, request.cookies.get(auth.DEVICE_COOKIE_NAME), account
    )
    refused = limiter.check(client, account, known_device=known_device)  # counts this attempt
    if refused:
        if refused == "account":
            logger.warning(
                "login refused: account %r has had too many failed attempts in the last hour "
                "(client %s)",
                account.strip().lower()[:80],
                client,
            )
        raise Refused(429, "too_many_attempts", "Too many failed attempts. Try again later.")
    if not auth.password_matches(settings, password):
        raise Refused(401, "invalid_password", "That password is not right.")
    limiter.succeeded(client, account)
    # A fresh token, never a promoted old one, stamped with the current epoch.
    request.state.session = auth.new_session(auth=True, epoch=auth.current_epoch(session))
    request.state.session_changed = True
    request.state.device_account = account  # a device cookie for this account, only now


def log_out(request: Request, session: Session) -> None:
    auth.bump_epoch(session)  # revokes this session and any copy of its cookie
    request.state.session = auth.new_session()
    request.state.session_changed = True


# ---- ids ----


class IdConvertor(Convertor):
    """Path ids: 1 to 10 ASCII digits. Longer input never reaches `int()`, which raises on
    numbers of more than 4300 digits."""

    regex = r"[0-9]{1,10}"

    def convert(self, value: str) -> int:
        return int(value)

    def to_string(self, value: int) -> str:
        return str(value)


register_url_convertor("id", IdConvertor())


def parse_id(value: str) -> int | None:
    """A database id from form text, or None when it is not a plain number in range."""
    if not (value.isascii() and value.isdigit()) or len(value) > MAX_ID_DIGITS:
        return None
    number = int(value)
    return number if 0 < number <= MAX_ID else None


def _in_range(pk: int | None) -> bool:
    return pk is not None and 0 < pk <= MAX_ID


# ---- how a meeting reads ----


def status_text(meeting: Meeting) -> str:
    """Plain words for the owner. Nothing here claims the meeting is being recorded."""
    if meeting.status == "bot_sent":
        return "Bot sent"
    if meeting.status == "processing":
        return STEP_TEXT.get(meeting.step, "Working on it")
    if meeting.status == "ready":
        return "Ready"
    return "Failed"


def status_tone(meeting: Meeting) -> str:
    return STATUS_TONES.get(meeting.status, "neutral")


def retry_block(meeting: Meeting) -> str | None:
    """Why a failed meeting cannot be retried, or None when it can.

    Retrying resumes at the step that failed, so it needs either the saved transcript or the
    downloaded audio. A bot that never recorded anything (step `waiting`) has neither.
    """
    if meeting.status != "failed":
        return "not_failed"
    if meeting.step == "waiting":
        return "no_audio"
    if meeting.failure_reason == REASON_NO_SPEECH:
        return "no_speech"  # the same audio would give the same result
    if meeting.transcript is None and meeting.audio_deleted_at is not None:
        return "audio_deleted"
    return None


RETRY_BLOCK_TEXT = {
    "audio_deleted": "This meeting can't be retried: the audio has been deleted and no transcript "
    "was saved.",
    "no_speech": "This meeting can't be retried: no speech was found, so trying again would give "
    "the same result.",
    "no_audio": "This meeting can't be retried: nothing was captured. Send the bot again with a "
    "new link.",
}

_markdown = MarkdownIt("commonmark", {"html": False, "linkify": False}).disable("image")


def _link_open(self, tokens, idx, options, env):
    tokens[idx].attrSet("rel", "noopener noreferrer nofollow")
    return self.renderToken(tokens, idx, options, env)


_markdown.add_render_rule("link_open", _link_open)


def render_markdown(source: str | None) -> Markup:
    """Notes come from a model reading untrusted meeting text, so raw HTML is escaped and
    links like `javascript:` are dropped by the renderer."""
    return Markup(_markdown.render(source or ""))


def worker_is_stale(session: Session) -> bool:
    state = session.get(AppState, 1)
    beat = state.worker_heartbeat_at if state else None
    return beat is None or datetime.now(UTC) - beat > WORKER_STALE_AFTER


# ---- meetings ----


def list_meetings(session: Session, limit: int = 50, before_id: int | None = None) -> list[Meeting]:
    query = select(Meeting).order_by(Meeting.id.desc()).limit(limit)
    if before_id is not None:
        query = query.where(Meeting.id < before_id)
    return list(session.scalars(query))


def meeting_or_404(session: Session, meeting_id: int) -> Meeting:
    meeting = session.get(Meeting, meeting_id) if _in_range(meeting_id) else None
    if meeting is None:
        raise HTTPException(404, "No such meeting")
    return meeting


def create_meeting(
    session: Session, vexa: VexaClient, url: str, template_id: int | None
) -> Meeting:
    try:
        return _create_meeting(session, vexa, url.strip(), template_id)
    except Refused:
        session.rollback()  # also releases the advisory lock
        raise


def _create_meeting(session: Session, vexa, url: str, template_id: int | None) -> Meeting:
    # The submitted link is never echoed back: a Zoom link carries its meeting password.
    try:
        if len(url) > MAX_URL_CHARS:
            raise UnsupportedMeetingUrl("too long")
        platform, native_id = parse_meeting_url(url)
    except UnsupportedMeetingUrl:
        raise Refused(
            400,
            "invalid_meeting_url",
            "That is not a Google Meet or Zoom link. Paste the full meeting link.",
        ) from None
    template = session.get(Template, template_id) if _in_range(template_id) else None
    if template is None:
        raise Refused(400, "template_required", "Choose a template for the notes.")

    session.execute(text("select pg_advisory_xact_lock(:key)"), {"key": NEW_MEETING_LOCK})
    active = session.scalars(select(Meeting.id).where(Meeting.status.in_(ACTIVE)).limit(1)).first()
    if active is not None:
        raise Refused(
            409, "bot_already_active", "A bot is already on a meeting. Wait for it to finish first."
        )
    try:
        vexa_id = vexa.send_bot(url)
    except VexaError as exc:
        logger.warning("send_bot failed (%s)", type(exc).__name__)
        raise Refused(
            502, "bot_send_failed", "The bot could not be sent. Please try again in a moment."
        ) from None
    meeting = Meeting(
        url=url,
        platform=platform,
        native_id=native_id,
        template_id=template.id,
        vexa_meeting_id=str(vexa_id),
        started_polling_at=datetime.now(UTC),
    )
    session.add(meeting)
    session.commit()
    return meeting


def regenerate_notes(
    session: Session, meeting: Meeting, template_id: int | None, openai_client
) -> Meeting:
    if meeting.status != "ready":
        raise Refused(409, "not_ready", "Notes can only be rewritten once a meeting is ready.")
    template = session.get(Template, template_id) if _in_range(template_id) else None
    if template is None:
        raise Refused(400, "template_required", "Choose a template.")
    source = meeting.speaker_transcript or meeting.transcript or ""
    terms = list(session.scalars(select(VocabularyTerm.term)))
    try:
        notes = generate_notes(source, template, terms, client=openai_client)
    except NotesFailed as exc:
        logger.error("meeting %s: regenerate failed (%s)", meeting.id, type(exc).__name__)
        raise Refused(
            502, "notes_failed", "The notes could not be written. The old notes were kept."
        ) from None
    meeting.notes = notes
    meeting.template_id = template.id
    session.commit()
    return meeting


def cancel_meeting(session: Session, vexa: VexaClient, meeting: Meeting) -> Meeting:
    """Take the bot out of a meeting it has not finished recording. No OpenAI call is made."""
    locked = session.scalars(  # re-read under a lock so the worker cannot move it meanwhile
        select(Meeting)
        .where(Meeting.id == meeting.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()
    if locked.status != "bot_sent":
        session.rollback()
        raise Refused(
            409,
            "not_waiting_for_bot",
            "Only a meeting that is still waiting for its bot can be cancelled.",
        )
    try:
        vexa.stop_bot(locked.platform, locked.native_id)
    except VexaError as exc:
        session.rollback()
        logger.warning("meeting %s: stop_bot failed (%s)", locked.id, type(exc).__name__)
        raise Refused(
            502, "bot_stop_failed", "The bot could not be stopped. Please try again in a moment."
        ) from None
    locked.status, locked.failure_reason = "failed", REASON_CANCELLED
    session.commit()
    return locked


def retry_meeting(session: Session, meeting: Meeting) -> Meeting:
    if retry_block(meeting) is not None:
        raise Refused(409, "not_retryable", "This meeting cannot be retried.")
    # The step is kept. A fresh polling start keeps the time limit from failing it at once.
    meeting.status, meeting.failure_reason = "processing", None
    meeting.started_polling_at = datetime.now(UTC)
    session.commit()
    return meeting


# ---- templates ----


def list_templates(session: Session) -> list[Template]:
    return list(session.scalars(select(Template).order_by(Template.name)))


def template_or_404(session: Session, template_id: int) -> Template:
    template = session.get(Template, template_id) if _in_range(template_id) else None
    if template is None:
        raise HTTPException(404, "No such template")
    return template


def _check_template(name: str, instructions: str) -> None:
    if not name or not instructions:
        raise Refused(400, "invalid_template", "Give the template a name and instructions.")
    if len(name) > MAX_TEMPLATE_NAME_CHARS:
        raise Refused(
            400,
            "invalid_template",
            f"The name can be at most {MAX_TEMPLATE_NAME_CHARS} characters.",
        )
    if len(instructions) > MAX_INSTRUCTION_CHARS:
        raise Refused(
            400,
            "invalid_template",
            f"The instructions can be at most {MAX_INSTRUCTION_CHARS} characters.",
        )


def _duplicate_template() -> Refused:
    return Refused(400, "duplicate_name", "A template with that name already exists.")


def save_template(
    session: Session, template: Template | None, name: str, instructions: str
) -> Template:
    """Create a template, or replace the name and instructions of `template`."""
    try:
        return _save_template(session, template, name.strip(), instructions.strip())
    except Refused:
        session.rollback()
        raise


def _save_template(
    session: Session, template: Template | None, name: str, instructions: str
) -> Template:
    _check_template(name, instructions)
    taken = session.scalars(select(Template.id).where(Template.name == name)).first()
    if taken is not None and (template is None or taken != template.id):
        raise _duplicate_template()
    if template is None:
        template = Template(name=name, instructions=instructions)
        session.add(template)
    else:
        template.name, template.instructions = name, instructions
    try:
        session.commit()
    except IntegrityError:  # lost a race with another request
        raise _duplicate_template() from None
    return template


# ---- vocabulary ----


def list_terms(session: Session) -> list[VocabularyTerm]:
    return list(session.scalars(select(VocabularyTerm).order_by(VocabularyTerm.term)))


def _check_term(term: str) -> None:
    if not term:
        raise Refused(400, "invalid_term", "Type a term to add.")
    if len(term) > MAX_TERM_CHARS:
        raise Refused(400, "invalid_term", f"A term can be at most {MAX_TERM_CHARS} characters.")
    if any(char in term for char in BAD_TERM_CHARS):
        raise Refused(
            400, "invalid_term", "A term cannot contain new lines or the characters < and >."
        )


def add_term(session: Session, term: str) -> VocabularyTerm:
    term = term.strip()
    _check_term(term)
    if session.scalars(select(VocabularyTerm.id).where(VocabularyTerm.term == term)).first():
        raise Refused(400, "duplicate_term", "That term is already in the list.")
    row = VocabularyTerm(term=term)
    session.add(row)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise Refused(400, "duplicate_term", "That term is already in the list.") from None
    return row


def delete_term(session: Session, term_id: int) -> bool:
    """Remove a term. False when there was no such term."""
    term = session.get(VocabularyTerm, term_id) if _in_range(term_id) else None
    if term is None:
        return False
    session.delete(term)
    session.commit()
    return True
