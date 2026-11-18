"""Drive the billing country flow over real HTTP and save every exchange for the guide.

    python scripts/capture_billing_country.py postgresql+psycopg://USER:PASS@localhost:PORT/postgres

Same setup as `scripts/capture_billing_profile.py`: real migrations (so the seeded plan and its
prices are the real rows), the real app under uvicorn, real HTTP, billing on and open to every
account. Files go to `docs/fe-captures/billing-country/NN-name.json`. Passwords in login calls are
placeholders; the people are placeholders (`lagos@example.test` and so on). Times are the capture
machine's real clock.

What it proves, in order:
1. **The suggestion.** A person whose saved time zone is `Africa/Lagos` gets Nigeria suggested, one
   in `Africa/Addis_Ababa` gets Ethiopia, one with no zone gets nothing. A read saves nothing.
2. **Free changes before the first payment.** One person picks Ethiopia, then Nigeria, then the
   United States: every change applies at once and no 30-day date is ever set.
3. **The plans preview** (`GET /plans?country=`): prices for a country that is not saved yet.
4. **After a payment** the rule is as before: one change applies, the next is refused with the
   date (409 `country_change_too_soon`). A change that crosses between Nigeria and elsewhere while
   a plan is live is `scheduled` for the end of the paid period, and a second one is refused
   (409 `country_change_pending`) with that date.

**What is NOT real:** the payment provider is `app.billing.fake.FakeProvider`, registered under the
names `flutterwave` and `paystack` (its checkout address is `https://fake.test/pay/<reference>`,
its webhook signature is `X-Fake-Signature`). Everything after the webhook (settlement, the
subscription, the hours, the lock) is the real code. Nothing here proves a real Paystack or
Flutterwave accepts these calls.
"""

import json
import shutil
import sys
from datetime import UTC, datetime

import capture_calendar_connect as base  # reads sys.argv at import (the admin database URL)

OUT = base.ROOT / "docs" / "fe-captures" / "billing-country"
base.OUT = OUT

import httpx  # noqa: E402
from sqlalchemy import create_engine, select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.billing.fake import FakeProvider  # noqa: E402
from app.billing.provider import PROVIDER_FACTORIES  # noqa: E402
from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import BillingCountryChange, Payment, User  # noqa: E402
from app.passwords import hash_password  # noqa: E402
from app.services import get_vexa  # noqa: E402

Recorder = base.Recorder
PASSWORD = "-".join(["capture", "person", "password"])  # a placeholder, scratch database only
PEOPLE = {
    "lagos": "lagos@example.test",
    "addis": "addis@example.test",
    "nozone": "nozone@example.test",
    "free": "free@example.test",
    "paid": "paid@example.test",
    "cross": "cross@example.test",
}
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


def deliver(server_base: str, name: str, provider: FakeProvider, tx, note: str) -> None:
    """Send the provider's webhook for `tx` to the real route, signed like the fake signs, and save
    the exchange."""
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
            "headers": {"Content-Type": "application/json", "X-Fake-Signature": "<signature>"},
            "body": json.loads(body),
        },
        "response": {"status": response.status_code, "body": response.json()},
    }
    (OUT / f"{Recorder.count:02d}-{name}.json").write_text(
        json.dumps(record, indent=2, ensure_ascii=False) + "\n"
    )
    print(f"{Recorder.count:02d}-{name:44} POST  webhook -> {response.status_code}")


def pays(engine, client: Recorder, server_base: str, who: str, email: str) -> None:
    """The person buys the plan through the real checkout and the real webhook (fake provider)."""
    started = client.call(
        f"{who}-checkout-plan",
        "POST",
        "/api/billing/checkout",
        body={"kind": "plan", "code": "plan_20h"},
        note="Starts the hosted checkout on the saved country's route. The country is saved BEFORE "
        "this call; a checkout never uses a suggestion.",
    ).json()
    with Session(engine) as session:
        payment = session.scalars(
            select(Payment).where(Payment.reference == started["payment"]["reference"])
        ).one()
        provider = USD if payment.provider == "flutterwave" else NGN
        tx = provider.complete(payment, card_token=f"tok-{who}", payer_email=email)
    deliver(
        server_base,
        f"{who}-webhook-paid",
        provider,
        tx,
        "The genuine webhook: the payment is verified and the plan starts. From here the person's "
        "billing country is locked (`country_locked` true).",
    )


def history(engine) -> list[dict]:
    with Session(engine) as session:
        rows = session.scalars(select(BillingCountryChange).order_by(BillingCountryChange.id))
        return [
            {
                "user": session.get(User, r.user_id).email,
                "old_country": r.old_country,
                "new_country": r.new_country,
                "effective_at": r.effective_at.isoformat(),
            }
            for r in rows
        ]


def run(engine, server_base: str) -> None:
    for email in PEOPLE.values():
        person(engine, email)

    # ---- 1. the suggestion, from the saved time zone ----
    lagos = signed_in(server_base, "lagos", PEOPLE["lagos"])
    lagos.call(
        "lagos-set-zone",
        "PUT",
        "/api/settings/time-zone",
        body={"time_zone": "Africa/Lagos"},
        note="The person's own saved time zone (the existing Settings route).",
    )
    lagos.call(
        "lagos-profile-suggests-nigeria",
        "GET",
        "/api/billing/profile",
        note="No country saved: `billing_country` and `route` are null, and `suggested_country` is "
        "`NG` with the route it pays on. A read saves nothing.",
    )
    addis = signed_in(server_base, "addis", PEOPLE["addis"])
    addis.call(
        "addis-set-zone",
        "PUT",
        "/api/settings/time-zone",
        body={"time_zone": "Africa/Addis_Ababa"},
        note="Another zone.",
    )
    addis.call(
        "addis-profile-suggests-ethiopia",
        "GET",
        "/api/billing/profile",
        note="`suggested_country` `ET`, on the US dollar route.",
    )
    nozone = signed_in(server_base, "nozone", PEOPLE["nozone"])
    nozone.call(
        "nozone-profile-no-suggestion",
        "GET",
        "/api/billing/profile",
        note="No saved time zone: both suggested fields are null. The screen then falls back to "
        "the browser's language region (read in the browser, not on the server), then the picker.",
    )

    # ---- 3. the plans preview ----
    lagos.call(
        "lagos-plans-no-country",
        "GET",
        "/api/billing/plans",
        note="No country saved and none asked for: `route` null, no plans.",
    )
    lagos.call(
        "lagos-plans-preview-ng",
        "GET",
        "/api/billing/plans?country=NG",
        note="A preview for the suggested country: prices in naira. Nothing is saved.",
    )
    lagos.call(
        "lagos-plans-preview-et",
        "GET",
        "/api/billing/plans?country=et",
        note="A preview for any other country: US dollars. Lower case is read.",
    )
    lagos.call(
        "lagos-plans-preview-bad",
        "GET",
        "/api/billing/plans?country=Nigeria",
        note="A name, not a code: 422 `billing_country_invalid`.",
    )
    lagos.call(
        "lagos-profile-still-unsaved",
        "GET",
        "/api/billing/profile",
        note="After the previews the country is still not saved.",
    )
    lagos.call(
        "lagos-country-confirmed",
        "PUT",
        "/api/billing/profile/country",
        body={"billing_country": "NG"},
        note="The person confirms the suggestion (the button, or the plans screen just before it "
        "starts a checkout). Applied at once.",
    )
    lagos.call(
        "lagos-profile-after-confirm",
        "GET",
        "/api/billing/profile",
        note="Saved: the suggestion is gone (both suggested fields null).",
    )

    # ---- 2. free changes before the first payment ----
    free = signed_in(server_base, "free", PEOPLE["free"])
    for name, country, note in (
        ("first-et", "ET", "The first choice, made by mistake."),
        ("second-ng", "NG", "Changed straight away: applied."),
        ("third-us", "US", "Changed again: applied. No limit before the first payment."),
    ):
        free.call(
            f"free-country-{name}",
            "PUT",
            "/api/billing/profile/country",
            body={"billing_country": country},
            note=note + " `country_locked` is false and `country_change_allowed_at` is null.",
        )
    free.call("free-profile", "GET", "/api/billing/profile", note="Still free to change.")
    base.note_file(
        "free-history-rows",
        "The `billing_country_changes` rows (read from the database): every change is written, "
        "free or not.",
        {"rows": [r for r in history(engine) if r["user"] == PEOPLE["free"]]},
    )

    # ---- 4. after a payment ----
    paid = signed_in(server_base, "paid", PEOPLE["paid"])
    paid.call(
        "paid-country-first",
        "PUT",
        "/api/billing/profile/country",
        body={"billing_country": "US"},
        note="Chosen before paying.",
    )
    paid.call(
        "paid-country-free-change",
        "PUT",
        "/api/billing/profile/country",
        body={"billing_country": "GB"},
        note="Changed before paying: free.",
    )
    pays(engine, paid, server_base, "paid", PEOPLE["paid"])
    paid.call(
        "paid-profile-after-payment",
        "GET",
        "/api/billing/profile",
        note="`country_locked` is true. `country_change_allowed_at` is still null: the changes "
        "made while the country was free do not count towards the 30 days.",
    )
    paid.call(
        "paid-country-one-change",
        "PUT",
        "/api/billing/profile/country",
        body={"billing_country": "FR"},
        note="One change is allowed. Same currency and provider (dollars, Flutterwave), so it "
        "applies at once; `country_change_allowed_at` is now 30 days away.",
    )
    paid.call(
        "paid-country-too-soon",
        "PUT",
        "/api/billing/profile/country",
        body={"billing_country": "DE"},
        note="The next one: 409 `country_change_too_soon`, with the date in the message. Nothing "
        "is written.",
    )
    cross = signed_in(server_base, "cross", PEOPLE["cross"])
    cross.call(
        "cross-country-first",
        "PUT",
        "/api/billing/profile/country",
        body={"billing_country": "US"},
        note="Chosen before paying.",
    )
    pays(engine, cross, server_base, "cross", PEOPLE["cross"])
    cross.call(
        "cross-country-scheduled",
        "PUT",
        "/api/billing/profile/country",
        body={"billing_country": "NG"},
        note="A live plan on the dollar route and a change to Nigeria (naira): `scheduled` for the "
        "end of the paid period. `billing_country` stays `US` until then.",
    )
    cross.call(
        "cross-profile-pending",
        "GET",
        "/api/billing/profile",
        note="`pending_billing_country` and `pending_effective_at` say what waits and when.",
    )
    cross.call(
        "cross-country-pending-refused",
        "PUT",
        "/api/billing/profile/country",
        body={"billing_country": "GB"},
        note="409 `country_change_pending`, with the date the waiting change takes effect.",
    )


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
    server, server_base = base.start_server(app)
    engine = create_engine(base.SCRATCH_URL)
    try:
        run(engine, server_base)
        for path in OUT.glob("*.json"):  # no password, even a placeholder, stays in a file
            body = path.read_text()
            for secret in (PASSWORD, base.ADMIN_PASSWORD):
                body = body.replace(secret, "<password placeholder>")
            path.write_text(body)
        with engine.connect() as conn:
            conn.execute(text("select 1"))
    finally:
        server.should_exit = True
        engine.dispose()


if __name__ == "__main__":
    main()
