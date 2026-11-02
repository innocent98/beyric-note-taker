"""Owner-facing pages: log in, paste a meeting link, read the notes and transcript."""

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from markdown_it import MarkdownIt
from markupsafe import Markup
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.convertors import Convertor, register_url_convertor

from app import auth
from app.db import get_session
from app.models import AppState, Meeting, Template, VocabularyTerm
from app.notes import MAX_TERM_CHARS, NotesFailed, generate_notes
from app.pipeline import REASON_CANCELLED, REASON_NO_SPEECH, REMOVAL_REASONS
from app.vexa import UnsupportedMeetingUrl, VexaClient, VexaError, parse_meeting_url

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

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


def status_text(meeting: Meeting) -> str:
    """Plain words for the owner. Nothing here claims the meeting is being recorded."""
    if meeting.status == "bot_sent":
        return "Bot sent"
    if meeting.status == "processing":
        return STEP_TEXT.get(meeting.step, "Working on it")
    if meeting.status == "ready":
        return "Ready"
    return "Failed"


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


_markdown = MarkdownIt("commonmark", {"html": False, "linkify": False}).disable("image")


def _link_open(self, tokens, idx, options, env):
    tokens[idx].attrSet("rel", "noopener noreferrer nofollow")
    return self.renderToken(tokens, idx, options, env)


_markdown.add_render_rule("link_open", _link_open)


def render_markdown(source: str | None) -> Markup:
    """Notes come from a model reading untrusted meeting text, so raw HTML is escaped and
    links like `javascript:` are dropped by the renderer."""
    return Markup(_markdown.render(source or ""))


templates.env.filters["markdown"] = render_markdown
templates.env.filters["local_time"] = lambda dt: dt.astimezone(UTC).strftime("%d %b %Y, %H:%M UTC")
templates.env.globals["status_text"] = status_text


def get_vexa() -> VexaClient:
    return VexaClient()


def get_openai_client():
    return None  # generate_notes builds its own client from settings


def render(request: Request, name: str, status_code: int = 200, **context):
    context["csrf"] = request.state.session["csrf"]
    context["logged_in"] = request.state.session["auth"]
    return templates.TemplateResponse(request, name, context, status_code=status_code)


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


def see_other(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


# ---- login ----


@router.get("/login")
def login_page(request: Request):
    if request.state.session["auth"]:
        return see_other("/")
    return render(request, "login.html")


@router.post("/login")
def login(request: Request, password: str = Form(""), session: Session = Depends(get_session)):
    limiter: auth.LoginLimiter = request.app.state.limiter
    client = request.client.host if request.client else "unknown"
    if not limiter.try_acquire(client):  # counts this attempt; success clears it below
        error = "Too many failed attempts. Try again in 15 minutes."
        return render(request, "login.html", 429, error=error)
    if not auth.password_matches(request.app.state.settings, password):
        return render(request, "login.html", 401, error="That password is not right.")
    limiter.reset(client)
    # A fresh token, never a promoted old one, stamped with the current epoch.
    request.state.session = auth.new_session(auth=True, epoch=auth.current_epoch(session))
    request.state.session_changed = True
    return see_other("/")


@router.post("/logout")
def logout(request: Request, session: Session = Depends(get_session)):
    auth.bump_epoch(session)  # revokes this session and any copy of its cookie
    request.state.session = auth.new_session()
    request.state.session_changed = True
    return see_other("/login")


# ---- meetings ----


def _index(request, session: Session, status_code=200, **context):
    meetings = session.scalars(select(Meeting).order_by(Meeting.id.desc()).limit(50)).all()
    all_templates = session.scalars(select(Template).order_by(Template.name)).all()
    state = session.get(AppState, 1)
    beat = state.worker_heartbeat_at if state else None
    worker_stale = beat is None or datetime.now(UTC) - beat > WORKER_STALE_AFTER
    return render(
        request,
        "index.html",
        status_code,
        meetings=meetings,
        templates=all_templates,
        worker_stale=worker_stale,
        **context,
    )


@router.get("/")
def index(request: Request, session: Session = Depends(get_session)):
    return _index(request, session)


@router.post("/meetings")
def create_meeting(
    request: Request,
    url: str = Form(""),
    template_id: str = Form(""),
    session: Session = Depends(get_session),
    vexa=Depends(get_vexa),
):
    url = url.strip()

    def refuse(code: int, error: str):
        session.rollback()  # also releases the advisory lock
        # The submitted link is not echoed back: a Zoom link carries its meeting password.
        return _index(request, session, code, error=error, chosen=template_id)

    try:
        if len(url) > MAX_URL_CHARS:
            raise UnsupportedMeetingUrl("too long")
        platform, native_id = parse_meeting_url(url)
    except UnsupportedMeetingUrl:
        return refuse(400, "That is not a Google Meet or Zoom link. Paste the full meeting link.")
    template_pk = parse_id(template_id)
    template = session.get(Template, template_pk) if template_pk else None
    if template is None:
        return refuse(400, "Choose a template for the notes.")

    session.execute(text("select pg_advisory_xact_lock(:key)"), {"key": NEW_MEETING_LOCK})
    active = session.scalars(select(Meeting.id).where(Meeting.status.in_(ACTIVE)).limit(1)).first()
    if active is not None:
        return refuse(409, "A bot is already on a meeting. Wait for it to finish first.")
    try:
        vexa_id = vexa.send_bot(url)
    except VexaError as exc:
        logger.warning("send_bot failed (%s)", type(exc).__name__)
        return refuse(502, "The bot could not be sent. Please try again in a moment.")
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
    return see_other(f"/meetings/{meeting.id}")


def _meeting_or_404(session: Session, meeting_id: int) -> Meeting:
    meeting = session.get(Meeting, meeting_id) if 0 < meeting_id <= MAX_ID else None
    if meeting is None:
        raise HTTPException(404, "No such meeting")
    return meeting


def _meeting_page(request, session: Session, meeting: Meeting, status_code=200, **context):
    all_templates = session.scalars(select(Template).order_by(Template.name)).all()
    return render(
        request,
        "meeting.html",
        status_code,
        meeting=meeting,
        templates=all_templates,
        retry_block=retry_block(meeting) if meeting.status == "failed" else None,
        bot_left_early=meeting.completion_reason in REMOVAL_REASONS,
        **context,
    )


@router.get("/meetings/{meeting_id:id}")
def meeting_page(meeting_id: int, request: Request, session: Session = Depends(get_session)):
    return _meeting_page(request, session, _meeting_or_404(session, meeting_id))


@router.post("/meetings/{meeting_id:id}/regenerate")
def regenerate(
    meeting_id: int,
    request: Request,
    template_id: str = Form(""),
    session: Session = Depends(get_session),
    openai_client=Depends(get_openai_client),
):
    meeting = _meeting_or_404(session, meeting_id)
    if meeting.status != "ready":
        error = "Notes can only be rewritten once a meeting is ready."
        return _meeting_page(request, session, meeting, 409, error=error)
    template_pk = parse_id(template_id)
    template = session.get(Template, template_pk) if template_pk else None
    if template is None:
        return _meeting_page(request, session, meeting, 400, error="Choose a template.")
    source = meeting.speaker_transcript or meeting.transcript or ""
    terms = list(session.scalars(select(VocabularyTerm.term)))
    try:
        notes = generate_notes(source, template, terms, client=openai_client)
    except NotesFailed as exc:
        logger.error("meeting %s: regenerate failed (%s)", meeting.id, type(exc).__name__)
        error = "The notes could not be written. The old notes were kept."
        return _meeting_page(request, session, meeting, 502, error=error)
    meeting.notes = notes
    meeting.template_id = template.id
    session.commit()
    return see_other(f"/meetings/{meeting.id}")


@router.post("/meetings/{meeting_id:id}/cancel")
def cancel(
    meeting_id: int,
    request: Request,
    session: Session = Depends(get_session),
    vexa=Depends(get_vexa),
):
    """Take the bot out of a meeting it has not finished recording. No OpenAI call is made."""
    _meeting_or_404(session, meeting_id)
    meeting = session.scalars(  # re-read under a lock so the worker cannot move it meanwhile
        select(Meeting)
        .where(Meeting.id == meeting_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()
    if meeting.status != "bot_sent":
        session.rollback()
        error = "Only a meeting that is still waiting for its bot can be cancelled."
        return _meeting_page(request, session, meeting, 409, error=error)
    try:
        vexa.stop_bot(meeting.platform, meeting.native_id)
    except VexaError as exc:
        session.rollback()
        logger.warning("meeting %s: stop_bot failed (%s)", meeting.id, type(exc).__name__)
        error = "The bot could not be stopped. Please try again in a moment."
        return _meeting_page(request, session, meeting, 502, error=error)
    meeting.status, meeting.failure_reason = "failed", REASON_CANCELLED
    session.commit()
    return see_other(f"/meetings/{meeting.id}")


@router.post("/meetings/{meeting_id:id}/retry")
def retry(meeting_id: int, request: Request, session: Session = Depends(get_session)):
    meeting = _meeting_or_404(session, meeting_id)
    if retry_block(meeting) is not None:
        error = "This meeting cannot be retried."
        return _meeting_page(request, session, meeting, 409, error=error)
    # The step is kept. A fresh polling start keeps the time limit from failing it at once.
    meeting.status, meeting.failure_reason = "processing", None
    meeting.started_polling_at = datetime.now(UTC)
    session.commit()
    return see_other(f"/meetings/{meeting.id}")


# ---- templates ----


def _templates_page(request, session: Session, status_code=200, **context):
    rows = session.scalars(select(Template).order_by(Template.name)).all()
    return render(request, "templates.html", status_code, rows=rows, **context)


def _check_template(name: str, instructions: str) -> str | None:
    if not name or not instructions:
        return "Give the template a name and instructions."
    if len(name) > MAX_TEMPLATE_NAME_CHARS:
        return f"The name can be at most {MAX_TEMPLATE_NAME_CHARS} characters."
    if len(instructions) > MAX_INSTRUCTION_CHARS:
        return f"The instructions can be at most {MAX_INSTRUCTION_CHARS} characters."
    return None


def _save_template(request, session: Session, template: Template | None, name, instructions):
    name, instructions = name.strip(), instructions.strip()
    error = _check_template(name, instructions)
    if error is None:
        taken = session.scalars(select(Template.id).where(Template.name == name)).first()
        if taken is not None and (template is None or taken != template.id):
            error = "A template with that name already exists."
    if error is not None:
        session.rollback()
        return _templates_page(request, session, 400, error=error)
    if template is None:
        template = Template(name=name, instructions=instructions)
        session.add(template)
    else:
        template.name, template.instructions = name, instructions
    try:
        session.commit()
    except IntegrityError:  # lost a race with another request
        session.rollback()
        return _templates_page(
            request, session, 400, error="A template with that name already exists."
        )
    return see_other("/templates")


@router.get("/templates")
def templates_page(request: Request, session: Session = Depends(get_session)):
    return _templates_page(request, session)


@router.post("/templates")
def add_template(
    request: Request,
    name: str = Form(""),
    instructions: str = Form(""),
    session: Session = Depends(get_session),
):
    return _save_template(request, session, None, name, instructions)


@router.post("/templates/{template_id:id}")
def edit_template(
    template_id: int,
    request: Request,
    name: str = Form(""),
    instructions: str = Form(""),
    session: Session = Depends(get_session),
):
    template = session.get(Template, template_id) if 0 < template_id <= MAX_ID else None
    if template is None:
        raise HTTPException(404, "No such template")
    return _save_template(request, session, template, name, instructions)


# ---- vocabulary ----


def _vocabulary_page(request, session: Session, status_code=200, **context):
    rows = session.scalars(select(VocabularyTerm).order_by(VocabularyTerm.term)).all()
    return render(request, "vocabulary.html", status_code, rows=rows, **context)


def _check_term(term: str) -> str | None:
    if not term:
        return "Type a term to add."
    if len(term) > MAX_TERM_CHARS:
        return f"A term can be at most {MAX_TERM_CHARS} characters."
    if any(char in term for char in BAD_TERM_CHARS):
        return "A term cannot contain new lines or the characters < and >."
    return None


@router.get("/vocabulary")
def vocabulary_page(request: Request, session: Session = Depends(get_session)):
    return _vocabulary_page(request, session)


@router.post("/vocabulary")
def add_term(request: Request, term: str = Form(""), session: Session = Depends(get_session)):
    term = term.strip()
    error = _check_term(term)
    if (
        error is None
        and session.scalars(select(VocabularyTerm.id).where(VocabularyTerm.term == term)).first()
    ):
        error = "That term is already in the list."
    if error is not None:
        return _vocabulary_page(request, session, 400, error=error, term=term)
    session.add(VocabularyTerm(term=term))
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        return _vocabulary_page(
            request, session, 400, error="That term is already in the list.", term=term
        )
    return see_other("/vocabulary")


@router.post("/vocabulary/{term_id:id}/delete")
def delete_term(term_id: int, session: Session = Depends(get_session)):
    term = session.get(VocabularyTerm, term_id) if 0 < term_id <= MAX_ID else None
    if term is not None:
        session.delete(term)
        session.commit()
    return see_other("/vocabulary")
