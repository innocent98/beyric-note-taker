"""Migration 0054: the Pro and Scale plans, as data. Runs the real migration on the scratch
database, like `test_billing_subscriptions_migration.py` does for 0052."""

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text

from tests import test_migrations as migrations
from tests.test_billing_subscriptions_migration import subscription, user_id

alembic_config = migrations.alembic_config
owner_env = migrations.owner_env
scratch_url = migrations.scratch_url

DECIDED_PLANS = [
    # code, name, seconds, sort order (Adebayo, 2026-11-17)
    ("plan_20h", "20 hours a month", 20 * 3600, 1),
    ("plan_50h", "Pro", 50 * 3600, 2),
    ("plan_100h", "Scale", 100 * 3600, 3),
]
DECIDED_PRICES = [
    ("plan_100h", "NGN", 11_200_000),
    ("plan_100h", "USD", 8000),
    ("plan_20h", "NGN", 2_500_000),
    ("plan_20h", "USD", 1800),
    ("plan_50h", "NGN", 5_600_000),
    ("plan_50h", "USD", 4000),
]


def test_0054_is_the_only_head_and_follows_the_billing_chain(scratch_url):
    script = ScriptDirectory.from_config(alembic_config(scratch_url))
    assert len(script.get_heads()) == 1
    # 0053 is S9's: until it has merged this points at 0052, afterwards at 0053.
    assert script.get_revision("0054").down_revision in ("0052", "0053")


def rows(conn):
    plans = conn.execute(
        text("select code, name, monthly_seconds, sort_order from plans order by sort_order")
    ).all()
    prices = conn.execute(
        text(
            "select plan_code, currency, amount_minor from plan_prices order by plan_code, currency"
        )
    ).all()
    flags = conn.execute(
        text("select code, is_public, active from plans order by sort_order")
    ).all()
    return plans, prices, flags


def test_the_three_decided_plans_and_six_prices_and_the_round_trip(scratch_url):
    cfg = alembic_config(scratch_url)
    engine = create_engine(scratch_url)
    command.upgrade(cfg, "head")
    with engine.connect() as conn:
        plans, prices, flags = rows(conn)
        intervals = conn.execute(text("select distinct interval from plan_prices")).all()
        packs = conn.execute(text("select code, currency, amount_minor from products")).all()
    assert plans == DECIDED_PLANS
    assert prices == DECIDED_PRICES
    assert flags == [
        ("plan_20h", False, True),
        ("plan_50h", False, True),
        ("plan_100h", False, True),
    ]
    assert intervals == [("month",)]
    # The pack and the first plan are exactly what 0052 seeded.
    assert sorted(packs) == [("pack_10h", "NGN", 1_500_000), ("pack_10h", "USD", 1000)]
    command.downgrade(cfg, "0052")
    with engine.connect() as conn:
        plans, prices, _flags = rows(conn)
    assert plans == DECIDED_PLANS[:1]
    assert prices == [p for p in DECIDED_PRICES if p[0] == "plan_20h"]
    command.upgrade(cfg, "head")
    with engine.connect() as conn:
        assert rows(conn)[0] == DECIDED_PLANS
    engine.dispose()


@pytest.mark.parametrize("where", ["subscription", "pending", "payment"])
def test_the_downgrade_refuses_while_a_money_record_points_at_pro_or_scale(scratch_url, where):
    cfg = alembic_config(scratch_url)
    engine = create_engine(scratch_url)
    command.upgrade(cfg, "head")
    with engine.begin() as conn:
        uid = user_id(conn)
        pro_price = conn.execute(
            text("select id from plan_prices where plan_code = 'plan_50h' and currency = 'USD'")
        ).scalar()
        if where == "subscription":
            subscription(conn, uid, plan_code="plan_50h", price_id=pro_price)
        elif where == "pending":
            subscription(conn, uid)
            conn.execute(text("update subscriptions set pending_plan_code = 'plan_100h'"))
        else:
            conn.execute(
                text(
                    "insert into payments (payer_user_id, provider, kind, currency, amount_minor, "
                    "reference, plan_price_id) values (:u, 'flutterwave', 'plan', 'USD', 4000, "
                    "'bn-1-p', :p)"
                ),
                {"u": uid, "p": pro_price},
            )
    with pytest.raises(RuntimeError, match="Refusing to downgrade 0054"):
        command.downgrade(cfg, "0052")
    with engine.connect() as conn:
        assert conn.execute(text("select count(*) from plans")).scalar() == 3
    engine.dispose()
