"""Capture what the front end gets for the "add the note taker to your event" tip, over real HTTP.

    python scripts/capture_bot_invite_tip.py postgresql+psycopg://USER:PASS@localhost:PORT/postgres

Same setup as `scripts/capture_calendar_state.py` (it imports `capture_calendar_connect` for the
HTTP recorder, the scratch database and the app server): real migrations, the real app under
uvicorn, real HTTP, the Google connection made through the real connect flow.

The server is started as production would run it with the tip on: `BOT_PROVIDER=skribby`, a signed
in account (`SKRIBBY_ACCOUNT_ID`) and `BOT_INVITE_EMAIL`. Every value is a placeholder. The same two
reads are then captured with the address unset, with the account unset and with Vexa as provider,
to show the field is `null` (present, not absent) whenever the server may not say it.

Files go to `docs/fe-captures/bot-invite-tip/NN-name.json`.

What is NOT real: Skribby and Google (a stand-in for each; no real bot was sent and no real meeting
joined), so the meetings below are rows set up directly in the states the worker leaves them in
(`bot_sent` + `awaiting_admission`; `failed` with the not-let-in reason). That the real worker
produces exactly those rows is covered by its own tests, not shown here.
"""

import os
import sys

# The app reads these at start through `get_settings()` (the meeting answer does), so they are set
# before anything imports it. All placeholders.
os.environ["BOT_PROVIDER"] = "skribby"
os.environ["SKRIBBY_API_KEY"] = "capture-placeholder-key"
os.environ["SKRIBBY_ACCOUNT_ID"] = "11111111-2222-3333-4444-555555555555"
os.environ["BOT_INVITE_EMAIL"] = "note@example.test"

import shutil  # noqa: E402
from datetime import UTC, datetime, timedelta  # noqa: E402

import capture_calendar_connect as base  # noqa: E402  (reads sys.argv: the admin database URL)

OUT = base.ROOT / "docs" / "fe-captures" / "bot-invite-tip"
base.OUT = OUT

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import google_oauth  # noqa: E402
from app.config import Settings, get_settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Meeting, Template, User  # noqa: E402
from app.pipeline import REASON_NOT_LET_IN  # noqa: E402
from app.services import get_vexa  # noqa: E402
from tests.fakes.google import FakeGoogle, FakeGoogleCalendar  # noqa: E402

Recorder = base.Recorder


def new_meeting(session, user, template, code, **fields) -> Meeting:
    values = {
        "owner_user_id": user.id,
        "url": f"https://meet.google.com/{code}",
        "platform": "google_meet",
        "native_id": code,
        "template_id": template.id,
        "bot_provider": "skribby",
    }
    meeting = Meeting(**{**values, **fields})
    session.add(meeting)
    session.commit()
    return meeting


def seed(engine) -> dict[str, int]:
    """Four meetings, one for each answer the page can get. Ids by name."""
    now = datetime.now(UTC)
    with Session(engine) as session:
        user = session.scalars(select(User).where(User.email == base.ADMIN_EMAIL)).one()
        template = session.scalars(
            select(Template).where(Template.owner_user_id == user.id)
        ).first()
        waiting = new_meeting(
            session,
            user,
            template,
            "aaa-bbbb-ccc",
            status="bot_sent",
            completion_reason="awaiting_admission",
            bot_state_seen_at=now,
            started_polling_at=now - timedelta(minutes=2),
        )
        refused = new_meeting(
            session,
            user,
            template,
            "ddd-eeee-fff",
            status="failed",
            step="waiting",
            failure_reason=REASON_NOT_LET_IN,
        )
        zoom = new_meeting(
            session,
            user,
            template,
            "81234567890",
            platform="zoom",
            url="https://us02web.zoom.us/j/81234567890",
            status="bot_sent",
            completion_reason="awaiting_admission",
            bot_state_seen_at=now,
        )
        inside = new_meeting(
            session,
            user,
            template,
            "ggg-hhhh-iii",
            status="bot_sent",
            completion_reason="active",
            bot_state_seen_at=now,
        )
        return {"waiting": waiting.id, "refused": refused.id, "zoom": zoom.id, "inside": inside.id}


def run(app, server_base, engine, google) -> None:
    ids = seed(engine)
    user = Recorder(server_base, "signed-in user")
    base.login(user, "login", base.ADMIN_EMAIL, base.ADMIN_PASSWORD)
    base.consent_and_callback(
        user, google, "connect", return_to="/settings", note="The real connect flow (fake Google)."
    )

    user.call(
        "calendar-tip-on",
        "GET",
        "/api/calendar",
        note="Skribby, signed in, `BOT_INVITE_EMAIL` set: `bot_invite_email` is the address. "
        "Settings draws the tip under the auto-join rules when `provider` is `google`.",
    )
    for key, label in (
        ("waiting", "A Google Meet meeting whose bot is waiting in the lobby"),
        ("refused", "A Google Meet meeting that failed because the bot was not let in"),
        ("zoom", "A Zoom meeting in the same waiting state: Zoom guests always wait for the host"),
        ("inside", "A Google Meet meeting whose bot is already in the call"),
    ):
        user.call(
            f"meeting-{key}-tip-on",
            "GET",
            f"/api/meetings/{ids[key]}",
            note=f"{label}. Tip on in the server's settings.",
        )

    # The same reads with the server not set up for the tip. `get_settings()` is read by the
    # meeting answer and `app.state.settings` by the calendar answer: both are changed.
    live = app.state.settings
    for name, env, value in (
        ("address-unset", "BOT_INVITE_EMAIL", ""),
        ("no-account", "SKRIBBY_ACCOUNT_ID", ""),
        ("vexa", "BOT_PROVIDER", "vexa"),
    ):
        saved = os.environ[env]
        os.environ[env] = value
        get_settings.cache_clear()
        app.state.settings = live.model_copy(update={env.lower(): value})
        user.call(
            f"calendar-tip-off-{name}",
            "GET",
            "/api/calendar",
            note=f"`{env}` is `{value or 'empty'}`: the field is present and `null`.",
        )
        if name == "address-unset":
            user.call(
                "meeting-waiting-tip-off-address-unset",
                "GET",
                f"/api/meetings/{ids['waiting']}",
                note="The waiting meeting again, with the address unset: `null`, so no tip.",
            )
        os.environ[env] = saved
        get_settings.cache_clear()
    app.state.settings = live


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
        google_client_id=FakeGoogleCalendar.CLIENT_ID,
        google_client_secret=FakeGoogleCalendar.CLIENT_SECRET,
        calendar_enabled=True,
        token_enc_key=base.KEY,
    )
    google = FakeGoogle(settings)
    app = create_app(settings)
    app.dependency_overrides[get_vexa] = lambda: base.FakeVexa()
    app.dependency_overrides[google_oauth.get_google] = lambda: google.oauth
    server, server_base = base.start_server(app)
    engine = create_engine(base.SCRATCH_URL)
    try:
        run(app, server_base, engine, google)
    finally:
        server.should_exit = True
        engine.dispose()


if __name__ == "__main__":
    main()
