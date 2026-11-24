"""Drive "Skip one-on-one meetings" over real HTTP and save every exchange.

    PYTHONPATH=. python scripts/capture_skip_one_on_ones.py postgresql+psycopg://USER:PASS@localhost:PORT/postgres

`PUT /api/calendar/settings` with `skip_one_on_ones` (the second box under "Join automatically"),
what `GET /api/calendar` then says for an event with two, one, no and three people invited, the
joins the re-run makes and cancels, the per-meeting switch beating the box in both directions,
and every error the page must handle. Same set-up as `scripts/capture_join_unanswered.py` (its
helpers are imported): real migrations, the real app under uvicorn, real HTTP, the Google
connection through the real connect flow, and the events from the REAL sync job reading
`FakeGoogleCalendar`.

Files go to `docs/fe-captures/skip-one-on-ones/NN-name.json`.

What is NOT real: Google's servers (consent screen, token endpoint, Calendar API are
`tests/fakes/google.py`, the event bodies are synthetic), so **no real Google calendar has been
read**; no Microsoft calendar at all (Outlook's invite list is only covered by a unit test on the
documented Graph shape); and no bot is sent (the scheduled joins are only read back).
"""

import shutil
import sys
from datetime import UTC, datetime

import capture_calendar_settings as cs  # reads sys.argv at import, via capture_calendar_state

st = cs.st
base = cs.base
OUT = base.ROOT / "docs" / "fe-captures" / "skip-one-on-ones"
base.OUT = OUT

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import google_oauth, vault  # noqa: E402
from app.config import Settings  # noqa: E402
from app.gcal_client import AccessTokenCache, GoogleSource  # noqa: E402
from app.gcal_sync import sync_connection  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import CalendarConnection  # noqa: E402
from app.services import get_bot_provider  # noqa: E402
from tests.fakes.google import FakeGoogle, FakeGoogleCalendar  # noqa: E402
from tests.test_gcal_sync import raw_event  # noqa: E402

Recorder = base.Recorder
SETTINGS_URL = cs.SETTINGS_URL
override_url = cs.override_url


def guests(*others: str) -> dict:
    """An invite list with the signed-in user (organising) and these other guests."""
    me = {
        "email": "admin@example.test",
        "self": True,
        "organizer": True,
        "responseStatus": "accepted",
    }
    return {"attendees": [me] + [{"email": o, "responseStatus": "accepted"} for o in others]}


def synthetic_calendar(now: datetime) -> list[dict]:
    """Five Meet events (placeholder people), one for each count the box looks at."""
    at = st.in_hours
    solo = raw_event("evt-solo", start=at(now, 8), summary="Focus block (no invite list)")
    del solo["attendees"]
    return [
        raw_event(
            "evt-pair",
            start=at(now, 2),
            summary="1:1 with Sam",
            code="aaa-aaaa-aaa",
            **guests("sam@example.test"),
        ),
        raw_event(
            "evt-pair-2",
            start=at(now, 4),
            summary="Mentoring chat",
            code="bbb-bbbb-bbb",
            **guests("lee@example.test"),
        ),
        raw_event(
            "evt-three",
            start=at(now, 5),
            summary="Design review",
            code="ccc-cccc-ccc",
            **guests("sam@example.test", "lee@example.test"),
        ),
        raw_event(
            "evt-me",
            start=at(now, 6),
            summary="Just me on the list",
            code="ddd-dddd-ddd",
            **guests(),
        ),
        solo,
    ]


def run(server_base, engine, settings, google) -> None:
    now = datetime.now(UTC)
    calendar = FakeGoogleCalendar()
    cache = AccessTokenCache(
        lambda: datetime.now(UTC), settings=settings, transport=calendar.transport
    )
    source = GoogleSource(settings, cache=cache, transport=calendar.transport)

    user = Recorder(server_base, "signed-in user")
    base.login(user, "login", base.ADMIN_EMAIL, base.ADMIN_PASSWORD)
    base.consent_and_callback(user, google, "connect", return_to="/settings", note="Connect.")
    with Session(engine) as session:
        conn = session.scalars(select(CalendarConnection)).one()
        blob, version = vault.encrypt(
            FakeGoogleCalendar.REFRESH_TOKEN, purpose="refresh_token", owner=conn.user_id
        )
        conn.refresh_token_enc, conn.key_version = blob, version
        session.commit()
    calendar.pages = [synthetic_calendar(now)]
    with Session(engine) as session:
        conn = session.scalars(select(CalendarConnection)).one()
        sync_connection(session, conn, source, now=datetime.now(UTC), settings=settings)

    state = user.call(
        "state-default",
        "GET",
        "/api/calendar",
        note="Connected, rule `all`, the box never ticked: `skip_one_on_ones` is false and every "
        "event is `Will join`, the two with two people invited included (`attendee_count` 2). "
        "The one with no invite list has `attendee_count` 0 (a solo event) and the one with only "
        "the person on the list has 1.",
    )
    ids = {e["title"]: e["id"] for e in state.json()["upcoming"]}
    base.note_file(
        "scheduled-joins-default",
        "The joins the sync made with the box off: all five events have one.",
        {"joins": cs.join_rows(engine)},
    )
    user.call(
        "settings-skip-on",
        "PUT",
        SETTINGS_URL,
        body={"skip_one_on_ones": True},
        note="Tick the box. The answer is the whole state: `skip_one_on_ones` is true, `rule_text` "
        "names the exception, and the two meetings with `attendee_count` 2 are `Off` with the "
        "reason `Skipped because only two people are invited. Settings can join these too.` "
        "The meetings with 0, 1 and 3 people are still `Will join`.",
    )
    base.note_file(
        "scheduled-joins-skip-on",
        "The re-run cancelled the two joins that had not started, with the reason `one_on_one` "
        "(same rows). The other three are untouched.",
        {"joins": cs.join_rows(engine)},
    )
    user.call(
        "settings-skip-on-again",
        "PUT",
        SETTINGS_URL,
        body={"skip_one_on_ones": True},
        note="Sending the value that is already stored changes nothing.",
    )
    user.call(
        "override-on-skipped-pair",
        "PUT",
        override_url(ids["1:1 with Sam"]),
        body={"override": True},
        note="The person's switch for one skipped meeting: `Will join`, and the join is made "
        "again. The switch beats the box.",
    )
    base.note_file(
        "scheduled-joins-override-on",
        "`1:1 with Sam` has a `scheduled` join again; `Mentoring chat` is still `cancelled` / "
        "`one_on_one`.",
        {"joins": cs.join_rows(engine)},
    )
    user.call(
        "override-off-big-meeting",
        "PUT",
        override_url(ids["Design review"]),
        body={"override": False},
        note="The switch the other way: a three-person meeting switched off is `Off`, reason "
        "`You switched this meeting off`, whatever the box says.",
    )
    user.call(
        "override-clear-skipped-pair",
        "PUT",
        override_url(ids["1:1 with Sam"]),
        body={"override": None},
        note="Clearing the switch hands the meeting back to the rule: skipped again, with the "
        "`one_on_one` reason.",
    )
    user.call(
        "settings-rule-organizer-keeps-skip",
        "PUT",
        SETTINGS_URL,
        body={"rule": "organizer"},
        note="A rule change clears the per-meeting switches (so `Design review` is back on the "
        "rule) and keeps the box. Under `organizer` the box still applies: `rule_text` names it, "
        "and the two meetings the person organises with two people invited stay `Off`.",
    )
    user.call(
        "settings-rule-none-keeps-skip",
        "PUT",
        SETTINGS_URL,
        body={"rule": "none"},
        note="Under `none` nothing is joined by itself, so the box does nothing (show it "
        "disabled): `skip_one_on_ones` is still true, `rule_text` is `Auto-join is off`.",
    )
    user.call(
        "settings-rule-all-keeps-skip",
        "PUT",
        SETTINGS_URL,
        body={"rule": "all"},
        note="Back to `all`: the stored box counts again.",
    )
    user.call(
        "settings-skip-off",
        "PUT",
        SETTINGS_URL,
        body={"skip_one_on_ones": False},
        note="Clear the box: the two meetings are `Will join` again.",
    )
    base.note_file(
        "scheduled-joins-skip-off",
        "The re-run revived the cancelled joins for the meetings that have not started.",
        {"joins": cs.join_rows(engine)},
    )
    user.call(
        "not-recorded-list",
        "GET",
        "/api/not-recorded",
        note="The `Not recorded` list holds meetings the note taker failed to get into. A skipped "
        "one-on-one is a cancelled join (the person's own choice, like a switched-off meeting), "
        "so it is never on it. Every event here is still in the future, so the list is empty "
        "whatever the box says; the same claim is proved against started meetings by "
        "`tests/test_calendar_settings_api.py`.",
    )

    user.call(
        "settings-skip-null",
        "PUT",
        SETTINGS_URL,
        body={"skip_one_on_ones": None},
        note="`null` is not a value for the box: 422, nothing stored.",
    )
    user.call(
        "settings-skip-not-a-boolean",
        "PUT",
        SETTINGS_URL,
        body={"skip_one_on_ones": "yes"},
        note="Not a true or false: 422.",
    )
    user.call(
        "settings-unknown-field",
        "PUT",
        SETTINGS_URL,
        body={"skip_one_on_ones": True, "skip_everything": True},
        note="A field the route does not know: 422, nothing stored (the box stays off).",
    )
    user.call(
        "settings-empty",
        "PUT",
        SETTINGS_URL,
        body={},
        note="Nothing to change: 422.",
    )
    user.call(
        "state-final",
        "GET",
        "/api/calendar",
        note="The box is still off after the refused writes.",
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
    app.dependency_overrides[get_bot_provider] = lambda: base.FakeBot()
    app.dependency_overrides[google_oauth.get_google] = lambda: google.oauth
    server, server_base = base.start_server(app)
    engine = create_engine(base.SCRATCH_URL)
    try:
        run(server_base, engine, settings, google)
    finally:
        server.should_exit = True
        engine.dispose()


if __name__ == "__main__":
    main()
