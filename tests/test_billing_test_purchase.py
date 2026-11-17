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
    assert "[ok] that payment has exactly one invoice" in out  # stage S9
    assert "[ok] the invoice is for what was paid, and its parts add up" in out
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


def test_the_proof_fails_when_the_invoice_is_missing(session, ada, providers, capsys):
    from app.models import Invoice

    buy_plan(session, ada, providers.usd)
    session.query(Invoice).delete()
    session.commit()

    passed, out = run_proof(session, capsys)

    assert not passed and "[FAIL] that payment has exactly one invoice" in out


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


# ---- the switch-on sequence's proofs ---------------------------------------------------------


def run_preflight(session, step, cfg):
    from sqlalchemy import text

    session.rollback()
    session.execute(text("set transaction read only"))
    passed = script.preflight(session, cfg, step)
    session.rollback()
    return passed


def test_preflight_one_passes_for_a_ready_server_and_names_what_is_missing(session, capsys):
    ready = settings(
        paystack_secret_key="sk_test_" + "a" * 20,
        token_enc_key="S" * 43 + "=",
    )
    assert run_preflight(session, 1, ready), capsys.readouterr().out
    out = capsys.readouterr().out
    assert "a price in USD" not in out or "[ok] a price in USD" in out
    assert "sk_test_" not in out  # a key is reported present, never printed


def test_preflight_one_fails_for_each_missing_precondition(session, capsys):
    base = {"paystack_secret_key": "sk_test_" + "a" * 20, "token_enc_key": "S" * 43 + "="}
    for over, label in (
        ({"billing_enabled": False}, "BILLING_ENABLED is true"),
        ({"billing_allowed_emails": ""}, "BILLING_ALLOWED_EMAILS is not empty"),
        ({"token_enc_key": ""}, "TOKEN_ENC_KEY is set"),
        ({"paystack_secret_key": ""}, "the Paystack secret key is set"),
    ):
        assert not run_preflight(session, 1, settings(**{**base, **over}))
        assert f"[FAIL] {label}" in capsys.readouterr().out


def test_preflight_three_refuses_while_pilot_usage_waits_to_be_allocated(session, ada, capsys):
    from app.models import HourGrant, UsageEntry
    from app.usage import email_hash

    session.add(
        HourGrant(user_id=ada.id, email_hash=email_hash(ada.email), kind="pilot", seconds=3600)
    )
    session.commit()
    assert run_preflight(session, 3, settings())  # nothing waits

    session.add(UsageEntry(user_id=ada.id, email_hash=email_hash(ada.email), seconds=600))
    session.commit()
    assert not run_preflight(session, 3, settings())
    assert "hours_reconcile_backlog" in capsys.readouterr().out


def test_state_prints_what_a_person_can_do_without_changing_anything(
    session, ada, providers, capsys
):
    from datetime import UTC, datetime

    buy_plan(session, ada, providers.usd, now=datetime.now(UTC))
    session.rollback()
    script.state(session, settings(), "ada@example.test")
    out = capsys.readouterr().out
    assert "state: active" in out and "Ask Beyric is allowed" in out
    assert "a new recording would start" in out
