"""Telling existing accounts that the privacy policy changed (a notification, once per account,
never a forced re-acceptance), and that the notice and the version stay in step."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app import legal_notice, maintenance, signup
from app.models import Notification
from tests.test_api import anon, api  # noqa: F401  (fixtures used by name)
from tests.test_web import (  # noqa: F401
    anon_client,
    app,
    fake_openai,
    fake_vexa,
    make_user,
    owner,
)

DAY = datetime.strptime(signup.TERMS_VERSION, "%Y-%m-%d").replace(tzinfo=UTC)
NOW = DAY + timedelta(hours=14)


def notices(session, user=None):
    query = select(Notification).where(Notification.kind == legal_notice.KIND)
    if user is not None:
        query = query.where(Notification.user_id == user.id)
    return list(session.scalars(query))


def test_the_notice_is_for_the_version_the_pages_show():
    """Bumping `TERMS_VERSION` without writing a new notice must fail here, so the people who
    already have an account are told about every version, and about this one only once."""
    assert legal_notice.VERSION == signup.TERMS_VERSION


def test_an_account_from_before_the_update_gets_one_plain_notice(session, owner):
    legal_notice.announce(session, NOW)
    (note,) = notices(session, owner)
    assert (note.severity, note.read_at) == ("info", None)
    assert note.title == "We updated our Privacy Policy"
    for words in ("notes of a meeting are ready", "Resend", "30 days", "Email preferences"):
        assert words in note.body
    assert "/privacy" in note.body
    assert note.dedupe_key == f"legal.updated:{signup.TERMS_VERSION}"


def test_running_it_again_adds_nothing_and_does_not_undo_a_read(session, owner):
    legal_notice.announce(session, NOW)
    (note,) = notices(session, owner)
    note.read_at = NOW
    session.commit()
    legal_notice.announce(session, NOW + timedelta(hours=1))
    legal_notice.announce(session, NOW + timedelta(days=3))
    (again,) = notices(session, owner)
    assert again.id == note.id and again.read_at == NOW


def test_only_active_confirmed_accounts_that_did_not_accept_this_version_are_told(session):
    old = make_user(session, "old@example.test", password=None, terms_version="2026-11-03")
    never = make_user(session, "never@example.test", password=None)  # the owner: no sign-up form
    current = make_user(
        session, "new@example.test", password=None, terms_version=signup.TERMS_VERSION
    )
    unconfirmed = make_user(session, "u@example.test", password=None, email_verified_at=None)
    suspended = make_user(session, "s@example.test", password=None, status="suspended")
    deleted = make_user(session, "d@example.test", password=None, status="deleted")
    legal_notice.announce(session, NOW)
    told = {n.user_id for n in notices(session)}
    assert told == {old.id, never.id}
    assert not told & {current.id, unconfirmed.id, suspended.id, deleted.id}


def test_the_notice_survives_the_ninety_day_clean_up_and_is_not_made_twice(session, owner):
    """The sweep deletes notifications after 90 days. This one is kept, because its row is the
    record that the account was told: deleting it would tell the account again."""
    legal_notice.announce(session, NOW)
    (note,) = notices(session, owner)
    maintenance.run_hourly(session, NOW + timedelta(days=91))
    (again,) = notices(session, owner)
    assert again.id == note.id


def test_an_account_still_not_told_is_told_however_late_the_sweep_first_runs(session, owner):
    """If the deploy slips, or the worker was down, the notice is not lost with the date: there is
    no window after which it silently stops being written."""
    for days in (31, 200, 900):
        legal_notice.announce(session, DAY + timedelta(days=days))
        assert len(notices(session, owner)) == 1, days
        session.query(Notification).delete()
        session.commit()


def test_the_hourly_sweep_makes_the_notice(session, owner):
    maintenance.run_hourly(session, NOW)
    assert len(notices(session, owner)) == 1


def test_the_notice_is_in_the_notifications_api_and_counts_as_unread(api, session):
    legal_notice.announce(session, datetime.now(UTC))
    body = api.get("/api/notifications").json()
    (note,) = [n for n in body["notifications"] if n["kind"] == legal_notice.KIND]
    assert note["title"] == "We updated our Privacy Policy" and note["read_at"] is None
    assert api.post("/api/notifications/read-all").json() == {"unread_notifications": 0}
