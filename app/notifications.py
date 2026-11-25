"""The in-app notification bell: what a user has been told and what they have read.

Rows are written by other features (for example `app/usage.py` at 80 % of the hours); this module
only reads and marks them, always for one user. A notification that belongs to someone else is
"no such notification" (404, never 403).

Every notification belongs to one workspace (`notifications.team_id`: its meeting's workspace, else
the person's personal one): the bell lists and counts the ACTIVE workspace's, and the session names
how many unread each OTHER workspace has so nothing is missed (`unread_by_workspace`). Hours and
account notices have no meeting: they are in the personal workspace.
"""

from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.models import Meeting, Notification, TeamMember, User
from app.visibility import can_read_clause


def _about_something_readable(user: User):
    """A notification that names a meeting is shown only while the person may still read it: one
    about a meeting moved to a team they have left would otherwise keep its title in their bell."""
    return or_(
        Notification.meeting_id.is_(None),
        Notification.meeting_id.in_(select(Meeting.id).where(can_read_clause(user))),
    )


def unread_count(session: Session, user: User, team_id: int) -> int:
    """Unread notifications of the workspace `team_id` (the active one)."""
    return session.scalar(
        select(func.count())
        .select_from(Notification)
        .where(
            Notification.user_id == user.id,
            Notification.team_id == team_id,
            Notification.read_at.is_(None),
            _about_something_readable(user),
        )
    )


def unread_by_workspace(session: Session, user: User) -> dict[int, int]:
    """Unread notifications per workspace the person is in (a workspace with none is 0), for the
    switcher's unread dots. Only workspaces they belong to: a notification filed in a team they
    left is nobody's to count."""
    mine = select(TeamMember.team_id).where(TeamMember.user_id == user.id)
    counts = dict.fromkeys(session.scalars(mine), 0)
    rows = session.execute(
        select(Notification.team_id, func.count())
        .where(
            Notification.user_id == user.id,
            Notification.read_at.is_(None),
            Notification.team_id.in_(mine),
            _about_something_readable(user),
        )
        .group_by(Notification.team_id)
    )
    counts.update({team_id: count for team_id, count in rows})
    return counts


def list_notifications(
    session: Session, user: User, team_id: int, limit: int, before_id: int | None
) -> list[Notification]:
    """`user`'s notifications in the workspace `team_id`, newest first (ask for one more than
    `limit` to know if there is another page)."""
    query = (
        select(Notification)
        .where(
            Notification.user_id == user.id,
            Notification.team_id == team_id,
            _about_something_readable(user),
        )
        .order_by(Notification.id.desc())
        .limit(limit)
    )
    if before_id is not None:
        query = query.where(Notification.id < before_id)
    return list(session.scalars(query))


def mark_read(session: Session, user: User, notification_id: int, now: datetime) -> Notification:
    """Mark one notification read (the first read time is kept). 404 when it is not `user`'s."""
    row = None
    if 0 < notification_id <= 2**63 - 1:
        row = session.scalars(
            select(Notification).where(
                Notification.id == notification_id, Notification.user_id == user.id
            )
        ).first()
    if row is None:
        raise HTTPException(404, "No such notification")
    if row.read_at is None:
        row.read_at = now
        session.commit()
    return row


def mark_all_read(session: Session, user: User, team_id: int, now: datetime) -> None:
    """Mark the workspace's notifications read; the other workspaces' stay unread."""
    session.execute(
        update(Notification)
        .where(
            Notification.user_id == user.id,
            Notification.team_id == team_id,
            Notification.read_at.is_(None),
        )
        .values(read_at=now)
    )
    session.commit()
