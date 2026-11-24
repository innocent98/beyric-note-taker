"""The team brief (plan T11, spec 5.4 item 6): a team's own reference text the model may use besides
the meeting: services, prices, terms, house style, a past proposal pasted in. One per team, the
personal team's included.

Owners and admins write it (`team_admin.require_manager`, the Team screen's rule); members read it,
since its prices and terms appear in the drafts they make anyway. It is DATA: stored as typed
(control and hidden-direction characters are dropped, newlines made one kind) and shown to the model
only inside a fence (`app/recipes/generate.py`). Nothing here commits; routes do.
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import TeamBrief, TeamMember, User
from app.recipes.import_child import clean_text
from app.services import Refused


def max_chars() -> int:
    return get_settings().brief_max_chars


def read(session: Session, team_id: int) -> TeamBrief | None:
    return session.scalars(select(TeamBrief).where(TeamBrief.team_id == team_id)).first()


def save(session: Session, member: TeamMember, user: User, text: str) -> TeamBrief:
    """Set the brief of the member's team. The caller has checked the member may write it. 422
    `brief_too_long` when the cleaned text is over the cap: nothing is kept then."""
    clean, _ = clean_text(text)
    cap = max_chars()
    if len(clean) > cap:
        raise Refused(
            422,
            "brief_too_long",
            f"The brief can hold {cap} characters; this one has {len(clean)}. Shorten it.",
        )
    now = datetime.now(UTC)
    # One row per team, made on the first save: the unique team id settles two saves at once.
    session.execute(
        insert(TeamBrief)
        .values(team_id=member.team_id, text=clean, updated_by_user_id=user.id, updated_at=now)
        .on_conflict_do_update(
            index_elements=[TeamBrief.team_id],
            set_={"text": clean, "updated_by_user_id": user.id, "updated_at": now},
        )
    )
    session.flush()
    session.expire_all()
    return read(session, member.team_id)
