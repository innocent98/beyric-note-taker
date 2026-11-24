"""The draft routes of meeting recipes (plan T6). Included into `app.api.router`, so they share its
`/api` prefix, login check, CSRF rule and error shape.

A request only queues the work; the worker's recipe thread writes the draft
(`app/recipes/service.py`), and the page asks again every 2 seconds while the status is `writing`.
No model call happens in a request. A draft is private to its creator: another person's draft is a
404, never a 403. Regenerating is a new `POST` (a new draft); discarding is `DELETE`.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import auth, services, team_admin
from app.api_schemas import (
    BlankOut,
    DocumentBrief,
    DocumentChecks,
    DocumentIn,
    DocumentList,
    DocumentOut,
    DocumentPatch,
    ErrorResponse,
    FlagOut,
)
from app.db import get_session
from app.models import Document, DocumentMeeting, User
from app.recipes import blocks as blocks_mod
from app.recipes import service

router = APIRouter()

MAX_LISTED = 100


def _meeting_ids(session: Session, document_id: int) -> list[int]:
    return sorted(
        session.scalars(
            select(DocumentMeeting.meeting_id).where(DocumentMeeting.document_id == document_id)
        )
    )


def _brief(session: Session, doc: Document) -> DocumentBrief:
    return DocumentBrief(
        id=doc.id,
        title=doc.title,
        output_type=doc.output_type,
        recipe_key=doc.recipe_key,
        recipe_id=doc.recipe_id,
        status=doc.status,
        edited=doc.edited,
        meeting_ids=_meeting_ids(session, doc.id),
        created_at=doc.created_at,
        updated_at=doc.updated_at,
    )


def _out(session: Session, user: User, doc: Document) -> DocumentOut:
    seen = service.reading(session, user, doc)
    ready = doc.status == "ready"
    failed = doc.status == "failed"
    return DocumentOut(
        **_brief(session, doc).model_dump(),
        blocks=blocks_mod.load_blocks(doc.blocks) if ready else [],
        checks=DocumentChecks(
            blanks=[BlankOut(block_index=b.block_index, what=b.what) for b in seen.blanks],
            flags=[
                FlagOut(block_index=f.block_index, text=f.text, kind=f.kind) for f in seen.flags
            ],
        ),
        stale=seen.stale,
        sources_available=seen.sources_available,
        left_out_count=doc.left_out_count,
        failure_code=doc.failure_code if failed else None,
        failure_message=service.failure_message(doc.failure_code) if failed else None,
        options=doc.options or {},
        use_brief=doc.use_brief,
    )


def document_or_404(session: Session, user: User, document_id: int) -> Document:
    """The caller's own draft. Another person's, or none, is a 404 (slice S4 widens this to a
    draft shared with the caller's team)."""
    doc = None
    if 1 <= document_id <= services.MAX_ID:
        doc = session.scalars(
            select(Document).where(Document.id == document_id, Document.owner_user_id == user.id)
        ).first()
    if doc is None:
        raise services.Refused(404, "not_found", "No such document.")
    return doc


def _sources_in(blocks: list) -> set[tuple]:
    """The sources a draft already has: an edit may keep them and may not invent new ones."""
    keys: set[tuple] = set()

    def add(sources):
        for s in sources:
            if s.kind == "line":
                keys.add(blocks_mod.line_key(s.meeting_id, s.line))
            elif s.kind == "meeting":
                keys.add(blocks_mod.meeting_key(s.meeting_id))
            else:
                keys.add(blocks_mod.ref_key(s.kind, s.meeting_id, s.ref))

    for block in blocks:
        add(getattr(block, "sources", []))
        for item in getattr(block, "items", []):
            add(item.sources)
        for row in getattr(block, "rows", []):
            for cell in row:
                add(cell.sources)
        for card in getattr(block, "cards", []):
            add(card.sources)
    return keys


CREATE_RESPONSES = {
    202: {"description": "Queued (or the draft just asked for is returned): poll `GET` every 2 s."},
    403: {
        "model": ErrorResponse,
        "description": "Missing or wrong X-CSRF-Token; or `plan_inactive`: making documents needs "
        "a live plan or trial (only when the server runs `FEATURE_GATES_MODE=enforce`)",
    },
    404: {
        "model": ErrorResponse,
        "description": "No such recipe, team or meeting (a meeting the caller cannot read too)",
    },
    409: {
        "model": ErrorResponse,
        "description": "`not_ready`: the meeting's notes are not written",
    },
    422: {
        "model": ErrorResponse,
        "description": "`recipe_invalid` (name exactly one recipe) or `meetings_invalid`",
    },
    429: {
        "model": ErrorResponse,
        "description": "`recipe_limit`: 20 drafts a day; failed ones and regenerations count",
    },
}


@router.post("/documents", status_code=202, tags=["documents"], responses=CREATE_RESPONSES)
def create_document(
    body: DocumentIn,
    team_id: int | None = Depends(team_admin.requested_team),
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> DocumentOut:
    """Ask for a draft made from a meeting (or several, for a cross-meeting recipe) by a recipe.
    Answers 202 with `status: "writing"`; the worker writes it and the page polls
    `GET /api/documents/{id}`. The team is the `X-Team-Id` header (404 for one the caller is not
    in), else their default team: it decides whose brief and team recipes are used. Asking again
    for the same recipe, meetings and options while that draft is still being written returns
    that draft and starts nothing. Regenerating is a new request and a new draft."""
    if (body.recipe_key is None) == (body.recipe_id is None):
        raise services.Refused(
            422, "recipe_invalid", "Name the recipe with exactly one of recipe_key and recipe_id."
        )
    doc = service.request_draft(
        session,
        user,
        recipe_ref=body.recipe_key if body.recipe_key is not None else body.recipe_id,
        meeting_ids=body.meeting_ids,
        options=body.options,
        team_id=team_id,
        use_brief=body.use_brief,
        picker=body.picker,
        now=datetime.now(UTC),
    )
    return _out(session, user, doc)


@router.get(
    "/documents/{document_id:id}", tags=["documents"], responses={404: {"model": ErrorResponse}}
)
def get_document(
    document_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> DocumentOut:
    """A draft: its `status` (poll every 2 seconds while `writing`), and when `ready` its `blocks`.
    `checks` is the audit of the blocks as they stand now: blanks to fill and figures to check.
    404 for another person's draft."""
    return _out(session, user, document_or_404(session, user, document_id))


@router.get("/documents", tags=["documents"])
def list_documents(
    meeting_id: int | None = Query(default=None, ge=1, le=services.MAX_ID),
    before_id: int | None = Query(default=None, ge=1, le=services.MAX_ID),
    limit: int = Query(default=50, ge=1, le=MAX_LISTED),
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> DocumentList:
    """The caller's own drafts, newest first; with `meeting_id`, only those made from that meeting
    (cross-meeting drafts included). For the next page pass the smallest `id` as `before_id`."""
    query = select(Document).where(Document.owner_user_id == user.id)
    if meeting_id is not None:
        query = query.where(
            Document.id.in_(
                select(DocumentMeeting.document_id).where(DocumentMeeting.meeting_id == meeting_id)
            )
        )
    if before_id is not None:
        query = query.where(Document.id < before_id)
    rows = session.scalars(query.order_by(Document.id.desc()).limit(limit))
    return DocumentList(documents=[_brief(session, doc) for doc in rows])


@router.patch(
    "/documents/{document_id:id}",
    tags=["documents"],
    responses={
        404: {"model": ErrorResponse},
        409: {
            "model": ErrorResponse,
            "description": "`writing`: the draft is still being written; `not_ready`: it failed",
        },
        422: {"model": ErrorResponse, "description": "`blocks_invalid`"},
    },
)
def edit_document(
    document_id: int,
    body: DocumentPatch,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> DocumentOut:
    """Save the edited draft: send every block as it now stands. Text is cleaned and bounded as
    at generation; a block keeps the sources it had and cannot gain new ones. Marks the draft
    `edited`. The `checks` in the answer are recomputed from what was saved. Refused while the
    draft is still `writing` (409 `writing`) and for one that `failed` (409 `not_ready`)."""
    doc = document_or_404(session, user, document_id)
    if doc.status == "writing":
        raise services.Refused(409, "writing", "This draft is still being written.")
    if doc.status != "ready":
        raise services.Refused(409, "not_ready", "This draft failed; make a new one.")
    try:
        current = blocks_mod.load_blocks(doc.blocks)
        edited = blocks_mod.parse_blocks(body.blocks, allowed_sources=_sources_in(current))
    except blocks_mod.BlocksInvalid as exc:
        raise services.Refused(
            422, "blocks_invalid", "Those blocks cannot be saved. Check the tables and try again."
        ) from exc
    doc.blocks = blocks_mod.dump_blocks(edited)
    if body.title is not None:
        title = blocks_mod.plain(body.title, 200)
        if title:
            doc.title = title
    doc.edited, doc.updated_at = True, datetime.now(UTC)
    session.commit()
    return _out(session, user, doc)


@router.delete(
    "/documents/{document_id:id}",
    status_code=204,
    tags=["documents"],
    responses={404: {"model": ErrorResponse}},
)
def delete_document(
    document_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> Response:
    """Discard a draft (also cancels one still being written; the model call it may have made is
    still counted against the day's 20)."""
    session.delete(document_or_404(session, user, document_id))
    session.commit()
    return Response(status_code=204)
