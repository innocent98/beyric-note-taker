"""The board's edit route (plan T16): `PATCH /api/documents/{id}/board`.

Included into `app.api.router`, so it shares its `/api` prefix, login check, CSRF rule and error
shape.

One operation per call (`app/recipes/board.py`: `move_card`, `rename_column`, `add_column`,
`delete_column`, `add_card`, `edit_card`, `delete_card`, `recolour`). The body is strict: no field
that is not named, numbers and text bounded. Every call carries the `revision` the person's screen
last saw (the draft's `updated_at`); if the board has changed since, the edit is refused with 409
`stale_board` and the screen reloads, so a card is never moved by a number that now means another
card. The check and the write happen under a row lock, so two tabs cannot both win.

Only the CREATOR edits a board, even once it is shared with the team: the draft is looked up by its
owner here and does not use `api_recipes.document_or_404`, which slice S4 widens to readers. Anyone
else gets a 404.
"""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import auth, services
from app.api_recipes import _out
from app.api_schemas import DocumentOut, ErrorResponse
from app.db import get_session
from app.models import Document, User
from app.recipes import blocks as blocks_mod
from app.recipes import board

router = APIRouter()

CANNOT = {
    "board_full": "A board holds at most 12 columns.",
    "column_full": "A column holds at most 30 cards.",
    "title_empty": "Give the column a name.",
    "card_empty": "A card needs some text.",
    "last_column": "A board keeps at least one column.",
}


def _revision(text: str | None) -> datetime:
    if text is None:
        raise services.Refused(
            422, "revision_required", "Send the revision (the draft's updated_at) you last saw."
        )
    try:
        when = datetime.fromisoformat(text)
    except ValueError as exc:
        raise services.Refused(422, "revision_invalid", "That revision is not a time.") from exc
    return when if when.tzinfo else when.replace(tzinfo=UTC)


@router.patch(
    "/documents/{document_id:id}/board",
    tags=["documents"],
    responses={
        404: {"model": ErrorResponse, "description": "No such draft, or not the caller's"},
        409: {
            "model": ErrorResponse,
            "description": "`stale_board` (the board changed since `revision`, or a column or card "
            "named is not there), `writing` (still being written), `not_ready` (it failed), "
            "`not_a_board`",
        },
        422: {
            "model": ErrorResponse,
            "description": "`revision_required`, `revision_invalid`, `board_full`, `column_full`, "
            "`title_empty`, `card_empty`, `last_column`; or `validation_error` for a body that "
            "is not one of the operations",
        },
    },
)
def edit_board(
    document_id: int,
    body: board.Op,
    session: Session = Depends(get_session),
    user: User = Depends(auth.current_user),
) -> DocumentOut:
    """Change a board by ONE operation and answer with the whole draft as it now stands (with the
    new `updated_at`, the `revision` for the next call). `card` numbers cards counting through the
    columns from 0; `index` is a place inside the target column. Marks the draft `edited`. Text is
    made plain and cut to size as at generation; a card keeps its sources when moved or edited and
    a new card has none."""
    revision = _revision(body.revision)
    doc = None
    if 1 <= document_id <= services.MAX_ID:
        doc = session.scalars(
            select(Document)
            .where(Document.id == document_id, Document.owner_user_id == user.id)
            .with_for_update()
        ).first()
    if doc is None:
        raise services.Refused(404, "not_found", "No such document.")
    if doc.output_type != "board":
        raise services.Refused(409, "not_a_board", "This draft is not a board.")
    if doc.status == "writing":
        raise services.Refused(409, "writing", "This draft is still being written.")
    if doc.status != "ready":
        raise services.Refused(409, "not_ready", "This draft failed; make a new one.")
    stale = services.Refused(
        409, "stale_board", "The board changed since you opened it. It has been reloaded."
    )
    if revision != doc.updated_at:
        raise stale
    try:
        changed = board.apply(blocks_mod.load_blocks(doc.blocks), body)
    except board.StaleBoard as exc:
        raise stale from exc
    except board.BoardInvalid as exc:
        if exc.code == "not_a_board":
            raise services.Refused(409, "not_a_board", "This draft is not a board.") from exc
        raise services.Refused(422, exc.code, CANNOT[exc.code]) from exc
    except blocks_mod.BlocksInvalid as exc:
        raise services.Refused(409, "not_a_board", "This draft is not a board.") from exc
    doc.blocks = blocks_mod.dump_blocks(changed)
    doc.edited = True
    # Strictly later than the revision the caller held, whatever the clock says.
    doc.updated_at = max(datetime.now(UTC), doc.updated_at + timedelta(microseconds=1))
    session.commit()
    return _out(session, user, doc)
