"""Migration 0065 (review S2): paid hour grants are bound to the account that paid for them.

Every paid grant is made with its `user_id`, so the backfill is a safety net: it gives a plan or
pack grant that has lost its `user_id` (a row from before the rule, or a repair) back to the live
account that paid for it, and nothing else. Runs the real migration on the scratch database."""

from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text

from tests import test_migrations as migrations

alembic_config = migrations.alembic_config
owner_env = migrations.owner_env
scratch_url = migrations.scratch_url


def test_0065_follows_0064_and_there_is_one_head_above_it(scratch_url):
    script = ScriptDirectory.from_config(alembic_config(scratch_url))
    assert script.get_revision("0065").down_revision == "0064"
    heads = script.get_heads()
    assert len(heads) == 1  # later migrations sit on top of 0065, so it need not be the head
    assert "0065" in {r.revision for r in script.iterate_revisions(heads[0], "base")}


def new_user(conn, email, status="active", purged=False):
    return conn.execute(
        text(
            "insert into users (email, status, purged_at) values "
            "(:e, :s, case when :p then now() else null end) returning id"
        ),
        {"e": email, "s": status, "p": purged},
    ).scalar()


def payment(conn, ref, payer):
    return conn.execute(
        text(
            "insert into payments (payer_user_id, provider, kind, currency, amount_minor, "
            "reference) values (:u, 'paystack', 'plan', 'NGN', 5, :r) returning id"
        ),
        {"u": payer, "r": ref},
    ).scalar()


def subscription(conn, owner, status="active"):
    return conn.execute(
        text(
            "insert into subscriptions (user_id, plan_code, price_id, status, currency, provider, "
            "current_period_start, current_period_end) values (:u, 'plan_20h', "
            "(select id from plan_prices limit 1), :s, 'USD', 'flutterwave', now(), "
            "now() + interval '30 days') returning id"
        ),
        {"u": owner, "s": status},
    ).scalar()


def grant(conn, kind, *, user=None, pay=None, sub=None, hash_="h"):
    return conn.execute(
        text(
            "insert into hour_grants (user_id, email_hash, kind, seconds, source_payment_id, "
            "subscription_id) values (:u, :h, :k, 3600, :p, :s) returning id"
        ),
        {"u": user, "h": hash_, "k": kind, "p": pay, "s": sub},
    ).scalar()


def owner_of(conn, grant_id):
    return conn.execute(
        text("select user_id from hour_grants where id = :i"), {"i": grant_id}
    ).scalar()


def test_the_backfill_binds_only_what_a_live_payer_clearly_owns_and_the_round_trip_holds(
    scratch_url,
):
    cfg = alembic_config(scratch_url)
    engine = create_engine(scratch_url)
    command.upgrade(cfg, "0064")
    try:
        _round_trip(cfg, engine)
    finally:
        engine.dispose()


def _round_trip(cfg, engine):
    with engine.begin() as conn:
        ada = new_user(conn, "ada@example.test")
        ben = new_user(conn, "ben@example.test")
        gone = new_user(conn, "gone@example.test", status="deleted")
        purged = new_user(conn, "purged@example.test", purged=True)
        by_payment = grant(conn, "plan_period", pay=payment(conn, "bn-1-a", ada))
        by_subscription = grant(conn, "plan_period", sub=subscription(conn, ben))
        a_pack = grant(conn, "pack", pay=payment(conn, "bn-2-b", ada))
        no_payer = grant(conn, "plan_period", pay=payment(conn, "bn-3-c", None))
        of_deleted = grant(conn, "pack", pay=payment(conn, "bn-4-d", gone))
        of_purged = grant(conn, "pack", pay=payment(conn, "bn-5-e", purged))
        disagree = grant(
            conn,
            "plan_period",
            pay=payment(conn, "bn-6-f", ada),
            sub=subscription(conn, ben, status="lapsed"),
        )
        already = grant(conn, "pack", user=ben, pay=payment(conn, "bn-7-g", ada))
        pilot = grant(conn, "pilot")
        trial = grant(conn, "trial")
        gift = grant(conn, "admin_gift")

    command.upgrade(cfg, "0065")

    with engine.connect() as conn:
        assert owner_of(conn, by_payment) == ada
        assert owner_of(conn, by_subscription) == ben
        assert owner_of(conn, a_pack) == ada
        for untouched in (no_payer, of_deleted, of_purged, disagree, pilot, trial, gift):
            assert owner_of(conn, untouched) is None, untouched
        assert owner_of(conn, already) == ben  # never overwritten

    command.downgrade(cfg, "0064")
    command.upgrade(cfg, "0065")  # re-runnable
    with engine.connect() as conn:
        assert conn.execute(text("select version_num from alembic_version")).all() == [("0065",)]
        assert owner_of(conn, by_payment) == ada
