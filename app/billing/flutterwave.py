"""The Flutterwave adapter, US dollars only (billing spec sections 5.3 to 5.6, stage S7).

Everything here speaks Flutterwave's v3 API (`https://api.flutterwave.com/v3`, a secret key as a
bearer token) and turns it into the neutral types of `types.py`. What each piece rests on is the
vendor's own page, read on 2026-11-17 (the SOP `docs/sop/2026-11-17-billing-flutterwave-adapter.md`
names each page and lists what is NOT verified live):

- `start_checkout`: `POST /v3/payments`, answer `data.link`
  (docs/collections-inflow/flutterwave-standard)
- `fetch_transaction`: `GET /v3/transactions/{id}/verify` (docs/transaction-verification)
- `verify_webhook`: the `verif-hash` header equals the secret hash set in the dashboard
  (docs/webhooks)
- `charge_saved_method`: `POST /v3/tokenized-charges` (docs/recurring-payments/card-tokenization)
- `refund`: `POST /v3/transactions/{id}/refund` (docs/payment-operations/refunds)

(All under `https://developer.flutterwave.com/`.)

**Dark until a key is set.** Without `FLUTTERWAVE_SECRET_KEY` (the default), every call that would
reach Flutterwave raises `ProviderError` BEFORE any network object is made, so no byte leaves the
process. `BILLING_LIVE` (shared with Paystack, `app/config.py`) says which kind of key the process
may hold: false, a TEST key only (it contains `_TEST`, so no real money can move); true, a live key
only. A key of the wrong kind stops the process at startup. Verifying a webhook and reading its
body need no network and work either way.

**Money is never a float.** A response is parsed with `parse_float=Decimal`; a dollar amount becomes
whole cents by exact decimal arithmetic (`_minor`), and the amount we send is written as a JSON
number straight from the integer cents (`_major_text`, `_encode`), so `9.99` goes out as `9.99` and
never as a binary float. An amount that is not a non-negative number of whole cents is handed to
`settle` unchanged (not an `int`), which refuses it as a malformed report; a currency is never
upper-cased or trimmed for the same reason (`usd` is malformed, not `USD`).

**What the vendor's word is worth.** The webhook only says which payment to look at. The decision is
`settle.process` on what `fetch_transaction` returns; this module never decides that a payment is
paid.

**An unknown outcome is not a failure and not a success.** A timeout or a 5xx on a call that moves
money (a charge, a refund) raises `OutcomeUnknown`: the vendor says (error-timeouts page) that a
timeout can mean "still processing", so the caller must look before it retries, never assume.
"""

import hmac
import json
import re
import secrets
from collections.abc import Mapping
from decimal import Decimal

import httpx

from app.billing.provider import PROVIDER_FACTORIES
from app.billing.types import (
    BadEvent,
    BillingError,
    Checkout,
    IgnoredEvent,
    NoRecord,
    OutcomeUnknown,
    ProviderError,
    ProviderEvent,
    ProviderTransaction,
    WebhookRejected,
)
from app.config import Settings

API_BASE = "https://api.flutterwave.com"
CURRENCY = "USD"
WEBHOOK_HEADER = "verif-hash"

# A call that only reads answers in a few seconds; the vendor itself waits 28 seconds before it
# reports a timeout on a charge (error-timeouts page), so a call that moves money waits longer than
# that and gets the vendor's own answer instead of giving up first. The webhook route holds a lock
# across `fetch_transaction` (S4 follow-up), which is why a read is cut at 10 seconds.
READ_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=5.0)
MONEY_TIMEOUT = httpx.Timeout(connect=5.0, read=35.0, write=10.0, pool=5.0)

# Payment kinds that must leave a reusable card token behind (the renewals charge it), so the
# checkout offers cards only ("only cards can be tokenized", card-tokenization page). A pack is a
# one-time payment: every method the account allows.
TOKEN_KINDS = ("plan", "renewal", "upgrade")

_MAX_MINOR = 10**12  # a billion dollars: anything bigger is not a price
_ID = re.compile(r"[0-9]{1,20}")
_TOKEN = re.compile(r"[A-Za-z0-9_\-]{8,200}")
# What Flutterwave's `data.status` is allowed to say (the verify and tokenized-charge samples show
# `successful`, `pending`; `failed` is the vendor's word for a declined attempt). Anything else is
# not guessed at: see `_transaction_from`.
_STATUSES = {"successful": "successful", "failed": "failed", "pending": "pending"}


class RequestRefused(ProviderError):
    """Flutterwave answered HTTP 400, 404 or 422: it understood the call and said no (the one
    documented body is `Wrong token or email passed`). `message` is its own words, cut to 200
    characters. A saved-card charge turns this into a `failed` result; other calls raise it."""

    def __init__(self, message: str, vendor_message: str = "") -> None:
        super().__init__(message)
        self.vendor_message = vendor_message


# ---------------------------------------------------------------- money, exactly


def _major_text(amount_minor: int) -> str:
    """Whole cents as the number Flutterwave takes: `1800` -> `18`, `999` -> `9.99`, `1850` ->
    `18.5`. Integer arithmetic only. Raises `ProviderError` for anything but a positive int."""
    if type(amount_minor) is not int or not 0 < amount_minor <= _MAX_MINOR:
        raise ProviderError("the amount must be a positive whole number of cents")
    dollars, cents = divmod(amount_minor, 100)
    return f"{dollars}.{cents:02d}".rstrip("0").rstrip(".")


def _minor(value: object) -> object:
    """A dollar amount from a Flutterwave answer as whole cents (an `int`), or `value` itself,
    untouched and not an `int`, when it is not a non-negative number of at most two decimal places
    (text, a bool, null, 18.005, a negative, NaN, a huge number). `settle` refuses a non-int amount
    as malformed, so a bad amount is a recorded refusal and never a payment."""
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        return value
    dollars = Decimal(value)
    # A refused number must not itself look like cents: an `int` goes back as a Decimal.
    refused = dollars if isinstance(value, int) else value
    if not dollars.is_finite() or len(dollars.as_tuple().digits) > 15:
        return refused
    cents = dollars * 100
    if cents != cents.to_integral_value() or not 0 <= cents <= _MAX_MINOR:
        return refused
    return int(cents)


def _encode(body: Mapping[str, object], amount_text: str | None = None) -> bytes:
    """`body` as JSON, with `amount` written as the exact decimal number `amount_text` (a binary
    float would turn `9.99` into `9.9900000000000002...`). A random marker takes the amount's place
    in the dump and is swapped for the number once, so no other field can collide with it."""
    if amount_text is None:
        return json.dumps(body).encode()
    marker = f"@@amount-{secrets.token_hex(8)}@@"
    text = json.dumps({**body, "amount": marker})
    quoted = json.dumps(marker)
    if text.count(quoted) != 1:  # unreachable unless a field held the marker; never send a guess
        raise ProviderError("could not encode the amount")
    return text.replace(quoted, amount_text).encode()


def _no_constant(name: str):
    raise ValueError(f"{name} is not a JSON number")


def _loads(raw: bytes | str):
    """Parse JSON with decimals instead of floats, and without NaN or Infinity."""
    return json.loads(raw, parse_float=Decimal, parse_constant=_no_constant)


# ---------------------------------------------------------------- reading answers


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _transaction_from(data: object) -> ProviderTransaction:
    """The neutral transaction in a Flutterwave `data` object (verify, tokenized charge).

    Raises `ProviderError` when there is no transaction id. A status that is not one of
    `_STATUSES` is passed on as it is: `settle._malformed` refuses it as an unknown status and
    records the refusal (an explicit "needs a human look" row, answered 200), instead of the event
    looping on 503 until Flutterwave's three retries run out. Flutterwave sends another webhook
    when a payment changes state, so a later `successful` one is still settled. Everything else is
    passed on as given for `settle._malformed` and the comparisons to judge: the reference, the
    currency (never normalised) and the amount (`_minor`)."""
    if not isinstance(data, dict):
        raise ProviderError("Flutterwave answered without a transaction")
    # Imported here, not at the top: `mismatch` imports `profile`, which imports `routing`, which
    # imports `app.models`, and `app.models` imports `app.billing.types`, which runs the package
    # `__init__` that imports this module. A top-level import would close that circle while
    # `app.models` is still loading.
    from app.billing.mismatch import normalize_card_country

    tx_id = data.get("id")
    if isinstance(tx_id, bool) or not isinstance(tx_id, (int, str)) or tx_id == "":
        raise ProviderError("Flutterwave answered without a transaction id")
    status = data.get("status")
    mapped = _STATUSES[status] if isinstance(status, str) and status in _STATUSES else status
    # `meta.authorization.redirect`: where the payer finishes a 3D Secure step (card-tokenization
    # page, the `pending` sample). Only an https link is passed on.
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    auth = meta.get("authorization") if isinstance(meta.get("authorization"), dict) else {}
    link = auth.get("redirect")
    card = data.get("card") if isinstance(data.get("card"), dict) else {}
    customer = data.get("customer") if isinstance(data.get("customer"), dict) else {}
    return ProviderTransaction(
        provider_tx_id=str(tx_id),
        reference=data.get("tx_ref"),  # type: ignore[arg-type]  # judged by settle
        status=mapped,  # type: ignore[arg-type]  # judged by settle
        amount_minor=_minor(data.get("amount")),  # type: ignore[arg-type]  # judged by settle
        currency=data.get("currency"),  # type: ignore[arg-type]  # judged by settle
        payer_email=_text(customer.get("email")),
        card_token=_text(card.get("token")),
        # `NIGERIA NG` (verify) and `NG` (webhook) both give `NG`; no card (a bank transfer, mobile
        # money) gives nothing.
        card_country=normalize_card_country(card.get("country")),
        payer_action_url=link if isinstance(link, str) and link.startswith("https://") else None,
    )


def _header(headers: Mapping[str, str], name: str) -> str:
    for key, value in headers.items():
        if isinstance(key, str) and key.lower() == name:
            return value if isinstance(value, str) else ""
    return ""


class FlutterwaveProvider:
    name = "flutterwave"
    currencies = (CURRENCY,)

    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None) -> None:
        self._settings = settings
        self._transport = transport  # tests only: the app never passes one

    # ---- the one place a request leaves the process ----

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: Mapping[str, object] | None = None,
        amount_text: str | None = None,
        params: Mapping[str, str] | None = None,
        money: bool = False,
    ) -> dict:
        """Call Flutterwave and return its decoded `{"status": "success", ...}` answer.

        Stops with `ProviderError` BEFORE building any network object when no secret key is set.
        Every failure is an explicit error naming what happened and never a
        key, a URL or a payer's data. `money=True` (a charge, a refund) turns a lost answer into
        `OutcomeUnknown`."""
        settings = self._settings
        key = settings.flutterwave_secret_key.strip()
        if not key:
            raise ProviderError("FLUTTERWAVE_SECRET_KEY is not set: no call was made")
        lost = OutcomeUnknown if money else ProviderError
        headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
        content = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            content = _encode(body, amount_text)
        failure = None
        try:
            with httpx.Client(
                base_url=API_BASE,
                timeout=MONEY_TIMEOUT if money else READ_TIMEOUT,
                transport=self._transport,
                trust_env=False,
                follow_redirects=False,
            ) as http:
                response = http.request(
                    method, path, headers=headers, content=content, params=params
                )
        except httpx.HTTPError as exc:  # the text can hold the URL; only the type is kept
            failure = type(exc).__name__
        if failure is not None:
            # Raised outside the `except`, so the error carries no `__context__`: the httpx
            # exception holds the request, and the request holds the Authorization header.
            raise lost(f"Flutterwave did not answer ({failure})")
        return self._answer(response, lost)

    @staticmethod
    def _answer(response: httpx.Response, lost: type[BillingError]) -> dict:
        code = response.status_code
        try:
            parsed = _loads(response.content)
        except ValueError:
            parsed = None
        answer = parsed if isinstance(parsed, dict) else {}
        message = _text(answer.get("message")) or ""
        if code >= 500:
            raise lost(f"Flutterwave answered HTTP {code}")
        if code in (401, 403):
            raise ProviderError(f"Flutterwave refused the secret key (HTTP {code})")
        if code == 429:
            raise ProviderError("Flutterwave rate limit (HTTP 429)")
        if code in (400, 404, 422):
            raise RequestRefused(
                f"Flutterwave refused the request (HTTP {code}): {message[:200]}", message[:200]
            )
        if code != 200:
            raise ProviderError(f"Flutterwave refused the request (HTTP {code}): {message[:200]}")
        # An HTTP 200 whose body we cannot use: on a call that moves money the vendor may already
        # have acted, so it is `OutcomeUnknown` there (`lost`), a plain error for a read.
        if parsed is None or not isinstance(parsed, dict):
            raise lost("Flutterwave answered with something that is not a JSON object")
        if answer.get("status") != "success":
            raise lost(f"Flutterwave did not report success: {message[:200]}")
        return answer

    # ---- the PaymentProvider interface ----

    def start_checkout(self, payment, payer, return_url: str) -> Checkout:
        if payment.currency != CURRENCY:
            raise ProviderError(f"Flutterwave takes {CURRENCY} here, not {payment.currency}")
        amount_text = _major_text(payment.amount_minor)
        if not isinstance(return_url, str) or not return_url:
            raise ProviderError("a return URL is required")
        customer = {"email": payer.email}
        if payer.name:
            customer["name"] = payer.name
        body: dict[str, object] = {
            "tx_ref": payment.reference,
            "currency": CURRENCY,
            "redirect_url": return_url,
            "customer": customer,
        }
        if payment.kind in TOKEN_KINDS:
            body["payment_options"] = "card"
        answer = self._request("POST", "/v3/payments", body=body, amount_text=amount_text)
        data = answer.get("data")
        link = data.get("link") if isinstance(data, dict) else None
        if not isinstance(link, str) or not link.startswith("https://"):
            raise ProviderError("Flutterwave gave no checkout link")
        return Checkout(url=link, reference=payment.reference)

    def verify_webhook(self, headers: Mapping[str, str], raw_body: bytes) -> None:
        """The `verif-hash` header must equal the secret hash we set in the dashboard. It is a
        shared secret, not a signature of the body (webhooks page), so `raw_body` is not read; the
        payment is re-fetched before it counts. No secret hash configured rejects everything: an
        empty setting never matches an empty header."""
        secret = self._settings.flutterwave_secret_hash
        if not secret:
            raise WebhookRejected("no secret hash is configured")
        given = _header(headers, WEBHOOK_HEADER)
        if not given or not hmac.compare_digest(given.encode(), secret.encode()):
            raise WebhookRejected("the verif-hash header is missing or wrong")

    def parse_event(self, raw_body: bytes) -> ProviderEvent:
        try:
            body = _loads(raw_body)
        except ValueError as exc:
            raise BadEvent("the body is not JSON") from exc
        if not isinstance(body, dict):
            raise BadEvent("the body is not a JSON object")
        if body.get("event") == "charge.completed":
            return self._charge_event(body.get("data"))
        if "event" not in body and "TransactionId" in body and "AmountRefunded" in body:
            return self._refund_event(body)
        if "event" in body:  # a genuine kind we do not act on: transfers, subscriptions, ...
            raise IgnoredEvent("an event this adapter does not act on")
        raise BadEvent("the body is not an event")

    @staticmethod
    def _charge_event(data: object) -> ProviderEvent:
        """`charge.completed`: the vendor's sample (`data.id`, `tx_ref`, `status`, `amount`,
        `currency`, `customer.email`, `card.token`). A `failed` status is a failed charge; any other
        word is passed on as a claim of success, which `settle` checks against the provider (so a
        `pending` one waits and a wrong one is refused there, never believed here)."""
        if not isinstance(data, dict):
            raise BadEvent("charge.completed carries no data")
        tx_id, reference, status = data.get("id"), data.get("tx_ref"), data.get("status")
        if isinstance(tx_id, bool) or not isinstance(tx_id, (int, str)) or tx_id == "":
            raise BadEvent("charge.completed carries no transaction id")
        if not _text(reference) or not _text(status):
            raise BadEvent("charge.completed carries no reference or status")
        card = data.get("card") if isinstance(data.get("card"), dict) else {}
        customer = data.get("customer") if isinstance(data.get("customer"), dict) else {}
        amount = _minor(data.get("amount"))
        currency = data.get("currency")
        return ProviderEvent(
            # The same delivery again gives the same id; a failed attempt and a later success of
            # one transaction give different ones.
            event_id=f"charge.completed:{tx_id}:{status}",
            kind="charge_failed" if status == "failed" else "charge_succeeded",
            reference=reference,  # type: ignore[arg-type]
            provider_tx_id=str(tx_id),
            # The event's amount and currency are claims that nothing reads (`settle` judges the
            # provider's own report), so a malformed one is carried as zero or empty.
            amount_minor=amount if type(amount) is int else 0,
            currency=currency if isinstance(currency, str) else "",
            payer_email=_text(customer.get("email")),
            card_token=_text(card.get("token")),
        )

    @staticmethod
    def _refund_event(body: dict) -> ProviderEvent:
        """A refund notice (the refunds page sample: `id`, `AmountRefunded`, `status`,
        `TransactionId`). It carries no `tx_ref`, so the reference is a placeholder naming the
        transaction; `settle` stores a refund and leaves it for S9 without looking it up."""
        refund_id, tx_id, status = body.get("id"), body.get("TransactionId"), body.get("status")
        if isinstance(tx_id, bool) or not isinstance(tx_id, (int, str)) or tx_id == "":
            raise BadEvent("the refund notice carries no transaction id")
        if isinstance(refund_id, bool) or not isinstance(refund_id, (int, str)) or refund_id == "":
            raise BadEvent("the refund notice carries no refund id")
        amount = _minor(body.get("AmountRefunded"))
        return ProviderEvent(
            event_id=f"refund:{refund_id}:{status if isinstance(status, str) else ''}",
            kind="refund",
            reference=f"flw-tx-{tx_id}",
            provider_tx_id=str(tx_id),
            amount_minor=amount if type(amount) is int else 0,
            currency="",
            refund_failed=isinstance(status, str) and status.strip().lower() == "failed",
        )

    def fetch_transaction(self, provider_tx_id: str) -> ProviderTransaction:
        """Re-read one payment: `GET /v3/transactions/{id}/verify`. The id is the number in the
        webhook's `data.id`; anything else is refused before it can shape a URL."""
        if not isinstance(provider_tx_id, str) or not _ID.fullmatch(provider_tx_id):
            raise ProviderError("the transaction id is not a Flutterwave transaction id")
        answer = self._request("GET", f"/v3/transactions/{provider_tx_id}/verify")
        return _transaction_from(answer.get("data"))

    def read_verify_data(self, provider_tx_id: str) -> dict:
        """The raw `data` object of the verify answer, for `scripts/flutterwave_test_run.py` only
        (it prints a few of its fields as evidence of the currency and amounts Flutterwave used).
        The app never reads it: only `fetch_transaction` decides anything."""
        if not isinstance(provider_tx_id, str) or not _ID.fullmatch(provider_tx_id):
            raise ProviderError("the transaction id is not a Flutterwave transaction id")
        data = self._request("GET", f"/v3/transactions/{provider_tx_id}/verify").get("data")
        if not isinstance(data, dict):
            raise ProviderError("Flutterwave answered without a transaction")
        return data

    def fetch_by_reference(self, reference: str) -> ProviderTransaction:
        """Look a payment up by our own reference (`GET /v3/transactions/verify_by_reference`),
        the vendor's advice after a timeout on a charge."""
        if not isinstance(reference, str) or not reference:
            raise ProviderError("a reference is required")
        try:
            answer = self._request(
                "GET", "/v3/transactions/verify_by_reference", params={"tx_ref": reference}
            )
        except RequestRefused as exc:
            # HTTP 400, 404 or 422 on a lookup: Flutterwave understood it and has no transaction
            # under this reference (it makes one only when the payer tries to pay). Not a failure
            # to ask: 401, 403, 429, 5xx and a lost connection stay plain `ProviderError`.
            raise NoRecord(str(exc)) from None
        return _transaction_from(answer.get("data"))

    def charge_saved_method(
        self,
        token: str,
        amount_minor: int,
        currency: str,
        reference: str,
        *,
        payer_email: str | None = None,
    ) -> ProviderTransaction:
        """Charge a saved card token: `POST /v3/tokenized-charges` (token, currency, amount, email,
        tx_ref). **Documented, not verified live.** Two facts from the vendor's page shape this:
        the token is tied to the email it was saved under, so `payer_email` is required; and a
        charge is authenticated with 3D Secure by default, so the payer must complete a step.

        What comes back (the caller treats anything but `successful` as "not paid yet"):

        * `pending` + `payer_action_url`: the charge is **open at Flutterwave, waiting for the
          payer's 3D Secure step** (`data.meta.authorization.redirect`), and it can still succeed
          and settle by webhook. Send the payer THAT link. **Never open a second checkout for the
          same period while it is open**: the payer could complete both and pay twice. (NOAUTH,
          which skips the step, needs Flutterwave support to enable it on the account.)
        * `failed` with `failure_reason` and a `refused-...` id: Flutterwave said no to the call
          (HTTP 400, for example `Wrong token or email passed`); nothing was charged; a dead token
          will keep failing, so do not retry it every cycle. `failed` with a real id: a declined
          charge.
        * any other status word: passed on unchanged; treat it as "not paid, look at it".

        **This answer never grants hours**: it is the vendor's reply to the charge, not a verified
        payment (only `settle`, after `fetch_transaction`, marks one paid).

        If the call times out, the charge may still be happening: it is looked up by our reference
        and its state returned; if that fails too, `OutcomeUnknown` (not a `ProviderError`)."""
        if currency != CURRENCY:
            raise ProviderError(f"Flutterwave takes {CURRENCY} here, not {currency}")
        amount_text = _major_text(amount_minor)
        if not isinstance(token, str) or not _TOKEN.fullmatch(token):
            raise ProviderError("the saved card token is not usable")
        if not isinstance(payer_email, str) or not payer_email.strip():
            raise ProviderError("the email the card was saved under is required")
        if not isinstance(reference, str) or not reference:
            raise ProviderError("a reference is required")
        body = {
            "token": token,
            "currency": CURRENCY,
            "email": payer_email,
            "tx_ref": reference,
        }
        try:
            answer = self._request(
                "POST", "/v3/tokenized-charges", body=body, amount_text=amount_text, money=True
            )
        except RequestRefused as exc:
            return ProviderTransaction(
                provider_tx_id=f"refused-{reference}"[:128],  # not a vendor id: no transaction
                reference=reference,
                status="failed",
                amount_minor=amount_minor,
                currency=CURRENCY,
                payer_email=payer_email,
                failure_reason=exc.vendor_message or "Flutterwave refused the charge",
            )
        except OutcomeUnknown:
            return self._look_up_lost_charge(reference)
        try:
            return _transaction_from(answer.get("data"))
        except ProviderError:  # a 200 with no usable transaction: the charge may have happened
            return self._look_up_lost_charge(reference)

    def _look_up_lost_charge(self, reference: str) -> ProviderTransaction:
        """After a charge whose answer is lost or unusable: ask Flutterwave about our reference
        and return what it says; if that fails too, `OutcomeUnknown`."""
        try:
            return self.fetch_by_reference(reference)
        except ProviderError:
            raise OutcomeUnknown(
                "the charge may or may not have gone through: check Flutterwave for "
                "this reference before charging again"
            ) from None

    def refund(self, provider_tx_id: str, amount_minor: int, *, reference: str) -> str:
        """Refund part or all of a payment: `POST /v3/transactions/{id}/refund` with the amount.
        Returns the refund's id (`data.id`). A refund the vendor accepts is only *initiated*: card
        refunds finish in 3 to 15 days (refunds page), so the id is a receipt, not proof the money
        has arrived. A timeout raises `OutcomeUnknown`: look at the dashboard before asking again,
        or the payer may be refunded twice. **Flutterwave v3 documents no idempotency key**, so
        `reference` (our key for this refund) cannot make a retry safe here, unlike Paystack's: it
        is sent as the refund's `comments`, only so the refund can be found in the dashboard."""
        if not isinstance(provider_tx_id, str) or not _ID.fullmatch(provider_tx_id):
            raise ProviderError("the transaction id is not a Flutterwave transaction id")
        amount_text = _major_text(amount_minor)
        if not isinstance(reference, str) or not 0 < len(reference) <= 100:
            raise ProviderError("a refund reference is 1 to 100 characters")
        body: dict[str, object] = {"comments": reference}
        answer = self._request(
            "POST",
            f"/v3/transactions/{provider_tx_id}/refund",
            body=body,
            amount_text=amount_text,
            money=True,
        )
        data = answer.get("data")
        refund_id = data.get("id") if isinstance(data, dict) else None
        if isinstance(refund_id, bool) or not isinstance(refund_id, (int, str)) or refund_id == "":
            # Accepted, but we cannot tell which refund it is: it may well exist.
            raise OutcomeUnknown(
                "Flutterwave answered the refund without a refund id: it may have been made, "
                "check the dashboard before asking again"
            )
        return str(refund_id)


def build(settings: Settings) -> FlutterwaveProvider:
    return FlutterwaveProvider(settings)


PROVIDER_FACTORIES["flutterwave"] = build
