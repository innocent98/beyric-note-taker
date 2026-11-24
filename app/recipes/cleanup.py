"""What deleting a meeting does to the drafts made from it (spec 5.4a: "delete means gone").

The database already removes the *links* (`document_meetings` cascades from `meetings`), so a draft
would otherwise outlive the meeting it quotes. This removes the drafts whose every source meeting
is going: a draft made from one meeting goes with it; a cross-meeting draft (a status report over a
sprint) stays while at least one of its meetings does, and its link to the deleted one reads "not
available" on the screen. Kept a leaf module (models and SQLAlchemy only) so `services` and
`deletion` can import it without a cycle.

The `ai_runs` rows of those drafts stay: the ledger outlives them and holds no meeting text.
"""

from collections.abc import Iterable

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import Document, DocumentMeeting


def delete_drafts_of(session: Session, meeting_ids: Iterable[int]) -> int:
    """Delete every draft all of whose meetings are in `meeting_ids`; return how many. Shares go
    with their draft (cascade). The caller commits, in the transaction that deletes the meetings."""
    going = set(meeting_ids)
    if not going:
        return 0
    survives = select(DocumentMeeting.document_id).where(DocumentMeeting.meeting_id.not_in(going))
    only_going = (
        select(DocumentMeeting.document_id)
        .where(DocumentMeeting.meeting_id.in_(going))
        .where(DocumentMeeting.document_id.not_in(survives))
    )
    return session.execute(delete(Document).where(Document.id.in_(only_going))).rowcount
