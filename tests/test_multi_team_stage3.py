"""Multi-team stage 3 (the frontend's stage): the one read-only flag the screens learn the switch
from, and the integrations refusal on a personal team.

Same cast as `tests/test_multi_team_leaks.py` (`world`): Ada owns team X and has a personal team;
Pat is in X and Y. The Slack address below is a deliberately invalid placeholder, so a request that
gets past the personal-team refusal ends in `400 slack_webhook_invalid` and nothing leaves the test.
"""

# ruff: noqa: F811  (fixtures are imported by name and used as arguments)
from sqlalchemy import select

from app import teams
from app.models import Team
from tests.test_api import (  # noqa: F401  (fixtures used by name)
    anon,
    anon_client,
    api,
    app,
    client,
    fake_openai,
    fake_vexa,
    owner,
    ready,
    template,
)
from tests.test_multi_team_api import ADA, call, get
from tests.test_multi_team_leaks import PAT, multi_team_on, world  # noqa: F401
from tests.test_team_visibility import sign_in

NOT_A_WEBHOOK = {"webhook_url": "https://example.test/not-slack"}


def personal_id(session, email) -> int:
    from app.models import User

    user = session.scalar(select(User).where(User.email == email))
    return session.scalar(
        select(Team.id).where(Team.owner_user_id == user.id, Team.is_personal.is_(True))
    )


# ---- the flag ----


def test_the_session_says_whether_the_switch_is_on_logged_in_and_out(app, session, world):
    ada = sign_in(app, ADA)
    assert ada.client.get("/api/session").json()["multi_team"] is True
    assert anon_state(app)["multi_team"] is True


def test_the_session_says_the_switch_is_off(app, session, world, monkeypatch):
    monkeypatch.setattr(teams, "multi_team_enabled", lambda: False)
    ada = sign_in(app, ADA)
    assert ada.client.get("/api/session").json()["multi_team"] is False
    assert anon_state(app)["multi_team"] is False


def anon_state(app):
    from fastapi.testclient import TestClient

    with TestClient(app) as bare:
        return bare.get("/api/session").json()


# ---- Slack and Linear are not for the personal team ----


def test_slack_is_refused_for_a_personal_team_while_the_switch_is_on(app, session, world):
    ada = sign_in(app, ADA)
    mine = personal_id(session, ADA)
    response = call(ada, "PUT", "/api/integrations/slack", team=mine, body=NOT_A_WEBHOOK)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "personal_team"
    assert "personal space" in response.json()["error"]["message"]


def test_linear_is_refused_for_a_personal_team_while_the_switch_is_on(app, session, world):
    ada = sign_in(app, ADA)
    mine = personal_id(session, ADA)
    for path, body in (
        ("/api/integrations/linear/teams", {"api_key": "k" * 20}),
        ("/api/integrations/linear", {"api_key": "k" * 20, "team_id": "t"}),
    ):
        response = call(
            ada, "PUT" if path.endswith("linear") else "POST", path, team=mine, body=body
        )
        assert (response.status_code, response.json()["error"]["code"]) == (409, "personal_team"), (
            path
        )


def test_the_default_team_of_a_person_with_two_teams_is_personal_and_is_refused_too(
    app, session, world
):
    """Pat is on X and Y, so a request with no header acts on the personal team."""
    pat = sign_in(app, PAT)
    response = call(pat, "PUT", "/api/integrations/slack", body=NOT_A_WEBHOOK)
    assert (response.status_code, response.json()["error"]["code"]) == (409, "personal_team")


def test_an_ordinary_team_still_gets_past_the_refusal(app, session, world):
    ada = sign_in(app, ADA)
    response = call(ada, "PUT", "/api/integrations/slack", team=world.x, body=NOT_A_WEBHOOK)
    assert (response.status_code, response.json()["error"]["code"]) == (
        400,
        "slack_webhook_invalid",
    )


def test_reading_the_state_of_a_personal_team_is_still_fine(app, session, world):
    ada = sign_in(app, ADA)
    mine = personal_id(session, ADA)
    assert get(ada, "/api/integrations/slack", team=mine).json()["connected"] is False


def test_the_team_of_one_may_connect_slack_while_the_switch_is_off(
    app, session, world, monkeypatch
):
    monkeypatch.setattr(teams, "multi_team_enabled", lambda: False)
    ada = sign_in(app, ADA)
    mine = personal_id(session, ADA)
    response = call(ada, "PUT", "/api/integrations/slack", team=mine, body=NOT_A_WEBHOOK)
    assert (response.status_code, response.json()["error"]["code"]) == (
        400,
        "slack_webhook_invalid",
    )
