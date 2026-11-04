"""The translation job: queueing, caching by (meeting, language), staleness, the ledger, the daily
limit and the worker's claim (app/translation.py). Real Postgres, the model faked."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text, update

from app import translation
from app.models import AiRun, Meeting, MeetingTranslation, Template, User, VocabularyTerm
from app.services import Refused
from tests.test_translation import NOTES, TRANSCRIPT, FakeModel

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)


@pytest.fixture
def user(session):
    row = User(email="owner@example.test")
    session.add(row)
    session.flush()
    session.add(Template(owner_user_id=row.id, name="General", instructions="x"))
    session.add(VocabularyTerm(owner_user_id=row.id, term="Metrika"))
    session.commit()
    return row


def make_meeting(session, user, *, notes=NOTES, transcript=TRANSCRIPT, status="ready", **fields):
    template = session.scalars(select(Template)).first()
    meeting = Meeting(
        owner_user_id=user.id,
        url="u",
        platform="zoom",
        native_id="1",
        template_id=template.id,
        status=status,
        step="done" if status == "ready" else "waiting",
        notes=notes,
        speaker_transcript=transcript,
        **fields,
    )
    session.add(meeting)
    session.commit()
    return meeting


@pytest.fixture
def meeting(session, user):
    return make_meeting(session, user)


def request(session, user, meeting, language="fr", now=NOW):
    return translation.request_translation(session, user, meeting, language, now=now)


def work(session, model=None, now=NOW):
    return translation.process_next(session, now, client=model or FakeModel())


def only_row(session) -> MeetingTranslation:
    session.expire_all()
    return session.scalars(select(MeetingTranslation)).one()


# ---- queueing and caching ----


def test_a_request_queues_one_translating_row_and_nothing_is_translated_yet(session, user, meeting):
    row, cached = request(session, user, meeting)
    assert cached is False
    assert (row.meeting_id, row.language, row.status) == (meeting.id, "fr", "translating")
    assert row.notes_md is None and row.transcript is None
    assert row.source_hash == translation.source_hash(meeting.notes, meeting.speaker_transcript)


def test_asking_again_while_it_is_working_changes_nothing_and_is_not_cached(session, user, meeting):
    first, _ = request(session, user, meeting)
    second, cached = request(session, user, meeting)
    assert second.id == first.id and cached is False
    assert session.scalars(select(MeetingTranslation)).all() == [first]


def test_the_worker_translates_notes_and_transcript_and_the_row_becomes_ready(
    session, user, meeting
):
    request(session, user, meeting)
    model = FakeModel()
    assert work(session, model) is not None
    row = only_row(session)
    assert row.status == "ready" and row.failure_code is None
    assert "## Résumé" in row.notes_md and "FR Launch is on 14 March." in row.notes_md
    assert row.transcript.count("\n") == meeting.speaker_transcript.count("\n")
    assert row.transcript.startswith("[Adebayo]: FR We use Metrika now.")
    assert row.model and row.source_hash


def test_the_owners_vocabulary_and_the_speakers_are_the_names_to_keep(session, user, meeting):
    request(session, user, meeting)
    model = FakeModel()
    work(session, model)
    kept = {n for call in model.calls for n in translation_keep(call)}
    assert {"Metrika", "Adebayo", "Grace"} <= kept


def translation_keep(call):
    from tests.test_translation import keep_exactly

    return keep_exactly(call)


def test_a_second_request_for_a_ready_translation_is_served_from_the_cache(session, user, meeting):
    request(session, user, meeting)
    work(session, FakeModel())
    row, cached = request(session, user, meeting)
    assert cached is True and row.status == "ready"
    model = FakeModel()
    assert work(session, model) is None and model.calls == []  # nothing queued, nothing spent


def test_each_language_is_its_own_row(session, user, meeting):
    request(session, user, meeting, "fr")
    request(session, user, meeting, "es")
    assert sorted(r.language for r in session.scalars(select(MeetingTranslation))) == ["es", "fr"]


def test_regenerating_the_notes_makes_a_ready_translation_stale_and_a_request_redoes_it(
    session, user, meeting
):
    request(session, user, meeting)
    work(session, FakeModel())
    row = only_row(session)
    assert translation.is_stale(row, meeting) is False
    meeting.notes = NOTES.replace("14 March", "21 March")
    session.commit()
    assert translation.is_stale(row, meeting) is True
    again, cached = request(session, user, meeting)
    assert cached is False and again.status == "translating"
    assert again.notes_md is None and again.transcript is None  # the old words are not shown
    work(session, FakeModel())
    done = only_row(session)
    assert "21 March" in done.notes_md and translation.is_stale(done, meeting) is False


def test_a_change_to_the_transcript_alone_also_makes_it_stale(session, user, meeting):
    request(session, user, meeting)
    work(session, FakeModel())
    meeting.speaker_transcript = TRANSCRIPT + "\n[Grace]: One more thing."
    session.commit()
    assert translation.is_stale(only_row(session), meeting) is True


def test_the_hash_stored_is_that_of_the_text_the_worker_actually_read(session, user, meeting):
    request(session, user, meeting)
    meeting.notes = NOTES.replace("14 March", "9 May")  # rewritten after it was queued
    session.commit()
    work(session, FakeModel())
    row = only_row(session)
    assert "9 May" in row.notes_md
    assert row.source_hash == translation.source_hash(meeting.notes, meeting.speaker_transcript)


def test_a_meeting_that_is_not_ready_cannot_be_translated(session, user):
    waiting = make_meeting(session, user, status="processing", notes=None)
    with pytest.raises(Refused) as refused:
        request(session, user, waiting)
    assert (refused.value.status, refused.value.code) == (409, "not_ready")


def test_a_language_that_is_not_offered_is_refused_with_a_plain_message(session, user, meeting):
    for language in ("yo", "de", "en", "FR", "", "fr "):
        with pytest.raises(Refused) as refused:
            request(session, user, meeting, language)
        assert (refused.value.status, refused.value.code) == (422, "language_unsupported")
    assert "French" in refused.value.message and "Spanish" in refused.value.message


def test_a_meeting_with_no_transcript_translates_the_notes_alone(session, user):
    quiet = make_meeting(session, user, transcript=None)
    request(session, user, quiet)
    work(session, FakeModel())
    row = only_row(session)
    assert row.status == "ready" and row.transcript is None


# ---- the ledger ----


def test_a_translation_is_one_ledger_row_with_summed_tokens_and_no_text(session, user, meeting):
    request(session, user, meeting)
    model = FakeModel()
    work(session, model)
    run = session.scalars(select(AiRun)).one()
    assert (run.kind, run.status, run.meeting_id, run.error) == (
        "translate",
        "ok",
        meeting.id,
        None,
    )
    assert run.input_tokens == 1000 * len(model.calls)
    assert run.output_tokens == 400 * len(model.calls)
    assert run.prompt_version == translation.TRANSLATE_PROMPT_VERSION
    assert run.model == model.calls[0]["model"]


def test_a_failed_translation_is_a_failed_row_a_failed_ledger_entry_and_a_plain_reason(
    session, user, meeting
):
    request(session, user, meeting)
    model = FakeModel(lambda texts, n: ["x"])
    work(session, model)
    row = only_row(session)
    assert row.status == "failed" and row.failure_code == "structure"
    assert row.notes_md is None and row.transcript is None
    run = session.scalars(select(AiRun)).one()
    assert (run.kind, run.status) == ("translate", "failed")
    assert run.error == "TranslationFailed" and run.input_tokens > 0
    assert translation.failure_message(row.failure_code)


def test_nothing_in_the_ledger_or_the_row_holds_meeting_words_after_a_failure(
    session, user, meeting
):
    request(session, user, meeting)
    work(session, FakeModel(lambda texts, n: ["x"]))
    run = session.scalars(select(AiRun)).one()
    stored = " ".join(str(v) for v in (run.error, run.model, run.prompt_version))
    row = only_row(session)
    stored += " " + str(row.failure_code)
    assert "Metrika" not in stored and "Launch" not in stored


def test_an_unexpected_error_fails_the_row_and_never_raises_to_the_worker(session, user, meeting):
    request(session, user, meeting)

    def boom(texts, n):
        raise ValueError("secret transcript words")

    work(session, FakeModel(boom))
    row = only_row(session)
    assert row.status == "failed" and row.failure_code == "error"
    run = session.scalars(select(AiRun)).one()
    assert run.error == "ValueError" and run.status == "failed"


def test_asking_again_after_a_failure_queues_it_again(session, user, meeting):
    request(session, user, meeting)
    work(session, FakeModel(lambda texts, n: ["x"]))
    row, cached = request(session, user, meeting)
    assert cached is False and row.status == "translating" and row.failure_code is None
    work(session, FakeModel())
    assert only_row(session).status == "ready"


# ---- the daily limit ----


def test_twenty_translations_a_day_and_the_twenty_first_is_refused_with_429(session, user):
    meetings = [make_meeting(session, user) for _ in range(21)]
    for meeting in meetings[:20]:
        request(session, user, meeting)
    with pytest.raises(Refused) as refused:
        request(session, user, meetings[20])
    assert (refused.value.status, refused.value.code) == (429, "translation_limit")
    assert "20" in refused.value.message


def test_queued_and_finished_translations_both_count(session, user):
    meetings = [make_meeting(session, user) for _ in range(21)]
    for meeting in meetings[:10]:
        request(session, user, meeting)
    for _ in range(10):
        work(session)  # ten finished (ledger rows), none queued
    for meeting in meetings[10:20]:
        request(session, user, meeting)  # ten queued
    assert translation.daily_count(session, user.id, NOW) == 20
    with pytest.raises(Refused):
        request(session, user, meetings[20])


def test_redoing_the_same_translation_over_and_over_is_not_a_way_round_the_limit(session, user):
    meeting = make_meeting(session, user)
    for _ in range(20):
        request(session, user, meeting)
        work(session)
        row = session.scalars(select(MeetingTranslation)).one()
        session.delete(row)  # what DELETE does
        session.commit()
    with pytest.raises(Refused) as refused:
        request(session, user, meeting)
    assert refused.value.code == "translation_limit"


def test_yesterdays_translations_do_not_count(session, user):
    meetings = [make_meeting(session, user) for _ in range(21)]
    for meeting in meetings[:20]:
        request(session, user, meeting)
        work(session)
    session.execute(update(AiRun).values(created_at=NOW - timedelta(hours=25)))
    session.commit()
    request(session, user, meetings[20])  # allowed again


def test_a_cached_answer_and_a_translation_in_progress_never_use_the_limit(session, user, meeting):
    request(session, user, meeting)
    for _ in range(30):
        request(session, user, meeting)  # still the one job
    assert translation.daily_count(session, user.id, NOW) == 1
    work(session)
    for _ in range(30):
        request(session, user, meeting)  # cached
    assert translation.daily_count(session, user.id, NOW) == 1


def test_the_limit_is_per_account(session, user):
    other = User(email="other@example.test")
    session.add(other)
    session.flush()
    session.add(Template(owner_user_id=other.id, name="General", instructions="x"))
    session.commit()
    for _ in range(20):
        request(session, user, make_meeting(session, user))
    request(session, other, make_meeting(session, other))  # not affected


# ---- the worker's claim ----


def test_a_row_deleted_while_the_model_works_is_not_brought_back_but_the_call_is_recorded(
    session, user, meeting
):
    request(session, user, meeting)

    def delete_meanwhile(texts, n):
        session.execute(text("delete from meeting_translations"))
        session.commit()
        return [f"FR {t}" for t in texts]

    work(session, FakeModel(delete_meanwhile))
    assert session.scalars(select(MeetingTranslation)).all() == []
    assert session.scalars(select(AiRun)).one().status == "ok"  # it cost something


def test_a_row_re_queued_while_the_model_works_keeps_the_new_request(session, user, meeting):
    row, _ = request(session, user, meeting)

    def requeue_meanwhile(texts, n):
        session.execute(
            update(MeetingTranslation).values(started_at=None, attempts=0, notes_md="STALE")
        )
        session.commit()
        return [f"FR {t}" for t in texts]

    work(session, FakeModel(requeue_meanwhile))
    again = only_row(session)
    assert again.status == "translating" and again.notes_md == "STALE"  # not overwritten


def test_a_translation_a_dead_worker_left_running_is_taken_again_after_a_while(
    session, user, meeting
):
    request(session, user, meeting)
    session.execute(
        update(MeetingTranslation).values(started_at=NOW - timedelta(minutes=5), attempts=1)
    )
    session.commit()
    assert work(session) is None  # five minutes: still held
    session.execute(
        update(MeetingTranslation).values(started_at=NOW - translation.STALE_RUNNING * 2)
    )
    session.commit()
    assert work(session) is not None
    assert only_row(session).status == "ready"


def test_a_translation_that_keeps_killing_the_worker_is_given_up_on(session, user, meeting):
    request(session, user, meeting)
    session.execute(
        update(MeetingTranslation).values(
            started_at=NOW - translation.STALE_RUNNING * 2, attempts=translation.MAX_ATTEMPTS
        )
    )
    session.commit()
    model = FakeModel()
    work(session, model)
    row = only_row(session)
    assert row.status == "failed" and row.failure_code == "gave_up" and model.calls == []


def test_a_meeting_of_an_account_being_deleted_is_not_worked_on(session, user, meeting):
    request(session, user, meeting)
    user.status = "deleted"
    session.commit()
    model = FakeModel()
    assert work(session, model) is None and model.calls == []


def test_work_is_taken_oldest_first_one_per_step(session, user):
    a, b = make_meeting(session, user), make_meeting(session, user)
    request(session, user, a)
    request(session, user, b)
    work(session)
    states = {r.meeting_id: r.status for r in session.scalars(select(MeetingTranslation))}
    assert states == {a.id: "ready", b.id: "translating"}


def test_deleting_a_meeting_deletes_its_translations(session, user, meeting):
    request(session, user, meeting)
    work(session)
    session.execute(text("delete from meetings where id = :i"), {"i": meeting.id})
    session.commit()
    assert session.scalars(select(MeetingTranslation)).all() == []
    assert session.scalars(select(AiRun)).one().meeting_id is None  # the ledger outlives it


def test_the_loop_runs_until_stopped_and_survives_a_failing_pass(monkeypatch):
    import threading

    stop = threading.Event()
    calls = []

    class Fake:
        def __enter__(self):
            return object()

        def __exit__(self, *exc):
            return False

    def step(session, now, client=None):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("database briefly down")
        if len(calls) >= 3:
            stop.set()
        return None

    monkeypatch.setattr(translation, "process_next", step)
    translation.run_loop(stop, session_factory=Fake, idle_seconds=0.0)
    assert len(calls) >= 3


def test_purging_an_account_removes_its_translations_and_keeps_only_the_ledger(
    session, user, meeting
):
    from app import deletion

    request(session, user, meeting)
    work(session)
    deletion.purge_user(session, user, vexa=None, force=True)
    assert session.scalars(select(MeetingTranslation)).all() == []
    [run] = session.scalars(select(AiRun)).all()
    assert run.kind == "translate" and run.meeting_id is None


def test_the_worker_starts_the_translation_thread_and_it_stops_with_the_worker(monkeypatch):
    import threading

    from app import worker

    started = threading.Event()
    seen = {}

    def loop(stop):
        seen["stop"] = stop
        started.set()
        stop.wait(5)

    monkeypatch.setattr(translation, "run_loop", loop)
    stop = threading.Event()
    thread = worker.start_translation_thread(stop)
    assert started.wait(2) and thread.daemon and thread.is_alive()
    stop.set()
    thread.join(2)
    assert not thread.is_alive() and seen["stop"] is stop


def test_deleting_meetings_does_not_give_translations_back(session, user):
    """The limit counts by account, not through the meeting: a meeting's ledger rows lose their
    `meeting_id` when it is deleted, which must not lower the count."""
    meetings = [make_meeting(session, user) for _ in range(20)]
    for meeting in meetings:
        request(session, user, meeting)
        work(session)
    session.execute(text("delete from meetings"))
    session.commit()
    assert translation.daily_count(session, user.id, NOW) == 20
    fresh = make_meeting(session, user)
    with pytest.raises(Refused) as refused:
        request(session, user, fresh)
    assert refused.value.code == "translation_limit"
