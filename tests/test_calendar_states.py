# ruff: noqa: F811
"""Connection states (plan C13, C14), against a real Postgres and `FakeGoogleCalendar` through the
real `GoogleSource`. Google bodies are SYNTHETIC (tests/fakes/google.py)."""

from datetime import timedelta

import pytest
from sqlalchemy import select

from app import alerts, calendar_states, mail
from app.calendar_source import AuthRevoked, RefreshTokenUnreadable, TransientError
from app.gcal_sync import SYNC_EVERY, sync_connection
from app.models import MailOutbox, Notification, User
from tests.fakes.google import FakeGoogleCalendar
from tests.test_gcal_sync import HOUR, NOW, joins, make_world, raw_event
from tests.test_gcal_sync_job import SETTINGS, connected, google, source, vault_key  # noqa: F401
from tests.test_web import make_settings


def bells(session, kind, user_id=None):
    query = select(Notification).where(Notification.kind == kind)
    if user_id is not None:
        query = query.where(Notification.user_id == user_id)
    return list(session.scalars(query))


def with_join(session, world, google, source):
    google.pages = [[raw_event()]]
    sync_connection(session, world.conn, source, now=NOW, settings=SETTINGS)
    (join,) = joins(session, world.user.id)
    return join


def break_grant(google):
    """Google revokes the grant: the refresh token is refused and cached access tokens stop."""
    google.valid_refresh_tokens.clear()
    google.expire_access_tokens()


def fail_lists(google, status=503):
    for index in range(google.list_calls, google.list_calls + 100):
        google.override("list", index, FakeGoogleCalendar.error(status, "backendError"))


def heal(google):
    google._overrides.clear()


def fail_for(session, world, source, minutes):
    for minute in range(5, minutes + 1, 5):
        at = NOW + timedelta(minutes=minute)
        sync_connection(session, world.conn, source, now=at, settings=SETTINGS)


def test_invalid_grant_sets_needs_reconnect_and_skips_calendar_joins_with_reason(
    session, google, source
):
    world = connected(session)
    join = with_join(session, world, google, source)
    break_grant(google)
    sync_connection(session, world.conn, source, now=NOW + SYNC_EVERY, settings=SETTINGS)
    session.refresh(world.conn)
    session.refresh(join)
    assert world.conn.status == "needs_reconnect"
    assert world.conn.needs_reconnect_reason == "revoked"
    assert (join.state, join.skip_reason) == ("skipped", "needs_reconnect")
    assert len(bells(session, "calendar.disconnected", world.user.id)) == 1


def test_reason_is_expired_when_expected_expiry_passed_else_revoked(session):
    world = make_world(session)
    world.conn.expected_expiry_at = NOW - timedelta(hours=1)
    exc = AuthRevoked("x")
    assert calendar_states.reason_for(world.conn, exc, NOW, testing=True) == "expired"
    assert calendar_states.reason_for(world.conn, exc, NOW, testing=False) == "revoked"
    world.conn.expected_expiry_at = NOW + timedelta(hours=1)
    assert calendar_states.reason_for(world.conn, exc, NOW, testing=True) == "revoked"
    unreadable = RefreshTokenUnreadable("x")
    assert calendar_states.reason_for(world.conn, unreadable, NOW, testing=True) == "error"


def test_transient_errors_under_thirty_minutes_change_nothing(session):
    world = make_world(session)
    for minutes in (0, 5, 10, 29):
        at = NOW + timedelta(minutes=minutes)
        calendar_states.on_sync_failure(session, world.conn, TransientError("x"), at)
    assert world.conn.status == "connected"
    assert bells(session, "calendar.degraded") == []


def test_thirty_minutes_of_errors_sets_degraded_and_one_warning_without_pausing_joins(
    session, google, source
):
    world = connected(session)
    join = with_join(session, world, google, source)
    fail_lists(google)
    fail_for(session, world, source, 40)
    session.refresh(world.conn)
    session.refresh(join)
    assert world.conn.status == "degraded"
    assert join.state == "scheduled"
    assert len(bells(session, "calendar.degraded", world.user.id)) == 1


def test_walks_connected_to_degraded_to_needs_reconnect_and_back(session, google, source):
    world = connected(session)
    join = with_join(session, world, google, source)
    fail_lists(google)
    fail_for(session, world, source, 40)
    assert world.conn.status == "degraded"
    heal(google)
    sync_connection(session, world.conn, source, now=NOW + timedelta(minutes=50), settings=SETTINGS)
    assert (world.conn.status, world.conn.error_since) == ("connected", None)
    break_grant(google)
    sync_connection(session, world.conn, source, now=NOW + timedelta(minutes=60), settings=SETTINGS)
    session.refresh(join)
    assert world.conn.status == "needs_reconnect" and join.state == "skipped"
    google.valid_refresh_tokens.add(FakeGoogleCalendar.REFRESH_TOKEN)
    world.conn.status, world.conn.needs_reconnect_reason = "connected", None
    assert (
        calendar_states.revive_on_reconnect(session, world.conn, NOW + timedelta(minutes=61)) == 1
    )


def test_reconnect_revives_joins_whose_deadline_has_not_passed_and_not_older_ones(
    session, google, source
):
    world = connected(session)
    google.pages = [[raw_event("new", code="aaa-aaaa-aaa"), raw_event("old", code="bbb-bbbb-bbb")]]
    sync_connection(session, world.conn, source, now=NOW, settings=SETTINGS)
    break_grant(google)
    sync_connection(session, world.conn, source, now=NOW + SYNC_EVERY, settings=SETTINGS)
    fresh, stale = joins(session, world.user.id)
    stale.deadline_at = NOW - HOUR  # its window closed while the person was disconnected
    session.commit()
    revived = calendar_states.revive_on_reconnect(session, world.conn, NOW + SYNC_EVERY)
    session.commit()
    session.refresh(fresh)
    session.refresh(stale)
    assert revived == 1
    assert (fresh.state, fresh.skip_reason) == ("scheduled", None)
    assert (stale.state, stale.skip_reason) == ("skipped", "needs_reconnect")


def test_decrypt_failure_sets_needs_reconnect_error_and_alerts_admin_without_crashing(
    session, google, source
):
    world = connected(session)
    admin = User(email="admin@example.test", password_hash="x", is_admin=True, status="active")
    session.add(admin)
    world.conn.refresh_token_enc = b"\x00" * 40
    session.commit()
    sync_connection(session, world.conn, source, now=NOW, settings=SETTINGS)
    session.refresh(world.conn)
    assert (world.conn.status, world.conn.needs_reconnect_reason) == ("needs_reconnect", "error")
    assert len(bells(session, "admin.calendar_token_unreadable", admin.id)) == 1


def test_expiry_warning_shows_under_24_hours_while_testing_flag_on(session):
    world = make_world(session)
    world.conn.expected_expiry_at = NOW + timedelta(hours=23)
    warned = calendar_states.expiry_warning(world.conn, testing=True, now=NOW)
    assert warned == world.conn.expected_expiry_at
    world.conn.expected_expiry_at = NOW + timedelta(days=3)
    assert calendar_states.expiry_warning(world.conn, testing=True, now=NOW) is None


def test_no_banner_and_no_expected_expiry_once_flag_off_even_for_connections_made_earlier(session):
    world = make_world(session)
    world.conn.expected_expiry_at = NOW + timedelta(hours=2)
    assert calendar_states.expiry_warning(world.conn, testing=False, now=NOW) is None


@pytest.fixture
def mail_on(monkeypatch):
    monkeypatch.setattr(alerts, "get_settings", lambda: make_settings())


def mails(session):
    return list(session.scalars(select(MailOutbox)))


def test_one_notification_and_one_email_per_disconnect(session, google, source, mail_on):
    world = connected(session)
    with_join(session, world, google, source)
    assert calendar_states.mark_needs_reconnect(session, world.conn, "revoked", NOW) == 1
    world.conn.status = "connected"  # the same connection, the same connect time
    calendar_states.mark_needs_reconnect(session, world.conn, "revoked", NOW)
    session.commit()
    assert len(bells(session, "calendar.disconnected", world.user.id)) == 1
    (row,) = mails(session)
    assert (row.template, row.to_email) == ("calendar_disconnected", world.user.email)


def test_a_second_disconnect_after_reconnecting_is_a_second_email(session, google, source, mail_on):
    world = connected(session)
    calendar_states.mark_needs_reconnect(session, world.conn, "revoked", NOW)
    world.conn.status = "connected"
    world.conn.connected_at = NOW + timedelta(days=1)  # connected again
    calendar_states.mark_needs_reconnect(session, world.conn, "revoked", NOW + timedelta(days=2))
    session.commit()
    assert len(mails(session)) == 2


def test_degraded_sends_no_email(session, mail_on):
    world = make_world(session)
    world.conn.error_since = NOW - timedelta(hours=1)
    calendar_states.mark_degraded(session, world.conn, NOW)
    session.commit()
    assert mails(session) == []


def test_email_skipped_when_notify_email_false(session, mail_on):
    world = make_world(session)
    world.user_settings.notify_email = False
    calendar_states.mark_needs_reconnect(session, world.conn, "revoked", NOW)
    session.commit()
    assert mails(session) == []
    assert len(bells(session, "calendar.disconnected", world.user.id)) == 1


def test_email_skipped_for_a_suppressed_address(session, mail_on):
    world = make_world(session)
    mail.suppress(session, world.user.email, "unsubscribed")
    calendar_states.mark_needs_reconnect(session, world.conn, "revoked", NOW)
    session.commit()
    assert mails(session) == []


def test_the_email_renders_in_the_designers_layout(session):
    message = mail.render("calendar_disconnected", {}, "ada@example.test", make_settings())
    assert message.subject == "Your calendar is disconnected"
    assert "Reconnect" in message.html and "/settings" in message.html
    assert "Email preferences" in message.html
    assert "/settings#email-preferences" in message.text


def test_count_is_joins_in_the_next_24_hours(session, google, source):
    world = connected(session)
    google.pages = [
        [
            raw_event("a", start=NOW + HOUR, code="aaa-aaaa-aaa"),
            raw_event("b", start=NOW + timedelta(hours=30), code="bbb-bbbb-bbb"),
        ]
    ]
    sync_connection(session, world.conn, source, now=NOW, settings=SETTINGS)
    assert calendar_states.mark_needs_reconnect(session, world.conn, "revoked", NOW) == 1
    assert {j.state for j in joins(session, world.user.id)} == {"skipped"}


def test_admin_told_when_more_than_one_user_disconnects_within_an_hour(session):
    admin = User(email="admin@example.test", password_hash="x", is_admin=True, status="active")
    session.add(admin)
    first = make_world(session, "a@example.test")
    second = make_world(session, "b@example.test")
    calendar_states.mark_needs_reconnect(session, first.conn, "revoked", NOW)
    session.commit()
    assert bells(session, "admin.calendar_disconnects", admin.id) == []
    later = NOW + timedelta(minutes=10)
    calendar_states.mark_needs_reconnect(session, second.conn, "revoked", later)
    session.commit()
    assert len(bells(session, "admin.calendar_disconnects", admin.id)) == 1
