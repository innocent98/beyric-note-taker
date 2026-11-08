"""The whole bot path through `SkribbyProvider` (task B2): a pretend Skribby, nothing else faked.

`BOT_PROVIDER=skribby` picks the provider through `build_provider()` exactly as production does; the
requests go through the real client to `pytest-httpx`, with the bodies Skribby's pages describe.
Real Postgres, the real placement lock, the real worker pass (`worker.run_pass`), the real ledger
over the `meetings` table; only the transcription and notes models are `FakeOpenAI` and the audio
step is the one `tests/test_pipeline.py` uses. What each case proves is in its name.
"""

import json
import uuid
from datetime import UTC, datetime, timedelta
from functools import partial

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app import audio, pipeline, services, worker
from app.bot_provider import build_provider
from app.config import Settings, get_settings
from app.models import Meeting, Notification
from app.skribby import recording_handle
from app.vexa import VexaProvider
from tests.test_pipeline import NOTES, TRANSCRIPT, FakeOpenAI, _good_chunks
from tests.test_placement import CODE, make_template, request_for, url_of
from tests.test_placement import place as place_at
from tests.test_web import make_user

pytestmark = pytest.mark.httpx_mock(
    assert_all_responses_were_requested=False, can_send_already_matched_responses=True
)

KEY = "skr-live-0123456789abcdef"
API = "https://platform.skribby.io/api/v1"
FILES = "https://files.skribby.test/rec.webm"
BOT = "3c09b2aa-6708-42c1-8145-aa55ee223613"
SECOND = "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d"


@pytest.fixture
def skribby(monkeypatch, tmp_path, engine):
    """Skribby chosen the way production chooses it, with our table as the ledger's source."""
    monkeypatch.setenv("BOT_PROVIDER", "skribby")
    monkeypatch.setenv("SKRIBBY_API_KEY", KEY)
    get_settings.cache_clear()
    monkeypatch.setattr("app.db.SessionLocal", sessionmaker(bind=engine, expire_on_commit=False))
    monkeypatch.setattr(pipeline, "get_settings", lambda: Settings(audio_dir=str(tmp_path / "a")))
    monkeypatch.setattr(
        audio, "prepare_recording", lambda source, out: audio.Recording(_good_chunks(out), 1830)
    )
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
    yield build_provider()
    get_settings.cache_clear()


@pytest.fixture
def user(session):
    return make_user(session, "ada@example.test")


@pytest.fixture
def template(session, user):
    return make_template(session, user)


def ago(seconds):
    return (datetime.now(UTC) - timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


def steps(*items):
    return [
        {"event": "status_update", "data": {"new_status": s}, "created_at": ago(a)}
        for s, a in items
    ]


def body(status, bot=BOT, *, events=(), **fields):
    return {"id": bot, "service": "gmeet", "status": status, "events": steps(*events), **fields}


def accept(mock, *ids):
    for bot in ids or (BOT,):
        mock.add_response(method="POST", url=f"{API}/bot", status_code=201, json={"id": bot})


def deletable(mock, bot=BOT, *statuses):
    """Skribby's `DELETE /bot/{id}`: each answer in turn (the last one repeats); 200 by default."""
    for status in statuses or (200,):
        mock.add_response(
            method="DELETE", url=f"{API}/bot/{bot}", status_code=status, json={"message": "x"}
        )


def deletes(mock):
    return [r for r in mock.get_requests() if r.method == "DELETE"]


def looks(mock, bot, *bodies):
    for item in bodies:
        mock.add_response(method="GET", url=f"{API}/bot/{bot}", json=item)


GOOD_CALL = [
    body("joining", events=[("booting", 30), ("joining", 5)]),
    body("recording", events=[("joining", 40), ("recording", 30)]),
    body(
        "finished",
        stop_reason="meeting_ended",
        events=[("joining", 600), ("recording", 590)],
        recording_url=FILES,
        transcript=[
            {
                "speaker_name": "Ada",
                "transcript": "hello everyone thanks for joining",
                "start": 1,
                "end": 3,
            }
        ],
    ),
]


def place(session, provider, user, template, code=CODE):
    return place_at(session, provider, request_for(user, template, code), now=datetime.now(UTC))


def passes(session, meeting_id, limit=12):
    seen = []
    for _ in range(limit):
        worker.run_pass(session, 0)
        session.expire_all()
        meeting = session.get(Meeting, meeting_id)
        seen.append((meeting.status, meeting.step, meeting.completion_reason))
        if meeting.status in ("ready", "failed"):
            break
    return seen


def posts(mock, suffix=""):
    return [
        r
        for r in mock.get_requests()
        if r.method == "POST" and str(r.url).endswith("/bot" + suffix)
    ]


# ---- a pasted link, from the send to the notes, and the retention after ----


def test_a_meeting_goes_from_the_send_to_notes_with_skribbys_speaker_names(
    session, skribby, user, template, httpx_mock
):
    deletable(httpx_mock)
    accept(httpx_mock)
    looks(httpx_mock, BOT, *GOOD_CALL)
    httpx_mock.add_response(method="GET", url=FILES, content=b"audio")

    meeting = place(session, skribby, user, template)

    assert (meeting.bot_provider, meeting.vexa_meeting_id) == ("skribby", BOT)
    sent = json.loads(posts(httpx_mock)[0].content)
    assert sent["meeting_url"] == url_of(CODE) and sent["service"] == "gmeet"

    seen = passes(session, meeting.id)

    assert seen == [
        ("bot_sent", "waiting", "joining"),
        ("bot_sent", "waiting", "active"),
        ("ready", "done", None),
    ]
    session.expire_all()
    done = session.get(Meeting, meeting.id)
    assert done.transcript == TRANSCRIPT  # our own transcription, from the downloaded audio
    assert done.speaker_transcript.startswith("[Ada]: ")  # Skribby's names label it
    assert done.speakers_incomplete is False
    assert done.notes == NOTES + "\n"
    assert done.vexa_recording_id == recording_handle(BOT)
    downloads = [r for r in httpx_mock.get_requests() if str(r.url) == FILES]
    assert len(downloads) == 1


def test_the_bots_data_at_skribby_is_deleted_as_soon_as_we_have_the_audio_and_the_names(
    session, skribby, user, template, httpx_mock
):
    accept(httpx_mock)
    looks(httpx_mock, BOT, *GOOD_CALL)
    httpx_mock.add_response(method="GET", url=FILES, content=b"audio")
    deletable(httpx_mock)
    meeting = place(session, skribby, user, template)

    passes(session, meeting.id)

    assert [r.url.path for r in deletes(httpx_mock)] == [f"/api/v1/bot/{BOT}"]
    session.expire_all()
    done = session.get(Meeting, meeting.id)
    assert done.status == "ready" and done.provider_copy_deleted_at is not None
    # the retention pass has nothing left to ask Skribby: it clears our folder and marks it
    cleared = pipeline.delete_expired_audio(session, datetime.now(UTC) + timedelta(days=10))
    assert cleared == 1 and len(deletes(httpx_mock)) == 1


def test_the_delete_is_after_the_download_and_the_speaker_lookup_never_before(
    session, skribby, user, template, httpx_mock
):
    accept(httpx_mock)
    looks(httpx_mock, BOT, *GOOD_CALL)
    httpx_mock.add_response(method="GET", url=FILES, content=b"audio")
    deletable(httpx_mock)
    meeting = place(session, skribby, user, template)

    passes(session, meeting.id)

    order = [
        r.method
        for r in httpx_mock.get_requests()
        if str(r.url) in (FILES,) or r.method == "DELETE"
    ]
    assert order == ["GET", "DELETE"]
    last_lookup = max(
        i
        for i, r in enumerate(httpx_mock.get_requests())
        if r.url.path.endswith(BOT) and r.method == "GET"
    )
    first_delete = min(i for i, r in enumerate(httpx_mock.get_requests()) if r.method == "DELETE")
    assert last_lookup < first_delete


def test_a_delete_skribby_refuses_never_holds_up_the_notes_and_is_retried_until_it_works(
    session, skribby, user, template, httpx_mock
):
    accept(httpx_mock)
    looks(httpx_mock, BOT, *GOOD_CALL)
    httpx_mock.add_response(method="GET", url=FILES, content=b"audio")
    deletable(httpx_mock, BOT, 500, 500, 200)  # the step, the first retry, the second retry
    meeting = place(session, skribby, user, template)

    passes(session, meeting.id)

    session.expire_all()
    done = session.get(Meeting, meeting.id)
    assert done.status == "ready" and done.notes == NOTES + "\n"  # the notes did not wait for it
    assert done.provider_copy_deleted_at is None
    assert pipeline.delete_fetched_copies(session, datetime.now(UTC)) == 0  # still refused
    session.expire_all()
    assert session.get(Meeting, meeting.id).provider_copy_deleted_at is None
    assert pipeline.delete_fetched_copies(session, datetime.now(UTC)) == 1
    session.expire_all()
    assert session.get(Meeting, meeting.id).provider_copy_deleted_at is not None
    assert pipeline.delete_fetched_copies(session, datetime.now(UTC)) == 0  # nothing left to do
    assert len(deletes(httpx_mock)) == 3


def test_a_delete_that_keeps_failing_is_tried_again_by_the_retention_pass_too(
    session, skribby, user, template, httpx_mock
):
    accept(httpx_mock)
    looks(httpx_mock, BOT, *GOOD_CALL)
    httpx_mock.add_response(method="GET", url=FILES, content=b"audio")
    deletable(httpx_mock, BOT, 500, 200)
    meeting = place(session, skribby, user, template)
    passes(session, meeting.id)

    cleared = pipeline.delete_expired_audio(session, datetime.now(UTC) + timedelta(days=10))

    assert cleared == 1
    session.expire_all()
    assert session.get(Meeting, meeting.id).provider_copy_deleted_at is not None


def test_the_worker_loops_job_retries_the_delete(session, skribby, user, template, httpx_mock):
    from app import worker

    accept(httpx_mock)
    looks(httpx_mock, BOT, *GOOD_CALL)
    httpx_mock.add_response(method="GET", url=FILES, content=b"audio")
    deletable(httpx_mock, BOT, 500, 200)
    meeting = place(session, skribby, user, template)
    passes(session, meeting.id)

    worker.background_jobs(session, None, datetime.now(UTC))

    session.expire_all()
    assert session.get(Meeting, meeting.id).provider_copy_deleted_at is not None


def test_a_meeting_that_has_not_yet_got_its_speaker_names_keeps_skribbys_copy(
    session, skribby, user, template, httpx_mock
):
    accept(httpx_mock)
    looks(httpx_mock, BOT, *GOOD_CALL)
    meeting = place(session, skribby, user, template)
    meeting.status, meeting.step = "failed", "transcribing"
    meeting.vexa_recording_id = recording_handle(BOT)  # the audio is fetched, the names are not
    session.commit()

    assert pipeline.delete_fetched_copies(session, datetime.now(UTC)) == 0

    assert deletes(httpx_mock) == []


def test_a_vexa_meeting_is_never_touched_by_the_delete_job(session, user, template, httpx_mock):
    meeting = vexa_row(session, user, template)
    meeting.status, meeting.speaker_transcript = "ready", "[Ada]: hi"
    session.commit()

    assert pipeline.delete_fetched_copies(session, datetime.now(UTC)) == 0

    assert httpx_mock.get_requests() == []


def test_a_meeting_that_took_the_key_to_skribby_kept_it_out_of_every_log(
    session, skribby, user, template, httpx_mock, caplog
):
    caplog.set_level("DEBUG")
    deletable(httpx_mock)
    accept(httpx_mock)
    looks(httpx_mock, BOT, *GOOD_CALL)
    httpx_mock.add_response(method="GET", url=FILES, content=b"audio")
    meeting = place(session, skribby, user, template)
    passes(session, meeting.id)
    assert KEY not in caplog.text


# ---- the door ----


def test_a_bot_that_knocks_shows_waiting_to_be_let_in_then_records(
    session, skribby, user, template, httpx_mock
):
    deletable(httpx_mock)
    accept(httpx_mock)
    looks(
        httpx_mock,
        BOT,
        body("joining", events=[("joining", 5)]),
        body("joining", events=[("joining", 30)]),
        body("joining", events=[("joining", 60)]),
        *GOOD_CALL[1:],
    )
    httpx_mock.add_response(method="GET", url=FILES, content=b"audio")
    meeting = place(session, skribby, user, template)

    seen = passes(session, meeting.id)

    states = [state for _, _, state in seen[:-1]]
    assert states == ["joining", "awaiting_admission", "awaiting_admission", "active"]
    assert seen[-1][:2] == ("ready", "done")


def test_a_host_who_declines_fails_the_meeting_and_tells_the_owner(
    session, skribby, user, template, httpx_mock
):
    accept(httpx_mock)
    looks(
        httpx_mock,
        BOT,
        body("joining", events=[("joining", 40)]),
        body("not_admitted", stop_reason="request_denied", events=[("joining", 70)]),
    )
    meeting = place(session, skribby, user, template)

    seen = passes(session, meeting.id)

    assert seen[-1][0] == "failed"
    session.expire_all()
    failed = session.get(Meeting, meeting.id)
    assert failed.failure_reason == pipeline.REASON_NOT_LET_IN
    assert list(session.scalars(select(Notification.kind))) == [pipeline.NOT_ADMITTED_KIND]


def test_a_waiting_room_that_ran_out_reads_the_same_as_a_decline(
    session, skribby, user, template, httpx_mock
):
    accept(httpx_mock)
    looks(
        httpx_mock,
        BOT,
        body("not_admitted", stop_reason="waiting_room_timeout", events=[("joining", 700)]),
    )
    meeting = place(session, skribby, user, template)
    passes(session, meeting.id)
    session.expire_all()
    assert session.get(Meeting, meeting.id).failure_reason == pipeline.REASON_NOT_LET_IN


def test_googles_block_pauses_for_one_retry_and_the_second_bot_goes_through_the_ledger(
    session, skribby, user, template, httpx_mock
):
    blocked = body(
        "bot_detected", events=[("bot_detected", 100 - n) for n in range(4)], finished_at=ago(1)
    )
    deletable(httpx_mock)
    accept(httpx_mock, BOT, SECOND)
    looks(httpx_mock, BOT, body("joining", events=[("joining", 5)]), blocked)
    looks(httpx_mock, SECOND, body("joining", SECOND, events=[("joining", 5)]))
    httpx_mock.add_response(
        method="POST", url=f"{API}/bot/{BOT}/stop", status_code=403, json={"message": "over"}
    )
    meeting = place(session, skribby, user, template)

    passes(session, meeting.id, limit=3)  # joining, then the block: the worker stops the bot

    session.expire_all()
    paused = session.get(Meeting, meeting.id)
    assert paused.completion_reason == pipeline.MEET_REFUSED
    assert len(posts(httpx_mock, f"/{BOT}/stop")) >= 1
    later = paused.terminal_seen_at + pipeline.refusal_retry_gap(paused) + timedelta(seconds=1)
    for _ in range(3):
        pipeline.advance_meeting(session, meeting.id, now=later, openai_client=FakeOpenAI())
    session.expire_all()
    resent = session.get(Meeting, meeting.id)
    assert resent.join_attempts == 2
    assert (resent.vexa_meeting_id, resent.bot_provider) == (SECOND, "skribby")
    assert len(posts(httpx_mock)) == 2


def test_a_second_user_cannot_put_a_bot_in_a_meeting_a_skribby_bot_is_in(
    session, skribby, user, template, httpx_mock
):
    from app.placement import PlacementRefused

    accept(httpx_mock)
    looks(httpx_mock, BOT, GOOD_CALL[0])
    place(session, skribby, user, template)
    other = make_user(session, "bo@example.test")
    other_template = make_template(session, other)

    with pytest.raises(PlacementRefused) as caught:
        place_at(
            session,
            skribby,
            request_for(other, other_template),
            now=datetime.now(UTC),
        )

    assert caught.value.code == "meeting_has_note_taker"  # our own table said so, before Skribby
    assert len(posts(httpx_mock)) == 1


def test_a_send_that_got_no_answer_is_kept_then_given_up_since_skribby_cannot_find_it(
    session, skribby, user, template, httpx_mock
):
    httpx_mock.add_response(method="POST", url=f"{API}/bot", status_code=504)
    meeting = place(session, skribby, user, template)
    assert (
        meeting.completion_reason == pipeline.SEND_UNCONFIRMED and meeting.vexa_meeting_id is None
    )

    pipeline.advance_meeting(session, meeting.id, openai_client=FakeOpenAI())
    session.expire_all()
    assert session.get(Meeting, meeting.id).status == "bot_sent"  # still within the grace

    later = meeting.started_polling_at + pipeline.UNCONFIRMED_GRACE + timedelta(seconds=1)
    pipeline.advance_meeting(session, meeting.id, now=later, openai_client=FakeOpenAI())
    session.expire_all()
    gone = session.get(Meeting, meeting.id)
    assert (gone.status, gone.failure_reason) == ("failed", pipeline.REASON_SEND_UNCONFIRMED)


# ---- the owner's actions ----


def test_cancelling_a_skribby_meeting_stops_its_bot_even_with_vexa_as_the_provider_given(
    session, skribby, user, template, httpx_mock
):
    accept(httpx_mock)
    looks(httpx_mock, BOT, GOOD_CALL[0])
    httpx_mock.add_response(method="POST", url=f"{API}/bot/{BOT}/stop", json={"message": "ok"})
    meeting = place(session, skribby, user, template)

    services.cancel_meeting(session, VexaProvider(), meeting)  # routed by the meeting's own row

    assert len(posts(httpx_mock, f"/{BOT}/stop")) == 1
    session.expire_all()
    assert session.get(Meeting, meeting.id).status == "failed"


def test_deleting_a_finished_skribby_meeting_deletes_the_bots_data_at_skribby(
    session, skribby, user, template, httpx_mock
):
    accept(httpx_mock)
    looks(httpx_mock, BOT, *GOOD_CALL)
    httpx_mock.add_response(method="GET", url=FILES, content=b"audio")
    httpx_mock.add_response(method="DELETE", url=f"{API}/bot/{BOT}", json={"message": "deleted"})
    meeting = place(session, skribby, user, template)
    passes(session, meeting.id)
    session.expire_all()
    assert len(deletes(httpx_mock)) == 1  # as soon as the names were saved

    services.delete_meeting(session, VexaProvider(), session.get(Meeting, meeting.id))

    # the owner's delete asks again and Skribby's answer for a bot already gone is success
    assert [r.url.path for r in deletes(httpx_mock)] == [f"/api/v1/bot/{BOT}"] * 2
    assert session.get(Meeting, meeting.id) is None


# ---- flipping the setting never strands a meeting in flight ----


def vexa_row(session, user, template, *, bot_id="55"):
    meeting = Meeting(
        owner_user_id=user.id,
        url=url_of(CODE),
        platform="google_meet",
        native_id=CODE,
        template_id=template.id,
        vexa_meeting_id=bot_id,
        started_polling_at=datetime.now(UTC),
    )
    session.add(meeting)
    session.commit()
    return meeting


def test_a_meeting_vexa_is_running_keeps_talking_to_vexa_after_the_setting_moved_to_skribby(
    session, skribby, user, template, httpx_mock
):
    meeting = vexa_row(session, user, template)
    vexa = "http://localhost:8056/transcripts/by-id/55"
    httpx_mock.add_response(
        method="GET",
        url=vexa,
        json={"id": 55, "status": "active", "segments": [{"speaker": "Ada", "text": "hi"}]},
    )

    pipeline.advance_meeting(session, meeting.id, openai_client=FakeOpenAI())

    asked = [str(r.url) for r in httpx_mock.get_requests()]
    assert asked == [vexa]  # nothing went to Skribby
    session.expire_all()
    assert session.get(Meeting, meeting.id).completion_reason == "recording"


def test_a_meeting_skribby_is_running_keeps_talking_to_skribby_after_the_setting_moved_back(
    session, user, template, httpx_mock, monkeypatch, engine
):
    monkeypatch.setenv("BOT_PROVIDER", "vexa")
    monkeypatch.setenv("SKRIBBY_API_KEY", KEY)
    get_settings.cache_clear()
    monkeypatch.setattr("app.db.SessionLocal", sessionmaker(bind=engine, expire_on_commit=False))
    try:
        meeting = vexa_row(session, user, template, bot_id=BOT)
        meeting.bot_provider = "skribby"
        session.commit()
        looks(httpx_mock, BOT, body("recording", events=[("joining", 40), ("recording", 30)]))

        pipeline.advance_meeting(session, meeting.id, openai_client=FakeOpenAI())

        assert [str(r.url) for r in httpx_mock.get_requests()] == [f"{API}/bot/{BOT}"]
        session.expire_all()
        assert session.get(Meeting, meeting.id).completion_reason == "active"
    finally:
        get_settings.cache_clear()


def test_a_skribby_meeting_waits_instead_of_failing_when_its_key_has_been_removed(
    session, user, template, httpx_mock, monkeypatch
):
    monkeypatch.setenv("BOT_PROVIDER", "vexa")
    monkeypatch.delenv("SKRIBBY_API_KEY", raising=False)
    get_settings.cache_clear()
    try:
        meeting = vexa_row(session, user, template, bot_id=BOT)
        meeting.bot_provider = "skribby"
        session.commit()

        pipeline.advance_meeting(session, meeting.id, openai_client=FakeOpenAI())

        assert httpx_mock.get_requests() == []
        session.expire_all()
        waiting = session.get(Meeting, meeting.id)
        assert waiting.status == "bot_sent" and waiting.failure_reason is None
    finally:
        get_settings.cache_clear()


def test_a_hand_retry_of_a_refused_meeting_goes_to_the_provider_in_use_now(
    session, skribby, user, template, httpx_mock
):
    meeting = vexa_row(session, user, template)  # a Vexa meeting, refused by Google earlier
    accept(httpx_mock, SECOND)

    assert pipeline.send_again(meeting, skribby) is True

    assert (meeting.bot_provider, meeting.vexa_meeting_id) == ("skribby", SECOND)
    assert meeting.vexa_recording_id is None
    assert uuid.UUID(meeting.vexa_meeting_id)


def test_the_owners_retry_of_a_refused_meeting_sends_a_skribby_bot_and_keeps_its_uuid_as_text(
    session, skribby, user, template, httpx_mock
):
    meeting = vexa_row(session, user, template, bot_id="55")
    meeting.status, meeting.step = "failed", "waiting"
    meeting.completion_reason = pipeline.MEET_REFUSED
    meeting.terminal_seen_at = datetime.now(UTC) - timedelta(hours=1)
    meeting.join_attempts = 2
    session.commit()
    accept(httpx_mock, SECOND)

    retried = services.retry_meeting(session, meeting, skribby)

    assert (retried.status, retried.bot_provider, retried.vexa_meeting_id) == (
        "bot_sent",
        "skribby",
        SECOND,
    )
    session.expire_all()
    assert session.get(Meeting, meeting.id).vexa_meeting_id == SECOND


def test_a_bot_that_was_never_let_in_has_its_record_deleted_at_skribby_too(
    session, skribby, user, template, httpx_mock
):
    accept(httpx_mock)
    looks(
        httpx_mock,
        BOT,
        body("not_admitted", stop_reason="request_denied", events=[("joining", 70)]),
    )
    deletable(httpx_mock)
    meeting = place(session, skribby, user, template)
    passes(session, meeting.id)

    assert pipeline.delete_fetched_copies(session, datetime.now(UTC)) == 1

    assert [r.url.path for r in deletes(httpx_mock)] == [f"/api/v1/bot/{BOT}"]
    cleared = pipeline.delete_expired_audio(session, datetime.now(UTC) + timedelta(days=10))
    assert cleared == 1 and len(deletes(httpx_mock)) == 1


def test_a_bot_whose_record_was_never_deleted_is_caught_by_the_retention_pass(
    session, skribby, user, template, httpx_mock
):
    accept(httpx_mock)
    looks(httpx_mock, BOT, body("not_admitted", stop_reason="request_denied"))
    deletable(httpx_mock)
    meeting = place(session, skribby, user, template)
    passes(session, meeting.id)

    cleared = pipeline.delete_expired_audio(session, datetime.now(UTC) + timedelta(days=10))

    assert cleared == 1 and len(deletes(httpx_mock)) == 1


def test_deleting_an_account_deletes_the_record_of_a_bot_that_never_recorded(
    session, skribby, user, template, httpx_mock
):
    from app import deletion

    accept(httpx_mock)
    looks(httpx_mock, BOT, body("not_admitted", stop_reason="request_denied"))
    deletable(httpx_mock)
    meeting = place(session, skribby, user, template)
    passes(session, meeting.id)

    deletion.purge_user(session, user, VexaProvider())

    assert [r.url.path for r in deletes(httpx_mock)] == [f"/api/v1/bot/{BOT}"]


def test_deleting_a_meeting_that_never_recorded_deletes_the_bots_record_too(
    session, skribby, user, template, httpx_mock
):
    accept(httpx_mock)
    looks(httpx_mock, BOT, body("not_admitted", stop_reason="request_denied"))
    deletable(httpx_mock)
    meeting = place(session, skribby, user, template)
    passes(session, meeting.id)
    session.expire_all()

    services.delete_meeting(session, VexaProvider(), session.get(Meeting, meeting.id))

    assert [r.url.path for r in deletes(httpx_mock)] == [f"/api/v1/bot/{BOT}"]


def test_sending_a_new_bot_deletes_the_replaced_bots_record_while_its_id_is_still_known(
    session, skribby, user, template, httpx_mock
):
    meeting = vexa_row(session, user, template, bot_id=BOT)
    meeting.bot_provider = "skribby"
    session.commit()
    looks(httpx_mock, BOT, body("not_admitted", stop_reason="request_denied"))  # it is over
    accept(httpx_mock, SECOND)
    deletable(httpx_mock)

    assert pipeline.send_again(meeting, skribby) is True

    assert [r.url.path for r in deletes(httpx_mock)] == [f"/api/v1/bot/{BOT}"]
    assert (meeting.vexa_meeting_id, meeting.provider_copy_deleted_at) == (SECOND, None)


def test_a_replaced_bots_record_that_cannot_be_deleted_never_stops_the_new_bot(
    session, skribby, user, template, httpx_mock
):
    meeting = vexa_row(session, user, template, bot_id=BOT)
    meeting.bot_provider = "skribby"
    session.commit()
    looks(httpx_mock, BOT, body("not_admitted", stop_reason="request_denied"))  # it is over
    accept(httpx_mock, SECOND)
    deletable(httpx_mock, BOT, 500)

    assert pipeline.send_again(meeting, skribby) is True
    assert meeting.vexa_meeting_id == SECOND


def test_a_failed_meeting_with_no_bot_id_has_nothing_to_delete_at_skribby(
    session, skribby, user, template, httpx_mock
):
    httpx_mock.add_response(method="POST", url=f"{API}/bot", status_code=504)
    meeting = place(session, skribby, user, template)
    later = meeting.started_polling_at + pipeline.UNCONFIRMED_GRACE + timedelta(seconds=1)
    pipeline.advance_meeting(session, meeting.id, now=later, openai_client=FakeOpenAI())

    cleared = pipeline.delete_expired_audio(session, datetime.now(UTC) + timedelta(days=10))

    assert cleared == 1
    assert not [r for r in httpx_mock.get_requests() if r.method == "DELETE"]
