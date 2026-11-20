"""Drive the cancel-a-checkout route over real HTTP and save every exchange for the guide.

The route is `POST /api/billing/payments/{reference}/cancel`.

    python scripts/capture_billing_cancel_checkout.py \
        postgresql+psycopg://USER:PASS@localhost:PORT/postgres

Same setup as `scripts/capture_billing_subscriptions.py`: real migrations (the seeded plan and pack
are the real rows), the real app under uvicorn, real HTTP, two copies of the app on one database
(billing on and open to every account, and billing off). Files go to
`docs/fe-captures/billing-cancel-checkout/NN-name.json`. Passwords are placeholders; the people are
placeholders (`ada@example.test`, `ben@example.test`).

**Real:** the app, the guard (login and CSRF), the route, the settlement rules, the grants, the
webhook route and its signature check, every error shape.

**NOT real, and each capture says so where it matters:** there is no real payment provider. The
provider is `app.billing.fake.FakeProvider` registered under the names `flutterwave` and
`paystack`; what it "reports" about a payment is scripted by this file (nothing paid, paid, failed,
no record, down). What a real Paystack or Flutterwave answers to a lookup of an abandoned page is
**not verified live**.
"""

import json
import shutil
import sys
from datetime import UTC, datetime

import capture_calendar_connect as base  # reads sys.argv at import (the admin database URL)

OUT = base.ROOT / "docs" / "fe-captures" / "billing-cancel-checkout"
base.OUT = OUT

import httpx  # noqa: E402
from sqlalchemy import create_engine, select, text, update  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.billing.fake import FakeProvider  # noqa: E402
from app.billing.provider import PROVIDER_FACTORIES  # noqa: E402
from app.billing.types import NoRecord, ProviderError  # noqa: E402
from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import HourGrant, Payment, Subscription, User  # noqa: E402
from app.passwords import hash_password  # noqa: E402
from app.services import get_bot_provider  # noqa: E402

Recorder = base.Recorder
PASSWORD = "-".join(["capture", "person", "password"])  # a placeholder, scratch database only
ADA, BEN = "ada@example.test", "ben@example.test"
USD = FakeProvider(name="flutterwave")
NGN = FakeProvider(name="paystack")
PROVIDER_FACTORIES["flutterwave"] = lambda settings: USD
PROVIDER_FACTORIES["paystack"] = lambda settings: NGN


def person(engine, email: str) -> None:
    with Session(engine) as session:
        session.add(
            User(
                email=email,
                password_hash=hash_password(PASSWORD),
                email_verified_at=datetime.now(UTC),
            )
        )
        session.commit()


def signed_in(server_base: str, who: str, email: str) -> Recorder:
    client = Recorder(server_base, who)
    base.login(client, f"login-{who.split()[0]}", email, PASSWORD)
    return client


def payment_by_reference(engine, reference: str) -> Payment:
    with Session(engine) as session:
        return session.scalars(select(Payment).where(Payment.reference == reference)).one()


def webhook(server_base: str, name: str, provider: FakeProvider, tx, note: str):
    """Deliver the provider's webhook for `tx` to the real route, signed like the fake signs."""
    body = provider.event_body(provider.event_for(tx))
    headers = {"Content-Type": "application/json", "X-Fake-Signature": provider.sign(body)}
    response = httpx.post(
        f"{server_base}/api/billing/webhooks/{provider.name}", content=body, headers=headers
    )
    Recorder.count += 1
    record = {
        "note": note,
        "client": "the provider",
        "request": {
            "method": "POST",
            "path": f"/api/billing/webhooks/{provider.name}",
            "headers": {
                k: ("<signature>" if k == "X-Fake-Signature" else v) for k, v in headers.items()
            },
            "body": json.loads(body),
        },
        "response": {"status": response.status_code, "body": response.json()},
    }
    (OUT / f"{Recorder.count:02d}-{name}.json").write_text(
        json.dumps(record, indent=2, ensure_ascii=False) + "\n"
    )
    print(f"{Recorder.count:02d}-{name:44} POST  webhook -> {response.status_code}")


def rows(engine) -> dict:
    with Session(engine) as session:
        return {
            "payments": [
                {"kind": p.kind, "status": p.status, "amount_minor": p.amount_minor}
                for p in session.scalars(select(Payment).order_by(Payment.id))
            ],
            "hour_grants": [
                {"kind": g.kind, "seconds": g.seconds}
                for g in session.scalars(select(HourGrant).order_by(HourGrant.id))
            ],
            "subscriptions": [
                {"plan": s.plan_code, "status": s.status}
                for s in session.scalars(select(Subscription).order_by(Subscription.id))
            ],
        }


def run(engine, on, off) -> None:
    for email in (ADA, BEN):
        person(engine, email)

    off_ada = signed_in(off, "ada flag-off", ADA)
    off_ada.call(
        "off-cancel",
        "POST",
        "/api/billing/payments/any-reference/cancel",
        note="`BILLING_ENABLED` false: 404 `not_found`, and nothing is asked of any provider.",
    )

    ada = signed_in(on, "ada billing-on", ADA)
    ben = signed_in(on, "ben billing-on", BEN)
    ada.call(
        "country-us",
        "PUT",
        "/api/billing/profile/country",
        body={"billing_country": "US"},
        note="Dollars through the provider named `flutterwave`.",
    )
    started = ada.call(
        "checkout-plan",
        "POST",
        "/api/billing/checkout",
        body={"kind": "plan", "code": "plan_20h"},
        note="A plan checkout the person will not finish.",
    ).json()
    ref = started["payment"]["reference"]
    ada.call(
        "subscription-with-waiting-payment",
        "GET",
        "/api/billing/subscription",
        note="`pending_payment` is what the banner shows: 'You started a payment and have not "
        "finished it'.",
    )

    ada.call(
        "cancel-no-csrf",
        "POST",
        f"/api/billing/payments/{ref}/cancel",
        csrf=None,
        note="No CSRF header: 403 `csrf_rejected`. Nothing changes.",
    )
    ada.call(
        "cancel-unknown-reference",
        "POST",
        "/api/billing/payments/pay_does_not_exist/cancel",
        note="404 `not_found`.",
    )
    ben.call(
        "cancel-someone-elses",
        "POST",
        f"/api/billing/payments/{ref}/cancel",
        note="Ben tries Ada's reference: the same plain 404 `not_found` as an unknown one. The "
        "payment is not touched and the provider is not asked.",
    )
    ada.call(
        "cancel-provider-has-no-record",
        "POST",
        f"/api/billing/payments/{ref}/cancel",
        note="The (fake) provider raises a plain `ProviderError` (it cannot give an answer): 502 "
        "`provider_error` and NOTHING changes. (A provider that says it has NO record is the "
        "separate `NoRecord` case, capture 32.)",
    )
    USD.fetch_error = ProviderError("the provider is down")
    ada.call(
        "cancel-provider-down",
        "POST",
        f"/api/billing/payments/{ref}/cancel",
        note="The provider cannot be asked at all: the same 502 `provider_error`, nothing changes.",
    )
    USD.fetch_error = None
    payment = payment_by_reference(engine, ref)
    USD.complete(payment, status="pending")
    ada.call(
        "cancel-abandoned",
        "POST",
        f"/api/billing/payments/{ref}/cancel",
        note="The provider reports the page unpaid (Paystack's `abandoned` is `pending` here): "
        "`result` `cancelled`, the payment is `failed` and has no `pay_url`.",
    )
    ada.call(
        "subscription-after-cancel",
        "GET",
        "/api/billing/subscription",
        note="`pending_payment` is null: the banner is gone. Nothing was granted.",
    )
    ada.call(
        "cancel-again",
        "POST",
        f"/api/billing/payments/{ref}/cancel",
        note="Cancelling again asks the provider again (a failed payment can still be paid) and "
        "ends the same.",
    )

    # ---- the page is paid after all ----
    again = ada.call(
        "checkout-plan-again",
        "POST",
        "/api/billing/checkout",
        body={"kind": "plan", "code": "plan_20h"},
        note="A new payment (the cancelled one is `failed`, so it is not reused).",
    ).json()
    ref2 = again["payment"]["reference"]
    USD.complete(payment_by_reference(engine, ref2), card_token="tok-capture-1", payer_email=ADA)
    ada.call(
        "cancel-paid-in-the-meantime",
        "POST",
        f"/api/billing/payments/{ref2}/cancel",
        note="The payer paid in another tab and no webhook has arrived: the provider says "
        "`successful`, so the payment is SETTLED (the plan and hours are granted) and the answer "
        "is `paid`. A paid payment is never cancelled.",
    )
    ada.call(
        "subscription-after-paid",
        "GET",
        "/api/billing/subscription",
        note="The plan is active with its 20 hours; no waiting payment.",
    )
    ada.call(
        "cancel-already-paid",
        "POST",
        f"/api/billing/payments/{ref2}/cancel",
        note="Already `succeeded`: answered `paid` without asking the provider; nothing changes.",
    )

    # ---- a pack ----
    pack = ada.call(
        "checkout-pack",
        "POST",
        "/api/billing/checkout",
        body={"kind": "pack", "code": "pack_10h"},
        note="A pack checkout (needs the live plan) the person will not finish.",
    ).json()
    ref3 = pack["payment"]["reference"]
    USD.complete(payment_by_reference(engine, ref3), status="failed")
    ada.call(
        "cancel-provider-says-failed",
        "POST",
        f"/api/billing/payments/{ref3}/cancel",
        note="The provider says the payment failed: `result` `failed`, the payment is `failed`.",
    )
    pack2 = ada.call(
        "checkout-pack-again",
        "POST",
        "/api/billing/checkout",
        body={"kind": "pack", "code": "pack_10h"},
        note="Another pack checkout.",
    ).json()
    ref4 = pack2["payment"]["reference"]
    paying = payment_by_reference(engine, ref4)
    USD.complete(paying, status="pending")
    ada.call(
        "cancel-pack",
        "POST",
        f"/api/billing/payments/{ref4}/cancel",
        note="A pack checkout the provider says is unpaid: `cancelled`.",
    )
    webhook(
        on,
        "webhook-cancelled-page-paid-later",
        USD,
        USD.complete(paying, card_token="tok-capture-2", payer_email=ADA),
        "The payer paid the OLD page after all and the provider's webhook arrives: the cancelled "
        "(`failed`) payment is still open to settlement, so it is paid and its hours are granted.",
    )
    ada.call(
        "payment-after-late-webhook",
        "GET",
        f"/api/billing/payments/{ref4}",
        note="`succeeded`: nothing was lost by cancelling.",
    )

    # ---- an upgrade or a renewal is not cancelled here ----
    stuck = ada.call(
        "checkout-pack-for-kind",
        "POST",
        "/api/billing/checkout",
        body={"kind": "pack", "code": "pack_10h"},
        note="A pack checkout whose KIND this capture then changes by SQL (to `upgrade`), only to "
        "show the refusal: no upgrade checkout is made here.",
    ).json()
    ref5 = stuck["payment"]["reference"]
    with Session(engine) as session:
        session.execute(update(Payment).where(Payment.reference == ref5).values(kind="upgrade"))
        session.commit()
    ada.call(
        "cancel-an-upgrade",
        "POST",
        f"/api/billing/payments/{ref5}/cancel",
        note="Only a plan or a pack checkout can be cancelled: 409 `cannot_cancel`.",
    )

    # ---- the provider has no record of the page (Flutterwave: none until a payer tries) ----
    empty = ada.call(
        "checkout-pack-no-record",
        "POST",
        "/api/billing/checkout",
        body={"kind": "pack", "code": "pack_10h"},
        note="A pack checkout whose page nobody opened.",
    ).json()
    ref6 = empty["payment"]["reference"]
    USD.fetch_error = NoRecord("no transaction was found")
    ada.call(
        "cancel-provider-has-no-transaction",
        "POST",
        f"/api/billing/payments/{ref6}/cancel",
        note="The provider is reachable and says it has no transaction under our reference "
        "(`NoRecord`; the real Flutterwave adapter raises it for HTTP 400, 404 or 422 on the "
        "lookup, tested on a scripted wire, NOT captured against Flutterwave): nobody paid, so it "
        "is closed. `result` `cancelled`, `payment.status` `failed`.",
    )
    USD.fetch_error = None

    base.note_file(
        "rows-at-the-end",
        "What the tables hold after all of the above (read from the database).",
        rows(engine),
    )


def main() -> None:
    if not sys.argv[1:]:
        sys.exit(__doc__)
    base.prepare_database()
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True)

    def settings_for(**extra) -> Settings:
        return Settings(
            _env_file=None,
            app_env="test",
            session_secret="capture-secret-" + "x" * 30,
            cookie_secure=False,
            database_url=base.SCRATCH_URL,
            public_base_url="https://notes.example.test",
            token_enc_key=base.KEY,
            **extra,
        )

    apps = [
        create_app(settings_for(billing_enabled=True, billing_allowed_emails="*")),
        create_app(settings_for(billing_enabled=False)),
    ]
    for each in apps:
        each.dependency_overrides[get_bot_provider] = lambda: base.FakeBot()
    started = [base.start_server(each) for each in apps]
    engine = create_engine(base.SCRATCH_URL)
    try:
        run(engine, started[0][1], started[1][1])
        for path in OUT.glob("*.json"):  # no password, even a placeholder, stays in a file
            body = path.read_text()
            for secret in (PASSWORD, base.ADMIN_PASSWORD):
                body = body.replace(secret, "<password placeholder>")
            path.write_text(body)
        with engine.connect() as conn:
            conn.execute(text("select 1"))
    finally:
        for server, _ in started:
            server.should_exit = True
        engine.dispose()


if __name__ == "__main__":
    main()
