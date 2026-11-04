"""`place_bot()`: the one place a bot is sent to a meeting (spec section 9.4, plan task C11).

A pasted link, a shared link, and (from C12) a calendar join all come through here, so the rules
about how many bots may run, and for whom, exist once. This is the single-host form: one local Vexa
host, `bot_max_local` bots at a time. Phase D generalises it to several hosts (the second int of
the lock is the host id, always 1 until then).

Pre-flight, in this order, each refusing with a `PlacementRefused` code:

1. `hours_cap_reached`: the user has used their recorded hours (`app/usage.py`'s gate).
2. `user_limit`: the user already has `user_bot_limit` bots on meetings.
3. `already_in_meeting`: the user already has a bot in this meeting (same platform and code).
4. `meeting_has_note_taker`: another user's bot is in this meeting. Vexa (one API key) keeps one
   bot per meeting code, so a second account cannot get its own; sharing waits for C12 or
   per-user keys. Neutral on purpose: no user, no meeting id, no hint of who.
5. `bot_busy`: the host is full. Says nothing about whose meeting holds it, and there is no queue:
   a queued meeting is an unrecorded meeting. The caller (a person, or the C12 runner) decides
   what to do with the refusal.

Whether the user's calendar connection is still good is the caller's check: a pasted link has no
connection.

Locking. The checks and the send run under `pg_try_advisory_xact_lock(PLACEMENT_LOCK, host)`,
taken first and held until the meeting row is committed (or the transaction is rolled back), so two
requests can never both read the one free slot. A request that finds it taken polls the try-lock for
at most `LOCK_WAIT_SECONDS` (3 s) and gives its pooled database connection back between looks
(a rollback), so a few waiters cannot starve every other endpoint; if the lock is still taken it
answers `bot_busy` with `slot_locked` set (words: a note taker is being set up, try again in a
minute), and if the slot is simply full it answers `bot_busy` (words: busy, try in a few minutes).
A caller that holds a row lock passes `wait_seconds=0`: the rollback between looks would drop it.
The holder is bounded too: `VexaClient.send_bot` has a total deadline
(`app.vexa.SEND_DEADLINE_SECONDS`).

An uncertain send (`VexaSendUncertain`: a timeout, a lost answer, a 5xx) may have reached the
host, which keeps handling the request after the client leaves, so the bot can join seconds later.
A stop sent now would find nothing, so instead the meeting is kept as a placeholder: `bot_sent`, no
Vexa id, `completion_reason = SEND_UNCONFIRMED` (the owner reads "Starting the bot"), counted for
capacity and for the duplicate checks, so the code stays taken. The worker settles it
(`pipeline._poll`): it adopts a bot that shows up for the code, or after `UNCONFIRMED_GRACE` stops
the code and fails the meeting with a plain "not recorded" reason.

A caller that holds a row lock must take it before calling in, never after: nothing here ever
waits for a row while holding the placement lock, which keeps the order row -> placement everywhere
and rules out a deadlock.
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app import titles, usage
from app.config import Settings, get_settings
from app.models import MEETING_SOURCES, Meeting, Template, User, UserSettings
from app.vexa import VexaClient, VexaDuplicateBot, VexaError, VexaSendUncertain

logger = logging.getLogger(__name__)

# Next to SIGNUP_LOCK (6002), ADMIN_LOCK (6003) and SCHEDULER_LOCK (6010). The first int of the
# two-int form; the second is the host id.
PLACEMENT_LOCK = 6004
LOCAL_HOST_ID = 1
# How long a request polls for the lock before it answers `bot_busy` ("being set up"). Short on
# purpose: it is time spent behind another request's send, and the connection is let go between
# looks. A send itself is bounded by `app.vexa.SEND_DEADLINE_SECONDS`.
LOCK_WAIT_SECONDS = 3.0
LOCK_POLL_SECONDS = 0.2
# Stored in `completion_reason` of a meeting whose send got no clear answer (see the module doc).
SEND_UNCONFIRMED = "send_unconfirmed"
# A bot counts against the user limit and the host's capacity from the moment it is sent until the
# meeting is over for the pipeline too, as before placement existed (`processing` still counts).
# The duplicate check is narrower: once the bot has left (`processing`), the same code is free.
HOLDS_A_SLOT = ("bot_sent", "processing")
IN_THE_CALL = "bot_sent"


@dataclass(frozen=True)
class PlacementRequest:
    user_id: int
    platform: str
    native_id: str
    meeting_url: str  # may carry a Zoom passcode: never logged, never echoed
    template_id: int | None  # None = the user's calendar template
    source: str  # one of `MEETING_SOURCES`: manual, calendar, instant_meet
    # The calendar event's title, stored on the meeting (cleaned: it is text from an invite).
    # None for a pasted link, which is then named by platform and code.
    title: str | None = None


class PlacementRefused(Exception):
    """No bot may be sent now. `code` is one of `hours_cap_reached`, `already_in_meeting`,
    `meeting_has_note_taker`, `user_limit`, `bot_busy`, `account_suspended`; the caller words it
    for its own reader."""

    def __init__(
        self,
        code: str,
        *,
        cap_seconds: int | None = None,
        limit: int | None = None,
        slot_locked: bool = False,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.slot_locked = slot_locked  # `bot_busy` because a send was in flight, not because full
        self.cap_seconds = cap_seconds  # `hours_cap_reached`: the user's cap
        self.limit = limit  # `user_limit`: how many bots a user may have


def _take_lock(session: Session, wait_seconds: float) -> None:
    """Take the placement lock, polling for at most `wait_seconds`, else `bot_busy` (locked).

    Between looks the transaction is rolled back so the pooled connection goes back to the pool
    while this request sleeps; nothing the caller needs may be pending (see the module doc)."""
    deadline = time.monotonic() + wait_seconds
    while True:
        got = session.scalar(
            text("select pg_try_advisory_xact_lock(cast(:key as int), cast(:host as int))"),
            {"key": PLACEMENT_LOCK, "host": LOCAL_HOST_ID},
        )
        if got:
            return
        if time.monotonic() >= deadline:
            session.rollback()
            raise PlacementRefused("bot_busy", slot_locked=True)
        session.rollback()  # the connection is not held while we wait
        time.sleep(LOCK_POLL_SECONDS)


def reserve_slot(
    session: Session,
    user: User,
    *,
    platform: str,
    native_id: str,
    now: datetime,
    settings: Settings | None = None,
    wait_seconds: float | None = None,
) -> None:
    """Take the placement lock and run the pre-flight checks. `wait_seconds` is how long to poll for
    the lock (default `LOCK_WAIT_SECONDS`; 0 for a caller holding a row lock, which a wait would
    drop). Returns holding the lock, which lasts until the caller commits or rolls back; the
    caller must do that, and must send the bot before it commits. Raises `PlacementRefused` (the
    caller rolls back)."""
    settings = settings or get_settings()
    _take_lock(session, LOCK_WAIT_SECONDS if wait_seconds is None else wait_seconds)
    # Under the lock, from the database (not the identity map): an account suspended while this
    # request waited for the lock must not get a bot.
    status = session.scalar(select(User.status).where(User.id == user.id))
    if status != "active":
        raise PlacementRefused("account_suspended")

    cap = usage.effective_cap(session, user)
    if (
        cap is not None
        and usage.hours_used(session, user, now, settings.max_poll_hours * 3600) >= cap
    ):
        raise PlacementRefused("hours_cap_reached", cap_seconds=cap)

    own = session.scalar(
        select(func.count(Meeting.id)).where(
            Meeting.owner_user_id == user.id, Meeting.status.in_(HOLDS_A_SLOT)
        )
    )
    if own >= settings.user_bot_limit:
        raise PlacementRefused("user_limit", limit=settings.user_bot_limit)

    already = session.scalar(
        select(Meeting.id)
        .where(
            Meeting.owner_user_id == user.id,
            Meeting.platform == platform,
            Meeting.native_id == native_id,
            Meeting.status == IN_THE_CALL,
        )
        .limit(1)
    )
    if already is not None:
        raise PlacementRefused("already_in_meeting")

    if session.scalar(
        select(Meeting.id)
        .where(
            Meeting.platform == platform,
            Meeting.native_id == native_id,
            Meeting.status == IN_THE_CALL,
        )
        .limit(1)
    ):
        raise PlacementRefused("meeting_has_note_taker")  # someone else's: the own check ran first

    busy = session.scalar(select(func.count(Meeting.id)).where(Meeting.status.in_(HOLDS_A_SLOT)))
    if busy >= settings.bot_max_local:
        raise PlacementRefused("bot_busy")


def _resolve(session: Session, req: PlacementRequest) -> tuple[User, int]:
    """The user and the template to write the notes with. A programming error (a caller that did
    not check) raises `ValueError`, never a refusal: nothing here is for a person to read."""
    if req.source not in MEETING_SOURCES:
        raise ValueError("unknown meeting source")
    user = session.get(User, req.user_id)
    if user is None:
        raise ValueError("no such user")
    template_id = req.template_id
    if template_id is None:
        template_id = session.scalar(
            select(UserSettings.calendar_template_id).where(UserSettings.user_id == user.id)
        )
    owned = (
        None
        if template_id is None
        else session.scalar(
            select(Template.id).where(Template.id == template_id, Template.owner_user_id == user.id)
        )
    )
    if owned is None:
        raise ValueError("the template is missing or belongs to someone else")
    return user, owned


def place_bot(
    session: Session,
    vexa: VexaClient,
    req: PlacementRequest,
    *,
    now: datetime,
    settings: Settings | None = None,
    wait_seconds: float | None = None,
    on_placed: Callable[[Session, Meeting], None] | None = None,
) -> Meeting:
    """Send a bot to `req.meeting_url` and record the meeting, or raise `PlacementRefused`.

    `wait_seconds` is passed to `reserve_slot` (0 for a caller that holds a row lock). `on_placed`
    runs once the meeting row exists (flushed, with its id) and before the commit, in the same
    transaction: the join runner marks its join `joined` there, so a crash can never leave a
    meeting with a join row that does not know it. If it raises, the bot is stopped and nothing
    is recorded, as for a failed flush.

    Commits (the meeting row and the lock leave together). A `VexaError` from the send is raised
    after a rollback: no meeting exists and the slot is free again. If the send may have reached
    the host without an answer (`VexaSendUncertain`), the meeting is returned as an unconfirmed
    start (see the module doc) for the worker to settle. If recording the meeting fails after a
    send, the bot is stopped, best effort, before the failure is raised."""
    user, template_id = _resolve(session, req)
    try:
        reserve_slot(
            session,
            user,
            platform=req.platform,
            native_id=req.native_id,
            now=now,
            settings=settings,
            wait_seconds=wait_seconds,
        )
    except PlacementRefused:
        session.rollback()  # also releases the lock
        raise
    try:
        vexa_id = vexa.send_bot(req.meeting_url)
    except VexaDuplicateBot:  # a bot that is not ours is there (another host, a race)
        session.rollback()
        raise PlacementRefused("meeting_has_note_taker") from None
    except VexaSendUncertain:  # it may be joining in a moment: keep the code, let the worker settle
        logger.warning("send_bot gave no clear answer: the meeting is kept as an unconfirmed start")
        vexa_id = None
    except BaseException:  # the host said no, or anything else: no meeting, free the slot
        session.rollback()
        raise
    meeting = Meeting(
        owner_user_id=user.id,
        url=req.meeting_url,
        platform=req.platform,
        native_id=req.native_id,
        template_id=template_id,
        vexa_meeting_id=None if vexa_id is None else str(vexa_id),
        started_polling_at=now,
        source=req.source,
        title=titles.event_title(req.title),
    )
    if vexa_id is None:
        # The grace counts from after the send, not from the caller's `now`, which was taken
        # before the lock wait and the send (up to 30 s of the 60 would be gone).
        sent_at = datetime.now(UTC)
        meeting.completion_reason, meeting.bot_state_seen_at = SEND_UNCONFIRMED, sent_at
        meeting.started_polling_at = sent_at
    try:
        session.add(meeting)
        session.flush()
        if on_placed is not None:
            on_placed(session, meeting)
            session.flush()
    except BaseException:
        stop_unrecorded_bot(vexa, req.platform, req.native_id)  # before the lock is let go
        session.rollback()
        raise
    commit_or_stop(session, vexa, platform=req.platform, native_id=req.native_id, vexa_id=vexa_id)
    return meeting


def commit_or_stop(
    session: Session, vexa: VexaClient, *, platform: str, native_id: str, vexa_id: int | None
) -> None:
    """Commit a meeting row that points at the bot `vexa_id` (None: an unconfirmed start). If the
    commit fails, roll back FIRST (the lock is gone from then on), then stop that bot, unless the
    row did land after all (a lost answer to a commit that succeeded): that bot is a recorded one
    and stopping it would throw a meeting away. With no id there is nothing to check: it stops.
    The stop is by meeting code, so it runs only under the placement lock again and only if no
    live meeting holds the code by then (`_stop_if_the_code_is_free`)."""
    try:
        session.commit()
    except BaseException:
        session.rollback()
        if vexa_id is None or not _is_recorded(session, platform, native_id, vexa_id):
            _stop_if_the_code_is_free(session, vexa, platform, native_id)
        raise


def _stop_if_the_code_is_free(
    session: Session, vexa: VexaClient, platform: str, native_id: str
) -> None:
    """Stop an unrecorded bot by code while holding the placement lock, so nobody else can place on
    the code meanwhile, and skip the stop when a live meeting of anyone holds the code: the bot
    there may be theirs. If the lock or the check cannot be had (the database is failing, so nobody
    else is placing either) the stop goes ahead: an unrecorded bot is the worse outcome. If the lock
    is simply not free another placement is under way and the stop is skipped."""
    try:
        _take_lock(session, LOCK_WAIT_SECONDS)
        holder = session.scalar(
            select(Meeting.id)
            .where(
                Meeting.platform == platform,
                Meeting.native_id == native_id,
                Meeting.status == IN_THE_CALL,
            )
            .limit(1)
        )
        if holder is not None:
            logger.warning("an unrecorded bot was left alone: a live meeting holds its code")
            return
    except PlacementRefused:
        # Another placement holds the lock: a stop by code could take out ITS bot, so skip it. The
        # bot's own `automatic_leave` is the backstop.
        logger.warning("an unrecorded bot was left alone: the placement lock was not free")
        return
    except Exception:  # cannot check: see above
        session.rollback()
    try:
        stop_unrecorded_bot(vexa, platform, native_id)
    finally:
        session.rollback()  # releases the lock


def _is_recorded(session: Session, platform: str, native_id: str, vexa_id: int) -> bool:
    try:
        return (
            session.scalar(
                select(Meeting.id)
                .where(
                    Meeting.platform == platform,
                    Meeting.native_id == native_id,
                    Meeting.vexa_meeting_id == str(vexa_id),
                )
                .limit(1)
            )
            is not None
        )
    except Exception:  # cannot tell: an unrecorded bot is worse than a stopped recorded one
        session.rollback()
        return False


def stop_unrecorded_bot(vexa: VexaClient, platform: str, native_id: str) -> None:
    """The bot is in a meeting the database has no row for. Take it out; if that fails too the
    bot stays until Vexa's own limits end it, which is logged for an operator."""
    try:
        vexa.stop_bot(platform, native_id)
    except (VexaError, ValueError) as exc:
        logger.error(
            "a bot was sent but its meeting could not be saved, and it could not be stopped (%s)",
            type(exc).__name__,
        )


def may_stop_bot(session: Session, meeting: Meeting) -> bool:
    """Whether stopping the bot at this meeting's code takes out THIS meeting's bot.

    `stop_bot` is keyed by platform and meeting code alone, and Vexa keeps one bot per code, so
    whichever bot is there goes. If another meeting row also has a live bot on the code, the bot
    there is that row's (Vexa let it in only after ours was gone, or it was sent later): a stop for
    this meeting must not run. This meeting owns the bot only if no other live row on the code is
    newer (or, when this one is not live itself, if there is no other live row at all)."""
    other = select(Meeting.id).where(
        Meeting.platform == meeting.platform,
        Meeting.native_id == meeting.native_id,
        Meeting.status == IN_THE_CALL,
        Meeting.id != meeting.id,
    )
    if meeting.status == IN_THE_CALL:
        other = other.where(Meeting.id > meeting.id)
    return session.scalar(other.limit(1)) is None
