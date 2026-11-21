"""`BILLING_LIVE_SINCE`, the go-live cutover (`app/billing/cutover.py`): the setting is validated at
startup, it is ignored while `BILLING_LIVE` is false, and while it is in force a payment made before
it (a test-era payment) is hidden from the person's invoices and payments, is not their "first plan
payment", and offers no refund. Real Postgres, the app's own routes, the fake providers."""

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker

from app.billing import cutover, invoice_pdf, invoices, refunds
from app.models import Invoice, Payment
from tests.billing_support import buy_plan, make_catalog, settings
from tests.test_billing_invoices_api import ADMIN_API, buy
from tests.test_billing_profile import API, api_client, error
from tests.test_billing_subscriptions_api import Buyer
from tests.test_web import make_settings, make_user

_spec = importlib.util.spec_from_file_location(
    "billing_close_test_data",
    Path(__file__).parent.parent / "scripts" / "billing_close_test_data.py",
)
closeout = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(closeout)

T_TEXT = "2026-11-10T12:00:00Z"
T = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)


# ---- the setting ----


def test_the_setting_is_a_timestamp_with_a_zone():
    for text_value in (T_TEXT, "2026-11-10T13:00:00+01:00", "2026-11-10 12:00:00+00:00"):
        cfg = make_settings(billing_live=True, billing_live_since=text_value)
        assert cutover.since(cfg) == T, text_value


@pytest.mark.parametrize("live", [True, False], ids=["live", "not live"])
@pytest.mark.parametrize(
    "value",
    ["garbage", "2026-11-10", "2026-11-10T12:00:00", "12:00", "yesterday", "2026-13-40T00:00:00Z"],
)
def test_garbage_stops_startup_and_the_value_is_never_printed(live, value):
    with pytest.raises(ValidationError) as exc:
        make_settings(billing_live=live, billing_live_since=value)
    message = str(exc.value)
    assert "BILLING_LIVE_SINCE" in message
    assert value not in message.replace("BILLING_LIVE_SINCE", "")


def test_a_future_cutover_stops_startup_only_while_billing_is_live():
    future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    with pytest.raises(ValidationError, match="in the future"):
        make_settings(billing_live=True, billing_live_since=future)
    cfg = make_settings(billing_live=False, billing_live_since=future)  # no effect: allowed
    assert cutover.since(cfg) is None


def test_the_setting_is_ignored_while_billing_is_not_live():
    cfg = make_settings(billing_live=False, billing_live_since=T_TEXT)
    assert cutover.since(cfg) is None
    assert cutover.is_test_era(T - timedelta(days=30), cfg) is False
    assert make_settings().billing_live_since == "" and cutover.since(make_settings()) is None


def test_the_rule_is_before_t_and_a_row_at_t_is_live():
    cfg = make_settings(billing_live=True, billing_live_since=T_TEXT)
    assert cutover.is_test_era(T - timedelta(seconds=1), cfg)
    assert not cutover.is_test_era(T, cfg)
    assert not cutover.is_test_era(T + timedelta(seconds=1), cfg)


# ---- the routes ----


@pytest.fixture(autouse=True)
def catalog(session, vault_key):
    make_catalog(session)


@pytest.fixture(autouse=True)
def process_settings(monkeypatch):
    monkeypatch.setattr(invoices, "get_settings", lambda: settings(mail_provider="console"))


def buy_pack(session, ada, provider):
    out = ada.post(f"{API}/checkout", {"kind": "pack", "code": "pack10"})
    assert out.status_code == 200, out.text
    payment = session.scalars(
        select(Payment).where(Payment.reference == out.json()["payment"]["reference"])
    ).one()
    from tests.billing_support import pay

    pay(session, provider, payment, datetime.now(UTC), card_token=None)
    session.expire_all()
    return payment


def backdate(session, payment, when):
    session.execute(
        text("update payments set created_at = :t where id = :i"), {"t": when, "i": payment.id}
    )
    session.commit()
    session.expire_all()


@pytest.fixture
def cutover_time():
    return datetime.now(UTC) - timedelta(hours=1)


def mixed(engine, session, providers, cutover_time, **client_settings):
    """Ada paid for a plan before the cutover (test keys) and for a pack after it (live keys)."""
    make_user(session, "ada@example.test")
    make_user(session, "root@example.test", admin=True)
    web = api_client(engine, **client_settings).__enter__()
    ada = Buyer(web, "ada@example.test")
    test_payment = buy(session, ada, providers.usd)
    backdate(session, test_payment, cutover_time - timedelta(days=1))
    live_payment = buy_pack(session, ada, providers.usd)
    root = Buyer(TestClient(web.app, follow_redirects=False), "root@example.test")
    return web, ada, root, test_payment, live_payment


def shown(session, payment):
    from app.billing.invoice_number import shown as shown_number

    return shown_number(
        session.scalars(select(Invoice.number).where(Invoice.payment_id == payment.id)).one()
    )


def test_a_test_era_payment_is_hidden_from_the_person_and_a_live_one_is_not(
    engine, session, providers, cutover_time, monkeypatch
):
    monkeypatch.setattr(invoice_pdf, "build_pdf", lambda doc: b"%PDF-1.4 fake")
    web, ada, root, test_payment, live_payment = mixed(
        engine,
        session,
        providers,
        cutover_time,
        billing_live=True,
        billing_live_since=cutover_time.isoformat(),
    )
    test_number, live_number = shown(session, test_payment), shown(session, live_payment)

    invoices_out = ada.get(f"{API}/invoices").json()["invoices"]
    assert [row["number"] for row in invoices_out] == [live_number]
    # the PDF of a test-era invoice does not exist for the person; the live one downloads
    error(ada.get(f"{API}/invoices/{test_number}/pdf"), 404, "not_found")
    assert ada.get(f"{API}/invoices/{live_number}/pdf").status_code == 200
    # the billing history
    rows = ada.get(f"{API}/payments").json()["payments"]
    assert [row["reference"] for row in rows] == [live_payment.reference]
    assert rows[0]["invoice_number"] == live_number
    error(ada.get(f"{API}/payments/{test_payment.reference}"), 404, "not_found")
    assert (
        ada.get(f"{API}/payments/{live_payment.reference}").json()["invoice_number"] == live_number
    )
    # nothing on the person's side of a test-era payment can be started or cancelled
    error(ada.post(f"{API}/payments/{test_payment.reference}/cancel", {}), 404, "not_found")


def test_a_pending_test_era_checkout_is_not_the_waiting_payment(
    engine, session, providers, cutover_time
):
    make_user(session, "ada@example.test")
    web = api_client(
        engine, billing_live=True, billing_live_since=cutover_time.isoformat()
    ).__enter__()
    ada = Buyer(web, "ada@example.test")
    assert ada.put(f"{API}/profile/country", {"billing_country": "US"}).status_code == 200
    out = ada.post(f"{API}/checkout", {"kind": "plan", "code": "p20"})
    payment = session.scalars(
        select(Payment).where(Payment.reference == out.json()["payment"]["reference"])
    ).one()
    assert (
        ada.get(f"{API}/subscription").json()["pending_payment"]["reference"] == payment.reference
    )
    backdate(session, payment, cutover_time - timedelta(days=1))
    assert ada.get(f"{API}/subscription").json()["pending_payment"] is None


def test_without_billing_live_the_setting_changes_nothing(engine, session, providers, cutover_time):
    web, ada, root, test_payment, live_payment = mixed(
        engine, session, providers, cutover_time, billing_live_since=cutover_time.isoformat()
    )  # `billing_live` is false: the setting is ignored

    assert len(ada.get(f"{API}/invoices").json()["invoices"]) == 2
    assert len(ada.get(f"{API}/payments").json()["payments"]) == 2
    assert ada.get(f"{API}/payments/{test_payment.reference}").status_code == 200


def test_without_the_setting_everything_is_listed(engine, session, providers, cutover_time):
    web, ada, root, test_payment, live_payment = mixed(engine, session, providers, cutover_time)

    assert len(ada.get(f"{API}/invoices").json()["invoices"]) == 2
    assert len(ada.get(f"{API}/payments").json()["payments"]) == 2


def test_the_admin_still_sees_a_test_era_payment_but_cannot_refund_it(
    engine, session, providers, cutover_time
):
    web, ada, root, test_payment, live_payment = mixed(
        engine,
        session,
        providers,
        cutover_time,
        billing_live=True,
        billing_live_since=cutover_time.isoformat(),
    )

    listed = {p["reference"]: p for p in root.get(f"{ADMIN_API}/payments").json()["payments"]}

    assert set(listed) == {test_payment.reference, live_payment.reference}  # the admin sees all
    assert listed[test_payment.reference]["invoice_number"] == shown(session, test_payment)
    option = listed[test_payment.reference]["refund_option"]
    assert option["allowed"] is False and "before billing went live" in option["message"]
    assert listed[live_payment.reference]["refund_option"]["allowed"] is True
    # and a refund asked for anyway is refused before the provider is ever called
    out = root.post(f"{ADMIN_API}/payments/{test_payment.id}/refund", {"reason": "first_plan_7d"})
    error(out, 409, "before_go_live")
    assert providers.usd.refund_calls == []


# ---- the first-payment refund rule ----

FIRST_ERA = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)  # a test plan bought with test keys
FIRST_LIVE = datetime(2026, 11, 12, 9, 0, tzinfo=UTC)  # the first live plan, after the closeout


def test_a_persons_first_live_plan_payment_is_the_first_after_the_cutover(
    session, engine, providers
):
    ada = make_user(session, "ada@example.test")
    admin = make_user(session, "root@example.test", admin=True)
    buy_plan(session, ada, providers.usd, FIRST_ERA, token="tok-test")
    test_payment = session.scalars(select(Payment).order_by(Payment.id)).first()
    session.execute(text("update subscriptions set created_at = :t"), {"t": FIRST_ERA})
    session.execute(text("update payment_methods set created_at = :t"), {"t": FIRST_ERA})
    session.commit()
    # the closeout ends the test plan so she can buy again, just before the flip
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    code = closeout.run(
        factory, settings(), T_TEXT, apply=True, now=T + timedelta(minutes=30), out=lambda _: None
    )
    assert code == 0
    buy_plan(session, ada, providers.usd, FIRST_LIVE, token="tok-live")
    live_payment = session.scalars(select(Payment).order_by(Payment.id.desc())).first()
    assert live_payment.id != test_payment.id
    live_cfg = settings(billing_live=True, billing_live_since=T_TEXT)

    # without the cutover the test-era payment is "the first" and the live one is not
    with pytest.raises(refunds.Refused) as exc:
        refunds.request(
            session, live_payment.id, "first_plan_7d", admin, None, settings(), FIRST_LIVE
        )
    session.rollback()
    assert exc.value.code == "not_first_payment"
    # with it, the live payment is her first live plan payment and can be refunded; the test-era
    # one never can
    with pytest.raises(refunds.Refused) as exc:
        refunds.request(
            session, test_payment.id, "first_plan_7d", admin, None, live_cfg, FIRST_LIVE
        )
    session.rollback()
    assert exc.value.code == "before_go_live"
    refund = refunds.request(
        session,
        live_payment.id,
        "first_plan_7d",
        admin,
        None,
        live_cfg,
        FIRST_LIVE + timedelta(hours=1),
    )
    assert refund.status == "submitted"
    assert providers.usd.refunds == [(live_payment.provider_tx_id, live_payment.amount_minor)]


# ---- the rest of the places a test payment must not count (Opus review of PR 255) ----


def test_a_test_era_payment_with_no_invoice_is_never_invoiced_after_go_live(session, providers):
    """`issue_missing` would give it a LIVE gap-free number and queue a receipt mail."""
    ada = make_user(session, "ada@example.test")
    buy_plan(session, ada, providers.usd, FIRST_ERA, token="tok-test")
    session.execute(text("delete from invoices"))
    session.commit()
    live_cfg = settings(billing_live=True, billing_live_since=T_TEXT)

    assert invoices.issue_missing(session, FIRST_LIVE, live_cfg) == 0
    assert session.scalars(select(Invoice)).all() == []
    # control: without the cutover the same sweep issues it
    assert invoices.issue_missing(session, FIRST_LIVE, settings()) == 1


def test_a_test_payment_does_not_lock_the_billing_country(session, providers):
    from app.billing import profile as profiles

    ada = make_user(session, "ada@example.test")
    buy_plan(session, ada, providers.usd, FIRST_ERA, token="tok-test")
    session.execute(text("update subscriptions set status = 'ended'"))  # the closeout's end state
    session.commit()
    live_cfg = settings(billing_live=True, billing_live_since=T_TEXT)

    assert profiles.country_lock(session, ada.id, FIRST_LIVE, live_cfg) is None  # free again
    assert profiles.country_change_allowed_at(session, ada.id, FIRST_LIVE, live_cfg) is None
    # control: without the cutover she is locked by the test payment
    assert profiles.country_lock(session, ada.id, FIRST_LIVE, settings()) is not None
    # and the country can be changed at once (the change takes effect, nothing scheduled)
    change = profiles.change_country(session, ada, "NG", now=FIRST_LIVE, settings=live_cfg)
    assert change.result != "scheduled"
    session.rollback()


def test_a_refund_of_a_test_payment_cannot_be_resolved_after_go_live(session, providers):
    """`made` would delete the person's LIVE saved card and mail them; `not_made` would give test
    hours back. The closeout refuses to run while one is open, and resolve refuses too."""
    from app.models import Refund

    ada = make_user(session, "ada@example.test")
    admin = make_user(session, "root@example.test", admin=True)
    buy_plan(session, ada, providers.usd, FIRST_ERA, token="tok-test")
    payment = session.scalars(select(Payment).order_by(Payment.id)).first()
    session.add(
        Refund(
            payment_id=payment.id,
            reference=f"bnrf-{payment.id}-bbbbbbbbbbbbbbbb",
            reason="first_plan_7d",
            status="unknown",
            amount_minor=payment.amount_minor,
            currency=payment.currency,
            created_at=FIRST_ERA + timedelta(hours=1),
        )
    )
    session.commit()
    refund = session.scalars(select(Refund)).one()
    live_cfg = settings(billing_live=True, billing_live_since=T_TEXT)

    for outcome in ("made", "not_made"):
        with pytest.raises(refunds.Refused) as exc:
            refunds.resolve(session, refund.id, outcome, admin, FIRST_LIVE, live_cfg)
        session.rollback()
        assert (exc.value.status, exc.value.code) == (409, "before_go_live")
    session.refresh(refund)
    assert refund.status == "unknown"
    # control: before the cutover is in force the same call resolves it
    done = refunds.resolve(session, refund.id, "not_made", admin, FIRST_LIVE, settings())
    assert done.status == "failed"
