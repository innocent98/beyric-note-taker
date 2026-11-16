"""Drive the billing profile routes over real HTTP and save every exchange for the guide (stage S5).

    python scripts/capture_billing_profile.py postgresql+psycopg://USER:PASS@localhost:PORT/postgres

Same setup as `scripts/capture_hours_usage.py` (it imports `capture_calendar_connect` for the HTTP
recorder, the scratch database and the app server): real migrations, the real app under uvicorn,
real HTTP. Four copies of the app run on one database: billing on with the default routes, billing
off, and billing on with a route table that has only Nigeria and an allow-list of one account.

Files go to `docs/fe-captures/billing-profile/NN-name.json`. Passwords in login calls are
placeholders; the people are placeholders (`ada@example.test` and so on). Times are the capture
machine's real clock.

What is real: the app, the guard (login and CSRF), validation, the change rules, the history table.
What is NOT, said in each file's note where it matters:
- **A scheduled country change (live subscription) is NOT LIVE.** There is no subscription yet
  (stage S8), so no request can produce one. The waiting state is written into the profile row by
  SQL, in the shape the code makes, and the reads and refusals that follow are real HTTP against
  that row.
- The card-country note has no route (it is two columns on a payment); it is proved by the tests
  through the webhook route, not captured here.
"""

import shutil
import sys
from datetime import UTC, datetime, timedelta

import capture_calendar_connect as base  # reads sys.argv at import (the admin database URL)

OUT = base.ROOT / "docs" / "fe-captures" / "billing-profile"
base.OUT = OUT

from sqlalchemy import create_engine, select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import BillingCountryChange, BillingProfile, User  # noqa: E402
from app.passwords import hash_password  # noqa: E402
from app.services import get_vexa  # noqa: E402

Recorder = base.Recorder
PASSWORD = "-".join(["capture", "person", "password"])  # a placeholder, scratch database only
ADA, BEN = "ada@example.test", "ben@example.test"


def person(engine, email: str) -> int:
    with Session(engine) as session:
        user = User(
            email=email,
            password_hash=hash_password(PASSWORD),
            email_verified_at=datetime.now(UTC),
        )
        session.add(user)
        session.commit()
        return user.id


def signed_in(server_base: str, who: str, email: str) -> Recorder:
    client = Recorder(server_base, who)
    base.login(client, f"login-{who.split()[0]}", email, PASSWORD)
    return client


def history(engine) -> list[dict]:
    with Session(engine) as session:
        rows = session.scalars(select(BillingCountryChange).order_by(BillingCountryChange.id))
        return [
            {
                "user": session.get(User, r.user_id).email,
                "old_country": r.old_country,
                "new_country": r.new_country,
                "effective_at": r.effective_at.isoformat(),
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ]


def run(engine, server_base, off_base, narrow_base) -> None:
    person(engine, ADA)
    person(engine, BEN)

    # ---- billing off: a plain 404, nothing written ----
    off = signed_in(off_base, "ada flag-off", ADA)
    off.call(
        "off-profile",
        "GET",
        "/api/billing/profile",
        note="With BILLING_ENABLED false (the default) every billing route is a 404 `not_found`.",
    )
    off.call(
        "off-country",
        "PUT",
        "/api/billing/profile/country",
        body={"billing_country": "NG"},
        note="Same 404 for a write; nothing is stored.",
    )

    # ---- billing on ----
    visitor = Recorder(server_base, "visitor")
    visitor.call("visitor-profile", "GET", "/api/billing/profile", note="Not signed in: 401.")

    ada = signed_in(server_base, "ada billing-on", ADA)
    ada.call(
        "profile-empty",
        "GET",
        "/api/billing/profile",
        note="A person who has saved nothing: every field null, `route` null. Reading creates no "
        "row.",
    )
    ada.call(
        "route-ng",
        "GET",
        "/api/billing/route?country=NG",
        note="What Nigeria pays with. The screen says this before the person commits.",
    )
    ada.call(
        "route-gb",
        "GET",
        "/api/billing/route?country=gb",
        note="Any other country; lower case is read.",
    )
    ada.call(
        "route-missing",
        "GET",
        "/api/billing/route",
        note="No country: 422 `billing_country_missing`.",
    )
    ada.call(
        "route-name",
        "GET",
        "/api/billing/route?country=Nigeria",
        note="A name, not a code: 422 `billing_country_invalid`. Nothing is guessed.",
    )
    ada.call(
        "profile-save",
        "PUT",
        "/api/billing/profile",
        body={
            "name_or_company": "  Placeholder Company Ltd ",
            "address": "1 Placeholder Road\nLagos",
            "tax_id": "TIN-PLACEHOLDER-0001",
            "billing_email": " Billing@Example.TEST ",
        },
        note="Replaces the four details; text is trimmed and the email lower-cased. The country is "
        "not set here.",
    )
    ada.call(
        "profile-save-bad-email",
        "PUT",
        "/api/billing/profile",
        body={"billing_email": "not-an-address"},
        note="422 `billing_email_invalid`.",
    )
    ada.call(
        "profile-save-too-long",
        "PUT",
        "/api/billing/profile",
        body={"tax_id": "x" * 65},
        note="422 `validation_error` with the field named.",
    )
    ada.call(
        "profile-save-with-country",
        "PUT",
        "/api/billing/profile",
        body={"billing_country": "NG"},
        note="The country is not a field of this route: 422 `validation_error` (unknown field).",
    )
    ada.call(
        "profile-save-no-csrf",
        "PUT",
        "/api/billing/profile",
        body={},
        csrf=None,
        note="No CSRF header: 403 `csrf_rejected`.",
    )
    ada.call(
        "country-first",
        "PUT",
        "/api/billing/profile/country",
        body={"billing_country": "ng"},
        note="The first choice applies at once and is not counted as a change.",
    )
    ada.call(
        "country-same",
        "PUT",
        "/api/billing/profile/country",
        body={"billing_country": "NG"},
        note="The same country again: `unchanged`, nothing written.",
    )
    ada.call(
        "country-empty",
        "PUT",
        "/api/billing/profile/country",
        body={"billing_country": ""},
        note="422 `billing_country_missing`.",
    )
    ada.call(
        "country-not-a-code",
        "PUT",
        "/api/billing/profile/country",
        body={"billing_country": "UK"},
        note="`UK` is not a code (the United Kingdom is `GB`): 422 `billing_country_invalid`.",
    )
    ada.call(
        "country-second",
        "PUT",
        "/api/billing/profile/country",
        body={"billing_country": "GB"},
        note="A second change with no live subscription applies at once. "
        "`country_change_allowed_at` is now 30 days away.",
    )
    ada.call(
        "country-too-soon",
        "PUT",
        "/api/billing/profile/country",
        body={"billing_country": "FR"},
        note="A third inside 30 days: 409 `country_change_too_soon`. Nothing is written.",
    )
    ada.call("profile-after", "GET", "/api/billing/profile", note="The profile after those calls.")
    base.note_file(
        "history-rows",
        "The `billing_country_changes` rows after those calls (read from the database).",
        {"rows": history(engine)},
    )

    # ---- a scheduled change: NOT LIVE (see the module note) ----
    with Session(engine) as session:
        row = session.scalars(select(BillingProfile).join(User).where(User.email == ADA)).one()
        row.pending_billing_country = "NG"
        row.pending_effective_at = datetime.now(UTC) + timedelta(days=12)
        session.commit()
    ada.call(
        "pending-profile",
        "GET",
        "/api/billing/profile",
        note="**NOT LIVE.** The waiting state was written by SQL (no subscription exists to make "
        "one). `billing_country` and `route` are still the old ones.",
    )
    ada.call(
        "pending-change-refused",
        "PUT",
        "/api/billing/profile/country",
        body={"billing_country": "US"},
        note="**NOT LIVE state, real refusal.** 409 `country_change_pending` while one waits.",
    )

    # ---- another person ----
    ben = signed_in(server_base, "ben billing-on", BEN)
    ben.call(
        "other-person-profile",
        "GET",
        "/api/billing/profile",
        note="Another account sees only its own profile: Ada's details and country are not here.",
    )

    # ---- a route table with Nigeria only, and an allow-list of one account ----
    narrow_ada = signed_in(narrow_base, "ada narrow-table", ADA)
    narrow_ada.call(
        "narrow-unsupported",
        "PUT",
        "/api/billing/profile/country",
        body={"billing_country": "US"},
        note="Server with `BILLING_COUNTRY_ROUTES=NG=NGN:paystack` (Flutterwave switched off): a "
        "country with no route is 422 `billing_country_unsupported`.",
    )
    narrow_ben = signed_in(narrow_base, "ben narrow-table", BEN)
    narrow_ben.call(
        "narrow-not-allowed",
        "GET",
        "/api/billing/profile",
        note="Same server with `BILLING_ALLOWED_EMAILS=ada@example.test`: any other account gets "
        "the 404 `not_found` that billing-off gives.",
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

    configs = [
        settings_for(billing_enabled=True),
        settings_for(billing_enabled=False),
        settings_for(
            billing_enabled=True,
            billing_country_routes="NG=NGN:paystack",
            billing_allowed_emails=ADA,
        ),
    ]
    apps = [create_app(c) for c in configs]
    for each in apps:
        each.dependency_overrides[get_vexa] = lambda: base.FakeVexa()
    started = [base.start_server(each) for each in apps]
    engine = create_engine(base.SCRATCH_URL)
    try:
        run(engine, started[0][1], started[1][1], started[2][1])
        for path in OUT.glob("*.json"):  # no password, even a placeholder, stays in a file
            body = path.read_text()
            for secret in (PASSWORD, base.ADMIN_PASSWORD):
                body = body.replace(secret, "<password placeholder>")
            path.write_text(body)
        with engine.connect() as conn:  # leave nothing to confuse the next run
            conn.execute(text("select 1"))
    finally:
        for server, _ in started:
            server.should_exit = True
        engine.dispose()


if __name__ == "__main__":
    main()
