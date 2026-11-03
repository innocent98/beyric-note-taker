"""Hourly housekeeping the worker runs next to its audio cleanup."""

import logging
from datetime import datetime, timedelta

from sqlalchemy import delete, or_
from sqlalchemy.orm import Session

from app import accounts, mail
from app.models import AuthToken, Notification, OAuthFlow

logger = logging.getLogger(__name__)

TOKEN_KEEP = timedelta(days=7)  # a spent or expired token is only kept this long, for debugging
NOTIFICATION_KEEP = timedelta(days=90)


def _delete_stale_rows(session: Session, now: datetime) -> None:
    session.execute(delete(OAuthFlow).where(OAuthFlow.expires_at < now))
    session.execute(
        delete(AuthToken).where(
            or_(AuthToken.used_at < now - TOKEN_KEEP, AuthToken.expires_at < now - TOKEN_KEEP)
        )
    )
    session.execute(delete(Notification).where(Notification.created_at < now - NOTIFICATION_KEEP))
    session.commit()


def run_hourly(session: Session, now: datetime) -> None:
    """Drop expired sign-in flows, spent tokens and old notifications, delete accounts that never
    confirmed their email, and tidy the mail outbox. Each part commits on its own and is isolated:
    one that raises is logged by type only (an exception can hold an address or a link) and the
    others still run."""
    for name, part in (
        ("stale rows", lambda: _delete_stale_rows(session, now)),
        ("unverified accounts", lambda: accounts.sweep_unverified(session, now)),
        ("mail outbox", lambda: mail.sweep_outbox(session, now)),
    ):
        try:
            part()
        except Exception as exc:
            session.rollback()
            logger.warning("hourly %s failed (%s)", name, type(exc).__name__)
