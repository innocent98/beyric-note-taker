"""`scripts/trial_report.py`: the dry run for the trial sweep and the pilot expiry. Numbers and
dates only, read-only, and it never gives or expires anything."""

import importlib.util
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError

from app.models import HourGrant
from app.usage import email_hash
from tests.test_web import make_settings, make_user

_spec = importlib.util.spec_from_file_location(
    "trial_report", Path(__file__).parent.parent / "scripts" / "trial_report.py"
)
report = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(report)

NOW = datetime(2027, 3, 1, 9, 0, tzinfo=UTC)
HOUR = 3600


def text(session, **settings):
    values = {"hours_mode": "enforce", **settings}
    return "\n".join(report.build(session, make_settings(**values), NOW))


def test_everything_off_says_so_and_names_nobody(session):
    make_user(session, "ada@example.test")
    out = text(session, hours_mode="off")
    assert "NOT in force" in out
    assert "the trial is off" in out
    assert "PAID_PLANS_OPEN_ON is unset" in out
    assert "ada" not in out


def test_it_counts_who_would_get_a_trial_and_the_budget_days(session):
    for name in ("a", "b", "c", "d", "e"):
        make_user(session, f"{name}@example.test")
    pilot = make_user(session, "pilot@example.test")
    session.add(
        HourGrant(user_id=pilot.id, email_hash=email_hash(pilot.email), kind="pilot", seconds=HOUR)
    )
    session.commit()
    out = text(session, trial_seconds=3 * HOUR, trial_daily_budget_seconds=6 * HOUR)
    assert "hold no trial or pilot grant: 5." in out
    assert "2 trial(s) in any 24 hours" in out and "about 3 day(s)" in out
    free = text(session, trial_seconds=3 * HOUR)
    assert "all of them at once" in free and "given at once: 15" in free


def test_it_counts_the_people_the_pilot_mail_would_reach(session):
    for name, left in (("ada", 2 * HOUR), ("ben", 0)):
        user = make_user(session, f"{name}@example.test")
        session.add(
            HourGrant(
                user_id=user.id, email_hash=email_hash(user.email), kind="pilot", seconds=left
            )
        )
    session.commit()
    out = text(session, paid_plans_open_on=date(2027, 4, 1))
    assert "holding pilot grants in force: 2; with hours left (they would be mailed): 1." in out
    assert "until the end of 2027-05-31 (UTC)" in out
    assert "from 2027-05-18 UTC" in out


def test_a_past_end_is_flagged(session):
    out = text(session, paid_plans_open_on=date(2026, 1, 1))
    assert "already past" in out


def test_it_is_read_only_and_gives_nothing(session):
    make_user(session, "ada@example.test")
    from sqlalchemy import text as sql

    session.execute(sql("SET TRANSACTION READ ONLY"))
    report.build(session, make_settings(hours_mode="enforce", trial_seconds=3 * HOUR), NOW)
    with pytest.raises(DBAPIError):
        session.execute(sql("update users set name = 'x'"))
    session.rollback()
    assert session.scalars(select(HourGrant)).all() == []
