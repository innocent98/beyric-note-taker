"""The in-memory payment provider the tests use, and the only one CI ever talks to.

It is deterministic and never registered in `provider.PROVIDER_FACTORIES` by the app: a test puts it
there itself, so it cannot be reached in production. A webhook is genuine when `X-Fake-Signature`
holds the hex HMAC-SHA256 of the exact body under `secret`; because the signature covers the bytes,
a body that was re-encoded on the way in fails verification. It remembers every call so a test can
prove what the inbox did: `verified_bodies` (the bytes `verify_webhook` was given), `fetched` (the
transaction ids re-read), `charges` and `refunds`.

For the subscription tests (stage S8) it is also scriptable: `complete` is "the payer paid on the
hosted page" (it records the successful transaction the provider would report), `charge_outcome`
decides what the next saved-card charges answer (`successful`, `failed`, `pending` with a payer
link, or `error` for an answer that never came), and `verify_reference` looks a payment up by our
reference like the real adapters do.
"""

import hashlib
import hmac
import json
from collections.abc import Mapping

from app.billing.types import (
    BadEvent,
    ChargeBlocked,
    Checkout,
    NothingMoved,
    OutcomeUnknown,
    ProviderError,
    ProviderEvent,
    ProviderTransaction,
    WebhookRejected,
)

SIGNATURE_HEADER = "X-Fake-Signature"


class FakeProvider:
    name = "fake"
    currencies = ("NGN", "USD")

    def __init__(self, secret: str = "fake-webhook-secret", name: str = "fake") -> None:
        # A test can name it after the real provider the route table picks (`paystack`,
        # `flutterwave`), because payments carry the route's provider name and settlement checks
        # that the provider asked is the payment's own.
        self.name = name
        self.secret = secret
        self.transactions: dict[str, ProviderTransaction] = {}
        self.fetch_error: Exception | None = None
        self.checkout_error: Exception | None = None
        self.verified_bodies: list[bytes] = []
        self.lookups: list[str] = []  # the reference of every `verify_reference` call
        self.checkouts: list[str] = []  # the reference of every hosted checkout started
        self.fetched: list[str] = []
        self.charges: list[tuple[str, int, str, str]] = []
        self.refunds: list[tuple[str, int]] = []  # refunds that EXIST at the fake provider
        self.refund_calls: list[tuple[str, int, str]] = []  # every call, with its reference
        self.refund_error: Exception | None = None
        self.refund_acts_on_error: bool = False
        # "successful", or "failed", "pending", "queued", "error", "unknown", "refused", "blocked"
        self.charge_outcome = "successful"
        self.payer_action_url = "https://fake.test/3ds/open-charge"
        self.charge_token: str | None = None  # a token the charges' transactions carry back

    # ---- helpers for tests ----

    def sign(self, raw_body: bytes) -> str:
        return hmac.new(self.secret.encode(), raw_body, hashlib.sha256).hexdigest()

    def signed_headers(self, raw_body: bytes) -> dict[str, str]:
        return {SIGNATURE_HEADER: self.sign(raw_body)}

    def add_transaction(self, tx: ProviderTransaction) -> ProviderTransaction:
        self.transactions[tx.provider_tx_id] = tx
        return tx

    def complete(
        self,
        payment,
        *,
        status: str = "successful",
        tx_id: str | None = None,
        amount_minor: int | None = None,
        currency: str | None = None,
        reference: str | None = None,
        card_token: str | None = None,
        payer_email: str | None = None,
        card_country: str | None = None,
    ) -> ProviderTransaction:
        """The payer finished (or abandoned) the hosted checkout for `payment`: records what the
        provider would now report about it. Every field can be set wrong on purpose."""
        return self.add_transaction(
            ProviderTransaction(
                provider_tx_id=tx_id or f"fake-tx-{len(self.transactions) + 1}",
                reference=reference or payment.reference,
                status=status,
                amount_minor=payment.amount_minor if amount_minor is None else amount_minor,
                currency=currency or payment.currency,
                payer_email=payer_email,
                card_token=card_token,
                card_country=card_country,
            )
        )

    def event_for(
        self, tx: ProviderTransaction, *, kind: str = "charge_succeeded"
    ) -> ProviderEvent:
        """The webhook event a provider would send about `tx` (a claim, like any webhook)."""
        return ProviderEvent(
            event_id=f"evt-{kind}-{tx.provider_tx_id}",
            kind=kind,
            reference=tx.reference,
            provider_tx_id=tx.provider_tx_id,
            amount_minor=tx.amount_minor,
            currency=tx.currency,
        )

    @staticmethod
    def event_body(event: ProviderEvent) -> bytes:
        return json.dumps(
            {
                "id": event.event_id,
                "kind": event.kind,
                "reference": event.reference,
                "tx_id": event.provider_tx_id,
                "amount_minor": event.amount_minor,
                "currency": event.currency,
                "payer_email": event.payer_email,
                "card_token": event.card_token,
                "refund_failed": event.refund_failed,
            }
        ).encode()

    # ---- the PaymentProvider interface ----

    def start_checkout(self, payment, payer, return_url: str) -> Checkout:
        if self.checkout_error is not None:
            raise self.checkout_error
        self.checkouts.append(payment.reference)
        return Checkout(
            url=f"https://fake.test/pay/{payment.reference}", reference=payment.reference
        )

    def verify_webhook(self, headers: Mapping[str, str], raw_body: bytes) -> None:
        self.verified_bodies.append(raw_body)
        given = headers.get(SIGNATURE_HEADER, "")
        if not hmac.compare_digest(given.encode(), self.sign(raw_body).encode()):
            raise WebhookRejected("bad signature")

    def parse_event(self, raw_body: bytes) -> ProviderEvent:
        try:
            data = json.loads(raw_body)
            return ProviderEvent(
                event_id=data["id"],
                kind=data["kind"],
                reference=data["reference"],
                provider_tx_id=data["tx_id"],
                amount_minor=data["amount_minor"],
                currency=data["currency"],
                payer_email=data.get("payer_email"),
                card_token=data.get("card_token"),
                refund_failed=data.get("refund_failed") is True,
            )
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise BadEvent("unreadable event") from exc

    def fetch_transaction(self, provider_tx_id: str) -> ProviderTransaction:
        self.fetched.append(provider_tx_id)
        if self.fetch_error is not None:
            raise self.fetch_error
        tx = self.transactions.get(provider_tx_id)
        if tx is None:
            raise ProviderError(f"no such transaction {provider_tx_id}")
        return tx

    def charge_saved_method(
        self,
        token: str,
        amount_minor: int,
        currency: str,
        reference: str,
        *,
        payer_email: str | None = None,
    ) -> ProviderTransaction:
        self.charges.append((token, amount_minor, currency, reference))
        if self.charge_outcome == "error":
            raise ProviderError("the charge call got no answer")
        if self.charge_outcome == "blocked":  # our key or a rate limit: not the customer's failure
            raise ChargeBlocked("the provider refused our key or rate limited us")
        if self.charge_outcome == "refused":  # a 4xx, a missing key: the call made no charge
            raise NothingMoved("the provider refused the charge call")
        if self.charge_outcome == "unknown":  # the newer contract: a money call with no answer
            raise OutcomeUnknown("the charge call got no answer")
        if self.charge_outcome == "queued":  # accepted, nothing for the payer to do, no verdict yet
            tx = ProviderTransaction(
                provider_tx_id=reference,
                reference=reference,
                status="pending",
                amount_minor=amount_minor,
                currency=currency,
            )
            self.transactions[reference] = tx
            return tx
        if self.charge_outcome == "pending":  # the card's bank wants the payer: a link, no id yet
            tx = ProviderTransaction(
                provider_tx_id=reference,
                reference=reference,
                status="pending",
                amount_minor=amount_minor,
                currency=currency,
                payer_action_url=self.payer_action_url,
            )
            self.transactions[reference] = tx
            return tx
        return self.add_transaction(
            ProviderTransaction(
                provider_tx_id=f"fake-tx-{len(self.transactions) + 1}",
                reference=reference,
                status=self.charge_outcome,
                amount_minor=amount_minor,
                currency=currency,
                card_token=self.charge_token,
            )
        )

    def verify_reference(self, reference: str) -> ProviderTransaction:
        """The payment under OUR reference, as the provider reports it now."""
        self.lookups.append(reference)
        if self.fetch_error is not None:
            raise self.fetch_error
        for tx in reversed(list(self.transactions.values())):  # the newest report wins
            if tx.reference == reference:
                return tx
        raise ProviderError(f"no transaction under reference {reference}")

    def refund(self, provider_tx_id: str, amount_minor: int, *, reference: str) -> str:
        """Scriptable: `refund_error` (an exception to raise: `ProviderError` for a refusal,
        `OutcomeUnknown` for a call that got no answer; raised AFTER the call is recorded, like a
        provider that did the work and lost the answer, which is what `refund_acts_on_error` says)
        and `refund_acts_on_error` (the refund exists although the call raised)."""
        if not isinstance(reference, str) or not reference:
            raise ProviderError("a refund reference is mandatory")
        self.refund_calls.append((provider_tx_id, amount_minor, reference))
        if self.refund_error is not None:
            if self.refund_acts_on_error:
                self.refunds.append((provider_tx_id, amount_minor))
            raise self.refund_error
        self.refunds.append((provider_tx_id, amount_minor))
        return f"fake-refund-{len(self.refunds)}"
