"""A local copy of the app with billing switched on and a SCRIPTED payment provider, so the billing
screens (`web/app/billing`) can be driven in a browser (billing stage S8b). For development
only: it never talks to Paystack or Flutterwave, needs no keys, and refuses to start against
anything but a local scratch database.

    docker run -d --name bn-s8b-pg -e POSTGRES_PASSWORD=scratch -p 55432:5432 postgres:16-alpine
    python scripts/dev_billing_stack.py postgresql+psycopg://postgres:scratch@localhost:55432/postgres
    # in web/:  FASTAPI_INTERNAL_URL=http://127.0.0.1:8765 npx next dev -p 3100   (then open http://localhost:3100)

The database `beyric_capture` on that server is dropped and rebuilt (real migrations, so the
seeded plan and pack are the real rows) every time. The app listens on 127.0.0.1:8765
(`DEV_API_PORT`; the Next dev server is expected on 3100, `DEV_WEB_PORT`) and runs with
`BILLING_ENABLED` on and open to every account, `HOURS_MODE=enforce` and
`FEATURE_GATES_MODE=enforce`.

People (all with the password printed at start, a placeholder that exists only in this scratch
database): `new@`, `us@`, `ng@`, `active@`, `cancel@`, `downgrade@`, `pastdue@`, `threeds@`,
`lapsed@`, `canceled@`, `low@`, `out@`, `pending@`, `invoices@`, `refunded@` and the administrator
`root@` (all `@example.test`; `GET /dev/scenario/adminboard` fills the refund screen). Their state
is built by `GET /dev/scenario/<name>` (JSON), through the real checkout, the real webhook inbox and
settlement rules and one real scheduler pass; the dates are moved by SQL because a month cannot
be waited for. `GET /dev/` lists the scenarios.

**The provider is scripted.** Its "hosted page" is `/dev/pay/<reference>`, a plain page with
buttons (paid, failed, wrong amount, back out) that delivers the webhook the real route would
get and then sends the browser to the return address `/billing/return?reference=...`, the way
Paystack does. A 3-D Secure charge's page is `/dev/3ds`. What a real provider's page and return
address look like is NOT shown here: that is the runbook's test-mode run."""

import html
import json
import os
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

# The hours rule and the feature gates read the process environment (`get_settings`), not the
# Settings object handed to `create_app`, so they are switched on here, before the app is imported.
os.environ["HOURS_MODE"] = "enforce"
os.environ["FEATURE_GATES_MODE"] = "enforce"
os.environ["BILLING_ENABLED"] = "true"
os.environ["BILLING_ALLOWED_EMAILS"] = "*"

import capture_calendar_connect as base  # noqa: E402  reads sys.argv[1] at import (the admin database URL)

if urlparse(base.ADMIN_URL).hostname not in ("localhost", "127.0.0.1", "::1"):
    sys.exit("This script only runs against a local scratch database.")

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from fastapi import APIRouter, FastAPI, HTTPException  # noqa: E402
from fastapi.responses import HTMLResponse, RedirectResponse  # noqa: E402
from sqlalchemy import select, update  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import usage  # noqa: E402
from app.billing import profile as profiles  # noqa: E402
from app.billing import (
    refunds,  # noqa: E402
    renewals,  # noqa: E402
)
from app.billing import subscriptions as subs  # noqa: E402
from app.billing.fake import FakeProvider  # noqa: E402
from app.billing.provider import PROVIDER_FACTORIES  # noqa: E402
from app.billing.types import OutcomeUnknown  # noqa: E402
from app.config import Settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import (  # noqa: E402
    HourGrant,
    Meeting,
    Payment,
    PlanPrice,
    Subscription,
    Template,
    User,
    UserSettings,
)
from app.passwords import hash_password  # noqa: E402
from app.services import get_vexa  # noqa: E402

# Where the Next dev server is (the provider's "return" goes there) and where this app listens.
# Ports 3000 and 8000 are often taken by other projects, so the defaults are not those.
WEB = f"http://localhost:{os.environ.get('DEV_WEB_PORT', '3100')}"
API_PORT = int(os.environ.get("DEV_API_PORT", "8765"))
API = f"http://127.0.0.1:{API_PORT}"
PASSWORD = "-".join(["dev", "screens", "password"])  # a placeholder, scratch database only
PEOPLE = [
    "new", "us", "ng", "active", "cancel", "downgrade", "pastdue", "threeds", "lapsed", "canceled",
    "low", "out", "pending", "ngactive", "lagos", "addis", "invoices", "refunded", "root",
    "rf1", "rf2", "rf3", "rf4", "rf5", "pro", "scale", "ngpro",
]  # fmt: skip
# A saved time zone and no billing country: the plans screen suggests the zone's country.
TIME_ZONES = {"lagos": "Africa/Lagos", "addis": "Africa/Addis_Ababa"}
LONG_AGO = datetime(2026, 1, 1, tzinfo=UTC)


class ScriptedProvider(FakeProvider):
    """The test double, with a hosted page of its own that a browser can open."""

    def start_checkout(self, payment, payer, return_url):
        checkout = super().start_checkout(payment, payer, return_url)
        return type(checkout)(url=f"{WEB}/dev/pay/{payment.reference}", reference=payment.reference)


USD = ScriptedProvider(name="flutterwave")
NGN = ScriptedProvider(name="paystack")
USD.payer_action_url = f"{WEB}/dev/3ds"
NGN.payer_action_url = f"{WEB}/dev/3ds"
PROVIDER_FACTORIES["flutterwave"] = lambda settings: USD
PROVIDER_FACTORIES["paystack"] = lambda settings: NGN
PROVIDERS = {"flutterwave": USD, "paystack": NGN}

router = APIRouter()  # mounted at /dev as a sub-app, so the app's login guard does not cover it
SETTINGS: Settings


def email_of(name: str) -> str:
    return f"{name}@example.test"


def user_for(session: Session, name: str) -> User:
    return session.scalars(select(User).where(User.email == email_of(name))).one()


def deliver(provider: ScriptedProvider, tx, *, kind: str = "charge_succeeded") -> int:
    """Send the webhook the real route would get, signed like the double signs it."""
    body = provider.event_body(provider.event_for(tx, kind=kind))
    response = httpx.post(
        f"{API}/api/billing/webhooks/{provider.name}",
        content=body,
        headers={"Content-Type": "application/json", "X-Fake-Signature": provider.sign(body)},
    )
    return response.status_code


def pay(session: Session, payment: Payment, *, status: str = "successful", short: int = 0) -> None:
    provider = PROVIDERS[payment.provider]
    tx = provider.complete(
        payment,
        status=status,
        amount_minor=payment.amount_minor - short,
        card_token="tok-dev",
        payer_email="payer@example.test",
    )
    deliver(provider, tx, kind="charge_failed" if status == "failed" else "charge_succeeded")


def age(session: Session, user: User, overdue: timedelta) -> None:
    """Move the stored dates so the paid period ended `overdue` ago (a month cannot be waited)."""
    sub = subs.live_row(session, user.id)
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
    session.execute(
        update(HourGrant)
        .where(HourGrant.subscription_id == sub.id, HourGrant.kind == "plan_period")
        .values(starts_at=HourGrant.starts_at - shift, expires_at=HourGrant.expires_at - shift)
    )
    session.commit()


def clock(session: Session) -> None:
    renewals.run_due(session, datetime.now(UTC), SETTINGS)
    session.commit()


def burn(session: Session, user: User, seconds: int) -> None:
    """A recording of `seconds` counted for the person, the way the pipeline counts one."""
    template = session.scalars(select(Template).where(Template.owner_user_id == user.id)).first()
    if template is None:
        template = Template(owner_user_id=user.id, name="General", instructions="Write notes.")
        session.add(template)
        session.flush()
    n = session.scalar(select(Meeting.id).order_by(Meeting.id.desc()).limit(1)) or 0
    meeting = Meeting(
        owner_user_id=user.id,
        url=f"https://zoom.us/j/{900 + n}",
        platform="zoom",
        native_id=str(900 + n),
        template_id=template.id,
        status="ready",
        step="done",
    )
    session.add(meeting)
    session.flush()
    usage.record_usage(session, meeting, seconds, now=datetime.now(UTC), settings=SETTINGS)
    session.commit()


def buy(session: Session, user: User, plan: str = "plan_20h", country: str = "US") -> Payment:
    profiles.change_country(session, user, country, now=LONG_AGO)
    session.commit()
    started = subs.start_plan_checkout(session, user, plan, SETTINGS, datetime.now(UTC))
    session.commit()
    pay(session, started.payment)
    session.expire_all()
    return started.payment


def pack(session: Session, user: User) -> None:
    started = subs.start_pack_checkout(session, user, "pack_10h", SETTINGS, datetime.now(UTC))
    session.commit()
    pay(session, started.payment)
    session.expire_all()


SCENARIOS = {
    "new": "no billing country, never had a plan",
    "us": "billing country US chosen, no plan",
    "ng": "billing country NG chosen, no plan (naira; /dev/ngn-prices?on=0 takes the prices away)",
    "active": "an active plan, 20 hours",
    "cancel": "an active plan with an extra-hours pack",
    "downgrade": "Pro (50 hours) with a downgrade to the 20-hour plan waiting for the renewal",
    "pro": "an active Pro plan (50 hours a month)",
    "scale": "an active Scale plan (100 hours a month)",
    "ngpro": "billed from Nigeria: an active Pro plan in naira",
    "pastdue": "the renewal failed an hour ago: the 3 days of grace",
    "threeds": "the renewal's charge waits for the bank (3-D Secure): the grace",
    "lapsed": "the grace ended unpaid",
    "canceled": "a plan that was cancelled and has run out",
    "low": "an active plan, 90% of the hours used",
    "out": "an active plan, every hour used",
    "pending": "a plan checkout started and not finished",
    "ngactive": "billed from Nigeria: an active plan in naira with an extra-hours pack",
    "invoices": "an active plan and an extra-hours pack, each with its invoice (S9)",
    "refunded": "a first plan payment the administrator refunded (S9)",
    "adminboard": "five payments for the administrator's refund screen: sign in as root (S9)",
    "lagos": "time zone Africa/Lagos, no billing country: Nigeria is suggested",
    "addis": "time zone Africa/Addis_Ababa, no billing country: Ethiopia is suggested",
}


@router.get("/")
def index():
    return {
        "people": {name: email_of(name) for name in PEOPLE},
        "password": PASSWORD,
        "scenarios": SCENARIOS,
    }


def refund(session: Session, payment: Payment, reason: str):
    admin = user_for(session, "root")
    return refunds.request(session, payment.id, reason, admin, None, SETTINGS, datetime.now(UTC))


def adminboard(session: Session) -> None:
    """Five people's payments for the refund screen: one the rules let the administrator refund (a
    first plan), one pack, one whose hours were used (refused, and says why), a double charge, and a
    refund that got no answer from the provider (waits for a person)."""
    from app.billing.payments import new_payment

    one, two, three, four, five = (user_for(session, f"rf{n}") for n in range(1, 6))
    buy(session, one)
    buy(session, two)
    pack(session, two)
    buy(session, three)
    burn(session, three, 3600)
    buy(session, four)
    duplicate = new_payment(
        session, payer=four, provider="flutterwave", kind="plan", currency="USD", amount_minor=1800
    )
    duplicate.plan_price_id = session.scalar(
        select(PlanPrice.id).where(PlanPrice.plan_code == "plan_20h", PlanPrice.currency == "USD")
    )
    session.commit()
    pay(session, duplicate)
    session.expire_all()
    first = buy(session, five)
    USD.refund_error = OutcomeUnknown("no answer")
    try:
        refund(session, first, "first_plan_7d")
    finally:
        USD.refund_error = None


@router.get("/scenario/{name}")
def scenario(name: str):
    if name not in SCENARIOS:
        raise HTTPException(404, "no such scenario")
    now = datetime.now(UTC)
    with SessionLocal() as session:
        if name == "adminboard":
            adminboard(session)
            return {"scenario": name, "email": email_of("root"), "password": PASSWORD}
        user = user_for(session, name)
        if name in ("invoices", "refunded"):
            payment = buy(session, user)
            if name == "invoices":
                pack(session, user)
            else:
                refund(session, payment, "first_plan_7d")
        if name in ("us", "pending"):
            profiles.change_country(session, user, "US", now=LONG_AGO)
            session.commit()
        if name == "ng":
            profiles.change_country(session, user, "NG", now=LONG_AGO)
            session.commit()
        if name == "pending":
            subs.start_plan_checkout(session, user, "plan_20h", SETTINGS, now)
            session.commit()
        if name in (
            "active",
            "cancel",
            "downgrade",
            "pastdue",
            "threeds",
            "lapsed",
            "canceled",
            "low",
            "out",
            "pro",
            "scale",
        ):
            buy(
                session,
                user,
                {"downgrade": "plan_50h", "pro": "plan_50h", "scale": "plan_100h"}.get(
                    name, "plan_20h"
                ),
            )
        if name == "ngpro":
            buy(session, user, "plan_50h", "NG")
        if name == "ngactive":
            buy(session, user, "plan_20h", "NG")
            pack(session, user)
        if name == "cancel":
            pack(session, user)
        if name == "downgrade":
            subs.change_plan(session, user, "plan_20h", SETTINGS, now)
            session.commit()
        if name in ("pastdue", "threeds", "lapsed"):
            provider = USD
            provider.charge_outcome = "pending" if name == "threeds" else "failed"
            age(session, user, timedelta(hours=80 if name == "lapsed" else 1))
            clock(session)
            provider.charge_outcome = "successful"
        if name == "canceled":
            subs.cancel(session, user, now)
            session.commit()
            age(session, user, timedelta(hours=2))
            clock(session)
        if name in ("low", "out"):
            burn(session, user, 72000 if name == "out" else int(72000 * 0.9))
    return {"scenario": name, "email": email_of(name), "password": PASSWORD}


@router.get("/ngn-prices")
def ngn_prices(on: int = 1):
    """The naira prices are seeded; this switches them off (a person billed from Nigeria then sees
    every plan as not on sale) or back on."""
    with SessionLocal() as session:
        value = None if on else datetime.now(UTC)
        session.execute(
            update(PlanPrice).where(PlanPrice.currency == "NGN").values(active_until=value)
        )
        session.commit()
    return {"ngn_prices": bool(on)}


def page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        f"<!doctype html><meta charset=utf-8><title>{html.escape(title)}</title>"
        "<body style='font:16px system-ui;max-width:34rem;margin:3rem auto;padding:0 1rem'>"
        f"<h1>{html.escape(title)}</h1>{body}"
        "<p style='color:#666'>This is the SCRIPTED provider of the local dev stack, "
        "not a real payment page.</p>"
    )


@router.get("/pay/{reference}", response_class=HTMLResponse)
def provider_page(reference: str):
    with SessionLocal() as session:
        payment = session.scalars(select(Payment).where(Payment.reference == reference)).first()
        if payment is None:
            raise HTTPException(404, "no such payment")
        what = f"{payment.kind}, {payment.amount_minor / 100:.2f} {payment.currency}"
    buttons = "".join(
        f"<form method=post action='/dev/pay/{reference}/{action}' style='margin:.5rem 0'>"
        f"<button style='font:inherit;padding:.5rem 1rem'>{label}</button></form>"
        for action, label in (
            ("paid", "Pay (the card works)"),
            ("failed", "Pay (the card is declined)"),
            ("wrong_amount", "Pay (the provider reports 1 unit too little)"),
            ("cancel", "Go back without paying"),
        )
    )
    return page(
        "Scripted payment page",
        f"<p>{html.escape(what)}</p><p>{html.escape(reference)}</p>{buttons}",
    )


@router.post("/pay/{reference}/{action}")
def provider_action(reference: str, action: str):
    with SessionLocal() as session:
        payment = session.scalars(select(Payment).where(Payment.reference == reference)).first()
        if payment is None:
            raise HTTPException(404, "no such payment")
        if action == "paid":
            pay(session, payment)
        elif action == "failed":
            pay(session, payment, status="failed")
        elif action == "wrong_amount":
            pay(session, payment, short=1)
        elif action != "cancel":
            raise HTTPException(404, "no such action")
    # Paystack adds `reference` and `trxref`; Flutterwave's `tx_ref` and `status` are read too.
    extra = "&status=cancelled" if action == "cancel" else ""
    return RedirectResponse(
        f"{WEB}/billing/return?reference={reference}&trxref={reference}{extra}", status_code=303
    )


@router.get("/3ds", response_class=HTMLResponse)
def bank_page():
    return page(
        "Scripted bank confirmation",
        "<form method=post action='/dev/3ds'><button>Confirm</button></form>",
    )


@router.post("/3ds")
def bank_confirm():
    with SessionLocal() as session:
        payment = session.scalars(
            select(Payment)
            .where(Payment.status == "pending", Payment.kind == "renewal")
            .order_by(Payment.id.desc())
        ).first()
        if payment is not None:
            pay(session, payment)
    return RedirectResponse(f"{WEB}/billing", status_code=303)


def main() -> None:
    global SETTINGS
    if not sys.argv[1:]:
        sys.exit(__doc__)
    base.prepare_database()
    SETTINGS = Settings(
        _env_file=None,
        app_env="test",
        session_secret="dev-screens-secret-" + "x" * 30,
        cookie_secure=False,
        database_url=base.SCRATCH_URL,
        public_base_url=WEB,
        token_enc_key=base.KEY,
        billing_enabled=True,
        billing_allowed_emails="*",
        hours_mode="enforce",
        feature_gates_mode="enforce",
    )
    app = create_app(SETTINGS)
    app.dependency_overrides[get_vexa] = lambda: base.FakeVexa()
    dev = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    dev.include_router(router)
    app.mount("/dev", dev)
    with SessionLocal() as session:
        for name in PEOPLE:
            session.add(
                User(
                    email=email_of(name),
                    password_hash=hash_password(PASSWORD),
                    email_verified_at=datetime.now(UTC),
                    is_admin=name == "root",
                )
            )
        session.flush()
        for name, zone in TIME_ZONES.items():
            session.add(UserSettings(user_id=user_for(session, name).id, time_zone=zone))
        session.commit()  # the plans are the real seeded rows (0052 and 0054): no dev-only plan
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=API_PORT, log_level="warning")
    )
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    print(
        json.dumps(
            {"api": API, "web": WEB, "password": PASSWORD, "scenarios": "GET /dev/"}, indent=2
        ),
        flush=True,
    )
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        server.should_exit = True


if __name__ == "__main__":
    main()
