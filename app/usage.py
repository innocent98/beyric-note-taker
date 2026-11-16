"""The pilot's recorded-hours cap: what a user has used, what they may use, and counting.

Unit: seconds of recorded audio, the length of the recording the pipeline prepares (what is
transcribed and paid for). Waiting-room time and failed joins cost nothing. The ledger is
`usage_entries`, kept apart from `meetings` so deleting a meeting gives nothing back, and keyed by
a hash of the email as well so deleting an account and signing up again does not reset the hours.

Used = ledger + the time since the bot was sent for each of the user's meetings that is still
running and not in the ledger yet. That second term over-estimates (it includes waiting-room time)
on purpose: it covers a meeting whose bot has left but whose audio is not counted yet, and is
capped at `max_poll_seconds` per meeting.
"""

import hashlib
import logging
from datetime import UTC, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import AppState, Meeting, Notification, UsageEntry, User

logger = logging.getLogger(__name__)

DEFAULT_CAP_SECONDS = 5 * 3600  # used when there is no app_state row (it has one in production)
LOW_HOURS_FRACTION = 0.8  # the "most of your hours are used" notice
RUNNING = ("bot_sent", "processing")
# The bot is in the call (or was just sent): the only status that reserves hours for a second
# meeting. A `processing` meeting has left and its elapsed time is already in `running_seconds`.
IN_THE_CALL = "bot_sent"


def email_hash(email: str) -> str:
    return hashlib.sha256(email.strip().lower().encode()).hexdigest()


def effective_cap(session: Session, user: User) -> int | None:
    """Seconds this user may record in total; None for no cap (an admin, or the cap removed)."""
    if user.is_admin:
        return None
    if user.hours_cap_seconds is not None:
        return user.hours_cap_seconds
    state = session.get(AppState, 1)
    return state.default_hours_cap_seconds if state else DEFAULT_CAP_SECONDS


def ledger_seconds(session: Session, user: User) -> int:
    return session.scalar(
        select(func.coalesce(func.sum(UsageEntry.seconds), 0)).where(
            or_(UsageEntry.user_id == user.id, UsageEntry.email_hash == email_hash(user.email))
        )
    )


def running_seconds(
    session: Session, user: User, now: datetime, max_poll_seconds: int = 6 * 3600
) -> int:
    """Estimated seconds of `user`'s own meetings still running and not in the ledger yet."""
    counted = select(UsageEntry.meeting_id).where(UsageEntry.meeting_id.is_not(None))
    running = session.scalars(
        select(Meeting.started_polling_at).where(
            Meeting.owner_user_id == user.id,
            Meeting.status.in_(RUNNING),
            Meeting.started_polling_at.is_not(None),
            Meeting.id.not_in(counted),
        )
    )
    live = sum(
        min(max((now - started).total_seconds(), 0), max_poll_seconds) for started in running
    )
    return int(live)


def hours_used(
    session: Session, user: User, now: datetime, max_poll_seconds: int = 6 * 3600
) -> int:
    """Seconds used by `user`, as described in the module docstring."""
    return ledger_seconds(session, user) + running_seconds(session, user, now, max_poll_seconds)


def record_usage(
    session: Session,
    meeting: Meeting,
    seconds: int,
    now: datetime | None = None,
    settings: Settings | None = None,
) -> UsageEntry | None:
    """Count a prepared recording against its owner. Idempotent per meeting; does not commit.

    Returns the new ledger entry, or None when this meeting was counted before.

    What else happens depends on `HOURS_MODE` (`app/hours.py`). `off` and `shadow`: nothing but the
    old 80% notice (shadow only logs at placement; it writes nothing here). `enforce`: the recording
    is split across the person's grants and the notices of spec 3.3 are raised instead of the old
    one. That bookkeeping is **best effort**: it runs in its own savepoints, a failure is logged
    (exception class and ids only) and the transcript carries on, so a paying person's meeting is
    never failed by it. A recording left without allocations is still counted in the ledger; for
    a person holding a pilot grant `entitlements.unallocated_seconds` still takes it off their
    hours, and `scripts/hours_reconcile_backlog.py` settles it.
    """
    if session.scalar(select(UsageEntry.id).where(UsageEntry.meeting_id == meeting.id)):
        return None
    owner = session.get(User, meeting.owner_user_id)
    meeting.recorded_seconds = seconds
    row = UsageEntry(
        user_id=owner.id, email_hash=email_hash(owner.email), meeting_id=meeting.id, seconds=seconds
    )
    session.add(row)
    session.flush()
    now = now or datetime.now(UTC)
    settings = settings or get_settings()
    if settings.hours_mode == "enforce":
        _hours_module().after_counted(session, owner, row, now, settings.max_poll_hours * 3600)
        return row
    _warn_when_low(session, owner, now)
    return row


def _hours_module():
    from app import hours  # hours imports alerts and entitlements, which import this module

    return hours


def _hours(seconds: float) -> str:
    return f"{seconds / 3600:.1f}".removesuffix(".0")


def _warn_when_low(session: Session, user: User, now: datetime) -> None:
    cap = effective_cap(session, user)
    if cap is None:
        return
    used = ledger_seconds(session, user)
    if used < cap * LOW_HOURS_FRACTION:
        return
    exists = session.scalar(
        select(Notification.id).where(
            Notification.user_id == user.id, Notification.dedupe_key == "usage.hours_low"
        )
    )
    if exists:
        return
    session.add(
        Notification(
            user_id=user.id,
            kind="usage.hours_low",
            severity="info",
            title="Most of your recorded hours are used",
            body=(
                f"You have used {_hours(used)} of your {_hours(cap)} recorded hours. When they "
                "run out the note taker will not join new meetings."
            ),
            dedupe_key="usage.hours_low",
            created_at=now,
        )
    )
