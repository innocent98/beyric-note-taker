"""The join runner: sends the bot to calendar meetings (spec 7.6 to 7.8, plan task C12).

`run_due_joins` is a scheduler job on the dispatch loop (every 10 s). It takes each due
`scheduled_joins` row and either sends the bot through `placement.place_bot()` (the one place a bot
is sent: this module never repeats its pre-flight) or ends the row with a plain reason.

**When a row is due.** At `run_at` (the start minus `AUTOJOIN_LEAD_SECONDS`); a row whose time has
passed is due at once, so a scheduler that was down from 09:55 to 10:05 sends the 10:00 meeting's
bot at 10:05 (a late join) and never skips a meeting before its deadline (start plus 15 minutes).
Two minutes before the start the event is read again once (`rechecked_at`) through the calendar
source, so a move or a cancel made after the last sync is picked up before the bot goes.

**Nothing is sent that the owner no longer wants.** Under the row's lock, immediately before the
send, the runner re-reads the account (suspended), the calendar connection (disconnected, needs
reconnecting) and the join rule for the event (rule switched off, this meeting switched off,
declined, cancelled). Any of those ends the row (`skipped` or `cancelled`, with the reason) and no
bot is sent. The row lock is held from that check until the meeting row is committed, and the
sync, the settings and a disconnect all take the same row lock, so one of them landing "while
dispatching" waits for the send to finish rather than slipping between the check and the send.

**Exactly one bot.** The row is taken with `FOR UPDATE SKIP LOCKED` and its state is checked again
under the lock, so two runners (a restart, a second scheduler for a moment) cannot both send for one
row; `place_bot()`'s placement lock and one-bot-per-code rule cover two rows for one meeting. The
row is marked `joined` by `place_bot()`'s `on_placed` hook, in the same transaction as the meeting
row, so a crash never leaves a meeting its join does not know.

**There is no queue.** A full host (`bot_busy`) tells the owner at once (a notification and an
email) and the admins (an alert), the row becomes `no_capacity`, and the runner tries again every
15 s until the deadline. At the deadline the row ends `failed` as "not recorded" and the owner is
told again. A queued meeting is an unrecorded meeting, so nothing waits for a slot silently.

**Another account in the same meeting.** One bot per meeting code (Vexa has one API key): the first
placement wins. The other user's row ends `skipped` with the neutral reason, and a plain notice
that a note taker is already in that meeting. Nobody's bot is stopped or adopted.

Notifications go through `app/alerts.py` (once per row, or once per 30-minute bucket for the ones
that repeat), with kinds that start `calendar.` so a disconnect deletes them. Admin text names the
user and the time, never the meeting or its link. Event titles are cleaned (`app/titles.py`).

Connection state is `connection_state()`: C13 replaces its body with the full state machine.
"""

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app import alerts, calendar_providers, calendar_states, titles
from app.calendar_source import AuthRevoked, CalendarSource, CalendarSourceError, SyncBatch
from app.config import Settings, get_settings
from app.gcal_rules import JoinSettings, will_join
from app.gcal_sync import DEFAULT_USER_SETTINGS, apply_batch
from app.models import (
    LIVE_JOIN_STATES,
    AppState,
    CalendarConnection,
    CalendarEvent,
    Meeting,
    ScheduledJoin,
    User,
    UserSettings,
)
from app.placement import PlacementRefused, PlacementRequest, place_bot
from app.vexa import UnsupportedMeetingUrl, VexaClient, VexaError

logger = logging.getLogger("app.join_runner")

# A refused or failed send is tried again this often, until the deadline (spec 7.6).
RETRY_EVERY = timedelta(seconds=15)
# A row skipped for a reason that can clear (hours, own limit, a note taker already there) is
# looked at again this often: nothing is lost by waiting, and nobody needs the database every 10 s.
SKIP_RETRY_EVERY = timedelta(seconds=60)
# The event is read once more this long before it starts (spec 7.6).
RECHECK_BEFORE_START = timedelta(seconds=120)
# A row that missed its deadline longer ago than this (the runner was switched off, or the
# scheduler was down for hours) is closed without telling anyone again: the news is old.
STALE_AFTER = timedelta(hours=1)
# One run starts no new row after this long (real time), so a host that hangs on every send cannot
# hold the dispatch loop for minutes; the rest are due on the next tick. A row started just inside
# the budget can still do a calendar re-check (about 20 s), a send (30 s) and a stop (30 s): the
# scheduler's stop grace in compose.yaml (120 s) covers all of it.
RUN_BUDGET_SECONDS = 20.0
BATCH = 25

DUE_STATES = ("scheduled", "dispatching", "no_capacity", "skipped")
# A `skipped` row the runner looks at again: the reason can clear. Everything else is FINAL for the
# row: `account_suspended` and `needs_reconnect` (a person or C13's revive acts first),
# `meeting_has_note_taker` (another account's bot is in that meeting: it may be over a minute
# later, and a retry would send a bot into it), and `duplicate_meeting`: the owner already started
# a meeting on that code FOR THIS EVENT (see `_started_for_this_event`).
#
# `code_in_use` is the other case of "a bot of the owner's is on that code now": it was started for
# an EARLIER meeting in the same room (back-to-back sessions on one Zoom personal room or one Meet
# link). That one is retried until the deadline, because the earlier session ends and this one
# starts, and it ends "not recorded" if the earlier bot never leaves.
RETRYABLE_SKIPS = ("hours_cap", "user_limit", "code_in_use")
# Of those, these still end "not recorded" at the deadline; `hours_cap` ends `skipped` (the person
# was told why).
NOT_RECORDED_SKIPS = ("user_limit", "code_in_use")
# A meeting the owner PASTED (or started from instant-Meet: any meeting no join row points at) on
# the same code counts as made for this event when it was created from this long before the start
# until the event's end. It is told apart from an EARLIER calendar meeting in the same room by where
# it came from (a join row points at the calendar ones), never by the clock alone.
PASTE_WINDOW_BEFORE = timedelta(minutes=30)
CRASH_TEXT = "The note taker hit a problem starting for this meeting. Trying again."
CRASH_FINAL_TEXT = "Not recorded: something went wrong starting the note taker."

# What a person reads. No codes, no links, no passcode.
NOT_RECORDED_TEXT = "Not recorded: the note taker could not join before the 15 minutes were up."
NO_TEMPLATE_TEXT = "Not recorded: choose a notes template for calendar meetings, then try again."
UNSUPPORTED_TEXT = "Not recorded: the note taker cannot join this kind of meeting link yet."
SEND_FAILED_TEXT = "The meeting service did not take the note taker. Trying again."

CONNECTED, NEEDS_RECONNECT, DISCONNECTED = "connected", "needs_reconnect", "disconnected"
AGAIN, WAIT = "again", "wait"  # what a re-check tells the runner to do next


@dataclass(frozen=True)
class JoinOutcome:
    join_id: int
    result: str  # joined, no_capacity, skipped, cancelled, failed, rescheduled
    reason: str | None = None


def connection_state(session: Session, join: ScheduledJoin) -> str:
    """Whether the calendar connection behind a join is good: `connected`, `needs_reconnect` or
    `disconnected`. One function so C13 can swap in its state machine; today it reads the status
    column (`connected` and `degraded` both mean the calendar can be joined from, as the sync
    treats them)."""
    if join.source != "calendar":
        return CONNECTED  # an instant-Meet join has no calendar connection
    conn = _connection_of(session, join)
    if conn is None or conn.status == "disconnected":
        return DISCONNECTED
    if conn.status == "needs_reconnect":
        return NEEDS_RECONNECT
    return CONNECTED


def _connection_of(session: Session, join: ScheduledJoin) -> CalendarConnection | None:
    event = session.get(CalendarEvent, join.calendar_event_id) if join.calendar_event_id else None
    if event is not None:
        return session.get(CalendarConnection, event.connection_id)
    return session.scalars(
        select(CalendarConnection)
        .where(CalendarConnection.user_id == join.user_id)
        .order_by(CalendarConnection.id)
    ).first()


# ---- the run ----


def run_due_joins(
    session: Session,
    vexa: VexaClient,
    calendar: "calendar_providers.SourceRegistry | CalendarSource | None",
    *,
    now: datetime,
    settings: Settings | None = None,
) -> list[JoinOutcome]:
    """Handle every join that is due at `now`; returns what happened to each row it acted on.
    Each row is its own transaction. A failure on one row is logged by type and never stops the
    others. `calendar` is the process's source registry (a single source is accepted too); a join's
    re-check asks the source of its own connection's provider, and with none the two-minute
    re-check is skipped."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    calendar = calendar_providers.as_registry(calendar)
    settings = settings or get_settings()
    outcomes: list[JoinOutcome] = []
    started = time.monotonic()
    _baseline(session, now)
    for join_id in _due_ids(session, now):
        if time.monotonic() - started > RUN_BUDGET_SECONDS:
            break
        try:
            outcome = _process(session, vexa, calendar, join_id, now, settings)
        except Exception as exc:  # one row must not stop the rest; its text may hold a link
            session.rollback()
            logger.error("join %s could not be handled (%s)", join_id, type(exc).__name__)
            _note_crash(session, join_id, now)
            continue
        if outcome is not None:
            outcomes.append(outcome)
    return outcomes


def _note_crash(session: Session, join_id: int, now: datetime) -> None:
    """An unexpected error on a row. It counts as an attempt and waits out the retry gap (it used
    to be left untouched, so it came back on every 10 s tick, sending and stopping a bot each
    time); a second crash ends the row as `failed/error`. Never raises."""
    try:
        join = session.scalars(
            select(ScheduledJoin)
            .where(ScheduledJoin.id == join_id, ScheduledJoin.state.in_(DUE_STATES))
            .with_for_update(skip_locked=True)
        ).first()
        if join is None:
            session.rollback()
            return
        again = join.last_error == CRASH_TEXT
        join.attempts += 1
        _touch(join, now)
        if not again:
            join.last_error = CRASH_TEXT
        else:
            join.state, join.skip_reason, join.last_error = "failed", "error", CRASH_FINAL_TEXT
            user = session.get(User, join.user_id)
            alerts.notify_user(
                session,
                join.user_id,
                kind="calendar.error",
                severity="error",
                title="Meeting not recorded",
                body=f'"{_name(session, join)}" was not recorded: something went wrong starting '
                "the note taker.",
                dedupe_key=f"calendar.error:{join.id}",
            )
            alerts.notify_admins(
                session,
                kind="admin.join_error",
                title="The join runner hit an error",
                body=f"A join for {user.email if user else 'a user'} failed twice with an "
                f"unexpected error at {now:%H:%M} UTC. See the scheduler log.",
                dedupe_key=alerts.bucket_key("admin.join_error", now),
            )
        session.commit()
    except Exception as exc:
        session.rollback()
        logger.error("join %s: could not record the error (%s)", join_id, type(exc).__name__)


def _baseline(session: Session, now: datetime) -> None:
    """Close, silently, the joins nobody should dispatch now (spec: no retroactive joins).

    * The first time the runner leads (`app_state.autojoin_started_at` unset): every join whose
      `run_at` is before now. They were synced before the feature existed; a meeting that started
      before the deploy is not joined late, and nobody is told "not recorded" for it.
    * When the operator's switch is turned back on (`autojoin_paused_since` set by the scheduler
      while it was off): every join whose deadline passed meanwhile.

    Only rows due after that dispatch. A closed row is `cancelled` with reason `before_runner`."""
    state = session.get(AppState, 1)
    if state is None:
        state = AppState(id=1)
        session.add(state)
        session.flush()
    first = state.autojoin_started_at is None
    if not first and state.autojoin_paused_since is None:
        session.rollback()
        return
    closed = (ScheduledJoin.run_at < now) if first else (ScheduledJoin.deadline_at < now)
    session.execute(
        update(ScheduledJoin)
        .where(ScheduledJoin.state.in_(DUE_STATES), closed)
        .values(state="cancelled", skip_reason="before_runner", updated_at=now)
    )
    if first:
        state.autojoin_started_at = now
    state.autojoin_paused_since = None
    session.commit()


def note_switch(session: Session, settings: Settings, now: datetime) -> None:
    """Called by the scheduler at start: remember since when auto-join has been off (the operator's
    switch, or the calendar itself off), once; `_baseline` uses it when it comes back on."""
    if settings.calendar_enabled and settings.autojoin_enabled:
        session.rollback()
        return
    state = session.get(AppState, 1)
    if state is None:
        state = AppState(id=1)
        session.add(state)
    if state.autojoin_paused_since is None:
        state.autojoin_paused_since = now
    session.commit()


def _due_conditions(now: datetime):
    """The rows that need the runner at `now`, as a where clause (used to find them and again,
    under the row lock, to be sure they still do)."""
    recheck_due = and_(
        ScheduledJoin.state == "scheduled",
        ScheduledJoin.rechecked_at.is_(None),
        ScheduledJoin.starts_at <= now + RECHECK_BEFORE_START,
    )
    skipped_alive = and_(
        ScheduledJoin.skip_reason.in_(RETRYABLE_SKIPS),
        or_(
            ScheduledJoin.deadline_at >= now,
            ScheduledJoin.skip_reason.in_(NOT_RECORDED_SKIPS),
        ),
    )
    # A row that has been tried waits out its gap IN THE QUERY: rows waiting must not come back
    # first on every tick and fill the batch, starving rows that became due later.
    gap_over = or_(
        ScheduledJoin.attempts == 0,
        ScheduledJoin.deadline_at < now,  # the end of a row never waits for a gap
        and_(ScheduledJoin.state != "skipped", ScheduledJoin.updated_at <= now - RETRY_EVERY),
        and_(ScheduledJoin.state == "skipped", ScheduledJoin.updated_at <= now - SKIP_RETRY_EVERY),
    )
    return and_(
        ScheduledJoin.state.in_(DUE_STATES),
        or_(ScheduledJoin.run_at <= now, recheck_due),
        or_(ScheduledJoin.state != "skipped", skipped_alive),
        gap_over,
    )


def _due_ids(session: Session, now: datetime) -> list[int]:
    ids = list(
        session.scalars(
            select(ScheduledJoin.id)
            .where(_due_conditions(now))
            .order_by(ScheduledJoin.run_at, ScheduledJoin.id)
            .limit(BATCH)
        )
    )
    session.rollback()  # the read must not hold a snapshot while the rows are worked one by one
    return ids


def _lock(session: Session, join_id: int, now: datetime) -> ScheduledJoin | None:
    """The row, under a lock another runner skips, and only if it is still due: another process
    may have moved, cancelled or sent it since the ids were read.

    The event row is locked FIRST, then the join: the override and rule routes take them in that
    order (event, then join), and the re-check's `apply_batch` takes the join and then writes the
    event, so a runner that took the join first could meet a route holding the event and wanting
    the join (AB/BA). An event somebody else holds is skipped, not waited for: next tick."""
    event_id = session.scalar(
        select(ScheduledJoin.calendar_event_id).where(ScheduledJoin.id == join_id)
    )
    if event_id is not None:
        held = session.scalar(
            select(CalendarEvent.id)
            .where(CalendarEvent.id == event_id)
            .with_for_update(skip_locked=True)
        )
        if held is None:
            session.rollback()
            return None
    return session.scalars(
        select(ScheduledJoin)
        .where(ScheduledJoin.id == join_id, _due_conditions(now))
        .with_for_update(skip_locked=True)
        .execution_options(populate_existing=True)
    ).first()


def _process(
    session: Session,
    vexa: VexaClient,
    calendar: "calendar_providers.SourceRegistry",
    join_id: int,
    now: datetime,
    settings: Settings,
) -> JoinOutcome | None:
    # `expire_on_commit` is off for the app's sessions, so what an earlier row of this run loaded
    # (the account, the connection, the rule) could be a minute old: read it again.
    session.expire_all()
    for attempt in range(2):  # a re-check rewrites the row; the second pass judges the new row
        join = _lock(session, join_id, now)
        if join is None:
            session.rollback()
            return _what_the_recheck_did(session, join_id) if attempt else None
        stop = _gate(session, join, now, settings)
        if stop is not None:
            return stop
        if _recheck_due(join, now):
            answer = _recheck(session, vexa, calendar, join, now, settings)
            if answer == WAIT:  # the calendar could not be asked and the run time is not here yet
                return None
            continue  # the row may have been moved or cancelled: look at it again
        if join.run_at > now:  # only a re-check made it due, and it has been done
            session.rollback()
            return None
        return _place(session, vexa, join, now, settings)
    session.rollback()  # pragma: no cover - the second pass never re-checks
    return None


def _what_the_recheck_did(session: Session, join_id: int) -> JoinOutcome | None:
    """The re-check moved or ended the row, so it is no longer due: say how."""
    row = session.get(ScheduledJoin, join_id)
    session.rollback()
    if row is None or row.state in ("joined", "completed"):
        return None
    if row.state == "scheduled":
        return JoinOutcome(join_id, "rescheduled")
    return JoinOutcome(join_id, row.state, row.skip_reason)


# ---- what must still be true before a bot goes ----


def _gate(session: Session, join: ScheduledJoin, now: datetime, settings: Settings):
    """The checks that end a row instead of sending a bot. None when the row may go on."""
    user = session.get(User, join.user_id)
    if user is None or user.status == "deleted":
        return _end(session, join, "cancelled", "account_deleted", now)
    if user.status != "active":
        return _end(session, join, "skipped", "account_suspended", now)
    state = connection_state(session, join)
    if state == DISCONNECTED:
        return _end(session, join, "cancelled", "disconnected", now)
    if state == NEEDS_RECONNECT:
        return _end(session, join, "skipped", "needs_reconnect", now)
    event = session.get(CalendarEvent, join.calendar_event_id) if join.calendar_event_id else None
    ended = False
    if event is not None:
        rule = (session.get(UserSettings, join.user_id) or DEFAULT_USER_SETTINGS).auto_join_rule
        decision = will_join(
            event,
            event.override,
            JoinSettings(rule=rule, teams_join_enabled=settings.teams_join_enabled),
            now=now,
        )
        if decision.reason == "ended":
            ended = True
        elif not decision.join:
            return _end(session, join, "cancelled", decision.reason, now)
    if now > join.deadline_at or ended:
        return _expire(session, join, user, now)
    return None


def _touch(row: ScheduledJoin, now: datetime) -> None:
    """Stamp `updated_at` with the runner's clock (the retry gap reads it). Flagged as modified so
    the stamp is written even when it equals the value already there: an unchanged attribute
    would be left out of the UPDATE and the column's `onupdate=now()` would put the database's
    real time in its place."""
    row.updated_at = now
    flag_modified(row, "updated_at")


def _end(
    session: Session, join: ScheduledJoin, state: str, reason: str, now: datetime
) -> JoinOutcome:
    join.state, join.skip_reason = state, reason
    _touch(join, now)
    session.commit()
    return JoinOutcome(join.id, state, reason)


def _expire(session: Session, join: ScheduledJoin, user: User, now: datetime) -> JoinOutcome | None:
    """The deadline has passed. A skip whose cause was already told stays `skipped`; anything else
    ends `failed` as not recorded, and the owner is told (unless the news is old)."""
    if join.state == "skipped" and join.skip_reason not in NOT_RECORDED_SKIPS:
        _touch(join, now)  # an ended event: look again after the retry gap, not on every tick
        session.commit()
        return None
    if _owner_bot_live_on_the_code(session, join):
        # The owner's own bot is in that room right now (an earlier session that never left, or a
        # paste): the room is being recorded, so no "not recorded" mail and no admin alert.
        join.state, join.skip_reason = "skipped", "duplicate_meeting"
        _touch(join, now)
        # One closing word in the bell (no mail, no admin alert), so a row that said "busy" or
        # "Will join" is not left hanging: this meeting's audio is in the earlier meeting's notes.
        alerts.notify_user(
            session,
            join.user_id,
            kind="calendar.in_earlier_notes",
            severity="info",
            title="Recorded in the earlier meeting",
            body=f'"{_name(session, join)}" shares a meeting link with a meeting your note taker '
            "was still in, so it is part of that meeting's notes, not a separate one.",
            dedupe_key=f"calendar.in_earlier_notes:{join.id}",
        )
        session.commit()
        return JoinOutcome(join.id, "skipped", "duplicate_meeting")
    join.state, join.skip_reason, join.last_error = "failed", "not_recorded", NOT_RECORDED_TEXT
    _touch(join, now)
    if now - join.deadline_at <= STALE_AFTER:
        name = _name(session, join)
        alerts.notify_user(
            session,
            join.user_id,
            kind="calendar.not_recorded",
            severity="error",
            title="Meeting not recorded",
            body=f'The note taker could not join "{name}" before the 15 minutes were up, '
            "so this meeting was not recorded.",
            dedupe_key=f"calendar.not_recorded:{join.id}",
            email_template=alerts.ALERT_TEMPLATE,
        )
        alerts.notify_admins(
            session,
            kind="admin.not_recorded",
            title="A meeting could not be recorded",
            body=f"A meeting for {user.email} was not recorded: the note taker could not join "
            f"by {join.deadline_at:%H:%M} UTC.",
            dedupe_key=alerts.bucket_key("admin.not_recorded", now),
        )
    session.commit()
    return JoinOutcome(join.id, "failed", "not_recorded")


def _name(session: Session, join: ScheduledJoin) -> str:
    event = session.get(CalendarEvent, join.calendar_event_id) if join.calendar_event_id else None
    return titles.alert_name(event.title if event else None, join.platform, join.native_id)


# ---- the two-minute re-check ----


def _recheck_due(join: ScheduledJoin, now: datetime) -> bool:
    return (
        join.state == "scheduled"
        and join.rechecked_at is None
        and join.calendar_event_id is not None
        and join.starts_at <= now + RECHECK_BEFORE_START
    )


def _recheck(
    session: Session,
    vexa: VexaClient,
    calendar: "calendar_providers.SourceRegistry",
    join: ScheduledJoin,
    now: datetime,
    settings: Settings,
) -> str:
    """Ask the calendar for the event once, just before it starts, and apply what comes back (a
    move, a cancel, a decline) through the same code the sync uses. Always ends the transaction
    (which drops the row lock), so the caller takes the row again (`AGAIN`), except `WAIT`: the
    calendar could not be asked and the run time is not here yet, so the next tick asks again. A
    calendar that cannot be asked never holds a join back: once the run time comes the bot goes on
    what is stored."""
    event = session.get(CalendarEvent, join.calendar_event_id)
    conn = session.get(CalendarConnection, event.connection_id) if event else None
    source = calendar.get(conn.provider) if conn is not None else None
    if event is None or source is None:
        join.rechecked_at = now  # nothing to ask: do not look again
        _touch(join, now)
        session.commit()
        return AGAIN
    try:
        raw = source.get_event(conn, event.source_event_id)
    except ValueError as exc:  # an event id the source refuses will never work: stop asking
        logger.warning("re-check of join %s refused (%s)", join.id, type(exc).__name__)
        join.rechecked_at = now
        _touch(join, now)
        session.commit()
        return AGAIN
    except CalendarSourceError as exc:
        logger.warning("re-check of join %s failed (%s)", join.id, type(exc).__name__)
        if isinstance(exc, AuthRevoked):
            # A revoked grant is a state change, not a blip: the next tick's gate skips the row.
            calendar_states.on_sync_failure(session, conn, exc, now, settings=settings)
            session.commit()
            return WAIT
        if now >= join.run_at:
            join.rechecked_at = now  # time is up: go on what is stored
            _touch(join, now)
            session.commit()
            return AGAIN
        session.rollback()
        return WAIT
    if raw is None:  # the source no longer has it: cancelled or deleted
        raw = {"id": event.source_event_id, "status": "cancelled"}
    join.rechecked_at = now  # committed by apply_batch with the rest
    _touch(join, now)
    user_settings = session.get(UserSettings, join.user_id) or DEFAULT_USER_SETTINGS
    try:
        apply_batch(
            session,
            conn,
            SyncBatch(raw_events=[raw], next_sync_token=None, complete=True, full=False),
            user_settings,
            now=now,
            settings=settings,
            vexa=vexa,
        )
    except Exception as exc:
        session.rollback()
        logger.warning("re-check of join %s could not be applied (%s)", join.id, type(exc).__name__)
        return AGAIN
    return AGAIN


# ---- sending ----


def _place(
    session: Session, vexa: VexaClient, join: ScheduledJoin, now: datetime, settings: Settings
) -> JoinOutcome | None:
    """`place_bot()` under the row lock (the retry gap is already in the due query). The row lock
    was taken before the
    placement lock (the order every code path uses), and `wait_seconds=0` keeps it: a wait would
    roll the lock away."""
    paste_id = _paste_for_this_event(session, join)
    if paste_id is not None:
        # The owner already made a meeting on this code for this event (a pasted link, in any
        # status: they may have ended it since). Final: never a bot into a meeting they chose or
        # ended. Checked BEFORE the placement pre-flight, so the hours and limit checks cannot turn
        # it into a retry. The row remembers WHICH meeting (`meeting_id`), so a later event on the
        # same code sees that paste as taken and cannot claim it.
        join.meeting_id = paste_id
        return _end(session, join, "skipped", "duplicate_meeting", now)
    if _another_live_join_on_the_code(session, join):
        # Another of the owner's rows on this code is live (back-to-back slots in one room): this
        # one waits; marking it `joined` or `no_capacity` too would break the one-live-join index.
        return _wait_for_the_code(session, join, now)
    event = session.get(CalendarEvent, join.calendar_event_id) if join.calendar_event_id else None
    request = PlacementRequest(
        user_id=join.user_id,
        platform=join.platform,
        native_id=join.native_id,
        meeting_url=join.meeting_url,
        template_id=join.template_id,
        source="calendar",
        title=event.title if event else None,
        adopt_orphan=True,
    )
    join_id, user_id = join.id, join.user_id

    def mark_joined(inner: Session, meeting: Meeting) -> None:
        # Inside place_bot's transaction: the meeting row and this update commit together.
        row = inner.get(ScheduledJoin, join_id)
        row.state, row.meeting_id, row.skip_reason, row.last_error = (
            "joined",
            meeting.id,
            None,
            None,
        )
        row.attempts = row.attempts + 1
        _touch(row, now)

    try:
        meeting = place_bot(
            session,
            vexa,
            request,
            now=now,
            settings=settings,
            wait_seconds=0,
            on_placed=mark_joined,
        )
    except PlacementRefused as refused:
        return _refused(session, join_id, user_id, refused, now)
    except IntegrityError:
        # Two runners racing on the one-live-join-per-code index: place_bot stopped the bot and
        # rolled back. A retry after the gap, never the error path and its mail.
        session.rollback()
        return _wait_after_a_conflict(session, join_id, now)
    except VexaError as exc:
        logger.warning("sending the bot for join %s failed (%s)", join_id, type(exc).__name__)
        return _send_failed(session, join_id, now)
    except UnsupportedMeetingUrl:  # a link the host cannot join (Teams until T1): retrying is vain
        return _fail(session, join_id, now, "unsupported_link", UNSUPPORTED_TEXT)
    except ValueError as exc:  # a caller-level problem (no template): retrying cannot fix it
        logger.error("join %s cannot be placed (%s)", join_id, type(exc).__name__)
        return _fail(session, join_id, now, "no_template", NO_TEMPLATE_TEXT)
    return JoinOutcome(join_id, "joined", f"meeting {meeting.id}")


def _paste_for_this_event(session: Session, join: ScheduledJoin) -> int | None:
    """The id of the owner's meeting, in ANY status, on this code that no join row points at (a
    pasted link or an instant-Meet one) created between `PASTE_WINDOW_BEFORE` before the start and
    the event's end, and not before the end of the owner's previous event on the same code; or
    None. A meeting a join row points at belongs to a calendar event (an earlier one in the same
    room, or an earlier event the owner pasted for: see `_place`) and does not count."""
    since = join.starts_at - PASTE_WINDOW_BEFORE
    previous_end = session.scalar(
        select(func.max(CalendarEvent.ends_at)).where(
            CalendarEvent.user_id == join.user_id,
            CalendarEvent.platform == join.platform,
            CalendarEvent.native_id == join.native_id,
            CalendarEvent.ends_at <= join.starts_at,
        )
    )
    if previous_end is not None and previous_end > since:
        since = previous_end
    linked = select(ScheduledJoin.id).where(ScheduledJoin.meeting_id == Meeting.id).exists()
    return session.scalar(
        select(Meeting.id)
        .where(
            Meeting.owner_user_id == join.user_id,
            Meeting.platform == join.platform,
            Meeting.native_id == join.native_id,
            ~linked,
            Meeting.created_at >= since,
            Meeting.created_at <= join.ends_at,
        )
        .order_by(Meeting.id)
        .limit(1)
    )


def _owner_bot_live_on_the_code(session: Session, join: ScheduledJoin) -> bool:
    return (
        session.scalar(
            select(Meeting.id)
            .where(
                Meeting.owner_user_id == join.user_id,
                Meeting.platform == join.platform,
                Meeting.native_id == join.native_id,
                Meeting.status == "bot_sent",
            )
            .limit(1)
        )
        is not None
    )


def _wait_after_a_conflict(session: Session, join_id: int, now: datetime) -> JoinOutcome | None:
    join = _relock(session, join_id, now)
    if join is None:
        session.rollback()
        return None
    return _wait_for_the_code(session, join, now)


def _wait_for_the_code(session: Session, join: ScheduledJoin, now: datetime) -> JoinOutcome:
    """`skipped/code_in_use`: retried after the gap until the deadline."""
    join.attempts += 1
    join.state, join.skip_reason = "skipped", "code_in_use"
    _touch(join, now)
    session.commit()
    return JoinOutcome(join.id, "skipped", "code_in_use")


def _relock(session: Session, join_id: int, now: datetime) -> ScheduledJoin | None:
    """`place_bot` rolled back (and with it the row lock): take the row again before writing what
    happened, and only if it is still one the runner should touch. If somebody cancelled or moved it
    meanwhile, their change stands and nothing is written."""
    return _lock(session, join_id, now)


def _refused(
    session: Session, join_id: int, user_id: int, refused: PlacementRefused, now: datetime
) -> JoinOutcome | None:
    code = refused.code
    if code == "bot_busy" and refused.slot_locked:
        session.rollback()  # another send is in flight: the next tick looks again, no alarm
        return None
    join = _relock(session, join_id, now)
    if join is None:
        session.rollback()
        return None
    # Anything that queries (and so autoflushes) comes BEFORE the row is changed, and every column
    # it changes is then set together: a second UPDATE without `updated_at` would let the database
    # overwrite it, and the retry gap reads it.
    crowded = code == "bot_busy" and _another_live_join_on_the_code(session, join)
    join.attempts += 1
    _touch(join, now)
    reason = _SKIP_REASONS.get(code, "refused")
    if crowded:
        # Two of the owner's rows on one code inside their windows (back-to-back slots in one
        # room): `no_capacity` is a live state and the code may be live only once per user.
        code, reason = "already_in_meeting", "code_in_use"
    if code == "bot_busy":
        join.state, join.skip_reason = "no_capacity", None
    else:
        join.state, join.skip_reason = "skipped", reason
    name = _name(session, join)
    user = session.get(User, user_id)
    who = user.email if user else "a user"
    if code == "bot_busy":
        alerts.notify_user(
            session,
            user_id,
            kind="calendar.no_capacity",
            severity="warning",
            title="The note taker is busy",
            body=f'The note taker is in another meeting and could not join "{name}" yet. It will '
            f"keep trying until {join.deadline_at:%H:%M} UTC. If it cannot get in by then, this "
            "meeting will not be recorded.",
            dedupe_key=alerts.bucket_key(f"calendar.no_capacity.{join.id}", now),
            email_template=alerts.ALERT_TEMPLATE,
        )
        alerts.notify_admins(
            session,
            kind="admin.no_capacity",
            title="No room for the note taker",
            body=f"A meeting for {who} could not get a bot at {now:%H:%M} UTC: the host is full.",
            dedupe_key=alerts.bucket_key("admin.no_capacity", now),
        )
        result, reason = "no_capacity", None
    else:
        notice = _NOTICES.get(reason)
        if notice is not None:
            title, body = notice
            alerts.notify_user(
                session,
                user_id,
                kind=f"calendar.{reason}",
                severity="warning",
                title=title,
                body=body.format(name=name),
                dedupe_key=f"calendar.{reason}:{join.id}",
            )
        result = "skipped"
    session.commit()
    return JoinOutcome(join_id, result, reason)


_SKIP_REASONS = {
    "hours_cap_reached": "hours_cap",
    "already_in_meeting": "code_in_use",
    "code_busy": "code_in_use",
    "meeting_has_note_taker": "meeting_has_note_taker",
    "user_limit": "user_limit",
    "account_suspended": "account_suspended",
}

# A plain notice for each reason that tells the owner something. `duplicate_meeting` has none:
# the owner's own bot is already in that meeting (they pasted its link), nothing is lost.
_NOTICES = {
    "hours_cap": (
        "Recording hours used up",
        'Your recording hours are used up, so the note taker did not join "{name}".',
    ),
    "user_limit": (
        "Too many note takers at once",
        "You already have the most note takers allowed at once, so the note taker did not "
        'join "{name}".',
    ),
    "meeting_has_note_taker": (
        "A note taker is already there",
        'A note taker is already in "{name}", so yours did not join. Sharing notes between '
        "accounts is not available yet.",
    ),
}


def _another_live_join_on_the_code(session: Session, join: ScheduledJoin) -> bool:
    return (
        session.scalar(
            select(ScheduledJoin.id)
            .where(
                ScheduledJoin.user_id == join.user_id,
                ScheduledJoin.platform == join.platform,
                ScheduledJoin.native_id == join.native_id,
                ScheduledJoin.state.in_(LIVE_JOIN_STATES),
                ScheduledJoin.id != join.id,
            )
            .limit(1)
        )
        is not None
    )


def _send_failed(session: Session, join_id: int, now: datetime) -> JoinOutcome | None:
    join = _relock(session, join_id, now)
    if join is None:
        session.rollback()
        return None
    join.attempts, join.last_error = join.attempts + 1, SEND_FAILED_TEXT
    _touch(join, now)
    user = session.get(User, join.user_id)
    name = _name(session, join)
    alerts.notify_user(
        session,
        join.user_id,
        kind="calendar.join_failed",
        severity="warning",
        title="The note taker could not start",
        body=f'The note taker could not start for "{name}". It will try again until '
        f"{join.deadline_at:%H:%M} UTC.",
        dedupe_key=alerts.bucket_key(f"calendar.join_failed.{join.id}", now),
    )
    alerts.notify_admins(
        session,
        kind="admin.join_failed",
        title="The bot host refused a bot",
        body=f"Sending a bot for {user.email if user else 'a user'} failed at {now:%H:%M} UTC.",
        dedupe_key=alerts.bucket_key("admin.join_failed", now),
    )
    session.commit()
    return JoinOutcome(join_id, "scheduled", "send_failed")


def _fail(
    session: Session, join_id: int, now: datetime, reason: str, text: str
) -> JoinOutcome | None:
    """End the row `failed` for a reason retrying cannot clear, and tell the owner."""
    join = _relock(session, join_id, now)
    if join is None:
        session.rollback()
        return None
    join.state, join.skip_reason, join.last_error = "failed", reason, text
    _touch(join, now)
    alerts.notify_user(
        session,
        join.user_id,
        kind=f"calendar.{reason}",
        severity="error",
        title="Meeting not recorded",
        body=f'"{_name(session, join)}" was not recorded. {text.removeprefix("Not recorded: ")}',
        dedupe_key=f"calendar.{reason}:{join.id}",
    )
    session.commit()
    return JoinOutcome(join_id, "failed", reason)


# ---- registration ----


def register_join_runner(
    scheduler,
    settings: Settings | None = None,
    *,
    source: "calendar_providers.SourceRegistry | CalendarSource | None" = None,
    vexa: VexaClient | None = None,
) -> bool:
    """Put the runner on the scheduler's dispatch loop (every tick, 10 s). Not registered while
    `AUTOJOIN_ENABLED` is false (the operator's emergency stop). The scheduler process shares its
    source registry with the sync job, so the two share each provider's access-token cache."""
    settings = settings or get_settings()
    if not settings.autojoin_enabled:
        return False
    vexa = vexa or VexaClient()
    scheduler.register(
        "join_runner",
        1,
        lambda session, now: run_due_joins(session, vexa, source, now=now, settings=settings),
        loop="dispatch",
    )
    return True
