"""Migration 0052 (billing stage S8a): the tables, the seed, the constraints that carry money rules,
the round trip and the downgrade guard. Runs the real migration on the scratch database, like
`test_billing_profile_migration.py` does for 0051."""

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from tests import test_migrations as migrations

alembic_config = migrations.alembic_config
owner_env = migrations.owner_env
scratch_url = migrations.scratch_url

TABLES = {"plans", "plan_prices", "products", "subscriptions"}


def user_id(conn) -> int:
    return conn.execute(text("select id from users order by id limit 1")).scalar()


def subscription(conn, uid, **over):
    values = {
        "user_id": uid,
        "plan_code": "plan_20h",
        "price_id": conn.execute(text("select id from plan_prices limit 1")).scalar(),
        "status": "active",
        "currency": "USD",
        "provider": "flutterwave",
        **over,
    }
    conn.execute(
        text(
            "insert into subscriptions (user_id, plan_code, price_id, status, currency, provider, "
            "current_period_start, current_period_end) values (:user_id, :plan_code, :price_id, "
            ":status, :currency, :provider, now(), now() + interval '30 days')"
        ),
        values,
    )


def test_0052_follows_0051(scratch_url):
    script = ScriptDirectory.from_config(alembic_config(scratch_url))
    assert script.get_revision("0052").down_revision == "0051"


def test_the_round_trip_and_the_seed_of_decided_facts_only(scratch_url):
    cfg = alembic_config(scratch_url)
    engine = create_engine(scratch_url)
    command.upgrade(cfg, "0052")
    assert TABLES <= set(inspect(engine).get_table_names())
    with engine.connect() as conn:
        plan = conn.execute(
            text("select code, monthly_seconds, is_public, active from plans")
        ).all()
        prices = conn.execute(
            text("select plan_code, currency, interval, amount_minor from plan_prices")
        ).all()
        packs = conn.execute(
            text("select code, currency, seconds, amount_minor, valid_months from products")
        ).all()
    # only what is decided: 20 hours at 18.00 USD, not public; one 10-hour pack at 10.00 USD for
    # 12 months. No naira price and no second tier (Adebayo's open facts).
    assert plan == [("plan_20h", 72000, False, True)]
    assert prices == [("plan_20h", "USD", "month", 1800)]
    assert packs == [("pack_10h", "USD", 36000, 1000, 12)]
    command.downgrade(cfg, "0051")
    assert not TABLES & set(inspect(engine).get_table_names())
    columns = {c["name"] for c in inspect(engine).get_columns("payments")}
    assert not columns & {"plan_price_id", "product_id", "cycle_end", "attempt", "pay_url"}
    command.upgrade(cfg, "head")
    assert TABLES <= set(inspect(engine).get_table_names())
    engine.dispose()


def test_one_live_subscription_per_person_but_history_is_free(scratch_url):
    cfg = alembic_config(scratch_url)
    engine = create_engine(scratch_url)
    command.upgrade(cfg, "0052")
    with engine.begin() as conn:
        uid = user_id(conn)
        subscription(conn, uid, status="lapsed")
        subscription(conn, uid, status="canceled")
        subscription(conn, uid, status="active")  # history does not count against a live one
    for status in ("active", "past_due"):  # a second live one, of either kind
        with pytest.raises(IntegrityError), engine.begin() as conn:
            subscription(conn, user_id(conn), status=status)
    engine.dispose()


def test_the_constraints_hold_in_the_database(scratch_url):
    cfg = alembic_config(scratch_url)
    engine = create_engine(scratch_url)
    command.upgrade(cfg, "0052")
    bad = [
        "insert into plans (code, name, monthly_seconds) values ('x', 'X', 0)",
        "insert into plan_prices (plan_code, currency, amount_minor) values ('plan_20h', 'EUR', 1)",
        "insert into plan_prices (plan_code, currency, amount_minor) values ('plan_20h', 'USD', 0)",
        "insert into plan_prices (plan_code, currency, amount_minor, interval) "
        "values ('plan_20h', 'USD', 5, 'year')",
        "insert into plan_prices (plan_code, currency, amount_minor, active_from, active_until) "
        "values ('plan_20h', 'USD', 5, now(), now() - interval '1 day')",
        "insert into plan_prices (plan_code, currency, amount_minor) values ('nope', 'USD', 5)",
        "insert into products (code, name, currency, seconds, amount_minor, valid_months) "
        "values ('pack_10h', 'dup', 'USD', 1, 1, 1)",  # one row per (code, currency)
        "insert into products (code, name, currency, seconds, amount_minor, valid_months) "
        "values ('p', 'P', 'USD', 1, 1, 0)",
    ]
    for statement in bad:
        with pytest.raises(IntegrityError), engine.begin() as conn:
            conn.execute(text(statement))
    with pytest.raises(IntegrityError), engine.begin() as conn:
        subscription(conn, user_id(conn), status="paused")
    with pytest.raises(IntegrityError), engine.begin() as conn:
        subscription(conn, user_id(conn), currency="EUR")
    engine.dispose()


def test_a_renewal_slot_holds_one_payment_and_the_new_foreign_keys_hold(scratch_url):
    cfg = alembic_config(scratch_url)
    engine = create_engine(scratch_url)
    command.upgrade(cfg, "0052")
    with engine.begin() as conn:
        uid = user_id(conn)
        subscription(conn, uid)
        sid = conn.execute(text("select id from subscriptions")).scalar()

    def payment(ref, *, kind="renewal", attempt=0, subscription_id=sid):
        return text(
            "insert into payments (payer_user_id, subscription_id, provider, kind, currency, "
            "amount_minor, reference, cycle_end, attempt) values (:u, :s, 'flutterwave', :k, "
            "'USD', 1800, :r, '2027-01-01', :a)"
        ).bindparams(u=uid, s=subscription_id, k=kind, r=ref, a=attempt)

    with engine.begin() as conn:
        conn.execute(payment("bn-1-a"))
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(payment("bn-2-b"))  # the same (subscription, cycle, attempt) slot
    with engine.begin() as conn:
        conn.execute(payment("bn-3-c", attempt=1))  # the next attempt is a different slot
        conn.execute(payment("bn-4-d", kind="plan", attempt=None))  # a link or a plan: no slot
        conn.execute(payment("bn-5-e", kind="plan", attempt=None))
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(payment("bn-6-f", subscription_id=-1))  # a payment cannot name a ghost plan
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(text("update payments set attempt = -1 where reference = 'bn-1-a'"))
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(
            text(
                "insert into hour_grants (user_id, email_hash, kind, seconds, source_payment_id) "
                "values (:u, 'h', 'plan_period', 1, -1)"
            ),
            {"u": uid},
        )
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(
            text(
                "insert into hour_grants (user_id, email_hash, kind, seconds, subscription_id) "
                "values (:u, 'h', 'plan_period', 1, -1)"
            ),
            {"u": uid},
        )
    engine.dispose()


def test_the_downgrade_refuses_while_a_subscription_or_a_bought_payment_exists(scratch_url):
    cfg = alembic_config(scratch_url)
    engine = create_engine(scratch_url)
    command.upgrade(cfg, "0052")
    with engine.begin() as conn:
        subscription(conn, user_id(conn))
    with pytest.raises(RuntimeError, match=r"Cannot downgrade 0052: 1 subscription\(s\)"):
        command.downgrade(cfg, "0051")
    with engine.connect() as conn:
        assert conn.execute(text("select version_num from alembic_version")).all() == [("0052",)]
    assert TABLES <= set(inspect(engine).get_table_names())

    with engine.begin() as conn:
        conn.execute(text("delete from subscriptions"))
        conn.execute(
            text(
                "insert into payments (payer_user_id, provider, kind, currency, amount_minor, "
                "reference, pay_url) values (:u, 'paystack', 'plan', 'NGN', 5, 'bn-9-z', 'https://x')"
            ),
            {"u": user_id(conn)},
        )
    with pytest.raises(RuntimeError, match=r"0 subscription\(s\) and 1 payment\(s\)"):
        command.downgrade(cfg, "0051")
    with engine.begin() as conn:
        conn.execute(text("delete from payments"))
    command.downgrade(cfg, "0051")  # once they are gone the migration goes down
    command.upgrade(cfg, "head")
    engine.dispose()


def test_a_deleted_account_keeps_its_subscription_row_unowned(scratch_url):
    """Money records outlive the person: deleting the user row leaves the subscription with a null
    owner (the app erases accounts in place; this is the database's own safety net)."""
    cfg = alembic_config(scratch_url)
    engine = create_engine(scratch_url)
    command.upgrade(cfg, "0052")
    with engine.begin() as conn:
        conn.execute(
            text("insert into users (email, status) values ('gone@example.test', 'active')")
        )
        uid = conn.execute(text("select id from users where email = 'gone@example.test'")).scalar()
        subscription(conn, uid)
        conn.execute(text("delete from users where id = :u"), {"u": uid})
        assert conn.execute(text("select user_id from subscriptions")).all() == [(None,)]
    engine.dispose()
