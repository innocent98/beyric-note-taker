"""A refused or stuck Google Meet join fails fast with a plain reason, and retries are polite.

Fake the provider answers follow the real payload shape. the provider v0.12.29 reports a refusal as a typed
`denial` (`bot/src/orchestrator.ts`, `modules/join/src/googlemeet/refusal.ts` at `31122f9`):
status `failed`, `completion_reason` `awaiting_admission_rejected` (the same value a host
declining the bot gets) and `data.reason` starting `google_meet_join_refused:`. v0.12.28 gave
`needs_help` about two minutes after the knock, and meeting 10's "Could not locate join button"
error (both seen live on 2026-11-03). In the provider `needs_help` is also the consent gate and reCAPTCHA
(`admission.ts`), so it never reads as a refusal.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app import pipeline, services
from app.bot_provider import BotMeeting, BotProviderError
from app.config import Settings
from app.models import Meeting, Template
from app.services import Refused, retry_meeting
from tests.test_pipeline import (  # noqa: F401  (fixtures used by name)
    DONE,
    NOW,
    FakeBot,
    env,
    meeting,
    run,
)
from tests.test_web import owner  # noqa: F401

REFUSED = pipeline.MEET_REFUSED
HELP = pipeline.NEEDS_HELP_MARK
REFUSED_29 = BotMeeting(
    status="failed",
    completion_reason="awaiting_admission_rejected",
    failure_reason=(
        f"{REFUSED}: Google Meet refused the join on 3 of 3 knocks "
        "(MeetingDeviceService/CreateMeetingDevice answered HTTP 403); "
        "the host never received a request to admit the bot"
    ),
)
HOST_DECLINED = BotMeeting(status="failed", completion_reason="awaiting_admission_rejected")
NO_JOIN_BUTTON = BotMeeting(
    status="failed",
    failure_reason=(
        "Could not locate join button by any locale-agnostic or English selector after 60000ms "
        "(url=https://workspace.google.com/products/meet/?utm_source=x)"
    ),
)
NEEDS_HELP = BotMeeting(status="needs_help")
LOBBY = BotMeeting(status="awaiting_admission")
SECOND = timedelta(seconds=1)
MINUTE = timedelta(minutes=1)
GRACE = pipeline.NEEDS_HELP_GRACE
POLL = timedelta(seconds=Settings().poll_interval_seconds)
CODE = ("google_meet", "abc-defg-hij")


def fresh_send(session, meeting, attempts):
    meeting.join_attempts = attempts
    meeting.provider_bot_id = "1"
    session.commit()


# ---- a refusal is recognised, the bot is stopped, the owner is told why ----


@pytest.mark.parametrize("seen", [REFUSED_29, NO_JOIN_BUTTON], ids=["v0.12.29", "join-button"])
def test_a_refusal_after_the_last_send_fails_with_the_plain_reason(session, meeting, seen):
    fresh_send(session, meeting, pipeline.REFUSAL_MAX_SENDS)
    provider = FakeBot(seen)
    run(session, meeting, provider)
    assert meeting.status == "failed"
    assert meeting.failure_reason == pipeline.REASON_JOIN_REFUSED
    assert "try sending the bot again in a few minutes" in meeting.failure_reason
    assert "the host's Meet settings may be blocking guests" in meeting.failure_reason
    assert provider.stopped == [CODE]
    assert not getattr(provider, "sent", [])
    assert meeting.completion_reason == REFUSED and meeting.terminal_seen_at == NOW


def test_a_host_declining_the_bot_is_not_a_refusal(session, meeting):
    fresh_send(session, meeting, 1)
    provider = FakeBot(HOST_DECLINED)
    run(session, meeting, provider)
    assert meeting.status == "failed" and meeting.failure_reason == pipeline.REASON_NOT_LET_IN
    assert not getattr(provider, "stopped", []) and not getattr(provider, "sent", [])


def test_the_refusal_marker_must_head_the_reason(session, meeting):
    fresh_send(session, meeting, pipeline.REFUSAL_MAX_SENDS)
    mentions = BotMeeting(
        status="failed", failure_reason=f"the host asked why {REFUSED} appeared in a log"
    )
    run(session, meeting, FakeBot(mentions))
    assert meeting.failure_reason.startswith(pipeline.REASON_BOT_FAILED)


def test_a_join_button_error_that_is_not_the_meet_marketing_page_is_not_a_refusal(session, meeting):
    other = BotMeeting(status="failed", failure_reason="Could not locate join button (url=x)")
    provider = FakeBot(other)
    fresh_send(session, meeting, pipeline.REFUSAL_MAX_SENDS)
    run(session, meeting, provider)
    assert meeting.failure_reason.startswith(pipeline.REASON_BOT_FAILED)
    assert not getattr(provider, "stopped", [])


def test_a_refusal_on_zoom_is_left_to_the_zoom_rules(session, meeting):
    meeting.platform, meeting.native_id = "zoom", "88205679674"
    fresh_send(session, meeting, pipeline.REFUSAL_MAX_SENDS)
    provider = FakeBot(REFUSED_29)
    run(session, meeting, provider)
    assert meeting.failure_reason != pipeline.REASON_JOIN_REFUSED
    assert not getattr(provider, "stopped", [])


# ---- needs_help: a prompt in the meeting, not a refusal ----


def test_needs_help_fails_after_two_minutes_with_its_own_reason(session, meeting):
    fresh_send(session, meeting, 1)  # no second bot: another one would hit the same prompt
    provider = FakeBot(NEEDS_HELP)
    run(session, meeting, provider, now=NOW)
    run(session, meeting, provider, now=NOW + GRACE - SECOND)
    assert meeting.status == "bot_sent" and not getattr(provider, "stopped", [])
    run(session, meeting, provider, now=NOW + GRACE)
    assert meeting.status == "failed"
    assert meeting.failure_reason == pipeline.REASON_NEEDS_HELP
    assert "consent or verification" in meeting.failure_reason
    assert "Google would not let the bot" not in meeting.failure_reason
    assert provider.stopped == [CODE] and not getattr(provider, "sent", [])
    assert meeting.completion_reason == HELP and meeting.terminal_seen_at == NOW + GRACE


def test_needs_help_that_clears_resets_the_grace(session, meeting):
    fresh_send(session, meeting, 1)
    provider = FakeBot(NEEDS_HELP)
    run(session, meeting, provider, now=NOW)
    provider.polls = [LOBBY]
    run(session, meeting, provider, now=NOW + MINUTE)
    provider.polls = [NEEDS_HELP]
    run(session, meeting, provider, now=NOW + 2 * MINUTE)  # a new run, 0 s old
    run(session, meeting, provider, now=NOW + 2 * MINUTE + GRACE - SECOND)
    assert meeting.status == "bot_sent"
    run(session, meeting, provider, now=NOW + 2 * MINUTE + GRACE)
    assert meeting.status == "failed"


def test_needs_help_that_turns_active_is_never_ended(session, meeting):
    fresh_send(session, meeting, 1)
    provider = FakeBot(NEEDS_HELP)
    run(session, meeting, provider, now=NOW)
    provider.polls = [BotMeeting(status="active")]
    run(session, meeting, provider, now=NOW + MINUTE)
    run(session, meeting, provider, now=NOW + 5 * MINUTE)
    assert meeting.status == "bot_sent" and not getattr(provider, "stopped", [])


def test_needs_help_never_ends_a_bot_that_is_already_recording(session, meeting):
    fresh_send(session, meeting, 1)
    live = BotMeeting(status="needs_help", segments=[{"speaker": "Ada", "text": "hello"}])
    provider = FakeBot(live)
    run(session, meeting, provider, now=NOW)
    run(session, meeting, provider, now=NOW + 5 * MINUTE)
    assert meeting.status == "bot_sent" and not getattr(provider, "stopped", [])


def test_a_slow_host_is_left_waiting(session, meeting):
    fresh_send(session, meeting, 1)
    provider = FakeBot(LOBBY)
    for minutes in (0, 3, 6, 9):
        run(session, meeting, provider, now=NOW + minutes * MINUTE)
    assert meeting.status == "bot_sent" and not getattr(provider, "stopped", [])


def test_needs_help_on_zoom_is_left_alone(session, meeting):
    meeting.platform, meeting.native_id = "zoom", "88205679674"
    fresh_send(session, meeting, 1)
    provider = FakeBot(NEEDS_HELP)
    run(session, meeting, provider, now=NOW)
    run(session, meeting, provider, now=NOW + 5 * MINUTE)
    assert meeting.status == "bot_sent" and not getattr(provider, "stopped", [])


# ---- a bot that cannot be stopped is not forgotten ----


def test_a_failed_stop_is_retried_on_the_next_polls_before_the_meeting_fails(session, meeting):
    fresh_send(session, meeting, pipeline.REFUSAL_MAX_SENDS)
    provider = FakeBot(REFUSED_29)
    provider.stop_error = BotProviderError("connection refused")
    run(session, meeting, provider, now=NOW)
    assert meeting.status == "bot_sent" and meeting.terminal_seen_at is None
    assert meeting.completion_reason != REFUSED  # nothing is marked until the bot is stopped
    provider.stop_error = None  # The provider is back
    run(session, meeting, provider, now=NOW + POLL)
    assert meeting.status == "failed" and meeting.failure_reason == pipeline.REASON_JOIN_REFUSED
    assert provider.stopped == [CODE, CODE]


def test_stopping_is_tried_three_times_then_the_meeting_fails_anyway(session, meeting):
    fresh_send(session, meeting, pipeline.REFUSAL_MAX_SENDS)
    provider = FakeBot(REFUSED_29)
    provider.stop_error = BotProviderError("connection refused")
    for tick in range(pipeline.STOP_TRIES - 1):
        run(session, meeting, provider, now=NOW + tick * POLL)
        assert meeting.status == "bot_sent"
    run(session, meeting, provider, now=NOW + (pipeline.STOP_TRIES - 1) * POLL)
    assert meeting.status == "failed" and len(provider.stopped) == pipeline.STOP_TRIES


def test_the_second_bot_waits_while_the_first_is_still_in_the_meeting(session, meeting):
    provider = FakeBot(REFUSED_29)
    run(session, meeting, provider)
    due = NOW + pipeline.refusal_retry_gap(meeting)
    provider.polls = [LOBBY]  # the refused bot is somehow still there
    run(session, meeting, provider, now=due)
    assert not getattr(provider, "sent", []) and provider.stopped == [CODE, CODE]  # asked again
    provider.polls = [REFUSED_29]  # now it has gone
    run(session, meeting, provider, now=due + POLL)
    assert provider.sent == [meeting.url]


def test_the_second_bot_goes_after_the_stop_checks_run_out(session, meeting):
    provider = FakeBot(REFUSED_29)
    run(session, meeting, provider)
    due = NOW + pipeline.refusal_retry_gap(meeting)
    provider.polls = [LOBBY]
    for tick in range(pipeline.STOP_TRIES):
        run(session, meeting, provider, now=due + tick * POLL)
        assert not getattr(provider, "sent", [])
    run(session, meeting, provider, now=due + pipeline.STOP_TRIES * POLL)
    assert provider.sent == [meeting.url]  # The provider will say no if the slot is truly taken


# ---- pacing: one polite retry, never inside the bot's own re-knocks ----


def test_the_first_refusal_stops_the_bot_and_waits_before_sending_again(session, meeting):
    provider = FakeBot(REFUSED_29)
    run(session, meeting, provider)
    assert meeting.status == "bot_sent" and meeting.failure_reason is None
    assert provider.stopped == [CODE]
    assert not getattr(provider, "sent", [])
    assert meeting.join_attempts == 1
    assert meeting.completion_reason == REFUSED and meeting.terminal_seen_at == NOW
    assert pipeline.join_retry_at(meeting) == NOW + pipeline.refusal_retry_gap(meeting)


def test_the_retry_waits_four_minutes_plus_jitter_and_ignores_the_old_bot(session, meeting):
    provider = FakeBot(REFUSED_29)
    run(session, meeting, provider)
    looked = provider.get_calls
    gap = pipeline.refusal_retry_gap(meeting)
    assert timedelta(minutes=4) <= gap <= timedelta(minutes=5)
    run(session, meeting, provider, now=NOW + gap - SECOND)
    assert (
        not getattr(provider, "sent", []) and provider.get_calls == looked
    )  # no lookup while paused
    run(session, meeting, provider, now=NOW + gap)
    assert provider.sent == [meeting.url]
    assert meeting.join_attempts == 2 and meeting.provider_bot_id == "101"
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
    provider = FakeBot(REFUSED_29)
    run(session, meeting, provider)
    run(session, meeting, provider, now=NOW + 5 * MINUTE)  # sends the second bot
    run(session, meeting, provider, now=NOW + 5 * MINUTE + POLL)  # it is refused too
    assert meeting.status == "failed" and meeting.failure_reason == pipeline.REASON_JOIN_REFUSED
    assert len(provider.sent) == 1
    assert provider.stopped == [CODE] * 2


def test_the_second_bot_getting_in_records_normally(session, meeting):
    provider = FakeBot(REFUSED_29)
    run(session, meeting, provider)
    run(session, meeting, provider, now=NOW + 5 * MINUTE)
    provider.polls = [DONE]
    run(session, meeting, provider, now=NOW + 6 * MINUTE)
    assert (meeting.status, meeting.step) == ("ready", "done")
    assert provider.id_lookups[-1] == "101"


def test_a_send_that_fails_after_the_pause_fails_the_meeting_with_the_reason(session, meeting):
    provider = FakeBot(REFUSED_29)
    run(session, meeting, provider)
    provider.send_error = BotProviderError("connection refused")
    run(session, meeting, provider, now=NOW + 5 * MINUTE)
    assert meeting.status == "failed"
    assert meeting.failure_reason.startswith(pipeline.REASON_JOIN_REFUSED)
    assert "A new bot could not be sent." in meeting.failure_reason


def test_a_restart_during_the_pause_still_sends_once_it_is_due(session, meeting):
    run(session, meeting, FakeBot(REFUSED_29))
    session.expire_all()  # nothing about the pause lives in memory
    provider = FakeBot(REFUSED_29)
    run(session, meeting, provider, now=NOW + 6 * MINUTE)
    assert provider.sent == [meeting.url]


def test_the_six_hour_limit_still_applies_during_the_pause(session, meeting):
    meeting.started_polling_at = NOW - timedelta(hours=7)
    session.commit()
    run(session, meeting, FakeBot(REFUSED_29))
    assert meeting.status == "failed" and "within 6 hours" in meeting.failure_reason


# ---- how long a refused knock can sit in bot_sent ----


def settle_time(session, meeting, events, *, until=timedelta(minutes=10)):
    """Poll every `poll_interval_seconds` from the send, with what the provider shows at that moment
    (`events` maps seconds since the send to the answer that holds from then on). Returns how
    long after the send the worker settled the refusal (failed, or paused with the bot stopped)
    and the fake the provider used."""
    provider = FakeBot(LOBBY)
    at = timedelta(0)
    while at <= until:
        at += POLL
        provider.polls = [
            [seen for start, seen in sorted(events.items()) if start <= at.total_seconds()][-1]
        ]
        run(session, meeting, provider, now=NOW + at)
        # A marker counts only with its time: while the bot waits on a prompt the same word is
        # the live state (`terminal_seen_at` is set only when the worker ends the join).
        marked = meeting.completion_reason in (REFUSED, HELP) and meeting.terminal_seen_at
        if meeting.status == "failed" or marked:
            return at, provider
    return None, provider


V28_NEEDS_HELP = {0: BotMeeting(status="joining"), 20: LOBBY, 120: NEEDS_HELP}
V28_BUTTON = {0: BotMeeting(status="joining"), 140: NO_JOIN_BUTTON}  # failed after about 2.3 min
V29_REFUSED = {0: BotMeeting(status="joining"), 25: LOBBY, 100: REFUSED_29}


def test_v028_needs_help_is_settled_within_the_grace_and_one_poll(session, meeting):
    fresh_send(session, meeting, 1)
    settled, provider = settle_time(session, meeting, V28_NEEDS_HELP)
    # needs_help shows at 120 s; the worker waits the grace, and sees it at poll granularity.
    assert settled is not None and settled <= timedelta(seconds=120) + GRACE + POLL
    assert meeting.status == "failed" and meeting.failure_reason == pipeline.REASON_NEEDS_HELP
    assert provider.stopped == [CODE]


def test_v028_join_button_error_is_settled_within_one_poll_of_provider_failing(session, meeting):
    fresh_send(session, meeting, pipeline.REFUSAL_MAX_SENDS)
    settled, provider = settle_time(session, meeting, V28_BUTTON)
    assert settled is not None and settled <= timedelta(seconds=140) + POLL  # 3 minutes at most
    assert meeting.failure_reason == pipeline.REASON_JOIN_REFUSED and provider.stopped == [CODE]


@pytest.mark.parametrize("attempts", [1, pipeline.REFUSAL_MAX_SENDS])
def test_v029_refusal_is_settled_within_one_poll_of_provider_reporting_it(
    session, meeting, attempts
):
    # The 100 s is made up: The provider measured 25 s to the lobby with one refusal and 41 s with two,
    # and the time to report after three refusals was not measured. The worker adds one poll.
    fresh_send(session, meeting, attempts)
    settled, provider = settle_time(session, meeting, V29_REFUSED)
    assert settled is not None and settled <= timedelta(seconds=100) + POLL
    assert provider.stopped == [CODE]
    assert (meeting.status == "failed") is (attempts == pipeline.REFUSAL_MAX_SENDS)


# ---- the owner sees why, and a manual send is paced ----


def refused_meeting(session, meeting, *, ago, mark=REFUSED):
    now = datetime.now(UTC)
    reason = pipeline.REASON_JOIN_REFUSED if mark == REFUSED else pipeline.REASON_NEEDS_HELP
    meeting.status, meeting.failure_reason = "failed", reason
    meeting.completion_reason, meeting.terminal_seen_at = mark, now - ago
    meeting.join_attempts = pipeline.REFUSAL_MAX_SENDS
    meeting.provider_bot_id = "1"
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
    provider = FakeBot()
    with pytest.raises(Refused) as caught:
        retry_meeting(session, meeting, provider)
    assert caught.value.status == 409
    assert "3 minutes" in caught.value.message
    assert not getattr(provider, "sent", [])
    assert meeting.status == "failed"


def test_the_wait_after_a_prompt_does_not_blame_google(session, meeting):
    refused_meeting(session, meeting, ago=2 * MINUTE, mark=HELP)
    message = services.retry_block_message(meeting, "no_audio")
    assert "3 minutes" in message and "Google" not in message


@pytest.mark.parametrize("mark", [REFUSED, HELP])
def test_a_manual_send_after_the_wait_sends_a_fresh_bot(session, meeting, mark):
    refused_meeting(session, meeting, ago=5 * MINUTE + SECOND, mark=mark)
    assert services.retry_block(meeting) is None
    provider = FakeBot()
    retry_meeting(session, meeting, provider)
    assert provider.sent == [meeting.url]
    assert (meeting.status, meeting.step, meeting.failure_reason) == ("bot_sent", "waiting", None)
    assert meeting.join_attempts == pipeline.REFUSAL_MAX_SENDS + 1
    assert meeting.provider_bot_id == "101"
    assert meeting.completion_reason is None and meeting.terminal_seen_at is None
    assert meeting.started_polling_at > datetime.now(UTC) - MINUTE


def test_a_manual_send_cannot_start_a_second_bot(session, meeting, owner, monkeypatch):
    monkeypatch.setattr(services, "get_settings", lambda: Settings(bot_max_local=1))
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
    provider = FakeBot()
    with pytest.raises(Refused) as caught:
        services.retry_meeting(session, meeting, provider)
    assert caught.value.code == "bot_busy"  # one slot, and the user's own meeting fills it
    assert not getattr(provider, "sent", [])


def test_a_manual_send_into_a_meeting_the_user_already_has_a_bot_in_is_refused(
    session, meeting, owner
):
    refused_meeting(session, meeting, ago=10 * MINUTE)
    session.add(
        Meeting(
            owner_user_id=owner.id,
            url=meeting.url,
            platform=meeting.platform,
            native_id=meeting.native_id,  # the same code: a second bot would join the same call
            template_id=meeting.template_id,
        )
    )
    session.commit()
    provider = FakeBot()
    with pytest.raises(Refused) as caught:
        services.retry_meeting(session, meeting, provider)
    assert (caught.value.status, caught.value.code) == (409, "already_in_meeting")
    assert not getattr(provider, "sent", [])


def test_a_manual_send_while_the_note_taker_is_on_someone_elses_meeting_is_a_neutral_busy(
    session, meeting, owner, monkeypatch
):
    """The same rule as a new meeting: someone else's bot never shows up as "your" meeting."""
    from tests.test_web import make_user

    monkeypatch.setattr(services, "get_settings", lambda: Settings(bot_max_local=1))

    refused_meeting(session, meeting, ago=10 * MINUTE)
    stranger = make_user(session, "stranger@example.test")
    theirs = Template(owner_user_id=stranger.id, name="General", instructions="x")
    session.add(theirs)
    session.flush()
    session.add(
        Meeting(
            owner_user_id=stranger.id,
            url=meeting.url,
            platform="google_meet",
            native_id="zzz-zzzz-zzz",
            template_id=theirs.id,
        )
    )
    session.commit()
    provider = FakeBot()
    with pytest.raises(Refused) as caught:
        services.retry_meeting(session, meeting, provider)
    assert caught.value.code == "bot_busy"
    assert "someone" not in caught.value.message.lower()
    assert not getattr(provider, "sent", [])


def test_a_manual_send_that_cannot_reach_provider_keeps_the_meeting_failed(session, meeting):
    refused_meeting(session, meeting, ago=10 * MINUTE)
    provider = FakeBot()
    provider.send_error = BotProviderError("down")
    with pytest.raises(Refused) as caught:
        retry_meeting(session, meeting, provider)
    assert caught.value.status == 502
    assert meeting.status == "failed" and meeting.failure_reason == pipeline.REASON_JOIN_REFUSED


def test_a_manual_send_whose_commit_fails_stops_the_bot_it_just_sent(session, meeting, monkeypatch):
    from sqlalchemy.exc import OperationalError

    refused_meeting(session, meeting, ago=10 * MINUTE)
    provider = FakeBot()

    def boom(*args, **kwargs):
        raise OperationalError("commit", {}, Exception("connection lost"))

    monkeypatch.setattr(session, "commit", boom)
    with pytest.raises(OperationalError):
        retry_meeting(session, meeting, provider)
    monkeypatch.undo()
    assert provider.stopped == [CODE]  # the new bot has no row that knows it: it is taken out
    session.refresh(meeting)
    assert meeting.status == "failed"


def test_a_manual_send_that_times_out_keeps_the_meeting_as_an_unconfirmed_start(session, meeting):
    """The provider may still be taking the bot: the row becomes a placeholder the worker adopts or reaps
    (a stop now would find nothing), not a 502 that frees the code for a second bot."""
    from app.bot_provider import BotSendUncertain

    refused_meeting(session, meeting, ago=10 * MINUTE)
    provider = FakeBot()
    provider.send_error = BotSendUncertain("read timed out")
    retry_meeting(session, meeting, provider)
    session.refresh(meeting)
    assert (meeting.status, meeting.step) == ("bot_sent", "waiting")
    assert meeting.provider_bot_id is None
    assert meeting.completion_reason == pipeline.SEND_UNCONFIRMED
    assert getattr(provider, "stopped", []) == []


def test_a_manual_send_into_a_meeting_provider_says_has_a_bot_is_the_neutral_refusal(
    session, meeting
):
    from app.bot_provider import BotDuplicate

    refused_meeting(session, meeting, ago=10 * MINUTE)
    provider = FakeBot()
    provider.send_error = BotDuplicate("a bot is already in that meeting")
    with pytest.raises(Refused) as caught:
        retry_meeting(session, meeting, provider)
    assert (caught.value.status, caught.value.code) == (409, "meeting_has_note_taker")
    assert caught.value.message == "A note taker is already in this meeting."
    assert meeting.status == "failed"


def test_the_worker_does_not_stop_a_bot_that_belongs_to_another_users_newer_meeting(
    session, meeting
):
    from tests.test_web import make_user

    stranger = make_user(session, "stranger@example.test")
    theirs = Template(owner_user_id=stranger.id, name="General", instructions="x")
    session.add(theirs)
    session.flush()
    session.add(
        Meeting(
            owner_user_id=stranger.id,
            url=meeting.url,
            platform=meeting.platform,
            native_id=meeting.native_id,
            template_id=theirs.id,
        )  # a newer meeting on the same code: the bot there is theirs
    )
    session.commit()
    provider = FakeBot()
    assert pipeline._stop_bot(meeting, provider) is True
    assert getattr(provider, "stopped", []) == []


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
    provider = FakeBot()
    with pytest.raises(Refused) as caught:
        retry_meeting(session, meeting, provider)
    assert (caught.value.status, caught.value.code) == (403, "hours_cap_reached")
    assert not getattr(provider, "sent", []) and meeting.status == "failed"


# ---- needs_help raised from the waiting room: the provider's lobby-timeout warning, not a prompt ----
#
# Seen live on 2026-11-06 (meetings 15 and 16): the bot sat in the waiting room, the provider raised
# `[Escalation] Triggered: waiting_room_timeout_approaching` at 80% of its 15-minute lobby budget
# (721 s) and turned the bot `needs_help`; the worker read that as a stuck prompt and withdrew the
# knock two minutes later, just as the host clicked Admit. the provider's own rule (`lifecycle/machine.py`
# at `31122f9`) is that `needs_help` entered after `awaiting_admission` is still the lobby.

LOBBY_WARNING = BotMeeting(status="needs_help", reached_lobby=True)


def test_a_lobby_timeout_warning_never_withdraws_the_knock(session, meeting):
    fresh_send(session, meeting, 1)
    provider = FakeBot(LOBBY)
    run(session, meeting, provider, now=NOW)
    provider.polls = [LOBBY_WARNING]
    run(session, meeting, provider, now=NOW + 12 * MINUTE)
    run(session, meeting, provider, now=NOW + 12 * MINUTE + GRACE)
    run(session, meeting, provider, now=NOW + 12 * MINUTE + 2 * GRACE)
    assert meeting.status == "bot_sent"
    assert not getattr(provider, "stopped", [])


def test_a_lobby_timeout_warning_still_reads_as_waiting_to_be_let_in(session, meeting):
    fresh_send(session, meeting, 1)
    run(session, meeting, FakeBot(LOBBY_WARNING), now=NOW)
    assert meeting.completion_reason == "awaiting_admission"


def test_the_host_admitting_after_the_warning_gets_a_bot_in_the_call(session, meeting):
    fresh_send(session, meeting, 1)
    provider = FakeBot(LOBBY_WARNING)
    run(session, meeting, provider, now=NOW)
    run(session, meeting, provider, now=NOW + GRACE + MINUTE)
    provider.polls = [BotMeeting(status="active")]
    run(session, meeting, provider, now=NOW + GRACE + 2 * MINUTE)
    assert meeting.status == "bot_sent" and meeting.completion_reason == "active"
    assert not getattr(provider, "stopped", [])


def test_provider_ending_the_lobby_wait_reads_as_not_let_in(session, meeting):
    fresh_send(session, meeting, 1)
    timed_out = BotMeeting(status="failed", completion_reason="awaiting_admission_timeout")
    run(session, meeting, FakeBot(timed_out))
    assert meeting.status == "failed" and meeting.failure_reason == pipeline.REASON_NOT_LET_IN


def test_needs_help_before_the_lobby_still_ends_after_the_grace(session, meeting):
    # A consent gate or captcha before the waiting room: the two-minute rule still applies.
    fresh_send(session, meeting, 1)
    provider = FakeBot(BotMeeting(status="needs_help", reached_lobby=False))
    run(session, meeting, provider, now=NOW)
    run(session, meeting, provider, now=NOW + GRACE)
    assert meeting.status == "failed" and meeting.failure_reason == pipeline.REASON_NEEDS_HELP
