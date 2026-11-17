"""Drive the Paystack webhook route over real HTTP and save every exchange (billing stage S6).

    python scripts/capture_paystack_webhook.py postgresql+psycopg://USER:PASS@localhost:PORT/postgres

Same setup as `scripts/capture_billing_profile.py` (it imports `capture_calendar_connect` for the
scratch database and the app server): real migrations, the real app under uvicorn, real HTTP to the
real webhook route, the real settlement rules and the real `PaystackProvider`.

**What is NOT real: Paystack itself.** There are no Paystack test keys yet, so the calls the adapter
makes to Paystack (verify) go to a script in this file that answers with the *documented samples*
in `tests/fixtures/paystack/` (page names in its README), changed only where a case needs it (our
reference, the amount, a float). The keys and the webhook secret are placeholders that exist only
on the scratch database run. Every file says so in its note. No request in this run reaches the
internet.

The route is called by Paystack, never by the browser: the frontend calls nothing here. The files
exist so the answers the route gives (and what each one leaves in the database) are copied, not
written from memory.

Files go to `docs/fe-captures/paystack-webhook/NN-name.json`.
"""

import hashlib
import hmac
import json
import shutil
import sys
import time
from datetime import UTC, datetime

import capture_calendar_connect as base  # reads sys.argv at import (the admin database URL)
import httpx

OUT = base.ROOT / "docs" / "fe-captures" / "paystack-webhook"
base.OUT = OUT

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.billing.payments import new_payment  # noqa: E402
from app.billing.paystack import PaystackProvider  # noqa: E402
from app.billing.provider import PROVIDER_FACTORIES  # noqa: E402
from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import BillingEvent, Payment, User  # noqa: E402
from app.passwords import hash_password  # noqa: E402
from app.services import get_vexa  # noqa: E402

FIXTURES = base.ROOT / "tests" / "fixtures" / "paystack"
# Placeholders, never keys: they exist only for this run against a scratch database.
SECRET_KEY = "sk_test_capture_placeholder_not_a_key"
WEBHOOK_SECRET = "whsec_capture_placeholder_not_a_key"
OLD_WEBHOOK_SECRET = "whsec_capture_old_placeholder_not_a_key"
URL = "/api/billing/webhooks/paystack"
ASKED = 150_000  # NGN 1,500.00 in kobo
SAMPLES = "the documented Paystack samples (tests/fixtures/paystack/README.md), not a real Paystack"
HEADER_SHAPE = "t=<unix seconds>,v1=<key id>:<128 lowercase hex>"

count = 0


def sample(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


class ScriptedPaystack:
    """Answers the adapter's verify call from the documented sample. `next_answer` is what the
    next call returns; `calls` is every request the adapter made."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.next_answer: dict = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(
            {
                "method": request.method,
                "path": request.url.path,
                "authorization": "Bearer <the secret key>",
            }
        )
        return httpx.Response(200, json=self.next_answer)

    def answer(self, payment: Payment, **over) -> None:
        body = sample("verify_response.json")
        body["data"].update(
            id=4099260516,
            reference=payment.reference,
            amount=payment.amount_minor,
            currency="NGN",
        )
        body["data"].update(over)  # a case may change the id, amount, currency or status
        self.next_answer = body


def sign(body: bytes, secret: str = WEBHOOK_SECRET, *, t: int | None = None) -> str:
    stamp = int(time.time()) if t is None else t
    digest = hmac.new(secret.encode(), f"{stamp}.".encode() + body, hashlib.sha512).hexdigest()
    return f"t={stamp},v1=whk_capture:{digest}"


def event_body(payment: Payment, tx_id: int = 4099260516, **data_over) -> bytes:
    body = sample("charge_success_event.json")
    body["data"].update(
        id=tx_id, reference=payment.reference, amount=payment.amount_minor, currency="NGN"
    )
    body["data"].update(data_over)
    return json.dumps(body, indent=2).encode()


def save(name: str, record: dict) -> None:
    global count
    count += 1
    (OUT / f"{count:02d}-{name}.json").write_text(
        json.dumps(record, indent=2, ensure_ascii=False) + "\n"
    )
    status = record.get("response", {}).get("status", "")
    print(f"{count:02d}-{name:46} {status}")


def post(server_base, name, body: bytes, *, header=None, note="", headers=None, scripted=None):
    """One webhook delivery over real HTTP, saved. The signature header is shown by shape: the
    real value is a function of the body and a placeholder secret and means nothing alone."""
    sent = {"Content-Type": "application/json", **(headers or {})}
    if header is not None:
        sent["Paystack-Signature"] = header
    asked_before = len(scripted.calls) if scripted else 0
    response = httpx.post(server_base + URL, content=body, headers=sent)
    try:
        shown_body = response.json()
    except ValueError:
        shown_body = response.text
    shown_headers = {
        k: (HEADER_SHAPE if k.lower() == "paystack-signature" else v) for k, v in sent.items()
    }
    record = {
        "note": f"{note} (Paystack's API side: {SAMPLES}.)",
        "request": {
            "method": "POST",
            "path": URL,
            "headers": shown_headers,
            "body": json.loads(body) if body.startswith((b"{", b"[")) else body.decode(),
        },
        "response": {"status": response.status_code, "body": shown_body},
    }
    if scripted:
        record["calls_to_paystack_made_by_the_adapter"] = scripted.calls[asked_before:]
    save(name, record)
    return response


def rows(engine) -> dict:
    with Session(engine) as session:
        payments = [
            {
                "reference": p.reference,
                "status": p.status,
                "provider_tx_id": p.provider_tx_id,
                "card_country": p.card_country,
                "country_mismatch": p.country_mismatch,
            }
            for p in session.scalars(select(Payment).order_by(Payment.id))
        ]
        events = [
            {
                "provider": e.provider,
                "dedupe_key": e.dedupe_key,
                "type": e.type,
                "decided": e.processed_at is not None,
                "error": e.error,
            }
            for e in session.scalars(select(BillingEvent).order_by(BillingEvent.id))
        ]
    return {"payments": payments, "billing_events": events}


def make_payment(engine, email: str) -> Payment:
    with Session(engine) as session:
        payer = User(
            email=email,
            password_hash=hash_password("-".join(["capture", "person", "password"])),
            email_verified_at=datetime.now(UTC),
        )
        session.add(payer)
        session.flush()
        payment = new_payment(
            session,
            payer=payer,
            provider="paystack",
            kind="plan",
            currency="NGN",
            amount_minor=ASKED,
        )
        session.commit()
        session.refresh(payment)
        session.expunge(payment)
        return payment


def run(engine, on_base, off_base, script: ScriptedPaystack) -> None:
    pay = make_payment(engine, "ada@example.test")
    body = event_body(pay)

    post(
        off_base,
        "billing-off",
        body,
        header=sign(body),
        note="BILLING_ENABLED is false (the default): 404 before the body is read; nothing stored.",
    )

    script.answer(pay)
    post(
        on_base,
        "paid",
        body,
        header=sign(body),
        scripted=script,
        note="A signed charge.success. The adapter asks Paystack for the transaction by id, finds "
        "our reference, NGN and the amount we asked for, and the payment becomes succeeded.",
    )
    save(
        "paid-rows",
        {"note": "The rows after that delivery.", "content": rows(engine)},
    )

    post(
        on_base,
        "replay",
        body,
        header=sign(body),
        scripted=script,
        note="The same event delivered again (a retry is signed again with a new timestamp): 200, "
        "no second call to Paystack, no second event row.",
    )
    save("replay-rows", {"note": "Still one event row.", "content": rows(engine)})

    other = make_payment(engine, "ben@example.test")
    other_body = event_body(other, tx_id=4099260999)

    post(
        on_base,
        "tampered",
        other_body.replace(b"150000", b"1"),
        header=sign(other_body),
        scripted=script,
        note="One changed byte in a signed body: 401 `The signature is not valid.`, nothing "
        "stored, Paystack not asked.",
    )
    post(
        on_base,
        "stale-timestamp",
        other_body,
        header=sign(other_body, t=int(time.time()) - 400),
        scripted=script,
        note="A correct signature over a timestamp 400 seconds old: outside the 300-second window, "
        "401.",
    )
    post(
        on_base,
        "unsigned",
        other_body,
        scripted=script,
        note="No Paystack-Signature header at all: 401.",
    )
    post(
        on_base,
        "ping-ignored",
        (ping := json.dumps({"event": "ping", "data": {}}).encode()),
        header=sign(ping),
        scripted=script,
        note="A genuine event we do not act on (ping, invoice.*, transfer.*): 200 with status "
        "`ignored`, stored nowhere, so Paystack does not resend it for 72 hours.",
    )

    script.answer(other, id=4099260999, amount=float(ASKED))
    post(
        on_base,
        "float-amount",
        other_body,
        header=sign(other_body),
        scripted=script,
        note="Paystack's answer carries amount 150000.0 (a float): never rounded, never trusted. "
        "503 `Try again later.`; the payment stays pending and the event stays waiting.",
    )
    save("float-amount-rows", {"note": "Waiting, not decided.", "content": rows(engine)})

    script.answer(other, id=4099260999)
    t = int(time.time())
    digest_old = hmac.new(
        OLD_WEBHOOK_SECRET.encode(), f"{t}.".encode() + other_body, hashlib.sha512
    ).hexdigest()
    digest_new = hmac.new(
        WEBHOOK_SECRET.encode(), f"{t}.".encode() + other_body, hashlib.sha512
    ).hexdigest()
    post(
        on_base,
        "retry-two-signatures",
        other_body,
        header=f"t={t},v1=whk_old:{digest_old},v1=whk_new:{digest_new}",
        scripted=script,
        note="Paystack's resend, during a secret rotation (two v1 signatures, one per secret); "
        "Paystack's answer is well formed this time. 200, the payment is settled.",
    )
    save("retry-rows", {"note": "Both payments are now succeeded.", "content": rows(engine)})

    third = make_payment(engine, "cy@example.test")
    third_body = event_body(third, tx_id=4099261111)
    script.answer(third, id=4099261111, amount=ASKED - 1)
    post(
        on_base,
        "amount-too-low",
        third_body,
        header=sign(third_body),
        scripted=script,
        note="Paystack's answer says 149999, one kobo under what we asked: refused. The reply is "
        "200 (a final answer, so Paystack stops resending); the reason is in the stored event.",
    )
    save(
        "amount-too-low-rows", {"note": "Refused, payment still pending.", "content": rows(engine)}
    )


def main() -> None:
    if not sys.argv[1:]:
        sys.exit(__doc__)
    base.prepare_database()
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True)

    script = ScriptedPaystack()

    def settings_for(**extra) -> Settings:
        return Settings(
            _env_file=None,
            app_env="test",
            session_secret="capture-secret-" + "x" * 30,
            cookie_secure=False,
            database_url=base.SCRATCH_URL,
            public_base_url="https://notes.example.test",
            token_enc_key=base.KEY,
            paystack_secret_key=SECRET_KEY,
            paystack_webhook_secret=WEBHOOK_SECRET,
            **extra,
        )

    # The adapter is the real one; only its transport (the line to Paystack) is the script.
    provider = PaystackProvider(
        secret_key=SECRET_KEY, webhook_secret=WEBHOOK_SECRET, transport=httpx.MockTransport(script)
    )
    PROVIDER_FACTORIES["paystack"] = lambda settings: provider

    apps = [create_app(settings_for(billing_enabled=True)), create_app(settings_for())]
    for each in apps:
        each.dependency_overrides[get_vexa] = lambda: base.FakeVexa()
    started = [base.start_server(each) for each in apps]
    engine = create_engine(base.SCRATCH_URL)
    try:
        run(engine, started[0][1], started[1][1], script)
    finally:
        for server, _ in started:
            server.should_exit = True
        engine.dispose()


if __name__ == "__main__":
    main()
