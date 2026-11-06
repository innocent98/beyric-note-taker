"""Calendar events to rows: the materialiser (C8) and the sync job (C8b).

`sync_connection` reads one connection's changes (a full listing the first time, every 6 hours and
after a 410) and hands them to `apply_batch`. The sync token is written only after `apply_batch`
committed a COMPLETE batch, so a failure at any point means the same changes are read again next
time, which is harmless because applying is idempotent. `sync_due_connections` is the loop job and
`register_calendar_sync` puts it on the scheduler's sync loop, only while `CALENDAR_ENABLED`.

`apply_batch` turns a `SyncBatch` of raw events into `calendar_events` rows and, for the events the
join rule says to join, `scheduled_joins` rows (spec 7.7, 7.8). It is **idempotent**: applying the
same batch again changes nothing, which is what lets a crashed or repeated sync be harmless.

What it stores and what it never does:

* Per-user by construction. Every row it writes carries `conn.user_id` and every query it makes is
  filtered by the connection and that user; nothing in an event (an attendee list, an extra field)
  can name another user.
* `ParsedEvent` has no description, location or attendee address, so none can be stored here.
* A batch that is not `complete` (a page failed) is applied as additions and updates only: no
  event is cancelled, because absence from half a calendar proves nothing, and a cancellation
  stub that came with it is applied when the same changes come again with the token unmoved.
* Only a complete full listing cancels events that are missing from it, and only for events that
  overlap the listed window and only before a bot was sent.
* A join whose bot is already sent follows the meeting (spec 7.7), not the calendar: a move is
  ignored, and only an explicit cancellation from the calendar stops the bot.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app import calendar_providers, calendar_states
from app.calendar_source import CalendarSource, CalendarSourceError, SyncBatch, SyncTokenExpired
from app.config import Settings, get_settings
from app.gcal_client import WINDOW_AFTER, WINDOW_BEFORE
from app.gcal_events import NO_TIME, ParsedEvent
from app.gcal_rules import JoinSettings, will_join
from app.models import (
    AppState,
    CalendarConnection,
    CalendarEvent,
    Meeting,
    ScheduledJoin,
    Template,
    User,
    UserSettings,
)
from app.services import Refused, cancel_meeting
from app.vexa import VexaClient

logger = logging.getLogger("app.gcal_sync")

# Spec 7.4: a sync per connection every 5 minutes, and a full listing every 6 hours (a sync token
# cannot be combined with a time window, so only a full listing slides the window forward).
SYNC_EVERY = timedelta(minutes=5)
FULL_RESYNC_EVERY = timedelta(hours=6)
# A connection in one of these states is synced; `needs_reconnect` and `disconnected` are not.
SYNCABLE = ("connected", "degraded")
# How many connections one run of the job takes: the pilot has about ten users, this only stops a
# backlog (after downtime) from holding the sync thread for minutes.
MAX_PER_RUN = 50
ERROR_LIMIT = 300
# What a user without a settings row gets (sign-up creates one, so this is a safety net).
DEFAULT_USER_SETTINGS = SimpleNamespace(auto_join_rule="all", calendar_template_id=None)

# The bot keeps trying to get in until this long after the start (spec 7.6).
DEADLINE_AFTER_START = timedelta(minutes=15)
# Titles are the user's own words but not bounded by Google; the Upcoming list needs a line, not a
# page.
TITLE_LIMIT = 1000
ID_LIMIT = 1024  # `calendar_events.source_event_id` and `recurring_event_id`
NATIVE_ID_LIMIT = 200  # `calendar_events.native_id`

# Where a join still can follow its event. `scheduled` is waiting; `no_capacity` is retrying until
# the deadline with no bot sent, so its times may move.
MOVABLE = ("scheduled", "no_capacity")
# A skipped row the runner is still retrying (the reason can clear) follows its event too: moved
# later, it goes back to waiting for the new time instead of ending "not recorded" at the old
# deadline. (The reasons are the join runner's `RETRYABLE_SKIPS`.)
MOVABLE_SKIPS = ("hours_cap", "user_limit", "code_in_use")
# No bot has been sent from these, so the calendar may cancel them. (`dispatching` is cancelled
# too: the runner re-reads the state before it sends, spec 7.7 and plan C12.)
CANCELLABLE = ("scheduled", "dispatching", "no_capacity", "skipped")


@dataclass
class ApplyResult:
    """How many join rows a batch made, moved, cancelled and revived; `unreadable` counts events
    skipped because they could not be parsed or would not fit the tables."""

    created: int = 0
    moved: int = 0
    cancelled: int = 0
    revived: int = 0
    unreadable: int = 0


@dataclass
class _Run:
    """What one `apply_batch` call is doing, so the helpers do not need ten arguments."""

    session: Session
    conn: CalendarConnection
    now: datetime
    lead: timedelta
    join_settings: JoinSettings
    template_id: int | None
    result: ApplyResult = field(default_factory=ApplyResult)
    # Joins whose bot is already sent and whose event was cancelled: stopped after the commit,
    # because `cancel_meeting` commits and talks to Vexa.
    to_stop: list[int] = field(default_factory=list)


def _clean(text: str) -> str:
    """Postgres refuses a NUL in text, and one hostile title must not fail a whole batch."""
    return text.replace("\x00", "")


def _fits(event: ParsedEvent) -> bool:
    texts = (event.source_event_id, event.recurring_event_id or "", event.meeting_url or "")
    return (
        len(event.source_event_id) <= ID_LIMIT
        and len(event.recurring_event_id or "") <= ID_LIMIT
        and len(event.native_id or "") <= NATIVE_ID_LIMIT
        and not any("\x00" in text for text in texts)
        and "\x00" not in (event.native_id or "")
    )


def _own_template(session: Session, user_id: int, template_id: int | None) -> int | None:
    """The template to record with, only if it is the user's own."""
    if template_id is None:
        return None
    owner = session.scalar(select(Template.owner_user_id).where(Template.id == template_id))
    return template_id if owner == user_id else None


def _parse_all(batch: SyncBatch, conn: CalendarConnection, result: ApplyResult):
    """(the last readable version of each event, in first-seen order; every id the batch named).

    Naming an event, even one that cannot be read, means the source still has it: the full-sync
    sweep must not treat an unreadable event as a missing one.
    """
    named: set[str] = set()
    latest: dict[str, ParsedEvent] = {}
    for raw in batch.raw_events:
        raw_id = raw.get("id") if isinstance(raw, dict) else None
        if isinstance(raw_id, str):
            named.add(raw_id)
        try:
            event = calendar_providers.parse(raw, conn)
        except Exception as exc:  # one odd event must not stall a user's whole calendar
            result.unreadable += 1
            logger.warning("skipping an unreadable calendar event (%s)", type(exc).__name__)
            continue
        if not _fits(event):
            result.unreadable += 1
            continue
        latest[event.source_event_id] = event
    return latest, named


def apply_batch(
    session: Session,
    conn: CalendarConnection,
    batch: SyncBatch,
    user_settings,
    *,
    now: datetime,
    settings: Settings | None = None,
    vexa: VexaClient | None = None,
) -> ApplyResult:
    """Apply `batch` for `conn` and commit. `user_settings` supplies `auto_join_rule` and
    `calendar_template_id`. `vexa` is only needed (and only built when absent) to stop a bot whose
    event was cancelled."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    settings = settings or get_settings()
    run = _Run(
        session=session,
        conn=conn,
        now=now,
        lead=timedelta(seconds=settings.autojoin_lead_seconds),
        join_settings=JoinSettings(
            rule=user_settings.auto_join_rule, teams_join_enabled=settings.teams_join_enabled
        ),
        template_id=_own_template(session, conn.user_id, user_settings.calendar_template_id),
    )
    latest, named = _parse_all(batch, conn, run.result)

    existing = _events_by_source_id(session, conn, list(latest))
    joins = _joins_by_event_id(session, conn.user_id, [e.id for e in existing.values()])
    for parsed in latest.values():
        row = existing.get(parsed.source_event_id)
        if parsed.status == "cancelled":
            if batch.complete and row is not None:
                _cancel_event(run, row, parsed, joins.get(row.id))
            continue
        if row is None:
            row = CalendarEvent(
                user_id=conn.user_id, connection_id=conn.id, source_event_id=parsed.source_event_id
            )
            session.add(row)
        _copy_event(row, parsed, now)
        if row.id is None:
            session.flush()
        _reconcile(run, row, joins.get(row.id))

    if batch.complete and batch.full:
        _cancel_missing(run, named)
    session.commit()
    _stop_cancelled_bots(run, vexa)
    return run.result


def rerun_rules(
    session: Session,
    conn: CalendarConnection,
    user_settings,
    events: list[CalendarEvent],
    *,
    now: datetime,
    settings: Settings | None = None,
) -> ApplyResult:
    """Bring the joins of these stored events in line with the rule, the person's switches and
    the template as they are now, without reading the source, and commit. This is what a rule
    change or a per-meeting switch from the API calls (spec 7.7): the same `_reconcile` the sync
    uses, so the rule logic is in one place. `events` must be this connection's own rows; they are
    reconciled as they stand, so the caller sets `override` first."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    settings = settings or get_settings()
    run = _Run(
        session=session,
        conn=conn,
        now=now,
        lead=timedelta(seconds=settings.autojoin_lead_seconds),
        join_settings=JoinSettings(
            rule=user_settings.auto_join_rule, teams_join_enabled=settings.teams_join_enabled
        ),
        template_id=_own_template(session, conn.user_id, user_settings.calendar_template_id),
    )
    owned = [e for e in events if e.user_id == conn.user_id and e.connection_id == conn.id]
    joins = _joins_by_event_id(session, conn.user_id, [e.id for e in owned])
    for row in owned:
        _reconcile(run, row, joins.get(row.id))
    session.commit()
    return run.result


def set_join_template(
    session: Session, user_id: int, template_id: int | None, *, commit: bool = True
) -> int:
    """Record `template_id` (the person's own, else none) on their calendar joins that have not
    sent a bot yet, so a change of the template for calendar meetings reaches meetings already
    planned. A join whose bot is in keeps the template it was sent with. Returns how many."""
    own = _own_template(session, user_id, template_id)
    result = session.execute(
        update(ScheduledJoin)
        .where(
            ScheduledJoin.user_id == user_id,
            ScheduledJoin.source == "calendar",
            ScheduledJoin.state.in_(MOVABLE),
        )
        .values(template_id=own)
    )
    if commit:
        session.commit()
    return result.rowcount


# ---- events ----


def _events_by_source_id(session: Session, conn, source_ids: list[str]) -> dict[str, CalendarEvent]:
    if not source_ids:
        return {}
    rows = session.scalars(
        select(CalendarEvent).where(
            CalendarEvent.connection_id == conn.id,
            CalendarEvent.user_id == conn.user_id,
            CalendarEvent.source_event_id.in_(source_ids),
        )
    )
    return {row.source_event_id: row for row in rows}


def _joins_by_event_id(session: Session, user_id: int, event_ids: list[int]):
    """The users's joins for these events, locked: the dispatcher and a switch from the API may be
    working on the same rows."""
    if not event_ids:
        return {}
    rows = session.scalars(
        select(ScheduledJoin)
        .where(ScheduledJoin.user_id == user_id, ScheduledJoin.calendar_event_id.in_(event_ids))
        .with_for_update()
    )
    return {row.calendar_event_id: row for row in rows}


def _copy_event(row: CalendarEvent, parsed: ParsedEvent, now: datetime) -> None:
    """The source's facts onto the row. The user's `override` is theirs and is never touched."""
    row.recurring_event_id = parsed.recurring_event_id
    row.title = _clean(parsed.title)[:TITLE_LIMIT]
    row.starts_at, row.ends_at = parsed.starts_at, parsed.ends_at
    row.platform, row.meeting_url, row.native_id = (
        parsed.platform,
        parsed.meeting_url,
        parsed.native_id,
    )
    row.organizer_self = parsed.organizer_self
    row.organizer_same_org = parsed.organizer_same_org
    row.attendee_count = parsed.attendee_count
    row.self_response = parsed.self_response
    row.status = parsed.status
    row.all_day, row.event_type = parsed.all_day, parsed.event_type
    row.synced_at = now


# ---- joins ----


def _reconcile(run: _Run, row: CalendarEvent, join: ScheduledJoin | None) -> None:
    """Bring the event's join in line with what the rule says now."""
    decision = will_join(row, row.override, run.join_settings, now=run.now)
    if not decision.join:
        # An event that has simply ended keeps its join as it is: if no bot went in, the runner
        # records it as "Not recorded", and that history should not be rewritten as a cancel.
        if decision.reason != "ended" and join is not None and join.state in CANCELLABLE:
            _cancel_join(run, join, decision.reason)
        return
    if not (row.platform and row.native_id and row.meeting_url):
        return  # a link we recognise but cannot join yet (Teams until T1): nothing to schedule
    deadline_at = row.starts_at + DEADLINE_AFTER_START
    if join is None:
        if run.now <= deadline_at:  # past it and still on is "In progress", not an automatic join
            _create_join(run, row, deadline_at)
    elif join.state == "cancelled":
        if run.now <= deadline_at and not _closed_before_the_runner(run, join, row):
            _revive_join(run, join, row, deadline_at)
    elif join.state in MOVABLE or (join.state == "skipped" and join.skip_reason in MOVABLE_SKIPS):
        _move_join(run, join, row, deadline_at)
    # Anything else has a bot sent or is finished: the meeting is the truth now (spec 7.7).


def _closed_before_the_runner(run: _Run, join: ScheduledJoin, row: CalendarEvent) -> bool:
    """A join the runner closed silently because its time came before the runner ever led (or
    while the operator's switch was off) must not be revived by a later sync, rule change or
    switch: that would send a bot into a meeting already in progress. It may come back only when
    the event moved so that its run time is after the moment the runner started."""
    if join.skip_reason != "before_runner":
        return False
    started = run.session.scalar(select(AppState.autojoin_started_at))
    return started is None or row.starts_at - run.lead < started


def _create_join(run: _Run, row: CalendarEvent, deadline_at: datetime) -> None:
    run.session.add(
        ScheduledJoin(
            user_id=run.conn.user_id,
            source="calendar",
            calendar_event_id=row.id,
            platform=row.platform,
            native_id=row.native_id,
            meeting_url=row.meeting_url,
            starts_at=row.starts_at,
            ends_at=row.ends_at,
            run_at=row.starts_at - run.lead,
            deadline_at=deadline_at,
            template_id=run.template_id,
            state="scheduled",
        )
    )
    run.result.created += 1


def _set_schedule(run: _Run, join: ScheduledJoin, row: CalendarEvent, deadline_at) -> None:
    join.starts_at, join.ends_at = row.starts_at, row.ends_at
    join.run_at, join.deadline_at = row.starts_at - run.lead, deadline_at


def _set_link(join: ScheduledJoin, row: CalendarEvent) -> None:
    join.platform, join.native_id, join.meeting_url = row.platform, row.native_id, row.meeting_url


def _revive_join(run: _Run, join: ScheduledJoin, row: CalendarEvent, deadline_at) -> None:
    """The same row, back to waiting: the unique `(user_id, calendar_event_id)` index forbids a
    second one. It starts over, so the 2-minute re-check runs for it again."""
    _set_schedule(run, join, row, deadline_at)
    _set_link(join, row)
    join.state, join.skip_reason, join.last_error = "scheduled", None, None
    join.attempts, join.rechecked_at = 0, None
    run.result.revived += 1


def _move_join(run: _Run, join: ScheduledJoin, row: CalendarEvent, deadline_at) -> None:
    before = (join.starts_at, join.ends_at, join.run_at, join.deadline_at)
    _set_schedule(run, join, row, deadline_at)
    if join.state == "scheduled":
        _set_link(join, row)  # a `no_capacity` row holds its meeting code: leave the link alone
    elif join.run_at > run.now:
        if join.state == "skipped":  # the new time is a fresh start: tried and re-checked again
            _set_link(join, row)
            join.skip_reason, join.attempts, join.rechecked_at = None, 0, None
        join.state = "scheduled"  # moved to later: stop retrying now, wait for the new time
    if before != (join.starts_at, join.ends_at, join.run_at, join.deadline_at):
        run.result.moved += 1


def _cancel_join(run: _Run, join: ScheduledJoin, reason: str) -> None:
    join.state, join.skip_reason = "cancelled", reason
    run.result.cancelled += 1


# ---- cancellations ----


def _cancel_event(run: _Run, row: CalendarEvent, parsed: ParsedEvent, join) -> None:
    """The calendar says this event is cancelled. A stub has no times: keep the row's own."""
    row.status = "cancelled"
    if parsed.starts_at != NO_TIME:
        row.starts_at, row.ends_at = parsed.starts_at, parsed.ends_at
    row.synced_at = run.now
    if join is None:
        return
    if join.state in CANCELLABLE:
        _cancel_join(run, join, "cancelled")
    elif join.state == "joined" and join.meeting_id is not None:
        run.to_stop.append(join.id)


def _cancel_missing(run: _Run, named: set[str]) -> None:
    """Events the source no longer lists, from a complete full listing: deleted, or moved out of
    the window. Only rows that overlap the window can be called missing (an event that ended long
    ago is just not in it), and only a join with no bot sent is cancelled: a bot already in a call
    is never stopped on the strength of absence."""
    conn, session = run.conn, run.session
    query = select(CalendarEvent).where(
        CalendarEvent.connection_id == conn.id,
        CalendarEvent.user_id == conn.user_id,
        CalendarEvent.status != "cancelled",
        CalendarEvent.ends_at > run.now - WINDOW_BEFORE,
        CalendarEvent.starts_at < run.now + WINDOW_AFTER,
    )
    if named:
        query = query.where(CalendarEvent.source_event_id.not_in(named))
    missing = list(session.scalars(query))
    joins = _joins_by_event_id(session, conn.user_id, [row.id for row in missing])
    for row in missing:
        row.status, row.synced_at = "cancelled", run.now
        join = joins.get(row.id)
        if join is not None and join.state in CANCELLABLE:
            _cancel_join(run, join, "removed")


def _stop_cancelled_bots(run: _Run, vexa: VexaClient | None) -> None:
    """Stop the bot of each meeting whose event was cancelled while the bot was sent and not yet
    done. `cancel_meeting` refuses a meeting the pipeline already took, and says 502 when Vexa will
    not stop the bot: in both cases the join stays `joined` and nothing is raised (the calendar
    change itself is already saved)."""
    session = run.session
    for join_id in run.to_stop:
        join = session.get(ScheduledJoin, join_id)
        meeting = session.get(Meeting, join.meeting_id) if join and join.meeting_id else None
        if join is None or meeting is None:
            continue
        vexa = vexa or VexaClient()
        try:
            cancel_meeting(session, vexa, meeting)
        except Refused as refused:
            logger.info("calendar cancel left meeting %s alone (%s)", meeting.id, refused.code)
            continue
        join.state, join.skip_reason = "cancelled", "cancelled"
        session.commit()
        run.result.cancelled += 1


# ---- the sync job (C8b) ----


def _full_due(conn: CalendarConnection, now: datetime) -> bool:
    return (
        conn.sync_token is None
        or conn.last_full_sync_at is None
        or now - conn.last_full_sync_at >= FULL_RESYNC_EVERY
    )


def _fetch(source: CalendarSource, conn: CalendarConnection, now: datetime) -> SyncBatch:
    """The next batch; an expired token (410) is answered with a full listing at once."""
    try:
        return source.sync(conn, now=now, full=_full_due(conn, now))
    except SyncTokenExpired:
        return source.sync(conn, now=now, full=True)


def _record_error(
    session: Session,
    conn: CalendarConnection,
    exc: Exception,
    now: datetime,
    settings: Settings | None = None,
):
    """Remember the failure on the connection (C13 will act on it: degrade after 30 minutes, ask
    for a reconnect on a revoked grant). A source's messages carry only a status or an exception
    type by contract; any other exception is recorded by type alone."""
    name = type(exc).__name__
    text = f"{name}: {exc}" if isinstance(exc, CalendarSourceError) and str(exc) else name
    logger.warning("calendar sync for connection %s failed (%s)", conn.id, name)
    conn.last_error, conn.last_error_at = text[:ERROR_LIMIT], now
    calendar_states.on_sync_failure(session, conn, exc, now, settings=settings)
    session.commit()


def sync_connection(
    session: Session,
    conn: CalendarConnection,
    source: CalendarSource,
    *,
    now: datetime,
    settings: Settings | None = None,
    vexa: VexaClient | None = None,
) -> None:
    """Sync one connection. Never raises for what Google or the data does: a failure is recorded
    in `last_error` and the next attempt starts from the same state."""
    if conn.status not in SYNCABLE:
        return
    user_settings = session.get(UserSettings, conn.user_id) or DEFAULT_USER_SETTINGS
    try:
        batch = _fetch(source, conn, now)
        apply_batch(session, conn, batch, user_settings, now=now, settings=settings, vexa=vexa)
    except Exception as exc:
        session.rollback()
        _record_error(session, conn, exc, now, settings)
        return
    # The batch is committed. Only now may the connection move on.
    if not batch.complete:
        partly = CalendarSourceError("the calendar was only partly read")
        _record_error(session, conn, partly, now, settings)
        return
    if batch.next_sync_token is not None:
        conn.sync_token = batch.next_sync_token
    if batch.full:
        conn.last_full_sync_at = now
    conn.last_sync_at = now
    conn.last_error = conn.last_error_at = None
    calendar_states.mark_healthy(session, conn, now)
    session.commit()


def _due_connection_ids(session: Session, now: datetime, provider: str) -> list[int]:
    """Connections whose last sync AND last failure are both at least `SYNC_EVERY` ago (a failing
    connection is retried at the same pace, not every minute). A null `last_sync_at` is a first
    sync. Suspended users are not synced."""
    cutoff = now - SYNC_EVERY
    query = (
        select(CalendarConnection.id)
        .join(User, User.id == CalendarConnection.user_id)
        .where(
            CalendarConnection.provider == provider,
            CalendarConnection.status.in_(SYNCABLE),
            User.status == "active",
            or_(
                CalendarConnection.last_sync_at.is_(None), CalendarConnection.last_sync_at <= cutoff
            ),
            or_(
                CalendarConnection.last_error_at.is_(None),
                CalendarConnection.last_error_at <= cutoff,
            ),
        )
        .order_by(CalendarConnection.id)
        .limit(MAX_PER_RUN)
    )
    return list(session.scalars(query))


def sync_due_connections(
    session: Session, now: datetime, source: CalendarSource, *, settings: Settings | None = None
) -> int:
    """The loop job: sync every due connection of the source's provider, one at a time, each
    committing on its own. Returns how many it tried."""
    ids = _due_connection_ids(session, now, source.provider)
    for conn_id in ids:
        try:
            conn = session.get(CalendarConnection, conn_id)
            if conn is not None:
                sync_connection(session, conn, source, now=now, settings=settings)
        except Exception as exc:  # the database itself failed: skip this one, try the rest
            session.rollback()
            logger.error("calendar sync of connection %s failed (%s)", conn_id, type(exc).__name__)
    return len(ids)


def register_calendar_sync(
    scheduler,
    settings: Settings | None = None,
    *,
    source: "calendar_providers.SourceRegistry | CalendarSource | None" = None,
) -> bool:
    """Put one sync job per source on the scheduler's sync loop, but only while
    `CALENDAR_ENABLED` is true. The loop wakes every 60 s; each connection is synced at most every
    `SYNC_EVERY`. `source` is a registry (or a single source): one source per provider, so one
    access-token cache per provider for the life of the process. Without it, one source for every
    provider switched on is built. Google's job keeps its name, `calendar_sync`."""
    settings = settings or get_settings()
    if not settings.calendar_enabled:
        return False
    sources = calendar_providers.build_sources(settings) if source is None else source
    for each in calendar_providers.as_registry(sources):
        name = "calendar_sync" if each.provider == "google" else f"calendar_sync_{each.provider}"
        scheduler.register(
            name,
            60,
            lambda session, now, each=each: sync_due_connections(
                session, now, each, settings=settings
            ),
            loop="sync",
        )
    return True
