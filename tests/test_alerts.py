"""C11b: the alerts helper. One call writes the in-app notification (the bell) and, when asked,
queues the email; it dedupes by key and never lets a mail problem reach its caller."""

import logging
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app import alerts, mail
from app.models import MailOutbox, Notification, UserSettings
from tests.test_mail import FakeMailer
from tests.test_web import make_settings, make_user

NOW = datetime(2026, 11, 4, 10, 0, tzinfo=UTC)
MEETING_URL = "https://us05web.zoom.us/j/88205679674?pwd=SECRETPASSCODE"


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    """Mail is configured (the console provider outside production) and admin alerts go to one
    address, unless a test says otherwise."""
    settings = make_settings(admin_alert_email="ops@example.test")
    monkeypatch.setattr(alerts, "get_settings", lambda: settings)
    return settings


def use_settings(monkeypatch, **overrides):
    settings = make_settings(**overrides)
    monkeypatch.setattr(alerts, "get_settings", lambda: settings)
    return settings


@pytest.fixture
def user(session):
    return make_user(session, "ada@example.test")


def notify(session, user, **overrides):
    values = {
        "kind": "calendar.disconnected",
        "severity": "warning",
        "title": "Your calendar was disconnected",
        "body": "Reconnect it to keep joining meetings.",
        "dedupe_key": "calendar.disconnected:1",
    }
    alerts.notify_user(session, user.id, **{**values, **overrides})
    session.commit()


def rows(session, user=None) -> list[Notification]:
    query = select(Notification).order_by(Notification.id)
    if user is not None:
        query = query.where(Notification.user_id == user.id)
    return list(session.scalars(query))


def outbox(session) -> list[MailOutbox]:
    return list(session.scalars(select(MailOutbox).order_by(MailOutbox.id)))


# ---- bucket_key ----


def test_bucket_key_is_the_same_inside_a_bucket_and_changes_at_the_boundary():
    start = datetime(2026, 11, 4, 10, 0, tzinfo=UTC)
    same = alerts.bucket_key("no_capacity", start)
    assert alerts.bucket_key("no_capacity", start + timedelta(minutes=29, seconds=59)) == same
    assert alerts.bucket_key("no_capacity", start + timedelta(minutes=30)) != same
    assert alerts.bucket_key("no_capacity", start - timedelta(seconds=1)) != same


def test_bucket_key_names_the_kind_and_takes_the_minutes():
    now = datetime(2026, 11, 4, 10, 0, tzinfo=UTC)
    assert alerts.bucket_key("a", now) != alerts.bucket_key("b", now)
    assert alerts.bucket_key("a", now, 60) == alerts.bucket_key(
        "a", now + timedelta(minutes=59), 60
    )
    assert alerts.bucket_key("a", now).startswith("a:")
    assert len(alerts.bucket_key("x" * 64, now)) <= 200  # fits notifications.dedupe_key


def test_bucket_key_is_the_same_for_the_same_instant_in_any_time_zone():
    from zoneinfo import ZoneInfo

    lagos = NOW.astimezone(ZoneInfo("Africa/Lagos"))
    assert alerts.bucket_key("k", lagos) == alerts.bucket_key("k", NOW)


def test_bucket_key_refuses_a_naive_time_and_a_zero_bucket():
    with pytest.raises(ValueError):
        alerts.bucket_key("k", datetime(2026, 11, 4, 10, 0))
    with pytest.raises(ValueError):
        alerts.bucket_key("k", NOW, 0)


# ---- notify_user ----


def test_same_dedupe_key_writes_one_row(session, user):
    notify(session, user)
    notify(session, user)
    notify(session, user, title="A different title, same key")
    assert [n.title for n in rows(session, user)] == ["Your calendar was disconnected"]


def test_a_user_notification_carries_every_field_and_is_unread(session, user):
    from app.models import Meeting, Template

    template = Template(owner_user_id=user.id, name="General", instructions="Notes.")
    session.add(template)
    session.flush()
    meeting = Meeting(
        owner_user_id=user.id,
        template_id=template.id,
        platform="zoom",
        native_id="1",
        url="https://zoom.us/j/1",
        status="bot_sent",
    )
    session.add(meeting)
    session.commit()

    notify(session, user, severity="error", meeting_id=meeting.id)

    (row,) = rows(session, user)
    assert (row.kind, row.severity, row.body) == (
        "calendar.disconnected",
        "error",
        "Reconnect it to keep joining meetings.",
    )
    assert row.meeting_id == meeting.id and row.read_at is None


def test_the_same_key_for_two_users_writes_a_row_each(session, user):
    other = make_user(session, "bob@example.test")
    notify(session, user)
    notify(session, other)
    assert len(rows(session)) == 2


def test_thirty_minute_bucket_dedupes_then_alerts_again_in_the_next_bucket(session, user):
    start = datetime(2026, 11, 4, 10, 0, tzinfo=UTC)
    for minute in (0, 5, 29):
        key = alerts.bucket_key("no_capacity", start + timedelta(minutes=minute))
        notify(session, user, kind="no_capacity", dedupe_key=key)
    assert len(rows(session, user)) == 1
    key = alerts.bucket_key("no_capacity", start + timedelta(minutes=30))
    notify(session, user, kind="no_capacity", dedupe_key=key)
    assert len(rows(session, user)) == 2


def test_user_notify_respects_notify_email_false(session, user):
    session.add(UserSettings(user_id=user.id, notify_email=False))
    session.commit()
    notify(session, user, email_template="alert")
    assert len(rows(session, user)) == 1  # the bell is not email: it still rings
    assert outbox(session) == []


def test_user_notify_queues_one_mail_when_asked_and_only_for_a_new_row(session, user):
    session.add(UserSettings(user_id=user.id, notify_email=True))
    session.commit()
    notify(session, user, email_template="alert")
    notify(session, user, email_template="alert")  # same key: no second row, no second mail
    (row,) = outbox(session)
    assert row.to_email == "ada@example.test" and row.template == "alert"
    assert row.payload == {
        "title": "Your calendar was disconnected",
        "body": "Reconnect it to keep joining meetings.",
    }


def test_a_user_without_a_settings_row_gets_the_mail(session, user):
    notify(session, user, email_template="alert")
    assert len(outbox(session)) == 1


def test_no_email_template_means_no_mail(session, user):
    notify(session, user)
    assert outbox(session) == []


def test_a_suppressed_address_gets_the_bell_but_no_mail(session, user):
    mail.suppress(session, "ADA@example.test", "unsubscribed")
    session.commit()
    notify(session, user, email_template="alert")
    assert len(rows(session, user)) == 1 and outbox(session) == []


def test_an_inactive_user_gets_no_mail(session):
    suspended = make_user(session, "gone@example.test", status="suspended")
    notify(session, suspended, email_template="alert")
    assert outbox(session) == []


def test_no_mail_when_production_has_no_provider(session, user, monkeypatch):
    use_settings(monkeypatch, app_env="production")
    notify(session, user, email_template="alert")
    assert len(rows(session, user)) == 1 and outbox(session) == []


def test_mail_failure_never_raises_into_the_caller(session, user, monkeypatch, caplog):
    def boom(*args, **kwargs):
        raise RuntimeError("provider said ada@example.test is bad")

    monkeypatch.setattr(alerts.mail, "enqueue", boom)
    caplog.set_level(logging.WARNING)

    notify(session, user, email_template="alert")  # must not raise

    assert len(rows(session, user)) == 1  # the bell still has it
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "RuntimeError" in logged and "ada@example.test" not in logged
    # and the caller's transaction is still usable
    notify(session, user, dedupe_key="another")
    assert len(rows(session, user)) == 2


def test_a_database_error_in_the_mail_part_rolls_back_only_the_mail(session, user, monkeypatch):
    """A savepoint, not the caller's whole transaction: work the caller did before is kept."""
    real = mail.enqueue

    def half_done(session, to, template, payload):
        real(session, to, template, payload)
        raise RuntimeError("after the insert")

    monkeypatch.setattr(alerts.mail, "enqueue", half_done)
    other = make_user(session, "caller-work@example.test")  # the caller's own pending work
    alerts.notify_user(
        session,
        user.id,
        kind="k",
        severity="info",
        title="t",
        body="b",
        dedupe_key="k1",
        email_template="alert",
    )
    session.commit()
    assert outbox(session) == [] and len(rows(session, user)) == 1
    assert other.id is not None


def test_a_user_who_no_longer_exists_does_not_raise(session, caplog):
    caplog.set_level(logging.WARNING)
    alerts.notify_user(
        session, 999999, kind="k", severity="info", title="t", body="b", dedupe_key="k"
    )
    session.commit()
    assert rows(session) == []
    assert "IntegrityError" in caplog.text


def test_a_bad_severity_or_an_overlong_key_is_a_caller_bug_and_raises(session, user):
    with pytest.raises(ValueError):
        notify(session, user, severity="loud")
    with pytest.raises(ValueError):
        notify(session, user, dedupe_key="k" * 201)


def test_an_overlong_title_is_cut_not_refused(session, user):
    notify(session, user, title="T" * 500)
    assert len(rows(session, user)[0].title) == 200


# ---- notify_admins ----


@pytest.fixture
def admins(session):
    return [
        make_user(session, "root@example.test", admin=True),
        make_user(session, "second@example.test", admin=True),
    ]


def admin_notify(session, **overrides):
    values = {
        "kind": "admin.no_capacity",
        "title": "No bot slot free",
        "body": "ada@example.test, 10:00 UTC: the meeting could not be joined.",
        "dedupe_key": alerts.bucket_key("admin.no_capacity", NOW),
    }
    alerts.notify_admins(session, **{**values, **overrides})
    session.commit()


def test_every_admin_gets_a_row_and_one_email_goes_out_per_bucket(session, admins, user):
    admin_notify(session)
    admin_notify(session)  # same bucket: nothing new
    assert {n.user_id for n in rows(session)} == {a.id for a in admins}
    assert len(rows(session)) == 2
    (row,) = outbox(session)
    assert row.to_email == "ops@example.test" and row.template == "alert"

    next_bucket = alerts.bucket_key("admin.no_capacity", NOW + timedelta(minutes=30))
    admin_notify(session, dedupe_key=next_bucket)
    assert len(rows(session)) == 4 and len(outbox(session)) == 2


def test_an_ordinary_user_gets_no_admin_alert(session, admins, user):
    admin_notify(session)
    assert user.id not in {n.user_id for n in rows(session)}


def test_without_an_alert_address_each_admin_is_mailed_at_their_own(session, admins, monkeypatch):
    use_settings(monkeypatch)  # no ADMIN_ALERT_EMAIL
    admin_notify(session)
    admin_notify(session)
    assert sorted(r.to_email for r in outbox(session)) == [
        "root@example.test",
        "second@example.test",
    ]


def test_an_admin_with_notify_email_off_is_not_mailed_at_their_own(session, admins, monkeypatch):
    use_settings(monkeypatch)
    session.add(UserSettings(user_id=admins[1].id, notify_email=False))
    session.commit()
    admin_notify(session)
    assert [r.to_email for r in outbox(session)] == ["root@example.test"]
    assert len(rows(session)) == 2  # both still see it in the bell


def test_a_suspended_admin_is_skipped(session, admins):
    admins[1].status = "suspended"
    session.commit()
    admin_notify(session)
    assert {n.user_id for n in rows(session)} == {admins[0].id}


def test_a_suppressed_alert_address_gets_nothing_but_the_bell_still_rings(session, admins):
    mail.suppress(session, "ops@example.test", "bounce")
    session.commit()
    admin_notify(session)
    assert len(rows(session)) == 2 and outbox(session) == []


def test_admin_text_names_user_and_time_but_not_the_meeting_url(session, admins):
    body = (
        f"ada@example.test, 10:00 UTC: could not join the meeting at {MEETING_URL}. "
        "Also zoom.us/j/88205679674?pwd=SECRETPASSCODE and meet.google.com/abc-defg-hij."
    )
    admin_notify(session, body=body, title="Join failed https://meet.google.com/abc-defg-hij")

    texts = [n.title + " " + n.body for n in rows(session)]
    texts.append(" ".join(str(v) for v in outbox(session)[0].payload.values()))
    for text in texts:
        assert "zoom.us" not in text and "SECRETPASSCODE" not in text
        assert "meet.google.com" not in text and "https://" not in text
        assert "ada@example.test, 10:00 UTC" in text  # who and when survive
    assert "[link removed]" in texts[0]


def test_admin_alert_mail_renders_the_text_and_never_a_url(session, admins):
    admin_notify(session, body=f"ada@example.test, 10:00 UTC. {MEETING_URL}")
    mailer = FakeMailer()
    sent = mail.deliver_pending(
        session,
        mailer,
        make_settings(admin_alert_email="ops@example.test"),
        NOW + timedelta(days=30),
    )
    assert sent == 1
    (message, _key) = mailer.sent[0]
    assert message.to == "ops@example.test" and "No bot slot free" in message.subject
    assert "ada@example.test, 10:00 UTC" in message.text
    assert "zoom.us" not in message.text + message.html
    assert "SECRETPASSCODE" not in message.text + message.html


def test_a_user_alert_mail_renders_and_the_row_is_scrubbed_once_sent(session, user):
    notify(session, user, email_template="alert", body="Line one.\nLine two with <b>markup</b>.")
    mailer = FakeMailer()
    mail.deliver_pending(session, mailer, make_settings(), datetime.now(UTC) + timedelta(minutes=5))
    (message, _key) = mailer.sent[0]
    assert message.to == "ada@example.test"
    assert "Line one." in message.text and "Line two" in message.text
    assert "<b>markup</b>" not in message.html and "&lt;b&gt;markup&lt;/b&gt;" in message.html
    session.expire_all()
    (row,) = outbox(session)
    assert row.status == "sent" and row.payload == {}


def test_a_title_cannot_put_a_line_break_in_the_subject(session, user):
    notify(session, user, email_template="alert", title="Hello\r\nBcc: evil@example.test")
    mailer = FakeMailer()
    mail.deliver_pending(session, mailer, make_settings(), datetime.now(UTC) + timedelta(minutes=5))
    (message, _key) = mailer.sent[0]
    assert "\n" not in message.subject and "\r" not in message.subject


def test_an_admin_alert_must_have_a_kind_that_starts_with_admin(session, admins):
    with pytest.raises(ValueError):
        admin_notify(session, kind="no_capacity")
    assert rows(session) == []


def test_no_admin_at_all_is_logged_not_raised(session, caplog):
    caplog.set_level(logging.WARNING)
    admin_notify(session)
    assert rows(session) == [] and outbox(session) == []
    assert "no active admin" in caplog.text


def test_admin_mail_failure_never_raises_and_the_bell_still_has_it(session, admins, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("down")

    monkeypatch.setattr(alerts.mail, "enqueue", boom)
    admin_notify(session)  # must not raise
    assert len(rows(session)) == 2
