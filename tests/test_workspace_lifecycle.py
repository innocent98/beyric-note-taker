"""Workspaces, Task 11: deleting a workspace, leaving or being removed, and class workspaces.

An owner deletes a workspace only when nobody else is in it; its tasks, projects, series and
integrations go with it, its meetings go back to their recorders' personal workspaces (private,
their tasks re-filed there) or are deleted when the recorder's account is gone. A member who leaves
or is removed loses access at once and what they recorded and kept goes home with them; a meeting
MOVED to the workspace stays with it. (The calendar connection and scheduled joins of a leaver are
W3's: `tests/test_calendar_workspaces.py`.) A class is a team with `kind = class`: wording only,
and the share default on. Real Postgres."""

# ruff: noqa: F811  (fixtures are imported by name and used as arguments)
import pytest
from sqlalchemy import select

from app import teams
from app.models import (
    Integration,
    Meeting,
    Project,
    Series,
    Task,
    Team,
    TeamMember,
    Template,
)
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
from tests.test_multi_team_api import ADA, call, get
from tests.test_multi_team_leaks import PAT, QUINN, ZED, new_team, world  # noqa: F401
from tests.test_team_visibility import sign_in
from tests.test_web import make_user
from tests.workspace_plans import subscribe


def fill(session, team, owner) -> dict:
    """What a workspace holds besides meetings: a project, a series, an integration, a task."""
    project = Project(team_id=team.id, name="P", color="#112233")
    series = Series(team_id=team.id, title="Weekly")
    integration = Integration(team_id=team.id, kind="slack_webhook", config_enc=b"x", key_version=1)
    session.add_all([project, series, integration])
    session.commit()
    task = add_task(session, team, title="Typed in", created_by_user_id=owner.id)
    return {
        "project": project.id,
        "series": series.id,
        "integration": integration.id,
        "task": task.id,
    }


def gone(session, email="gone@example.test"):
    """A person whose account was deleted (the hollow row a kept meeting points at)."""
    user = make_user(session, email)
    user.status = "deleted"
    session.commit()
    return user


# ---- deleting a workspace ----


def test_an_owner_alone_deletes_the_workspace_and_what_it_held(app, session, world):
    held = fill(session, world.z, world.zed)
    z_id = world.z.id
    zed = sign_in(app, ZED)
    assert call(zed, "DELETE", f"/api/teams/{z_id}").status_code == 204
    session.expire_all()
    assert session.get(Team, z_id) is None
    assert session.scalars(select(TeamMember).where(TeamMember.team_id == z_id)).all() == []
    for model, key in ((Project, "project"), (Series, "series"), (Integration, "integration")):
        assert session.get(model, held[key]) is None
    assert session.get(Task, held["task"]) is None
    assert session.get(Team, teams.personal_team(session, world.zed.id).id) is not None
    # the workspace is no longer anyone's to name
    error_of(get(zed, "/api/team", team=z_id), 404, "not_found")
    assert z_id not in {t["id"] for t in get(zed, "/api/teams").json()["teams"]}


def test_a_workspace_with_another_member_is_not_deleted(app, session, world):
    ada = sign_in(app, ADA)  # X: Ada owns it, Pat is in it
    body = error_of(call(ada, "DELETE", f"/api/teams/{world.x.id}"), 409, "team_has_members")
    assert "only member" in body["message"]
    assert session.get(Team, world.x.id) is not None
    assert world.in_x.id == session.get(Meeting, world.in_x.id).id  # nothing touched


def test_only_the_owner_deletes_and_a_stranger_cannot_tell_it_exists(app, session, world):
    pat = sign_in(app, PAT)  # a plain member of X
    error_of(call(pat, "DELETE", f"/api/teams/{world.x.id}"), 403, "team_forbidden")
    quinn = sign_in(app, QUINN)  # not in X
    error_of(call(quinn, "DELETE", f"/api/teams/{world.x.id}"), 404, "not_found")
    error_of(call(quinn, "DELETE", "/api/teams/999999"), 404, "not_found")
    assert session.get(Team, world.x.id) is not None


def test_an_admin_cannot_delete_the_workspace(app, session, world):
    member = session.scalars(
        select(TeamMember).where(
            TeamMember.team_id == world.x.id, TeamMember.user_id == world.pat.id
        )
    ).one()
    member.role = "admin"
    session.commit()
    error_of(call(sign_in(app, PAT), "DELETE", f"/api/teams/{world.x.id}"), 403, "team_forbidden")


def test_the_personal_workspace_cannot_be_deleted(app, session, world):
    personal = teams.personal_team(session, world.zed.id)
    error_of(call(sign_in(app, ZED), "DELETE", f"/api/teams/{personal.id}"), 409, "personal_team")
    assert session.get(Team, personal.id) is not None


def test_meetings_with_a_living_recorder_go_home_private_and_their_tasks_with_them(
    app, session, world, template
):
    """Review focus 5: a former member's meeting is never deleted while its recorder exists."""
    moved = ready(  # Pat recorded it, moved it to Z, and has since left Z
        session, template, owner_user_id=world.pat.id, owner_team_id=world.z.id
    )
    kept = world.in_z  # Zed's own, shared to Z
    from_moved = add_task(session, world.z, moved, title="From Pat's call")
    from_kept = add_task(session, world.z, kept, title="From Zed's call")
    assert moved.workspace_team_id == world.z.id and kept.workspace_team_id == world.z.id
    assert call(sign_in(app, ZED), "DELETE", f"/api/teams/{world.z.id}").status_code == 204
    session.expire_all()
    pat_home = teams.personal_team(session, world.pat.id)
    zed_home = teams.personal_team(session, world.zed.id)
    moved, kept = session.get(Meeting, moved.id), session.get(Meeting, kept.id)
    assert (moved.workspace_team_id, moved.owner_team_id, moved.shared_team_id) == (
        pat_home.id,
        None,
        None,
    )
    assert (kept.workspace_team_id, kept.owner_team_id, kept.shared_team_id) == (
        zed_home.id,
        None,
        None,
    )
    assert session.get(Task, from_moved.id).team_id == pat_home.id
    assert session.get(Task, from_kept.id).team_id == zed_home.id
    # and Pat reads her meeting in her own personal workspace now
    pat = sign_in(app, PAT)
    listed = get(pat, "/api/meetings", team=pat_home.id).json()["meetings"]
    assert moved.id in {m["id"] for m in listed}


def test_a_meeting_whose_recorder_is_gone_is_deleted_with_the_workspace(
    app, session, world, template
):
    hollow = gone(session)
    orphan = ready(session, template, owner_user_id=hollow.id, owner_team_id=world.z.id)
    survivor = ready(session, template, owner_user_id=world.pat.id, owner_team_id=world.z.id)
    orphan_id, survivor_id = orphan.id, survivor.id
    assert call(sign_in(app, ZED), "DELETE", f"/api/teams/{world.z.id}").status_code == 204
    session.expire_all()
    assert session.get(Meeting, orphan_id) is None
    assert session.get(Meeting, survivor_id) is not None


def test_a_member_who_joins_mid_deletion_stops_the_rest_nothing_more_is_destroyed(
    app, session, world, template, monkeypatch
):
    """Each destructive step is authorised under the team's lock as it happens."""
    from app import services

    hollow = gone(session)
    first = ready(session, template, owner_user_id=hollow.id, owner_team_id=world.z.id)
    second = ready(session, template, owner_user_id=hollow.id, owner_team_id=world.z.id)
    first_id, second_id, z_id, pat_id = first.id, second.id, world.z.id, world.pat.id
    real = services.delete_meeting

    def delete_then_someone_joins(session_, provider, meeting):
        real(session_, provider, meeting)
        session_.add(TeamMember(team_id=z_id, user_id=pat_id, role="member"))  # an invite accepted
        session_.commit()

    monkeypatch.setattr(services, "delete_meeting", delete_then_someone_joins)
    error_of(call(sign_in(app, ZED), "DELETE", f"/api/teams/{z_id}"), 409, "team_has_members")
    session.expire_all()
    assert session.get(Meeting, first_id) is None  # deleted while the owner was alone
    assert session.get(Meeting, second_id) is not None  # kept: Pat is in the team now
    assert session.get(Team, z_id) is not None


def test_a_template_a_kept_meeting_was_written_with_outlives_the_workspace(
    app, session, world, template
):
    """The meeting names its template: the workspace's row is not deleted under it."""
    mine = Template(
        owner_user_id=world.zed.id, team_id=world.z.id, name="Class notes", instructions="x"
    )
    unused = Template(
        owner_user_id=world.zed.id, team_id=world.z.id, name="Spare", instructions="x"
    )
    session.add_all([mine, unused])
    session.commit()
    meeting = ready(session, template, owner_user_id=world.zed.id, owner_team_id=None)
    meeting.template_id = mine.id
    session.commit()
    mine_id, unused_id, meeting_id = mine.id, unused.id, meeting.id
    assert call(sign_in(app, ZED), "DELETE", f"/api/teams/{world.z.id}").status_code == 204
    session.expire_all()
    assert session.get(Template, mine_id) is not None
    assert session.get(Template, unused_id) is None
    assert session.get(Meeting, meeting_id).template_id == mine_id


def test_deleting_a_workspace_never_needs_a_plan(app, session, world):
    """A lapsed owner (Free now) still deletes what they own: the limit only blocks making more."""
    subscribe(session, world.zed, "plan_20h", status="lapsed")
    assert call(sign_in(app, ZED), "DELETE", f"/api/teams/{world.z.id}").status_code == 204


# ---- leaving and being removed ----


def test_a_removed_member_loses_access_at_once(app, session, world):
    ada, pat = sign_in(app, ADA), sign_in(app, PAT)
    assert get(pat, "/api/team", team=world.x.id).status_code == 200
    assert world.in_x.id in {
        m["id"] for m in get(pat, "/api/meetings", team=world.x.id).json()["meetings"]
    }
    assert (
        call(ada, "DELETE", f"/api/team/members/{world.pat.id}", team=world.x.id).status_code == 204
    )
    error_of(get(pat, "/api/team", team=world.x.id), 404, "not_found")
    error_of(get(pat, "/api/meetings", team=world.x.id), 404, "not_found")
    assert get(pat, f"/api/meetings/{world.in_x.id}").status_code == 404  # no longer readable
    assert world.x.id not in {t["id"] for t in get(pat, "/api/teams").json()["teams"]}


def test_what_a_leaver_recorded_goes_home_and_a_meeting_moved_to_the_team_stays(
    app, session, world, template
):
    recorded = ready(  # Pat recorded into X and shared it
        session, template, owner_user_id=world.pat.id, shared_team_id=world.x.id
    )
    moved = ready(session, template, owner_user_id=world.pat.id, owner_team_id=world.x.id)
    pat = sign_in(app, PAT)
    assert (
        call(pat, "DELETE", f"/api/team/members/{world.pat.id}", team=world.x.id).status_code == 204
    )
    session.expire_all()
    home = teams.personal_team(session, world.pat.id)
    recorded, moved = session.get(Meeting, recorded.id), session.get(Meeting, moved.id)
    assert (recorded.workspace_team_id, recorded.shared_team_id) == (home.id, None)
    assert (moved.workspace_team_id, moved.owner_team_id) == (world.x.id, world.x.id)
    ada = sign_in(app, ADA)  # the team still reads the meeting that belongs to it
    assert get(ada, f"/api/meetings/{moved.id}").status_code == 200
    assert get(ada, f"/api/meetings/{recorded.id}").status_code == 404


def test_a_workspace_stays_when_its_member_leaves_and_the_owner_is_left_alone(app, session, world):
    pat = sign_in(app, PAT)
    assert (
        call(pat, "DELETE", f"/api/team/members/{world.pat.id}", team=world.x.id).status_code == 204
    )
    ada = sign_in(app, ADA)
    assert get(ada, "/api/team", team=world.x.id).json()["member_count"] == 1
    assert call(ada, "DELETE", f"/api/teams/{world.x.id}").status_code == 204  # now she may


def test_the_personal_workspace_cannot_be_left(app, session, world):
    personal = teams.personal_team(session, world.zed.id)
    zed = sign_in(app, ZED)
    error_of(
        call(zed, "DELETE", f"/api/team/members/{world.zed.id}", team=personal.id),
        409,
        "personal_team",
    )
    assert teams.membership(session, world.zed.id, personal.id) is not None


def test_the_owner_who_still_has_members_is_told_how_to_leave(app, session, world):
    ada = sign_in(app, ADA)
    body = error_of(
        call(ada, "DELETE", f"/api/team/members/{world.ada.id}", team=world.x.id),
        409,
        "owner_cannot_leave",
    )
    assert "delete" in body["message"]


# ---- classes ----


def test_a_class_is_a_team_that_shares_by_default_and_says_so(app, session, world):
    subscribe(session, world.pat, "plan_20h")
    pat = sign_in(app, PAT)
    made = call(pat, "POST", "/api/teams", body={"name": "Maths 101", "kind": "class"})
    assert made.status_code == 201
    body = made.json()
    assert body["kind"] == "class" and body["role"] == "owner"
    team = session.get(Team, body["id"])
    assert team.kind == "class" and team.share_new_meetings is True
    mine = {t["id"]: t for t in get(pat, "/api/teams").json()["teams"]}
    assert mine[team.id]["kind"] == "class"
    assert get(pat, "/api/team", team=team.id).json()["kind"] == "class"


def test_an_ordinary_team_stays_a_team_that_does_not_share_by_default(app, session, world):
    subscribe(session, world.pat, "plan_20h")
    body = call(sign_in(app, PAT), "POST", "/api/teams", body={"name": "Crew"}).json()
    team = session.get(Team, body["id"])
    assert body["kind"] == "team" and team.kind == "team" and team.share_new_meetings is False


def test_a_class_counts_toward_the_owned_team_limit(app, session, world):
    pat = sign_in(app, PAT)  # Free
    error_of(
        call(pat, "POST", "/api/teams", body={"name": "Maths", "kind": "class"}), 409, "team_limit"
    )


@pytest.mark.parametrize("kind", ["school", "", 3, None])
def test_an_unknown_kind_is_refused(app, session, world, kind):
    subscribe(session, world.pat, "plan_20h")
    pat = sign_in(app, PAT)
    assert call(pat, "POST", "/api/teams", body={"name": "X", "kind": kind}).status_code == 422


def test_a_class_meeting_of_the_lecturer_is_shared_with_the_class_when_its_notes_are_written(
    session, world, template
):
    subscribe(session, world.pat, "plan_20h")
    klass = teams.create_owned_team(session, world.pat, "Maths", kind="class")
    session.commit()
    meeting = ready(session, template, owner_user_id=world.pat.id, workspace_team_id=klass.id)
    teams.apply_share_default(session, meeting)
    assert meeting.shared_team_id == klass.id
