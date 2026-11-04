"""The mailer: which backend is chosen, the Resend call, and the outbox that sends with retries and
scrubs the links it holds."""

import json
import logging
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from app import mail
from app.models import MailOutbox
from tests.test_web import make_settings

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
LINK = "https://notes.example.test/verify-email?token=SECRETTOKEN"


class FakeMailer:
    def __init__(self, fail: Exception | None = None):
        self.fail = fail
        self.sent: list[tuple[mail.Message, str]] = []

    def send(self, message: mail.Message, key: str) -> None:
        if self.fail:
            raise self.fail
        self.sent.append((message, key))


def queue(session, template="verify_email", payload=None, created=NOW, to="ada@example.test"):
    row = mail.enqueue(session, to, template, payload or {"link": LINK, "name": "Ada"})
    row.created_at = created
    session.commit()
    return row


# ---- which backend ----


def test_no_provider_and_no_key_in_production_means_no_mailer():
    assert mail.build_mailer(make_settings(app_env="production")) is None


def test_a_resend_key_selects_resend():
    settings = make_settings(app_env="production", resend_api_key="re_test_key")
    assert isinstance(mail.build_mailer(settings), mail.ResendMailer)


def test_development_falls_back_to_the_console_mailer():
    assert isinstance(mail.build_mailer(make_settings(app_env="development")), mail.ConsoleMailer)


def test_the_console_mailer_is_refused_in_production():
    with pytest.raises(RuntimeError, match="console"):
        mail.build_mailer(make_settings(app_env="production", mail_provider="console"))
    with pytest.raises(RuntimeError, match="console"):
        mail.check_settings(make_settings(app_env="production", mail_provider="console"))


def test_resend_without_a_key_is_refused_at_start():
    with pytest.raises(RuntimeError, match="RESEND_API_KEY"):
        mail.check_settings(make_settings(app_env="production", mail_provider="resend"))


def test_an_unknown_provider_is_refused():
    with pytest.raises(RuntimeError, match="mail_provider"):
        mail.check_settings(make_settings(mail_provider="smtp"))


# ---- the Resend call ----


def resend(handler, **settings):
    values = {"resend_api_key": "re_test_key", "mail_from": "Beyric <no-reply@example.test>"}
    transport = httpx.MockTransport(handler)
    return mail.ResendMailer(make_settings(**values | settings), transport=transport)


def test_resend_posts_the_message_with_an_idempotency_key():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["key"] = request.headers["idempotency-key"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "abc"})

    resend(handler).send(mail.Message("ada@example.test", "Hi", "plain", "<p>html</p>"), "mail-7")
    assert seen["url"] == "https://api.resend.com/emails"
    assert seen["auth"] == "Bearer re_test_key"
    assert seen["key"] == "mail-7"
    assert seen["body"] == {
        "from": "Beyric <no-reply@example.test>",
        "to": ["ada@example.test"],
        "subject": "Hi",
        "text": "plain",
        "html": "<p>html</p>",
    }


@pytest.mark.parametrize(
    "status, permanent", [(500, False), (429, False), (422, True), (403, True)]
)
def test_resend_errors_say_whether_to_retry_and_hold_no_detail(status, permanent):
    def handler(request):
        return httpx.Response(status, json={"message": "to ada@example.test is bad re_test_key"})

    with pytest.raises(mail.MailError) as caught:
        resend(handler).send(mail.Message("ada@example.test", "s", "t", "h"), "k")
    assert caught.value.permanent is permanent
    assert str(caught.value) == f"HTTP {status}"


def test_a_network_failure_is_a_retryable_error():
    def handler(request):
        raise httpx.ConnectError("boom ada@example.test")

    with pytest.raises(mail.MailError) as caught:
        resend(handler).send(mail.Message("a@b.test", "s", "t", "h"), "k")
    assert caught.value.permanent is False
    assert "ada@example" not in str(caught.value)


# ---- the outbox ----


def test_enqueue_adds_a_pending_row_and_does_not_commit(session):
    row = mail.enqueue(session, "Ada@Example.test", "verify_email", {"link": LINK})
    assert (row.to_email, row.status, row.attempts) == ("ada@example.test", "pending", 0)
    session.rollback()
    assert session.scalars(select(MailOutbox)).all() == []


def test_a_pending_mail_is_sent_and_its_payload_scrubbed(session):
    row = queue(session)
    mailer = FakeMailer()
    assert mail.deliver_pending(session, mailer, make_settings(), NOW) == 1
    [(message, key)] = mailer.sent
    assert message.to == "ada@example.test" and LINK in message.text and LINK in message.html
    assert key == f"mail-{row.id}"
    session.refresh(row)
    assert (row.status, row.payload, row.sent_at, row.attempts) == ("sent", {}, NOW, 1)


def test_a_failed_send_keeps_the_mail_pending_and_waits_one_minute(session):
    row = queue(session)
    failing = FakeMailer(mail.MailError("HTTP 500", permanent=False))
    assert mail.deliver_pending(session, failing, make_settings(), NOW) == 0
    session.refresh(row)
    assert (row.status, row.attempts, row.last_error) == ("pending", 1, "HTTP 500")
    assert row.payload["link"] == LINK  # still needed for the retry
    ok = FakeMailer()
    mail.deliver_pending(session, ok, make_settings(), NOW + timedelta(seconds=59))
    assert ok.sent == []  # not due yet
    mail.deliver_pending(session, ok, make_settings(), NOW + timedelta(minutes=1))
    assert len(ok.sent) == 1


def test_retries_are_one_five_and_thirty_minutes_after_each_other(session):
    row = queue(session)
    failing = FakeMailer(mail.MailError("HTTP 503", permanent=False))
    settings = make_settings()
    for offset in (0, 1, 6, 36):  # minutes after creation: the attempt, then each retry
        assert (
            mail.deliver_pending(session, failing, settings, NOW + timedelta(minutes=offset)) == 0
        )
        session.refresh(row)
    assert (row.status, row.attempts) == ("failed", 4)
    assert row.payload == {}  # nothing left that a token could be read from
    assert row.last_error == "HTTP 503"
    assert mail.deliver_pending(session, FakeMailer(), settings, NOW + timedelta(hours=1)) == 0


def test_a_permanent_error_fails_at_once_and_scrubs(session):
    row = queue(session)
    failing = FakeMailer(mail.MailError("HTTP 422", permanent=True))
    mail.deliver_pending(session, failing, make_settings(), NOW)
    session.refresh(row)
    assert (row.status, row.attempts, row.payload) == ("failed", 1, {})


def test_one_bad_row_does_not_stop_the_next(session):
    first = queue(session, template="no_such_template")
    second = queue(session)
    mailer = FakeMailer()
    assert mail.deliver_pending(session, mailer, make_settings(), NOW) == 1
    session.refresh(first)
    session.refresh(second)
    assert first.status == "failed" and first.last_error == "unknown template"
    assert second.status == "sent"


def test_an_unexpected_error_is_recorded_by_type_only(session, caplog):
    row = queue(session)
    caplog.set_level(logging.INFO)
    mail.deliver_pending(session, FakeMailer(RuntimeError(f"leaked {LINK}")), make_settings(), NOW)
    session.refresh(row)
    assert row.last_error == "RuntimeError"
    assert "SECRETTOKEN" not in caplog.text and "ada@example.test" not in caplog.text


def test_the_sweep_scrubs_unsent_mail_after_an_hour_and_fails_it(session):
    """A raw token sits in the payload until the mail is sent; backups must not hold it longer."""
    old = queue(session, created=NOW - timedelta(hours=1, minutes=1))
    fresh = queue(session, created=NOW - timedelta(minutes=30))
    mail.sweep_outbox(session, NOW)
    session.refresh(old)
    session.refresh(fresh)
    assert (old.status, old.payload, old.last_error) == ("failed", {}, "expired")
    assert (fresh.status, fresh.payload["link"]) == ("pending", LINK)


def test_the_sweep_scrubs_a_failed_row_after_an_hour_and_deletes_sent_after_30_days(session):
    failed = queue(session, created=NOW - timedelta(hours=2))
    failed.status = "failed"
    sent = queue(session, created=NOW - timedelta(days=31))
    sent.status, sent.payload = "sent", {}
    recent_sent = queue(session, created=NOW - timedelta(days=2))
    recent_sent.status, recent_sent.payload = "sent", {}
    session.commit()
    ids = failed.id, sent.id, recent_sent.id
    mail.sweep_outbox(session, NOW)
    session.expire_all()
    assert session.get(MailOutbox, ids[0]).payload == {}
    assert session.get(MailOutbox, ids[1]) is None
    assert session.get(MailOutbox, ids[2]) is not None


# ---- what each mail says ----


@pytest.mark.parametrize(
    "template", ["verify_email", "reset_password", "already_registered", "waitlist_invite"]
)
def test_every_template_renders_with_text_and_html(template):
    settings = make_settings()
    message = mail.render(template, {"link": LINK, "name": "Ada"}, "ada@example.test", settings)
    assert message.subject and message.text and message.html
    assert message.to == "ada@example.test"


def test_a_hostile_name_appears_nowhere_in_the_mail():
    message = mail.render(
        "verify_email", {"link": LINK, "name": "<script>x</script>"}, "a@b.test", make_settings()
    )
    assert "<script>" not in message.html and "script" not in message.text


def test_the_link_is_escaped_in_the_html_part():
    message = mail.render(
        "verify_email", {"link": 'https://x.test/?a=1&b="2"'}, "a@b.test", make_settings()
    )
    assert "&amp;b=&quot;2&quot;" in message.html


def test_the_reset_mail_says_it_expires_in_30_minutes():
    message = mail.render("reset_password", {"link": LINK}, "a@b.test", make_settings())
    assert "30 minutes" in message.text


def test_the_console_mailer_prints_the_link_only_for_development(capsys):
    mail.ConsoleMailer().send(mail.Message("a@b.test", "Hi", f"open {LINK}", "<p>h</p>"), "k")
    assert LINK in capsys.readouterr().out


def test_the_worker_sends_due_mail_and_does_nothing_without_a_mailer(session, monkeypatch):
    from app import worker

    row = queue(session)
    monkeypatch.setattr(worker, "get_settings", make_settings)
    assert worker.send_mail(session, None, NOW) == 0
    session.refresh(row)
    assert row.status == "pending"
    assert worker.send_mail(session, FakeMailer(), NOW) == 1
    session.refresh(row)
    assert row.status == "sent"


@pytest.mark.parametrize("name", ["Ada Lovelace", "O'Neil", "Zoë-Marie", "李雷"])
def test_a_plain_name_is_used_in_the_greeting(name):
    message = mail.render("verify_email", {"link": LINK, "name": name}, "a@b.test", make_settings())
    assert f"Hi {name}, " in message.text


@pytest.mark.parametrize(
    "name",
    [
        "Ada\n\nYour account is locked, open https://evil.test/x",
        "visit evil.test",
        "<b>x</b>",
        "a@b.test",
        "Ada 5",
        "x" * 61,
        "",
        None,
    ],
)
def test_a_name_that_is_not_just_a_name_is_dropped_from_the_greeting(name):
    message = mail.render(
        "waitlist_invite", {"link": LINK, "name": name}, "a@b.test", make_settings()
    )
    assert "Hi " not in message.text
    assert "evil" not in message.text and "evil" not in message.html


# ---- is mail configured? (no token is queued when nothing can send it) ----


def test_mail_is_configured_with_a_key_in_the_console_outside_production_and_not_otherwise():
    assert mail.is_configured(make_settings(app_env="production", resend_api_key="re_x"))
    assert mail.is_configured(make_settings(app_env="development"))
    assert mail.is_configured(make_settings(app_env="test"))
    assert not mail.is_configured(make_settings(app_env="production"))


def test_asking_whether_mail_is_configured_builds_no_client(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("a mailer (and its HTTP client) was built just to ask")

    monkeypatch.setattr(mail.ResendMailer, "__init__", refuse)
    assert mail.is_configured(make_settings(app_env="production", resend_api_key="re_x"))
