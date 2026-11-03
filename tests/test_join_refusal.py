"""A refused or stuck Google Meet join fails fast with a plain reason, and retries are polite.

Fake Vexa answers follow the real payload shape. Vexa v0.12.29 reports a refusal as a typed
`denial` (`bot/src/orchestrator.ts`, `modules/join/src/googlemeet/refusal.ts` at `31122f9`):
status `failed`, `completion_reason` `awaiting_admission_rejected` (the same value a host
declining the bot gets) and `data.reason` starting `google_meet_join_refused:`. v0.12.28 gave
`needs_help` about two minutes after the knock, and meeting 10's "Could not locate join button"
error (both seen live on 2026-11-03). In Vexa `needs_help` is also the consent gate and reCAPTCHA
(`admission.ts`), so it never reads as a refusal.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app import pipeline, services
from app.config import Settings
from app.models import Meeting
from app.services import Refused, retry_meeting
from app.vexa import VexaError, VexaMeeting
from tests.test_pipeline import (  # noqa: F401  (fixtures used by name)
    DONE,
    NOW,
    FakeVexa,
    env,
    meeting,
    run,
)
from tests.test_web import owner  # noqa: F401

REFUSED = pipeline.MEET_REFUSED
HELP = pipeline.NEEDS_HELP_MARK
REFUSED_29 = VexaMeeting(
    status="failed",
    completion_reason="awaiting_admission_rejected",
    failure_reason=(
        f"{REFUSED}: Google Meet refused the join on 3 of 3 knocks "
        "(MeetingDeviceService/CreateMeetingDevice answered HTTP 403); "
        "the host never received a request to admit the bot"
    ),
)
HOST_DECLINED = VexaMeeting(status="failed", completion_reason="awaiting_admission_rejected")
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
GRACE = pipeline.NEEDS_HELP_GRACE
POLL = timedelta(seconds=Settings().poll_interval_seconds)
CODE = ("google_meet", "abc-defg-hij")


def fresh_send(session, meeting, attempts):
    meeting.join_attempts = attempts
    meeting.vexa_meeting_id = "1"
    session.commit()


# ---- a refusal is recognised, the bot is stopped, the owner is told why ----


@pytest.mark.parametrize("seen", [REFUSED_29, NO_JOIN_BUTTON], ids=["v0.12.29", "join-button"])
def test_a_refusal_after_the_last_send_fails_with_the_plain_reason(session, meeting, seen):
    fresh_send(session, meeting, pipeline.REFUSAL_MAX_SENDS)
    vexa = FakeVexa(seen)
    run(session, meeting, vexa)
    assert meeting.status == "failed"
    assert meeting.failure_reason == pipeline.REASON_JOIN_REFUSED
    assert "try sending the bot again in a few minutes" in meeting.failure_reason
    assert "the host's Meet settings may be blocking guests" in meeting.failure_reason
    assert vexa.stopped == [CODE]
    assert not getattr(vexa, "sent", [])
    assert meeting.completion_reason == REFUSED and meeting.terminal_seen_at == NOW


def test_a_host_declining_the_bot_is_not_a_refusal(session, meeting):
    fresh_send(session, meeting, 1)
    vexa = FakeVexa(HOST_DECLINED)
    run(session, meeting, vexa)
    assert meeting.status == "failed" and meeting.failure_reason == pipeline.REASON_NOT_LET_IN
    assert not getattr(vexa, "stopped", []) and not getattr(vexa, "sent", [])


def test_the_refusal_marker_must_head_the_reason(session, meeting):
    fresh_send(session, meeting, pipeline.REFUSAL_MAX_SENDS)
    mentions = VexaMeeting(
        status="failed", failure_reason=f"the host asked why {REFUSED} appeared in a log"
    )
    run(session, meeting, FakeVexa(mentions))
    assert meeting.failure_reason.startswith(pipeline.REASON_BOT_FAILED)


def test_a_join_button_error_that_is_not_the_meet_marketing_page_is_not_a_refusal(session, meeting):
    other = VexaMeeting(status="failed", failure_reason="Could not locate join button (url=x)")
    vexa = FakeVexa(other)
    fresh_send(session, meeting, pipeline.REFUSAL_MAX_SENDS)
    run(session, meeting, vexa)
    assert meeting.failure_reason.startswith(pipeline.REASON_BOT_FAILED)
    assert not getattr(vexa, "stopped", [])


def test_a_refusal_on_zoom_is_left_to_the_zoom_rules(session, meeting):
    meeting.platform, meeting.native_id = "zoom", "88205679674"
    fresh_send(session, meeting, pipeline.REFUSAL_MAX_SENDS)
    vexa = FakeVexa(REFUSED_29)
    run(session, meeting, vexa)
    assert meeting.failure_reason != pipeline.REASON_JOIN_REFUSED
    assert not getattr(vexa, "stopped", [])


# ---- needs_help: a prompt in the meeting, not a refusal ----


def test_needs_help_fails_after_two_minutes_with_its_own_reason(session, meeting):
    fresh_send(session, meeting, 1)  # no second bot: another one would hit the same prompt
    vexa = FakeVexa(NEEDS_HELP)
    run(session, meeting, vexa, now=NOW)
    run(session, meeting, vexa, now=NOW + GRACE - SECOND)
    assert meeting.status == "bot_sent" and not getattr(vexa, "stopped", [])
    run(session, meeting, vexa, now=NOW + GRACE)
    assert meeting.status == "failed"
    assert meeting.failure_reason == pipeline.REASON_NEEDS_HELP
    assert "consent or verification" in meeting.failure_reason
    assert "Google would not let the bot" not in meeting.failure_reason
    assert vexa.stopped == [CODE] and not getattr(vexa, "sent", [])
    assert meeting.completion_reason == HELP and meeting.terminal_seen_at == NOW + GRACE


def test_needs_help_that_clears_resets_the_grace(session, meeting):
    fresh_send(session, meeting, 1)
    vexa = FakeVexa(NEEDS_HELP)
    run(session, meeting, vexa, now=NOW)
    vexa.polls = [LOBBY]
    run(session, meeting, vexa, now=NOW + MINUTE)
    vexa.polls = [NEEDS_HELP]
    run(session, meeting, vexa, now=NOW + 2 * MINUTE)  # a new run, 0 s old
    run(session, meeting, vexa, now=NOW + 2 * MINUTE + GRACE - SECOND)
    assert meeting.status == "bot_sent"
    run(session, meeting, vexa, now=NOW + 2 * MINUTE + GRACE)
    assert meeting.status == "failed"


def test_needs_help_that_turns_active_is_never_ended(session, meeting):
    fresh_send(session, meeting, 1)
    vexa = FakeVexa(NEEDS_HELP)
    run(session, meeting, vexa, now=NOW)
    vexa.polls = [VexaMeeting(status="active")]
    run(session, meeting, vexa, now=NOW + MINUTE)
    run(session, meeting, vexa, now=NOW + 5 * MINUTE)
    assert meeting.status == "bot_sent" and not getattr(vexa, "stopped", [])


def test_needs_help_never_ends_a_bot_that_is_already_recording(session, meeting):
    fresh_send(session, meeting, 1)
    live = VexaMeeting(status="needs_help", segments=[{"speaker": "Ada", "text": "hello"}])
    vexa = FakeVexa(live)
    run(session, meeting, vexa, now=NOW)
    run(session, meeting, vexa, now=NOW + 5 * MINUTE)
    assert meeting.status == "bot_sent" and not getattr(vexa, "stopped", [])


def test_a_slow_host_is_left_waiting(session, meeting):
    fresh_send(session, meeting, 1)
    vexa = FakeVexa(LOBBY)
    for minutes in (0, 3, 6, 9):
        run(session, meeting, vexa, now=NOW + minutes * MINUTE)
    assert meeting.status == "bot_sent" and not getattr(vexa, "stopped", [])


def test_needs_help_on_zoom_is_left_alone(session, meeting):
    meeting.platform, meeting.native_id = "zoom", "88205679674"
    fresh_send(session, meeting, 1)
    vexa = FakeVexa(NEEDS_HELP)
    run(session, meeting, vexa, now=NOW)
    run(session, meeting, vexa, now=NOW + 5 * MINUTE)
    assert meeting.status == "bot_sent" and not getattr(vexa, "stopped", [])


# ---- a bot that cannot be stopped is not forgotten ----


def test_a_failed_stop_is_retried_on_the_next_polls_before_the_meeting_fails(session, meeting):
    fresh_send(session, meeting, pipeline.REFUSAL_MAX_SENDS)
    vexa = FakeVexa(REFUSED_29)
    vexa.stop_error = VexaError("connection refused")
    run(session, meeting, vexa, now=NOW)
    assert meeting.status == "bot_sent" and meeting.terminal_seen_at is None
    assert meeting.completion_reason != REFUSED  # nothing is marked until the bot is stopped
    vexa.stop_error = None  # Vexa is back
    run(session, meeting, vexa, now=NOW + POLL)
    assert meeting.status == "failed" and meeting.failure_reason == pipeline.REASON_JOIN_REFUSED
    assert vexa.stopped == [CODE, CODE]


def test_stopping_is_tried_three_times_then_the_meeting_fails_anyway(session, meeting):
    fresh_send(session, meeting, pipeline.REFUSAL_MAX_SENDS)
    vexa = FakeVexa(REFUSED_29)
    vexa.stop_error = VexaError("connection refused")
    for tick in range(pipeline.STOP_TRIES - 1):
        run(session, meeting, vexa, now=NOW + tick * POLL)
        assert meeting.status == "bot_sent"
    run(session, meeting, vexa, now=NOW + (pipeline.STOP_TRIES - 1) * POLL)
    assert meeting.status == "failed" and len(vexa.stopped) == pipeline.STOP_TRIES


def test_the_second_bot_waits_while_the_first_is_still_in_the_meeting(session, meeting):
    vexa = FakeVexa(REFUSED_29)
    run(session, meeting, vexa)
    due = NOW + pipeline.refusal_retry_gap(meeting)
    vexa.polls = [LOBBY]  # the refused bot is somehow still there
    run(session, meeting, vexa, now=due)
    assert not getattr(vexa, "sent", []) and vexa.stopped == [CODE, CODE]  # asked again
    vexa.polls = [REFUSED_29]  # now it has gone
    run(session, meeting, vexa, now=due + POLL)
    assert vexa.sent == [meeting.url]


def test_the_second_bot_goes_after_the_stop_checks_run_out(session, meeting):
    vexa = FakeVexa(REFUSED_29)
    run(session, meeting, vexa)
    due = NOW + pipeline.refusal_retry_gap(meeting)
    vexa.polls = [LOBBY]
    for tick in range(pipeline.STOP_TRIES):
        run(session, meeting, vexa, now=due + tick * POLL)
        assert not getattr(vexa, "sent", [])
    run(session, meeting, vexa, now=due + pipeline.STOP_TRIES * POLL)
    assert vexa.sent == [meeting.url]  # Vexa will say no if the slot is truly taken


# ---- pacing: one polite retry, never inside the bot's own re-knocks ----


def test_the_first_refusal_stops_the_bot_and_waits_before_sending_again(session, meeting):
    vexa = FakeVexa(REFUSED_29)
    run(session, meeting, vexa)
    assert meeting.status == "bot_sent" and meeting.failure_reason is None
    assert vexa.stopped == [CODE]
    assert not getattr(vexa, "sent", [])
    assert meeting.join_attempts == 1
    assert meeting.completion_reason == REFUSED and meeting.terminal_seen_at == NOW
    assert pipeline.join_retry_at(meeting) == NOW + pipeline.refusal_retry_gap(meeting)


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
    assert pipeline.join_retry_at(meeting) is None


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
    run(session, meeting, vexa, now=NOW + 5 * MINUTE + POLL)  # it is refused too
    assert meeting.status == "failed" and meeting.failure_reason == pipeline.REASON_JOIN_REFUSED
    assert len(vexa.sent) == 1
    assert vexa.stopped == [CODE] * 2


def test_the_second_bot_getting_in_records_normally(session, meeting):
    vexa = FakeVexa(REFUSED_29)
    run(session, meeting, vexa)
    run(session, meeting, vexa, now=NOW + 5 * MINUTE)
    vexa.polls = [DONE]
    run(session, meeting, vexa, now=NOW + 6 * MINUTE)
    assert (meeting.status, meeting.step) == ("ready", "done")
    assert vexa.id_lookups[-1] == "101"


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


# ---- how long a refused knock can sit in bot_sent ----


def settle_time(session, meeting, events, *, until=timedelta(minutes=10)):
    """Poll every `poll_interval_seconds` from the send, with what Vexa shows at that moment
    (`events` maps seconds since the send to the answer that holds from then on). Returns how
    long after the send the worker settled the refusal (failed, or paused with the bot stopped)
    and the fake Vexa used."""
    vexa = FakeVexa(LOBBY)
    at = timedelta(0)
    while at <= until:
        at += POLL
        vexa.polls = [
            [seen for start, seen in sorted(events.items()) if start <= at.total_seconds()][-1]
        ]
        run(session, meeting, vexa, now=NOW + at)
        # A marker counts only with its time: while the bot waits on a prompt the same word is
        # the live state (`terminal_seen_at` is set only when the worker ends the join).
        marked = meeting.completion_reason in (REFUSED, HELP) and meeting.terminal_seen_at
        if meeting.status == "failed" or marked:
            return at, vexa
    return None, vexa


V28_NEEDS_HELP = {0: VexaMeeting(status="joining"), 20: LOBBY, 120: NEEDS_HELP}
V28_BUTTON = {0: VexaMeeting(status="joining"), 140: NO_JOIN_BUTTON}  # failed after about 2.3 min
V29_REFUSED = {0: VexaMeeting(status="joining"), 25: LOBBY, 100: REFUSED_29}


def test_v028_needs_help_is_settled_within_the_grace_and_one_poll(session, meeting):
    fresh_send(session, meeting, 1)
    settled, vexa = settle_time(session, meeting, V28_NEEDS_HELP)
    # needs_help shows at 120 s; the worker waits the grace, and sees it at poll granularity.
    assert settled is not None and settled <= timedelta(seconds=120) + GRACE + POLL
    assert meeting.status == "failed" and meeting.failure_reason == pipeline.REASON_NEEDS_HELP
    assert vexa.stopped == [CODE]


def test_v028_join_button_error_is_settled_within_one_poll_of_vexa_failing(session, meeting):
    fresh_send(session, meeting, pipeline.REFUSAL_MAX_SENDS)
    settled, vexa = settle_time(session, meeting, V28_BUTTON)
    assert settled is not None and settled <= timedelta(seconds=140) + POLL  # 3 minutes at most
    assert meeting.failure_reason == pipeline.REASON_JOIN_REFUSED and vexa.stopped == [CODE]


@pytest.mark.parametrize("attempts", [1, pipeline.REFUSAL_MAX_SENDS])
def test_v029_refusal_is_settled_within_one_poll_of_vexa_reporting_it(session, meeting, attempts):
    # The 100 s is made up: Vexa measured 25 s to the lobby with one refusal and 41 s with two,
    # and the time to report after three refusals was not measured. The worker adds one poll.
    fresh_send(session, meeting, attempts)
    settled, vexa = settle_time(session, meeting, V29_REFUSED)
    assert settled is not None and settled <= timedelta(seconds=100) + POLL
    assert vexa.stopped == [CODE]
    assert (meeting.status == "failed") is (attempts == pipeline.REFUSAL_MAX_SENDS)


# ---- the owner sees why, and a manual send is paced ----


def refused_meeting(session, meeting, *, ago, mark=REFUSED):
    now = datetime.now(UTC)
    reason = pipeline.REASON_JOIN_REFUSED if mark == REFUSED else pipeline.REASON_NEEDS_HELP
    meeting.status, meeting.failure_reason = "failed", reason
    meeting.completion_reason, meeting.terminal_seen_at = mark, now - ago
    meeting.join_attempts = pipeline.REFUSAL_MAX_SENDS
    meeting.vexa_meeting_id = "1"
    session.commit()
    return meeting


def test_a_failed_join_is_told_by_its_marker_not_its_wording(session, meeting):
    refused_meeting(session, meeting, ago=MINUTE)
    meeting.failure_reason = "worded some other way"
    session.commit()
    assert pipeline.is_join_failure(meeting)
    meeting.completion_reason = "awaiting_admission_rejected"
    session.commit()
    assert not pipeline.is_join_failure(meeting)


def test_a_manual_send_is_refused_for_five_minutes_after_a_refusal(session, meeting):
    refused_meeting(session, meeting, ago=2 * MINUTE)
    assert services.retry_block(meeting) == "no_audio"
    assert "3 minutes" in services.retry_block_message(meeting, "no_audio")
    assert "Google refused the bot" in services.retry_block_message(meeting, "no_audio")
    vexa = FakeVexa()
    with pytest.raises(Refused) as caught:
        retry_meeting(session, meeting, vexa)
    assert caught.value.status == 409
    assert "3 minutes" in caught.value.message
    assert not getattr(vexa, "sent", [])
    assert meeting.status == "failed"


def test_the_wait_after_a_prompt_does_not_blame_google(session, meeting):
    refused_meeting(session, meeting, ago=2 * MINUTE, mark=HELP)
    message = services.retry_block_message(meeting, "no_audio")
    assert "3 minutes" in message and "Google" not in message


@pytest.mark.parametrize("mark", [REFUSED, HELP])
def test_a_manual_send_after_the_wait_sends_a_fresh_bot(session, meeting, mark):
    refused_meeting(session, meeting, ago=5 * MINUTE + SECOND, mark=mark)
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
