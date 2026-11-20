from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app import audio, pipeline, teams
from app.bot_provider import BotMeeting, BotProviderError
from app.config import Settings
from app.models import AiRun, AppState, Meeting, Template, VocabularyTerm
from app.notes import HEADINGS
from tests.test_web import owner  # noqa: F401  (fixture used by name)

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
RECORDED_SECONDS = 1830  # what the fake audio step reports for every recording
TRANSCRIPT = "Hello everyone thanks for joining. I can ship it on Friday. Great let us do that."
NOTES = "\n\n".join(f"## {h}\nNone noted" for h in HEADINGS)
DONE = BotMeeting(
    status="completed",
    segments=[{"speaker": "Ada", "text": "hello everyone thanks for joining"}],
    recording_id=7,
    media_id=9,
    recording_status="completed",
)


class FakeBot:
    def __init__(self, *polls):
        self.polls = list(polls)
        self.get_calls = 0
        self.downloads = 0

    def get_meeting(self, platform, native_id):
        self.code_lookups = getattr(self, "code_lookups", 0) + 1
        return self._next()

    def get_meeting_by_id(self, provider_bot_id):
        self.id_lookups = getattr(self, "id_lookups", []) + [provider_bot_id]
        return self._next()

    def _next(self):
        self.get_calls += 1
        item = self.polls[0] if len(self.polls) == 1 else self.polls.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    fail_download = False
    send_error: Exception | None = None
    next_bot_id = 100

    def send_bot(self, meeting_url):
        self.sent = getattr(self, "sent", []) + [meeting_url]
        if self.send_error:
            raise self.send_error
        self.next_bot_id += 1
        return self.next_bot_id

    stop_error: Exception | None = None

    def stop_bot(self, platform, native_id):
        self.stopped = getattr(self, "stopped", []) + [(platform, native_id)]
        if self.stop_error:
            raise self.stop_error

    recording_deletes: list
    delete_error: Exception | None = None

    def delete_recording(self, recording_id):
        self.recording_deletes = getattr(self, "recording_deletes", []) + [recording_id]
        if self.delete_error:
            raise self.delete_error

    def download_audio(self, recording_id, media_id, path):
        self.downloads += 1
        if self.fail_download:
            Path(path).write_bytes(b"trunc")
            raise BotProviderError("connection dropped")
        Path(path).write_bytes(b"audio")


class FakeOpenAI:
    def __init__(self, transcript=TRANSCRIPT, notes=NOTES, fail_notes=None):
        self.transcript = transcript
        self.notes = notes
        self.fail_notes = fail_notes
        self.transcriptions = 0
        self.chats = 0
        self.audio = SimpleNamespace(transcriptions=SimpleNamespace(create=self._transcribe))
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._chat))

    def _transcribe(self, **kwargs):
        self.transcriptions += 1
        return SimpleNamespace(text=self.transcript)

    def _chat(self, **kwargs):
        self.chats += 1
        system = kwargs["messages"][0]["content"]
        if system.startswith("You add speaker labels"):
            text = "[Ada]: " + self.transcript
        else:
            if self.fail_notes:
                raise self.fail_notes
            text = self.notes
        message = SimpleNamespace(content=text)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=40),
        )


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(pipeline, "get_settings", lambda: Settings(audio_dir=str(tmp_path / "a")))
    pipeline._no_audio_polls.clear()
    pipeline._needs_help_since.clear()
    pipeline._stop_tries.clear()
    pipeline._gone_checks.clear()
    pipeline._lookup_misses.clear()
    pipeline._speaker_misses.clear()

    def fake_prepare(source, out_dir):
        return audio.Recording(_good_chunks(out_dir), RECORDED_SECONDS)

    monkeypatch.setattr(audio, "prepare_recording", fake_prepare)
    return tmp_path


@pytest.fixture
def meeting(session, owner):
    template = Template(owner_user_id=owner.id, name="General", instructions="Be brief.")
    session.add(template)
    session.flush()
    m = Meeting(
        owner_user_id=owner.id,
        url="https://meet.google.com/abc-defg-hij",
        platform="google_meet",
        native_id="abc-defg-hij",
        template_id=template.id,
        started_polling_at=NOW - timedelta(minutes=5),
    )
    session.add(m)
    session.commit()
    return m


def run(session, meeting, provider, openai=None, now=NOW):
    openai = openai or FakeOpenAI()
    pipeline.advance_meeting(session, meeting.id, provider=provider, openai_client=openai, now=now)
    session.refresh(meeting)
    return openai


def test_completed_meeting_reaches_ready(session, meeting, env):
    session.add(VocabularyTerm(owner_user_id=meeting.owner_user_id, term="Beyric"))
    session.commit()
    run(session, meeting, FakeBot(DONE))
    assert (meeting.status, meeting.step) == ("ready", "done")
    assert meeting.transcript == TRANSCRIPT
    assert meeting.speaker_transcript.startswith("[Ada]:")
    assert meeting.notes == NOTES + "\n"
    assert meeting.failure_reason is None
    assert (env / "a" / str(meeting.id) / "source.webm").exists()


def test_the_teams_share_default_is_read_when_the_notes_are_written(session, meeting, env, owner):
    team = teams.ensure_team(session, owner)
    team.share_new_meetings = True
    session.commit()
    run(session, meeting, FakeBot(DONE))
    assert meeting.shared_team_id == team.id


def test_the_share_default_is_not_read_when_the_bot_joins(session, meeting, env, owner):
    """It was on when the meeting was made and off when the notes were written: private."""
    team = teams.ensure_team(session, owner)
    team.share_new_meetings = True
    session.commit()  # the meeting exists now: the bot has joined
    team.share_new_meetings = False
    session.commit()
    run(session, meeting, FakeBot(DONE))
    assert meeting.shared_team_id is None


def test_the_labelling_and_notes_calls_are_in_the_cost_ledger(session, meeting, env):
    run(session, meeting, FakeBot(DONE))
    rows = session.execute(
        select(AiRun.kind, AiRun.model, AiRun.input_tokens, AiRun.output_tokens, AiRun.user_id)
        .where(AiRun.meeting_id == meeting.id, AiRun.kind != "transcribe")
        .order_by(AiRun.id)
    ).all()
    assert [r[0] for r in rows] == ["label", "notes"]
    assert all(r[1] == "gpt-6-luna" and r[4] == meeting.owner_user_id for r in rows)
    assert rows[1][2:4] == (100, 40)
    transcription = session.scalars(
        select(AiRun).where(AiRun.meeting_id == meeting.id, AiRun.kind == "transcribe")
    ).one()
    assert transcription.audio_seconds == RECORDED_SECONDS
    assert transcription.user_id == meeting.owner_user_id


def test_a_ready_meeting_is_queued_for_extraction_in_the_same_commit(session, meeting, env):
    run(session, meeting, FakeBot(DONE))
    assert (meeting.extraction_status, meeting.extraction_attempts) == ("pending", 0)


def test_a_meeting_that_failed_never_reaches_extraction(session, meeting):
    run(session, meeting, FakeBot(BotMeeting(status="failed")))
    assert meeting.extraction_status is None


def test_failed_provider_meeting_sets_reason_and_makes_no_openai_call(session, meeting):
    openai = run(session, meeting, FakeBot(BotMeeting(status="failed")))
    assert meeting.status == "failed"
    assert meeting.failure_reason
    assert openai.transcriptions == 0 and openai.chats == 0


# ---- Zoom refusing the bot ----

ZOOM_RTMS = (
    "[Zoom Web] zoom_requires_rtms: meeting/account blocks automated browser joins and "
    "requires Zoom RTMS (Realtime Media Streams); route to the RTMS path"
)
ZOOM_REFUSED = BotMeeting(
    status="failed",
    completion_reason="awaiting_admission_rejected",
    failure_reason=ZOOM_RTMS,
)


@pytest.fixture
def zoom_meeting(session, meeting):
    meeting.url = "https://us05web.zoom.us/j/88205679674?pwd=abc"
    meeting.platform, meeting.native_id = "zoom", "88205679674"
    meeting.provider_bot_id = "1"
    session.commit()
    return meeting


def test_zoom_refusal_sends_a_new_bot(session, zoom_meeting):
    provider = FakeBot(ZOOM_REFUSED)
    openai = run(session, zoom_meeting, provider)
    assert (zoom_meeting.status, zoom_meeting.step) == ("bot_sent", "waiting")
    assert zoom_meeting.join_attempts == 2
    assert zoom_meeting.provider_bot_id == "101"
    assert provider.sent == [zoom_meeting.url]
    assert zoom_meeting.completion_reason is None and zoom_meeting.failure_reason is None
    assert openai.transcriptions == 0


def test_zoom_refusal_after_the_last_attempt_fails_with_the_reason(session, zoom_meeting):
    zoom_meeting.join_attempts = pipeline.MAX_JOIN_ATTEMPTS
    session.commit()
    provider = FakeBot(ZOOM_REFUSED)
    run(session, zoom_meeting, provider)
    assert zoom_meeting.status == "failed"
    assert zoom_meeting.failure_reason.startswith("Zoom blocked the bot from joining")
    assert "3 times in a row" in zoom_meeting.failure_reason
    assert not getattr(provider, "sent", [])


def test_zoom_refusal_when_a_new_bot_cannot_be_sent(session, zoom_meeting):
    provider = FakeBot(ZOOM_REFUSED)
    provider.send_error = BotProviderError("connection refused")
    run(session, zoom_meeting, provider)
    assert zoom_meeting.status == "failed"
    assert "A new bot could not be sent." in zoom_meeting.failure_reason
    assert zoom_meeting.join_attempts == 1


def test_three_refusals_then_admission_records_normally(session, zoom_meeting):
    provider = FakeBot(ZOOM_REFUSED)
    run(session, zoom_meeting, provider)
    run(session, zoom_meeting, provider)
    assert zoom_meeting.join_attempts == 3 and zoom_meeting.status == "bot_sent"
    provider.polls = [DONE]
    run(session, zoom_meeting, provider)
    assert (zoom_meeting.status, zoom_meeting.step) == ("ready", "done")
    assert provider.id_lookups[-1] == "102"  # the bot that got in, not the refused ones


def test_host_declining_is_not_retried(session, meeting):
    provider = FakeBot(BotMeeting(status="failed", completion_reason="awaiting_admission_rejected"))
    run(session, meeting, provider)
    assert meeting.status == "failed"
    assert meeting.failure_reason == pipeline.REASON_NOT_LET_IN
    assert not getattr(provider, "sent", [])


def test_unknown_bot_failure_shows_provider_reason(session, meeting):
    run(session, meeting, FakeBot(BotMeeting(status="failed", failure_reason="Chrome crashed")))
    assert meeting.failure_reason == (
        pipeline.REASON_BOT_FAILED + " The bot reported: Chrome crashed"
    )


def test_polls_and_fetches_segments_by_provider_id(session, meeting):
    """A reused link must not match an earlier meeting with the same code."""
    meeting.provider_bot_id = "4242"
    session.commit()
    provider = FakeBot(DONE)
    run(session, meeting, provider)
    assert meeting.status == "ready"
    assert provider.id_lookups and set(provider.id_lookups) == {"4242"}
    assert getattr(provider, "code_lookups", 0) == 0


def test_meeting_without_provider_id_falls_back_to_the_meeting_code(session, meeting):
    provider = FakeBot(DONE)
    run(session, meeting, provider)
    assert meeting.status == "ready"
    assert provider.code_lookups >= 1
    assert getattr(provider, "id_lookups", []) == []


def test_completed_without_recording_fails_with_admission_reason(session, meeting):
    provider = FakeBot(BotMeeting(status="completed"))
    for _ in range(2):
        run(session, meeting, provider)
        assert meeting.status == "bot_sent"
    run(session, meeting, provider)
    assert meeting.status == "failed"
    assert meeting.failure_reason == (
        "The bot could not record this meeting. It may not have been admitted."
    )


def test_completed_with_recording_still_finishing_keeps_waiting(session, meeting):
    pending = BotMeeting(
        status="completed", recording_id=7, media_id=9, recording_status="uploading"
    )
    for _ in range(5):
        run(session, meeting, FakeBot(pending))
    assert (meeting.status, meeting.step) == ("bot_sent", "waiting")


@pytest.mark.parametrize("status", ["requested", "joining", "awaiting_admission", "active", "odd"])
def test_in_progress_statuses_keep_waiting(session, meeting, status):
    run(session, meeting, FakeBot(BotMeeting(status=status)))
    assert (meeting.status, meeting.step) == ("bot_sent", "waiting")


def test_resume_after_restart_does_not_repeat_finished_steps(session, meeting):
    first = FakeOpenAI(notes="", fail_notes=RuntimeError("secret sk-123"))
    provider = FakeBot(DONE)
    run(session, meeting, provider, first)
    assert meeting.status == "failed" and meeting.step == "notes"
    assert first.transcriptions == 1
    # Restart: retry from the saved step with a working notes model.
    meeting.status = "processing"
    session.commit()
    second = FakeOpenAI()
    run(session, meeting, provider, second)
    assert meeting.status == "ready"
    assert second.transcriptions == 0
    assert provider.downloads == 1
    assert second.chats == 1  # notes only; speaker labels were saved


def test_provider_outage_keeps_bot_sent_status(session, meeting):
    run(session, meeting, FakeBot(BotProviderError("down")))
    assert (meeting.status, meeting.step) == ("bot_sent", "waiting")
    assert meeting.failure_reason is None


def test_gives_up_after_six_hours(session, meeting):
    meeting.started_polling_at = NOW - timedelta(hours=6, minutes=1)
    session.commit()
    run(session, meeting, FakeBot(BotMeeting(status="active")))
    assert meeting.status == "failed"
    assert "6 hours" in meeting.failure_reason
    run(session, meeting, FakeBot(BotProviderError("down")))  # already failed: untouched


def test_no_speech_sets_clear_reason(session, meeting, monkeypatch):
    def silent(source, out_dir):
        raise audio.NoSpeechError("silent")

    monkeypatch.setattr(audio, "prepare_recording", silent)
    openai = run(session, meeting, FakeBot(DONE))
    assert meeting.status == "failed"
    assert meeting.failure_reason == "No speech was found in this recording."
    assert openai.chats == 0


def test_tiny_transcript_counts_as_no_speech(session, meeting):
    openai = run(session, meeting, FakeBot(DONE), FakeOpenAI(transcript="Thank you."))
    assert meeting.failure_reason == "No speech was found in this recording."
    assert meeting.notes is None
    assert openai.chats == 0


def test_failure_reason_never_contains_exception_text(session, meeting, env):
    openai = FakeOpenAI(fail_notes=RuntimeError("sk-secret-key"))
    run(session, meeting, FakeBot(DONE), openai)
    assert meeting.status == "failed"
    assert "sk-secret" not in meeting.failure_reason
    assert meeting.audio_deleted_at is None
    assert (env / "a" / str(meeting.id) / "source.webm").is_file()


def test_audio_redownloaded_when_missing_on_resume(session, meeting, env):
    provider = FakeBot(DONE)
    run(session, meeting, provider, FakeOpenAI(fail_notes=RuntimeError("x")))
    (env / "a" / str(meeting.id) / "source.webm").unlink()
    meeting.status, meeting.step, meeting.transcript = "processing", "audio", None
    session.commit()
    run(session, meeting, provider)
    assert provider.downloads == 2 and meeting.status == "ready"


def test_interrupted_download_is_not_kept_and_is_retried(session, meeting, env):
    folder = env / "a" / str(meeting.id)
    provider = FakeBot(DONE)
    provider.fail_download = True
    run(session, meeting, provider)
    assert (meeting.status, meeting.step) == ("processing", "audio")
    assert not (folder / "source.webm").exists()
    assert not (folder / "source.webm.part").exists()
    provider.fail_download = False
    openai = run(session, meeting, provider)
    assert provider.downloads == 2
    assert meeting.status == "ready"
    assert openai.transcriptions == 1
    assert (folder / "source.webm").read_bytes() == b"audio"


def test_redownload_clears_audio_deleted_at(session, meeting):
    meeting.audio_deleted_at = NOW - timedelta(days=1)
    meeting.status, meeting.step = "processing", "audio"
    session.commit()
    run(session, meeting, FakeBot(DONE))
    assert meeting.status == "ready"
    assert meeting.audio_deleted_at is None


def test_provider_error_in_audio_step_keeps_step(session, meeting):
    run(session, meeting, FakeBot(DONE, BotProviderError("down")))
    assert (meeting.status, meeting.step) == ("processing", "audio")
    assert meeting.failure_reason is None
    run(session, meeting, FakeBot(DONE))
    assert meeting.status == "ready"


def test_provider_error_in_speakers_step_keeps_saved_transcript(session, meeting):
    first = run(session, meeting, FakeBot(DONE, DONE, BotProviderError("down")))
    assert (meeting.status, meeting.step) == ("processing", "speakers")
    assert meeting.transcript == TRANSCRIPT
    second = run(session, meeting, FakeBot(DONE))
    assert meeting.status == "ready"
    assert first.transcriptions == 1 and second.transcriptions == 0


def test_unknown_step_fails_instead_of_looping(session, meeting):
    meeting.status, meeting.step = "processing", "done"
    session.commit()
    run(session, meeting, FakeBot(DONE))
    assert meeting.status == "failed"
    assert meeting.failure_reason


def _db_error(*_a, **_k):
    raise OperationalError("select", {}, Exception("connection reset"))


def test_database_error_while_processing_leaves_status_alone(session, meeting, monkeypatch):
    meeting.status, meeting.step = "processing", "notes"
    session.commit()
    monkeypatch.setattr(pipeline, "_step_notes", _db_error)
    run(session, meeting, FakeBot(DONE))
    assert (meeting.status, meeting.step) == ("processing", "notes")
    assert meeting.failure_reason is None


def test_database_error_while_waiting_retries_then_gives_up(session, meeting, monkeypatch):
    monkeypatch.setattr(pipeline, "_poll", _db_error)
    run(session, meeting, FakeBot(DONE))
    assert (meeting.status, meeting.failure_reason) == ("bot_sent", None)
    meeting.started_polling_at = NOW - timedelta(hours=7)
    session.commit()
    run(session, meeting, FakeBot(DONE))
    assert meeting.status == "failed"


def test_audio_deleted_after_seven_days(session, meeting, env):
    meeting.provider_recording_id = None  # no the provider copy known
    folder = env / "a" / str(meeting.id)
    folder.mkdir(parents=True)
    (folder / "source.webm").write_bytes(b"a")
    meeting.status, meeting.transcript, meeting.notes = "ready", "t", "n"
    meeting.created_at = NOW - timedelta(days=8)
    young = Meeting(
        owner_user_id=meeting.owner_user_id,
        url="u",
        platform="zoom",
        native_id="1",
        template_id=meeting.template_id,
        status="ready",
    )
    young.created_at = NOW - timedelta(days=2)
    session.add(young)
    session.commit()
    (env / "a" / "999").mkdir()
    assert pipeline.delete_expired_audio(session, NOW) == 1
    session.refresh(meeting)
    assert not folder.exists()
    assert meeting.audio_deleted_at is not None
    assert meeting.transcript == "t" and meeting.notes == "n"
    assert pipeline.delete_expired_audio(session, NOW) == 0


def test_worker_pass_walks_meetings_without_starving(session, meeting, monkeypatch):
    from app import worker

    second = Meeting(
        owner_user_id=meeting.owner_user_id,
        url="u",
        platform="zoom",
        native_id="2",
        template_id=meeting.template_id,
    )
    session.add(second)
    session.commit()
    seen = []
    monkeypatch.setattr(worker, "advance_meeting", lambda s, i: seen.append(i))
    cursor = 0
    while (found := worker.run_pass(session, cursor)) is not None:
        cursor = found
    assert seen == [meeting.id, second.id]


def test_worker_pass_survives_unexpected_error(session, meeting, monkeypatch):
    from app import worker

    def boom(s, i):
        raise RuntimeError("sk-secret")

    monkeypatch.setattr(worker, "advance_meeting", boom)
    assert worker.run_pass(session, 0) == meeting.id
    assert session.get(Meeting, meeting.id) is not None  # session still usable after rollback


# ---- the provider copy of the audio ----


def _expired_ready_meeting(session, meeting, env, recording_id=7):
    folder = env / "a" / str(meeting.id)
    folder.mkdir(parents=True)
    (folder / "source.webm").write_bytes(b"a")
    meeting.status, meeting.provider_recording_id = "ready", recording_id
    meeting.created_at = NOW - timedelta(days=8)
    session.commit()
    return folder


def test_first_sight_of_the_recording_stores_its_id(session, meeting):
    pending = BotMeeting(
        status="active", recording_id=7, media_id=9, recording_status="in_progress"
    )
    run(session, meeting, FakeBot(pending))
    assert meeting.provider_recording_id == 7


def test_recording_ids_beyond_32_bits_are_stored(session, meeting):
    # Seen live on 2026-11-02: the provider recording 676401909012 failed every meeting at this
    # step.
    big = BotMeeting(
        status="active",
        recording_id=676401909012,
        media_id=729193055666,
        recording_status="in_progress",
    )
    run(session, meeting, FakeBot(big))
    session.expire_all()
    assert meeting.status == "bot_sent"
    assert meeting.provider_recording_id == 676401909012


def test_cleanup_deletes_both_copies_before_marking_audio_deleted(session, meeting, env):
    folder = _expired_ready_meeting(session, meeting, env)
    provider = FakeBot(DONE)
    assert pipeline.delete_expired_audio(session, NOW, provider=provider) == 1
    session.refresh(meeting)
    assert not folder.exists()
    assert provider.recording_deletes == [7]
    assert meeting.audio_deleted_at is not None


def test_provider_failure_keeps_audio_deleted_at_unset_and_retries_next_pass(session, meeting, env):
    folder = _expired_ready_meeting(session, meeting, env)
    provider = FakeBot(DONE)
    provider.delete_error = BotProviderError("down")
    assert pipeline.delete_expired_audio(session, NOW, provider=provider) == 0
    session.refresh(meeting)
    assert not folder.exists()  # the local copy is gone either way
    assert meeting.audio_deleted_at is None
    provider.delete_error = None
    assert pipeline.delete_expired_audio(session, NOW, provider=provider) == 1
    session.refresh(meeting)
    assert meeting.audio_deleted_at is not None
    assert provider.recording_deletes == [7, 7]


def test_meeting_without_a_known_recording_skips_provider(session, meeting, env):
    _expired_ready_meeting(session, meeting, env, recording_id=None)
    provider = FakeBot(DONE)
    assert pipeline.delete_expired_audio(session, NOW, provider=provider) == 1
    assert getattr(provider, "recording_deletes", []) == []


def test_local_failure_does_not_touch_provider(session, meeting, env, monkeypatch):
    _expired_ready_meeting(session, meeting, env)

    def boom(path):
        raise OSError("busy")

    monkeypatch.setattr(pipeline.shutil, "rmtree", boom)
    provider = FakeBot(DONE)
    assert pipeline.delete_expired_audio(session, NOW, provider=provider) == 0
    assert getattr(provider, "recording_deletes", []) == []


# ---- a recording stuck in progress ----

STUCK = BotMeeting(
    status="completed",
    recording_id=7,
    media_id=9,
    recording_status="in_progress",
    completion_reason="stopped",
)


def test_stuck_recording_is_downloaded_after_the_grace(session, meeting):
    provider = FakeBot(STUCK)
    run(session, meeting, provider)  # first sight of the finished meeting
    assert (meeting.status, meeting.step) == ("bot_sent", "waiting")
    assert meeting.terminal_seen_at == NOW
    assert provider.downloads == 0
    run(session, meeting, provider, now=NOW + timedelta(minutes=9))
    assert provider.downloads == 0
    run(session, meeting, provider, now=NOW + timedelta(minutes=10, seconds=1))
    assert provider.downloads == 1
    assert (meeting.status, meeting.step) == ("ready", "done")


def test_stuck_recording_with_failed_download_stays_for_retry(session, meeting):
    provider = FakeBot(STUCK)
    provider.fail_download = True
    run(session, meeting, provider)
    run(session, meeting, provider, now=NOW + timedelta(minutes=11))
    assert (meeting.status, meeting.step) == ("processing", "audio")
    assert meeting.failure_reason is None


def test_grace_does_not_apply_while_the_meeting_is_still_running(session, meeting):
    running = BotMeeting(
        status="active", recording_id=7, media_id=9, recording_status="in_progress"
    )
    provider = FakeBot(running)
    run(session, meeting, provider, now=NOW + timedelta(hours=1))
    assert provider.downloads == 0 and meeting.terminal_seen_at is None


# ---- audio problems ----


def test_corrupt_audio_is_deleted_so_retry_downloads_again(session, meeting, env, monkeypatch):
    def corrupt(source, out_dir):
        raise audio.AudioError("ffmpeg exited with 1")

    monkeypatch.setattr(audio, "prepare_recording", corrupt)
    provider = FakeBot(DONE)
    run(session, meeting, provider)
    assert meeting.status == "failed"
    assert meeting.failure_reason == "The recording could not be prepared for transcription."
    assert meeting.step == "audio"
    assert not (env / "a" / str(meeting.id) / "source.webm").exists()

    monkeypatch.setattr(
        audio, "prepare_recording", lambda s, o: audio.Recording(_good_chunks(o), RECORDED_SECONDS)
    )
    meeting.status = "processing"  # what the Try again button does
    session.commit()
    run(session, meeting, provider)
    assert provider.downloads == 2
    assert meeting.status == "ready"


def _good_chunks(out_dir):
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    chunk = Path(out_dir) / "chunk_000.webm"
    chunk.write_bytes(b"x")
    return [chunk]


# ---- the 6 hour limit belongs to the waiting phase ----


def test_provider_blip_after_six_hours_does_not_fail_a_meeting_with_a_transcript(session, meeting):
    meeting.status, meeting.step = "processing", "speakers"
    meeting.transcript = TRANSCRIPT
    meeting.started_polling_at = NOW - timedelta(hours=7)
    session.commit()
    run(session, meeting, FakeBot(BotProviderError("down")))
    # One miss is a blip, not the end: it waits, whatever the hour.
    assert (meeting.status, meeting.step) == ("processing", "speakers")
    assert meeting.failure_reason is None


def test_a_speaker_lookup_that_keeps_failing_for_long_enough_gives_up_with_no_names(
    session, meeting
):
    meeting.status, meeting.step = "processing", "speakers"
    meeting.transcript = TRANSCRIPT
    meeting.started_polling_at = NOW - timedelta(hours=7)
    session.commit()
    provider = FakeBot(BotProviderError("down"))
    # misses a few minutes apart: enough of them, but not over enough time
    for minutes in range(pipeline.SPEAKER_MISSES_TO_GIVE_UP + 2):
        run(session, meeting, provider, now=NOW + timedelta(minutes=minutes))
    assert (meeting.status, meeting.step) == ("processing", "speakers")
    # over enough time, with enough misses: the notes go ahead from the plain transcript
    run(
        session, meeting, provider, now=NOW + pipeline.SPEAKERS_GIVE_UP_AFTER + timedelta(minutes=1)
    )
    assert (meeting.status, meeting.step) == ("processing", "notes")
    assert meeting.failure_reason is None
    assert meeting.speaker_transcript == TRANSCRIPT and meeting.speakers_incomplete is True


def test_provider_blip_on_the_speaker_lookup_within_six_hours_just_waits(session, meeting):
    meeting.status, meeting.step = "processing", "speakers"
    meeting.transcript = TRANSCRIPT
    meeting.started_polling_at = NOW - timedelta(hours=1)
    session.commit()
    run(session, meeting, FakeBot(BotProviderError("down")))
    assert (meeting.status, meeting.step) == ("processing", "speakers")
    assert meeting.speaker_transcript is None


def test_a_provider_that_does_not_know_the_meeting_at_the_speaker_step_means_no_names(
    session, meeting
):
    from app.bot_provider import BotNotFound

    meeting.status, meeting.step = "processing", "speakers"
    meeting.transcript = TRANSCRIPT
    session.commit()
    run(session, meeting, FakeBot(BotNotFound("404")))
    assert (meeting.status, meeting.step) == ("ready", "done")
    assert meeting.speakers_incomplete is True and meeting.speaker_transcript == TRANSCRIPT


def test_download_that_never_works_gives_up_with_its_own_reason(session, meeting):
    meeting.status, meeting.step = "processing", "audio"
    meeting.started_polling_at = NOW - timedelta(hours=7)
    session.commit()
    run(session, meeting, FakeBot(BotProviderError("down")))
    assert meeting.status == "failed"
    assert meeting.failure_reason == "The recording could not be downloaded from the meeting bot."
    assert "6 hours" not in meeting.failure_reason


# ---- speaker names ----


def test_speaker_flag_is_false_when_everything_was_labelled(session, meeting):
    run(session, meeting, FakeBot(DONE))
    assert meeting.speakers_incomplete is False


def test_speaker_flag_is_true_when_there_are_no_segments(session, meeting):
    bare = BotMeeting(status="completed", recording_id=7, media_id=9, recording_status="completed")
    run(session, meeting, FakeBot(bare))
    assert meeting.status == "ready"
    assert meeting.speakers_incomplete is True


# ---- completion reason ----


def test_completion_reason_is_stored_and_does_not_fail_the_meeting(session, meeting):
    done = BotMeeting(
        status="completed",
        segments=DONE.segments,
        recording_id=7,
        media_id=9,
        recording_status="completed",
        completion_reason="evicted",
    )
    run(session, meeting, FakeBot(done))
    assert meeting.status == "ready"
    assert meeting.completion_reason == "evicted"


# ---- heartbeat ----


def test_heartbeat_is_recorded_on_the_single_state_row(session):
    from app import worker

    session.add(AppState(id=1))
    session.commit()
    worker.record_heartbeat(session, NOW)
    assert session.get(AppState, 1).worker_heartbeat_at == NOW
    later = NOW + timedelta(minutes=1)
    worker.record_heartbeat(session, later)
    assert session.get(AppState, 1).worker_heartbeat_at == later


# ---- a meeting whose owner was deleted is left alone ----


def delete_owner(session, meeting):
    from app.models import User

    session.get(User, meeting.owner_user_id).status = "deleted"
    session.commit()


def test_a_meeting_of_a_deleted_owner_is_not_advanced_at_all(session, meeting, env):
    delete_owner(session, meeting)
    provider, openai = FakeBot(DONE), FakeOpenAI()
    run(session, meeting, provider, openai)
    assert (meeting.status, meeting.step) == ("bot_sent", "waiting")
    assert provider.get_calls == 0 and provider.downloads == 0
    assert openai.transcriptions == 0 and openai.chats == 0
    assert not (env / "a" / str(meeting.id)).exists()
    from sqlalchemy import select

    from app.models import UsageEntry

    assert session.scalars(select(UsageEntry)).all() == []


def test_the_worker_does_not_even_pick_up_a_deleted_owners_meeting(session, meeting):
    from app import worker

    assert worker.next_meeting_id(session, 0) == meeting.id
    session.rollback()
    delete_owner(session, meeting)
    assert worker.next_meeting_id(session, 0) is None


def test_an_owner_deleted_between_two_steps_stops_the_run_before_openai(session, meeting, env):
    """The admin deletes the account mid-meeting: the next step must not run."""
    real = pipeline._step_audio

    def audio_then_delete(db, meeting_row, provider):
        proceed = real(db, meeting_row, provider)
        delete_owner(db, meeting_row)  # the admin's click lands right after the download
        return proceed

    pipeline_step = pytest.MonkeyPatch()
    pipeline_step.setattr(pipeline, "_step_audio", audio_then_delete)
    try:
        openai = run(session, meeting, FakeBot(DONE))
    finally:
        pipeline_step.undo()
    assert openai.transcriptions == 0 and openai.chats == 0
    assert meeting.step == "transcribing"  # left exactly where it was
    from sqlalchemy import select

    from app.models import UsageEntry

    assert session.scalars(select(UsageEntry)).all() == []


# ---- the notes-ready mail is queued with the notes ----


@pytest.fixture
def mail_on(monkeypatch, env):
    monkeypatch.setattr(
        pipeline, "get_settings", lambda: Settings(audio_dir=str(env / "a"), app_env="development")
    )


def _queued_mail(session):
    from app.models import MailOutbox

    return session.scalars(select(MailOutbox)).all()


def test_a_ready_meeting_queues_one_notes_ready_mail_with_ids_only(session, meeting, mail_on):
    run(session, meeting, FakeBot(DONE))
    [row] = _queued_mail(session)
    assert (row.template, row.to_email, row.payload) == (
        "notes_ready",
        "owner@example.test",
        {"meeting_id": meeting.id},
    )


def test_no_mail_is_queued_when_no_provider_is_configured(session, meeting, env):
    run(session, meeting, FakeBot(DONE))  # production settings, no key
    assert meeting.status == "ready" and _queued_mail(session) == []


def test_a_failed_meeting_queues_no_notes_mail(session, meeting, mail_on):
    run(session, meeting, FakeBot(BotMeeting(status="failed")))
    assert _queued_mail(session) == []


def test_a_failure_while_queueing_never_fails_the_meeting(session, meeting, mail_on, monkeypatch):
    from app import mail

    def boom(*args, **kwargs):
        raise RuntimeError("ada@example.test leaked")

    monkeypatch.setattr(mail, "queue_notes_ready", boom)
    run(session, meeting, FakeBot(DONE))
    assert (meeting.status, meeting.step) == ("ready", "done")
    assert meeting.notes and meeting.extraction_status == "pending"
    assert _queued_mail(session) == []
