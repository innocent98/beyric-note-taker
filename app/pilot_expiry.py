"""The pilot grants' end (billing spec 3.9, stage S10): the pilot hours expire `PILOT_EXPIRY_DAYS`
days (60, decided 2026-11-16) after paid plans open, with one plain mail before.

**Nothing expires until Adebayo sets the date.** `PAID_PLANS_OPEN_ON` is unset by default. Set it
to the day paid plans open and, from the next scheduler run, every pilot grant that is still in
force gets that end (`expires_at`): the end of the day `open date + 60 days`, UTC, which is the
start of the next day. After that instant the grant is simply not in force, and the existing rule
(`entitlements.grants_in_force`) does the rest: the person's state, the join refusal and the
feature gates all read it, so expiry works even if the scheduler is down at that moment. The job
acts only while `HOURS_MODE=enforce`: under `off` and `shadow` the old cap decides, and an expiry
date would change nothing but a mail that describes a rule that is not on.

**One mail before, and never less than a minimum notice.** From `PILOT_EXPIRY_NOTICE_DAYS` days
(14) before the end, each confirmed person who holds pilot hours that are not used up gets one
bell entry and one mail, once per person **per end date** (the dedupe key names the date). The
effective end for a person is never sooner than `PILOT_EXPIRY_MIN_NOTICE_DAYS` (7) after their
mail: if the job first acts when the end is closer than that (the date was set late, `enforce`
came on late, the scheduler was down), that person's pilot grants are moved to the first midnight
UTC at least that far ahead and the mail names that date. Seven days, because the mail has to be
read by someone who checks their inbox about once a week, and a week still leaves time to take a
plan. Moving `PAID_PLANS_OPEN_ON` later sends one corrected mail for the new date; it never shortens
a date a person was already told (moving it earlier changes only people not yet mailed). A person
with nothing left, an admin, an unconfirmed or suspended account and anyone with no pilot grant are
not written to. The text is `notice_body`.

**A lawyer item.** The spec (3.9, section 9 question 10 and 3.7) says this changes what the pilot
was promised ("5 hours for the pilot", the Terms line) and that what we say about keeping data
after hours end is a lawyer question, so the wording of the mail and the date itself need
Adebayo's and the lawyer's confirmation. The mail says only what the Privacy Policy already says
about keeping notes and transcripts. Do not set `PAID_PLANS_OPEN_ON` before that; the code never
sets it.

**Safety.** If the end has already passed when the date is first seen and some pilot grant in
force was never given an end, nothing is expired and a warning is logged (every run until the date
is moved): nobody loses hours unwarned. A grant that has already expired is never brought back by
moving the date. Everything here is idempotent, so a leader that loses its lock mid-run and a new
one starting the same job cannot disagree.
"""

import logging
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app import alerts, entitlements
from app.config import Settings, get_settings
from app.models import HourGrant, Notification, User

logger = logging.getLogger(__name__)

KIND = "pilot.expiry"
NOTICE_TITLE = "Your pilot hours will expire"
DAY = timedelta(days=1)


def end_of(settings: Settings) -> datetime | None:
    """The instant the pilot grants stop counting: the start of the day after
    `PAID_PLANS_OPEN_ON + PILOT_EXPIRY_DAYS`, UTC. None while the date is unset."""
    opened = settings.paid_plans_open_on
    if opened is None:
        return None
    last_day = opened + timedelta(days=settings.pilot_expiry_days)
    return datetime(last_day.year, last_day.month, last_day.day, tzinfo=UTC) + DAY


def last_day_of(end: datetime) -> date:
    """The last day the hours can be used (the end is the start of the next day)."""
    return (end - DAY).date()


def earliest_end(now: datetime, settings: Settings) -> datetime:
    """The first midnight UTC that is at least `PILOT_EXPIRY_MIN_NOTICE_DAYS` after `now`."""
    soonest = now + timedelta(days=settings.pilot_expiry_min_notice_days)
    midnight = datetime(soonest.year, soonest.month, soonest.day, tzinfo=UTC)
    return midnight if midnight >= soonest else midnight + DAY


def _day(value: date) -> str:
    return f"{value.day} {value.strftime('%B %Y')}"


def dedupe_key(end: datetime) -> str:
    return f"{KIND}:{last_day_of(end).isoformat()}"


def _amount(seconds: int) -> str:
    if seconds < 3600:
        return f"{max((seconds + 59) // 60, 1)} min"
    return f"{seconds / 3600:.1f}".removesuffix(".0") + " h"


def notice_body(settings: Settings, end: datetime, seconds_left: int) -> str:
    """The mail, word for word. Needs Adebayo's and the lawyer's confirmation (module note). It
    names only what is switched on: the Ask, translation and rewriting sentence appears only when
    `FEATURE_GATES_MODE=enforce`. The keeping sentence is the Privacy Policy's own."""
    opened = settings.paid_plans_open_on
    closed = "new meetings"
    if settings.feature_gates_mode == "enforce":
        closed += (
            ", and Ask Beyric, new translations and rewriting notes with another template will "
            "not work,"
        )
    return (
        f"Paid plans opened on {_day(opened)}. The recorded hours you still have from the pilot "
        f"({_amount(seconds_left)}) can be used until the end of {_day(last_day_of(end))} (UTC), "
        f"and then they expire. After that the note taker will not join {closed} until you take "
        "a plan. You can still read, search and export your notes and transcripts, which we keep "
        "until you ask us to delete them or close your account. If you do not need more hours, "
        "you do not need to do anything."
    )


def _has_notice(session: Session, user_id: int, key: str) -> bool:
    return (
        session.scalar(
            select(Notification.id)
            .where(Notification.user_id == user_id, Notification.dedupe_key == key)
            .limit(1)
        )
        is not None
    )


def _in_force(now: datetime):
    return (
        HourGrant.kind == "pilot",
        HourGrant.starts_at <= now,
        or_(HourGrant.expires_at.is_(None), HourGrant.expires_at > now),
    )


def _sync_ends(session: Session, end: datetime, now: datetime) -> int:
    """Give every pilot grant still in force the end. A grant already past its own end is left
    alone, and a date its holder was already mailed is never shortened. Returns the rows changed."""
    changed = 0
    for grant in session.scalars(select(HourGrant).where(*_in_force(now))).all():
        if grant.expires_at == end:
            continue
        if (
            grant.expires_at is not None
            and end < grant.expires_at
            and grant.user_id is not None
            and _has_notice(session, grant.user_id, dedupe_key(grant.expires_at))
        ):
            continue
        grant.expires_at = end
        changed += 1
    return changed


def _pilot_seconds_left(session: Session, user: User, now: datetime) -> int:
    """What is left of the person's pilot grants in force, after the usage that no allocation
    covers yet (what the rule itself would let them spend)."""
    held = entitlements.held_after_backlog(session, user, now)
    return sum(left for grant, left in held if grant.kind == "pilot")


def _holders(session: Session, now: datetime) -> list[User]:
    """Confirmed, active, non-admin people holding a pilot grant in force."""
    ids = select(HourGrant.user_id).where(HourGrant.user_id.is_not(None), *_in_force(now))
    return list(
        session.scalars(
            select(User)
            .where(
                User.id.in_(ids),
                User.is_admin.is_(False),
                User.status == "active",
                User.email_verified_at.is_not(None),
            )
            .order_by(User.id)
        )
    )


def _their_end(session: Session, user: User, now: datetime) -> datetime | None:
    return session.scalar(
        select(HourGrant.expires_at)
        .where(HourGrant.user_id == user.id, HourGrant.expires_at.is_not(None), *_in_force(now))
        .order_by(HourGrant.expires_at.desc())
        .limit(1)
    )


def _push_end(session: Session, user: User, to: datetime, now: datetime) -> None:
    for grant in session.scalars(
        select(HourGrant).where(HourGrant.user_id == user.id, *_in_force(now))
    ).all():
        if grant.expires_at is None or grant.expires_at < to:
            grant.expires_at = to


def _notify(session: Session, user: User, settings: Settings, now: datetime) -> int:
    """The one mail for one person, if they are in their window and not yet told. Returns 1 when a
    notice was written."""
    their_end = _their_end(session, user, now)
    if their_end is None:
        return 0
    notice_days = timedelta(days=settings.pilot_expiry_notice_days)
    if not (their_end - notice_days <= now < their_end):
        return 0
    left = _pilot_seconds_left(session, user, now)
    if left <= 0:
        return 0
    if _has_notice(session, user.id, dedupe_key(their_end)):
        return 0
    if their_end - now < timedelta(days=settings.pilot_expiry_min_notice_days):
        # Closer than the minimum notice and not yet told: this person ends no sooner than that
        # after the mail.
        their_end = earliest_end(now, settings)
        _push_end(session, user, their_end, now)
        if _has_notice(session, user.id, dedupe_key(their_end)):
            return 0
    alerts.notify_user(
        session,
        user.id,
        kind=KIND,
        severity="info",
        title=NOTICE_TITLE,
        body=notice_body(settings, their_end, left),
        dedupe_key=dedupe_key(their_end),
        email_template=alerts.ALERT_TEMPLATE,
    )
    return 1


def run(session: Session, now: datetime, settings: Settings | None = None) -> tuple[int, int]:
    """One pass of the job: set the end on the pilot grants, then send the one mail to each person
    in their notice window. Commits. Returns `(grants changed, people told)`. Does nothing while
    the date is unset or `HOURS_MODE` is not `enforce`."""
    settings = settings or get_settings()
    end = end_of(settings)
    if end is None or settings.hours_mode != "enforce":
        return 0, 0
    changed = 0
    if end <= now:
        never_given_an_end = session.scalar(
            select(HourGrant.id).where(HourGrant.expires_at.is_(None), *_in_force(now)).limit(1)
        )
        if never_given_an_end is not None:
            logger.warning(
                "pilot expiry date %s has passed and some pilot hours were never given an end: "
                "nothing is expired; move PAID_PLANS_OPEN_ON",
                last_day_of(end).isoformat(),
            )
            return 0, 0
    else:
        changed = _sync_ends(session, end, now)
    told = sum(_notify(session, user, settings, now) for user in _holders(session, now))
    session.commit()
    if changed or told:
        logger.info("pilot expiry: %s grant(s) given an end, %s person(s) told", changed, told)
    return changed, told
