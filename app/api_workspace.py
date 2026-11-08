"""The Decisions and Review screens' JSON routes. Included into `app.api.router`, so they share its
`/api` prefix, login check, CSRF rule and error shape.

Everything here is scoped to the caller through the meeting's owner (a foreign id is 404, never
403). What they show is read out of the notes by code (`app/workspace.py`): no model call happens
in a request.
"""

from typing import Literal

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import auth, services, tasks, workspace
from app.api_schemas import (
    DecisionCounts,
    DecisionGroup,
    DecisionList,
    DecisionOut,
    DecisionPatch,
    DecisionRef,
    DecisionStatus,
    DecisionTaskRef,
    ErrorResponse,
    MeetingRef,
    ReviewItemOut,
    ReviewList,
    ReviewTermIn,
    ReviewTermOut,
    TermOut,
)
from app.db import get_session
from app.extraction import TranscriptIndex
from app.models import Decision, DecisionTask, Meeting, ReviewItem, Task, User
from app.visibility import can_read_clause, owns

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


def _present_decisions(
    session: Session, user: User, rows: list[tuple[Decision, Meeting]]
) -> list[DecisionOut]:
    """The API shape of decisions the caller may read, with what they link to: only the tasks the
    caller may see (never a suggested one), only the decisions they may read, and where the quote
    was said in the transcript the meeting page shows. A few queries for the whole page."""
    ids = [decision.id for decision, _ in rows]
    related: dict[int, list[DecisionTaskRef]] = {i: [] for i in ids}
    if ids:
        linked = session.execute(
            select(DecisionTask.decision_id, Task)
            .join(Task, Task.id == DecisionTask.task_id)
            .where(DecisionTask.decision_id.in_(ids), tasks.visible(user), tasks.confirmed())
            .order_by(DecisionTask.decision_id, Task.position, Task.id)
        )
        for decision_id, task in linked:
            related[decision_id].append(
                DecisionTaskRef(id=task.id, title=task.title, status=task.status)
            )
    named = {d.replaced_by_id for d, _ in rows if d.replaced_by_id}
    readable = (
        {
            d.id: d
            for d in session.scalars(
                select(Decision)
                .join(Meeting)
                .where(
                    Decision.hidden_at.is_(None),
                    can_read_clause(user),
                    (Decision.id.in_(named)) | (Decision.replaced_by_id.in_(ids)),
                )
                .order_by(Decision.meeting_id.desc(), Decision.position)
            )
        }
        if ids
        else {}
    )
    replaces: dict[int, list[DecisionRef]] = {i: [] for i in ids}
    for other in readable.values():
        if other.replaced_by_id in replaces and other.status == "replaced":
            replaces[other.replaced_by_id].append(DecisionRef(id=other.id, text=other.text))
    indexes: dict[int, TranscriptIndex] = {}
    out = []
    for decision, meeting in rows:
        said = None
        if decision.quote:
            if meeting.id not in indexes:  # each transcript is read once per request
                indexes[meeting.id] = TranscriptIndex(
                    meeting.speaker_transcript or meeting.transcript or ""
                )
            said = indexes[meeting.id].locate(decision.quote)
        target = readable.get(decision.replaced_by_id) if decision.replaced_by_id else None
        out.append(
            DecisionOut(
                id=decision.id,
                text=decision.text,
                status=decision.status,
                decided_by=decision.decided_by,
                quote=decision.quote,
                quote_speaker=said[0] if said else None,
                line=said[1] if said else None,
                revisit_on=decision.revisit_on,
                replaced_by=(
                    DecisionRef(id=target.id, text=target.text)
                    if target is not None and decision.status == "replaced"
                    else None
                ),
                replaces=replaces[decision.id],
                related_tasks=related[decision.id],
                can_edit=owns(user, meeting),
            )
        )
    return out


@router.get("/decisions", tags=["decisions"], responses={422: {"model": ErrorResponse}})
def list_decisions(
    q: str | None = Query(None, max_length=workspace.MAX_SEARCH_CHARS),
    status: DecisionStatus | None = None,
    limit: int = Query(
        workspace.DECISIONS_DEFAULT_MEETINGS, ge=1, le=workspace.DECISIONS_MAX_MEETINGS
    ),
    before_id: int | None = Query(None, ge=1, le=services.MAX_ID),
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> DecisionList:
    """Everything the caller's meetings decided, newest meeting first, grouped by meeting. `q`
    matches a decision's words or its meeting's title (a title match shows all of that meeting's
    decisions). `status` keeps one tab's decisions. `limit` counts meetings, not decisions. For
    the next page pass `before_id` = `next_before_id`. A decision the caller hid is not listed.
    `counts` and `due_this_week` are over the whole log, whatever `q` and `status` say."""
    groups, more = workspace.list_decisions(session, user, q, limit, before_id, status)
    flat = [(decision, meeting) for meeting, decisions in groups for decision in decisions]
    shown = iter(_present_decisions(session, user, flat))
    return DecisionList(
        groups=[
            DecisionGroup(meeting=_ref(meeting), decisions=[next(shown) for _ in decisions])
            for meeting, decisions in groups
        ],
        has_more=more,
        next_before_id=groups[-1][0].id if more else None,
        counts=DecisionCounts(**workspace.decision_counts(session, user)),
        due_this_week=workspace.due_this_week(session, user),
    )


@router.patch(
    "/decisions/{decision_id:id}",
    tags=["decisions"],
    responses={403: {"model": ErrorResponse, "description": "`not_owner`"}}
    | {code: {"model": ErrorResponse} for code in (404, 422)},
)
def patch_decision(
    decision_id: int,
    body: DecisionPatch,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> DecisionOut:
    """Change a decision's status, revisit date or what replaced it. Only the person who recorded
    the meeting may: 403 `not_owner` for a teammate who can read it, 404 for someone who cannot.
    `replaced_by_id` must be another decision of the caller's log, only with status `replaced`
    (422 `replaced_by_needs_replaced`, `replaced_by_self`, `replaced_by_not_found`,
    `replaced_by_cycle`). Moving a decision off `replaced` clears `replaced_by_id`. A change a
    person made is never overwritten when the notes are read again."""
    found = workspace.readable_decision(session, user, decision_id)
    if found is None:
        raise StarletteHTTPException(404, "No such decision")
    decision, meeting = found
    changes = {k: getattr(body, k) for k in body.model_fields_set}
    if changes.get("status", "") is None:
        raise services.Refused(422, "validation_error", "Status cannot be null.")
    try:
        workspace.update_decision(
            session,
            user,
            decision,
            meeting,
            **{k: v for k, v in changes.items()},
        )
    except workspace.DecisionForbidden:
        raise services.Refused(
            403, "not_owner", "Only the person who recorded the meeting can change this."
        ) from None
    except workspace.DecisionInvalid as exc:
        raise services.Refused(422, exc.code, exc.message) from None
    return _present_decisions(session, user, [(decision, meeting)])[0]


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
