"""The Flutterwave adapter (billing stage S7), with no network and no keys.

**Every Flutterwave body in this file is a documented sample, NOT a capture** from a real account:
the shapes come from developer.flutterwave.com (webhooks, transaction-verification,
card-tokenization, refunds, the Checkout and Tokenized Charges API pages, read 2026-11-17), with
our own references and amounts put in. The keys and the secret hash below are made-up test
strings. Nothing here proves how the real service behaves; the test-mode runbook in the SOP does
that (not verified live).

Two layers: the adapter alone against `httpx.MockTransport` (what it sends, how it reads, how it
fails), and the real webhook route with this adapter registered, so the S4 settlement rules are
shown to hold for Flutterwave-shaped answers (exact cents, wrong amount, wrong currency, wrong
reference, a replay, a malformed answer).
"""

import json
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.billing import flutterwave, settle
from app.billing.flutterwave import (
    FlutterwaveProvider,
    OutcomeUnknown,
    _encode,
    _major_text,
    _minor,
)
from app.billing.payments import new_payment
from app.billing.provider import PROVIDER_FACTORIES, provider_named
from app.billing.routing import provider_for
from app.billing.types import BadEvent, BillingError, IgnoredEvent, ProviderError, WebhookRejected
from app.db import get_session
from app.main import create_app
from app.models import BillingEvent, Payment
from tests.test_web import make_settings, make_user

SECRET_KEY = "FAKE-NOT-A-KEY_TEST"  # not a real key (the _TEST marks it as a test key)
SECRET_HASH = "FAKE-NOT-A-HASH"  # not a real hash
HOOK_URL = "/api/billing/webhooks/flutterwave"
REF = "bn-7-FAKE-REFERENCE"


def settings(**over):
    values = dict(
        billing_enabled=True,
        billing_live=False,  # test mode: TEST keys only
        flutterwave_secret_key=SECRET_KEY,
        flutterwave_secret_hash=SECRET_HASH,
    )
    return make_settings(**{**values, **over})


class Wire:
    """A stand-in for Flutterwave's servers: records each request, answers from a script."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer

    def provider(self, **over) -> FlutterwaveProvider:
        return FlutterwaveProvider(settings(**over), transport=httpx.MockTransport(self))

    @property
    def sent(self):
        return [(r.method, r.url.path) for r in self.requests]

    def body(self, index=-1) -> dict:
        return json.loads(self.requests[index].content)


def reply(text: str, code=200) -> httpx.Response:
    return httpx.Response(code, content=text.encode(), headers={"content-type": "application/json"})


# ---- documented samples (shapes from the vendor's pages; values ours) ----


def verify_text(
    *,
    amount="18.00",
    currency="USD",
    status="successful",
    tx_ref=REF,
    tx_id=285959875,
    card='{"first_6digits":"553188","last_4digits":"2950","issuer":" CREDIT",'
    '"country":"NIGERIA NG","type":"MASTERCARD","token":"FAKE-NOT-A-TOKEN",'
    '"expiry":"09/32"}',
) -> str:
    """Transaction Verification page, "Success Response": `amount` stays a decimal number in the
    text, so no test ever passes through a float."""
    card_part = f',"card":{card}' if card else ""
    return (
        '{"status":"success","message":"Transaction fetched successfully","data":{'
        f'"id":{tx_id},"tx_ref":"{tx_ref}","flw_ref":"FLW-MOCK-1","device_fingerprint":"N/A",'
        f'"amount":{amount},"currency":"{currency}","charged_amount":{amount},"app_fee":1.4,'
        '"merchant_fee":0,"processor_response":"Approved","auth_model":"PIN","ip":"::1",'
        f'"narration":"CARD Transaction","status":"{status}","payment_type":"card",'
        '"created_at":"2020-07-06T19:17:04.000Z","account_id":17321,"amount_settled":17.3'
        f'{card_part},"customer":{{"id":1,"name":"Pat Payer","phone_number":null,'
        '"email":"payer@example.test","created_at":"2020-07-06T19:17:04.000Z"}}}'
    )


def hook_text(*, amount="18.00", currency="USD", status="successful", tx_ref=REF, tx_id=285959875):
    """Webhooks page, "Successful Payment": the card country is plain `NG`, not `NIGERIA NG`."""
    return (
        '{"event":"charge.completed","data":{'
        f'"id":{tx_id},"tx_ref":"{tx_ref}","flw_ref":"FLW-MOCK-1","amount":{amount},'
        f'"currency":"{currency}","charged_amount":{amount},"status":"{status}",'
        '"payment_type":"card","customer":{"id":1,"name":"Pat Payer",'
        '"email":"payer@example.test"},"card":{"first_6digits":"123456","last_4digits":"7889",'
        '"issuer":"VERVE","country":"NG","type":"VERVE","expiry":"02/23",'
        '"token":"FAKE-NOT-A-TOKEN"}}}'
    )


CHECKOUT_OK = '{"status":"success","message":"Hosted Link","data":{"link":"https://checkout.flutterwave.com/v3/hosted/pay/flwlnk-FAKE-LINK"}}'
CHARGE_OK = (
    '{"status":"success","message":"Charge successful","data":{"id":1163077,'
    f'"tx_ref":"{REF}","amount":18,"currency":"USD","status":"successful",'
    '"payment_type":"card","customer":{"email":"payer@example.test"},'
    '"card":{"country":"NG","token":"FAKE-NOT-A-TOKEN-2"}}}'
)
PAYER_LINK = "https://3ds.example.test/pay-FAKE-LINK"
# Card Tokenization page, the 3D Secure sample: `pending`, with the payer's link in
# `data.meta.authorization.redirect`.
CHARGE_3DS = CHARGE_OK.replace('"status":"successful"', '"status":"pending"')[:-2] + (
    f',"meta":{{"authorization":{{"mode":"redirect","redirect":"{PAYER_LINK}"}}}}}}}}'
)
REFUND_OK = (
    '{"status":"success","message":"Transaction refund initiated","data":{"id":8612,'
    '"tx_id":285959875,"amount_refunded":5,"status":"completed","destination":"payment_source"}}'
)
ERROR_400 = '{"status":"error","message":"Cardno is required","data":null}'


def payment_stub(*, amount_minor=1800, currency="USD", kind="plan", reference=REF):
    return SimpleNamespace(
        amount_minor=amount_minor, currency=currency, kind=kind, reference=reference
    )


PAYER = SimpleNamespace(email="payer@example.test", name="Pat Payer")


# ---------------------------------------------------------------- the verif-hash check


def test_the_right_hash_is_accepted():
    FlutterwaveProvider(settings()).verify_webhook({"verif-hash": SECRET_HASH}, b"{}")


@pytest.mark.parametrize(
    "headers",
    [
        {"verif-hash": "not-the-hash"},
        {"verif-hash": SECRET_HASH + "x"},
        {"verif-hash": SECRET_HASH[:-1]},
        {"verif-hash": ""},
        {},
        {"x-other": SECRET_HASH},
    ],
)
def test_a_wrong_or_missing_hash_is_rejected(headers):
    with pytest.raises(WebhookRejected):
        FlutterwaveProvider(settings()).verify_webhook(headers, b"{}")


def test_the_header_name_is_matched_whatever_its_case():
    FlutterwaveProvider(settings()).verify_webhook({"Verif-Hash": SECRET_HASH}, b"{}")


def test_no_configured_hash_rejects_everything_even_an_empty_header():
    """An empty setting must never match an empty or missing header."""
    provider = FlutterwaveProvider(settings(flutterwave_secret_hash=""))
    for headers in ({}, {"verif-hash": ""}, {"verif-hash": "anything"}):
        with pytest.raises(WebhookRejected):
            provider.verify_webhook(headers, b"{}")


def test_the_hash_is_compared_in_constant_time(monkeypatch):
    calls = []
    real = flutterwave.hmac.compare_digest

    def spy(a, b):
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr(flutterwave.hmac, "compare_digest", spy)
    FlutterwaveProvider(settings()).verify_webhook({"verif-hash": SECRET_HASH}, b"{}")
    assert calls == [(SECRET_HASH.encode(), SECRET_HASH.encode())]


def test_a_non_ascii_hash_is_rejected_not_an_error():
    with pytest.raises(WebhookRejected):
        FlutterwaveProvider(settings()).verify_webhook({"verif-hash": "häsh"}, b"{}")


# ---------------------------------------------------------------- money is exact


@pytest.mark.parametrize(
    ("cents", "text"),
    [(1800, "18"), (999, "9.99"), (1850, "18.5"), (5, "0.05"), (100, "1"), (1801, "18.01")],
)
def test_cents_become_the_exact_decimal_number_we_send(cents, text):
    assert _major_text(cents) == text


@pytest.mark.parametrize("bad", [0, -1, 18.0, "1800", None, True, 10**13])
def test_a_bad_amount_is_never_sent(bad):
    with pytest.raises(ProviderError):
        _major_text(bad)


@pytest.mark.parametrize(
    ("given", "cents"),
    [
        (Decimal("18.00"), 1800),
        (Decimal("9.99"), 999),
        (Decimal("0.10"), 10),
        (Decimal("0.3"), 30),  # a float 0.3 * 100 is 30.000000000000004
        (Decimal("1.15"), 115),  # a float 1.15 * 100 is 114.99999999999999
        (18, 1800),
        (Decimal("0"), 0),
    ],
)
def test_a_dollar_amount_is_read_as_exact_cents(given, cents):
    assert _minor(given) == cents and type(_minor(given)) is int


@pytest.mark.parametrize(
    "given",
    [
        Decimal("18.005"),  # a third decimal place: not whole cents
        Decimal("-1"),
        -18,  # a negative whole number of dollars must not pass for cents
        10**20,
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("1E+30"),
        18.0,  # a float is never an amount
        0.1,
        "18.00",
        True,
        None,
    ],
)
def test_anything_that_is_not_whole_cents_is_handed_on_unchanged_for_settle_to_refuse(given):
    result = _minor(given)
    assert result is given or result == given
    assert type(result) is not int


def test_the_amount_goes_out_as_the_exact_number_never_a_float():
    body = _encode({"tx_ref": REF, "currency": "USD"}, _major_text(999))
    assert b'"amount": 9.99' in body
    assert json.loads(body, parse_float=Decimal)["amount"] == Decimal("9.99")
    whole = _encode({"tx_ref": REF}, _major_text(1800))
    assert b'"amount": 18' in whole and b"18.0" not in whole


def test_a_field_that_looks_like_the_amount_marker_cannot_change_the_amount():
    body = _encode({"customer": {"name": "@@amount-0000@@"}}, "9.99")
    parsed = json.loads(body, parse_float=Decimal)
    assert parsed["amount"] == Decimal("9.99")
    assert parsed["customer"]["name"] == "@@amount-0000@@"


# ---------------------------------------------------------------- reading what Flutterwave says


def test_the_verify_sample_is_read_into_the_neutral_transaction():
    tx = flutterwave._transaction_from(flutterwave._loads(verify_text())["data"])
    assert (tx.provider_tx_id, tx.reference, tx.status) == ("285959875", REF, "successful")
    assert (tx.amount_minor, tx.currency) == (1800, "USD")
    assert tx.payer_email == "payer@example.test"
    assert tx.card_token == "FAKE-NOT-A-TOKEN"
    assert tx.card_country == "NG"  # from "NIGERIA NG"


@pytest.mark.parametrize(
    ("raw", "code"),
    [("NIGERIA NG", "NG"), ("NG", "NG"), ("UNITED STATES US", "US"), ("NIGERIA", None), ("", None)],
)
def test_the_card_country_is_the_trailing_two_letters(raw, code):
    card = json.dumps({"country": raw, "token": "FAKE-NOT-A-TOKEN-3"})
    tx = flutterwave._transaction_from(flutterwave._loads(verify_text(card=card))["data"])
    assert tx.card_country == code


def test_a_payment_with_no_card_carries_no_card_country_or_token():
    """A bank transfer or mobile money has no `card` object (the verify page says so)."""
    tx = flutterwave._transaction_from(flutterwave._loads(verify_text(card=""))["data"])
    assert (tx.card_country, tx.card_token) == (None, None)


def test_a_failed_and_a_pending_status_are_read_as_they_are():
    for status in ("failed", "pending"):
        data = flutterwave._loads(verify_text(status=status))["data"]
        assert flutterwave._transaction_from(data).status == status


def test_an_unknown_status_is_passed_on_so_settle_records_it_for_a_human_look():
    """Not a 503 loop: the word reaches `settle._malformed`, which refuses and records it."""
    data = flutterwave._loads(verify_text(status="new-thing"))["data"]
    tx = flutterwave._transaction_from(data)
    assert tx.status == "new-thing"
    assert "unknown status" in settle._malformed(tx)


@pytest.mark.parametrize("bad", ["", "null", "[]", "3", '{"status":"success"}', '{"data":{}}'])
def test_an_answer_without_a_transaction_is_an_error(bad):
    answer = flutterwave._loads(bad) if bad else None
    data = answer.get("data") if isinstance(answer, dict) else None
    with pytest.raises(ProviderError):
        flutterwave._transaction_from(data)


def test_a_lowercase_currency_and_a_three_decimal_amount_pass_through_to_be_refused():
    """The adapter never tidies a vendor answer: `usd` and 17.999 reach `settle._malformed`."""
    text = verify_text(currency="usd", amount="17.999")
    tx = flutterwave._transaction_from(flutterwave._loads(text)["data"])
    assert tx.currency == "usd" and type(tx.amount_minor) is not int
    assert settle._malformed(tx) is not None


def test_a_string_amount_is_malformed():
    text = verify_text(amount='"18.00"')
    tx = flutterwave._transaction_from(flutterwave._loads(text)["data"])
    assert settle._malformed(tx).startswith("malformed provider report")


# ---------------------------------------------------------------- the webhook body


def test_the_charge_sample_becomes_a_neutral_event():
    event = FlutterwaveProvider(settings()).parse_event(hook_text().encode())
    assert event.kind == "charge_succeeded"
    assert (event.reference, event.provider_tx_id) == (REF, "285959875")
    assert (event.amount_minor, event.currency) == (1800, "USD")
    assert event.payer_email == "payer@example.test"
    assert event.card_token == "FAKE-NOT-A-TOKEN"
    assert "FAKE-NOT-A-TOKEN" not in repr(event)  # the token never reaches a log line


def test_the_same_delivery_has_the_same_id_and_a_different_attempt_a_different_one():
    provider = FlutterwaveProvider(settings())
    first = provider.parse_event(hook_text().encode())
    again = provider.parse_event(hook_text().encode())
    failed = provider.parse_event(hook_text(status="failed").encode())
    assert first.event_id == again.event_id
    assert failed.event_id != first.event_id
    assert failed.kind == "charge_failed"


def test_a_webhook_cannot_make_its_own_claim_believed():
    """A status we do not know is passed on as a claim of success; settle checks the provider."""
    event = FlutterwaveProvider(settings()).parse_event(hook_text(status="weird").encode())
    assert event.kind == "charge_succeeded"


@pytest.mark.parametrize(
    "raw",
    [
        b"not json",
        b"[]",
        b"{}",
        b'{"status":"ok"}',  # a JSON object that is not an event at all
        b'{"event":"charge.completed"}',
        b'{"event":"charge.completed","data":{"id":1,"status":"successful"}}',  # no tx_ref
        b'{"event":"charge.completed","data":{"tx_ref":"x","status":"successful"}}',  # no id
        b'{"event":"charge.completed","data":{"id":true,"tx_ref":"x","status":"successful"}}',
        b'{"event":"charge.completed","data":{"id":1,"tx_ref":"x"}}',  # no status
        b'{"event":"charge.completed","data":{"id":NaN,"tx_ref":"x","status":"successful"}}',
    ],
)
def test_a_body_that_is_not_a_readable_charge_event_is_a_bad_event(raw):
    with pytest.raises(BadEvent):
        FlutterwaveProvider(settings()).parse_event(raw)


def test_a_genuine_event_of_another_kind_is_ignored_not_a_bad_event():
    for body in (
        b'{"event":"transfer.completed","data":{"id":1}}',
        b'{"event":"subscription.cancelled","data":{}}',
    ):
        with pytest.raises(IgnoredEvent):
            FlutterwaveProvider(settings()).parse_event(body)


def test_a_malformed_amount_in_the_claim_does_not_stop_the_event_being_stored():
    """The event's amount is never read; the provider's own report decides. So an odd claim is
    carried as zero, not turned into a 400 that would hide the payment."""
    event = FlutterwaveProvider(settings()).parse_event(hook_text(amount="17.999").encode())
    assert event.amount_minor == 0


def test_the_refund_sample_is_a_refund_event():
    """Refunds page sample: no `event` field and no `tx_ref`, only `TransactionId`."""
    body = (
        '{"id":89074,"AmountRefunded":50.00,"status":"completed","FlwRef":"flwm3s4m0c",'
        '"destination":"payment_source","TransactionId":8784082}'
    )
    event = FlutterwaveProvider(settings()).parse_event(body.encode())
    assert (event.kind, event.provider_tx_id, event.amount_minor) == ("refund", "8784082", 5000)
    assert event.event_id == "refund:89074:completed"


# ---------------------------------------------------------------- hosted checkout


def test_checkout_asks_for_a_hosted_link_in_dollars_with_our_reference():
    wire = Wire(reply(CHECKOUT_OK))
    checkout = wire.provider().start_checkout(payment_stub(), PAYER, "http://localhost:3000/back")

    assert checkout.url.startswith("https://checkout.flutterwave.com/")
    assert checkout.reference == REF
    assert wire.sent == [("POST", "/v3/payments")]
    request = wire.requests[0]
    assert request.headers["authorization"] == f"Bearer {SECRET_KEY}"
    assert request.headers["content-type"] == "application/json"
    body = wire.body()
    assert (body["tx_ref"], body["currency"], body["redirect_url"]) == (
        REF,
        "USD",
        "http://localhost:3000/back",
    )
    assert body["customer"] == {"email": "payer@example.test", "name": "Pat Payer"}
    assert body["amount"] == 18
    assert request.content.count(b'"amount": 18') == 1  # an integer, not 18.0


def test_checkout_sends_cents_as_the_exact_decimal():
    wire = Wire(reply(CHECKOUT_OK))
    wire.provider().start_checkout(payment_stub(amount_minor=999), PAYER, "https://x.test/back")
    assert b'"amount": 9.99' in wire.requests[0].content


def test_a_plan_checkout_offers_cards_only_and_a_pack_does_not():
    """Only a card can leave a reusable token (card-tokenization page)."""
    wire = Wire(reply(CHECKOUT_OK))
    provider = wire.provider()
    provider.start_checkout(payment_stub(kind="plan"), PAYER, "https://x.test/back")
    provider.start_checkout(payment_stub(kind="pack"), PAYER, "https://x.test/back")
    assert wire.body(0)["payment_options"] == "card"
    assert "payment_options" not in wire.body(1)


def test_checkout_without_a_payer_name_sends_the_email_only():
    wire = Wire(reply(CHECKOUT_OK))
    wire.provider().start_checkout(
        payment_stub(), SimpleNamespace(email="a@example.test", name=None), "https://x.test/b"
    )
    assert wire.body()["customer"] == {"email": "a@example.test"}


@pytest.mark.parametrize(
    "answer",
    [
        reply(ERROR_400, 400),
        reply('{"status":"error","message":"nope","data":null}', 200),
        reply('{"status":"success","data":{}}'),
        reply('{"status":"success","data":{"link":"http://insecure.test/pay"}}'),
        reply('{"status":"success","data":{"link":42}}'),
        reply("<html>gateway</html>"),
        reply("[]"),
        reply("", 200),
    ],
)
def test_a_checkout_that_gives_no_https_link_is_an_error(answer):
    with pytest.raises(ProviderError):
        Wire(answer).provider().start_checkout(payment_stub(), PAYER, "https://x.test/b")


@pytest.mark.parametrize(
    "payment",
    [payment_stub(currency="NGN"), payment_stub(amount_minor=0), payment_stub(amount_minor=-5)],
)
def test_a_checkout_for_a_payment_we_cannot_take_sends_nothing(payment):
    wire = Wire(reply(CHECKOUT_OK))
    with pytest.raises(ProviderError):
        wire.provider().start_checkout(payment, PAYER, "https://x.test/b")
    assert wire.requests == []


# ---------------------------------------------------------------- verify


def test_fetch_asks_for_the_transaction_by_its_id():
    wire = Wire(reply(verify_text()))
    tx = wire.provider().fetch_transaction("285959875")
    assert wire.sent == [("GET", "/v3/transactions/285959875/verify")]
    assert wire.requests[0].headers["authorization"] == f"Bearer {SECRET_KEY}"
    assert (tx.reference, tx.amount_minor, tx.card_country) == (REF, 1800, "NG")


@pytest.mark.parametrize(
    "bad", ["", "abc", "12/../34", "12?x=1", "1 2", "-5", "٣٣", "1" * 21, None, 12]
)
def test_an_id_that_is_not_a_transaction_id_never_reaches_the_wire(bad):
    wire = Wire(reply(verify_text()))
    with pytest.raises(ProviderError):
        wire.provider().fetch_transaction(bad)
    assert wire.requests == []


@pytest.mark.parametrize(
    "answer",
    [
        reply(
            '{"status":"error","message":"No transaction was found for this id","data":null}', 404
        ),
        reply(ERROR_400, 400),
        reply('{"message":"Authorization required"}', 401),
        reply('{"message":"Too many requests"}', 429),
        reply("oops", 500),
        reply("oops", 503),
        reply(verify_text(), 302),
        reply("not json"),
        reply('{"status":"error","message":"x"}'),
        httpx.ConnectTimeout("t"),
        httpx.ReadTimeout("t"),
        httpx.ConnectError("c"),
    ],
)
def test_every_failure_to_read_a_payment_is_an_explicit_error_never_a_success(answer):
    with pytest.raises(ProviderError):
        Wire(answer).provider().fetch_transaction("1")


def test_an_error_names_the_cause_but_never_the_key_or_the_url():
    for answer in (reply(ERROR_400, 400), reply("x", 500), httpx.ReadTimeout("slow")):
        with pytest.raises(ProviderError) as caught:
            Wire(answer).provider().fetch_transaction("1")
        text = str(caught.value)
        assert SECRET_KEY not in text and "api.flutterwave.com" not in text and "Bearer" not in text
    with pytest.raises(ProviderError, match="HTTP 401"):
        Wire(reply("{}", 401)).provider().fetch_transaction("1")
    with pytest.raises(ProviderError, match="ReadTimeout"):
        Wire(httpx.ReadTimeout("slow")).provider().fetch_transaction("1")


def test_a_read_that_times_out_is_not_an_unknown_outcome():
    """Reading moves no money, so it is a plain retryable error."""
    with pytest.raises(ProviderError) as caught:
        Wire(httpx.ReadTimeout("slow")).provider().fetch_transaction("1")
    assert not isinstance(caught.value, OutcomeUnknown)


# ---------------------------------------------------------------- saved card charge


def test_a_saved_card_is_charged_with_its_token_email_and_our_reference():
    wire = Wire(reply(CHARGE_OK))
    tx = wire.provider().charge_saved_method(
        "FAKE-NOT-A-TOKEN", 1800, "USD", REF, payer_email="payer@example.test"
    )
    assert wire.sent == [("POST", "/v3/tokenized-charges")]
    body = wire.body()
    assert body == {
        "token": "FAKE-NOT-A-TOKEN",
        "currency": "USD",
        "email": "payer@example.test",
        "tx_ref": REF,
        "amount": 18,
    }
    assert (tx.status, tx.amount_minor, tx.currency, tx.reference) == (
        "successful",
        1800,
        "USD",
        REF,
    )


def test_the_default_3ds_answer_is_pending_with_the_payers_own_link():
    """card-tokenization page: a charge is authenticated with 3DS unless NOAUTH is enabled. The
    charge stays open; the caller sends THIS link and opens no second checkout."""
    tx = (
        Wire(reply(CHARGE_3DS))
        .provider()
        .charge_saved_method("FAKE-NOT-A-TOKEN", 1800, "USD", REF, payer_email="payer@example.test")
    )
    assert tx.status == "pending"
    assert tx.payer_action_url == PAYER_LINK


@pytest.mark.parametrize(
    "meta",
    [
        "",
        ',"meta":{}',
        ',"meta":{"authorization":{"mode":"redirect"}}',
        ',"meta":{"authorization":{"mode":"redirect","redirect":"http://insecure.test/x"}}',
        ',"meta":{"authorization":{"mode":"redirect","redirect":42}}',
        ',"meta":"text"',
    ],
)
def test_no_https_payer_link_means_none(meta):
    answer = CHARGE_3DS.split(',"meta"')[0] + meta + "}}"
    tx = (
        Wire(reply(answer))
        .provider()
        .charge_saved_method("FAKE-NOT-A-TOKEN", 1800, "USD", REF, payer_email="payer@example.test")
    )
    assert tx.status == "pending" and tx.payer_action_url is None


def test_a_charge_answer_with_an_unknown_status_is_passed_on_never_called_successful():
    answer = CHARGE_OK.replace('"status":"successful"', '"status":"cancelled"')
    tx = (
        Wire(reply(answer))
        .provider()
        .charge_saved_method("FAKE-NOT-A-TOKEN", 1800, "USD", REF, payer_email="payer@example.test")
    )
    assert tx.status == "cancelled"


def test_a_declined_charge_is_a_failed_transaction_not_an_exception_and_not_a_success():
    declined = CHARGE_OK.replace('"status":"successful"', '"status":"failed"')
    tx = (
        Wire(reply(declined))
        .provider()
        .charge_saved_method("FAKE-NOT-A-TOKEN", 1800, "USD", REF, payer_email="payer@example.test")
    )
    assert tx.status == "failed"


@pytest.mark.parametrize("code", [400, 404, 422])
def test_a_charge_the_vendor_refuses_is_a_failed_result_with_its_reason_never_paid(code):
    """card-tokenization page: HTTP 400 `Wrong token or email passed`. Nothing was charged and a
    dead token keeps failing, so it is a result the caller can read, not a retryable exception."""
    body = '{"status":"error","message":"Wrong token or email passed","data":null}'
    tx = (
        Wire(reply(body, code))
        .provider()
        .charge_saved_method("FAKE-NOT-A-TOKEN", 1800, "USD", REF, payer_email="payer@example.test")
    )
    assert (tx.status, tx.reference, tx.amount_minor, tx.currency) == ("failed", REF, 1800, "USD")
    assert tx.failure_reason == "Wrong token or email passed"
    assert tx.provider_tx_id == f"refused-{REF}" and not tx.provider_tx_id.isdigit()
    assert tx.payer_action_url is None


@pytest.mark.parametrize(
    "answer",
    [reply("{}", 401), reply("{}", 403), reply("{}", 429), reply("x", 418), reply("not json")],
)
def test_a_charge_that_failed_for_another_reason_is_still_an_error(answer):
    """A bad key or a rate limit is not a declined card: it stays a retryable `ProviderError`."""
    with pytest.raises(ProviderError) as caught:
        Wire(answer).provider().charge_saved_method(
            "FAKE-NOT-A-TOKEN", 1800, "USD", REF, payer_email="payer@example.test"
        )
    assert not isinstance(caught.value, OutcomeUnknown)


def test_an_unknown_outcome_is_not_a_provider_error():
    """A caller that retries on `ProviderError` must never catch a lost refund or charge."""
    assert issubclass(OutcomeUnknown, BillingError)
    assert not issubclass(OutcomeUnknown, ProviderError)
    for lost in (httpx.ReadTimeout("slow"), reply("busy", 503)):
        with pytest.raises(OutcomeUnknown) as caught:
            Wire(lost).provider().refund("285959875", 500)
        assert not isinstance(caught.value, ProviderError)


def test_payer_email_cannot_be_passed_by_position():
    with pytest.raises(TypeError):
        Wire(reply(CHARGE_OK)).provider().charge_saved_method(
            "FAKE-NOT-A-TOKEN", 1800, "USD", REF, "payer@example.test"
        )


def test_a_failed_call_carries_no_context_that_holds_the_key():
    """The httpx exception holds the request, and the request holds the Authorization header."""
    with pytest.raises(ProviderError) as caught:
        Wire(httpx.ReadTimeout("slow")).provider().fetch_transaction("1")
    assert caught.value.__context__ is None and caught.value.__cause__ is None


def test_a_charge_sends_cents_as_the_exact_decimal():
    wire = Wire(reply(CHARGE_OK))
    wire.provider().charge_saved_method(
        "FAKE-NOT-A-TOKEN", 999, "USD", REF, payer_email="payer@example.test"
    )
    assert b'"amount": 9.99' in wire.requests[0].content


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(payer_email=None),  # the token is tied to the email: without it the call is refused
        dict(payer_email="  "),
        dict(token="short"),
        dict(token="flw t1nf made up token"),
        dict(token=None),
        dict(currency="NGN"),
        dict(amount_minor=0),
        dict(reference=""),
    ],
)
def test_a_charge_that_cannot_be_made_properly_sends_nothing(kwargs):
    values = dict(
        token="FAKE-NOT-A-TOKEN",
        amount_minor=1800,
        currency="USD",
        reference=REF,
        payer_email="payer@example.test",
    )
    wire = Wire(reply(CHARGE_OK))
    with pytest.raises(ProviderError):
        wire.provider().charge_saved_method(**{**values, **kwargs})
    assert wire.requests == []


@pytest.mark.parametrize("lost", [httpx.ReadTimeout("slow"), reply("busy", 503), reply("x", 500)])
def test_a_charge_that_times_out_is_looked_up_by_reference_before_anything_else(lost):
    wire = Wire(lost, reply(verify_text(tx_ref=REF)))
    tx = wire.provider().charge_saved_method(
        "FAKE-NOT-A-TOKEN", 1800, "USD", REF, payer_email="payer@example.test"
    )
    assert wire.sent == [
        ("POST", "/v3/tokenized-charges"),
        ("GET", "/v3/transactions/verify_by_reference"),
    ]
    assert wire.requests[1].url.params["tx_ref"] == REF
    assert tx.status == "successful"


def test_a_charge_that_times_out_and_cannot_be_found_is_an_unknown_outcome():
    wire = Wire(httpx.ReadTimeout("slow"), reply('{"status":"error","message":"nf"}', 404))
    with pytest.raises(OutcomeUnknown, match="may or may not"):
        wire.provider().charge_saved_method(
            "FAKE-NOT-A-TOKEN", 1800, "USD", REF, payer_email="payer@example.test"
        )


# ---------------------------------------------------------------- refund


def test_a_refund_names_the_transaction_and_the_exact_amount_and_returns_its_id():
    wire = Wire(reply(REFUND_OK))
    refund_id = wire.provider().refund("285959875", 500)
    assert refund_id == "8612"
    assert wire.sent == [("POST", "/v3/transactions/285959875/refund")]
    assert wire.requests[0].content == b'{"amount": 5}'


def test_our_refund_reference_travels_as_the_comments_not_as_an_idempotency_key():
    """Flutterwave v3 documents no idempotency key: `comments` is only for finding it later."""
    wire = Wire(reply(REFUND_OK))
    wire.provider().refund("285959875", 500, reference="refund-row-9")
    assert wire.body() == {"comments": "refund-row-9", "amount": 5}


@pytest.mark.parametrize("bad", ["", "x" * 101, 5])
def test_a_refund_reference_that_cannot_be_sent_sends_nothing(bad):
    wire = Wire(reply(REFUND_OK))
    with pytest.raises(ProviderError):
        wire.provider().refund("285959875", 500, reference=bad)
    assert wire.requests == []


def test_a_refund_of_cents_is_sent_exactly():
    wire = Wire(reply(REFUND_OK))
    wire.provider().refund("285959875", 999)
    assert wire.requests[0].content == b'{"amount": 9.99}'


@pytest.mark.parametrize(
    "answer",
    [
        reply('{"status":"error","message":"Amount should be above NGN100","data":null}', 400),
        reply('{"status":"success","message":"x","data":{}}'),
        reply('{"status":"success","message":"x","data":null}'),
        reply("nope"),
    ],
)
def test_a_refund_the_vendor_does_not_accept_is_an_error(answer):
    with pytest.raises(ProviderError) as caught:
        Wire(answer).provider().refund("285959875", 500)
    assert not isinstance(caught.value, OutcomeUnknown)


@pytest.mark.parametrize(
    "lost", [httpx.ReadTimeout("slow"), httpx.ConnectError("c"), reply("x", 503)]
)
def test_a_refund_that_gets_no_answer_is_an_unknown_outcome_never_a_second_try(lost):
    wire = Wire(lost)
    with pytest.raises(OutcomeUnknown):
        wire.provider().refund("285959875", 500)
    assert len(wire.requests) == 1  # no hidden retry: a second refund could pay twice


@pytest.mark.parametrize("bad", [("abc", 500), ("1/2", 500), ("12", 0), ("12", -1), ("12", 5.5)])
def test_a_refund_that_cannot_be_made_properly_sends_nothing(bad):
    wire = Wire(reply(REFUND_OK))
    with pytest.raises(ProviderError):
        wire.provider().refund(*bad)
    assert wire.requests == []


# ---------------------------------------------------------------- the flag-off guarantee


def calls_of(provider):
    return [
        lambda: provider.start_checkout(payment_stub(), PAYER, "https://x.test/b"),
        lambda: provider.fetch_transaction("285959875"),
        lambda: provider.fetch_by_reference(REF),
        lambda: provider.charge_saved_method(
            "FAKE-NOT-A-TOKEN", 1800, "USD", REF, payer_email="payer@example.test"
        ),
        lambda: provider.refund("285959875", 500),
    ]


@pytest.mark.parametrize(
    "over",
    [
        dict(),  # the default: no key
        dict(billing_live=True),
        dict(flutterwave_secret_key="   "),
    ],
)
def test_with_no_key_no_call_leaves_the_process(over, monkeypatch):
    """Not even a network object is made: building an `httpx.Client` is itself a failure here."""

    def boom(*args, **kwargs):
        raise AssertionError("a network client was built while billing is dark")

    monkeypatch.setattr(httpx, "Client", boom)
    wire = Wire(reply(CHECKOUT_OK))
    provider = FlutterwaveProvider(
        settings(**{"flutterwave_secret_key": "", "flutterwave_secret_hash": "", **over}),
        transport=httpx.MockTransport(wire),
    )
    for call in calls_of(provider):
        with pytest.raises(ProviderError, match="no call was made"):
            call()
    assert wire.requests == []


def test_the_defaults_are_dark_and_keyless():
    plain = make_settings()
    assert plain.billing_live is False
    assert plain.flutterwave_secret_key == plain.flutterwave_secret_hash == ""
    for call in calls_of(FlutterwaveProvider(plain)):
        with pytest.raises(ProviderError, match="no call was made"):
            call()


def test_the_keys_are_not_in_the_settings_repr():
    text = repr(settings())
    assert SECRET_KEY not in text and SECRET_HASH not in text


def test_webhook_checks_need_no_network(monkeypatch):
    monkeypatch.setattr(httpx, "Client", lambda *a, **k: pytest.fail("network"))
    provider = FlutterwaveProvider(settings())
    provider.verify_webhook({"verif-hash": SECRET_HASH}, b"{}")
    assert provider.parse_event(hook_text().encode()).reference == REF


# ---------------------------------------------------------------- registration and routing


def test_the_provider_is_registered_under_the_name_routing_uses():
    route = provider_for("US")
    assert (route.currency, route.provider) == ("USD", "flutterwave")
    adapter = provider_named(route.provider, make_settings())
    assert isinstance(adapter, FlutterwaveProvider)
    assert (adapter.name, adapter.currencies) == ("flutterwave", ("USD",))
    assert PROVIDER_FACTORIES["flutterwave"] is flutterwave.build


# ---------------------------------------------------------------- through the real webhook route


ASKED = 1800  # 18.00 USD


@pytest.fixture
def grants(monkeypatch):
    calls: list[int] = []
    monkeypatch.setattr(settle, "GRANT_HANDLERS", [lambda db, payment: calls.append(payment.id)])
    return calls


@pytest.fixture
def payment(session):
    payer = make_user(session, "payer@example.test")
    row = new_payment(
        session,
        payer=payer,
        provider="flutterwave",
        kind="plan",
        currency="USD",
        amount_minor=ASKED,
    )
    session.commit()
    return row


@pytest.fixture
def wire(monkeypatch):
    """Flutterwave's servers, scripted per test with `wire.answers`."""
    scripted = Wire(reply(verify_text()))
    monkeypatch.setitem(
        PROVIDER_FACTORIES,
        "flutterwave",
        lambda s: FlutterwaveProvider(s, transport=httpx.MockTransport(scripted)),
    )
    return scripted


def make_client(engine, **over):
    app = create_app(settings(**over))

    def session_override():
        with Session(engine, expire_on_commit=False) as s:
            yield s

    app.dependency_overrides[get_session] = session_override
    return TestClient(app, follow_redirects=False)


@pytest.fixture
def client(engine, wire):
    with make_client(engine) as c:
        yield c


def post_hook(client, payment, *, hash_value=SECRET_HASH, **fields):
    text = hook_text(tx_ref=payment.reference, **fields)
    headers = {"verif-hash": hash_value} if hash_value is not None else {}
    return client.post(HOOK_URL, content=text.encode(), headers=headers)


def verify_for(payment, **fields):
    return reply(verify_text(tx_ref=payment.reference, **fields))


def reload(session, payment) -> Payment:
    session.expire_all()
    return session.get(Payment, payment.id)


def stored_events(session) -> list[BillingEvent]:
    session.expire_all()
    return list(session.scalars(select(BillingEvent).order_by(BillingEvent.id)))


def test_a_flutterwave_payment_is_paid_only_after_the_provider_confirms_it(
    client, wire, grants, session, payment
):
    wire.answers = [verify_for(payment)]

    response = post_hook(client, payment)

    assert response.status_code == 200
    paid = reload(session, payment)
    assert (paid.status, paid.provider_tx_id, paid.card_country) == ("succeeded", "285959875", "NG")
    assert grants == [payment.id]
    assert wire.sent == [("GET", "/v3/transactions/285959875/verify")]
    [row] = stored_events(session)
    assert row.error is None and row.processed_at is not None
    assert row.dedupe_key == "charge.completed:285959875:successful"
    assert row.payload["data"]["card"]["token"] == "[removed]"  # the token is never stored readable
    assert "FAKE-NOT-A-TOKEN" not in json.dumps(row.payload)


def test_a_replay_of_the_same_webhook_grants_once_and_asks_the_provider_once(
    client, wire, grants, session, payment
):
    wire.answers = [verify_for(payment)]

    assert post_hook(client, payment).status_code == 200
    assert post_hook(client, payment).status_code == 200

    assert grants == [payment.id]
    assert len(stored_events(session)) == 1
    assert len(wire.requests) == 1


def test_a_webhook_with_a_wrong_or_missing_hash_is_refused_and_stores_nothing(
    client, wire, grants, session, payment
):
    for value in ("wrong", "", None):
        assert post_hook(client, payment, hash_value=value).status_code == 401
    assert stored_events(session) == []
    assert wire.requests == [] and grants == []
    assert reload(session, payment).status == "pending"


def test_with_no_secret_hash_configured_every_webhook_is_refused(engine, wire, session, payment):
    with make_client(engine, flutterwave_secret_hash="") as client:
        assert post_hook(client, payment, hash_value="").status_code == 401
        assert post_hook(client, payment, hash_value=None).status_code == 401
    assert stored_events(session) == [] and wire.requests == []


def test_a_webhook_cannot_raise_the_amount_or_change_the_currency(
    client, wire, grants, session, payment
):
    """The webhook claims 18.00 USD every time; Flutterwave's own answer is what counts."""
    wire.answers = [verify_for(payment, amount="17.99")]
    post_hook(client, payment, amount="18.00")
    assert reload(session, payment).status == "pending" and grants == []
    assert "amount too low" in stored_events(session)[-1].error


@pytest.mark.parametrize(
    ("fields", "reason"),
    [
        (dict(amount="17.99"), "amount too low"),
        (dict(amount="9.99"), "amount too low"),
        (dict(amount="0"), "amount too low"),
        (dict(currency="NGN"), "currency mismatch"),
        (dict(currency="EUR"), "malformed"),
        (dict(tx_ref="bn-someone-elses-payment"), "reference mismatch"),
        (dict(status="failed"), "not successful"),
    ],
)
def test_a_provider_answer_that_does_not_match_what_we_asked_is_refused(
    client, wire, grants, session, payment, fields, reason
):
    values = dict(fields)
    reference = values.pop("tx_ref", payment.reference)
    wire.answers = [reply(verify_text(tx_ref=reference, **values))]

    response = post_hook(client, payment)

    assert response.status_code == 200  # a refusal is final: the vendor is not asked to resend
    assert reload(session, payment).status == "pending" and grants == []
    assert reason in stored_events(session)[-1].error


@pytest.mark.parametrize("amount", ["18", "18.0", "18.00", "18.000", "18.01", "25.50"])
def test_an_amount_at_least_what_we_asked_is_paid_whatever_its_spelling(
    client, wire, grants, session, payment, amount
):
    """Decimal text, never a float: `18`, `18.0` and `18.000` are the same eighteen dollars."""
    wire.answers = [verify_for(payment, amount=amount)]
    post_hook(client, payment)
    assert reload(session, payment).status == "succeeded" and grants == [payment.id]


@pytest.mark.parametrize(
    ("asked", "answered", "paid"),
    [
        (999, "9.99", True),
        (999, "9.98", False),
        (999, "10", True),
        (1, "0.01", True),
        (1, "0.009", False),
        (1815, "18.15", True),  # a float would give 18.15 * 100 = 1814.9999999999998
        (1815, "18.14", False),
    ],
)
def test_odd_cent_amounts_are_compared_exactly(
    client, wire, grants, session, payment, asked, answered, paid
):
    payment.amount_minor = asked
    session.commit()
    wire.answers = [verify_for(payment, amount=answered)]

    post_hook(client, payment)

    assert (reload(session, payment).status == "succeeded") is paid
    assert grants == ([payment.id] if paid else [])


@pytest.mark.parametrize(
    "fields",
    [
        dict(amount="17.999"),  # three decimal places
        dict(amount='"18.00"'),  # text
        dict(amount="null"),
        dict(amount="true"),
        dict(amount="-18"),
        dict(amount="1e400"),
        dict(currency="usd"),  # lowercase
        dict(currency=""),
    ],
)
def test_a_malformed_answer_is_refused_and_recorded_before_anything_else(
    client, wire, grants, session, payment, fields
):
    wire.answers = [verify_for(payment, **fields)]

    response = post_hook(client, payment)

    assert response.status_code == 200
    assert reload(session, payment).status == "pending" and grants == []
    assert "malformed" in stored_events(session)[-1].error


def test_a_malformed_answer_is_judged_before_a_pending_status(
    client, wire, grants, session, payment
):
    """Pending means "ask again later" (503); a malformed report must not wait on that."""
    wire.answers = [verify_for(payment, status="pending", currency="usd")]

    response = post_hook(client, payment)

    assert response.status_code == 200
    assert "malformed" in stored_events(session)[-1].error


def test_a_payment_still_pending_waits_and_a_retry_settles_it(
    client, wire, grants, session, payment
):
    wire.answers = [verify_for(payment, status="pending")]
    assert post_hook(client, payment).status_code == 503
    assert reload(session, payment).status == "pending" and grants == []

    wire.answers = [verify_for(payment)]
    assert post_hook(client, payment).status_code == 200
    assert reload(session, payment).status == "succeeded" and grants == [payment.id]


@pytest.mark.parametrize(
    "outage", [reply("down", 500), reply("down", 503), httpx.ReadTimeout("slow"), reply("junk")]
)
def test_a_provider_that_does_not_answer_gives_503_and_a_payment_unchanged(
    client, wire, grants, session, payment, outage
):
    wire.answers = [outage]
    assert post_hook(client, payment).status_code == 503
    assert reload(session, payment).status == "pending" and grants == []


def test_an_unknown_status_is_recorded_for_a_human_look_not_looped(
    client, wire, grants, session, payment
):
    """No 503 loop until Flutterwave's three retries run out: a recorded row, answered 200. A later
    webhook for the same payment (the vendor sends one per state change) still settles it."""
    wire.answers = [verify_for(payment, status="mystery")]
    assert post_hook(client, payment).status_code == 200
    assert reload(session, payment).status == "pending" and grants == []
    assert "unknown status" in stored_events(session)[-1].error

    wire.answers = [verify_for(payment)]
    assert post_hook(client, payment, status="mystery2").status_code == 200
    assert reload(session, payment).status == "succeeded" and grants == [payment.id]


def test_a_failed_charge_event_fails_the_payment_only_when_the_provider_says_so(
    client, wire, grants, session, payment
):
    wire.answers = [verify_for(payment, status="failed")]
    assert post_hook(client, payment, status="failed").status_code == 200
    assert reload(session, payment).status == "failed" and grants == []

    # The same checkout can still be paid afterwards (the vendor keeps the page open for a retry).
    wire.answers = [verify_for(payment)]
    assert post_hook(client, payment).status_code == 200
    assert reload(session, payment).status == "succeeded" and grants == [payment.id]


def test_a_failed_claim_for_a_payment_the_provider_says_succeeded_grants_nothing(
    client, wire, grants, session, payment
):
    wire.answers = [verify_for(payment, status="successful")]
    post_hook(client, payment, status="failed")
    assert reload(session, payment).status == "pending" and grants == []


def test_the_card_country_in_both_vendor_formats_reaches_the_payment(
    client, wire, grants, session, payment
):
    """`NIGERIA NG` (the verify sample) is stored as `NG`; so would `NG`."""
    wire.answers = [verify_for(payment)]
    post_hook(client, payment)
    assert reload(session, payment).card_country == "NG"


def test_a_bank_transfer_payment_carries_no_card_country(client, wire, grants, session, payment):
    wire.answers = [verify_for(payment, card="")]
    post_hook(client, payment)
    paid = reload(session, payment)
    assert paid.status == "succeeded" and paid.card_country is None
    assert paid.country_mismatch is False


def test_another_providers_payment_is_not_settled_by_a_flutterwave_webhook(
    client, wire, grants, session
):
    payer = make_user(session, "other@example.test")
    other = new_payment(
        session, payer=payer, provider="paystack", kind="plan", currency="NGN", amount_minor=500000
    )
    session.commit()
    wire.answers = [verify_for(other, currency="NGN", amount="5000")]
    post_hook(client, other)
    assert reload(session, other).status == "pending" and grants == []
    assert "wrong provider" in stored_events(session)[-1].error


def test_an_event_of_another_kind_is_answered_200_ignored_and_stores_nothing(client, wire, session):
    response = client.post(
        HOOK_URL,
        content=b'{"event":"transfer.completed","data":{"id":1}}',
        headers={"verif-hash": SECRET_HASH},
    )
    assert response.status_code == 200 and response.json() == {"status": "ignored"}
    assert stored_events(session) == [] and wire.requests == []


def test_a_body_that_is_not_an_event_is_a_400_and_stores_nothing(client, wire, session):
    response = client.post(HOOK_URL, content=b"[]", headers={"verif-hash": SECRET_HASH})
    assert response.status_code == 400
    assert stored_events(session) == [] and wire.requests == []


# ---------------------------------------------------------------- the key kind check at startup


def test_a_test_key_in_test_mode_and_no_key_at_all_start_fine():
    make_settings(flutterwave_secret_key=SECRET_KEY, flutterwave_secret_hash=SECRET_HASH)
    make_settings()


@pytest.mark.parametrize(
    ("over", "name"),
    [
        (dict(flutterwave_secret_key="FAKE-NOT-A-KEY"), "FLUTTERWAVE_SECRET_KEY"),  # no _TEST
        (dict(flutterwave_public_key="FAKE-NOT-A-KEY"), "FLUTTERWAVE_PUBLIC_KEY"),
        (dict(billing_live=True, flutterwave_secret_key=SECRET_KEY), "FLUTTERWAVE_SECRET_KEY"),
        (dict(billing_live=True, flutterwave_public_key=SECRET_KEY), "FLUTTERWAVE_PUBLIC_KEY"),
        (dict(flutterwave_secret_hash=SECRET_HASH), "FLUTTERWAVE_SECRET_HASH"),  # hash, no key
        (dict(flutterwave_secret_key=SECRET_KEY, flutterwave_secret_hash="  "), "SECRET_HASH"),
    ],
)
def test_a_key_of_the_wrong_kind_stops_startup_naming_the_setting_but_not_the_key(over, name):
    with pytest.raises(ValueError) as caught:
        make_settings(**over)
    assert name in str(caught.value) and "FAKE-NOT-A-KEY" not in str(caught.value)


def test_a_live_looking_key_in_live_mode_is_accepted():
    """What a live key looks like is not documented; only a `_TEST` key is refused in live mode."""
    make_settings(billing_live=True, flutterwave_secret_key="FAKE-NOT-A-LIVE-KEY")
