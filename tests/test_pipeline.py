from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import OperationalError

from app import audio, pipeline
from app.config import Settings
from app.models import AppState, Meeting, Template, VocabularyTerm
from app.notes import HEADINGS
from app.vexa import VexaError, VexaMeeting
from tests.test_web import owner  # noqa: F401  (fixture used by name)

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
RECORDED_SECONDS = 1830  # what the fake audio step reports for every recording
TRANSCRIPT = "Hello everyone thanks for joining. I can ship it on Friday. Great let us do that."
NOTES = "\n\n".join(f"## {h}\nNone noted" for h in HEADINGS)
DONE = VexaMeeting(
    status="completed",
    segments=[{"speaker": "Ada", "text": "hello everyone thanks for joining"}],
    recording_id=7,
    media_id=9,
    recording_status="completed",
)


class FakeVexa:
    def __init__(self, *polls):
        self.polls = list(polls)
        self.get_calls = 0
        self.downloads = 0

    def get_meeting(self, platform, native_id):
        self.code_lookups = getattr(self, "code_lookups", 0) + 1
        return self._next()

    def get_meeting_by_id(self, vexa_meeting_id):
        self.id_lookups = getattr(self, "id_lookups", []) + [vexa_meeting_id]
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
            raise VexaError("connection dropped")
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
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")])


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(pipeline, "get_settings", lambda: Settings(audio_dir=str(tmp_path / "a")))
    pipeline._no_audio_polls.clear()
    pipeline._needs_help_since.clear()
    pipeline._stop_tries.clear()
    pipeline._gone_checks.clear()

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


def run(session, meeting, vexa, openai=None, now=NOW):
    openai = openai or FakeOpenAI()
    pipeline.advance_meeting(session, meeting.id, vexa=vexa, openai_client=openai, now=now)
    session.refresh(meeting)
    return openai


def test_completed_meeting_reaches_ready(session, meeting, env):
    session.add(VocabularyTerm(owner_user_id=meeting.owner_user_id, term="Beyric"))
    session.commit()
    run(session, meeting, FakeVexa(DONE))
    assert (meeting.status, meeting.step) == ("ready", "done")
    assert meeting.transcript == TRANSCRIPT
    assert meeting.speaker_transcript.startswith("[Ada]:")
    assert meeting.notes == NOTES + "\n"
    assert meeting.failure_reason is None
    assert (env / "a" / str(meeting.id) / "source.webm").exists()


def test_failed_vexa_meeting_sets_reason_and_makes_no_openai_call(session, meeting):
    openai = run(session, meeting, FakeVexa(VexaMeeting(status="failed")))
    assert meeting.status == "failed"
    assert meeting.failure_reason
    assert openai.transcriptions == 0 and openai.chats == 0


# ---- Zoom refusing the bot ----

ZOOM_RTMS = (
    "[Zoom Web] zoom_requires_rtms: meeting/account blocks automated browser joins and "
    "requires Zoom RTMS (Realtime Media Streams); route to the RTMS path"
)
ZOOM_REFUSED = VexaMeeting(
    status="failed",
    completion_reason="awaiting_admission_rejected",
    failure_reason=ZOOM_RTMS,
)


@pytest.fixture
def zoom_meeting(session, meeting):
    meeting.url = "https://us05web.zoom.us/j/88205679674?pwd=abc"
    meeting.platform, meeting.native_id = "zoom", "88205679674"
    meeting.vexa_meeting_id = "1"
    session.commit()
    return meeting


def test_zoom_refusal_sends_a_new_bot(session, zoom_meeting):
    vexa = FakeVexa(ZOOM_REFUSED)
    openai = run(session, zoom_meeting, vexa)
    assert (zoom_meeting.status, zoom_meeting.step) == ("bot_sent", "waiting")
    assert zoom_meeting.join_attempts == 2
    assert zoom_meeting.vexa_meeting_id == "101"
    assert vexa.sent == [zoom_meeting.url]
    assert zoom_meeting.completion_reason is None and zoom_meeting.failure_reason is None
    assert openai.transcriptions == 0


def test_zoom_refusal_after_the_last_attempt_fails_with_the_reason(session, zoom_meeting):
    zoom_meeting.join_attempts = pipeline.MAX_JOIN_ATTEMPTS
    session.commit()
    vexa = FakeVexa(ZOOM_REFUSED)
    run(session, zoom_meeting, vexa)
    assert zoom_meeting.status == "failed"
    assert zoom_meeting.failure_reason.startswith("Zoom blocked the bot from joining")
    assert "3 times in a row" in zoom_meeting.failure_reason
    assert not getattr(vexa, "sent", [])


def test_zoom_refusal_when_a_new_bot_cannot_be_sent(session, zoom_meeting):
    vexa = FakeVexa(ZOOM_REFUSED)
    vexa.send_error = VexaError("connection refused")
    run(session, zoom_meeting, vexa)
    assert zoom_meeting.status == "failed"
    assert "A new bot could not be sent." in zoom_meeting.failure_reason
    assert zoom_meeting.join_attempts == 1


def test_three_refusals_then_admission_records_normally(session, zoom_meeting):
    vexa = FakeVexa(ZOOM_REFUSED)
    run(session, zoom_meeting, vexa)
    run(session, zoom_meeting, vexa)
    assert zoom_meeting.join_attempts == 3 and zoom_meeting.status == "bot_sent"
    vexa.polls = [DONE]
    run(session, zoom_meeting, vexa)
    assert (zoom_meeting.status, zoom_meeting.step) == ("ready", "done")
    assert vexa.id_lookups[-1] == "102"  # the bot that got in, not the refused ones


def test_host_declining_is_not_retried(session, meeting):
    vexa = FakeVexa(VexaMeeting(status="failed", completion_reason="awaiting_admission_rejected"))
    run(session, meeting, vexa)
    assert meeting.status == "failed"
    assert meeting.failure_reason == pipeline.REASON_NOT_LET_IN
    assert not getattr(vexa, "sent", [])


def test_unknown_bot_failure_shows_vexa_reason(session, meeting):
    run(session, meeting, FakeVexa(VexaMeeting(status="failed", failure_reason="Chrome crashed")))
    assert meeting.failure_reason == (
        pipeline.REASON_BOT_FAILED + " The bot reported: Chrome crashed"
    )


def test_polls_and_fetches_segments_by_vexa_id(session, meeting):
    """A reused link must not match an earlier meeting with the same code."""
    meeting.vexa_meeting_id = "4242"
    session.commit()
    vexa = FakeVexa(DONE)
    run(session, meeting, vexa)
    assert meeting.status == "ready"
    assert vexa.id_lookups and set(vexa.id_lookups) == {"4242"}
    assert getattr(vexa, "code_lookups", 0) == 0


def test_meeting_without_vexa_id_falls_back_to_the_meeting_code(session, meeting):
    vexa = FakeVexa(DONE)
    run(session, meeting, vexa)
    assert meeting.status == "ready"
    assert vexa.code_lookups >= 1
    assert getattr(vexa, "id_lookups", []) == []


def test_completed_without_recording_fails_with_admission_reason(session, meeting):
    vexa = FakeVexa(VexaMeeting(status="completed"))
    for _ in range(2):
        run(session, meeting, vexa)
        assert meeting.status == "bot_sent"
    run(session, meeting, vexa)
    assert meeting.status == "failed"
    assert meeting.failure_reason == (
        "The bot could not record this meeting. It may not have been admitted."
    )


def test_completed_with_recording_still_finishing_keeps_waiting(session, meeting):
    pending = VexaMeeting(
        status="completed", recording_id=7, media_id=9, recording_status="uploading"
    )
    for _ in range(5):
        run(session, meeting, FakeVexa(pending))
    assert (meeting.status, meeting.step) == ("bot_sent", "waiting")


@pytest.mark.parametrize("status", ["requested", "joining", "awaiting_admission", "active", "odd"])
def test_in_progress_statuses_keep_waiting(session, meeting, status):
    run(session, meeting, FakeVexa(VexaMeeting(status=status)))
    assert (meeting.status, meeting.step) == ("bot_sent", "waiting")


def test_resume_after_restart_does_not_repeat_finished_steps(session, meeting):
    first = FakeOpenAI(notes="", fail_notes=RuntimeError("secret sk-123"))
    vexa = FakeVexa(DONE)
    run(session, meeting, vexa, first)
    assert meeting.status == "failed" and meeting.step == "notes"
    assert first.transcriptions == 1
    # Restart: retry from the saved step with a working notes model.
    meeting.status = "processing"
    session.commit()
    second = FakeOpenAI()
    run(session, meeting, vexa, second)
    assert meeting.status == "ready"
    assert second.transcriptions == 0
    assert vexa.downloads == 1
    assert second.chats == 1  # notes only; speaker labels were saved


def test_vexa_outage_keeps_bot_sent_status(session, meeting):
    run(session, meeting, FakeVexa(VexaError("down")))
    assert (meeting.status, meeting.step) == ("bot_sent", "waiting")
    assert meeting.failure_reason is None


def test_gives_up_after_six_hours(session, meeting):
    meeting.started_polling_at = NOW - timedelta(hours=6, minutes=1)
    session.commit()
    run(session, meeting, FakeVexa(VexaMeeting(status="active")))
    assert meeting.status == "failed"
    assert "6 hours" in meeting.failure_reason
    run(session, meeting, FakeVexa(VexaError("down")))  # already failed: untouched


def test_no_speech_sets_clear_reason(session, meeting, monkeypatch):
    def silent(source, out_dir):
        raise audio.NoSpeechError("silent")

    monkeypatch.setattr(audio, "prepare_recording", silent)
    openai = run(session, meeting, FakeVexa(DONE))
    assert meeting.status == "failed"
    assert meeting.failure_reason == "No speech was found in this recording."
    assert openai.chats == 0


def test_tiny_transcript_counts_as_no_speech(session, meeting):
    openai = run(session, meeting, FakeVexa(DONE), FakeOpenAI(transcript="Thank you."))
    assert meeting.failure_reason == "No speech was found in this recording."
    assert meeting.notes is None
    assert openai.chats == 0


def test_failure_reason_never_contains_exception_text(session, meeting, env):
    openai = FakeOpenAI(fail_notes=RuntimeError("sk-secret-key"))
    run(session, meeting, FakeVexa(DONE), openai)
    assert meeting.status == "failed"
    assert "sk-secret" not in meeting.failure_reason
    assert meeting.audio_deleted_at is None
    assert (env / "a" / str(meeting.id) / "source.webm").is_file()


def test_audio_redownloaded_when_missing_on_resume(session, meeting, env):
    vexa = FakeVexa(DONE)
    run(session, meeting, vexa, FakeOpenAI(fail_notes=RuntimeError("x")))
    (env / "a" / str(meeting.id) / "source.webm").unlink()
    meeting.status, meeting.step, meeting.transcript = "processing", "audio", None
    session.commit()
    run(session, meeting, vexa)
    assert vexa.downloads == 2 and meeting.status == "ready"


def test_interrupted_download_is_not_kept_and_is_retried(session, meeting, env):
    folder = env / "a" / str(meeting.id)
    vexa = FakeVexa(DONE)
    vexa.fail_download = True
    run(session, meeting, vexa)
    assert (meeting.status, meeting.step) == ("processing", "audio")
    assert not (folder / "source.webm").exists()
    assert not (folder / "source.webm.part").exists()
    vexa.fail_download = False
    openai = run(session, meeting, vexa)
    assert vexa.downloads == 2
    assert meeting.status == "ready"
    assert openai.transcriptions == 1
    assert (folder / "source.webm").read_bytes() == b"audio"


def test_redownload_clears_audio_deleted_at(session, meeting):
    meeting.audio_deleted_at = NOW - timedelta(days=1)
    meeting.status, meeting.step = "processing", "audio"
    session.commit()
    run(session, meeting, FakeVexa(DONE))
    assert meeting.status == "ready"
    assert meeting.audio_deleted_at is None


def test_vexa_error_in_audio_step_keeps_step(session, meeting):
    run(session, meeting, FakeVexa(DONE, VexaError("down")))
    assert (meeting.status, meeting.step) == ("processing", "audio")
    assert meeting.failure_reason is None
    run(session, meeting, FakeVexa(DONE))
    assert meeting.status == "ready"


def test_vexa_error_in_speakers_step_keeps_saved_transcript(session, meeting):
    first = run(session, meeting, FakeVexa(DONE, DONE, VexaError("down")))
    assert (meeting.status, meeting.step) == ("processing", "speakers")
    assert meeting.transcript == TRANSCRIPT
    second = run(session, meeting, FakeVexa(DONE))
    assert meeting.status == "ready"
    assert first.transcriptions == 1 and second.transcriptions == 0


def test_unknown_step_fails_instead_of_looping(session, meeting):
    meeting.status, meeting.step = "processing", "done"
    session.commit()
    run(session, meeting, FakeVexa(DONE))
    assert meeting.status == "failed"
    assert meeting.failure_reason


def _db_error(*_a, **_k):
    raise OperationalError("select", {}, Exception("connection reset"))


def test_database_error_while_processing_leaves_status_alone(session, meeting, monkeypatch):
    meeting.status, meeting.step = "processing", "notes"
    session.commit()
    monkeypatch.setattr(pipeline, "_step_notes", _db_error)
    run(session, meeting, FakeVexa(DONE))
    assert (meeting.status, meeting.step) == ("processing", "notes")
    assert meeting.failure_reason is None


def test_database_error_while_waiting_retries_then_gives_up(session, meeting, monkeypatch):
    monkeypatch.setattr(pipeline, "_poll", _db_error)
    run(session, meeting, FakeVexa(DONE))
    assert (meeting.status, meeting.failure_reason) == ("bot_sent", None)
    meeting.started_polling_at = NOW - timedelta(hours=7)
    session.commit()
    run(session, meeting, FakeVexa(DONE))
    assert meeting.status == "failed"


def test_audio_deleted_after_seven_days(session, meeting, env):
    meeting.vexa_recording_id = None  # no Vexa copy known
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


# ---- Vexa copy of the audio ----


def _expired_ready_meeting(session, meeting, env, recording_id=7):
    folder = env / "a" / str(meeting.id)
    folder.mkdir(parents=True)
    (folder / "source.webm").write_bytes(b"a")
    meeting.status, meeting.vexa_recording_id = "ready", recording_id
    meeting.created_at = NOW - timedelta(days=8)
    session.commit()
    return folder


def test_first_sight_of_the_recording_stores_its_id(session, meeting):
    pending = VexaMeeting(
        status="active", recording_id=7, media_id=9, recording_status="in_progress"
    )
    run(session, meeting, FakeVexa(pending))
    assert meeting.vexa_recording_id == 7


def test_recording_ids_beyond_32_bits_are_stored(session, meeting):
    # Seen live on 2026-11-02: Vexa recording 676401909012 failed every meeting at this step.
    big = VexaMeeting(
        status="active",
        recording_id=676401909012,
        media_id=729193055666,
        recording_status="in_progress",
    )
    run(session, meeting, FakeVexa(big))
    session.expire_all()
    assert meeting.status == "bot_sent"
    assert meeting.vexa_recording_id == 676401909012


def test_cleanup_deletes_both_copies_before_marking_audio_deleted(session, meeting, env):
    folder = _expired_ready_meeting(session, meeting, env)
    vexa = FakeVexa(DONE)
    assert pipeline.delete_expired_audio(session, NOW, vexa=vexa) == 1
    session.refresh(meeting)
    assert not folder.exists()
    assert vexa.recording_deletes == [7]
    assert meeting.audio_deleted_at is not None


def test_vexa_failure_keeps_audio_deleted_at_unset_and_retries_next_pass(session, meeting, env):
    folder = _expired_ready_meeting(session, meeting, env)
    vexa = FakeVexa(DONE)
    vexa.delete_error = VexaError("down")
    assert pipeline.delete_expired_audio(session, NOW, vexa=vexa) == 0
    session.refresh(meeting)
    assert not folder.exists()  # the local copy is gone either way
    assert meeting.audio_deleted_at is None
    vexa.delete_error = None
    assert pipeline.delete_expired_audio(session, NOW, vexa=vexa) == 1
    session.refresh(meeting)
    assert meeting.audio_deleted_at is not None
    assert vexa.recording_deletes == [7, 7]


def test_meeting_without_a_known_recording_skips_vexa(session, meeting, env):
    _expired_ready_meeting(session, meeting, env, recording_id=None)
    vexa = FakeVexa(DONE)
    assert pipeline.delete_expired_audio(session, NOW, vexa=vexa) == 1
    assert getattr(vexa, "recording_deletes", []) == []


def test_local_failure_does_not_touch_vexa(session, meeting, env, monkeypatch):
    _expired_ready_meeting(session, meeting, env)

    def boom(path):
        raise OSError("busy")

    monkeypatch.setattr(pipeline.shutil, "rmtree", boom)
    vexa = FakeVexa(DONE)
    assert pipeline.delete_expired_audio(session, NOW, vexa=vexa) == 0
    assert getattr(vexa, "recording_deletes", []) == []


# ---- a recording stuck in progress ----

STUCK = VexaMeeting(
    status="completed",
    recording_id=7,
    media_id=9,
    recording_status="in_progress",
    completion_reason="stopped",
)


def test_stuck_recording_is_downloaded_after_the_grace(session, meeting):
    vexa = FakeVexa(STUCK)
    run(session, meeting, vexa)  # first sight of the finished meeting
    assert (meeting.status, meeting.step) == ("bot_sent", "waiting")
    assert meeting.terminal_seen_at == NOW
    assert vexa.downloads == 0
    run(session, meeting, vexa, now=NOW + timedelta(minutes=9))
    assert vexa.downloads == 0
    run(session, meeting, vexa, now=NOW + timedelta(minutes=10, seconds=1))
    assert vexa.downloads == 1
    assert (meeting.status, meeting.step) == ("ready", "done")


def test_stuck_recording_with_failed_download_stays_for_retry(session, meeting):
    vexa = FakeVexa(STUCK)
    vexa.fail_download = True
    run(session, meeting, vexa)
    run(session, meeting, vexa, now=NOW + timedelta(minutes=11))
    assert (meeting.status, meeting.step) == ("processing", "audio")
    assert meeting.failure_reason is None


def test_grace_does_not_apply_while_the_meeting_is_still_running(session, meeting):
    running = VexaMeeting(
        status="active", recording_id=7, media_id=9, recording_status="in_progress"
    )
    vexa = FakeVexa(running)
    run(session, meeting, vexa, now=NOW + timedelta(hours=1))
    assert vexa.downloads == 0 and meeting.terminal_seen_at is None


# ---- audio problems ----


def test_corrupt_audio_is_deleted_so_retry_downloads_again(session, meeting, env, monkeypatch):
    def corrupt(source, out_dir):
        raise audio.AudioError("ffmpeg exited with 1")

    monkeypatch.setattr(audio, "prepare_recording", corrupt)
    vexa = FakeVexa(DONE)
    run(session, meeting, vexa)
    assert meeting.status == "failed"
    assert meeting.failure_reason == "The recording could not be prepared for transcription."
    assert meeting.step == "audio"
    assert not (env / "a" / str(meeting.id) / "source.webm").exists()

    monkeypatch.setattr(
        audio, "prepare_recording", lambda s, o: audio.Recording(_good_chunks(o), RECORDED_SECONDS)
    )
    meeting.status = "processing"  # what the Try again button does
    session.commit()
    run(session, meeting, vexa)
    assert vexa.downloads == 2
    assert meeting.status == "ready"


def _good_chunks(out_dir):
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    chunk = Path(out_dir) / "chunk_000.webm"
    chunk.write_bytes(b"x")
    return [chunk]


# ---- the 6 hour limit belongs to the waiting phase ----


def test_vexa_blip_after_six_hours_does_not_fail_a_meeting_with_a_transcript(session, meeting):
    meeting.status, meeting.step = "processing", "speakers"
    meeting.transcript = TRANSCRIPT
    meeting.started_polling_at = NOW - timedelta(hours=7)
    session.commit()
    run(session, meeting, FakeVexa(VexaError("down")))
    assert (meeting.status, meeting.step) == ("processing", "speakers")
    assert meeting.failure_reason is None


def test_download_that_never_works_gives_up_with_its_own_reason(session, meeting):
    meeting.status, meeting.step = "processing", "audio"
    meeting.started_polling_at = NOW - timedelta(hours=7)
    session.commit()
    run(session, meeting, FakeVexa(VexaError("down")))
    assert meeting.status == "failed"
    assert meeting.failure_reason == "The recording could not be downloaded from the meeting bot."
    assert "6 hours" not in meeting.failure_reason


# ---- speaker names ----


def test_speaker_flag_is_false_when_everything_was_labelled(session, meeting):
    run(session, meeting, FakeVexa(DONE))
    assert meeting.speakers_incomplete is False


def test_speaker_flag_is_true_when_there_are_no_segments(session, meeting):
    bare = VexaMeeting(status="completed", recording_id=7, media_id=9, recording_status="completed")
    run(session, meeting, FakeVexa(bare))
    assert meeting.status == "ready"
    assert meeting.speakers_incomplete is True


# ---- completion reason ----


def test_completion_reason_is_stored_and_does_not_fail_the_meeting(session, meeting):
    done = VexaMeeting(
        status="completed",
        segments=DONE.segments,
        recording_id=7,
        media_id=9,
        recording_status="completed",
        completion_reason="evicted",
    )
    run(session, meeting, FakeVexa(done))
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
    vexa, openai = FakeVexa(DONE), FakeOpenAI()
    run(session, meeting, vexa, openai)
    assert (meeting.status, meeting.step) == ("bot_sent", "waiting")
    assert vexa.get_calls == 0 and vexa.downloads == 0
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

    def audio_then_delete(db, meeting_row, vexa):
        proceed = real(db, meeting_row, vexa)
        delete_owner(db, meeting_row)  # the admin's click lands right after the download
        return proceed

    pipeline_step = pytest.MonkeyPatch()
    pipeline_step.setattr(pipeline, "_step_audio", audio_then_delete)
    try:
        openai = run(session, meeting, FakeVexa(DONE))
    finally:
        pipeline_step.undo()
    assert openai.transcriptions == 0 and openai.chats == 0
    assert meeting.step == "transcribing"  # left exactly where it was
    from sqlalchemy import select

    from app.models import UsageEntry

    assert session.scalars(select(UsageEntry)).all() == []
