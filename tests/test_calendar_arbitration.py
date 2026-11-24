"""The same event in several of a person's workspaces records exactly once, in the right one
(workspaces design 12.4; plan task 9).

Real Postgres, the real materialiser (`apply_batch`), the real join runner and `FakeBotHost`. Each
workspace has its own connection, its own auto-join rule (`WorkspacePrefs`) and its own copy of the
event. The chooser, in order: a bot already in keeps the meeting; a workspace where the person
switched it ON wins; otherwise the first-connected workspace whose rules would join it; none when
none would. Google bodies are synthetic and nothing here ran against a real calendar.
"""

import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app import calendar_arbitration, calendar_prefs, gcal_sync, teams
from app.calendar_arbitration import JoinCandidate, choose_workspace
from app.gcal_sync import apply_batch, rerun_rules
from app.join_runner import run_due_joins
from app.models import (
    AppState,
    CalendarConnection,
    CalendarEvent,
    Meeting,
    ScheduledJoin,
    Team,
    Template,
    UserSettings,
    WorkspacePrefs,
)
from tests.fakes.bot_host import FakeBotHost
from tests.test_gcal_sync import HOUR, NOW, SETTINGS, batch, raw_event
from tests.test_join_runner import StubCalendar
from tests.test_multi_team_leaks import new_team
from tests.test_web import make_user

# ---- the chooser, alone ----


def cand(team, conn, *, join=True, override=None, locked=False) -> JoinCandidate:
    return JoinCandidate(
        team_id=team,
        connection_id=conn,
        event_id=conn * 10,
        override=override,
        would_join=join,
        locked=locked,
    )


def test_the_first_connected_workspace_whose_rules_would_join_it_wins():
    assert choose_workspace([cand(2, 20), cand(1, 10)]).team_id == 1


def test_when_the_first_connected_workspace_would_skip_it_the_other_records():
    assert choose_workspace([cand(1, 10, join=False), cand(2, 20)]).team_id == 2


def test_a_workspace_where_the_meeting_was_switched_on_beats_the_first_connected():
    chosen = choose_workspace([cand(1, 10), cand(2, 20, override=True)])
    assert chosen.team_id == 2


def test_a_bot_already_in_keeps_the_meeting_whatever_the_rules_say_now():
    chosen = choose_workspace(
        [cand(1, 10), cand(2, 20, override=True), cand(3, 30, join=False, locked=True)]
    )
    assert chosen.team_id == 3


def test_nobody_records_it_when_nobody_would():
    assert choose_workspace([cand(1, 10, join=False), cand(2, 20, join=False)]) is None
    assert choose_workspace([]) is None


def test_two_switched_on_workspaces_go_to_the_first_connected_of_them():
    assert choose_workspace([cand(2, 20, override=True), cand(3, 30, override=True)]).team_id == 2


# ---- two connections of one person ----

CODE = "abc-defg-hij"


@dataclass
class Duo:
    user: object
    template: Template
    home: Team
    luran: Team
    a: CalendarConnection  # the personal workspace: connected first
    b: CalendarConnection  # Luran: connected second


def make_duo(session, email="ada@example.test") -> Duo:
    user = make_user(session, email)
    home = teams.ensure_personal_team(session, user)
    session.commit()
    luran = new_team(session, user, "Luran")
    template = Template(owner_user_id=user.id, name="General", instructions="Notes.")
    session.add(template)
    session.flush()
    session.add(UserSettings(user_id=user.id, calendar_template_id=template.id))
    a = CalendarConnection(user_id=user.id, team_id=home.id, provider="google")
    session.add(a)
    session.flush()
    b = CalendarConnection(user_id=user.id, team_id=luran.id, provider="google")
    session.add(b)
    session.commit()
    return Duo(user, template, home, luran, a, b)


@pytest.fixture
def duo(session) -> Duo:
    return make_duo(session)


def set_rule(session, duo, team, rule="all"):
    row = calendar_prefs.for_update(session, duo.user.id, team.id)
    row.auto_join_rule = rule
    row.calendar_template_id = duo.template.id
    session.commit()


def sync(session, conn, *events, now=NOW, settings=SETTINGS, **kwargs):
    prefs = calendar_prefs.for_workspace(session, conn.user_id, conn.team_id)
    return apply_batch(session, conn, batch(*events), prefs, now=now, settings=settings, **kwargs)


def joins(session) -> list[ScheduledJoin]:
    session.expire_all()
    return list(session.scalars(select(ScheduledJoin).order_by(ScheduledJoin.id)))


def live_joins(session) -> list[ScheduledJoin]:
    """Joins that would still send a bot (not cancelled): there must never be two."""
    return [j for j in joins(session) if j.state not in ("cancelled",)]


def event_of(session, conn) -> CalendarEvent:
    session.expire_all()
    return session.scalars(
        select(CalendarEvent).where(CalendarEvent.connection_id == conn.id)
    ).one()


def join_of_conn(session, conn) -> ScheduledJoin | None:
    session.expire_all()
    return session.scalars(
        select(ScheduledJoin)
        .join(CalendarEvent, CalendarEvent.id == ScheduledJoin.calendar_event_id)
        .where(CalendarEvent.connection_id == conn.id)
    ).first()


def start_event(**fields):
    return raw_event("evt-1", start=NOW + HOUR, code=CODE, **fields)


# ---- through the materialiser ----


@pytest.mark.parametrize("first", ["a", "b"])
def test_the_same_event_in_two_workspaces_makes_one_join_in_the_first_connected_one(
    session, duo, first
):
    order = [duo.a, duo.b] if first == "a" else [duo.b, duo.a]
    for conn in order:
        sync(session, conn, start_event())

    [only] = live_joins(session)
    assert only.team_id == duo.home.id  # a was connected first, whichever synced first
    loser = join_of_conn(session, duo.b)
    assert loser is None or (loser.state, loser.skip_reason) == (
        "cancelled",
        calendar_arbitration.RECORDED_ELSEWHERE,
    )


def test_a_workspace_whose_rule_skips_the_event_leaves_it_to_the_other(session, duo):
    set_rule(session, duo, duo.home, "none")
    set_rule(session, duo, duo.luran, "all")

    sync(session, duo.a, start_event())
    sync(session, duo.b, start_event())

    [only] = live_joins(session)
    assert only.team_id == duo.luran.id


def test_events_of_different_meetings_each_get_their_own_join(session, duo):
    sync(session, duo.a, raw_event("evt-1", start=NOW + HOUR, code="aaa-bbbb-ccc"))
    sync(session, duo.b, raw_event("evt-2", start=NOW + HOUR, code="ddd-eeee-fff"))

    assert sorted(j.team_id for j in live_joins(session)) == sorted([duo.home.id, duo.luran.id])


def test_the_same_code_at_another_time_is_another_meeting(session, duo):
    # a personal Zoom room or one Meet link reused for a later meeting
    sync(session, duo.a, raw_event("evt-1", start=NOW + HOUR, code=CODE))
    sync(session, duo.b, raw_event("evt-1", start=NOW + 3 * HOUR, code=CODE))

    assert len(live_joins(session)) == 2


def test_switching_it_on_in_the_other_workspace_moves_the_join_and_cancels_the_first(session, duo):
    sync(session, duo.a, start_event())
    sync(session, duo.b, start_event())
    assert [j.team_id for j in live_joins(session)] == [duo.home.id]

    row_b = event_of(session, duo.b)
    row_b.override = True
    rerun_rules(
        session,
        duo.b,
        calendar_prefs.for_workspace(session, duo.user.id, duo.luran.id),
        [row_b],
        now=NOW,
        settings=SETTINGS,
    )

    assert [j.team_id for j in live_joins(session)] == [duo.luran.id]
    moved_from = join_of_conn(session, duo.a)
    assert (moved_from.state, moved_from.skip_reason) == (
        "cancelled",
        calendar_arbitration.RECORDED_ELSEWHERE,
    )


def test_clearing_the_switch_gives_the_meeting_back_to_the_first_connected(session, duo):
    sync(session, duo.a, start_event())
    sync(session, duo.b, start_event())
    row_b = event_of(session, duo.b)
    prefs_b = calendar_prefs.for_workspace(session, duo.user.id, duo.luran.id)
    row_b.override = True
    rerun_rules(session, duo.b, prefs_b, [row_b], now=NOW, settings=SETTINGS)

    row_b.override = None
    rerun_rules(session, duo.b, prefs_b, [row_b], now=NOW, settings=SETTINGS)

    [only] = live_joins(session)
    assert (only.team_id, only.state) == (duo.home.id, "scheduled")  # revived, not duplicated
    assert len(joins(session)) == 2


def test_switching_a_workspace_off_hands_the_meeting_to_the_other_one(session, duo):
    sync(session, duo.a, start_event())
    sync(session, duo.b, start_event())
    row_a = event_of(session, duo.a)
    row_a.override = False  # the person says: not from this workspace

    rerun_rules(
        session,
        duo.a,
        calendar_prefs.for_workspace(session, duo.user.id, duo.home.id),
        [row_a],
        now=NOW,
        settings=SETTINGS,
    )

    [only] = live_joins(session)
    assert only.team_id == duo.luran.id


def test_a_running_bot_is_never_moved_by_a_switch(session, duo):
    sync(session, duo.a, start_event())
    sync(session, duo.b, start_event())
    held = join_of_conn(session, duo.a)
    meeting = Meeting(
        owner_user_id=duo.user.id,
        url=held.meeting_url,
        platform=held.platform,
        native_id=held.native_id,
        template_id=duo.template.id,
        status="bot_sent",
        workspace_team_id=duo.home.id,
    )
    session.add(meeting)
    session.flush()
    held.state, held.meeting_id = "joined", meeting.id
    session.commit()

    row_b = event_of(session, duo.b)
    row_b.override = True
    rerun_rules(
        session,
        duo.b,
        calendar_prefs.for_workspace(session, duo.user.id, duo.luran.id),
        [row_b],
        now=NOW,
        settings=SETTINGS,
    )

    assert [(j.team_id, j.state) for j in live_joins(session)] == [(duo.home.id, "joined")]
    assert join_of_conn(session, duo.b) is None or join_of_conn(session, duo.b).state == "cancelled"


def test_a_workspace_with_no_row_of_its_own_uses_the_persons_defaults(session, duo):
    # neither workspace has a prefs row: both fall back to the person's own `all`
    assert session.get(WorkspacePrefs, (duo.luran.id, duo.user.id)) is None
    sync(session, duo.b, start_event())

    assert [j.team_id for j in live_joins(session)] == [duo.luran.id]


def test_the_meeting_is_born_in_the_winners_workspace(session, duo):
    host, cal = FakeBotHost(), StubCalendar()
    session.add(AppState(id=1, autojoin_started_at=datetime(2026, 1, 1, tzinfo=UTC)))
    session.commit()
    set_rule(session, duo, duo.home, "none")  # the first-connected workspace skips it
    sync(session, duo.a, start_event())
    sync(session, duo.b, start_event())

    cal.serve(start_event())  # the two-minute re-check finds the event unchanged
    run_due_joins(session, host, cal, now=NOW + HOUR - timedelta(seconds=60), settings=SETTINGS)

    session.expire_all()
    [meeting] = session.scalars(select(Meeting)).all()
    assert meeting.workspace_team_id == duo.luran.id
    assert host.bots_sent == 1


def test_the_runner_reads_the_rule_of_the_joins_own_workspace(session, duo):
    host, cal = FakeBotHost(), StubCalendar()
    session.add(AppState(id=1, autojoin_started_at=datetime(2026, 1, 1, tzinfo=UTC)))
    session.commit()
    sync(session, duo.b, start_event())
    cal.serve(start_event())
    set_rule(session, duo, duo.luran, "none")  # changed after the join was planned
    set_rule(session, duo, duo.home, "all")  # another workspace's rule must not matter

    run_due_joins(session, host, cal, now=NOW + HOUR - timedelta(seconds=60), settings=SETTINGS)

    [only] = joins(session)
    assert (only.team_id, only.state, only.skip_reason) == (duo.luran.id, "cancelled", "rule_none")
    assert host.bots_sent == 0


def test_remove_recurring_in_one_workspace_leaves_the_other(session, duo):
    from app import series
    from app.models import SeriesIgnored

    session.add(SeriesIgnored(team_id=duo.home.id, calendar_recurring_id="rid-1"))
    session.commit()

    assert series.is_ignored(session, duo.home.id, "rid-1") is True
    assert series.is_ignored(session, duo.luran.id, "rid-1") is False


# ---- two syncs at once ----


def test_two_connections_syncing_at_once_record_the_meeting_once(engine):
    """The arbitration runs under one lock per person: the second sync sees the first one's
    committed rows. Without it each would see an empty field and each would make a join."""
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as setup:
        made = make_duo(setup)
        ids = (made.a.id, made.b.id, made.user.id)
    real_lock = calendar_arbitration.lock_person
    slow = []

    def lock_then_linger(session, user_id):
        real_lock(session, user_id)
        slow.append(1)
        time.sleep(0.4)  # the other sync is inside its transaction by now

    barrier = threading.Barrier(2)
    errors = []

    def go(conn_id):
        try:
            with factory() as session:
                conn = session.get(CalendarConnection, conn_id)
                barrier.wait(timeout=10)
                sync(session, conn, start_event())
        except Exception as exc:  # pragma: no cover - makes a failure visible
            errors.append(exc)

    from unittest import mock

    with mock.patch.object(calendar_arbitration, "lock_person", lock_then_linger):
        threads = [threading.Thread(target=go, args=(i,)) for i in ids[:2]]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

    assert errors == [] and slow, "the arbitration lock was never taken"
    with factory() as check:
        rows = list(check.scalars(select(ScheduledJoin)))
        assert [r.state for r in rows if r.state != "cancelled"] == ["scheduled"]
        assert len(list(check.scalars(select(CalendarEvent)))) == 2  # both calendars show it


def test_the_live_code_index_still_holds_for_the_person(session, duo):
    # the guarantee under the chooser: two live joins on one code for one person cannot exist
    from sqlalchemy.exc import IntegrityError

    sync(session, duo.a, start_event())
    held = join_of_conn(session, duo.a)
    held.state = "joined"
    session.commit()
    twin = ScheduledJoin(
        user_id=duo.user.id,
        team_id=duo.luran.id,
        platform=held.platform,
        native_id=held.native_id,
        meeting_url=held.meeting_url,
        starts_at=held.starts_at,
        ends_at=held.ends_at,
        run_at=held.run_at,
        deadline_at=held.deadline_at,
        state="no_capacity",
    )
    session.add(twin)
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_gcal_sync_exports_the_default_settings_for_older_callers():
    assert gcal_sync.DEFAULT_USER_SETTINGS.auto_join_rule == "all"
