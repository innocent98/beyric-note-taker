"""The Paystack adapter, naira only (billing spec sections 5.1 to 5.5, stage S6).

It speaks Paystack's shapes and turns them into the neutral types (`app/billing/types.py`); it
never decides whether a payment is paid (that is `settle.py`, on what `fetch_transaction` returns).
Facts below come from Paystack's own pages, read 2026-11-17 and named in
`docs/sop/2026-11-17-billing-paystack-adapter.md`; nothing here has run against Paystack yet (no
test keys): see that SOP's "Not verified live".

**Nothing leaves the process without a key of the right kind.** Every call to Paystack goes through
`_call`, which refuses (`NotConfigured`, before any connection is opened) when no secret key is set,
or when the key is not `sk_test_...` while `BILLING_LIVE` is false (or not `sk_live_...` while it is
true). `app/config.py` stops the process at startup for the same mismatch. With the defaults (no
keys) the adapter cannot make a call at all; tests inject a transport and never reach the network.

**Webhook signatures** (`verify_webhook`, on the exact raw bytes):

* `Paystack-Signature: t=<unix seconds>,v1=<key id>:<128 lowercase hex>[,v1=...]`: HMAC SHA512 of
  `t`, a dot and the raw body, keyed with the whole webhook signing secret (`whsec_...`). Rejected
  when `t` is more than 300 seconds from our clock. A header can carry two `v1` values while a
  secret is being rotated; the event is genuine if any of them matches. Other fields are ignored.
* `x-paystack-signature`: lowercase hex HMAC SHA512 of the raw body keyed with the secret key, no
  time. **Used only while no webhook signing secret is set.** Once `PAYSTACK_WEBHOOK_SECRET` is set,
  the timestamped header is required and the legacy one is ignored: otherwise an attacker holding
  one captured genuine body could strip the new header and replay it for ever.
* Every comparison is `hmac.compare_digest`; every `v1` is compared, with no early exit.

**What a webhook is.** Paystack sends no event id and, for transactions, sends events only for
successful ones (`charge.success`); a failed or abandoned payment is learned only by asking
(`fetch_transaction`). The dedupe key is therefore `<event name>:<object id>`: the same event
delivered again gives the same key, a charge and its refund give different ones. Every other kind of
event (`ping`, invoices, subscriptions, transfers, ...) is `IgnoredEvent`: a genuine event we do not
act on is answered 200 and stored nowhere, so Paystack does not resend it for 72 hours.

**Strict answers.** A Paystack answer is used only if its id is a whole number, its reference is
text, its amount is a whole number (a JSON float such as `5000.0` is refused, never rounded), its
currency is three upper-case letters (`ngn` is refused, never upper-cased) and its status is one
Paystack documents. The shape is checked before the status is read, so a `pending` answer with a
broken amount is an error too, not a quiet "try later". The refusal is a `ProviderError`: explicit,
retryable, never a silent success. `settle._malformed` stays as the second line behind this.

Amounts are kobo, integers all the way: Paystack takes the amount as the sub-unit (docs: NGN 100 is
`10000`), and no float is ever built.
"""

import hashlib
import hmac
import json
import re
import time
from collections.abc import Callable, Mapping
from urllib.parse import quote, urlparse

import httpx

from app.billing.provider import PROVIDER_FACTORIES
from app.billing.types import (
    BadEvent,
    Checkout,
    IgnoredEvent,
    ProviderError,
    ProviderEvent,
    ProviderTransaction,
    WebhookRejected,
)
from app.config import Settings

API_BASE = "https://api.paystack.co"
# A short client timeout: the payment row stays locked while the webhook re-reads the payment
# (S4 follow-up). Paystack's own test-mode webhook timeout is 30 seconds.
TIMEOUT = httpx.Timeout(connect=3.0, read=10.0, write=5.0, pool=3.0)
TOLERANCE_SECONDS = 300
MAX_SIGNATURES = 8  # one per secret during a rotation; more than this is not a Paystack header

_SIGNATURE_HEX = re.compile(r"[0-9a-f]{128}")
_TIMESTAMP = re.compile(r"[0-9]{1,12}")
_TRANSACTION_ID = re.compile(r"[0-9]{1,20}")  # Paystack: an unsigned 64-bit integer
_REFERENCE = re.compile(r"[A-Za-z0-9.=\-]{1,64}")  # Paystack: only -, ., = and alphanumerics
_CURRENCY = re.compile(r"[A-Z]{3}")

# Paystack's documented transaction statuses (docs: "Verify Payments") to the neutral ones. A state
# that is not final is `pending` (never `failed`: a failed payment can no longer be paid);
# `reversed` (refunded, or a chargeback was logged) is not a payment we can count, so it is failed.
_STATUSES = {
    "success": "successful",
    "failed": "failed",
    "reversed": "failed",
    "abandoned": "pending",
    "ongoing": "pending",
    "pending": "pending",
    "processing": "pending",
    "queued": "pending",
}
_REFUND_EVENTS = ("refund.failed", "refund.pending", "refund.processed", "refund.processing")
_DISPUTE_EVENTS = ("charge.dispute.create", "charge.dispute.remind", "charge.dispute.resolve")


class NotConfigured(ProviderError):
    """No usable key: nothing was sent. Raised before any connection is opened."""


def _whole_number(value: object) -> bool:
    return type(value) is int  # not a bool, not a float, not a digit string


def _u64(value: object) -> bool:
    """A Paystack id: a whole number that fits an unsigned 64-bit integer (the docs' own type)."""
    return type(value) is int and 0 <= value < 2**64


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _dig(value: object, *keys: str) -> object:
    for key in keys:
        value = value.get(key) if isinstance(value, dict) else None
    return value


class PaystackProvider:
    name = "paystack"
    currencies = ("NGN",)

    def __init__(
        self,
        *,
        secret_key: str = "",
        webhook_secret: str = "",
        live: bool = False,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._secret_key = secret_key.strip()
        self._webhook_secret = webhook_secret.strip()
        self._live = live
        self._transport = transport
        self._clock = clock

    def __repr__(self) -> str:  # never shows a key
        return f"PaystackProvider(live={self._live})"

    # ---- webhooks ----

    def verify_webhook(self, headers: Mapping[str, str], raw_body: bytes) -> None:
        if not isinstance(raw_body, bytes | bytearray):
            raise WebhookRejected("the body is not bytes")
        lowered = {str(key).lower(): value for key, value in headers.items()}
        if self._webhook_secret:
            self._verify_timestamped(lowered.get("paystack-signature"), bytes(raw_body))
        elif self._secret_key:
            self._verify_legacy(lowered.get("x-paystack-signature"), bytes(raw_body))
        else:
            raise WebhookRejected("no webhook secret or secret key is set")

    def _verify_timestamped(self, header: object, raw_body: bytes) -> None:
        if not isinstance(header, str):
            raise WebhookRejected("no Paystack-Signature header")
        stamp: str | None = None
        signatures: list[str] = []
        for part in header.split(","):
            key, equals, value = part.partition("=")
            key, value = key.strip(), value.strip()
            if not equals:
                continue
            if key == "t":
                if stamp is not None or not _TIMESTAMP.fullmatch(value):
                    raise WebhookRejected("bad timestamp in Paystack-Signature")
                stamp = value
            elif key == "v1":
                _key_id, colon, signature = value.partition(":")
                if colon:
                    signatures.append(signature)
        if stamp is None or not signatures or len(signatures) > MAX_SIGNATURES:
            raise WebhookRejected("unreadable Paystack-Signature")
        if abs(int(self._clock()) - int(stamp)) > TOLERANCE_SECONDS:
            raise WebhookRejected("Paystack-Signature timestamp is outside the 300-second window")
        expected = hmac.new(
            self._webhook_secret.encode(), f"{stamp}.".encode() + raw_body, hashlib.sha512
        ).digest()
        genuine = False
        for signature in signatures:  # all of them, no early exit
            if _SIGNATURE_HEX.fullmatch(signature):
                genuine |= hmac.compare_digest(expected, bytes.fromhex(signature))
        if not genuine:
            raise WebhookRejected("bad signature")

    def _verify_legacy(self, header: object, raw_body: bytes) -> None:
        if not isinstance(header, str) or not _SIGNATURE_HEX.fullmatch(header):
            raise WebhookRejected("no valid x-paystack-signature header")
        expected = hmac.new(self._secret_key.encode(), raw_body, hashlib.sha512).digest()
        if not hmac.compare_digest(expected, bytes.fromhex(header)):
            raise WebhookRejected("bad signature")

    def parse_event(self, raw_body: bytes) -> ProviderEvent:
        try:
            payload = json.loads(raw_body)
        except ValueError as exc:
            raise BadEvent("the body is not JSON") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("event"), str):
            raise BadEvent("the body is not a Paystack event")
        name, data = payload["event"], payload.get("data")
        if name == "charge.success":
            return self._charge_event(name, data)
        if name in _REFUND_EVENTS:
            return self._refund_event(name, data)
        if name in _DISPUTE_EVENTS:
            return self._dispute_event(name, data)
        raise IgnoredEvent("an event we do not act on")

    def _charge_event(self, name: str, data: object) -> ProviderEvent:
        tx_id, reference = _dig(data, "id"), _text(_dig(data, "reference"))
        amount, currency = _dig(data, "amount"), _dig(data, "currency")
        if not (
            _u64(tx_id)
            and reference
            and _whole_number(amount)
            and isinstance(currency, str)
            and _CURRENCY.fullmatch(currency)
        ):
            raise BadEvent("malformed charge event")
        return ProviderEvent(
            event_id=f"{name}:{tx_id}",
            kind="charge_succeeded",
            reference=reference,
            provider_tx_id=str(tx_id),
            amount_minor=amount,
            currency=currency,
            payer_email=_text(_dig(data, "customer", "email")),
            card_token=_reusable_token(_dig(data, "authorization")),
        )

    def _refund_event(self, name: str, data: object) -> ProviderEvent:
        refund_reference = _text(_dig(data, "refund_reference"))
        reference = _text(_dig(data, "transaction_reference"))
        amount, currency = _dig(data, "amount"), _dig(data, "currency")
        if isinstance(amount, str) and re.fullmatch(r"[0-9]{1,15}", amount):
            amount = int(amount)  # Paystack's refund events carry the amount as text
        if not (
            refund_reference
            and reference
            and _whole_number(amount)
            and isinstance(currency, str)
            and _CURRENCY.fullmatch(currency)
        ):
            raise BadEvent("malformed refund event")
        return ProviderEvent(
            event_id=f"{name}:{refund_reference}",
            kind="refund",
            reference=reference,
            provider_tx_id=refund_reference,  # the refund's own id: this event is about it
            amount_minor=amount,
            currency=currency,
            payer_email=_text(_dig(data, "customer", "email")),
        )

    def _dispute_event(self, name: str, data: object) -> ProviderEvent:
        dispute_id = _dig(data, "id")
        reference = _text(_dig(data, "transaction", "reference"))
        tx_id = _dig(data, "transaction", "id")
        amount, currency = _dig(data, "refund_amount"), _dig(data, "currency")
        if not (
            _u64(dispute_id)
            and _u64(tx_id)
            and reference
            and _whole_number(amount)
            and isinstance(currency, str)
            and _CURRENCY.fullmatch(currency)
        ):
            raise BadEvent("malformed dispute event")
        return ProviderEvent(
            event_id=f"{name}:{dispute_id}",
            kind="dispute",
            reference=reference,
            provider_tx_id=str(tx_id),
            amount_minor=amount,
            currency=currency,
            payer_email=_text(_dig(data, "customer", "email")),
        )

    # ---- calls to Paystack ----

    def start_checkout(self, payment, payer, return_url: str) -> Checkout:
        """`POST /transaction/initialize`: a hosted checkout for `payment`, in kobo."""
        if payment.provider != self.name or payment.currency not in self.currencies:
            raise ProviderError("Paystack takes naira payments made for it only")
        if not _whole_number(payment.amount_minor) or payment.amount_minor <= 0:
            raise ProviderError("the amount must be a positive whole number of kobo")
        if not _REFERENCE.fullmatch(payment.reference or ""):
            raise ProviderError("the payment reference is not one Paystack accepts")
        data = self._call(
            "POST",
            "/transaction/initialize",
            {
                "email": payer.email,
                "amount": str(payment.amount_minor),  # the docs type the amount as text
                "currency": payment.currency,
                "reference": payment.reference,
                "callback_url": return_url,
            },
        )
        url, reference = data.get("authorization_url"), data.get("reference")
        parsed = urlparse(url) if isinstance(url, str) else None
        if (
            parsed is None
            or parsed.scheme != "https"
            or not (parsed.hostname or "").endswith("paystack.com")
        ):
            raise ProviderError("Paystack gave no usable checkout address")
        if reference != payment.reference:
            raise ProviderError("Paystack answered for a different reference")
        return Checkout(url=url, reference=payment.reference)

    def fetch_transaction(self, provider_tx_id: str) -> ProviderTransaction:
        """`GET /transaction/:id`: the payment as Paystack reports it. `settle` compares it with
        what we asked for; this only refuses a shape it cannot read."""
        if not isinstance(provider_tx_id, str) or not _TRANSACTION_ID.fullmatch(provider_tx_id):
            raise ProviderError("that is not a Paystack transaction id")
        return _transaction(self._call("GET", f"/transaction/{provider_tx_id}"))

    def verify_reference(self, reference: str) -> ProviderTransaction:
        """`GET /transaction/verify/:reference`: the same, found by our own reference. For the
        payer's return and for settling a payment whose webhook never came (stage S8)."""
        if not isinstance(reference, str) or not _REFERENCE.fullmatch(reference):
            raise ProviderError("that is not a reference Paystack accepts")
        return _transaction(self._call("GET", f"/transaction/verify/{quote(reference, safe='')}"))

    def charge_saved_method(
        self,
        token: str,
        amount_minor: int,
        currency: str,
        reference: str,
        *,
        payer_email: str | None = None,
    ) -> ProviderTransaction:
        """`POST /transaction/charge_authorization`: charge a reusable card token. Paystack answers
        200 whether the card paid or not, so a decline is a `failed` transaction. A card that must
        be challenged again (`paused`) is `pending`: the payer must be sent to Paystack. A timeout
        or an error leaves the outcome unknown; the reference is ours and unique, so the caller
        resolves it with `verify_reference` before charging again (a repeat is refused as a
        duplicate reference)."""
        if currency not in self.currencies:
            raise ProviderError("Paystack takes naira only")
        if not _whole_number(amount_minor) or amount_minor <= 0:
            raise ProviderError("the amount must be a positive whole number of kobo")
        if not _text(token) or not _REFERENCE.fullmatch(reference or ""):
            raise ProviderError("a saved card token and a usable reference are needed")
        if not _text(payer_email):
            raise ProviderError("Paystack charges a saved card only with the email that made it")
        data = self._call(
            "POST",
            "/transaction/charge_authorization",
            {
                "authorization_code": token,
                "email": payer_email,
                "amount": str(amount_minor),
                "currency": currency,
                "reference": reference,
            },
        )
        if data.get("paused") is True:  # the card must be authorized again by the payer
            if data.get("reference") != reference:
                raise ProviderError("Paystack answered for a different reference")
            return ProviderTransaction(
                provider_tx_id=reference,  # Paystack gives no id yet; the reference is ours
                reference=reference,
                status="pending",
                amount_minor=amount_minor,
                currency=currency,
                payer_email=payer_email,
            )
        return _transaction(data)

    def refund(self, provider_tx_id: str, amount_minor: int) -> str:
        """`POST /refund`: refund `amount_minor` kobo; returns Paystack's refund id. The refund is
        queued, not done: `refund.processed` arrives later as an event."""
        if not isinstance(provider_tx_id, str) or not _TRANSACTION_ID.fullmatch(provider_tx_id):
            raise ProviderError("that is not a Paystack transaction id")
        if not _whole_number(amount_minor) or amount_minor <= 0:
            raise ProviderError("the amount must be a positive whole number of kobo")
        data = self._call(
            "POST", "/refund", {"transaction": provider_tx_id, "amount": amount_minor}
        )
        refund_id = data.get("id")
        if not _u64(refund_id):
            raise ProviderError("Paystack gave no refund id")
        if data.get("status") == "failed":
            raise ProviderError("Paystack reports the refund as failed")
        return str(refund_id)

    def _call(self, method: str, path: str, body: dict | None = None) -> dict:
        """One call to Paystack; returns its `data` object. Raises `NotConfigured` before any
        connection when the key is missing or of the wrong kind, and `ProviderError` for a
        transport failure, a timeout, any non-success answer or an answer that is not Paystack's
        shape. No message holds the key, an address, a body or an email."""
        wanted = "sk_live_" if self._live else "sk_test_"
        if not self._secret_key:
            raise NotConfigured("Paystack is not configured: PAYSTACK_SECRET_KEY is not set")
        if not self._secret_key.startswith(wanted):
            raise NotConfigured(
                f"PAYSTACK_SECRET_KEY is not a {wanted[3:-1]}-mode key while BILLING_LIVE is "
                f"{str(self._live).lower()}"
            )
        try:
            with httpx.Client(
                timeout=TIMEOUT,
                transport=self._transport,
                trust_env=False,
                follow_redirects=False,
            ) as http:
                response = http.request(
                    method,
                    API_BASE + path,
                    json=body,
                    headers={
                        "Authorization": f"Bearer {self._secret_key}",
                        "Accept": "application/json",
                    },
                )
        except httpx.TimeoutException:
            raise ProviderError("Paystack did not answer in time") from None
        except httpx.HTTPError as exc:  # its text can hold the address: only the type is kept
            raise ProviderError(f"the Paystack request failed ({type(exc).__name__})") from None
        return _data(response)


def _data(response: httpx.Response) -> dict:
    code = response.status_code
    if code == 401:
        raise ProviderError("Paystack refused the secret key (401)")
    if code == 429 or code >= 500:
        raise ProviderError(f"Paystack answered {code}")
    if code >= 400:
        raise ProviderError(f"Paystack refused the request ({code}){_reason(response)}")
    if code not in (200, 201):
        raise ProviderError(f"Paystack answered with an unexpected status ({code})")
    try:
        payload = response.json()
    except ValueError:
        raise ProviderError(f"Paystack answered {code} with something that is not JSON") from None
    if (
        not isinstance(payload, dict)
        or payload.get("status") is not True
        or not isinstance(payload.get("data"), dict)
    ):
        raise ProviderError("Paystack answered with an unexpected shape")
    return payload["data"]


def _reason(response: httpx.Response) -> str:
    """Paystack's own one-line reason for a refusal (": Email Address is required"), printable
    characters only and cut short, or nothing when the body has none."""
    try:
        message = response.json().get("message")
    except (ValueError, AttributeError):
        return ""
    if not isinstance(message, str):
        return ""
    text = re.sub(r"[^\x20-\x7e]", "", message)[:120].strip()
    return f": {text}" if text else ""


def _reusable_token(authorization: object) -> str | None:
    """The saved-card token, only when Paystack says the card can be charged again."""
    if _dig(authorization, "reusable") is True:
        return _text(_dig(authorization, "authorization_code"))
    return None


def _transaction(data: dict) -> ProviderTransaction:
    """A Paystack transaction object (verify, fetch or charge answer) as the neutral type. The
    shape is checked first, the status after: see the module note on strict answers."""
    tx_id, reference = data.get("id"), data.get("reference")
    amount, currency, status = data.get("amount"), data.get("currency"), data.get("status")
    if not _u64(tx_id):
        raise ProviderError("malformed Paystack answer: no whole-number transaction id")
    if not _text(reference):
        raise ProviderError("malformed Paystack answer: reference is not text")
    if not _whole_number(amount):
        raise ProviderError("malformed Paystack answer: amount is not a whole number of kobo")
    if not isinstance(currency, str) or not _CURRENCY.fullmatch(currency):
        raise ProviderError("malformed Paystack answer: currency is not three upper-case letters")
    if not isinstance(status, str) or status not in _STATUSES:
        raise ProviderError("malformed Paystack answer: unknown status")
    authorization = data.get("authorization")
    country = _dig(authorization, "country_code")
    return ProviderTransaction(
        provider_tx_id=str(tx_id),
        reference=reference,
        status=_STATUSES[status],
        amount_minor=amount,
        currency=currency,
        payer_email=_text(_dig(data, "customer", "email")),
        card_token=_reusable_token(authorization),
        card_country=country if isinstance(country, str) else None,
    )


def from_settings(settings: Settings) -> PaystackProvider:
    return PaystackProvider(
        secret_key=settings.paystack_secret_key,
        webhook_secret=settings.paystack_webhook_secret,
        live=settings.billing_live,
    )


PROVIDER_FACTORIES["paystack"] = from_settings
