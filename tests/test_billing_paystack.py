"""The Paystack adapter (billing stage S6), with no network, no keys and no database.

Every Paystack body used here is a **documented sample** (`tests/fixtures/paystack/README.md` names
the page of each), not a capture: no test keys exist yet. The signature vectors are computed with
`openssl` from the algorithm on Paystack's webhooks page, over the exact bytes of a fixture; the
secrets in them are invented. Calls to Paystack go through `httpx.MockTransport`; an autouse guard
makes any real connection attempt fail the test, so a green run proves nothing left the process.

What these prove: both signature header forms, the 300-second window, two signatures during a
rotation, a tampered or re-encoded body; Paystack's events and answers mapped onto the neutral
types with whole kobo and no float; a float amount, a lower-case currency or any other broken shape
refused before the status is read; HTTP errors and timeouts as explicit errors; nothing sent without
a key of the right kind.
"""

import hmac
import json
import re
import socket
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.billing import paystack
from app.billing.paystack import API_BASE, NotConfigured, PaystackProvider
from app.billing.provider import PROVIDER_FACTORIES, provider_named
from app.billing.types import (
    BadEvent,
    ChargeBlocked,
    IgnoredEvent,
    NothingMoved,
    OutcomeUnknown,
    ProviderError,
    WebhookRejected,
)
from tests.test_web import make_settings

FIXTURES = Path(__file__).parent / "fixtures" / "paystack"
KEY = "sk_test_unit_test_value_not_a_key"
WEBHOOK_SECRET = "whsec_vector_secret_not_a_key_new"
OLD_WEBHOOK_SECRET = "whsec_vector_secret_not_a_key_old"
LEGACY_KEY = "sk_test_vector_secret_not_a_key"


def raw(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def sample(name: str) -> dict:
    return json.loads(raw(name))


VECTORS = sample("signature_vectors.json")
EVENT_BODY = raw("charge_success_event.json")
T = VECTORS["t"]


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Any real connection attempt fails the test loudly."""

    def refuse(*args, **kwargs):
        raise AssertionError("a test tried to open a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


class Paystack:
    """A scripted Paystack: answers each request in order and remembers what it was sent."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        status, body = answer
        if isinstance(body, bytes):
            return httpx.Response(status, content=body)
        return httpx.Response(status, json=body)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)

    def sent(self, index: int = 0) -> dict:
        return json.loads(self.requests[index].content)


def make(*answers, **kwargs) -> tuple[PaystackProvider, Paystack]:
    script = Paystack(*answers) if answers else Paystack((500, {}))
    values = dict(
        secret_key=KEY,
        webhook_secret=WEBHOOK_SECRET,
        transport=script.transport,
        clock=lambda: T,
    )
    return PaystackProvider(**{**values, **kwargs}), script


def ok(name: str, **data_over) -> tuple[int, dict]:
    body = sample(name)
    body["data"].update(data_over)
    return 200, body


def timestamped(signature: str, *, t: int = T, kid: str = "whk_3f9a1c2e") -> dict:
    return {"Paystack-Signature": f"t={t},v1={kid}:{signature}"}


# ---- webhook signatures: the vectors ----


def test_the_timestamped_vector_is_accepted():
    provider, _ = make()
    provider.verify_webhook(timestamped(VECTORS["timestamped_signature_new"]), EVENT_BODY)


def test_header_names_are_not_case_sensitive():
    provider, _ = make()
    headers = {"paystack-signature": f"t={T},v1=whk_x:{VECTORS['timestamped_signature_new']}"}
    provider.verify_webhook(headers, EVENT_BODY)


def test_the_legacy_vector_is_accepted_while_no_webhook_secret_is_set():
    provider, _ = make(secret_key=LEGACY_KEY, webhook_secret="")
    provider.verify_webhook({"x-paystack-signature": VECTORS["legacy_signature"]}, EVENT_BODY)


def test_once_a_webhook_secret_is_set_the_legacy_header_alone_is_refused():
    """A captured body with its legacy signature has no clock; it must not be replayable."""
    provider, _ = make(secret_key=LEGACY_KEY)
    with pytest.raises(WebhookRejected):
        provider.verify_webhook({"x-paystack-signature": VECTORS["legacy_signature"]}, EVENT_BODY)


def test_a_bad_timestamped_signature_is_not_rescued_by_a_good_legacy_one():
    provider, _ = make(secret_key=LEGACY_KEY)
    headers = {
        **timestamped("0" * 128),
        "x-paystack-signature": VECTORS["legacy_signature"],
    }
    with pytest.raises(WebhookRejected):
        provider.verify_webhook(headers, EVENT_BODY)


def test_a_tampered_body_is_refused():
    provider, _ = make()
    tampered = EVENT_BODY.replace(b"10000", b"10001")
    assert tampered != EVENT_BODY
    with pytest.raises(WebhookRejected):
        provider.verify_webhook(timestamped(VECTORS["timestamped_signature_new"]), tampered)
    legacy, _ = make(secret_key=LEGACY_KEY, webhook_secret="")
    with pytest.raises(WebhookRejected):
        legacy.verify_webhook({"x-paystack-signature": VECTORS["legacy_signature"]}, tampered)


def test_a_re_encoded_body_is_refused():
    """The signature is over the bytes received: the same JSON, spaced differently, differs."""
    provider, _ = make()
    reencoded = json.dumps(json.loads(EVENT_BODY)).encode()
    assert reencoded != EVENT_BODY
    with pytest.raises(WebhookRejected):
        provider.verify_webhook(timestamped(VECTORS["timestamped_signature_new"]), reencoded)


@pytest.mark.parametrize(
    "offset, accepted", [(0, True), (300, True), (-300, True), (301, False), (-301, False)]
)
def test_the_300_second_window(offset, accepted):
    provider, _ = make(clock=lambda: T + offset)
    headers = timestamped(VECTORS["timestamped_signature_new"])
    if accepted:
        provider.verify_webhook(headers, EVENT_BODY)
    else:
        with pytest.raises(WebhookRejected, match="300-second window"):
            provider.verify_webhook(headers, EVENT_BODY)


def test_a_rewritten_timestamp_does_not_match_the_signature():
    """An old genuine signature with `t` moved to now: the signature covers `t`."""
    provider, _ = make(clock=lambda: T + 10_000)
    with pytest.raises(WebhookRejected, match="bad signature"):
        provider.verify_webhook(
            timestamped(VECTORS["timestamped_signature_new"], t=T + 10_000), EVENT_BODY
        )


@pytest.mark.parametrize("holder", [WEBHOOK_SECRET, OLD_WEBHOOK_SECRET])
def test_two_signatures_during_a_rotation_either_one_is_enough(holder):
    header = (
        f"t={T},v1=whk_old:{VECTORS['timestamped_signature_old']},"
        f"v1=whk_new:{VECTORS['timestamped_signature_new']}"
    )
    provider, _ = make(webhook_secret=holder)
    provider.verify_webhook({"Paystack-Signature": header}, EVENT_BODY)


def test_two_signatures_that_both_miss_are_refused_and_unknown_fields_are_ignored():
    header = f"t={T},v1=whk_a:{'0' * 128},v1=whk_b:{'1' * 128},v2=whk_c:ignored,x=y"
    provider, _ = make()
    with pytest.raises(WebhookRejected):
        provider.verify_webhook({"Paystack-Signature": header}, EVENT_BODY)
    good = f"t={T},v9=whk_c:ignored,v1=whk_new:{VECTORS['timestamped_signature_new']},x=y"
    provider.verify_webhook({"Paystack-Signature": good}, EVENT_BODY)


def test_every_signature_is_compared_with_no_early_exit(monkeypatch):
    calls = []
    real = hmac.compare_digest
    monkeypatch.setattr(paystack.hmac, "compare_digest", lambda a, b: calls.append(1) or real(a, b))
    header = f"t={T},v1=whk_new:{VECTORS['timestamped_signature_new']},v1=whk_other:{'a' * 128}"
    provider, _ = make()
    provider.verify_webhook({"Paystack-Signature": header}, EVENT_BODY)
    assert len(calls) == 2  # the good one first, and the second is still compared


@pytest.mark.parametrize(
    "header",
    [
        "",
        "garbage",
        f"v1=whk_x:{'0' * 128}",  # no timestamp
        f"t={T}",  # no signature
        f"t={T},v1={'0' * 128}",  # no key id separator
        f"t={T},t={T},v1=whk_x:{'0' * 128}",  # two timestamps
        f"t=abc,v1=whk_x:{'0' * 128}",
        f"t=-5,v1=whk_x:{'0' * 128}",
        f"t={T},v1=whk_x:{'A' * 128}",  # upper-case hex is not what Paystack sends
        f"t={T},v1=whk_x:{'0' * 127}",
        f"t={T}," + ",".join(f"v1=whk_x:{'0' * 128}" for _ in range(9)),  # too many signatures
    ],
)
def test_unreadable_headers_are_refused(header):
    provider, _ = make()
    with pytest.raises(WebhookRejected):
        provider.verify_webhook({"Paystack-Signature": header}, EVENT_BODY)


def test_a_missing_header_and_a_provider_with_no_secret_refuse_everything():
    provider, _ = make()
    with pytest.raises(WebhookRejected):
        provider.verify_webhook({}, EVENT_BODY)
    bare, _ = make(secret_key="", webhook_secret="")
    with pytest.raises(WebhookRejected):
        bare.verify_webhook(timestamped(VECTORS["timestamped_signature_new"]), EVENT_BODY)
    with pytest.raises(WebhookRejected):
        bare.verify_webhook({"x-paystack-signature": VECTORS["legacy_signature"]}, EVENT_BODY)


def test_a_body_that_is_not_bytes_is_refused():
    provider, _ = make()
    with pytest.raises(WebhookRejected):
        provider.verify_webhook(timestamped("0" * 128), "not bytes")  # type: ignore[arg-type]


# ---- webhook events ----


def test_a_charge_success_event_becomes_a_neutral_event():
    provider, _ = make()
    event = provider.parse_event(EVENT_BODY)
    assert (event.kind, event.reference, event.provider_tx_id) == (
        "charge_succeeded",
        "qTPrJoy9Bx",
        "302961",
    )
    assert event.amount_minor == 10000 and type(event.amount_minor) is int
    assert (event.currency, event.payer_email) == ("NGN", "bojack@horseman.com")
    assert event.card_token is None  # the documented sample says nothing about `reusable`


def test_the_event_id_is_stable_for_a_redelivery_and_differs_between_events():
    provider, _ = make()
    again = provider.parse_event(EVENT_BODY)
    assert provider.parse_event(EVENT_BODY).event_id == again.event_id == "charge.success:302961"
    refund = provider.parse_event(raw("refund_processed_event.json"))
    other = json.loads(EVENT_BODY)
    other["data"]["id"] = 302962
    assert provider.parse_event(json.dumps(other).encode()).event_id != again.event_id
    assert refund.event_id == "refund.processed:rr:132013318360" != again.event_id


def test_a_reusable_card_gives_a_token_and_the_token_is_not_in_the_repr():
    provider, _ = make()
    body = json.loads(EVENT_BODY)
    body["data"]["authorization"]["reusable"] = True
    event = provider.parse_event(json.dumps(body).encode())
    assert event.card_token == "AUTH_f5rnfq9p"
    assert "AUTH_f5rnfq9p" not in repr(event)
    body["data"]["authorization"]["reusable"] = False
    assert provider.parse_event(json.dumps(body).encode()).card_token is None


def test_a_refund_event_reads_its_text_amount_as_whole_kobo():
    event = make()[0].parse_event(raw("refund_processed_event.json"))
    assert (event.kind, event.reference, event.provider_tx_id) == (
        "refund",
        "T2154954_412829_3be32076_6lcg3",
        "132013318360",
    )
    assert event.amount_minor == 5000 and type(event.amount_minor) is int


def test_only_a_refund_failed_event_says_the_refund_failed():
    provider, _ = make()
    processed = provider.parse_event(raw("refund_processed_event.json"))
    body = json.loads(raw("refund_processed_event.json"))
    body["event"] = "refund.failed"
    failed = provider.parse_event(json.dumps(body).encode())
    assert processed.refund_failed is False
    assert failed.refund_failed is True and failed.kind == "refund"
    assert failed.event_id.startswith("refund.failed:")


def test_a_dispute_event_points_at_the_charge_it_is_about():
    event = make()[0].parse_event(raw("dispute_create_event.json"))
    assert (event.kind, event.reference, event.provider_tx_id) == (
        "dispute",
        "v3mjfgbnc19v97x",
        "896467688",
    )
    assert event.event_id == "charge.dispute.create:id:358950" and event.amount_minor == 5800


@pytest.mark.parametrize(
    "name",
    [
        "ping",
        "invoice.create",
        "invoice.payment_failed",
        "transfer.success",
        "subscription.create",
        "customeridentification.success",
        "refund.somethingnew",
        "charge.dispute.other",
        "charge.failed",
    ],
)
def test_events_we_do_not_act_on_are_ignored_not_unreadable(name):
    with pytest.raises(IgnoredEvent):
        make()[0].parse_event(json.dumps({"event": name, "data": {}}).encode())


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(amount=10000.0),
        lambda d: d.update(amount="10000"),
        lambda d: d.update(amount=True),
        lambda d: d.update(amount=None),
        lambda d: d.update(currency="ngn"),
        lambda d: d.update(currency="NGN "),
        lambda d: d.update(currency=None),
        lambda d: d.update(id=302961.0),
        lambda d: d.update(id="302961"),
        lambda d: d.update(id=True),
        lambda d: d.update(id=-1),
        lambda d: d.update(id=2**64),
        lambda d: d.update(reference=None),
        lambda d: d.update(reference=""),
        lambda d: d.pop("id"),
    ],
)
def test_a_malformed_charge_event_is_unreadable(mutate):
    body = json.loads(EVENT_BODY)
    mutate(body["data"])
    with pytest.raises(BadEvent):
        make()[0].parse_event(json.dumps(body).encode())


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"not json",
        b"[]",
        b'"charge.success"',
        b"{}",
        b'{"event": 5}',
        b'{"event": "charge.success"}',
        b'{"event": "charge.success", "data": []}',
        b'{"event": "charge.success", "data": null}',
        b"\xff\xfe",
    ],
)
def test_a_body_that_is_not_an_event_is_unreadable(body):
    with pytest.raises(BadEvent):
        make()[0].parse_event(body)


def test_a_refund_or_dispute_event_with_null_fields_is_still_an_event_never_a_lost_one():
    """A `refund.pending` may carry no processor reference yet, a dispute no refund amount: stored
    as decided notices (S9), never a 400 that Paystack resends for 72 hours and then drops."""
    provider, _ = make()
    refund = sample("refund_processed_event.json")
    refund["data"].update(refund_reference=None, status="pending")
    event = provider.parse_event(json.dumps(refund).encode())
    assert event.kind == "refund" and event.reference == "T2154954_412829_3be32076_6lcg3"
    assert event.event_id.startswith("refund.processed:body:")  # never the transaction reference
    assert event.provider_tx_id == "T2154954_412829_3be32076_6lcg3"

    for amount in ("50.00", 5000.5, None, "x"):
        refund["data"]["amount"] = amount
        assert provider.parse_event(json.dumps(refund).encode()).amount_minor == 0

    refund["data"].update(refund_reference=None, transaction_reference=None, currency="ngn")
    body = json.dumps(refund).encode()
    nothing = provider.parse_event(body)
    assert nothing.reference == "unknown" and nothing.currency == "NGN"
    assert nothing.event_id == provider.parse_event(body).event_id  # stable for a redelivery
    assert nothing.event_id.startswith("refund.processed:") and len(nothing.event_id) < 200

    dispute = sample("dispute_create_event.json")
    dispute["data"].update(refund_amount=None, currency=None)
    event = provider.parse_event(json.dumps(dispute).encode())
    assert (event.kind, event.amount_minor, event.currency) == ("dispute", 0, "NGN")
    assert event.reference == "v3mjfgbnc19v97x" and event.provider_tx_id == "896467688"

    dispute["data"].update(id=None, transaction=None, transaction_reference="v3mjfgbnc19v97x")
    event = provider.parse_event(json.dumps(dispute).encode())
    assert event.reference == "v3mjfgbnc19v97x" and event.event_id.startswith("charge.dispute.")

    # Two partial refunds of ONE payment, neither with a refund reference: different events.
    refund["data"].update(transaction_reference="T2154954_412829_3be32076_6lcg3", amount="5000")
    first = provider.parse_event(json.dumps(refund).encode())
    refund["data"]["amount"] = "2500"
    second = provider.parse_event(json.dumps(refund).encode())
    assert first.event_id != second.event_id and "T2154954" not in first.event_id
    refund["data"]["id"] = 77  # a refund id, if the event carries one, is the key
    assert provider.parse_event(json.dumps(refund).encode()).event_id == "refund.processed:id:77"

    refund["data"]["transaction_reference"] = "x" * 100  # over the column: a placeholder, not a 400
    assert provider.parse_event(json.dumps(refund).encode()).reference == "unknown"


@pytest.mark.parametrize("name", ["refund.processed", "charge.dispute.create"])
@pytest.mark.parametrize("data", [None, [], "x", 5])
def test_a_refund_or_dispute_event_whose_data_is_not_an_object_is_unreadable(name, data):
    with pytest.raises(BadEvent):
        make()[0].parse_event(json.dumps({"event": name, "data": data}).encode())


@pytest.mark.parametrize("reference", ["x" * 65, "has space", "a/b", "é"])
def test_a_charge_that_is_not_ours_is_ignored_not_resent_for_days(reference):
    body = json.loads(EVENT_BODY)
    body["data"]["reference"] = reference
    with pytest.raises(IgnoredEvent):
        make()[0].parse_event(json.dumps(body).encode())


# ---- hosted checkout ----

PAYMENT = SimpleNamespace(
    provider="paystack", currency="NGN", amount_minor=150000, reference="bn-7-0123456789abcdef"
)
PAYER = SimpleNamespace(email="payer@example.test")


def test_start_checkout_sends_whole_kobo_our_reference_and_the_callback():
    provider, script = make(ok("initialize_response.json", reference=PAYMENT.reference))

    checkout = provider.start_checkout(PAYMENT, PAYER, "https://notes.example.test/billing/return")

    assert checkout.url == "https://checkout.paystack.com/3ni8kdavz62431k"
    assert checkout.reference == PAYMENT.reference
    [request] = script.requests
    assert (request.method, str(request.url)) == ("POST", f"{API_BASE}/transaction/initialize")
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert script.sent() == {
        "email": "payer@example.test",
        "amount": "150000",
        "currency": "NGN",
        "reference": PAYMENT.reference,
        "callback_url": "https://notes.example.test/billing/return",
    }
    assert b"." not in request.content.split(b'"amount"')[1].split(b",")[0]  # no decimal point


@pytest.mark.parametrize(
    "bad",
    [
        {"currency": "USD"},
        {"provider": "flutterwave"},
        {"amount_minor": 0},
        {"amount_minor": 1500.5},
        {"amount_minor": True},
        {"reference": "has space"},
        {"reference": ""},
    ],
)
def test_start_checkout_refuses_a_payment_paystack_cannot_take_before_any_call(bad):
    provider, script = make(ok("initialize_response.json"))
    payment = SimpleNamespace(**{**vars(PAYMENT), **bad})
    with pytest.raises(ProviderError):
        provider.start_checkout(payment, PAYER, "https://x.example.test/")
    assert script.requests == []


@pytest.mark.parametrize(
    "data",
    [
        {"reference": "someone-elses"},
        {"authorization_url": "http://checkout.paystack.com/abc"},
        {"authorization_url": "https://evil.example.test/abc"},
        {"authorization_url": "https://checkout.paystack.com.evil.test/abc"},
        {"authorization_url": None},
    ],
)
def test_start_checkout_refuses_an_answer_it_cannot_trust(data):
    provider, _ = make(ok("initialize_response.json", **{"reference": PAYMENT.reference, **data}))
    with pytest.raises(ProviderError):
        provider.start_checkout(PAYMENT, PAYER, "https://x.example.test/")


# ---- verify and fetch ----


def test_fetch_transaction_reads_the_documented_verify_sample():
    provider, script = make(ok("verify_response.json"))

    tx = provider.fetch_transaction("4099260516")

    assert (script.requests[0].method, str(script.requests[0].url)) == (
        "GET",
        f"{API_BASE}/transaction/4099260516",
    )
    assert tx.provider_tx_id == "4099260516" and tx.reference == "re4lyvq3s3"
    assert (tx.status, tx.amount_minor, tx.currency) == ("successful", 40333, "NGN")
    assert type(tx.amount_minor) is int
    assert tx.card_country == "NG" and tx.payer_email == "demo@test.com"
    assert tx.card_token == "AUTH_uh8bcl3zbn" and "AUTH_uh8bcl3zbn" not in repr(tx)


def test_verify_reference_asks_for_our_reference():
    provider, script = make(ok("verify_response.json"))
    provider.verify_reference("bn-7-0123456789abcdef")
    assert str(script.requests[0].url) == f"{API_BASE}/transaction/verify/bn-7-0123456789abcdef"


@pytest.mark.parametrize("bad", ["", "abc", "12.5", "../x", "1 2", "-1", "1" * 21, None, 12])
def test_fetch_refuses_an_id_that_is_not_a_number_before_any_call(bad):
    provider, script = make(ok("verify_response.json"))
    with pytest.raises(ProviderError):
        provider.fetch_transaction(bad)
    assert script.requests == []


@pytest.mark.parametrize("bad", ["", "a b", "a/b", "../x", "x" * 65, "é", None])
def test_verify_refuses_a_reference_paystack_would_not_accept_before_any_call(bad):
    provider, script = make(ok("verify_response.json"))
    with pytest.raises(ProviderError):
        provider.verify_reference(bad)
    assert script.requests == []


@pytest.mark.parametrize(
    "paystack_status, neutral",
    [
        ("success", "successful"),
        ("failed", "failed"),
        ("reversed", "failed"),
        ("abandoned", "pending"),
        ("ongoing", "pending"),
        ("pending", "pending"),
        ("processing", "pending"),
        ("queued", "pending"),
    ],
)
def test_every_documented_status_maps_and_unfinished_is_never_failed(paystack_status, neutral):
    provider, _ = make(ok("verify_response.json", status=paystack_status))
    assert provider.fetch_transaction("4099260516").status == neutral


@pytest.mark.parametrize("status", ["SUCCESS", "paid", "", None, 5, ["success"]])
def test_an_unknown_status_is_an_error_not_a_guess(status):
    provider, _ = make(ok("verify_response.json", status=status))
    with pytest.raises(ProviderError, match="unknown status"):
        provider.fetch_transaction("4099260516")


@pytest.mark.parametrize(
    "field, value",
    [
        ("amount", 40333.0),  # a float is refused, never rounded
        ("amount", 40333.5),
        ("amount", "40333"),
        ("amount", True),
        ("amount", None),
        ("currency", "ngn"),  # lower case is refused, never upper-cased
        ("currency", "NGN "),
        ("currency", "NG"),
        ("currency", None),
        ("id", "4099260516"),
        ("id", 4099260516.0),
        ("id", True),
        ("id", None),
        ("reference", None),
        ("reference", ""),
        ("reference", 5),
    ],
)
@pytest.mark.parametrize("status", ["success", "ongoing"])
def test_a_malformed_answer_is_refused_before_the_status_is_read(field, value, status):
    """A broken shape is an error whatever the status says: a `pending` answer with a float amount
    is not a quiet "try later"."""
    provider, _ = make(ok("verify_response.json", status=status, **{field: value}))
    with pytest.raises(ProviderError, match="malformed Paystack answer"):
        provider.fetch_transaction("4099260516")


@pytest.mark.parametrize(
    "authorization, token, country",
    [
        ({"authorization_code": "AUTH_x", "reusable": False, "country_code": "GB"}, None, "GB"),
        ({"authorization_code": "AUTH_x", "country_code": "GB"}, None, "GB"),
        ({"authorization_code": "AUTH_x", "reusable": "true"}, None, None),
        ({"authorization_code": "", "reusable": True}, None, None),
        ({"authorization_code": "AUTH_x", "reusable": True, "country_code": 5}, "AUTH_x", None),
        ({}, None, None),
        (None, None, None),
    ],
)
def test_a_card_token_only_from_a_reusable_card_and_the_country_only_if_text(
    authorization, token, country
):
    provider, _ = make(ok("verify_response.json", authorization=authorization))
    tx = provider.fetch_transaction("4099260516")
    assert (tx.card_token, tx.card_country) == (token, country)


def test_the_country_is_passed_on_as_paystack_sent_it_for_the_mismatch_note():
    provider, _ = make(
        ok("verify_response.json", authorization={"reusable": True, "country_code": "us"})
    )
    assert provider.fetch_transaction("4099260516").card_country == "us"  # mismatch.py normalises


# ---- saved-card charge ----


def charge(provider, **over):
    values = dict(
        token="AUTH_uh8bcl3zbn",
        amount_minor=35247,
        currency="NGN",
        reference="bn-9-fedcba9876543210",
        payer_email="demo@test.com",
    )
    values.update(over)
    return provider.charge_saved_method(
        values["token"],
        values["amount_minor"],
        values["currency"],
        values["reference"],
        payer_email=values["payer_email"],
    )


def test_a_saved_card_charge_sends_the_token_the_email_and_whole_kobo():
    provider, script = make(
        ok("charge_authorization_response.json", reference="bn-9-fedcba9876543210")
    )

    tx = charge(provider)

    assert str(script.requests[0].url) == f"{API_BASE}/transaction/charge_authorization"
    assert script.sent() == {
        "authorization_code": "AUTH_uh8bcl3zbn",
        "email": "demo@test.com",
        "amount": "35247",
        "currency": "NGN",
        "reference": "bn-9-fedcba9876543210",
    }
    assert (tx.status, tx.amount_minor, tx.currency) == ("successful", 35247, "NGN")
    assert tx.provider_tx_id == "4099490251" and tx.reference == "bn-9-fedcba9876543210"


def test_a_declined_charge_is_a_failed_transaction_not_an_exception():
    """Paystack answers 200 for a charge that did not pay (docs: "check the data object")."""
    provider, _ = make(
        ok(
            "charge_authorization_response.json",
            status="failed",
            gateway_response="Insufficient Funds",
            reference="bn-9-fedcba9876543210",
        )
    )
    tx = charge(provider)
    assert tx.status == "failed" and tx.provider_tx_id == "4099490251"


def test_a_card_that_must_be_challenged_again_is_pending_never_an_exception():
    provider, _ = make((200, sample("charge_authorization_paused_response.json")))
    tx = charge(provider, reference="jvx2o36ghlvrgtt")
    assert (tx.status, tx.reference) == ("pending", "jvx2o36ghlvrgtt")
    assert tx.amount_minor == 35247 and tx.currency == "NGN"
    assert tx.payer_action_url == "https://checkout.paystack.com/resume/0744ub5o065nwyz"
    assert tx.provider_tx_id == "jvx2o36ghlvrgtt"  # our reference, not a Paystack id


@pytest.mark.parametrize(
    "url", [None, "http://checkout.paystack.com/x", "https://evilpaystack.com/x", "https://x.test/"]
)
def test_a_paused_answer_without_a_usable_address_is_an_error(url):
    body = sample("charge_authorization_paused_response.json")
    body["data"]["authorization_url"] = url
    provider, _ = make((200, body))
    with pytest.raises(ProviderError, match="checkout address"):
        charge(provider, reference="jvx2o36ghlvrgtt")


@pytest.mark.parametrize(
    "over",
    [
        {"reference": "someone-elses-reference"},
        {"currency": "USD"},
        {"amount": 35246},
        {"amount": 1},
    ],
)
def test_a_charge_answer_that_does_not_match_the_request_is_an_error_not_a_result(over):
    """The outcome is unknown then (money may have moved): the caller verifies by reference."""
    provider, _ = make(
        ok("charge_authorization_response.json", **{"reference": "bn-9-fedcba9876543210", **over})
    )
    with pytest.raises(ProviderError, match="does not match"):
        charge(provider)


def test_a_charge_answer_with_a_higher_amount_is_accepted():
    provider, _ = make(
        ok("charge_authorization_response.json", reference="bn-9-fedcba9876543210", amount=40000)
    )
    assert charge(provider).amount_minor == 40000


def test_a_paused_answer_for_another_reference_is_an_error():
    provider, _ = make((200, sample("charge_authorization_paused_response.json")))
    with pytest.raises(ProviderError):
        charge(provider, reference="bn-9-fedcba9876543210")


@pytest.mark.parametrize(
    "bad",
    [
        {"payer_email": None},
        {"payer_email": ""},
        {"token": ""},
        {"token": None},
        {"currency": "USD"},
        {"currency": "ngn"},
        {"amount_minor": 0},
        {"amount_minor": 352.47},
        {"amount_minor": True},
        {"reference": "has space"},
        {"reference": ""},
    ],
)
def test_a_charge_that_cannot_be_asked_properly_is_refused_before_any_call(bad):
    provider, script = make(ok("charge_authorization_response.json"))
    with pytest.raises(ProviderError):
        charge(provider, **bad)
    assert script.requests == []


def test_a_charge_answer_with_a_float_amount_is_refused():
    provider, _ = make(ok("charge_authorization_response.json", amount=35247.0))
    with pytest.raises(ProviderError, match="malformed"):
        charge(provider)


@pytest.mark.parametrize(
    "answer",
    [
        (400, {"status": False, "message": "Authorization code not found"}),
        (403, {"status": False, "message": "Forbidden"}),
        (404, {"status": False, "message": "Not found"}),
    ],
)
def test_a_charge_paystack_refuses_with_a_4xx_is_nothing_moved_and_the_customers_failure(answer):
    """Review S1: a refused call made no charge, so a caller need not wait for a lookup."""
    provider, _ = make(answer)
    with pytest.raises(NothingMoved) as caught:
        charge(provider)
    assert not isinstance(caught.value, ChargeBlocked)


@pytest.mark.parametrize(
    "answer",
    [
        (401, {"status": False, "message": "Invalid key"}),
        (429, {"status": False, "message": "Too many requests"}),
    ],
)
def test_a_charge_refused_for_our_key_or_a_rate_limit_is_blocked_not_the_customers_failure(answer):
    """Review delta (3): ours, or the provider's, so the renewal clock must not count it."""
    provider, _ = make(answer)
    with pytest.raises(ChargeBlocked):
        charge(provider)


@pytest.mark.parametrize(
    "bad",
    [
        {"payer_email": None},
        {"token": ""},
        {"currency": "USD"},
        {"amount_minor": 0},
        {"reference": "has space"},
    ],
)
def test_a_charge_refused_before_any_call_is_nothing_moved(bad):
    provider, script = make(ok("charge_authorization_response.json"))
    with pytest.raises(NothingMoved):
        charge(provider, **bad)
    assert script.requests == []


def test_a_charge_with_no_key_is_blocked():
    provider, script = make(ok("charge_authorization_response.json"), secret_key="")
    with pytest.raises(ChargeBlocked):
        charge(provider)
    assert script.requests == []


@pytest.mark.parametrize(
    "answer",
    [
        httpx.ReadTimeout("slow"),
        httpx.ConnectError("down"),
        (500, {"status": False, "message": "boom"}),
        (503, {}),
        (200, {"status": True, "message": "x", "data": "not an object"}),
    ],
)
def test_a_charge_with_no_usable_answer_is_not_nothing_moved(answer):
    """A lost or unreadable answer may still have charged the card: it stays a plain
    `ProviderError`, which the caller resolves by our reference."""
    provider, _ = make(answer)
    with pytest.raises(ProviderError) as caught:
        charge(provider)
    assert not isinstance(caught.value, NothingMoved)


# ---- refunds ----


REF = "bnrf-17-0123456789abcdef"  # the shape our refund references have


def test_a_refund_sends_whole_kobo_and_returns_paystacks_refund_id():
    provider, script = make(ok("refund_response.json"))

    assert provider.refund("1004723697", 10000, reference=REF) == "3018284"

    assert str(script.requests[0].url) == f"{API_BASE}/refund"
    assert script.sent() == {"transaction": "1004723697", "amount": 10000, "reference": REF}
    assert b"10000," in script.requests[0].content  # an integer, not "10000.0"


def test_a_refund_without_our_reference_cannot_be_asked_at_all():
    """The reference is Paystack's idempotency key: without it a retried refund pays twice, so the
    argument is required (S9), not optional as it was in S6."""
    provider, script = make(ok("refund_response.json"))
    with pytest.raises(TypeError):
        provider.refund("1004723697", 10000)
    assert script.requests == []


def test_a_retried_refund_that_paystack_refuses_as_a_conflict_is_unknown_not_a_second_refund():
    """A 409 says a refund with this reference exists: an earlier try went through. It is an
    `OutcomeUnknown` (look at the dashboard), not a refusal, and nothing is sent twice."""
    provider, script = make((409, {"status": False, "message": "Duplicate refund reference"}))
    with pytest.raises(OutcomeUnknown, match="already exists"):
        provider.refund("1004723697", 10000, reference=REF)
    assert len(script.requests) == 1


@pytest.mark.parametrize("reference", ["short", "x" * 65, "has space", "a/b/c/d/e/f", "", 5, None])
def test_a_refund_reference_paystack_would_refuse_is_refused_before_any_call(reference):
    provider, script = make(ok("refund_response.json"))
    with pytest.raises(ProviderError):
        provider.refund("1004723697", 10000, reference=reference)
    assert script.requests == []


def test_a_refund_for_another_amount_than_asked_may_have_moved_money_so_it_is_unknown():
    provider, _ = make(ok("refund_response.json", amount=5000))
    with pytest.raises(OutcomeUnknown, match="amount we asked"):
        provider.refund("1004723697", 10000, reference=REF)


def test_a_refund_paystack_reports_as_failed_is_a_refusal():
    provider, _ = make(ok("refund_response.json", status="failed"))
    with pytest.raises(ProviderError, match="failed"):
        provider.refund("1004723697", 10000, reference=REF)


@pytest.mark.parametrize("data", [{"id": None}, {"id": "3018284"}, {"id": 3018284.0}, {"id": True}])
def test_a_refund_answer_without_a_whole_number_id_is_unknown_it_may_exist(data):
    provider, _ = make(ok("refund_response.json", **data))
    with pytest.raises(OutcomeUnknown, match="refund id"):
        provider.refund("1004723697", 10000, reference=REF)


@pytest.mark.parametrize(
    "tx, amount",
    [
        ("abc", 100),
        ("", 100),
        ("12.5", 100),
        ("1004723697", 0),
        ("1004723697", 1.5),
        ("1004723697", True),
        ("1004723697", -5),
    ],
)
def test_a_refund_that_cannot_be_asked_properly_is_refused_before_any_call(tx, amount):
    provider, script = make(ok("refund_response.json"))
    with pytest.raises(ProviderError):
        provider.refund(tx, amount, reference=REF)
    assert script.requests == []


# What a refund does when nothing usable came back. A call that moves money may have gone out, so
# a missing answer is `OutcomeUnknown`, which is NOT a `ProviderError`: nobody may retry it blind.
# A plain refusal (the key, the request) means nothing moved and stays a `ProviderError`.
@pytest.mark.parametrize(
    "answer",
    [
        (500, {"status": False, "message": "boom"}),
        (502, b"<html>bad gateway</html>"),
        (503, b""),
        (409, {"status": False, "message": "Duplicate refund reference"}),
        (200, b"<html>not json</html>"),
        (200, []),
        (200, {"status": False, "message": "no", "data": {}}),
        (200, {"status": True, "message": "ok"}),
        (302, b""),
        (204, b""),
        httpx.ReadTimeout("secret-looking text"),
        httpx.ConnectTimeout("t"),
        httpx.ConnectError("https://api.paystack.co/leak"),
        httpx.RemoteProtocolError("x"),
    ],
)
def test_a_refund_that_got_no_usable_answer_is_unknown_and_leaks_nothing(answer):
    provider, script = make(answer)
    with pytest.raises(OutcomeUnknown) as caught:
        provider.refund("1004723697", 10000, reference=REF)
    assert not isinstance(caught.value, ProviderError)  # a retry-on-ProviderError loop misses it
    message = str(caught.value)
    assert KEY not in message and "paystack.co" not in message and "payer@example" not in message
    assert len(script.requests) <= 1  # never retried inside the adapter


@pytest.mark.parametrize(
    "answer, text",
    [
        ((429, {"status": False, "message": "slow down"}), "answered 429"),
        ((401, {"status": False, "message": "Invalid key"}), "refused the secret key"),
        ((404, {"status": False, "message": "Transaction not found"}), "refused the request (404)"),
        (
            (400, sample("error_validation_response.json")),
            "refused the request (400): Email Address is required",
        ),
    ],
)
def test_a_refund_paystack_plainly_refused_is_a_provider_error_nothing_moved(answer, text):
    provider, _ = make(answer)
    with pytest.raises(ProviderError, match=re.escape(text)):
        provider.refund("1004723697", 10000, reference=REF)


# ---- HTTP failures are explicit errors, never a quiet success ----

EVERY_CALL = [
    lambda p: p.fetch_transaction("4099260516"),
    lambda p: p.verify_reference("re4lyvq3s3"),
    lambda p: p.refund("1004723697", 10000, reference=REF),
    lambda p: p.start_checkout(PAYMENT, PAYER, "https://x.example.test/"),
    lambda p: charge(p),
]


# The refund is judged on its own above (a missing answer is `OutcomeUnknown` there).
NOT_MONEY_CALLS = [call for call in EVERY_CALL if call is not EVERY_CALL[2]]


@pytest.mark.parametrize("call", NOT_MONEY_CALLS)
@pytest.mark.parametrize(
    "answer, text",
    [
        ((500, {"status": False, "message": "boom"}), "answered 500"),
        ((502, b"<html>bad gateway</html>"), "answered 502"),
        ((429, {"status": False, "message": "slow down"}), "answered 429"),
        ((401, {"status": False, "message": "Invalid key"}), "refused the secret key"),
        ((404, {"status": False, "message": "Transaction not found"}), "refused the request (404)"),
        (
            (400, sample("error_validation_response.json")),
            "refused the request (400): Email Address is required",
        ),
        (
            (409, {"status": False, "message": "Duplicate Transaction Reference"}),
            "(409): Duplicate",
        ),
        ((200, b"<html>not json</html>"), "not JSON"),
        ((200, []), "unexpected shape"),
        ((200, {"status": False, "message": "no", "data": {}}), "unexpected shape"),
        ((200, {"status": True, "message": "ok"}), "unexpected shape"),
        ((200, {"status": True, "data": []}), "unexpected shape"),
        ((302, b""), "unexpected status"),
        ((204, b""), "unexpected status"),
        (httpx.ReadTimeout("secret-looking text"), "did not answer in time"),
        (httpx.ConnectTimeout("t"), "did not answer in time"),
        (httpx.ConnectError("https://api.paystack.co/leak"), "request failed (ConnectError)"),
        (httpx.RemoteProtocolError("x"), "request failed (RemoteProtocolError)"),
    ],
)
def test_a_failed_call_raises_a_provider_error_that_leaks_nothing(call, answer, text):
    provider, _ = make(answer)
    with pytest.raises(ProviderError) as caught:
        call(provider)
    message = str(caught.value)
    assert text in message
    assert KEY not in message and "paystack.co" not in message and "payer@example" not in message


def test_a_redirect_is_not_followed():
    provider, script = make((302, b""))
    with pytest.raises(ProviderError, match="unexpected status"):
        provider.fetch_transaction("4099260516")
    assert len(script.requests) == 1


# ---- nothing is sent without a key of the right kind ----


@pytest.mark.parametrize("call", EVERY_CALL)
@pytest.mark.parametrize(
    "key, live",
    [
        ("", False),
        ("   ", False),
        ("", True),
        ("sk_live_" + "x" * 20, False),  # a live key while BILLING_LIVE is off
        ("sk_test_" + "x" * 20, True),  # a test key while BILLING_LIVE is on
        ("pk_test_" + "x" * 20, False),  # a public key is not a secret key
        ("whsec_" + "x" * 20, False),
        ("x" * 20, False),
        ("sk_test_é" + "x" * 20, False),  # not even sendable in a header
    ],
)
def test_no_call_leaves_the_process_without_a_key_of_the_right_kind(call, key, live):
    provider, script = make((200, sample("verify_response.json")), secret_key=key, live=live)
    with pytest.raises(NotConfigured) as caught:
        call(provider)
    assert key.strip() == "" or key not in str(caught.value)
    assert script.requests == []


def test_a_live_key_is_accepted_only_when_billing_is_live():
    provider, script = make(ok("verify_response.json"), secret_key="sk_live_" + "x" * 20, live=True)
    provider.fetch_transaction("4099260516")
    assert len(script.requests) == 1


def test_the_adapter_built_from_default_settings_can_call_nothing():
    provider = provider_named("paystack", make_settings())
    assert isinstance(provider, PaystackProvider)
    for call in EVERY_CALL:
        with pytest.raises(NotConfigured):
            call(provider)  # no transport was given: a real connection would trip the guard
    with pytest.raises(WebhookRejected):
        provider.verify_webhook(timestamped("0" * 128), EVENT_BODY)


def test_the_repr_of_the_adapter_shows_no_key():
    provider, _ = make()
    assert KEY not in repr(provider) and WEBHOOK_SECRET not in repr(provider)


def test_paystack_registers_itself_and_the_fake_is_never_reachable_when_live(monkeypatch):
    assert PROVIDER_FACTORIES["paystack"] is paystack.from_settings
    monkeypatch.setitem(PROVIDER_FACTORIES, "fake", lambda settings: object())
    assert provider_named("fake", make_settings()) is not None  # tests may use it
    assert provider_named("fake", make_settings(billing_live=True)) is None


# ---- the settings: test and live keys are never mixed ----


@pytest.mark.parametrize(
    "overrides, setting",
    [
        ({"paystack_secret_key": "sk_live_" + "a" * 12}, "PAYSTACK_SECRET_KEY"),
        ({"paystack_public_key": "pk_live_" + "a" * 12}, "PAYSTACK_PUBLIC_KEY"),
        (
            {"billing_live": True, "paystack_secret_key": "sk_test_" + "a" * 12},
            "PAYSTACK_SECRET_KEY",
        ),
        (
            {"billing_live": True, "paystack_public_key": "pk_test_" + "a" * 12},
            "PAYSTACK_PUBLIC_KEY",
        ),
        ({"paystack_secret_key": "pk_test_" + "a" * 12}, "PAYSTACK_SECRET_KEY"),
        ({"paystack_secret_key": "garbage"}, "PAYSTACK_SECRET_KEY"),
    ],
)
def test_a_key_of_the_other_kind_stops_the_process_naming_the_setting_not_the_key(
    overrides, setting
):
    with pytest.raises(ValueError) as caught:
        make_settings(**overrides)
    text = str(caught.value)
    assert setting in text and "BILLING_LIVE" in text
    assert "a" * 12 not in text and "garbage" not in text


def test_matching_keys_load_and_stay_out_of_the_settings_repr():
    test_mode = make_settings(
        paystack_secret_key=" sk_test_" + "a" * 12 + " ",
        paystack_public_key="pk_test_" + "b" * 12,
        paystack_webhook_secret="whsec_" + "c" * 12,
    )
    live_mode = make_settings(
        billing_live=True,
        paystack_secret_key="sk_live_" + "a" * 12,
        paystack_public_key="pk_live_" + "b" * 12,
    )
    assert test_mode.billing_live is False and live_mode.billing_live is True
    for settings in (test_mode, live_mode):
        assert not any(part * 12 in repr(settings) for part in "abc")
    assert make_settings().paystack_secret_key == ""  # the defaults are empty


@pytest.mark.parametrize(
    "overrides, text",
    [
        ({"paystack_webhook_secret": "   "}, "PAYSTACK_WEBHOOK_SECRET is blank"),
        ({"paystack_webhook_secret": "whsec_" + "c" * 12}, "PAYSTACK_SECRET_KEY is not"),
    ],
)
def test_a_webhook_secret_that_is_blank_or_has_no_secret_key_stops_the_process(overrides, text):
    with pytest.raises(ValueError, match=text):
        make_settings(**overrides)
