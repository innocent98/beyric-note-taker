"""What a person's hours allow: the one place that answers "how many hours are left?" and "may this
person start a recording?" (billing spec, sections 2.1 and 3.2; stage S1, shadow only).

Pure reading: nothing here writes, commits or imports a route, so any caller may use it, and the
shadow report can run it inside a read-only transaction. Today it decides nothing: `reserve_slot`
still enforces the old cap (`usage.effective_cap`) and only compares this answer with it, behind
`HOURS_MODE` (`app/placement.py`).

A person's hours are their grants (`hour_grants`) in force at `now`: started, not expired. What is
left of a grant is its `seconds` minus its allocations. Two kinds of spending have no allocation
yet and are taken off the grants here, in the order of spec 3.2 (trial or pilot, then plan hours,
then packs, soonest expiry first):

- usage counted after the person's pilot grant began and not yet split across grants. Stage S1
  does not write allocations (that is S2), and the migration netted everything before it out of
  the pilot grant, so this is exactly what `usage.record_usage` has counted since the migration;
- the estimate for the person's own meetings still running (`usage.running_seconds`), the same
  term the old rule adds.

Grants are found by user id or by the email fingerprint, the same two keys as
`usage.ledger_seconds`. An admin has no limit (`unlimited`), as under the old rule.

Only grants exist in S1, so only the states they can show exist here: `trial`, `pilot`, `active`
(a paid plan period) and `none`. `past_due` and `lapsed` need subscriptions (stage S8).
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app import usage
from app.models import HourGrant, UsageAllocation, UsageEntry, User

NO_HOURS = "no_hours"  # a grant is in force but spent
NO_PLAN = "no_plan"  # no grant is in force at all

# Spec 3.2: trial or pilot first, then plan hours, then packs. The spec does not place an admin's
# gift; it goes after the plan hours and before the packs.
SPEND_ORDER = {"trial": 0, "pilot": 0, "plan_period": 1, "admin_gift": 2, "pack": 3}
# The state a grant with hours left shows. A pack or a gift alone is not a plan: it shows `none`.
GRANT_STATE = {"trial": "trial", "pilot": "pilot", "plan_period": "active"}


@dataclass(frozen=True)
class Status:
    state: str  # trial, pilot, active or none (S8 adds past_due and lapsed)
    seconds_left: int | None  # None = no limit (an admin)
    unlimited: bool
    has_grant: bool  # a grant is in force now, spent or not (tells no_hours from no_plan)


@dataclass(frozen=True)
class JoinDecision:
    allowed: bool
    reason: str | None  # NO_HOURS or NO_PLAN when refused, else None
    seconds_left: int | None


def _mine(user: User, user_id_column, hash_column):
    """`user`'s rows, by id or by the fingerprint of the email, as `ledger_seconds` does."""
    return or_(user_id_column == user.id, hash_column == usage.email_hash(user.email))


def _grants_in_force(session: Session, user: User, now: datetime) -> list[list]:
    """`[grant, seconds left]` for each of the person's grants in force, in spending order."""
    allocated = (
        select(func.coalesce(func.sum(UsageAllocation.seconds), 0))
        .where(UsageAllocation.grant_id == HourGrant.id)
        .scalar_subquery()
    )
    rows = session.execute(
        select(HourGrant, allocated).where(
            _mine(user, HourGrant.user_id, HourGrant.email_hash),
            HourGrant.starts_at <= now,
            or_(HourGrant.expires_at.is_(None), HourGrant.expires_at > now),
        )
    ).all()
    held = [[grant, max(grant.seconds - int(spent), 0)] for grant, spent in rows]
    far = datetime.max.replace(tzinfo=now.tzinfo)
    held.sort(key=lambda r: (SPEND_ORDER[r[0].kind], r[0].expires_at or far, r[0].id))
    return held


def _unallocated_seconds(session: Session, user: User) -> int:
    """Usage counted since the person's pilot grant began that no allocation covers yet."""
    since = session.scalar(
        select(func.min(HourGrant.starts_at)).where(
            _mine(user, HourGrant.user_id, HourGrant.email_hash), HourGrant.kind == "pilot"
        )
    )
    if since is None:
        return 0
    return session.scalar(
        select(func.coalesce(func.sum(UsageEntry.seconds), 0)).where(
            _mine(user, UsageEntry.user_id, UsageEntry.email_hash),
            UsageEntry.counted_at > since,
            UsageEntry.id.not_in(select(UsageAllocation.usage_entry_id)),
        )
    )


def status(session: Session, user: User, now: datetime, max_poll_seconds: int = 6 * 3600) -> Status:
    """The person's state and the seconds they may still record."""
    if user.is_admin:
        return Status("none", None, True, False)
    held = _grants_in_force(session, user, now)
    to_spend = _unallocated_seconds(session, user) + usage.running_seconds(
        session, user, now, max_poll_seconds
    )
    for row in held:  # take the spending off the grants in order; it never goes below zero
        taken = min(row[1], to_spend)
        row[1] -= taken
        to_spend -= taken
    states = [GRANT_STATE[g.kind] for g, left in held if left > 0 and g.kind in GRANT_STATE]
    return Status(
        state=states[0] if states else "none",
        seconds_left=sum(left for _, left in held),
        unlimited=False,
        has_grant=bool(held),
    )


def check_join(
    session: Session, user: User, now: datetime, max_poll_seconds: int = 6 * 3600
) -> JoinDecision:
    """What the new rule would decide for a new recording: allowed while any hour is left."""
    s = status(session, user, now, max_poll_seconds)
    if s.unlimited or (s.seconds_left or 0) > 0:
        return JoinDecision(True, None, s.seconds_left)
    return JoinDecision(False, NO_HOURS if s.has_grant else NO_PLAN, 0)
