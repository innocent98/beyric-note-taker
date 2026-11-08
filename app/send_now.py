"""Send now: the person sends the note taker to a meeting the scheduler did not get into (spec 7.7,
plan task C15b).

`POST /api/scheduled-joins/{id}/send-now` is a person pressing a button on a "Not recorded" row (or
an Upcoming row that says so). It is allowed while the meeting is still on (`now <= ends_at`) and
the join is `failed`, `no_capacity` or `skipped` (never for a suspended account). It does not make a
second way to send a bot: the bot goes through `placement.place_bot()`, the one function that runs
the pre-flight (hours cap, the per-user limit, one bot per meeting code, capacity) under the
placement lock, so nothing here can bypass a check and two placements cannot happen.

**Locking is the join runner's.** Event row first, then the join row, both `FOR UPDATE`, held from
the checks until the meeting row is committed (`place_bot(wait_seconds=0)` keeps them: waiting for
the placement lock would roll the row lock away). The runner takes the same two rows in the same
order, so a runner tick and a click on one row cannot both send: whichever has the row first
finishes, and the other finds it `joined` (the runner skips it; a click answers with the same
meeting). The runner's `_lock` skips a row somebody holds, so it never waits for a click.

**Idempotent.** A second click (a double click, a retry after a lost answer) waits on the row lock,
finds the join already `joined` and answers 200 with the same meeting. It sends nothing.

**A refusal changes nothing.** The pre-flight's refusals come back as the same codes the pasted
link gives (`services.placement_refusal`): 403 `hours_cap_reached`, 409 `user_limit`,
`already_in_meeting`, `meeting_has_note_taker`, `bot_busy`. The row is left exactly as it was, so a
409 means what it says and "Not recorded" stays true. There is no hidden retry: the runner does not
pick the row up again after a refusal (the deadline is extended only by a successful send).
A short wait covers one send in flight elsewhere (`bot_busy` with `slot_locked`): the row lock is
let go between looks and everything is checked again each time.

Not done here, on purpose: the calendar connection's state (the link is stored, a lapsed calendar
does not stop a bot being sent to a meeting that is on) and the event's join rule (the person is
overriding it by asking). A join the person switched off is `cancelled`, which is not sendable:
they switch it on from Upcoming.
"""

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import calendar_text, join_runner, placement, services
from app.config import Settings
from app.models import CalendarEvent, Meeting, ScheduledJoin, Template, User, UserSettings
from app.placement import PlacementRefused, PlacementRequest, place_bot
from app.vexa import UnsupportedMeetingUrl, VexaClient, VexaError

logger = logging.getLogger("app.send_now")

# A row sent by hand is given this long from the click (spec 7.7: `deadline_at = now + 15 min`).
NEW_DEADLINE = timedelta(minutes=15)

NOT_SENDABLE_TEXT = {
    "scheduled": "The note taker is already getting ready for this meeting.",
    "dispatching": "The note taker is already getting ready for this meeting.",
    "completed": "The note taker already joined this meeting.",
    "cancelled": "This meeting is switched off, so the note taker will not join it.",
    "skipped": "This meeting was not missed, so there is nothing to send again.",
}
ENDED_TEXT = "This meeting is already over, so the note taker cannot join it."
NOT_STARTED_TEXT = "This meeting has not started yet."
SEND_FAILED_TEXT = "The bot could not be sent. Please try again in a moment."
NO_TEMPLATE_TEXT = "Choose a notes template for calendar meetings in Settings, then try again."
UNSUPPORTED_TEXT = "The note taker cannot join this kind of meeting link yet."


@dataclass(frozen=True)
class Sent:
    join_id: int
    meeting_id: int


def send_now(
    session: Session,
    vexa: VexaClient,
    user: User,
    join_id: int,
    *,
    now: datetime,
    settings: Settings,
) -> Sent:
    """Send the note taker for `user`'s join `join_id`, or raise `services.Refused` (a refusal the
    API words) or a 404 for a join that is not the caller's. Commits. See the module doc."""
    patience = time.monotonic() + placement.LOCK_WAIT_SECONDS
    while True:
        try:
            return _attempt(session, vexa, user, join_id, now, settings)
        except PlacementRefused as refused:  # `place_bot` rolled back: nothing is held
            if refused.slot_locked and time.monotonic() < patience:
                time.sleep(placement.LOCK_POLL_SECONDS)
                continue
            raise services.placement_refusal(refused) from None


def _attempt(
    session: Session,
    vexa: VexaClient,
    user: User,
    join_id: int,
    now: datetime,
    settings: Settings,
) -> Sent:
    # The session may hold what an earlier request of this connection read: look again.
    session.expire_all()
    found = session.execute(
        select(ScheduledJoin.calendar_event_id).where(
            ScheduledJoin.id == join_id, ScheduledJoin.user_id == user.id
        )
    ).first()
    if found is None:  # also another person's join: it looks like a missing one
        session.rollback()
        raise StarletteHTTPException(404, "No such meeting")
    if found[0] is not None:
        # The event row first, as every writer of a join does (the runner, the override route).
        session.execute(
            select(CalendarEvent.id).where(CalendarEvent.id == found[0]).with_for_update()
        )
    join = session.scalars(
        select(ScheduledJoin)
        .where(ScheduledJoin.id == join_id, ScheduledJoin.user_id == user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).first()
    if join is None:  # deleted while this waited (a disconnect deletes every join)
        session.rollback()
        raise StarletteHTTPException(404, "No such meeting")
    if join.state == "joined" and join.meeting_id is not None:
        sent = Sent(join.id, join.meeting_id)  # a second click: the first one's answer
        session.rollback()
        return sent
    refusal = _not_sendable(session, join, user, now)
    if refusal is not None:
        session.rollback()
        raise refusal
    if not _has_template(session, join):
        session.rollback()
        raise services.Refused(409, "template_required", NO_TEMPLATE_TEXT)
    request = PlacementRequest(
        user_id=join.user_id,
        platform=join.platform,
        native_id=join.native_id,
        meeting_url=join.meeting_url,
        template_id=join.template_id,
        source=join.source,
        title=_event_title(session, join),
        # As the runner does: a bot of the owner's that an earlier killed send left on this code is
        # adopted instead of answering that someone else's note taker is there.
        adopt_orphan=True,
    )
    row_id = join.id

    def mark_joined(inner: Session, meeting: Meeting) -> None:
        # Inside place_bot's transaction: the meeting row and this update commit together.
        row = inner.get(ScheduledJoin, row_id)
        row.state, row.meeting_id, row.skip_reason, row.last_error = (
            "joined",
            meeting.id,
            None,
            None,
        )
        row.attempts += 1
        row.deadline_at = now + NEW_DEADLINE
        join_runner._touch(row, now)

    try:
        meeting = place_bot(
            session,
            vexa,
            request,
            now=now,
            settings=settings,
            wait_seconds=0,  # a wait would roll the row lock away
            on_placed=mark_joined,
        )
    except IntegrityError:
        # The one-live-join-per-code index: another row of the owner's holds this code. Rolled
        # back by `place_bot` (and the bot stopped).
        session.rollback()
        raise services.placement_refusal(PlacementRefused("already_in_meeting")) from None
    except VexaError as exc:
        logger.warning("send now for join %s: the host refused (%s)", row_id, type(exc).__name__)
        raise services.Refused(502, "bot_send_failed", SEND_FAILED_TEXT) from None
    except UnsupportedMeetingUrl:  # a link the host cannot join (Teams until T1)
        raise services.Refused(409, "unsupported_link", UNSUPPORTED_TEXT) from None
    except ValueError:
        # Anything `place_bot` rejects as a caller bug (the template was checked above): not a
        # refusal for a person to read. Let go of the row locks, then let it be a 500.
        session.rollback()
        raise
    return Sent(row_id, meeting.id)


def _has_template(session: Session, join: ScheduledJoin) -> bool:
    """Whether the notes can be written: the join's own template, else the person's calendar
    template, and it is theirs (what `placement._resolve` needs)."""
    template_id = join.template_id
    if template_id is None:
        template_id = session.scalar(
            select(UserSettings.calendar_template_id).where(UserSettings.user_id == join.user_id)
        )
    if template_id is None:
        return False
    return (
        session.scalar(
            select(Template.id).where(
                Template.id == template_id, Template.owner_user_id == join.user_id
            )
        )
        is not None
    )


def _event_title(session: Session, join: ScheduledJoin) -> str | None:
    if join.calendar_event_id is None:
        return None
    return session.scalar(
        select(CalendarEvent.title).where(CalendarEvent.id == join.calendar_event_id)
    )


def _not_sendable(
    session: Session, join: ScheduledJoin, user: User, now: datetime
) -> services.Refused | None:
    """What stops a send before the pre-flight, or None. Read under the row lock."""
    status = session.scalar(select(User.status).where(User.id == user.id))
    block = calendar_text.send_now_block(join, now)
    if status != "active" or block == "account_suspended":
        return services.placement_refusal(PlacementRefused("account_suspended"))
    if block == "not_sendable":
        return services.Refused(
            409, "cannot_send_now", NOT_SENDABLE_TEXT.get(join.state, "This cannot be sent again.")
        )
    if block == "not_started":
        return services.Refused(409, "not_started", NOT_STARTED_TEXT)
    if block == "meeting_ended":
        return services.Refused(409, "meeting_ended", ENDED_TEXT)
    if join_runner._another_live_join_on_the_code(session, join):
        return services.placement_refusal(PlacementRefused("already_in_meeting"))
    return None
