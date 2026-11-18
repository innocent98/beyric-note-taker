"""Drive the plan routes over real HTTP with the three seeded plans and save every exchange for the
guide (the Pro and Scale plans, migration 0054).

    python scripts/capture_billing_plans.py postgresql+psycopg://USER:PASS@localhost:PORT/postgres

Same setup as `scripts/capture_billing_subscriptions.py` (whose helpers it reuses): real
migrations, so the plans and prices are the REAL seeded rows (20 hours, Pro 50 hours, Scale 100
hours; USD and NGN), the real app under uvicorn, real HTTP, a scripted payment provider. No plan
is invented here (the older capture script's dev-only `capture_40h` is not used).

Files go to `docs/fe-captures/billing-plans/NN-name.json`. Passwords are placeholders; the people
are placeholders (`ada@example.test`, `ben@example.test`, `ngozi@example.test`).

**What is real:** the app, the routes, the proration, the checkout, the webhook route with its
signature check, the settlement rules, the grants, the downgrade applied by the scheduler pass at
the renewal. **What is NOT:** the payment provider is `app.billing.fake.FakeProvider` under the
names `flutterwave` and `paystack` (a real provider's pages and signatures are not shown here),
and the clock is moved by SQL so a renewal can happen without waiting a month."""

import json
import shutil
import sys
from datetime import timedelta

import capture_billing_subscriptions as cbs
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.main import create_app
from app.models import Subscription
from app.services import get_vexa

base = cbs.base
OUT = base.ROOT / "docs" / "fe-captures" / "billing-plans"
base.OUT = OUT
cbs.OUT = OUT

ADA, BEN, NGOZI = cbs.ADA, cbs.BEN, "ngozi@example.test"
USD, NGN = cbs.USD, cbs.NGN
Recorder = cbs.Recorder


def pay(engine, server, name: str, provider, reference: str, note: str, **tx_extra) -> None:
    payment = cbs.payment_by_reference(engine, reference)
    cbs.webhook(server, name, provider, provider.complete(payment, **tx_extra), note)


def preview(client, name: str, plan: str, note: str):
    return client.call(
        name,
        "GET",
        f"/api/billing/subscription/change-plan/preview?plan_code={plan}",
        note=note,
    )


def change(client, name: str, plan: str, note: str):
    return client.call(
        name,
        "POST",
        "/api/billing/subscription/change-plan",
        body={"plan_code": plan},
        note=note,
    ).json()


def run(engine, on, settings) -> None:
    for email in (ADA, BEN, NGOZI):
        cbs.person(engine, email)

    ada = cbs.signed_in(on, "ada billing-on", ADA)
    ada.call(
        "country-us",
        "PUT",
        "/api/billing/profile/country",
        body={"billing_country": "US"},
        note="Dollars through the provider named `flutterwave`.",
    )
    ada.call(
        "plans-us",
        "GET",
        "/api/billing/plans",
        note="The catalog in dollars (`amount_minor` is cents), in `sort_order`: the 20-hour plan "
        "(1800), Pro (4000, 50 hours) and Scale (8000, 100 hours), then the 10-hour pack (1000). "
        "These are the seeded rows of migrations 0052 and 0054.",
    )
    ngozi = cbs.signed_in(on, "ngozi billing-on", NGOZI)
    ngozi.call(
        "country-ng",
        "PUT",
        "/api/billing/profile/country",
        body={"billing_country": "NG"},
        note="Naira through the provider named `paystack`.",
    )
    ngozi.call(
        "plans-ng",
        "GET",
        "/api/billing/plans",
        note="The same catalog in naira (`amount_minor` is kobo): 2,500,000 (NGN 25,000), "
        "5,600,000 (NGN 56,000) and 11,200,000 (NGN 112,000); the pack 1,500,000.",
    )

    # ---- Starter -> Pro -> Scale, then Scale -> Starter ----
    first = ada.call(
        "checkout-starter",
        "POST",
        "/api/billing/checkout",
        body={"kind": "plan", "code": "plan_20h"},
        note="Each plan has its own checkout: the plan's code and the price in the person's route.",
    ).json()
    pay(
        engine,
        on,
        "webhook-starter-paid",
        USD,
        first["payment"]["reference"],
        "The 20-hour plan's payment verified: the plan starts and 20 hours are granted.",
        card_token="tok-plans-1",
        payer_email=ADA,
    )
    ada.call(
        "subscription-starter",
        "GET",
        "/api/billing/subscription",
        note="On the 20-hour plan: 72000 seconds held, `amount_minor` 1800.",
    )
    preview(
        ada,
        "preview-starter-to-pro",
        "plan_50h",
        "An upgrade: `amount_minor` is what is charged NOW (the price difference, 4000 - 1800, for "
        "the days left), `extra_seconds` the hours added to this period (30 hours for the whole "
        "month), `takes_effect` `now`. A read: nothing is written.",
    )
    preview(
        ada,
        "preview-starter-to-scale",
        "plan_100h",
        "Starter to Scale: the difference is 8000 - 1800 and the extra hours are 80 for the "
        "days left.",
    )
    up = change(
        ada,
        "change-starter-to-pro",
        "plan_50h",
        "Confirming the upgrade: a hosted checkout for the prorated difference. The plan changes "
        "only when that payment is verified.",
    )
    pay(
        engine,
        on,
        "webhook-pro-paid",
        USD,
        up["payment"]["reference"],
        "The upgrade payment verified: the plan becomes Pro and the extra hours are granted.",
        card_token="tok-plans-2",
    )
    ada.call(
        "subscription-pro",
        "GET",
        "/api/billing/subscription",
        note='On Pro (`plan_name` "Pro"): `amount_minor` 4000 for the next renewal; the hours '
        "held grew by the prorated extra.",
    )
    preview(
        ada,
        "preview-pro-to-scale",
        "plan_100h",
        "Pro to Scale: the difference is 8000 - 4000, the extra hours 50 for the days left.",
    )
    up2 = change(
        ada,
        "change-pro-to-scale",
        "plan_100h",
        "Pro to Scale: a checkout for the prorated difference.",
    )
    pay(
        engine,
        on,
        "webhook-scale-paid",
        USD,
        up2["payment"]["reference"],
        "Verified: the plan becomes Scale.",
        card_token="tok-plans-3",
    )
    ada.call(
        "subscription-scale",
        "GET",
        "/api/billing/subscription",
        note="On Scale: `amount_minor` 8000.",
    )
    preview(
        ada,
        "preview-scale-to-starter",
        "plan_20h",
        "A smaller plan: `takes_effect` is `next_renewal`, `amount_minor` is what the renewal will "
        "charge (1800), no extra hours.",
    )
    change(
        ada,
        "change-scale-to-starter",
        "plan_20h",
        "Scale to the 20-hour plan: `scheduled` for the next renewal; nothing changes now.",
    )
    ada.call(
        "subscription-downgrade-waiting",
        "GET",
        "/api/billing/subscription",
        note="Still Scale; `pending_plan_code` is `plan_20h` and `amount_minor` is the 20-hour "
        "price the renewal will charge.",
    )
    change(
        ada,
        "change-back-to-scale",
        "plan_100h",
        "Choosing the plan you are on takes the waiting downgrade back: `cleared`.",
    )
    change(
        ada,
        "change-scale-to-pro",
        "plan_50h",
        "Scale to Pro is a downgrade too (fewer hours): `scheduled`.",
    )
    # The renewal applies the waiting downgrade at the smaller plan's price.
    cbs.age(engine, timedelta(hours=1))
    cbs.clock_pass(engine, settings=settings)
    ada.call(
        "subscription-after-renewal",
        "GET",
        "/api/billing/subscription",
        note="**The clock was moved by SQL (not live time).** One scheduler pass at the period's "
        "end charged the saved card Pro's 4000 and applied the downgrade: the plan is Pro, "
        "`pending_plan_code` is null.",
    )
    ada.call(
        "payments",
        "GET",
        "/api/billing/payments",
        note="Every payment of the journey: the plan, the two upgrades and the renewal.",
    )

    # ---- a person who goes straight from the 20-hour plan to Scale, and one paying in naira ----
    ben = cbs.signed_in(on, "ben billing-on", BEN)
    ben.call(
        "country-us",
        "PUT",
        "/api/billing/profile/country",
        body={"billing_country": "US"},
        note="Dollars.",
    )
    b1 = ben.call(
        "checkout-starter",
        "POST",
        "/api/billing/checkout",
        body={"kind": "plan", "code": "plan_20h"},
        note="Starter.",
    ).json()
    pay(
        engine,
        on,
        "webhook-ben-starter-paid",
        USD,
        b1["payment"]["reference"],
        "Verified.",
        card_token="tok-plans-4",
        payer_email=BEN,
    )
    b2 = change(
        ben,
        "change-starter-to-scale",
        "plan_100h",
        "Starter straight to Scale: one checkout for the whole difference.",
    )
    pay(
        engine,
        on,
        "webhook-ben-scale-paid",
        USD,
        b2["payment"]["reference"],
        "Verified.",
        card_token="tok-plans-5",
    )
    ben.call(
        "subscription-scale",
        "GET",
        "/api/billing/subscription",
        note="On Scale after one upgrade: 100 hours less what the proration gave, in total "
        "`hours.holdings_seconds`.",
    )
    n1 = ngozi.call(
        "checkout-ng-pro",
        "POST",
        "/api/billing/checkout",
        body={"kind": "plan", "code": "plan_50h"},
        note="Pro in naira: `amount_minor` 5,600,000 kobo, `currency` NGN.",
    ).json()
    pay(
        engine,
        on,
        "webhook-ng-pro-paid",
        NGN,
        n1["payment"]["reference"],
        "The Paystack double's webhook: Pro starts in naira.",
        card_token="tok-plans-6",
        payer_email=NGOZI,
    )
    ngozi.call(
        "subscription-ng-pro",
        "GET",
        "/api/billing/subscription",
        note="Pro in naira: `currency` NGN, `amount_minor` 5,600,000.",
    )
    preview(
        ngozi,
        "preview-ng-pro-to-scale",
        "plan_100h",
        "An upgrade in naira: the difference 11,200,000 - 5,600,000 kobo for the days left.",
    )
    base.note_file(
        "rows-at-the-end",
        "What the database holds after the calls, in the words of the tables.",
        cbs.rows(engine),
    )
    with Session(engine) as session:
        subs = session.scalars(select(Subscription).order_by(Subscription.id)).all()
        print([(s.plan_code, s.status, s.pending_plan_code) for s in subs])


def main() -> None:
    if not sys.argv[1:]:
        sys.exit(__doc__)
    base.prepare_database()
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True)
    settings = Settings(
        _env_file=None,
        app_env="test",
        session_secret="capture-secret-" + "x" * 30,
        cookie_secure=False,
        database_url=base.SCRATCH_URL,
        public_base_url="https://notes.example.test",
        token_enc_key=base.KEY,
        billing_enabled=True,
        billing_allowed_emails="*",
    )
    app = create_app(settings)
    app.dependency_overrides[get_vexa] = lambda: base.FakeVexa()
    server, on = base.start_server(app)
    engine = create_engine(base.SCRATCH_URL)
    try:
        run(engine, on, settings)
        for path in OUT.glob("*.json"):  # no password, even a placeholder, stays in a file
            body = path.read_text()
            for secret in (cbs.PASSWORD, base.ADMIN_PASSWORD):
                body = body.replace(secret, "<password placeholder>")
            path.write_text(body)
    finally:
        server.should_exit = True
        engine.dispose()
    print(json.dumps({"captures": len(list(OUT.glob("*.json")))}))


if __name__ == "__main__":
    main()
