"""The "Could not join" mail and its bell entry, sent when a meeting fails because the bot was NOT
admitted: Vexa's `awaiting_admission_timeout` / `awaiting_admission_rejected`, or Google refusing
the bot's ask to join. Every other way a meeting ends keeps what it had (no mail, no bell)."""

import logging
from datetime import timedelta

import pytest
from sqlalchemy import select

from app import alerts, mail, mail_templates, pipeline
from app.models import MailOutbox, Notification, UserSettings
from app.vexa import VexaError, VexaMeeting
from tests.test_join_refusal import (  # noqa: F401  (fixtures used by name)
    HOST_DECLINED,
    NEEDS_HELP,
    REFUSED_29,
    fresh_send,
)
from tests.test_pipeline import (  # noqa: F401  (fixtures used by name)
    NOW,
    ZOOM_REFUSED,
    FakeVexa,
    env,
    meeting,
    run,
    zoom_meeting,
)
from tests.test_web import make_settings, owner  # noqa: F401

SEEN = NOW + timedelta(minutes=7)  # the poll that saw the failure: the time the mail must carry
TIMED_OUT = VexaMeeting(status="failed", completion_reason="awaiting_admission_timeout")
KIND = "meeting.not_admitted"


@pytest.fixture(autouse=True)
def mail_is_on(monkeypatch):
    monkeypatch.setattr(alerts, "get_settings", lambda: make_settings())


def bells(session):
    return list(session.scalars(select(Notification).order_by(Notification.id)))


def mails(session):
    return list(session.scalars(select(MailOutbox).order_by(MailOutbox.id)))


def told_nothing(session):
    assert bells(session) == [] and mails(session) == []


# ---- the not-admitted causes: one bell and one mail each ----


@pytest.mark.parametrize(
    "seen", [TIMED_OUT, HOST_DECLINED], ids=["waiting-room-timeout", "host-declined"]
)
def test_a_bot_that_was_not_admitted_tells_the_owner_once(session, meeting, owner, seen):
    run(session, meeting, FakeVexa(seen), now=SEEN)
    assert meeting.status == "failed" and meeting.failure_reason == pipeline.REASON_NOT_LET_IN
    (bell,) = bells(session)
    assert (bell.user_id, bell.kind, bell.severity) == (owner.id, KIND, "error")
    assert bell.meeting_id == meeting.id and bell.dedupe_key == f"{KIND}:{meeting.id}"
    assert bell.title == "The note taker could not join your meeting"
    assert bell.body.startswith("We tried to join Google Meet abc-defg-hij but were not admitted.")
    (sent,) = mails(session)
    assert (sent.to_email, sent.template) == (owner.email, mail_templates.NOT_JOINED)
    # the moment the failure was seen, not a later deadline
    assert sent.payload == {"meeting": "Google Meet abc-defg-hij", "at": "12:07 UTC"}


def test_google_refusing_the_bot_tells_the_owner_once(session, meeting, owner):
    fresh_send(session, meeting, pipeline.REFUSAL_MAX_SENDS)
    run(session, meeting, FakeVexa(REFUSED_29), now=SEEN)
    assert meeting.failure_reason == pipeline.REASON_JOIN_REFUSED
    (bell,) = bells(session)
    assert bell.kind == KIND and bell.meeting_id == meeting.id
    (sent,) = mails(session)
    assert sent.template == mail_templates.NOT_JOINED and sent.payload["at"] == "12:07 UTC"


def test_the_first_google_refusal_only_pauses_and_tells_nobody(session, meeting):
    meeting.vexa_meeting_id = "1"
    session.commit()
    run(session, meeting, FakeVexa(REFUSED_29), now=SEEN)
    assert meeting.status == "bot_sent"
    told_nothing(session)


def test_the_message_is_in_the_owners_time_zone(session, meeting, owner):
    session.add(UserSettings(user_id=owner.id, time_zone="Africa/Lagos"))
    session.commit()
    run(session, meeting, FakeVexa(TIMED_OUT), now=SEEN)
    (sent,) = mails(session)
    assert sent.payload["at"] == "13:07 GMT+1"


# ---- every other ending keeps the old behaviour ----


def _unknown_failure(session, meeting):
    return meeting, FakeVexa(VexaMeeting(status="failed", failure_reason="Chrome crashed"))


def _recording_failed(session, meeting):
    done = VexaMeeting(status="completed", recording_status="failed")
    return meeting, FakeVexa(done)


def _stopped_by_the_owner(session, meeting):
    meeting.completion_reason = pipeline.BOT_END_REQUESTED
    session.commit()
    return meeting, FakeVexa(TIMED_OUT)


def _send_failed_after_a_refusal(session, meeting):
    meeting.vexa_meeting_id = "1"
    meeting.join_attempts = 1
    meeting.completion_reason, meeting.terminal_seen_at = pipeline.MEET_REFUSED, NOW
    session.commit()
    vexa = FakeVexa(REFUSED_29)
    vexa.send_error = VexaError("connection refused")
    return meeting, vexa


def _needs_help(session, meeting):
    meeting.vexa_meeting_id = "1"
    session.commit()
    pipeline._needs_help_since[meeting.id] = NOW - pipeline.NEEDS_HELP_GRACE
    return meeting, FakeVexa(NEEDS_HELP)


@pytest.mark.parametrize(
    "setup",
    [
        _unknown_failure,
        _recording_failed,
        _stopped_by_the_owner,
        _send_failed_after_a_refusal,
        _needs_help,
    ],
    ids=lambda f: f.__name__.strip("_"),
)
def test_other_causes_send_neither_a_mail_nor_a_bell(session, meeting, setup):
    target, vexa = setup(session, meeting)
    run(session, target, vexa, now=SEEN + pipeline.NEEDS_HELP_GRACE)
    assert target.status == "failed"
    told_nothing(session)


def test_zoom_blocking_the_bot_sends_neither(session, zoom_meeting):
    """Zoom's refusal also reports `awaiting_admission_rejected`, but it is not a waiting room."""
    zoom_meeting.join_attempts = pipeline.MAX_JOIN_ATTEMPTS
    session.commit()
    run(session, zoom_meeting, FakeVexa(ZOOM_REFUSED), now=SEEN)
    assert zoom_meeting.failure_reason.startswith("Zoom blocked the bot")
    told_nothing(session)


def test_a_recording_that_never_appears_sends_neither(session, meeting):
    vexa = FakeVexa(VexaMeeting(status="completed"))
    for _ in range(pipeline.NO_AUDIO_GRACE_POLLS):
        run(session, meeting, vexa, now=SEEN)
    assert meeting.status == "failed" and meeting.failure_reason == pipeline.REASON_NOT_ADMITTED
    told_nothing(session)


# ---- once per meeting ----


def test_a_second_failure_of_the_same_meeting_is_not_told_again(session, meeting):
    run(session, meeting, FakeVexa(TIMED_OUT), now=SEEN)
    meeting.status, meeting.step = "bot_sent", "waiting"  # sent again by hand, refused again
    session.commit()
    run(session, meeting, FakeVexa(TIMED_OUT), now=SEEN + timedelta(minutes=30))
    assert meeting.status == "failed"
    assert len(bells(session)) == 1 and len(mails(session)) == 1


def test_two_meetings_each_get_their_own(session, meeting, owner):
    run(session, meeting, FakeVexa(TIMED_OUT), now=SEEN)
    other = type(meeting)(
        owner_user_id=owner.id,
        url="https://meet.google.com/zzz-zzzz-zzz",
        platform="google_meet",
        native_id="zzz-zzzz-zzz",
        template_id=meeting.template_id,
        started_polling_at=NOW,
    )
    session.add(other)
    session.commit()
    run(session, other, FakeVexa(TIMED_OUT), now=SEEN)
    assert len(bells(session)) == 2 and len(mails(session)) == 2


# ---- a problem in telling never stops the meeting failing ----


def test_a_failing_mail_build_still_fails_the_meeting_and_rings_the_bell(
    session, meeting, monkeypatch, caplog
):
    def broken(*args, **kwargs):
        raise ValueError("the zone said ada@example.test")

    monkeypatch.setattr(mail_templates, "not_joined_payload", broken)
    caplog.set_level(logging.WARNING)
    run(session, meeting, FakeVexa(TIMED_OUT), now=SEEN)
    assert meeting.status == "failed" and meeting.failure_reason == pipeline.REASON_NOT_LET_IN
    (bell,) = bells(session)
    assert bell.kind == KIND
    # the owner is still told by mail, in the plain alert's words, not left with nothing
    (sent,) = mails(session)
    assert sent.template == alerts.ALERT_TEMPLATE
    assert sent.payload == {"title": bell.title, "body": bell.body}
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "ValueError" in logged and "ada@example.test" not in logged


def test_a_database_error_while_building_the_mail_is_rolled_back_alone(
    session, meeting, monkeypatch
):
    def half_done(session, *args, **kwargs):
        session.add(Notification(user_id=1, kind="x", title="x", dedupe_key="x"))
        session.flush()
        raise RuntimeError("after the write")

    monkeypatch.setattr(mail_templates, "not_joined_payload", half_done)
    run(session, meeting, FakeVexa(TIMED_OUT), now=SEEN)
    assert meeting.status == "failed"
    assert [b.kind for b in bells(session)] == [KIND]  # the half-written row is gone


def test_a_mail_that_cannot_be_queued_still_leaves_the_bell(session, meeting, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("queue is down")

    monkeypatch.setattr(mail, "enqueue", boom)
    run(session, meeting, FakeVexa(TIMED_OUT), now=SEEN)
    assert meeting.status == "failed"
    assert len(bells(session)) == 1 and mails(session) == []


def test_even_the_alert_call_failing_leaves_the_meeting_failed(
    session, meeting, monkeypatch, caplog
):
    def boom(*args, **kwargs):
        raise ValueError("a caller bug")

    monkeypatch.setattr(alerts, "notify_user", boom)
    caplog.set_level(logging.WARNING)
    run(session, meeting, FakeVexa(TIMED_OUT), now=SEEN)
    assert meeting.status == "failed" and meeting.failure_reason == pipeline.REASON_NOT_LET_IN
    assert "ValueError" in " ".join(r.getMessage() for r in caplog.records)


# ---- the owner's own choices ----


def test_mail_switched_off_gives_the_bell_but_no_mail(session, meeting, owner):
    session.add(UserSettings(user_id=owner.id, notify_email=False))
    session.commit()
    run(session, meeting, FakeVexa(TIMED_OUT), now=SEEN)
    assert len(bells(session)) == 1 and mails(session) == []


def test_a_suppressed_address_gets_the_bell_but_no_mail(session, meeting, owner):
    mail.suppress(session, owner.email, "unsubscribed")
    session.commit()
    run(session, meeting, FakeVexa(TIMED_OUT), now=SEEN)
    assert len(bells(session)) == 1 and mails(session) == []


def test_the_sent_mail_renders_without_the_send_again_button(session, meeting):
    run(session, meeting, FakeVexa(TIMED_OUT), now=SEEN)
    (sent,) = mails(session)
    message = mail_templates.render_link(
        sent.template, sent.payload, sent.to_email, make_settings()
    )
    assert "The note taker could not join your meeting" in message.text
    assert "Google Meet abc-defg-hij at 12:07 UTC" in message.text
    assert "Send it again" not in message.text
