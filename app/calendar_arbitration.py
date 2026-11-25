"""Which workspace records an event the person holds in several of them (workspaces design, 12.4).

One person may connect the same calendar, or two calendars that both hold the same meeting, in more
than one workspace. Each connection syncs its own copy of the event (`calendar_events` rows are per
connection) and each workspace has its own auto-join rule, so each copy may want a bot. Only ONE bot
may go: a meeting is recorded exactly once, and the right workspace gets it.

The grain is the **event occurrence**: the same platform and meeting code, starting within
`SAME_EVENT_START_TOLERANCE` of each other (a recurring series or a personal Zoom room reuses one
code for many meetings, so the code alone is not enough). The guarantee underneath is the existing
database index: one live scheduled join per person per meeting code
(`uq_scheduled_joins_live_code`).
This module decides who gets to hold it, so the answer is by rule, never by whichever sync ran
first:

1. a join whose bot is already in (or that already ran) keeps the meeting: **a running bot is never
   moved**;
2. otherwise a workspace where the person switched THIS meeting on wins;
3. otherwise, among the workspaces whose rules would join it, the **first-connected** wins (the
   lowest connection id: the row is made at the first connect in a workspace and kept by every
   reconnect, so the order is stable); so when the first-connected workspace's rules would skip the
   event and another's would join it, the other records;
4. when no workspace would join it, nobody does.

The pure chooser is `choose_workspace`; `siblings` finds the person's other copies of the same
occurrence; `lock_person` serialises two syncs of the same person (two connections syncing at once
would each see an empty field and each make a join), and the arbitration runs under it.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import and_, select, text
from sqlalchemy.orm import Session

from app.models import CalendarConnection, CalendarEvent, ScheduledJoin

# Two copies are "the same event" when the meeting code is the same and the starts are this close
# (the organiser's-bot rule, `app/organizer_bot.py`, uses the same tolerance).
SAME_EVENT_START_TOLERANCE = timedelta(minutes=5)
# A join in one of these states has sent a bot or has already run: the workspace holding it keeps
# the meeting whatever the rules or the switches say afterwards.
STARTED = ("joined", "completed", "failed")
# What a loser's cancelled join says about itself.
RECORDED_ELSEWHERE = "recorded_elsewhere"
# `pg_advisory_xact_lock` key: this namespace plus the user id (ids stay under 2**31).
_LOCK_NAMESPACE = 0x5753_4C43 << 32


@dataclass(frozen=True)
class JoinCandidate:
    """One workspace's claim on an event occurrence. `would_join` is its rule's answer now (with
    the person's own switch for the event); `locked` is a bot already sent from its join."""

    team_id: int
    connection_id: int
    event_id: int
    override: bool | None
    would_join: bool
    locked: bool = False


def choose_workspace(candidates: list[JoinCandidate]) -> JoinCandidate | None:
    """The workspace that records the occurrence, or None when none would (spec 12.4). Order: a
    locked one (a bot already in), then one where the person switched it on, then the others whose
    rules would join; inside each tier the first-connected (lowest connection id)."""
    eligible = [c for c in candidates if c.locked or c.would_join]
    if not eligible:
        return None
    return min(eligible, key=lambda c: (not c.locked, c.override is not True, c.connection_id))


@dataclass(frozen=True)
class Member:
    """One copy of the event: its row, the connection (and so the workspace) it came through, and
    that copy's scheduled join if it has one."""

    event: CalendarEvent
    conn: CalendarConnection
    join: ScheduledJoin | None

    @property
    def locked(self) -> bool:
        return self.join is not None and self.join.state in STARTED


def lock_person(session: Session, user_id: int) -> None:
    """Serialise the arbitration of one person's events until the transaction ends. Two
    connections of one person syncing at once would otherwise each see no other copy yet and each
    make a join; under this lock the second one sees the first one's committed rows."""
    session.execute(text("select pg_advisory_xact_lock(:key)"), {"key": _LOCK_NAMESPACE + user_id})


def siblings(session: Session, row: CalendarEvent) -> list[Member]:
    """The person's OTHER copies of this event occurrence (same platform and meeting code, a start
    within the tolerance, not cancelled), each with its connection and join. Empty for an event
    with no meeting code. A copy whose connection can no longer be synced is included only when its
    join already sent a bot (it still holds the meeting)."""
    if not row.platform or not row.native_id:
        return []
    found = session.execute(
        select(CalendarEvent, CalendarConnection, ScheduledJoin)
        .join(CalendarConnection, CalendarConnection.id == CalendarEvent.connection_id)
        .outerjoin(
            ScheduledJoin,
            and_(
                ScheduledJoin.calendar_event_id == CalendarEvent.id,
                ScheduledJoin.user_id == CalendarEvent.user_id,
            ),
        )
        .where(
            CalendarEvent.user_id == row.user_id,
            CalendarEvent.id != row.id,
            CalendarEvent.connection_id != row.connection_id,
            CalendarEvent.platform == row.platform,
            CalendarEvent.native_id == row.native_id,
            CalendarEvent.status != "cancelled",
            CalendarEvent.starts_at >= row.starts_at - SAME_EVENT_START_TOLERANCE,
            CalendarEvent.starts_at <= row.starts_at + SAME_EVENT_START_TOLERANCE,
        )
        .order_by(CalendarConnection.id)
    ).all()
    return [Member(event, conn, join) for event, conn, join in found]


def candidate_of(
    member: Member, wants: Callable[[CalendarEvent, CalendarConnection], bool]
) -> JoinCandidate:
    """The member's claim: `wants(event, conn)` is the caller's answer to "would this workspace's
    rules record it now" (the same rule and checks the join creation uses)."""
    live = member.conn.status in ("connected", "degraded")
    return JoinCandidate(
        team_id=member.conn.team_id,
        connection_id=member.conn.id,
        event_id=member.event.id,
        override=member.event.override,
        would_join=live and wants(member.event, member.conn),
        locked=member.locked,
    )


def winner_among(
    own: Member,
    others: list[Member],
    wants: Callable[[CalendarEvent, CalendarConnection], bool],
) -> JoinCandidate | None:
    """The workspace that records this occurrence, over the copy `own` and its `others`."""
    return choose_workspace([candidate_of(m, wants) for m in [own, *others]])
