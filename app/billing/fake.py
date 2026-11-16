"""The in-memory payment provider the tests use, and the only one CI ever talks to.

It is deterministic and never registered in `provider.PROVIDER_FACTORIES` by the app: a test puts it
there itself, so it cannot be reached in production. A webhook is genuine when `X-Fake-Signature`
holds the hex HMAC-SHA256 of the exact body under `secret`; because the signature covers the bytes,
a body that was re-encoded on the way in fails verification. It remembers every call so a test can
prove what the inbox did: `verified_bodies` (the bytes `verify_webhook` was given), `fetched` (the
transaction ids re-read), `charges` and `refunds`.
"""

import hashlib
import hmac
import json
from collections.abc import Mapping

from app.billing.types import (
    BadEvent,
    Checkout,
    ProviderError,
    ProviderEvent,
    ProviderTransaction,
    WebhookRejected,
)

SIGNATURE_HEADER = "X-Fake-Signature"


class FakeProvider:
    name = "fake"
    currencies = ("NGN", "USD")

    def __init__(self, secret: str = "fake-webhook-secret") -> None:
        self.secret = secret
        self.transactions: dict[str, ProviderTransaction] = {}
        self.fetch_error: Exception | None = None
        self.verified_bodies: list[bytes] = []
        self.fetched: list[str] = []
        self.charges: list[tuple[str, int, str, str]] = []
        self.refunds: list[tuple[str, int]] = []

    # ---- helpers for tests ----

    def sign(self, raw_body: bytes) -> str:
        return hmac.new(self.secret.encode(), raw_body, hashlib.sha256).hexdigest()

    def signed_headers(self, raw_body: bytes) -> dict[str, str]:
        return {SIGNATURE_HEADER: self.sign(raw_body)}

    def add_transaction(self, tx: ProviderTransaction) -> ProviderTransaction:
        self.transactions[tx.provider_tx_id] = tx
        return tx

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
            }
        ).encode()

    # ---- the PaymentProvider interface ----

    def start_checkout(self, payment, payer, return_url: str) -> Checkout:
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
        self, token: str, amount_minor: int, currency: str, reference: str
    ) -> ProviderTransaction:
        self.charges.append((token, amount_minor, currency, reference))
        return self.add_transaction(
            ProviderTransaction(
                provider_tx_id=f"fake-tx-{len(self.transactions) + 1}",
                reference=reference,
                status="successful",
                amount_minor=amount_minor,
                currency=currency,
            )
        )

    def refund(self, provider_tx_id: str, amount_minor: int) -> str:
        self.refunds.append((provider_tx_id, amount_minor))
        return f"fake-refund-{len(self.refunds)}"
