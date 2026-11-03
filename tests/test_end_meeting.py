"""\"End now and write the notes\": the owner stops the bot while it is in the call and the
pipeline writes the notes from what it recorded, as for any meeting.

It is not Cancel (which discards). What Vexa does with it is read from its source
(`lifecycle/stop_router.py`, `lifecycle/stop.py` at `31122f9`): `DELETE /bots/{platform}/{id}`
marks the meeting stop-requested, answers 200 `{"status": "stopping"}` at once and publishes a
`leave` command; an `active` bot leaves, finalises its recording and ends as `completed` with
completion reason `stopped`; a bot that has not joined yet (`awaiting_admission`) ends as `failed`
with the same reason and has recorded nothing. None of this has run against a real call.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app import pipeline, services
from app.models import Meeting
from app.vexa import VexaError, VexaMeeting
from tests.test_api import anon, api, error_of, ready  # noqa: F401
from tests.test_pipeline import DONE, NOW, FakeVexa, env, run  # noqa: F401
from tests.test_pipeline import meeting as waiting  # noqa: F401
from tests.test_web import (  # noqa: F401
    add_meeting,
    anon_client,
    app,
    client,
    fake_openai,
    fake_vexa,
    owner,
    post,
    stranger,
    template,
)

POLL = timedelta(seconds=30)
SEGMENT = {"speaker": "Ada", "text": "hello everyone thanks for joining"}
ENDING = "The bot is leaving the meeting. The notes start once it has gone"


def seen(session, template, state, **fields) -> Meeting:
    """A meeting whose bot the worker has just seen in `state`."""
    return add_meeting(
        session,
        template,
        completion_reason=state,
        bot_state_seen_at=datetime.now(UTC),
        **fields,
    )


# ---- what can be ended ----


@pytest.mark.parametrize("state", ["active", "recording"])
def test_a_bot_in_the_call_can_be_ended(api, session, template, fake_vexa, state):
    meeting = seen(session, template, state)
    assert api.get(f"/api/meetings/{meeting.id}").json()["can_end"] is True
    response = api.post(f"/api/meetings/{meeting.id}/end")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "bot_sent" and body["step"] == "waiting"
    assert body["status_text"] == ENDING
    assert body["can_end"] is False and body["failure_reason"] is None
    assert body["refresh_after_seconds"] == 30
    assert fake_vexa.stopped == [("google_meet", "abc-defg-hij")]
    session.refresh(meeting)
    assert meeting.completion_reason == pipeline.BOT_END_REQUESTED
    assert meeting.status == "bot_sent"


@pytest.mark.parametrize(
    "state",
    [
        None,
        "requested",
        "joining",
        "awaiting_admission",  # at the door Vexa ends it `failed`: nothing to write notes from
        "needs_help",
        "stopping",
        "google_meet_join_refused",
    ],
)
def test_a_bot_that_is_not_in_the_call_cannot_be_ended(api, session, template, fake_vexa, state):
    meeting = seen(session, template, state)
    assert api.get(f"/api/meetings/{meeting.id}").json()["can_end"] is False
    error = error_of(api.post(f"/api/meetings/{meeting.id}/end"), 409, "not_endable")
    assert error["message"] == (
        "The bot can only be ended while it is in the call. Cancel it instead if it is not in yet."
    )
    assert not hasattr(fake_vexa, "stopped")
    session.refresh(meeting)
    assert meeting.completion_reason == state


@pytest.mark.parametrize(
    ("status", "step"),
    [("processing", "transcribing"), ("ready", "done"), ("failed", "waiting")],
)
def test_a_meeting_that_is_not_waiting_for_its_bot_cannot_be_ended(
    api, session, template, fake_vexa, status, step
):
    meeting = seen(session, template, "active", status=status, step=step)
    assert api.get(f"/api/meetings/{meeting.id}").json()["can_end"] is False
    error_of(api.post(f"/api/meetings/{meeting.id}/end"), 409, "not_endable")
    assert not hasattr(fake_vexa, "stopped")
    session.refresh(meeting)
    assert meeting.status == status


def test_ending_twice_stops_the_bot_once(api, session, template, fake_vexa):
    meeting = seen(session, template, "active")
    api.post(f"/api/meetings/{meeting.id}/end")
    error = error_of(api.post(f"/api/meetings/{meeting.id}/end"), 409, "already_ending")
    assert (
        error["message"]
        == "The bot has already been told to leave. The notes start once it has gone."
    )
    assert fake_vexa.stopped == [("google_meet", "abc-defg-hij")]


def test_a_vexa_failure_changes_nothing_and_leaks_nothing(api, session, template, fake_vexa):
    fake_vexa.stop_error = VexaError("stop_bot failed: secret detail")
    meeting = seen(session, template, "active")
    response = api.post(f"/api/meetings/{meeting.id}/end")
    error = error_of(response, 502, "bot_stop_failed")
    assert error["message"] == "The bot could not be stopped. Please try again in a moment."
    assert "secret" not in response.text
    session.refresh(meeting)
    assert meeting.completion_reason == "active" and meeting.status == "bot_sent"
    fake_vexa.stop_error = None  # and it can be tried again
    assert api.post(f"/api/meetings/{meeting.id}/end").status_code == 200


def test_someone_elses_meeting_is_not_found_and_the_bot_is_left_alone(
    api, session, stranger, template, fake_vexa
):
    from app.models import Template

    theirs = Template(owner_user_id=stranger.id, name="T", instructions="x")
    session.add(theirs)
    session.commit()
    meeting = seen(session, theirs, "active", owner_user_id=stranger.id)
    error_of(api.post(f"/api/meetings/{meeting.id}/end"), 404, "not_found")
    error_of(api.post("/api/meetings/999/end"), 404, "not_found")
    assert not hasattr(fake_vexa, "stopped")


def test_the_meeting_is_looked_at_again_under_the_lock(engine, session, template, fake_vexa):
    """The row the caller loaded can be old: the worker may have moved it since."""
    meeting = seen(session, template, "active")
    assert meeting.status == "bot_sent"  # loaded, and now about to go out of date
    with Session(engine) as worker:
        row = worker.get(Meeting, meeting.id)
        row.status, row.step = "processing", "audio"
        worker.commit()
    with pytest.raises(services.Refused) as raised:
        services.end_meeting(session, fake_vexa, meeting)
    assert raised.value.code == "not_endable"
    assert not hasattr(fake_vexa, "stopped")


# ---- the page ----


def test_the_page_offers_the_button_only_while_the_bot_can_be_ended(
    client, session, template, fake_vexa
):
    meeting = seen(session, template, "recording")
    page = client.get(f"/meetings/{meeting.id}").text
    assert f'action="/meetings/{meeting.id}/end"' in page
    assert "End now and write the notes" in page
    assert 'data-dialog-open="end-dialog"' in page  # a confirmation first, as the Next page asks
    assert 'id="end-dialog"' in page
    assert "The bot leaves the meeting now and the notes are written from what it heard" in page
    response = post(client, f"/meetings/{meeting.id}/end", page=f"/meetings/{meeting.id}")
    assert response.status_code == 303
    assert fake_vexa.stopped == [("google_meet", "abc-defg-hij")]
    after = client.get(f"/meetings/{meeting.id}").text
    assert f'action="/meetings/{meeting.id}/end"' not in after
    assert "The bot is leaving the meeting" in after


def test_the_page_shows_a_plain_message_when_it_cannot_end(client, session, template, fake_vexa):
    meeting = seen(session, template, "joining")
    assert "End now and write the notes" not in client.get(f"/meetings/{meeting.id}").text
    response = post(client, f"/meetings/{meeting.id}/end", page=f"/meetings/{meeting.id}")
    assert response.status_code == 409
    assert "only be ended while it is in the call" in response.text


def test_the_page_reports_a_stop_that_failed(client, session, template, fake_vexa):
    fake_vexa.stop_error = VexaError("stop_bot failed: secret detail")
    meeting = seen(session, template, "active")
    response = post(client, f"/meetings/{meeting.id}/end", page=f"/meetings/{meeting.id}")
    assert response.status_code == 502 and "secret" not in response.text


def test_ending_a_missing_meeting_on_the_page_is_404(client):
    assert post(client, "/meetings/999/end").status_code == 404


def test_ending_makes_no_openai_call_by_itself(client, session, template, fake_vexa, fake_openai):
    meeting = seen(session, template, "active")
    post(client, f"/meetings/{meeting.id}/end", page=f"/meetings/{meeting.id}")
    assert fake_openai.chat_calls == [] and fake_openai.transcription_calls == 0


# ---- what the worker does afterwards ----


def ended(session, meeting) -> None:
    meeting.completion_reason, meeting.bot_state_seen_at = (
        pipeline.BOT_END_REQUESTED,
        NOW,
    )
    session.commit()


def test_while_the_bot_is_leaving_the_marker_stays_and_the_words_do_not_change(session, waiting):
    ended(session, waiting)
    run(session, waiting, FakeVexa(VexaMeeting(status="stopping")), now=NOW + POLL)
    assert waiting.completion_reason == pipeline.BOT_END_REQUESTED
    assert services.status_text(waiting, NOW + POLL) == ENDING
    run(session, waiting, FakeVexa(VexaMeeting(status="active")), now=NOW + 2 * POLL)  # not yet
    assert waiting.completion_reason == pipeline.BOT_END_REQUESTED
    assert waiting.status == "bot_sent"


def test_the_marker_survives_vexa_being_unreachable(session, waiting):
    ended(session, waiting)
    for tick in (1, 2, 3):
        run(session, waiting, FakeVexa(VexaError("down")), now=NOW + tick * POLL)
    assert waiting.completion_reason == pipeline.BOT_END_REQUESTED


def test_after_the_bot_has_left_the_recording_becomes_a_normal_meeting_with_notes(
    session, waiting, env
):
    ended(session, waiting)
    stopped = VexaMeeting(
        status="completed",
        segments=DONE.segments,
        recording_id=7,
        media_id=9,
        recording_status="completed",
        completion_reason="stopped",
    )
    openai = run(session, waiting, FakeVexa(stopped), now=NOW + POLL)
    assert (waiting.status, waiting.step) == ("ready", "done")
    assert waiting.notes and waiting.transcript and waiting.speaker_transcript
    assert openai.transcriptions == 1
    assert (
        waiting.completion_reason == pipeline.BOT_END_REQUESTED
    )  # Vexa's reason never replaces it
    assert waiting.failure_reason is None


def test_a_stopped_meeting_is_not_reported_as_having_been_removed(session, waiting):
    from app.api import _detail

    ended(session, waiting)
    stopped = VexaMeeting(
        status="completed",
        recording_id=7,
        media_id=9,
        recording_status="completed",
        completion_reason="stopped",
        segments=DONE.segments,
    )
    run(session, waiting, FakeVexa(stopped), now=NOW + POLL)
    assert _detail(waiting, session).bot_left_early is False


def test_while_the_recording_is_being_finished_the_words_say_so_not_leaving(session, waiting):
    ended(session, waiting)
    finishing = VexaMeeting(
        status="completed",
        recording_id=7,
        media_id=9,
        recording_status="in_progress",
        completion_reason="stopped",
    )
    run(session, waiting, FakeVexa(finishing), now=NOW + POLL)
    assert waiting.status == "bot_sent"
    assert services.status_text(waiting, NOW + POLL) == "The call ended; finishing the recording"
    assert waiting.completion_reason == pipeline.BOT_END_REQUESTED


def test_ending_before_anything_was_recorded_fails_the_meeting_the_usual_way(session, waiting):
    ended(session, waiting)
    nothing = VexaMeeting(status="completed", completion_reason="stopped")
    for tick in (1, 2, 3):  # the same few polls a recording gets to appear
        run(session, waiting, FakeVexa(nothing), now=NOW + tick * POLL)
    assert waiting.status == "failed" and waiting.step == "waiting"
    assert waiting.failure_reason == pipeline.REASON_ENDED_NOTHING_RECORDED
    assert services.retry_block(waiting) == "no_audio"  # same result as an empty meeting today


def test_ending_at_the_door_fails_with_the_words_for_a_meeting_never_recorded(session, waiting):
    ended(session, waiting)
    run(
        session,
        waiting,
        FakeVexa(VexaMeeting(status="failed", completion_reason="stopped")),
        now=NOW + POLL,
    )
    assert waiting.status == "failed"
    assert waiting.failure_reason == pipeline.REASON_ENDED_NOTHING_RECORDED
    assert services.retry_block(waiting) == "no_audio"


def test_a_failed_bot_that_was_not_stopped_by_the_owner_keeps_the_old_words(session, waiting):
    run(session, waiting, FakeVexa(VexaMeeting(status="failed")), now=NOW + POLL)
    assert waiting.failure_reason == pipeline.REASON_BOT_FAILED


def test_an_ended_meeting_with_no_speech_gives_the_same_result_as_any_other(
    session, waiting, env, monkeypatch
):
    ended(session, waiting)
    stopped = VexaMeeting(
        status="completed",
        recording_id=7,
        media_id=9,
        recording_status="completed",
        completion_reason="stopped",
    )
    from tests.test_pipeline import FakeOpenAI

    run(session, waiting, FakeVexa(stopped), openai=FakeOpenAI(transcript="uh"), now=NOW + POLL)
    assert waiting.status == "failed"
    assert waiting.failure_reason == pipeline.REASON_NO_SPEECH


# ---- the reasons are told by the owner's press, not by Vexa's word "stopped" ----


def test_a_stop_the_owner_did_not_ask_for_does_not_say_you_ended_the_meeting(session, waiting):
    """The worker's own stop can reach Vexa and still look failed on our side; Vexa then says
    `stopped` for a meeting nobody pressed End on."""
    run(
        session,
        waiting,
        FakeVexa(VexaMeeting(status="failed", completion_reason="stopped")),
        now=NOW + POLL,
    )
    assert waiting.status == "failed"
    assert waiting.failure_reason == pipeline.REASON_BOT_FAILED


def test_no_recording_after_a_stop_nobody_asked_for_keeps_the_old_words(session, waiting):
    nothing = VexaMeeting(status="completed", completion_reason="stopped")
    for tick in (1, 2, 3):
        run(session, waiting, FakeVexa(nothing), now=NOW + tick * POLL)
    assert waiting.failure_reason == pipeline.REASON_NOT_ADMITTED


def test_the_marker_is_kept_through_the_polls_that_wait_for_a_recording(session, waiting):
    ended(session, waiting)
    nothing = VexaMeeting(status="completed", completion_reason="stopped")
    run(session, waiting, FakeVexa(nothing), now=NOW + POLL)
    assert waiting.completion_reason == pipeline.BOT_END_REQUESTED
    run(session, waiting, FakeVexa(nothing), now=NOW + 2 * POLL)
    run(session, waiting, FakeVexa(nothing), now=NOW + 3 * POLL)
    assert waiting.failure_reason == pipeline.REASON_ENDED_NOTHING_RECORDED


# ---- Cancel after End ----


def test_cancel_is_not_offered_once_the_bot_has_been_told_to_leave(
    api, session, template, fake_vexa
):
    meeting = seen(session, template, "active")
    assert api.get(f"/api/meetings/{meeting.id}").json()["can_cancel"] is True
    body = api.post(f"/api/meetings/{meeting.id}/end").json()
    assert body["can_cancel"] is False
    assert api.get(f"/api/meetings/{meeting.id}").json()["can_cancel"] is False


def test_cancel_after_end_is_refused_so_the_recording_is_not_thrown_away(
    api, session, template, fake_vexa
):
    meeting = seen(session, template, "active")
    api.post(f"/api/meetings/{meeting.id}/end")
    error = error_of(api.post(f"/api/meetings/{meeting.id}/cancel"), 409, "already_ending")
    assert "can't be cancelled" in error["message"]
    assert fake_vexa.stopped == [("google_meet", "abc-defg-hij")]  # End's stop only
    session.refresh(meeting)
    assert meeting.status == "bot_sent"


def test_the_page_has_no_cancel_form_once_the_bot_is_leaving(client, session, template, fake_vexa):
    meeting = seen(session, template, "active")
    assert f'action="/meetings/{meeting.id}/cancel"' in client.get(f"/meetings/{meeting.id}").text
    post(client, f"/meetings/{meeting.id}/end", page=f"/meetings/{meeting.id}")
    assert (
        f'action="/meetings/{meeting.id}/cancel"' not in client.get(f"/meetings/{meeting.id}").text
    )


# ---- a stop Vexa's client refuses, and a row the worker is holding ----


def test_a_meeting_code_the_client_refuses_is_a_plain_failure_not_a_crash(
    api, session, template, fake_vexa
):
    fake_vexa.stop_error = ValueError("unsupported platform or meeting code")
    meeting = seen(session, template, "active")
    error_of(api.post(f"/api/meetings/{meeting.id}/end"), 502, "bot_stop_failed")
    session.refresh(meeting)
    assert meeting.completion_reason == "active"


@pytest.mark.parametrize("action", ["end", "cancel"])
def test_a_row_the_worker_holds_gives_a_plain_409_instead_of_waiting_for_minutes(
    engine, session, template, fake_vexa, monkeypatch, action
):
    meeting = seen(session, template, "active")
    monkeypatch.setattr(services, "LOCK_WAIT", "200ms")
    call = services.end_meeting if action == "end" else services.cancel_meeting
    with Session(engine) as worker:  # the worker, in the middle of a pass
        worker.execute(select(Meeting).where(Meeting.id == meeting.id).with_for_update())
        with pytest.raises(services.Refused) as raised:
            call(session, fake_vexa, meeting)
    assert raised.value.status == 409 and raised.value.code == "meeting_busy"
    assert "being worked on right now" in raised.value.message
    assert not hasattr(fake_vexa, "stopped")
    session.refresh(meeting)
    assert meeting.completion_reason == "active" and meeting.status == "bot_sent"


def test_the_lock_wait_does_not_leak_into_the_next_statements(session, template, fake_vexa):
    meeting = seen(session, template, "active")
    services.end_meeting(session, fake_vexa, meeting)
    session.execute(select(Meeting))  # a new transaction, with the connection's own setting
    assert session.execute(text("show lock_timeout")).scalar() == "0"
