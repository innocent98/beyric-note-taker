"""The counters behind the login and sign-up limits, in memory (unit tests) and in the database
(production): the same behaviour in both, and for the database what memory could never do: survive
a restart, work for two workers at once, and keep no raw address, email or client address."""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.ratelimit import DbStore, Gate, MemoryStore

SECRET = b"s" * 32


def gate(key="k", limit=3, window=100.0, bucket="b", label="x"):
    return Gate(label, bucket, key, limit, window)


@pytest.fixture(params=["memory", "database"])
def store(request, engine):
    if request.param == "memory":
        return MemoryStore()
    return DbStore(lambda: Session(engine), SECRET)


@pytest.fixture
def db_store(engine):
    return DbStore(lambda: Session(engine), SECRET)


# ---- behaviour both stores share ----


def test_allows_up_to_the_limit_then_refuses_with_the_gates_label(store):
    g = gate(limit=3, label="address")
    assert [store.acquire([g], now=1.0) for _ in range(3)] == [None, None, None]
    assert store.acquire([g], now=1.0) == "address"


def test_a_refused_hit_is_not_counted_so_hammering_does_not_extend_the_wait(store):
    g = gate(limit=2, window=100.0)
    store.acquire([g], now=0.0)
    store.acquire([g], now=10.0)
    for t in range(20, 90, 10):
        assert store.acquire([g], now=float(t)) == "x"  # refused, not counted
    assert store.acquire([g], now=101.0) is None  # the first hit (t=0) has expired


def test_the_window_slides(store):
    g = gate(limit=1, window=60.0)
    assert store.acquire([g], now=0.0) is None
    assert store.acquire([g], now=59.0) == "x"
    assert store.acquire([g], now=61.0) is None


def test_keys_and_buckets_are_separate(store):
    for _ in range(3):
        store.acquire([gate(key="a")], now=1.0)
    assert store.acquire([gate(key="a")], now=1.0) == "x"
    assert store.acquire([gate(key="b")], now=1.0) is None
    assert store.acquire([gate(key="a", bucket="other")], now=1.0) is None


def test_several_gates_are_counted_together_or_not_at_all(store):
    """A refusal by one gate counts nothing on the others (an address over its limit must not
    spend the account's budget)."""
    address, account = (
        gate(key="ip", limit=1, label="address"),
        gate(key="me", limit=5, bucket="acct", label="account"),
    )
    assert store.acquire([address, account], now=1.0) is None
    assert store.acquire([address, account], now=2.0) == "address"
    for _ in range(4):
        assert (
            store.acquire([gate(key="other-ip", limit=9, label="address"), account], now=3.0)
            is None
        )
    # the account has had 1 + 4 = 5 hits: the refused call did not add a sixth, a fifth is the last
    assert store.full(account, now=3.0) is True


def test_gates_are_checked_in_order(store):
    first, second = gate(key="1", limit=0, label="first"), gate(key="2", limit=0, label="second")
    assert store.acquire([first, second], now=0.0) == "first"
    assert store.acquire([second, first], now=0.0) == "second"


def test_full_counts_nothing_and_add_counts_unconditionally(store):
    g = gate(limit=2)
    assert store.full(g, now=0.0) is False
    assert store.full(g, now=0.0) is False
    store.add(g, now=0.0)
    store.add(g, now=0.0)
    assert store.full(g, now=0.0) is True
    store.add(g, now=0.0)  # past the limit: still counted (a wrong guess found after the fact)
    assert store.acquire([g], now=0.0) == "x"


def test_refund_gives_back_the_newest_hit_only(store):
    g = gate(limit=2, window=100.0)
    store.acquire([g], now=0.0)
    store.acquire([g], now=50.0)
    store.refund("b", "k")  # the t=50 hit goes
    assert store.acquire([g], now=60.0) is None
    assert store.acquire([g], now=61.0) == "x"
    assert store.acquire([g], now=101.0) is None  # t=0 expired; t=60 remains


def test_clear_forgets_every_hit_for_the_key_and_says_how_many(store):
    g = gate(limit=3)
    store.acquire([g], now=0.0)
    store.acquire([g], now=1.0)
    assert store.clear("b", "k") == 2
    assert store.clear("b", "k") == 0
    assert store.acquire([gate(limit=1)], now=0.0) is None


def test_refund_and_clear_on_an_unknown_key_do_nothing(store):
    store.refund("b", "nobody")
    store.clear("b", "nobody")
    assert store.acquire([gate(key="nobody")], now=0.0) is None


def test_size_counts_keys_in_use(store):
    assert store.size() == 0
    store.acquire([gate(key="a"), gate(key="b")], now=0.0)
    store.acquire([gate(key="a")], now=1.0)
    assert store.size() == 2


# ---- what only the database can do ----


def test_the_database_keeps_hits_across_a_restart(engine):
    first = DbStore(lambda: Session(engine), SECRET)
    g = gate(limit=2, label="account")
    first.acquire([g], now=0.0)
    first.acquire([g], now=1.0)
    restarted = DbStore(lambda: Session(engine), SECRET)  # a new process, same database
    assert restarted.acquire([g], now=2.0) == "account"


def test_two_workers_at_once_never_let_more_than_the_limit_through(engine):
    stores = [DbStore(lambda: Session(engine), SECRET) for _ in range(2)]
    g = gate(limit=5, window=1000.0)
    results: list[str | None] = []
    lock = threading.Lock()

    def attempt(n):
        outcome = stores[n % 2].acquire([g], now=1.0)
        with lock:
            results.append(outcome)

    with ThreadPoolExecutor(16) as pool:
        list(pool.map(attempt, range(40)))
    assert results.count(None) == 5


def test_overlapping_gates_taken_in_opposite_orders_do_not_deadlock(engine):
    store = DbStore(lambda: Session(engine), SECRET)
    a, b = gate(key="a", limit=1000), gate(key="b", limit=1000)
    outcomes: list = []

    def run(order):
        for _ in range(25):
            outcomes.append(store.acquire(order, now=1.0))

    threads = [threading.Thread(target=run, args=(o,)) for o in ([a, b], [b, a], [a, b], [b, a])]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert all(not t.is_alive() for t in threads) and outcomes == [None] * 100


def test_no_raw_key_is_stored(engine, db_store):
    """Keys are an email address or a client address: personal data. Only a keyed hash is kept."""
    db_store.acquire([gate(key="victim@example.test"), gate(key="203.0.113.9")], now=0.0)
    with Session(engine) as session:
        keys = [r[0] for r in session.execute(text("select key from rate_hits"))]
    assert len(keys) == 2
    for stored in keys:
        assert "victim" not in stored and "203.0.113" not in stored and len(stored) == 32


def test_the_hash_depends_on_a_secret_so_a_reader_of_the_table_cannot_check_a_guess(engine):
    one = DbStore(lambda: Session(engine), b"a" * 32)
    two = DbStore(lambda: Session(engine), b"b" * 32)
    one.acquire([gate(key="same", limit=1)], now=0.0)
    assert two.acquire([gate(key="same", limit=1)], now=0.0) is None  # a different key to it


def test_expired_hits_of_a_key_are_dropped_when_it_is_touched(engine, db_store):
    g = gate(key="k", limit=5, window=10.0)
    for t in range(5):
        db_store.acquire([g], now=float(t))
    db_store.acquire([g], now=100.0)
    with Session(engine) as session:
        assert session.scalar(text("select count(*) from rate_hits")) == 1


def test_reset_empties_the_table(engine, db_store):
    db_store.acquire([gate()], now=0.0)
    db_store.reset()
    assert db_store.size() == 0
