"""The one-time trial (billing spec 3.8, stage S10): hours given once to a person at their first
confirmed sign-in, so an account made after migration 0049 no longer holds zero hours.

A trial is a `trial` grant in `hour_grants`. Everything else (spending it first, the 80% and 100%
notices, `no_hours` at exactly the grant's size, `none` once it has ended) is the existing rule in
`app/entitlements.py` and `app/hours.py`; nothing here decides a join. This module only decides
whether to **give** one:

- **Off by default.** `TRIAL_SECONDS=0` gives nothing and writes nothing. Set, it is given in any
  mode (`HOURS_MODE` off, shadow or enforce), so a person who confirms before `enforce` already
  holds their hours when it comes on. Recordings are only split across grants under `enforce`;
  what a trial holder recorded before that has no allocation, and
  `entitlements.unallocated_seconds` (the same backlog rule as for pilot grants, `BACKLOG_KINDS`)
  takes it off the trial, so nobody gets those hours twice.
  `scripts/hours_reconcile_backlog.py` settles it into allocations.
- **Once per person.** Not given to an admin, to anyone who has held a `trial` or a `pilot` grant
  (a pilot account has its own hours, even when they are all used), or whose email fingerprint has:
  grants are found by user id or by the fingerprint (`entitlements.mine`), and a deleted account's
  grants keep their fingerprint (the user id is set null), so deleting an account and signing up
  again with the same address gives nothing.
- **The end.** The grant ends at the hours used (`entitlements` counts what is left) or
  `TRIAL_DAYS` days after it is given, whichever comes first. It is given the moment the email is
  confirmed (password link or Google), so "after sign-up" is the confirmation, as spec 3.8 says. A
  trial given late, by the sweep, starts when it is given: the 14 days are never spent waiting.
- **The brake.** `max_accounts` still limits how many people can confirm while sign-up is open
  (`app/signup.py`, unchanged). `TRIAL_DAILY_BUDGET_SECONDS` adds a second one on cost: no more than
  that many trial seconds are given in any 24 hours; a person past it holds no trial yet and
  `sweep` gives it when the window frees. 0 means no cap.

`give_on_confirmation` is called from the three places an account becomes confirmed, inside that
transaction and best effort (a failure is logged by class and never fails the sign-up).
`sweep` runs on the scheduler once `TRIAL_SECONDS` is set: it gives a trial to every confirmed
account that has none, which is also what closes the S2 "before enforce" gap (accounts made after
0049) at the moment the flag goes on.
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from app import usage
from app.config import Settings, get_settings
from app.entitlements import mine
from app.models import HourGrant, User

logger = logging.getLogger(__name__)

# Next to HOURS_LOCK (6020): one app-wide transaction lock, so the budget is read and spent by one
# worker at a time. Confirmations and the sweep are rare; nothing waits long.
TRIAL_LOCK = 6021
KINDS_THAT_USE_THE_TRIAL_UP = ("trial", "pilot")
WINDOW = timedelta(hours=24)
REASON = "trial: given once to a person when their email was first confirmed"


def enabled(settings: Settings) -> bool:
    """The trial is given whenever `TRIAL_SECONDS` is set, in any mode: a person who signs up
    before `HOURS_MODE=enforce` already holds their 3 hours when it comes on."""
    return settings.trial_seconds > 0


def had_one(
    session: Session, user: User, kinds: tuple[str, ...] = KINDS_THAT_USE_THE_TRIAL_UP
) -> bool:
    """Whether the person (by id or by email fingerprint) has ever held a grant of one of `kinds`
    (default: a trial or a pilot grant), in force or not."""
    return (
        session.scalar(
            select(HourGrant.id)
            .where(
                mine(user, HourGrant.user_id, HourGrant.email_hash),
                HourGrant.kind.in_(kinds),
            )
            .limit(1)
        )
        is not None
    )


def given_in_window(session: Session, now: datetime) -> int:
    """Trial seconds given in the 24 hours up to `now` (one given exactly 24 hours ago is out)."""
    return int(
        session.scalar(
            select(func.coalesce(func.sum(HourGrant.seconds), 0)).where(
                HourGrant.kind == "trial", HourGrant.starts_at > now - WINDOW
            )
        )
    )


def give(
    session: Session,
    user: User,
    now: datetime,
    settings: Settings | None = None,
    *,
    after_pilot: bool = False,
) -> HourGrant | None:
    """Give `user` the trial if they are owed one and the budget allows; else None. Flushes, never
    commits: the caller's transaction owns the row. Takes the app-wide trial lock first, so two
    workers cannot give one person two trials or spend one budget twice. `after_pilot` is the ONE
    exception to "a past pilot used the trial up": only a prior TRIAL grant then counts. It exists
    for the one-time go-live conversion of ended pilot accounts
    (`scripts/billing_close_test_data.py --trial-for-ended-pilot`); nothing else passes it."""
    settings = settings or get_settings()
    if not enabled(settings):
        return None
    if user.is_admin or user.status != "active" or user.email_verified_at is None:
        return None
    session.execute(
        text("select pg_advisory_xact_lock(:key)"),
        {"key": TRIAL_LOCK},
    )
    if had_one(session, user, ("trial",) if after_pilot else KINDS_THAT_USE_THE_TRIAL_UP):
        return None
    budget = settings.trial_daily_budget_seconds
    if budget and given_in_window(session, now) + settings.trial_seconds > budget:
        return None
    grant = HourGrant(
        user_id=user.id,
        email_hash=usage.email_hash(user.email),
        kind="trial",
        seconds=settings.trial_seconds,
        starts_at=now,
        expires_at=now + timedelta(days=settings.trial_days),
        reason=REASON,
    )
    session.add(grant)
    session.flush()
    return grant


def give_on_confirmation(
    session: Session, user: User, now: datetime, settings: Settings | None = None
) -> None:
    """`give`, from the moment an email is confirmed. Best effort and inside a savepoint: a failure
    is logged by class (never the message, it could carry parameters) and the confirmation goes on.
    The sweep gives the trial a minute later."""
    settings = settings or get_settings()
    if not enabled(settings):
        return  # off: not even a savepoint
    try:
        with session.begin_nested():
            give(session, user, now, settings)
    except Exception as exc:
        logger.warning("trial not given user=%s error=%s", user.id, type(exc).__name__)


def waiting(session: Session) -> list[User]:
    """Confirmed, active, non-admin accounts that hold no trial and no pilot grant, oldest
    confirmation first."""
    has_grant = (
        select(HourGrant.id)
        .where(
            or_(HourGrant.user_id == User.id, HourGrant.email_hash == _hash_of_user_email()),
            HourGrant.kind.in_(KINDS_THAT_USE_THE_TRIAL_UP),
        )
        .exists()
    )
    return list(
        session.scalars(
            select(User)
            .where(
                User.is_admin.is_(False),
                User.status == "active",
                User.email_verified_at.is_not(None),
                ~has_grant,
            )
            .order_by(User.email_verified_at, User.id)
        )
    )


def _hash_of_user_email():
    """`usage.email_hash` in SQL: sha256 of the trimmed, lower-cased email, as migration 0049
    computes it."""
    return func.encode(
        func.sha256(func.convert_to(func.lower(func.trim(User.email)), "UTF8")), "hex"
    )


def sweep(session: Session, now: datetime, settings: Settings | None = None) -> int:
    """Give a trial to every account that is owed one, while the budget lasts. Commits once per
    person given, so a failure part way keeps what was given. Returns how many were given."""
    settings = settings or get_settings()
    if not enabled(settings):
        return 0
    given = 0
    for user in waiting(session):
        try:
            grant = give(session, user, now, settings)
            session.commit()
        except Exception as exc:
            session.rollback()
            logger.warning("trial sweep failed user=%s error=%s", user.id, type(exc).__name__)
            continue
        if grant is not None:
            given += 1
        elif settings.trial_daily_budget_seconds and (
            given_in_window(session, now) + settings.trial_seconds
            > settings.trial_daily_budget_seconds
        ):
            break  # the budget is spent: everyone after this one waits too
    if given:
        logger.info("trial sweep gave %s trial(s)", given)
    return given
