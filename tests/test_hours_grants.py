"""Migration 0049 (billing stage S1): the two tables, the pilot backfill and the round trip.

The backfill gives every account that is not an admin and not deleted one `pilot` grant of its old
cap minus its ledger (by user id or email fingerprint), never below 0, so nobody loses or gains an
hour. These run the real migration on the scratch database, from revision 0048 with people in each
shape the backfill must tell apart."""

from datetime import UTC, datetime

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from app.usage import email_hash
from tests import test_migrations as migrations

# The scratch database, the owner environment (an autouse fixture) and the alembic config are the
# migration tests' own, bound here by assignment so pytest finds the fixtures in this module.
alembic_config = migrations.alembic_config
owner_env = migrations.owner_env
scratch_url = migrations.scratch_url
OWNER_EMAIL = migrations.OWNER_EMAIL

HOUR = 3600


def _rows(engine, sql, **params):
    with engine.connect() as conn:
        return [tuple(r) for r in conn.execute(text(sql), params)]


def _people(engine) -> dict[str, int]:
    """Accounts at 0048, by short name. Usage is written by hand (the ledger's own columns)."""
    ids: dict[str, int] = {}
    with engine.begin() as conn:

        def user(name, **fields):
            cols = {"email": f"{name}@example.test", "status": "active", **fields}
            names = ", ".join(cols)
            marks = ", ".join(f":{k}" for k in cols)
            ids[name] = conn.execute(
                text(f"insert into users ({names}) values ({marks}) returning id"), cols
            ).scalar()

        def spend(who, seconds, *, by_id=True, email=None):
            conn.execute(
                text(
                    "insert into usage_entries (user_id, email_hash, seconds) values (:u, :h, :s)"
                ),
                {
                    "u": ids[who] if by_id else None,
                    "h": email_hash(email or f"{who}@example.test"),
                    "s": seconds,
                },
            )

        user("fresh")  # pilot account, nothing used
        user("partly")
        spend("partly", 2 * HOUR)
        user("spent")
        spend("spent", 5 * HOUR)
        user("over")  # a running meeting finished past the cap
        spend("over", 5 * HOUR + 1200)
        user("bigger", hours_cap_seconds=10 * HOUR)  # an admin raised this person's cap
        spend("bigger", HOUR)
        user("smaller", hours_cap_seconds=HOUR)
        spend("smaller", 2 * HOUR)
        # Deleted and signed up again: the old account's rows kept only the fingerprint, and one
        # more row is keyed by both the id and the fingerprint (it must count once).
        user("again")
        spend("again", 3 * HOUR, by_id=False)
        spend("again", HOUR)
        user("suspended", status="suspended")
        spend("suspended", HOUR)
        user("gone", status="deleted")
        user("hollow", status="deleted", purged_at=datetime(2026, 11, 1, tzinfo=UTC))
        user("boss", is_admin=True)
        spend("boss", 50 * HOUR)
        ids["owner"] = conn.execute(
            text("select id from users where email = :e"), {"e": OWNER_EMAIL}
        ).scalar()
    return ids


def _grants(engine) -> dict[str, tuple]:
    rows = _rows(
        engine,
        "select u.email, g.kind, g.seconds, g.email_hash, g.expires_at is null, g.starts_at "
        "from hour_grants g join users u on u.id = g.user_id order by u.id",
    )
    return {r[0].split("@")[0]: r[1:] for r in rows}


@pytest.fixture
def at_0048(scratch_url):
    cfg = alembic_config(scratch_url)
    command.upgrade(cfg, "0048")
    engine = create_engine(scratch_url)
    ids = _people(engine)
    yield cfg, engine, ids
    engine.dispose()


def test_0049_follows_0048_and_is_the_single_head(scratch_url):
    script = ScriptDirectory.from_config(alembic_config(scratch_url))
    assert script.get_revision("0049").down_revision == "0048"
    assert script.get_heads() == ["0049"]


def test_the_backfill_gives_each_person_their_old_cap_minus_their_ledger(at_0048):
    cfg, engine, ids = at_0048
    command.upgrade(cfg, "0049")
    grants = _grants(engine)
    seconds = {name: row[1] for name, row in grants.items()}
    assert seconds == {
        "fresh": 5 * HOUR,
        "partly": 3 * HOUR,
        "spent": 0,  # fully used: a row, with nothing left
        "over": 0,  # never below zero
        "bigger": 9 * HOUR,  # their own cap, not the default
        "smaller": 0,
        "again": 1 * HOUR,  # 5 - 3 (the old account's rows) - 1 (counted once)
        "suspended": 4 * HOUR,
    }


def test_the_backfill_matches_what_the_old_rule_says_is_left(at_0048):
    from sqlalchemy.orm import Session

    from app import usage
    from app.models import User

    cfg, engine, ids = at_0048
    command.upgrade(cfg, "0049")
    grants = _grants(engine)
    with Session(engine) as session:
        for name, row in grants.items():
            user = session.get(User, ids[name])
            left = max(usage.effective_cap(session, user) - usage.ledger_seconds(session, user), 0)
            assert row[1] == left, name


def test_the_grant_is_a_pilot_grant_without_expiry_keyed_by_the_email_fingerprint(at_0048):
    cfg, engine, ids = at_0048
    command.upgrade(cfg, "0049")
    kind, seconds, fingerprint, no_expiry, starts_at = _grants(engine)["fresh"]
    assert kind == "pilot" and no_expiry
    assert fingerprint == email_hash("fresh@example.test")  # SQL and Python agree
    assert starts_at is not None


def test_an_admin_a_deleted_and_a_purged_account_get_no_grant(at_0048):
    cfg, engine, ids = at_0048
    command.upgrade(cfg, "0049")
    got = set(_grants(engine))
    assert not {"boss", "owner", "gone", "hollow"} & got  # the migration's own admin is `owner`
    assert _rows(engine, "select count(*) from hour_grants") == [(len(got),)]


def test_no_allocation_is_written_for_the_past(at_0048):
    cfg, engine, ids = at_0048
    command.upgrade(cfg, "0049")
    assert _rows(engine, "select count(*) from usage_allocations") == [(0,)]
    # the ledger itself is untouched
    assert _rows(engine, "select count(*) from usage_entries") == [(9,)]


def test_the_app_default_cap_is_the_default_the_backfill_reads(at_0048):
    cfg, engine, ids = at_0048
    with engine.begin() as conn:
        conn.execute(text("update app_state set default_hours_cap_seconds = :c"), {"c": 8 * HOUR})
    command.upgrade(cfg, "0049")
    seconds = {name: row[1] for name, row in _grants(engine).items()}
    assert seconds["fresh"] == 8 * HOUR and seconds["partly"] == 6 * HOUR
    assert seconds["bigger"] == 9 * HOUR  # an override still wins


def test_a_missing_app_default_falls_back_to_five_hours_like_the_spec_says(at_0048):
    cfg, engine, ids = at_0048
    with engine.begin() as conn:
        conn.execute(text("update app_state set default_hours_cap_seconds = null"))
    command.upgrade(cfg, "0049")
    assert _grants(engine)["fresh"][1] == 5 * HOUR  # 18000: the spec's last fallback


def test_the_tables_have_their_constraints(at_0048):
    cfg, engine, ids = at_0048
    command.upgrade(cfg, "0049")
    inspector = inspect(engine)
    assert {c["name"] for c in inspector.get_columns("hour_grants")} == {
        "id",
        "user_id",
        "email_hash",
        "kind",
        "seconds",
        "starts_at",
        "expires_at",
        "source_payment_id",
        "subscription_id",
        "reason",
        "created_by",
        "created_at",
    }
    assert {c["name"] for c in inspector.get_columns("usage_allocations")} == {
        "id",
        "usage_entry_id",
        "grant_id",
        "seconds",
    }
    uniques = {u["name"] for u in inspector.get_unique_constraints("hour_grants")}
    assert "uq_hour_grants_kind_payment" in uniques


def test_a_payment_can_grant_only_once_but_grants_without_a_payment_do_not_collide(at_0048):
    from sqlalchemy.exc import IntegrityError

    cfg, engine, ids = at_0048
    command.upgrade(cfg, "0049")

    def insert(kind, payment):
        with engine.begin() as conn:
            conn.execute(
                text(
                    "insert into hour_grants (email_hash, kind, seconds, source_payment_id) "
                    "values ('h', :k, 1, :p)"
                ),
                {"k": kind, "p": payment},
            )

    insert("pack", None)
    insert("pack", None)  # null payments never collide
    insert("pack", 7)
    with pytest.raises(IntegrityError):
        insert("pack", 7)  # the same payment, replayed
    insert("plan_period", 7)  # another kind from the same payment id is its own row
    with pytest.raises(IntegrityError):
        insert("coupon", None)  # not a kind


def test_the_round_trip_goes_down_and_up_again_with_the_same_grants(at_0048):
    cfg, engine, ids = at_0048
    command.upgrade(cfg, "0049")
    before = {name: row[:2] for name, row in _grants(engine).items()}
    command.downgrade(cfg, "0048")
    tables = set(inspect(engine).get_table_names())
    assert not {"hour_grants", "usage_allocations"} & tables
    command.upgrade(cfg, "0049")
    assert {name: row[:2] for name, row in _grants(engine).items()} == before


def test_the_downgrade_refuses_while_a_grant_that_is_not_a_pilot_grant_exists(at_0048):
    cfg, engine, ids = at_0048
    command.upgrade(cfg, "0049")
    with engine.begin() as conn:
        conn.execute(
            text(
                "insert into hour_grants (user_id, email_hash, kind, seconds) "
                "values (:u, 'h', 'pack', 36000)"
            ),
            {"u": ids["fresh"]},
        )
    with pytest.raises(RuntimeError, match=r"Cannot downgrade 0049: 1 hour grant"):
        command.downgrade(cfg, "0048")
    # nothing half-ran: still at 0049 with its tables
    assert _rows(engine, "select version_num from alembic_version") == [("0049",)]
    assert "hour_grants" in inspect(engine).get_table_names()
    with engine.begin() as conn:
        conn.execute(text("delete from hour_grants where kind = 'pack'"))
    command.downgrade(cfg, "0048")  # once it is gone the migration goes down
    command.upgrade(cfg, "head")
