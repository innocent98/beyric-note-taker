"""Paystack through the real webhook route and the real settlement rules (billing stage S6).

Real Postgres, the app's own route (`POST /api/billing/webhooks/paystack`), `settle.py`'s rules, and
the Paystack adapter; only Paystack itself is scripted (`httpx.MockTransport`, documented samples,
see `tests/fixtures/paystack/README.md`). Nothing here proves a real Paystack accepts these calls:
see the SOP's "Not verified live".

What these prove: a signed `charge.success` is paid only after the payment is re-read from Paystack
and matches what we asked for; a replay grants once; a wrong amount, currency or reference is
refused whatever the webhook claims; a float amount or a lower-case currency in Paystack's answer is
never trusted (503, the payment stays pending, the retry settles); a tampered body or a stale
timestamp stores nothing; an event we do not act on is answered 200 and stored nowhere.
"""

import hashlib
import hmac
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.billing import settle
from app.billing.payments import new_payment
from app.billing.paystack import PaystackProvider
from app.billing.provider import PROVIDER_FACTORIES
from app.db import get_session
from app.main import create_app
from app.models import BillingEvent, Payment
from tests.test_billing_paystack import KEY, WEBHOOK_SECRET, Paystack, ok, raw, sample
from tests.test_web import make_settings, make_user

URL = "/api/billing/webhooks/paystack"
ASKED = 150_000  # NGN 1,500.00 in kobo
TX_ID = 4099260516


@pytest.fixture
def grants(monkeypatch):
    calls: list[int] = []
    monkeypatch.setattr(settle, "GRANT_HANDLERS", [lambda db, payment: calls.append(payment.id)])
    return calls


@pytest.fixture
def payment(session):
    payer = make_user(session, "payer@example.test")
    row = new_payment(
        session, payer=payer, provider="paystack", kind="plan", currency="NGN", amount_minor=ASKED
    )
    session.commit()
    return row


def serve(monkeypatch, *answers, **provider_args) -> Paystack:
    """Registers a Paystack adapter whose API calls are answered by a script."""
    script = Paystack(*answers)
    provider = PaystackProvider(
        **{
            "secret_key": KEY,
            "webhook_secret": WEBHOOK_SECRET,
            "transport": script.transport,
            **provider_args,
        }
    )
    monkeypatch.setitem(PROVIDER_FACTORIES, "paystack", lambda settings: provider)
    return script


def client_for(engine, *, enabled=True):
    app = create_app(make_settings(billing_enabled=enabled))

    def session_override():
        with Session(engine, expire_on_commit=False) as s:
            yield s

    app.dependency_overrides[get_session] = session_override
    return TestClient(app, follow_redirects=False)


@pytest.fixture
def client(engine):
    with client_for(engine) as c:
        yield c


def event_body(payment, *, tx_id=TX_ID, indent=2, **data_over) -> bytes:
    """A `charge.success` body, as Paystack documents it, claiming this payment is paid. Indented
    on purpose: the signature is over exactly these bytes."""
    body = sample("charge_success_event.json")
    body["data"].update(
        id=tx_id, reference=payment.reference, amount=payment.amount_minor, currency="NGN"
    )
    body["data"].update(data_over)
    return json.dumps(body, indent=indent).encode()


def sign(body: bytes, secret: str = WEBHOOK_SECRET, *, t: int | None = None) -> str:
    stamp = int(time.time()) if t is None else t
    digest = hmac.new(secret.encode(), f"{stamp}.".encode() + body, hashlib.sha512).hexdigest()
    return f"t={stamp},v1=whk_test:{digest}"


def post(client, body: bytes, header: str | None = None):
    headers = {"Paystack-Signature": header or sign(body), "Content-Type": "application/json"}
    return client.post(URL, content=body, headers=headers)


def verified(payment, **over) -> tuple[int, dict]:
    """Paystack's answer when asked about the payment: the documented verify sample, made to be
    about this payment."""
    return ok(
        "verify_response.json",
        **{
            "id": TX_ID,
            "reference": payment.reference,
            "amount": payment.amount_minor,
            "currency": "NGN",
            **over,
        },
    )


def reload(session, payment) -> Payment:
    session.expire_all()
    return session.get(Payment, payment.id)


def events(session) -> list[BillingEvent]:
    session.expire_all()
    return list(session.scalars(select(BillingEvent).order_by(BillingEvent.id)))


# ---- a payment is paid only when Paystack, asked, says so ----


def test_a_signed_charge_success_is_paid_after_the_payment_is_re_read(
    client, monkeypatch, grants, session, payment
):
    script = serve(monkeypatch, verified(payment))

    response = post(client, event_body(payment))

    assert response.status_code == 200 and response.json() == {"status": "ok"}
    paid = reload(session, payment)
    assert (paid.status, paid.provider_tx_id, paid.card_country) == (
        "succeeded",
        str(TX_ID),
        "NG",
    )
    assert paid.country_mismatch is False and paid.paid_at is not None
    assert grants == [payment.id]
    [request] = script.requests  # the one call: asking Paystack for the transaction
    assert (request.method, request.url.path) == ("GET", f"/transaction/{TX_ID}")
    assert request.headers["authorization"] == f"Bearer {KEY}"
    [row] = events(session)
    assert (row.provider, row.dedupe_key, row.type) == (
        "paystack",
        f"charge.success:{TX_ID}",
        "charge_succeeded",
    )
    assert row.processed_at is not None and row.error is None


def test_the_stored_event_holds_no_card_token_or_card_signature(
    client, monkeypatch, grants, session, payment
):
    serve(monkeypatch, verified(payment))
    post(client, event_body(payment))
    [row] = events(session)
    authorization = row.payload["data"]["authorization"]
    assert authorization["authorization_code"] == "[removed]"
    assert "AUTH_f5rnfq9p" not in json.dumps(row.payload)


def test_the_same_event_twice_grants_once_and_asks_paystack_once(
    client, monkeypatch, grants, session, payment
):
    script = serve(monkeypatch, verified(payment))
    body = event_body(payment)

    first = post(client, body)
    second = post(client, body, sign(body))  # a retry is signed again with a new timestamp

    assert (first.status_code, second.status_code) == (200, 200)
    assert grants == [payment.id]
    assert len(script.requests) == 1  # the replay found a decided row
    assert len(events(session)) == 1 and reload(session, payment).status == "succeeded"


def test_a_card_from_another_country_is_noted_and_the_payment_still_succeeds(
    client, monkeypatch, grants, session, payment
):
    answer = verified(payment)
    answer[1]["data"]["authorization"]["country_code"] = "GB"
    serve(monkeypatch, answer)
    post(client, event_body(payment))
    paid = reload(session, payment)
    assert (paid.status, paid.card_country) == ("succeeded", "GB")
    assert grants == [payment.id]


@pytest.mark.parametrize(
    "over, reason",
    [
        ({"amount": ASKED - 1}, "amount too low"),
        ({"amount": 100}, "amount too low"),
        ({"currency": "USD"}, "currency mismatch"),
        ({"reference": "someone-elses-reference"}, "reference mismatch"),
        ({"status": "failed"}, "not successful"),
        ({"status": "reversed"}, "not successful"),
    ],
)
def test_a_wrong_amount_currency_or_reference_in_paystacks_answer_is_refused(
    client, monkeypatch, grants, session, payment, over, reason
):
    """The webhook always claims the right values; only Paystack's own answer decides."""
    serve(monkeypatch, verified(payment, **over))

    response = post(client, event_body(payment))

    assert response.status_code == 200  # a final answer: Paystack is not asked to resend
    assert reload(session, payment).status == "pending" and grants == []
    [row] = events(session)
    assert row.processed_at is not None and reason in row.error


def test_a_higher_amount_is_accepted(client, monkeypatch, grants, session, payment):
    """Paystack can add its fee to what the payer pays (the documented sample shows 40333 for a
    requested 30050): at least what we asked for is enough."""
    serve(monkeypatch, verified(payment, amount=ASKED + 4_300))
    post(client, event_body(payment))
    assert reload(session, payment).status == "succeeded" and grants == [payment.id]


def test_a_webhook_for_a_payment_we_never_made_is_refused(client, monkeypatch, grants, session):
    script = serve(monkeypatch, ok("verify_response.json"))
    ghost = type("Ghost", (), {"reference": "bn-0-ghost", "amount_minor": ASKED})()
    response = post(client, event_body(ghost))
    assert response.status_code == 200 and grants == []
    [row] = events(session)
    assert "unknown reference" in row.error
    assert script.requests == []  # nothing to ask about


# ---- an answer we cannot trust is never settled on, and never lost ----


@pytest.mark.parametrize(
    "over",
    [{"amount": float(ASKED)}, {"amount": str(ASKED)}, {"currency": "ngn"}, {"id": float(TX_ID)}],
)
def test_a_malformed_answer_leaves_the_payment_pending_and_the_retry_settles(
    client, monkeypatch, grants, session, payment, over
):
    """503, not a final refusal: the event stays waiting, so Paystack's resend (or ours) can settle
    the payment once the answer is well formed. A float amount is never rounded to fit."""
    script = serve(monkeypatch, verified(payment, **over), verified(payment))
    body = event_body(payment)

    first = post(client, body)

    assert first.status_code == 503
    assert reload(session, payment).status == "pending" and grants == []
    [row] = events(session)
    assert row.processed_at is None and "malformed Paystack answer" in row.error

    second = post(client, body)  # Paystack's resend, answered well this time

    assert second.status_code == 200 and len(script.requests) == 2
    assert reload(session, payment).status == "succeeded" and grants == [payment.id]
    assert len(events(session)) == 1


def test_a_payment_paystack_says_is_still_ongoing_is_tried_again_later(
    client, monkeypatch, grants, session, payment
):
    serve(monkeypatch, verified(payment, status="ongoing"), verified(payment))
    body = event_body(payment)

    assert post(client, body).status_code == 503
    assert reload(session, payment).status == "pending"
    assert post(client, body).status_code == 200
    assert reload(session, payment).status == "succeeded" and grants == [payment.id]


@pytest.mark.parametrize(
    "answer",
    [
        (500, {"status": False, "message": "boom"}),
        (401, {"status": False, "message": "Invalid key"}),
        (200, b"not json"),
        httpx.ReadTimeout("slow"),
        httpx.ConnectError("down"),
    ],
)
def test_paystack_being_down_or_refusing_us_is_a_503_never_a_silent_success(
    client, monkeypatch, grants, session, payment, answer
):
    serve(monkeypatch, answer)

    response = post(client, event_body(payment))

    assert response.status_code == 503
    assert reload(session, payment).status == "pending" and grants == []
    [row] = events(session)
    assert row.processed_at is None and row.error


# ---- the signature is the credential ----


def test_a_tampered_body_stores_nothing_and_asks_nobody(
    client, monkeypatch, grants, session, payment
):
    script = serve(monkeypatch, verified(payment))
    body = event_body(payment)
    header = sign(body)

    response = post(client, body.replace(b"150000", b"1"), header)

    assert response.status_code == 401
    assert events(session) == [] and script.requests == [] and grants == []
    assert reload(session, payment).status == "pending"


def test_a_stale_timestamp_stores_nothing(client, monkeypatch, grants, session, payment):
    script = serve(monkeypatch, verified(payment))
    body = event_body(payment)

    response = post(client, body, sign(body, t=int(time.time()) - 400))

    assert response.status_code == 401
    assert events(session) == [] and script.requests == []


def test_a_signature_from_the_wrong_secret_or_no_header_stores_nothing(
    client, monkeypatch, session, payment
):
    serve(monkeypatch, verified(payment))
    body = event_body(payment)
    assert post(client, body, sign(body, "whsec_someone_elses")).status_code == 401
    assert client.post(URL, content=body).status_code == 401
    assert events(session) == []


def test_two_signatures_during_a_rotation_are_accepted(
    client, monkeypatch, grants, session, payment
):
    serve(monkeypatch, verified(payment))
    body = event_body(payment)
    t = int(time.time())
    digest_old = hmac.new(
        b"whsec_the_old_secret_not_a_key", f"{t}.".encode() + body, hashlib.sha512
    ).hexdigest()
    digest_new = hmac.new(
        WEBHOOK_SECRET.encode(), f"{t}.".encode() + body, hashlib.sha512
    ).hexdigest()
    header = f"t={t},v1=whk_old:{digest_old},v1=whk_new:{digest_new}"
    assert post(client, body, header).status_code == 200
    assert grants == [payment.id]


def test_the_legacy_header_works_until_a_webhook_secret_is_set(
    client, monkeypatch, grants, session, payment
):
    serve(monkeypatch, verified(payment), webhook_secret="")
    body = event_body(payment)
    legacy = hmac.new(KEY.encode(), body, hashlib.sha512).hexdigest()

    response = client.post(URL, content=body, headers={"x-paystack-signature": legacy})

    assert response.status_code == 200 and grants == [payment.id]


def test_with_no_keys_set_every_webhook_is_refused(engine, session, payment):
    """The real factory, default settings: billing on, but nothing configured."""
    body = event_body(payment)
    with client_for(engine) as c:
        assert post(c, body).status_code == 401
    assert events(session) == []


def test_with_billing_off_the_route_is_a_404_that_asks_nobody(
    engine, monkeypatch, session, payment
):
    script = serve(monkeypatch, verified(payment))
    body = event_body(payment)
    with client_for(engine, enabled=False) as c:
        assert post(c, body).status_code == 404
    assert events(session) == [] and script.requests == []


# ---- events we do not act on ----


@pytest.mark.parametrize(
    "name", ["ping", "invoice.create", "transfer.success", "subscription.create"]
)
def test_an_event_we_do_not_act_on_is_answered_200_and_stored_nowhere(
    client, monkeypatch, session, payment, name
):
    script = serve(monkeypatch, verified(payment))
    body = json.dumps({"event": name, "data": {"id": 1}}).encode()

    response = post(client, body)

    assert response.status_code == 200 and response.json() == {"status": "ignored"}
    assert events(session) == [] and script.requests == []


def test_an_unsigned_event_we_do_not_act_on_is_still_a_401(client, monkeypatch, session, payment):
    serve(monkeypatch, verified(payment))
    body = json.dumps({"event": "ping", "data": {}}).encode()
    assert client.post(URL, content=body).status_code == 401


def test_a_refund_event_is_stored_and_left_for_the_refund_stage(
    client, monkeypatch, grants, session, payment
):
    script = serve(monkeypatch, verified(payment))
    body = raw("refund_processed_event.json")

    response = post(client, body)

    assert response.status_code == 200
    [row] = events(session)
    assert (row.type, row.dedupe_key) == ("refund", "refund.processed:132013318360")
    assert row.processed_at is None and "waiting for the refund stage" in row.error
    assert script.requests == [] and reload(session, payment).status == "pending"


def test_a_malformed_charge_success_is_a_400_and_stores_nothing(
    client, monkeypatch, session, payment
):
    serve(monkeypatch, verified(payment))
    body = event_body(payment, amount=ASKED + 0.5)
    assert post(client, body).status_code == 400
    assert events(session) == []


# ---- the runbook script (scripts/paystack_test_run.py), against the scripted Paystack ----


def test_the_test_run_script_makes_a_pending_payment_then_reports_the_settlement(
    client, monkeypatch, grants, session, payment
):
    from scripts import paystack_test_run as script_module

    # Paystack echoes the reference we send: answer with it.
    def echo(request: httpx.Request) -> httpx.Response:
        reference = json.loads(request.content)["reference"]
        return httpx.Response(
            200,
            json={
                "status": True,
                "message": "Authorization URL created",
                "data": {
                    "authorization_url": "https://checkout.paystack.com/abc123",
                    "access_code": "abc123",
                    "reference": reference,
                },
            },
        )

    provider = PaystackProvider(
        secret_key=KEY, webhook_secret=WEBHOOK_SECRET, transport=httpx.MockTransport(echo)
    )
    made, checkout = script_module.make_checkout(
        session, provider, "PAYER@example.test", 50, "https://notes.example.test/"
    )

    assert checkout.url == "https://checkout.paystack.com/abc123"
    assert (made.status, made.amount_minor, made.currency, made.provider) == (
        "pending",
        5000,
        "NGN",
        "paystack",
    )
    report = script_module.status_report(session, made.reference)
    assert report["payment"]["status"] == "pending" and report["events"] == []

    # The webhook then settles it, and the script reports that.
    serve(monkeypatch, verified(made, id=4099267777, amount=5000))
    body = event_body(made, tx_id=4099267777, amount=5000)
    assert post(client, body).status_code == 200
    session.expire_all()
    after = script_module.status_report(session, made.reference)
    assert after["payment"]["status"] == "succeeded"
    assert after["payment"]["provider_tx_id"] == "4099267777"
    assert [e["dedupe_key"] for e in after["events"]] == ["charge.success:4099267777"]
    assert KEY not in json.dumps(after)


@pytest.mark.parametrize("naira", [0, 49, -5, 50.5, "50"])
def test_the_test_run_script_refuses_an_amount_below_paystacks_minimum(session, payment, naira):
    from scripts import paystack_test_run as script_module

    provider = PaystackProvider(secret_key=KEY, webhook_secret=WEBHOOK_SECRET)
    with pytest.raises(SystemExit):
        script_module.make_checkout(session, provider, "payer@example.test", naira, "https://x/")


def test_the_test_run_script_stops_before_any_call_without_a_test_key(session, payment):
    from scripts import paystack_test_run as script_module

    provider = PaystackProvider(secret_key="", webhook_secret="")
    with pytest.raises(SystemExit, match="not configured"):
        script_module.make_checkout(session, provider, "payer@example.test", 50, "https://x/")
    live_key_in_test_mode = PaystackProvider(secret_key="sk_live_" + "x" * 20, live=False)
    with pytest.raises(SystemExit, match="mode key"):
        script_module.make_checkout(
            session, live_key_in_test_mode, "payer@example.test", 50, "https://x/"
        )


def test_a_refund_event_with_no_refund_reference_is_stored_not_lost(
    client, monkeypatch, session, payment
):
    serve(monkeypatch, verified(payment))
    body = sample("refund_processed_event.json")
    body["data"].update(refund_reference=None, amount=None, status="pending")
    raw_body = json.dumps(body, indent=2).encode()

    first = post(client, raw_body)
    second = post(client, raw_body)  # Paystack's resend: the same key, one row

    assert (first.status_code, second.status_code) == (200, 200)
    [row] = events(session)
    assert row.type == "refund" and "waiting for the refund stage" in row.error
    assert row.dedupe_key == "refund.processed:T2154954_412829_3be32076_6lcg3"


def test_a_dispute_event_with_no_refund_amount_is_stored_not_lost(
    client, monkeypatch, session, payment
):
    serve(monkeypatch, verified(payment))
    body = sample("dispute_create_event.json")
    body["data"]["refund_amount"] = None
    assert post(client, json.dumps(body).encode()).status_code == 200
    [row] = events(session)
    assert (row.type, row.dedupe_key) == ("dispute", "charge.dispute.create:358950")


def test_a_charge_that_is_not_ours_is_answered_200_and_stored_nowhere(
    client, monkeypatch, session, payment
):
    """Another product on the same Paystack account: a reference we could never have made."""
    script = serve(monkeypatch, verified(payment))
    assert post(client, event_body(payment, reference="x" * 80)).status_code == 200
    assert events(session) == [] and script.requests == []


def test_the_test_run_script_does_nothing_when_billing_is_live(monkeypatch, session, payment):
    from scripts import paystack_test_run as script_module

    live = make_settings(billing_live=True, paystack_secret_key="sk_live_" + "x" * 20)
    monkeypatch.setattr("app.config.get_settings", lambda: live)
    for command in (
        ["checkout", "payer@example.test", "--naira", "50"],
        ["status", "x"],
        ["verify", "x"],
    ):
        with pytest.raises(SystemExit, match="BILLING_LIVE is true"):
            script_module.main(command)
    session.expire_all()
    assert session.scalars(select(Payment)).all() == [payment]  # nothing was written


@pytest.mark.parametrize(
    "answer",
    [(500, {"status": False, "message": "boom"}), httpx.ConnectError("down"), (200, b"not json")],
)
def test_a_failed_checkout_leaves_no_pending_row_behind(session, payment, answer):
    from scripts import paystack_test_run as script_module

    provider = PaystackProvider(
        secret_key=KEY, webhook_secret=WEBHOOK_SECRET, transport=Paystack(answer).transport
    )
    with pytest.raises(SystemExit):
        script_module.make_checkout(session, provider, "payer@example.test", 50, "https://x/")
    session.expire_all()
    assert session.scalars(select(Payment)).all() == [payment]  # only the fixture's own row


def test_no_key_leaves_no_pending_row_either(session, payment):
    from scripts import paystack_test_run as script_module

    provider = PaystackProvider(secret_key="", webhook_secret="")
    with pytest.raises(SystemExit):
        script_module.make_checkout(session, provider, "payer@example.test", 50, "https://x/")
    session.expire_all()
    assert session.scalars(select(Payment)).all() == [payment]
