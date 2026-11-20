"""Drive the hours rule over real HTTP and save every exchange for the billing guide (stage S2).

    python scripts/capture_hours_usage.py postgresql+psycopg://USER:PASS@localhost:PORT/postgres

Same setup as `scripts/capture_send_now.py` (it imports `capture_calendar_connect` for the HTTP
recorder, the scratch database and the app server): real migrations, the real app under uvicorn
with `HOURS_MODE=enforce`, real HTTP. A second copy of the app on the same database runs with the
flag off, to show the payload then.

Files go to `docs/fe-captures/hours-usage/NN-name.json`. Passwords in login calls are placeholders.

What is real: the app, the routes, the entitlement rule, the grants and the allocation code, the
403 refusal, the notices, the database rows. What is NOT, said in each file's note where it
matters:
- The people and their hours are placeholders (`ada@example.test` and so on). Their grants were
  inserted by SQL in the shape the code makes: sign-up gives nobody a trial yet (stage S10) and no
  purchase exists yet (stage S8), so there is no route that makes a `trial` or `plan_period` grant.
- The recording that is "counted" is counted by calling `usage.record_usage` in this process, the
  way the pipeline does when a recording is prepared, because no real meeting was recorded: **no
  bot, no audio, no real pipeline run**.
- Mail is not captured (the notice's mail goes to the outbox; its text is in the tests).
"""

import shutil
import sys
from datetime import UTC, datetime, timedelta

import capture_calendar_connect as base  # reads sys.argv at import (the admin database URL)

OUT = base.ROOT / "docs" / "fe-captures" / "hours-usage"
base.OUT = OUT

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import usage  # noqa: E402
from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import HourGrant, Meeting, Template, UsageEntry, User  # noqa: E402
from app.passwords import hash_password  # noqa: E402
from app.services import get_bot_provider  # noqa: E402
from app.usage import email_hash  # noqa: E402

Recorder = base.Recorder
HOUR = 3600
MEET = "https://meet.google.com/abc-defg-hij"
PASSWORD = "-".join(["capture", "person", "password"])  # a placeholder, scratch database only


def person(engine, email: str) -> int:
    """An ordinary verified, active account with one template. Returns the user id."""
    with Session(engine) as session:
        user = User(
            email=email,
            password_hash=hash_password(PASSWORD),
            email_verified_at=datetime.now(UTC),
        )
        session.add(user)
        session.flush()
        session.add(Template(owner_user_id=user.id, name="General", instructions="Write notes."))
        session.commit()
        return user.id


def give(engine, user_id: int, email: str, kind: str, seconds: int, **fields) -> int:
    with Session(engine) as session:
        row = HourGrant(
            user_id=user_id, email_hash=email_hash(email), kind=kind, seconds=seconds, **fields
        )
        session.add(row)
        session.commit()
        return row.id


def count(engine, settings, email: str, seconds: int, code: str) -> None:
    """Count a recording the way the pipeline does when it is prepared (see the module note)."""
    with Session(engine) as session:
        user = session.query(User).filter_by(email=email).one()
        template = session.query(Template).filter_by(owner_user_id=user.id).one()
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


def signed_in(server_base: str, who: str, email: str) -> Recorder:
    client = Recorder(server_base, who)
    base.login(client, f"login-{who.split()[0]}", email, PASSWORD)
    return client


def run(engine, settings, server_base, off_base) -> None:
    now = datetime.now(UTC)
    admin = Recorder(server_base, "platform admin")
    base.login(admin, "login-admin", base.ADMIN_EMAIL, base.ADMIN_PASSWORD)
    admin.call(
        "session-admin",
        "GET",
        "/api/session",
        note="The platform admin: no limit. `cap_seconds` and `seconds_left` are null, "
        "`unlimited` is true, `can_record` is true. `state` is `none` and means nothing here: read "
        "`unlimited` first.",
    )

    # A pilot account as migration 0049 left it: a 5-hour allowance of which 4 hours were used
    # before the grant was made, so the grant holds the 1 hour that was left.
    ada = "ada@example.test"
    ada_id = person(engine, ada)
    pilot = give(engine, ada_id, ada, "pilot", HOUR, starts_at=now - timedelta(days=10))
    with Session(engine) as session:
        session.add(
            UsageEntry(
                user_id=ada_id,
                email_hash=email_hash(ada),
                seconds=4 * HOUR,
                counted_at=now - timedelta(days=11),
            )
        )
        session.commit()
    a = signed_in(server_base, "ada pilot", ada)
    a.call(
        "session-pilot",
        "GET",
        "/api/session",
        note="A pilot account that had used 4 of its 5 hours before the grant was made (so it "
        "holds 1 hour): the numbers show the ORIGINAL total, `used_seconds` 14400 of "
        "`cap_seconds` 18000, "
        "while `seconds_left` is the 3600 that can still be recorded. `state` `pilot`.",
    )
    off = signed_in(off_base, "ada flag off", ada)
    off.call(
        "session-flag-off",
        "GET",
        "/api/session",
        note="The same person with HOURS_MODE off (a second copy of the app on the same database): "
        "the two old fields as always, from the old cap rule, and every new field is null.",
    )

    # A real count (see the note at the top): 0.8 of the 1 hour held is exactly 80%.
    count(engine, settings, ada, 2880, "aaa-aaaa-aaa")
    a.call(
        "session-80-percent",
        "GET",
        "/api/session",
        note="After a 0.8-hour recording was counted (`usage.record_usage`, not a real meeting): "
        "80% "
        "of the 1 hour held, so `warning` is `low`. `used_seconds` 17280 of `cap_seconds` 18000: "
        "still the original 5-hour total.",
    )
    a.call(
        "notifications-80-percent",
        "GET",
        "/api/notifications",
        note="The 80% notice, written by the same count: kind `usage.hours_low`, one per person "
        'and period. It says "4.8 of your 5 recorded hours" (the original total).',
    )

    # A paying person: pilot hours left plus a plan period (inserted by SQL, see the note).
    pay = "paula@example.test"
    pay_id = person(engine, pay)
    give(engine, pay_id, pay, "pilot", 2 * HOUR)
    give(
        engine,
        pay_id,
        pay,
        "plan_period",
        20 * HOUR,
        starts_at=now - timedelta(days=3),
        expires_at=now + timedelta(days=27),
    )
    signed_in(server_base, "paula plan", pay).call(
        "session-plan-and-pilot",
        "GET",
        "/api/session",
        note="A plan period of 20 hours (a `plan_period` grant inserted by SQL: nothing sells one "
        "yet) plus 2 pilot hours left: `state` is `active`, not `pilot`; `period_ends_at` is the "
        "plan period's end; 22 hours held.",
    )

    # A trial grant.
    tri = "tomas@example.test"
    tri_id = person(engine, tri)
    give(engine, tri_id, tri, "trial", 3 * HOUR, expires_at=now + timedelta(days=14))
    signed_in(server_base, "tomas trial", tri).call(
        "session-trial",
        "GET",
        "/api/session",
        note="A trial grant of 3 hours ending in 14 days (inserted by SQL: sign-up does not give "
        "one until stage S10): `state` `trial`, `trial_ends_at` set.",
    )

    # Out of hours: Ada records the last hour.
    count(engine, settings, ada, 720, "bbb-bbbb-bbb")
    a.call(
        "session-out-of-hours",
        "GET",
        "/api/session",
        note="The last hour counted: `warning` `out`, `can_record` false, `seconds_left` 0, "
        "`used_seconds` equals `cap_seconds`.",
    )
    with Session(engine) as session:
        template_id = session.query(Template).filter_by(owner_user_id=ada_id).one().id
    a.call(
        "start-meeting-no-hours",
        "POST",
        "/api/meetings",
        body={"url": MEET, "template_id": template_id},
        note="A pasted link with nothing left: 403 `hours_cap_reached` with `reason` `no_hours`. "
        "The same refusal comes from Send now and the shared link.",
    )
    a.call(
        "notifications-out-of-hours",
        "GET",
        "/api/notifications",
        note="The 100% notice, raised by the count: kind `usage.hours_out`, **one** per person "
        "and period. The 80% one from before is still there.",
    )
    count(engine, settings, ada, 2 * HOUR, "ccc-cccc-ccc")
    a.call(
        "session-past-zero",
        "GET",
        "/api/session",
        note="A meeting that was already running finished after the hours were gone (a 2-hour "
        "recording counted): it is counted in full, `seconds_left` stays 0 and never goes "
        "negative, and no second notice is raised (next file).",
    )
    a.call(
        "notifications-past-zero",
        "GET",
        "/api/notifications",
        note="Still one `usage.hours_out` and one `usage.hours_low`.",
    )

    # No hours at all.
    nob = "nora@example.test"
    nob_id = person(engine, nob)
    n = signed_in(server_base, "nora none", nob)
    n.call(
        "session-no-hours",
        "GET",
        "/api/session",
        note="A person who holds no grant (an account made after migration 0049, today): "
        "`state` `none`, `cap_seconds` 0, `can_record` false, `warning` null.",
    )
    with Session(engine) as session:
        template_id = session.query(Template).filter_by(owner_user_id=nob_id).one().id
    n.call(
        "start-meeting-no-plan",
        "POST",
        "/api/meetings",
        body={"url": MEET, "template_id": template_id},
        note="403 `hours_cap_reached` with `reason` `no_plan`: no hours are held at all.",
    )
    # A second meeting on the last minute: 40 minutes held, one meeting just placed.
    rita = "rita@example.test"
    rita_id = person(engine, rita)
    give(engine, rita_id, rita, "plan_period", 40 * 60, starts_at=now - timedelta(days=1))
    with Session(engine) as session:
        template = session.query(Template).filter_by(owner_user_id=rita_id).one()
        session.add(
            Meeting(
                owner_user_id=rita_id,
                url="https://meet.google.com/rrr-rrrr-rrr",
                platform="google_meet",
                native_id="rrr-rrrr-rrr",
                template_id=template.id,
                status="bot_sent",
                started_polling_at=datetime.now(UTC),
            )
        )
        session.commit()
        template_id = template.id
    r = signed_in(server_base, "rita one running", rita)
    r.call(
        "session-meeting-running",
        "GET",
        "/api/session",
        note="40 minutes held and one meeting just placed (inserted by SQL, as the placement "
        "would make it): `seconds_left` is still about 2400, but `can_record` is false, because "
        "a second meeting needs its 30 minutes plus the 30 set aside for the running one.",
    )
    r.call(
        "start-meeting-reserve",
        "POST",
        "/api/meetings",
        body={"url": "https://meet.google.com/new-aaaa-aaa", "template_id": template_id},
        note="A second link while that meeting runs: 403 `hours_cap_reached` with `reason` "
        "`reserve`. A first meeting is never refused for this.",
    )
    print("pilot grant id", pilot)


def main() -> None:
    if not sys.argv[1:]:
        sys.exit(__doc__)
    base.prepare_database()
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True)

    def settings_for(mode: str) -> Settings:
        return Settings(
            _env_file=None,
            app_env="test",
            session_secret="capture-secret-" + "x" * 30,
            cookie_secure=False,
            database_url=base.SCRATCH_URL,
            public_base_url="https://notes.example.test",
            token_enc_key=base.KEY,
            hours_mode=mode,
        )

    enforce, off = settings_for("enforce"), settings_for("off")
    app, app_off = create_app(enforce), create_app(off)
    for each in (app, app_off):
        each.dependency_overrides[get_bot_provider] = lambda: base.FakeBot()
    server, server_base = base.start_server(app)
    server_off, off_base = base.start_server(app_off)
    engine = create_engine(base.SCRATCH_URL)
    try:
        run(engine, enforce, server_base, off_base)
        for path in OUT.glob("*.json"):  # no password, even a placeholder, stays in a file
            text = path.read_text()
            for secret in (PASSWORD, base.ADMIN_PASSWORD):
                text = text.replace(secret, "<password placeholder>")
            path.write_text(text)
    finally:
        server.should_exit = server_off.should_exit = True
        engine.dispose()


if __name__ == "__main__":
    main()
