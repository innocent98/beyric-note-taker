"""`place_bot()`: the one place a bot is sent to a meeting (spec section 9.4, plan task C11).

A pasted link, a shared link, and (from C12) a calendar join all come through here, so the rules
about how many bots may run, and for whom, exist once. This is the single-host form: one local Vexa
host, `bot_max_local` bots at a time. Phase D generalises it to several hosts (the second int of
the lock is the host id, always 1 until then).

Pre-flight, in this order, each refusing with a `PlacementRefused` code:

1. `hours_cap_reached`: the user has used their recorded hours (`app/usage.py`'s gate).
2. `user_limit`: the user already has `user_bot_limit` bots on meetings.
3. `already_in_meeting`: the user already has a bot in this meeting (same platform and code).
   Per user on purpose: a second account in the same meeting gets its own bot until C12 decides
   how sharing works, and one user can never block another's join by registering a link first.
4. `bot_busy`: the host is full. Says nothing about whose meeting holds it, and there is no queue:
   a queued meeting is an unrecorded meeting. The caller (a person, or the C12 runner) decides
   what to do with the refusal.

Whether the user's calendar connection is still good is the caller's check: a pasted link has no
connection.

Locking. The checks and the send run under `pg_advisory_xact_lock(PLACEMENT_LOCK, host)`, taken
first and held until the meeting row is committed (or the transaction is rolled back), so two
requests can never both read the one free slot. The lock is held across `send_bot` on purpose (a
send is bounded by the client's 30 s timeout), which also means a waiter waits at most that long
plus a margin (`LOCK_WAIT_SECONDS`) and then answers `bot_busy` instead of hanging.

A caller that holds a row lock must take it before calling in, never after: nothing here ever
waits for a row while holding the placement lock, which keeps the order row -> placement everywhere
and rules out a deadlock.
"""

import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app import usage
from app.config import Settings, get_settings
from app.models import MEETING_SOURCES, Meeting, Template, User, UserSettings
from app.vexa import VexaClient, VexaError

logger = logging.getLogger(__name__)

# Next to SIGNUP_LOCK (6002), ADMIN_LOCK (6003) and SCHEDULER_LOCK (6010). The first int of the
# two-int form; the second is the host id.
PLACEMENT_LOCK = 6004
LOCAL_HOST_ID = 1
# `VexaClient` gives a send 30 s. A request waits for the lock a little longer than that, so it
# gets through behind one slow send, and no longer: a stuck holder cannot hang the waiters.
LOCK_WAIT_SECONDS = 35.0
LOCK_NOT_AVAILABLE = "55P03"
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
    `user_limit`, `bot_busy`; the caller words it for its own reader."""

    def __init__(
        self, code: str, *, cap_seconds: int | None = None, limit: int | None = None
    ) -> None:
        super().__init__(code)
        self.code = code
        self.cap_seconds = cap_seconds  # `hours_cap_reached`: the user's cap
        self.limit = limit  # `user_limit`: how many bots a user may have


def _take_lock(session: Session) -> None:
    """Wait for the placement lock, at most `LOCK_WAIT_SECONDS`, else `bot_busy`."""
    try:
        session.execute(text(f"set local lock_timeout = '{int(LOCK_WAIT_SECONDS * 1000)}ms'"))
        session.execute(
            text("select pg_advisory_xact_lock(cast(:key as int), cast(:host as int))"),
            {"key": PLACEMENT_LOCK, "host": LOCAL_HOST_ID},
        )
    except OperationalError as exc:
        session.rollback()
        if getattr(exc.orig, "sqlstate", None) != LOCK_NOT_AVAILABLE:
            raise
        logger.warning("placement lock not free after %s s", LOCK_WAIT_SECONDS)
        raise PlacementRefused("bot_busy") from None


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
    after a rollback: no meeting exists and the slot is free again. If recording the meeting fails
    after the bot was sent, the bot is stopped, best effort, and the failure is raised."""
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
    except BaseException:  # a VexaError, or anything else: no meeting exists, free the slot
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
        session.commit()
    except BaseException:
        session.rollback()
        _stop_unrecorded_bot(vexa, req)
        raise
    return meeting


def _stop_unrecorded_bot(vexa: VexaClient, req: PlacementRequest) -> None:
    """The bot is in a meeting the database has no row for. Take it out; if that fails too the
    bot stays until Vexa's own limits end it, which is logged for an operator."""
    try:
        vexa.stop_bot(req.platform, req.native_id)
    except (VexaError, ValueError) as exc:
        logger.error(
            "a bot was sent but its meeting could not be saved, and it could not be stopped (%s)",
            type(exc).__name__,
        )
