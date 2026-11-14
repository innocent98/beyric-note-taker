"""Multi-team stage 2b, the permission guard of the explicit team (review of PR #184).

Cast (`tests/test_multi_team_leaks.py`'s `world`): Quinn owns team Y and Pat is a plain member.
Pat also OWNS a team of their own, A (`new_team`). So Pat holds two roles at once: manager in A,
member in Y. Every test here is about the role and the ids of the team NAMED in `X-Team-Id`, never
of another team Pat belongs to. The switch is on unless a test says otherwise.
"""

# ruff: noqa: F811  (fixtures are imported by name and used as arguments)
import threading
import time
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import teams
from app.models import (
    Meeting,
    Milestone,
    Project,
    Series,
    Sprint,
    Team,
    TeamInvite,
    TeamMember,
    User,
)
from tests.test_api import (  # noqa: F401  (fixtures used by name)
    anon,
    anon_client,
    api,
    app,
    client,
    error_of,
    fake_openai,
    fake_vexa,
    owner,
    ready,
    template,
)
from tests.test_multi_team_api import call, get
from tests.test_multi_team_leaks import PAT, QUINN, multi_team_on, new_team, world  # noqa: F401
from tests.test_series import meeting_in
from tests.test_team_visibility import sign_in
from tests.test_web import make_user

SLACK = "https://hooks.slack.com/services/T000/B000/placeholder"


@pytest.fixture
def cast(session, world):
    """Pat owns A and is a plain member of Y; Y has a project, a milestone, a sprint, an open
    invitation and a member (Bea) that A has not."""
    world.a = new_team(session, world.pat, "A")
    world.bea = make_user(session, "bea@example.test")
    teams.ensure_personal_team(session, world.bea)
    session.add(TeamMember(team_id=world.y.id, user_id=world.bea.id, role="member"))
    world.proj_y = Project(team_id=world.y.id, name="YP", color="#000000", keywords=[])
    session.add(world.proj_y)
    session.flush()
    world.mile_y = Milestone(
        team_id=world.y.id,
        name="Launch Y",
        due_date=date(2030, 1, 1),
        project_id=world.proj_y.id,
        confirmed_at=datetime.now(UTC),
    )
    world.invite_y = TeamInvite(
        team_id=world.y.id,
        email="invitee@example.test",
        role="member",
        invited_by=world.quinn.id,
        token_hash="y-token",
        expires_at=datetime.now(UTC) + timedelta(days=7),
        created_at=datetime.now(UTC),
    )
    session.add_all([world.mile_y, world.invite_y])
    session.commit()
    return world


def y_sprint(app, session, world) -> Sprint:
    """Quinn opens Y's sprints (the first call makes them); the sprint is Y's."""
    assert get(sign_in(app, QUINN), "/api/sprints", world.y).status_code == 200
    return session.scalars(select(Sprint).where(Sprint.team_id == world.y.id)).first()


# ---- role confusion: manager in A, plain member in Y, Y named ----


def test_a_manager_of_a_sends_the_header_of_y_where_they_are_a_member_and_every_write_is_refused(
    app, session, cast
):
    world = cast
    sprint = y_sprint(app, session, world)
    pat = sign_in(app, PAT)
    y = world.y
    attempts = [
        ("POST", "/api/team/invites", {"invitees": [{"email": "n@example.test"}]}),
        ("PATCH", "/api/team", {"name": "Hijacked"}),
        ("PATCH", f"/api/team/members/{world.bea.id}", {"aliases": ["x"]}),
        ("DELETE", f"/api/team/members/{world.bea.id}", None),
        ("POST", f"/api/team/invites/{world.invite_y.id}/resend", None),
        ("DELETE", f"/api/team/invites/{world.invite_y.id}", None),
        ("PUT", "/api/integrations/slack", {"webhook_url": SLACK}),
        ("DELETE", "/api/integrations/slack", None),
        ("DELETE", "/api/integrations/linear", None),
        ("POST", "/api/projects", {"name": "Mine now"}),
        ("PATCH", f"/api/projects/{world.proj_y.id}", {"name": "Renamed"}),
        ("DELETE", f"/api/projects/{world.proj_y.id}", None),
        ("POST", "/api/milestones", {"name": "M", "due_date": "2030-02-02"}),
        ("PATCH", f"/api/milestones/{world.mile_y.id}", {"name": "Renamed"}),
        ("DELETE", f"/api/milestones/{world.mile_y.id}", None),
        ("POST", f"/api/sprints/{sprint.id}/close", None),
    ]
    for method, path, body in attempts:
        response = call(pat, method, path, y, body)
        assert response.status_code == 403, (method, path, response.status_code, response.text)
        assert response.json()["error"]["code"] == "team_forbidden", (method, path)
    # nothing changed
    session.expire_all()
    assert session.get(Team, y.id).name == "Y"
    assert session.get(Project, world.proj_y.id).name == "YP"
    assert session.get(Milestone, world.mile_y.id).name == "Launch Y"
    assert session.get(TeamInvite, world.invite_y.id).revoked_at is None
    assert session.get(Sprint, sprint.id).closed_at is None
    assert session.scalars(select(Project).where(Project.name == "Mine now")).all() == []


def test_the_same_writes_with_the_header_of_a_succeed_where_pat_is_the_owner(app, session, cast):
    """The other half: the role really is read from the team named."""
    world = cast
    pat = sign_in(app, PAT)
    assert call(pat, "PATCH", "/api/team", world.a, {"name": "A2"}).status_code == 200
    assert call(pat, "POST", "/api/projects", world.a, {"name": "AP"}).status_code == 201
    invited = call(
        pat, "POST", "/api/team/invites", world.a, {"invitees": [{"email": "n@example.test"}]}
    )
    assert invited.status_code == 200


# ---- ids of another team with a valid header ----


def test_ids_of_team_y_are_404_under_a_header_of_a_team_the_caller_manages(app, session, cast):
    world = cast
    sprint = y_sprint(app, session, world)
    pat = sign_in(app, PAT)
    a = world.a
    attempts = [
        ("GET", f"/api/projects/{world.proj_y.id}", None),
        ("PATCH", f"/api/projects/{world.proj_y.id}", {"name": "Renamed"}),
        ("DELETE", f"/api/projects/{world.proj_y.id}", None),
        ("PATCH", f"/api/milestones/{world.mile_y.id}", {"name": "Renamed"}),
        ("DELETE", f"/api/milestones/{world.mile_y.id}", None),
        ("POST", f"/api/sprints/{sprint.id}/close", None),
        ("POST", f"/api/team/invites/{world.invite_y.id}/resend", None),
        ("DELETE", f"/api/team/invites/{world.invite_y.id}", None),
        ("PATCH", f"/api/team/members/{world.bea.id}", {"aliases": ["x"]}),
        ("DELETE", f"/api/team/members/{world.bea.id}", None),
    ]
    for method, path, body in attempts:
        error_of(call(pat, method, path, a, body), 404, "not_found")
    session.expire_all()
    assert session.get(Project, world.proj_y.id) is not None
    assert session.get(Milestone, world.mile_y.id) is not None
    assert session.get(TeamInvite, world.invite_y.id).revoked_at is None
    assert session.get(Sprint, sprint.id).closed_at is None
    assert (
        session.scalars(
            select(TeamMember).where(
                TeamMember.team_id == world.y.id, TeamMember.user_id == world.bea.id
            )
        ).first()
        is not None
    )


def test_a_task_made_in_a_names_no_project_sprint_or_person_of_y(app, session, cast):
    world = cast
    sprint = y_sprint(app, session, world)
    pat = sign_in(app, PAT)
    for body in (
        {"title": "T", "project_id": world.proj_y.id},
        {"title": "T", "sprint_id": sprint.id},
        {"title": "T", "assignee_user_id": world.bea.id},
    ):
        response = call(pat, "POST", "/api/tasks", world.a, body)
        assert response.status_code == 422, (body, response.status_code, response.text)
    assert call(pat, "POST", "/api/tasks", world.a, {"title": "Fine"}).status_code == 201


# ---- series: manager of A, member of Y ----


def test_a_manager_of_a_who_is_only_a_member_of_y_cannot_rename_or_link_ys_series(
    app, session, template, cast
):
    world = cast
    weekly = Series(team_id=world.y.id, owner_user_id=world.quinn.id, title="Y sync")
    session.add(weekly)
    session.commit()
    meeting_in(session, template, weekly, user=world.quinn).shared_team_id = world.y.id
    session.commit()
    pat = sign_in(app, PAT)
    assert pat.get(f"/api/series/{weekly.id}").status_code == 200  # a member reads it
    error_of(
        pat.client.patch(
            f"/api/series/{weekly.id}",
            json={"title": "Hijacked"},
            headers={"X-CSRF-Token": pat.csrf},
        ),
        403,
        "team_forbidden",
    )
    error_of(
        call(pat, "PUT", f"/api/series/{weekly.id}/project", None, {"project_id": None}),
        403,
        "team_forbidden",
    )
    session.expire_all()
    assert session.get(Series, weekly.id).title == "Y sync"


# ---- header edge cases ----


def test_an_empty_team_header_is_a_validation_error(app, session, cast):
    pat = sign_in(app, PAT)
    for path in ("/api/team", "/api/tasks", "/api/series", "/api/projects"):
        response = pat.client.get(path, headers={"X-Team-Id": ""})
        assert response.status_code == 422, (path, response.status_code)


def test_a_write_with_no_header_lands_in_the_default_team_which_is_personal_for_two_teams(
    app, session, cast
):
    """Pat is on X, Y and A (three ordinary teams): no header means the personal team, never a
    guess between them."""
    world = cast
    pat = sign_in(app, PAT)
    home = teams.personal_team(session, world.pat.id)
    task = call(pat, "POST", "/api/tasks", None, {"title": "No header"})
    assert task.status_code == 201
    series = call(pat, "POST", "/api/series", None, {"title": "No header series"})
    assert series.status_code == 201
    from app.models import Task

    assert session.get(Task, task.json()["id"]).team_id == home.id
    assert session.get(Series, series.json()["id"]).team_id == home.id


# ---- attaching a meeting to a series: the series' team must be the meeting's ----


def test_a_meeting_cannot_join_the_series_of_a_team_it_does_not_belong_to(
    app, session, template, cast
):
    world = cast
    # both made by Pat (a series is seen by its maker), so only the team rule can refuse
    series_y = Series(team_id=world.y.id, owner_user_id=world.pat.id, title="Y sync")
    series_x = Series(team_id=world.x.id, owner_user_id=world.pat.id, title="X sync")
    session.add_all([series_y, series_x])
    session.commit()
    private = ready(session, template, owner_user_id=world.pat.id)
    in_x = ready(session, template, owner_user_id=world.pat.id, shared_team_id=world.x.id)
    pat = sign_in(app, PAT)

    def attach(meeting, series):
        return pat.put(f"/api/meetings/{meeting.id}/series", {"series_id": series.id})

    error_of(attach(private, series_y), 404, "not_found")  # private: only the home team's
    error_of(attach(in_x, series_y), 404, "not_found")  # shared to X: not Y's series
    assert attach(in_x, series_x).status_code == 200  # its own team's
    session.expire_all()
    assert session.get(Meeting, private.id).series_id is None
    assert session.get(Meeting, in_x.id).series_id == series_x.id


def test_a_meeting_moved_to_a_team_joins_that_teams_series_only(app, session, template, cast):
    world = cast
    series_y = Series(team_id=world.y.id, owner_user_id=world.pat.id, title="Y sync")
    series_a = Series(team_id=world.a.id, owner_user_id=world.pat.id, title="A sync")
    session.add_all([series_y, series_a])
    session.commit()
    meeting = ready(session, template, owner_user_id=world.pat.id, owner_team_id=world.a.id)
    pat = sign_in(app, PAT)
    error_of(
        pat.put(f"/api/meetings/{meeting.id}/series", {"series_id": series_y.id}), 404, "not_found"
    )
    assert (
        pat.put(f"/api/meetings/{meeting.id}/series", {"series_id": series_a.id}).status_code == 200
    )


def test_with_one_team_and_the_switch_off_attaching_is_as_it_was(
    app, session, template, world, monkeypatch
):
    monkeypatch.setattr(teams, "multi_team_enabled", lambda: False)
    series_x = Series(team_id=world.x.id, owner_user_id=world.ada.id, title="X sync")
    session.add(series_x)
    session.commit()
    ada = sign_in(app, "owner@example.test")  # one ordinary team: X, her default
    meeting = ready(session, template)
    assert (
        ada.put(f"/api/meetings/{meeting.id}/series", {"series_id": series_x.id}).status_code == 200
    )


# ---- auto-share while the switch is off ----


def test_auto_share_is_refused_and_ignored_while_the_switch_is_off(
    app, session, template, world, monkeypatch
):
    monkeypatch.setattr(teams, "multi_team_enabled", lambda: False)
    pat = sign_in(app, PAT)
    error_of(
        call(pat, "PUT", "/api/settings/auto-share", None, {"team_id": world.x.id}),
        409,
        "multi_team_disabled",
    )
    # a row written while the switch was on is ignored while it is off: today's rule only
    from app.models import UserSettings

    monkeypatch.setattr(teams, "multi_team_enabled", lambda: True)
    assert (
        call(pat, "PUT", "/api/settings/auto-share", None, {"team_id": world.y.id}).status_code
        == 200
    )
    monkeypatch.setattr(teams, "multi_team_enabled", lambda: False)
    assert teams.auto_share_team(session, world.pat) is None
    assert get(pat, "/api/settings/auto-share").json() == {"team_id": None}
    mine = ready(session, template, owner_user_id=world.pat.id)  # a plain member
    teams.apply_share_default(session, mine)
    assert mine.shared_team_id is None
    assert session.scalars(select(UserSettings.auto_share_team_id)).all()  # the row is still there


# ---- the teams list and creation ----


def test_nothing_can_be_created_while_the_switch_is_off_and_the_list_says_so(
    app, session, world, monkeypatch
):
    monkeypatch.setattr(teams, "multi_team_enabled", lambda: False)
    assert get(sign_in(app, PAT), "/api/teams").json()["can_create_more"] == 0


def test_a_team_name_body_is_strict_like_every_other_request_body(app, session, world):
    pat = sign_in(app, PAT)
    assert call(pat, "POST", "/api/teams", None, {"name": "ok", "extra": 1}).status_code == 422
    assert call(pat, "POST", "/api/teams", None, {"name": 5}).status_code == 422


def test_two_creates_at_once_cannot_pass_the_limit(app, session, world):
    """A create in flight holds the person's row: a second one waits for it and then sees the count
    it left (three), refusing with `team_limit`."""
    engine = session.get_bind()
    new_team(session, world.zed, "Z0")  # Zed owns Z and Z0: two of three
    zed_id = world.zed.id
    holder = Session(engine)
    holder.execute(select(User.id).where(User.id == zed_id).with_for_update())  # create #1 begins
    outcome: dict = {}

    def second_create():
        with Session(engine) as other:
            try:
                teams.create_owned_team(other, other.get(User, zed_id), "Fourth")
                other.commit()
                outcome["result"] = "created"
            except teams.TeamChangeRefused as exc:
                outcome["result"] = exc.code

    thread = threading.Thread(target=second_create)
    thread.start()
    try:
        time.sleep(1.0)
        assert thread.is_alive()  # waiting on the person's row
        third = Team(owner_user_id=zed_id, name="Third", is_personal=False)
        holder.add(third)
        holder.flush()
        holder.add(TeamMember(team_id=third.id, user_id=zed_id, role="owner"))
        holder.commit()  # create #1 ends: three owned
    finally:
        holder.rollback()  # never leave the row locked for the fixtures' teardown
        holder.close()
        thread.join(10)
    assert outcome["result"] == "team_limit"
    owned = session.scalars(
        select(Team).where(Team.owner_user_id == zed_id, Team.is_personal.is_(False))
    ).all()
    assert len(owned) == teams.MAX_OWNED_TEAMS
