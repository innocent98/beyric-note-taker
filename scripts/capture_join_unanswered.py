"""Drive "Also join invites I haven't answered yet" over real HTTP and save every exchange.

    python scripts/capture_join_unanswered.py postgresql+psycopg://USER:PASS@localhost:PORT/postgres

`PUT /api/calendar/settings` with `join_unanswered` (the box under "All meetings with a video
link"), what `GET /api/calendar` then says for the unanswered invite from outside the person's
organisation, the joins the re-run makes and cancels, and every error the page must handle. Same
set-up as `scripts/capture_calendar_settings.py` (it is imported for the helpers): real migrations,
the real app under uvicorn, real HTTP, the Google connection through the real connect flow, and the
events from the REAL sync job reading `FakeGoogleCalendar`.

Files go to `docs/fe-captures/join-unanswered/NN-name.json`.

What is NOT real: Google's servers (consent screen, token endpoint, Calendar API are
`tests/fakes/google.py`, the event bodies are synthetic), so **no real Google calendar has been
read**, and no bot is sent (the scheduled joins are only read back).
"""

import shutil
import sys
from datetime import UTC, datetime

import capture_calendar_settings as cs  # reads sys.argv at import, via capture_calendar_state

st = cs.st
base = cs.base
OUT = base.ROOT / "docs" / "fe-captures" / "join-unanswered"
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

Recorder = base.Recorder
SETTINGS_URL = cs.SETTINGS_URL


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
    calendar.pages = [st.synthetic_calendar(now)]
    with Session(engine) as session:
        conn = session.scalars(select(CalendarConnection)).one()
        sync_connection(session, conn, source, now=datetime.now(UTC), settings=settings)

    user.call(
        "state-default",
        "GET",
        "/api/calendar",
        note="Connected, rule `all`, the box never ticked: `join_unanswered` is false, `rule_text` "
        "names the exception, and `Partnership idea` (an unanswered invite from outside the "
        "person's organisation) is `Off` with the reason `Waiting for you to accept the invite`.",
    )
    base.note_file(
        "scheduled-joins-default",
        "The joins the sync made with the box off: `Partnership idea` has none.",
        {"joins": cs.join_rows(engine)},
    )
    user.call(
        "settings-box-on",
        "PUT",
        SETTINGS_URL,
        body={"join_unanswered": True},
        note="Tick the box. The answer is the whole state: `join_unanswered` is true, `rule_text` "
        "is the plain label again, and `Partnership idea` is `Will join` (`reason_text` null).",
    )
    base.note_file(
        "scheduled-joins-box-on",
        "The re-run made the join for `Partnership idea` (state `scheduled`). `Budget review` "
        "(declined) still has none.",
        {"joins": cs.join_rows(engine)},
    )
    user.call(
        "settings-box-on-again",
        "PUT",
        SETTINGS_URL,
        body={"join_unanswered": True},
        note="Sending the value that is already stored changes nothing.",
    )
    user.call(
        "settings-rule-organizer-keeps-box",
        "PUT",
        SETTINGS_URL,
        body={"rule": "organizer"},
        note="A rule change that leaves `join_unanswered` out keeps it: still true, but under "
        "`organizer` it does nothing (show the box disabled).",
    )
    user.call(
        "settings-rule-all-keeps-box",
        "PUT",
        SETTINGS_URL,
        body={"rule": "all"},
        note="Back to `all`: the stored box counts again, `Partnership idea` is `Will join`.",
    )
    user.call(
        "settings-box-off",
        "PUT",
        SETTINGS_URL,
        body={"join_unanswered": False},
        note="Clear the box: `Partnership idea` is `Off` again.",
    )
    base.note_file(
        "scheduled-joins-box-off",
        "The join that had not started is cancelled with the reason `not_accepted` (same row).",
        {"joins": cs.join_rows(engine)},
    )

    user.call(
        "settings-box-null",
        "PUT",
        SETTINGS_URL,
        body={"join_unanswered": None},
        note="`null` is not a value for the box: 422, nothing stored.",
    )
    user.call(
        "settings-box-not-a-boolean",
        "PUT",
        SETTINGS_URL,
        body={"join_unanswered": "yes"},
        note="Not a true or false: 422.",
    )
    user.call(
        "settings-unknown-field",
        "PUT",
        SETTINGS_URL,
        body={"join_unanswered": True, "join_everything": True},
        note="A field the route does not know: 422, nothing stored (the box stays off).",
    )
    user.call(
        "settings-empty",
        "PUT",
        SETTINGS_URL,
        body={},
        note="Nothing to change: 422. The message now names all three fields "
        "(it used to name two).",
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
