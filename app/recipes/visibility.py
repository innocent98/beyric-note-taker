"""Who may read a draft, and what a reader of a shared draft may see of its sources (plan T13).

A draft is private to its creator. The creator may share it, read-only, with a team they are in
(`DocumentShare`); every current member of that team may then read it, and nobody else. Three rules
keep that honest:

* **Access is looked up now, never remembered.** A reader's right comes from their membership of
  the team at the moment of the request, so leaving a team ends it at once, and so does removing the
  share. The share also stops counting when the person who made it has left that team (a draft is
  shared by a member, on a team they belong to).
* **Sharing a draft never gives a meeting.** The draft's own words are shared (the share dialog
  says so); its sources are not. For a reader every source that points at a meeting they cannot read
  is replaced by `{"kind": "hidden"}`: no meeting id, line, quote or title leaves the server. The
  list of meeting ids on the draft is cut to the readable ones for the same reason.
* **Only the creator writes.** Edit, regenerate-from, discard, share, unshare and the board's
  operations look the draft up through `api_recipes.document_or_404` (the creator, else 404), never
  through `readable_document_or_404`.

Not found and not allowed look the same (404, never 403): a person must not learn that a draft
exists. The platform admin has no route into drafts (`is_admin` is not consulted).
"""

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.orm import Session, aliased

from app.models import Document, DocumentMeeting, DocumentShare, Meeting, TeamMember, User
from app.recipes import blocks as blocks_mod
from app.visibility import visible_meetings


def _shared_to(user: User):
    """`documents.id` of the drafts shared to a team `user` is in, by someone still in that team."""
    sharer, mine, doc = aliased(TeamMember), aliased(TeamMember), aliased(Document)
    return (
        select(DocumentShare.document_id)
        .join(mine, and_(mine.team_id == DocumentShare.team_id, mine.user_id == user.id))
        .join(doc, doc.id == DocumentShare.document_id)
        .join(
            sharer,
            and_(
                sharer.team_id == DocumentShare.team_id,
                sharer.user_id == DocumentShare.shared_by_user_id,
            ),
        )
        .where(doc.owner_user_id == DocumentShare.shared_by_user_id)
    )


def readable_clause(user: User):
    """The `where` condition for "`user` may read this draft": theirs, or shared to their team."""
    return or_(Document.owner_user_id == user.id, Document.id.in_(_shared_to(user)))


def shared_clause(user: User):
    """Drafts someone else shared with `user` (not their own)."""
    return and_(Document.owner_user_id != user.id, Document.id.in_(_shared_to(user)))


def can_read_document(session: Session, user: User, doc: Document) -> bool:
    return bool(
        session.scalar(select(exists().where(Document.id == doc.id, readable_clause(user))))
    )


def readable_sources(session: Session, user: User, doc: Document) -> set[int]:
    """The ids of the draft's meetings that `user` may read (and that still exist)."""
    return set(
        session.scalars(
            visible_meetings(user)
            .with_only_columns(Meeting.id)
            .where(
                Meeting.id.in_(
                    select(DocumentMeeting.meeting_id).where(DocumentMeeting.document_id == doc.id)
                )
            )
        )
    )


def _hide(sources: list[blocks_mod.Source], readable: set[int]) -> None:
    for index, source in enumerate(sources):
        if source.meeting_id is None or source.meeting_id not in readable:
            sources[index] = blocks_mod.Source(kind="hidden")


def redact(blocks: list[blocks_mod.Block], readable: set[int]) -> list[blocks_mod.Block]:
    """A copy of `blocks` in which every source of a meeting not in `readable` is `hidden`. The
    text is untouched. Two hidden sources in one place stay two (their count is the draft's own)."""
    copy = [block.model_copy(deep=True) for block in blocks]
    for block in copy:
        _hide(getattr(block, "sources", []), readable)
        for item in getattr(block, "items", []):
            _hide(item.sources, readable)
        for row in getattr(block, "rows", []):
            for cell in row:
                _hide(cell.sources, readable)
        for card in getattr(block, "cards", []):
            _hide(card.sources, readable)
    return copy


def shared_team_ids(session: Session, doc: Document) -> list[int]:
    return sorted(
        session.scalars(select(DocumentShare.team_id).where(DocumentShare.document_id == doc.id))
    )
