"""While the call runs, the meeting says what the bot is doing, from the state Vexa reports.

The states are the ones Vexa's lifecycle schema lists (`core/meetings/contracts/lifecycle.v1` at
`31122f9`): `joining`, `awaiting_admission`, `active`, `needs_help`. Real runs on 2026-11-03: a
good join went `joining` -> `awaiting_admission` -> `active` -> done (meeting 12); a refused
v0.12.28 join went `awaiting_admission` -> `needs_help` -> failed. The worker keeps the state in
`Meeting.completion_reason` while the meeting is `bot_sent`, so there is no new column.
"""

from datetime import timedelta

import pytest

from app import pipeline, services
from app.vexa import VexaError, VexaMeeting
from tests.test_join_refusal import GRACE, POLL, fresh_send
from tests.test_pipeline import (  # noqa: F401  (fixtures used by name)
    DONE,
    NOW,
    FakeVexa,
    env,
    meeting,
    run,
)
from tests.test_web import owner  # noqa: F401

BOT = "Beyric Note Taker (recording)"
JOINING = "Bot is joining the meeting"
LOBBY_TEXT = f"Waiting to be let in. Admit '{BOT}' in the meeting"
IN_MEETING = "Bot is in the meeting"
RECORDING = "Recording"
SEGMENT = {"speaker": "Ada", "text": "hello everyone"}
CHECKING = "Checking on the bot..."


def said(meeting, now=NOW):
    """What the owner reads at `now`: a live state is only current if a recent poll wrote it."""
    return services.status_text(meeting, now)


def hold(meeting, state, seen_at=NOW):
    meeting.completion_reason, meeting.bot_state_seen_at = state, seen_at


STATES = [
    pytest.param(VexaMeeting(status="joining"), JOINING, id="joining"),
    pytest.param(VexaMeeting(status="awaiting_admission"), LOBBY_TEXT, id="awaiting_admission"),
    pytest.param(VexaMeeting(status="active"), IN_MEETING, id="active"),
    pytest.param(
        VexaMeeting(status="active", segments=[SEGMENT]), RECORDING, id="active-with-segments"
    ),
    pytest.param(
        VexaMeeting(status="active", recording_id=7, media_id=9),
        RECORDING,
        id="active-with-recording",
    ),
]


@pytest.mark.parametrize(("seen", "words"), STATES)
def test_the_owner_sees_what_the_bot_is_doing(session, meeting, seen, words):
    run(session, meeting, FakeVexa(seen))
    assert meeting.status == "bot_sent"
    assert said(meeting) == words


def test_a_bot_that_has_not_reported_yet_reads_bot_sent(session, meeting):
    assert said(meeting) == "Bot sent"
    run(session, meeting, FakeVexa(VexaMeeting(status="scheduled")))  # not a state of a live bot
    assert said(meeting) == "Bot sent"


def test_active_does_not_claim_a_recording_without_proof(session, meeting):
    run(session, meeting, FakeVexa(VexaMeeting(status="active")))
    assert "record" not in said(meeting).lower()


def test_needs_help_asks_the_owner_to_check_the_meeting_without_calling_it_failed(session, meeting):
    run(session, meeting, FakeVexa(VexaMeeting(status="needs_help")))
    words = said(meeting)
    assert meeting.status == "bot_sent"
    assert "waiting on a prompt in the meeting" in words
    assert "consent or verification screen" in words
    assert "Check the meeting for a prompt" in words
    assert "send the bot again" not in words and "could not continue" not in words


def test_a_good_join_changes_the_words_poll_by_poll_and_ends_clean(session, meeting):
    seen = [
        (VexaMeeting(status="joining"), JOINING),
        (VexaMeeting(status="awaiting_admission"), LOBBY_TEXT),
        (VexaMeeting(status="active"), IN_MEETING),
        (VexaMeeting(status="active", segments=[SEGMENT]), RECORDING),
    ]
    now = NOW
    for info, words in seen:
        run(session, meeting, FakeVexa(info), now=now)
        assert said(meeting, now) == words
        now += POLL
    run(session, meeting, FakeVexa(DONE), now=now)
    assert meeting.status == "ready"
    assert said(meeting, now) == "Ready"
    assert meeting.completion_reason is None  # the live state does not linger as a "reason"


def test_a_refused_v0_12_28_join_goes_lobby_then_needs_help_then_fails(session, meeting):
    fresh_send(session, meeting, pipeline.REFUSAL_MAX_SENDS)
    run(session, meeting, FakeVexa(VexaMeeting(status="awaiting_admission")), now=NOW)
    assert said(meeting) == LOBBY_TEXT
    run(session, meeting, FakeVexa(VexaMeeting(status="needs_help")), now=NOW + POLL)
    assert "waiting on a prompt in the meeting" in said(meeting, NOW + POLL)
    run(
        session,
        meeting,
        FakeVexa(VexaMeeting(status="needs_help")),
        now=NOW + POLL + GRACE,
    )
    assert meeting.status == "failed"
    assert said(meeting) == "Failed"
    assert meeting.completion_reason == pipeline.NEEDS_HELP_MARK
    assert meeting.failure_reason == pipeline.REASON_NEEDS_HELP


def test_a_failed_bot_clears_the_live_state(session, meeting):
    run(session, meeting, FakeVexa(VexaMeeting(status="joining")))
    run(session, meeting, FakeVexa(VexaMeeting(status="failed")), now=NOW + POLL)
    assert meeting.status == "failed"
    assert meeting.completion_reason is None
    assert said(meeting) == "Failed"


def test_one_failed_lookup_does_not_flip_the_label_and_two_in_a_row_do(session, meeting):
    run(session, meeting, FakeVexa(VexaMeeting(status="active")))
    assert said(meeting) == IN_MEETING
    run(session, meeting, FakeVexa(VexaError("down")), now=NOW + POLL)
    assert said(meeting, NOW + POLL) == IN_MEETING
    run(session, meeting, FakeVexa(VexaError("down")), now=NOW + 2 * POLL)
    assert meeting.status == "bot_sent"
    assert said(meeting, NOW + 2 * POLL) == "Bot sent"
    run(session, meeting, FakeVexa(VexaMeeting(status="active")), now=NOW + 3 * POLL)
    assert said(meeting, NOW + 3 * POLL) == IN_MEETING


def test_a_good_lookup_between_two_failures_starts_the_count_again(session, meeting):
    run(session, meeting, FakeVexa(VexaMeeting(status="active")))
    run(session, meeting, FakeVexa(VexaError("down")), now=NOW + POLL)
    run(session, meeting, FakeVexa(VexaMeeting(status="active")), now=NOW + 2 * POLL)
    run(session, meeting, FakeVexa(VexaError("down")), now=NOW + 3 * POLL)
    assert said(meeting, NOW + 3 * POLL) == IN_MEETING


def test_a_state_the_worker_has_not_renewed_is_not_shown_as_current(session, meeting):
    run(session, meeting, FakeVexa(VexaMeeting(status="active")))
    assert meeting.bot_state_seen_at == NOW
    assert said(meeting, NOW + 2 * POLL) == IN_MEETING
    assert said(meeting, NOW + 2 * POLL + timedelta(seconds=1)) == CHECKING


def test_a_failed_lookup_does_not_renew_the_state(session, meeting):
    run(session, meeting, FakeVexa(VexaMeeting(status="active")))
    run(session, meeting, FakeVexa(VexaError("down")), now=NOW + POLL)
    assert meeting.bot_state_seen_at == NOW


def test_a_live_state_with_no_time_is_not_trusted(session, meeting):
    hold(meeting, "active", seen_at=None)  # written before the time was kept
    assert said(meeting) == CHECKING


def test_a_meeting_with_no_state_is_never_checking(session, meeting):
    assert said(meeting, NOW + timedelta(days=1)) == "Bot sent"


def test_a_new_bot_starts_with_no_state(session, meeting):
    run(session, meeting, FakeVexa(VexaMeeting(status="active")))
    pipeline.send_again(meeting, FakeVexa())
    session.commit()
    assert said(meeting) == "Bot sent"


def test_the_live_state_never_replaces_the_refusal_marker(session, meeting):
    meeting.completion_reason, meeting.terminal_seen_at = pipeline.MEET_REFUSED, NOW
    session.commit()
    run(session, meeting, FakeVexa(VexaMeeting(status="joining")), now=NOW + POLL)
    assert meeting.completion_reason == pipeline.MEET_REFUSED
    assert said(meeting, NOW + POLL) == "Bot sent"


@pytest.mark.parametrize(
    ("status", "step", "words"),
    [
        ("processing", "transcribing", "Writing the transcript"),
        ("ready", "done", "Ready"),
        ("failed", "waiting", "Failed"),
    ],
)
def test_nothing_changes_once_the_call_is_over(session, meeting, status, step, words):
    meeting.status, meeting.step, meeting.completion_reason = status, step, "active"
    session.commit()
    assert said(meeting) == words


def test_the_words_follow_the_bot_name_setting(session, meeting, monkeypatch):
    from app.config import Settings

    monkeypatch.setattr(services, "get_settings", lambda: Settings(bot_name="Acme Notes"))
    hold(meeting, "awaiting_admission")
    assert "Admit 'Acme Notes' in the meeting" in said(meeting)


def test_an_unknown_value_in_the_reason_column_reads_bot_sent(session, meeting):
    meeting.completion_reason = "evicted"  # a Vexa completion reason, not a live state
    assert said(meeting) == "Bot sent"


def test_the_state_is_cleared_with_its_time_when_vexa_says_something_else(session, meeting):
    run(session, meeting, FakeVexa(VexaMeeting(status="active")))
    run(session, meeting, FakeVexa(VexaMeeting(status="failed")), now=NOW + POLL)
    assert meeting.completion_reason is None and meeting.bot_state_seen_at is None


WORDS = [
    pytest.param(VexaMeeting(status="requested"), "Starting the bot", id="requested"),
    pytest.param(VexaMeeting(status="stopping"), "The bot is leaving the meeting", id="stopping"),
]


@pytest.mark.parametrize(("seen", "words"), WORDS)
def test_the_bot_before_it_joins_and_as_it_leaves_have_their_own_words(
    session, meeting, seen, words
):
    run(session, meeting, FakeVexa(seen))
    assert meeting.status == "bot_sent"
    assert said(meeting) == words


def test_after_the_call_ends_the_page_never_says_the_bot_is_in_the_meeting(session, meeting):
    run(session, meeting, FakeVexa(VexaMeeting(status="active", segments=[SEGMENT])), now=NOW)
    ended = VexaMeeting(
        status="completed",
        segments=[SEGMENT],
        recording_id=7,
        media_id=9,
        recording_status="in_progress",
        completion_reason="left_alone",
    )
    run(session, meeting, FakeVexa(ended), now=NOW + POLL)
    words = said(meeting, NOW + POLL)
    assert meeting.status == "bot_sent"
    assert words == "The call ended; finishing the recording"
    assert "in the meeting" not in words and "Recording" != words


def test_a_call_that_ended_with_no_completion_reason_reads_the_same(session, meeting):
    run(session, meeting, FakeVexa(VexaMeeting(status="active")), now=NOW)
    run(session, meeting, FakeVexa(VexaMeeting(status="completed")), now=NOW + POLL)
    assert said(meeting, NOW + POLL) == "The call ended; finishing the recording"


def test_a_needs_help_bot_that_already_has_segments_is_not_said_to_wait_on_a_prompt(
    session, meeting
):
    run(session, meeting, FakeVexa(VexaMeeting(status="needs_help", segments=[SEGMENT])))
    assert said(meeting) == IN_MEETING
    assert "prompt" not in said(meeting)


def test_a_needs_help_bot_with_a_recording_is_not_said_to_wait_on_a_prompt(session, meeting):
    run(session, meeting, FakeVexa(VexaMeeting(status="needs_help", recording_id=7, media_id=9)))
    assert said(meeting) == IN_MEETING


def test_the_refusal_pause_still_reads_bot_sent_though_the_call_is_over(session, meeting):
    meeting.completion_reason, meeting.terminal_seen_at = pipeline.MEET_REFUSED, NOW
    session.commit()
    assert said(meeting, NOW + POLL) == "Bot sent"
