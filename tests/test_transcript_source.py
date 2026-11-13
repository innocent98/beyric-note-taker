"""The transcript-source switch (`TRANSCRIPT_SOURCE`, `app/transcript_source.py`): the stored
transcript built from Skribby's segments, the fallback to our own transcription, the order of the
provider's copy being deleted, metering, the cost ledger, the sampled shadow check, the settings,
and the vocabulary sent to Skribby. Pure rules first, then the pipeline over a real database."""

import logging
import threading
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import sessionmaker

from app import pipeline, skribby, transcript_source, translation, worker
from app.bot_provider import BotProviderError
from app.config import Settings
from app.models import (
    AiRun,
    Meeting,
    TranscriptShadowMetric,
    UsageEntry,
    VocabularyTerm,
)
from app.skribby import SkribbyClient
from app.vexa import VexaMeeting
from scripts import transcript_shadow_report
from tests.test_pipeline import (  # noqa: F401  (fixtures and fakes used by name)
    NOTES,
    NOW,
    RECORDED_SECONDS,
    TRANSCRIPT,
    FakeOpenAI,
    FakeVexa,
    env,
    meeting,
    run,
)
from tests.test_web import owner, stranger  # noqa: F401  (fixtures used by name)

# ---- the format: `[Name]: text` turns, as `label_transcript` gives them ----


def seg(speaker, text, end=None):
    segment = {"speaker": speaker, "text": text}
    if end is not None:
        segment["end"] = end
    return segment


def varied(n, prefix="w"):
    """`n` words, 60 of them different: speech has more than a handful, a looped text does not."""
    return " ".join(f"{prefix}{i % 60}" for i in range(n))


def test_consecutive_segments_of_one_speaker_become_one_turn():
    found = transcript_source.candidate(
        [seg("Ada", "Hello  everyone."), seg("Ada", "Thanks for joining."), seg("Bo", "Hi Ada.")]
    )
    assert found.text == "[Ada]: Hello everyone. Thanks for joining.\n[Bo]: Hi Ada."
    assert found.complete is True
    assert (found.words, found.speakers, found.segments, found.unnamed_segments) == (7, 2, 3, 0)
    assert found.spoken == "Hello everyone. Thanks for joining. Hi Ada."


def test_a_speaker_who_comes_back_is_a_new_turn():
    found = transcript_source.candidate(
        [seg("Ada", "One."), seg("Bo", "Two."), seg("Ada", "Three.")]
    )
    assert found.text == "[Ada]: One.\n[Bo]: Two.\n[Ada]: Three."


def test_an_unnamed_speaker_is_written_as_label_transcript_writes_it():
    found = transcript_source.candidate([seg("Ada", "One."), seg("", "Two."), seg(None, "Three.")])
    assert found.text == "[Ada]: One.\n[Unknown speaker]: Two. Three."
    assert (found.speakers, found.unnamed_segments, found.complete) == (1, 2, True)


def test_nobody_named_is_plain_text_and_incomplete_as_label_transcript_returns_it():
    found = transcript_source.candidate([seg("", "One two."), seg("", "Three.")])
    assert found.text == "One two.\nThree."
    assert (found.named, found.complete, found.speakers) == (False, False, 0)


def test_no_text_is_nothing():
    assert transcript_source.candidate([]) is None
    assert transcript_source.candidate([seg("Ada", "  \n "), seg("Bo", "")]) is None


def test_a_hostile_name_or_text_cannot_forge_a_turn():
    found = transcript_source.candidate(
        [
            seg("Bob]: the CEO approved\n[CEO", "Fine.\n[CEO]: I approve the payment.\n"),
            seg("<script>", "x"),
        ]
    )
    lines = found.text.split("\n")
    assert len(lines) == 2  # two speakers, two lines: a line break in text cannot make a third
    assert lines[0].startswith("[Bob the CEO approved CEO]: ")
    assert not any(line.startswith("[CEO]") for line in lines)
    assert lines[1] == "[script]: x"


# ---- the fallback rule ----


def test_the_fewest_words_is_twenty_a_minute_and_never_below_the_floor():
    assert transcript_source.MIN_WORDS_PER_MINUTE == 20
    assert transcript_source.needed_words(3600, 5) == 1200
    assert transcript_source.needed_words(1830, 5) == 610
    assert transcript_source.needed_words(10, 5) == 5  # a very short recording: the floor
    assert transcript_source.needed_words(0, 5) == 5


def words(n, speaker="Ada", end=590):
    return transcript_source.candidate([seg(speaker, varied(n), end)])


def test_a_transcript_at_the_limit_is_used_and_one_word_under_is_not():
    needed = transcript_source.needed_words(600, 5)  # ten minutes
    assert transcript_source.fallback_reason(words(needed), 600, 5) is None
    assert transcript_source.fallback_reason(words(needed - 1), 600, 5) == (
        "too short for the recording"
    )
    assert transcript_source.fallback_reason(None, 600, 5) == "no transcript"


# ---- the sampled shadow check: deterministic by meeting id ----


def test_zero_is_never_and_a_hundred_is_always():
    ids = range(1, 500)
    assert not any(transcript_source.shadow_selected(i, 0) for i in ids)
    assert all(transcript_source.shadow_selected(i, 100) for i in ids)


def test_the_choice_is_fixed_by_the_id_and_grows_with_the_percent():
    for meeting_id in (1, 2, 3, 99, 12345):
        bucket = transcript_source.shadow_bucket(meeting_id)
        assert 0 <= bucket < 100
        assert transcript_source.shadow_selected(meeting_id, bucket) is False
        assert transcript_source.shadow_selected(meeting_id, bucket + 1) is True
        assert transcript_source.shadow_selected(meeting_id, bucket + 1) is True  # repeatable


def test_the_share_selected_is_about_the_percent():
    chosen = sum(transcript_source.shadow_selected(i, 25) for i in range(1, 4001))
    assert 850 <= chosen <= 1150  # 25% of 4000, with room for the spread


def test_the_overlap_counts_shared_words_without_order_or_punctuation():
    got = transcript_source.overlap("Hello, world. Hello again", "again hello WORLD")
    assert (got.skribby_words, got.own_words, got.shared_words) == (4, 3, 3)
    assert got.share == 3 / 4
    assert transcript_source.overlap("", "").share == 1.0
    assert transcript_source.overlap("one two", "").share == 0.0


# ---- the settings ----


def test_a_bad_source_stops_startup_naming_the_setting():
    with pytest.raises(
        ValueError, match="TRANSCRIPT_SOURCE is 'whisper'; it must be own or skribby"
    ):
        Settings(transcript_source="whisper")
    assert Settings().transcript_source == "own"  # the default is today's behaviour
    assert Settings(transcript_source="").transcript_source == "own"
    on = Settings(transcript_source=" Skribby ", skribby_transcription_model=MODEL)
    assert on.transcript_source == "skribby"


@pytest.mark.parametrize("bad", [-1, 101])
def test_the_shadow_percent_must_be_from_0_to_100(bad):
    with pytest.raises(ValueError, match="TRANSCRIPT_SHADOW_PERCENT"):
        Settings(transcript_shadow_percent=bad)
    assert Settings().transcript_shadow_percent == 0
    assert Settings(transcript_shadow_percent=100).transcript_shadow_percent == 100
    assert Settings(transcript_shadow_percent="").transcript_shadow_percent == 0


# ---- the owner's vocabulary goes to Skribby only in skribby mode ----


def make_client(model="soniox/stt-async-v6"):  # a model Skribby lists with custom vocabulary
    return SkribbyClient(api_key="k" * 8, transcription_model=model)


def test_the_vocabulary_is_sent_to_skribby_as_custom_vocabulary_cleaned_and_capped():
    terms = ["Beyric", "Beyric", " Ada ", "bad<term>", "x" * 150, *[f"t{i}" for i in range(80)]]
    body = make_client().payload(
        "google_meet", "abc-defg-hij", "https://meet.google.com/abc", terms
    )
    sent = body["custom_vocabulary"]
    assert sent[:2] == ["Beyric", "Ada"]
    assert len(sent) == 50 and all(len(t) <= 100 and "<" not in t for t in sent)


def test_no_vocabulary_field_without_terms_or_when_transcription_is_off():
    plain = make_client().payload("google_meet", "abc-defg-hij", "https://meet.google.com/abc")
    assert "custom_vocabulary" not in plain
    off = make_client("none").payload("google_meet", "abc", "https://meet.google.com/abc", ["Ada"])
    assert "custom_vocabulary" not in off


def skribby_settings(model):
    """Skribby mode with `model`. Startup only allows ElevenLabs in this mode, so a model that takes
    vocabulary (the setting is only read, never re-validated) is set afterwards."""
    settings = Settings(transcript_source="skribby", skribby_transcription_model=MODEL)
    settings.skribby_transcription_model = model
    return settings


class TakesVocabulary:
    accepts_vocabulary = True


class Plain:
    pass


def test_send_options_pass_the_owners_own_terms_only_in_skribby_mode(session, owner, stranger):
    session.add_all(
        [
            VocabularyTerm(owner_user_id=owner.id, term="Beyric"),
            VocabularyTerm(owner_user_id=stranger.id, term="Secret"),
        ]
    )
    session.commit()
    on = skribby_settings(VOCAB_MODEL)
    off = Settings()
    assert transcript_source.send_options(session, TakesVocabulary(), owner.id, on) == {
        "vocabulary": ["Beyric"]
    }
    assert transcript_source.send_options(session, TakesVocabulary(), owner.id, off) == {}
    assert transcript_source.send_options(session, Plain(), owner.id, on) == {}
    assert transcript_source.send_options(None, TakesVocabulary(), owner.id, on) == {}
    # ElevenLabs is not listed by Skribby with custom vocabulary: nothing is sent (or even read)
    assert (
        transcript_source.send_options(
            session, TakesVocabulary(), owner.id, skribby_settings(MODEL)
        )
        == {}
    )


def test_only_the_models_skribby_lists_with_custom_vocabulary_are_sent_it():
    taken = {
        "soniox/stt-async-v6",
        "groq/whisper-large-v3-turbo",
        "groq/whisper-large-v3",
        "mistral/voxtral-mini-2",
        "gladia/async",
    }
    assert skribby.VOCABULARY_MODELS == taken
    for model in taken:
        body = make_client(model).payload("google_meet", "a", "https://meet.google.com/a", ["Ada"])
        assert body["custom_vocabulary"] == ["Ada"], model
    for model in (
        "elevenlabs/scribe-v2",  # neither page lists it
        "deepgram/nova-3-multilingual",  # the two pages disagree: left out
        "assemblyai/universal-3-5-pro",
        "speechmatics/async",
        "soniox/stt-async-v5",  # deprecated
        "none",
    ):
        body = make_client(model).payload("google_meet", "a", "https://meet.google.com/a", ["Ada"])
        assert "custom_vocabulary" not in body, model


# ---- the pipeline ----

MODEL = "elevenlabs/scribe-v2"
VOCAB_MODEL = "soniox/stt-async-v6"  # listed by Skribby with custom vocabulary
SKRIBBY_TEXT_WORDS = 700  # RECORDED_SECONDS (1830) needs 610


def long_segments():
    """Ada, Bo, Ada again, with enough words for the recording's length."""
    chunk = varied(SKRIBBY_TEXT_WORDS // 3, "alpha")
    return [seg("Ada", chunk, 600), seg("Bo", chunk, 1200), seg("Ada", chunk, 1800)]


def done(segments):
    """A finished bot; `last_segment_end` as Skribby's mapping would give it (seconds)."""
    ends = [x["end"] for x in segments if "end" in x]
    return VexaMeeting(
        status="completed",
        segments=segments,
        last_segment_end=max(ends) if ends else None,
        recording_id=7,
        media_id=9,
        recording_status="completed",
    )


# Looks made in order: the poll, the audio step, the transcript fetch, then the speakers step.
FETCH_FAILS = (done(long_segments()), done(long_segments()), BotProviderError("down"), done([]))


def expected_text():
    return transcript_source.candidate(long_segments()).text


@pytest.fixture
def mode(monkeypatch, env):
    """Switch the pipeline's settings: `mode(source="skribby", shadow=0)`."""

    def switch(source="skribby", shadow=0):
        monkeypatch.setattr(
            pipeline,
            "get_settings",
            lambda: Settings(
                audio_dir=str(env / "a"),
                transcript_source=source,
                skribby_transcription_model=MODEL,
                transcript_shadow_percent=shadow,
            ),
        )

    return switch


@pytest.fixture
def skribby_meeting(session, meeting):
    meeting.bot_provider = "skribby"
    session.commit()
    return meeting


def ledger(session, meeting_id, kind=None):
    query = select(AiRun).where(AiRun.meeting_id == meeting_id).order_by(AiRun.id)
    if kind:
        query = query.where(AiRun.kind == kind)
    return list(session.scalars(query))


def test_the_default_is_unchanged_even_for_a_skribby_meeting(session, skribby_meeting):
    openai = run(session, skribby_meeting, FakeVexa(done(long_segments())))
    assert skribby_meeting.transcript == TRANSCRIPT  # our own transcription
    assert skribby_meeting.transcript_source == "own"
    assert openai.transcriptions == 1
    assert [r.kind for r in ledger(session, skribby_meeting.id)] == ["transcribe", "label", "notes"]
    assert session.scalars(select(TranscriptShadowMetric)).first() is None


def test_skribby_mode_makes_the_notes_from_skribbys_turns(session, skribby_meeting, mode):
    mode()
    openai = run(session, skribby_meeting, FakeVexa(done(long_segments())))
    assert (skribby_meeting.status, skribby_meeting.step) == ("ready", "done")
    assert skribby_meeting.transcript == expected_text()
    assert skribby_meeting.speaker_transcript == expected_text()
    assert skribby_meeting.transcript.split("\n")[0].startswith("[Ada]: alpha")
    assert skribby_meeting.transcript_source == "skribby"
    assert skribby_meeting.speakers_incomplete is False
    assert skribby_meeting.notes == NOTES + "\n"
    assert openai.transcriptions == 0  # no OpenAI transcription at all
    assert openai.chats == 1  # the notes only: no model-based speaker labelling


def test_nothing_is_billed_to_openai_for_a_skribby_transcript(session, skribby_meeting, mode):
    mode()
    run(session, skribby_meeting, FakeVexa(done(long_segments())))
    assert [r.kind for r in ledger(session, skribby_meeting.id)] == ["notes"]  # no fake row


def test_hours_metering_is_unchanged(session, skribby_meeting, mode):
    mode()
    run(session, skribby_meeting, FakeVexa(done(long_segments())))
    entry = session.scalars(
        select(UsageEntry).where(UsageEntry.meeting_id == skribby_meeting.id)
    ).one()
    assert entry.seconds == RECORDED_SECONDS == skribby_meeting.recorded_seconds


def test_nobody_named_is_unlabelled_and_marked_incomplete(session, skribby_meeting, mode):
    mode()
    chunk = varied(700, "alpha")
    run(session, skribby_meeting, FakeVexa(done([seg("", chunk, 1800)])))
    assert skribby_meeting.transcript == chunk
    assert skribby_meeting.speakers_incomplete is True
    assert skribby_meeting.transcript_source == "skribby"


@pytest.mark.parametrize(
    "segments",
    [
        [],
        [seg("Ada", "   ")],
        [seg("Ada", "just a few words here")],
        [seg("Ada", varied(609), 1800)],  # one word under 20 a minute for 1830 s
        [seg("Ada", "Thank you. " * 400, 1800)],  # a loop: enough words, a handful of different
        [seg("Ada", varied(700), 1000)],  # ends at 55% of the recording: the rest is missing
        [seg("Ada", varied(700))],  # no segment times at all
    ],
    ids=["none", "blank", "short", "just_under", "loop", "stops_early", "no_times"],
)
def test_missing_or_too_short_falls_back_to_our_own_transcription(
    session, skribby_meeting, mode, segments
):
    mode()
    openai = run(session, skribby_meeting, FakeVexa(done(segments)))
    assert skribby_meeting.transcript == TRANSCRIPT
    assert skribby_meeting.transcript_source == "own"
    assert openai.transcriptions == 1
    assert (skribby_meeting.status, skribby_meeting.step) == ("ready", "done")
    assert len(ledger(session, skribby_meeting.id, "transcribe")) == 1  # the real call is logged


def test_a_transcript_at_the_limit_is_used(session, skribby_meeting, mode):
    mode()
    openai = run(session, skribby_meeting, FakeVexa(done([seg("Ada", varied(610), 1281)])))
    assert skribby_meeting.transcript_source == "skribby"
    assert openai.transcriptions == 0


def test_a_failed_fetch_of_skribbys_transcript_falls_back_without_failing(
    session, skribby_meeting, mode, caplog
):
    """The poll and the audio step work, then the transcript fetch fails: ours is used (and the
    speakers step, which looks again, works)."""
    mode()
    vexa = FakeVexa(*FETCH_FAILS)
    with caplog.at_level(logging.INFO):
        openai = run(session, skribby_meeting, vexa)
    assert skribby_meeting.transcript_source == "own"
    assert openai.transcriptions == 1
    assert "could not be fetched (BotProviderError)" in caplog.text


def test_a_vexa_meeting_always_uses_our_transcription(session, meeting, mode):
    mode()
    openai = run(session, meeting, FakeVexa(done(long_segments())))  # bot_provider is vexa
    assert meeting.transcript_source == "own"
    assert openai.transcriptions == 1


def test_the_log_says_which_source_and_never_the_text(session, skribby_meeting, mode, caplog):
    mode()
    segments = [seg("Ada", "zebraquill " + varied(699), 1800)]
    with caplog.at_level(logging.DEBUG):
        run(session, skribby_meeting, FakeVexa(done(segments)))
    assert "transcript from skribby (700 words, 1 speakers, 0 unnamed)" in caplog.text
    assert "zebraquill" not in caplog.text and "Ada" not in caplog.text


class CopyHolder(FakeVexa):
    """Records what the database holds at the moment the provider's copy is deleted."""

    def __init__(self, engine, *polls):
        super().__init__(*polls)
        self.engine = engine
        self.deleted_when = []

    def delete_bot_record(self, bot_id):
        with sessionmaker(bind=self.engine)() as other:
            row = other.execute(
                select(Meeting.transcript, Meeting.speaker_transcript, Meeting.step).where(
                    Meeting.vexa_meeting_id == str(bot_id)
                )
            ).one()
        self.deleted_when.append(row)


def test_skribbys_copy_is_kept_until_the_transcript_is_saved_then_deleted(
    session, engine, skribby_meeting, mode
):
    mode()
    skribby_meeting.vexa_meeting_id = "bot-1"
    session.commit()
    vexa = CopyHolder(engine, done(long_segments()))
    run(session, skribby_meeting, vexa)
    assert len(vexa.deleted_when) == 1  # deleted once, after the notes' step was reached
    transcript, speaker_transcript, step = vexa.deleted_when[0]
    assert transcript == expected_text() == speaker_transcript  # already committed by then
    assert step == "notes"
    assert skribby_meeting.provider_copy_deleted_at is not None


def test_the_copy_is_not_deleted_while_the_transcript_step_is_failing(
    session, engine, skribby_meeting, mode
):
    """Skribby's text is the only copy: if the step cannot save it, nothing is deleted."""
    mode()
    skribby_meeting.vexa_meeting_id = "bot-1"
    session.commit()
    vexa = CopyHolder(engine, done(long_segments()))
    openai = FakeOpenAI(fail_notes=RuntimeError("boom"))
    run(session, skribby_meeting, vexa, openai)
    assert skribby_meeting.status == "failed"
    assert len(vexa.deleted_when) == 1  # deleted only AFTER the transcript was saved
    assert vexa.deleted_when[0][0] == expected_text()


# ---- the sampled shadow check, in the pipeline ----


@pytest.fixture(autouse=True)
def empty_shadow_queue():
    pipeline._shadow_queue.clear()
    yield
    pipeline._shadow_queue.clear()


def shadow(session, openai):
    return pipeline.run_shadow_checks(session, openai)


class OrderedOpenAI(FakeOpenAI):
    """Records the order of the calls: `notes` or `transcribe`."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.order = []

    def _transcribe(self, **kwargs):
        self.order.append("transcribe")
        return super()._transcribe(**kwargs)

    def _chat(self, **kwargs):
        reply = super()._chat(**kwargs)
        if not kwargs["messages"][0]["content"].startswith("You add speaker labels"):
            self.order.append("notes")
        return reply


def test_shadow_zero_never_calls_openai_transcription(session, skribby_meeting, mode):
    mode(shadow=0)
    openai = run(session, skribby_meeting, FakeVexa(done(long_segments())))
    assert shadow(session, openai) == 0
    assert openai.transcriptions == 0
    assert session.scalars(select(TranscriptShadowMetric)).first() is None


def test_the_shadow_waits_for_the_notes_and_never_delays_them(session, skribby_meeting, mode):
    """The meeting is ready, with its notes, before our transcription is even attempted."""
    mode(shadow=100)
    openai = OrderedOpenAI()
    run(session, skribby_meeting, FakeVexa(done(long_segments())), openai)
    assert (skribby_meeting.status, skribby_meeting.step) == ("ready", "done")
    assert skribby_meeting.notes == NOTES + "\n"
    assert openai.transcriptions == 0  # not attempted in the meeting's own pass
    assert session.scalars(select(TranscriptShadowMetric)).first() is None
    assert skribby_meeting.id in pipeline._shadow_queue  # waiting its turn
    assert shadow(session, openai) == 1
    assert openai.order == ["notes", "transcribe"]  # the notes came first


def test_shadow_hundred_runs_ours_keeps_numbers_and_the_notes_stay_skribbys(
    session, skribby_meeting, mode
):
    mode(shadow=100)
    openai = run(session, skribby_meeting, FakeVexa(done(long_segments())))
    assert shadow(session, openai) == 1
    assert openai.transcriptions == 1
    session.refresh(skribby_meeting)
    assert skribby_meeting.transcript == expected_text()  # still Skribby's, ours was dropped
    assert skribby_meeting.transcript_source == "skribby"
    row = session.scalars(select(TranscriptShadowMetric)).one()
    assert row.meeting_id == skribby_meeting.id
    assert (row.skribby_words, row.segments, row.skribby_speakers) == (699, 3, 2)
    assert row.unnamed_segments == 0 and row.notes_source == "skribby"
    assert row.audio_seconds == RECORDED_SECONDS
    assert row.own_words == len(TRANSCRIPT.split())
    assert 0.0 <= row.shared_share <= 1.0 and row.shared_words <= row.own_words
    assert shadow(session, openai) == 0  # taken off the queue: it never runs twice


def test_the_shadow_spend_is_its_own_ledger_kind_with_no_user(session, skribby_meeting, mode):
    """Real spend (it is in the totals) that is not a transcript and not the owner's doing."""
    mode(shadow=100)
    openai = run(session, skribby_meeting, FakeVexa(done(long_segments())))
    shadow(session, openai)
    assert ledger(session, skribby_meeting.id, "transcribe") == []
    paid = ledger(session, skribby_meeting.id, "shadow")
    assert len(paid) == 1
    assert paid[0].audio_seconds == RECORDED_SECONDS and paid[0].model == "gpt-transcribe"
    assert paid[0].user_id is None and paid[0].status == "ok"
    total = session.scalar(
        select(func.sum(AiRun.audio_seconds)).where(AiRun.kind.in_(("transcribe", "shadow")))
    )
    assert total == RECORDED_SECONDS  # a cost query over audio seconds still counts it


def test_shadow_picks_by_the_meeting_id(session, skribby_meeting, mode):
    bucket = transcript_source.shadow_bucket(skribby_meeting.id)
    openai = FakeOpenAI()
    mode(shadow=bucket)  # one under: not chosen
    run(session, skribby_meeting, FakeVexa(done(long_segments())), openai)
    assert shadow(session, openai) == 0 and openai.transcriptions == 0
    session.execute(
        Meeting.__table__.update()
        .where(Meeting.id == skribby_meeting.id)
        .values(status="bot_sent", step="waiting", transcript=None, speaker_transcript=None)
    )
    session.commit()
    mode(shadow=bucket + 1)  # at the id's bucket: chosen
    run(session, skribby_meeting, FakeVexa(done(long_segments())), openai)
    assert shadow(session, openai) == 1 and openai.transcriptions == 1


def test_a_queued_check_waits_for_a_meeting_still_working_and_is_dropped_if_it_failed(
    session, skribby_meeting
):
    recording = SimpleNamespace(chunks=[], seconds=60)
    found = transcript_source.candidate([seg("Ada", varied(50), 60)])
    skribby_meeting.status = "processing"
    session.commit()
    pipeline._queue_shadow(
        skribby_meeting.id, skribby_meeting.owner_user_id, found, recording, datetime.now(UTC)
    )
    assert shadow(session, FakeOpenAI()) == 0
    assert skribby_meeting.id in pipeline._shadow_queue  # still waiting
    skribby_meeting.status = "failed"
    session.commit()
    assert shadow(session, FakeOpenAI()) == 0
    assert pipeline._shadow_queue == {}  # no check for a meeting with no notes


def test_the_shadow_row_holds_numbers_only_never_text(session, skribby_meeting, mode, caplog):
    mode(shadow=100)
    ours = "lemonquartz " * 40
    segments = [seg("Zeppelin Quokka", "walnutmarble " + varied(699), 1800)]
    openai = FakeOpenAI(transcript=ours)
    with caplog.at_level(logging.DEBUG):
        run(session, skribby_meeting, FakeVexa(done(segments)), openai)
        shadow(session, openai)
    row = session.scalars(select(TranscriptShadowMetric)).one()
    columns = {c.name: getattr(row, c.name) for c in TranscriptShadowMetric.__table__.columns}
    assert set(columns) == {
        "id", "meeting_id", "skribby_words", "own_words", "shared_words", "shared_share",
        "skribby_speakers", "unnamed_segments", "segments", "audio_seconds", "notes_source",
        "created_at",
    }  # fmt: skip
    dump = " ".join(str(v) for v in columns.values())
    assert not any(w in dump for w in ("lemonquartz", "walnutmarble", "Zeppelin", "@"))
    assert not any(w in caplog.text for w in ("lemonquartz", "walnutmarble", "Zeppelin"))
    assert row.shared_words == 0 and row.shared_share == 0.0
    session.refresh(skribby_meeting)
    meeting_text = skribby_meeting.transcript + (skribby_meeting.speaker_transcript or "")
    assert "lemonquartz" not in meeting_text  # our shadow text was thrown away


def test_a_failing_shadow_run_leaves_the_ready_meeting_alone(
    session, skribby_meeting, mode, caplog
):
    mode(shadow=100)

    class Broken(FakeOpenAI):
        def _transcribe(self, **kwargs):
            raise RuntimeError("openai down")

    openai = run(session, skribby_meeting, FakeVexa(done(long_segments())), Broken())
    with caplog.at_level(logging.WARNING):
        shadow(session, openai)
    session.refresh(skribby_meeting)
    assert (skribby_meeting.status, skribby_meeting.step) == ("ready", "done")
    assert skribby_meeting.transcript_source == "skribby"
    assert session.scalars(select(TranscriptShadowMetric)).first() is None
    assert ledger(session, skribby_meeting.id, "shadow") == []  # nothing was billed
    assert "the shadow check was not made" in caplog.text


def test_a_database_error_reading_the_vocabulary_cannot_fail_a_saved_transcript(
    session, skribby_meeting, mode, monkeypatch, caplog
):
    """The vocabulary is read inside the shadow's own guard, after the notes: never before."""
    mode(shadow=100)
    openai = run(session, skribby_meeting, FakeVexa(done(long_segments())))

    def broken(*args, **kwargs):
        raise RuntimeError("db gone")

    monkeypatch.setattr(pipeline, "_terms", broken)
    with caplog.at_level(logging.WARNING):
        assert shadow(session, openai) == 1  # attempted, failed quietly
    session.refresh(skribby_meeting)
    assert (skribby_meeting.status, skribby_meeting.transcript_source) == ("ready", "skribby")
    assert "the shadow check was not made (RuntimeError)" in caplog.text


def test_if_the_ledger_row_cannot_be_written_the_cost_is_logged_and_the_numbers_kept(
    session, skribby_meeting, mode, monkeypatch, caplog
):
    mode(shadow=100)
    openai = run(session, skribby_meeting, FakeVexa(done(long_segments())))

    def broken(*args, **kwargs):
        raise RuntimeError("ledger down")

    monkeypatch.setattr(pipeline.ai_runs, "record", broken)
    with caplog.at_level(logging.ERROR):
        shadow(session, openai)
    assert (
        f"{RECORDED_SECONDS} seconds of audio were transcribed for the shadow check" in caplog.text
    )
    assert session.scalars(select(TranscriptShadowMetric)).one().own_words > 0


def test_the_worker_runs_the_shadow_checks_in_a_thread_of_its_own(monkeypatch):
    stop = threading.Event()
    calls = []

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_checks(session):
        calls.append(session)
        stop.set()
        return 1

    monkeypatch.setattr(worker, "SessionLocal", FakeSession)
    monkeypatch.setattr(worker, "run_shadow_checks", fake_checks)
    thread = worker.start_shadow_thread(stop)
    thread.join(timeout=5)
    assert not thread.is_alive() and len(calls) == 1 and thread.daemon


def test_on_a_fallback_the_shadow_numbers_cost_nothing_more(session, skribby_meeting, mode):
    mode(shadow=100)
    openai = run(session, skribby_meeting, FakeVexa(done([seg("Ada", "too few words", 1800)])))
    assert openai.transcriptions == 1  # the fallback's own call, no second one
    assert shadow(session, openai) == 0 and openai.transcriptions == 1
    row = session.scalars(select(TranscriptShadowMetric)).one()
    assert (row.notes_source, row.skribby_words, row.segments) == ("own", 3, 1)
    assert len(ledger(session, skribby_meeting.id, "transcribe")) == 1


def test_no_shadow_numbers_when_the_fetch_itself_failed(session, skribby_meeting, mode):
    mode(shadow=100)
    openai = run(session, skribby_meeting, FakeVexa(*FETCH_FAILS))
    shadow(session, openai)
    assert session.scalars(select(TranscriptShadowMetric)).first() is None


def test_metrics_go_with_the_meeting(session, skribby_meeting, mode):
    mode(shadow=100)
    openai = run(session, skribby_meeting, FakeVexa(done(long_segments())))
    shadow(session, openai)
    session.delete(skribby_meeting)
    session.commit()
    assert session.scalars(select(TranscriptShadowMetric)).first() is None


# ---- Skribby's text is not trusted blindly ----


def test_a_looped_text_is_not_used():
    loop = transcript_source.candidate([seg("Ada", "Thank you. " * 400, 590)])
    assert transcript_source.fallback_reason(loop, 600, 5) == "repeats the same few words"
    real = transcript_source.candidate([seg("Ada", varied(400), 590)])
    assert transcript_source.fallback_reason(real, 600, 5) is None


def test_a_text_that_stops_early_is_not_used_and_the_edge_is_70_percent():
    def reason(end):
        found = transcript_source.candidate([seg("Ada", varied(400), end)])
        return transcript_source.fallback_reason(found, 600, 5)

    assert reason(420) is None  # exactly 70% of 600 s
    assert reason(419.9) == "stops well before the end of the recording"
    assert reason(600) is None


def test_the_last_segment_time_is_the_latest_end_in_seconds_and_junk_is_ignored():
    found = transcript_source.candidate(
        [
            seg("Ada", "one two", 19.11),
            {"speaker": "Bo", "text": "three", "end": "soon"},
            {"speaker": "Bo", "text": "four", "end": True},
            {"speaker": "Bo", "text": "five", "end": float("nan")},
            {"speaker": "Bo", "text": "six", "end": -4},
            seg("Bo", "seven", 7.53),
        ]
    )
    assert found.last_end == 19.11
    assert transcript_source.candidate([seg("Ada", "one two")]).last_end is None
    nothing = transcript_source.candidate([seg("Ada", varied(400))])
    assert transcript_source.fallback_reason(nothing, 600, 5) == "no segment times"


def test_skribbys_last_segment_end_is_read_from_its_answer_and_segments_keep_their_shape():
    from app.skribby import _last_segment_end, _segments

    body = {
        "transcript": [
            {"speaker_name": "Ada", "transcript": "hello there", "start": 7.53, "end": 19.11},
            {"speaker_name": "Bo", "transcript": "hi", "end": "x"},
            {"speaker_name": "Bo", "transcript": "late", "end": 2},
        ]
    }
    assert _last_segment_end(body) == 19.11
    assert _last_segment_end({"transcript": [{"transcript": "x"}]}) is None
    assert _last_segment_end({"transcript": None}) is None
    assert _segments(body) == [  # still speaker and text only
        {"speaker": "Ada", "text": "hello there"},
        {"speaker": "Bo", "text": "hi"},
        {"speaker": "Bo", "text": "late"},
    ]


def test_skribby_mode_refuses_to_start_without_a_diarizing_model():
    with pytest.raises(ValueError) as error:
        Settings(transcript_source="skribby")  # the default model is Groq's
    message = str(error.value)
    assert "TRANSCRIPT_SOURCE is 'skribby' but SKRIBBY_TRANSCRIPTION_MODEL is" in message
    assert "groq/whisper-large-v3-turbo" in message and "elevenlabs/scribe-v2" in message
    assert "soniox" not in message  # v5 is deprecated and no longer allowed
    with pytest.raises(ValueError, match="SKRIBBY_TRANSCRIPTION_MODEL"):
        Settings(transcript_source="skribby", skribby_transcription_model="none")
    with pytest.raises(ValueError, match="SKRIBBY_TRANSCRIPTION_MODEL"):
        Settings(transcript_source="skribby", skribby_transcription_model="soniox/stt-async-v5")
    ok = Settings(transcript_source="skribby", skribby_transcription_model=MODEL)
    assert ok.transcript_source == "skribby"
    # the default source never looks at the model
    assert Settings(
        skribby_transcription_model="groq/whisper-large-v3-turbo"
    ).transcript_source == ("own")


# ---- the vocabulary field has never been tried live ----

BASE = "https://skribby.test/api/v1"
BOT = "0f9e7d6c-5b4a-4c3d-8e2f-1a0b9c8d7e6f"


def test_a_validation_error_on_the_vocabulary_retries_once_without_it(httpx_mock, caplog):
    client = SkribbyClient(api_key="k" * 12, base_url=BASE, transcription_model=VOCAB_MODEL)
    httpx_mock.add_response(method="POST", url=f"{BASE}/bot", status_code=422, json={"m": "bad"})
    httpx_mock.add_response(method="POST", url=f"{BASE}/bot", status_code=201, json={"id": BOT})
    payload = client.payload("google_meet", "abc-defg-hij", "https://meet.google.com/x", ["Zorbl"])
    with caplog.at_level(logging.INFO):
        assert client.create(payload) == BOT
    first, second = httpx_mock.get_requests()
    assert b"custom_vocabulary" in first.content and b"Zorbl" in first.content
    assert b"custom_vocabulary" not in second.content  # the same request, without the field
    assert "retrying once without the custom vocabulary" in caplog.text
    assert "Zorbl" not in caplog.text  # no terms in the log


def test_other_failures_and_a_create_without_vocabulary_are_not_retried(httpx_mock):
    client = SkribbyClient(api_key="k" * 12, base_url=BASE, transcription_model=VOCAB_MODEL)
    with_terms = client.payload("google_meet", "a", "https://meet.google.com/x", ["Zorbl"])
    httpx_mock.add_response(method="POST", url=f"{BASE}/bot", status_code=403, json={"m": "plan"})
    with pytest.raises(BotProviderError):
        client.create(with_terms)  # a plan limit is not about the vocabulary
    httpx_mock.add_response(method="POST", url=f"{BASE}/bot", status_code=400, json={"m": "bad"})
    with pytest.raises(BotProviderError):
        client.create(client.payload("google_meet", "a", "https://meet.google.com/x"))
    assert len(httpx_mock.get_requests()) == 2  # one try each, no second


def test_a_second_validation_error_is_an_error_not_a_loop(httpx_mock):
    client = SkribbyClient(api_key="k" * 12, base_url=BASE, transcription_model=VOCAB_MODEL)
    httpx_mock.add_response(method="POST", url=f"{BASE}/bot", status_code=400, json={"m": "bad"})
    httpx_mock.add_response(method="POST", url=f"{BASE}/bot", status_code=400, json={"m": "bad"})
    payload = client.payload("google_meet", "a", "https://meet.google.com/x", ["Zorbl"])
    with pytest.raises(BotProviderError):
        client.create(payload)
    assert len(httpx_mock.get_requests()) == 2


def test_a_failed_vocabulary_read_cannot_abort_the_placement_transaction(
    session, owner, monkeypatch
):
    """A failing statement aborts a Postgres transaction unless it ran in a savepoint."""
    real = session.scalars

    def failing(statement, *args, **kwargs):
        session.execute(text("select * from table_that_is_not_there"))
        return real(statement, *args, **kwargs)

    monkeypatch.setattr(session, "scalars", failing)
    on = skribby_settings(VOCAB_MODEL)
    assert transcript_source.send_options(session, TakesVocabulary(), owner.id, on) == {}
    monkeypatch.undo()
    assert session.scalar(select(1)) == 1  # the transaction is still usable


# ---- what reads `meeting.transcript` ----


def test_every_reader_gets_the_same_labelled_text_in_either_mode(session, skribby_meeting, mode):
    """In both modes `speaker_transcript` (what every reader prefers) holds the `[Name]:` turns;
    only `transcript` itself differs (plain in `own`, the same labelled text in `skribby`)."""
    mode()
    run(session, skribby_meeting, FakeVexa(done(long_segments())))
    assert skribby_meeting.speaker_transcript == skribby_meeting.transcript
    assert skribby_meeting.speaker_transcript.startswith("[Ada]: ")
    assert translation.speaker_names(skribby_meeting.speaker_transcript) == ["Ada", "Bo"]


# ---- the report ----


def test_the_report_with_nothing_says_so(session):
    text = transcript_shadow_report.report(session)
    assert "Meetings with a comparison: 0" in text and "Nothing to compare yet" in text


def test_the_report_prints_numbers_and_the_worst_five_by_id(session, skribby_meeting):
    other = []
    for index, share in enumerate((0.9, 0.5, 0.95, 0.2, 0.8, 0.7, 0.99)):
        m = Meeting(
            owner_user_id=skribby_meeting.owner_user_id,
            url=f"https://meet.google.com/aaa-bbbb-cc{index}",
            platform="google_meet",
            native_id=f"aaa-bbbb-cc{index}",
            template_id=skribby_meeting.template_id,
            transcript_source="skribby",
            started_polling_at=datetime(2026, 11, 9, tzinfo=UTC),
        )
        session.add(m)
        session.flush()
        other.append(m.id)
        session.add(
            TranscriptShadowMetric(
                meeting_id=m.id, skribby_words=900, own_words=1000, shared_words=int(share * 1000),
                shared_share=share, skribby_speakers=2, unnamed_segments=1, segments=10,
                audio_seconds=3600, notes_source="skribby" if index else "own",
            )
        )  # fmt: skip
    session.commit()
    text = transcript_shadow_report.report(session)
    assert "Meetings with a comparison: 7" in text
    assert "notes made from Skribby's: 6; fell back to ours" in text and ": 1\n" in text
    assert "audio compared: 7.0 hours" in text
    assert "median 0.80" in text  # words in common
    worst = [line for line in text.splitlines() if line.startswith("  meeting ")]
    assert [line.split()[1].rstrip(":") for line in worst] == [
        str(other[3]), str(other[1]), str(other[5]), str(other[4]), str(other[0])
    ]  # fmt: skip
    assert "@" not in text
