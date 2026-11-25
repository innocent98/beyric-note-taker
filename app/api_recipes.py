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
from app.models import (
    AiRun,
    Document,
    DocumentMeeting,
    DocumentShare,
    Meeting,
    Recipe,
    Team,
    User,
)
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


def _recipe_name(session: Session, doc: Document) -> str:
    if doc.recipe_id is not None:
        name = session.scalar(select(Recipe.name).where(Recipe.id == doc.recipe_id))
        if name:
            return name
    starter = service.starter_for(doc.recipe_key) if doc.recipe_key else None
    return starter.name if starter is not None else doc.output_type.capitalize()


def _generic_title(session: Session, doc: Document) -> str:
    """ "Proposal, 24 Nov": what a reader who cannot read every source meeting is shown instead of
    the model's title, which can carry the title of a meeting they may not know exists."""
    when = doc.created_at
    return f"{_recipe_name(session, doc)}, {when.day} {when.strftime('%b')}"


def _share_refusal(session: Session, user: User, doc: Document) -> services.Refused | None:
    """Why `doc` cannot be shared right now, or None. A draft is shared with the team it was made
    in, never another: the brief and the meetings it used belong to that team."""
    if doc.status != "ready":
        return services.Refused(409, "not_ready", "Only a finished draft can be shared.")
    team = session.get(Team, doc.team_id) if doc.team_id is not None else None
    if team is None or teams.is_personal_of(team, user.id):
        return services.Refused(
            409,
            "personal_team",
            "This draft was made in your own space. Make it in a team workspace to share it with "
            "that team.",
        )
    if teams.membership(session, user.id, team.id) is None:
        return services.Refused(
            409,
            "team_left",
            "You are no longer in the team this draft was made in, so it cannot be shared.",
        )
    return None


def _brief(session: Session, user: User, doc: Document) -> DocumentBrief:
    """A draft as a list row. For a reader of a shared draft the meeting ids are only the ones they
    may read, the creator's name is shown, and the title is generic unless they can read every
    source meeting. The teams it was shared with, and the team it was made in, are the creator's."""
    ids = _meeting_ids(session, doc.id)
    mine = doc.owner_user_id == user.id
    shared_with: list[SharedTeam] = []
    owner_name = None
    team = None
    title = doc.title
    if mine:
        team_ids = doc_visibility.shared_team_ids(session, doc)
        wanted = set(team_ids)
        if doc.team_id is not None:
            wanted.add(doc.team_id)
        found = {
            t.id: SharedTeam(id=t.id, name=t.name)
            for t in session.scalars(select(Team).where(Team.id.in_(wanted)).order_by(Team.id))
        }
        shared_with = [found[i] for i in team_ids if i in found]
        team = found.get(doc.team_id) if doc.team_id is not None else None
    else:
        readable = doc_visibility.readable_sources(session, user, doc)
        if len(readable) < len(ids):
            title = _generic_title(session, doc)
        ids = [i for i in ids if i in readable]
        owner = session.get(User, doc.owner_user_id)
        owner_name = meeting_visibility.former_or_name(owner) if owner is not None else None
    return DocumentBrief(
        id=doc.id,
        title=title,
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
        team=team,
    )


def _out(session: Session, user: User, doc: Document) -> DocumentOut:
    """The draft as `user` may see it. Every source of a meeting `user` cannot read (a reader who
    was never allowed it, or the creator who has lost access to it or whose meeting was deleted) is
    `{"kind": "hidden"}`. The checks and `stale` are worked out from what `user` can read, as `user`
    would see them; a reader who cannot read every source meeting gets `stale: false` (their
    partial text could only differ). A reader also gets no left-out count and none of the creator's
    choices."""
    mine = doc.owner_user_id == user.id
    seen = service.reading(session, user, doc)
    ready = doc.status == "ready"
    failed = doc.status == "failed"
    blocks = blocks_mod.load_blocks(doc.blocks) if ready else []
    readable = doc_visibility.readable_sources(session, user, doc)
    blocks = doc_visibility.redact(blocks, readable)
    all_ids = _meeting_ids(session, doc.id)
    stale = seen.stale and (mine or len(readable) == len(all_ids))
    return DocumentOut(
        **_brief(session, user, doc).model_dump(),
        blocks=blocks,
        checks=DocumentChecks(
            blanks=[BlankOut(block_index=b.block_index, what=b.what) for b in seen.blanks],
            flags=[
                FlagOut(block_index=f.block_index, text=f.text, kind=f.kind) for f in seen.flags
            ],
        ),
        stale=stale,
        sources_available=bool(readable),
        left_out_count=doc.left_out_count if mine else 0,
        failure_code=doc.failure_code if failed else None,
        failure_message=service.failure_message(doc.failure_code) if failed else None,
        failure_detail=doc.failure_detail if failed else None,
        options=(doc.options or {}) if mine else {},
        use_brief=doc.use_brief if mine else False,
        can_edit=mine,
        share_blocked=(
            (lambda refusal: refusal.message if refusal else None)(
                _share_refusal(session, user, doc)
            )
            if mine and ready
            else None
        ),
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
            "description": "`writing`: the draft is still being written; `not_ready`: it failed; "
            "`use_board`: a board is edited through `PATCH .../board`",
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
    draft is still `writing` (409 `writing`), for one that `failed` (409 `not_ready`) and for a
    board (409 `use_board`: `PATCH .../board` has the revision check and the row lock)."""
    doc = document_or_404(session, user, document_id)
    if doc.status == "writing":
        raise services.Refused(409, "writing", "This draft is still being written.")
    if doc.output_type == "board":
        raise services.Refused(
            409,
            "use_board",
            "A board is changed one step at a time. Use the board's own screen to edit it.",
        )
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
    """Discard a draft. One still being written is cancelled. If the worker has not picked it up
    yet no model was called, so its place in the day's limit is given back; once the worker has
    started it, the call may have been made and still counts."""
    doc = document_or_404(session, user, document_id)
    # Lock the row: the worker claims with `FOR UPDATE SKIP LOCKED`, so it either already has the
    # draft (started_at is set, below) or skips it until this transaction ends.
    session.refresh(doc, with_for_update=True)
    if doc.status == "writing" and doc.started_at is None and doc.ledger_run_id is not None:
        session.execute(
            delete(AiRun).where(
                AiRun.id == doc.ledger_run_id, AiRun.kind == "recipe", AiRun.status == "pending"
            )
        )
    session.delete(doc)
    session.commit()
    return Response(status_code=204)


@router.post(
    "/documents/{document_id:id}/share",
    tags=["documents"],
    responses={
        404: {"model": ErrorResponse},
        409: {
            "model": ErrorResponse,
            "description": "`not_ready`: only a finished draft can be shared; `personal_team`: the "
            "draft was made in the creator's own space, so there is no team to share it with; "
            "`team_left`: the creator is no longer in the team the draft was made in",
        },
    },
)
def share_document(
    document_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> DocumentOut:
    """Share the draft, read-only, with **the team it was made in** (`team` on the draft; never
    another, whatever `X-Team-Id` says): every current member can then open it and read its text and
    title, but not the meetings it was drafted from (a source they cannot read shows as hidden, and
    a reader who cannot read every source meeting sees a generic title). Only the creator may;
    asking twice changes nothing. Leaving the team, or `DELETE .../share`, ends access at once."""
    doc = document_or_404(session, user, document_id)
    refusal = _share_refusal(session, user, doc)
    if refusal is not None:
        raise refusal
    session.execute(
        insert(DocumentShare)
        .values(document_id=doc.id, team_id=doc.team_id, shared_by_user_id=user.id)
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
