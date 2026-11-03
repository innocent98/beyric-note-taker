"""The in-app notification bell: what a user has been told and what they have read.

Rows are written by other features (for example `app/usage.py` at 80 % of the hours); this module
only reads and marks them, always for one user. A notification that belongs to someone else is
"no such notification" (404, never 403).
"""

from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models import Notification, User


def unread_count(session: Session, user: User) -> int:
    return session.scalar(
        select(func.count())
        .select_from(Notification)
        .where(Notification.user_id == user.id, Notification.read_at.is_(None))
    )


def list_notifications(
    session: Session, user: User, limit: int, before_id: int | None
) -> list[Notification]:
    """`user`'s notifications, newest first (ask for one more than `limit` to know if there is
    another page)."""
    query = (
        select(Notification)
        .where(Notification.user_id == user.id)
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


def mark_all_read(session: Session, user: User, now: datetime) -> None:
    session.execute(
        update(Notification)
        .where(Notification.user_id == user.id, Notification.read_at.is_(None))
        .values(read_at=now)
    )
    session.commit()
