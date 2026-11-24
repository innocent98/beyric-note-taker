"""The active workspace is required context (workspaces design, section 7; plan Task 2).

Cast (from `tests/test_multi_team_leaks.py`): Ada owns X and has a personal workspace; Pat is in X
and Y and has a personal one; Quinn owns Y; Zed owns Z. Ada's `in_x` is shared to X, her `private`
is nobody's; Zed's `in_z` is shared to Z. A meeting is born in a workspace: the team that owns it,
else the one it is shared to, else its recorder's personal one.
"""

# ruff: noqa: F811  (fixtures are imported by name and used as arguments)
import pytest

from app import teams, visibility, workspace_context
from app.config import Settings
from app.models import Meeting
from app.services import Refused
from tests.test_api import (  # noqa: F401  (fixtures used by name)
    anon,
    anon_client,
    api,
    app,
    client,
    error_of,
    fake_bot,
    fake_openai,
    owner,
    ready,
    template,
)
from tests.test_multi_team_leaks import PAT, QUINN, ZED, new_team, world  # noqa: F401
from tests.test_team_visibility import sign_in

ADA = "owner@example.test"


def ids(handle, team=None):
    headers = {"X-Team-Id": str(team.id)} if team is not None else {}
    response = handle.client.get("/api/meetings", headers=headers)
    assert response.status_code == 200, response.text
    return sorted(m["id"] for m in response.json()["meetings"])


# ---- the switch is gone ----


def test_there_is_no_multi_team_setting_any_more():
    assert not hasattr(Settings(_env_file=None), "multi_team_enabled")
    assert not hasattr(teams, "multi_team_enabled")


def test_the_session_still_answers_without_the_flag(app, session, world):
    pat = sign_in(app, PAT)
    body = pat.client.get("/api/session").json()
    assert body["authenticated"] is True and body["multi_team"] is True
    assert body["badges"] is not None


# ---- require(): the header, else the personal workspace ----


def test_the_header_names_a_team_the_person_is_in(session, world):
    got = workspace_context.require(session, world.pat, world.x.id)
    assert got.id == world.x.id
    assert workspace_context.require(session, world.pat, world.y.id).id == world.y.id


def test_a_team_the_person_is_not_in_is_404_not_found(session, world):
    with pytest.raises(Refused) as caught:
        workspace_context.require(session, world.pat, world.z.id)
    assert (caught.value.status, caught.value.code) == (404, "not_found")
    with pytest.raises(Refused):  # a team that does not exist is the same answer
        workspace_context.require(session, world.pat, 999999)


def test_no_header_is_the_personal_workspace_even_for_a_person_in_one_team(session, world):
    # Ada is in exactly one ordinary team (X): the old "default team" would have been X.
    personal = teams.personal_team(session, world.ada.id)
    assert workspace_context.require(session, world.ada, None).id == personal.id
    assert workspace_context.require(session, world.pat, None).id == (
        teams.personal_team(session, world.pat.id).id
    )


def test_a_person_with_no_personal_team_gets_one_made(session, world):
    from app.models import Team, TeamMember
    from tests.test_web import make_user

    bola = make_user(session, "bola@example.test")
    assert teams.personal_team(session, bola.id) is None
    made = workspace_context.require(session, bola, None)
    session.commit()
    assert made.is_personal and made.owner_user_id == bola.id
    assert session.query(TeamMember).filter_by(team_id=made.id, user_id=bola.id).count() == 1
    assert session.query(Team).filter_by(owner_user_id=bola.id, is_personal=True).count() == 1


def test_the_dependency_is_the_same_rule_over_http(app, session, world):
    pat = sign_in(app, PAT)
    assert ids(pat, world.x) == [world.in_x.id]
    error = error_of(
        pat.client.get("/api/meetings", headers={"X-Team-Id": str(world.z.id)}), 404, "not_found"
    )
    assert error["message"] == "No such team."


# ---- in_workspace(): readable AND filed in the workspace ----


def test_a_meeting_is_born_in_its_owning_team_else_its_shared_team_else_the_personal_one(
    session, world, template
):
    personal = teams.personal_team(session, world.ada.id)
    assert world.private.workspace_team_id == personal.id
    assert world.in_x.workspace_team_id == world.x.id
    moved = ready(session, template, owner_team_id=world.x.id)
    assert moved.workspace_team_id == world.x.id
    both = ready(session, template, shared_team_id=world.y.id, owner_user_id=world.pat.id)
    assert both.workspace_team_id == world.y.id


def test_the_personal_workspace_lists_only_the_meetings_filed_there(app, session, world):
    pat_personal = teams.personal_team(session, world.pat.id)
    pat = sign_in(app, PAT)
    # Pat reads Ada's shared meeting through X, but it is not in Pat's personal workspace
    assert world.in_x.id not in ids(pat)
    assert ids(pat, pat_personal) == []
    assert ids(pat, world.x) == [world.in_x.id]
    ada = sign_in(app, ADA)
    assert ids(ada) == [world.private.id]  # no header: the personal workspace, not "everything"
    assert ids(ada, world.x) == [world.in_x.id]


def test_a_member_sees_a_teammates_shared_meeting_in_the_team_workspace_not_in_personal(
    session, world
):
    for_pat_x = visibility.in_workspace(world.pat, world.x)
    for_pat_personal = visibility.in_workspace(
        world.pat, teams.personal_team(session, world.pat.id)
    )
    from sqlalchemy import select

    in_x = select(Meeting.id).where(for_pat_x).order_by(Meeting.id)
    in_personal = select(Meeting.id).where(for_pat_personal)
    assert session.scalars(in_x).all() == [world.in_x.id]
    assert session.scalars(in_personal).all() == []


def test_the_workspace_never_widens_who_may_read(session, world, template):
    """A teammate's UNSHARED meeting filed in the team's workspace is not listed for anyone else:
    the workspace is the container, `can_read_clause` is still who may read."""
    from sqlalchemy import select

    mine = ready(session, template, owner_user_id=world.ada.id, shared_team_id=world.x.id)
    mine.shared_team_id = None  # "keep private": still filed in X, readable by Ada alone
    session.commit()
    assert mine.workspace_team_id == world.x.id
    pat_sees = select(Meeting.id).where(visibility.in_workspace(world.pat, world.x))
    ada_sees = select(Meeting.id).where(visibility.in_workspace(world.ada, world.x))
    assert mine.id not in session.scalars(pat_sees).all()
    assert mine.id in session.scalars(ada_sees).all()


def test_a_meeting_opened_by_id_from_another_workspace_is_still_readable(app, session, world):
    """The header never turns a readable meeting into a 404 (spec 12.2): the web switches."""
    pat = sign_in(app, PAT)
    response = pat.client.get(
        f"/api/meetings/{world.in_x.id}", headers={"X-Team-Id": str(world.y.id)}
    )
    assert response.status_code == 200
    assert response.json()["id"] == world.in_x.id


def test_removing_the_flag_does_not_make_a_stranger_able_to_read(app, session, world):
    quinn = sign_in(app, QUINN)
    assert ids(quinn, teams.personal_team(session, world.quinn.id)) == []
    error_of(quinn.client.get(f"/api/meetings/{world.in_x.id}"), 404, "not_found")
    zed = sign_in(app, ZED)
    assert ids(zed, world.z) == [world.in_z.id]
