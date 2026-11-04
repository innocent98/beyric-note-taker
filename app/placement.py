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
requests can never both read the one free slot. A request that finds the lock taken answers
`bot_busy` AT ONCE instead of waiting: a waiter would hold a pooled database connection and an open
transaction for as long as the holder's send, and a handful of them would starve every other
endpoint. The holder is bounded too: `VexaClient.send_bot` has a total deadline
(`app.vexa.SEND_DEADLINE_SECONDS`).

When a send may have reached the host (a timeout, a lost answer), the bot is stopped by meeting code
BEFORE the lock is let go. Stopping by code is only safe while no other placement can use the code,
and the pre-flight check made sure no live meeting of any user has it.

A caller that holds a row lock must take it before calling in, never after: nothing here ever
waits for a row while holding the placement lock, which keeps the order row -> placement everywhere
and rules out a deadlock.
"""

import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app import usage
from app.config import Settings, get_settings
from app.models import MEETING_SOURCES, Meeting, Template, User, UserSettings
from app.vexa import VexaClient, VexaDuplicateBot, VexaError, VexaSendUncertain

logger = logging.getLogger(__name__)

# Next to SIGNUP_LOCK (6002), ADMIN_LOCK (6003) and SCHEDULER_LOCK (6010). The first int of the
# two-int form; the second is the host id.
PLACEMENT_LOCK = 6004
LOCAL_HOST_ID = 1
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


class PlacementRefused(Exception):
    """No bot may be sent now. `code` is one of `hours_cap_reached`, `already_in_meeting`,
    `meeting_has_note_taker`, `user_limit`, `bot_busy`; the caller words it for its own reader."""

    def __init__(
        self, code: str, *, cap_seconds: int | None = None, limit: int | None = None
    ) -> None:
        super().__init__(code)
        self.code = code
        self.cap_seconds = cap_seconds  # `hours_cap_reached`: the user's cap
        self.limit = limit  # `user_limit`: how many bots a user may have


def _take_lock(session: Session) -> None:
    """Take the placement lock or answer `bot_busy` at once (never wait for it)."""
    got = session.scalar(
        text("select pg_try_advisory_xact_lock(cast(:key as int), cast(:host as int))"),
        {"key": PLACEMENT_LOCK, "host": LOCAL_HOST_ID},
    )
    if not got:
        raise PlacementRefused("bot_busy")


def reserve_slot(
    session: Session,
    user: User,
    *,
    platform: str,
    native_id: str,
    now: datetime,
    settings: Settings | None = None,
) -> None:
    """Take the placement lock and run the four pre-flight checks. Returns holding the lock, which
    lasts until the caller commits or rolls back; the caller must do that, and must send the bot
    before it commits. Raises `PlacementRefused` (the caller rolls back)."""
    settings = settings or get_settings()
    _take_lock(session)

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
) -> Meeting:
    """Send a bot to `req.meeting_url` and record the meeting, or raise `PlacementRefused`.

    Commits (the meeting row and the lock leave together). A `VexaError` from the send is raised
    after a rollback: no meeting exists and the slot is free again. If the send may have reached
    the host without an answer (`VexaSendUncertain`), or recording the meeting fails after a
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
        )
    except PlacementRefused:
        session.rollback()  # also releases the lock
        raise
    try:
        vexa_id = vexa.send_bot(req.meeting_url)
    except VexaDuplicateBot:  # a bot that is not ours is there (another host, a race)
        session.rollback()
        raise PlacementRefused("meeting_has_note_taker") from None
    except VexaSendUncertain:  # it may have been taken: stop it while the lock is still ours
        stop_unrecorded_bot(vexa, req.platform, req.native_id)
        session.rollback()
        raise
    except BaseException:  # the host said no, or anything else: no meeting, free the slot
        session.rollback()
        raise
    meeting = Meeting(
        owner_user_id=user.id,
        url=req.meeting_url,
        platform=req.platform,
        native_id=req.native_id,
        template_id=template_id,
        vexa_meeting_id=str(vexa_id),
        started_polling_at=now,
        source=req.source,
    )
    try:
        session.add(meeting)
        session.flush()
    except BaseException:
        stop_unrecorded_bot(vexa, req.platform, req.native_id)  # before the lock is let go
        session.rollback()
        raise
    commit_or_stop(session, vexa, platform=req.platform, native_id=req.native_id, vexa_id=vexa_id)
    return meeting


def commit_or_stop(
    session: Session, vexa: VexaClient, *, platform: str, native_id: str, vexa_id: int
) -> None:
    """Commit a meeting row that points at the bot `vexa_id`. If the commit fails, roll back and
    stop that bot unless the row did land after all (a lost answer to a commit that succeeded):
    that bot is a recorded one and stopping it would throw a meeting away."""
    try:
        session.commit()
    except BaseException:
        session.rollback()
        if not _is_recorded(session, platform, native_id, vexa_id):
            stop_unrecorded_bot(vexa, platform, native_id)
        raise


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
