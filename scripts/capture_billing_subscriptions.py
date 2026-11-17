"""Drive the subscription routes over real HTTP and save every exchange for the guide (stage S8a).

    python scripts/capture_billing_subscriptions.py postgresql+psycopg://USER:PASS@localhost:PORT/postgres

Same setup as `scripts/capture_billing_profile.py`: real migrations (so the seeded plan and pack are
the real rows), the real app under uvicorn, real HTTP. Two copies of the app run on one database:
billing on and open to every account, and billing off.

Files go to `docs/fe-captures/billing-subscriptions/NN-name.json`. Passwords in login calls are
placeholders; the people are placeholders (`ada@example.test`, `ben@example.test`). Times are the
capture machine's real clock.

**What is real:** the app, the guard (login and CSRF), validation, the checkout routes, the webhook
route with its signature check, the settlement rules, the grants, the state machine, the scheduler
pass (`renewals.run_due`), the pay link, every error shape.

**What is NOT, and each capture says so where it matters:**
- **No real payment provider.** The provider is `app.billing.fake.FakeProvider`, registered under
  the names `flutterwave` and `paystack` so the route table picks it; its checkout address is
  `https://fake.test/pay/<reference>` and its webhook signature is `X-Fake-Signature` (a real
  provider's differs, and S6 and S7 own those). Nothing here proves a real Paystack or Flutterwave
  accepts these calls: **not verified live**.
- **The clock is moved by SQL.** The scheduler cannot wait a month: the stored dates are shifted
  back (the period ended an hour ago, and later 80 hours ago) and `renewals.run_due` is then called
  with the real time, once, by this script (the scheduler would do it every 60 seconds).
- **A second plan exists only here.** The real catalog has one decided plan (20 hours, 18 USD);
  upgrade and downgrade need a second, so this script inserts `capture_40h` (40 hours, 34 USD).
  It is not a decided plan; the real second tier is Adebayo's open fact (so it has no naira price
  either). The seeded naira plan price (NGN 25,000, decided 2026-11-17) is real; the naira pack
  (NGN 15,000) is decided too (2026-11-17).
- A 3-D Secure charge's own link is the fake's `https://fake.test/3ds/open-charge`.
"""

import contextlib
import importlib.util
import io
import json
import shutil
import sys
from datetime import UTC, datetime, timedelta

import capture_calendar_connect as base  # reads sys.argv at import (the admin database URL)

OUT = base.ROOT / "docs" / "fe-captures" / "billing-subscriptions"
base.OUT = OUT

import httpx  # noqa: E402
from sqlalchemy import create_engine, select, text, update  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.billing import renewals  # noqa: E402
from app.billing.fake import FakeProvider  # noqa: E402
from app.billing.provider import PROVIDER_FACTORIES  # noqa: E402
from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import HourGrant, Payment, Plan, PlanPrice, Subscription, User  # noqa: E402
from app.passwords import hash_password  # noqa: E402
from app.services import get_vexa  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "billing_test_purchase", base.ROOT / "scripts" / "billing_test_purchase.py"
)
runbook = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runbook)

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


def webhook(server_base: str, name: str, provider: FakeProvider, tx, note: str, *, sign=True):
    """Deliver the provider's webhook for `tx` to the real route, signed like the fake signs, and
    save the exchange. The body names OUR reference, as a real provider's does."""
    body = provider.event_body(provider.event_for(tx))
    headers = {"Content-Type": "application/json"}
    if sign:
        headers["X-Fake-Signature"] = provider.sign(body)
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
    return response


def payment_by_reference(engine, reference: str) -> Payment:
    with Session(engine) as session:
        return session.scalars(select(Payment).where(Payment.reference == reference)).one()


def age(engine, overdue: timedelta) -> None:
    """Move the stored dates so the paid period ended `overdue` ago (the capture cannot wait a
    month): the period, and the hours that belong to it, shift together."""
    with Session(engine) as session:
        sub = session.scalars(select(Subscription).order_by(Subscription.id.desc())).first()
        shift = sub.current_period_end - (datetime.now(UTC) - overdue)
        session.execute(
            update(Subscription)
            .where(Subscription.id == sub.id)
            .values(
                current_period_start=Subscription.current_period_start - shift,
                current_period_end=Subscription.current_period_end - shift,
                grace_ends_at=Subscription.grace_ends_at - shift,
                next_charge_at=Subscription.next_charge_at - shift,
            )
        )
        session.execute(  # the plan's own periods only: a pack has its own twelve months
            update(HourGrant)
            .where(HourGrant.subscription_id == sub.id, HourGrant.kind == "plan_period")
            .values(starts_at=HourGrant.starts_at - shift, expires_at=HourGrant.expires_at - shift)
        )
        session.commit()


def run_script(engine, name: str, note: str, call) -> None:
    """Run one of the runbook script's read-only commands in this process, against the scratch
    database, and save its printed output (no email, key or token is ever printed)."""
    out = io.StringIO()
    with Session(engine) as session, contextlib.redirect_stdout(out):
        session.execute(text("set transaction read only"))
        result = call(session)
        session.rollback()
    base.note_file(
        name,
        note,
        {"exit": 0 if result in (None, True) else 1, "output": out.getvalue().splitlines()},
    )


def clock_pass(engine, settings: Settings) -> None:
    with Session(engine) as session:
        renewals.run_due(session, datetime.now(UTC), settings)


def rows(engine) -> dict:
    """What the database holds after the calls, in the words of the tables."""
    with Session(engine) as session:
        return {
            "subscriptions": [
                {
                    "plan": s.plan_code,
                    "status": s.status,
                    "currency": s.currency,
                    "provider": s.provider,
                    "cancel_at_period_end": s.cancel_at_period_end,
                    "pending_plan_code": s.pending_plan_code,
                    "retry_count": s.retry_count,
                }
                for s in session.scalars(select(Subscription).order_by(Subscription.id))
            ],
            "hour_grants": [
                {"kind": g.kind, "seconds": g.seconds, "source_payment_id": g.source_payment_id}
                for g in session.scalars(select(HourGrant).order_by(HourGrant.id))
            ],
            "payments": [
                {
                    "kind": p.kind,
                    "status": p.status,
                    "attempt": p.attempt,
                    "amount_minor": p.amount_minor,
                    "currency": p.currency,
                }
                for p in session.scalars(select(Payment).order_by(Payment.id))
            ],
        }


def run(engine, on, off, settings) -> None:
    for email in (ADA, BEN):
        person(engine, email)
    with Session(engine) as session:  # a second plan, only for this capture (see the module note)
        session.add(
            Plan(code="capture_40h", name="40 hours a month", monthly_seconds=144000, sort_order=2)
        )
        session.flush()
        session.add(PlanPrice(plan_code="capture_40h", currency="USD", amount_minor=3400))
        session.commit()

    # ---- billing off ----
    off_ada = signed_in(off, "ada flag-off", ADA)
    off_ada.call(
        "off-plans", "GET", "/api/billing/plans", note="`BILLING_ENABLED` false: 404 `not_found`."
    )
    off_ada.call(
        "off-checkout",
        "POST",
        "/api/billing/checkout",
        body={"kind": "plan", "code": "plan_20h"},
        note="Same 404 for a write; nothing is stored.",
    )

    # ---- billing on ----
    Recorder(on, "visitor").call(
        "visitor-subscription", "GET", "/api/billing/subscription", note="Not signed in: 401."
    )
    ada = signed_in(on, "ada billing-on", ADA)
    ada.call(
        "plans-no-country",
        "GET",
        "/api/billing/plans",
        note="No billing country chosen yet: no route and no prices. Nothing is guessed.",
    )
    ada.call(
        "subscription-never",
        "GET",
        "/api/billing/subscription",
        note="A person who never had a plan: `state` is `none`, `subscription` is null.",
    )
    ada.call(
        "checkout-no-country",
        "POST",
        "/api/billing/checkout",
        body={"kind": "plan", "code": "plan_20h"},
        note="422 `billing_country_missing`: the country is chosen first (S5 route).",
    )
    ada.call(
        "country-us",
        "PUT",
        "/api/billing/profile/country",
        body={"billing_country": "US"},
        note="The S5 route; dollars through the provider named `flutterwave`.",
    )
    ada.call(
        "plans-us",
        "GET",
        "/api/billing/plans",
        note="The seeded catalog in dollars (`amount_minor` is cents): the real decided plan, 20 "
        "hours at 1800, and the 10-hour pack at 1000. `capture_40h` is this capture's extra plan.",
    )
    ada.call(
        "checkout-pack-without-plan",
        "POST",
        "/api/billing/checkout",
        body={"kind": "pack", "code": "pack_10h"},
        note="A pack needs a live plan: 409 `plan_required`.",
    )
    ada.call(
        "checkout-bad-kind",
        "POST",
        "/api/billing/checkout",
        body={"kind": "gift", "code": "x"},
        note="422 `validation_error`.",
    )
    ada.call(
        "checkout-no-csrf",
        "POST",
        "/api/billing/checkout",
        body={"kind": "plan", "code": "plan_20h"},
        csrf=None,
        note="No CSRF header: 403 `csrf_rejected`.",
    )
    ada.call(
        "checkout-unknown-plan",
        "POST",
        "/api/billing/checkout",
        body={"kind": "plan", "code": "nope"},
        note="404 `plan_not_found`.",
    )
    started = ada.call(
        "checkout-plan",
        "POST",
        "/api/billing/checkout",
        body={"kind": "plan", "code": "plan_20h"},
        note="Starts the hosted checkout. The payment row is made first (`pending`); "
        "`checkout_url` is where to send the person (here the fake provider's address). "
        "Nothing is granted by this call.",
    ).json()
    reference = started["payment"]["reference"]
    ada.call(
        "payment-pending",
        "GET",
        f"/api/billing/payments/{reference}",
        note="The page the payer returns to polls this. Still `pending`: the redirect grants "
        "nothing.",
    )
    ada.call(
        "subscription-pending",
        "GET",
        "/api/billing/subscription",
        note="Before the payment is verified: no subscription, no hours, and `pending_payment` "
        "shows the checkout still waiting.",
    )
    payment = payment_by_reference(engine, reference)
    tx = USD.complete(payment, card_token="tok-capture-1", payer_email=ADA)
    webhook(
        on,
        "webhook-wrong-amount",
        USD,
        USD.complete(payment, tx_id="fake-short", amount_minor=payment.amount_minor - 1),
        "A webhook for the right reference whose provider-reported amount is 1 cent short: the "
        "payment is re-read, the amount refused, nothing is granted. (200: decided, not retried.)",
    )
    ada.call(
        "payment-after-wrong-amount",
        "GET",
        f"/api/billing/payments/{reference}",
        note="Still `pending`: a wrong amount never pays.",
    )
    webhook(
        on,
        "webhook-bad-signature",
        USD,
        tx,
        "A webhook with a missing signature: 401, nothing stored.",
        sign=False,
    )
    webhook(
        on,
        "webhook-paid",
        USD,
        tx,
        "The genuine webhook. The route re-reads the payment from the provider and, because it "
        "matches the reference, the currency and the amount, marks it paid and grants the hours "
        "once, in the same transaction.",
    )
    webhook(
        on,
        "webhook-replay",
        USD,
        tx,
        "The same event again (the provider retries): 200, nothing granted twice.",
    )
    ada.call(
        "payment-paid",
        "GET",
        f"/api/billing/payments/{reference}",
        note="The payment is now `succeeded`.",
    )
    ada.call(
        "subscription-active",
        "GET",
        "/api/billing/subscription",
        note="Active: `hours.seconds_left` is the 20 hours, the period is one month, and the "
        "buttons the screen may show are the `can_*` flags.",
    )
    run_script(
        engine,
        "script-proof-ok",
        "**The runbook's proof command, run for real against this scratch database** "
        "(`scripts/billing_test_purchase.py proof`, here called in process): after the verified "
        "plan payment it prints every check and `PROOF: OK`.",
        lambda s_: runbook.proof(s_, settings, ADA),
    )
    run_script(
        engine,
        "script-state",
        "`billing_test_purchase.py state`: what this person can do under the modes the server "
        "runs with (here HOURS_MODE and FEATURE_GATES_MODE are at their defaults, off).",
        lambda s_: runbook.state(s_, settings, ADA),
    )
    run_script(
        engine,
        "script-preflight-1-no-keys",
        "`billing_test_purchase.py preflight 1` on THIS capture server, which has no provider "
        "keys: it lists exactly what is missing (a key is reported present or missing, never "
        "printed).",
        lambda s_: runbook.preflight(s_, settings, 1),
    )
    ada.call(
        "checkout-second-plan",
        "POST",
        "/api/billing/checkout",
        body={"kind": "plan", "code": "plan_20h"},
        note="Already subscribed: 409 `already_subscribed`.",
    )
    ada.call(
        "change-same-plan",
        "POST",
        "/api/billing/subscription/change-plan",
        body={"plan_code": "plan_20h"},
        note="409 `same_plan`.",
    )
    ada.call(
        "pay-link-nothing-due",
        "POST",
        "/api/billing/subscription/pay-link",
        note="Inside a paid period there is nothing to pay: 409 `nothing_due`.",
    )

    # ---- a pack ----
    pack = ada.call(
        "checkout-pack",
        "POST",
        "/api/billing/checkout",
        body={"kind": "pack", "code": "pack_10h"},
        note="A pack, with a live plan: paid on the route the plan was sold on.",
    ).json()
    pack_tx = USD.complete(payment_by_reference(engine, pack["payment"]["reference"]))
    webhook(
        on,
        "webhook-pack-paid",
        USD,
        pack_tx,
        "The pack's payment verified: 10 hours granted, valid 12 months.",
    )
    ada.call(
        "subscription-with-pack",
        "GET",
        "/api/billing/subscription",
        note="`hours.seconds_left` is the plan's 20 hours plus the pack's 10.",
    )

    # ---- cancel, resume, change plan ----
    ada.call(
        "cancel",
        "POST",
        "/api/billing/subscription/cancel",
        note="Stops the renewal; access and hours last to the period's end (`status` stays "
        "`active`).",
    )
    ada.call(
        "cancel-again",
        "POST",
        "/api/billing/subscription/cancel",
        note="Idempotent: the same answer.",
    )
    ada.call("resume", "POST", "/api/billing/subscription/resume", note="Takes the cancel back.")
    ada.call(
        "resume-not-canceled",
        "POST",
        "/api/billing/subscription/resume",
        note="Nothing to take back: 409 `not_canceled`.",
    )
    ada.call(
        "preview-same-plan",
        "GET",
        "/api/billing/subscription/change-plan/preview?plan_code=plan_20h",
        note="The plan they are on: 409 `same_plan`, as the change itself would say.",
    )
    ada.call(
        "preview-upgrade",
        "GET",
        "/api/billing/subscription/change-plan/preview?plan_code=capture_40h",
        note="What the upgrade would cost, BEFORE the person commits: `amount_minor` is charged "
        "the moment they press the button, `extra_seconds` is added to this period. A read: "
        "nothing is written.",
    )
    up = ada.call(
        "change-upgrade",
        "POST",
        "/api/billing/subscription/change-plan",
        body={"plan_code": "capture_40h"},
        note="A plan with more hours: a hosted checkout for the price difference for the days "
        "left (here almost the whole month, so almost 16.00). The plan changes only when that "
        "payment is verified.",
    ).json()
    up_tx = USD.complete(
        payment_by_reference(engine, up["payment"]["reference"]), card_token="tok-capture-2"
    )
    webhook(
        on,
        "webhook-upgrade-paid",
        USD,
        up_tx,
        "The upgrade's payment verified: the plan changes and the extra hours are granted.",
    )
    ada.call(
        "subscription-upgraded",
        "GET",
        "/api/billing/subscription",
        note="On the 40-hour plan; `hours.seconds_left` has grown by the prorated extra.",
    )
    ada.call(
        "preview-downgrade",
        "GET",
        "/api/billing/subscription/change-plan/preview?plan_code=plan_20h",
        note="A smaller plan: `takes_effect` is `next_renewal` and `amount_minor` is what the "
        "renewal will charge.",
    )
    ada.call(
        "change-downgrade",
        "POST",
        "/api/billing/subscription/change-plan",
        body={"plan_code": "plan_20h"},
        note="A smaller plan: `scheduled` for the next renewal, nothing changes now "
        "(`pending_plan_code`, and `amount_minor` is what the renewal will charge).",
    )
    ada.call(
        "change-downgrade-cleared",
        "POST",
        "/api/billing/subscription/change-plan",
        body={"plan_code": "capture_40h"},
        note="Choosing the plan you are on takes a waiting downgrade back: `cleared`.",
    )
    ada.call(
        "change-unknown-plan",
        "POST",
        "/api/billing/subscription/change-plan",
        body={"plan_code": "nope"},
        note="404 `plan_not_found`.",
    )

    # ---- a failed renewal: the grace ----
    USD.charge_outcome = "failed"
    age(engine, timedelta(hours=1))
    clock_pass(engine, settings)
    base.note_file(
        "clock-pass-failed-card",
        "**The clock was moved by SQL (NOT LIVE time).** The period ended an hour ago and one "
        "pass of the scheduler (`renewals.run_due`) ran: the saved card was charged, the provider "
        "said it failed, so the subscription is `past_due` and the grace has started. The rows "
        "afterwards:",
        rows(engine),
    )
    ada.call(
        "subscription-past-due",
        "GET",
        "/api/billing/subscription",
        note="`past_due`: `state` is `past_due`, `grace_ends_at` is 72 hours after the due "
        "moment, `next_retry_at` is the day-1 retry. `hours.seconds_left` is only what was left "
        "of the old period; no new hours were granted. `can_pay_now` is true, `can_change_plan` "
        "false.",
    )
    ada.call(
        "change-plan-in-grace",
        "POST",
        "/api/billing/subscription/change-plan",
        body={"plan_code": "plan_20h"},
        note="Not in the grace: 409 `payment_overdue`.",
    )
    link = ada.call(
        "pay-link",
        "POST",
        "/api/billing/subscription/pay-link",
        note="The provider's page to pay what is overdue: a fresh hosted checkout for the "
        "renewal amount.",
    ).json()
    ada.call(
        "pay-link-again",
        "POST",
        "/api/billing/subscription/pay-link",
        note="The same page again, not a second checkout.",
    )
    ada.call(
        "pack-in-grace",
        "POST",
        "/api/billing/checkout",
        body={"kind": "pack", "code": "pack_10h"},
        note="A pack may still be bought in the grace (a payment that succeeds).",
    )
    link_tx = USD.complete(
        payment_by_reference(engine, link["payment"]["reference"]), card_token="tok-capture-3"
    )
    webhook(
        on,
        "webhook-renewal-paid",
        USD,
        link_tx,
        "The overdue payment verified: a NEW period starts from the payment date and its hours "
        "are granted.",
    )
    ada.call(
        "subscription-renewed",
        "GET",
        "/api/billing/subscription",
        note="Active again: a new period from the payment date; the grace fields are null.",
    )

    # ---- a 3-D Secure charge keeps its own link ----
    USD.charge_outcome = "pending"
    age(engine, timedelta(hours=1))
    clock_pass(engine, settings)
    ada.call(
        "subscription-3ds",
        "GET",
        "/api/billing/subscription",
        note="The saved card's bank wants the payer to confirm: the charge is `pending` with the "
        "bank's own link in `pending_payment.pay_url`, and the plan is in its grace.",
    )
    ada.call(
        "pay-link-3ds",
        "POST",
        "/api/billing/subscription/pay-link",
        note="The pay link is THAT charge's own page (the same `checkout_url`), never a second "
        "checkout.",
    )

    # ---- the lapse ----
    USD.charge_outcome = "failed"
    age(engine, timedelta(hours=80))
    ada.call(
        "subscription-lapsed",
        "GET",
        "/api/billing/subscription",
        note="80 hours after the due moment, even before the scheduler has run, the clock says "
        "`lapsed`: `state` `lapsed`, no hours, no buttons but a new checkout.",
    )
    clock_pass(engine, settings)
    base.note_file("rows-after-lapse", "The rows after the lapse pass.", rows(engine))
    ada.call(
        "subscription-lapsed-after-pass",
        "GET",
        "/api/billing/subscription",
        note="After the scheduler's pass: the same.",
    )
    ada.call(
        "pay-link-lapsed",
        "POST",
        "/api/billing/subscription/pay-link",
        note="Nothing is overdue on a lapsed plan any more: 409 `no_subscription`; the person "
        "subscribes again from `/checkout`.",
    )

    ada.call(
        "payments-list",
        "GET",
        "/api/billing/payments",
        note="The billing history: Ada's own payments, newest first. `pay_url` is set only on a "
        "pending one.",
    )

    # ---- another person ----
    ben = signed_in(on, "ben billing-on", BEN)
    ben.call(
        "other-person-payment",
        "GET",
        f"/api/billing/payments/{reference}",
        note="Another account cannot see Ada's payment: 404 `not_found`.",
    )
    ben.call(
        "payments-list-empty",
        "GET",
        "/api/billing/payments",
        note="Another account sees none of Ada's payments.",
    )
    ben.call(
        "country-ng",
        "PUT",
        "/api/billing/profile/country",
        body={"billing_country": "NG"},
        note="Nigeria: naira through the provider named `paystack`.",
    )
    ben.call(
        "plans-ng",
        "GET",
        "/api/billing/plans",
        note="The seeded naira list (decided 2026-11-17: NGN 25,000 a month, `amount_minor` is "
        "kobo), and the naira 10-hour pack (NGN 15,000, 1,500,000 kobo, decided the same day).",
    )
    ng = ben.call(
        "checkout-ng",
        "POST",
        "/api/billing/checkout",
        body={"kind": "plan", "code": "plan_20h"},
        note="A person billed from Nigeria: naira, through the provider named `paystack` (here "
        "the scripted one).",
    ).json()
    ng_tx = NGN.complete(
        payment_by_reference(engine, ng["payment"]["reference"]),
        card_token="tok-capture-ng",
        payer_email=BEN,
    )
    webhook(
        on,
        "webhook-ng-paid",
        NGN,
        ng_tx,
        "The naira payment verified through the route named `paystack`: 20 hours granted.",
    )
    ben.call(
        "subscription-ng-active",
        "GET",
        "/api/billing/subscription",
        note="Active in naira: `amount_minor` 2500000 kobo is the next renewal.",
    )
    pack_ng = ben.call(
        "checkout-pack-ng",
        "POST",
        "/api/billing/checkout",
        body={"kind": "pack", "code": "pack_10h"},
        note="The naira pack, with a naira plan: paid on the plan's route.",
    ).json()
    pack_ng_tx = NGN.complete(payment_by_reference(engine, pack_ng["payment"]["reference"]))
    webhook(
        on, "webhook-ng-pack-paid", NGN, pack_ng_tx, "The naira pack verified: 10 hours granted."
    )
    ben.call(
        "subscription-ng-with-pack",
        "GET",
        "/api/billing/subscription",
        note="`hours.seconds_left` 108000: the plan's 20 hours plus the pack's 10.",
    )
    base.note_file("rows-at-the-end", "The rows at the end of the run.", rows(engine))


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

    on_settings = settings_for(billing_enabled=True, billing_allowed_emails="*")
    apps = [create_app(on_settings), create_app(settings_for(billing_enabled=False))]
    for each in apps:
        each.dependency_overrides[get_vexa] = lambda: base.FakeVexa()
    started = [base.start_server(each) for each in apps]
    engine = create_engine(base.SCRATCH_URL)
    try:
        run(engine, started[0][1], started[1][1], on_settings)
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
