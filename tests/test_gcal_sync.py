"""The materialiser (plan task C8): `apply_batch` turns a `SyncBatch` of raw Google events into
`calendar_events` rows and `scheduled_joins` rows.

Everything here runs against a real Postgres and the real `parse_event` and `will_join`; only
Vexa is faked (for the one path that stops a bot). The event bodies are SYNTHETIC.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import inspect, select

from app import gcal_sync
from app.calendar_source import SyncBatch
from app.gcal_sync import ApplyResult, apply_batch
from app.models import (
    CalendarConnection,
    CalendarEvent,
    Meeting,
    ScheduledJoin,
    Template,
    UserSettings,
)
from app.pipeline import REASON_CANCELLED
from app.vexa import VexaError
from tests.test_web import make_settings, make_user

NOW = datetime(2026, 11, 4, 8, 0, tzinfo=UTC)
HOUR = timedelta(hours=1)
SETTINGS = make_settings(autojoin_lead_seconds=60)


def iso(moment: datetime) -> str:
    return moment.isoformat()


def raw_event(
    event_id: str = "evt-1",
    *,
    start: datetime = NOW + HOUR,
    length: timedelta = timedelta(minutes=30),
    code: str = "abc-defg-hij",
    **fields,
) -> dict:
    """A Google event the signed-in user organises, with a Meet link."""
    event = {
        "kind": "calendar#event",
        "id": event_id,
        "status": "confirmed",
        "summary": "Weekly planning",
        "start": {"dateTime": iso(start)},
        "end": {"dateTime": iso(start + length)},
        "organizer": {"email": "me@example.test", "self": True},
        "attendees": [
            {
                "email": "me@example.test",
                "self": True,
                "organizer": True,
                "responseStatus": "accepted",
            },
            {"email": "colleague@example.test", "responseStatus": "accepted"},
        ],
        "hangoutLink": f"https://meet.google.com/{code}",
        "eventType": "default",
    }
    event.update(fields)
    return event


def stub(event_id: str = "evt-1") -> dict:
    return {"kind": "calendar#event", "id": event_id, "status": "cancelled"}


def batch(*events: dict, complete: bool = True, full: bool = False) -> SyncBatch:
    return SyncBatch(
        raw_events=list(events),
        next_sync_token="token-1" if complete else None,
        complete=complete,
        full=full,
    )


@dataclass
class World:
    user: object
    template: Template
    user_settings: UserSettings
    conn: CalendarConnection


def make_world(session, email="ada@example.test", *, hosted_domain: str | None = None) -> World:
    user = make_user(session, email)
    template = Template(owner_user_id=user.id, name="General", instructions="Notes.")
    session.add(template)
    session.flush()
    user_settings = UserSettings(user_id=user.id, calendar_template_id=template.id)
    conn = CalendarConnection(user_id=user.id, provider="google", account_domain=hosted_domain)
    session.add_all([user_settings, conn])
    session.commit()
    return World(user, template, user_settings, conn)


@pytest.fixture
def world(session) -> World:
    return make_world(session)


def apply(session, world: World, sync_batch: SyncBatch, *, now=NOW, settings=SETTINGS, vexa=None):
    return apply_batch(
        session, world.conn, sync_batch, world.user_settings, now=now, settings=settings, vexa=vexa
    )


def joins(session, user_id: int | None = None) -> list[ScheduledJoin]:
    query = select(ScheduledJoin).order_by(ScheduledJoin.id)
    if user_id is not None:
        query = query.where(ScheduledJoin.user_id == user_id)
    return list(session.scalars(query))


def events(session, user_id: int | None = None) -> list[CalendarEvent]:
    query = select(CalendarEvent).order_by(CalendarEvent.id)
    if user_id is not None:
        query = query.where(CalendarEvent.user_id == user_id)
    return list(session.scalars(query))


def only_join(session) -> ScheduledJoin:
    (found,) = joins(session)
    return found


def snapshot(session) -> list[dict]:
    """Every column of every event and join row, so a test can prove nothing changed."""
    session.expire_all()
    out = []
    for row in [*events(session), *joins(session)]:
        out.append(
            {
                "table": type(row).__tablename__,
                **{c.key: getattr(row, c.key) for c in inspect(type(row)).column_attrs},
            }
        )
    return out


class FakeVexa:
    def __init__(self, *, fail_stop: bool = False) -> None:
        self.fail_stop = fail_stop
        self.stopped: list[tuple[str, str]] = []

    def stop_bot(self, platform, native_id):
        if self.fail_stop:
            raise VexaError("down")
        self.stopped.append((platform, native_id))


def dispatched(session, world: World, state: str = "joined", *, bot_sent: bool = True):
    """Take the one join through the dispatcher: a Meeting row and the join in `state`."""
    found = only_join(session)
    meeting = Meeting(
        owner_user_id=world.user.id,
        url=found.meeting_url,
        platform=found.platform,
        native_id=found.native_id,
        template_id=world.template.id,
        status="bot_sent" if bot_sent else "processing",
    )
    session.add(meeting)
    session.flush()
    found.state, found.meeting_id = state, meeting.id
    session.commit()
    return found, meeting


# ---- new events ----


@pytest.mark.parametrize("lead", [60, 90])
def test_new_event_creates_event_row_and_scheduled_join_with_lead_and_deadline(
    session, world, lead
):
    start = NOW + HOUR
    result = apply(
        session,
        world,
        batch(raw_event(start=start)),
        settings=make_settings(autojoin_lead_seconds=lead),
    )

    assert result == ApplyResult(created=1, moved=0, cancelled=0, revived=0)
    (event,) = events(session)
    assert (event.user_id, event.connection_id) == (world.user.id, world.conn.id)
    assert (event.source_event_id, event.title) == ("evt-1", "Weekly planning")
    assert (event.platform, event.native_id) == ("google_meet", "abc-defg-hij")
    assert event.starts_at == start and event.attendee_count == 2
    join = only_join(session)
    assert join.user_id == world.user.id and join.calendar_event_id == event.id
    assert join.state == "scheduled" and join.source == "calendar"
    assert join.starts_at == start and join.ends_at == start + timedelta(minutes=30)
    assert join.run_at == start - timedelta(seconds=lead)
    assert join.deadline_at == start + timedelta(minutes=15)
    assert join.template_id == world.template.id
    assert (join.platform, join.native_id) == ("google_meet", "abc-defg-hij")
    assert join.meeting_url == "https://meet.google.com/abc-defg-hij"


def test_default_lead_is_sixty_seconds():
    assert make_settings().autojoin_lead_seconds == 60


def test_event_started_but_inside_deadline_gets_a_past_run_at_so_it_dispatches_now(session, world):
    start = NOW - timedelta(minutes=5)
    apply(session, world, batch(raw_event(start=start, length=timedelta(minutes=30))))

    join = only_join(session)
    assert join.run_at < NOW < join.deadline_at
    assert join.state == "scheduled"


def test_event_past_its_deadline_but_still_on_gets_no_new_join_row(session, world):
    # Spec 7.8: connecting mid-meeting shows "In progress" with Send now, not an automatic join.
    start = NOW - timedelta(minutes=20)
    result = apply(session, world, batch(raw_event(start=start, length=timedelta(hours=1))))

    assert result.created == 0 and joins(session) == []
    assert len(events(session)) == 1


def test_declined_event_gets_no_join_row(session, world):
    declined = raw_event(
        attendees=[{"email": "me@example.test", "self": True, "responseStatus": "declined"}]
    )
    result = apply(session, world, batch(declined))

    assert result.created == 0 and joins(session) == []
    (event,) = events(session)
    assert event.self_response == "declined"


def test_accepting_a_declined_event_creates_the_join(session, world):
    declined = raw_event(
        attendees=[{"email": "me@example.test", "self": True, "responseStatus": "declined"}]
    )
    apply(session, world, batch(declined))
    result = apply(session, world, batch(raw_event()))

    assert result.created == 1 and only_join(session).state == "scheduled"


def test_declining_a_scheduled_event_cancels_its_join_with_the_reason(session, world):
    apply(session, world, batch(raw_event()))
    declined = raw_event(
        attendees=[{"email": "me@example.test", "self": True, "responseStatus": "declined"}]
    )
    result = apply(session, world, batch(declined))

    assert result.cancelled == 1
    join = only_join(session)
    assert (join.state, join.skip_reason) == ("cancelled", "declined")


def test_event_without_a_link_is_stored_but_has_no_join(session, world):
    plain = raw_event()
    del plain["hangoutLink"]
    apply(session, world, batch(plain))

    (event,) = events(session)
    assert event.platform is None and event.meeting_url is None
    assert joins(session) == []


def test_all_day_event_is_stored_flagged_and_never_joined(session, world):
    all_day = raw_event(start=NOW)
    all_day["start"] = {"date": "2026-11-04"}
    all_day["end"] = {"date": "2026-11-05"}
    apply(session, world, batch(all_day))

    (event,) = events(session)
    assert event.all_day is True
    assert event.starts_at == datetime(2026, 11, 4, tzinfo=UTC)
    assert joins(session) == []


def test_two_events_sharing_a_recurring_meet_code_each_get_a_join(session, world):
    first = raw_event("series_20261104", start=NOW + HOUR)
    second = raw_event("series_20261111", start=NOW + HOUR + timedelta(days=7))
    apply(session, world, batch(first, second))

    assert len(joins(session)) == 2


def test_a_stranger_invite_follows_the_c7_rule(session, world):
    invite = raw_event(
        organizer={"email": "stranger@elsewhere.test"},
        attendees=[
            {"email": "stranger@elsewhere.test", "organizer": True, "responseStatus": "accepted"},
            {"email": "me@example.test", "self": True, "responseStatus": "needsAction"},
        ],
    )
    apply(session, world, batch(invite))
    assert joins(session) == []  # not_accepted: waits for the user

    (event,) = events(session)
    event.override = True  # the user switches it on
    session.commit()
    apply(session, world, batch(invite))
    assert only_join(session).state == "scheduled"


def test_an_unanswered_invite_from_the_users_own_workspace_joins(session):
    world = make_world(session, hosted_domain="acme.example")
    invite = raw_event(
        organizer={"email": "boss@ACME.example"},
        attendees=[
            {"email": "boss@acme.example", "organizer": True, "responseStatus": "accepted"},
            {"email": "me@acme.example", "self": True, "responseStatus": "needsAction"},
        ],
    )
    apply(session, world, batch(invite))

    assert only_join(session).state == "scheduled"


def test_rule_none_creates_no_join_until_the_user_switches_the_event_on(session, world):
    world.user_settings.auto_join_rule = "none"
    session.commit()
    apply(session, world, batch(raw_event()))
    assert joins(session) == []

    (event,) = events(session)
    event.override = True
    session.commit()
    result = apply(session, world, batch(raw_event()))
    assert result.created == 1


# ---- moves ----


def test_move_before_dispatch_updates_the_same_row(session, world):
    apply(session, world, batch(raw_event(start=NOW + HOUR)))
    first = only_join(session)
    later = NOW + 3 * HOUR

    result = apply(session, world, batch(raw_event(start=later)))

    assert result == ApplyResult(created=0, moved=1, cancelled=0, revived=0)
    (join,) = joins(session)
    assert join.id == first.id
    assert join.starts_at == later and join.ends_at == later + timedelta(minutes=30)
    assert join.run_at == later - timedelta(seconds=60)
    assert join.deadline_at == later + timedelta(minutes=15)
    (event,) = events(session)
    assert event.starts_at == later


def test_a_changed_link_before_dispatch_updates_the_join(session, world):
    apply(session, world, batch(raw_event(code="aaa-aaaa-aaa")))
    apply(session, world, batch(raw_event(code="bbb-bbbb-bbb")))

    join = only_join(session)
    assert join.native_id == "bbb-bbbb-bbb"
    assert join.meeting_url == "https://meet.google.com/bbb-bbbb-bbb"


def test_move_after_dispatch_is_ignored(session, world):
    apply(session, world, batch(raw_event(start=NOW + HOUR)))
    join, _ = dispatched(session, world, "joined")
    before = (join.starts_at, join.run_at, join.deadline_at)

    result = apply(session, world, batch(raw_event(start=NOW + 3 * HOUR)))

    assert result.moved == 0
    session.refresh(join)
    assert (join.starts_at, join.run_at, join.deadline_at) == before
    assert join.state == "joined"
    (event,) = events(session)
    assert event.starts_at == NOW + 3 * HOUR  # the event row still follows the calendar


def test_a_join_being_dispatched_is_not_moved(session, world):
    apply(session, world, batch(raw_event(start=NOW + HOUR)))
    join = only_join(session)
    join.state = "dispatching"
    session.commit()
    before = join.starts_at

    apply(session, world, batch(raw_event(start=NOW + 3 * HOUR)))

    session.refresh(join)
    assert join.starts_at == before


def test_a_no_capacity_join_moved_into_the_future_waits_again(session, world):
    apply(session, world, batch(raw_event(start=NOW + HOUR)))
    join = only_join(session)
    join.state = "no_capacity"
    session.commit()

    apply(session, world, batch(raw_event(start=NOW + 5 * HOUR)))

    session.refresh(join)
    assert join.state == "scheduled" and join.run_at == NOW + 5 * HOUR - timedelta(seconds=60)


# ---- cancellations and the switch ----


def test_cancel_before_dispatch_sets_cancelled(session, world):
    apply(session, world, batch(raw_event()))
    (event_before,) = events(session)
    start = event_before.starts_at

    result = apply(session, world, batch(stub()))

    assert result == ApplyResult(created=0, moved=0, cancelled=1, revived=0)
    join = only_join(session)
    assert (join.state, join.skip_reason) == ("cancelled", "cancelled")
    (event,) = events(session)
    assert event.status == "cancelled"
    assert event.starts_at == start  # the stub's missing times do not overwrite the real ones


def test_a_cancelled_event_nobody_has_seen_is_not_stored(session, world):
    result = apply(session, world, batch(stub("never-seen")))

    assert result == ApplyResult(created=0, moved=0, cancelled=0, revived=0)
    assert events(session) == [] and joins(session) == []


@pytest.mark.parametrize("state", ["dispatching", "no_capacity", "skipped"])
def test_cancel_reaches_a_join_that_has_not_sent_a_bot(session, world, state):
    apply(session, world, batch(raw_event()))
    join = only_join(session)
    join.state = state
    session.commit()

    apply(session, world, batch(stub()))

    session.refresh(join)
    assert join.state == "cancelled"


@pytest.mark.parametrize("state", ["completed", "failed"])
def test_finished_joins_keep_their_history_when_the_event_is_cancelled(session, world, state):
    apply(session, world, batch(raw_event()))
    join = only_join(session)
    join.state = state
    session.commit()

    result = apply(session, world, batch(stub()))

    session.refresh(join)
    assert join.state == state and result.cancelled == 0


def test_switch_off_cancels_then_on_revives_the_same_row_id(session, world):
    apply(session, world, batch(raw_event()))
    first = only_join(session)
    (event,) = events(session)

    event.override = False
    session.commit()
    off = apply(session, world, batch(raw_event()))
    assert off.cancelled == 1
    assert (only_join(session).state, only_join(session).skip_reason) == (
        "cancelled",
        "override_off",
    )

    event.override = None
    session.commit()
    on = apply(session, world, batch(raw_event()))

    assert on == ApplyResult(created=0, moved=0, cancelled=0, revived=1)
    (join,) = joins(session)
    assert join.id == first.id
    assert (join.state, join.skip_reason, join.attempts) == ("scheduled", None, 0)


def test_a_cancelled_join_is_not_revived_after_its_deadline(session, world):
    apply(session, world, batch(raw_event(start=NOW + HOUR)))
    (event,) = events(session)
    event.override = False
    session.commit()
    apply(session, world, batch(raw_event(start=NOW + HOUR)))
    event.override = None
    session.commit()

    late = NOW + HOUR + timedelta(minutes=16)
    result = apply(session, world, batch(raw_event(start=NOW + HOUR)), now=late)

    assert result.revived == 0 and only_join(session).state == "cancelled"


def test_revive_clears_the_recheck_mark(session, world):
    apply(session, world, batch(raw_event()))
    join = only_join(session)
    join.state, join.rechecked_at = "cancelled", NOW
    session.commit()

    apply(session, world, batch(raw_event()))

    session.refresh(join)
    assert join.state == "scheduled" and join.rechecked_at is None


def test_cancel_after_dispatch_while_bot_sent_calls_cancel_meeting(session, world):
    apply(session, world, batch(raw_event()))
    join, meeting = dispatched(session, world, "joined")
    vexa = FakeVexa()

    result = apply(session, world, batch(stub()), vexa=vexa)

    assert result.cancelled == 1
    assert vexa.stopped == [("google_meet", "abc-defg-hij")]
    session.refresh(meeting)
    session.refresh(join)
    assert (meeting.status, meeting.failure_reason) == ("failed", REASON_CANCELLED)
    assert (join.state, join.skip_reason) == ("cancelled", "cancelled")


def test_cancel_after_dispatch_leaves_a_meeting_the_pipeline_already_took(session, world):
    apply(session, world, batch(raw_event()))
    join, meeting = dispatched(session, world, "joined", bot_sent=False)
    vexa = FakeVexa()

    apply(session, world, batch(stub()), vexa=vexa)

    assert vexa.stopped == []
    session.refresh(meeting)
    session.refresh(join)
    assert meeting.status == "processing" and join.state == "joined"


def test_a_bot_that_will_not_stop_leaves_the_join_joined_and_does_not_raise(session, world):
    apply(session, world, batch(raw_event()))
    join, meeting = dispatched(session, world, "joined")

    result = apply(session, world, batch(stub()), vexa=FakeVexa(fail_stop=True))

    session.refresh(join)
    session.refresh(meeting)
    assert join.state == "joined" and meeting.status == "bot_sent"
    assert result.cancelled == 0
    assert events(session)[0].status == "cancelled"  # the calendar change itself was kept


def test_switch_off_after_dispatch_does_not_stop_a_running_bot(session, world):
    apply(session, world, batch(raw_event()))
    join, meeting = dispatched(session, world, "joined")
    (event,) = events(session)
    event.override = False
    session.commit()
    vexa = FakeVexa()

    apply(session, world, batch(raw_event()), vexa=vexa)

    session.refresh(join)
    assert vexa.stopped == [] and join.state == "joined"


# ---- incomplete and full batches ----


def test_incomplete_batch_applies_no_cancellations_and_no_deletions(session, world):
    apply(session, world, batch(raw_event("a"), raw_event("b", code="bbb-bbbb-bbb")))
    before = {j.calendar_event_id: j.state for j in joins(session)}

    result = apply(
        session,
        world,
        batch(stub("a"), raw_event("c", code="ccc-cccc-ccc"), complete=False, full=True),
    )

    assert result == ApplyResult(created=1, moved=0, cancelled=0, revived=0)
    states = {
        e.source_event_id: next(j.state for j in joins(session) if j.calendar_event_id == e.id)
        for e in events(session)
    }
    assert states == {"a": "scheduled", "b": "scheduled", "c": "scheduled"}
    assert all(e.status == "confirmed" for e in events(session))
    assert set(before.values()) == {"scheduled"}


def test_complete_full_batch_cancels_events_missing_from_the_window(session, world):
    ended_earlier = raw_event("old", start=NOW - 3 * HOUR)
    far_future = raw_event("far", start=NOW + timedelta(days=20), code="fff-ffff-fff")
    apply(
        session,
        world,
        batch(
            raw_event("a"),
            raw_event("b", code="bbb-bbbb-bbb"),
            ended_earlier,
            far_future,
        ),
    )

    result = apply(session, world, batch(raw_event("a"), full=True), now=NOW + timedelta(minutes=1))

    assert result.cancelled == 1
    by_id = {e.source_event_id: e for e in events(session)}
    assert by_id["b"].status == "cancelled" and by_id["a"].status == "confirmed"
    assert by_id["old"].status == "confirmed"  # ended before the window: just history
    assert by_id["far"].status == "confirmed"  # starts after the window: not listed, not removed
    join_of = {j.calendar_event_id: j for j in joins(session)}
    assert join_of[by_id["b"].id].state == "cancelled"
    assert join_of[by_id["b"].id].skip_reason == "removed"
    assert join_of[by_id["a"].id].state == "scheduled"


def test_an_incremental_batch_never_cancels_what_it_does_not_mention(session, world):
    apply(session, world, batch(raw_event("a"), raw_event("b", code="bbb-bbbb-bbb")))

    result = apply(session, world, batch(raw_event("a"), full=False))

    assert result.cancelled == 0 and {j.state for j in joins(session)} == {"scheduled"}


def test_a_missing_event_with_a_bot_in_the_call_is_left_alone(session, world):
    apply(session, world, batch(raw_event("a")))
    join, meeting = dispatched(session, world, "joined")
    vexa = FakeVexa()

    apply(session, world, batch(full=True), vexa=vexa)

    session.refresh(join)
    session.refresh(meeting)
    assert vexa.stopped == [] and join.state == "joined" and meeting.status == "bot_sent"


# ---- idempotence ----


def test_a_replayed_batch_changes_nothing(session, world):
    mixed = batch(
        raw_event("a"),
        raw_event("b", code="bbb-bbbb-bbb", start=NOW + 2 * HOUR),
        raw_event(
            "c",
            code="ccc-cccc-ccc",
            attendees=[{"email": "me@example.test", "self": True, "responseStatus": "declined"}],
        ),
        stub("never-seen"),
        full=True,
    )
    first = apply(session, world, mixed)
    assert first.created == 2
    before = snapshot(session)

    second = apply(session, world, mixed)

    assert second == ApplyResult(created=0, moved=0, cancelled=0, revived=0)
    assert snapshot(session) == before


def test_a_replayed_cancellation_and_move_change_nothing(session, world):
    apply(session, world, batch(raw_event("a"), raw_event("b", code="bbb-bbbb-bbb")))
    changes = batch(stub("a"), raw_event("b", code="bbb-bbbb-bbb", start=NOW + 4 * HOUR))
    apply(session, world, changes)
    before = snapshot(session)

    again = apply(session, world, changes)

    assert again == ApplyResult(0, 0, 0, 0)
    assert snapshot(session) == before


# ---- scoping and hostile input ----


def test_another_users_rows_are_never_touched(session, world):
    other = make_world(session, "grace@example.test")
    apply(session, other, batch(raw_event("evt-1"), raw_event("evt-2", code="bbb-bbbb-bbb")))
    theirs = snapshot(session)

    # Same source ids on ours: a move, a cancellation, and a full batch that mentions nothing.
    apply(session, world, batch(raw_event("evt-1")))
    apply(session, world, batch(raw_event("evt-1", start=NOW + 5 * HOUR)))
    apply(session, world, batch(stub("evt-1")))
    apply(session, world, batch(full=True))

    mine = {e.user_id for e in events(session, world.user.id)}
    assert mine == {world.user.id}
    after = [row for row in snapshot(session) if row.get("user_id") == other.user.id]
    assert after == [row for row in theirs if row.get("user_id") == other.user.id]
    assert {j.state for j in joins(session, other.user.id)} == {"scheduled"}
    assert all(j.user_id == world.user.id for j in joins(session, world.user.id))


def test_an_event_cannot_create_a_join_for_another_user(session, world):
    other = make_world(session, "grace@example.test")
    hostile = raw_event(
        user_id=other.user.id,  # extra keys in the payload mean nothing
        attendees=[
            {"email": "me@example.test", "self": True, "responseStatus": "accepted"},
            {"email": other.user.email, "responseStatus": "accepted"},
        ],
    )
    apply(session, world, batch(hostile))

    assert joins(session, other.user.id) == [] and events(session, other.user.id) == []
    assert len(joins(session, world.user.id)) == 1


def test_a_template_that_is_not_the_users_is_not_used(session, world):
    other = make_world(session, "grace@example.test")
    world.user_settings.calendar_template_id = other.template.id
    session.commit()

    apply(session, world, batch(raw_event()))

    assert only_join(session).template_id is None


def test_no_template_setting_gives_a_join_without_a_template(session, world):
    world.user_settings.calendar_template_id = None
    session.commit()
    apply(session, world, batch(raw_event()))

    assert only_join(session).template_id is None


def test_unreadable_events_are_counted_and_skipped_not_fatal(session, world):
    no_id = raw_event()
    del no_id["id"]
    no_time = raw_event("no-time")
    no_time["start"] = {}
    huge_id = raw_event("x" * 2000)
    result = apply(session, world, batch(no_id, no_time, huge_id, raw_event("good")))

    assert result.created == 1 and result.unreadable == 3
    assert [e.source_event_id for e in events(session)] == ["good"]


def test_a_nul_character_in_a_title_cannot_break_the_batch(session, world):
    apply(
        session,
        world,
        batch(raw_event("a", summary="Plan\x00ning"), raw_event("b", code="bbb-bbbb-bbb")),
    )

    titles = {e.source_event_id: e.title for e in events(session)}
    assert titles["a"] == "Planning" and "b" in titles


def test_a_nul_character_in_an_event_id_skips_that_event(session, world):
    result = apply(session, world, batch(raw_event("bad\x00id"), raw_event("good")))

    assert result.unreadable == 1
    assert [e.source_event_id for e in events(session)] == ["good"]


def test_an_enormous_title_is_cut_not_stored_whole(session, world):
    apply(session, world, batch(raw_event(summary="é" * 100_000)))

    (event,) = events(session)
    assert len(event.title) == gcal_sync.TITLE_LIMIT


def test_emoji_and_non_latin_title_round_trip(session, world):
    title = "Planification équipe 🎉 会議 — Ünïcode"
    apply(session, world, batch(raw_event(summary=title)))
    session.expire_all()

    assert events(session)[0].title == title


def test_description_location_and_attendee_addresses_are_not_stored(session, world):
    secret_text = "agenda-secret-text"
    event = raw_event(
        description=f"{secret_text} https://example.test/doc",
        location="Room 12 secret-room",
    )
    apply(session, world, batch(event))

    dump = " ".join(
        str(value) for row in snapshot(session) for value in row.values() if value is not None
    )
    for forbidden in (secret_text, "secret-room", "colleague@example.test", "me@example.test"):
        assert forbidden not in dump


def test_zoom_passcode_is_kept_for_the_join_and_never_logged(session, world, caplog):
    zoom = raw_event(location="https://acme.zoom.us/j/123456789?pwd=hunter2pass")
    del zoom["hangoutLink"]
    with caplog.at_level(logging.DEBUG):
        apply(session, world, batch(zoom))

    join = only_join(session)
    assert join.platform == "zoom" and "hunter2pass" in join.meeting_url
    assert "hunter2pass" not in caplog.text


# ---- times and zones ----


def test_an_offset_time_is_stored_as_utc_and_run_at_follows(session, world):
    lagos = timezone(timedelta(hours=1))
    start = datetime(2026, 11, 5, 10, 0, tzinfo=lagos)
    event = raw_event(start=start)
    event["start"]["timeZone"] = "Europe/Lagos"
    apply(session, world, batch(event))

    join = only_join(session)
    assert join.starts_at == datetime(2026, 11, 5, 9, 0, tzinfo=UTC)
    assert join.starts_at.utcoffset() is not None
    assert join.run_at == datetime(2026, 11, 5, 8, 59, tzinfo=UTC)


def test_event_crossing_midnight_keeps_both_instants(session, world):
    lagos = timezone(timedelta(hours=1))
    start = datetime(2026, 11, 5, 23, 45, tzinfo=lagos)
    apply(session, world, batch(raw_event(start=start, length=timedelta(minutes=30))))

    (event,) = events(session)
    assert event.starts_at == datetime(2026, 11, 5, 22, 45, tzinfo=UTC)
    assert event.ends_at == datetime(2026, 11, 5, 23, 15, tzinfo=UTC)
    assert only_join(session).run_at == datetime(2026, 11, 5, 22, 44, tzinfo=UTC)


def test_a_series_across_a_dst_change_gets_the_right_utc_for_each_instance(session, world):
    # 09:00 in New York: the clocks go back on 2026-11-01, so the same wall time is 13:00Z before
    # and 14:00Z after. Google sends the offset on every instance.
    new_york_summer = timezone(timedelta(hours=-4))
    new_york_winter = timezone(timedelta(hours=-5))
    before = raw_event(
        "series_20261030",
        start=datetime(2026, 10, 30, 9, 0, tzinfo=new_york_summer),
        recurringEventId="series",
    )
    after = raw_event(
        "series_20261103",
        start=datetime(2026, 11, 3, 9, 0, tzinfo=new_york_winter),
        recurringEventId="series",
    )
    now = datetime(2026, 10, 30, 6, 0, tzinfo=UTC)
    apply(session, world, batch(before, after), now=now)

    by_id = {e.source_event_id: e for e in events(session)}
    assert by_id["series_20261030"].starts_at == datetime(2026, 10, 30, 13, 0, tzinfo=UTC)
    assert by_id["series_20261103"].starts_at == datetime(2026, 11, 3, 14, 0, tzinfo=UTC)
    assert {e.recurring_event_id for e in events(session)} == {"series"}
    run_ats = sorted(j.run_at for j in joins(session))
    assert run_ats == [
        datetime(2026, 10, 30, 12, 59, tzinfo=UTC),
        datetime(2026, 11, 3, 13, 59, tzinfo=UTC),
    ]


def test_cancelling_one_instance_of_a_series_leaves_the_others(session, world):
    one = raw_event("series_1", start=NOW + HOUR, recurringEventId="series")
    two = raw_event("series_2", start=NOW + 25 * HOUR, recurringEventId="series")
    apply(session, world, batch(one, two))

    apply(session, world, batch(stub("series_2")))

    states = {
        e.source_event_id: next(j.state for j in joins(session) if j.calendar_event_id == e.id)
        for e in events(session)
    }
    assert states == {"series_1": "scheduled", "series_2": "cancelled"}


def test_moving_one_instance_of_a_series_leaves_the_others(session, world):
    one = raw_event("series_1", start=NOW + HOUR, recurringEventId="series")
    two = raw_event("series_2", start=NOW + 25 * HOUR, recurringEventId="series")
    apply(session, world, batch(one, two))

    moved = raw_event("series_2", start=NOW + 27 * HOUR, recurringEventId="series")
    result = apply(session, world, batch(moved))

    assert result.moved == 1
    by_id = {e.source_event_id: e for e in events(session)}
    join_of = {j.calendar_event_id: j for j in joins(session)}
    assert join_of[by_id["series_1"].id].starts_at == NOW + HOUR
    assert join_of[by_id["series_2"].id].starts_at == NOW + 27 * HOUR


def test_an_event_the_batch_names_but_cannot_be_read_is_not_called_missing(session, world):
    apply(session, world, batch(raw_event("a"), raw_event("b", code="bbb-bbbb-bbb")))
    broken = raw_event("b")
    broken["start"] = {"dateTime": "not a time"}

    result = apply(session, world, batch(raw_event("a"), broken, full=True))

    assert result.unreadable == 1 and result.cancelled == 0
    assert {j.state for j in joins(session)} == {"scheduled"}
    assert {e.status for e in events(session)} == {"confirmed"}
