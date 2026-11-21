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


# ---- live billing: only the read-only commands run (the live proof, refund proof, live-check) ---

LIVE_SINCE = "2026-01-01T00:00:00Z"
PAYSTACK_KEY = "sk_live_" + "Z9y8x7w6v5u4t3s2r1q0"  # the body must never appear in any output
FLUTTERWAVE_KEY = "FLWSECK-" + "Q9w8e7r6t5y4u3i2o1p0"


def live_settings(**over):
    return settings(
        billing_live=True,
        billing_live_since=LIVE_SINCE,
        paystack_secret_key=PAYSTACK_KEY,
        paystack_public_key="pk_live_" + "A1b2c3d4e5f6g7h8",
        flutterwave_secret_key=FLUTTERWAVE_KEY,
        flutterwave_public_key="FLWPUBK-" + "M1n2b3v4c5x6z7",
        flutterwave_secret_hash="h" * 64,
        billing_seller_address="1 Marina, Lagos",
        billing_seller_registration_no="RC 000000",
        **over,
    )


@pytest.fixture
def live_invoices(monkeypatch):
    """What the app reads from the process while it is live: invoices get the live series."""
    from app.billing import invoices

    monkeypatch.setattr(invoices, "get_settings", live_settings)


def run_live(session, capsys, *argv, cfg=None):
    """One command through the script's dispatcher: (exit code or message, output)."""
    session.rollback()
    try:
        code = script.run(list(argv), cfg or live_settings(), session)
    except SystemExit as stop:
        code = stop.code
    return code, capsys.readouterr().out


def test_live_billing_refuses_everything_that_writes_or_moves_money(
    session, ada, providers, capsys
):
    from sqlalchemy import func, select

    from app.models import Payment

    buy_plan(session, ada, providers.usd)
    session.rollback()
    before = session.scalar(select(func.count()).select_from(Payment))
    for argv in (("checkout", "ada@example.test"), ("make-due", "ada@example.test", "--yes")):
        code, _ = run_live(session, capsys, *argv)
        assert "test mode only" in str(code)
    # the flag alone is not the lock: with BILLING_LIVE flipped to false the live keys and the
    # cutover time still refuse it (a copy, because `Settings` would refuse the mix at startup)
    flipped = live_settings().model_copy(update={"billing_live": False})
    for argv in (("checkout", "ada@example.test"), ("make-due", "ada@example.test", "--yes")):
        code, _ = run_live(session, capsys, *argv, cfg=flipped)
        assert "test mode only" in str(code)
    only_cutover = settings(billing_live_since=LIVE_SINCE)
    with pytest.raises(SystemExit, match="test mode only"):
        script.guard(only_cutover, "ada@example.test")
    session.rollback()
    assert session.scalar(select(func.count()).select_from(Payment)) == before  # nothing was made
    assert len(providers.usd.checkouts) == 1  # only the purchase, never a second checkout


def test_a_write_inside_the_live_path_fails_in_the_database(
    session, ada, providers, capsys, monkeypatch, live_invoices
):
    """The read-only guarantee is the database's: a command that tried to write fails even though
    the script's own code allowed it to run."""
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    from app.models import Subscription

    buy_plan(session, ada, providers.usd)
    session.rollback()

    def sneaky(db, cfg, email):
        db.execute(text("update subscriptions set retry_count = 9"))
        return 0

    monkeypatch.setattr(script, "live_proof", sneaky)
    with pytest.raises(DBAPIError, match="read-only"):
        script.run(["proof", "ada@example.test"], live_settings(), session)
    session.rollback()
    assert session.query(Subscription).one().retry_count == 0

    with pytest.raises(DBAPIError):
        with script.read_only(session):
            session.execute(text("delete from invoices"))
    session.rollback()


def test_read_only_stops_the_run_when_the_database_does_not_hold_it(session, monkeypatch):
    from sqlalchemy import text

    real = session.execute

    def lie(statement, *args, **kwargs):
        if "show transaction_read_only" in str(statement):
            return real(text("select 'off'"))
        return real(statement, *args, **kwargs)

    session.rollback()
    monkeypatch.setattr(session, "execute", lie)
    with pytest.raises(SystemExit, match="read only"):
        with script.read_only(session):
            pytest.fail("the body must not run")


def test_the_live_proof_passes_with_a_signed_webhook_and_says_so(
    session, ada, providers, capsys, live_invoices
):
    buy_plan(session, ada, providers.usd)

    code, out = run_live(session, capsys, "proof", "ada@example.test")

    assert code == 0, out
    print(out)  # `pytest -s` shows the real output pasted in the PR
    for line in (
        "[ok] the payment succeeded",
        "[ok] paid_at is set",
        "[ok] exactly one live subscription",
        "[ok] the subscription is active",
        "[ok] the invoice number is in the live series BN-<year>-NNNNNN: BN-",
        "[ok] the invoice is not test-mode",
        "[ok] the invoice is not hidden by BILLING_LIVE_SINCE",
        "[ok] the grant equals the plan's hours: 20.00 h, the plan has 20.00 h",
        "WEBHOOK PATH PROVED",
        "PROOF: OK",
    ):
        assert line in out, line
    assert "WEBHOOK PATH NOT PROVED" not in out
    assert "@" not in out and "tok-1" not in out and "Z9y8x7w6" not in out


def test_the_live_proof_says_the_webhook_path_is_not_proved_when_the_sweeper_settled_it(
    session, ada, providers, capsys, live_invoices
):
    from app.models import BillingEvent

    buy_plan(session, ada, providers.usd)
    session.query(BillingEvent).delete()  # no webhook reached us: the payment was settled otherwise
    session.commit()

    code, out = run_live(session, capsys, "proof", "ada@example.test")

    assert code == 2, out
    assert "WEBHOOK PATH NOT PROVED: the sweeper settled it without a signed webhook" in out
    assert "PROOF: OK, BUT WEBHOOK PATH NOT PROVED" in out
    assert "[FAIL]" not in out


def test_an_unsigned_or_undecided_event_does_not_prove_the_webhook_path(
    session, ada, providers, capsys, live_invoices
):
    from app.models import BillingEvent

    buy_plan(session, ada, providers.usd)
    session.query(BillingEvent).update({"processed_at": None})
    session.commit()
    code, out = run_live(session, capsys, "proof", "ada@example.test")
    assert code == 2 and "WEBHOOK PATH NOT PROVED" in out and "decided False" in out

    session.query(BillingEvent).update({"signature_ok": False})
    session.commit()
    code, out = run_live(session, capsys, "proof", "ada@example.test")
    assert code == 2 and "signature_ok False" in out


def test_the_live_proof_fails_for_a_test_mode_invoice_and_for_a_test_era_payment(
    session, ada, providers, capsys
):
    from datetime import timedelta

    from app.billing.cutover import parse_instant
    from app.models import Payment

    buy_plan(session, ada, providers.usd)  # settled with test settings: a BN-T- invoice
    code, out = run_live(session, capsys, "proof", "ada@example.test")
    assert code == 1
    assert "[FAIL] the invoice number is in the live series" in out
    assert "[FAIL] the invoice is not test-mode" in out and "PROOF: FAILED" in out

    # a payment made before the cutover does not exist for the live proof
    session.query(Payment).update({"created_at": parse_instant(LIVE_SINCE) - timedelta(days=1)})
    session.commit()
    code, out = run_live(session, capsys, "proof", "ada@example.test")
    assert code == 1 and "[FAIL] a live plan payment exists for this account" in out


def test_the_live_proof_fails_when_the_grant_does_not_match_the_plan(
    session, ada, providers, capsys, live_invoices
):
    from app.models import HourGrant

    buy_plan(session, ada, providers.usd)
    session.query(HourGrant).update({"seconds": 3600})
    session.commit()

    code, out = run_live(session, capsys, "proof", "ada@example.test")

    assert code == 1 and "[FAIL] the grant equals the plan's hours" in out


def test_the_refund_proof_passes_after_the_first_refund_and_fails_before_it(
    session, ada, providers, capsys, live_invoices
):
    from datetime import timedelta

    from app.billing import refunds
    from tests.billing_support import T0
    from tests.test_web import make_user

    admin = make_user(session, "admin@example.test", admin=True)
    buy_plan(session, ada, providers.usd)
    code, out = run_live(session, capsys, "proof", "ada@example.test", "--refund")
    assert code == 1 and "[FAIL] a refund of a live plan payment exists" in out

    payment = session.query(script.Payment).one()
    refunds.request(
        session, payment.id, "first_plan_7d", admin, None, live_settings(), T0 + timedelta(days=2)
    )

    code, out = run_live(session, capsys, "proof", "ada@example.test", "--refund")
    assert code == 0, out
    print(out)
    for line in (
        "[ok] the reason is first_plan_7d",
        "[ok] the refund is submitted",
        "[ok] the provider's refund id is present: present",
        "[ok] the payment is refunded",
        "[ok] the subscription is ended",
        "[ok] the hours the payment bought were ended",
        "REFUND PROOF: OK",
    ):
        assert line in out, line
    assert "fake-refund-1" not in out and "@" not in out
    # and the payment proof now says the payment was refunded, pointing at --refund
    code, out = run_live(session, capsys, "proof", "ada@example.test")
    assert code == 1 and "status refunded (use --refund)" in out


def test_live_check_passes_for_a_ready_live_server_and_prints_no_secret(session, capsys):
    code, out = run_live(session, capsys, "live-check")

    assert code == 0, out
    print(out)
    assert "PREFLIGHT OK" in out
    for line in (
        "[ok] the Paystack secret key is a live key: sk_live_",
        "[ok] the Flutterwave secret key is set and is not a test key: FLWSECK-",
        "[ok] FLUTTERWAVE_SECRET_HASH is set",
        "[ok] PAYSTACK_WEBHOOK_SECRET is unset: the classic x-paystack-signature check",
        "[ok] BILLING_LIVE_SINCE is set and has passed",
        "plan p20: 20 h, USD 18.00",
        "plan p20: 20 h, NGN 25,000.00",
        "live invoice counters: none used yet",
    ):
        assert line in out, line
    for secret in (PAYSTACK_KEY, FLUTTERWAVE_KEY, "Z9y8x7w6", "Q9w8e7r6", "h" * 64, "@"):
        assert secret not in out


def test_live_check_fails_for_each_missing_live_precondition(session, capsys):
    base = live_settings()
    cases = (
        ({"billing_live_since": ""}, "BILLING_LIVE_SINCE is set and has passed"),
        ({"flutterwave_secret_hash": ""}, "FLUTTERWAVE_SECRET_HASH is set"),
        ({"billing_seller_address": ""}, "BILLING_SELLER_ADDRESS is set"),
        ({"billing_seller_registration_no": ""}, "BILLING_SELLER_REGISTRATION_NO is set"),
        ({"flutterwave_secret_key": ""}, "the Flutterwave secret key is set"),
        ({"paystack_secret_key": ""}, "the Paystack secret key is a live key"),
        ({"billing_enabled": False}, "BILLING_ENABLED is true"),
        ({"billing_live": False}, "BILLING_LIVE is true"),
        # the 48-character value that was once pasted there: it equals the key, so it is refused
        ({"paystack_webhook_secret": PAYSTACK_KEY}, "PAYSTACK_WEBHOOK_SECRET is set and is not"),
    )
    for over, label in cases:
        code, out = run_live(session, capsys, "live-check", cfg=base.model_copy(update=over))
        assert code == 1 and f"[FAIL] {label}" in out and "PREFLIGHT FAILED" in out, label
    # a webhook secret of its own is allowed, and the line says which scheme it implies
    ok = base.model_copy(update={"paystack_webhook_secret": "w" * 40})
    code, out = run_live(session, capsys, "live-check", cfg=ok)
    assert code == 0 and "timestamped Paystack-Signature scheme" in out and "w" * 40 not in out


def test_live_check_needs_prices_in_both_currencies_and_reports_the_inbox(
    session, ada, providers, capsys, live_invoices
):
    from datetime import timedelta

    from app.models import BillingEvent, PlanPrice

    buy_plan(session, ada, providers.usd)
    code, out = run_live(session, capsys, "live-check")
    assert code == 0 and "live invoice counters: " in out and "none used yet" not in out

    from datetime import UTC, datetime

    half_hour_ago = datetime.now(UTC) - timedelta(minutes=30)  # the fixtures' clock is T0, ahead
    session.query(BillingEvent).update({"processed_at": None, "received_at": half_hour_ago})
    session.commit()
    code, out = run_live(session, capsys, "live-check")
    assert code == 1 and "[FAIL] no webhook has waited more than 5 minutes" in out

    session.query(BillingEvent).update(
        {"processed_at": BillingEvent.received_at, "signature_ok": False}
    )
    session.commit()
    code, out = run_live(session, capsys, "live-check")
    assert code == 1 and "[FAIL] no stored webhook has signature_ok false" in out

    session.query(PlanPrice).filter(PlanPrice.currency == "NGN").delete()
    session.commit()
    code, out = run_live(session, capsys, "live-check")
    assert code == 1 and "[FAIL] a price in NGN" in out


def test_state_and_the_test_mode_commands_still_run_through_the_dispatcher(
    session, ada, providers, capsys, live_invoices
):
    buy_plan(session, ada, providers.usd)

    code, out = run_live(session, capsys, "state", "ada@example.test")
    assert code == 0 and "Ask Beyric is" in out and "@" not in out  # live: allowed, read only

    cfg = settings()  # test mode: the old proof, unchanged
    code, out = run_live(session, capsys, "proof", "ada@example.test", cfg=cfg)
    assert code == 0 and "PROOF: OK" in out and "WEBHOOK PATH" not in out
    code, out = run_live(session, capsys, "preflight", "1", cfg=live_settings())
    assert "use live-check" in str(code)
