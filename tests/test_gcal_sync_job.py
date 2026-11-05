"""The sync job (plan task C8b): `sync_connection` and the loop job that runs it for every due
connection, against a real Postgres and `FakeGoogleCalendar` through the real `GoogleSource`.

The Google bodies are SYNTHETIC (see tests/fakes/google.py).
"""

import base64
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app import gcal_sync, vault
from app.gcal_client import AccessTokenCache, GoogleSource
from app.gcal_sync import FULL_RESYNC_EVERY, SYNC_EVERY, sync_connection, sync_due_connections
from app.models import CalendarConnection, UserSettings
from app.scheduler import Scheduler, direct_connection_factory, register_jobs
from tests.conftest import TEST_DATABASE_URL
from tests.fakes.google import FakeGoogleCalendar, gone
from tests.helpers import FakeClock
from tests.test_gcal_sync import HOUR, NOW, events, joins, make_world, raw_event, stub
from tests.test_web import make_settings

KEY = base64.b64encode(b"J" * 32).decode()
SETTINGS = make_settings(
    google_client_id=FakeGoogleCalendar.CLIENT_ID,
    google_client_secret=FakeGoogleCalendar.CLIENT_SECRET,
    token_enc_key=KEY,
    autojoin_lead_seconds=60,
)


@pytest.fixture(autouse=True)
def vault_key(monkeypatch):
    monkeypatch.setattr(vault, "get_settings", lambda: SETTINGS)


@pytest.fixture
def google():
    return FakeGoogleCalendar()


@pytest.fixture
def source(google):
    cache = AccessTokenCache(lambda: NOW, settings=SETTINGS, transport=google.transport)
    return GoogleSource(SETTINGS, cache=cache, transport=google.transport)


def connected(session, email="ada@example.test", **fields):
    """A user, their settings and a Google connection holding a real (fake-Google) refresh token."""
    world = make_world(session, email)
    blob, version = vault.encrypt(
        FakeGoogleCalendar.REFRESH_TOKEN, purpose="refresh_token", owner=world.user.id
    )
    world.conn.refresh_token_enc, world.conn.key_version = blob, version
    for name, value in fields.items():
        setattr(world.conn, name, value)
    session.commit()
    return world


def sync(session, world, source, *, now=NOW):
    sync_connection(session, world.conn, source, now=now, settings=SETTINGS)
    session.refresh(world.conn)
    return world.conn


def list_queries(google):
    return [google.query(r) for r in google.list_requests()]


# ---- the first sync, then changes only ----


def test_first_sync_is_full_then_incremental(session, google, source):
    world = connected(session)
    google.pages = [[raw_event("a")]]

    conn = sync(session, world, source)

    first = list_queries(google)[0]
    assert "timeMin" in first and "timeMax" in first and "syncToken" not in first
    assert conn.sync_token == "sync-token-1"
    assert conn.last_sync_at == NOW and conn.last_full_sync_at == NOW
    assert conn.last_error is None and conn.last_error_at is None
    assert len(events(session)) == 1 and len(joins(session)) == 1

    google.pages = [[raw_event("a", start=NOW + 3 * HOUR)]]
    google.sync_token = "sync-token-2"
    later = NOW + SYNC_EVERY
    conn = sync(session, world, source, now=later)

    second = list_queries(google)[1]
    assert second["syncToken"] == "sync-token-1" and "timeMin" not in second
    assert conn.sync_token == "sync-token-2"
    assert conn.last_sync_at == later and conn.last_full_sync_at == NOW  # still the old full one
    assert joins(session)[0].starts_at == NOW + 3 * HOUR


def test_410_falls_back_to_full_and_stores_new_token(session, google, source):
    world = connected(session, sync_token="old-token", last_full_sync_at=NOW, last_sync_at=NOW)
    google.pages = [[raw_event("a")]]
    google.sync_token = "fresh-token"
    google.override("list", 0, gone())
    later = NOW + SYNC_EVERY

    conn = sync(session, world, source, now=later)

    first, second = list_queries(google)
    assert first["syncToken"] == "old-token"
    assert "timeMin" in second and "syncToken" not in second
    assert conn.sync_token == "fresh-token" and conn.last_full_sync_at == later
    assert len(joins(session)) == 1


def test_failed_second_page_leaves_token_and_events_untouched(session, google, source):
    world = connected(session)
    google.pages = [[raw_event("old", code="ooo-oooo-ooo")]]
    sync(session, world, source)
    before_events = [(e.id, e.status, e.starts_at) for e in events(session)]
    before_joins = [(j.id, j.state, j.starts_at) for j in joins(session)]

    # Six hours later the next listing is full; the calendar no longer shows "old" on its first
    # page, and the second page fails. "old" must not be taken for deleted.
    google.pages = [[raw_event("new", code="nnn-nnnn-nnn")], [raw_event("page-two")]]
    google.override("list", 2, FakeGoogleCalendar.error(503, "backendError"))
    later = NOW + FULL_RESYNC_EVERY
    conn = sync(session, world, source, now=later)

    assert conn.sync_token == "sync-token-1"
    assert conn.last_sync_at == NOW and conn.last_full_sync_at == NOW
    assert conn.last_error and conn.last_error_at == later
    assert [(e.id, e.status, e.starts_at) for e in events(session)[:1]] == before_events
    assert [(j.id, j.state, j.starts_at) for j in joins(session)[:1]] == before_joins
    assert {e.status for e in events(session)} == {"confirmed"}  # nothing cancelled
    assert "new" in {e.source_event_id for e in events(session)}  # what was read is kept


def test_a_cancellation_on_a_failed_listing_waits_for_the_next_complete_one(
    session, google, source
):
    world = connected(session)
    google.pages = [[raw_event("a")]]
    sync(session, world, source)

    google.pages = [[stub("a")], [raw_event("two")]]
    google.override("list", 2, FakeGoogleCalendar.error(503, "backendError"))
    sync(session, world, source, now=NOW + SYNC_EVERY)
    assert {j.state for j in joins(session)} == {"scheduled"}  # not cancelled yet

    google.pages = [[stub("a")]]
    google.sync_token = "sync-token-2"
    sync(session, world, source, now=NOW + 2 * SYNC_EVERY)
    assert {j.state for j in joins(session)} == {"cancelled"}


def test_full_resync_every_six_hours(session, google, source):
    world = connected(session)
    google.pages = [[raw_event("a")]]
    sync(session, world, source)

    sync(session, world, source, now=NOW + FULL_RESYNC_EVERY - timedelta(minutes=5))
    sync(session, world, source, now=NOW + FULL_RESYNC_EVERY)
    conn = sync(session, world, source, now=NOW + FULL_RESYNC_EVERY + SYNC_EVERY)

    full = ["timeMin" in q for q in list_queries(google)]
    assert full == [True, False, True, False]
    assert conn.last_full_sync_at == NOW + FULL_RESYNC_EVERY


def test_a_connection_with_a_token_but_no_full_sync_mark_syncs_in_full(session, google, source):
    world = connected(session, sync_token="legacy-token")
    google.pages = [[raw_event("a")]]

    sync(session, world, source)

    assert "timeMin" in list_queries(google)[0]


# ---- failures ----


def test_transient_error_records_last_error_and_does_not_raise_out_of_the_job(
    session, google, source
):
    world = connected(session)
    google.pages = [[raw_event("a")]]
    google.override("list", 0, FakeGoogleCalendar.error(503, "backendError"))

    conn = sync(session, world, source)  # does not raise

    assert conn.last_error.startswith("TransientError") and conn.last_error_at == NOW
    assert "access-token" not in conn.last_error and FakeGoogleCalendar.REFRESH_TOKEN not in (
        conn.last_error
    )
    assert conn.sync_token is None and conn.last_sync_at is None
    assert events(session) == []

    later = NOW + SYNC_EVERY
    conn = sync(session, world, source, now=later)
    assert conn.last_error is None and conn.last_error_at is None  # a good sync clears it
    assert conn.sync_token == "sync-token-1" and conn.last_sync_at == later


def test_a_revoked_grant_is_recorded_and_marks_the_connection_needs_reconnect(
    session, google, source
):
    world = connected(session)
    google.valid_refresh_tokens.clear()

    conn = sync(session, world, source)

    assert conn.last_error.startswith("AuthRevoked")
    assert conn.status == "needs_reconnect"  # C13: the connection is marked and the user told


def test_an_error_while_applying_is_recorded_by_type_only_and_the_token_is_not_stored(
    session, google, source, monkeypatch
):
    world = connected(session)
    google.pages = [[raw_event("a")]]

    def explode(*args, **kwargs):
        raise RuntimeError("boom with a secret title")

    monkeypatch.setattr(gcal_sync, "apply_batch", explode)
    conn = sync(session, world, source)

    assert conn.last_error == "RuntimeError"
    assert conn.sync_token is None and conn.last_sync_at is None


def test_token_is_written_only_after_apply_batch_committed_the_batch(
    session, google, source, engine, monkeypatch
):
    world = connected(session)
    google.pages = [[raw_event("a")]]
    seen: dict = {}
    real = gcal_sync.apply_batch

    def watch(*args, **kwargs):
        result = real(*args, **kwargs)
        with Session(engine) as other:  # a different connection sees only committed data
            seen["token"] = other.get(CalendarConnection, world.conn.id).sync_token
            seen["events"] = len(list(other.scalars(select(gcal_sync.CalendarEvent))))
        return result

    monkeypatch.setattr(gcal_sync, "apply_batch", watch)
    conn = sync(session, world, source)

    assert seen == {"token": None, "events": 1}  # the batch was committed, the token not yet
    assert conn.sync_token == "sync-token-1"


@pytest.mark.parametrize("status", ["needs_reconnect", "disconnected"])
def test_a_connection_that_is_not_connected_is_not_synced(session, google, source, status):
    world = connected(session, status=status)

    sync(session, world, source)

    assert google.requests == []


def test_a_user_without_a_settings_row_gets_the_defaults(session, google, source):
    world = connected(session)
    session.delete(session.get(UserSettings, world.user.id))
    session.commit()
    google.pages = [[raw_event("a")]]

    sync(session, world, source)

    (join,) = joins(session)
    assert join.state == "scheduled" and join.template_id is None


# ---- the job over every due connection ----


def test_three_connections_sync_in_one_call_each(session, google, source):
    worlds = [connected(session, f"user{i}@example.test") for i in range(3)]
    google.pages = [[raw_event("a")]]

    count = sync_due_connections(session, NOW, source, settings=SETTINGS)

    assert count == 3 and google.list_calls == 3
    for world in worlds:
        session.refresh(world.conn)
        assert world.conn.last_sync_at == NOW and world.conn.sync_token == "sync-token-1"
    assert len(events(session)) == 3  # one row per user: the same source id, three owners
    assert {e.user_id for e in events(session)} == {w.user.id for w in worlds}
    assert {j.user_id for j in joins(session)} == {w.user.id for w in worlds}


def test_only_due_connections_are_synced(session, google, source):
    fresh = connected(session, "a@example.test", last_sync_at=NOW - timedelta(minutes=2))
    due = connected(session, "b@example.test", last_sync_at=NOW - SYNC_EVERY)
    first = connected(session, "c@example.test")  # last_sync_at null: a first sync
    cooling = connected(
        session,
        "d@example.test",
        last_sync_at=NOW - timedelta(hours=1),
        last_error_at=NOW - timedelta(minutes=2),
    )
    retry = connected(
        session,
        "e@example.test",
        last_sync_at=NOW - timedelta(hours=1),
        last_error_at=NOW - SYNC_EVERY,
    )

    count = sync_due_connections(session, NOW, source, settings=SETTINGS)

    assert count == 3
    for world, synced in [
        (fresh, False),
        (due, True),
        (first, True),
        (cooling, False),
        (retry, True),
    ]:
        session.refresh(world.conn)
        assert (world.conn.last_sync_at == NOW) is synced


def test_connections_that_must_not_sync_are_left_out(session, google, source):
    paused = connected(session, "a@example.test", status="needs_reconnect")
    gone_ = connected(session, "b@example.test", status="disconnected")
    pasted = connected(session, "c@example.test", provider="ics")
    suspended = connected(session, "d@example.test")
    suspended.user.status = "suspended"
    degraded = connected(session, "e@example.test", status="degraded")  # still syncs: retrying
    session.commit()

    count = sync_due_connections(session, NOW, source, settings=SETTINGS)

    assert count == 1
    session.refresh(degraded.conn)
    assert degraded.conn.last_sync_at == NOW
    for world in (paused, gone_, pasted, suspended):
        session.refresh(world.conn)
        assert world.conn.last_sync_at is None


def test_one_connection_blowing_up_does_not_stop_the_others(session, google, source, monkeypatch):
    first = connected(session, "a@example.test")
    second = connected(session, "b@example.test")
    real = gcal_sync.sync_connection

    def flaky(sess, conn, src, **kwargs):
        if conn.id == first.conn.id:
            raise RuntimeError("database hiccup")
        return real(sess, conn, src, **kwargs)

    monkeypatch.setattr(gcal_sync, "sync_connection", flaky)
    sync_due_connections(session, NOW, source, settings=SETTINGS)

    session.refresh(second.conn)
    assert second.conn.last_sync_at == NOW


# ---- registration behind CALENDAR_ENABLED ----


@pytest.fixture
def scheduler(engine):
    clock = FakeClock(NOW)
    built = Scheduler(
        clock,
        sessionmaker(bind=engine, expire_on_commit=False),
        leader_conn_factory=direct_connection_factory(TEST_DATABASE_URL),
    )
    yield built
    built.release()


def sync_jobs(scheduler) -> list[str]:
    """The calendar jobs only: the sweeps and the mail sender (C10b) are registered either way."""
    return [job.name for job in scheduler._jobs["sync"] if job.name.startswith("calendar")]


def test_calendar_is_off_by_default():
    assert make_settings().calendar_enabled is False


def test_no_calendar_job_is_registered_while_calendar_enabled_is_false(scheduler):
    register_jobs(scheduler, make_settings(calendar_enabled=False))

    assert sync_jobs(scheduler) == []
    assert not [j for j in scheduler._jobs["dispatch"] if j.name.startswith("calendar")]


def test_the_sync_job_is_registered_on_the_sync_loop_when_enabled(scheduler):
    register_jobs(scheduler, make_settings(calendar_enabled=True))

    assert sync_jobs(scheduler) == ["calendar_sync"]
    assert not [j for j in scheduler._jobs["dispatch"] if j.name.startswith("calendar")]


def test_the_registered_job_syncs_due_connections_when_the_scheduler_ticks(
    session, scheduler, google, source
):
    world = connected(session)
    google.pages = [[raw_event("a")]]
    assert gcal_sync.register_calendar_sync(
        scheduler, make_settings(calendar_enabled=True), source=source
    )

    assert scheduler.try_lead()
    scheduler.tick_sync()

    session.refresh(world.conn)
    assert world.conn.sync_token == "sync-token-1"
    assert len(joins(session)) == 1


def test_register_does_nothing_when_disabled(scheduler):
    assert not gcal_sync.register_calendar_sync(scheduler, make_settings(calendar_enabled=False))
    assert sync_jobs(scheduler) == []
