"""While the call runs, the meeting says what the bot is doing, from the state Vexa reports.

The states are the ones Vexa's lifecycle schema lists (`core/meetings/contracts/lifecycle.v1` at
`31122f9`): `joining`, `awaiting_admission`, `active`, `needs_help`. Real runs on 2026-11-03: a
good join went `joining` -> `awaiting_admission` -> `active` -> done (meeting 12); a refused
v0.12.28 join went `awaiting_admission` -> `needs_help` -> failed. The worker keeps the state in
`Meeting.completion_reason` while the meeting is `bot_sent`, so there is no new column.
"""

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
    assert services.status_text(meeting) == words


def test_a_bot_that_has_not_reported_yet_reads_bot_sent(session, meeting):
    assert services.status_text(meeting) == "Bot sent"
    run(session, meeting, FakeVexa(VexaMeeting(status="requested")))
    assert services.status_text(meeting) == "Bot sent"


def test_active_does_not_claim_a_recording_without_proof(session, meeting):
    run(session, meeting, FakeVexa(VexaMeeting(status="active")))
    assert "record" not in services.status_text(meeting).lower()


def test_needs_help_asks_the_owner_to_check_the_meeting_without_calling_it_failed(session, meeting):
    run(session, meeting, FakeVexa(VexaMeeting(status="needs_help")))
    words = services.status_text(meeting)
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
        assert services.status_text(meeting) == words
        now += POLL
    run(session, meeting, FakeVexa(DONE), now=now)
    assert meeting.status == "ready"
    assert services.status_text(meeting) == "Ready"
    assert meeting.completion_reason is None  # the live state does not linger as a "reason"


def test_a_refused_v0_12_28_join_goes_lobby_then_needs_help_then_fails(session, meeting):
    fresh_send(session, meeting, pipeline.REFUSAL_MAX_SENDS)
    run(session, meeting, FakeVexa(VexaMeeting(status="awaiting_admission")), now=NOW)
    assert services.status_text(meeting) == LOBBY_TEXT
    run(session, meeting, FakeVexa(VexaMeeting(status="needs_help")), now=NOW + POLL)
    assert "waiting on a prompt in the meeting" in services.status_text(meeting)
    run(
        session,
        meeting,
        FakeVexa(VexaMeeting(status="needs_help")),
        now=NOW + POLL + GRACE,
    )
    assert meeting.status == "failed"
    assert services.status_text(meeting) == "Failed"
    assert meeting.completion_reason == pipeline.NEEDS_HELP_MARK
    assert meeting.failure_reason == pipeline.REASON_NEEDS_HELP


def test_a_failed_bot_clears_the_live_state(session, meeting):
    run(session, meeting, FakeVexa(VexaMeeting(status="joining")))
    run(session, meeting, FakeVexa(VexaMeeting(status="failed")), now=NOW + POLL)
    assert meeting.status == "failed"
    assert meeting.completion_reason is None
    assert services.status_text(meeting) == "Failed"


def test_vexa_being_unreachable_goes_back_to_bot_sent(session, meeting):
    run(session, meeting, FakeVexa(VexaMeeting(status="active")))
    assert services.status_text(meeting) == IN_MEETING
    run(session, meeting, FakeVexa(VexaError("down")), now=NOW + POLL)
    assert meeting.status == "bot_sent"
    assert services.status_text(meeting) == "Bot sent"
    run(session, meeting, FakeVexa(VexaMeeting(status="active")), now=NOW + 2 * POLL)
    assert services.status_text(meeting) == IN_MEETING


def test_a_new_bot_starts_with_no_state(session, meeting):
    run(session, meeting, FakeVexa(VexaMeeting(status="active")))
    pipeline.send_again(meeting, FakeVexa())
    session.commit()
    assert services.status_text(meeting) == "Bot sent"


def test_the_live_state_never_replaces_the_refusal_marker(session, meeting):
    meeting.completion_reason, meeting.terminal_seen_at = pipeline.MEET_REFUSED, NOW
    session.commit()
    run(session, meeting, FakeVexa(VexaMeeting(status="joining")), now=NOW + POLL)
    assert meeting.completion_reason == pipeline.MEET_REFUSED
    assert services.status_text(meeting) == "Bot sent"


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
    assert services.status_text(meeting) == words


def test_the_words_follow_the_bot_name_setting(session, meeting, monkeypatch):
    from app.config import Settings

    monkeypatch.setattr(services, "get_settings", lambda: Settings(bot_name="Acme Notes"))
    meeting.completion_reason = "awaiting_admission"
    assert "Admit 'Acme Notes' in the meeting" in services.status_text(meeting)


def test_an_unknown_value_in_the_reason_column_reads_bot_sent(session, meeting):
    meeting.completion_reason = "evicted"  # a Vexa completion reason, not a live state
    assert services.status_text(meeting) == "Bot sent"
