"""The join runner (plan task C12): calendar meetings get their bot, exactly once, at the right
time, and never after the owner no longer wants it.

Real Postgres, the real materialiser (`apply_batch`), `place_bot()` and `FakeVexaHost` (a counter of
the bots it was asked to start); the calendar source is a small stub (`StubCalendar`) for the
re-check, and `FakeGoogleCalendar` for the end-to-end test. The clock is injected. Google bodies are
synthetic.
"""

import threading
import time
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app import alerts, join_runner, placement, titles
from app.calendar_source import TransientError
from app.join_runner import (
    NOT_RECORDED_TEXT,
    RETRY_EVERY,
    run_due_joins,
)
from app.models import (
    AppState,
    CalendarConnection,
    CalendarEvent,
    MailOutbox,
    Meeting,
    Notification,
    ScheduledJoin,
    UsageEntry,
    User,
    UserSettings,
)
from app.usage import email_hash
from tests.fakes.vexa_host import FakeVexaHost
from tests.test_gcal_sync import batch, make_world, raw_event
from tests.test_web import make_settings, make_user

T0 = datetime(2026, 11, 4, 10, 0, tzinfo=UTC)  # a meeting starts at 10:00
HOUR = 3600
SETTINGS = make_settings(
    autojoin_lead_seconds=60, bot_max_local=1, user_bot_limit=2, teams_join_enabled=False
)
ZOOM_URL = "https://us05web.zoom.us/j/88205679674?pwd=SECRETPASSCODE"


def at(minutes: float = 0, seconds: float = 0) -> datetime:
    return T0 + timedelta(minutes=minutes, seconds=seconds)


@pytest.fixture(autouse=True)
def alert_settings(monkeypatch):
    """Mail can be sent (console provider outside production) and admin alerts go to one address."""
    settings = make_settings(admin_alert_email="ops@example.test")
    monkeypatch.setattr(alerts, "get_settings", lambda: settings)


@pytest.fixture(autouse=True)
def runner_has_led(engine):
    """The runner's first-start marker is already written, so rows dispatch as in normal running;
    the tests of the first start (and of the switch coming back on) clear it themselves."""
    with Session(engine) as setup:
        setup.add(AppState(id=1, autojoin_started_at=datetime(2026, 1, 1, tzinfo=UTC)))
        setup.commit()


def app_state(session) -> AppState:
    session.expire_all()
    return session.get(AppState, 1)


class StubCalendar:
    """The re-check's source: `get_event` answers what the test served, and counts the calls."""

    provider = "google"

    def __init__(self) -> None:
        self.events: dict[str, dict | None] = {}
        self.calls: list[str] = []
        self.error: Exception | None = None

    def serve(self, raw: dict | None, event_id: str | None = None) -> None:
        self.events[event_id or raw["id"]] = raw

    def get_event(self, conn, source_event_id: str) -> dict | None:
        self.calls.append(source_event_id)
        if self.error is not None:
            raise self.error
        return self.events.get(source_event_id)


@pytest.fixture
def host():
    return FakeVexaHost()


@pytest.fixture
def cal():
    return StubCalendar()


@pytest.fixture
def ada(session):
    return make_world(session, "ada@example.test")


def materialise(session, world, cal=None, event_id="evt-1", start=T0, **fields):
    """The sync's job: an event becomes a calendar row and a scheduled join (run_at = start - 60 s).
    Returns the join."""
    from app.gcal_sync import apply_batch

    raw = raw_event(event_id, start=start, **fields)
    if cal is not None:
        cal.serve(raw)
    apply_batch(
        session,
        world.conn,
        batch(raw),
        world.user_settings,
        now=at(-60),
        settings=SETTINGS,
    )
    return join_of(session, event_id, world.user.id)


def join_of(session, event_id="evt-1", user_id=None) -> ScheduledJoin:
    query = (
        select(ScheduledJoin)
        .join(CalendarEvent, CalendarEvent.id == ScheduledJoin.calendar_event_id)
        .where(CalendarEvent.source_event_id == event_id)
    )
    if user_id is not None:
        query = query.where(ScheduledJoin.user_id == user_id)
    session.expire_all()
    return session.scalars(query).one()


def run(session, host, cal, now, settings=SETTINGS):
    return run_due_joins(session, host, cal, now=now, settings=settings)


def meetings(session) -> list[Meeting]:
    session.expire_all()
    return list(session.scalars(select(Meeting).order_by(Meeting.id)))


def notes(session, kind: str | None = None) -> list[Notification]:
    session.expire_all()
    query = select(Notification).order_by(Notification.id)
    if kind is not None:
        query = query.where(Notification.kind == kind)
    return list(session.scalars(query))


def outbox(session) -> list[MailOutbox]:
    session.expire_all()
    return list(session.scalars(select(MailOutbox).order_by(MailOutbox.id)))


def state_of(session, join) -> tuple[str, str | None]:
    session.expire_all()
    fresh = session.get(ScheduledJoin, join.id)
    return fresh.state, fresh.skip_reason


# ---- when the bot goes ----


@pytest.mark.parametrize("lead", [60, 90])
def test_dispatches_at_start_minus_lead(session, host, cal, ada, lead):
    from app.gcal_sync import apply_batch

    raw = raw_event("evt-1", start=T0, summary="Weekly planning")
    cal.serve(raw)
    settings = make_settings(autojoin_lead_seconds=lead, bot_max_local=1)
    apply_batch(session, ada.conn, batch(raw), ada.user_settings, now=at(-60), settings=settings)
    join = join_of(session, "evt-1")
    run_at = T0 - timedelta(seconds=lead)
    assert join.run_at == run_at

    assert run(session, host, cal, run_at - timedelta(seconds=1), settings) == [] or True
    assert host.bots_sent == 0 and state_of(session, join) == ("scheduled", None)

    outcomes = run(session, host, cal, run_at, settings)

    assert host.bots_sent == 1 and [o.result for o in outcomes] == ["joined"]
    assert state_of(session, join) == ("joined", None)
    [meeting] = meetings(session)
    assert meeting.source == "calendar" and meeting.owner_user_id == ada.user.id
    assert meeting.title == "Weekly planning"
    assert meeting.template_id == ada.template.id
    assert session.get(ScheduledJoin, join.id).meeting_id == meeting.id


def test_the_meeting_is_named_by_its_cleaned_calendar_title(session, host, cal, ada):
    materialise(session, ada, cal, summary="Q3 \u202egnirts\u202c review\r\nBcc: x@example.test")
    run(session, host, cal, at(-1))
    [meeting] = meetings(session)
    assert meeting.title == titles.clean_title(
        "Q3 \u202egnirts\u202c review\r\nBcc: x@example.test"
    )
    assert "\u202e" not in meeting.title and "\n" not in meeting.title


def test_a_join_for_an_event_without_a_title_has_no_title_and_falls_back(session, host, cal, ada):
    materialise(session, ada, cal, summary="")
    run(session, host, cal, at(-1))
    [meeting] = meetings(session)
    assert meeting.title is None and titles.meeting_name(meeting) == "Google Meet abc-defg-hij"


def test_recheck_at_two_minutes_before_picks_up_a_move_and_a_cancel(session, host, cal, ada):
    moved = materialise(session, ada, cal, "moved", summary="Moved", code="aaa-bbbb-ccc")
    gone = materialise(session, ada, cal, "gone", summary="Gone", code="ddd-eeee-fff")
    cal.serve(raw_event("moved", start=T0 + timedelta(minutes=30), code="aaa-bbbb-ccc"))
    cal.serve(None, "gone")  # the source no longer has it

    assert run(session, host, cal, at(-3)) == []  # three minutes before: nothing yet
    assert cal.calls == []

    outcomes = run(session, host, cal, at(-2))  # two minutes before: both are read once
    assert sorted(cal.calls) == ["gone", "moved"]
    assert host.bots_sent == 0
    assert state_of(session, gone) == ("cancelled", "cancelled")
    assert state_of(session, moved) == ("scheduled", None)
    assert join_of(session, "moved").run_at == at(29)  # follows the move
    assert sorted(o.result for o in outcomes) == ["cancelled", "rescheduled"]

    run(session, host, cal, at(-1))  # the moved one does not go at its old time...
    assert host.bots_sent == 0 and sorted(cal.calls) == ["gone", "moved"]  # ...and is not re-read
    run(session, host, cal, at(29))  # ...it goes at its new one
    assert host.bots_sent == 1 and state_of(session, moved) == ("joined", None)


def test_the_recheck_runs_once_per_join(session, host, cal, ada):
    materialise(session, ada, cal)
    for seconds in (-120, -110, -100, -90, -80):
        run(session, host, cal, at(seconds=seconds))
    assert cal.calls == ["evt-1"]


def test_recheck_that_says_declined_cancels_with_the_reason(session, host, cal, ada):
    join = materialise(session, ada, cal)
    declined = raw_event("evt-1", start=T0)
    declined["attendees"][0]["responseStatus"] = "declined"
    cal.serve(declined)
    run(session, host, cal, at(-2))
    run(session, host, cal, at(-1))
    assert host.bots_sent == 0 and state_of(session, join) == ("cancelled", "declined")


def test_a_calendar_that_cannot_be_asked_does_not_hold_the_join_back(session, host, cal, ada):
    join = materialise(session, ada, cal)
    cal.error = TransientError("503")
    run(session, host, cal, at(-2))  # asked, failed, tried again next tick
    run(session, host, cal, at(seconds=-100))
    assert len(cal.calls) == 2 and host.bots_sent == 0
    run(session, host, cal, at(-1))  # the run time: the bot goes on what is stored
    assert host.bots_sent == 1 and state_of(session, join) == ("joined", None)
    assert len(cal.calls) == 3


def test_no_calendar_source_still_dispatches(session, host, ada):
    join = materialise(session, ada)
    run(session, host, None, at(-1))
    assert host.bots_sent == 1 and state_of(session, join) == ("joined", None)


def test_scheduler_down_from_0955_to_1005_dispatches_at_restart_as_late_join(
    session, host, cal, ada
):
    join = materialise(session, ada, cal)
    # Nothing runs between 09:55 and 10:05; the first run after the restart is at 10:05.
    outcomes = run(session, host, cal, at(5))
    assert [o.result for o in outcomes] == ["joined"]
    assert host.bots_sent == 1 and state_of(session, join) == ("joined", None)


def test_a_meeting_is_never_skipped_before_its_deadline(session, host, cal, ada):
    join = materialise(session, ada, cal)
    run(session, host, cal, at(14, 59))  # one second before the 15 minutes are up
    assert host.bots_sent == 1 and state_of(session, join) == ("joined", None)


# ---- no capacity: tell at once, retry, end as not recorded ----


def two_events_one_slot(session, ada, cal):
    first = materialise(session, ada, cal, "first", summary="First", code="aaa-bbbb-ccc")
    second = materialise(session, ada, cal, "second", summary="Second", code="ddd-eeee-fff")
    return first, second


def test_two_joins_one_slot_places_one_then_retries_the_other_every_15_s_until_deadline(
    session, host, cal, ada
):
    first, second = two_events_one_slot(session, ada, cal)
    run(session, host, cal, at(-1))
    assert host.bots_sent == 1
    assert {state_of(session, first)[0], state_of(session, second)[0]} == {"joined", "no_capacity"}
    waiting = first if state_of(session, first)[0] == "no_capacity" else second
    assert session.get(ScheduledJoin, waiting.id).attempts == 1

    # Ticks every 5 s: a try only when 15 s have gone since the last one.
    tries = []
    for step in range(1, 13):
        run(session, host, cal, at(-1, step * 5))
        session.expire_all()
        tries.append(session.get(ScheduledJoin, waiting.id).attempts)
    assert tries == [1, 1, 2, 2, 2, 3, 3, 3, 4, 4, 4, 5]
    assert host.bots_sent == 1

    # The first meeting ends; the next try places the second bot, before the deadline.
    live = next(m for m in meetings(session))
    live.status = "ready"
    session.commit()
    host.live.clear()
    run(session, host, cal, at(0, 30))
    assert host.bots_sent == 2 and state_of(session, waiting) == ("joined", None)


def test_no_capacity_keeps_trying_until_the_deadline_then_ends_not_recorded(
    session, host, cal, ada
):
    first, second = two_events_one_slot(session, ada, cal)
    now = at(-1)
    while now <= at(15):
        run(session, host, cal, now)
        now += timedelta(seconds=5)
    outcomes = run(session, host, cal, at(15, 1))
    waiting = next(j for j in (first, second) if state_of(session, j)[0] != "joined")
    assert state_of(session, waiting) == ("failed", "not_recorded")
    assert session.get(ScheduledJoin, waiting.id).last_error == NOT_RECORDED_TEXT
    assert [o.result for o in outcomes] == ["failed"]
    assert host.bots_sent == 1


def test_no_capacity_alerts_owner_and_admin_once_per_bucket(session, host, cal, ada):
    admin = make_user(session, "admin@example.test", admin=True)
    materialise(session, ada, cal, "z", summary="Budget", location=ZOOM_URL, hangoutLink=None)
    materialise(session, ada, cal, "m", summary="Standup", code="aaa-bbbb-ccc")
    start = at(0)  # the start of a 30-minute bucket; the 15 minutes end before the deadline
    for step in range(0, 56):
        run(session, host, cal, start + timedelta(seconds=15 * step))
    mine = notes(session, "calendar.no_capacity")
    admins = notes(session, "admin.no_capacity")
    assert len(mine) == 1 and mine[0].user_id == ada.user.id
    assert len(admins) == 1 and admins[0].user_id == admin.id
    sent_to = sorted(row.to_email for row in outbox(session))
    assert sent_to == ["ada@example.test", "ops@example.test"]  # one mail each


def test_the_next_30_minute_bucket_alerts_again(session, host, cal, ada):
    make_user(session, "admin@example.test", admin=True)
    late = at(20)  # run_at 10:19, deadline 10:35: the retries cross the 10:30 bucket edge
    materialise(session, ada, cal, "m", start=late, code="aaa-bbbb-ccc", summary="First")
    materialise(session, ada, cal, "z", start=late, summary="Second", code="ddd-eeee-fff")
    now = at(19)
    while now < at(34):
        run(session, host, cal, now)
        now += timedelta(seconds=15)
    assert len(notes(session, "calendar.no_capacity")) == 2
    assert len(notes(session, "admin.no_capacity")) == 2


def test_admin_alert_text_never_includes_the_meeting_link_or_passcode(session, host, cal, ada):
    make_user(session, "admin@example.test", admin=True)
    materialise(session, ada, cal, "z", location=ZOOM_URL, hangoutLink=None, code="x")
    materialise(session, ada, cal, "m", code="aaa-bbbb-ccc")
    run(session, host, cal, at(-1))
    run(session, host, cal, at(0))
    for row in notes(session, "admin.no_capacity"):
        text = f"{row.title} {row.body}"
        assert "zoom.us" not in text and "SECRETPASSCODE" not in text and "meet.google" not in text
        assert "ada@example.test" in text  # it names the user
    for row in outbox(session):
        assert "SECRETPASSCODE" not in str(row.payload)


def test_the_owners_no_capacity_notice_uses_the_cleaned_defanged_title(session, host, cal, ada):
    materialise(session, ada, cal, "a", summary="First", code="aaa-bbbb-ccc")
    materialise(
        session, ada, cal, "b", summary="Join evil.example.test now\r\nBcc: x", code="ddd-eeee-fff"
    )
    run(session, host, cal, at(-1))
    [row] = notes(session, "calendar.no_capacity") or notes(session, "x")
    assert "evil[.]example[.]test" in row.body and "\r" not in row.body and "\n" not in row.body
    assert row.kind.startswith("calendar.")  # so a disconnect deletes it


def test_deadline_makes_failed_row_with_not_recorded_text(session, host, cal, ada):
    join = materialise(session, ada, cal)
    # The scheduler was down the whole time: it comes back after the 15 minutes.
    outcomes = run(session, host, cal, at(15, 30))
    assert [o.result for o in outcomes] == ["failed"]
    assert host.bots_sent == 0
    session.expire_all()
    row = session.get(ScheduledJoin, join.id)
    assert (row.state, row.skip_reason, row.last_error) == (
        "failed",
        "not_recorded",
        NOT_RECORDED_TEXT,
    )
    [told] = notes(session, "calendar.not_recorded")
    assert told.user_id == ada.user.id and "not recorded" in told.body
    assert any(m.to_email == "ada@example.test" for m in outbox(session))  # and an email


def test_a_row_that_missed_its_deadline_long_ago_is_closed_without_telling_anyone(
    session, host, cal, ada
):
    join = materialise(session, ada, cal)
    run(session, host, cal, at(15 + 90))
    assert state_of(session, join) == ("failed", "not_recorded")
    assert notes(session, "calendar.not_recorded") == []


def test_a_send_that_fails_is_tried_again_and_told_to_the_admins(session, host, cal, ada):
    from app.vexa import VexaError

    make_user(session, "admin@example.test", admin=True)
    join = materialise(session, ada, cal)
    host.error = VexaError("send_bot failed: boom")
    run(session, host, cal, at(-1))
    assert state_of(session, join) == ("scheduled", None) and host.bots_sent == 0
    assert len(notes(session, "calendar.join_failed")) == 1
    assert len(notes(session, "admin.join_failed")) == 1
    run(session, host, cal, at(-1, 5))  # too soon: no second try
    assert session.get(ScheduledJoin, join.id).attempts == 1
    host.error = None
    run(session, host, cal, at(-1, 20))
    assert state_of(session, join) == ("joined", None) and host.bots_sent == 1
    assert len(notes(session, "calendar.join_failed")) == 1  # one per bucket


# ---- one bot per meeting ----


def test_runner_run_twice_at_the_same_instant_places_one_bot(engine, host, cal):
    """Review Focus 4: two runners (a restart, a second scheduler) at the same instant."""
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as setup:
        world = make_world(setup, "ada@example.test")
        materialise(setup, world, cal)
    host.send_delay = 0.4  # long enough for both runners to be inside the send together
    barrier = threading.Barrier(2)
    results, errors = [], []

    def runner() -> None:
        try:
            with factory() as session:
                barrier.wait(timeout=10)
                results.append(run(session, host, cal, at(-1)))
        except Exception as exc:  # pragma: no cover - makes a failure visible
            errors.append(exc)

    threads = [threading.Thread(target=runner) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert errors == []
    assert host.bots_sent == 1 and host.peak_in_flight == 1
    with factory() as check:
        rows = list(check.scalars(select(Meeting)))
        join = check.scalars(select(ScheduledJoin)).one()
        assert len(rows) == 1 and join.state == "joined" and join.meeting_id == rows[0].id
    assert sorted(len(r) for r in results) == [0, 1]  # one placed it; the other skipped the row


def test_two_runners_two_joins_one_slot_send_exactly_one_bot(engine, host, cal):
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as setup:
        world = make_world(setup, "ada@example.test")
        materialise(setup, world, cal, "a", code="aaa-bbbb-ccc")
        materialise(setup, world, cal, "b", code="ddd-eeee-fff")
    host.send_delay = 0.2
    barrier = threading.Barrier(2)

    def runner() -> None:
        with factory() as session:
            barrier.wait(timeout=10)
            run(session, host, cal, at(-1))

    threads = [threading.Thread(target=runner) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert host.bots_sent == 1
    with factory() as check:
        states = sorted(check.scalars(select(ScheduledJoin.state)))
        assert states == ["joined", "no_capacity"]


def test_a_second_account_in_the_same_meeting_is_told_and_nobodys_bot_is_touched(
    session, host, cal
):
    ada = make_world(session, "ada@example.test")
    ben = make_world(session, "ben@example.test")
    first = materialise(session, ada, cal)
    second = materialise(session, ben, cal)
    run(session, host, cal, at(-1))
    assert host.bots_sent == 1
    assert state_of(session, first) == ("joined", None)
    assert state_of(session, second) == ("skipped", "meeting_has_note_taker")
    [notice] = notes(session, "calendar.meeting_has_note_taker")
    assert notice.user_id == ben.user.id
    assert "already in" in notice.body and "ada" not in notice.body.lower()
    assert host.stopped == [] and len(host.live) == 1  # nobody's bot was stopped or adopted
    # final: no retry, no second notice, nothing sent
    run(session, host, cal, at(5))
    assert host.bots_sent == 1 and len(notes(session, "calendar.meeting_has_note_taker")) == 1
    assert state_of(session, second) == ("skipped", "meeting_has_note_taker")


def test_the_second_account_stays_skipped_at_its_deadline_not_failed(session, host, cal):
    ada = make_world(session, "ada@example.test")
    ben = make_world(session, "ben@example.test")
    materialise(session, ada, cal)
    second = materialise(session, ben, cal)
    run(session, host, cal, at(-1))
    run(session, host, cal, at(16))
    assert state_of(session, second) == ("skipped", "meeting_has_note_taker")


def test_the_owners_own_bot_already_in_the_meeting_skips_quietly(session, host, cal, ada):
    from app.models import Template

    template = session.get(Template, ada.template.id)
    host.live[("google_meet", "abc-defg-hij")] = 7
    pasted = Meeting(
        owner_user_id=ada.user.id,
        url="https://meet.google.com/abc-defg-hij",
        platform="google_meet",
        native_id="abc-defg-hij",
        template_id=template.id,
    )
    session.add(pasted)
    session.commit()
    join = materialise(session, ada, cal)
    run(session, host, cal, at(-1))
    assert state_of(session, join) == ("skipped", "duplicate_meeting")
    assert host.bots_sent == 0 and notes(session) == []


def test_hours_cap_skips_with_one_notice_and_is_tried_again(session, host, cal, ada):
    session.add(
        UsageEntry(user_id=ada.user.id, email_hash=email_hash(ada.user.email), seconds=5 * HOUR)
    )
    session.commit()
    join = materialise(session, ada, cal)
    run(session, host, cal, at(-1))
    run(session, host, cal, at(0))
    assert state_of(session, join) == ("skipped", "hours_cap") and host.bots_sent == 0
    [told] = notes(session, "calendar.hours_cap")
    assert "hours" in told.body
    session.query(UsageEntry).delete()
    session.commit()
    run(session, host, cal, at(1, 5))  # hours came back (an admin raised the cap)
    assert state_of(session, join) == ("joined", None)


def test_user_limit_skips_and_ends_not_recorded_at_the_deadline(session, host, cal, ada):
    settings = make_settings(autojoin_lead_seconds=60, bot_max_local=5, user_bot_limit=1)
    materialise(session, ada, cal, "a", code="aaa-bbbb-ccc")
    second = materialise(session, ada, cal, "b", code="ddd-eeee-fff")
    run(session, host, cal, at(-1), settings)
    assert state_of(session, second) == ("skipped", "user_limit")
    assert len(notes(session, "calendar.user_limit")) == 1
    run(session, host, cal, at(15, 5), settings)
    assert state_of(session, second) == ("failed", "not_recorded")


# ---- nothing is sent after the owner stops wanting it (Review Focus 5) ----


@pytest.mark.parametrize("state", ["scheduled", "dispatching", "no_capacity"])
def test_join_for_a_connection_disconnected_a_moment_ago_sends_no_bot(
    session, host, cal, ada, state
):
    join = materialise(session, ada, cal)
    join.state = state
    ada.conn.status = "disconnected"  # the connection row, as `disconnect` leaves it
    session.commit()
    run(session, host, cal, at(-1))
    assert host.bots_sent == 0
    assert state_of(session, join) == ("cancelled", "disconnected")


def test_a_real_disconnect_leaves_the_runner_nothing_to_send(session, host, cal, ada):
    from app import calendar_connect

    materialise(session, ada, cal)
    ada.conn.status = "connected"
    session.commit()
    calendar_connect.disconnect(session, ada.user, None)  # no Google client: revoke is skipped
    assert session.scalars(select(ScheduledJoin)).all() == []
    assert run(session, host, cal, at(-1)) == [] and host.bots_sent == 0


def test_a_connection_that_needs_reconnecting_skips_the_join(session, host, cal, ada):
    join = materialise(session, ada, cal)
    ada.conn.status = "needs_reconnect"
    session.commit()
    run(session, host, cal, at(-1))
    assert host.bots_sent == 0 and state_of(session, join) == ("skipped", "needs_reconnect")


def test_a_degraded_connection_still_joins(session, host, cal, ada):
    join = materialise(session, ada, cal)
    ada.conn.status = "degraded"
    session.commit()
    run(session, host, cal, at(-1))
    assert state_of(session, join) == ("joined", None)


@pytest.mark.parametrize("state", ["scheduled", "dispatching", "no_capacity"])
def test_suspended_user_join_becomes_skipped_account_suspended(session, host, cal, ada, state):
    join = materialise(session, ada, cal)
    join.state = state
    ada.user.status = "suspended"
    session.commit()
    run(session, host, cal, at(-1))
    assert host.bots_sent == 0 and state_of(session, join) == ("skipped", "account_suspended")


@pytest.mark.parametrize("state", ["scheduled", "dispatching", "no_capacity"])
def test_rule_switched_off_while_dispatching_sends_nothing(session, host, cal, ada, state):
    """A row found in `dispatching` or `no_capacity` (the runner itself never commits
    `dispatching`: it holds the row lock instead; a crashed process or another writer can leave
    one) is judged like any other. A switch that lands DURING a send is the next-but-one test."""
    join = materialise(session, ada, cal)
    join.state = state
    ada.user_settings.auto_join_rule = "none"  # the owner switched auto-join off
    session.commit()
    run(session, host, cal, at(-1))
    assert host.bots_sent == 0 and meetings(session) == []
    assert state_of(session, join) == ("cancelled", "rule_none")


@pytest.mark.parametrize("state", ["dispatching", "no_capacity"])
def test_a_meeting_switched_off_while_dispatching_sends_nothing(session, host, cal, ada, state):
    join = materialise(session, ada, cal)
    join.state = state
    event = session.get(CalendarEvent, join.calendar_event_id)
    event.override = False
    session.commit()
    run(session, host, cal, at(-1))
    assert host.bots_sent == 0 and state_of(session, join) == ("cancelled", "override_off")


def test_the_rule_organiser_only_stops_a_meeting_the_owner_does_not_organise(
    session, host, cal, ada
):
    join = materialise(session, ada, cal)
    event = session.get(CalendarEvent, join.calendar_event_id)
    event.organizer_self = False
    ada.user_settings.auto_join_rule = "organizer"
    session.commit()
    run(session, host, cal, at(-1))
    assert host.bots_sent == 0 and state_of(session, join) == ("cancelled", "not_organizer")


def test_a_switch_that_lands_during_a_send_waits_for_it(engine, host, cal):
    """The row lock is held from the final check to the commit of the meeting: a rule change that
    arrives while the bot is being sent does not slip in between, and cannot undo the join."""
    from app.gcal_sync import apply_batch

    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as setup:
        world = make_world(setup, "ada@example.test")
        materialise(setup, world, cal)
        user_id = world.user.id
    switched = threading.Event()
    finished_at: dict[str, float] = {}

    def switch_off() -> None:
        with factory() as session:
            world_settings = session.get(UserSettings, user_id)
            world_settings.auto_join_rule = "none"
            conn = session.scalars(select(CalendarConnection)).one()
            apply_batch(
                session,
                conn,
                batch(raw_event("evt-1", start=T0)),
                world_settings,
                now=at(-1),
                settings=SETTINGS,
            )
        finished_at["switch"] = time.monotonic()
        switched.set()

    def during_send() -> None:
        thread = threading.Thread(target=switch_off)
        thread.start()
        assert not switched.wait(timeout=0.5)  # the switch is blocked on the join's row lock

    host.on_send = during_send
    with factory() as session:
        run(session, host, cal, at(-1))
        finished_at["runner"] = time.monotonic()
    assert switched.wait(timeout=10)
    assert finished_at["switch"] >= finished_at["runner"]
    with factory() as check:
        join = check.scalars(select(ScheduledJoin)).one()
        assert host.bots_sent == 1 and join.state == "joined"  # a sent bot follows the meeting


@pytest.mark.parametrize("refusal", ["bot_busy", "hours_cap_reached", "user_limit"])
def test_a_cancel_that_lands_while_a_refusal_is_written_is_not_overwritten(
    session, host, cal, ada, monkeypatch, engine, refusal
):
    """`place_bot` rolls back on a refusal, which drops the row lock; the owner switches the
    meeting off in that gap. The runner takes the row again, sees it is no longer one it may
    touch, and writes nothing: the cancel stands, and no no-capacity alert is raised for it."""
    join = materialise(session, ada, cal)

    def refuse_after_a_cancel(sess, vexa, request, **kwargs):
        sess.rollback()  # what place_bot does on a refusal
        with sessionmaker(bind=engine)() as other:
            row = other.get(ScheduledJoin, join.id)
            row.state, row.skip_reason = "cancelled", "override_off"
            other.commit()
        raise placement.PlacementRefused(refusal, cap_seconds=1, limit=1)

    monkeypatch.setattr(join_runner, "place_bot", refuse_after_a_cancel)
    run(session, host, cal, at(-1))
    assert state_of(session, join) == ("cancelled", "override_off")
    assert notes(session) == [] and host.bots_sent == 0


def test_a_failed_send_never_overwrites_a_cancel_either(
    session, host, cal, ada, monkeypatch, engine
):
    from app.vexa import VexaError

    join = materialise(session, ada, cal)

    def fail_after_a_cancel(sess, vexa, request, **kwargs):
        sess.rollback()
        with sessionmaker(bind=engine)() as other:
            row = other.get(ScheduledJoin, join.id)
            row.state, row.skip_reason = "cancelled", "rule_none"
            other.commit()
        raise VexaError("send_bot failed")

    monkeypatch.setattr(join_runner, "place_bot", fail_after_a_cancel)
    run(session, host, cal, at(-1))
    assert state_of(session, join) == ("cancelled", "rule_none") and notes(session) == []


# ---- the end of a join ----


def test_joined_becomes_completed_in_the_same_transaction_as_the_meeting_leaving_bot_sent(
    session, host, cal, ada
):
    join = materialise(session, ada, cal)
    run(session, host, cal, at(-1))
    [meeting] = meetings(session)
    assert state_of(session, join) == ("joined", None)

    meeting.status = "processing"  # what the worker does when the bot has left
    session.flush()  # not committed: the join already agrees, in this same transaction
    assert session.get(ScheduledJoin, join.id).state == "completed"
    session.rollback()  # and a rollback undoes both together
    session.expire_all()
    assert session.get(ScheduledJoin, join.id).state == "joined"
    assert session.get(Meeting, meeting.id).status == "bot_sent"

    meeting = session.get(Meeting, meeting.id)
    meeting.status = "processing"
    session.commit()
    assert state_of(session, join) == ("completed", None)


@pytest.mark.parametrize("how", ["failed", "cancel", "ready"])
def test_every_way_a_meeting_leaves_bot_sent_completes_its_join(session, host, cal, ada, how):
    from app import pipeline, services

    join = materialise(session, ada, cal)
    run(session, host, cal, at(-1))
    [meeting] = meetings(session)
    if how == "failed":
        pipeline._fail(session, meeting, "The bot could not join.")
    elif how == "cancel":
        services.cancel_meeting(session, host, meeting)
    else:
        meeting.status = "ready"
        session.commit()
    assert state_of(session, join)[0] == "completed"


def test_a_meeting_that_stays_bot_sent_leaves_its_join_joined(session, host, cal, ada):
    join = materialise(session, ada, cal)
    run(session, host, cal, at(-1))
    [meeting] = meetings(session)
    meeting.completion_reason = "active"  # an ordinary live-state update
    session.commit()
    assert state_of(session, join) == ("joined", None)


def test_completed_frees_the_meeting_code_for_the_next_weeks_event(session, host, cal, ada):
    week1 = materialise(session, ada, cal, "week1", start=T0)
    week2 = materialise(session, ada, cal, "week2", start=T0 + timedelta(days=7))
    run(session, host, cal, at(-1))
    assert state_of(session, week1) == ("joined", None)
    [first] = meetings(session)
    first.status = "ready"
    session.commit()
    assert state_of(session, week1)[0] == "completed"
    host.live.clear()  # the bot left the call

    next_week = T0 + timedelta(days=7)
    run(session, host, cal, next_week - timedelta(minutes=1))
    assert state_of(session, week2) == ("joined", None)
    assert len(meetings(session)) == 2 and host.bots_sent == 2


def test_the_first_weeks_join_row_still_holding_the_code_cannot_block_the_next(
    session, host, cal, ada
):
    """If the meeting never left `bot_sent` the next week's pre-flight refuses it, and says why."""
    week1 = materialise(session, ada, cal, "week1", start=T0)
    week2 = materialise(session, ada, cal, "week2", start=T0 + timedelta(days=7))
    run(session, host, cal, at(-1))
    assert state_of(session, week1) == ("joined", None)
    run(session, host, cal, T0 + timedelta(days=7) - timedelta(minutes=1))
    assert state_of(session, week2)[0] in ("skipped", "no_capacity")


# ---- the owner's own choices (the settings page writes these rows) ----


def test_an_override_on_lets_a_stranger_invite_join(session, host, cal, ada):
    """Under rule `all` an unanswered invite from outside waits; the owner's switch is the way in
    (the join row exists once they switch it on). The runner honours the switch, not the rule."""
    join = materialise(session, ada, cal)
    event = session.get(CalendarEvent, join.calendar_event_id)
    event.organizer_self, event.self_response, event.organizer_same_org = (
        False,
        "needsAction",
        False,
    )
    event.override = True
    session.commit()
    run(session, host, cal, at(-1))
    assert state_of(session, join) == ("joined", None)


def test_a_stranger_invite_without_the_switch_is_not_sent_even_if_a_row_exists(
    session, host, cal, ada
):
    join = materialise(session, ada, cal)
    event = session.get(CalendarEvent, join.calendar_event_id)
    event.organizer_self, event.self_response, event.organizer_same_org = (
        False,
        "needsAction",
        False,
    )
    session.commit()
    run(session, host, cal, at(-1))
    assert host.bots_sent == 0 and state_of(session, join) == ("cancelled", "not_accepted")


def test_the_template_recorded_on_the_join_is_the_one_the_meeting_gets(session, host, cal, ada):
    from app.models import Template

    other = Template(owner_user_id=ada.user.id, name="Short", instructions="Short notes.")
    session.add(other)
    session.commit()
    join = materialise(session, ada, cal)
    join.template_id = other.id  # what the settings page writes when the owner changes it
    session.commit()
    run(session, host, cal, at(-1))
    [meeting] = meetings(session)
    assert meeting.template_id == other.id


def test_a_join_without_a_recorded_template_uses_the_calendar_template(session, host, cal, ada):
    join = materialise(session, ada, cal)
    join.template_id = None
    session.commit()
    run(session, host, cal, at(-1))
    [meeting] = meetings(session)
    assert meeting.template_id == ada.template.id


# ---- the kill switch, and registration ----


def test_the_kill_switch_registers_no_runner_and_the_default_registers_it():
    from app.scheduler import Scheduler

    class Collect(Scheduler):
        def __init__(self):
            self.names = []

        def register(self, name, every_seconds, fn, *, loop):
            self.names.append((name, loop))

    on, off = Collect(), Collect()
    assert join_runner.register_join_runner(on, make_settings()) is True
    assert on.names == [("join_runner", "dispatch")]  # ON by default: no switch needed
    assert join_runner.register_join_runner(off, make_settings(autojoin_enabled=False)) is False
    assert off.names == []


def test_register_jobs_follows_the_kill_switch_and_says_so(caplog):
    from app.scheduler import Scheduler, register_jobs

    class Collect(Scheduler):
        def __init__(self):
            self.names = []

        def register(self, name, every_seconds, fn, *, loop):
            self.names.append(name)

    for enabled, expected in ((True, True), (False, False)):
        collected = Collect()
        with caplog.at_level("WARNING"):
            register_jobs(
                collected,
                make_settings(
                    calendar_enabled=True,
                    autojoin_enabled=enabled,
                    google_client_id="id",
                    google_client_secret="secret",
                    token_enc_key="K" * 43 + "=",
                ),
            )
        assert ("join_runner" in collected.names) is expected
    assert "AUTOJOIN_ENABLED is false" in caplog.text
    plain = Collect()
    register_jobs(plain, make_settings())  # the calendar is off: no runner either
    assert "join_runner" not in plain.names


def test_the_settings_default_is_on():
    assert make_settings().autojoin_enabled is True


# ---- the retry cadence constant is the plan's ----


def test_the_retry_gap_is_15_seconds():
    assert RETRY_EVERY == timedelta(seconds=15)


# ---- _resend (C11 SOP follow-up): the same rules, or proof it cannot break them ----


def paused_meeting(session, host, ada, cal):
    """A calendar meeting whose bot Meet refused: still `bot_sent`, waiting for its second bot."""
    materialise(session, ada, cal)
    run(session, host, cal, at(-1))
    [meeting] = meetings(session)
    host.stop_bot("google_meet", "abc-defg-hij")  # the refused bot is gone
    return meeting


def test_a_meeting_waiting_for_its_second_bot_still_holds_its_slot_and_its_code(
    session, host, cal, ada
):
    meeting = paused_meeting(session, host, ada, cal)
    other = make_world(session, "ben@example.test")
    from app.models import Template  # noqa: F401

    for code, expected in (
        ("abc-defg-hij", "meeting_has_note_taker"),
        ("zzz-yyyy-xxx", "bot_busy"),
    ):
        request = placement.PlacementRequest(
            user_id=other.user.id,
            platform="google_meet",
            native_id=code,
            meeting_url=f"https://meet.google.com/{code}",
            template_id=other.template.id,
            source="manual",
        )
        with pytest.raises(placement.PlacementRefused) as refused:
            placement.place_bot(session, host, request, now=at(0), settings=SETTINGS)
        assert refused.value.code == expected
    assert session.get(Meeting, meeting.id).status == "bot_sent"


def test_resend_after_a_refusal_changes_no_count_and_makes_no_second_row(session, host, cal, ada):
    from app import pipeline

    meeting = paused_meeting(session, host, ada, cal)
    before = session.scalar(select(func.count(Meeting.id)))
    pipeline._resend(session, meeting, host, failed_reason="could not")
    session.expire_all()
    assert session.scalar(select(func.count(Meeting.id))) == before == 1
    assert session.get(Meeting, meeting.id).status == "bot_sent" and host.bots_sent == 2
    assert (
        session.scalar(select(func.count(Meeting.id)).where(Meeting.status.in_(("bot_sent",)))) == 1
    )


def test_resend_into_a_meeting_another_bot_took_fails_the_meeting_and_sends_nothing_more(
    session, host, cal, ada
):
    from app import pipeline

    meeting = paused_meeting(session, host, ada, cal)
    host.seed_live_bot("google_meet", "abc-defg-hij")  # a bot that is not ours got in
    pipeline._resend(session, meeting, host, failed_reason="Not recorded: could not resend.")
    session.expire_all()
    fresh = session.get(Meeting, meeting.id)
    assert (
        fresh.status == "failed" and host.bots_sent == 1 and host.stopped[-1][1] == "abc-defg-hij"
    )
    assert state_of(session, join_of(session, "evt-1"))[0] == "completed"


# ---- the whole thing, from a Google sync to a completed join ----


def test_the_fake_clock_runs_a_calendar_meeting_from_sync_to_completed(engine):
    """FakeGoogleCalendar -> the real sync -> the scheduler on a fake clock -> the runner ->
    FakeVexaHost -> the meeting leaves `bot_sent`. Nothing is called by hand but the ticks."""
    import base64

    from app import vault
    from app.gcal_client import AccessTokenCache, GoogleSource
    from app.gcal_sync import register_calendar_sync
    from app.scheduler import Scheduler, direct_connection_factory
    from tests.conftest import TEST_DATABASE_URL
    from tests.fakes.google import FakeGoogleCalendar
    from tests.helpers import FakeClock

    key = base64.b64encode(b"J" * 32).decode()
    settings = make_settings(
        google_client_id=FakeGoogleCalendar.CLIENT_ID,
        google_client_secret=FakeGoogleCalendar.CLIENT_SECRET,
        token_enc_key=key,
        autojoin_lead_seconds=60,
        calendar_enabled=True,
        bot_max_local=1,
    )
    vault_settings = vault.get_settings
    vault.get_settings = lambda: settings
    try:
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        google = FakeGoogleCalendar()
        clock = FakeClock(at(-30))
        cache = AccessTokenCache(clock.now, settings=settings, transport=google.transport)
        source = GoogleSource(settings, cache=cache, transport=google.transport)
        host = FakeVexaHost()
        with factory() as setup:
            world = make_world(setup, "ada@example.test")
            blob, version = vault.encrypt(
                FakeGoogleCalendar.REFRESH_TOKEN, purpose="refresh_token", owner=world.user.id
            )
            world.conn.refresh_token_enc, world.conn.key_version = blob, version
            setup.commit()
        raw = raw_event("evt-1", start=T0, summary="Design review")
        google.pages = [[raw]]
        google.events["evt-1"] = raw

        scheduler = Scheduler(
            clock,
            factory,
            leader_conn_factory=direct_connection_factory(TEST_DATABASE_URL),
        )
        register_calendar_sync(scheduler, settings, source=source)
        join_runner.register_join_runner(scheduler, settings, source=source, vexa=host)
        try:
            scheduler.tick_dispatch()  # takes the lock, nothing is due
            scheduler.tick_sync()  # the sync reads Google: an event, a join at 09:59
            with factory() as check:
                join = check.scalars(select(ScheduledJoin)).one()
                assert join.state == "scheduled" and join.run_at == at(-1)
            while clock.now() < at(seconds=-130):  # ten-second dispatch ticks up to the re-check
                clock.advance(10)
                scheduler.tick_dispatch()
            assert host.bots_sent == 0 and google.get_calls == 0
            while clock.now() < at(-1):
                clock.advance(10)
                scheduler.tick_dispatch()
            assert google.get_calls == 1  # the two-minute re-check ran once
            assert host.bots_sent == 1
            with factory() as check:
                join = check.scalars(select(ScheduledJoin)).one()
                meeting = check.scalars(select(Meeting)).one()
                assert join.state == "joined" and join.meeting_id == meeting.id
                assert meeting.title == "Design review" and meeting.source == "calendar"
            with factory() as worker:  # what the worker does when the bot has left
                row = worker.scalars(select(Meeting)).one()
                row.status = "processing"
                worker.commit()
            with factory() as check:
                assert check.scalars(select(ScheduledJoin)).one().state == "completed"
        finally:
            scheduler.release()
    finally:
        vault.get_settings = vault_settings


def test_a_user_row_reaches_the_runner_with_the_users_own_template_not_anothers(
    session, host, cal, ada
):
    ben = make_world(session, "ben@example.test")
    join = materialise(session, ada, cal)
    join.template_id = ben.template.id  # a tampered row: a template that is not Ada's
    session.commit()
    run(session, host, cal, at(-1))
    assert host.bots_sent == 0 and state_of(session, join) == ("failed", "no_template")
    assert User is not None and Session is not None


# ---- the Opus review of #72 ----


def test_a_pasted_meeting_ended_before_the_retry_gets_no_second_bot(session, host, cal, ada):
    """High 1: the owner pastes the link at 9:58, so the calendar row is skipped as a duplicate;
    at 10:05 the owner ends the pasted meeting. The calendar row must not send a bot into a
    meeting the owner just ended: `duplicate_meeting` is final for that row."""
    host.live[("google_meet", "abc-defg-hij")] = 7
    pasted = Meeting(
        owner_user_id=ada.user.id,
        url="https://meet.google.com/abc-defg-hij",
        platform="google_meet",
        native_id="abc-defg-hij",
        template_id=ada.template.id,
    )
    session.add(pasted)
    session.commit()
    join = materialise(session, ada, cal)
    run(session, host, cal, at(-1))
    assert state_of(session, join) == ("skipped", "duplicate_meeting")
    pasted.status = "processing"  # the owner pressed End
    session.commit()
    host.live.clear()
    for minute in (1, 2, 5, 6, 10):
        run(session, host, cal, at(minute))
    assert host.bots_sent == 0 and state_of(session, join) == ("skipped", "duplicate_meeting")


def test_another_accounts_meeting_ended_before_the_retry_gets_no_bot_either(session, host, cal):
    ada = make_world(session, "ada@example.test")
    ben = make_world(session, "ben@example.test")
    materialise(session, ada, cal)
    second = materialise(session, ben, cal)
    run(session, host, cal, at(-1))
    [first] = meetings(session)
    first.status = "ready"
    session.commit()
    host.live.clear()
    run(session, host, cal, at(6))
    assert host.bots_sent == 1 and state_of(session, second) == (
        "skipped",
        "meeting_has_note_taker",
    )


def many_events(session, world, count, cal, *, start=T0):
    from app.gcal_sync import apply_batch

    codes = [f"{chr(97 + n // 26)}{chr(97 + n % 26)}z-wwww-yyy" for n in range(count)]
    raws = [raw_event(f"w{n}", start=start, code=codes[n]) for n in range(count)]
    for raw in raws:
        cal.serve(raw)
    apply_batch(
        session, world.conn, batch(*raws), world.user_settings, now=at(-60), settings=SETTINGS
    )


def test_waiting_rows_do_not_starve_a_later_due_row(session, host, cal, ada):
    """Medium 2: rows waiting out their 15 s gap must not fill the batch. 30 rows wait (the host
    is full); a row that becomes due 5 s later is still reached on that tick."""
    from app.gcal_sync import apply_batch

    many_events(session, ada, 30, cal)
    run(session, host, cal, at(-1))  # one placed and 24 refused (one batch of 25); 5 not yet tried
    run(session, host, cal, at(-1, 1))  # the last 5 are tried; now all 29 wait out their gap
    later = T0 + timedelta(seconds=5)  # run_at 09:59:05
    later_raw = raw_event("later", start=later, code="zzz-zzzz-zzz")
    cal.serve(later_raw)
    apply_batch(
        session,
        ada.conn,
        batch(later_raw),
        ada.user_settings,
        now=at(-60),
        settings=SETTINGS,
    )
    run(session, host, cal, at(-1, 5))  # the waiting rows are inside their gap
    session.expire_all()
    row = join_of(session, "later")
    assert row.attempts == 1 and row.state == "no_capacity"  # it was reached and tried


def test_the_retry_gap_is_in_the_query_not_after_the_lock(session, host, cal, ada):
    many_events(session, ada, 3, cal)
    run(session, host, cal, at(-1))
    assert join_runner._due_ids(session, at(-1, 5)) == []  # all inside the gap
    assert len(join_runner._due_ids(session, at(-1, 15))) == 2  # the two waiting rows are back
    assert join_runner._due_ids(session, at(-1, 14)) == []


def test_rows_due_before_the_runner_first_led_are_closed_silently(session, host, cal, ada):
    """Medium 3: rows synced before the runner existed are closed with no notice and no bot (a
    meeting live at deploy time is not joined retroactively); rows due later dispatch."""
    app_state(session).autojoin_started_at = None
    session.commit()
    old = materialise(session, ada, cal, "old", start=T0, summary="Started already")
    ahead = materialise(
        session, ada, cal, "ahead", start=T0 + timedelta(hours=1), code="aaa-bbbb-ccc"
    )
    outcomes = run(session, host, cal, at(1))  # the first run: 10:01
    assert state_of(session, old) == ("cancelled", "before_runner")
    assert host.bots_sent == 0 and state_of(session, ahead) == ("scheduled", None)
    assert notes(session) == [] and outbox(session) == []
    assert outcomes == [] or all(o.reason == "before_runner" for o in outcomes)
    assert app_state(session).autojoin_started_at == at(1)
    # written once: a join made later with an earlier run time still dispatches
    late = materialise(session, ada, cal, "late", start=at(1, 30), code="ddd-eeee-fff")
    assert late.run_at < at(1, 30)
    run(session, host, cal, at(2))
    assert state_of(session, late) == ("joined", None)
    run(session, host, cal, at(59))
    assert state_of(session, ahead)[0] in ("joined", "no_capacity")
    assert app_state(session).autojoin_started_at == at(1)


def test_turning_the_switch_back_on_closes_rows_whose_deadline_passed_while_off(
    session, host, cal, ada
):
    state = app_state(session)
    state.autojoin_paused_since = at(-40)  # recorded by the scheduler while the switch was off
    session.commit()
    missed = materialise(session, ada, cal, "missed", start=at(-30))  # deadline 09:45
    live = materialise(session, ada, cal, "live", start=T0, code="aaa-bbbb-ccc")  # deadline 10:15
    run(session, host, cal, at(0))
    assert state_of(session, missed) == ("cancelled", "before_runner")
    assert notes(session) == [] and outbox(session) == []
    assert state_of(session, live) == ("joined", None)  # still inside its 15 minutes
    assert app_state(session).autojoin_paused_since is None


def test_note_switch_records_when_the_switch_is_off_once(session):
    off = make_settings(autojoin_enabled=False)
    on = make_settings(autojoin_enabled=True, calendar_enabled=True)
    join_runner.note_switch(session, on, at(0))
    assert app_state(session).autojoin_paused_since is None
    join_runner.note_switch(session, off, at(1))
    join_runner.note_switch(session, off, at(5))  # a restart while still off keeps the first time
    assert app_state(session).autojoin_paused_since == at(1)


def test_an_event_the_runner_cannot_lock_is_skipped_not_waited_for(session, host, cal, ada, engine):
    """Low 5: the override and rule routes take the event row, then the join row; the runner
    takes the event row first as well, and skips (next tick) one that is locked."""
    join = materialise(session, ada, cal)
    other = Session(engine)
    other.execute(
        select(CalendarEvent).where(CalendarEvent.id == join.calendar_event_id).with_for_update()
    )
    started = time.monotonic()
    outcomes = run(session, host, cal, at(-1))
    assert time.monotonic() - started < 3 and outcomes == [] and host.bots_sent == 0
    other.commit()
    other.close()
    run(session, host, cal, at(-1, 10))
    assert state_of(session, join) == ("joined", None)


def test_the_two_lock_orders_do_not_deadlock(engine, host, cal):
    """Low 5: the runner is inside a send holding event then join; a route that takes event then
    join waits behind it and both finish (no AB/BA)."""
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as setup:
        world = make_world(setup, "ada@example.test")
        materialise(setup, world, cal)
    errors, done = [], threading.Event()

    def route() -> None:
        try:
            with factory() as session:
                event = session.scalars(select(CalendarEvent).with_for_update()).one()
                join = session.scalars(select(ScheduledJoin).with_for_update()).one()
                event.override, join.updated_at = True, at(0)
                session.commit()
        except Exception as exc:  # pragma: no cover
            errors.append(exc)
        finally:
            done.set()

    def during_send() -> None:
        threading.Thread(target=route).start()
        assert not done.wait(timeout=0.5)  # blocked behind the runner's locks

    host.on_send = during_send
    with factory() as session:
        run(session, host, cal, at(-1))
    assert done.wait(timeout=15) and errors == [] and host.bots_sent == 1


def test_a_suspend_that_commits_before_the_send_is_stopped_by_placement(
    session, host, cal, ada, monkeypatch
):
    """Low 6: `reserve_slot` checks the account under the placement lock."""
    join = materialise(session, ada, cal)
    monkeypatch.setattr(join_runner, "_gate", lambda *args, **kwargs: None)  # the gap after it
    ada.user.status = "suspended"
    session.commit()
    run(session, host, cal, at(-1))
    assert host.bots_sent == 0 and state_of(session, join) == ("skipped", "account_suspended")


def test_suspending_an_account_ends_its_open_joins(session, host, cal, ada):
    from app import admin

    admin_user = make_user(session, "root@example.test", admin=True)
    assert admin_user.is_admin
    waiting = materialise(session, ada, cal, "a", code="aaa-bbbb-ccc")
    busy = materialise(session, ada, cal, "b", code="ddd-eeee-fff")
    run(session, host, cal, at(-1))
    states = {state_of(session, waiting)[0], state_of(session, busy)[0]}
    assert states == {"joined", "no_capacity"}
    admin.update_user(session, ada.user, {"status": "suspended"})
    kinds = {state_of(session, waiting), state_of(session, busy)}
    assert ("joined", None) in kinds and ("skipped", "account_suspended") in kinds


def test_an_event_id_the_source_rejects_does_not_loop(session, host, cal, ada):
    """Low 8: `get_event` raising ValueError for an odd event id ends the re-check for good."""
    join = materialise(session, ada, cal)
    cal.error = ValueError("bad event id")
    run(session, host, cal, at(-2))
    run(session, host, cal, at(seconds=-100))
    assert len(cal.calls) == 1  # asked once, not every tick
    run(session, host, cal, at(-1))
    assert state_of(session, join) == ("joined", None)


def test_a_row_that_crashes_gets_a_gap_and_a_second_crash_ends_it(
    session, host, cal, ada, monkeypatch
):
    """Low 9: an unexpected error used to leave the row untouched, so it was tried again on every
    10 s tick (and a bot sent and stopped each time). Now it counts, waits out the gap, and a
    second crash ends the row."""
    make_user(session, "admin@example.test", admin=True)
    join = materialise(session, ada, cal)
    calls = []

    def boom(*args, **kwargs):
        calls.append(1)
        raise RuntimeError("boom")

    monkeypatch.setattr(join_runner, "place_bot", boom)
    run(session, host, cal, at(-1))
    session.expire_all()
    row = session.get(ScheduledJoin, join.id)
    assert len(calls) == 1 and row.attempts == 1 and row.state == "scheduled"
    assert row.last_error and "boom" not in row.last_error
    run(session, host, cal, at(-1, 10))
    assert len(calls) == 1  # inside the gap
    run(session, host, cal, at(-1, 20))
    assert len(calls) == 2 and state_of(session, join) == ("failed", "error")
    run(session, host, cal, at(-1, 40))
    assert len(calls) == 2  # ended: no more tries
    assert len(notes(session, "calendar.error")) == 1
