"""Move one meeting through the pipeline: wait for the bot, then audio, transcript, speakers, notes.

Every step is committed as soon as it finishes, so a restart resumes at `Meeting.step` and reuses
the saved `transcript` and `speaker_transcript`. A finished paid step is never run twice.

`failure_reason` is a plain sentence for the owner. Exception text is never copied into it (OpenAI
errors can carry key fragments or request details); only the exception type is logged.
"""

import logging
import os
import random
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app import audio, usage
from app.audio import AudioError, NoSpeechError
from app.config import get_settings
from app.models import Meeting, Template, User, VocabularyTerm
from app.notes import NotesFailed, generate_notes, label_transcript
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
REASON_DOWNLOAD = "The recording could not be downloaded from the meeting bot."
REASON_CANCELLED = "You cancelled this meeting before it was recorded."
REASON_NOT_LET_IN = (
    "The bot was not let into the meeting. The host may have declined it, or the waiting room "
    "timed out."
)

REASON_JOIN_REFUSED = (
    "Google would not let the bot ask to join this meeting. This sometimes happens; try sending "
    "the bot again in a few minutes. If it keeps happening, the host's Meet settings may be "
    "blocking guests."
)

REASON_NEEDS_HELP = (
    "The bot is waiting on a prompt in the meeting, such as a consent or verification screen, "
    "and could not continue. Check the meeting for a prompt, then send the bot again."
)

# Google sometimes answers the bot's "Ask to join" with a refusal (HTTP 403, "You can't join this
# video call"). Vexa v0.12.29 reports it as a typed denial: status `failed`, completion reason
# `awaiting_admission_rejected` (a host declining gets the same) and `data.reason` starting with
# this code, after re-knocking up to 3 times itself. v0.12.28 has no typed reason; its refusal
# ends as the join-button error below.
MEET_REFUSED = "google_meet_join_refused"
# Vexa `needs_help`: the bot is held behind a human decision (the Gemini consent gate, a
# reCAPTCHA) and keeps polling; it can still turn `active`. It is not a refusal.
NEEDS_HELP_MARK = "needs_help"
# Stored in `Meeting.completion_reason` (with `terminal_seen_at` as the time) when the worker ended
# a join itself, so what follows reads the structured marker, never the wording of the reason.
JOIN_FAILURE_MARKS = (MEET_REFUSED, NEEDS_HELP_MARK)
# Bots sent for one meeting when Google refuses them, the first included. Vexa already knocks up to
# 3 times inside one bot, so more sends only look like hammering.
REFUSAL_MAX_SENDS = 2
# The pause before the second bot, counted from the refusal, plus a stable 0 to 60 s per meeting.
REFUSAL_RETRY_GAP = timedelta(minutes=4)
REFUSAL_JITTER_SECONDS = 60
# The shortest time between a failed join and a manual "Try again".
MANUAL_RETRY_GAP = timedelta(minutes=5)
# A bot in `needs_help` this long, with no segments and no recording, has its join failed. Long
# enough for a person to answer a consent prompt.
NEEDS_HELP_GRACE = timedelta(minutes=2)
# How often a bot that will not stop is asked again, and how often the second send checks that the
# first bot has gone before it gives up checking.
STOP_TRIES = 3
# A refusal whose page says this is the Meet marketing page the bot lands on after Google's 403.
JOIN_BUTTON_ERROR = "could not locate join button"
MEET_MARKETING_URL = "workspace.google.com/products/meet"

# Zoom sometimes refuses the bot's browser and lets the next one in (2 of 3 joins were refused
# on 2026-11-02, the third got in), so a refusal is retried up to this many bots in total.
MAX_JOIN_ATTEMPTS = 3
ZOOM_BLOCKED = "zoom_requires_rtms"

# A finished meeting whose recording is still "in progress" after this long is downloaded anyway:
# Vexa builds the audio file when it is read (`GET /recordings/{id}/media/{id}/raw` assembles the
# master from the uploaded chunks first), so the status can lag behind the file.
RECORDING_GRACE = timedelta(minutes=10)
# Vexa `completion_reason` values that mean the bot did not stay to the end. "evicted" is also
# reported on calls that ended normally, so it only ever produces a notice, never a failure.
REMOVAL_REASONS = frozenset({"evicted", "max_bot_time_exceeded"})

# Consecutive polls that saw a finished meeting with no audio recording, per meeting. Lost on a
# restart, which only gives the recording a fresh grace period.
_no_audio_polls: dict[int, int] = {}
# When each meeting's bot was first seen in `needs_help` (this run of it). Lost on a restart, which
# only gives the bot a fresh grace period.
_needs_help_since: dict[int, datetime] = {}
# Failed `stop_bot` calls per meeting, and checks that a refused bot had left before the second
# send. Lost on a restart, which only gives them a fresh count.
_stop_tries: dict[int, int] = {}
_gone_checks: dict[int, int] = {}


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


def _remember(meeting: Meeting, info: VexaMeeting) -> None:
    """Keep what Vexa told us that is needed later: its recording id and why the bot left."""
    if info.recording_id is not None:
        meeting.vexa_recording_id = info.recording_id
    if info.completion_reason is not None:
        meeting.completion_reason = info.completion_reason


def _zoom_blocked(info: VexaMeeting) -> bool:
    return info.failure_reason is not None and ZOOM_BLOCKED in info.failure_reason


def _zoom_blocked_reason(attempts: int, *, resend_failed: bool = False) -> str:
    tries = "once" if attempts == 1 else f"{attempts} times in a row"
    tail = (
        " A new bot could not be sent."
        if resend_failed
        else " Send the bot again in a minute, or ask the host whether the meeting blocks bots."
    )
    return f"Zoom blocked the bot from joining: it refused the bot's browser {tries}.{tail}"


def bot_failure_reason(meeting: Meeting, info: VexaMeeting) -> str:
    """Plain words for a failed bot, from what Vexa reported."""
    if _zoom_blocked(info):
        return _zoom_blocked_reason(meeting.join_attempts)
    if info.completion_reason == "awaiting_admission_rejected":
        return REASON_NOT_LET_IN
    if info.failure_reason:
        return f"{REASON_BOT_FAILED} The bot reported: {info.failure_reason}"
    return REASON_BOT_FAILED


def send_again(meeting: Meeting, vexa) -> bool:
    """Send a fresh bot to the same link and point the meeting at it. The caller commits.

    False, with the meeting untouched, when Vexa could not take the bot."""
    try:
        vexa_id = vexa.send_bot(meeting.url)
    except VexaError as exc:
        logger.warning("meeting %s: resending the bot failed (%s)", meeting.id, type(exc).__name__)
        return False
    meeting.join_attempts += 1
    meeting.status, meeting.step, meeting.failure_reason = "bot_sent", "waiting", None
    meeting.vexa_meeting_id = str(vexa_id)
    meeting.vexa_recording_id = None
    meeting.completion_reason = None
    meeting.terminal_seen_at = None
    _no_audio_polls.pop(meeting.id, None)
    _needs_help_since.pop(meeting.id, None)
    _stop_tries.pop(meeting.id, None)
    _gone_checks.pop(meeting.id, None)
    logger.info("meeting %s: sent a new bot (attempt %s)", meeting.id, meeting.join_attempts)
    return True


def _resend(session: Session, meeting: Meeting, vexa, *, failed_reason: str) -> None:
    """Send a fresh bot after a refusal, or fail the meeting with `failed_reason`."""
    if send_again(meeting, vexa):
        session.commit()
    else:
        _fail(session, meeting, failed_reason)


def refusal_retry_gap(meeting: Meeting) -> timedelta:
    """How long after a refusal the second bot waits. The jitter is fixed per meeting and send,
    so every poll agrees on when the pause ends, with nothing stored."""
    jitter = random.Random(f"{meeting.id}:{meeting.join_attempts}").uniform(
        0, REFUSAL_JITTER_SECONDS
    )
    return REFUSAL_RETRY_GAP + timedelta(seconds=jitter)


def is_join_failure(meeting: Meeting) -> bool:
    """A meeting whose join the worker ended (Google refused it, or it hung on a prompt), told
    by the marker stored with it and not by the wording of the reason."""
    return (
        meeting.status == "failed"
        and meeting.step == "waiting"
        and meeting.terminal_seen_at is not None
        and meeting.completion_reason in JOIN_FAILURE_MARKS
    )


def manual_retry_wait(meeting: Meeting, now: datetime) -> timedelta:
    """Time left before a failed join may be sent again by hand; zero when it may."""
    if meeting.terminal_seen_at is None:
        return timedelta(0)
    return max(timedelta(0), meeting.terminal_seen_at + MANUAL_RETRY_GAP - now)


def _paused_for_retry(meeting: Meeting) -> bool:
    """Google refused the first bot and the second has not been sent yet."""
    return (
        meeting.status == "bot_sent"
        and meeting.completion_reason == MEET_REFUSED
        and meeting.terminal_seen_at is not None
    )


def join_retry_at(meeting: Meeting) -> datetime | None:
    """When the second bot will be sent, while the meeting is paused for it; otherwise None."""
    if not _paused_for_retry(meeting):
        return None
    return meeting.terminal_seen_at + refusal_retry_gap(meeting)


def _join_problem(meeting: Meeting, info: VexaMeeting, now: datetime) -> str | None:
    """Why a Meet join should be ended (a marker from `JOIN_FAILURE_MARKS`), or None."""
    if meeting.platform != "google_meet":
        return None
    text = info.failure_reason or ""
    lowered = text.lower()
    if text.lstrip().startswith(MEET_REFUSED) or (
        JOIN_BUTTON_ERROR in lowered and MEET_MARKETING_URL in lowered
    ):
        return MEET_REFUSED
    # A bot that is already recording is Vexa asking for help for another reason: leave it be.
    if info.status == "needs_help" and not info.segments and info.recording_id is None:
        since = _needs_help_since.setdefault(meeting.id, now)
        return NEEDS_HELP_MARK if now - since >= NEEDS_HELP_GRACE else None
    _needs_help_since.pop(meeting.id, None)
    return None


def _stop_bot(meeting: Meeting, vexa) -> bool:
    """Ask Vexa to take the bot out. False when it could not, until it has been tried
    `STOP_TRIES` times: then it is given up on, so Vexa being down cannot hold the meeting."""
    try:
        vexa.stop_bot(meeting.platform, meeting.native_id)  # keyed by platform and meeting code
        return True
    except ValueError:
        return True  # a code Vexa would never accept; nothing to retry
    except VexaError as exc:
        tries = _stop_tries.get(meeting.id, 0) + 1
        _stop_tries[meeting.id] = tries
        logger.warning(
            "meeting %s: stop_bot failed (%s), try %s of %s",
            meeting.id,
            type(exc).__name__,
            tries,
            STOP_TRIES,
        )
        return tries >= STOP_TRIES


def _old_bot_gone(meeting: Meeting, vexa) -> bool:
    """Before the second bot is sent: has the refused one left? Not gone means stop it again and
    look again on the next poll, for `STOP_TRIES` checks; after that the send goes ahead and
    Vexa says no itself if the slot is still taken."""
    checks = _gone_checks.get(meeting.id, 0)
    if checks >= STOP_TRIES:
        return True
    _gone_checks[meeting.id] = checks + 1
    try:
        old = _lookup(vexa, meeting)
    except VexaError:
        return False
    if old.status in ("failed", "completed"):
        return True
    _stop_bot(meeting, vexa)
    return False


def _on_join_problem(session: Session, meeting: Meeting, vexa, mark: str, now: datetime) -> bool:
    """Stop the bot so it does not hold the slot, then fail the meeting, or pause for one polite
    retry after a refusal. True when the meeting was failed. When the bot could not be stopped
    yet, nothing changes and the next poll finds the same problem and tries again."""
    if not _stop_bot(meeting, vexa):
        return False
    _needs_help_since.pop(meeting.id, None)
    _stop_tries.pop(meeting.id, None)
    meeting.completion_reason, meeting.terminal_seen_at = mark, now
    if mark == MEET_REFUSED and meeting.join_attempts < REFUSAL_MAX_SENDS:
        logger.info("meeting %s: Google refused the bot; pausing before one retry", meeting.id)
        return False
    logger.info("meeting %s: join ended by the worker (%s)", meeting.id, mark)
    _fail(session, meeting, REASON_JOIN_REFUSED if mark == MEET_REFUSED else REASON_NEEDS_HELP)
    return True


def _poll(session: Session, meeting: Meeting, vexa, now: datetime) -> bool:
    """One look at Vexa while waiting. True once the recording is ready to download."""
    if meeting.started_polling_at is None:
        meeting.started_polling_at = now
    info: VexaMeeting | None = None
    if _paused_for_retry(meeting):
        # Looking the refused bot up would only report the refusal again, except to check it left.
        if now - meeting.terminal_seen_at >= refusal_retry_gap(meeting) and _old_bot_gone(
            meeting, vexa
        ):
            _resend(
                session,
                meeting,
                vexa,
                failed_reason=f"{REASON_JOIN_REFUSED} A new bot could not be sent.",
            )
            return False
    else:
        try:
            info = _lookup(vexa, meeting)
        except VexaError as exc:
            logger.warning("meeting %s: Vexa unreachable (%s)", meeting.id, type(exc).__name__)

    if info is not None:
        _remember(meeting, info)
        if info.status == "completed" and meeting.terminal_seen_at is None:
            meeting.terminal_seen_at = now

    if info is None:
        pass
    elif (mark := _join_problem(meeting, info, now)) is not None:
        if _on_join_problem(session, meeting, vexa, mark, now):
            return False
    elif info.status == "failed":
        if _zoom_blocked(info) and meeting.join_attempts < MAX_JOIN_ATTEMPTS:
            _resend(
                session,
                meeting,
                vexa,
                failed_reason=_zoom_blocked_reason(meeting.join_attempts, resend_failed=True),
            )
        else:
            _fail(session, meeting, bot_failure_reason(meeting, info))
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
        elif (
            info.recording_id is not None
            and info.media_id is not None
            and meeting.terminal_seen_at is not None
            and now - meeting.terminal_seen_at >= RECORDING_GRACE
        ):
            # Still "in progress" long after the meeting ended: try the download anyway.
            logger.warning(
                "meeting %s: recording still %s; downloading anyway",
                meeting.id,
                info.recording_status,
            )
            return True
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
        _remember(meeting, info)
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


def _terms(session: Session, owner_user_id: int) -> list[str]:
    """The meeting owner's vocabulary, never anyone else's."""
    return list(
        session.scalars(
            select(VocabularyTerm.term)
            .where(VocabularyTerm.owner_user_id == owner_user_id)
            .order_by(VocabularyTerm.id)
        )
    )


def _step_transcribing(session: Session, meeting: Meeting, openai_client) -> bool:
    if meeting.transcript is None:
        folder = audio_path(meeting.id).parent
        recording = audio.prepare_recording(
            audio_path(meeting.id), folder / "chunks"
        )  # rebuilt freely
        # Counted now, in the same commit as whatever this step ends with: a recording that turns
        # out to hold no speech was still transcribed, and a retry counts it only once.
        usage.record_usage(session, meeting, recording.seconds)
        # Committed before the OpenAI call: if it fails, the failure path rolls the session back,
        # and the cost already spent would otherwise leave the ledger. Retaking the row lock
        # keeps another worker off the meeting.
        session.commit()
        if _lock(session, meeting.id) is None:
            return False
        text = transcribe_chunks(
            recording.chunks, _terms(session, meeting.owner_user_id), client=openai_client
        )
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
        text, complete = label_transcript(
            meeting.transcript or "", info.segments, client=openai_client
        )
        meeting.speaker_transcript = text
        meeting.speakers_incomplete = not complete
    meeting.step = "notes"
    session.commit()
    return True


def _step_notes(session: Session, meeting: Meeting, openai_client) -> bool:
    template = session.get(Template, meeting.template_id)
    text = meeting.speaker_transcript or meeting.transcript or ""
    meeting.notes = generate_notes(
        text, template, _terms(session, meeting.owner_user_id), client=openai_client
    )
    meeting.status, meeting.step, meeting.failure_reason = "ready", "done", None
    session.commit()
    return True


def _owner_deleted(session: Session, meeting: Meeting) -> bool:
    """Whether the meeting's owner is being deleted. Such a meeting is left exactly as it is:
    nothing is downloaded, transcribed, written or counted for an account that is going away
    (`app/deletion.py` stops the bot and removes the rows and the recordings)."""
    status = session.scalar(select(User.status).where(User.id == meeting.owner_user_id))
    return status == "deleted"


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
            if _owner_deleted(session, meeting):
                session.rollback()  # looked at on every step, so a deletion mid-run stops it
                return
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
        # The 6 hour limit belongs to the waiting phase (see `_poll`). Once the recording is
        # ready, a Vexa blip never fails a meeting. The one exception is a download that has not
        # worked in 6 hours, which would otherwise keep the bot slot taken forever.
        if (
            meeting is not None
            and meeting.status == "processing"
            and meeting.step == "audio"
            and meeting.transcript is None
            and _expired(meeting, now)
        ):
            _fail(session, meeting, REASON_DOWNLOAD)
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
        # Audio stays on disk so the meeting can be retried, unless ffmpeg could not read it.
        _fail_with(session, meeting_id, exc)
    except Exception as exc:
        _fail_with(session, meeting_id, exc)


def _fail_with(session: Session, meeting_id: int, exc: Exception) -> None:
    session.rollback()
    # The database's error code (e.g. 22003, a number out of range) names the cause without
    # logging the statement, whose parameters can hold transcript text.
    code = getattr(getattr(exc, "orig", None), "sqlstate", None)
    detail = f" (database error {code})" if code else ""
    logger.error("meeting %s failed: %s%s", meeting_id, type(exc).__name__, detail)
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
        if isinstance(exc, AudioError):
            # A zero-byte or corrupt download would fail the same way on every retry. Remove it
            # and go back to the download step, so "Try again" fetches the recording afresh.
            shutil.rmtree(audio_path(meeting_id).parent, ignore_errors=True)
            meeting.step = "audio"
        _fail(session, meeting, reason)


def delete_expired_audio(session: Session, now: datetime, *, vexa=None) -> int:
    """Delete audio of finished meetings older than the retention period, here and in Vexa.

    The transcript and notes are kept. `audio_deleted_at` is set only once the local folder is
    gone and, when Vexa's recording id is known, Vexa's copy is gone too (`DELETE
    /recordings/{id}`; Vexa answers 404 for one already deleted). A Vexa failure leaves the
    meeting unmarked so the next pass tries again. Returns how many meetings were cleared.
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
        if meeting.vexa_recording_id is not None:
            try:
                vexa = vexa or VexaClient()
                vexa.delete_recording(meeting.vexa_recording_id)
            except VexaError as exc:
                logger.warning(
                    "meeting %s: could not delete Vexa's copy (%s); will retry",
                    meeting.id,
                    type(exc).__name__,
                )
                continue
        meeting.audio_deleted_at = now
        cleared += 1
    session.commit()
    return cleared
