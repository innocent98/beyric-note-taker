"""Drive the trial and the pilot expiry over real HTTP and save every exchange (stage S10).

    python scripts/capture_trial.py postgresql+psycopg://USER:PASS@localhost:PORT/postgres

Same harness as `scripts/capture_pilot_api.py` (a scratch database with the real migrations, the
real app under uvicorn, httpx). Files go to `docs/fe-captures/trial/NN-name.json`.

What is real: the app, the routes, the password sign-up, the mailed link read back from the
outbox, the email confirmation that gives the trial, the grants, the entitlement rule, the 403
refusals, the notices, the database rows, and the pilot-expiry job (`pilot_expiry.run`).

What is NOT, said in each file's note where it matters:
- **Google sign-in is not captured**: the other two confirmation paths (a new Google account, and
  Google confirming an unconfirmed password account) call the same `trial.give_on_confirmation`;
  they are covered by `tests/test_trial.py` only.
- The 'counted' recordings are counted by calling `usage.record_usage` in this process, the way
  the pipeline does, because no real meeting was recorded: no bot, no audio.
- **The pilot job runs in this process with an injected clock** (three days before the end), then
  the app answers with the real clock, after that end: the server cannot be moved in time.
- Deleting an account and signing up again has no route in the API; it is covered by a test of
  `deletion.erase_account_row` and the fingerprint, not captured here.
- Mail is not sent: the outbox row is what the worker would send.
"""

import shutil
import sys
from datetime import UTC, datetime, timedelta

import capture_pilot_api as P  # reads sys.argv at import (the admin database URL)

OUT = P.ROOT / "docs" / "fe-captures" / "trial"
P.OUT = OUT

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import alerts, pilot_expiry, usage  # noqa: E402
from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import HourGrant, Meeting, Template, User  # noqa: E402
from app.passwords import hash_password  # noqa: E402
from app.services import get_vexa  # noqa: E402
from app.usage import email_hash  # noqa: E402

Recorder = P.Recorder
HOUR = 3600
MEET = "https://meet.google.com/abc-defg-hij"
PEOPLE = {  # placeholders, not real people
    "ada": "ada@example.test",
    "ben": "ben@example.test",
    "paula": "paula@example.test",
}
PASSWORD = "-".join(["chosen", "capture", "password", "1"])


def sign_up(visitor, engine, who: str) -> None:
    email = PEOPLE[who]
    visitor.call(
        f"signup-{who}",
        "POST",
        "/api/signup",
        body={
            "name": f"{who.title()} Example",
            "email": email,
            "password": PASSWORD,
            "accept_terms": True,
            "accept_recording": True,
        },
        note="202: the confirmation link is mailed.",
    )
    token = P.token_of(P.outbox(engine, "verify_email", email))
    visitor.call(
        f"verify-{who}",
        "POST",
        "/api/verify-email",
        body={"token": token, "password": PASSWORD},
        note="Confirming the email is what gives the trial (TRIAL_SECONDS is set on this app). The "
        "response is the same as it was before the trial existed.",
    )


def grants(engine, email: str) -> list[dict]:
    with Session(engine) as session:
        rows = session.scalars(
            select(HourGrant)
            .where(HourGrant.email_hash == email_hash(email))
            .order_by(HourGrant.id)
        ).all()
        return [
            {
                "kind": g.kind,
                "seconds": g.seconds,
                "starts_at": g.starts_at.isoformat(),
                "expires_at": g.expires_at.isoformat() if g.expires_at else None,
            }
            for g in rows
        ]


def count(engine, settings, email: str, seconds: int, code: str) -> None:
    with Session(engine) as session:
        user = session.query(User).filter_by(email=email).one()
        template = session.query(Template).filter_by(owner_user_id=user.id).first()
        meeting = Meeting(
            owner_user_id=user.id,
            url=f"https://meet.google.com/{code}",
            platform="google_meet",
            native_id=code,
            template_id=template.id,
            status="ready",
        )
        session.add(meeting)
        session.flush()
        usage.record_usage(session, meeting, seconds, settings=settings)
        session.commit()


def template_of(engine, email: str) -> int:
    with Session(engine) as session:
        user = session.query(User).filter_by(email=email).one()
        return session.query(Template).filter_by(owner_user_id=user.id).first().id


def run(engine, settings, expired_settings, server_base, off_base) -> None:
    now = datetime.now(UTC)
    admin = Recorder(server_base, "platform admin")
    admin.call("admin-session-anonymous", "GET", "/api/session")
    P.login(admin, "admin-login", P.ADMIN_EMAIL, P.ADMIN_PASSWORD)
    admin.call(
        "admin-pilot-open",
        "PUT",
        "/api/admin/pilot",
        body={
            "signups_open": True,
            "max_accounts": 10,
            "default_hours_cap_seconds": 18000,
            "password_signup_enabled": True,
        },
        note="Sign-up open for ten accounts (`max_accounts` is still the brake).",
    )

    # A new person, on the app with the trial on and HOURS_MODE=enforce.
    visitor = Recorder(server_base, "visitor ada")
    visitor.call("visitor-session", "GET", "/api/session")
    sign_up(visitor, engine, "ada")
    P.note_file(
        "ada-grants-after-confirming",
        "The `hour_grants` row the confirmation made (read from the database): one `trial` "
        "grant of 3 hours (10800 s) that ends 14 days after it was given.",
        {"grants": grants(engine, PEOPLE["ada"])},
    )
    ada = Recorder(server_base, "ada")
    ada.call("ada-session-anonymous", "GET", "/api/session")
    P.login(ada, "ada-login", PEOPLE["ada"], PASSWORD)
    ada.call(
        "ada-session-trial",
        "GET",
        "/api/session",
        note="A person who has just confirmed their email: `state` `trial`, 3 hours held and left, "
        "`trial_ends_at` 14 days out, `can_record` true. The same `usage` shape as before; "
        "only the values are new for a brand-new account.",
    )
    count(engine, settings, PEOPLE["ada"], 2 * HOUR, "aaa-aaaa-aaa")
    ada.call(
        "ada-session-after-two-hours",
        "GET",
        "/api/session",
        note="After a 2-hour recording was counted: 1 hour left, `warning` `low` (2 of 3 hours is "
        "past 80%).",
    )
    count(engine, settings, PEOPLE["ada"], HOUR, "bbb-bbbb-bbb")
    ada.call(
        "ada-session-three-hours-used",
        "GET",
        "/api/session",
        note="Exactly 3 hours used: `state` `none` (a used-up trial shows `none`), `seconds_left` "
        "0, `warning` `out`, `can_record` false.",
    )
    ada.call(
        "ada-start-meeting-trial-used",
        "POST",
        "/api/meetings",
        body={"url": MEET, "template_id": template_of(engine, PEOPLE["ada"])},
        note="403 `hours_cap_reached` with `reason` `no_hours`: the 3 hours are used.",
    )
    ada.call("ada-notifications", "GET", "/api/notifications")

    # The same flow with the hours rule off (the trial setting alone changes nothing).
    visitor_off = Recorder(off_base, "visitor ben (HOURS_MODE off)")
    visitor_off.call("visitor-off-session", "GET", "/api/session")
    sign_up(visitor_off, engine, "ben")
    P.note_file(
        "ben-grants-flag-off",
        "The same sign-up on an app with TRIAL_SECONDS set but HOURS_MODE off: no grant is "
        "written.",
        {"grants": grants(engine, PEOPLE["ben"])},
    )
    ben = Recorder(off_base, "ben")
    ben.call("ben-session-anonymous", "GET", "/api/session")
    P.login(ben, "ben-login", PEOPLE["ben"], PASSWORD)
    ben.call(
        "ben-session-flag-off",
        "GET",
        "/api/session",
        note="HOURS_MODE off: the old cap rule only. The two old fields from the old cap, every "
        "new field null, exactly as before this stage.",
    )

    # The pilot expiry: a pilot account (placeholder, grant inserted by SQL as migration 0049 makes
    # it), the job run three days before the end, then the app answering after the end.
    with Session(engine) as session:
        paula = User(
            email=PEOPLE["paula"], password_hash=hash_password(PASSWORD), email_verified_at=now
        )
        session.add(paula)
        session.flush()
        session.add(Template(owner_user_id=paula.id, name="General", instructions="Write notes."))
        session.add(
            HourGrant(
                user_id=paula.id,
                email_hash=email_hash(PEOPLE["paula"]),
                kind="pilot",
                seconds=2 * HOUR,
                starts_at=now - timedelta(days=80),
            )
        )
        session.commit()
    alerts.get_settings = lambda: expired_settings  # this process's alerts mail like the app's
    end = pilot_expiry.end_of(expired_settings)
    with Session(engine) as session:
        changed, told = pilot_expiry.run(session, end - timedelta(days=3), expired_settings)
    P.note_file(
        "paula-pilot-job-run",
        "`pilot_expiry.run` with the clock three days before the end: the pilot grant is given its "
        "end and the one notice is written (the numbers are `(grants changed, people told)`). "
        "Running it again tells nobody.",
        {"changed": changed, "told": told, "end": end.isoformat()},
    )
    with Session(engine) as session:
        again = pilot_expiry.run(session, end - timedelta(days=2), expired_settings)
    P.note_file("paula-pilot-job-run-again", "The job a day later.", {"result": list(again)})
    P.note_file(
        "paula-pilot-grants",
        "The pilot grant now has the end: the start of the day after the last usable day (UTC).",
        {"grants": grants(engine, PEOPLE["paula"])},
    )
    P.note_file(
        "paula-pilot-mail",
        "The mail the worker would send, the `alert` template with this title and body.",
        P.outbox(engine, alerts.ALERT_TEMPLATE, PEOPLE["paula"]),
    )
    paula = Recorder(server_base, "paula")
    paula.call("paula-session-anonymous", "GET", "/api/session")
    P.login(paula, "paula-login", PEOPLE["paula"], PASSWORD)
    paula.call("paula-notifications", "GET", "/api/notifications", note="The bell entry.")
    paula.call(
        "paula-session-after-the-end",
        "GET",
        "/api/session",
        note="The app's clock is now after the pilot end: the pilot hours are not in force. "
        "`state` `none`, nothing left, `can_record` false.",
    )
    paula.call(
        "paula-start-meeting-after-the-end",
        "POST",
        "/api/meetings",
        body={"url": MEET, "template_id": template_of(engine, PEOPLE["paula"])},
        note="403 `hours_cap_reached` with `reason` `no_plan`: no grant is in force.",
    )


def main() -> None:
    if not sys.argv[1:]:
        sys.exit(__doc__)
    P.prepare_database()
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True)
    today = datetime.now(UTC).date()

    def settings_for(**over) -> Settings:
        values = {
            "_env_file": None,
            "app_env": "test",
            "session_secret": "capture-secret-" + "x" * 30,
            "cookie_secure": False,
            "database_url": P.scratch_url,
            "public_base_url": "https://notes.example.test",
            "trial_seconds": 3 * HOUR,
            **over,
        }
        return Settings(**values)

    enforce = settings_for(hours_mode="enforce")
    off = settings_for(hours_mode="off")
    # Paid plans opened 62 days ago: the pilot's last usable day was yesterday.
    expired = settings_for(hours_mode="enforce", paid_plans_open_on=today - timedelta(days=62))
    app, app_off = create_app(enforce), create_app(off)
    for each in (app, app_off):
        each.dependency_overrides[get_vexa] = lambda: P.FakeVexa()
    server, server_base = P.start_server(app)
    server_off, off_base = P.start_server(app_off)
    engine = create_engine(P.scratch_url)
    try:
        run(engine, enforce, expired, server_base, off_base)
        for path in OUT.glob("*.json"):  # no password, even a placeholder, stays in a file
            text = path.read_text()
            for secret in (PASSWORD, P.ADMIN_PASSWORD):
                text = text.replace(secret, "<password placeholder>")
            path.write_text(text)
    finally:
        server.should_exit = server_off.should_exit = True
        engine.dispose()


if __name__ == "__main__":
    main()
