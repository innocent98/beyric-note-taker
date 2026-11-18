"""Migration 0053 (billing stage S9): the tables, the constraints that carry money rules, the round
trip and the downgrade guard. Runs the real migration on the scratch database, like
`test_billing_subscriptions_migration.py` does for 0052."""

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from tests import test_migrations as migrations

alembic_config = migrations.alembic_config
owner_env = migrations.owner_env
scratch_url = migrations.scratch_url

TABLES = {"invoice_counters", "invoices", "refunds"}


def payment(conn, **over) -> int:
    values = {"ref": "bn-1-x", "reason": None, **over}
    return conn.execute(
        text(
            "insert into payments (provider, reference, kind, currency, amount_minor, status, "
            "needs_refund_reason) values ('fake', :ref, 'plan', 'USD', 1800, 'succeeded', :reason) "
            "returning id"
        ),
        values,
    ).scalar()


def invoice(conn, payment_id, **over):
    values = {"n": "BN-T-2026-000001", "sub": 1800, "vat": 0, "total": 1800, **over}
    conn.execute(
        text(
            "insert into invoices (number, payment_id, issued_at, currency, subtotal_minor, "
            "vat_minor, total_minor, seller, buyer, line) values (:n, :p, now(), 'USD', :sub, "
            ":vat, :total, '{}', '{}', '{}')"
        ),
        {"p": payment_id, **values},
    )


def refund(conn, payment_id, status="pending", ref="bnrf-1-a"):
    conn.execute(
        text(
            "insert into refunds (payment_id, reference, reason, status, amount_minor, currency) "
            "values (:p, :r, 'double_charge', :s, 1800, 'USD')"
        ),
        {"p": payment_id, "r": ref, "s": status},
    )


def test_0053_follows_0052(scratch_url):
    assert (
        ScriptDirectory.from_config(alembic_config(scratch_url)).get_revision("0053").down_revision
        == "0052"
    )


def test_the_round_trip(scratch_url):
    cfg = alembic_config(scratch_url)
    engine = create_engine(scratch_url)
    command.upgrade(cfg, "0053")
    assert TABLES <= set(inspect(engine).get_table_names())
    assert "needs_refund_reason" in {c["name"] for c in inspect(engine).get_columns("payments")}
    command.downgrade(cfg, "0052")
    assert not TABLES & set(inspect(engine).get_table_names())
    assert "needs_refund_reason" not in {c["name"] for c in inspect(engine).get_columns("payments")}
    command.upgrade(cfg, "head")
    assert TABLES <= set(inspect(engine).get_table_names())
    engine.dispose()


def test_the_constraints_carry_the_money_rules(scratch_url):
    cfg = alembic_config(scratch_url)
    engine = create_engine(scratch_url)
    command.upgrade(cfg, "0053")
    with engine.begin() as conn:
        first = payment(conn)
        second = payment(conn, ref="bn-2-x")
        invoice(conn, first)
    for bad in (
        lambda c: invoice(c, second, n="BN-T-2026-000001"),  # a number is given once
        lambda c: invoice(c, first, n="BN-T-2026-000002"),  # a payment has one invoice
        lambda c: invoice(c, second, n="BN-T-2026-000003", sub=1000, vat=1),  # parts must add up
        lambda c: invoice(c, second, n="BN-T-2026-000004", sub=0, vat=0, total=0),
    ):
        with pytest.raises(IntegrityError), engine.begin() as conn:
            bad(conn)
    with engine.begin() as conn:
        refund(conn, first, "pending")
    with pytest.raises(IntegrityError), engine.begin() as conn:
        refund(conn, first, "unknown", ref="bnrf-1-b")  # one live refund per payment
    with engine.begin() as conn:
        refund(conn, second, "failed", ref="bnrf-2-a")
        refund(conn, second, "failed", ref="bnrf-2-b")  # closed ones are history
        refund(conn, second, "pending", ref="bnrf-2-c")
    engine.dispose()


@pytest.mark.parametrize("what", ["invoice", "refund", "owed"])
def test_the_downgrade_refuses_while_a_money_record_exists(scratch_url, what):
    cfg = alembic_config(scratch_url)
    engine = create_engine(scratch_url)
    command.upgrade(cfg, "0053")
    with engine.begin() as conn:
        pid = payment(conn, reason="the person already has a live plan" if what == "owed" else None)
        if what == "invoice":
            invoice(conn, pid)
        if what == "refund":
            refund(conn, pid)
    with pytest.raises(Exception, match="Cannot downgrade 0053"):
        command.downgrade(cfg, "0052")
    assert "needs_refund_reason" in {c["name"] for c in inspect(engine).get_columns("payments")}
    engine.dispose()
