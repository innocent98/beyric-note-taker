from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import OperationalError

from app import audio, pipeline
from app.config import Settings
from app.models import Meeting, Template, VocabularyTerm
from app.notes import HEADINGS
from app.vexa import VexaError, VexaMeeting

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
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
        self.get_calls += 1
        item = self.polls[0] if len(self.polls) == 1 else self.polls.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    fail_download = False

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

    def fake_prepare(source, out_dir):
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        chunk = Path(out_dir) / "chunk_000.webm"
        chunk.write_bytes(b"x")
        return [chunk]

    monkeypatch.setattr(audio, "prepare_chunks", fake_prepare)
    return tmp_path


@pytest.fixture
def meeting(session):
    template = Template(name="General", instructions="Be brief.")
    session.add(template)
    session.flush()
    m = Meeting(
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
    session.add(VocabularyTerm(term="Beyric"))
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

    monkeypatch.setattr(audio, "prepare_chunks", silent)
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
    folder = env / "a" / str(meeting.id)
    folder.mkdir(parents=True)
    (folder / "source.webm").write_bytes(b"a")
    meeting.status, meeting.transcript, meeting.notes = "ready", "t", "n"
    meeting.created_at = NOW - timedelta(days=8)
    young = Meeting(
        url="u", platform="zoom", native_id="1", template_id=meeting.template_id, status="ready"
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

    second = Meeting(url="u", platform="zoom", native_id="2", template_id=meeting.template_id)
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
