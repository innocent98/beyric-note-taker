"""W4 Projects and milestones API (`app/api_projects.py`): who may read and change what, the task
filters, and the shapes. The cast is `tests/test_api_tasks.py`'s: `owner`, `admin` and `member` on
one team, and `outsider` with a team of their own."""

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from app import teams
from app.models import Milestone, Project, Task
from tests.test_api import (  # noqa: F401  (fixtures used by name)
    Api,
    anon,
    anon_client,
    api,
    api_routes,
    app,
    client,
    error_of,
    fake_openai,
    fake_vexa,
    owner,
    ready,
    template,
)
from tests.test_api_tasks import (  # noqa: F401
    ADMIN_EMAIL,
    MEMBER_EMAIL,
    add_task,
    admin,
    as_user,
    member,
    private,
    shared_meeting,
)
from tests.test_team_visibility import OTHER_EMAIL, outsider, share, sign_in  # noqa: F401
from tests.test_web import OWNER_EMAIL, make_user, stranger  # noqa: F401


@pytest.fixture
def team(session, owner):
    """The owner's team, committed: the API's own connection must see it, or its `ensure_team`
    would wait on the uncommitted membership."""
    row = teams.ensure_team(session, owner)
    session.commit()
    return row


TOMORROW = (datetime.now(UTC) + timedelta(days=30)).date()


def make_project(api, name="Portal", **more):
    response = api.post("/api/projects", {"name": name, **more})
    assert response.status_code == 201, response.text
    return response.json()


def make_milestone(session, team, *, project=None, confirmed=True, meeting=None, **more):
    row = Milestone(
        team_id=team.id,
        project_id=project,
        name=more.pop("name", "Client Portal launch"),
        due_date=more.pop("due_date", TOMORROW),
        source_meeting_id=meeting.id if meeting else None,
        quote=more.pop("quote", None),
        confirmed_at=datetime.now(UTC) if confirmed else None,
    )
    session.add(row)
    session.commit()
    return row


# ---- projects ----


def test_a_project_is_made_with_the_next_colour_and_cleaned_fields(api):
    first = make_project(api, "  Client   Portal ", keywords=[" portal ", "Portal", "checkout"])
    second = make_project(api, "")
    assert first["name"] == "Client Portal" and first["keywords"] == ["portal", "checkout"]
    assert first["color"] == "#0E6B5E" and second["color"] == "#3C4A7A"
    assert second["name"] == "" and first["open_tasks"] == 0 and first["milestone"] is None
    assert api.get("/api/projects").json()["can_manage"] is True


def test_a_project_saves_one_field_at_a_time(api):
    project = make_project(api, "Portal")
    url = f"/api/projects/{project['id']}"
    assert api.call("PATCH", url, {"name": "Client Portal"}).json()["name"] == "Client Portal"
    assert api.call("PATCH", url, {"color": "#8A2E5C"}).json()["color"] == "#8A2E5C"
    got = api.call("PATCH", url, {"keywords": ["portal", "checkout"]}).json()
    assert (got["name"], got["color"], got["keywords"]) == (
        "Client Portal",
        "#8A2E5C",
        ["portal", "checkout"],
    )
    assert api.get(url).json() == got


def test_bad_project_fields_are_refused_with_a_code(api):
    project = make_project(api)
    url = f"/api/projects/{project['id']}"
    error_of(api.call("PATCH", url, {"keywords": ["x" * 41]}), 422, "keyword_too_long")
    error_of(
        api.call("PATCH", url, {"keywords": [f"k{i}" for i in range(21)]}), 422, "validation_error"
    )
    error_of(api.call("PATCH", url, {"color": "red"}), 422, "validation_error")
    error_of(api.call("PATCH", url, {"name": None}), 422, "validation_error")
    error_of(api.call("PATCH", url, {"nope": 1}), 422, "validation_error")


def test_open_task_counts_follow_what_the_viewer_may_see(
    api, app, session, team, private, shared_meeting, owner, member
):
    project = make_project(api)
    pid = project["id"]
    add_task(session, team, private, title="Hidden", project_id=pid)
    add_task(session, team, shared_meeting, title="Shown", project_id=pid)
    add_task(session, team, shared_meeting, title="Done", project_id=pid, status="done")
    assert api.get(f"/api/projects/{pid}").json()["open_tasks"] == 2
    mine = as_user(app, MEMBER_EMAIL)
    assert (
        mine.get(f"/api/projects/{pid}").json()["open_tasks"] == 1
    )  # the private meeting's one is not theirs


def test_the_project_line_shows_the_next_milestone_ahead_else_the_latest_that_passed(
    api, session, team
):
    pid = make_project(api)["id"]
    make_milestone(session, team, project=pid, name="Old", due_date=date(2020, 1, 1))
    assert api.get(f"/api/projects/{pid}").json()["milestone"]["name"] == "Old"
    make_milestone(session, team, project=pid, name="Soon", due_date=TOMORROW)
    make_milestone(session, team, project=pid, name="Later", due_date=TOMORROW + timedelta(days=9))
    make_milestone(
        session, team, project=pid, name="Proposal", confirmed=False, due_date=date(2021, 1, 1)
    )
    line = api.get(f"/api/projects/{pid}").json()["milestone"]
    assert line == {"id": line["id"], "name": "Soon", "due_date": TOMORROW.isoformat()}


def test_deleting_a_project_keeps_its_tasks_without_a_project_and_removes_its_milestones(
    api, session, team, private
):
    pid = make_project(api)["id"]
    task = add_task(session, team, private, project_id=pid, check_project=True)
    make_milestone(session, team, project=pid)
    assert api.delete(f"/api/projects/{pid}").status_code == 204
    session.expire_all()
    task = session.get(Task, task.id)
    assert (task.project_id, task.check_project) == (None, False)
    assert session.scalars(select(Milestone)).all() == []
    error_of(api.get(f"/api/projects/{pid}"), 404, "not_found")


# ---- roles and teams ----


def test_a_member_may_read_but_every_write_is_403_and_changes_nothing(
    api, app, session, team, member, shared_meeting
):
    pid = make_project(api, "Portal")["id"]
    ms = make_milestone(session, team, project=pid, confirmed=False, meeting=shared_meeting)
    them = as_user(app, MEMBER_EMAIL)
    assert [p["id"] for p in them.get("/api/projects").json()["projects"]] == [pid]
    assert them.get("/api/projects").json()["can_manage"] is False
    for method, path, body in [
        ("POST", "/api/projects", {"name": "x"}),
        ("PATCH", f"/api/projects/{pid}", {"name": "x"}),
        ("DELETE", f"/api/projects/{pid}", None),
        ("POST", "/api/milestones", {"name": "x", "due_date": "2026-12-08"}),
        ("PATCH", f"/api/milestones/{ms.id}", {"name": "x"}),
        ("DELETE", f"/api/milestones/{ms.id}", None),
        ("POST", f"/api/milestones/{ms.id}/confirm", None),
    ]:
        error_of(them.call(method, path, body), 403, "team_forbidden")
    session.expire_all()
    assert session.get(Project, pid).name == "Portal"
    assert session.get(Milestone, ms.id).confirmed_at is None


def test_an_admin_may_write(api, app, session, team, admin):
    pid = make_project(api, "Portal")["id"]
    them = as_user(app, ADMIN_EMAIL)
    assert (
        them.call("PATCH", f"/api/projects/{pid}", {"name": "Renamed"}).json()["name"] == "Renamed"
    )
    assert them.post("/api/projects", {"name": "Second"}).status_code == 201


def test_another_team_gets_404_on_every_project_and_milestone_id_and_nothing_changes(
    api, app, session, team, outsider
):
    pid = make_project(api, "Portal")["id"]
    ms = make_milestone(session, team, project=pid, confirmed=False)
    them = sign_in(app, OTHER_EMAIL)
    assert them.get("/api/projects").json()["projects"] == []
    assert them.get("/api/milestones").json()["milestones"] == []
    for method, path, body in [
        ("GET", f"/api/projects/{pid}", None),
        ("PATCH", f"/api/projects/{pid}", {"name": "x"}),
        ("DELETE", f"/api/projects/{pid}", None),
        ("PATCH", f"/api/milestones/{ms.id}", {"name": "x"}),
        ("DELETE", f"/api/milestones/{ms.id}", None),
        ("POST", f"/api/milestones/{ms.id}/confirm", None),
    ]:
        error_of(them.call(method, path, body), 404, "not_found")
    session.expire_all()
    assert session.get(Project, pid).name == "Portal"
    assert session.get(Milestone, ms.id).confirmed_at is None


def test_every_new_route_is_classified(app):
    found = {r for r in api_routes(app) if "/projects" in r[1] or "/milestones" in r[1]}
    assert found == {
        ("GET", "/api/projects"),
        ("POST", "/api/projects"),
        ("GET", "/api/projects/{project_id}"),
        ("PATCH", "/api/projects/{project_id}"),
        ("DELETE", "/api/projects/{project_id}"),
        ("GET", "/api/milestones"),
        ("POST", "/api/milestones"),
        ("PATCH", "/api/milestones/{milestone_id}"),
        ("DELETE", "/api/milestones/{milestone_id}"),
        ("POST", "/api/milestones/{milestone_id}/confirm"),
        ("GET", "/api/meetings/{meeting_id}/milestones"),
    }, found


# ---- milestones ----


def test_a_milestone_made_by_hand_is_confirmed_and_counts_its_tasks(api, session, team, private):
    pid = make_project(api)["id"]
    created = api.post(
        "/api/milestones", {"name": "Launch", "due_date": "2026-12-08", "project_id": pid}
    )
    assert created.status_code == 201
    body = created.json()
    assert body["confirmed_at"].endswith("Z") and body["can_confirm"] is False
    assert (body["task_count"], body["done_count"]) == (0, 0)
    add_task(session, team, private, title="A", milestone_id=body["id"])
    add_task(session, team, private, title="B", milestone_id=body["id"], status="done")
    listing = api.get(f"/api/milestones?project_id={pid}").json()["milestones"]
    assert [(m["task_count"], m["done_count"]) for m in listing] == [(2, 1)]
    error_of(
        api.post("/api/milestones", {"name": "x", "due_date": "2026-12-08", "project_id": 99999}),
        422,
        "project_not_on_team",
    )
    error_of(
        api.post("/api/milestones", {"name": "x", "due_date": "soon"}), 422, "validation_error"
    )


def test_the_list_hides_proposals_and_a_meeting_shows_them_with_the_words_said(
    api, session, team, private
):
    make_milestone(session, team, meeting=private, confirmed=False, quote="Launch is December 8.")
    assert api.get("/api/milestones").json()["milestones"] == []
    got = api.get(f"/api/meetings/{private.id}/milestones").json()["milestones"]
    assert len(got) == 1
    assert got[0]["quote"] == "Launch is December 8." and got[0]["confirmed_at"] is None
    assert got[0]["can_confirm"] is True and got[0]["source_meeting"]["id"] == private.id


def test_confirming_a_proposal_makes_it_a_milestone_and_may_choose_the_project(
    api, session, team, private
):
    pid = make_project(api)["id"]
    ms = make_milestone(session, team, meeting=private, confirmed=False)
    done = api.post(f"/api/milestones/{ms.id}/confirm", {"project_id": pid}).json()
    assert done["confirmed_at"] and done["project_id"] == pid and done["can_confirm"] is False
    again = api.post(f"/api/milestones/{ms.id}/confirm").json()  # nothing to do the second time
    assert again["confirmed_at"] == done["confirmed_at"]
    assert [m["id"] for m in api.get("/api/milestones").json()["milestones"]] == [ms.id]
    error_of(
        api.post(f"/api/milestones/{ms.id}/confirm", {"project_id": 99999}),
        422,
        "project_not_on_team",
    )


def test_a_milestone_can_be_renamed_moved_and_deleted_and_its_tasks_stay(
    api, session, team, private
):
    ms = make_milestone(session, team)
    task = add_task(session, team, private, milestone_id=ms.id)
    patched = api.call(
        "PATCH", f"/api/milestones/{ms.id}", {"name": " Go live ", "due_date": "2027-01-04"}
    )
    assert (patched.json()["name"], patched.json()["due_date"]) == ("Go live", "2027-01-04")
    assert api.delete(f"/api/milestones/{ms.id}").status_code == 204
    session.expire_all()
    assert session.get(Task, task.id).milestone_id is None


def test_a_teammate_who_can_read_a_shared_meeting_sees_its_proposal_but_not_the_private_one(
    api, app, session, team, private, shared_meeting, member
):
    make_milestone(session, team, meeting=shared_meeting, confirmed=False, name="Shared one")
    make_milestone(session, team, meeting=private, confirmed=False, name="Private one")
    them = as_user(app, MEMBER_EMAIL)
    got = them.get(f"/api/meetings/{shared_meeting.id}/milestones").json()["milestones"]
    assert [m["name"] for m in got] == ["Shared one"] and got[0]["can_confirm"] is False
    error_of(them.get(f"/api/meetings/{private.id}/milestones"), 404, "not_found")


# ---- tasks: project, milestone, check, filters ----


def test_a_task_is_filed_by_hand_and_that_clears_the_check(api, session, team, private):
    pid, other = make_project(api)["id"], make_project(api, "Other")["id"]
    task = add_task(session, team, private, project_id=pid, check_project=True)
    got = api.call("PATCH", f"/api/tasks/{task.id}", {"project_id": other}).json()
    assert (got["project_id"], got["check_project"]) == (other, False)
    task2 = add_task(session, team, private, title="Two", project_id=pid, check_project=True)
    kept = api.call("PATCH", f"/api/tasks/{task2.id}", {"check_project": False}).json()
    assert (kept["project_id"], kept["check_project"]) == (pid, False)
    error_of(
        api.call("PATCH", f"/api/tasks/{task2.id}", {"check_project": True}),
        422,
        "validation_error",
    )
    assert (
        api.call("PATCH", f"/api/tasks/{task2.id}", {"project_id": None}).json()["project_id"]
        is None
    )


def test_a_task_takes_only_a_project_and_a_confirmed_milestone_of_its_own_team(
    api, app, session, team, private, outsider
):
    mine = add_task(session, team, private)
    theirs = Project(team_id=outsider_team(session, outsider), name="Theirs", color="#000000")
    session.add(theirs)
    session.commit()
    error_of(
        api.call("PATCH", f"/api/tasks/{mine.id}", {"project_id": theirs.id}),
        422,
        "project_not_on_team",
    )
    proposal = make_milestone(session, team, confirmed=False)
    error_of(
        api.call("PATCH", f"/api/tasks/{mine.id}", {"milestone_id": proposal.id}),
        422,
        "milestone_not_on_team",
    )
    confirmed = make_milestone(session, team)
    got = api.call("PATCH", f"/api/tasks/{mine.id}", {"milestone_id": confirmed.id}).json()
    assert got["milestone_id"] == confirmed.id


def outsider_team(session, user):
    return teams.ensure_team(session, user).id


def test_a_member_may_change_status_but_not_where_a_task_is_filed(
    api, app, session, team, private, member, owner
):
    """Opus review: filing is the owner's and admin's (spec 6.3); a member keeps status and due."""
    pid = make_project(api)["id"]
    mine = add_task(session, team, private, title="Mine", assignee=member, check_project=True)
    owners = add_task(session, team, private, title="Owners", assignee=owner)
    me = as_user(app, MEMBER_EMAIL)
    assert me.call("PATCH", f"/api/tasks/{mine.id}", {"status": "doing"}).status_code == 200
    for change in ({"project_id": pid}, {"check_project": False}, {"milestone_id": None}):
        if change == {"milestone_id": None}:
            continue  # unchanged, so not a change at all
        error_of(me.call("PATCH", f"/api/tasks/{mine.id}", change), 403, "team_forbidden")
    session.expire_all()
    assert (session.get(Task, mine.id).project_id, session.get(Task, mine.id).check_project) == (
        None,
        True,
    )
    # the member cannot see the owner's private-meeting task at all
    error_of(me.call("PATCH", f"/api/tasks/{owners.id}", {"project_id": pid}), 404, "not_found")


def test_a_member_may_file_a_task_they_made_themselves(api, app, session, team, member):
    pid = make_project(api)["id"]
    me = as_user(app, MEMBER_EMAIL)
    made = me.post("/api/tasks", {"title": "Mine"}).json()
    assert me.call("PATCH", f"/api/tasks/{made['id']}", {"project_id": pid}).status_code == 200


def test_a_proposal_with_no_source_meeting_is_no_ones_even_an_admins(
    api, app, session, team, admin
):
    orphan = make_milestone(session, team, confirmed=False, name="Orphan")  # source gone
    them = as_user(app, ADMIN_EMAIL)
    for method, path, body in [
        ("POST", f"/api/milestones/{orphan.id}/confirm", None),
        ("PATCH", f"/api/milestones/{orphan.id}", {"name": "x"}),
        ("DELETE", f"/api/milestones/{orphan.id}", None),
    ]:
        error_of(them.call(method, path, body), 404, "not_found")


def test_a_team_can_have_at_most_fifty_projects(api, session, team):
    from app import projects as projects_module

    for i in range(projects_module.MAX_PROJECTS):
        session.add(Project(team_id=team.id, name=f"P{i}", color="#0E6B5E", keywords=[]))
    session.commit()
    error_of(api.post("/api/projects", {"name": "one more"}), 422, "too_many_projects")


def test_the_list_filters_by_project_and_milestone_and_counts_checks(api, session, team, private):
    a, b = make_project(api, "A")["id"], make_project(api, "B")["id"]
    ms = make_milestone(session, team, project=a)
    one = add_task(session, team, private, title="1", project_id=a, milestone_id=ms.id)
    two = add_task(session, team, private, title="2", project_id=b, check_project=True)
    three = add_task(session, team, private, title="3")
    done = add_task(
        session, team, private, title="4", project_id=b, check_project=True, status="done"
    )

    def ids(query):
        return sorted(t["id"] for t in api.get(f"/api/tasks?{query}").json()["tasks"])

    assert ids(f"project={a}") == [one.id]
    assert ids(f"project={b}") == sorted([two.id, done.id])
    assert ids("project=none") == [three.id]
    assert ids(f"milestone={ms.id}") == [one.id]
    for bad in ("project=x", "project=-1", "milestone=0", "milestone=x"):
        error_of(api.get(f"/api/tasks?{bad}"), 422, "validation_error")
    assert api.get("/api/tasks").json()["attention"]["check_project"] == 1  # open ones only


def test_a_new_manual_task_may_be_filed_in_the_open_project(api, session, team):
    pid = make_project(api)["id"]
    got = api.post("/api/tasks", {"title": "x", "project_id": pid}).json()
    assert got["project_id"] == pid and got["check_project"] is False
    error_of(
        api.post("/api/tasks", {"title": "x", "project_id": 99999}), 422, "project_not_on_team"
    )


def test_a_manager_who_cannot_read_the_meeting_gets_404_on_its_proposal_and_no_quote(
    api, app, session, team, private, admin
):
    pending = make_milestone(session, team, meeting=private, confirmed=False, quote="secret words")
    done = make_milestone(session, team, meeting=private, name="Done one", quote="more words")
    them = as_user(app, ADMIN_EMAIL)
    for method, path, body in [
        ("POST", f"/api/milestones/{pending.id}/confirm", None),
        ("PATCH", f"/api/milestones/{pending.id}", {"name": "x"}),
        ("DELETE", f"/api/milestones/{pending.id}", None),
    ]:
        error_of(them.call(method, path, body), 404, "not_found")
    session.expire_all()
    assert session.get(Milestone, pending.id).confirmed_at is None
    seen = them.get("/api/milestones").json()["milestones"]
    assert [m["name"] for m in seen] == ["Done one"]
    assert (
        seen[0]["quote"] is None and seen[0]["source_meeting"] is None
    )  # the admin cannot read it
    mine = api.get("/api/milestones").json()["milestones"]
    assert mine[0]["quote"] == "more words" and done.id == mine[0]["id"]
