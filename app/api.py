"""The JSON API under `/api/`: everything the pages do, for the Next.js frontend.

Same session cookie, same login limit and same rules as the pages (the work is in
`app/services.py`). What differs is the wire format:

- `app/auth.py` `guard` runs first: no login gives 401 JSON, never a redirect, and every call
  that is not a read must send the session's token in the `X-CSRF-Token` header (403 otherwise).
- Every error has one shape, `{"error": {"code": "...", "message": "..."}}`; see `install_errors`.
"""

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, FastAPI, Query, Request, Response
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import (
    api_accounts,
    api_admin,
    api_ask,
    api_integrations,
    api_projects,
    api_series,
    api_settings,
    api_shares,
    api_sprints,
    api_tasks,
    api_team,
    api_translation,
    api_workspace,
    auth,
    calendar_api,
    calendar_recurring,
    export,
    notifications,
    services,
    tasks,
    teams,
    titles,
    translation,
    usage,
    visibility,
    workspace,
)
from app import series as series_rules
from app.api_schemas import (
    BadgesOut,
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
    UsageOut,
    UserOut,
)
from app.db import get_session
from app.models import Decision, Meeting, MeetingTranslation, ReviewItem, User, UserSettings
from app.notes_document import (
    build_notes_document,
    content_disposition,
    export_filename,
    parse_transcript,
)
from app.pipeline import (
    MAX_JOIN_ATTEMPTS,
    REASON_UNEXPECTED,
    REMOVAL_REASONS,
    is_join_failure,
    join_retry_at,
)
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


def _session_state(request: Request, db: Session) -> SessionState:
    current, user = request.state.session, request.state.user
    if user is None:
        return SessionState(
            authenticated=False,
            csrf_token=current["csrf"],
            user=None,
            usage=None,
            unread_notifications=None,
            badges=None,
        )
    settings = request.app.state.settings
    used = usage.hours_used(db, user, datetime.now(UTC), settings.max_poll_hours * 3600)
    return SessionState(
        authenticated=True,
        csrf_token=current["csrf"],
        user=UserOut(
            email=user.email,
            name=user.name,
            is_admin=user.is_admin,
            email_verified=user.email_verified_at is not None,
            time_zone=db.scalar(
                select(UserSettings.time_zone).where(UserSettings.user_id == user.id)
            ),
        ),
        usage=UsageOut(used_seconds=used, cap_seconds=usage.effective_cap(db, user)),
        unread_notifications=notifications.unread_count(db, user),
        badges=BadgesOut(
            review_open=workspace.open_review_count(db, user),
            tasks_new=tasks.new_count(db, user),
        ),
    )


@router.get("/session", tags=["session"])
def read_session(request: Request, session: Session = Depends(get_session)) -> SessionState:
    """Whether the cookie is a logged-in session, plus the CSRF token to send on writes.

    Public. Call it first: it also gives a logged-out visitor a cookie to log in with. Logged in,
    it names the user and reports their recorded hours (`usage`).
    """
    return _session_state(request, session)


@router.post(
    "/session",
    tags=["session"],
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        429: {"model": ErrorResponse},
    },
)
def log_in(body: Login, request: Request, session: Session = Depends(get_session)) -> SessionState:
    """Log in with email and password. Public, but needs the X-CSRF-Token from `GET /session`.

    401 `invalid_credentials` for a wrong password or an unknown email (the same answer for
    both, and for an account whose email was never confirmed). 403 `account_suspended` when the
    password was right but the account is suspended. The login limits are the page's: 5 failed
    attempts per client address in 5 minutes and 10 per email in an hour, then 429. The response
    carries the new session's CSRF token: it replaces the one from before login.
    """
    services.log_in(request, session, body.email, body.password)
    return _session_state(request, session)


@router.delete("/session", tags=["session"])
def log_out(request: Request, session: Session = Depends(get_session)) -> SessionState:
    """Log this user out everywhere: every session cookie they were issued stops working.
    Other users are not affected."""
    services.log_out(request, session)
    return _session_state(request, session)


# ---- meetings ----


_title = services.meeting_title


def _platform_label(meeting: Meeting) -> str:
    return "Zoom" if meeting.platform == "zoom" else "Google Meet"


def _summary(meeting: Meeting, template_name: str, tasks_count: int = 0) -> MeetingSummary:
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
        template_name=template_name,
        created_at=meeting.created_at,
        tasks_count=tasks_count,
    )


def _participants(meeting: Meeting) -> list[str]:
    """Speaker names from the labelled transcript, each once, in order of first speaking."""
    if not meeting.speaker_transcript:
        return []
    names: list[str] = []
    for turn in parse_transcript(meeting.speaker_transcript):
        if turn.speaker and turn.speaker not in names:
            names.append(turn.speaker)
    return names


def _workspace_counts(meeting: Meeting, session: Session) -> tuple[int, int]:
    """(shown decisions, open review items) of a meeting, as the Decisions and Review screens
    count them."""
    decisions = session.scalar(
        select(func.count())
        .select_from(Decision)
        .where(Decision.meeting_id == meeting.id, Decision.hidden_at.is_(None))
    )
    review = session.scalar(
        select(func.count())
        .select_from(ReviewItem)
        .where(ReviewItem.meeting_id == meeting.id, ReviewItem.checked_at.is_(None))
    )
    return decisions or 0, review or 0


def _owner_name(session: Session, meeting: Meeting) -> str | None:
    owner = session.get(User, meeting.owner_user_id)
    if owner is None:
        return None
    return titles.clean_title(owner.name, 100) or owner.email.split("@")[0]


def _detail(meeting: Meeting, session: Session, user: User) -> MeetingDetail:
    """The meeting as its page shows it. The meeting link is never included: a Zoom link
    carries its meeting password. The `can_*` flags are false for a teammate reading a shared
    meeting: only the owner may change it (those routes answer 404 to anyone else)."""
    failed, ready = meeting.status == "failed", meeting.status == "ready"
    owner = visibility.owns(user, meeting)
    block = services.retry_block(meeting) if failed else None
    active = meeting.status in services.ACTIVE
    decisions_count, review_open_count = _workspace_counts(meeting, session) if ready else (0, 0)
    review_open_count = review_open_count if owner else 0  # review items are owner-only
    task_rows = tasks.meeting_tasks(session, user, meeting.id) if ready else []
    return MeetingDetail(
        **_summary(
            meeting,
            services.template_names(session, [meeting])[meeting.template_id],
            sum(1 for task in task_rows if not task.suggested),  # a suggestion is not counted
        ).model_dump(),
        failure_reason=(meeting.failure_reason or REASON_UNEXPECTED) if failed else None,
        retry_block=block,
        retry_block_message=services.retry_block_message(meeting, block),
        can_cancel=owner and services.can_cancel(meeting),
        can_end=owner and services.can_end(meeting),
        can_retry=owner and failed and block is None,
        can_regenerate=owner and ready,
        can_delete=owner and not active,
        shared_with_team=meeting.shared_team_id is not None,
        can_share=owner,
        shared_by=None if owner else _owner_name(session, meeting),
        join_attempts=meeting.join_attempts,
        max_join_attempts=MAX_JOIN_ATTEMPTS,
        bot_left_early=ready and meeting.completion_reason in REMOVAL_REASONS,
        speakers_incomplete=meeting.speakers_incomplete,
        refresh_after_seconds=(
            REFRESH_SECONDS if active or (block is not None and is_join_failure(meeting)) else None
        ),
        worker_stale=active and services.worker_is_stale(session),
        join_retry_at=join_retry_at(meeting),
        extraction_status=meeting.extraction_status if ready else None,
        notes=meeting.notes if ready else None,
        notes_html=str(services.render_markdown(meeting.notes)) if ready else None,
        transcript=meeting.speaker_transcript or meeting.transcript or None,
        duration_seconds=meeting.recorded_seconds,
        participants=_participants(meeting),
        decisions_count=decisions_count,
        review_open_count=review_open_count,
        tasks=api_tasks.present(session, user, task_rows),
        series=api_series.meeting_series(session, user, meeting),
        suggest_recurring=owner and ready and series_rules.reused_code(session, meeting),
        open_questions=api_series.meeting_questions(session, user, meeting) if ready else [],
        translations=[
            api_translation.brief(row, meeting)
            for row in session.scalars(
                select(MeetingTranslation)
                .where(MeetingTranslation.meeting_id == meeting.id)
                .order_by(MeetingTranslation.language)
            )
        ]
        if ready
        else [],
    )


@router.get("/meetings", tags=["meetings"])
def list_meetings(
    limit: int = Query(LIST_DEFAULT, ge=1, le=LIST_MAX),
    before_id: int | None = Query(None, ge=1, le=services.MAX_ID),
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> MeetingList:
    """The caller's meetings, plus any shared with their team, newest first. For the next page
    pass `before_id` = `next_before_id`."""
    rows = services.list_meetings(session, user, limit + 1, before_id)
    page = rows[:limit]
    names = services.template_names(session, page)
    counts = tasks.counts_for_meetings(session, user, [m.id for m in page])
    return MeetingList(
        meetings=[
            _summary(meeting, names[meeting.template_id], counts.get(meeting.id, 0))
            for meeting in page
        ],
        has_more=len(rows) > limit,
        next_before_id=page[-1].id if len(rows) > limit else None,
        worker_stale=services.worker_is_stale(session),
        refresh_after_seconds=(
            REFRESH_SECONDS if any(m.status in services.ACTIVE for m in page) else None
        ),
    )


@router.get("/meetings/{meeting_id:id}", tags=["meetings"])
def get_meeting(
    meeting_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> MeetingDetail:
    return _detail(services.visible_meeting_or_404(session, user, meeting_id), session, user)


EXPORT_RESPONSES = {
    200: {
        "description": "The file. Built for this request and not stored.",
        "content": {
            "application/pdf": {"schema": {"type": "string", "format": "binary"}},
            export.FORMATS["docx"][0]: {"schema": {"type": "string", "format": "binary"}},
        },
    },
    404: {
        "model": ErrorResponse,
        "description": "No such meeting (or not one the caller may read)",
    },
    409: {
        "model": ErrorResponse,
        "description": "`not_ready`: the notes are not written yet; `translation_missing`: no "
        "finished, current translation in that language",
    },
    500: {"model": ErrorResponse, "description": "`export_failed`: the file could not be built"},
    503: {"model": ErrorResponse, "description": "`export_busy`: another file is being built"},
    504: {"model": ErrorResponse, "description": "`export_timeout`: it took too long"},
}


@router.get(
    "/meetings/{meeting_id:id}/export",
    tags=["meetings"],
    response_class=Response,
    responses=EXPORT_RESPONSES,
)
def export_meeting(
    meeting_id: int,
    format: Literal["pdf", "docx"] = Query("pdf"),
    transcript: bool = Query(False),
    language: Literal["en", "fr", "es"] = Query("en"),
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> Response:
    """The notes (and, with `transcript=true`, the full transcript) as a PDF or Word file.

    `language` `fr` or `es` builds it from the finished, current translation (409
    `translation_missing` when there is none: translate first); `en`, the default, is the
    original.

    Built when asked and never stored. One file is built at a time per server process: a second
    request meanwhile gets 503 `export_busy` with `Retry-After`. The file name is the meeting's
    title, cleaned, in `Content-Disposition`.
    """
    meeting = services.visible_meeting_or_404(session, user, meeting_id)
    if meeting.status != "ready" or not meeting.notes:
        raise Refused(409, "not_ready", "The notes are not ready yet.")
    title = _title(meeting)
    notes, words = meeting.notes, meeting.speaker_transcript or meeting.transcript
    if language != "en":
        row = session.scalars(
            select(MeetingTranslation).where(
                MeetingTranslation.meeting_id == meeting.id,
                MeetingTranslation.language == language,
            )
        ).first()
        name = translation.language_name(language)
        if row is not None and translation.is_stale(row, meeting):
            raise Refused(
                409,
                "translation_missing",
                f"The {name} translation is out of date. Translate again first.",
            )
        if row is None or row.status != "ready":
            raise Refused(409, "translation_missing", f"Translate the notes to {name} first.")
        notes, words = row.notes_md, row.transcript
    doc = build_notes_document(
        title=title,
        platform_label=_platform_label(meeting),
        started_at=meeting.created_at,
        recorded_seconds=meeting.recorded_seconds,
        notes=notes,
        transcript=words if transcript else None,
        generated_at=datetime.now(UTC),
    )
    session.close()  # the build can take seconds: do not hold a database connection meanwhile
    media_type, extension = export.FORMATS[format]
    try:
        content = export.build_file(doc, format)
    except export.ExportBusy:
        return error_response(
            503,
            "export_busy",
            "Another file is being prepared. Try again in a few seconds.",
            headers={"Retry-After": "5"},
        )
    except export.ExportFailed as failure:
        if failure.timed_out:
            return error_response(
                504,
                "export_timeout",
                "Preparing the file took too long. Try again, or leave the transcript out.",
            )
        return error_response(500, "export_failed", "The file could not be prepared. Try again.")
    return Response(
        content,
        media_type=media_type,
        headers={
            "Content-Disposition": content_disposition(export_filename(title, extension)),
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post(
    "/meetings",
    status_code=201,
    tags=["meetings"],
    responses={code: {"model": ErrorResponse} for code in (400, 403, 409, 502)},
)
def create_meeting(
    body: NewMeeting,
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
    vexa=Depends(get_vexa),
) -> MeetingDetail:
    """Send the bot to a Google Meet or Zoom link, through the same placement as every other way a
    bot is sent. 403 `hours_cap_reached` once the account has used its recorded hours. 409 for the
    rest: `user_limit` (the account is at the most bots allowed at once), `already_in_meeting` (a
    bot of the account's is already in that meeting), `meeting_has_note_taker` (another account's
    bot is in that meeting; the text names nobody) and `bot_busy` (the note taker is full, or a
    placement is under way; the text never says whose meeting holds it). 502 `bot_send_failed`
    when the bot host did not take the bot."""
    meeting = services.create_meeting(
        session, vexa, user, body.url, body.template_id, request.app.state.settings
    )
    response.headers["Location"] = f"/api/meetings/{meeting.id}"
    return _detail(meeting, session, user)


@router.delete(
    "/meetings/{meeting_id:id}",
    status_code=204,
    tags=["meetings"],
    responses={code: {"model": ErrorResponse} for code in (404, 409, 502)},
)
def delete_meeting(
    meeting_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
    vexa=Depends(get_vexa),
) -> Response:
    """Delete a meeting and everything taken from it: transcript, notes, translations, decisions,
    open questions, review items, notifications, mail still waiting to be sent, the audio and
    Vexa's copy of it. The recorded hours stay counted. Only the one who recorded it (a teammate
    or anyone else gets 404). 409 `meeting_active` while the bot is in the call or the notes are
    being written, 409 `meeting_busy` when the worker holds it (try again), 502
    `recording_delete_failed` when Vexa would not remove the recording: nothing is deleted."""
    meeting = services.owned_meeting_or_404(session, user, meeting_id)
    services.delete_meeting(session, vexa, meeting)
    return Response(status_code=204)


@router.post(
    "/meetings/{meeting_id:id}/share",
    tags=["meetings"],
    responses={
        404: {"model": ErrorResponse, "description": "No such meeting (or not the caller's)"}
    },
)
def share_meeting(
    meeting_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> MeetingDetail:
    """Share the meeting with the caller's team (the "Share with team" switch): teammates may read
    its notes, transcript and tasks, never change it. Only the one who recorded it (a teammate or
    anyone else gets 404). Doing it twice is fine. The team's default (`share_new_meetings`) is
    read once, when the notes are first written; this is how a person shares one by hand."""
    meeting = services.owned_meeting_or_404(session, user, meeting_id)
    team = teams.ensure_team(session, user)
    meeting.shared_team_id = team.id
    session.commit()
    return _detail(meeting, session, user)


@router.delete(
    "/meetings/{meeting_id:id}/share",
    tags=["meetings"],
    responses={
        404: {"model": ErrorResponse, "description": "No such meeting (or not the caller's)"}
    },
)
def unshare_meeting(
    meeting_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> MeetingDetail:
    """Make the meeting private again: teammates lose it at once. Only the owner; 404 for anyone
    else. Doing it twice is fine."""
    meeting = services.owned_meeting_or_404(session, user, meeting_id)
    meeting.shared_team_id = None
    session.commit()
    return _detail(meeting, session, user)


@router.post(
    "/meetings/{meeting_id:id}/cancel",
    tags=["meetings"],
    responses={code: {"model": ErrorResponse} for code in (404, 409, 502)},
)
def cancel_meeting(
    meeting_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
    vexa=Depends(get_vexa),
) -> MeetingDetail:
    """Take the bot out of a meeting that is still `bot_sent` and discard it. Only when `can_cancel`
    is true: once the bot has been ended with `POST /end` (the notes are being written) it is 409
    `already_ending`, and while the bot is still starting (its send got no clear answer; the page
    reads "Starting the bot") it is 409 `bot_starting`: try again in a minute; 409 `meeting_busy`
    when the worker holds the meeting, try again."""
    meeting = services.owned_meeting_or_404(session, user, meeting_id)
    return _detail(services.cancel_meeting(session, vexa, meeting), session, user)


@router.post(
    "/meetings/{meeting_id:id}/end",
    tags=["meetings"],
    responses={code: {"model": ErrorResponse} for code in (404, 409, 502)},
)
def end_meeting(
    meeting_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
    vexa=Depends(get_vexa),
) -> MeetingDetail:
    """ "End now and write the notes": take the bot out of the call and write the notes from what
    it recorded. Only when `can_end` is true (409 `not_endable` otherwise, 409 `already_ending`
    when it was asked already, 409 `meeting_busy` when the worker holds the meeting: try again).
    The meeting stays `bot_sent` while the bot leaves, then follows the usual steps; it is not a
    cancel and nothing is discarded."""
    meeting = services.owned_meeting_or_404(session, user, meeting_id)
    return _detail(services.end_meeting(session, vexa, meeting), session, user)


@router.post(
    "/meetings/{meeting_id:id}/retry",
    tags=["meetings"],
    responses={code: {"model": ErrorResponse} for code in (403, 404, 409, 502)},
)
def retry_meeting(
    meeting_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
    vexa=Depends(get_vexa),
) -> MeetingDetail:
    """Resume a failed meeting at the step that failed. Only when `can_retry` is true. For a
    meeting whose join the worker ended (Google refused the bot, or it hung on a prompt) this
    sends a new bot instead, no sooner than 5 minutes after the failure."""
    meeting = services.owned_meeting_or_404(session, user, meeting_id)
    return _detail(services.retry_meeting(session, meeting, vexa), session, user)


@router.post(
    "/meetings/{meeting_id:id}/regenerate",
    tags=["meetings"],
    responses={code: {"model": ErrorResponse} for code in (400, 404, 409, 502)},
)
def regenerate_notes(
    meeting_id: int,
    body: Regenerate,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
    openai_client=Depends(get_openai_client),
) -> MeetingDetail:
    """Rewrite the notes of a `ready` meeting with a template. Runs in the request: allow 5 min."""
    meeting = services.owned_meeting_or_404(session, user, meeting_id)
    return _detail(
        services.regenerate_notes(session, meeting, body.template_id, openai_client),
        session,
        user,
    )


# ---- templates ----


@router.get("/templates", tags=["templates"])
def list_templates(
    session: Session = Depends(get_session), user: User = Depends(auth.current_user)
) -> TemplateList:
    """The caller's templates, sorted by name."""
    rows = services.list_templates(session, user)
    return TemplateList(templates=[TemplateOut.model_validate(row) for row in rows])


@router.post(
    "/templates",
    status_code=201,
    tags=["templates"],
    responses={400: {"model": ErrorResponse}},
)
def create_template(
    body: TemplateIn,
    response: Response,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> TemplateOut:
    template = services.save_template(session, user, None, body.name, body.instructions)
    response.headers["Location"] = f"/api/templates/{template.id}"
    return TemplateOut.model_validate(template)


@router.put(
    "/templates/{template_id:id}",
    tags=["templates"],
    responses={code: {"model": ErrorResponse} for code in (400, 404)},
)
def update_template(
    template_id: int,
    body: TemplateIn,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> TemplateOut:
    """Replace the name and instructions."""
    template = services.template_or_404(session, user, template_id)
    services.save_template(session, user, template, body.name, body.instructions)
    return TemplateOut.model_validate(template)


@router.delete(
    "/templates/{template_id:id}",
    status_code=204,
    tags=["templates"],
    responses={code: {"model": ErrorResponse} for code in (404, 409)},
)
def delete_template(
    template_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> Response:
    """Delete a template. Meetings written with it keep their notes and the template name they
    show; the calendar setting that pointed at it moves to the oldest template left. 409
    `last_template` for the only one."""
    services.delete_template(session, user, template_id)
    return Response(status_code=204)


# ---- vocabulary ----


@router.get("/vocabulary", tags=["vocabulary"])
def list_terms(
    session: Session = Depends(get_session), user: User = Depends(auth.current_user)
) -> TermList:
    """The caller's terms, sorted alphabetically."""
    return TermList(
        terms=[TermOut(id=row.id, term=row.term) for row in services.list_terms(session, user)]
    )


@router.post(
    "/vocabulary",
    status_code=201,
    tags=["vocabulary"],
    responses={400: {"model": ErrorResponse}},
)
def add_term(
    body: NewTerm,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> TermOut:
    row = services.add_term(session, user, body.term)
    return TermOut(id=row.id, term=row.term)


@router.delete(
    "/vocabulary/{term_id:id}",
    status_code=204,
    tags=["vocabulary"],
    responses={404: {"model": ErrorResponse}},
)
def delete_term(
    term_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> Response:
    if not services.delete_term(session, user, term_id):
        raise StarletteHTTPException(404, "No such term")
    return Response(status_code=204)


router.include_router(api_accounts.router)
router.include_router(api_admin.router)
router.include_router(api_settings.router)
router.include_router(api_integrations.router)
router.include_router(calendar_api.router)
router.include_router(calendar_recurring.router)
router.include_router(api_workspace.router)
router.include_router(api_tasks.router)
router.include_router(api_series.router)
router.include_router(api_projects.router)
router.include_router(api_sprints.router)
router.include_router(api_team.router)
router.include_router(api_translation.router)
router.include_router(api_ask.router)
router.include_router(api_shares.router)
