"""`scripts/hours_shadow_report.py`: the comparison on a seeded database, the read-only rule, and
that nothing about a person but a rank is printed."""

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.exc import DBAPIError

from app.models import HourGrant, UsageEntry
from app.usage import email_hash
from tests.test_web import make_user

_spec = importlib.util.spec_from_file_location(
    "hours_shadow_report", Path(__file__).parent.parent / "scripts" / "hours_shadow_report.py"
)
report = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(report)

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
HOUR = 3600
POLL = 6 * HOUR


def give(session, user, seconds):
    session.add(
        HourGrant(
            user_id=user.id,
            email_hash=email_hash(user.email),
            kind="pilot",
            seconds=seconds,
            starts_at=NOW - timedelta(days=2),
        )
    )


def spend(session, user, seconds, days_ago=1):
    session.add(
        UsageEntry(
            user_id=user.id,
            email_hash=email_hash(user.email),
            seconds=seconds,
            counted_at=NOW - timedelta(days=days_ago),
        )
    )


@pytest.fixture
def seeded(session):
    """Four people: one agreeing, one with no grant, one with a spent grant but hours on the old
    rule, one the old rule refuses while a grant has hours; plus an admin and a deleted account."""
    same = make_user(session, "same@example.test")
    give(session, same, 5 * HOUR)  # what the migration gives: the whole 5 h cap, 2 h used since
    spend(session, same, 2 * HOUR)
    nogrant = make_user(session, "nogrant@example.test")
    spent = make_user(session, "spent@example.test")
    give(session, spent, HOUR)
    spend(session, spent, HOUR)  # new: nothing left; old: 4 h left (the cap is the default 5 h)
    over = make_user(session, "over@example.test")
    give(session, over, 9 * HOUR)
    spend(session, over, 5 * HOUR, days_ago=40)  # old: at the cap; new: a grant with hours
    make_user(session, "boss@example.test", admin=True)
    make_user(session, "gone@example.test", status="deleted")
    session.commit()
    return {"same": same, "nogrant": nogrant, "spent": spent, "over": over}


def people(session):
    return report.fetch_people(session, NOW - timedelta(days=30), NOW, POLL)


def test_the_transaction_is_read_only(session):
    report.open_read_only(session)
    with pytest.raises(DBAPIError):
        session.execute(report.text("create table x (a int)"))
    session.rollback()


def test_it_compares_the_two_rules_per_person(session, seeded):
    by_id = {r.user_id: r for r in people(session)}
    assert len(by_id) == 4  # no admin, no deleted account
    same, nogrant = by_id[seeded["same"].id], by_id[seeded["nogrant"].id]
    spent, over = by_id[seeded["spent"].id], by_id[seeded["over"].id]
    assert report.disagreement(same) is None
    assert (same.window_seconds, same.old_left, same.new_left) == (2 * HOUR, 3 * HOUR, 3 * HOUR)
    assert same.old_allows and same.new_allows
    assert report.disagreement(nogrant) == "old rule allows, new rule refuses: no grant in force"
    assert report.disagreement(spent) == "old rule allows, new rule refuses: grant hours spent"
    assert report.disagreement(over) == "old rule refuses, new rule allows"
    assert over.window_seconds == 0  # its usage is older than the window


def test_the_render_prints_ranks_counts_and_no_person_data(session, seeded):
    text = report.render(days=30, now=NOW, rows=people(session))
    assert "user 1:" in text and "user 4:" in text and "user 5:" not in text
    assert "4 account(s); 3 disagreement(s)." in text
    assert "1 x old rule refuses, new rule allows" in text
    for leak in ("example.test", "same@", "boss", "@"):
        assert leak not in text
    for user in seeded.values():
        assert email_hash(user.email) not in text


def test_people_are_ranked_by_hours_recorded_in_the_window():
    rows = [
        report.PersonRow(7, 0, 0, False, "none", 0, False, "no_plan"),
        report.PersonRow(3, 2 * HOUR, 0, False, "none", 0, False, "no_plan"),
        report.PersonRow(5, 2 * HOUR, 0, False, "none", 0, False, "no_plan"),
    ]
    assert [r.user_id for r in report.ranked(rows)] == [3, 5, 7]


def test_an_empty_database_says_so():
    assert "No accounts to compare." in report.render(days=30, now=NOW, rows=[])


def test_when_everything_agrees_it_says_so():
    row = report.PersonRow(1, HOUR, 4 * HOUR, True, "pilot", 4 * HOUR, True, None)
    assert "The two rules agree for everyone." in report.render(days=30, now=NOW, rows=[row])
