"""User actions shared by the Jinja pages (`app/web.py`) and the JSON API (`app/api.py`).

Each action does the work and commits, or raises `Refused` with the HTTP status, a stable code
and the message the user reads. The callers only decide how to show that: a page with a notice,
or a JSON error. Keeping the rules here means the two can never disagree.

Every function that touches meetings, templates or vocabulary takes the acting `User` and filters
by `owner_user_id`. A row that belongs to someone else is "no such row" (404, never 403: ids are
sequential, so a 403 would confirm that the row exists).
"""

import logging
import math
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, Request
from markdown_it import MarkdownIt
from markupsafe import Markup
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session
from starlette.convertors import Convertor, register_url_convertor

from app import auth, edge, passwords, placement, usage, workspace
from app.config import Settings, get_settings
from app.models import AppState, Meeting, Template, User, VocabularyTerm
from app.notes import MAX_TERM_CHARS, NotesFailed, generate_notes
from app.pipeline import (
    BOT_END_REQUESTED,
    BOT_ENDABLE_STATES,
    BOT_RECORDING,
    JOIN_FAILURE_MARKS,
    MEET_REFUSED,
    REASON_CANCELLED,
    REASON_NO_SPEECH,
    is_join_failure,
    manual_retry_wait,
    send_again,
)
from app.placement import PlacementRefused, PlacementRequest
from app.vexa import UnsupportedMeetingUrl, VexaClient, VexaError, parse_meeting_url

logger = logging.getLogger(__name__)

MAX_URL_CHARS = 2000
MAX_ID_DIGITS = 10
MAX_ID = 2**31 - 1  # ids are Postgres integers; anything larger cannot exist
MAX_TEMPLATE_NAME_CHARS = 100
MAX_INSTRUCTION_CHARS = 4000
BAD_TERM_CHARS = ("<", ">", "\r", "\n")  # the same characters app/transcribe.py drops
ACTIVE = ("bot_sent", "processing")
# One worker pass runs a whole recording through transcription and notes, and the heartbeat is
# only stamped between passes, so an hour-long meeting can keep it quiet for several minutes.
WORKER_STALE_AFTER = timedelta(minutes=20)
# How long Cancel and End wait for a meeting row the worker holds (a poll takes seconds; a pass
# that has gone on to transcribe holds it for minutes) before answering 409 instead of hanging.
LOCK_WAIT = "10s"
LOCK_NOT_AVAILABLE = "55P03"

STEP_TEXT = {
    "waiting": "Getting started",
    "audio": "Downloading the audio",
    "transcribing": "Writing the transcript",
    "speakers": "Working out who said what",
    "notes": "Writing the notes",
}
# What the owner reads while the bot is in a call, by the state the worker last saw Vexa report.
# `{bot}` is the name the bot shows in the meeting. "Recording" is only said when something is
# being captured (`BOT_RECORDING`); `active` alone only means the bot is in.
BOT_STATE_TEXT = {
    "requested": "Starting the bot",
    "joining": "Bot is joining the meeting",
    "awaiting_admission": "Waiting to be let in. Admit '{bot}' in the meeting",
    "active": "Bot is in the meeting",
    BOT_RECORDING: "Recording",
    "needs_help": (
        "The bot is waiting on a prompt in the meeting, such as a consent or verification "
        "screen. Check the meeting for a prompt."
    ),
    "stopping": "The bot is leaving the meeting",
}
# After "End now and write the notes": the bot has been told to leave and has not been seen gone.
BOT_ENDING_TEXT = "The bot is leaving the meeting. The notes start once it has gone"
# Vexa reported the call over but the recording is not ready to download yet.
CALL_ENDED_TEXT = "The call ended; finishing the recording"
# A live state older than this many poll intervals is not shown as current: the worker is behind,
# or Vexa has not answered for a while. The worker handles meetings one at a time, so a pass on
# another meeting (minutes of transcription) leaves this one unseen for that long: the window only
# covers a slow Vexa and a short pass, not a long one.
BOT_STATE_STALE_POLLS = 4
BOT_STATE_UNSURE_TEXT = "Checking on the bot..."
STATUS_TONES = {
    "ready": "success",
    "failed": "danger",
    "processing": "info",
    "bot_sent": "warning",
}


class Refused(Exception):
    """An action the owner asked for that cannot go ahead. `message` is safe to show as is."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


# ---- dependencies the tests replace ----


def get_vexa() -> VexaClient:
    return VexaClient()


def get_openai_client():
    return None  # generate_notes builds its own client from settings


# ---- login ----


lockout_log_throttle = edge.Throttle()


def log_in(request: Request, session: Session, email: str, password: str) -> None:
    """Check the email and password and give the request a fresh logged-in session.

    The attempt is counted against the client address and the email (`auth.LoginLimiter`) before
    the password is looked at. An unknown email, an account with no password, an account whose email
    was never confirmed and a wrong password are the same answer, and each costs one hash check, so
    neither the reply nor its timing says whether an account exists.
    """
    settings = request.app.state.settings
    limiter: auth.LoginLimiter = request.app.state.limiter
    client = edge.client_address(request, settings)
    account = auth.account_key(email)
    device = auth.device_id(
        request.app.state.device_key,
        request.cookies.get(auth.device_cookie_name(settings)),
        account,
    )
    refused = limiter.check(client, account, device=device)  # counts this attempt
    if refused:
        if refused != "address" and lockout_log_throttle.ready():
            # The account is logged as a short hash (the ledger's `email_hash`, first 12 hex
            # characters), not the address itself: an email is personal data and logs outlive
            # the account. The operator can hash a suspected address to match it.
            logger.warning(
                "login refused: too many failed attempts in the last hour on the %s limit "
                "(account %s, client %s); further refusals are not logged for a minute",
                refused,
                usage.email_hash(account)[:12],
                client,
            )
        raise Refused(429, "too_many_attempts", "Too many failed attempts. Try again later.")
    user = session.scalars(select(User).where(User.email == account)).first()
    if user is not None and user.status == "deleted":
        user = None  # a deleted account is an unknown one
    try:
        right = passwords.verify_password(user.password_hash if user else None, password)
    except passwords.HashBusy:
        limiter.refund(client, account, device)  # the password was never checked: not a failure
        raise Refused(429, "server_busy", "The server is busy. Try again in a moment.") from None
    # An account that never confirmed its email fails exactly like a wrong password, after the
    # same one hash check. Telling it apart ("confirm your email") would let anyone find out
    # whether an address has a confirmed account: sign up with a password, then log in with it.
    if user is None or user.email_verified_at is None or not right:
        raise Refused(401, "invalid_credentials", "That email or password is not right.")
    limiter.succeeded(client, account, device)  # the password was right, whatever happens next
    if user.status != "active":
        raise Refused(403, "account_suspended", "This account has been suspended.")
    start_session(request, session, user)
    # A device cookie for this account, only now that the password was right; a known device
    # keeps its id so its failures stay on one counter.
    request.state.device_cookie = (account, device)


def start_session(request: Request, session: Session, user: User) -> None:
    """Give the request a fresh logged-in session for `user` and stamp the login time. The one
    place a session is issued (password login and Google sign-in both end here): a fresh token,
    never a promoted old one, stamped with this user's and the global epoch."""
    user.last_login_at = datetime.now(UTC)
    session.commit()
    request.state.session = auth.new_session(
        uid=user.id, epoch=user.session_epoch, gepoch=auth.current_epoch(session)
    )
    request.state.session_changed = True
    request.state.user = user


def log_out(request: Request, session: Session) -> None:
    """Sign this user out everywhere (their epoch is bumped), and nobody else."""
    user: User | None = getattr(request.state, "user", None)
    if user is not None:
        auth.bump_user_epoch(session, user.id)
    request.state.session = auth.new_session()
    request.state.session_changed = True
    request.state.user = None


# ---- ids ----


class IdConvertor(Convertor):
    """Path ids: 1 to 10 ASCII digits. Longer input never reaches `int()`, which raises on
    numbers of more than 4300 digits."""

    regex = r"[0-9]{1,10}"

    def convert(self, value: str) -> int:
        return int(value)

    def to_string(self, value: int) -> str:
        return str(value)


register_url_convertor("id", IdConvertor())


def parse_id(value: str) -> int | None:
    """A database id from form text, or None when it is not a plain number in range."""
    if not (value.isascii() and value.isdigit()) or len(value) > MAX_ID_DIGITS:
        return None
    number = int(value)
    return number if 0 < number <= MAX_ID else None


def _in_range(pk: int | None) -> bool:
    return pk is not None and 0 < pk <= MAX_ID


# ---- how a meeting reads ----


def bot_state_is_stale(meeting: Meeting, now: datetime | None = None) -> bool:
    """True when the bot's live state was last seen too long ago to say what the bot is doing now
    (or when no time was kept for it)."""
    seen = meeting.bot_state_seen_at
    if seen is None:
        return True
    window = timedelta(seconds=BOT_STATE_STALE_POLLS * get_settings().poll_interval_seconds)
    return (now or datetime.now(UTC)) - seen > window


def status_text(meeting: Meeting, now: datetime | None = None) -> str:
    """Plain words for the owner. "Recording" is said only once something is being captured, and a
    live state is said only while a recent poll has seen it; a bot whose state is not known (an
    older meeting, Vexa not reachable) reads "Bot sent"."""
    if meeting.status == "bot_sent":
        reason = meeting.completion_reason or ""
        if reason == BOT_END_REQUESTED and meeting.terminal_seen_at is None:
            return BOT_ENDING_TEXT
        text = BOT_STATE_TEXT.get(reason)
        if text:
            if bot_state_is_stale(meeting, now):
                return BOT_STATE_UNSURE_TEXT
            return text.format(bot=get_settings().bot_name)
        if meeting.terminal_seen_at is not None and reason not in JOIN_FAILURE_MARKS:
            return CALL_ENDED_TEXT  # Vexa said the call is over and the recording is being made
        return "Bot sent"
    if meeting.status == "processing":
        return STEP_TEXT.get(meeting.step, "Working on it")
    if meeting.status == "ready":
        return "Ready"
    return "Failed"


def status_tone(meeting: Meeting) -> str:
    return STATUS_TONES.get(meeting.status, "neutral")


def retry_block(meeting: Meeting, now: datetime | None = None) -> str | None:
    """Why a failed meeting cannot be retried, or None when it can.

    Retrying resumes at the step that failed, so it needs either the saved transcript or the
    downloaded audio. A bot that never recorded anything (step `waiting`) has neither, except one
    whose join the worker ended (Google refused it, or it hung on a prompt): that retry sends a
    new bot, and is held back for a few minutes.
    """
    if meeting.status != "failed":
        return "not_failed"
    if meeting.step == "waiting":
        if is_join_failure(meeting) and not manual_retry_wait(meeting, now or datetime.now(UTC)):
            return None
        return "no_audio"
    if meeting.failure_reason == REASON_NO_SPEECH:
        return "no_speech"  # the same audio would give the same result
    if meeting.transcript is None and meeting.audio_deleted_at is not None:
        return "audio_deleted"
    return None


def retry_block_message(
    meeting: Meeting, block: str | None, now: datetime | None = None
) -> str | None:
    """The words for a retry block. The wait after a failed join says how long is left."""
    if block == "no_audio" and is_join_failure(meeting):
        wait = manual_retry_wait(meeting, now or datetime.now(UTC))
        minutes = max(1, math.ceil(wait.total_seconds() / 60))
        unit = "minute" if minutes == 1 else "minutes"
        if meeting.completion_reason == MEET_REFUSED:
            return (
                f"Google refused the bot a moment ago. To be polite to Google, you can send it "
                f"again in {minutes} {unit}."
            )
        return (
            "The bot was just taken out of this meeting. "
            f"You can send it again in {minutes} {unit}."
        )
    return RETRY_BLOCK_TEXT.get(block)


RETRY_BLOCK_TEXT = {
    "audio_deleted": "This meeting can't be retried: the audio has been deleted and no transcript "
    "was saved.",
    "no_speech": "This meeting can't be retried: no speech was found, so trying again would give "
    "the same result.",
    "no_audio": "This meeting can't be retried: nothing was captured. Send the bot again with a "
    "new link.",
}

_markdown = MarkdownIt("commonmark", {"html": False, "linkify": False}).disable("image")


def _link_open(self, tokens, idx, options, env):
    tokens[idx].attrSet("rel", "noopener noreferrer nofollow")
    return self.renderToken(tokens, idx, options, env)


_markdown.add_render_rule("link_open", _link_open)


def render_markdown(source: str | None) -> Markup:
    """Notes come from a model reading untrusted meeting text, so raw HTML is escaped and
    links like `javascript:` are dropped by the renderer."""
    return Markup(_markdown.render(source or ""))


def worker_is_stale(session: Session) -> bool:
    state = session.get(AppState, 1)
    beat = state.worker_heartbeat_at if state else None
    return beat is None or datetime.now(UTC) - beat > WORKER_STALE_AFTER


# ---- meetings ----


def list_meetings(
    session: Session, user: User, limit: int = 50, before_id: int | None = None
) -> list[Meeting]:
    query = (
        select(Meeting)
        .where(Meeting.owner_user_id == user.id)
        .order_by(Meeting.id.desc())
        .limit(limit)
    )
    if before_id is not None:
        query = query.where(Meeting.id < before_id)
    return list(session.scalars(query))


def meeting_title(meeting: Meeting) -> str:
    return f"{'Zoom' if meeting.platform == 'zoom' else 'Google Meet'} {meeting.native_id}"


def meeting_or_404(session: Session, user: User, meeting_id: int) -> Meeting:
    meeting = None
    if _in_range(meeting_id):
        meeting = session.scalars(
            select(Meeting).where(Meeting.id == meeting_id, Meeting.owner_user_id == user.id)
        ).first()
    if meeting is None:
        raise HTTPException(404, "No such meeting")
    return meeting


def _own_template(session: Session, user_id: int, template_id: int | None) -> Template | None:
    if not _in_range(template_id):
        return None
    return session.scalars(
        select(Template).where(Template.id == template_id, Template.owner_user_id == user_id)
    ).first()


def hours_cap_message(cap_seconds: int) -> str:
    hours = f"{cap_seconds / 3600:.1f}".removesuffix(".0")
    return (
        f"You have used your {hours} recorded hours for the pilot, so the note taker will not "
        "join new meetings. Write to us at hello@beyrictech.com if you need more."
    )


def placement_refusal(exc: PlacementRefused) -> Refused:
    """The words and status for a refused placement. Two refusals name the user's own situation
    (their hours, their own bots); a full note taker is only "busy" and says nothing about whose
    meeting holds it, so one user's activity is never visible to another."""
    if exc.code == "hours_cap_reached":
        return Refused(403, "hours_cap_reached", hours_cap_message(exc.cap_seconds or 0))
    if exc.code == "already_in_meeting":
        return Refused(
            409,
            "already_in_meeting",
            "A note taker is already in that meeting for you. Wait for it to finish first.",
        )
    if exc.code == "user_limit":
        return Refused(
            409,
            "user_limit",
            f"You already have {exc.limit} bots in meetings. Wait for one to finish first.",
        )
    return Refused(409, "bot_busy", "The note taker is busy right now. Try again in a few minutes.")


def _send_failed() -> Refused:
    return Refused(
        502, "bot_send_failed", "The bot could not be sent. Please try again in a moment."
    )


def create_meeting(
    session: Session,
    vexa: VexaClient,
    user: User,
    url: str,
    template_id: int | None,
    settings: Settings,
) -> Meeting:
    try:
        return _create_meeting(session, vexa, user, url.strip(), template_id, settings)
    except Refused:
        session.rollback()  # also releases the advisory lock
        raise


def _create_meeting(
    session: Session,
    vexa,
    user: User,
    url: str,
    template_id: int | None,
    settings: Settings,
) -> Meeting:
    # The submitted link is never echoed back: a Zoom link carries its meeting password.
    try:
        if len(url) > MAX_URL_CHARS:
            raise UnsupportedMeetingUrl("too long")
        platform, native_id = parse_meeting_url(url)
    except UnsupportedMeetingUrl:
        raise Refused(
            400,
            "invalid_meeting_url",
            "That is not a Google Meet or Zoom link. Paste the full meeting link.",
        ) from None
    template = _own_template(session, user.id, template_id)
    if template is None:  # also a template that is someone else's: it looks like a missing one
        raise Refused(400, "template_required", "Choose a template for the notes.")

    request = PlacementRequest(
        user_id=user.id,
        platform=platform,
        native_id=native_id,
        meeting_url=url,
        template_id=template.id,
        source="manual",
    )
    try:
        return placement.place_bot(session, vexa, request, now=datetime.now(UTC), settings=settings)
    except PlacementRefused as exc:
        raise placement_refusal(exc) from None
    except VexaError as exc:
        logger.warning("send_bot failed (%s)", type(exc).__name__)
        raise _send_failed() from None


def regenerate_notes(
    session: Session, meeting: Meeting, template_id: int | None, openai_client
) -> Meeting:
    if meeting.status != "ready":
        raise Refused(409, "not_ready", "Notes can only be rewritten once a meeting is ready.")
    template = _own_template(session, meeting.owner_user_id, template_id)
    if template is None:
        raise Refused(400, "template_required", "Choose a template.")
    source = meeting.speaker_transcript or meeting.transcript or ""
    terms = list(
        session.scalars(
            select(VocabularyTerm.term).where(VocabularyTerm.owner_user_id == meeting.owner_user_id)
        )
    )
    try:
        notes = generate_notes(source, template, terms, client=openai_client)
    except NotesFailed as exc:
        logger.error("meeting %s: regenerate failed (%s)", meeting.id, type(exc).__name__)
        raise Refused(
            502, "notes_failed", "The notes could not be written. The old notes were kept."
        ) from None
    meeting.notes = notes
    meeting.template_id = template.id
    workspace.mark_pending(meeting)  # the lists read from the notes follow the new ones
    session.commit()
    return meeting


def _lock_meeting(session: Session, meeting: Meeting) -> Meeting:
    """Re-read the meeting under a row lock so the worker cannot move it meanwhile. A row the
    worker is holding is waited for at most `LOCK_WAIT`; then it is a plain 409 and the owner may
    press again (`SET LOCAL` ends with the transaction, so it never leaks to later statements)."""
    try:
        session.execute(text(f"set local lock_timeout = '{LOCK_WAIT}'"))
        return session.scalars(
            select(Meeting)
            .where(Meeting.id == meeting.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).one()
    except OperationalError as exc:
        session.rollback()
        if getattr(exc.orig, "sqlstate", None) != LOCK_NOT_AVAILABLE:
            raise
        raise Refused(
            409,
            "meeting_busy",
            "The meeting is being worked on right now. Try again in a moment.",
        ) from None


def _stop_bot_or_refuse(session: Session, vexa, meeting: Meeting) -> None:
    """Ask Vexa to take the bot out, or roll back and refuse with a plain 502 (the message never
    carries Vexa's own text; a meeting code the client will not send is the same failure)."""
    try:
        vexa.stop_bot(meeting.platform, meeting.native_id)
    except (VexaError, ValueError) as exc:
        session.rollback()
        logger.warning("meeting %s: stop_bot failed (%s)", meeting.id, type(exc).__name__)
        raise Refused(
            502, "bot_stop_failed", "The bot could not be stopped. Please try again in a moment."
        ) from None


def cancel_meeting(session: Session, vexa: VexaClient, meeting: Meeting) -> Meeting:
    """Take the bot out of a meeting it has not finished recording. No OpenAI call is made."""
    locked = _lock_meeting(session, meeting)
    if locked.status != "bot_sent":
        session.rollback()
        raise Refused(
            409,
            "not_waiting_for_bot",
            "Only a meeting that is still waiting for its bot can be cancelled.",
        )
    if locked.completion_reason == BOT_END_REQUESTED:
        session.rollback()
        raise Refused(
            409,
            "already_ending",
            "The bot is already leaving and the notes will be written from what it recorded, "
            "so this meeting can't be cancelled now.",
        )
    _stop_bot_or_refuse(session, vexa, locked)
    locked.status, locked.failure_reason = "failed", REASON_CANCELLED
    session.commit()
    return locked


def can_end(meeting: Meeting) -> bool:
    """Whether "End now and write the notes" is offered: the bot is in the call, as the worker last
    saw it. The state's age does not matter here: the owner who wants the bot out should not lose
    the button because the worker is a little behind."""
    return (
        meeting.status == "bot_sent"
        and meeting.step == "waiting"
        and meeting.completion_reason in BOT_ENDABLE_STATES
    )


def can_cancel(meeting: Meeting) -> bool:
    """Cancel discards the meeting, so it is not offered once the owner has asked the bot to leave
    to have the notes written (Vexa answers a second stop with 404, which would pass for success
    and quietly throw the recording away)."""
    return meeting.status == "bot_sent" and meeting.completion_reason != BOT_END_REQUESTED


def _not_endable() -> Refused:
    return Refused(
        409,
        "not_endable",
        "The bot can only be ended while it is in the call. Cancel it instead if it is not in yet.",
    )


def _check_endable(meeting: Meeting) -> None:
    if meeting.completion_reason == BOT_END_REQUESTED and meeting.status == "bot_sent":
        raise Refused(
            409,
            "already_ending",
            "The bot has already been told to leave. The notes start once it has gone.",
        )
    if not can_end(meeting):
        raise _not_endable()


def end_meeting(session: Session, vexa: VexaClient, meeting: Meeting) -> Meeting:
    """Take the bot out of the call and let the pipeline write the notes from what it recorded.

    Unlike `cancel_meeting` nothing is discarded: the meeting stays `bot_sent`, marked
    `end_requested`, and the worker carries on as for any call that ended (Vexa reports the bot
    gone with the reason `stopped`, the recording is downloaded and the usual steps run). A bot
    that recorded nothing ends as the same "nothing was recorded" failure as any empty meeting."""
    _check_endable(meeting)  # without the lock: a meeting being processed must not make us wait
    locked = _lock_meeting(session, meeting)
    try:
        _check_endable(locked)
    except Refused:
        session.rollback()
        raise
    _stop_bot_or_refuse(session, vexa, locked)
    locked.completion_reason = BOT_END_REQUESTED
    locked.bot_state_seen_at = datetime.now(UTC)
    session.commit()
    logger.info("meeting %s: the owner ended the bot", locked.id)
    return locked


def retry_meeting(session: Session, meeting: Meeting, vexa: VexaClient | None = None) -> Meeting:
    now = datetime.now(UTC)
    refused = is_join_failure(meeting)
    if retry_block(meeting, now) is not None:
        raise Refused(
            409,
            "not_retryable",
            retry_block_message(meeting, "no_audio", now)
            if refused
            else "This meeting cannot be retried.",
        )
    if refused:
        return _send_refused_meeting_again(session, vexa or get_vexa(), meeting)
    # The step is kept. A fresh polling start keeps the time limit from failing it at once.
    meeting.status, meeting.failure_reason = "processing", None
    meeting.started_polling_at = now
    session.commit()
    return meeting


def _send_refused_meeting_again(session: Session, vexa: VexaClient, meeting: Meeting) -> Meeting:
    """Send a new bot to a meeting Google refused. It goes through the same pre-flight as a new
    meeting (hours, the user's bots, a bot already in that meeting, room on the host) under the
    same placement lock, so a retry can never take a slot a new meeting was promised."""
    # The row first, the placement lock second: nothing waits for a row while holding the lock.
    locked = _lock_meeting(session, meeting)  # a double click must not send two bots
    now = datetime.now(UTC)
    if retry_block(locked, now) is not None:
        session.rollback()
        raise Refused(409, "not_retryable", "This meeting cannot be retried.")
    owner = session.get(User, locked.owner_user_id)
    try:
        placement.reserve_slot(
            session,
            owner,
            platform=locked.platform,
            native_id=locked.native_id,
            now=now,
            settings=get_settings(),
        )
    except PlacementRefused as exc:
        session.rollback()
        raise placement_refusal(exc) from None
    if not send_again(locked, vexa):
        session.rollback()
        raise _send_failed()
    locked.started_polling_at = now
    session.commit()
    return locked


# ---- templates ----


def list_templates(session: Session, user: User) -> list[Template]:
    return list(
        session.scalars(
            select(Template).where(Template.owner_user_id == user.id).order_by(Template.name)
        )
    )


def template_or_404(session: Session, user: User, template_id: int) -> Template:
    template = _own_template(session, user.id, template_id)
    if template is None:
        raise HTTPException(404, "No such template")
    return template


def _check_template(name: str, instructions: str) -> None:
    if not name or not instructions:
        raise Refused(400, "invalid_template", "Give the template a name and instructions.")
    if len(name) > MAX_TEMPLATE_NAME_CHARS:
        raise Refused(
            400,
            "invalid_template",
            f"The name can be at most {MAX_TEMPLATE_NAME_CHARS} characters.",
        )
    if len(instructions) > MAX_INSTRUCTION_CHARS:
        raise Refused(
            400,
            "invalid_template",
            f"The instructions can be at most {MAX_INSTRUCTION_CHARS} characters.",
        )


def _duplicate_template() -> Refused:
    return Refused(400, "duplicate_name", "A template with that name already exists.")


def save_template(
    session: Session, user: User, template: Template | None, name: str, instructions: str
) -> Template:
    """Create a template of `user`'s, or replace the name and instructions of `template`."""
    try:
        return _save_template(session, user, template, name.strip(), instructions.strip())
    except Refused:
        session.rollback()
        raise


def _save_template(
    session: Session, user: User, template: Template | None, name: str, instructions: str
) -> Template:
    _check_template(name, instructions)
    taken = session.scalars(
        select(Template.id).where(Template.owner_user_id == user.id, Template.name == name)
    ).first()
    if taken is not None and (template is None or taken != template.id):
        raise _duplicate_template()
    if template is None:
        template = Template(owner_user_id=user.id, name=name, instructions=instructions)
        session.add(template)
    else:
        template.name, template.instructions = name, instructions
    try:
        session.commit()
    except IntegrityError:  # lost a race with another request
        raise _duplicate_template() from None
    return template


# ---- vocabulary ----


def list_terms(session: Session, user: User) -> list[VocabularyTerm]:
    return list(
        session.scalars(
            select(VocabularyTerm)
            .where(VocabularyTerm.owner_user_id == user.id)
            .order_by(VocabularyTerm.term)
        )
    )


def _check_term(term: str) -> None:
    if not term:
        raise Refused(400, "invalid_term", "Type a term to add.")
    if len(term) > MAX_TERM_CHARS:
        raise Refused(400, "invalid_term", f"A term can be at most {MAX_TERM_CHARS} characters.")
    if any(char in term for char in BAD_TERM_CHARS):
        raise Refused(
            400, "invalid_term", "A term cannot contain new lines or the characters < and >."
        )


def add_term(session: Session, user: User, term: str) -> VocabularyTerm:
    term = term.strip()
    _check_term(term)
    taken = session.scalars(
        select(VocabularyTerm.id).where(
            VocabularyTerm.owner_user_id == user.id, VocabularyTerm.term == term
        )
    ).first()
    if taken:
        raise Refused(400, "duplicate_term", "That term is already in the list.")
    row = VocabularyTerm(owner_user_id=user.id, term=term)
    session.add(row)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise Refused(400, "duplicate_term", "That term is already in the list.") from None
    return row


def add_term_from_review(session: Session, user: User, item, term: str) -> VocabularyTerm:
    """Add `term` to the caller's vocabulary and mark the review item checked. The term is
    checked before anything changes (400 `invalid_term`). A term that exists already is 409
    `duplicate_term`, but the item is still checked: the person has dealt with it."""
    term = term.strip()
    _check_term(term)
    workspace.set_checked(session, item, True)
    taken = session.scalars(
        select(VocabularyTerm).where(
            VocabularyTerm.owner_user_id == user.id, VocabularyTerm.term == term
        )
    ).first()
    if taken:
        raise Refused(409, "duplicate_term", "That term is already in your vocabulary.")
    row = VocabularyTerm(owner_user_id=user.id, term=term)
    session.add(row)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise Refused(409, "duplicate_term", "That term is already in your vocabulary.") from None
    return row


def delete_term(session: Session, user: User, term_id: int) -> bool:
    """Remove one of `user`'s terms. False when they have no such term."""
    term = None
    if _in_range(term_id):
        term = session.scalars(
            select(VocabularyTerm).where(
                VocabularyTerm.id == term_id, VocabularyTerm.owner_user_id == user.id
            )
        ).first()
    if term is None:
        return False
    session.delete(term)
    session.commit()
    return True
