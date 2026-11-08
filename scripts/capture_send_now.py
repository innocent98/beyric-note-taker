"""Drive Send now and Not recorded over real HTTP and save every exchange for the frontend guide.

    python scripts/capture_send_now.py postgresql+psycopg://USER:PASS@localhost:PORT/postgres

Same setup as `scripts/capture_calendar_state.py` (it imports `capture_calendar_connect` for the
HTTP recorder, the scratch database and the app server): real migrations, the real app under
uvicorn, real HTTP. The calendar is connected through the real connect flow, the events become
`scheduled_joins` through the REAL sync job reading `FakeGoogleCalendar`, and the rows that end
as "not recorded" or "no capacity" are ended by the REAL join runner (`run_due_joins`) against
`FakeVexaHost`, the same fake host the placement tests use (it keeps one bot per meeting code and
counts the bots it was asked for). The Send now calls then go through the real route and the real
`place_bot()`.

Files go to `docs/fe-captures/send-now/NN-name.json`. Passwords in login calls are written as
`<password placeholder>`.

What is NOT real, said in each file's note where it matters:
- Google (consent, token and Calendar API are `tests/fakes/google.py`; event bodies are synthetic).
- The bot host: `FakeVexaHost` answers the send, so **no real bot was placed in a real meeting**.
- The runner's clock for the "not recorded" row is moved forward four minutes (the runner takes
  its clock as an argument) so that a meeting that started 14 minutes ago passes its 15-minute
  deadline; the row it writes is the runner's own.
- One row (a meeting that ended) is inserted directly: the runner only ends a row this way at its
  deadline, 15 minutes after a start, while the meeting is still on for longer.
"""

import re
import shutil
import sys
from datetime import UTC, datetime, timedelta

import capture_calendar_connect as base  # reads sys.argv at import (the admin database URL)

OUT = base.ROOT / "docs" / "fe-captures" / "send-now"
base.OUT = OUT

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import google_oauth, join_runner, mail_templates, vault  # noqa: E402
from app.config import Settings  # noqa: E402
from app.gcal_client import AccessTokenCache, GoogleSource  # noqa: E402
from app.gcal_sync import sync_connection  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import (  # noqa: E402
    AppState,
    CalendarConnection,
    CalendarEvent,
    Meeting,
    ScheduledJoin,
    UsageEntry,
    User,
)
from app.passwords import hash_password  # noqa: E402
from app.services import get_vexa  # noqa: E402
from app.usage import email_hash  # noqa: E402
from app.vexa import VexaError  # noqa: E402
from tests.fakes.google import FakeGoogle, FakeGoogleCalendar  # noqa: E402
from tests.fakes.vexa_host import FakeVexaHost  # noqa: E402
from tests.test_gcal_sync import raw_event  # noqa: E402

Recorder = base.Recorder


def minutes(now: datetime, n: float) -> datetime:
    return (now + timedelta(minutes=n)).replace(second=0, microsecond=0)


def run(app, server_base, engine, settings, google, host) -> None:
    now = datetime.now(UTC)
    calendar = FakeGoogleCalendar()
    cache = AccessTokenCache(
        lambda: datetime.now(UTC), settings=settings, transport=calendar.transport
    )
    source = GoogleSource(settings, cache=cache, transport=calendar.transport)

    user = Recorder(server_base, "signed-in user")
    visitor = Recorder(server_base, "visitor")
    visitor.call("list-without-login", "GET", "/api/not-recorded", csrf=None, note="No cookie.")
    visitor.call(
        "send-now-without-login",
        "POST",
        "/api/scheduled-joins/1/send-now",
        csrf=None,
        note="No cookie.",
    )
    base.login(user, "login", base.ADMIN_EMAIL, base.ADMIN_PASSWORD)
    user.call(
        "send-now-without-csrf-header",
        "POST",
        "/api/scheduled-joins/1/send-now",
        csrf=None,
        note="Signed in, but the `X-CSRF-Token` header is left out.",
    )
    user.call(
        "list-empty",
        "GET",
        "/api/not-recorded",
        note="Signed in, nothing has gone wrong yet.",
    )

    # The admin account has the default templates (migration 0006): use its first for calendar
    # meetings, through the real routes.
    templates = user.call(
        "templates",
        "GET",
        "/api/templates",
        note="The account's own templates; the first is chosen for calendar meetings below.",
    ).json()
    made = templates["templates"][0]
    user.call(
        "calendar-template",
        "PUT",
        "/api/calendar/settings",
        body={"calendar_template_id": made["id"]},
        note="Choose it for calendar meetings (the runner and Send now read it).",
    )

    base.consent_and_callback(
        user, google, "connect", return_to="/settings", note="Real connect flow."
    )
    with Session(engine) as session:
        conn = session.scalars(select(CalendarConnection)).one()
        blob, version = vault.encrypt(
            FakeGoogleCalendar.REFRESH_TOKEN, purpose="refresh_token", owner=conn.user_id
        )
        conn.refresh_token_enc, conn.key_version = blob, version
        # The runner has "led" since long before: rows are dispatched, not closed as old.
        state = session.get(AppState, 1) or AppState(id=1)
        state.autojoin_started_at = now - timedelta(days=30)
        session.add(state)
        session.commit()

    # Four meetings, each with its own Meet code (placeholders):
    #   missed:   started 14 minutes ago, 60 minutes long: the runner will end it "not recorded"
    #   first:    started 2 minutes ago: the one slot goes to it
    #   second:   started 2 minutes ago: no slot left, so `no_capacity`
    #   later:    in two hours: still `scheduled`
    calendar.pages = [
        [
            raw_event(
                "evt-missed",
                start=minutes(now, -14),
                length=timedelta(minutes=60),
                summary="Weekly planning",
                code="aaa-aaaa-aaa",
            ),
            raw_event(
                "evt-first",
                start=minutes(now, -2),
                length=timedelta(minutes=60),
                summary="Customer call",
                code="bbb-bbbb-bbb",
            ),
            raw_event(
                "evt-second",
                start=minutes(now, -2),
                length=timedelta(minutes=60),
                summary="Design review",
                code="ccc-cccc-ccc",
            ),
            raw_event(
                "evt-later",
                start=minutes(now, 120),
                length=timedelta(minutes=30),
                summary="Retro",
                code="ddd-dddd-ddd",
            ),
        ]
    ]
    with Session(engine) as session:
        conn = session.scalars(select(CalendarConnection)).one()
        sync_connection(session, conn, source, now=datetime.now(UTC), settings=settings)

    # The REAL runner, with its clock four minutes ahead: "missed" passes its 15-minute deadline
    # (failed / not recorded), "first" is placed, "second" finds the one slot taken.
    with Session(engine) as session:
        outcomes = join_runner.run_due_joins(
            session, host, None, now=datetime.now(UTC) + timedelta(minutes=4), settings=settings
        )
    print("runner:", [(o.join_id, o.result, o.reason) for o in outcomes])

    listed = user.call(
        "list",
        "GET",
        "/api/not-recorded",
        note="Two rows the real runner left: `missed` ended `failed` (its 15 minutes passed) and "
        "`second` is `no_capacity` (the one slot is taken by `first`). The runner's clock was moved "
        "four minutes ahead for that run; the rows are its own. The joined meeting `first` is not "
        "here: it is a meeting.",
    ).json()
    ids = {item["title"]: item["join_id"] for item in listed["items"]}
    missed, second = ids["Weekly planning"], ids["Design review"]

    user.call(
        "calendar-upcoming",
        "GET",
        "/api/calendar",
        note="The Upcoming rows carry `join_id`, `can_send_now` and `not_recorded_text`. The rows "
        "that were not missed have `join_id` set and the two flags null/false; `Retro` (in two "
        "hours) has a `scheduled` join.",
    )
    with Session(engine) as session:
        later = session.scalars(
            select(ScheduledJoin)
            .join(CalendarEvent, CalendarEvent.id == ScheduledJoin.calendar_event_id)
            .where(CalendarEvent.source_event_id == "evt-later")
        ).one()
        later_id = later.id

    user.call(
        "send-now-busy",
        "POST",
        f"/api/scheduled-joins/{missed}/send-now",
        note="The one slot is held by `first`'s bot: the pre-flight refuses with 409 `bot_busy`. "
        "Nothing changed (see the next list). The sentence is the same as for a pasted link.",
    )
    user.call("list-after-refusal", "GET", "/api/not-recorded", note="Unchanged by the refusal.")
    user.call(
        "send-now-not-sendable",
        "POST",
        f"/api/scheduled-joins/{later_id}/send-now",
        note="A join that is still `scheduled` (the runner will join it): 409 `cannot_send_now`.",
    )
    user.call(
        "send-now-not-found",
        "POST",
        "/api/scheduled-joins/999999/send-now",
        note="An id that does not exist answers like another person's: 404 `not_found`.",
    )

    with Session(engine) as session:  # a cap that is used up
        admin = session.scalars(select(User).where(User.email == base.ADMIN_EMAIL)).one()
        usage = UsageEntry(user_id=admin.id, email_hash=email_hash(admin.email), seconds=50 * 3600)
        session.add(usage)
        admin.is_admin = False  # an admin has no cap: this account is an ordinary one for the call
        session.commit()
        usage_id = usage.id
    user.call(
        "send-now-hours-cap",
        "POST",
        f"/api/scheduled-joins/{missed}/send-now",
        note="The recorded hours are used up (a usage row of 50 hours was inserted for this call, and "
        "the capture account, an admin, was made an ordinary one for it; both undone after): 403 `hours_cap_reached`. The hours check runs before the capacity check.",
    )
    with Session(engine) as session:
        session.delete(session.get(UsageEntry, usage_id))
        session.scalars(select(User).where(User.email == base.ADMIN_EMAIL)).one().is_admin = True
        session.commit()

    # The first meeting ends: its row leaves the slot (set directly, as a finished meeting would).
    with Session(engine) as session:
        for meeting in session.scalars(select(Meeting)):
            meeting.status = "ready"
            meeting.step = "done"
        session.commit()
    host.live.clear()
    host.error = VexaError("send_bot failed: placeholder detail that must never reach the page")
    user.call(
        "send-now-host-refused",
        "POST",
        f"/api/scheduled-joins/{missed}/send-now",
        note="The bot host refused the bot (the fake host raises `VexaError`): 502 `bot_send_failed`; "
        "no meeting was made and the row is unchanged, so pressing again later works.",
    )
    host.error = None

    sent = user.call(
        "send-now",
        "POST",
        f"/api/scheduled-joins/{missed}/send-now",
        note="The slot is free: the same pre-flight passes and the bot is sent (`FakeVexaHost`, "
        "not a real bot). 200 with the new `meeting_id`.",
    ).json()
    user.call(
        "send-now-again",
        "POST",
        f"/api/scheduled-joins/{missed}/send-now",
        note="The button pressed twice: the same 200 and the same `meeting_id`, no second bot. "
        f"Bots the host was asked for so far: {host.bots_sent}.",
    )
    print("bots sent:", host.bots_sent)
    user.call(
        "send-now-second-busy",
        "POST",
        f"/api/scheduled-joins/{second}/send-now",
        note="Now the first resent meeting holds the slot, so the other `no_capacity` row is "
        "refused 409 `bot_busy`.",
    )
    user.call(
        "list-after-send",
        "GET",
        "/api/not-recorded",
        note="`missed` left the list: it is a meeting now.",
    )
    user.call(
        "meeting-after-send",
        "GET",
        f"/api/meetings/{sent['meeting_id']}",
        note="The meeting Send now made: a calendar meeting with its title, still joining.",
    )

    # A meeting that is over: inserted directly (see the top of this file).
    with Session(engine) as session:
        admin = session.scalars(select(User).where(User.email == base.ADMIN_EMAIL)).one()
        start = datetime.now(UTC) - timedelta(minutes=90)
        over = ScheduledJoin(
            user_id=admin.id,
            platform="google_meet",
            native_id="eee-eeee-eee",
            meeting_url="https://meet.google.com/eee-eeee-eee",
            starts_at=start,
            ends_at=start + timedelta(minutes=30),
            run_at=start - timedelta(minutes=1),
            state="failed",
            skip_reason="not_recorded",
            deadline_at=start + timedelta(minutes=15),
            template_id=made["id"],
        )
        session.add(over)
        session.commit()
        over_id = over.id
    user.call(
        "list-with-ended",
        "GET",
        "/api/not-recorded",
        note="A row for a meeting that is over (inserted directly): `can_send_now` is false and the "
        "item has no title from the calendar, so it is named by platform and code.",
    )
    user.call(
        "send-now-ended",
        "POST",
        f"/api/scheduled-joins/{over_id}/send-now",
        note="The meeting is over: 409 `meeting_ended`.",
    )

    # Another person.
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
    ben.call("other-user-list", "GET", "/api/not-recorded", note="Sees only their own: nothing.")
    ben.call(
        "other-user-send-now",
        "POST",
        f"/api/scheduled-joins/{second}/send-now",
        note="Another person's join: 404 `not_found`, and nothing is sent.",
    )

    # The mail's button, rendered by the real template from the payload the pipeline queues.
    message = mail_templates.render_link(
        mail_templates.NOT_JOINED,
        {"meeting": "Google Meet btp-tkio-gtb", "at": "11:58 GMT+1"},
        "ada@example.test",
        settings,
    )
    link = re.search(r'<a href="([^"]*)"[^>]*>\s*Send it again', message.html)
    base.note_file(
        "could-not-join-mail",
        "The designer's Could not join mail, rendered by the real template. The button opens the "
        "Meetings page in the signed-in app: a plain link, no token, nothing happens by opening "
        "it. The payload is the one the pipeline queues for a not-admitted bot (the meeting and "
        "time are placeholders).",
        {
            "send_it_again_href": link.group(1) if link else None,
            "text_version": message.text,
        },
    )
    print("bots sent in total:", host.bots_sent)


def redact() -> None:
    """Passwords typed into the login calls are placeholders already; say so."""
    for path in OUT.glob("*.json"):
        text = path.read_text()
        for secret in (base.ADMIN_PASSWORD, base.OTHER_PASSWORD):
            text = text.replace(secret, "<password placeholder>")
        path.write_text(text)


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
        bot_max_local=1,
        user_bot_limit=2,
        autojoin_lead_seconds=60,
    )
    google = FakeGoogle(settings)
    host = FakeVexaHost()
    app = create_app(settings)
    app.dependency_overrides[get_vexa] = lambda: host
    app.dependency_overrides[google_oauth.get_google] = lambda: google.oauth
    server, server_base = base.start_server(app)
    engine = create_engine(base.SCRATCH_URL)
    try:
        run(app, server_base, engine, settings, google, host)
    finally:
        server.should_exit = True
        engine.dispose()
        redact()


if __name__ == "__main__":
    main()
