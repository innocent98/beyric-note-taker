"""The whole bot path through `FakeProvider` (task B1): no Vexa, no HTTP.

`place_bot`, the join runner, the worker's poll loop and the pipeline all take the provider from the
`BOT_PROVIDER` setting or as an argument, so a meeting can be driven from the send to its notes (or
to its refusal) with a scripted bot. Real Postgres, the real worker pass (`worker.run_pass`), the
real placement lock; the transcription and notes calls are `FakeOpenAI`, the audio step is the one
`tests/test_pipeline.py` uses.
"""

from datetime import UTC, datetime, timedelta
from functools import partial

import pytest
from sqlalchemy import select

from app import audio, pipeline, services, worker
from app.bot_provider import PROVIDER_FACTORIES, BotProviderError
from app.config import Settings, get_settings
from app.join_runner import run_due_joins
from app.models import Meeting, Notification
from app.placement import PlacementRefused
from tests.fakes.provider import FakeProvider
from tests.test_join_runner import (  # noqa: F401  (fixtures used by name, autouse ones too)
    SETTINGS as RUNNER_SETTINGS,
)
from tests.test_join_runner import (  # noqa: F401
    ada,
    alert_settings,
    at,
    cal,
    materialise,
    meetings,
    runner_has_led,
    state_of,
)
from tests.test_pipeline import NOTES, TRANSCRIPT, FakeOpenAI, _good_chunks
from tests.test_placement import CODE, make_template, request_for, url_of
from tests.test_placement import place as place_at
from tests.test_web import make_user


@pytest.fixture
def fake(monkeypatch, tmp_path):
    """A FakeProvider chosen the way production chooses one: by `BOT_PROVIDER`, through
    `build_provider()`. Nothing is passed to the worker or the pipeline."""
    provider = FakeProvider()
    monkeypatch.setitem(PROVIDER_FACTORIES, "fake", lambda: provider)
    monkeypatch.setenv("BOT_PROVIDER", "fake")
    get_settings.cache_clear()
    monkeypatch.setattr(pipeline, "get_settings", lambda: Settings(audio_dir=str(tmp_path / "a")))
    monkeypatch.setattr(
        audio, "prepare_recording", lambda source, out: audio.Recording(_good_chunks(out), 1830)
    )
    # The worker's pass calls `advance_meeting(session, id)`: give it the fake transcription and
    # notes models, and nothing else.
    monkeypatch.setattr(
        worker, "advance_meeting", partial(pipeline.advance_meeting, openai_client=FakeOpenAI())
    )
    for table in (
        pipeline._no_audio_polls,
        pipeline._needs_help_since,
        pipeline._stop_tries,
        pipeline._gone_checks,
        pipeline._lookup_misses,
    ):
        table.clear()
    yield provider
    get_settings.cache_clear()


def place(session, provider, req):
    """`place_bot` at the real time: the worker's passes run on the real clock too."""
    return place_at(session, provider, req, now=datetime.now(UTC))


@pytest.fixture
def user(session):
    return make_user(session, "ada@example.test")


@pytest.fixture
def template(session, user):
    return make_template(session, user)


def passes(session, meeting_id: int, limit: int = 12) -> list[tuple]:
    """Worker passes until the meeting is over: what each one left (status, step, bot state)."""
    seen = []
    for _ in range(limit):
        worker.run_pass(session, 0)
        session.expire_all()
        meeting = session.get(Meeting, meeting_id)
        seen.append((meeting.status, meeting.step, meeting.completion_reason))
        if meeting.status in ("ready", "failed"):
            break
    return seen


# ---- a pasted link, from the send to the notes ----


def test_place_bot_and_the_worker_loop_take_a_meeting_from_send_to_notes(
    session, fake, user, template
):
    meeting = place(session, fake, request_for(user, template))

    assert fake.bots_sent == 1 and fake.sent == [url_of(CODE)]
    assert (meeting.status, meeting.step) == ("bot_sent", "waiting")
    assert meeting.vexa_meeting_id == "2001"

    seen = passes(session, meeting.id)

    assert seen == [
        ("bot_sent", "waiting", "joining"),
        ("bot_sent", "waiting", "recording"),
        ("ready", "done", None),
    ]
    session.expire_all()
    done = session.get(Meeting, meeting.id)
    assert done.transcript == TRANSCRIPT
    assert done.notes == NOTES + "\n"
    assert done.failure_reason is None
    assert fake.downloads == [(7, 9)]


def test_a_bot_that_waits_in_the_lobby_shows_waiting_then_records(session, fake, user, template):
    fake.script(CODE, "knocked_then_admitted")
    meeting = place(session, fake, request_for(user, template))

    seen = passes(session, meeting.id)

    states = [state for _, _, state in seen[:-1]]
    assert states == ["joining", "awaiting_admission", "awaiting_admission", "recording"]
    assert seen[-1][:2] == ("ready", "done")


def test_a_host_who_declines_the_bot_fails_the_meeting_and_tells_the_owner(
    session, fake, user, template
):
    fake.script(CODE, "refused")
    meeting = place(session, fake, request_for(user, template))

    seen = passes(session, meeting.id)

    assert seen[-1][0] == "failed"
    session.expire_all()
    failed = session.get(Meeting, meeting.id)
    assert failed.failure_reason == pipeline.REASON_NOT_LET_IN
    kinds = list(session.scalars(select(Notification.kind)))
    assert kinds == [pipeline.NOT_ADMITTED_KIND]
    assert fake.downloads == []


def test_googles_refusal_pauses_for_one_polite_retry_with_a_second_bot(
    session, fake, user, template
):
    fake.script(CODE, "join_refused")
    meeting = place(session, fake, request_for(user, template))

    passes(session, meeting.id, limit=4)  # joining, lobby, refused: the worker stops the bot

    session.expire_all()
    paused = session.get(Meeting, meeting.id)
    assert paused.completion_reason == pipeline.MEET_REFUSED
    assert fake.stopped == [("google_meet", CODE)]
    # four minutes later the worker sends the second bot, and the fake took the first one's place
    later = paused.terminal_seen_at + pipeline.refusal_retry_gap(paused) + timedelta(seconds=1)
    for _ in range(3):
        pipeline.advance_meeting(session, meeting.id, now=later, openai_client=FakeOpenAI())
    session.expire_all()
    assert session.get(Meeting, meeting.id).join_attempts == 2
    assert fake.bots_sent == 2


def test_a_bot_that_never_gets_in_is_given_up_on_after_the_poll_limit(
    session, fake, user, template
):
    fake.script(CODE, "never_joined")
    meeting = place(session, fake, request_for(user, template))
    started = datetime.now(UTC)

    pipeline.advance_meeting(session, meeting.id, now=started)
    late = started + timedelta(hours=get_settings().max_poll_hours, minutes=1)
    pipeline.advance_meeting(session, meeting.id, now=late)

    session.expire_all()
    gave_up = session.get(Meeting, meeting.id)
    assert gave_up.status == "failed"
    assert "did not finish being recorded" in gave_up.failure_reason


# ---- what place_bot does with the provider's three kinds of failure ----


def test_a_send_error_is_raised_and_no_meeting_is_recorded(session, fake, user, template):
    fake.script(CODE, "error")
    with pytest.raises(BotProviderError):
        place(session, fake, request_for(user, template))
    session.rollback()
    assert session.scalars(select(Meeting)).all() == []


def test_an_uncertain_send_keeps_the_code_as_an_unconfirmed_start(session, fake, user, template):
    fake.script(CODE, "uncertain")
    meeting = place(session, fake, request_for(user, template))
    assert (meeting.status, meeting.completion_reason) == ("bot_sent", pipeline.SEND_UNCONFIRMED)
    assert meeting.vexa_meeting_id is None


def test_a_bot_the_database_does_not_know_about_is_refused_as_another_note_taker(
    session, fake, user, template
):
    """One bot per meeting code is the provider's rule. Our own checks pass (no row holds the
    code), the provider answers `BotDuplicate`, and `place_bot` words it neutrally."""
    fake.send_bot(url_of(CODE))  # someone else's bot, live on the code
    with pytest.raises(PlacementRefused) as refused:
        place(session, fake, request_for(user, template))
    assert refused.value.code == "meeting_has_note_taker"
    assert fake.bots_sent == 1


# ---- stopping ----


def test_cancelling_a_meeting_stops_its_bot_through_the_provider(session, fake, user, template):
    meeting = place(session, fake, request_for(user, template))
    services.cancel_meeting(session, fake, meeting)
    assert fake.stopped == [("google_meet", CODE)]
    session.refresh(meeting)
    assert meeting.status == "failed" and meeting.failure_reason == pipeline.REASON_CANCELLED


# ---- the join runner ----


def test_the_join_runner_sends_a_calendar_bot_and_the_worker_takes_it_to_notes(
    session, fake, cal, ada, monkeypatch
):
    # the calendar fixtures live on 2026-11-04: the worker's passes run on that clock too
    monkeypatch.setattr(
        worker,
        "advance_meeting",
        partial(pipeline.advance_meeting, openai_client=FakeOpenAI(), now=at(1)),
    )
    join = materialise(session, ada, cal, summary="Weekly planning")

    outcomes = run_due_joins(session, fake, cal, now=at(-1), settings=RUNNER_SETTINGS)

    assert [o.result for o in outcomes] == ["joined"] and fake.bots_sent == 1
    [meeting] = meetings(session)
    assert meeting.source == "calendar" and meeting.vexa_meeting_id == "2001"
    assert state_of(session, join) == ("joined", None)

    seen = passes(session, meeting.id)

    assert seen[-1][:2] == ("ready", "done")
    assert fake.downloads == [(7, 9)]


def test_a_refused_calendar_join_is_reported_by_the_runner_and_leaves_no_meeting(
    session, fake, cal, ada
):
    fake.default = "error"
    materialise(session, ada, cal, summary="Weekly planning")

    run_due_joins(session, fake, cal, now=at(-1), settings=RUNNER_SETTINGS)

    assert fake.bots_sent == 0
    assert meetings(session) == []
