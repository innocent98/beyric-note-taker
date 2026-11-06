"""C10b: the periodic sweeps and the mail tick, registered on the scheduler.

Each sweep is proven at the boundary second: a row one second on the keeping side stays, a row
one second on the deleting side goes. The jobs are run the way the scheduler runs them (by name,
with a session and the clock's `now`), and a few tests drive real ticks on a fake clock to prove
how often each one runs.
"""

import logging
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app import accounts, mail, signup, sweeps
from app.models import (
    AppState,
    AuthToken,
    CalendarConnection,
    CalendarEvent,
    MailOutbox,
    Notification,
    OAuthFlow,
    ScheduledJoin,
    User,
)
from app.scheduler import Scheduler, direct_connection_factory, register_jobs
from tests.conftest import TEST_DATABASE_URL
from tests.helpers import FakeClock
from tests.test_mail import FakeMailer
from tests.test_signup import joined_unconfirmed
from tests.test_web import make_settings, make_user

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
SECOND = timedelta(seconds=1)
DAY = timedelta(days=1)


@pytest.fixture
def scheduler(engine):
    clock = FakeClock(NOW)
    built = Scheduler(
        clock,
        sessionmaker(bind=engine, expire_on_commit=False),
        leader_conn_factory=direct_connection_factory(TEST_DATABASE_URL),
    )
    built.clock = clock
    yield built
    built.release()


@pytest.fixture
def registered(scheduler):
    """A scheduler with the default sweeps registered and a mailer that records what it sent."""
    mailer = FakeMailer()
    sweeps.register_default_sweeps(scheduler, make_settings(), mailer=mailer)
    scheduler.mailer = mailer
    return scheduler


def job(scheduler, name: str):
    for jobs in scheduler._jobs.values():
        for candidate in jobs:
            if candidate.name == name:
                return candidate
    raise AssertionError(
        f"no job named {name!r}: {[j.name for v in scheduler._jobs.values() for j in v]}"
    )


def run(scheduler, name: str, session, now: datetime = NOW) -> None:
    job(scheduler, name).fn(session, now)
    session.expire_all()


def event(session, user, conn, source_id: str, ends_at: datetime) -> CalendarEvent:
    row = CalendarEvent(
        user_id=user.id,
        connection_id=conn.id,
        source_event_id=source_id,
        title=source_id,
        starts_at=ends_at - timedelta(hours=1),
        ends_at=ends_at,
    )
    session.add(row)
    session.commit()
    return row


def titles(session, column) -> set[str]:
    return set(session.scalars(select(column)))


# ---- one test per sweep, rows on both sides of the boundary second ----


def test_sweep_deletes_calendar_events_ended_over_two_days_ago_keeps_newer(registered, session):
    user = make_user(session)
    conn = CalendarConnection(user_id=user.id, provider="google")
    session.add(conn)
    session.commit()
    event(session, user, conn, "gone", NOW - 2 * DAY - SECOND)
    event(session, user, conn, "boundary", NOW - 2 * DAY)
    event(session, user, conn, "recent", NOW - 2 * DAY + SECOND)
    event(session, user, conn, "future", NOW + DAY)

    run(registered, "sweep_calendar_events", session)

    assert titles(session, CalendarEvent.title) == {"boundary", "recent", "future"}


def test_calendar_event_sweep_keeps_the_scheduled_join_of_a_deleted_event(registered, session):
    """The event row goes two days after the meeting; the join (and the meeting it made) stays."""
    user = make_user(session)
    conn = CalendarConnection(user_id=user.id, provider="google")
    session.add(conn)
    session.commit()
    old = event(session, user, conn, "old", NOW - 3 * DAY)
    session.add(
        ScheduledJoin(
            user_id=user.id,
            calendar_event_id=old.id,
            platform="google_meet",
            native_id="abc-defg-hij",
            meeting_url="https://meet.google.com/abc-defg-hij",
            state="completed",
            run_at=NOW - 3 * DAY,
            deadline_at=NOW - 3 * DAY,
            starts_at=NOW - 3 * DAY,
            ends_at=NOW - 3 * DAY + timedelta(hours=1),
        )
    )
    session.commit()

    run(registered, "sweep_calendar_events", session)

    assert session.scalars(select(CalendarEvent)).all() == []
    assert session.scalars(select(ScheduledJoin.calendar_event_id)).one() is None


def test_sweep_deletes_expired_oauth_flows_keeps_live(registered, session):
    for name, expires in (
        ("expired", NOW - SECOND),
        ("boundary", NOW),
        ("live", NOW + SECOND),
    ):
        session.add(
            OAuthFlow(
                state_hash=name[0] * 64,
                purpose="signin",
                code_verifier="v",
                nonce="n",
                expires_at=expires,
            )
        )
    session.commit()

    run(registered, "sweep_oauth_flows", session)

    assert {f.state_hash[0] for f in session.scalars(select(OAuthFlow))} == {"b", "l"}


def _token(session, n: int, *, used_at=None, expires_at=None) -> AuthToken:
    row = AuthToken(
        email="a@example.test",
        purpose="verify_email",
        token_hash=f"{n:064d}",
        used_at=used_at,
        expires_at=expires_at or NOW + timedelta(hours=1),
    )
    session.add(row)
    session.commit()
    return row


def test_sweep_deletes_used_and_expired_tokens_past_seven_days_keeps_newer(registered, session):
    keep_for = sweeps.TOKEN_KEEP
    assert keep_for == timedelta(days=7)  # the debugging window documented in the SOP
    live = _token(session, 1)
    _token(session, 2, used_at=NOW - keep_for - SECOND)
    used_boundary = _token(session, 3, used_at=NOW - keep_for)
    used_recent = _token(session, 4, used_at=NOW - keep_for + SECOND)
    _token(session, 5, expires_at=NOW - keep_for - SECOND)
    expired_boundary = _token(session, 6, expires_at=NOW - keep_for)
    expired_recent = _token(session, 7, expires_at=NOW - keep_for + SECOND)
    kept = {t.id for t in (live, used_boundary, used_recent, expired_boundary, expired_recent)}

    run(registered, "sweep_auth_tokens", session)

    assert {t.id for t in session.scalars(select(AuthToken))} == kept


def test_sweep_deletes_notifications_older_than_90_days_keeps_newer(registered, session):
    owner = make_user(session)
    for name, age in (
        ("gone", 90 * DAY + SECOND),
        ("boundary", 90 * DAY),
        ("recent", 90 * DAY - SECOND),
    ):
        session.add(
            Notification(
                user_id=owner.id,
                kind="k",
                title=name,
                dedupe_key=name,
                created_at=NOW - age,
            )
        )
    session.commit()

    run(registered, "sweep_notifications", session)

    assert titles(session, Notification.title) == {"boundary", "recent"}


def _mail(session, n: int, status: str, age: timedelta) -> MailOutbox:
    row = MailOutbox(
        to_email=f"u{n}@example.test",
        template="notes_ready",
        payload={"meeting_id": n},
        status=status,
        created_at=NOW - age,
    )
    session.add(row)
    session.commit()
    return row


def test_sweep_deletes_sent_mail_older_than_30_days_keeps_newer(registered, session):
    """The privacy policy promises a 30-day mail log; this reuses `mail.sweep_outbox`, which
    W0.3 already wrote, so there is one rule and no second copy of it."""
    month = timedelta(days=30)
    _mail(session, 1, "sent", month + SECOND)
    _mail(session, 2, "sent", month)
    _mail(session, 3, "sent", month - SECOND)
    _mail(session, 4, "sent", 2 * month)
    failed_old = _mail(session, 5, "failed", 2 * month)  # the admin's failure list needs it
    pending_new = _mail(session, 6, "pending", timedelta(hours=1))

    run(registered, "sweep_mail_outbox", session)

    left = {r.to_email for r in session.scalars(select(MailOutbox))}
    assert left == {
        "u2@example.test",
        "u3@example.test",
        f"u{failed_old.payload['meeting_id']}@example.test",
        f"u{pending_new.payload['meeting_id']}@example.test",
    }


def test_the_mail_sweep_is_the_function_mail_already_has(registered):
    """Guard against a second copy of the rule: the job calls `mail.sweep_outbox`."""
    assert job(registered, "sweep_mail_outbox").fn is mail.sweep_outbox


def test_unverified_sweep_frees_the_account_slot(registered, session):
    session.add(AppState(id=1, max_accounts=1))
    session.commit()
    user, _ = joined_unconfirmed(session, "invitee@example.test")
    user.created_at = user.terms_accepted_at = NOW - timedelta(hours=48) - SECOND
    session.commit()
    assert signup.slots_taken(session, NOW) == 1  # an invited, unconfirmed account holds a place
    assert signup.has_room(session, NOW) is False

    run(registered, "sweep_unverified_accounts", session)

    assert session.scalars(select(User).where(User.email == "invitee@example.test")).first() is None
    assert signup.slots_taken(session, NOW) == 0
    assert signup.has_room(session, NOW) is True


def test_unverified_sweep_keeps_an_account_at_the_48_hour_boundary(registered, session):
    old = make_user(session, "old@example.test", email_verified_at=None)
    edge = make_user(session, "edge@example.test", email_verified_at=None)
    new = make_user(session, "new@example.test", email_verified_at=None)
    verified = make_user(session, "verified@example.test")
    for user, age in (
        (old, timedelta(hours=48) + SECOND),
        (edge, timedelta(hours=48)),
        (new, timedelta(hours=48) - SECOND),
        (verified, 30 * DAY),
    ):
        user.created_at = NOW - age
    session.commit()
    for user in (old, edge, new):
        accounts.create_user_defaults(session, user)

    run(registered, "sweep_unverified_accounts", session)

    assert titles(session, User.email) == {
        "edge@example.test",
        "new@example.test",
        "verified@example.test",
    }


def test_sweeps_are_idempotent(registered, session):
    owner = make_user(session)
    conn = CalendarConnection(user_id=owner.id, provider="google")
    session.add(conn)
    session.commit()
    event(session, owner, conn, "old", NOW - 3 * DAY)
    event(session, owner, conn, "new", NOW)
    session.add(
        Notification(
            user_id=owner.id, kind="k", title="old", dedupe_key="o", created_at=NOW - 100 * DAY
        )
    )
    _mail(session, 1, "sent", 31 * DAY)
    _mail(session, 2, "sent", DAY)
    stale = make_user(session, "stale@example.test", email_verified_at=None)
    stale.created_at = NOW - timedelta(hours=60)
    session.commit()

    def snapshot() -> dict[str, int]:
        return {
            model.__tablename__: session.scalar(select(func.count()).select_from(model))
            for model in (User, CalendarEvent, Notification, MailOutbox, AuthToken, OAuthFlow)
        }

    names = [
        "sweep_oauth_flows",
        "sweep_auth_tokens",
        "sweep_notifications",
        "sweep_calendar_events",
        "sweep_mail_outbox",
        "sweep_unverified_accounts",
    ]
    for name in names:
        run(registered, name, session)
    first = snapshot()
    assert first["calendar_events"] == 1 and first["notifications"] == 0
    assert (
        first["mail_outbox"] == 1 and first["users"] == 1
    )  # only the stale unverified account went
    for name in names:
        run(registered, name, session)
    assert snapshot() == first


# ---- how they are registered ----


def test_every_sweep_is_registered_hourly_on_the_sync_loop(registered):
    sweep_names = {
        "sweep_oauth_flows",
        "sweep_auth_tokens",
        "sweep_notifications",
        "sweep_calendar_events",
        "sweep_mail_outbox",
        "sweep_unverified_accounts",
        "series_from_joins",
    }
    hourly = [j for j in registered._jobs["sync"] if j.every == timedelta(hours=1)]
    assert {j.name for j in hourly} == sweep_names
    # the reminders before recurring meetings look every minute, also on the sync loop
    assert [j.name for j in registered._jobs["sync"] if j.every != timedelta(hours=1)] == [
        "series_reminders"
    ]


def test_the_mail_sender_is_registered_on_the_mail_loop_not_the_dispatch_loop(registered):
    assert [j.name for j in registered._jobs["mail"]] == ["mail_outbox_send"]
    assert registered._jobs["dispatch"] == []  # a hung provider must never hold a dispatch tick


def test_the_mail_sender_runs_on_every_mail_tick(registered, session):
    for n in range(3):
        row = mail.enqueue(
            session, f"u{n}@example.test", "verify_email", {"link": "https://x.test/?t=1"}
        )
        row.created_at = registered.clock.now()  # the fake clock, not the database's
        session.commit()
        registered.tick_dispatch()  # takes (and keeps) the leader lock
        registered.tick_mail()
        registered.clock.advance(10)
        assert len(registered.mailer.sent) == n + 1
    assert [m.to for m, _ in registered.mailer.sent] == [
        "u0@example.test",
        "u1@example.test",
        "u2@example.test",
    ]


def test_sweeps_run_once_an_hour_on_a_sync_tick(registered, session):
    owner = make_user(session)

    def old_notification(name: str) -> None:
        session.add(
            Notification(
                user_id=owner.id,
                kind="k",
                title=name,
                dedupe_key=name,
                created_at=registered.clock.now() - 100 * DAY,
            )
        )
        session.commit()

    def remaining() -> set[str]:
        session.expire_all()
        return titles(session, Notification.title)

    registered.tick_dispatch()  # the first dispatch tick takes the leader lock
    assert registered.is_leader
    old_notification("first")
    registered.tick_sync()  # the first run after start is immediate
    assert remaining() == set()
    old_notification("second")
    registered.clock.advance(59 * 60)
    registered.tick_sync()
    assert remaining() == {"second"}  # not yet: an hour has not passed
    registered.clock.advance(60)
    registered.tick_sync()
    assert remaining() == set()


def test_a_failing_sweep_does_not_stop_the_other_sweeps_or_leak_what_it_says(
    scheduler, session, monkeypatch, caplog
):
    owner = make_user(session)
    session.add(
        Notification(
            user_id=owner.id, kind="k", title="old", dedupe_key="o", created_at=NOW - 100 * DAY
        )
    )
    session.commit()

    def boom(*args, **kwargs):
        raise RuntimeError("secret detail ada@example.test")

    monkeypatch.setattr(sweeps.mail, "sweep_outbox", boom)
    monkeypatch.setattr(sweeps.accounts, "sweep_unverified", boom)
    sweeps.register_default_sweeps(scheduler, make_settings(), mailer=FakeMailer())
    caplog.set_level(logging.WARNING)

    scheduler.tick_dispatch()
    scheduler.tick_sync()

    session.expire_all()
    assert session.scalars(select(Notification)).all() == []  # the notifications sweep still ran
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "RuntimeError" in logged and "secret detail" not in logged
    assert "ada@example.test" not in logged


def test_no_mail_sender_is_registered_when_production_has_no_provider(scheduler, caplog):
    caplog.set_level(logging.WARNING)
    sweeps.register_default_sweeps(scheduler, make_settings(app_env="production"))

    assert scheduler._jobs["dispatch"] == []
    assert "no mail provider" in caplog.text
    assert len(scheduler._jobs["sync"]) == 8  # the sweeps do not depend on mail


@pytest.mark.parametrize("calendar_enabled", [False, True])
def test_register_jobs_registers_the_sweeps_whether_or_not_the_calendar_is_on(
    scheduler, calendar_enabled
):
    register_jobs(scheduler, make_settings(calendar_enabled=calendar_enabled))

    names = {j.name for jobs in scheduler._jobs.values() for j in jobs}
    assert {"sweep_calendar_events", "sweep_notifications"} <= names
    assert ("calendar_sync" in names) is calendar_enabled
