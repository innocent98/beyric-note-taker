"""Move one meeting through the pipeline: wait for the bot, then audio, transcript, speakers, notes.

Every step is committed as soon as it finishes, so a restart resumes at `Meeting.step` and reuses
the saved `transcript` and `speaker_transcript`. A finished paid step is never run twice.

`failure_reason` is a plain sentence for the owner. Exception text is never copied into it (OpenAI
errors can carry key fragments or request details); only the exception type is logged.
"""

import logging
import os
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app import audio
from app.audio import AudioError, NoSpeechError
from app.config import get_settings
from app.models import Meeting, Template, VocabularyTerm
from app.notes import NotesFailed, generate_notes, label_speakers
from app.transcribe import TranscriptionFailed, transcribe_chunks
from app.vexa import VexaClient, VexaError, VexaMeeting

logger = logging.getLogger(__name__)

NO_AUDIO_GRACE_POLLS = 3
MIN_WORDS = 5
REASON_NOT_ADMITTED = "The bot could not record this meeting. It may not have been admitted."
REASON_NO_SPEECH = "No speech was found in this recording."
REASON_BOT_FAILED = "The meeting bot reported a failure and could not record this meeting."
REASON_RECORDING_FAILED = "The bot's recording failed, so there is nothing to transcribe."
REASON_TRANSCRIPTION = "The recording could not be transcribed. Please try again later."
REASON_AUDIO = "The recording could not be prepared for transcription."
REASON_NOTES = "The notes could not be written. The transcript was saved."
REASON_UNEXPECTED = "Something went wrong while processing this meeting."

# Consecutive polls that saw a finished meeting with no audio recording, per meeting. Lost on a
# restart, which only gives the recording a fresh grace period.
_no_audio_polls: dict[int, int] = {}


def audio_path(meeting_id: int) -> Path:
    return Path(get_settings().audio_dir) / str(meeting_id) / "source.webm"


def _lookup(vexa, meeting: Meeting) -> VexaMeeting:
    """Ask Vexa about this meeting by its Vexa id, so a reused link cannot pick up an earlier
    recording. Rows without an id (made before it was stored) fall back to the meeting code."""
    if meeting.vexa_meeting_id:
        return vexa.get_meeting_by_id(meeting.vexa_meeting_id)
    return vexa.get_meeting(meeting.platform, meeting.native_id)


def _lock(session: Session, meeting_id: int) -> Meeting | None:
    """Re-read the meeting under a row lock. None if another worker holds it."""
    return session.scalars(
        select(Meeting)
        .where(Meeting.id == meeting_id)
        .with_for_update(skip_locked=True)
        .execution_options(populate_existing=True)
    ).first()


def _fail(session: Session, meeting: Meeting, reason: str) -> None:
    meeting.status = "failed"
    meeting.failure_reason = reason
    _no_audio_polls.pop(meeting.id, None)
    session.commit()


def _expired(meeting: Meeting, now: datetime) -> bool:
    started = meeting.started_polling_at
    return started is not None and now - started > timedelta(hours=get_settings().max_poll_hours)


def _give_up(session: Session, meeting: Meeting) -> None:
    hours = get_settings().max_poll_hours
    _fail(session, meeting, f"The meeting did not finish being recorded within {hours} hours.")


def _poll(session: Session, meeting: Meeting, vexa, now: datetime) -> bool:
    """One look at Vexa while waiting. True once the recording is ready to download."""
    if meeting.started_polling_at is None:
        meeting.started_polling_at = now
    try:
        info: VexaMeeting | None = _lookup(vexa, meeting)
    except VexaError as exc:
        logger.warning("meeting %s: Vexa unreachable (%s)", meeting.id, type(exc).__name__)
        info = None

    if info is None:
        pass
    elif info.status == "failed":
        _fail(session, meeting, REASON_BOT_FAILED)
        return False
    elif info.status == "completed":
        if info.recording_status == "failed":
            _fail(session, meeting, REASON_RECORDING_FAILED)
            return False
        done = info.recording_status == "completed"
        if done and info.recording_id is not None and info.media_id is not None:
            _no_audio_polls.pop(meeting.id, None)
            return True
        if info.recording_status in (None, "completed"):
            # Finished, with no audio. Give the recording a few polls to appear.
            count = _no_audio_polls.get(meeting.id, 0) + 1
            _no_audio_polls[meeting.id] = count
            if count >= NO_AUDIO_GRACE_POLLS:
                _fail(session, meeting, REASON_NOT_ADMITTED)
                return False
        # Otherwise the recording is still being finished off: keep waiting.

    if _expired(meeting, now):
        _give_up(session, meeting)
    else:
        session.commit()
    return False


def _step_audio(session: Session, meeting: Meeting, vexa) -> bool:
    path = audio_path(meeting.id)
    if not path.is_file():
        info = _lookup(vexa, meeting)
        if info.recording_id is None or info.media_id is None:
            _fail(session, meeting, REASON_NOT_ADMITTED)
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        # Download beside the target and rename on success, so an interrupted download can never
        # be mistaken for the whole recording.
        partial = path.with_name(path.name + ".part")
        try:
            vexa.download_audio(info.recording_id, info.media_id, partial)
            os.replace(partial, path)
        finally:
            partial.unlink(missing_ok=True)
        meeting.audio_deleted_at = None  # audio is back, so cleanup must remove it again
    meeting.step = "transcribing"
    session.commit()
    return True


def _terms(session: Session) -> list[str]:
    return list(session.scalars(select(VocabularyTerm.term)))


def _step_transcribing(session: Session, meeting: Meeting, openai_client) -> bool:
    if meeting.transcript is None:
        folder = audio_path(meeting.id).parent
        chunks = audio.prepare_chunks(audio_path(meeting.id), folder / "chunks")  # rebuilt freely
        text = transcribe_chunks(chunks, _terms(session), client=openai_client)
        if len(text.split()) < MIN_WORDS:
            # The audio check only looks at peak volume, so noise can slip through.
            _fail(session, meeting, REASON_NO_SPEECH)
            return False
        meeting.transcript = text
    meeting.step = "speakers"
    session.commit()
    return True


def _step_speakers(session: Session, meeting: Meeting, vexa, openai_client) -> bool:
    if meeting.speaker_transcript is None:
        info = _lookup(vexa, meeting)
        meeting.speaker_transcript = label_speakers(
            meeting.transcript or "", info.segments, client=openai_client
        )
    meeting.step = "notes"
    session.commit()
    return True


def _step_notes(session: Session, meeting: Meeting, openai_client) -> bool:
    template = session.get(Template, meeting.template_id)
    text = meeting.speaker_transcript or meeting.transcript or ""
    meeting.notes = generate_notes(text, template, _terms(session), client=openai_client)
    meeting.status, meeting.step, meeting.failure_reason = "ready", "done", None
    session.commit()
    return True


def advance_meeting(
    session: Session,
    meeting_id: int,
    *,
    vexa=None,
    openai_client=None,
    now: datetime | None = None,
) -> None:
    """Do as much work as possible for one meeting, committing after each step.

    While waiting for the bot this makes a single Vexa poll and returns. Once the recording is
    ready it runs the remaining steps in turn. Anything that cannot continue leaves the meeting
    `failed` with a plain reason, or untouched when the problem is temporary.
    """
    now = now or datetime.now(UTC)
    vexa = vexa or VexaClient()
    meeting = _lock(session, meeting_id)
    try:
        while meeting is not None and meeting.status in ("bot_sent", "processing"):
            if meeting.step == "waiting":
                proceed = _poll(session, meeting, vexa, now)
                if proceed:
                    meeting.status, meeting.step = "processing", "audio"
                    session.commit()
            elif meeting.step == "audio":
                proceed = _step_audio(session, meeting, vexa)
            elif meeting.step == "transcribing":
                proceed = _step_transcribing(session, meeting, openai_client)
            elif meeting.step == "speakers":
                proceed = _step_speakers(session, meeting, vexa, openai_client)
            elif meeting.step == "notes":
                proceed = _step_notes(session, meeting, openai_client)
            else:
                _fail(session, meeting, REASON_UNEXPECTED)
                logger.error("meeting %s: unknown step %r", meeting_id, meeting.step)
                return
            if not proceed:
                return
            meeting = _lock(session, meeting_id)  # commit released the lock; take it back
    except VexaError as exc:
        # Temporary: stay at this step and try again on the next pass, until we give up.
        session.rollback()
        logger.warning("meeting %s: Vexa error (%s)", meeting_id, type(exc).__name__)
        meeting = session.get(Meeting, meeting_id)
        if meeting is not None and _expired(meeting, now):
            _give_up(session, meeting)
    except OperationalError as exc:
        # Transient database trouble: do not fail the meeting, try again on the next pass.
        logger.warning("meeting %s: database error (%s)", meeting_id, type(exc).__name__)
        try:
            session.rollback()
            meeting = session.get(Meeting, meeting_id)
            if meeting is not None and meeting.status == "bot_sent" and _expired(meeting, now):
                _give_up(session, meeting)
        except OperationalError as inner:
            logger.warning("meeting %s: database still down (%s)", meeting_id, type(inner).__name__)
    except (TranscriptionFailed, NotesFailed, AudioError) as exc:
        # Audio stays on disk so the meeting can be retried.
        _fail_with(session, meeting_id, exc)
    except Exception as exc:
        _fail_with(session, meeting_id, exc)


def _fail_with(session: Session, meeting_id: int, exc: Exception) -> None:
    session.rollback()
    logger.error("meeting %s failed: %s", meeting_id, type(exc).__name__)
    if isinstance(exc, NoSpeechError):
        reason = REASON_NO_SPEECH
    elif isinstance(exc, TranscriptionFailed):
        reason = REASON_TRANSCRIPTION
    elif isinstance(exc, NotesFailed):
        reason = REASON_NOTES
    elif isinstance(exc, AudioError):
        reason = REASON_AUDIO
    else:
        reason = REASON_UNEXPECTED
    meeting = session.get(Meeting, meeting_id)
    if meeting is not None:
        _fail(session, meeting, reason)


def delete_expired_audio(session: Session, now: datetime) -> int:
    """Delete audio folders of finished meetings older than the retention period.

    The transcript and notes are kept. `audio_deleted_at` is set only once the folder is gone.
    Returns how many meetings were cleared.
    """
    cutoff = now - timedelta(days=get_settings().audio_retention_days)
    expired = session.scalars(
        select(Meeting).where(
            Meeting.created_at < cutoff,
            Meeting.audio_deleted_at.is_(None),
            Meeting.status.in_(("ready", "failed")),
        )
    ).all()
    cleared = 0
    for meeting in expired:
        folder = audio_path(meeting.id).parent
        try:
            if folder.exists():
                shutil.rmtree(folder)
        except OSError as exc:
            logger.error("meeting %s: could not delete audio (%s)", meeting.id, type(exc).__name__)
            continue
        meeting.audio_deleted_at = now
        cleared += 1
    session.commit()
    return cleared
