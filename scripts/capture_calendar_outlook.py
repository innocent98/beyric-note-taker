"""Drive the Outlook (Microsoft) calendar routes over real HTTP and save each exchange.

    python scripts/capture_calendar_outlook.py postgresql+psycopg://USER:PASS@localhost:PORT/postgres

Same method as `scripts/capture_calendar_connect.py` (it reuses its recorder, server and database
helpers): a scratch Postgres database, the real migrations, the real app under uvicorn, walked with
httpx. Each exchange is written to `docs/fe-captures/calendar-outlook/NN-name.json`, so every
claim in `docs/fe-integration-guide-calendar-outlook.md` can be re-derived. (The sibling folder
`docs/fe-captures/calendar-outlook/graph/` holds the M0 run's redacted Microsoft answers and is not
touched here.)

What is real: the app, the guard, CSRF, validation, cookies, the consent URL (built by the real
`MicrosoftOAuth`), the ID token checks, the PKCE and token-exchange code, the probe request, the
vault and the database row, every route's status and body. What is NOT: Microsoft's own servers.
`tests/fakes/microsoft.py` stands in for the consent screen, the token endpoint and the three Graph
calls, with DOCUMENTED shapes (the M0 run has not happened), so **no real Microsoft consent, token
answer, mailbox error or work-account behaviour has been captured**: the guide's table says so. A
revoked grant (`needs_reconnect`) is shown from a row set by hand, as the sync would leave it.

Secrets are masked as in the sibling script (CSRF tokens, cookie values, state, nonce, challenge).
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import capture_calendar_connect as base  # noqa: E402  (reads sys.argv[1], sets the scratch database)
import httpx  # noqa: E402
from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import google_oauth, microsoft_oauth  # noqa: E402
from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import CalendarConnection, CalendarEvent  # noqa: E402
from app.services import get_vexa  # noqa: E402
from tests.fakes.google import FakeGoogle  # noqa: E402
from tests.fakes.microsoft import FakeMicrosoft  # noqa: E402

ROOT = base.ROOT
OUT = ROOT / "docs" / "fe-captures" / "calendar-outlook"
CONNECT = "/api/calendar/microsoft/connect"
CALLBACK = "/api/calendar/microsoft/callback"
DISCONNECT = "/api/calendar/microsoft/disconnect"
WORK_TENANT = "00000000-0000-0000-0000-0000000000aa"
OID_1 = "00000000-0000-0000-0000-000000000001"  # placeholders, never real identifiers
OID_2 = "00000000-0000-0000-0000-000000000002"


def summary(engine) -> list[dict]:
    """The stored Microsoft rows; the token column is only its length."""
    with Session(engine) as session:
        rows = []
        for conn in session.scalars(
            select(CalendarConnection)
            .where(CalendarConnection.provider == "microsoft")
            .order_by(CalendarConnection.id)
        ):
            blob = conn.refresh_token_enc
            rows.append(
                {
                    "user_id": conn.user_id,
                    "provider": conn.provider,
                    "account_email": conn.account_email,
                    "account_subject": conn.account_subject,
                    "tenant_id": conn.tenant_id,
                    "account_kind": conn.account_kind,
                    "tenant_domains": conn.tenant_domains,
                    "account_domain": conn.account_domain,
                    "status": conn.status,
                    "needs_reconnect_reason": conn.needs_reconnect_reason,
                    "granted_scopes": conn.granted_scopes,
                    "refresh_token_enc": (
                        None if blob is None else f"<{type(blob).__name__}, {len(blob)} bytes>"
                    ),
                    "key_version": conn.key_version,
                    "last_sync_at": conn.last_sync_at,
                    "sync_token": conn.sync_token,
                    "expected_expiry_at": conn.expected_expiry_at,
                }
            )
        return json.loads(json.dumps(rows, default=str))


def seed_rows(engine) -> None:
    """Placeholder calendar data for the Microsoft connection: two events with joins (one still
    scheduled, one already joined) and a calendar notification and an unrelated one."""
    start = base.datetime(2026, 11, 12, 9, 0, tzinfo=base.UTC)
    with Session(engine) as session:
        conn = session.scalars(
            select(CalendarConnection).where(CalendarConnection.provider == "microsoft")
        ).one()
        for n, state in enumerate(("scheduled", "joined"), start=1):
            event = CalendarEvent(
                user_id=conn.user_id,
                connection_id=conn.id,
                source_event_id=f"placeholder-disconnect-{n}",
                title=f"Placeholder meeting {n}",
                starts_at=start + base.timedelta(hours=n),
                ends_at=start + base.timedelta(hours=n + 1),
                platform="zoom",
                meeting_url=f"https://us02web.zoom.us/j/8123456789{n}",
                native_id=f"8123456789{n}",
                status="confirmed",
            )
            session.add(event)
            session.flush()
            session.add(
                base.ScheduledJoin(
                    user_id=conn.user_id,
                    calendar_event_id=event.id,
                    platform="zoom",
                    native_id=event.native_id,
                    meeting_url=event.meeting_url,
                    starts_at=event.starts_at,
                    ends_at=event.ends_at,
                    run_at=event.starts_at - base.timedelta(seconds=60),
                    deadline_at=event.starts_at + base.timedelta(minutes=15),
                    state=state,
                )
            )
        for kind, key in (("calendar.not_recorded", "cap-ms-1"), ("usage.hours_low", "cap-ms-2")):
            session.add(
                base.Notification(
                    user_id=conn.user_id, kind=kind, title="Placeholder", dedupe_key=key
                )
            )
        session.commit()


def outlook(client, ms, name, *, note="", **consent):
    """Start a connect, press Accept on the fake Microsoft screen, follow the redirect."""
    started = client.call(f"{name}-connect", "POST", CONNECT, body={}, note="Start the connect.")
    url = started.json()["authorize_url"]
    state = base.parse_qs(base.urlparse(url).query)["state"][0]
    code = ms.consent(url, **consent)
    return client.call(
        f"{name}-callback", "GET", f"{CALLBACK}?code={code}&state={state}", note=note
    ), state


def run(engine, settings, ms, google, off_base) -> None:
    user = base.Recorder(base_url_of["main"], "signed-in user")
    base.login(user, "login", base.ADMIN_EMAIL, base.ADMIN_PASSWORD)
    user.call("state-not-connected", "GET", "/api/calendar", note="Outlook on, nothing connected.")

    visitor = base.Recorder(base_url_of["main"], "visitor")
    visitor.call("connect-without-login", "POST", CONNECT, body={}, csrf=None, note="No session.")
    user.call("connect-without-csrf", "POST", CONNECT, body={}, csrf=None, note="No CSRF header.")
    user.call(
        "connect-meet-feature",
        "POST",
        CONNECT,
        body={"features": ["meet"]},
        note="Microsoft has no Meet feature.",
    )
    user.call(
        "connect",
        "POST",
        CONNECT,
        body={"features": ["calendar"], "return_to": "/settings"},
        note="The authorize URL (state, nonce and challenge masked) and the flow cookie "
        "`calendar_flow_ms` on its own path.",
    )

    # ---- refusals at the callback ----
    user.call(
        "callback-state-unknown",
        "GET",
        f"{CALLBACK}?code=x&state=not-a-state",
        note="An unknown state.",
    )
    url = user.call("connect-for-denied", "POST", CONNECT, body={}).json()["authorize_url"]
    state = base.parse_qs(base.urlparse(url).query)["state"][0]
    user.call(
        "callback-denied",
        "GET",
        f"{CALLBACK}?error=access_denied&state={state}",
        note="The person pressed Cancel (`error=access_denied`).",
    )
    ms.probe = httpx.Response(404, json={"error": {"code": "ResourceNotFound"}})
    outlook(user, ms, "no-mailbox", note="The probe (GET /me/calendarView) answers 404.")
    ms.probe = httpx.Response(200, json={"value": []})
    outlook(
        user,
        ms,
        "tenant-not-allowed",
        tid=WORK_TENANT,
        note="A work-account tenant at the consumers endpoint (the pilot): refused before storage.",
    )
    outlook(
        user,
        ms,
        "scope-missing",
        grant={"openid", "profile", "offline_access", "https://graph.microsoft.com/User.Read"},
        note="The calendar permission was not granted.",
    )
    base.note_file("rows-after-refusals", "No Microsoft row exists yet.", {"rows": summary(engine)})

    # ---- a connect that works ----
    outlook(user, ms, "personal", oid=OID_1, note="A personal Outlook.com account connects.")
    base.note_file(
        "rows-after-connect",
        "The row: identity is tenant + object id, the address is a label, the token is its length.",
        {"rows": summary(engine)},
    )
    user.call("state-connected", "GET", "/api/calendar", note="Connected, before the first sync.")

    # ---- the Teams row, a Zoom row ----
    with Session(engine) as session:
        conn = session.scalars(select(CalendarConnection)).one()
        start = base.datetime.now(base.UTC) + base.timedelta(hours=3)
        for n, (platform, url_, native, title) in enumerate(
            (
                (
                    "teams",
                    "https://teams.microsoft.com/l/meetup-join/19%3ameeting_x%40thread.v2/0",
                    None,
                    "Planning (Teams)",
                ),
                ("zoom", "https://us02web.zoom.us/j/81234567890", "81234567890", "Standup (Zoom)"),
            ),
            start=1,
        ):
            session.add(
                CalendarEvent(
                    user_id=conn.user_id,
                    connection_id=conn.id,
                    source_event_id=f"placeholder-outlook-{n}",
                    title=title,
                    starts_at=start + base.timedelta(hours=n),
                    ends_at=start + base.timedelta(hours=n, minutes=30),
                    platform=platform,
                    meeting_url=url_,
                    native_id=native,
                    organizer_self=True,
                    self_response="accepted",
                    attendee_count=2,
                    status="confirmed",
                )
            )
        session.commit()
    state = user.call(
        "state-upcoming-teams-and-zoom",
        "GET",
        "/api/calendar",
        note="Two seeded events as the sync would store them: the Teams one is `Cannot join` "
        "while Teams joining is off, the Zoom one `Will join`.",
    ).json()
    teams_id = next(r["id"] for r in state["upcoming"] if r["platform"] == "teams")
    user.call(
        "override-teams-409",
        "PUT",
        f"/api/calendar/events/{teams_id}/override",
        body={"override": True},
        note="The per-meeting switch on a Teams event: 409 `not_joinable`.",
    )

    # ---- one calendar at a time, both ways ----
    other = base.Recorder(base_url_of["main"], "second tab")
    base.login(other, "second-login", base.ADMIN_EMAIL, base.ADMIN_PASSWORD)
    other.call(
        "google-connect-while-outlook-is-connected",
        "POST",
        "/api/calendar/google/connect",
        body={},
        note="Another provider's calendar is live: 409 `calendar_already_connected`.",
    )
    # a second identity over the working connection
    outlook(
        user,
        ms,
        "different-account",
        oid=OID_2,
        note="Another Microsoft identity over a working connection is refused.",
    )
    # the other tab finishes a Google connect that started before Outlook was connected
    user.call(
        "disconnect-for-race", "POST", DISCONNECT, note="Disconnect Outlook to set up a race."
    )
    started = user.call("race-outlook-connect", "POST", CONNECT, body={}).json()["authorize_url"]
    ms_state = base.parse_qs(base.urlparse(started).query)["state"][0]
    ms_code = ms.consent(started, oid=OID_1)
    g_url = user.call(
        "race-google-connect", "POST", "/api/calendar/google/connect", body={}
    ).json()["authorize_url"]
    g_state = base.parse_qs(base.urlparse(g_url).query)["state"][0]
    user.call(
        "race-google-callback",
        "GET",
        f"/api/calendar/google/callback?code={google.consent(g_url)}&state={g_state}",
        note="Google finishes first.",
    )
    user.call(
        "race-outlook-callback",
        "GET",
        f"{CALLBACK}?code={ms_code}&state={ms_state}",
        note="Then the Outlook callback arrives: `already_connected`, nothing stored.",
    )
    user.call("race-google-disconnect", "POST", "/api/calendar/google/disconnect")

    # ---- reconnect state, as the sync would leave it ----
    outlook(user, ms, "again", oid=OID_1, note="Connected again.")
    with Session(engine) as session:
        conn = session.scalars(
            select(CalendarConnection).where(CalendarConnection.provider == "microsoft")
        ).one()
        conn.status, conn.needs_reconnect_reason = "needs_reconnect", "revoked"
        conn.last_error = "AuthRevoked: Microsoft no longer honours the refresh token (HTTP 400)"
        session.commit()
    user.call(
        "state-needs-reconnect",
        "GET",
        "/api/calendar",
        note="The row was set by hand to what the sync leaves after a revoked grant (a real "
        "revoked grant has not been captured).",
    )
    user.call(
        "reconnect-connect", "POST", CONNECT, body={}, note="`connect` works for a dead connection."
    )

    # ---- disconnect ----
    seed_rows(engine)
    user.call(
        "disconnect",
        "POST",
        DISCONNECT,
        note="Deletes the local data, sends nothing to Microsoft: `revoked: false` and a `reason`.",
    )
    base.note_file(
        "rows-after-disconnect",
        "Emptied row (status disconnected, no token, no identity) and the counts.",
        {"rows": summary(engine), "calendar": base.calendar_counts(engine)},
    )
    user.call(
        "disconnect-again", "POST", DISCONNECT, note="Safe to repeat: `revoked: true`, no reason."
    )
    user.call("state-after-disconnect", "GET", "/api/calendar", note="Not connected again.")

    # ---- the flag off ----
    off = base.Recorder(off_base, "signed-in user, Outlook switched off")
    base.login(off, "off-login", base.ADMIN_EMAIL, base.ADMIN_PASSWORD)
    off.call("off-state", "GET", "/api/calendar", note="`microsoft_enabled` is false.")
    off.call("off-connect", "POST", CONNECT, body={}, note="404 `calendar_unavailable`.")
    off.call(
        "off-callback", "GET", f"{CALLBACK}?code=x&state=y", note="404 `calendar_unavailable`."
    )
    off.call(
        "off-disconnect",
        "POST",
        DISCONNECT,
        note="Still 200: deleting your data never depends on the flag.",
    )


base_url_of: dict[str, str] = {}


def main() -> None:
    if not sys.argv[1:]:
        sys.exit(__doc__)
    base.prepare_database()
    base.OUT = OUT
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("[0-9][0-9]-*.json"):
        old.unlink()
    base.Recorder.count = 0
    values = dict(
        _env_file=None,
        app_env="test",
        session_secret="capture-secret-" + "x" * 30,
        cookie_secure=False,
        database_url=base.SCRATCH_URL,
        public_base_url="https://notes.example.test",
        google_client_id="capture-client-id.apps.example.test",
        google_client_secret="capture-client-secret",
        microsoft_client_id="capture-ms-client-id",
        microsoft_client_secret="capture-ms-client-secret",
        calendar_enabled=True,
        token_enc_key=base.KEY,
    )
    settings = Settings(**values, microsoft_calendar_enabled=True)
    off_settings = Settings(**values, microsoft_calendar_enabled=False)
    google = FakeGoogle(settings)
    ms = FakeMicrosoft(settings)

    def build(cfg):
        app = create_app(cfg)
        app.dependency_overrides[get_vexa] = lambda: base.FakeVexa()
        app.dependency_overrides[google_oauth.get_google] = lambda: google.oauth
        app.dependency_overrides[microsoft_oauth.get_microsoft] = lambda: ms.oauth
        return app

    server, base_url_of["main"] = base.start_server(build(settings))
    off_server, off_base = base.start_server(build(off_settings))
    engine = create_engine(base.SCRATCH_URL)
    try:
        run(engine, settings, ms, google, off_base)
    finally:
        server.should_exit = True
        off_server.should_exit = True
        engine.dispose()


if __name__ == "__main__":
    main()
