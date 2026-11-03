import logging
from datetime import UTC
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app import auth, services
from app.db import get_session
from app.models import User
from app.pipeline import MAX_JOIN_ATTEMPTS, REMOVAL_REASONS, is_join_failure
from app.services import (
    Refused,
    get_openai_client,
    get_vexa,
    meeting_or_404,
    parse_id,
    render_markdown,
    retry_block,
    status_text,
    status_tone,
    template_or_404,
)

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

templates.env.filters["markdown"] = render_markdown
templates.env.filters["local_time"] = lambda dt: dt.astimezone(UTC).strftime("%d %b %Y, %H:%M UTC")
templates.env.globals["status_text"] = status_text
templates.env.globals["status_tone"] = status_tone


def render(request: Request, name: str, status_code: int = 200, **context):
    context["csrf"] = request.state.session["csrf"]
    context["logged_in"] = request.state.user is not None
    return templates.TemplateResponse(request, name, context, status_code=status_code)


def see_other(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


# ---- login ----


@router.get("/login")
def login_page(request: Request):
    if request.state.user is not None:
        return see_other("/")
    return render(request, "login.html")


@router.post("/login")
def login(
    request: Request,
    email: str = Form(""),
    password: str = Form(""),
    session: Session = Depends(get_session),
):
    try:
        services.log_in(request, session, email, password)
    except Refused as exc:
        # The email is shown again so a typo is easy to fix; the password never is.
        return render(request, "login.html", exc.status, error=exc.message, email=email.strip())
    return see_other("/")


@router.post("/logout")
def logout(request: Request, session: Session = Depends(get_session)):
    services.log_out(request, session)
    return see_other("/login")


# ---- meetings ----


def _index(request, session: Session, user: User, status_code=200, **context):
    meetings = services.list_meetings(session, user)
    return render(
        request,
        "index.html",
        status_code,
        meetings=meetings,
        refresh=any(m.status in services.ACTIVE for m in meetings),
        templates=services.list_templates(session, user),
        worker_stale=services.worker_is_stale(session),
        **context,
    )


@router.get("/")
def index(request: Request, session: Session = Depends(get_session)):
    if request.state.user is None:
        return render(request, "landing.html")
    return _index(request, session, request.state.user)


# ---- public pages ----


@router.get("/privacy")
def privacy(request: Request):
    days = request.app.state.settings.audio_retention_days
    return render(request, "privacy.html", audio_retention_days=days)


@router.get("/terms")
def terms(request: Request):
    return render(request, "terms.html")


@router.get("/styleguide")
def styleguide(request: Request):
    """Every shared component in every state: the parity reference. Owner only, never indexed."""
    response = render(request, "styleguide.html")
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


@router.post("/meetings")
def create_meeting(
    request: Request,
    url: str = Form(""),
    template_id: str = Form(""),
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
    vexa=Depends(get_vexa),
):
    try:
        meeting = services.create_meeting(
            session, vexa, user, url, parse_id(template_id), request.app.state.settings
        )
    except Refused as exc:
        # The submitted link is not echoed back: a Zoom link carries its meeting password.
        return _index(request, session, user, exc.status, error=exc.message, chosen=template_id)
    return see_other(f"/meetings/{meeting.id}")


def _meeting_page(request, session: Session, meeting, status_code=200, **context):
    block = retry_block(meeting) if meeting.status == "failed" else None
    return render(
        request,
        "meeting.html",
        status_code,
        meeting=meeting,
        templates=services.list_templates(session, request.state.user),
        worker_stale=meeting.status in services.ACTIVE and services.worker_is_stale(session),
        can_end=services.can_end(meeting),
        retry_block=block,
        refused_wait=block is not None and is_join_failure(meeting),
        retry_block_message=services.retry_block_message(meeting, block),
        bot_left_early=meeting.completion_reason in REMOVAL_REASONS,
        max_join_attempts=MAX_JOIN_ATTEMPTS,
        **context,
    )


@router.get("/meetings/{meeting_id:id}")
def meeting_page(
    meeting_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
):
    return _meeting_page(request, session, meeting_or_404(session, user, meeting_id))


@router.post("/meetings/{meeting_id:id}/regenerate")
def regenerate(
    meeting_id: int,
    request: Request,
    template_id: str = Form(""),
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
    openai_client=Depends(get_openai_client),
):
    meeting = meeting_or_404(session, user, meeting_id)
    try:
        services.regenerate_notes(session, meeting, parse_id(template_id), openai_client)
    except Refused as exc:
        return _meeting_page(request, session, meeting, exc.status, error=exc.message)
    return see_other(f"/meetings/{meeting.id}")


@router.post("/meetings/{meeting_id:id}/cancel")
def cancel(
    meeting_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
    vexa=Depends(get_vexa),
):
    meeting = meeting_or_404(session, user, meeting_id)
    try:
        services.cancel_meeting(session, vexa, meeting)
    except Refused as exc:
        return _meeting_page(request, session, meeting, exc.status, error=exc.message)
    return see_other(f"/meetings/{meeting.id}")


@router.post("/meetings/{meeting_id:id}/end")
def end(
    meeting_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
    vexa=Depends(get_vexa),
):
    meeting = meeting_or_404(session, user, meeting_id)
    try:
        services.end_meeting(session, vexa, meeting)
    except Refused as exc:
        return _meeting_page(request, session, meeting, exc.status, error=exc.message)
    return see_other(f"/meetings/{meeting.id}")


@router.post("/meetings/{meeting_id:id}/retry")
def retry(
    meeting_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
    vexa=Depends(get_vexa),
):
    meeting = meeting_or_404(session, user, meeting_id)
    try:
        services.retry_meeting(session, meeting, vexa)
    except Refused as exc:
        return _meeting_page(request, session, meeting, exc.status, error=exc.message)
    return see_other(f"/meetings/{meeting.id}")


# ---- templates ----


def _templates_page(request, session: Session, user: User, status_code=200, **context):
    return render(
        request,
        "templates.html",
        status_code,
        rows=services.list_templates(session, user),
        **context,
    )


def _save_template(request, session: Session, user: User, template, name, instructions):
    try:
        services.save_template(session, user, template, name, instructions)
    except Refused as exc:
        return _templates_page(request, session, user, exc.status, error=exc.message)
    return see_other("/templates")


@router.get("/templates")
def templates_page(
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
):
    return _templates_page(request, session, user)


@router.post("/templates")
def add_template(
    request: Request,
    name: str = Form(""),
    instructions: str = Form(""),
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
):
    return _save_template(request, session, user, None, name, instructions)


@router.post("/templates/{template_id:id}")
def edit_template(
    template_id: int,
    request: Request,
    name: str = Form(""),
    instructions: str = Form(""),
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
):
    template = template_or_404(session, user, template_id)
    return _save_template(request, session, user, template, name, instructions)


# ---- vocabulary ----


def _vocabulary_page(request, session: Session, user: User, status_code=200, **context):
    return render(
        request,
        "vocabulary.html",
        status_code,
        rows=services.list_terms(session, user),
        **context,
    )


@router.get("/vocabulary")
def vocabulary_page(
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
):
    return _vocabulary_page(request, session, user)


@router.post("/vocabulary")
def add_term(
    request: Request,
    term: str = Form(""),
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
):
    try:
        services.add_term(session, user, term)
    except Refused as exc:
        return _vocabulary_page(
            request, session, user, exc.status, error=exc.message, term=term.strip()
        )
    return see_other("/vocabulary")


@router.post("/vocabulary/{term_id:id}/delete")
def delete_term(
    term_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
):
    services.delete_term(session, user, term_id)
    return see_other("/vocabulary")
