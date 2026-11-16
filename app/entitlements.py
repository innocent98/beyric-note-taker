"""What a person's hours allow: the one place that answers "how many hours are left?" and "may this
person start a recording?" (billing spec, sections 2.1 and 3.2; stages S1 and S2).

Pure reading: nothing here writes, commits or imports a route, so any caller may use it, and the
shadow report can run it inside a read-only transaction. Writing (splitting a counted recording
across grants, the notices) is `app/hours.py`. `reserve_slot` asks `check_join` only under
`HOURS_MODE=enforce`; under `shadow` it logs how this answer differs from the old cap rule
(`usage.effective_cap`) and under `off` it never runs (`app/placement.py`).

A person's hours are their grants (`hour_grants`) in force at `now`: started, not expired. What is
left of a grant is its `seconds` minus its allocations. They are spent in the order of spec 3.2,
decided for S2: (1) a trial or pilot grant, (2) plan hours, oldest period first, (3) then admin
gifts and packs together, whichever ends soonest first, a grant with no expiry last. Two kinds of
spending have no allocation yet and are taken off the grants here, in that order:

- usage counted after the person's pilot grant began that no allocation covers: what was counted
  between migration 0049 and the first allocation (S1 wrote none; the migration netted everything
  before it out of the pilot grant). `hours.reconcile_backlog` turns these into allocations, and
  until it has run this term keeps the person's hours right. An entry that has any allocation is
  settled, a shortfall included (see `hours.allocate`), so it never comes off a second time;
- the estimate for the person's own meetings still running (`usage.running_seconds`), the same
  term the old rule adds. It is dropped the moment the meeting is counted and allocated.

Grants are found by user id or by the email fingerprint, the same two keys as
`usage.ledger_seconds`. An admin has no limit (`unlimited`), as under the old rule.

Only grants exist in S1, so only the states they can show exist here: `trial`, `pilot`, `active`
(a paid plan period) and `none`. `past_due` and `lapsed` need subscriptions (stage S8).

The bottom of the module (`can`, `gate`) answers the feature gates of spec 2.3 (stage S3): which
states may use Ask Beyric, new translations and rewriting notes. The mode switch and the refusal
are `app/feature_gates.py`.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app import usage
from app.models import HourGrant, Meeting, ScheduledJoin, UsageAllocation, UsageEntry, User

NO_HOURS = "no_hours"  # a grant is in force but spent
NO_PLAN = "no_plan"  # no grant is in force at all
# Hours are held (the first meeting would start) but every one of them is set aside for meetings
# that are already running or just placed: a second meeting must not be started on them.
RESERVE = "reserve"

# Spec 3.2, as decided for S2: trial or pilot first, then plan hours (oldest period first), then
# admin gifts and packs together, soonest expiry first (no expiry last). The rule behind it:
# whatever ends soonest is used first.
SPEND_RANK = {"trial": 0, "pilot": 0, "plan_period": 1, "admin_gift": 2, "pack": 2}
# The state a grant shows. A pack or a gift alone is not a plan: it shows `none`.
GRANT_STATE = {"trial": "trial", "pilot": "pilot", "plan_period": "active"}
LOW_PERCENT = 80  # the warning moment: used reaches this share of what the person holds
LOW = "low"
OUT = "out"


@dataclass(frozen=True)
class Status:
    state: str  # trial, pilot, active or none (S8 adds past_due and lapsed)
    seconds_left: int | None  # None = no limit (an admin)
    unlimited: bool
    has_grant: bool  # a grant is in force now, spent or not (tells no_hours from no_plan)
    # What the person holds now: the whole size of every grant in force, spent or not. The 80%
    # warning and the usage bar are a share of this. 0 when no grant is in force, None for an admin.
    holdings_seconds: int | None = 0
    warning: str | None = None  # LOW (80% used), OUT (nothing left) or None
    period_ends_at: datetime | None = None  # the newest plan period in force ends then
    trial_ends_at: datetime | None = None  # the trial in force ends then
    # Names the person's current period for the notices (once per person and period): the newest
    # plan period in force, else the first grant in spending order, else "none".
    period_key: str = "none"
    # The usage counted before the person's pilot grant was made, while a pilot grant is in force
    # (0 otherwise). A pilot grant holds only what was LEFT of the old allowance, so a person who
    # had used 4 of 5 hours holds 1 hour; the screens and mails show the original total by adding
    # this to both numbers ("4 of 5 h", not "0 of 1 h"). The ledger is the baseline: nothing else
    # is stored.
    display_offset_seconds: int = 0


@dataclass(frozen=True)
class JoinDecision:
    allowed: bool
    reason: str | None  # NO_HOURS or NO_PLAN when refused, else None
    seconds_left: int | None


def mine(user: User, user_id_column, hash_column):
    """`user`'s rows, by id or by the fingerprint of the email, as `ledger_seconds` does."""
    return or_(user_id_column == user.id, hash_column == usage.email_hash(user.email))


def spend_key(grant: HourGrant) -> tuple:
    """Where a grant stands in the spending order (lower is spent first)."""
    far = datetime.max.replace(tzinfo=UTC)
    if grant.kind == "plan_period":
        return (SPEND_RANK[grant.kind], grant.starts_at, grant.id)  # oldest period first
    return (SPEND_RANK[grant.kind], grant.expires_at or far, grant.id)  # soonest expiry first


def grants_in_force(session: Session, user: User, now: datetime) -> list[list]:
    """`[grant, seconds left]` for each of the person's grants in force, in spending order. What is
    left is the grant minus its allocations only: no backlog and no running meeting yet."""
    allocated = (
        select(func.coalesce(func.sum(UsageAllocation.seconds), 0))
        .where(UsageAllocation.grant_id == HourGrant.id)
        .scalar_subquery()
    )
    rows = session.execute(
        select(HourGrant, allocated).where(
            mine(user, HourGrant.user_id, HourGrant.email_hash),
            HourGrant.starts_at <= now,
            or_(HourGrant.expires_at.is_(None), HourGrant.expires_at > now),
        )
    ).all()
    held = [[grant, max(grant.seconds - int(spent), 0)] for grant, spent in rows]
    held.sort(key=lambda r: spend_key(r[0]))
    return held


def unallocated_seconds(session: Session, user: User, exclude_entry_id: int | None = None) -> int:
    """Usage counted since the person's pilot grant began that has no allocation at all: what was
    counted between migration 0049 and the first allocation. `hours.reconcile_backlog` settles it.
    An entry with any allocation is settled, so `hours.allocate` always leaves a row on an entry it
    could not cover in full (the shortfall is written off, never counted again). Usage after the
    pilot grant's expiry (set in S10) is never part of it: it was counted under S2."""
    since, until, open_ended = session.execute(
        select(
            func.min(HourGrant.starts_at),
            func.max(HourGrant.expires_at),
            func.count().filter(HourGrant.expires_at.is_(None)),
        ).where(mine(user, HourGrant.user_id, HourGrant.email_hash), HourGrant.kind == "pilot")
    ).one()
    if open_ended:  # one pilot grant never expires, so nothing is past its end
        until = None
    if since is None:
        return 0
    query = select(func.coalesce(func.sum(UsageEntry.seconds), 0)).where(
        mine(user, UsageEntry.user_id, UsageEntry.email_hash),
        UsageEntry.counted_at > since,
        ~select(UsageAllocation.id).where(UsageAllocation.usage_entry_id == UsageEntry.id).exists(),
    )
    if exclude_entry_id is not None:
        query = query.where(UsageEntry.id != exclude_entry_id)
    if until is not None:
        query = query.where(UsageEntry.counted_at <= until)
    return session.scalar(query)


def pre_pilot_seconds(session: Session, user: User) -> int:
    """Usage counted at or before the moment the person's pilot grants were made (migration 0049
    made each as the old cap minus the ledger of that moment). 0 with no pilot grant."""
    since = session.scalar(
        select(func.min(HourGrant.starts_at)).where(
            mine(user, HourGrant.user_id, HourGrant.email_hash), HourGrant.kind == "pilot"
        )
    )
    if since is None:
        return 0
    return int(
        session.scalar(
            select(func.coalesce(func.sum(UsageEntry.seconds), 0)).where(
                mine(user, UsageEntry.user_id, UsageEntry.email_hash),
                UsageEntry.counted_at <= since,
            )
        )
    )


def held_after_backlog(
    session: Session, user: User, now: datetime, exclude_entry_id: int | None = None
) -> list[list]:
    """`grants_in_force` with the backlog taken off, in spending order. What `hours.allocate`
    spends a new recording from (the recording itself is `exclude_entry_id`)."""
    held = grants_in_force(session, user, now)
    _take_off(held, unallocated_seconds(session, user, exclude_entry_id))
    return held


def _take_off(held: list[list], to_spend: int) -> None:
    """Take `to_spend` seconds off the grants in order; no grant goes below zero."""
    for row in held:
        taken = min(row[1], to_spend)
        row[1] -= taken
        to_spend -= taken


def status(session: Session, user: User, now: datetime, max_poll_seconds: int = 6 * 3600) -> Status:
    """The person's state and the seconds they may still record."""
    if user.is_admin:
        return Status("none", None, True, False, holdings_seconds=None)
    held = held_after_backlog(session, user, now)
    holdings = sum(g.seconds for g, _ in held)
    _take_off(held, usage.running_seconds(session, user, now, max_poll_seconds))
    left = sum(left for _, left in held)
    used = holdings - left
    plans = [g for g, _ in held if g.kind == "plan_period"]
    trials = [g for g, _ in held if g.kind == "trial"]
    # A person with a plan period in force is `active` even with its hours spent (they pay; a new
    # recording is refused for want of hours, not of a plan). Otherwise the first trial or pilot
    # grant that still has hours; a pack or a gift alone shows `none`.
    if plans:
        state = "active"
    else:
        shown = [GRANT_STATE[g.kind] for g, rest in held if rest > 0 and g.kind in GRANT_STATE]
        state = shown[0] if shown else "none"
    if held and left == 0:
        warning = OUT
    elif held and used * 100 >= holdings * LOW_PERCENT:
        warning = LOW
    else:
        warning = None
    offset = pre_pilot_seconds(session, user) if any(g.kind == "pilot" for g, _ in held) else 0
    anchor = max(plans, key=lambda g: (g.starts_at, g.id)) if plans else None
    if anchor is None and held:
        anchor = held[0][0]
    return Status(
        state=state,
        seconds_left=left,
        unlimited=False,
        has_grant=bool(held),
        holdings_seconds=holdings,
        warning=warning,
        period_ends_at=max((g.expires_at for g in plans if g.expires_at), default=None),
        trial_ends_at=max((g.expires_at for g in trials if g.expires_at), default=None),
        period_key=f"{anchor.kind}{anchor.id}" if anchor else "none",
        display_offset_seconds=offset,
    )


def _reserved_beyond_elapsed(
    session: Session, user: User, now: datetime, default_seconds: int, max_poll_seconds: int
) -> tuple[int, int]:
    """For the person's own meetings whose bot is in the call (or was just sent) and that are not
    counted yet (a `processing` meeting has left and reserves nothing):
    `(how many, the seconds still set aside beyond what has elapsed)`. Each reserves
    `default_seconds`, or the calendar event's length when a calendar join made the meeting, never
    more than the poll cap; what has run already is counted by `status`, so only the rest is added.
    """
    counted = select(UsageEntry.meeting_id).where(UsageEntry.meeting_id.is_not(None))
    rows = session.execute(
        select(
            Meeting.id, Meeting.started_polling_at, ScheduledJoin.starts_at, ScheduledJoin.ends_at
        )
        .outerjoin(ScheduledJoin, ScheduledJoin.meeting_id == Meeting.id)
        .where(
            Meeting.owner_user_id == user.id,
            Meeting.status == usage.IN_THE_CALL,
            Meeting.started_polling_at.is_not(None),
            Meeting.id.not_in(counted),
        )
        .order_by(Meeting.id)
    ).all()
    # `scheduled_joins.meeting_id` is not unique: a meeting reserves once, whatever joins name it
    meetings = {}
    for meeting_id, started, event_start, event_end in rows:
        meetings.setdefault(meeting_id, (started, event_start, event_end))
    extra = 0
    for started, event_start, event_end in meetings.values():
        reserve = default_seconds
        if event_start is not None and event_end is not None and event_end > event_start:
            reserve = int((event_end - event_start).total_seconds())
        reserve = min(reserve, max_poll_seconds)
        elapsed = min(max((now - started).total_seconds(), 0), max_poll_seconds)
        extra += max(int(reserve - elapsed), 0)
    return len(meetings), extra


def check_join(
    session: Session,
    user: User,
    now: datetime,
    max_poll_seconds: int = 6 * 3600,
    *,
    reserve_seconds: int = 0,
    expected_seconds: int | None = None,
) -> JoinDecision:
    """What the new rule would decide for a new recording: allowed while any hour is left.

    With `reserve_seconds` (`HOURS_RESERVE_MINUTES`, enforce only) a SECOND concurrent meeting is
    held to more: a meeting just placed counts as about 0 s until it has run a while, so with 60 s
    left a person could start two. Every meeting of theirs that is running or just placed reserves
    `reserve_seconds` (its calendar event's length when known), and the new one reserves
    `expected_seconds` (its event's length) or `reserve_seconds`; the second meeting starts only if
    the hours left cover all of them (reason `reserve`). A first meeting is never held to it."""
    s = status(session, user, now, max_poll_seconds)
    if s.unlimited:
        return JoinDecision(True, None, s.seconds_left)
    if (s.seconds_left or 0) <= 0:
        return JoinDecision(False, NO_HOURS if s.has_grant else NO_PLAN, 0)
    if reserve_seconds > 0:
        running, extra = _reserved_beyond_elapsed(
            session, user, now, reserve_seconds, max_poll_seconds
        )
        # A calendar length of zero or less (an event that ends before it starts) is not a length.
        need = min(
            expected_seconds if expected_seconds and expected_seconds > 0 else reserve_seconds,
            max_poll_seconds,
        )
        if running and s.seconds_left - extra < need:
            return JoinDecision(False, RESERVE, s.seconds_left)
    return JoinDecision(True, None, s.seconds_left)


# ---- feature gates (billing spec 2.2 and 2.3, stage S3) ----

ASK = "ask"  # Ask Beyric
TRANSLATE = "translate"  # a NEW translation (an existing one is only read)
REWRITE = "rewrite"  # rewriting the notes with a template
FEATURES = (ASK, TRANSLATE, REWRITE)
# The states of spec 2.2 in which these features work. `trial`, `pilot` and `active` do
# everything, and `past_due` (the 3 days after a failed renewal) keeps Ask, translate and rewrite
# (they use no recorded hours). `lapsed` and `none` (no plan, an expired plan, a used-up or expired
# trial, a person who only reads) cannot. Anything not listed is closed: a state added later has to
# be opened here on purpose. `status` shows only trial, pilot, active and none until S8 adds the
# subscription states.
OPEN_STATES = frozenset({"trial", "pilot", "active", "past_due"})


@dataclass(frozen=True)
class GateDecision:
    allowed: bool
    state: str  # the person's state when asked (`unlimited` for the admin)


def state_allows(state: str, unlimited: bool = False) -> bool:
    """The whole rule of the three gates: the admin always, others by the state they are in."""
    return unlimited or state in OPEN_STATES


def gate(
    session: Session,
    user: User,
    feature: str,
    now: datetime,
    max_poll_seconds: int = 6 * 3600,
) -> GateDecision:
    """Whether `user` (the person pressing the button, not the meeting's recorder) may use
    `feature` now, with the state that decided it. Pure reading. All three features answer alike
    today; `feature` is checked so a typo cannot silently open or close a gate, and so a feature
    can get its own rule later."""
    if feature not in FEATURES:
        raise ValueError(f"unknown feature {feature!r}")
    s = status(session, user, now, max_poll_seconds)
    return GateDecision(state_allows(s.state, s.unlimited), "unlimited" if s.unlimited else s.state)


def can(
    session: Session,
    user: User,
    feature: str,
    now: datetime,
    max_poll_seconds: int = 6 * 3600,
) -> bool:
    """`gate(...).allowed`: the question every gated route asks (`app/feature_gates.py`)."""
    return gate(session, user, feature, now, max_poll_seconds).allowed
