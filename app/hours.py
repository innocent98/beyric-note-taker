"""Writing a person's hours (billing spec 3.2 and 3.3; stage S2). The reading side, and the rule
that decides a join, is `app/entitlements.py`; this module is what changes the database: splitting a
counted recording across grants, settling the usage counted before allocations existed, and the
notices at 80% and 100%.

Nothing here commits. The caller's transaction owns the rows, as with `usage.record_usage` and
`alerts.notify_user`.

Allocating. Each counted recording (`usage_entries`) is split across the person's grants in the
spending order of `entitlements.spend_key`, into `usage_allocations`, so a grant's hours left are
always a sum and an expiry or a refund can be explained line by line. A recording that is longer
than what is left is **finished and counted in full**: the part the grants cannot cover is written
off, not billed and not carried into the next period (spec 3.3). It is written as an over-draw on
the last grant that was spent from (a row larger than the hours that grant had left, which
`entitlements` reads as zero left, never negative). That row is also what marks the entry settled:
an entry with no allocation at all is the "backlog" (usage counted between migration 0049 and the
first allocation) and is taken off the person's grants by `entitlements.unallocated_seconds`, so a
written-off shortfall must never look like backlog.
"""

import logging
from datetime import datetime

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app import alerts, entitlements
from app.models import HourGrant, UsageAllocation, UsageEntry, User

logger = logging.getLogger(__name__)

# Next to PLACEMENT_LOCK (6004): the first int of the two-int advisory lock; the second is the user.
HOURS_LOCK = 6020
LOW_KIND = "usage.hours_low"
OUT_KIND = "usage.hours_out"

LOW_TITLE = "Most of your recorded hours are used"
OUT_TITLE = "You are out of recorded hours"
NO_PLAN_TITLE = "You have no recorded hours"
STAYS = "Your notes, transcripts and exports stay as they are."


def _hours(seconds: float) -> str:
    return f"{seconds / 3600:.1f}".removesuffix(".0")


def _lock_person(session: Session, user: User) -> None:
    """Hold this person's allocations still until the caller commits: two of their recordings
    counted at the same moment (two workers) would otherwise both read the same hours left and
    spend the same hour twice, leaving the next grant untouched. Nothing else takes this lock, and
    it is taken after the entry is written and released at the commit that follows."""
    session.execute(
        text("select pg_advisory_xact_lock(cast(:key as int), cast(:user as int))"),
        {"key": HOURS_LOCK, "user": user.id},
    )


def _spend(
    session: Session, entry_id: int, seconds: int, held: list[list]
) -> list[UsageAllocation]:
    """Write the allocations for `seconds` of one entry out of `held` (`[grant, left]` in spending
    order; the first grants are spent first). What the grants cannot cover goes on the last grant
    spent from as an over-draw (see the module note); with no grants nothing can be written."""
    remaining = seconds
    taken: dict[int, int] = {}
    last = None
    for grant, left in held:
        part = min(left, remaining)
        if part > 0:
            taken[grant.id] = part
            remaining -= part
            last = grant
    if remaining > 0 and held:
        target = last or held[0][0]
        taken[target.id] = taken.get(target.id, 0) + remaining
    rows = [
        UsageAllocation(usage_entry_id=entry_id, grant_id=grant_id, seconds=part)
        for grant_id, part in taken.items()
    ]
    session.add_all(rows)
    session.flush()
    return rows


def allocate(
    session: Session, user: User, entry: UsageEntry, now: datetime
) -> list[UsageAllocation]:
    """Split a counted recording (flushed, so it has an id) across the person's grants in force at
    `now`. Idempotent: an entry that already has an allocation is left alone. An admin holds no
    grants and gets none."""
    if user.is_admin:
        return []
    _lock_person(session, user)
    if session.scalar(
        select(UsageAllocation.id).where(UsageAllocation.usage_entry_id == entry.id).limit(1)
    ):
        return []
    held = entitlements.held_after_backlog(session, user, now, exclude_entry_id=entry.id)
    return _spend(session, entry.id, entry.seconds, held)


def reconcile_backlog(session: Session, user: User) -> int:
    """Settle the usage counted since the person's pilot grant began that has no allocation: turn it
    into allocations on the pilot grants, oldest recording first. Returns the seconds settled.

    This is the one-off step before `HOURS_MODE=enforce`. It changes nobody's hours left: until it
    runs `entitlements.unallocated_seconds` takes the same seconds off the grants in the same order
    (the pilot grant is first, so the same grant). It only matters when the pilot grant later
    expires, because then the backlog would otherwise come off the person's paid hours. Idempotent.
    """
    pilots = list(
        session.scalars(
            select(HourGrant).where(
                entitlements.mine(user, HourGrant.user_id, HourGrant.email_hash),
                HourGrant.kind == "pilot",
            )
        )
    )
    if not pilots:
        return 0
    _lock_person(session, user)
    since = min(g.starts_at for g in pilots)
    until = max((g.expires_at for g in pilots if g.expires_at), default=None)
    spent = dict(
        session.execute(
            select(UsageAllocation.grant_id, func.sum(UsageAllocation.seconds))
            .where(UsageAllocation.grant_id.in_([g.id for g in pilots]))
            .group_by(UsageAllocation.grant_id)
        ).all()
    )
    pilots.sort(key=entitlements.spend_key)
    held = [[g, max(g.seconds - int(spent.get(g.id, 0)), 0)] for g in pilots]
    query = (
        select(UsageEntry)
        .where(
            entitlements.mine(user, UsageEntry.user_id, UsageEntry.email_hash),
            UsageEntry.counted_at > since,
            ~select(UsageAllocation.id)
            .where(UsageAllocation.usage_entry_id == UsageEntry.id)
            .exists(),
        )
        .order_by(UsageEntry.counted_at, UsageEntry.id)
    )
    if until is not None:
        query = query.where(UsageEntry.counted_at <= until)
    settled = 0
    for entry in session.scalars(query).all():
        for row in _spend(session, entry.id, entry.seconds, held):
            for pair in held:  # what each pilot grant has left for the next entry
                if pair[0].id == row.grant_id:
                    pair[1] = max(pair[1] - row.seconds, 0)
        settled += entry.seconds
    return settled


# ---- notices (one per person and period) ----


def notify_low(session: Session, user: User, status: entitlements.Status) -> None:
    """The 80% notice: a bell entry and one mail, once per person and period."""
    used, held = status.holdings_seconds - status.seconds_left, status.holdings_seconds
    alerts.notify_user(
        session,
        user.id,
        kind=LOW_KIND,
        severity="info",
        title=LOW_TITLE,
        body=(
            f"You have used {_hours(used)} of your {_hours(held)} recorded hours. When they run "
            "out the note taker will not join new meetings."
        ),
        dedupe_key=f"{LOW_KIND}:{status.period_key}",
        email_template=alerts.ALERT_TEMPLATE,
    )


def notify_out(session: Session, user: User, status: entitlements.Status) -> None:
    """The 100% notice: a bell entry and one mail, **once per person and period**, whether it is
    raised when a recording is counted or when a meeting is skipped. The skipped meetings
    themselves show as "Not recorded" in the app; they do not each raise a notice."""
    if status.has_grant:
        title = OUT_TITLE
        body = (
            "You have used all of your recorded hours, so the note taker will not join new "
            f"meetings. A meeting that is recording now will finish. {STAYS}"
        )
    else:
        title = NO_PLAN_TITLE
        body = (
            "You do not have any recorded hours at the moment, so the note taker will not join "
            f"new meetings. {STAYS}"
        )
    alerts.notify_user(
        session,
        user.id,
        kind=OUT_KIND,
        severity="warning",
        title=title,
        body=body,
        dedupe_key=f"{OUT_KIND}:{status.period_key}",
        email_template=alerts.ALERT_TEMPLATE,
    )


def notify_out_of_hours(
    session: Session, user: User, now: datetime, max_poll_seconds: int = 6 * 3600
) -> None:
    """The skipped-meeting path: the same one notice a count would raise."""
    notify_out(session, user, entitlements.status(session, user, now, max_poll_seconds))


def after_counted(
    session: Session,
    user: User,
    entry: UsageEntry,
    now: datetime,
    max_poll_seconds: int,
    *,
    notices: bool,
) -> None:
    """Allocate a counted recording and, when `notices` (enforce only), tell the person if they
    have reached 80% or 100%. At 100% the 100% notice only: the 80% one would be stale."""
    allocate(session, user, entry, now)
    if not notices or user.is_admin:
        return
    status = entitlements.status(session, user, now, max_poll_seconds)
    if status.warning == entitlements.OUT:
        notify_out(session, user, status)
    elif status.warning == entitlements.LOW:
        notify_low(session, user, status)
