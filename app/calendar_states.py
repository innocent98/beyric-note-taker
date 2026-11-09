"""How healthy a calendar connection is, and what that does (plan C13, C14).

States (`calendar_connections.status`): `connected` -> `degraded` -> `needs_reconnect`, and back.

* **degraded**: syncs have failed for 30 minutes without a revoked grant (Google down, a network
  fault). Joins keep going on what is stored. One bell entry, once per run of failures.
* **needs_reconnect**: Google no longer honours the grant (`AuthRevoked`: `invalid_grant`, a missing
  scope) or the stored token cannot be read. The sync stops, the user's not-yet-started calendar
  joins become `skipped` with reason `needs_reconnect` (the runner skips the rest at its own
  gate), and one bell entry says so, once per disconnect (`cal_disc:{connection}:{connected_at}`).
  More than one user disconnecting within an hour also tells the admins, once per hour.
* **reconnect** (`calendar_connect`): `revive_on_reconnect` puts those joins back on the schedule
  when their 15-minute deadline has not passed; older ones stay skipped.

The user is mailed once per disconnect (`calendar_disconnected`, lead-designed from the "Could not
join" mail; never for `degraded`). The bell text is the wording the Settings card already uses
(`calendar_text`). Nothing here commits: the caller's transaction owns the rows.
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app import alerts, calendar_text, mail_templates
from app.calendar_source import AuthRevoked, RefreshTokenUnreadable
from app.config import Settings, get_settings
from app.models import CalendarConnection, CalendarEvent, ScheduledJoin

logger = logging.getLogger(__name__)

DEGRADE_AFTER = timedelta(minutes=30)
ADMIN_WINDOW = timedelta(hours=1)
NEXT_DAY = timedelta(hours=24)
# A skipped join this close to its deadline stays skipped: revived, it would end "not recorded"
# a minute later and mail the owner about a meeting they had already been told was off.
REVIVE_MARGIN = timedelta(minutes=2)
# The joins a dead connection can still hold back: not started, not finished.
PAUSABLE_STATES = ("scheduled", "no_capacity")
SKIP_REASON = "needs_reconnect"
KIND_DISCONNECTED = "calendar.disconnected"
KIND_DEGRADED = "calendar.degraded"


def _testing(settings: Settings | None) -> bool:
    return (settings or get_settings()).google_oauth_testing


def _own_joins(conn: CalendarConnection):
    """The calendar joins that came from this connection's events."""
    return ScheduledJoin.calendar_event_id.in_(
        select(CalendarEvent.id).where(CalendarEvent.connection_id == conn.id)
    )


def reason_for(conn: CalendarConnection, exc: Exception, now: datetime, *, testing: bool) -> str:
    """`error` for a token we cannot read, `expired` when the provider says the grant lapsed
    unused or the 7-day Testing expiry has passed (only while the project is in Testing),
    otherwise `revoked`."""
    if isinstance(exc, RefreshTokenUnreadable):
        return "error"
    if isinstance(exc, AuthRevoked) and exc.expired:
        return "expired"
    if testing and conn.expected_expiry_at is not None and now >= conn.expected_expiry_at:
        return "expired"
    return "revoked"


def mark_needs_reconnect(
    session: Session, conn: CalendarConnection, reason: str, now: datetime
) -> int:
    """Move the connection to `needs_reconnect`, skip its pending joins, tell the user once.
    Returns how many of the user's joins in the next 24 hours are now skipped. A connection that
    is already there is left alone (0)."""
    if conn.status == "needs_reconnect":
        return 0
    conn.status, conn.needs_reconnect_reason = "needs_reconnect", reason
    conn.last_error_at = now
    skipped = session.execute(
        update(ScheduledJoin)
        .where(
            ScheduledJoin.user_id == conn.user_id,
            ScheduledJoin.source == "calendar",
            ScheduledJoin.state.in_(PAUSABLE_STATES),
            ScheduledJoin.deadline_at > now,
            _own_joins(conn),
        )
        .values(state="skipped", skip_reason=SKIP_REASON, updated_at=now)
        .returning(ScheduledJoin.starts_at)
    ).all()
    soon = sum(1 for (starts_at,) in skipped if starts_at <= now + NEXT_DAY)
    text = calendar_text.RECONNECT_TEXT.get(reason, calendar_text.GENERIC_RECONNECT_TEXT)
    alerts.notify_user(
        session,
        conn.user_id,
        kind=KIND_DISCONNECTED,
        severity="warning",
        title=text,
        body=text,
        dedupe_key=f"cal_disc:{conn.id}:{int(conn.connected_at.timestamp())}",
        # One mail per disconnect: the mail is queued only by the call that wrote the bell entry.
        email_template=mail_templates.CALENDAR_DISCONNECTED,
    )
    if reason == "error":
        alerts.notify_admins(
            session,
            kind="admin.calendar_token_unreadable",
            title="A calendar connection's stored token could not be read",
            body=f"The token of user {conn.user_id}'s calendar connection could not be decrypted "
            f"at {now:%H:%M} UTC. Check the token key; the user must connect again.",
            dedupe_key=f"admin.calendar_token_unreadable:{conn.id}:{int(conn.connected_at.timestamp())}",
        )
    _tell_admins_of_a_wave(session, now)
    return soon


def _tell_admins_of_a_wave(session: Session, now: datetime) -> None:
    """More than one user disconnecting within an hour looks like our fault (the OAuth client, the
    key), not theirs: tell the admins, once per hour."""
    users = session.scalar(
        select(func.count(func.distinct(CalendarConnection.user_id))).where(
            CalendarConnection.status == "needs_reconnect",
            CalendarConnection.last_error_at >= now - ADMIN_WINDOW,
        )
    )
    if users and users > 1:
        alerts.notify_admins(
            session,
            kind="admin.calendar_disconnects",
            title="Several calendars disconnected",
            body=f"{users} users' calendar connections needed a reconnect within the last hour "
            f"(as of {now:%H:%M} UTC).",
            dedupe_key=alerts.bucket_key("admin.calendar_disconnects", now, 60),
        )


def mark_degraded(session: Session, conn: CalendarConnection, now: datetime) -> None:
    """Syncs have been failing for a while: say so once per run of failures. Joins go on."""
    if conn.status != "connected":
        return
    conn.status = "degraded"
    since = conn.error_since or now
    alerts.notify_user(
        session,
        conn.user_id,
        kind=KIND_DEGRADED,
        severity="warning",
        title=calendar_text.DEGRADED_TEXT,
        body=calendar_text.DEGRADED_TEXT,
        dedupe_key=f"cal_degr:{conn.id}:{int(since.timestamp())}",
    )


def mark_healthy(session: Session, conn: CalendarConnection, now: datetime) -> None:
    """A sync worked: the run of failures is over."""
    conn.error_since = None
    if conn.status == "degraded":
        conn.status = "connected"


def on_sync_failure(
    session: Session,
    conn: CalendarConnection,
    exc: Exception,
    now: datetime,
    *,
    settings: Settings | None = None,
) -> int:
    """A sync (or a re-check) failed. A revoked grant needs a reconnect at once; anything else
    starts the clock, and `degraded` follows after 30 minutes of it. Returns the count
    `mark_needs_reconnect` gives (0 otherwise)."""
    if isinstance(exc, AuthRevoked):
        reason = reason_for(conn, exc, now, testing=_testing(settings))
        return mark_needs_reconnect(session, conn, reason, now)
    if conn.status not in ("connected", "degraded"):
        return 0
    if conn.error_since is None:
        conn.error_since = now
    if now - conn.error_since >= DEGRADE_AFTER:
        mark_degraded(session, conn, now)
    return 0


def revive_on_reconnect(session: Session, conn: CalendarConnection, now: datetime) -> int:
    """The person connected again: joins skipped for `needs_reconnect` whose deadline has not
    passed go back on the schedule (the runner re-checks everything else before a bot goes).
    Returns how many."""
    conn.error_since = None
    if conn.id is None:
        return 0
    result = session.execute(
        update(ScheduledJoin)
        .where(
            ScheduledJoin.user_id == conn.user_id,
            ScheduledJoin.source == "calendar",
            ScheduledJoin.state == "skipped",
            ScheduledJoin.skip_reason == SKIP_REASON,
            ScheduledJoin.deadline_at > now + REVIVE_MARGIN,
            _own_joins(conn),
        )
        .values(
            state="scheduled",
            skip_reason=None,
            last_error=None,
            attempts=0,
            rechecked_at=None,  # the event may have moved or gone while disconnected
            updated_at=now,
        )
    )
    return result.rowcount


def expiry_warning(conn: CalendarConnection, *, testing: bool, now: datetime) -> datetime | None:
    """When the 7-day Testing expiry will hit, if that is under 24 hours away; else None. Always
    None once the project is out of Testing, even for a connection made while it was in it."""
    if not testing or conn.expected_expiry_at is None:
        return None
    remaining = conn.expected_expiry_at - now
    return conn.expected_expiry_at if timedelta(0) < remaining <= NEXT_DAY else None
