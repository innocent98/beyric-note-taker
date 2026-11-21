"""`scripts/trial_report.py`: the dry run for the trial sweep. Numbers and
dates only, read-only, and it never gives anything."""

import importlib.util
from datetime import UTC, datetime, timedelta
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
    assert "NOT enforcing" in out
    assert "the trial is off" in out
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


def test_it_is_read_only_and_gives_nothing(session):
    make_user(session, "ada@example.test")
    from sqlalchemy import text as sql

    session.execute(sql("SET TRANSACTION READ ONLY"))
    report.build(session, make_settings(hours_mode="enforce", trial_seconds=3 * HOUR), NOW)
    with pytest.raises(DBAPIError):
        session.execute(sql("update users set name = 'x'"))
    session.rollback()
    assert session.scalars(select(HourGrant)).all() == []


def _trial(session, name, started):
    user = make_user(session, f"{name}@example.test")
    session.add(
        HourGrant(
            user_id=user.id,
            email_hash=email_hash(user.email),
            kind="trial",
            seconds=3 * HOUR,
            starts_at=started,
            expires_at=started + timedelta(days=14),
        )
    )
    session.commit()


def test_a_trial_with_time_left_before_enforce_is_a_note_not_a_warning(session):
    """The state between steps 2 and 5 of the switch-on: the proof must be able to pass."""
    _trial(session, "a", NOW - timedelta(days=2))  # ends in 12 days
    out = text(session, hours_mode="shadow", trial_seconds=3 * HOUR)
    assert "Trials in force: 1, the first ends 2027-03-13" in out
    assert "NOTE: 1 trial(s) in force end on their own clock, the first in 12 day(s)" in out
    assert "WARNING" not in out


def test_a_trial_nearly_over_or_already_ended_before_enforce_is_a_warning(session):
    _trial(session, "a", NOW - timedelta(days=13))  # ends in 1 day
    _trial(session, "b", NOW - timedelta(days=20))  # ended
    out = text(session, hours_mode="shadow", trial_seconds=3 * HOUR)
    assert "WARNING: 1 trial(s) in force end on their own clock, the first in 1 day(s)" in out
    assert "WARNING: 1 trial(s) have already ended while HOURS_MODE was not enforce" in out
    enforcing = text(session, hours_mode="enforce", trial_seconds=3 * HOUR)
    assert "WARNING" not in enforcing and "NOTE" not in enforcing
    assert "Trials in force: 1" in enforcing
