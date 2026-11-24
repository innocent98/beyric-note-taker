"""A meeting recorded into a team's workspace carries its tasks, recurring card and share default
with that workspace, even when it is neither shared nor moved (workspaces design 3 and 12.1).

Cast (`tests/test_multi_team_leaks.py`): Pat is a plain member of X and Y and has a personal
workspace; Ada owns X. A meeting is "in T" when `workspace_team_id` is T.
"""

# ruff: noqa: F811  (fixtures are imported by name and used as arguments)
from sqlalchemy import select

from app import series as series_rules
from app import teams, visibility
from app.models import Meeting, Task
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
from tests.test_api_tasks import add_task
from tests.test_multi_team_leaks import PAT, world  # noqa: F401
from tests.test_team_visibility import sign_in


def in_scope(session, team, meeting) -> bool:
    found = session.scalars(
        select(Meeting.id).where(Meeting.id == meeting.id, visibility.series_scope_clause(team))
    ).all()
    return bool(found)


def private_in(session, template, world, team, **fields) -> Meeting:
    """Pat's meeting recorded into `team`'s workspace and shared with nobody (a paste with
    `X-Team-Id` by a non-manager, a calendar join from a connection filed there, an unshare)."""
    return ready(session, template, owner_user_id=world.pat.id, workspace_team_id=team.id, **fields)


# ---- tasks and recurring cards follow the workspace ----


def test_the_tasks_of_a_private_meeting_in_a_team_workspace_belong_to_that_team(
    session, template, world
):
    paste = private_in(session, template, world, world.x)
    join = private_in(session, template, world, world.y, source="calendar")
    shared = ready(session, template, owner_user_id=world.pat.id, shared_team_id=world.x.id)
    shared.shared_team_id = None  # an unshare: still filed in X
    session.commit()
    assert teams.team_for_meeting(session, paste).id == world.x.id
    assert teams.team_for_meeting(session, join).id == world.y.id
    assert teams.team_for_meeting(session, shared).id == world.x.id
    personal = teams.personal_team(session, world.pat.id)
    own = ready(session, template, owner_user_id=world.pat.id)
    assert teams.team_for_meeting(session, own).id == personal.id


def test_a_private_meeting_in_a_team_workspace_is_in_that_teams_recurring_scope(
    session, template, world
):
    personal = teams.personal_team(session, world.pat.id)
    paste = private_in(session, template, world, world.x)
    assert visibility.scope_team_of(session, paste) == world.x.id
    assert series_rules.scope_team_id(session, world.pat, paste, follow=True) == world.x.id
    assert in_scope(session, world.x, paste) and not in_scope(session, personal, paste)
    own = ready(session, template, owner_user_id=world.pat.id)
    assert visibility.scope_team_of(session, own) is None  # her personal scope
    assert in_scope(session, personal, own) and not in_scope(session, world.x, own)


def test_the_tasks_list_and_csv_of_a_workspace_show_a_private_meetings_tasks(
    app, session, template, world
):
    paste = private_in(session, template, world, world.x, title="DATA_X standup")
    team = teams.team_for_meeting(session, paste)  # what the extraction files its tasks in
    task = add_task(session, team, paste, title="DATA_X follow-up", assignee=world.pat)
    pat = sign_in(app, PAT)
    in_x = pat.client.get("/api/tasks", headers={"X-Team-Id": str(world.x.id)}).json()["tasks"]
    assert [t["id"] for t in in_x] == [task.id]
    personal = teams.personal_team(session, world.pat.id)
    in_p = pat.client.get("/api/tasks", headers={"X-Team-Id": str(personal.id)}).json()["tasks"]
    assert in_p == []
    csv_x = pat.client.get("/api/tasks/export", headers={"X-Team-Id": str(world.x.id)}).text
    csv_p = pat.client.get("/api/tasks/export", headers={"X-Team-Id": str(personal.id)}).text
    assert "DATA_X follow-up" in csv_x and "DATA_X follow-up" not in csv_p
    assert session.get(Task, task.id).team_id == world.x.id


# ---- the auto-share choice stays inside its own workspace ----


def choose(session, user, team):
    from app.models import UserSettings

    session.merge(UserSettings(user_id=user.id, auto_share_team_id=team.id))
    session.commit()


def test_a_chosen_auto_share_team_never_publishes_a_meeting_recorded_in_another_workspace(
    session, template, world
):
    choose(session, world.pat, world.y)
    recorded_in_x = private_in(session, template, world, world.x)
    teams.apply_share_default(session, recorded_in_x)
    assert recorded_in_x.shared_team_id is None  # not published to Y
    assert recorded_in_x.workspace_team_id == world.x.id  # and not moved to Y


def test_a_chosen_auto_share_team_applies_to_its_own_workspace_and_to_the_personal_one(
    session, template, world
):
    choose(session, world.pat, world.y)
    in_y = private_in(session, template, world, world.y)
    teams.apply_share_default(session, in_y)
    assert in_y.shared_team_id == world.y.id and in_y.workspace_team_id == world.y.id
    personal = ready(session, template, owner_user_id=world.pat.id)  # filed in her personal one
    teams.apply_share_default(session, personal)
    assert personal.shared_team_id == world.y.id  # her choice; sharing files it in Y
    assert personal.workspace_team_id == world.y.id


def test_the_workspaces_own_default_decides_when_the_chosen_team_is_elsewhere(
    session, template, world
):
    """Ada owns X and X shares by default; Ada chose Y: a meeting she recorded into X is shared
    to X (its own workspace's rule), not to Y."""
    from app.models import TeamMember

    session.add(TeamMember(team_id=world.y.id, user_id=world.ada.id, role="member"))
    world.x.share_new_meetings = True
    session.commit()
    choose(session, world.ada, world.y)
    meeting = ready(session, template, owner_user_id=world.ada.id, workspace_team_id=world.x.id)
    teams.apply_share_default(session, meeting)
    assert meeting.shared_team_id == world.x.id
