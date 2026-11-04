"""Drive `GET /api/calendar` over real HTTP and save every exchange for the frontend guide.

    python scripts/capture_calendar_state.py postgresql+psycopg://USER:PASS@localhost:PORT/postgres

Same setup as `scripts/capture_calendar_connect.py` (it is imported for the HTTP recorder, the
scratch database and the app server): real migrations, the real app under uvicorn, real HTTP. The
Google connection is made through the real connect flow, and the events in the answer come from
the REAL sync job (`gcal_sync.sync_connection` over `GoogleSource`) reading `FakeGoogleCalendar`,
so the stored rows are exactly what production would store from those Google bodies.

Files go to `docs/fe-captures/calendar-state/NN-name.json`.

What is NOT real: Google's servers (consent screen, token endpoint and Calendar API are
`tests/fakes/google.py`; the event bodies are synthetic, built from Google's documented shapes), so
**no real Google calendar has been read**. The `degraded` and `needs_reconnect` states are set
directly on the stored row (a note in each file says so) because nothing sets them yet (task C13).
"""

import shutil
import sys
from datetime import UTC, datetime, timedelta

import capture_calendar_connect as base  # reads sys.argv at import (the admin database URL)

# The recorder writes to `base.OUT`; point it at this script's folder.
OUT = base.ROOT / "docs" / "fe-captures" / "calendar-state"
base.OUT = OUT

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import google_oauth, vault  # noqa: E402
from app.config import Settings  # noqa: E402
from app.gcal_client import AccessTokenCache, GoogleSource  # noqa: E402
from app.gcal_sync import sync_connection  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import CalendarConnection, User  # noqa: E402
from app.passwords import hash_password  # noqa: E402
from app.services import get_vexa  # noqa: E402
from tests.fakes.google import FakeGoogle, FakeGoogleCalendar  # noqa: E402
from tests.test_gcal_sync import raw_event  # noqa: E402

Recorder = base.Recorder


def in_hours(now: datetime, hours: float) -> datetime:
    # On the minute, so the captured times are tidy.
    moment = now + timedelta(hours=hours)
    return moment.replace(second=0, microsecond=0)


def synthetic_calendar(now: datetime) -> list[dict]:
    """Eight Google events (placeholder people and links), one for each answer the page can show."""
    me = {"email": "admin@example.test", "self": True, "responseStatus": "accepted"}
    return [
        raw_event("evt-meet", start=in_hours(now, 2), summary="Weekly planning"),
        raw_event(
            "evt-zoom",
            start=in_hours(now, 5),
            summary="Customer call",
            hangoutLink=None,
            location="https://us02web.zoom.us/j/81234567890?pwd=PLACEHOLDERPASSCODE",
            description="Agenda: pricing. Dial-in details are in the invite.",
        ),
        raw_event(
            "evt-nolink",
            start=in_hours(now, 8),
            summary="Lunch with Sam",
            hangoutLink=None,
            description="Table for two.",
        ),
        raw_event(
            "evt-teams",
            start=in_hours(now, 26),
            summary="Vendor sync",
            hangoutLink=None,
            description="https://teams.microsoft.com/l/meetup-join/19%3ameeting_PLACEHOLDER%40thread.v2/0",
        ),
        raw_event(
            "evt-declined",
            start=in_hours(now, 30),
            summary="Budget review",
            attendees=[
                {**me, "responseStatus": "declined"},
                {"email": "colleague@example.test", "responseStatus": "accepted"},
            ],
            organizer={"email": "colleague@example.test"},
        ),
        raw_event("evt-emoji", start=in_hours(now, 50), summary="Sprint 🚀 planning · 週次"),
        raw_event(
            "evt-stranger",
            start=in_hours(now, 55),
            summary="Partnership idea",
            organizer={"email": "stranger@elsewhere.test"},
            attendees=[
                {**me, "responseStatus": "needsAction"},
                {"email": "stranger@elsewhere.test", "organizer": True},
            ],
        ),
        raw_event("evt-later", start=in_hours(now, 24 * 10), summary="Quarterly offsite"),
    ]


def run(app, server_base, engine, settings, google) -> None:
    now = datetime.now(UTC)
    calendar = FakeGoogleCalendar()
    cache = AccessTokenCache(
        lambda: datetime.now(UTC), settings=settings, transport=calendar.transport
    )
    source = GoogleSource(settings, cache=cache, transport=calendar.transport)

    def sync_now() -> None:
        with Session(engine) as session:
            conn = session.scalars(select(CalendarConnection)).one()
            sync_connection(session, conn, source, now=datetime.now(UTC), settings=settings)

    def set_row(**fields) -> None:
        with Session(engine) as session:
            conn = session.scalars(select(CalendarConnection)).one()
            for name, value in fields.items():
                setattr(conn, name, value)
            session.commit()

    user = Recorder(server_base, "signed-in user")
    Recorder(server_base, "visitor").call(
        "state-without-login",
        "GET",
        "/api/calendar",
        csrf=None,
        note="No session cookie.",
    )
    base.login(user, "login", base.ADMIN_EMAIL, base.ADMIN_PASSWORD)
    live = app.state.settings
    app.state.settings = live.model_copy(update={"calendar_enabled": False})
    user.call(
        "state-feature-off",
        "GET",
        "/api/calendar",
        note="`CALENDAR_ENABLED` is false: 404, like every other calendar route.",
    )
    app.state.settings = live
    user.call(
        "state-not-connected",
        "GET",
        "/api/calendar",
        note="Signed in, never connected.",
    )

    base.consent_and_callback(
        user,
        google,
        "connect",
        return_to="/settings/calendar",
        note="The real connect flow over the fake consent screen.",
    )
    # The fake consent screen's refresh token is not one the fake Calendar API knows; store one
    # it does, through the real vault, so the real sync job can read the fake calendar.
    with Session(engine) as session:
        conn = session.scalars(select(CalendarConnection)).one()
        blob, version = vault.encrypt(
            FakeGoogleCalendar.REFRESH_TOKEN, purpose="refresh_token", owner=conn.user_id
        )
        conn.refresh_token_enc, conn.key_version = blob, version
        session.commit()
    user.call(
        "state-connected-before-first-sync",
        "GET",
        "/api/calendar",
        note="Connected a moment ago: the sync job has not run yet.",
    )

    calendar.pages = [synthetic_calendar(now)]
    sync_now()
    user.call(
        "state-connected-with-upcoming",
        "GET",
        "/api/calendar",
        note="After the real sync job read eight synthetic events: seven are inside the next seven "
        "days, the eighth (in ten days) is not listed. The Zoom passcode, the descriptions and "
        "every attendee address are in the stored rows or the Google bodies and nowhere in this "
        "answer.",
    )

    # A revoked grant: Google refuses the refresh token on the next sync.
    calendar.valid_refresh_tokens.clear()
    cache.forget(1)
    sync_now()
    user.call(
        "state-last-sync-failed-revoked",
        "GET",
        "/api/calendar",
        note="The next real sync run was refused by Google (`invalid_grant`, a revoked grant). The "
        "job records it; the answer shows it in words. `state` is still `connected` because "
        "nothing turns a revoked grant into `needs_reconnect` yet (task C13).",
    )
    calendar.valid_refresh_tokens.add(FakeGoogleCalendar.REFRESH_TOKEN)
    calendar.override("list", calendar.list_calls, FakeGoogleCalendar.error(503, "backendError"))
    sync_now()
    user.call(
        "state-last-sync-failed-transient",
        "GET",
        "/api/calendar",
        note="A Google 503 on the next real sync run: the same plain sentence for anything that "
        "may pass on its own.",
    )
    set_row(status="degraded")
    user.call(
        "state-degraded",
        "GET",
        "/api/calendar",
        note="NOT reached live: nothing sets `degraded` yet (C13), so the stored row was set to "
        "`degraded` directly to see the route's answer.",
    )
    set_row(status="needs_reconnect", needs_reconnect_reason="revoked")
    user.call(
        "state-needs-reconnect-revoked",
        "GET",
        "/api/calendar",
        note="NOT reached live (C13): `needs_reconnect` with reason `revoked` set directly.",
    )
    set_row(needs_reconnect_reason="expired")
    user.call(
        "state-needs-reconnect-expired",
        "GET",
        "/api/calendar",
        note="NOT reached live (C13): reason `expired` set directly.",
    )
    set_row(status="connected", needs_reconnect_reason=None)

    # Another person never sees it.
    with Session(engine) as session:
        session.add(
            User(
                email=base.OTHER_EMAIL,
                password_hash=hash_password(base.OTHER_PASSWORD),
                email_verified_at=datetime.now(UTC),
            )
        )
        session.commit()
    ben = Recorder(server_base, "a different user")
    base.login(ben, "ben-login", base.OTHER_EMAIL, base.OTHER_PASSWORD)
    ben.call(
        "state-other-user",
        "GET",
        "/api/calendar",
        note="A second account sees only its own state: not connected, no events, no address.",
    )

    user.call(
        "disconnect",
        "POST",
        "/api/calendar/google/disconnect",
        note="Disconnect (the route from C4; the page calls it).",
    )
    user.call(
        "state-after-disconnect",
        "GET",
        "/api/calendar",
        note="Back to not connected: no address, no events.",
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
        run(app, server_base, engine, settings, google)
    finally:
        server.should_exit = True
        engine.dispose()


if __name__ == "__main__":
    main()
