"""The JSON API under `/api/`: everything the owner pages do, for the Next.js frontend.

Same session cookie, same login limit and same rules as the pages (the work is in
`app/services.py`). What differs is the wire format:

- `app/auth.py` `guard` runs first: no login gives 401 JSON, never a redirect, and every call
  that is not a read must send the session's token in the `X-CSRF-Token` header (403 otherwise).
- Every error has one shape, `{"error": {"code": "...", "message": "..."}}`; see `install_errors`.
"""

from fastapi import APIRouter, Depends, FastAPI, Query, Request, Response
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import auth, services
from app.api_schemas import (
    ErrorResponse,
    Login,
    MeetingDetail,
    MeetingList,
    MeetingSummary,
    NewMeeting,
    NewTerm,
    Regenerate,
    SessionState,
    TemplateIn,
    TemplateList,
    TemplateOut,
    TermList,
    TermOut,
)
from app.db import get_session
from app.models import Meeting
from app.pipeline import MAX_JOIN_ATTEMPTS, REASON_UNEXPECTED, REMOVAL_REASONS
from app.services import Refused, get_openai_client, get_vexa

REFRESH_SECONDS = 30  # how often the Jinja page refreshes while a meeting is active
LIST_DEFAULT, LIST_MAX = 50, 100

router = APIRouter(
    prefix="/api",
    responses={
        401: {"model": ErrorResponse, "description": "Not logged in"},
        403: {"model": ErrorResponse, "description": "Missing or wrong X-CSRF-Token"},
        422: {"model": ErrorResponse, "description": "Malformed request"},
    },
    # Operation ids are the function names, so generated client methods read well.
    generate_unique_id_function=lambda route: route.name,
)


# ---- errors ----

HTTP_CODES = {
    401: "not_authenticated",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
}


def is_api_path(request: Request) -> bool:
    path = auth.request_path(request)
    return path == "/api" or path.startswith(auth.API_PREFIX)


def error_response(
    status: int, code: str, message: str, fields: list[dict] | None = None, headers=None
) -> JSONResponse:
    error: dict = {"code": code, "message": message}
    if fields is not None:
        error["fields"] = fields
    return JSONResponse({"error": error}, status_code=status, headers=headers)


def _validation_fields(exc: RequestValidationError) -> list[dict]:
    """Which fields failed and why. The submitted values are left out: one is a password."""
    fields = []
    for problem in exc.errors():
        path = [part for part in problem["loc"][1:] if isinstance(part, str)]
        fields.append({"field": ".".join(path) or "body", "message": problem["msg"]})
    return fields


def install_errors(app: FastAPI) -> None:
    """Make every error under `/api/` the one JSON shape. Other paths keep their own handling.

    The 401 and 403 handlers are in `app/main.py` beside the page versions of the same two.
    """

    @app.exception_handler(Refused)
    async def _refused(request: Request, exc: Refused):
        return error_response(exc.status, exc.code, exc.message)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException):
        if not is_api_path(request):
            return await http_exception_handler(request, exc)
        code = HTTP_CODES.get(exc.status_code, "error")
        return error_response(exc.status_code, code, str(exc.detail), headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _invalid(request: Request, exc: RequestValidationError):
        if not is_api_path(request):
            return await request_validation_exception_handler(request, exc)
        message = "Some fields are missing or not valid."
        return error_response(422, "validation_error", message, _validation_fields(exc))

    @app.exception_handler(Exception)
    async def _crashed(request: Request, exc: Exception):
        # Starlette re-raises after this, so the cause is still logged. Nothing of it is sent.
        if is_api_path(request):
            return error_response(500, "server_error", "Something went wrong on our side.")
        return PlainTextResponse("Internal Server Error", status_code=500)


# ---- session ----


def _session_state(request: Request) -> SessionState:
    current = request.state.session
    return SessionState(authenticated=current["auth"], csrf_token=current["csrf"])


@router.get("/session", tags=["session"])
def read_session(request: Request) -> SessionState:
    """Whether the cookie is a logged-in session, plus the CSRF token to send on writes.

    Public. Call it first: it also gives a logged-out visitor a cookie to log in with.
    """
    return _session_state(request)


@router.post(
    "/session",
    tags=["session"],
    responses={401: {"model": ErrorResponse}, 429: {"model": ErrorResponse}},
)
def log_in(body: Login, request: Request, session: Session = Depends(get_session)) -> SessionState:
    """Log in with the owner password. Public, but needs the X-CSRF-Token from `GET /session`.

    The login limits are the page's: 5 failed attempts per client address in 5 minutes and 10 per
    account in an hour, then 429. The response carries the new session's CSRF token: it replaces
    the one from before login.
    """
    services.log_in(request, session, body.password)
    return _session_state(request)


@router.delete("/session", tags=["session"])
def log_out(request: Request, session: Session = Depends(get_session)) -> SessionState:
    """Log out everywhere: every session cookie issued so far stops working."""
    services.log_out(request, session)
    return _session_state(request)


# ---- meetings ----


def _title(meeting: Meeting) -> str:
    return f"{'Zoom' if meeting.platform == 'zoom' else 'Google Meet'} {meeting.native_id}"


def _summary(meeting: Meeting) -> MeetingSummary:
    return MeetingSummary(
        id=meeting.id,
        title=_title(meeting),
        platform=meeting.platform,
        native_id=meeting.native_id,
        status=meeting.status,
        step=meeting.step,
        status_text=services.status_text(meeting),
        status_tone=services.status_tone(meeting),
        template_id=meeting.template_id,
        created_at=meeting.created_at,
    )


def _detail(meeting: Meeting) -> MeetingDetail:
    """The meeting as its page shows it. The meeting link is never included: a Zoom link
    carries its meeting password."""
    failed, ready = meeting.status == "failed", meeting.status == "ready"
    block = services.retry_block(meeting) if failed else None
    return MeetingDetail(
        **_summary(meeting).model_dump(),
        failure_reason=(meeting.failure_reason or REASON_UNEXPECTED) if failed else None,
        retry_block=block,
        retry_block_message=services.RETRY_BLOCK_TEXT.get(block),
        can_cancel=meeting.status == "bot_sent",
        can_retry=failed and block is None,
        can_regenerate=ready,
        join_attempts=meeting.join_attempts,
        max_join_attempts=MAX_JOIN_ATTEMPTS,
        bot_left_early=ready and meeting.completion_reason in REMOVAL_REASONS,
        speakers_incomplete=meeting.speakers_incomplete,
        refresh_after_seconds=REFRESH_SECONDS if meeting.status in services.ACTIVE else None,
        notes=meeting.notes if ready else None,
        notes_html=str(services.render_markdown(meeting.notes)) if ready else None,
        transcript=meeting.speaker_transcript or meeting.transcript or None,
    )


@router.get("/meetings", tags=["meetings"])
def list_meetings(
    limit: int = Query(LIST_DEFAULT, ge=1, le=LIST_MAX),
    before_id: int | None = Query(None, ge=1, le=services.MAX_ID),
    session: Session = Depends(get_session),
) -> MeetingList:
    """Newest first. For the next page pass `before_id` = `next_before_id`."""
    rows = services.list_meetings(session, limit + 1, before_id)
    page = rows[:limit]
    return MeetingList(
        meetings=[_summary(meeting) for meeting in page],
        has_more=len(rows) > limit,
        next_before_id=page[-1].id if len(rows) > limit else None,
        worker_stale=services.worker_is_stale(session),
    )


@router.get("/meetings/{meeting_id:id}", tags=["meetings"])
def get_meeting(meeting_id: int, session: Session = Depends(get_session)) -> MeetingDetail:
    return _detail(services.meeting_or_404(session, meeting_id))


@router.post(
    "/meetings",
    status_code=201,
    tags=["meetings"],
    responses={code: {"model": ErrorResponse} for code in (400, 409, 502)},
)
def create_meeting(
    body: NewMeeting,
    response: Response,
    session: Session = Depends(get_session),
    vexa=Depends(get_vexa),
) -> MeetingDetail:
    """Send the bot to a Google Meet or Zoom link. One bot at a time: 409 while one is active."""
    meeting = services.create_meeting(session, vexa, body.url, body.template_id)
    response.headers["Location"] = f"/api/meetings/{meeting.id}"
    return _detail(meeting)


@router.post(
    "/meetings/{meeting_id:id}/cancel",
    tags=["meetings"],
    responses={code: {"model": ErrorResponse} for code in (404, 409, 502)},
)
def cancel_meeting(
    meeting_id: int, session: Session = Depends(get_session), vexa=Depends(get_vexa)
) -> MeetingDetail:
    """Take the bot out of a meeting that is still `bot_sent`."""
    meeting = services.meeting_or_404(session, meeting_id)
    return _detail(services.cancel_meeting(session, vexa, meeting))


@router.post(
    "/meetings/{meeting_id:id}/retry",
    tags=["meetings"],
    responses={code: {"model": ErrorResponse} for code in (404, 409)},
)
def retry_meeting(meeting_id: int, session: Session = Depends(get_session)) -> MeetingDetail:
    """Resume a failed meeting at the step that failed. Only when `can_retry` is true."""
    meeting = services.meeting_or_404(session, meeting_id)
    return _detail(services.retry_meeting(session, meeting))


@router.post(
    "/meetings/{meeting_id:id}/regenerate",
    tags=["meetings"],
    responses={code: {"model": ErrorResponse} for code in (400, 404, 409, 502)},
)
def regenerate_notes(
    meeting_id: int,
    body: Regenerate,
    session: Session = Depends(get_session),
    openai_client=Depends(get_openai_client),
) -> MeetingDetail:
    """Rewrite the notes of a `ready` meeting with a template. Runs in the request: allow 5 min."""
    meeting = services.meeting_or_404(session, meeting_id)
    return _detail(services.regenerate_notes(session, meeting, body.template_id, openai_client))


# ---- templates ----


@router.get("/templates", tags=["templates"])
def list_templates(session: Session = Depends(get_session)) -> TemplateList:
    """Sorted by name."""
    rows = services.list_templates(session)
    return TemplateList(templates=[TemplateOut.model_validate(row) for row in rows])


@router.post(
    "/templates",
    status_code=201,
    tags=["templates"],
    responses={400: {"model": ErrorResponse}},
)
def create_template(
    body: TemplateIn, response: Response, session: Session = Depends(get_session)
) -> TemplateOut:
    template = services.save_template(session, None, body.name, body.instructions)
    response.headers["Location"] = f"/api/templates/{template.id}"
    return TemplateOut.model_validate(template)


@router.put(
    "/templates/{template_id:id}",
    tags=["templates"],
    responses={code: {"model": ErrorResponse} for code in (400, 404)},
)
def update_template(
    template_id: int, body: TemplateIn, session: Session = Depends(get_session)
) -> TemplateOut:
    """Replace the name and instructions."""
    template = services.template_or_404(session, template_id)
    services.save_template(session, template, body.name, body.instructions)
    return TemplateOut.model_validate(template)


# ---- vocabulary ----


@router.get("/vocabulary", tags=["vocabulary"])
def list_terms(session: Session = Depends(get_session)) -> TermList:
    """Sorted alphabetically."""
    return TermList(
        terms=[TermOut(id=row.id, term=row.term) for row in services.list_terms(session)]
    )


@router.post(
    "/vocabulary",
    status_code=201,
    tags=["vocabulary"],
    responses={400: {"model": ErrorResponse}},
)
def add_term(body: NewTerm, session: Session = Depends(get_session)) -> TermOut:
    row = services.add_term(session, body.term)
    return TermOut(id=row.id, term=row.term)


@router.delete(
    "/vocabulary/{term_id:id}",
    status_code=204,
    tags=["vocabulary"],
    responses={404: {"model": ErrorResponse}},
)
def delete_term(term_id: int, session: Session = Depends(get_session)) -> Response:
    if not services.delete_term(session, term_id):
        raise StarletteHTTPException(404, "No such term")
    return Response(status_code=204)
