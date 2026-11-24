"""The draft routes of meeting recipes (plan T6). Included into `app.api.router`, so they share its
`/api` prefix, login check, CSRF rule and error shape.

A request only queues the work; the worker's recipe thread writes the draft
(`app/recipes/service.py`), and the page asks again every 2 seconds while the status is `writing`.
No model call happens in a request. A draft is private to its creator until they share it,
read-only, with their team (`POST /documents/{id}/share`): another person's draft is a 404, never a
403. Reading goes through `readable_document_or_404`, every write through `document_or_404` (the
creator only). Regenerating is a new `POST` (a new draft); discarding is `DELETE`.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import delete, exists, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app import auth, services, team_admin, teams
from app import visibility as meeting_visibility
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
    SharedTeam,
)
from app.db import get_session
from app.models import Document, DocumentMeeting, DocumentShare, Meeting, Team, User
from app.recipes import blocks as blocks_mod
from app.recipes import service
from app.recipes import visibility as doc_visibility

router = APIRouter()

MAX_LISTED = 100


def _meeting_ids(session: Session, document_id: int) -> list[int]:
    return sorted(
        session.scalars(
            select(DocumentMeeting.meeting_id).where(DocumentMeeting.document_id == document_id)
        )
    )


def _brief(session: Session, user: User, doc: Document) -> DocumentBrief:
    """A draft as a list row. For a reader of a shared draft the meeting ids are only the ones they
    may read, and the creator's name is shown; the teams it was shared with are the creator's."""
    ids = _meeting_ids(session, doc.id)
    mine = doc.owner_user_id == user.id
    shared_with: list[SharedTeam] = []
    owner_name = None
    if mine:
        team_ids = doc_visibility.shared_team_ids(session, doc)
        if team_ids:
            shared_with = [
                SharedTeam(id=t.id, name=t.name)
                for t in session.scalars(
                    select(Team).where(Team.id.in_(team_ids)).order_by(Team.id)
                )
            ]
    else:
        readable = doc_visibility.readable_sources(session, user, doc)
        ids = [i for i in ids if i in readable]
        owner = session.get(User, doc.owner_user_id)
        owner_name = meeting_visibility.former_or_name(owner) if owner is not None else None
    return DocumentBrief(
        id=doc.id,
        title=doc.title,
        output_type=doc.output_type,
        recipe_key=doc.recipe_key,
        recipe_id=doc.recipe_id,
        status=doc.status,
        edited=doc.edited,
        meeting_ids=ids,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
        access="owner" if mine else "shared",
        owner_name=owner_name,
        shared_with=shared_with,
    )


def _out(session: Session, user: User, doc: Document) -> DocumentOut:
    """The draft as `user` may see it. For the creator: everything. For a reader of a shared draft
    (`access: "shared"`): the text and its checks, with every source of a meeting they cannot read
    hidden, no left-out count and none of the creator's choices. The checks and `stale` are worked
    out as the creator would see them (they say something about the draft's own text, not about the
    meetings' words), so a reader is not told "check this" about a figure from a meeting they
    cannot open."""
    mine = doc.owner_user_id == user.id
    viewer = user if mine else session.get(User, doc.owner_user_id)
    seen = service.reading(session, viewer, doc)
    ready = doc.status == "ready"
    failed = doc.status == "failed"
    blocks = blocks_mod.load_blocks(doc.blocks) if ready else []
    sources_available = seen.sources_available
    if not mine:
        readable = doc_visibility.readable_sources(session, user, doc)
        blocks = doc_visibility.redact(blocks, readable)
        sources_available = bool(readable)
    return DocumentOut(
        **_brief(session, user, doc).model_dump(),
        blocks=blocks,
        checks=DocumentChecks(
            blanks=[BlankOut(block_index=b.block_index, what=b.what) for b in seen.blanks],
            flags=[
                FlagOut(block_index=f.block_index, text=f.text, kind=f.kind) for f in seen.flags
            ],
        ),
        stale=seen.stale,
        sources_available=sources_available,
        left_out_count=doc.left_out_count if mine else 0,
        failure_code=doc.failure_code if failed else None,
        failure_message=service.failure_message(doc.failure_code) if failed else None,
        options=(doc.options or {}) if mine else {},
        use_brief=doc.use_brief if mine else False,
        can_edit=mine,
    )


def _find(session: Session, document_id: int, *conditions) -> Document:
    doc = None
    if 1 <= document_id <= services.MAX_ID:
        doc = session.scalars(
            select(Document).where(Document.id == document_id, *conditions)
        ).first()
    if doc is None:
        raise services.Refused(404, "not_found", "No such document.")
    return doc


def document_or_404(session: Session, user: User, document_id: int) -> Document:
    """The caller's OWN draft: the one every write uses (edit, discard, share, the board's
    operations). Another person's, or none, is a 404, even for a draft shared with the caller."""
    return _find(session, document_id, Document.owner_user_id == user.id)


def readable_document_or_404(session: Session, user: User, document_id: int) -> Document:
    """A draft the caller may READ: their own, or one shared with a team they are in right now. The
    one every read uses (the draft itself and its exports); the rule is `recipes.visibility`."""
    return _find(session, document_id, doc_visibility.readable_clause(user))


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
    The creator reads it whole. A teammate reading a draft shared with their team (`access:
    "shared"`) gets the text with every source of a meeting they cannot read replaced by
    `{"kind": "hidden"}`, and `can_edit: false`. 404 for anyone else's draft, and for a shared one
    once it is unshared or the reader has left the team."""
    return _out(session, user, readable_document_or_404(session, user, document_id))


@router.get("/documents", tags=["documents"])
def list_documents(
    meeting_id: int | None = Query(default=None, ge=1, le=services.MAX_ID),
    mine: bool | None = Query(default=None),
    shared: bool | None = Query(default=None),
    before_id: int | None = Query(default=None, ge=1, le=services.MAX_ID),
    limit: int = Query(default=50, ge=1, le=MAX_LISTED),
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> DocumentList:
    """Drafts, newest first. With no filter, or `mine=true`: the caller's own. `shared=true`: drafts
    teammates shared with a team the caller is in now (never their own; an unshared one, or one of
    a team they left, is not listed). Both true: the two together. With `meeting_id`, only drafts
    made from that meeting (cross-meeting drafts included; a shared draft only when the caller may
    read that meeting). For the next page pass the smallest `id` as `before_id`."""
    want_mine = True if mine is None and not shared else bool(mine)
    want_shared = bool(shared)
    scopes = []
    if want_mine:
        scopes.append(Document.owner_user_id == user.id)
    if want_shared:
        scopes.append(doc_visibility.shared_clause(user))
    if not scopes:
        return DocumentList(documents=[])
    query = select(Document).where(or_(*scopes))
    if meeting_id is not None:
        query = query.where(
            Document.id.in_(
                select(DocumentMeeting.document_id).where(DocumentMeeting.meeting_id == meeting_id)
            )
        )
        if want_shared and not session.scalar(
            select(
                exists().where(Meeting.id == meeting_id, meeting_visibility.can_read_clause(user))
            )
        ):
            # A shared draft of a meeting the caller cannot read is not found by asking for it.
            query = query.where(Document.owner_user_id == user.id)
    if before_id is not None:
        query = query.where(Document.id < before_id)
    rows = session.scalars(query.order_by(Document.id.desc()).limit(limit))
    return DocumentList(documents=[_brief(session, user, doc) for doc in rows])


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


@router.post(
    "/documents/{document_id:id}/share",
    tags=["documents"],
    responses={
        404: {"model": ErrorResponse},
        409: {
            "model": ErrorResponse,
            "description": "`not_ready`: only a finished draft can be shared; `personal_team`: "
            "the active team is the caller's own space, which has no one to share with",
        },
    },
)
def share_document(
    document_id: int,
    team_id: int | None = Depends(team_admin.requested_team),
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> DocumentOut:
    """Share the draft, read-only, with the active team (`X-Team-Id`, else the caller's default
    team): every current member can then open it, and see its text, but not the meetings it was
    drafted from (a source they cannot read shows as hidden). Only the creator may; asking twice
    changes nothing. Leaving the team, or `DELETE .../share`, ends the access at once."""
    doc = document_or_404(session, user, document_id)
    if doc.status != "ready":
        raise services.Refused(409, "not_ready", "Only a finished draft can be shared.")
    member = team_admin.my_membership(session, user, team_id)
    team = session.get(Team, member.team_id)
    if teams.is_personal_of(team, user.id):
        raise services.Refused(
            409,
            "personal_team",
            "This is your own space, so there is no team to share with. Switch to a team first.",
        )
    session.execute(
        insert(DocumentShare)
        .values(document_id=doc.id, team_id=team.id, shared_by_user_id=user.id)
        .on_conflict_do_nothing(constraint="uq_document_shares_document_team")
    )
    session.commit()
    return _out(session, user, doc)


@router.delete(
    "/documents/{document_id:id}/share",
    status_code=204,
    tags=["documents"],
    responses={404: {"model": ErrorResponse}},
)
def unshare_document(
    document_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> Response:
    """Stop sharing the draft with every team. Takes effect at once; asking again changes nothing.
    Only the creator may."""
    doc = document_or_404(session, user, document_id)
    session.execute(delete(DocumentShare).where(DocumentShare.document_id == doc.id))
    session.commit()
    return Response(status_code=204)
