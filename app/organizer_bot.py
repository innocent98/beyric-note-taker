"""The organizer's bot is preferred (billing spec 2.5, stage S12).

Several accounts can hold the same calendar event with the same meeting link. The code allows one
bot per meeting code (`app/placement.py`), so only one of those accounts' bots records, and that
account's hours are charged. Before this rule the first bot placed won, a race the organizer could
lose to a guest. Now the organizer's bot goes in at its run time and a guest's join runner holds
back for `ORGANIZER_BOT_WAIT_SECONDS` (120 s) first.

`guest_should_wait` is the whole decision, asked by the join runner just before it would place a
guest's bot (`join_runner._place`). It says yes only when ALL of these hold, and every "no" means
the guest's bot goes now, exactly as it did before the rule existed:

* the switch is on (`PREFER_ORGANIZER_BOT`) and the guest's run time plus the wait is still ahead;
* the guest's own event is a calendar one and is not marked as organized by the guest
  (`organizer_self` is not true: false, or unknown, which is what an event with no organizer says);
* another account holds the same event: same platform and meeting code, a start within
  `SAME_EVENT_START_TOLERANCE`, that account's event says it is the organizer (its own claim, not
  cross-checked), its join row is due no later than the end of the guest's wait, and the row
  is still ahead of its bot (`scheduled`, `dispatching` or `no_capacity`; a row that is `joined`
  has its bot in, `skipped`, `cancelled` and `failed` ones will not place);
* that account CAN place: it is active, its calendar connection is good, its join rule and its
  event still say "join" (the same `will_join` decision the runner's own gate takes), and
  placement would not refuse it for its hours, its plan or its own bot limit
  (`placement.could_be_placed`, the same entitlement rule `reserve_slot` applies).

So a lone guest, an organizer with no account, with auto-join off, with no plan or no hours, or
whose calendar needs reconnecting, never causes a wait. The wait is also bounded: it ends at the
guest's run time plus the wait whatever the organizer does, and a join is open for 15 minutes after
the start, so no meeting can go unrecorded because of it. It is a scheduling delay, not a sleep:
the guest's row simply stays `scheduled` and the 10 s dispatch tick looks at it again; nothing is
stored that a restart could lose, so a runner that dies and comes back reads the clock and places.

Hours stay with the account whose bot recorded (`placement.py` and `usage.py` are unchanged).
"""

import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import placement, workspace_prefs
from app.config import Settings
from app.gcal_rules import JoinSettings, will_join
from app.models import CalendarEvent, ScheduledJoin, User

logger = logging.getLogger("app.organizer_bot")

# Two calendars hold "the same event" when the meeting code is the same and the starts are this
# close. A recurring series or a personal Zoom room reuses one code for many meetings, so the code
# alone is not enough; a moved copy (an invite updated for one person) still lines up.
SAME_EVENT_START_TOLERANCE = timedelta(minutes=5)
# The organizer's join is ahead of its bot in these states.
BEFORE_THE_BOT = ("scheduled", "dispatching", "no_capacity")
# Read this many candidate organizers at most (one is normal; more only when the same code is
# shared by several accounts that each claim to organize).
MAX_CANDIDATES = 5


def wait_ends_at(join: ScheduledJoin, settings: Settings) -> datetime:
    """When a guest's wait is over: its run time plus the wait."""
    return join.run_at + timedelta(seconds=settings.organizer_bot_wait_seconds)


def guest_should_wait(
    session: Session,
    join: ScheduledJoin,
    now: datetime,
    settings: Settings,
    *,
    connection_ok: Callable[[ScheduledJoin], bool],
) -> bool:
    """Whether `join` (a calendar join about to place its owner's bot) holds back for another
    account's organizer bot (see the module doc). Read-only. `connection_ok` says whether the
    calendar connection behind another account's join is good (the join runner's
    `connection_state`: passed in so this module does not import the runner)."""
    if not settings.prefer_organizer_bot or join.calendar_event_id is None:
        return False
    if now >= wait_ends_at(join, settings):
        return False
    mine = session.get(CalendarEvent, join.calendar_event_id)
    if mine is None or mine.organizer_self is True:
        return False
    candidates = session.execute(
        select(ScheduledJoin, CalendarEvent)
        .join(CalendarEvent, CalendarEvent.id == ScheduledJoin.calendar_event_id)
        .where(
            ScheduledJoin.user_id != join.user_id,
            ScheduledJoin.platform == join.platform,
            ScheduledJoin.native_id == join.native_id,
            ScheduledJoin.state.in_(BEFORE_THE_BOT),
            ScheduledJoin.deadline_at >= now,
            # An organizer whose own run time is after the end of this guest's wait cannot beat
            # it: waiting for that row would cost the guest time and still lose the slot.
            ScheduledJoin.run_at <= wait_ends_at(join, settings),
            ScheduledJoin.starts_at >= join.starts_at - SAME_EVENT_START_TOLERANCE,
            ScheduledJoin.starts_at <= join.starts_at + SAME_EVENT_START_TOLERANCE,
            CalendarEvent.organizer_self.is_(True),
            CalendarEvent.status == "confirmed",
        )
        .order_by(ScheduledJoin.id)
        .limit(MAX_CANDIDATES)
    ).all()
    for other, event in candidates:
        if _can_place(session, other, event, now, settings, connection_ok):
            logger.debug("join %s waits for the organizer's join %s", join.id, other.id)
            return True
    return False


def _can_place(
    session: Session,
    other: ScheduledJoin,
    event: CalendarEvent,
    now: datetime,
    settings: Settings,
    connection_ok: Callable[[ScheduledJoin], bool],
) -> bool:
    """Whether the organizer's own runner would place their bot at its run time: the same account,
    calendar, rule and entitlement checks the runner and `place_bot` apply, read without locks."""
    user = session.get(User, other.user_id)
    if user is None or user.status != "active":
        return False
    if not connection_ok(other):
        return False
    prefs = workspace_prefs.for_user(session, other.user_id, other.team_id)
    decision = will_join(
        event,
        event.override,
        JoinSettings.for_user(prefs, teams_join_enabled=settings.teams_join_enabled),
        now=now,
    )
    if not decision.join:
        return False
    return placement.could_be_placed(
        session,
        user,
        now=now,
        settings=settings,
        expected_seconds=int((other.ends_at - other.starts_at).total_seconds()),
    )
