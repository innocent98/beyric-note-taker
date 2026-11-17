"""`scripts/billing_test_purchase.py`: the runbook's proof command and its guards (billing stage
S8a). The proof must pass for a real purchase through the real settlement and fail for anything
less; it is read-only; and the script does nothing outside test mode or off the allow-list."""

import importlib.util
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy.exc import DBAPIError

from app.billing import subscriptions as subs
from tests.billing_support import T0, buy_plan, choose_country, make_catalog, settings
from tests.test_web import make_user

_spec = importlib.util.spec_from_file_location(
    "billing_test_purchase",
    Path(__file__).parent.parent / "scripts" / "billing_test_purchase.py",
)
script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(script)


@pytest.fixture(autouse=True)
def catalog(session, vault_key):
    make_catalog(session)


@pytest.fixture
def ada(session):
    return make_user(session, "ada@example.test")


def run_proof(session, capsys):
    session.rollback()
    from sqlalchemy import text

    session.execute(text("set transaction read only"))
    passed = script.proof(session, settings(), "ada@example.test")
    session.rollback()
    return passed, capsys.readouterr().out


def test_the_proof_passes_for_a_real_purchase_and_says_what_it_checked(
    session, ada, providers, capsys
):
    buy_plan(session, ada, providers.usd)

    passed, out = run_proof(session, capsys)

    assert passed, out
    assert "[ok] a plan payment has succeeded" in out
    assert "[ok] exactly one plan-period grant came from that payment" in out
    assert "saved card for renewals: yes" in out
    assert "@" not in out and "tok-1" not in out  # no email, no token


def test_the_proof_fails_when_nothing_was_bought(session, ada, providers, capsys):
    choose_country(session, ada, "US")
    started = subs.start_plan_checkout(session, ada, "p20", settings(), T0)  # never paid
    assert started.payment.status == "pending"

    passed, out = run_proof(session, capsys)

    assert not passed and "[FAIL] a plan payment has succeeded" in out


def test_the_proof_fails_when_a_grant_is_missing(session, ada, providers, capsys):
    from app.models import HourGrant

    buy_plan(session, ada, providers.usd)
    session.query(HourGrant).delete()
    session.commit()

    passed, out = run_proof(session, capsys)

    assert not passed and "[FAIL] exactly one plan-period grant" in out


def test_the_proof_is_read_only(session, ada, providers):
    from sqlalchemy import text

    buy_plan(session, ada, providers.usd)
    session.rollback()
    session.execute(text("set transaction read only"))
    with pytest.raises(DBAPIError):
        session.execute(text("update subscriptions set retry_count = 9"))
    session.rollback()


def test_the_script_refuses_outside_test_mode_or_off_the_allow_list(session, ada):
    with pytest.raises(SystemExit, match="BILLING_ENABLED"):
        script.guard(settings(billing_enabled=False), "ada@example.test")
    with pytest.raises(SystemExit, match="test mode only"):
        script.guard(settings(billing_live=True), "ada@example.test")
    with pytest.raises(SystemExit, match="ALLOWED_EMAILS"):
        script.guard(settings(billing_allowed_emails="other@example.test"), "ada@example.test")
    script.guard(settings(), "ada@example.test")  # all three right: no error


def test_make_due_moves_the_period_back_an_hour_and_the_scheduler_then_renews(
    session, ada, providers
):
    from datetime import UTC, datetime

    from app.billing import renewals

    buy_plan(session, ada, providers.usd, now=datetime.now(UTC) - timedelta(days=3))
    script.make_due(session, settings(), "ada@example.test")
    session.expire_all()
    sub = subs.live_row(session, ada.id)
    assert sub.current_period_end < datetime.now(UTC)

    renewals.run_due(session, datetime.now(UTC), settings())

    assert len(providers.usd.charges) == 1  # the saved test card was charged
    assert subs.live_row(session, ada.id).status == "active"
