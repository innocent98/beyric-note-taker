"""What the Opus permission review of the Workspaces release found (docs/sop/2026-11-25-workspaces-
release.md): each fix has a test that fails without it.

1. "Keep private" on a meeting filed in a team workspace leaves its tasks with the teammates they
   were assigned to, and its proposed dates with the team.
2. A draft is made only from the workspace it is made in (and so shared only with that team).
3. Replacing a dead calendar connection with another account must not revoke a grant another
   workspace still uses.
4. Deleting a workspace runs the owner's calendar clean-up.
"""

# ruff: noqa: F811  (fixtures are imported by name and used as arguments)
from datetime import date

import pytest
from sqlalchemy import select

from app import meeting_teams, tasks, teams
from app.models import CalendarConnection, Milestone, Task
from app.recipes import service as recipe_service
from app.services import Refused
from tests.test_api_tasks import add_task
from tests.test_calendar_connect import kill  # noqa: F401
from tests.test_calendar_workspaces import (  # noqa: F401  (fixtures used by name)
    CSRF,
    capp,
    connect_in,
    connections,
    disconnect_in,
    fake_bot,
    fake_openai,
    google,
    home,
    luran,
    owner,
    settings,
    template,
    vault_key,
    web,
)
from tests.test_workspace_leaks import cast, world  # noqa: F401  (fixtures used by name)

# ---- 1. keep private ----


def visible_titles(session, user, meeting):
    session.expire_all()
    rows = session.scalars(
        select(Task).where(Task.meeting_id == meeting.id, tasks.visible(user))
    ).all()
    return sorted(t.title for t in rows if t.title in {"SECRETTASK", "PATSTASK"})


def test_keeping_a_meeting_private_takes_its_tasks_and_dates_away_from_the_team(session, cast):
    meeting = cast.meetings["x"]  # Pat's, shared to X, filed in X
    add_task(session, cast.x, meeting, title="SECRETTASK", assignee=cast.ada)
    add_task(session, cast.x, meeting, title="PATSTASK", assignee=cast.pat)
    proposal = Milestone(
        team_id=cast.x.id,
        name="SECRETDATE",
        due_date=date(2026, 12, 15),
        source_meeting_id=meeting.id,
    )
    session.add(proposal)
    session.commit()
    assert visible_titles(session, cast.ada, meeting) == ["PATSTASK", "SECRETTASK"]

    meeting_teams.unshare(session, meeting)
    session.commit()

    assert visible_titles(session, cast.ada, meeting) == []  # nothing assigned to her is left
    assert visible_titles(session, cast.pat, meeting) == ["PATSTASK", "SECRETTASK"]  # his own
    session.refresh(proposal)
    assert proposal.team_id == teams.personal_team(session, cast.pat.id).id
    assert meeting.workspace_team_id == cast.x.id  # still filed in the team's workspace


# ---- 2. a draft only from its own workspace ----


def test_a_draft_is_filed_where_its_meetings_live_and_never_mixes_workspaces(session, cast):
    for meeting in cast.meetings.values():
        meeting.status = "ready"
    session.commit()
    mine = {k: cast.meetings[k].id for k in "pxy"}

    def ask(ids, team, recipe="follow_up_email"):
        return recipe_service.request_draft(
            session, cast.pat, recipe_ref=recipe, meeting_ids=ids, team_id=team.id
        )

    # a meeting of Y opened while X is active is made in Y, never filed in X (spec 12.2)
    assert ask([mine["y"]], cast.x).team_id == cast.y.id
    # several meetings: those of the active workspace only, the rest left out and counted
    both = ask([mine["x"], mine["y"]], cast.x, "status_report")
    assert both.team_id == cast.x.id and both.left_out_count == 1
    # meetings of two workspaces and none of the active one: nothing is made
    with pytest.raises(Refused) as refused:
        ask([mine["p"], mine["y"]], cast.x, "status_report")
    assert refused.value.status == 404


# ---- 3. a different account must not take another workspace's grant ----


def test_replacing_a_dead_connection_does_not_revoke_a_grant_another_workspace_uses(
    web, session, owner, google, home, luran
):
    connect_in(web, google, home)
    connect_in(web, google, luran)  # the same account stands in both workspaces
    rows = connections(session, owner)
    rows[home.id].status, rows[home.id].needs_reconnect_reason = "needs_reconnect", "revoked"
    session.commit()

    connect_in(web, google, home, email="other@example.test", sub="g-other-2")

    assert google.revoked == []  # luran's connection still stands on the first account
    rows = connections(session, owner)
    assert rows[home.id].account_subject == "g-other-2"
    assert rows[luran.id].status == "connected" and rows[luran.id].refresh_token_enc is not None


def test_replacing_a_dead_connection_revokes_the_old_grant_when_no_other_workspace_uses_it(
    web, session, owner, google, home, luran
):
    connect_in(web, google, home)
    old = google.refresh_tokens[-1]
    rows = connections(session, owner)
    rows[home.id].status, rows[home.id].needs_reconnect_reason = "needs_reconnect", "revoked"
    session.commit()

    connect_in(web, google, home, email="other@example.test", sub="g-other-2")

    assert google.revoked == [old]


# ---- 4. deleting a workspace cleans up the owner's calendar ----


def test_deleting_a_workspace_revokes_the_owners_grant_there(
    web, session, owner, google, home, luran
):
    connect_in(web, google, luran)
    token = google.refresh_tokens[-1]

    response = web.client.delete(
        f"/api/teams/{luran.id}", headers={CSRF: web.csrf, "X-Team-Id": str(luran.id)}
    )

    assert response.status_code == 204, response.text
    assert google.revoked == [token]
    assert session.scalars(select(CalendarConnection).filter_by(team_id=luran.id)).all() == []


def test_deleting_a_workspace_keeps_the_grant_another_workspace_uses(
    web, session, owner, google, home, luran
):
    connect_in(web, google, home)
    connect_in(web, google, luran)

    response = web.client.delete(
        f"/api/teams/{luran.id}", headers={CSRF: web.csrf, "X-Team-Id": str(luran.id)}
    )

    assert response.status_code == 204, response.text
    assert google.revoked == []
    assert connections(session, owner)[home.id].status == "connected"
    assert disconnect_in(web, home).status_code == 200
