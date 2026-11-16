"""The webhook route, the inbox and the settlement rules (billing stage S4), all against
`FakeProvider`. Real Postgres through the app's own route: the dedupe key, the row lock and the
rollback are the point.

What these prove: a replay grants once; a wrong amount, currency or reference is refused whatever
the webhook claims; a bad signature is refused and stores nothing; the verifier gets the request's
exact bytes; with billing off the route is a 404 that writes nothing."""

import json
import threading
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.billing import settle
from app.billing.api import MAX_BODY_BYTES, _handle
from app.billing.fake import FakeProvider
from app.billing.payments import new_payment
from app.billing.provider import PROVIDER_FACTORIES
from app.billing.types import ProviderError, ProviderEvent, ProviderTransaction
from app.db import get_session
from app.main import create_app
from app.models import BillingEvent, Payment
from tests.test_web import make_settings, make_user

URL = "/api/billing/webhooks/fake"
ASKED = 1800_00  # 1,800.00 USD in minor units


@pytest.fixture
def fake(monkeypatch):
    provider = FakeProvider()
    monkeypatch.setitem(PROVIDER_FACTORIES, "fake", lambda settings: provider)
    return provider


@pytest.fixture
def grants(monkeypatch):
    """Stands in for the later stages' hour grants: records each payment it is called for."""
    calls: list[int] = []
    monkeypatch.setattr(settle, "GRANT_HANDLERS", [lambda db, payment: calls.append(payment.id)])
    return calls


def client_for(engine, *, enabled=True):
    app = create_app(make_settings(billing_enabled=enabled))

    def session_override():
        with Session(engine, expire_on_commit=False) as s:
            yield s

    app.dependency_overrides[get_session] = session_override
    return TestClient(app, follow_redirects=False)


@pytest.fixture
def client(engine, fake):
    with client_for(engine) as c:
        yield c


@pytest.fixture
def payment(session):
    payer = make_user(session, "payer@example.test")
    row = new_payment(
        session, payer=payer, provider="fake", kind="plan", currency="USD", amount_minor=ASKED
    )
    session.commit()
    return row


def tx_for(payment, **over) -> ProviderTransaction:
    values = dict(
        provider_tx_id="tx-1",
        reference=payment.reference,
        status="successful",
        amount_minor=payment.amount_minor,
        currency=payment.currency,
        card_country="NG",
    )
    return ProviderTransaction(**{**values, **over})


def event_for(payment, **over) -> ProviderEvent:
    """What the webhook CLAIMS: always the right amount, currency and reference, so a test that
    makes the provider's own report differ shows the decision does not come from the webhook."""
    values = dict(
        event_id="evt-1",
        kind="charge_succeeded",
        reference=payment.reference,
        provider_tx_id="tx-1",
        amount_minor=payment.amount_minor,
        currency=payment.currency,
    )
    return ProviderEvent(**{**values, **over})


def send(client, fake, event: ProviderEvent):
    body = fake.event_body(event)
    return client.post(URL, content=body, headers=fake.signed_headers(body))


def reload_payment(session, payment) -> Payment:
    session.expire_all()
    return session.get(Payment, payment.id)


def events(session) -> list[BillingEvent]:
    session.expire_all()
    return list(session.scalars(select(BillingEvent).order_by(BillingEvent.id)))


# ---- a payment is paid only when the provider says so ----


def test_a_verified_payment_is_marked_paid_and_grants_once(client, fake, grants, session, payment):
    fake.add_transaction(tx_for(payment))

    response = send(client, fake, event_for(payment))

    assert response.status_code == 200
    paid = reload_payment(session, payment)
    assert (paid.status, paid.provider_tx_id, paid.card_country) == ("succeeded", "tx-1", "NG")
    assert paid.paid_at is not None
    assert grants == [payment.id]
    assert fake.fetched == ["tx-1"]  # the payment was re-read from the provider
    [row] = events(session)
    assert (row.provider, row.dedupe_key, row.type) == ("fake", "evt-1", "charge_succeeded")
    assert row.signature_ok and row.processed_at is not None and row.error is None


def test_the_same_event_twice_grants_once(client, fake, grants, session, payment):
    fake.add_transaction(tx_for(payment))

    first = send(client, fake, event_for(payment))
    second = send(client, fake, event_for(payment))

    assert (first.status_code, second.status_code) == (200, 200)
    assert grants == [payment.id]
    assert len(events(session)) == 1
    assert fake.fetched == ["tx-1"]  # the replay did not even ask the provider again


def test_two_different_events_for_one_payment_grant_once(client, fake, grants, session, payment):
    fake.add_transaction(tx_for(payment))

    send(client, fake, event_for(payment, event_id="evt-1"))
    send(client, fake, event_for(payment, event_id="evt-2"))  # the provider's own second delivery

    assert grants == [payment.id]
    assert len(events(session)) == 2
    assert reload_payment(session, payment).status == "succeeded"


def test_a_larger_amount_than_asked_is_accepted(client, fake, grants, session, payment):
    fake.add_transaction(tx_for(payment, amount_minor=ASKED + 500))

    send(client, fake, event_for(payment))

    assert reload_payment(session, payment).status == "succeeded"
    assert grants == [payment.id]


@pytest.mark.parametrize(
    ("report", "reason"),
    [
        ({"amount_minor": ASKED - 1}, "amount too low"),
        ({"currency": "NGN"}, "currency mismatch"),
        ({"reference": "bn-someone-elses"}, "reference mismatch"),
        ({"status": "failed"}, "not successful"),
    ],
)
def test_a_report_that_does_not_match_what_we_asked_is_refused(
    client, fake, grants, session, payment, report, reason
):
    """The webhook claims the right numbers every time; the provider's own report decides."""
    fake.add_transaction(tx_for(payment, **report))

    response = send(client, fake, event_for(payment))

    assert response.status_code == 200
    assert reload_payment(session, payment).status == "pending"
    assert grants == []
    [row] = events(session)
    assert row.processed_at is not None and reason in row.error


def test_a_webhook_for_a_reference_we_never_made_is_refused(client, fake, grants, session):
    fake.add_transaction(ProviderTransaction("tx-9", "bn-forged", "successful", ASKED, "USD"))

    response = send(
        client,
        fake,
        ProviderEvent("evt-9", "charge_succeeded", "bn-forged", "tx-9", ASKED, "USD"),
    )

    assert response.status_code == 200
    assert grants == []
    assert fake.fetched == []  # nothing to compare against: the provider is not even asked
    assert "unknown reference" in events(session)[0].error


def test_a_payment_with_another_provider_is_refused(client, fake, grants, session, payment):
    payment.provider = "someone-else"
    session.commit()
    fake.add_transaction(tx_for(payment))

    send(client, fake, event_for(payment))

    assert grants == []
    assert reload_payment(session, payment).status == "pending"
    assert "wrong provider" in events(session)[0].error


def test_one_provider_transaction_cannot_pay_a_second_payment(
    client, fake, grants, session, payment
):
    """An event that names another payment's reference but the first payment's transaction id: the
    provider's report for that transaction carries the first payment's reference: refused."""
    other = new_payment(
        session,
        payer=make_user(session, "other@example.test"),
        provider="fake",
        kind="plan",
        currency="USD",
        amount_minor=ASKED,
    )
    session.commit()
    fake.add_transaction(tx_for(payment))
    send(client, fake, event_for(payment))

    send(client, fake, event_for(other, event_id="evt-2", provider_tx_id="tx-1"))

    assert grants == [payment.id]
    assert reload_payment(session, other).status == "pending"
    assert "reference mismatch" in events(session)[1].error


# ---- retries: stored but not decided ----


def test_a_provider_that_cannot_be_reached_leaves_the_event_waiting_then_a_retry_settles(
    client, fake, grants, session, payment
):
    fake.fetch_error = ProviderError("timeout")
    first = send(client, fake, event_for(payment))
    assert first.status_code == 503
    assert grants == [] and reload_payment(session, payment).status == "pending"
    [row] = events(session)
    assert row.processed_at is None and "timeout" in row.error

    fake.fetch_error = None
    fake.add_transaction(tx_for(payment))
    second = send(client, fake, event_for(payment))  # the provider's own retry, same event id

    assert second.status_code == 200
    assert grants == [payment.id]
    assert len(events(session)) == 1 and events(session)[0].processed_at is not None


def test_a_payment_the_provider_still_calls_pending_is_retried_not_refused(
    client, fake, grants, session, payment
):
    fake.add_transaction(tx_for(payment, status="pending"))

    assert send(client, fake, event_for(payment)).status_code == 503
    assert events(session)[0].processed_at is None

    fake.add_transaction(tx_for(payment))
    assert send(client, fake, event_for(payment)).status_code == 200
    assert grants == [payment.id]


def test_a_grant_handler_that_fails_undoes_the_payment_and_is_retried(
    client, fake, monkeypatch, session, payment
):
    fake.add_transaction(tx_for(payment))
    calls = []

    def broken(db, paid):
        calls.append(paid.id)
        raise RuntimeError("grant failed")

    monkeypatch.setattr(settle, "GRANT_HANDLERS", [broken])

    assert send(client, fake, event_for(payment)).status_code == 503
    assert reload_payment(session, payment).status == "pending"  # rolled back, not half paid
    assert events(session)[0].processed_at is None

    monkeypatch.setattr(settle, "GRANT_HANDLERS", [lambda db, paid: calls.append(paid.id)])
    assert send(client, fake, event_for(payment)).status_code == 200
    assert reload_payment(session, payment).status == "succeeded"
    assert calls == [payment.id, payment.id]  # the failed try, then the one that stuck


# ---- failed charges, refunds ----


def test_a_failed_charge_is_recorded_only_when_the_provider_agrees(
    client, fake, grants, session, payment
):
    fake.add_transaction(tx_for(payment, status="successful"))
    send(client, fake, event_for(payment, kind="charge_failed", event_id="evt-f1"))
    assert reload_payment(session, payment).status == "pending"  # the provider says it paid
    assert "does not report" in events(session)[0].error

    fake.add_transaction(tx_for(payment, status="failed"))
    send(client, fake, event_for(payment, kind="charge_failed", event_id="evt-f2"))
    assert reload_payment(session, payment).status == "failed"
    assert grants == []


def test_a_failed_payment_can_still_be_paid_later_and_grants_once(
    client, fake, grants, session, payment
):
    fake.add_transaction(tx_for(payment, status="failed"))
    send(client, fake, event_for(payment, kind="charge_failed", event_id="evt-f"))
    assert reload_payment(session, payment).status == "failed"

    fake.add_transaction(tx_for(payment))
    send(client, fake, event_for(payment, event_id="evt-ok"))
    send(client, fake, event_for(payment, event_id="evt-ok-again"))

    assert reload_payment(session, payment).status == "succeeded"
    assert grants == [payment.id]


@pytest.mark.parametrize("kind", ["refund", "dispute"])
def test_refund_and_dispute_events_are_stored_and_change_nothing_yet(
    client, fake, grants, session, payment, kind
):
    fake.add_transaction(tx_for(payment))
    send(client, fake, event_for(payment))

    response = send(client, fake, event_for(payment, kind=kind, event_id=f"evt-{kind}"))

    assert response.status_code == 200
    assert reload_payment(session, payment).status == "succeeded"
    assert grants == [payment.id]
    row = events(session)[1]
    assert row.type == kind and row.processed_at is None and "S9" in row.error


# ---- the signature, the bytes, the body ----


@pytest.mark.parametrize("headers", [{}, {"X-Fake-Signature": "00"}])
def test_a_bad_or_missing_signature_is_refused_and_nothing_is_stored(
    client, fake, grants, session, payment, headers
):
    fake.add_transaction(tx_for(payment))
    body = fake.event_body(event_for(payment))

    response = client.post(URL, content=body, headers=headers)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "not_authenticated"
    assert events(session) == []
    assert fake.fetched == [] and grants == []
    assert reload_payment(session, payment).status == "pending"


def test_a_signature_for_a_different_body_is_refused(client, fake, grants, session, payment):
    signed = fake.event_body(event_for(payment, amount_minor=1))
    tampered = fake.event_body(event_for(payment))

    response = client.post(URL, content=tampered, headers=fake.signed_headers(signed))

    assert response.status_code == 401 and events(session) == [] and grants == []


def test_the_verifier_gets_the_request_bytes_unchanged(client, fake, session, payment):
    """Spacing, key order, a trailing newline and non-ASCII text: signed as they arrived, so any
    re-encoding before the check would fail the signature."""
    fake.add_transaction(tx_for(payment))
    claim = json.loads(fake.event_body(event_for(payment)))
    claim["payer_email"] = "josé@example.test"
    body = (
        json.dumps(dict(reversed(list(claim.items()))), indent=3, ensure_ascii=False) + "\n\n"
    ).encode()

    response = client.post(URL, content=body, headers=fake.signed_headers(body))

    assert response.status_code == 200
    assert fake.verified_bodies == [body]


def test_a_genuine_request_with_an_unreadable_body_stores_nothing(client, fake, session):
    body = b"not json at all"

    response = client.post(URL, content=body, headers=fake.signed_headers(body))

    assert response.status_code == 400 and events(session) == []


def test_a_body_over_the_limit_is_refused_before_it_is_checked(client, fake, session):
    body = b"x" * (MAX_BODY_BYTES + 1)

    response = client.post(URL, content=body, headers=fake.signed_headers(body))

    assert response.status_code == 413
    assert fake.verified_bodies == [] and events(session) == []


def test_tokens_and_secrets_are_removed_from_the_stored_payload(client, fake, session, payment):
    fake.add_transaction(tx_for(payment))
    claim = json.loads(fake.event_body(event_for(payment, card_token="tok-reusable-123")))
    claim["data"] = {"authorization": {"authorization_code": "AUTH_x", "last4": "4081"}}
    body = json.dumps(claim).encode()

    client.post(URL, content=body, headers=fake.signed_headers(body))

    [row] = events(session)
    assert "tok-reusable-123" not in json.dumps(row.payload) and "AUTH_x" not in json.dumps(
        row.payload
    )
    assert row.payload["data"]["authorization"]["last4"] == "4081"  # only secrets go


# ---- billing off, unknown provider ----


def test_with_billing_off_the_route_is_a_404_and_writes_nothing(engine, fake, session, payment):
    fake.add_transaction(tx_for(payment))
    body = fake.event_body(event_for(payment))

    with client_for(engine, enabled=False) as off:
        response = off.post(URL, content=body, headers=fake.signed_headers(body))

    assert response.status_code == 404
    assert events(session) == [] and fake.verified_bodies == [] and fake.fetched == []
    assert reload_payment(session, payment).status == "pending"


def test_billing_is_off_by_default():
    assert make_settings().billing_enabled is False


def test_an_unregistered_provider_is_a_404(client, fake, session):
    body = b"{}"

    response = client.post(
        "/api/billing/webhooks/nobody", content=body, headers=fake.signed_headers(body)
    )

    assert response.status_code == 404 and events(session) == []


def test_the_fake_is_not_registered_unless_a_test_registers_it():
    assert "fake" not in PROVIDER_FACTORIES


def test_the_route_needs_no_login_and_no_csrf_token(client, fake, grants, session, payment):
    """No session cookie, no X-CSRF-Token: the signature is the only credential."""
    fake.add_transaction(tx_for(payment))
    assert "Cookie" not in client.headers
    assert send(client, fake, event_for(payment)).status_code == 200


def test_only_the_webhook_path_is_open(client):
    assert client.post("/api/billing/other", json={}).status_code in (401, 404)
    assert client.get(URL).status_code == 405


def test_the_login_and_csrf_exemption_covers_only_the_webhook_route(engine):
    """`auth.guard` skips login and CSRF for the webhook prefix. Whatever else is ever mounted
    under it would skip them too, so this fails if anything but the one route is."""
    from app import auth
    from tests.test_api import api_routes

    app = create_app(make_settings())

    under_prefix = [(m, p) for m, p in api_routes(app) if p.startswith(auth.WEBHOOK_PREFIX)]

    assert under_prefix == [("POST", "/api/billing/webhooks/{provider}")]
    assert auth.WEBHOOK_PREFIX == "/api/billing/webhooks/"  # with the slash: no wider match


# ---- a malformed answer is a recorded refusal, never an exception ----


@pytest.mark.parametrize(
    "bad",
    [
        {"amount_minor": "180000"},
        {"amount_minor": 1800.0},
        {"amount_minor": True},
        {"currency": "XXX"},
        {"status": "weird"},
        {"reference": None},
        {"provider_tx_id": ""},
        {"provider_tx_id": "t" * 200},
    ],
)
def test_a_malformed_provider_report_is_refused_and_recorded_not_retried(
    client, fake, grants, session, payment, bad
):
    fake.transactions["tx-1"] = tx_for(payment, **bad)

    response = send(client, fake, event_for(payment))

    assert response.status_code == 200  # decided: the vendor is not asked to resend it
    assert reload_payment(session, payment).status == "pending" and grants == []
    [row] = events(session)
    assert row.processed_at is not None and "malformed provider report" in row.error


@pytest.mark.parametrize(
    "claim",
    [
        {"id": 123},
        {"id": ""},
        {"reference": None},
        {"tx_id": 7},
        {"kind": "nonsense"},
        {"id": "x" * 201},
    ],
)
def test_an_event_with_unusable_fields_is_a_400_and_stores_nothing(
    client, fake, session, payment, claim
):
    body = json.dumps({**json.loads(fake.event_body(event_for(payment))), **claim}).encode()

    response = client.post(URL, content=body, headers=fake.signed_headers(body))

    assert response.status_code == 400 and events(session) == []


@pytest.mark.parametrize(
    ("reported", "stored"),
    [("NIGERIA NG", None), ("ng", "NG"), ("NG", "NG"), ("", None), ("NGA", None), (None, None)],
)
def test_the_card_country_is_two_letters_or_nothing_and_never_blocks_the_payment(
    client, fake, grants, session, payment, reported, stored
):
    fake.add_transaction(tx_for(payment, card_country=reported))

    response = send(client, fake, event_for(payment))

    assert response.status_code == 200 and grants == [payment.id]
    paid = reload_payment(session, payment)
    assert paid.status == "succeeded" and paid.card_country == stored


# ---- two deliveries at the same moment ----


@pytest.mark.parametrize("second_id", ["evt-1", "evt-2"], ids=["same-event", "two-events"])
def test_two_deliveries_at_the_same_moment_grant_once(
    engine, fake, grants, session, payment, second_id
):
    """Two threads, two sessions, released together; the provider's answer is slow so the second
    arrives while the first holds the payment row. Either the same event twice or two events for
    one payment: one grant."""
    fake.add_transaction(tx_for(payment))
    real_fetch = fake.fetch_transaction

    def slow_fetch(tx_id):
        time.sleep(0.3)
        return real_fetch(tx_id)

    fake.fetch_transaction = slow_fetch
    barrier, errors = threading.Barrier(2), []

    def deliver(event_id):
        try:
            body = fake.event_body(event_for(payment, event_id=event_id))
            with Session(engine, expire_on_commit=False) as db:
                barrier.wait(timeout=10)
                _handle(db, fake, fake.signed_headers(body), body)
        except BaseException as exc:  # noqa: BLE001 - re-raised below, in the test's thread
            errors.append(exc)

    threads = [threading.Thread(target=deliver, args=(i,)) for i in ("evt-1", second_id)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert errors == []
    assert grants == [payment.id]
    assert reload_payment(session, payment).status == "succeeded"
    assert len(events(session)) == len({"evt-1", second_id})
    assert all(row.processed_at is not None for row in events(session))
