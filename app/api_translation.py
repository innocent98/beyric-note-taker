"""The Translate routes and the default notes language (spec section 11). Included into
`app.api.router`, so they share its `/api` prefix, login check, CSRF rule and error shape.

Owner only: another account's meeting is a 404, never a 403. A request only queues the work; the
worker's translation thread does it (`app/translation.py`), and the page asks again every 2
seconds while the status is `translating`. No model call happens in a request.
"""

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import auth, services, translation
from app.api_schemas import (
    ErrorResponse,
    TranslationBrief,
    TranslationIn,
    TranslationOut,
)
from app.db import get_session
from app.models import Meeting, MeetingTranslation, User

router = APIRouter()


def brief(row: MeetingTranslation, meeting: Meeting) -> TranslationBrief:
    return TranslationBrief(
        language=row.language, status=row.status, stale=translation.is_stale(row, meeting)
    )


def _out(row: MeetingTranslation, meeting: Meeting) -> TranslationOut:
    ready = row.status == "ready"
    return TranslationOut(
        language=row.language,
        status=row.status,
        stale=translation.is_stale(row, meeting),
        notes=row.notes_md if ready else None,
        notes_html=str(translation.render_translated(row.notes_md)) if ready else None,
        transcript=row.transcript if ready else None,
        failure_code=row.failure_code if row.status == "failed" else None,
        failure_message=translation.failure_message(row.failure_code)
        if row.status == "failed"
        else None,
        updated_at=row.updated_at,
    )


def _existing(session: Session, meeting: Meeting, language: str) -> MeetingTranslation:
    row = None
    if language in translation.TARGETS:
        row = session.scalars(
            select(MeetingTranslation).where(
                MeetingTranslation.meeting_id == meeting.id,
                MeetingTranslation.language == language,
            )
        ).first()
    if row is None:
        raise services.Refused(404, "no_translation", "This meeting has no translation yet.")
    return row


TRANSLATE_RESPONSES = {
    200: {"description": "A finished translation of the current text, from the cache."},
    202: {"description": "Queued, or already being made: ask again every 2 seconds."},
    404: {"model": ErrorResponse, "description": "No such meeting (or not the caller's)"},
    409: {"model": ErrorResponse, "description": "`not_ready`: the notes are not written yet"},
    422: {"model": ErrorResponse, "description": "`language_unsupported`: only French or Spanish"},
    429: {"model": ErrorResponse, "description": "`translation_limit`: 20 translations a day"},
}


@router.post(
    "/meetings/{meeting_id:id}/translations",
    tags=["translations"],
    responses=TRANSLATE_RESPONSES,
)
def request_translation(
    meeting_id: int,
    body: TranslationIn,
    response: Response,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> TranslationOut:
    """Translate the notes and transcript of a ready meeting into French or Spanish. 200 with the
    translation when one is already cached for the current text; otherwise 202 with
    `status: "translating"` and the work is queued. A failed or stale translation asked for again
    is queued again. Asking again while it is being made changes nothing."""
    meeting = services.meeting_or_404(session, user, meeting_id)
    row, cached = translation.request_translation(session, user, meeting, body.language)
    response.status_code = 200 if cached else 202
    return _out(row, meeting)


@router.get(
    "/meetings/{meeting_id:id}/translations/{language}",
    tags=["translations"],
    responses={404: {"model": ErrorResponse}},
)
def get_translation(
    meeting_id: int,
    language: str,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> TranslationOut:
    """The translation in one language: its `status` (poll every 2 seconds while `translating`),
    and when `ready` the `notes`, `notes_html` and `transcript`. 404 `no_translation` when none
    was asked for, or for a language that is not offered."""
    meeting = services.meeting_or_404(session, user, meeting_id)
    return _out(_existing(session, meeting, language), meeting)


@router.delete(
    "/meetings/{meeting_id:id}/translations/{language}",
    status_code=204,
    tags=["translations"],
    responses={404: {"model": ErrorResponse}},
)
def delete_translation(
    meeting_id: int,
    language: str,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> Response:
    """Throw the translation away so it can be made again (also cancels one still queued). It does
    not give back one of the day's 20."""
    meeting = services.meeting_or_404(session, user, meeting_id)
    session.delete(_existing(session, meeting, language))
    session.commit()
    return Response(status_code=204)
