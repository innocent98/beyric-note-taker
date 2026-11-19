"""The organizer's bot is preferred (billing spec 2.5, stage S12).

Real Postgres, the real materialiser (`apply_batch`), the real join runner and `place_bot()`, and
`FakeVexaHost`. The clock is injected: every run gets the `now` the test names, so "two minutes
later" is a number, never a sleep. Three fake accounts: ada organizes the event, ben and cleo are
guests who hold the same event with the same link. Hours run under `HOURS_MODE=enforce`, the way
billing runs, so "has a plan", "has hours" and "is on a trial" are real grants.

Nothing here has been proved live with three real calendars: see the SOP's "not proved live".
"""

import json
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app import alerts, join_runner, organizer_bot, placement
from app.join_runner import run_due_joins
from app.models import (
    AppState,
    CalendarEvent,
    HourGrant,
    Meeting,
    ScheduledJoin,
    UsageEntry,
    User,
    UserSettings,
)
from app.placement import PlacementRefused
from app.usage import email_hash
from tests.fakes.vexa_host import FakeVexaHost
from tests.test_gcal_sync import batch, make_world
from tests.test_join_runner import (
    StubCalendar,
    at,
    materialise,
    meetings,
    notes,
    state_of,
)
from tests.test_web import make_settings

HOUR = 3600
# `materialise` writes the events under the plain settings; the runs use these.
ENFORCE = make_settings(
    autojoin_lead_seconds=60,
    bot_max_local=1,
    user_bot_limit=2,
    teams_join_enabled=False,
    hours_mode="enforce",
)
SWITCHED_OFF = make_settings(
    autojoin_lead_seconds=60,
    bot_max_local=1,
    user_bot_limit=2,
    teams_join_enabled=False,
    hours_mode="enforce",
    prefer_organizer_bot=False,
)
RUN_AT = at(-1)  # the start minus the 60 s lead: when every row of the event is due
WAIT_ENDS = at(1)  # RUN_AT + 120 s


@pytest.fixture(autouse=True)
def alert_settings(monkeypatch):
    settings = make_settings(admin_alert_email="ops@example.test")
    monkeypatch.setattr(alerts, "get_settings", lambda: settings)


@pytest.fixture(autouse=True)
def runner_has_led(engine):
    with Session(engine) as setup:
        setup.add(AppState(id=1, autojoin_started_at=datetime(2026, 1, 1, tzinfo=UTC)))
        setup.commit()


@pytest.fixture
def host():
    return FakeVexaHost()


@pytest.fixture
def cal():
    return StubCalendar()


def run(session, host, cal, now, settings=ENFORCE):
    return run_due_joins(session, host, cal, now=now, settings=settings)


def give_hours(session, user, *, kind="pilot", seconds=10 * HOUR):
    session.add(
        HourGrant(
            user_id=user.id,
            email_hash=email_hash(user.email),
            kind=kind,
            seconds=seconds,
            starts_at=at(-24 * 60),
        )
    )
    session.commit()


def organizer_event(**fields) -> dict:
    return fields  # the plain `raw_event`: the signed-in user organizes it


def guest_event(email: str, **fields) -> dict:
    """The same event as a guest sees it: somebody else organizes and the guest accepted."""
    return {
        "organizer": {"email": "ada@example.test"},
        "attendees": [
            {"email": "ada@example.test", "organizer": True, "responseStatus": "accepted"},
            {"email": email, "self": True, "responseStatus": "accepted"},
        ],
        **fields,
    }


@dataclass
class Cast:
    ada: object  # World: the organizer
    ben: object  # World: a guest
    cleo: object  # World: a guest
    ada_join: ScheduledJoin
    ben_join: ScheduledJoin
    cleo_join: ScheduledJoin


def join_row(session, world) -> ScheduledJoin:
    session.expire_all()
    return session.scalars(
        select(ScheduledJoin).where(ScheduledJoin.user_id == world.user.id)
    ).one()


def cast_of(session, cal, *, ada_hours=True, guests_first=True) -> Cast:
    """ada organizes; ben and cleo are guests; all three have hours. The guests' rows are made
    first by default, so their ids are LOWER than the organizer's: whatever order rows come in,
    the rule must not depend on it."""
    ada = make_world(session, "ada@example.test")
    ben = make_world(session, "ben@example.test")
    cleo = make_world(session, "cleo@example.test")
    for world, has in ((ada, ada_hours), (ben, True), (cleo, True)):
        if has:
            give_hours(session, world.user)
    order = [(ben, guest_event("ben@example.test")), (cleo, guest_event("cleo@example.test"))]
    mine = (ada, organizer_event())
    for world, fields in order + [mine] if guests_first else [mine] + order:
        # One event id per account: the stub calendar of the re-check answers by id, and each
        # person's calendar has its own copy of the event.
        materialise(session, world, cal, event_id=f"evt-{world.user.email}", **fields)
    return Cast(
        ada, ben, cleo, join_row(session, ada), join_row(session, ben), join_row(session, cleo)
    )


@contextmanager
def organizer_runner_is_busy(engine, join_id):
    """Another process holds the organizer's row (a slow send, a crash mid-tick): the guests'
    runner skips that row this tick, the way `FOR UPDATE SKIP LOCKED` makes it."""
    other = Session(engine)
    other.execute(select(ScheduledJoin.id).where(ScheduledJoin.id == join_id).with_for_update())
    try:
        yield
    finally:
        other.rollback()
        other.close()


def owners(session) -> list[str]:
    return [session.get(User, m.owner_user_id).email.split("@")[0] for m in meetings(session)]


# ---- the rule, end to end ----


def test_the_organizers_bot_records_and_the_guests_are_told_why(session, host, cal):
    cast = cast_of(session, cal)

    outcomes = run(session, host, cal, RUN_AT)

    assert host.bots_sent == 1 and owners(session) == ["ada"]
    assert state_of(session, cast.ada_join) == ("joined", None)
    for guest_join, world in ((cast.ben_join, cast.ben), (cast.cleo_join, cast.cleo)):
        assert state_of(session, guest_join) == ("skipped", "meeting_has_note_taker")
        [notice] = [n for n in notes(session) if n.user_id == world.user.id]
        assert notice.kind == "calendar.meeting_has_note_taker"
    assert [o.result for o in outcomes].count("joined") == 1


def test_guests_hold_while_the_organizers_runner_is_busy_then_stay_out(
    session, engine, host, cal, monkeypatch
):
    cast = cast_of(session, cal)

    def no_sleeping(seconds):
        raise AssertionError("the wait must be a scheduling delay, not a sleep")

    monkeypatch.setattr("time.sleep", no_sleeping)
    with organizer_runner_is_busy(engine, cast.ada_join.id):
        assert run(session, host, cal, RUN_AT) == []
        assert run(session, host, cal, at(-1, 20)) == []  # still inside the wait
    assert host.bots_sent == 0 and meetings(session) == []
    for guest_join in (cast.ben_join, cast.cleo_join):
        assert state_of(session, guest_join) == ("scheduled", None)
        assert session.get(ScheduledJoin, guest_join.id).attempts >= 1
    assert notes(session) == []  # a holding guest is told nothing

    run(session, host, cal, at(-1, 40))  # the organizer's runner is back, inside the wait

    assert host.bots_sent == 1 and owners(session) == ["ada"]
    assert state_of(session, cast.ben_join) == ("skipped", "meeting_has_note_taker")
    assert state_of(session, cast.cleo_join) == ("skipped", "meeting_has_note_taker")


def test_when_the_organizer_never_arrives_the_first_guest_records_as_today(
    session, engine, host, cal
):
    cast = cast_of(session, cal)
    with organizer_runner_is_busy(engine, cast.ada_join.id):
        run(session, host, cal, RUN_AT)
        run(session, host, cal, at(0, 30))
        assert host.bots_sent == 0
        run(session, host, cal, WAIT_ENDS)  # the wait is over: nobody waits any longer
    assert host.bots_sent == 1 and owners(session) == ["ben"]  # the first placed bot, as today
    assert state_of(session, cast.cleo_join) == ("skipped", "meeting_has_note_taker")

    run(session, host, cal, at(1, 20))  # the organizer's runner finally gets there

    assert host.bots_sent == 1 and owners(session) == ["ben"]
    assert state_of(session, cast.ada_join) == ("skipped", "meeting_has_note_taker")


def test_a_runner_that_restarts_during_the_wait_still_places(session, engine, host, cal):
    """Nothing about the wait is stored: a new process reads the clock and places."""
    cast = cast_of(session, cal)
    with organizer_runner_is_busy(engine, cast.ada_join.id):
        run(session, host, cal, RUN_AT)
        assert host.bots_sent == 0
        with Session(engine) as restarted:  # a new process, a new connection
            run(restarted, host, cal, WAIT_ENDS)
    assert host.bots_sent == 1
    assert owners(session) == ["ben"]


def test_the_guest_is_looked_at_again_at_the_end_of_the_wait_not_a_gap_later(
    session, engine, host, cal
):
    cast = cast_of(session, cal)
    with organizer_runner_is_busy(engine, cast.ada_join.id):
        run(session, host, cal, at(0, 50))  # first look 10 s before the wait ends
        assert host.bots_sent == 0
        # Not looked at again before the wait is over (the row is out of the due query between
        # looks, so a crowd of waiting guests cannot starve other rows), and due again exactly when
        # it ends, not a retry gap later.
        assert cast.ben_join.id not in join_runner._due_ids(session, at(0, 59), ENFORCE)
        assert cast.ben_join.id in join_runner._due_ids(session, WAIT_ENDS, ENFORCE)
        run(session, host, cal, WAIT_ENDS)
    assert owners(session) == ["ben"]


# ---- nobody waits when the organizer cannot be counted on ----


def test_an_organizer_with_no_plan_does_not_hold_the_guests_up(session, host, cal):
    cast = cast_of(session, cal, ada_hours=False)

    run(session, host, cal, RUN_AT)

    assert host.bots_sent == 1 and owners(session) == ["ben"]  # at once, no wait
    assert state_of(session, cast.ada_join)[0] == "skipped"  # ada was refused for want of a plan


def test_a_lone_guest_records_at_once(session, host, cal):
    ben = make_world(session, "ben@example.test")
    give_hours(session, ben.user)
    join = materialise(session, ben, cal, **guest_event("ben@example.test"))  # one account only

    run(session, host, cal, RUN_AT)

    assert host.bots_sent == 1 and state_of(session, join) == ("joined", None)


def test_an_organizer_with_no_account_here_does_not_make_the_guest_wait(session, host, cal):
    """The organizer is a stranger to Beyric: no calendar of ours holds the event as theirs, so the
    guest's own event is the only one. Two guests of that stranger: the first placed records."""
    ben = make_world(session, "ben@example.test")
    cleo = make_world(session, "cleo@example.test")
    for world in (ben, cleo):
        give_hours(session, world.user)
    outside = {"organizer": {"email": "boss@elsewhere.test"}}
    for world in (ben, cleo):
        fields = guest_event(world.user.email) | outside
        fields["attendees"][0]["email"] = "boss@elsewhere.test"
        materialise(session, world, cal, event_id=f"evt-{world.user.email}", **fields)

    run(session, host, cal, RUN_AT)

    assert host.bots_sent == 1 and owners(session) == ["ben"]


def test_two_organizers_of_one_event_behave_as_before(session, host, cal):
    """Both calendars say 'I organize' (a shared calendar, a copied invite): nobody waits."""
    ben = make_world(session, "ben@example.test")
    ada = make_world(session, "ada@example.test")
    for world in (ben, ada):
        give_hours(session, world.user)
        materialise(session, world, cal, event_id=f"evt-{world.user.email}")

    run(session, host, cal, RUN_AT)

    assert host.bots_sent == 1 and owners(session) == ["ben"]  # the first placed, as today


def test_the_switch_off_is_the_old_race(session, host, cal):
    cast_of(session, cal)

    run(session, host, cal, RUN_AT, SWITCHED_OFF)

    assert host.bots_sent == 1 and owners(session) == ["ben"]  # not the organizer: today's rule


def test_the_wait_can_be_set_shorter_and_to_nothing(session, engine, host, cal):
    cast = cast_of(session, cal)
    short = make_settings(**{**ENFORCE.model_dump(), "organizer_bot_wait_seconds": 30})
    with organizer_runner_is_busy(engine, cast.ada_join.id):
        run(session, host, cal, at(-1, 10), short)
        assert host.bots_sent == 0
        run(session, host, cal, at(-1, 30), short)  # run time + 30 s
    assert host.bots_sent == 1 and owners(session) == ["ben"]


def test_a_wait_of_zero_is_no_wait(session, engine, host, cal):
    cast = cast_of(session, cal)
    none = make_settings(**{**ENFORCE.model_dump(), "organizer_bot_wait_seconds": 0})
    with organizer_runner_is_busy(engine, cast.ada_join.id):
        run(session, host, cal, RUN_AT, none)
    assert host.bots_sent == 1 and owners(session) == ["ben"]


# ---- the decision, one cause at a time ----


def waits(session, join, now=RUN_AT, settings=ENFORCE) -> bool:
    session.expire_all()
    row = session.get(ScheduledJoin, join.id)
    return organizer_bot.guest_should_wait(
        session,
        row,
        now,
        settings,
        connection_ok=lambda other: (
            join_runner.connection_state(session, other) == join_runner.CONNECTED
        ),
    )


def ada_event(session, cast) -> CalendarEvent:
    return session.get(CalendarEvent, cast.ada_join.calendar_event_id)


def test_the_baseline_waits(session, cal):
    cast = cast_of(session, cal)
    assert waits(session, cast.ben_join) and waits(session, cast.cleo_join)
    assert not waits(session, cast.ada_join)  # the organizer never waits for anybody


def _no_grant(session, cast):
    session.query(HourGrant).filter_by(user_id=cast.ada.user.id).delete()


def _hours_used_up(session, cast):
    session.add(
        UsageEntry(
            user_id=cast.ada.user.id,
            email_hash=email_hash(cast.ada.user.email),
            seconds=10 * HOUR,
            counted_at=at(-60),
        )
    )


def _suspended(session, cast):
    cast.ada.user.status = "suspended"


def _needs_reconnect(session, cast):
    cast.ada.conn.status = "needs_reconnect"


def _disconnected(session, cast):
    cast.ada.conn.status = "disconnected"


def _rule_none(session, cast):
    session.get(UserSettings, cast.ada.user.id).auto_join_rule = "none"


def _override_off(session, cast):
    ada_event(session, cast).override = False


def _declined(session, cast):
    ada_event(session, cast).self_response = "declined"


def _event_cancelled(session, cast):
    ada_event(session, cast).status = "cancelled"


def _no_longer_the_organizer(session, cast):
    ada_event(session, cast).organizer_self = False


def _organizer_flag_unknown(session, cast):
    ada_event(session, cast).organizer_self = None


def _join_state(state):
    def change(session, cast):
        session.get(ScheduledJoin, cast.ada_join.id).state = state

    return change


def _at_the_bot_limit(session, cast):
    for number in range(2):  # `user_bot_limit` is 2
        session.add(
            Meeting(
                owner_user_id=cast.ada.user.id,
                url=f"https://meet.google.com/zzz-zzzz-zz{number}",
                platform="google_meet",
                native_id=f"zzz-zzzz-zz{number}",
                template_id=cast.ada.template.id,
                status="bot_sent",
                created_at=at(-5),
            )
        )


def _different_code(session, cast):
    session.get(ScheduledJoin, cast.ada_join.id).native_id = "other-code-xyz"


def _starts_ten_minutes_later(session, cast):
    session.get(ScheduledJoin, cast.ada_join.id).starts_at += timedelta(minutes=10)


@pytest.mark.parametrize(
    "change",
    [
        _no_grant,
        _hours_used_up,
        _suspended,
        _needs_reconnect,
        _disconnected,
        _rule_none,
        _override_off,
        _declined,
        _event_cancelled,
        _no_longer_the_organizer,
        _organizer_flag_unknown,
        _join_state("joined"),
        _join_state("skipped"),
        _join_state("cancelled"),
        _join_state("failed"),
        _join_state("completed"),
        _at_the_bot_limit,
        _different_code,
        _starts_ten_minutes_later,
    ],
    ids=lambda f: getattr(f, "__name__", "join_state"),
)
def test_a_guest_does_not_wait_when(session, cal, change):
    cast = cast_of(session, cal)
    assert waits(session, cast.ben_join)
    change(session, cast)
    session.commit()
    assert not waits(session, cast.ben_join)


def test_a_guest_does_not_wait_past_the_end_of_the_wait_or_with_the_switch_off(session, cal):
    cast = cast_of(session, cal)
    assert waits(session, cast.ben_join, at(0, 59))
    assert not waits(session, cast.ben_join, WAIT_ENDS)
    assert not waits(session, cast.ben_join, WAIT_ENDS + timedelta(hours=1))
    assert not waits(session, cast.ben_join, RUN_AT, SWITCHED_OFF)


def test_a_guest_whose_organizer_flag_is_unknown_still_waits_for_a_known_organizer(session, cal):
    """An event with no organizer information (None) is not 'mine': if another account's copy says
    it organizes the event, the guest holds back."""
    cast = cast_of(session, cal)
    session.get(CalendarEvent, cast.ben_join.calendar_event_id).organizer_self = None
    session.commit()
    assert waits(session, cast.ben_join)


def test_an_unknown_flag_on_both_sides_means_nobody_waits(session, cal):
    cast = cast_of(session, cal)
    for join in (cast.ada_join, cast.ben_join):
        session.get(CalendarEvent, join.calendar_event_id).organizer_self = None
    session.commit()
    assert not waits(session, cast.ben_join)


def test_the_organizer_may_be_on_a_trial_an_admin_or_a_busy_host(session, cal):
    cast = cast_of(session, cal, ada_hours=False)
    assert not waits(session, cast.ben_join)  # no plan, no trial
    give_hours(session, cast.ada.user, kind="trial", seconds=HOUR)
    assert waits(session, cast.ben_join)  # a trial is a plan for this
    session.query(HourGrant).filter_by(user_id=cast.ada.user.id).delete()
    cast.ada.user.is_admin = True
    session.commit()
    assert waits(session, cast.ben_join)  # an admin is unlimited
    cast.ada.user.is_admin = False
    give_hours(session, cast.ada.user)
    session.get(ScheduledJoin, cast.ada_join.id).state = "no_capacity"
    session.commit()
    assert waits(session, cast.ben_join)  # still waiting for room for the organizer's bot


def test_a_start_a_minute_apart_is_the_same_event_and_still_worth_waiting_for(session, cal):
    """The organizer's row is due a minute after the guest's: inside the guest's two-minute wait."""
    cast = cast_of(session, cal)
    ada = session.get(ScheduledJoin, cast.ada_join.id)
    ada.starts_at += timedelta(minutes=1)
    ada.run_at += timedelta(minutes=1)
    session.commit()
    assert waits(session, cast.ben_join)


def test_an_organizer_due_after_the_guests_wait_ends_is_not_waited_for(session, host, cal):
    """Same event, the organizer's copy starts 4 minutes later (inside the 5-minute tolerance), so
    her run time is after the end of the guest's wait: waiting would cost the guest two minutes
    and still lose the slot. The guest records at once."""
    cast = cast_of(session, cal)
    ada = session.get(ScheduledJoin, cast.ada_join.id)
    ada.starts_at += timedelta(minutes=4)
    ada.run_at += timedelta(minutes=4)
    session.commit()
    assert not waits(session, cast.ben_join)

    run(session, host, cal, RUN_AT)

    assert host.bots_sent == 1 and owners(session) == ["ben"]  # the first placed, as today


def test_holding_many_times_never_fails_or_caps_the_guest(session, engine, host, cal):
    """Each hold counts as an attempt; nothing reads a cap, so a guest held every look for the
    whole wait is still placed when it ends (never `failed`)."""
    cast = cast_of(session, cal)
    with organizer_runner_is_busy(engine, cast.ada_join.id):
        for seconds in range(0, 120, 10):  # a tick every 10 s from the run time
            run(session, host, cal, RUN_AT + timedelta(seconds=seconds))
        assert host.bots_sent == 0
        held = session.get(ScheduledJoin, cast.ben_join.id)
        session.refresh(held)
        assert held.attempts >= 5 and held.state == "scheduled"
        run(session, host, cal, WAIT_ENDS)
    assert owners(session) == ["ben"] and state_of(session, cast.ben_join) == ("joined", None)


# ---- the same checks placement uses ----


@pytest.mark.parametrize("mode", ["off", "enforce"])
@pytest.mark.parametrize(
    "case", ["plan", "no_plan", "used_up", "suspended", "admin", "at_limit", "trial"]
)
def test_could_be_placed_says_what_reserve_slot_says(session, mode, case):
    settings = make_settings(hours_mode=mode, bot_max_local=10, user_bot_limit=1)
    world = make_world(session, f"{case}@example.test")
    user = world.user
    now = at(0)
    if case == "plan":
        give_hours(session, user, kind="plan_period")
    elif case == "trial":
        give_hours(session, user, kind="trial", seconds=HOUR)
    elif case == "used_up":
        give_hours(session, user, seconds=HOUR)
        session.add(
            UsageEntry(
                user_id=user.id,
                email_hash=email_hash(user.email),
                seconds=20 * HOUR,
                counted_at=at(-60),
            )
        )
    elif case == "suspended":
        give_hours(session, user)
        user.status = "suspended"
    elif case == "admin":
        user.is_admin = True
    elif case == "at_limit":
        give_hours(session, user)
        session.add(
            Meeting(
                owner_user_id=user.id,
                url="https://meet.google.com/zzz-zzzz-zzz",
                platform="google_meet",
                native_id="zzz-zzzz-zzz",
                template_id=world.template.id,
                status="bot_sent",
                created_at=at(-5),
            )
        )
    session.commit()

    predicted = placement.could_be_placed(session, user, now=now, settings=settings)
    try:
        placement.reserve_slot(
            session,
            user,
            platform="google_meet",
            native_id="free-code-xyz",
            now=now,
            settings=settings,
            wait_seconds=0,
        )
        refused = False
    except PlacementRefused:
        refused = True
    finally:
        session.rollback()

    assert predicted is (not refused), (mode, case)


# ---- ordering, the placement lock and the in-the-call check ----


def test_a_crowd_of_guests_cannot_fill_the_batch_ahead_of_the_organizer(
    session, host, cal, monkeypatch
):
    """With a batch of two and the organizer's row made LAST (the highest id), her row still comes
    first in the tick, so the guests are never the only rows looked at while they wait for her."""
    monkeypatch.setattr(join_runner, "BATCH", 2)
    cast = cast_of(session, cal)
    assert cast.ada_join.id > cast.ben_join.id and cast.ada_join.id > cast.cleo_join.id

    assert join_runner._due_ids(session, RUN_AT, ENFORCE)[0] == cast.ada_join.id
    assert join_runner._due_ids(session, RUN_AT, SWITCHED_OFF)[0] == cast.ben_join.id  # as before

    run(session, host, cal, RUN_AT)

    assert host.bots_sent == 1 and owners(session) == ["ada"]


def test_the_organizer_and_a_guest_racing_after_the_wait_send_exactly_one_bot(engine, host, cal):
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as setup:
        cast_of(setup, cal)
    host.send_delay = 0.2
    barrier = threading.Barrier(2)
    errors: list[Exception] = []

    def runner() -> None:
        try:
            with factory() as session:
                barrier.wait(timeout=10)
                run(session, host, cal, at(2))  # the wait is over for everybody
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=runner) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    with factory() as again:
        run(again, host, cal, at(2, 20))  # a refused-while-locked row is looked at next tick
        run(again, host, cal, at(2, 40))
        rows = list(again.scalars(select(Meeting)))
        states = sorted(again.scalars(select(ScheduledJoin.state)))
        reasons = sorted(
            r or "" for r in again.scalars(select(ScheduledJoin.skip_reason).order_by())
        )
    assert errors == []
    assert host.bots_sent == 1 and host.peak_in_flight == 1 and len(rows) == 1
    assert states == ["joined", "skipped", "skipped"]
    assert reasons == ["", "meeting_has_note_taker", "meeting_has_note_taker"]


# ---- the skipped person's notice ----


def test_the_skipped_persons_notice_is_true_about_sharing(session, host, cal):
    cast = cast_of(session, cal)
    run(session, host, cal, RUN_AT)
    [notice] = [n for n in notes(session) if n.user_id == cast.ben.user.id]
    assert notice.title == "A note taker is already there"
    assert "not available yet" not in notice.body
    assert "Sharing notes between accounts" not in notice.body
    assert "belongs to someone else" in notice.body
    assert (
        "you can read its notes only if they share the meeting with a team you are in, "
        "or move it to one"
    ) in notice.body
    assert "ada" not in notice.body.lower().replace("already", "")  # nobody is named


# ---- Outlook ----

GRAPH = Path(__file__).parent / "fixtures" / "graph"


def graph_event(*, is_organizer: bool) -> dict:
    raw = json.loads((GRAPH / "documented_zoom_in_body__replace_with_m0_capture.json").read_text())
    raw["isOrganizer"] = is_organizer
    raw["start"]["dateTime"] = "2026-11-04T10:00:00.0000000"
    raw["end"]["dateTime"] = "2026-11-04T10:30:00.0000000"
    return raw


def outlook_world(session, email, *, is_organizer: bool):
    from app.gcal_sync import apply_batch

    world = make_world(session, email)
    world.conn.provider = "microsoft"
    session.commit()
    give_hours(session, world.user)
    apply_batch(
        session,
        world.conn,
        batch(graph_event(is_organizer=is_organizer)),
        world.user_settings,
        now=at(-60),
        settings=make_settings(autojoin_lead_seconds=60, teams_join_enabled=False),
    )
    return world


def test_outlooks_organizer_flag_reaches_the_rule(session, engine, host, cal):
    """`isOrganizer` (asked for in `graph_client.py`) is parsed into `organizer_self`, stored by
    the sync, and read by the rule: an Outlook guest holds for an Outlook organizer. The
    documented Graph shape stands in for a live capture (the fixture says so by its name)."""
    guest = outlook_world(session, "ben@example.test", is_organizer=False)
    organizer = outlook_world(session, "ada@example.test", is_organizer=True)
    guest_join, organizer_join = join_row(session, guest), join_row(session, organizer)
    assert session.get(CalendarEvent, guest_join.calendar_event_id).organizer_self is False
    assert session.get(CalendarEvent, organizer_join.calendar_event_id).organizer_self is True
    assert guest_join.native_id == organizer_join.native_id and guest_join.platform == "zoom"

    assert waits(session, guest_join)

    with organizer_runner_is_busy(engine, organizer_join.id):
        run(session, host, cal, RUN_AT)
    assert host.bots_sent == 0
    run(session, host, cal, at(-1, 30))
    assert host.bots_sent == 1 and owners(session) == ["ada"]


# ---- the switches ----


def test_the_switch_defaults_on_and_takes_only_true_or_false():
    assert make_settings().prefer_organizer_bot is True
    assert make_settings().organizer_bot_wait_seconds == 120
    assert make_settings(prefer_organizer_bot="false").prefer_organizer_bot is False
    assert make_settings(prefer_organizer_bot="true").prefer_organizer_bot is True
    with pytest.raises(ValidationError):
        make_settings(prefer_organizer_bot="maybe")
    with pytest.raises(ValidationError):
        make_settings(prefer_organizer_bot="")


@pytest.mark.parametrize("bad", [-1, 601, "soon"])
def test_a_wait_outside_zero_to_six_hundred_stops_startup(bad):
    with pytest.raises(ValidationError):
        make_settings(organizer_bot_wait_seconds=bad)


def test_the_wait_setting_takes_the_ends_of_its_range_and_a_blank_line():
    assert make_settings(organizer_bot_wait_seconds=0).organizer_bot_wait_seconds == 0
    assert make_settings(organizer_bot_wait_seconds=600).organizer_bot_wait_seconds == 600
    assert make_settings(organizer_bot_wait_seconds="").organizer_bot_wait_seconds == 120
