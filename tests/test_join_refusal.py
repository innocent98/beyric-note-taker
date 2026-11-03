"""A refused or stuck Google Meet join fails fast with a plain reason, and retries are polite.

Fake Vexa answers are built from what was seen on 2026-11-03 (v0.12.28: `needs_help` about two
minutes after the knock; meeting 10's "Could not locate join button" error) and from Vexa PR #1921
(v0.12.29: completion reason `google_meet_join_refused`, not verified live).
"""

from datetime import UTC, datetime, timedelta

import pytest

from app import pipeline
from app.models import Meeting
from app.services import Refused, retry_meeting
from app.vexa import VexaError, VexaMeeting
from tests.test_pipeline import (  # noqa: F401  (fixtures used by name)
    NOW,
    FakeVexa,
    env,
    meeting,
    run,
)
from tests.test_web import owner  # noqa: F401

REFUSED = "google_meet_join_refused"
REFUSED_29 = VexaMeeting(
    status="failed",
    completion_reason=REFUSED,
    failure_reason=(
        f"{REFUSED}: Google Meet refused the join on 3 of 3 knocks "
        "(CreateMeetingDevice answered HTTP 403); this is Google refusing the bot"
    ),
)
NO_JOIN_BUTTON = VexaMeeting(
    status="failed",
    failure_reason=(
        "Could not locate join button by any locale-agnostic or English selector after 60000ms "
        "(url=https://workspace.google.com/products/meet/?utm_source=x)"
    ),
)
NEEDS_HELP = VexaMeeting(status="needs_help")
LOBBY = VexaMeeting(status="awaiting_admission")
SECOND = timedelta(seconds=1)
MINUTE = timedelta(minutes=1)


def fresh_send(session, meeting, attempts):
    meeting.join_attempts = attempts
    meeting.vexa_meeting_id = "1"
    session.commit()


# ---- the refusal is recognised, the bot is stopped, the owner is told why ----


@pytest.mark.parametrize("seen", [REFUSED_29, NO_JOIN_BUTTON], ids=["v0.12.29", "join-button"])
def test_a_refusal_after_the_last_send_fails_with_the_plain_reason(session, meeting, seen):
    fresh_send(session, meeting, pipeline.REFUSAL_MAX_SENDS)
    vexa = FakeVexa(seen)
    run(session, meeting, vexa)
    assert meeting.status == "failed"
    assert meeting.failure_reason == pipeline.REASON_JOIN_REFUSED
    assert "try sending the bot again in a few minutes" in meeting.failure_reason
    assert "the host's Meet settings may be blocking guests" in meeting.failure_reason
    assert vexa.stopped == [("google_meet", "abc-defg-hij")]
    assert not getattr(vexa, "sent", [])
    assert meeting.completion_reason == REFUSED and meeting.terminal_seen_at == NOW


def test_a_refusal_reported_only_as_a_completion_reason_is_still_a_refusal(session, meeting):
    fresh_send(session, meeting, pipeline.REFUSAL_MAX_SENDS)
    run(session, meeting, FakeVexa(VexaMeeting(status="completed", completion_reason=REFUSED)))
    assert meeting.failure_reason == pipeline.REASON_JOIN_REFUSED


def test_a_join_button_error_that_is_not_the_meet_marketing_page_is_not_a_refusal(session, meeting):
    other = VexaMeeting(status="failed", failure_reason="Could not locate join button (url=x)")
    vexa = FakeVexa(other)
    fresh_send(session, meeting, pipeline.REFUSAL_MAX_SENDS)
    run(session, meeting, vexa)
    assert meeting.failure_reason.startswith(pipeline.REASON_BOT_FAILED)
    assert not getattr(vexa, "stopped", [])


def test_needs_help_fails_the_join_after_a_thirty_second_grace(session, meeting):
    fresh_send(session, meeting, pipeline.REFUSAL_MAX_SENDS)
    vexa = FakeVexa(NEEDS_HELP)
    run(session, meeting, vexa, now=NOW)
    assert meeting.status == "bot_sent" and not getattr(vexa, "stopped", [])
    run(session, meeting, vexa, now=NOW + 29 * SECOND)
    assert meeting.status == "bot_sent"
    run(session, meeting, vexa, now=NOW + 30 * SECOND)
    assert meeting.status == "failed"
    assert meeting.failure_reason == pipeline.REASON_JOIN_REFUSED
    assert vexa.stopped == [("google_meet", "abc-defg-hij")]


def test_needs_help_that_clears_resets_the_grace(session, meeting):
    fresh_send(session, meeting, pipeline.REFUSAL_MAX_SENDS)
    vexa = FakeVexa(NEEDS_HELP)
    run(session, meeting, vexa, now=NOW)
    vexa.polls = [LOBBY]
    run(session, meeting, vexa, now=NOW + 20 * SECOND)
    vexa.polls = [NEEDS_HELP]
    run(session, meeting, vexa, now=NOW + 40 * SECOND)  # a new needs_help run, 0 s old
    run(session, meeting, vexa, now=NOW + 60 * SECOND)  # 20 s old
    assert meeting.status == "bot_sent"
    run(session, meeting, vexa, now=NOW + 70 * SECOND)
    assert meeting.status == "failed"


def test_needs_help_never_ends_a_bot_that_is_already_recording(session, meeting):
    fresh_send(session, meeting, pipeline.REFUSAL_MAX_SENDS)
    live = VexaMeeting(status="needs_help", segments=[{"speaker": "Ada", "text": "hello"}])
    vexa = FakeVexa(live)
    run(session, meeting, vexa, now=NOW)
    run(session, meeting, vexa, now=NOW + 5 * MINUTE)
    assert meeting.status == "bot_sent" and not getattr(vexa, "stopped", [])


def test_needs_help_on_zoom_is_left_alone(session, meeting):
    meeting.platform, meeting.native_id = "zoom", "88205679674"
    fresh_send(session, meeting, pipeline.REFUSAL_MAX_SENDS)
    vexa = FakeVexa(NEEDS_HELP)
    run(session, meeting, vexa, now=NOW)
    run(session, meeting, vexa, now=NOW + 5 * MINUTE)
    assert meeting.status == "bot_sent" and not getattr(vexa, "stopped", [])


def test_a_failed_stop_does_not_stop_the_meeting_failing(session, meeting):
    fresh_send(session, meeting, pipeline.REFUSAL_MAX_SENDS)
    vexa = FakeVexa(REFUSED_29)
    vexa.stop_error = VexaError("connection refused")
    run(session, meeting, vexa)
    assert meeting.status == "failed"
    assert meeting.failure_reason == pipeline.REASON_JOIN_REFUSED


# ---- pacing: one polite retry, never inside the bot's own re-knocks ----


def test_the_first_refusal_stops_the_bot_and_waits_before_sending_again(session, meeting):
    vexa = FakeVexa(REFUSED_29)
    run(session, meeting, vexa)
    assert meeting.status == "bot_sent" and meeting.failure_reason is None
    assert vexa.stopped == [("google_meet", "abc-defg-hij")]
    assert not getattr(vexa, "sent", [])
    assert meeting.join_attempts == 1
    assert meeting.completion_reason == REFUSED and meeting.terminal_seen_at == NOW


def test_the_retry_waits_four_minutes_plus_jitter_and_ignores_the_old_bot(session, meeting):
    vexa = FakeVexa(REFUSED_29)
    run(session, meeting, vexa)
    looked = vexa.get_calls
    gap = pipeline.refusal_retry_gap(meeting)
    assert timedelta(minutes=4) <= gap <= timedelta(minutes=5)
    run(session, meeting, vexa, now=NOW + gap - SECOND)
    assert not getattr(vexa, "sent", []) and vexa.get_calls == looked  # no lookup while paused
    run(session, meeting, vexa, now=NOW + gap)
    assert vexa.sent == [meeting.url]
    assert meeting.join_attempts == 2 and meeting.vexa_meeting_id == "101"
    assert meeting.status == "bot_sent" and meeting.failure_reason is None
    assert meeting.completion_reason is None and meeting.terminal_seen_at is None


def test_the_gap_is_stable_for_a_meeting_and_varies_between_meetings(session, meeting):
    assert pipeline.refusal_retry_gap(meeting) == pipeline.refusal_retry_gap(meeting)
    gaps = set()
    for meeting_id in range(1, 40):
        probe = Meeting(id=meeting_id, join_attempts=1)
        gaps.add(pipeline.refusal_retry_gap(probe))
    assert len(gaps) > 10


def test_the_second_bot_being_refused_fails_the_meeting_with_no_third_send(session, meeting):
    vexa = FakeVexa(REFUSED_29)
    run(session, meeting, vexa)
    run(session, meeting, vexa, now=NOW + 5 * MINUTE)  # sends the second bot
    run(session, meeting, vexa, now=NOW + 5 * MINUTE + 30 * SECOND)  # it is refused too
    assert meeting.status == "failed" and meeting.failure_reason == pipeline.REASON_JOIN_REFUSED
    assert len(vexa.sent) == 1
    assert vexa.stopped == [("google_meet", "abc-defg-hij")] * 2


def test_the_second_bot_getting_in_records_normally(session, meeting):
    vexa = FakeVexa(REFUSED_29)
    run(session, meeting, vexa)
    run(session, meeting, vexa, now=NOW + 5 * MINUTE)
    vexa.polls = [pipeline_done()]
    run(session, meeting, vexa, now=NOW + 6 * MINUTE)
    assert (meeting.status, meeting.step) == ("ready", "done")
    assert vexa.id_lookups[-1] == "101"


def pipeline_done():
    from tests.test_pipeline import DONE

    return DONE


def test_a_send_that_fails_after_the_pause_fails_the_meeting_with_the_reason(session, meeting):
    vexa = FakeVexa(REFUSED_29)
    run(session, meeting, vexa)
    vexa.send_error = VexaError("connection refused")
    run(session, meeting, vexa, now=NOW + 5 * MINUTE)
    assert meeting.status == "failed"
    assert meeting.failure_reason.startswith(pipeline.REASON_JOIN_REFUSED)
    assert "A new bot could not be sent." in meeting.failure_reason


def test_a_restart_during_the_pause_still_sends_once_it_is_due(session, meeting):
    run(session, meeting, FakeVexa(REFUSED_29))
    session.expire_all()  # nothing about the pause lives in memory
    vexa = FakeVexa(REFUSED_29)
    run(session, meeting, vexa, now=NOW + 6 * MINUTE)
    assert vexa.sent == [meeting.url]


def test_the_six_hour_limit_still_applies_during_the_pause(session, meeting):
    meeting.started_polling_at = NOW - timedelta(hours=7)
    session.commit()
    run(session, meeting, FakeVexa(REFUSED_29))
    assert meeting.status == "failed" and "within 6 hours" in meeting.failure_reason


# ---- nothing waits on a refused knock for long ----

# What the worker sees every 30 s from the knock on, as v0.12.28 behaved on 2026-11-03.
V28_TIMELINE = [LOBBY] * 4 + [NEEDS_HELP] * 8
V29_TIMELINE = [LOBBY, LOBBY, REFUSED_29]
BUTTON_TIMELINE = [VexaMeeting(status="joining")] * 4 + [NO_JOIN_BUTTON]


@pytest.mark.parametrize(
    "timeline",
    [V28_TIMELINE, V29_TIMELINE, BUTTON_TIMELINE],
    ids=["v0.12.28", "v0.12.29", "button"],
)
@pytest.mark.parametrize("attempts", [1, 2])
def test_a_refused_knock_is_settled_within_three_minutes(session, meeting, timeline, attempts):
    fresh_send(session, meeting, attempts)
    vexa = FakeVexa(timeline[0])
    settled_at = None
    for tick, seen in enumerate(timeline):
        vexa.polls = [seen]
        at = NOW + tick * 30 * SECOND
        run(session, meeting, vexa, now=at)
        session.refresh(meeting)
        if meeting.status == "failed" or meeting.completion_reason == REFUSED:
            settled_at = at - NOW
            break
    assert settled_at is not None and settled_at <= timedelta(minutes=3)
    assert vexa.stopped == [("google_meet", "abc-defg-hij")]  # the slot is free at once
    if attempts == 2:
        assert meeting.status == "failed"


# ---- the owner sees why, and a manual send is paced ----


def refused_meeting(session, meeting, *, ago):
    now = datetime.now(UTC)
    meeting.status, meeting.failure_reason = "failed", pipeline.REASON_JOIN_REFUSED
    meeting.completion_reason, meeting.terminal_seen_at = REFUSED, now - ago
    meeting.join_attempts = pipeline.REFUSAL_MAX_SENDS
    meeting.vexa_meeting_id = "1"
    session.commit()
    return meeting


def test_a_manual_send_is_refused_for_five_minutes_after_a_refusal(session, meeting):
    from app import services

    refused_meeting(session, meeting, ago=2 * MINUTE)
    assert services.retry_block(meeting) == "no_audio"
    assert "3 minutes" in services.retry_block_message(meeting, "no_audio")
    vexa = FakeVexa()
    with pytest.raises(Refused) as caught:
        retry_meeting(session, meeting, vexa)
    assert caught.value.status == 409
    assert "3 minutes" in caught.value.message
    assert not getattr(vexa, "sent", [])
    assert meeting.status == "failed"


def test_a_manual_send_after_the_wait_sends_a_fresh_bot(session, meeting):
    from app import services

    refused_meeting(session, meeting, ago=5 * MINUTE + SECOND)
    assert services.retry_block(meeting) is None
    vexa = FakeVexa()
    retry_meeting(session, meeting, vexa)
    assert vexa.sent == [meeting.url]
    assert (meeting.status, meeting.step, meeting.failure_reason) == ("bot_sent", "waiting", None)
    assert meeting.join_attempts == pipeline.REFUSAL_MAX_SENDS + 1
    assert meeting.vexa_meeting_id == "101"
    assert meeting.completion_reason is None and meeting.terminal_seen_at is None
    assert meeting.started_polling_at > datetime.now(UTC) - MINUTE


def test_a_manual_send_cannot_start_a_second_bot(session, meeting, owner):
    from app import services

    refused_meeting(session, meeting, ago=10 * MINUTE)
    other = Meeting(
        owner_user_id=owner.id,
        url=meeting.url,
        platform="google_meet",
        native_id="zzz-zzzz-zzz",
        template_id=meeting.template_id,
    )
    session.add(other)
    session.commit()
    vexa = FakeVexa()
    with pytest.raises(Refused) as caught:
        services.retry_meeting(session, meeting, vexa)
    assert caught.value.code == "bot_already_active"
    assert not getattr(vexa, "sent", [])


def test_a_manual_send_that_cannot_reach_vexa_keeps_the_meeting_failed(session, meeting):
    refused_meeting(session, meeting, ago=10 * MINUTE)
    vexa = FakeVexa()
    vexa.send_error = VexaError("down")
    with pytest.raises(Refused) as caught:
        retry_meeting(session, meeting, vexa)
    assert caught.value.status == 502
    assert meeting.status == "failed" and meeting.failure_reason == pipeline.REASON_JOIN_REFUSED


def test_a_manual_send_respects_the_hours_cap(session, meeting, owner):
    from app.models import UsageEntry, User
    from app.usage import email_hash

    refused_meeting(session, meeting, ago=10 * MINUTE)
    user = session.get(User, meeting.owner_user_id)
    user.is_admin, user.hours_cap_seconds = False, 3600
    session.add(
        UsageEntry(
            user_id=user.id,
            email_hash=email_hash(user.email),
            seconds=3600,
            counted_at=datetime.now(UTC),
        )
    )
    session.commit()
    vexa = FakeVexa()
    with pytest.raises(Refused) as caught:
        retry_meeting(session, meeting, vexa)
    assert (caught.value.status, caught.value.code) == (403, "hours_cap_reached")
    assert not getattr(vexa, "sent", []) and meeting.status == "failed"
