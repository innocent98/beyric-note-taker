"""Hourly housekeeping: the delete-old-rows sweeps.

The scheduler registers each of these as its own hourly job (`app/sweeps.py`); the worker still
calls `run_hourly` next to its audio cleanup, which is safe because every sweep is idempotent.
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy import delete, or_
from sqlalchemy.orm import Session

from app import accounts, mail
from app.models import AuthToken, CalendarEvent, Notification, OAuthFlow

logger = logging.getLogger(__name__)

TOKEN_KEEP = timedelta(days=7)  # a spent or expired token is only kept this long, for debugging
NOTIFICATION_KEEP = timedelta(days=90)
CALENDAR_EVENT_KEEP = timedelta(days=2)  # after the event ENDS (spec 12.2)


def sweep_oauth_flows(session: Session, now: datetime) -> None:
    """Sign-in and connect flows past their expiry. Commits."""
    session.execute(delete(OAuthFlow).where(OAuthFlow.expires_at < now))
    session.commit()


def sweep_auth_tokens(session: Session, now: datetime) -> None:
    """Tokens used, or expired, more than `TOKEN_KEEP` ago. Commits."""
    session.execute(
        delete(AuthToken).where(
            or_(AuthToken.used_at < now - TOKEN_KEEP, AuthToken.expires_at < now - TOKEN_KEEP)
        )
    )
    session.commit()


def sweep_notifications(session: Session, now: datetime) -> None:
    """Notifications older than `NOTIFICATION_KEEP`, read or not. Commits."""
    session.execute(delete(Notification).where(Notification.created_at < now - NOTIFICATION_KEEP))
    session.commit()


def sweep_calendar_events(session: Session, now: datetime) -> None:
    """Calendar events that ended more than two days ago. The row can hold a Zoom passcode in its
    link, so it is not kept longer than the day-after view needs. A scheduled join and the meeting
    it made stay (`scheduled_joins.calendar_event_id` goes to null). Commits."""
    session.execute(delete(CalendarEvent).where(CalendarEvent.ends_at < now - CALENDAR_EVENT_KEEP))
    session.commit()


def _delete_stale_rows(session: Session, now: datetime) -> None:
    sweep_oauth_flows(session, now)
    sweep_auth_tokens(session, now)
    sweep_notifications(session, now)


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
