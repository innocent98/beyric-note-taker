"""Drive the calendar-per-workspace routes over real HTTP and save every exchange for the guide.

    python scripts/capture_workspaces_calendar.py postgresql+psycopg://USER:PASS@localhost:PORT/postgres

Same setup as `scripts/capture_calendar_connect.py` (imported for the HTTP recorder, the scratch
database and the app server): real migrations, the real app under uvicorn, real HTTP. Ada has her
personal workspace and creates Luran through the real `POST /api/teams`; Google connects through the
real connect flow in each workspace, named by `X-Team-Id`. Files go to
`docs/fe-captures/workspaces/calendar/NN-name.json`.

The same meeting is put on both calendars by the REAL materialiser (`gcal_sync.apply_batch`) from a
synthetic Google event body, so the Upcoming rows, the arbitration, the joins and the switch are the
real code over the real database.

What is NOT real: Google's servers (consent screen, token endpoint: `tests/fakes/google.py`; the
event body is synthetic, built from Google's documented shape), and nobody's calendar was read, so
**nothing here was verified against a real Google or Microsoft calendar**. A running bot (the 409
`already_recording`) and another provider's calendar (the 409 `calendar_already_connected`) are set
directly in the database because nothing here can start a real bot or sign in to Microsoft; a note
in each file says so. The people, links and tokens are placeholders.
"""

import shutil
import sys
from datetime import UTC, datetime, timedelta

import capture_calendar_connect as base  # reads sys.argv at import (the admin database URL)

OUT = base.ROOT / "docs" / "fe-captures" / "workspaces" / "calendar"
base.OUT = OUT

from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import google_oauth, workspace_prefs  # noqa: E402
from app.config import Settings  # noqa: E402
from app.gcal_sync import apply_batch  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import (  # noqa: E402
    CalendarConnection,
    Meeting,
    ScheduledJoin,
    Team,
    TeamMember,
    Template,
    User,
    UserSettings,
)
from app.passwords import hash_password  # noqa: E402
from app.services import get_bot_provider  # noqa: E402
from tests.fakes.google import FakeGoogle  # noqa: E402
from tests.test_gcal_sync import batch, raw_event  # noqa: E402

Recorder = base.Recorder
CODE = "abc-defg-hij"


def connect_in(user, google, name, team, *, note="", return_to="/settings", **identity):
    """Start the connect from workspace `team`, press Allow on the fake consent screen, follow the
    redirect to the callback (no header on it: the workspace is remembered with the state)."""
    started = user.call(
        f"{name}-start",
        "POST",
        "/api/calendar/google/connect",
        body={"return_to": return_to},
        team=team,
        note=f"Starts the connection from workspace {team}.",
    )
    from urllib.parse import parse_qs, urlparse

    url = started.json()["authorize_url"]
    state = parse_qs(urlparse(url).query)["state"][0]
    code = google.consent(url, **identity)
    return user.call(
        f"{name}-callback",
        "GET",
        f"/api/calendar/google/callback?code={code}&state={state}",
        note=note,
    )


def run(app, server_base, engine, settings, google) -> None:
    now = datetime.now(UTC)
    start = (now + timedelta(hours=3)).replace(minute=0, second=0, microsecond=0)
    ada = Recorder(server_base, "Ada, signed in")
    base.login(ada, "login", base.ADMIN_EMAIL, base.ADMIN_PASSWORD)
    created = ada.call(
        "create-luran",
        "POST",
        "/api/teams",
        body={"name": "Luran"},
        note="A second workspace (a team Ada owns). Her personal workspace already exists.",
    )
    luran = created.json()["id"]
    listing = ada.call("teams", "GET", "/api/teams").json()
    home = next(t["id"] for t in listing["teams"] if t["is_personal"])
    with Session(engine) as session:
        user = session.scalars(select(User).where(User.email == base.ADMIN_EMAIL)).one()
        template = session.scalars(
            select(Template).where(Template.owner_user_id == user.id).order_by(Template.id)
        ).first()  # the account made by migration 0006 already has the seeded templates
        if session.get(UserSettings, user.id) is None:
            session.add(UserSettings(user_id=user.id, calendar_template_id=template.id))
        session.commit()
        ada_id = user.id
        template_id = template.id

    ada.call(
        "calendar-before-any-connection",
        "GET",
        "/api/calendar",
        team=home,
        note="Nothing connected in the personal workspace yet.",
    )
    connect_in(
        ada,
        google,
        "connect-personal",
        home,
        note="The personal workspace is not the default one (Ada is in one ordinary team), so the "
        "redirect carries `team=` and the page returns to the workspace the connect began in.",
        return_to="/settings",
    )
    connect_in(
        ada,
        google,
        "connect-luran",
        luran,
        note="The SAME Google account in a second workspace: allowed. Luran is Ada's default "
        "workspace, so the redirect adds nothing.",
    )
    connect_in(
        ada,
        google,
        "connect-other-account-in-luran",
        luran,
        email="other@example.test",
        sub="g-other-2",
        note="A different Google account in a workspace that already has one: refused, and "
        "nothing is changed.",
    )
    ada.call(
        "calendar-personal-connected",
        "GET",
        "/api/calendar",
        team=home,
        note="The personal workspace's own calendar state.",
    )
    ada.call(
        "calendar-luran-connected",
        "GET",
        "/api/calendar",
        team=luran,
        note="Luran's own calendar state: the same account, a separate connection.",
    )
    with Session(engine) as session:
        stranger = User(
            email=base.OTHER_EMAIL,
            password_hash=hash_password(base.OTHER_PASSWORD),
            email_verified_at=datetime.now(UTC),
        )
        session.add(stranger)
        session.flush()
        foreign = Team(owner_user_id=stranger.id, name="Elsewhere", is_personal=False)
        session.add(foreign)
        session.flush()
        session.add(TeamMember(team_id=foreign.id, user_id=stranger.id, role="owner"))
        session.commit()
        foreign_id = foreign.id
    ada.call(
        "calendar-foreign-workspace",
        "GET",
        "/api/calendar",
        team=foreign_id,
        note="A workspace Ada is not in: 404 `not_found`, never another workspace's calendar.",
    )

    # The same meeting on both calendars, planned by the real materialiser.
    with Session(engine) as session:
        for conn in session.scalars(select(CalendarConnection).order_by(CalendarConnection.id)):
            prefs = workspace_prefs.for_user(session, ada_id, conn.team_id)
            apply_batch(
                session,
                conn,
                batch(
                    raw_event(
                        "evt-weekly",
                        start=start,
                        code=CODE,
                        summary="Weekly planning",
                        recurringEventId="series-weekly",
                    ),
                    raw_event(
                        "evt-only-luran" if conn.team_id == luran else "evt-only-home",
                        start=start + timedelta(hours=2),
                        code="lur-anxx-xxx" if conn.team_id == luran else "hom-exxx-xxx",
                        summary="Luran roadmap" if conn.team_id == luran else "Dentist call",
                    ),
                ),
                prefs,
                now=now,
                settings=settings,
            )
    ada.call(
        "upcoming-personal-records",
        "GET",
        "/api/calendar",
        team=home,
        note="The same meeting is on both calendars. The personal workspace was connected first "
        "and neither switched it, so it records it: `will_join` true, no `recorded_in`.",
    )
    upcoming_luran = ada.call(
        "upcoming-luran-recorded-elsewhere",
        "GET",
        "/api/calendar",
        team=luran,
        note="Luran's copy of the same meeting: `recorded_in` names the workspace that records it, "
        "`recorded_in_text` is the sentence to show next to a Switch here button, `will_join` is "
        "false and `will_join_text` says where. The other rows are Luran's own.",
    ).json()
    shared = next(e for e in upcoming_luran["upcoming"] if e["recorded_in"])
    ada.call(
        "recurring-luran",
        "GET",
        "/api/calendar/recurring",
        team=luran,
        note="The Recurring cards are the workspace's own connection's.",
    )
    ada.call(
        "recurring-personal",
        "GET",
        "/api/calendar/recurring",
        team=home,
        note="The personal workspace's cards.",
    )
    ada.call(
        "switch-to-luran",
        "PUT",
        f"/api/calendar/events/{shared['id']}/override",
        body={"override": True},
        team=luran,
        note="Switching the meeting ON in Luran moves the recording there: the answer carries "
        "`moved_text` (show it where the person sees it), Luran's join is made and the personal "
        "workspace's, which had not started, is cancelled (`recorded_elsewhere`).",
    )
    ada.call(
        "upcoming-personal-after-switch",
        "GET",
        "/api/calendar",
        team=home,
        note="Now it is the personal workspace's row that says where it is recorded.",
    )

    # A bot already in the meeting is never moved: put one in Luran's join, then try to switch back.
    with Session(engine) as session:
        join = session.scalars(
            select(ScheduledJoin).where(
                ScheduledJoin.team_id == luran, ScheduledJoin.native_id == CODE
            )
        ).one()
        meeting = Meeting(
            owner_user_id=ada_id,
            url=join.meeting_url,
            platform=join.platform,
            native_id=join.native_id,
            template_id=template_id,
            status="bot_sent",
            workspace_team_id=luran,
        )
        session.add(meeting)
        session.flush()
        join.state, join.meeting_id = "joined", meeting.id
        session.commit()
    with Session(engine) as session:
        personal_event = session.scalars(
            select(CalendarConnection).where(CalendarConnection.team_id == home)
        ).one()
        from app.models import CalendarEvent

        home_event = session.scalars(
            select(CalendarEvent).where(
                CalendarEvent.connection_id == personal_event.id,
                CalendarEvent.native_id == CODE,
            )
        ).one()
        home_event_id = home_event.id
    ada.call(
        "switch-refused-bot-already-in",
        "PUT",
        f"/api/calendar/events/{home_event_id}/override",
        body={"override": True},
        team=home,
        note="NOT a real bot: Luran's join was set to `joined` directly with a meeting row. A "
        "note taker that is already in is never moved: 409 `already_recording`, nothing changes.",
    )

    ada.call(
        "not-recorded-luran",
        "GET",
        "/api/not-recorded",
        team=luran,
        note="The meetings the note taker missed are the workspace's own (none here).",
    )

    # Disconnecting: the grant is reference counted.
    ada.call(
        "disconnect-personal",
        "POST",
        "/api/calendar/google/disconnect",
        team=home,
        note="Disconnecting the personal workspace while Luran still uses the same Google account: "
        "`revoked` is true but nothing was revoked at Google (the grant is still in use); Luran's "
        "calendar stays connected.",
    )
    base.note_file(
        "google-revoke-calls-after-first-disconnect",
        "What the fake Google was asked to revoke so far (the proof that the first disconnect "
        "revoked nothing).",
        {"revoked_tokens": len(google.revoked)},
    )
    ada.call(
        "calendar-luran-still-connected",
        "GET",
        "/api/calendar",
        team=luran,
        note="Luran's connection is untouched.",
    )

    # Leaving a workspace takes the member's connection there with them.
    with Session(engine) as session:
        boss = session.scalars(select(User).where(User.email == base.OTHER_EMAIL)).one()
        session.add(TeamMember(team_id=foreign_id, user_id=ada_id, role="member"))
        session.add(
            CalendarConnection(
                user_id=ada_id,
                team_id=foreign_id,
                provider="microsoft",
                status="connected",
                granted_scopes="https://graph.microsoft.com/Calendars.Read",
            )
        )
        session.commit()
        boss_id = boss.id
        ada_user_id = ada_id
    ada.call(
        "connect-refused-other-provider-in-workspace",
        "POST",
        "/api/calendar/google/connect",
        body={},
        team=foreign_id,
        note="NOT a real Outlook connection: a Microsoft calendar row was set directly in "
        "Elsewhere. One calendar provider per workspace: 409 `calendar_already_connected`. The "
        "same person can connect Google in a different workspace (captured above).",
    )
    with Session(engine) as session:
        session.query(CalendarConnection).filter_by(
            user_id=ada_id, team_id=foreign_id, provider="microsoft"
        ).delete()
        session.commit()
    connect_in(
        ada,
        google,
        "connect-in-elsewhere",
        foreign_id,
        note="Ada, a member of Elsewhere, connects Google there with another account.",
        email="work@example.test",
        sub="g-work-9",
    )
    ada.call(
        "leave-elsewhere",
        "DELETE",
        f"/api/team/members/{ada_user_id}",
        team=foreign_id,
        note="Ada leaves Elsewhere: her connection there and its joins are gone, and the Google "
        "grant of THAT account is revoked (no other workspace of hers uses it). Her other "
        "workspaces are untouched.",
    )
    ada.call(
        "calendar-after-leaving",
        "GET",
        "/api/calendar",
        team=foreign_id,
        note="She is no longer in Elsewhere: 404 `not_found`.",
    )
    base.note_file(
        "google-revoke-calls-after-leaving",
        "Tokens revoked at the fake Google by the end (the work account's, at the leave).",
        {"revoked_tokens": len(google.revoked), "boss_id": boss_id},
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
        google_client_id="capture-client-id.apps.example.test",
        google_client_secret="capture-client-secret",
        calendar_enabled=True,
        token_enc_key=base.KEY,
        autojoin_lead_seconds=60,
    )
    google = FakeGoogle(settings)
    app = create_app(settings)
    app.dependency_overrides[get_bot_provider] = lambda: base.FakeBot()
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
