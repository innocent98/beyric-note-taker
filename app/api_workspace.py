"""The Decisions and Review screens' JSON routes. Included into `app.api.router`, so they share its
`/api` prefix, login check, CSRF rule and error shape.

Everything here is scoped to the caller through the meeting's owner (a foreign id is 404, never
403). What they show is read out of the notes by code (`app/workspace.py`): no model call happens
in a request.
"""

from typing import Literal

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import auth, services, workspace
from app.api_schemas import (
    DecisionGroup,
    DecisionList,
    DecisionOut,
    ErrorResponse,
    MeetingRef,
    ReviewItemOut,
    ReviewList,
    ReviewTermIn,
    ReviewTermOut,
    TermOut,
)
from app.db import get_session
from app.models import Meeting, ReviewItem, User

router = APIRouter()


def _ref(meeting: Meeting) -> MeetingRef:
    return MeetingRef(
        id=meeting.id,
        title=services.meeting_title(meeting),
        platform=meeting.platform,
        created_at=meeting.created_at,
    )


def _item(item: ReviewItem, meeting: Meeting) -> ReviewItemOut:
    return ReviewItemOut(
        id=item.id,
        text=item.text,
        heard_as=item.heard_as,
        checked_at=item.checked_at,
        meeting=_ref(meeting),
    )


@router.get("/decisions", tags=["decisions"])
def list_decisions(
    q: str | None = Query(None, max_length=workspace.MAX_SEARCH_CHARS),
    limit: int = Query(
        workspace.DECISIONS_DEFAULT_MEETINGS, ge=1, le=workspace.DECISIONS_MAX_MEETINGS
    ),
    before_id: int | None = Query(None, ge=1, le=services.MAX_ID),
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> DecisionList:
    """Everything the caller's meetings decided, newest meeting first, grouped by meeting. `q`
    matches a decision's words or its meeting's title (a title match shows all of that meeting's
    decisions). `limit` counts meetings, not decisions. For the next page pass `before_id` =
    `next_before_id`. A decision the caller hid is not listed."""
    groups, more = workspace.list_decisions(session, user, q, limit, before_id)
    return DecisionList(
        groups=[
            DecisionGroup(
                meeting=_ref(meeting),
                decisions=[DecisionOut(id=d.id, text=d.text) for d in decisions],
            )
            for meeting, decisions in groups
        ],
        has_more=more,
        next_before_id=groups[-1][0].id if more else None,
    )


@router.post(
    "/decisions/{decision_id:id}/hide",
    status_code=204,
    tags=["decisions"],
    responses={404: {"model": ErrorResponse}},
)
def hide_decision(
    decision_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> Response:
    """Remove a decision from the log. It is hidden, never edited, and stays hidden when the
    meeting's notes are rewritten. Hiding it again is 204 too."""
    if not workspace.hide_decision(session, user, decision_id):
        raise StarletteHTTPException(404, "No such decision")
    return Response(status_code=204)


@router.get("/review", tags=["review"])
def list_review(
    state: Literal["open", "checked"] = "open",
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> ReviewList:
    """The caller's "Please check" items from their meetings: `state=open` (default) or
    `checked`. Newest meeting first, in the order of the notes."""
    rows, truncated = workspace.list_review(session, user, checked=state == "checked")
    return ReviewList(
        items=[_item(item, meeting) for item, meeting in rows],
        open_count=workspace.open_review_count(session, user),
        truncated=truncated,
    )


def _review_or_404(session: Session, user: User, item_id: int) -> tuple[ReviewItem, Meeting]:
    found = workspace.review_item(session, user, item_id)
    if found is None:
        raise StarletteHTTPException(404, "No such review item")
    return found


@router.post(
    "/review/{item_id:id}/check",
    tags=["review"],
    responses={404: {"model": ErrorResponse}},
)
def check_review_item(
    item_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> ReviewItemOut:
    """Mark an item checked. Checking it again keeps the first time."""
    item, meeting = _review_or_404(session, user, item_id)
    workspace.set_checked(session, item, True)
    return _item(item, meeting)


@router.post(
    "/review/{item_id:id}/uncheck",
    tags=["review"],
    responses={404: {"model": ErrorResponse}},
)
def uncheck_review_item(
    item_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> ReviewItemOut:
    """Put a checked item back among the open ones."""
    item, meeting = _review_or_404(session, user, item_id)
    workspace.set_checked(session, item, False)
    return _item(item, meeting)


@router.post(
    "/review/{item_id:id}/vocabulary",
    status_code=201,
    tags=["review"],
    responses={code: {"model": ErrorResponse} for code in (400, 404, 409)},
)
def add_review_term(
    item_id: int,
    body: ReviewTermIn,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> ReviewTermOut:
    """Add the corrected spelling to the caller's vocabulary and mark the item checked. 400
    `invalid_term` (nothing changes). 409 `duplicate_term` when the term is already there; the
    item is still marked checked. The notes of this meeting are not rewritten: offer "rewrite
    the notes" (`POST /meetings/{id}/regenerate`) afterwards."""
    item, meeting = _review_or_404(session, user, item_id)
    term = services.add_term_from_review(session, user, item, body.term)
    return ReviewTermOut(item=_item(item, meeting), term=TermOut.model_validate(term))
