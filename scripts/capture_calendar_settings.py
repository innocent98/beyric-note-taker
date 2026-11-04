"""Drive the calendar WRITE routes over real HTTP and save every exchange for the frontend guide.

    python scripts/capture_calendar_settings.py postgresql+psycopg://USER:PASS@localhost:PORT/postgres

`PUT /api/calendar/settings` (the auto-join rule, the template for calendar meetings) and
`PUT /api/calendar/events/{id}/override` (the switch on each Upcoming row), with every error the
page must handle. Same set-up as `scripts/capture_calendar_state.py` (it is imported for the
synthetic Google calendar): real migrations, the real app under uvicorn, real HTTP, the Google
connection through the real connect flow, and the events from the REAL sync job reading
`FakeGoogleCalendar`. After the switches, a file shows the stored `scheduled_joins` rows, which is
what the runner (task C12) will read.

Files go to `docs/fe-captures/calendar-settings/NN-name.json`.

What is NOT real: Google's servers (consent screen, token endpoint, Calendar API are
`tests/fakes/google.py`, the event bodies are synthetic), so **no real Google calendar has been
read**.
"""

import shutil
import sys
from datetime import UTC, datetime

import capture_calendar_state as st  # reads sys.argv at import, via capture_calendar_connect

base = st.base
OUT = base.ROOT / "docs" / "fe-captures" / "calendar-settings"
base.OUT = OUT

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import google_oauth, vault  # noqa: E402
from app.config import Settings  # noqa: E402
from app.gcal_client import AccessTokenCache, GoogleSource  # noqa: E402
from app.gcal_sync import sync_connection  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import CalendarConnection, CalendarEvent, ScheduledJoin, User  # noqa: E402
from app.passwords import hash_password  # noqa: E402
from app.services import get_vexa  # noqa: E402
from tests.fakes.google import FakeGoogle, FakeGoogleCalendar  # noqa: E402

Recorder = base.Recorder
SETTINGS_URL = "/api/calendar/settings"


def override_url(event_id: int) -> str:
    return f"/api/calendar/events/{event_id}/override"


def join_rows(engine) -> list[dict]:
    """The stored joins as the runner will read them (no meeting link, no token)."""
    with Session(engine) as session:
        rows = session.execute(
            select(
                CalendarEvent.title,
                ScheduledJoin.state,
                ScheduledJoin.skip_reason,
                ScheduledJoin.template_id,
            )
            .join(ScheduledJoin, ScheduledJoin.calendar_event_id == CalendarEvent.id)
            .order_by(CalendarEvent.starts_at)
        ).all()
    return [{"event": t, "state": s, "skip_reason": r, "template_id": tpl} for t, s, r, tpl in rows]


def run(app, server_base, engine, settings, google) -> None:
    now = datetime.now(UTC)
    calendar = FakeGoogleCalendar()
    cache = AccessTokenCache(
        lambda: datetime.now(UTC), settings=settings, transport=calendar.transport
    )
    source = GoogleSource(settings, cache=cache, transport=calendar.transport)

    user = Recorder(server_base, "signed-in user")
    ben = Recorder(server_base, "a different user")
    base.login(user, "login", base.ADMIN_EMAIL, base.ADMIN_PASSWORD)
    base.consent_and_callback(user, google, "connect", return_to="/settings", note="Connect.")
    with Session(engine) as session:
        conn = session.scalars(select(CalendarConnection)).one()
        blob, version = vault.encrypt(
            FakeGoogleCalendar.REFRESH_TOKEN, purpose="refresh_token", owner=conn.user_id
        )
        conn.refresh_token_enc, conn.key_version = blob, version
        session.commit()
    calendar.pages = [st.synthetic_calendar(now)]
    with Session(engine) as session:
        conn = session.scalars(select(CalendarConnection)).one()
        sync_connection(session, conn, source, now=datetime.now(UTC), settings=settings)

    state = user.call(
        "state-before",
        "GET",
        "/api/calendar",
        note="Connected, seven meetings, the default rule and no template chosen: "
        "`calendar_template_id` is null (the default).",
    ).json()
    ids = {event["title"]: event["id"] for event in state["upcoming"]}
    base.note_file(
        "scheduled-joins-before",
        "The joins the sync made under the rule `all`.",
        {"joins": join_rows(engine)},
    )

    # Templates: the four migration 0001 seeds for the admin account are the person's own.
    templates = user.call(
        "templates",
        "GET",
        "/api/templates",
        note="What the select lists: the person's own templates, sorted by name.",
    ).json()
    mine = {t["name"]: t["id"] for t in templates["templates"]}

    # ---- the template for calendar meetings ----
    user.call(
        "settings-template",
        "PUT",
        SETTINGS_URL,
        body={"calendar_template_id": mine["Client call"]},
        note="Choose one of the person's own templates. The answer is the whole state.",
    )
    base.note_file(
        "scheduled-joins-after-template",
        "The template reached the joins that have not sent a bot yet.",
        {"joins": join_rows(engine)},
    )
    user.call(
        "settings-template-null",
        "PUT",
        SETTINGS_URL,
        body={"calendar_template_id": None},
        note="null puts it back to the default (the person's first template).",
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
    base.login(ben, "ben-login", base.OTHER_EMAIL, base.OTHER_PASSWORD)
    theirs = ben.call(
        "ben-template",
        "POST",
        "/api/templates",
        body={"name": "Ben private", "instructions": "Ben's own instructions."},
        note="Another person's template.",
    ).json()
    user.call(
        "settings-template-foreign",
        "PUT",
        SETTINGS_URL,
        body={"calendar_template_id": theirs["id"]},
        note="Another person's template id is refused: 404, nothing stored, nothing of it shown.",
    )
    user.call(
        "settings-template-missing",
        "PUT",
        SETTINGS_URL,
        body={"calendar_template_id": 987654},
        note="A template that does not exist: the same 404.",
    )

    # ---- bad bodies ----
    user.call(
        "settings-empty",
        "PUT",
        SETTINGS_URL,
        body={},
        note="Nothing to change: 422.",
    )
    user.call(
        "settings-bad-rule",
        "PUT",
        SETTINGS_URL,
        body={"rule": "sometimes"},
        note="A rule that is not `all`, `organizer` or `none`: 422.",
    )

    # ---- the rule ----
    user.call(
        "settings-rule-none",
        "PUT",
        SETTINGS_URL,
        body={"rule": "none"},
        note="Only when I turn it on: every joinable meeting is `Off`, its join is cancelled.",
    )
    base.note_file(
        "scheduled-joins-rule-none",
        "Under `none` the joins are cancelled with the reason `rule_none`.",
        {"joins": join_rows(engine)},
    )

    # ---- the switch on each row ----
    user.call(
        "override-on",
        "PUT",
        override_url(ids["Weekly planning"]),
        body={"override": True},
        note="Switch a meeting on under `none`: the answer is the event's new row.",
    )
    base.note_file(
        "scheduled-joins-override-on",
        "The cancelled join was revived (same row) for that one meeting.",
        {"joins": join_rows(engine)},
    )
    user.call(
        "override-off",
        "PUT",
        override_url(ids["Weekly planning"]),
        body={"override": False},
        note="Switch it off: `Off` with the reason `You switched this meeting off`.",
    )
    user.call(
        "override-clear",
        "PUT",
        override_url(ids["Weekly planning"]),
        body={"override": None},
        note="null clears the switch: the rule decides again.",
    )
    user.call(
        "settings-rule-all",
        "PUT",
        SETTINGS_URL,
        body={"rule": "all"},
        note="Back to every meeting with a video link.",
    )
    user.call(
        "override-stranger-invite-before",
        "GET",
        "/api/calendar",
        note="Under `all`, the unanswered invite from outside the person's organisation waits: "
        "`Off`, `Waiting for you to accept the invite`.",
    )
    user.call(
        "override-stranger-invite-on",
        "PUT",
        override_url(ids["Partnership idea"]),
        body={"override": True},
        note="The one-click way in: the switch joins it.",
    )
    base.note_file(
        "scheduled-joins-stranger-invite",
        "That meeting now has a join.",
        {"joins": join_rows(engine)},
    )

    # ---- what cannot be switched ----
    for name, title, why in (
        ("declined", "Budget review", "You declined this invite."),
        ("teams", "Vendor sync", "Teams cannot be joined yet."),
        ("no-link", "Lunch with Sam", "The event has no meeting link."),
    ):
        user.call(
            f"override-409-{name}",
            "PUT",
            override_url(ids[title]),
            body={"override": True},
            note=f"409 `not_joinable`: {why} Nothing is stored; `message` is the reason in words.",
        )

    # ---- a rule change clears the switches on meetings that have not started ----
    user.call(
        "override-before-rule-change",
        "PUT",
        override_url(ids["Customer call"]),
        body={"override": False},
        note="A switch the person made (off) on a meeting that has not started.",
    )
    user.call(
        "settings-rule-organizer",
        "PUT",
        SETTINGS_URL,
        body={"rule": "organizer"},
        note="Changing the rule clears that switch: `Customer call` is `Will join` again (the "
        "person organises it), and `Partnership idea` (switched on above) is `Off` again.",
    )
    base.note_file(
        "scheduled-joins-rule-organizer",
        "The joins follow the new rule and no switch is left.",
        {"joins": join_rows(engine)},
    )
    user.call(
        "settings-rule-organizer-again",
        "PUT",
        SETTINGS_URL,
        body={"rule": "organizer"},
        note="Choosing the rule that is already set changes nothing.",
    )

    # ---- who may call ----
    ben.call(
        "override-foreign-event",
        "PUT",
        override_url(ids["Weekly planning"]),
        body={"override": False},
        note="Another person's event id: 404, nothing changes.",
    )
    user.call(
        "override-missing-event",
        "PUT",
        override_url(987654),
        body={"override": True},
        note="An event that does not exist: the same 404.",
    )
    user.call(
        "override-bad-body",
        "PUT",
        override_url(ids["Weekly planning"]),
        body={"override": "yes"},
        note="`override` must be true, false or null: 422.",
    )
    Recorder(server_base, "visitor").call(
        "settings-without-login",
        "PUT",
        SETTINGS_URL,
        body={"rule": "none"},
        csrf=None,
        note="No session cookie: 401.",
    )
    user.call(
        "settings-without-csrf",
        "PUT",
        SETTINGS_URL,
        body={"rule": "none"},
        csrf=None,
        note="Signed in but no CSRF header: 403.",
    )
    live = app.state.settings
    app.state.settings = live.model_copy(update={"calendar_enabled": False})
    user.call(
        "settings-feature-off",
        "PUT",
        SETTINGS_URL,
        body={"rule": "none"},
        note="`CALENDAR_ENABLED` is false: 404 `calendar_unavailable`, like every calendar route.",
    )
    user.call(
        "override-feature-off",
        "PUT",
        override_url(ids["Weekly planning"]),
        body={"override": True},
        note="The same for the switch.",
    )
    app.state.settings = live
    user.call("disconnect", "POST", "/api/calendar/google/disconnect", note="Disconnect.")
    user.call(
        "override-after-disconnect",
        "PUT",
        override_url(ids["Weekly planning"]),
        body={"override": True},
        note="After a disconnect the events are gone: 404.",
    )
    user.call(
        "settings-not-connected",
        "PUT",
        SETTINGS_URL,
        body={"rule": "all"},
        note="The rule can be chosen with nothing connected; the answer is not-connected.",
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
