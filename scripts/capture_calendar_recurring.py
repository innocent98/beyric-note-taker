"""Drive `GET /api/calendar/recurring` over real HTTP; save every exchange for the frontend guide.

    python scripts/capture_calendar_recurring.py postgresql+psycopg://USER:PASS@localhost:PORT/postgres

Same setup as `scripts/capture_calendar_state.py` (it is imported for the recorder, the scratch
database and the app server): real migrations, the real app under uvicorn, real HTTP. The Google
connection is made through the real connect flow and the events come from the REAL sync job
(`gcal_sync.sync_connection` over `GoogleSource`) reading `FakeGoogleCalendar`, so the stored rows
are what production would store from those Google bodies (Google's `recurringEventId` included).

Files go to `docs/fe-captures/calendar-recurring/NN-name.json`. The event times are placed relative
to the day the script runs, so the dates in the files are those of that run.

What is NOT real: Google's servers (the event bodies are synthetic, built from Google's documented
shapes), so no real Google recurring event has been read.
"""

import shutil
import sys
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import capture_calendar_connect as base  # reads sys.argv at import (the admin database URL)

OUT = base.ROOT / "docs" / "fe-captures" / "calendar-recurring"
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
LAGOS = ZoneInfo("Africa/Lagos")


def next_weekday(now: datetime, weekday: int, hh: int, mm: int, *, zone=LAGOS, weeks=0) -> datetime:
    """The first `weekday` (Monday is 0) at hh:mm local time that is at least a day away, plus
    `weeks` weeks, as an aware datetime."""
    local = now.astimezone(zone) + timedelta(days=1)
    day = local.date() + timedelta(days=(weekday - local.weekday()) % 7 + 7 * weeks)
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=zone)


def synthetic_calendar(now: datetime) -> list[dict]:
    """Google events for every answer the route can give (placeholder people and links)."""
    events = []
    # Weekly on Mondays 14:00 and Fridays 15:00 (Lagos), two weeks each.
    for n, week in enumerate((0, 1)):
        events.append(
            raw_event(
                f"mon_{n}",
                start=next_weekday(now, 0, 14, 0, weeks=week),
                summary="Beyric Monday planning",
                recurringEventId="mon",
            )
        )
        events.append(
            raw_event(
                f"fri_{n}",
                start=next_weekday(now, 4, 15, 0, weeks=week),
                summary="Beyric Friday review",
                recurringEventId="fri",
                code="fri-dayr-evw",
            )
        )
    # Daily stand-up at 09:30 for four days (the daily case needs three or more).
    for n in range(4):
        start = (now.astimezone(LAGOS) + timedelta(days=1 + n)).replace(
            hour=9, minute=30, second=0, microsecond=0
        )
        events.append(
            raw_event(
                f"standup_{n}",
                start=start,
                summary="Stand-up",
                recurringEventId="standup",
                code="std-upsd-ayy",
            )
        )
    # One occurrence only: what the data cannot prove is just "Recurring".
    events.append(
        raw_event(
            "board_0",
            start=next_weekday(now, 2, 11, 0),
            summary="Board call 🚀 週次",
            recurringEventId="board",
            code="brd-callx-yz",
        )
    )
    # Occurrences at different times of day: also just "Recurring".
    events.append(
        raw_event(
            "sync_0",
            start=next_weekday(now, 1, 10, 0),
            summary="Vendor sync",
            recurringEventId="sync",
            code="vnd-sync-aaa",
        )
    )
    events.append(
        raw_event(
            "sync_1",
            start=next_weekday(now, 1, 16, 0, weeks=1),
            summary="Vendor sync",
            recurringEventId="sync",
            code="vnd-sync-aaa",
        )
    )
    # A plain event (no recurringEventId): never a card.
    events.append(raw_event("plain", start=now + timedelta(hours=3), summary="Lunch with Sam"))
    return events


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

    user = Recorder(server_base, "signed-in user")
    Recorder(server_base, "visitor").call(
        "without-login", "GET", "/api/calendar/recurring", csrf=None, note="No session cookie."
    )
    base.login(user, "login", base.ADMIN_EMAIL, base.ADMIN_PASSWORD)
    live = app.state.settings
    app.state.settings = live.model_copy(update={"calendar_enabled": False})
    user.call(
        "feature-off",
        "GET",
        "/api/calendar/recurring",
        note="`CALENDAR_ENABLED` is false: 404, like every other calendar route.",
    )
    app.state.settings = live
    user.call(
        "not-connected",
        "GET",
        "/api/calendar/recurring",
        note="Signed in, never connected: 200 and an empty list (draw nothing).",
    )

    base.consent_and_callback(
        user, google, "connect", return_to="/settings", note="The real connect flow."
    )
    with Session(engine) as session:
        conn = session.scalars(select(CalendarConnection)).one()
        blob, version = vault.encrypt(
            FakeGoogleCalendar.REFRESH_TOKEN, purpose="refresh_token", owner=conn.user_id
        )
        conn.refresh_token_enc, conn.key_version = blob, version
        session.commit()
    user.call(
        "connected-before-first-sync",
        "GET",
        "/api/calendar/recurring",
        note="Connected a moment ago, the sync job has not run: empty list.",
    )

    calendar.pages = [synthetic_calendar(now)]
    sync_now()
    user.call(
        "series-utc",
        "GET",
        "/api/calendar/recurring",
        note="After the real sync job read the synthetic events. No `tz`: the texts are in UTC and "
        "`timezone` says so.",
    )
    user.call(
        "series-lagos",
        "GET",
        "/api/calendar/recurring?tz=Africa%2FLagos",
        note="The same rows, written in Africa/Lagos (the viewer's own zone). Weekly Monday 14:00 "
        "and Friday 15:00 are provable; the stand-up is daily; the board call has one occurrence "
        "and the vendor sync changes its time of day, so both are only `Recurring`.",
    )
    user.call(
        "series-auckland",
        "GET",
        "/api/calendar/recurring?tz=Pacific%2FAuckland",
        note="The same rows in Pacific/Auckland: the weekday and clock time move with the zone, "
        "the `next_starts_at` instants do not.",
    )
    user.call(
        "unknown-timezone",
        "GET",
        "/api/calendar/recurring?tz=Mars%2FOlympus",
        note="A zone name that is not known: 422, never a guess.",
    )
    user.call(
        "timezone-too-long",
        "GET",
        "/api/calendar/recurring?tz=" + "x" * 80,
        note="The query is validated before the zone is looked up: the general validation error.",
    )

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
        "other-user",
        "GET",
        "/api/calendar/recurring",
        note="A second account sees only its own calendar: nothing connected, nothing listed.",
    )

    user.call("disconnect", "POST", "/api/calendar/google/disconnect", note="Disconnect (C4).")
    user.call(
        "after-disconnect",
        "GET",
        "/api/calendar/recurring",
        note="Back to nothing: the events were deleted with the connection.",
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
