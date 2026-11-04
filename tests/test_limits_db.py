"""The login and sign-up limits keep their counters in the database: a restart in the middle of a
lockout does not end it, a second worker shares it, and the table holds no address or email."""

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from app import maintenance, ratelimit
from app.auth import LoginLimiter
from app.main import create_app
from app.models import RateHit
from app.ratelimit import DbStore
from tests.test_accounts import visitor
from tests.test_api import anon, error_of  # noqa: F401  (fixtures used by name)
from tests.test_signup import open_pilot, sign_up
from tests.test_web import (  # noqa: F401
    OWNER_EMAIL,
    PASSWORD,
    anon_client,
    app,
    fake_openai,
    fake_vexa,
    make_user,
    owner,
)

SECRET = b"t" * 32


def restart(app):
    """A new process: a fresh app object on the same database (nothing carried in memory)."""
    fresh = create_app(app.state.settings)
    fresh.dependency_overrides = app.dependency_overrides
    return fresh


def db_limiter(engine, **kw):
    return LoginLimiter(store=DbStore(lambda: Session(engine), SECRET), **kw)


# ---- the login limiter on the database ----


def test_a_lockout_survives_a_restart_in_the_middle_of_it(engine):
    # spread over addresses, so it is the account's window that is full, not one address's
    first = db_limiter(engine)
    for i in range(10):
        assert first.try_acquire(f"198.51.100.{i}", "victim@example.test", now=1000.0)
    assert first.check("198.51.100.99", "victim@example.test", now=1001.0) == "account"
    restarted = db_limiter(engine)  # a new process
    assert restarted.check("198.51.100.99", "victim@example.test", now=1002.0) == "account"
    assert restarted.check("198.51.100.99", "victim@example.test", now=1000.0 + 3601) is None


def test_the_lockout_survives_a_restart_of_the_whole_app(app, owner):
    for i in range(10):
        error_of(visitor(app, f"203.0.113.{i}").log_in("guess"), 401, "invalid_credentials")
    after = restart(app)
    error_of(visitor(after, "203.0.113.200").log_in(), 429, "too_many_attempts")  # right password
    # and another account is still fine
    assert (
        visitor(after, "203.0.113.201").log_in("guess", email="x@example.test").status_code == 401
    )


def test_the_sign_up_limits_survive_a_restart_too(app, session):
    open_pilot(session)
    api = visitor(app, "192.0.2.50")
    for n in range(3):
        assert sign_up(api, email=f"p{n}@example.test").status_code == 202
    error_of(sign_up(api, email="p9@example.test"), 429, "too_many_requests")
    again = visitor(restart(app), "192.0.2.50")
    error_of(sign_up(again, email="p10@example.test"), 429, "too_many_requests")


def test_two_workers_share_the_accounts_budget(engine):
    workers = [db_limiter(engine, account_limit=7) for _ in range(2)]
    results: list[bool] = []
    lock = threading.Lock()

    def attempt(n):
        outcome = workers[n % 2].try_acquire(f"10.0.0.{n}", "victim@example.test", now=5.0)
        with lock:
            results.append(outcome)

    with ThreadPoolExecutor(16) as pool:
        list(pool.map(attempt, range(60)))
    assert results.count(True) == 7


def test_success_gives_back_the_clients_attempt_and_clears_the_accounts_failures(engine):
    limiter = db_limiter(engine)
    for _ in range(3):
        limiter.try_acquire("203.0.113.1", "me@example.test", now=1.0)
    limiter.succeeded("203.0.113.1", "me@example.test")
    assert limiter.check("203.0.113.1", "me@example.test", now=2.0) is None
    # the account's earlier failures are gone, the client has only the new attempt
    store = limiter._store
    assert store.full(limiter._account("me@example.test"), now=2.0) is False


def test_a_known_device_does_not_spend_the_accounts_budget_in_the_database(engine):
    limiter = db_limiter(engine, account_limit=2)
    for _ in range(5):
        assert limiter.check("203.0.113.1", "me@example.test", now=1.0, device="dev1") is None
    assert limiter.check("203.0.113.2", "me@example.test", now=1.0) is None  # account untouched


def test_no_address_or_email_reaches_the_table(app, owner, engine):
    visitor(app, "203.0.113.77").log_in("guess")
    with Session(engine) as session:
        rows = session.query(RateHit).all()
        dump = " ".join(f"{r.bucket} {r.key}" for r in rows)
        assert rows and "203.0.113" not in dump and "owner" not in dump and "example" not in dump
        assert session.scalar(text("select count(*) from rate_hits")) == len(rows)


def test_the_hourly_sweep_clears_old_hits(app, session):
    store = app.state.rate_store
    store.acquire([ratelimit.Gate("x", "b", "old", 5, 60.0)], now=1.0)
    maintenance.run_hourly(session, datetime.now(UTC))
    assert store.size() == 0
