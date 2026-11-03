"""The scheduler skeleton: leader lock, two loops, injected clock, heartbeat.

The lock tests need a real Postgres (a session-level advisory lock is the thing under test), so
they use the same database as the rest of the suite.
"""

import logging
import threading
from datetime import timedelta

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.clock import Clock, SystemClock
from app.models import AppState
from app.scheduler import Scheduler, direct_connection_factory
from tests.conftest import TEST_DATABASE_URL
from tests.helpers import FakeClock


@pytest.fixture
def session_factory(engine):
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def make_scheduler(session_factory):
    """Build schedulers sharing one fake clock; every one is shut down at the end of the test."""
    clock = FakeClock()
    built: list[Scheduler] = []

    def build(**kwargs) -> Scheduler:
        scheduler = Scheduler(
            clock,
            session_factory,
            leader_conn_factory=direct_connection_factory(TEST_DATABASE_URL),
            **kwargs,
        )
        built.append(scheduler)
        return scheduler

    build.clock = clock
    yield build
    for scheduler in built:
        scheduler.release()


def heartbeat(session_factory):
    with session_factory() as session:
        state = session.get(AppState, 1)
        return state.scheduler_heartbeat_at if state else None


def test_system_clock_is_a_clock_and_timezone_aware():
    clock: Clock = SystemClock()
    assert clock.now().tzinfo is not None


def test_second_scheduler_cannot_lead_while_first_holds_lock(make_scheduler):
    first, second = make_scheduler(), make_scheduler()
    assert first.try_lead() is True
    assert second.try_lead() is False
    assert first.is_leader and not second.is_leader
    assert first.try_lead() is True  # asking again while leading is a no-op, not a second lock


def test_standby_takes_over_within_one_retry_after_leader_connection_closes(make_scheduler):
    leader, standby = make_scheduler(), make_scheduler()
    assert leader.try_lead()
    assert not standby.try_lead()
    leader.release()  # the process died or its connection was cut: Postgres frees the lock
    assert standby.try_lead() is True
    assert not leader.is_leader


def test_leader_whose_connection_was_killed_stops_leading(make_scheduler, engine):
    leader, standby = make_scheduler(), make_scheduler()
    assert leader.try_lead()
    with engine.begin() as conn:  # the server drops the leader's connection
        conn.execute(
            text(
                "select pg_terminate_backend(pid) from pg_locks "
                "where locktype = 'advisory' and granted"
            )
        )
    assert standby.try_lead() is True  # the lock was freed with the connection
    assert leader.try_lead() is False  # the old leader notices, stands down, finds it taken
    assert not leader.is_leader


def test_restart_at_run_time_has_no_duplicate_leader(make_scheduler):
    """A leader restarts (new object, same lock) while a standby waits: never two leaders, and a
    job that came due during the downtime runs once, not once per missed interval."""
    runs: list[object] = []
    first, standby = make_scheduler(), make_scheduler()
    for scheduler in (first, standby):
        scheduler.register("job", 10, lambda s, now: runs.append(now), loop="dispatch")
    first.tick_dispatch()
    standby.tick_dispatch()
    assert [first.is_leader, standby.is_leader] == [True, False]
    assert len(runs) == 1

    first.release()  # restart
    restarted = make_scheduler()
    restarted.register("job", 10, lambda s, now: runs.append(now), loop="dispatch")
    make_scheduler.clock.advance(3600)  # an hour of downtime
    for scheduler in (restarted, standby):  # whoever ticks first takes the lock
        scheduler.tick_dispatch()
    assert sorted([restarted.is_leader, standby.is_leader]) == [False, True]
    assert len(runs) == 2  # one catch-up run, not 360


def test_jobs_run_at_their_interval_on_the_fake_clock(make_scheduler):
    scheduler = make_scheduler()
    seen: dict[str, int] = {"fast": 0, "hourly": 0, "sync": 0}
    scheduler.register(
        "fast", 10, lambda s, now: seen.__setitem__("fast", seen["fast"] + 1), loop="dispatch"
    )
    scheduler.register(
        "hourly",
        3600,
        lambda s, now: seen.__setitem__("hourly", seen["hourly"] + 1),
        loop="dispatch",
    )
    scheduler.register(
        "sync", 300, lambda s, now: seen.__setitem__("sync", seen["sync"] + 1), loop="sync"
    )
    clock = make_scheduler.clock
    for _ in range(86400 // 10):  # a whole simulated day
        scheduler.tick_dispatch()
        if clock.now().second == 0:  # the sync loop wakes once a minute
            scheduler.tick_sync()
        clock.advance(10)
    assert seen["fast"] == 8640
    assert seen["hourly"] == 24
    assert seen["sync"] == 288


def test_job_receives_a_working_session_and_the_clock_time(make_scheduler):
    scheduler = make_scheduler()
    got = {}

    def job(session, now):
        got["one"] = session.execute(text("select 1")).scalar()
        got["now"] = now

    scheduler.register("job", 10, job, loop="dispatch")
    scheduler.tick_dispatch()
    assert got == {"one": 1, "now": make_scheduler.clock.now()}


def test_failing_job_does_not_stop_other_jobs_and_logs_without_secrets(make_scheduler, caplog):
    scheduler = make_scheduler()
    ran: list[str] = []

    def broken(session, now):
        session.execute(text("select 1"))
        raise RuntimeError("token=sk-very-secret-value postgresql://u:hunter2@db/x")

    scheduler.register("broken", 10, broken, loop="dispatch")
    scheduler.register("fine", 10, lambda s, now: ran.append("fine"), loop="dispatch")
    with caplog.at_level(logging.DEBUG):
        scheduler.tick_dispatch()
        make_scheduler.clock.advance(10)
        scheduler.tick_dispatch()
    assert ran == ["fine", "fine"]  # still runs, and the failing job is tried again next time
    text_logged = caplog.text
    assert "broken" in text_logged and "RuntimeError" in text_logged
    assert "sk-very-secret-value" not in text_logged
    assert "hunter2" not in text_logged


def test_job_that_leaves_its_transaction_broken_cannot_poison_the_next_job(make_scheduler):
    scheduler = make_scheduler()
    ran: list[int] = []

    def poisoned(session, now):
        session.execute(text("select * from table_that_does_not_exist"))

    scheduler.register("poisoned", 10, poisoned, loop="dispatch")
    scheduler.register(
        "next", 10, lambda s, now: ran.append(s.execute(text("select 1")).scalar()), loop="dispatch"
    )
    scheduler.tick_dispatch()
    assert ran == [1]


def test_sync_loop_uses_its_own_session_and_thread(make_scheduler):
    scheduler = make_scheduler(dispatch_every=0.05, sync_every=0.05)
    seen = {}

    def dispatch_job(session, now):
        seen["dispatch"] = (threading.get_ident(), session)

    def sync_job(session, now):
        seen["sync"] = (threading.get_ident(), session)

    scheduler.register("d", 10, dispatch_job, loop="dispatch")
    scheduler.register("s", 10, sync_job, loop="sync")
    stop = threading.Event()
    thread = threading.Thread(target=scheduler.run_forever, args=(stop,))
    thread.start()
    try:
        deadline = threading.Event()
        for _ in range(100):
            if {"dispatch", "sync"} <= seen.keys():
                break
            deadline.wait(0.05)
    finally:
        stop.set()
        thread.join(timeout=10)
    assert {"dispatch", "sync"} <= seen.keys()
    assert seen["dispatch"][0] != seen["sync"][0]
    assert seen["dispatch"][1] is not seen["sync"][1]


def test_slow_sync_job_does_not_delay_a_dispatch_tick(make_scheduler, session_factory):
    scheduler = make_scheduler()
    started, release = threading.Event(), threading.Event()

    def slow(session, now):
        started.set()
        release.wait(timeout=20)

    scheduler.register("slow", 10, slow, loop="sync")
    scheduler.tick_dispatch()  # becomes leader
    thread = threading.Thread(target=scheduler.tick_sync)
    thread.start()
    try:
        assert started.wait(timeout=10)
        before = heartbeat(session_factory)
        make_scheduler.clock.advance(10)
        scheduler.tick_dispatch()  # must return while the sync job is still blocked
        assert thread.is_alive()
        assert heartbeat(session_factory) == before + timedelta(seconds=10)
    finally:
        release.set()
        thread.join(timeout=10)


def test_heartbeat_written_every_dispatch_tick(make_scheduler, session_factory):
    scheduler = make_scheduler()
    clock = make_scheduler.clock
    assert heartbeat(session_factory) is None
    for _ in range(3):
        scheduler.tick_dispatch()
        assert heartbeat(session_factory) == clock.now()
        clock.advance(10)


def test_standby_does_not_write_a_heartbeat_or_run_jobs(make_scheduler, session_factory):
    leader, standby = make_scheduler(), make_scheduler()
    ran: list[str] = []
    standby.register("job", 10, lambda s, now: ran.append("standby"), loop="dispatch")
    standby.register("sjob", 10, lambda s, now: ran.append("standby-sync"), loop="sync")
    leader.tick_dispatch()
    first = heartbeat(session_factory)
    make_scheduler.clock.advance(10)
    standby.tick_dispatch()
    standby.tick_sync()
    assert ran == []
    assert heartbeat(session_factory) == first  # a standby must not mask a dead leader


def test_heartbeat_failure_does_not_stop_jobs(make_scheduler, monkeypatch):
    scheduler = make_scheduler()
    ran: list[str] = []
    scheduler.register("job", 10, lambda s, now: ran.append("job"), loop="dispatch")

    def boom(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(scheduler, "_write_heartbeat", boom)
    scheduler.tick_dispatch()
    assert ran == ["job"]


def test_register_rejects_bad_arguments(make_scheduler):
    scheduler = make_scheduler()
    with pytest.raises(ValueError):
        scheduler.register("x", 0, lambda s, now: None, loop="dispatch")
    with pytest.raises(ValueError):
        scheduler.register("x", 10, lambda s, now: None, loop="other")  # type: ignore[arg-type]
    scheduler.register("x", 10, lambda s, now: None, loop="dispatch")
    with pytest.raises(ValueError):
        scheduler.register("x", 10, lambda s, now: None, loop="sync")  # duplicate name


def test_the_leader_lock_lives_on_a_connection_that_really_closes():
    """Closing the leader connection must free the lock at once; a pooled connection would go
    back to the pool still holding it."""
    factory = direct_connection_factory(TEST_DATABASE_URL)
    first = factory()
    first.execute(text("select pg_advisory_lock(424242)"))
    first.close()
    other = create_engine(TEST_DATABASE_URL).connect()
    try:
        assert other.execute(text("select pg_try_advisory_lock(424242)")).scalar() is True
    finally:
        other.close()
