"""The pilot grants' end (billing spec 3.9, stage S10): the pilot hours expire `PILOT_EXPIRY_DAYS`
days (60, decided 2026-11-16) after paid plans open, with one plain mail before.

**Nothing expires until Adebayo sets the date.** `PAID_PLANS_OPEN_ON` is unset by default. Set it
to the day paid plans open and, from the next scheduler run, every pilot grant that is still in
force gets that end (`expires_at`): the end of the day `open date + 60 days`, UTC, which is the
start of the next day. After that instant the grant is simply not in force, and the existing rule
(`entitlements.grants_in_force`) does the rest: the person's state, the join refusal and the
feature gates all read it, so expiry works even if the scheduler is down at that moment. Like the
trial, this only runs while the hours rule is in force (`app/trial.hours_rule_in_force`); under
`off` and `shadow` the old cap decides and an expiry date would change nothing but a mail.

**One mail before.** From `PILOT_EXPIRY_NOTICE_DAYS` days (14) before the end, each person who
holds pilot hours that are not used up gets one bell entry and one mail, once per person **per end
date** (the dedupe key names the date): moving the date later or earlier sends a corrected mail,
which is the honest thing, and never a second one for the same date. A person with nothing left, an
admin and anyone with no pilot grant are not written to. The text is in `NOTICE_TITLE` and
`notice_body`.

**A lawyer item.** The spec (3.9, section 9 question 10) says this changes what the pilot was
promised ("5 hours for the pilot", the Terms line), so the wording of the mail and the date itself
are for the lawyer. Do not set `PAID_PLANS_OPEN_ON` before the lawyer has read the text below; the
code never sets it.

**Safety.** If the end has already passed when the date is first seen and no mail was ever sent
for it, nothing is expired (and a warning is logged): nobody loses hours without having been told.
A grant that has already expired is never brought back by moving the date. Everything here is
idempotent, so a leader that loses its lock mid-run and a new one starting the same job cannot
disagree.
"""

import logging
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app import alerts, entitlements, trial
from app.config import Settings, get_settings
from app.models import HourGrant, Notification, User

logger = logging.getLogger(__name__)

KIND = "pilot.expiry"
NOTICE_TITLE = "Your pilot hours will expire"


def end_of(settings: Settings) -> datetime | None:
    """The instant the pilot grants stop counting: the start of the day after
    `PAID_PLANS_OPEN_ON + PILOT_EXPIRY_DAYS`, UTC. None while the date is unset."""
    opened = settings.paid_plans_open_on
    if opened is None:
        return None
    last_day = opened + timedelta(days=settings.pilot_expiry_days)
    return datetime(last_day.year, last_day.month, last_day.day, tzinfo=UTC) + timedelta(days=1)


def last_day_of(end: datetime) -> date:
    """The last day the hours can be used (the end is the start of the next day)."""
    return (end - timedelta(days=1)).date()


def _day(value: date) -> str:
    return f"{value.day} {value.strftime('%B %Y')}"


def dedupe_key(end: datetime) -> str:
    return f"{KIND}:{last_day_of(end).isoformat()}"


def notice_body(settings: Settings, end: datetime, seconds_left: int) -> str:
    """The mail, word for word. A lawyer item: see the module note."""
    opened = settings.paid_plans_open_on
    hours = f"{seconds_left / 3600:.1f}".removesuffix(".0")
    return (
        f"Paid plans opened on {_day(opened)}. The recorded hours you still have from the pilot "
        f"({hours} h) can be used until the end of {_day(last_day_of(end))} (UTC), "
        f"{settings.pilot_expiry_days} days later, and then they expire. "
        "After that your notes, transcripts and exports stay exactly as they are and you can still "
        "read, search and export them, but the note taker will not join new meetings, and Ask "
        "Beyric, new translations and rewriting notes with another template will not work, until "
        "you take a plan. Nothing is deleted. If you do not need more hours, you do not need to do "
        "anything."
    )


def _sync_ends(session: Session, end: datetime, now: datetime) -> int:
    """Give every pilot grant still in force the end. A grant already past its own end is left
    alone. Returns how many rows changed."""
    rows = session.scalars(
        select(HourGrant).where(
            HourGrant.kind == "pilot",
            or_(HourGrant.expires_at.is_(None), HourGrant.expires_at > now),
            or_(HourGrant.expires_at.is_(None), HourGrant.expires_at != end),
        )
    ).all()
    for row in rows:
        row.expires_at = end
    return len(rows)


def _notice_was_sent(session: Session, key: str) -> bool:
    return (
        session.scalar(select(Notification.id).where(Notification.dedupe_key == key).limit(1))
        is not None
    )


def _pilot_seconds_left(session: Session, user: User, now: datetime) -> int:
    """What is left of the person's pilot grants in force, after the usage that no allocation
    covers yet (what the rule itself would let them spend)."""
    held = entitlements.held_after_backlog(session, user, now)
    return sum(left for grant, left in held if grant.kind == "pilot")


def _holders(session: Session, now: datetime) -> list[User]:
    ids = select(HourGrant.user_id).where(
        HourGrant.kind == "pilot",
        HourGrant.user_id.is_not(None),
        HourGrant.starts_at <= now,
        or_(HourGrant.expires_at.is_(None), HourGrant.expires_at > now),
    )
    return list(
        session.scalars(
            select(User)
            .where(User.id.in_(ids), User.is_admin.is_(False), User.status == "active")
            .order_by(User.id)
        )
    )


def run(session: Session, now: datetime, settings: Settings | None = None) -> tuple[int, int]:
    """One pass of the job: set the end on the pilot grants, then send the one mail to each person
    in the notice window. Commits. Returns `(grants changed, people told)`. Does nothing while the
    date is unset or the hours rule is not in force."""
    settings = settings or get_settings()
    end = end_of(settings)
    if end is None or not trial.hours_rule_in_force(settings):
        return 0, 0
    key = dedupe_key(end)
    if end <= now and not _notice_was_sent(session, key):
        logger.warning(
            "pilot expiry date %s has passed and no notice was ever sent: nothing is expired",
            last_day_of(end).isoformat(),
        )
        return 0, 0
    changed = _sync_ends(session, end, now)
    told = 0
    if end - timedelta(days=settings.pilot_expiry_notice_days) <= now < end:
        for user in _holders(session, now):
            left = _pilot_seconds_left(session, user, now)
            if left <= 0:
                continue
            before = _notice_count(session, user, key)
            alerts.notify_user(
                session,
                user.id,
                kind=KIND,
                severity="info",
                title=NOTICE_TITLE,
                body=notice_body(settings, end, left),
                dedupe_key=key,
                email_template=alerts.ALERT_TEMPLATE,
            )
            told += _notice_count(session, user, key) - before
    session.commit()
    if changed or told:
        logger.info("pilot expiry: %s grant(s) given an end, %s person(s) told", changed, told)
    return changed, told


def _notice_count(session: Session, user: User, key: str) -> int:
    return len(
        session.scalars(
            select(Notification.id).where(
                Notification.user_id == user.id, Notification.dedupe_key == key
            )
        ).all()
    )
