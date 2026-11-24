"""Calendar connections per workspace (workspaces design, section 4; plan task 8).

A connection belongs to ONE workspace, and a person may hold a Google (or Outlook) connection in
several of them, even the same account. The connect flow starts from the active workspace
(`X-Team-Id`) and its callback files the connection there and returns to it; the refusals
(`different_account`, `already_connected`) apply per workspace; and disconnecting one workspace
revokes the provider's grant only when no other connection of the person uses the same account.

Google's side is `tests/fakes/google.py`, as in `tests/test_calendar_connect.py`. Nothing here ran
against a real Google or Microsoft calendar.
"""

from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import select

from app import calendar_connect, teams
from app.models import (
    CalendarConnection,
    CalendarEvent,
    Notification,
    OAuthFlow,
    ScheduledJoin,
)
from tests.test_api import CSRF, error_of
from tests.test_calendar_connect import (  # noqa: F401  (fixtures used by name)
    CALENDAR_SCOPE,
    CALLBACK,
    CONNECT,
    DISCONNECT,
    add_join,
    add_notification,
    callback,
    capp,
    count,
    google,
    landing,
    settings,
    vault_key,
    web,
)
from tests.test_multi_team_leaks import new_team
from tests.test_web import (  # noqa: F401
    fake_bot,
    fake_openai,
    make_user,
    owner,
    stranger,
    template,
)

MS_SCOPE = "https://graph.microsoft.com/Calendars.Read"


@pytest.fixture
def luran(session, owner):
    teams.ensure_personal_team(session, owner)
    session.commit()
    return new_team(session, owner, "Luran")


@pytest.fixture
def home(session, owner, luran):
    return teams.personal_team(session, owner.id)


def headers(web, team):
    return {CSRF: web.csrf, "X-Team-Id": str(team.id)}


def start(web, team, features=None, **body):
    if features is not None:
        body["features"] = features
    return web.client.post(CONNECT, json=body, headers=headers(web, team))


def connect_in(web, google, team, **consent):
    """Connect a Google calendar from `team`: start with its header, consent, finish the callback
    (which carries no header, as in a browser coming back from Google). Returns the callback."""
    response = start(web, team)
    assert response.status_code == 200, response.text
    url = response.json()["authorize_url"]
    state = parse_qs(urlparse(url).query)["state"][0]
    code = google.consent(url, **consent)
    return callback(web, state, code)


def connections(session, user) -> dict[int, CalendarConnection]:
    session.expire_all()
    rows = session.scalars(select(CalendarConnection).where(CalendarConnection.user_id == user.id))
    return {row.team_id: row for row in rows}


def disconnect_in(web, team, provider="google"):
    return web.client.post(f"/api/calendar/{provider}/disconnect", headers=headers(web, team))


# ---- one person, several workspaces ----


def test_a_person_connects_google_in_a_workspace_and_the_same_account_in_another(
    web, session, owner, google, home, luran
):
    first = connect_in(web, google, home)
    second = connect_in(web, google, luran)

    assert landing(first)[1]["calendar"] == "connected"
    assert landing(second)[1]["calendar"] == "connected"
    rows = connections(session, owner)
    assert set(rows) == {home.id, luran.id}
    assert rows[home.id].id != rows[luran.id].id
    assert rows[home.id].account_subject == rows[luran.id].account_subject == "g-grace-1"
    for row in rows.values():  # each holds its own token and is ready for a first sync
        assert row.status == "connected" and row.refresh_token_enc is not None
        assert row.last_sync_at is None


def test_a_workspace_calendar_never_changes_the_other_workspaces_row(
    web, session, owner, google, home, luran
):
    connect_in(web, google, home)
    before = connections(session, owner)[home.id]
    snapshot = (before.id, before.refresh_token_enc, before.connected_at, before.account_email)

    connect_in(web, google, luran, email="grace@example.test")

    after = connections(session, owner)[home.id]
    assert (after.id, after.refresh_token_enc, after.connected_at, after.account_email) == snapshot


def test_a_second_google_account_in_the_same_workspace_is_refused_and_keeps_the_first(
    web, session, owner, google, luran
):
    connect_in(web, google, luran)

    response = connect_in(web, google, luran, email="other@example.test", sub="g-other-2")

    assert landing(response)[1] == {"calendar_error": "different_account"}
    [row] = connections(session, owner).values()
    assert row.account_subject == "g-grace-1" and row.status == "connected"


def test_the_same_account_again_in_the_same_workspace_is_a_reconnect_not_a_second_row(
    web, session, owner, google, luran
):
    connect_in(web, google, luran)
    first = connections(session, owner)[luran.id].id

    connect_in(web, google, luran)

    assert [row.id for row in connections(session, owner).values()] == [first]


def test_another_providers_calendar_in_the_same_workspace_is_refused_but_not_in_another(
    web, session, owner, google, home, luran
):
    session.add(
        CalendarConnection(
            user_id=owner.id,
            team_id=luran.id,
            provider="microsoft",
            status="connected",
            granted_scopes=MS_SCOPE,
        )
    )
    session.commit()

    refused = start(web, luran)
    assert error_of(refused, 409, "calendar_already_connected")
    assert start(web, home).status_code == 200  # Google in the personal workspace is fine


def test_the_finish_refuses_when_another_provider_took_the_workspace_meanwhile(
    web, session, owner, google, luran
):
    response = start(web, luran)
    url = response.json()["authorize_url"]
    state = parse_qs(urlparse(url).query)["state"][0]
    code = google.consent(url)
    session.add(
        CalendarConnection(
            user_id=owner.id,
            team_id=luran.id,
            provider="microsoft",
            status="connected",
            granted_scopes=MS_SCOPE,
        )
    )
    session.commit()

    landed = landing(callback(web, state, code))

    assert landed[1] == {"calendar_error": "already_connected"}
    assert [r.provider for r in connections(session, owner).values()] == ["microsoft"]


# ---- the flow carries the workspace ----


def test_the_flow_remembers_the_workspace_and_the_callback_files_the_connection_there(
    web, session, owner, google, home, luran
):
    new_team(
        session, owner, "Side"
    )  # two ordinary teams: the default workspace is the personal one
    response = start(web, luran)
    url = response.json()["authorize_url"]
    state = parse_qs(urlparse(url).query)["state"][0]
    session.expire_all()
    [flow] = session.scalars(select(OAuthFlow)).all()
    assert flow.team_id == luran.id

    done = callback(web, state, google.consent(url))  # no header on the callback

    path, query = landing(done)
    assert query["calendar"] == "connected"
    assert query["team"] == str(luran.id)  # the page returns to the workspace it left
    assert list(connections(session, owner)) == [luran.id]


def test_a_failure_names_the_workspace_too(web, session, owner, google, luran):
    new_team(session, owner, "Side")
    response = start(web, luran)
    url = response.json()["authorize_url"]
    state = parse_qs(urlparse(url).query)["state"][0]

    landed = landing(callback(web, state, google.consent(url, grant={"openid"})))

    assert landed[1] == {"calendar_error": "scope_missing", "team": str(luran.id)}


def test_the_default_workspace_adds_nothing_to_the_return_address(
    web, session, owner, google, luran
):
    # the person is in one ordinary team besides their own: that team IS their default workspace
    done = connect_in(web, google, luran)

    assert landing(done)[1] == {"calendar": "connected"}


def test_no_header_starts_in_the_default_workspace(web, session, owner, google, home, luran):
    # the person is in one ordinary team besides their own: that is their default workspace
    response = web.client.post(CONNECT, json={}, headers={CSRF: web.csrf})
    assert response.status_code == 200
    session.expire_all()
    [flow] = session.scalars(select(OAuthFlow)).all()
    assert flow.team_id == teams.team_of(session, owner.id).id


def test_a_workspace_the_person_is_not_in_cannot_start_a_connect(
    web, session, owner, google, stranger
):
    foreign = new_team(session, stranger, "Elsewhere")

    error_of(start(web, foreign), 404, "not_found")

    assert session.scalars(select(OAuthFlow)).all() == []


def test_leaving_the_workspace_during_consent_files_nothing(web, session, owner, google, luran):
    response = start(web, luran)
    url = response.json()["authorize_url"]
    state = parse_qs(urlparse(url).query)["state"][0]
    code = google.consent(url)
    teams_member = teams.membership(session, owner.id, luran.id)
    session.delete(teams_member)
    session.commit()

    landed = landing(callback(web, state, code))

    assert landed[1]["calendar_error"] == "workspace_unavailable"
    assert connections(session, owner) == {}


# ---- the calendar state follows the workspace ----


def test_the_calendar_state_is_the_active_workspaces(web, session, owner, google, home, luran):
    connect_in(web, google, luran)

    mine = web.client.get("/api/calendar", headers={"X-Team-Id": str(luran.id)}).json()
    other = web.client.get("/api/calendar", headers={"X-Team-Id": str(home.id)}).json()

    assert (mine["connected"], mine["account_email"]) == (True, "grace@example.test")
    assert (other["connected"], other["state"]) == (False, "not_connected")
    assert other["account_email"] is None


# ---- disconnecting: the grant is reference counted ----


def test_disconnecting_one_workspace_keeps_the_grant_while_another_uses_the_account(
    web, session, owner, google, home, luran
):
    connect_in(web, google, home)
    connect_in(web, google, luran)

    response = disconnect_in(web, home)

    assert response.status_code == 200, response.text
    assert response.json() == {"status": "disconnected", "revoked": True}
    assert google.revoked == []  # luran's connection still stands on this account
    rows = connections(session, owner)
    assert rows[home.id].status == "disconnected" and rows[home.id].refresh_token_enc is None
    assert rows[luran.id].status == "connected" and rows[luran.id].refresh_token_enc is not None


def test_the_last_workspace_using_an_account_revokes_the_grant(
    web, session, owner, google, home, luran
):
    connect_in(web, google, home)
    connect_in(web, google, luran)
    last_token = google.refresh_tokens[-1]

    disconnect_in(web, home)
    response = disconnect_in(web, luran)

    assert response.json() == {"status": "disconnected", "revoked": True}
    assert google.revoked == [last_token]  # the token of the connection that was left


def test_a_different_account_in_the_other_workspace_does_not_hold_the_grant(
    web, session, owner, google, home, luran
):
    connect_in(web, google, home)
    home_token = google.refresh_tokens[-1]
    connect_in(web, google, luran, email="other@example.test", sub="g-other-2")

    disconnect_in(web, home)

    assert google.revoked == [home_token]


def test_a_dead_connection_of_the_same_account_does_not_hold_the_grant(
    web, session, owner, google, home, luran
):
    connect_in(web, google, home)
    connect_in(web, google, luran)
    rows = connections(session, owner)
    rows[luran.id].status = "disconnected"  # the person already disconnected luran
    rows[luran.id].account_subject = None
    session.commit()

    disconnect_in(web, home)

    assert len(google.revoked) == 1


def test_disconnect_takes_only_that_workspaces_events_joins_and_calendar_notices(
    web, session, owner, google, home, luran
):
    connect_in(web, google, home)
    connect_in(web, google, luran)
    rows = connections(session, owner)
    add_join(session, rows[home.id], owner, "scheduled", 1)
    add_join(session, rows[luran.id], owner, "scheduled", 2)
    for team, key in ((home, "a"), (luran, "b")):
        notice = add_notification(session, owner, "calendar.not_recorded", f"cal:{key}")
        notice.team_id = team.id
    session.commit()

    disconnect_in(web, home)

    session.expire_all()
    assert [e.connection_id for e in session.scalars(select(CalendarEvent))] == [rows[luran.id].id]
    assert [j.team_id for j in session.scalars(select(ScheduledJoin))] == [luran.id]
    assert [n.team_id for n in session.scalars(select(Notification))] == [luran.id]


def test_disconnect_in_a_workspace_with_nothing_connected_is_a_no_op(
    web, session, owner, google, home, luran
):
    connect_in(web, google, luran)

    response = disconnect_in(web, home)

    assert response.json() == {"status": "disconnected", "revoked": True}
    assert connections(session, owner)[luran.id].status == "connected"


def test_live_calendar_connection_is_per_workspace(session, owner, home, luran):
    session.add(
        CalendarConnection(
            user_id=owner.id,
            team_id=luran.id,
            provider="google",
            status="connected",
            granted_scopes=CALENDAR_SCOPE,
        )
    )
    session.commit()

    assert calendar_connect.live_calendar_connection(session, owner, luran.id).team_id == luran.id
    assert calendar_connect.live_calendar_connection(session, owner, home.id) is None


def test_a_workspace_must_be_named_to_find_a_connection(session, owner):
    with pytest.raises(TypeError):
        calendar_connect.live_calendar_connection(session, owner)  # type: ignore[call-arg]
