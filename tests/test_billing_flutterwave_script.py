"""`scripts/flutterwave_test_run.py` (billing stage S7): the guard that keeps a live key out, and
what it prints. No network: the Flutterwave side is `httpx.MockTransport`; the bodies are documented
samples, not captures (see `tests/test_billing_flutterwave.py`)."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.billing.flutterwave import FlutterwaveProvider
from app.models import Payment
from tests.test_billing_flutterwave import CHECKOUT_OK, Wire, reply, settings, verify_text
from tests.test_web import make_user

SPEC = importlib.util.spec_from_file_location(
    "flutterwave_test_run",
    Path(__file__).resolve().parent.parent / "scripts/flutterwave_test_run.py",
)
script = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(script)


def test_a_run_in_live_mode_is_refused():
    live = SimpleNamespace(billing_live=True, flutterwave_secret_key="FAKE-NOT-A-LIVE-KEY")
    with pytest.raises(SystemExit, match="BILLING_LIVE"):
        script.guard(live)


@pytest.mark.parametrize("key", ["", "FAKE-NOT-A-LIVE-KEY", "something-else"])
def test_a_run_with_anything_but_a_test_key_is_refused(key):
    with pytest.raises(SystemExit, match="TEST key"):
        script.guard(SimpleNamespace(billing_live=False, flutterwave_secret_key=key))


def test_a_test_key_in_test_mode_is_allowed():
    script.guard(settings())  # the fake key in the fixtures contains _TEST


def test_checkout_makes_a_pending_dollar_payment_and_prints_the_link(session, capsys):
    make_user(session, "payer@example.test")
    session.commit()
    wire = Wire(reply(CHECKOUT_OK))
    provider = FlutterwaveProvider(settings(), transport=httpx.MockTransport(wire))

    payment = script.checkout(
        session, provider, "Payer@Example.test", 1800, "http://localhost/back"
    )

    out = capsys.readouterr().out
    assert payment.reference in out and "https://checkout.flutterwave.com/" in out
    row = session.get(Payment, payment.id)
    assert (row.status, row.currency, row.amount_minor, row.provider) == (
        "pending",
        "USD",
        1800,
        "flutterwave",
    )
    assert wire.body()["tx_ref"] == payment.reference


def test_checkout_for_an_unknown_email_makes_nothing(session):
    wire = Wire(reply(CHECKOUT_OK))
    provider = FlutterwaveProvider(settings(), transport=httpx.MockTransport(wire))
    with pytest.raises(SystemExit, match="No account"):
        script.checkout(session, provider, "nobody@example.test", 1800, "http://localhost/back")
    assert wire.requests == []


def test_raw_prints_only_the_money_fields_never_the_token_or_email(capsys):
    provider = FlutterwaveProvider(
        settings(), transport=httpx.MockTransport(Wire(reply(verify_text())))
    )
    script.raw(provider, "285959875")
    out = capsys.readouterr().out
    for line in (
        "currency: USD",
        "amount: 18.00",
        "amount_settled: 17.3",
        "card.country: NIGERIA NG",
    ):
        assert line in out
    assert "FAKE-NOT-A-TOKEN" not in out and "payer@example.test" not in out


def test_raw_shows_the_token_only_when_asked(capsys):
    provider = FlutterwaveProvider(
        settings(), transport=httpx.MockTransport(Wire(reply(verify_text())))
    )
    script.raw(provider, "285959875", show_token=True)
    assert "card.token: FAKE-NOT-A-TOKEN" in capsys.readouterr().out


def test_charge_makes_a_pending_renewal_payment_and_prints_what_flutterwave_says(session, capsys):
    from tests.test_billing_flutterwave import CHARGE_3DS

    make_user(session, "payer@example.test")
    session.commit()
    wire = Wire(reply(CHARGE_3DS))
    provider = FlutterwaveProvider(settings(), transport=httpx.MockTransport(wire))

    script.charge(session, provider, "payer@example.test", "FAKE-NOT-A-TOKEN", 999)

    assert "status=pending" in capsys.readouterr().out
    assert (
        wire.body()["email"] == "payer@example.test"
        and b'"amount": 9.99' in wire.requests[0].content
    )
    [row] = session.query(Payment).all()
    assert (row.kind, row.status, row.amount_minor) == ("renewal", "pending", 999)
