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
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, object_session

from app import (
    ai_runs,
    alerts,
    audio,
    mail,
    mail_templates,
    placement,
    series,
    teams,
    titles,
    transcript_source,
    usage,
    workspace,
)
from app.audio import AudioError, NoSpeechError
from app.bot_provider import (
    BotDuplicate,
    BotMeeting,
    BotNotFound,
    BotProviderError,
    BotRegionMismatch,
    BotSendUncertain,
    build_provider,
    provider_for,
    provider_name,
    provider_region,
)
from app.config import get_settings
from app.meeting_url import UnsupportedMeetingUrl
from app.models import (
    Meeting,
    SkribbySegmentEvidence,
    Template,
    TranscriptShadowMetric,
    User,
    VocabularyTerm,
)
from app.notes import NotesFailed, generate_notes, label_transcript
from app.transcribe import TranscriptionFailed, transcribe_chunks

logger = logging.getLogger(__name__)

NO_AUDIO_GRACE_POLLS = 3
# How long what the bot provider sent about who spoke is kept (`skribby_segment_evidence`).
EVIDENCE_RETENTION_DAYS = 7
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
REASON_ENDED_NOTHING_RECORDED = (
    "You ended this meeting before anything was recorded, so there are no notes to write."
)
REASON_NOT_LET_IN = (
    "The bot was not let into the meeting. The host may have declined it, or the waiting room "
    "timed out."
)

# The bell entry's kind for a bot that was sent and not let in. Its dedupe key adds the meeting id
# and `join_attempts` (1 for the first bot, +1 for every bot sent again, by the worker or by the
# owner's retry): a deliberate retry that is refused again is news, the same attempt is never told
# twice.
NOT_ADMITTED_KIND = "meeting.not_admitted"

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

# What the bot is doing while the call runs, as Vexa reports it (`status` in the lifecycle schema).
# The worker keeps it in `Meeting.completion_reason` while the meeting is `bot_sent`, with the time
# of the poll that saw it in `bot_state_seen_at`, so the owner's page can say it
# (`services.status_text`); no column of its own for the state. `BOT_RECORDING` is not a Vexa
# status: it is `active` once segments or a recording id prove something is being captured.
BOT_RECORDING = "recording"
BOT_LIVE_STATES = (
    "requested",
    "joining",
    "awaiting_admission",
    "active",
    "needs_help",
    "stopping",
    BOT_RECORDING,
)
# Stored in `completion_reason` by "End now and write the notes" (`services.end_meeting`) while the
# bot is leaving. Not a live state, so a poll never replaces it; Vexa's own completion reason
# replaces it once the bot has gone.
BOT_END_REQUESTED = "end_requested"
# A bot was sent and Vexa gave no clear answer: it may be joining, or not at all. The meeting is
# kept as a placeholder (`bot_sent`, no Vexa id) with this in `completion_reason`, read as
# "Starting the bot", until `_settle_unconfirmed` adopts a bot that shows up for the code or gives
# up.
SEND_UNCONFIRMED = placement.SEND_UNCONFIRMED
# The Vexa states in which a bot is taken as the one a placeholder was waiting for: not `stopping`,
# which is a leftover leaving (a cancelled meeting's bot), never one that is arriving.
ADOPTABLE_STATES = tuple(state for state in BOT_LIVE_STATES if state != "stopping")
# How long the worker waits for a bot to show up before it stops the code and fails the meeting.
UNCONFIRMED_GRACE = timedelta(seconds=60)
REASON_SEND_UNCONFIRMED = (
    "The bot could not be confirmed in the meeting, so this meeting was not recorded. "
    "Please send the bot again."
)
# The states in which the owner may end the bot with "End now and write the notes": in the call.
# At the door Vexa tears the bot down and ends it `failed`, so there would be nothing to write
# notes from (Cancel says that better); a bot still joining, one asking for help, and one already
# leaving are not offered it either.
BOT_ENDABLE_STATES = ("active", BOT_RECORDING)
# Consecutive failed lookups before the bot's state is given up on. One failed lookup is a blip.
LOOKUP_MISSES_BEFORE_UNKNOWN = 2

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

# The deletion of a hosted provider's copy (`delete_fetched_copy`): a failed try waits twice as long
# as the one before (5 minutes, 10, 20 ... up to 6 hours) and after this many failures the retry job
# leaves the row to the hourly retention pass.
COPY_RETRY_BASE = timedelta(minutes=5)
COPY_RETRY_MAX = timedelta(hours=6)
COPY_MAX_ATTEMPTS = 8
# A meeting that failed with no recording and no speaker names keeps its provider copy until the
# retention pass (the bot may still be processing a recording we have not fetched: a long call can
# end after our own give-up), except where the bot clearly never recorded: it was not let in, or
# Google refused it.
NEVER_RECORDED_MARKS = ("awaiting_admission_rejected", "awaiting_admission_timeout", MEET_REFUSED)

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
# Consecutive failed Vexa lookups per meeting. Lost on a restart, which only forgets a miss.
_lookup_misses: dict[int, int] = {}
# Failed speaker lookups per meeting: how many, and when the first was. The speakers step gives up
# (notes from the plain transcript) only after the 6 hours AND this many misses over this long,
# so one 5xx at the end of a long call never drops every speaker name. Lost on a restart, which
# only starts the count again.
SPEAKER_MISSES_TO_GIVE_UP = 5
SPEAKERS_GIVE_UP_AFTER = timedelta(minutes=30)
_speaker_misses: dict[int, tuple[int, datetime]] = {}


def audio_path(meeting_id: int) -> Path:
    return Path(get_settings().audio_dir) / str(meeting_id) / "source.webm"


def _lookup(vexa, meeting: Meeting) -> BotMeeting:
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


def _tell_not_admitted(session: Session, meeting: Meeting, at: datetime) -> None:
    """Tell the owner the bot was sent and NOT admitted (the waiting room timed out, the host
    declined it, or Google refused its ask to join): a bell entry, once per meeting, and the
    designed "Could not join" mail. `at` is the moment the failure was seen.

    Nothing here may stop the meeting failing, so each step is guarded and logged by exception
    type only (a message can hold an address). The mail's payload is built first, in a savepoint,
    because `mail.deliver_pending` fails a row whose build raises `ValueError` for good, with no
    retry: a payload that cannot be built falls back to the plain alert mail (the bell's own
    words) instead. The bell is written either way. The caller commits."""
    template, payload = mail_templates.NOT_JOINED, None
    try:
        with session.begin_nested():
            payload = mail_templates.not_joined_payload(
                session, meeting.owner_user_id, meeting.platform, meeting.native_id, at
            )
    except Exception as exc:
        logger.warning(
            "meeting %s: not-joined mail payload failed (%s)", meeting.id, type(exc).__name__
        )
        template = alerts.ALERT_TEMPLATE
    try:
        name = titles.alert_name(None, meeting.platform, meeting.native_id)
        alerts.notify_user(
            session,
            meeting.owner_user_id,
            kind=NOT_ADMITTED_KIND,
            severity="error",
            title=mail_templates.NOT_JOINED_TITLE,
            body=f"We tried to join {name} but were not admitted. "
            f"{mail_templates.NOT_JOINED_REASON}",
            dedupe_key=f"{NOT_ADMITTED_KIND}:{meeting.id}:{meeting.join_attempts}",
            meeting_id=meeting.id,
            email_template=template,
            email_payload=payload,
        )
    except Exception as exc:
        logger.warning("meeting %s: not-joined alert failed (%s)", meeting.id, type(exc).__name__)


def _fail(
    session: Session, meeting: Meeting, reason: str, *, not_admitted_at: datetime | None = None
) -> None:
    """Fail the meeting. `not_admitted_at` (the moment the failure was seen) is given only for the
    causes that mean "the bot was sent and not let in": the owner is then told in the same
    commit."""
    meeting.status = "failed"
    meeting.failure_reason = reason
    _no_audio_polls.pop(meeting.id, None)
    _lookup_misses.pop(meeting.id, None)
    _speaker_misses.pop(meeting.id, None)
    if not_admitted_at is not None:
        _tell_not_admitted(session, meeting, not_admitted_at)
    session.commit()


def _expired(meeting: Meeting, now: datetime) -> bool:
    started = meeting.started_polling_at
    return started is not None and now - started > timedelta(hours=get_settings().max_poll_hours)


def _give_up(session: Session, meeting: Meeting) -> None:
    hours = get_settings().max_poll_hours
    _fail(session, meeting, f"The meeting did not finish being recorded within {hours} hours.")


def _remember(meeting: Meeting, info: BotMeeting) -> None:
    """Keep what Vexa told us that is needed later: its recording id and why the bot left."""
    if info.recording_id is not None:
        meeting.vexa_recording_id = info.recording_id
    # The owner's End press stays on the row until the meeting is done: it is what tells "you
    # ended this" from a stop Vexa reports for any other reason, in every poll that follows.
    if info.completion_reason is not None and meeting.completion_reason != BOT_END_REQUESTED:
        meeting.completion_reason = info.completion_reason


def _live_state(info: BotMeeting) -> str | None:
    """The bot's state for the owner while the call runs, or None when Vexa says something else
    (over, failed)."""
    captured = bool(info.segments) or info.recording_id is not None
    if info.status == "active" and captured:
        return BOT_RECORDING
    if info.status == "needs_help" and captured:
        # Help is asked for, but the bot has already heard the call, so it was in: do not tell
        # the owner it is waiting on a prompt (`_join_problem` leaves such a bot be, too).
        return "active"
    if info.status == "needs_help" and info.reached_lobby:
        # Vexa's lobby-timeout warning: to the owner the bot is still waiting to be let in.
        return "awaiting_admission"
    return info.status if info.status in BOT_LIVE_STATES else None


def _record_live_state(meeting: Meeting, state: str | None, now: datetime) -> None:
    """Keep the bot's state in `completion_reason` and when it was seen in `bot_state_seen_at`,
    never over a marker the worker stored itself (the refusal marker, the end request) or over a
    reason Vexa gave for the bot leaving."""
    held = meeting.completion_reason
    if held is not None and held not in BOT_LIVE_STATES:
        return
    meeting.completion_reason = state
    meeting.bot_state_seen_at = now if state is not None else None


def _lookup_missed(meeting: Meeting, now: datetime) -> None:
    """A lookup failed: forget the bot's state only after `LOOKUP_MISSES_BEFORE_UNKNOWN` in a row.
    The state's time is not renewed meanwhile, so the page stops trusting it on its own."""
    misses = _lookup_misses.get(meeting.id, 0) + 1
    _lookup_misses[meeting.id] = misses
    if misses >= LOOKUP_MISSES_BEFORE_UNKNOWN:
        _record_live_state(meeting, None, now)


def _zoom_blocked(info: BotMeeting) -> bool:
    return info.failure_reason is not None and ZOOM_BLOCKED in info.failure_reason


def _zoom_blocked_reason(attempts: int, *, resend_failed: bool = False) -> str:
    tries = "once" if attempts == 1 else f"{attempts} times in a row"
    tail = (
        " A new bot could not be sent."
        if resend_failed
        else " Send the bot again in a minute, or ask the host whether the meeting blocks bots."
    )
    return f"Zoom blocked the bot from joining: it refused the bot's browser {tries}.{tail}"


def _nothing_recorded_reason(meeting: Meeting) -> str:
    """Why a finished meeting has no recording: the owner ended it first, or the bot was never
    let in."""
    if meeting.completion_reason == BOT_END_REQUESTED:
        return REASON_ENDED_NOTHING_RECORDED
    return REASON_NOT_ADMITTED


def bot_failure_reason(meeting: Meeting, info: BotMeeting) -> str:
    """Plain words for a failed bot, from what Vexa reported."""
    if meeting.completion_reason == BOT_END_REQUESTED:
        return REASON_ENDED_NOTHING_RECORDED  # the owner stopped the bot before it recorded
    if _zoom_blocked(info):
        return _zoom_blocked_reason(meeting.join_attempts)
    if info.completion_reason in ("awaiting_admission_rejected", "awaiting_admission_timeout"):
        return REASON_NOT_LET_IN
    if info.failure_reason:
        return f"{REASON_BOT_FAILED} The bot reported: {info.failure_reason}"
    return REASON_BOT_FAILED


def _delete_replaced_record(meeting: Meeting, default) -> None:
    """The bot this meeting is about to stop pointing at (refused, blocked, or ended) may leave a
    record at a hosted provider (the link, the name, its timeline). Delete it while its id is still
    on the row; best effort, since the new bot matters more and the id is lost either way."""
    old_id = meeting.vexa_meeting_id
    if not old_id or meeting.provider_copy_deleted_at is not None:
        return
    try:
        old = provider_for(default, meeting.bot_provider, meeting.bot_region)
        if hasattr(type(old), "delete_bot_record"):
            old.delete_bot_record(old_id)
    except Exception as exc:  # the resend must never fail for this
        logger.warning(
            "meeting %s: the replaced bot's record was not deleted (%s)",
            meeting.id,
            type(exc).__name__,
        )


def send_again(meeting: Meeting, vexa, *, raise_duplicate: bool = False) -> bool:
    """Send a fresh bot to the same link and point the meeting at it. The caller commits.

    False, with the meeting untouched, when Vexa could not take the bot. A send that got no clear
    answer (`BotSendUncertain`) leaves the meeting as an unconfirmed start for the worker to
    settle, and counts as sent. A Vexa duplicate refusal (a bot that is not ours is in that
    meeting) is raised when `raise_duplicate`, else it is a plain failure."""
    unconfirmed = False
    try:
        vexa_id = vexa.send_bot(
            meeting.url,
            **transcript_source.send_options(
                object_session(meeting), vexa, meeting.owner_user_id, get_settings()
            ),
        )
    except BotDuplicate:
        logger.warning("meeting %s: Vexa has another bot in that meeting", meeting.id)
        if raise_duplicate:
            raise
        return False
    except BotSendUncertain:
        logger.warning("meeting %s: resending the bot got no clear answer", meeting.id)
        vexa_id, unconfirmed = None, True
    except BotProviderError as exc:
        logger.warning("meeting %s: resending the bot failed (%s)", meeting.id, type(exc).__name__)
        return False
    except UnsupportedMeetingUrl:  # a link this provider cannot join (Teams under Vexa)
        logger.warning("meeting %s: the provider cannot join this link", meeting.id)
        return False
    _delete_replaced_record(meeting, vexa)  # before its id is lost to the new bot's
    meeting.join_attempts += 1
    meeting.status, meeting.step, meeting.failure_reason = "bot_sent", "waiting", None
    meeting.provider_copy_deleted_at = None
    meeting.provider_copy_attempts, meeting.provider_copy_next_try_at = 0, None
    meeting.bot_region = provider_region(vexa)
    meeting.vexa_meeting_id = None if vexa_id is None else str(vexa_id)
    meeting.bot_provider = provider_name(vexa)  # a hand retry goes to the provider in use now
    meeting.vexa_recording_id = None
    meeting.completion_reason = SEND_UNCONFIRMED if unconfirmed else None
    meeting.terminal_seen_at = None
    meeting.bot_state_seen_at = datetime.now(UTC) if unconfirmed else None
    if unconfirmed:
        meeting.started_polling_at = datetime.now(UTC)  # the grace counts from this send
    _no_audio_polls.pop(meeting.id, None)
    _needs_help_since.pop(meeting.id, None)
    _stop_tries.pop(meeting.id, None)
    _gone_checks.pop(meeting.id, None)
    _lookup_misses.pop(meeting.id, None)
    logger.info("meeting %s: sent a new bot (attempt %s)", meeting.id, meeting.join_attempts)
    return True


def _resend(
    session: Session,
    meeting: Meeting,
    vexa,
    *,
    failed_reason: str,
    not_admitted_at: datetime | None = None,
) -> None:
    """Send a fresh bot after a refusal, or fail the meeting with `failed_reason`. When the
    refusal that led here was Google's (`not_admitted_at`: the moment it was seen), the owner is
    told even though the new bot could not be sent: the first bot really was not admitted."""
    if send_again(meeting, vexa):
        session.commit()
    else:
        _fail(session, meeting, failed_reason, not_admitted_at=not_admitted_at)


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


def _join_problem(meeting: Meeting, info: BotMeeting, now: datetime) -> str | None:
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
    # So is one still in the waiting room: there `needs_help` is Vexa warning that its lobby
    # budget is nearly spent (`waiting_room_timeout_approaching`), and the host may admit it any
    # moment; Vexa ends the wait itself when the budget runs out (seen live on 2026-11-06).
    if (
        info.status == "needs_help"
        and not info.segments
        and info.recording_id is None
        and not info.reached_lobby
    ):
        since = _needs_help_since.setdefault(meeting.id, now)
        return NEEDS_HELP_MARK if now - since >= NEEDS_HELP_GRACE else None
    _needs_help_since.pop(meeting.id, None)
    return None


def _stop_bot(meeting: Meeting, vexa) -> bool:
    """Ask Vexa to take the bot out. False when it could not, until it has been tried
    `STOP_TRIES` times: then it is given up on, so Vexa being down cannot hold the meeting."""
    session = object_session(meeting)
    if session is not None and not placement.may_stop_bot(session, meeting):
        return True  # the bot at this code is a newer meeting's, not this one's: nothing to stop
    try:
        vexa.stop_bot(meeting.platform, meeting.native_id)  # keyed by platform and meeting code
        return True
    except ValueError:
        return True  # a code Vexa would never accept; nothing to retry
    except BotProviderError as exc:
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
    except BotProviderError:
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
    refused = mark == MEET_REFUSED
    _fail(
        session,
        meeting,
        REASON_JOIN_REFUSED if refused else REASON_NEEDS_HELP,
        not_admitted_at=now if refused else None,
    )
    return True


def _held_by_another_meeting(session: Session, meeting: Meeting, vexa_id: int) -> bool:
    """Whether a different meeting row already points at this Vexa bot: then it is that meeting's
    (for instance a cancelled one still stopping), never this placeholder's."""
    return (
        session.scalar(
            select(Meeting.id)
            .where(Meeting.vexa_meeting_id == str(vexa_id), Meeting.id != meeting.id)
            .limit(1)
        )
        is not None
    )


def _settle_unconfirmed(session: Session, meeting: Meeting, vexa, now: datetime) -> bool:
    """A meeting whose send got no clear answer. True once a bot for its code has shown up (the
    meeting then carries on as any other); False while it is still waiting or has been given up on.
    Only a bot that is arriving (not `stopping`), comes with its Vexa id, and whose id no other
    meeting holds counts: the code of a recurring meeting can match an older, finished Vexa
    meeting, or a cancelled one's leftover, and that is not ours. The code is the meeting's alone
    meanwhile (the placeholder is `bot_sent`, so no other placement can use it)."""
    try:
        info = vexa.get_meeting(meeting.platform, meeting.native_id)
    except BotProviderError:
        info = None
    if (
        info is not None
        and info.status in ADOPTABLE_STATES
        and info.vexa_id is not None
        and not _held_by_another_meeting(session, meeting, info.vexa_id)
    ):
        # Adopted, and pinned to Vexa's id from here on: a reused code can then never hand this
        # meeting someone else's (or an older) recording.
        meeting.vexa_meeting_id = str(info.vexa_id)
        meeting.completion_reason, meeting.bot_state_seen_at = None, None
        _lookup_misses.pop(meeting.id, None)
        logger.info("meeting %s: the bot of an unconfirmed send showed up", meeting.id)
        return True
    if now - meeting.started_polling_at >= UNCONFIRMED_GRACE:
        _stop_bot(meeting, vexa)  # in case it lands late; only ever this meeting's own bot
        meeting.completion_reason, meeting.bot_state_seen_at = None, None
        _fail(session, meeting, REASON_SEND_UNCONFIRMED)
        return False
    meeting.bot_state_seen_at = now  # keeps "Starting the bot" fresh for the page
    session.commit()
    return False


def _poll(session: Session, meeting: Meeting, vexa, now: datetime) -> bool:
    """One look at Vexa while waiting. True once the recording is ready to download."""
    if meeting.started_polling_at is None:
        meeting.started_polling_at = now
    if (
        meeting.completion_reason == SEND_UNCONFIRMED
        and meeting.vexa_meeting_id is None
        and not _settle_unconfirmed(session, meeting, vexa, now)
    ):
        return False
    info: BotMeeting | None = None
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
                not_admitted_at=meeting.terminal_seen_at,
            )
            return False
    else:
        try:
            info = _lookup(vexa, meeting)
        except BotProviderError as exc:
            logger.warning("meeting %s: Vexa unreachable (%s)", meeting.id, type(exc).__name__)
            _lookup_missed(meeting, now)

    if info is not None:
        _lookup_misses.pop(meeting.id, None)
        _remember(meeting, info)
        _record_live_state(meeting, _live_state(info), now)
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
            reason = bot_failure_reason(meeting, info)
            _fail(
                session,
                meeting,
                reason,
                not_admitted_at=now if reason == REASON_NOT_LET_IN else None,
            )
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
                _fail(session, meeting, _nothing_recorded_reason(meeting))
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
            _fail(session, meeting, _nothing_recorded_reason(meeting))
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


def _skribby_mode(meeting: Meeting) -> bool:
    """Whether this meeting's notes are to be made from Skribby's own transcript: the setting says
    so AND Skribby ran the meeting (a Vexa meeting always uses our transcription)."""
    return (
        get_settings().transcript_source == transcript_source.SKRIBBY
        and meeting.bot_provider == "skribby"
    )


def _skribby_segments(
    vexa, meeting: Meeting
) -> tuple[list[dict], float | None, dict | None] | None:
    """Skribby's segments for this meeting, where the last one ends (seconds) and the evidence of
    how they were named (`BotMeeting.evidence`, kept by `_keep_evidence` when they are used), or
    None when they could not be fetched (the provider is down, no longer has the meeting, or
    anything else). Never raises: a failed fetch is the fallback to our own transcription, never a
    failed meeting. Logged by error type only."""
    try:
        info = _lookup(vexa, meeting)
        return list(info.segments or []), info.last_segment_end, info.evidence
    except Exception as exc:
        logger.warning(
            "meeting %s: Skribby's transcript could not be fetched (%s)",
            meeting.id,
            type(exc).__name__,
        )
        return None


def _record_shadow(
    session: Session,
    meeting_id: int,
    found: transcript_source.Candidate | None,
    own_text: str,
    seconds: int,
    notes_source: str,
) -> None:
    """Keep the NUMBERS of a Skribby-versus-ours comparison (never either text) and drop both. In
    the caller's transaction."""
    overlap = transcript_source.overlap(found.spoken if found else "", own_text)
    session.add(
        TranscriptShadowMetric(
            meeting_id=meeting_id,
            skribby_words=overlap.skribby_words,
            own_words=overlap.own_words,
            shared_words=overlap.shared_words,
            shared_share=overlap.share,
            skribby_speakers=found.speakers if found else 0,
            unnamed_segments=found.unnamed_segments if found else 0,
            segments=found.segments if found else 0,
            audio_seconds=seconds,
            notes_source=notes_source,
        )
    )


@dataclass
class _ShadowJob:
    """A sampled comparison waiting for its meeting's notes to be committed."""

    owner_user_id: int
    found: transcript_source.Candidate
    recording: audio.Recording
    queued_at: datetime


# Held in this process's memory, keyed by meeting id: best effort by design. A restart forgets the
# queue (the check is then simply missing for those meetings), and an entry whose meeting never
# becomes ready is dropped after `SHADOW_MAX_AGE`.
_shadow_queue: dict[int, _ShadowJob] = {}
_shadow_lock = threading.Lock()
SHADOW_MAX_AGE = timedelta(hours=24)


def _queue_shadow(
    meeting_id: int, owner_user_id: int, found, recording: audio.Recording, now: datetime
) -> None:
    with _shadow_lock:
        _shadow_queue[meeting_id] = _ShadowJob(owner_user_id, found, recording, now)


def _shadow_ready(session: Session, now: datetime) -> list[tuple[int, _ShadowJob]]:
    """Take from the queue the jobs whose meeting now has its notes; drop those whose meeting is
    gone, failed, or has waited longer than `SHADOW_MAX_AGE`. The rest stay queued."""
    with _shadow_lock:
        waiting = list(_shadow_queue.items())
    ready = []
    for meeting_id, job in waiting:
        status = session.scalar(select(Meeting.status).where(Meeting.id == meeting_id))
        if status == "ready":
            ready.append((meeting_id, job))
        elif status in (None, "failed") or now - job.queued_at > SHADOW_MAX_AGE:
            with _shadow_lock:
                _shadow_queue.pop(meeting_id, None)
    session.rollback()  # the reads above hold no lock the model call should wait behind
    return ready


def _shadow_run(session: Session, meeting_id: int, job: _ShadowJob, openai_client) -> None:
    """The sampled check: transcribe the audio ourselves as `own` mode would, keep only the
    numbers, throw the text away. Runs only AFTER the meeting's notes are committed, so it can
    never delay or fail them. Best effort: any error is logged by type and the check is simply
    missing for this meeting.

    Its OpenAI cost is a `shadow` ledger row with no user (the owner did not cause it), committed
    on its own right after the call so that a failure later still leaves the spend recorded; if
    even that commit fails, the cost is logged."""
    seconds = job.recording.seconds
    try:
        text = transcribe_chunks(
            job.recording.chunks, _terms(session, job.owner_user_id), client=openai_client
        )
    except Exception as exc:
        session.rollback()
        logger.warning(
            "meeting %s: the shadow check was not made (%s)", meeting_id, type(exc).__name__
        )
        return
    try:
        ai_runs.record(
            session,
            meeting_id,
            "shadow",
            get_settings().transcription_model,
            audio_seconds=seconds,
        )
        session.commit()
    except Exception as exc:
        session.rollback()
        logger.error(
            "meeting %s: %d seconds of audio were transcribed for the shadow check and the "
            "ledger row could not be written (%s)",
            meeting_id,
            seconds,
            type(exc).__name__,
        )
    try:
        _record_shadow(session, meeting_id, job.found, text, seconds, transcript_source.SKRIBBY)
        session.commit()
    except Exception as exc:
        session.rollback()
        logger.warning(
            "meeting %s: the shadow numbers were not saved (%s)", meeting_id, type(exc).__name__
        )


def run_shadow_checks(session: Session, openai_client=None, now: datetime | None = None) -> int:
    """Run the queued shadow checks whose meetings are ready; how many ran. Called from a thread of
    its own in the worker (`app/worker.py`), never from the meeting pass."""
    now = now or datetime.now(UTC)
    ran = 0
    for meeting_id, job in _shadow_ready(session, now):
        with _shadow_lock:
            if _shadow_queue.pop(meeting_id, None) is None:
                continue  # another call took it
        _shadow_run(session, meeting_id, job, openai_client)
        ran += 1
    return ran


def _step_transcribing(session: Session, meeting: Meeting, vexa, openai_client) -> bool:
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
        meeting_id, owner_id = meeting.id, meeting.owner_user_id
        found, fetched = None, False
        if _skribby_mode(meeting):
            looked = _skribby_segments(vexa, meeting)
            fetched = looked is not None
            segments, last_end, evidence = looked or ([], None, None)
            found = transcript_source.candidate(segments, last_end)
            why_not = transcript_source.fallback_reason(found, recording.seconds, MIN_WORDS)
            if why_not is None:
                # Skribby's transcript is the transcript: our transcription and the model-based
                # speaker labelling are skipped (it already carries the names). Saved, with the
                # names, in this one commit, BEFORE the provider's copy is deleted (that is the
                # speakers step's, after this commit).
                meeting.transcript = found.text
                meeting.speaker_transcript = found.text
                meeting.speakers_incomplete = not found.complete
                meeting.transcript_source = transcript_source.SKRIBBY
                logger.info(
                    "meeting %s: transcript from skribby (%d words, %d speakers, %d unnamed)",
                    meeting_id,
                    found.words,
                    found.speakers,
                    found.unnamed_segments,
                )
                # The same evidence the speakers step keeps when it labels (that step fetches
                # nothing in this mode: the transcript already carries the names), from the data
                # fetched above plus the speaker events. A savepoint inside: it can fail without
                # touching the transcript saved in this commit, and never stops the meeting.
                _keep_evidence(
                    session,
                    meeting_id,
                    evidence,
                    _speaker_events(vexa, meeting) if evidence else None,
                )
                meeting.step = "speakers"
                session.commit()
                if transcript_source.shadow_selected(
                    meeting_id, get_settings().transcript_shadow_percent
                ):
                    # Queued, not run here: it starts only once the notes are committed.
                    _queue_shadow(meeting_id, owner_id, found, recording, datetime.now(UTC))
                return True
            logger.info(
                "meeting %s: Skribby's transcript not used (%s); transcribing ourselves",
                meeting_id,
                why_not,
            )
        text = transcribe_chunks(
            recording.chunks, _terms(session, meeting.owner_user_id), client=openai_client
        )
        # One row per transcription, written in the same commit as whatever this step ends with
        # (no speech included: it was still billed). `recording.seconds` is the whole recording,
        # which is what the chunks add up to. A failed transcription records nothing.
        ai_runs.record(
            session,
            meeting.id,
            "transcribe",
            get_settings().transcription_model,
            audio_seconds=recording.seconds,
            user_id=meeting.owner_user_id,
        )
        if (
            _skribby_mode(meeting)
            and fetched
            and transcript_source.shadow_selected(
                meeting.id, get_settings().transcript_shadow_percent
            )
        ):
            # Skribby's was not used, and ours is already made: the comparison costs nothing more.
            _record_shadow(
                session, meeting.id, found, text, recording.seconds, transcript_source.OWN
            )
        if len(text.split()) < MIN_WORDS:
            # The audio check only looks at peak volume, so noise can slip through.
            _fail(session, meeting, REASON_NO_SPEECH)
            return False
        meeting.transcript = text
        meeting.transcript_source = transcript_source.OWN
    meeting.step = "speakers"
    session.commit()
    return True


def _copy_backoff(attempts: int) -> timedelta:
    return min(COPY_RETRY_BASE * 2 ** max(attempts - 1, 0), COPY_RETRY_MAX)


def _copy_failed(meeting: Meeting, now: datetime, exc: Exception) -> None:
    """One failed try at deleting the provider's copy: count it and set when the next may be made,
    for every kind of failure (a refusal, a region that does not match, a provider that is not
    configured), so no row can be picked again at once and starve the queue."""
    meeting.provider_copy_attempts = (meeting.provider_copy_attempts or 0) + 1
    meeting.provider_copy_next_try_at = now + _copy_backoff(meeting.provider_copy_attempts)
    last = meeting.provider_copy_attempts >= COPY_MAX_ATTEMPTS
    (logger.error if last else logger.warning)(
        "meeting %s: could not delete the provider's copy (%s), try %s of %s%s",
        meeting.id,
        type(exc).__name__,
        meeting.provider_copy_attempts,
        COPY_MAX_ATTEMPTS,
        "; left to the retention pass" if last else "; will retry",
    )


def delete_fetched_copy(meeting: Meeting, provider, now: datetime | None = None) -> bool:
    """Delete a hosted provider's copy of the meeting (recording, transcript, the record of the bot)
    now that we hold the audio and the speaker names. True when it is gone or there is nothing to
    delete. Never raises for a failure: the notes must not wait for it. The caller commits.
    `provider_copy_deleted_at` is set here on success. A failure counts an attempt and sets
    `provider_copy_next_try_at` (a doubling wait) so `delete_fetched_copies` neither repeats it
    every loop nor lets stuck rows crowd out newer ones; after `COPY_MAX_ATTEMPTS` the row is left
    to the retention pass. Vexa has no `delete_bot_record`: its copy is on our own server and
    follows the retention rule (`delete_expired_audio`)."""
    if meeting.provider_copy_deleted_at is not None:
        return True
    if not meeting.vexa_meeting_id or not hasattr(type(provider), "delete_bot_record"):
        return True
    now = now or datetime.now(UTC)
    region = provider_region(provider)
    if meeting.bot_region and region and meeting.bot_region != region:
        _copy_failed(
            meeting,
            now,
            BotRegionMismatch(f"bot in region {meeting.bot_region}, provider set to {region}"),
        )
        return False
    try:
        provider.delete_bot_record(meeting.vexa_meeting_id)
    except Exception as exc:  # a provider error, or anything else: never in the notes' way
        _copy_failed(meeting, now, exc)
        return False
    meeting.provider_copy_deleted_at = now
    meeting.provider_copy_next_try_at = None
    return True


def delete_fetched_copies(session: Session, now: datetime, *, vexa=None, limit: int = 5) -> int:
    """Retry the deletion `delete_fetched_copy` could not do (the provider was down), and do it for
    a bot that never recorded. Rows chosen: a hosted provider's meeting whose copy is not yet
    deleted, that is ready or failed, whose next try is due and that has tries left, and that needs
    nothing more from the provider: its speaker names are saved, or no recording was made and the
    bot clearly never recorded (`NEVER_RECORDED_MARKS`). A meeting that failed with no recording
    for any other reason keeps its copy until the retention pass. Rows are taken with a row lock
    (skipping any being worked on, such as one a resend is pointing at a new bot), the ones never
    tried or due longest ago first, so failing rows go to the back. At most `limit` a pass;
    returns how many were deleted."""
    candidates = session.scalars(
        select(Meeting)
        .where(
            Meeting.bot_provider != "vexa",
            Meeting.provider_copy_deleted_at.is_(None),
            Meeting.vexa_meeting_id.is_not(None),
            Meeting.status.in_(("ready", "failed")),
            Meeting.provider_copy_attempts < COPY_MAX_ATTEMPTS,
            (Meeting.provider_copy_next_try_at.is_(None))
            | (Meeting.provider_copy_next_try_at <= now),
            (Meeting.speaker_transcript.is_not(None))
            | (
                Meeting.vexa_recording_id.is_(None)
                & Meeting.completion_reason.in_(NEVER_RECORDED_MARKS)
            ),
        )
        .order_by(Meeting.provider_copy_next_try_at.asc().nulls_first(), Meeting.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    ).all()
    deleted = 0
    for meeting in candidates:
        try:
            provider = provider_for(vexa, meeting.bot_provider, meeting.bot_region)
        except BotProviderError as exc:  # not configured here, or the bot is in another region
            _copy_failed(meeting, now, exc)
            continue
        if delete_fetched_copy(meeting, provider, now):
            deleted += 1
    session.commit()
    return deleted


def _delete_copy_after_commit(session: Session, meeting_id: int, provider) -> None:
    """The deletion of the step that saved the speaker names, in a transaction of its own AFTER the
    names were committed: a failed commit or a restart between the two can never lose the names to
    a delete that already happened. The row is locked again for it (a row another worker holds is
    left to the retry job)."""
    try:
        locked = _lock(session, meeting_id)
        if locked is None:
            return
        delete_fetched_copy(locked, provider)
        session.commit()
    except Exception as exc:
        session.rollback()
        logger.warning(
            "meeting %s: the provider copy step failed (%s)", meeting_id, type(exc).__name__
        )


def _speaker_events(provider, meeting: Meeting) -> dict | None:
    """Who was speaking when, per the meeting platform, for the evidence (Skribby only; None for a
    provider without it). Best effort and never in the way: any failure is None, logged by
    exception class only."""
    if not meeting.vexa_meeting_id or not hasattr(type(provider), "get_speaker_events"):
        return None
    try:
        return provider.get_speaker_events(meeting.vexa_meeting_id)
    except Exception as exc:
        logger.warning("meeting %s: no speaker events kept (%s)", meeting.id, type(exc).__name__)
        return None


def _keep_evidence(
    session: Session, meeting_id: int, evidence: dict | None, events: dict | None = None
) -> None:
    """Keep what the provider sent for this meeting for `EVIDENCE_RETENTION_DAYS` days, in the
    commit that saves the speaker names (just before the provider's copy is deleted), so a bad
    labelling can be diagnosed (`scripts/skribby_segments_report.py`). A record for diagnosis
    only: any failure rolls back just this write (a savepoint), is logged by exception class only
    (the text is what people said) and never stops the meeting."""
    if not evidence:
        return
    # The speaker events are an extra: if they cannot be written, the transcript evidence still is.
    for kept in (events, None) if events is not None else (None,):
        try:
            with session.begin_nested():
                session.execute(
                    delete(SkribbySegmentEvidence).where(
                        SkribbySegmentEvidence.meeting_id == meeting_id
                    )
                )
                session.add(
                    SkribbySegmentEvidence(
                        meeting_id=meeting_id,
                        items=evidence["items"],
                        tags=evidence["tags"],
                        by_elimination=bool(evidence["by_elimination"]),
                        events=kept,
                    )
                )
            return
        except Exception as exc:
            logger.warning(
                "meeting %s: could not keep the speaker evidence%s (%s)",
                meeting_id,
                " with its events" if kept is not None else "",
                type(exc).__name__,
            )


def _step_speakers(session: Session, meeting: Meeting, vexa, openai_client) -> bool:
    if meeting.speaker_transcript is None:
        try:
            info = _lookup(vexa, meeting)
            segments = info.segments
            _speaker_misses.pop(meeting.id, None)
            _keep_evidence(
                session,
                meeting.id,
                info.evidence,
                _speaker_events(vexa, meeting) if info.evidence else None,
            )
        except BotNotFound:
            # The provider no longer has the meeting (its copy is gone): there are no segments to
            # label with, which is "speakers incomplete", never a retry that cannot succeed.
            logger.warning("meeting %s: no speaker segments (the provider has none)", meeting.id)
            segments = []
        spent: list = []
        text, complete = label_transcript(
            meeting.transcript or "", segments, client=openai_client, usage=spent
        )
        ai_runs.record_usage(
            session,
            meeting.id,
            "label",
            get_settings().notes_model,
            spent,
            user_id=meeting.owner_user_id,
        )
        meeting.speaker_transcript = text
        meeting.speakers_incomplete = not complete
    meeting.step = "notes"
    session.commit()  # the speaker names are saved FIRST ...
    # ... and only then does a hosted provider's copy go: it holds nothing more we need. Best
    # effort and never in the way of the notes (`delete_fetched_copies` retries).
    _delete_copy_after_commit(session, meeting.id, vexa)
    return True


def _step_notes(session: Session, meeting: Meeting, openai_client) -> bool:
    template = session.get(Template, meeting.template_id)
    text = meeting.speaker_transcript or meeting.transcript or ""
    spent: list = []
    first_notes = meeting.notes is None  # a rewrite never re-shares what the owner unshared
    meeting.notes = generate_notes(
        text, template, _terms(session, meeting.owner_user_id), client=openai_client, usage=spent
    )
    ai_runs.record_usage(
        session,
        meeting.id,
        "notes",
        get_settings().notes_model,
        spent,
        user_id=meeting.owner_user_id,
    )
    meeting.status, meeting.step, meeting.failure_reason = "ready", "done", None
    if first_notes:
        teams.apply_share_default(session, meeting)  # read now, not when the bot joined (D3)
    # Queued in the same commit as the notes, and worked by its own job (app/workspace.py): it can
    # fail and retry without touching the meeting, whose notes are the deliverable.
    workspace.mark_pending(meeting)
    _link_series(session, meeting)
    _queue_notes_mail(session, meeting)
    session.commit()
    return True


def _link_series(session: Session, meeting: Meeting) -> None:
    """Put a calendar meeting into its recurring meeting (W8), in the same commit as the notes.
    Best effort like the mail: a failure is logged by type and rolls back only the link (a
    savepoint), never the notes."""
    try:
        with session.begin_nested():
            series.link_from_calendar(session, meeting)
    except Exception as exc:
        logger.warning("meeting %s: not linked to a series (%s)", meeting.id, type(exc).__name__)


def _queue_notes_mail(session: Session, meeting: Meeting) -> None:
    """Queue the "notes ready" mail with the notes, in the same commit. Best effort: a failure here
    is logged by type and rolls back only the mail (a savepoint), never the notes. The sender
    decides at send time whether the mail still goes (`app/mail.py`)."""
    try:
        if not mail.is_configured(get_settings()):
            return
        with session.begin_nested():
            mail.queue_notes_ready(session, meeting)
    except Exception as exc:
        logger.warning("meeting %s: notes mail not queued (%s)", meeting.id, type(exc).__name__)


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
    default = vexa or build_provider()  # the setting's provider (or the caller's, in a test)
    meeting = _lock(session, meeting_id)
    try:
        while meeting is not None and meeting.status in ("bot_sent", "processing"):
            if _owner_deleted(session, meeting):
                session.rollback()  # looked at on every step, so a deletion mid-run stops it
                return
            # The provider that ran THIS meeting, whatever the setting says now.
            vexa = provider_for(default, meeting.bot_provider, meeting.bot_region)
            if meeting.step == "waiting":
                proceed = _poll(session, meeting, vexa, now)
                if proceed:
                    meeting.status, meeting.step = "processing", "audio"
                    session.commit()
            elif meeting.step == "audio":
                proceed = _step_audio(session, meeting, vexa)
            elif meeting.step == "transcribing":
                proceed = _step_transcribing(session, meeting, vexa, openai_client)
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
    except BotProviderError as exc:
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
        elif (
            meeting is not None
            and meeting.status == "processing"
            and meeting.step == "speakers"
            and meeting.speaker_transcript is None
        ):
            # The same give-up for the speaker lookup, but not on the first miss: after the 6 hours
            # AND `SPEAKER_MISSES_TO_GIVE_UP` misses over `SPEAKERS_GIVE_UP_AFTER`, the notes go
            # ahead from the plain transcript (speakers incomplete) rather than wait for ever on a
            # provider that does not answer.
            count, first = _speaker_misses.get(meeting_id, (0, now))
            _speaker_misses[meeting_id] = (count + 1, first)
            if (
                _expired(meeting, now)
                and count + 1 >= SPEAKER_MISSES_TO_GIVE_UP
                and now - first >= SPEAKERS_GIVE_UP_AFTER
            ):
                _speaker_misses.pop(meeting_id, None)
                meeting.speaker_transcript = meeting.transcript or ""
                meeting.speakers_incomplete = True
                meeting.step = "notes"
                session.commit()
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
    gone and, when the provider's recording id is known, the provider's copy is gone too (Vexa:
    `DELETE /recordings/{id}`, 404 for one already deleted; Skribby: `DELETE /bot/{id}`, which also
    removes the transcript and the record of the bot, and is called for a bot with no recording
    too). A provider failure leaves the meeting unmarked so the next pass tries again. Returns how
    many meetings were cleared.
    """
    cutoff = now - timedelta(days=get_settings().audio_retention_days)
    # What the bot provider sent about who spoke goes first and on its own clock: it is deleted
    # EVIDENCE_RETENTION_DAYS after it was written, whatever state its meeting is in.
    session.execute(
        delete(SkribbySegmentEvidence).where(
            SkribbySegmentEvidence.created_at < now - timedelta(days=EVIDENCE_RETENTION_DAYS)
        )
    )
    expired = session.scalars(
        select(Meeting).where(
            Meeting.created_at < cutoff,
            Meeting.audio_deleted_at.is_(None),
            Meeting.status.in_(("ready", "failed")),
        )
    ).all()
    cleared = 0
    default = vexa
    for meeting in expired:
        folder = audio_path(meeting.id).parent
        try:
            if folder.exists():
                shutil.rmtree(folder)
        except OSError as exc:
            logger.error("meeting %s: could not delete audio (%s)", meeting.id, type(exc).__name__)
            continue
        if meeting.provider_copy_deleted_at is None and (
            meeting.vexa_recording_id is not None or meeting.vexa_meeting_id
        ):
            try:
                # The provider that holds this meeting's copy, whatever the setting says now.
                provider = provider_for(
                    default or build_provider(), meeting.bot_provider, meeting.bot_region
                )
                if meeting.vexa_recording_id is not None:
                    provider.delete_recording(meeting.vexa_recording_id)
                elif hasattr(type(provider), "delete_bot_record"):
                    # No recording, but a hosted provider still keeps a record of the bot (the
                    # link, the name, its timeline): a bot that was never let in, say.
                    provider.delete_bot_record(meeting.vexa_meeting_id)
                if hasattr(type(provider), "delete_bot_record"):
                    meeting.provider_copy_deleted_at = now
            except BotProviderError as exc:
                logger.warning(
                    "meeting %s: could not delete the provider's copy (%s); will retry",
                    meeting.id,
                    type(exc).__name__,
                )
                continue
        meeting.audio_deleted_at = now
        cleared += 1
    session.commit()
    return cleared
