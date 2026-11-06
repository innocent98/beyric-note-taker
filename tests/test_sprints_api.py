"""W9 sprints API (`app/api_sprints.py`, the sprint fields of tasks, projects and the team): who may
read and change what, the team boundary on every id, and the shapes. The cast is
`tests/test_api_tasks.py`'s: `owner`, `admin` and `member` on one team, `outsider` with a team of
their own."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app import sprints, teams
from app.models import Project, Sprint, Task, Team
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
from tests.test_projects_api import make_project
from tests.test_team_visibility import OTHER_EMAIL, outsider, share, sign_in  # noqa: F401
from tests.test_web import OWNER_EMAIL, make_user, stranger  # noqa: F401

TODAY = datetime.now(UTC).date()


@pytest.fixture
def team(session, owner):
    row = teams.ensure_team(session, owner)
    session.commit()
    return row


def board(api, **query):
    suffix = "".join(f"?{k}={v}" for k, v in query.items())
    response = api.get(f"/api/sprints{suffix}")
    assert response.status_code == 200, response.text
    return response.json()


def rows(session):
    session.expire_all()
    return session.scalars(select(Sprint).order_by(Sprint.id)).all()


# ---- reading ----


def test_the_first_call_makes_the_current_and_next_sprint_and_counts_what_is_planned(
    api, session, team, private
):
    body = board(api)
    current, nxt = body["sprints"][-2], body["sprints"][-1]
    assert (current["state"], nxt["state"]) == ("current", "next")
    assert (current["name"], nxt["name"]) == ("Sprint 1", "Sprint 2")
    assert body["current_id"] == current["id"] and body["next_id"] == nxt["id"]
    assert body["project_id"] is None and body["own_series"] is False and body["can_manage"] is True
    assert (body["sprint_length"], body["sprint_custom_days"]) == ("week5", 10)
    assert body["today"] == TODAY.isoformat() or body["today"]  # the viewer's zone may differ
    add_task(session, team, private, title="A", sprint_id=current["id"])
    add_task(session, team, private, title="B", sprint_id=current["id"], status="done")
    add_task(session, team, private, title="C", sprint_id=nxt["id"])
    again = board(api)["sprints"]
    assert [(s["tasks_total"], s["tasks_done"]) for s in again] == [(2, 1), (1, 0)]
    assert len(rows(session)) == 2


def test_counts_follow_what_the_viewer_may_see(
    api, app, session, team, private, shared_meeting, member
):
    cur = board(api)["current_id"]
    add_task(session, team, private, title="Hidden", sprint_id=cur)
    add_task(session, team, shared_meeting, title="Shown", sprint_id=cur)
    assert board(api)["sprints"][-2]["tasks_total"] == 2
    theirs = board(as_user(app, MEMBER_EMAIL))["sprints"][-2]
    assert theirs["tasks_total"] == 1


def test_a_project_board_counts_its_tasks_and_an_own_length_has_its_own_series(
    api, session, team, private
):
    one = make_project(api, "One")["id"]
    two = make_project(api, "Two")["id"]
    cur = board(api)["current_id"]
    add_task(session, team, private, title="a", project_id=one, sprint_id=cur)
    add_task(session, team, private, title="b", project_id=two, sprint_id=cur)
    assert board(api, project=one)["sprints"][-2]["tasks_total"] == 1
    assert board(api)["sprints"][-2]["tasks_total"] == 2
    got = api.call("PATCH", f"/api/projects/{two}", {"sprint_length": "week14"})
    assert got.status_code == 200 and got.json()["sprint_length"] == "week14"
    own = board(api, project=two)
    assert own["own_series"] is True and own["sprint_length"] == "week14"
    assert own["current_id"] != cur and own["sprints"][-2]["tasks_total"] == 1
    # "All projects" still shows both: the team's current stands for every series' current.
    assert board(api)["sprints"][-2]["tasks_total"] == 2


def test_closed_sprints_are_listed_before_the_current_one(api, session, team):
    first = board(api)
    live = session.get(Sprint, first["current_id"])
    live.start_date, live.end_date = TODAY - timedelta(days=1), TODAY + timedelta(days=4)
    session.commit()
    api.post(f"/api/sprints/{first['current_id']}/close")
    after = board(api)["sprints"]
    assert [s["state"] for s in after] == ["closed", "current", "next"]
    assert after[0]["id"] == first["current_id"] and after[0]["closed_early"] is True


def test_another_teams_project_is_404_and_a_bad_project_is_422(api, app, session, team, outsider):
    theirs = sign_in(app, OTHER_EMAIL)
    pid = make_project(theirs, "Theirs")["id"]
    error_of(api.get(f"/api/sprints?project={pid}"), 404, "not_found")
    error_of(api.get("/api/sprints?project=abc"), 422, "validation_error")


def test_sprints_never_mix_between_teams(api, app, session, team, outsider):
    mine = board(api)
    theirs = board(sign_in(app, OTHER_EMAIL))
    assert {s["id"] for s in mine["sprints"]}.isdisjoint({s["id"] for s in theirs["sprints"]})
    assert {s.team_id for s in rows(session)} == {team.id, teams.team_of(session, outsider.id).id}


# ---- closing ----


def test_closing_carries_the_unfinished_tasks_and_says_how_many_the_caller_sees(
    api, session, team, private, shared_meeting
):
    body = board(api)
    cur, nxt = body["current_id"], body["next_id"]
    todo = add_task(session, team, shared_meeting, title="a", sprint_id=cur)
    hidden = add_task(session, team, private, title="b", sprint_id=cur)
    done = add_task(session, team, shared_meeting, title="c", sprint_id=cur, status="done")
    got = api.post(f"/api/sprints/{cur}/close")
    assert got.status_code == 200, got.text
    result = got.json()
    assert result["moved"] == 2  # both belong to the owner, who can see both
    assert result["closed"]["state"] == "closed" and result["current"]["id"] == nxt
    assert result["current"]["name"] == "Sprint 2" and result["current"]["state"] == "current"
    session.expire_all()
    assert [session.get(Task, t.id).sprint_id for t in (todo, hidden, done)] == [nxt, nxt, cur]
    assert [session.get(Task, t.id).carried for t in (todo, hidden, done)] == [True, True, False]


def test_the_moved_count_leaves_out_tasks_the_closer_cannot_see(
    api, app, session, team, private, shared_meeting, admin
):
    cur = board(api)["current_id"]
    add_task(session, team, shared_meeting, title="a", sprint_id=cur)
    add_task(session, team, private, title="b", sprint_id=cur)
    result = as_user(app, ADMIN_EMAIL).post(f"/api/sprints/{cur}/close").json()
    assert result["moved"] == 1  # the private meeting's task moved too, but is not the admin's
    session.expire_all()
    assert {t.carried for t in session.scalars(select(Task))} == {True}


def test_only_the_current_sprint_closes_and_only_once(api, session, team):
    body = board(api)
    error_of(api.post(f"/api/sprints/{body['next_id']}/close"), 409, "sprint_not_current")
    assert api.post(f"/api/sprints/{body['current_id']}/close").status_code == 200
    error_of(api.post(f"/api/sprints/{body['current_id']}/close"), 409, "sprint_not_current")
    assert len([s for s in rows(session) if s.closed_at is None]) == 2


def test_a_member_cannot_close_and_nothing_changes(api, app, session, team, member):
    cur = board(api)["current_id"]
    error_of(as_user(app, MEMBER_EMAIL).post(f"/api/sprints/{cur}/close"), 403, "team_forbidden")
    assert all(s.closed_at is None for s in rows(session))


def test_an_admin_closes_and_another_team_gets_404_with_nothing_changed(
    api, app, session, team, admin, outsider
):
    cur = board(api)["current_id"]
    stranger_api = sign_in(app, OTHER_EMAIL)
    error_of(stranger_api.post(f"/api/sprints/{cur}/close"), 404, "not_found")
    assert all(s.closed_at is None for s in rows(session))
    error_of(api.post("/api/sprints/99999/close"), 404, "not_found")
    assert as_user(app, ADMIN_EMAIL).post(f"/api/sprints/{cur}/close").status_code == 200


def test_a_member_of_another_team_who_is_an_owner_there_still_cannot_close_ours(
    api, app, session, team, outsider
):
    """Being an owner of one's own team grants nothing on another team's sprint."""
    cur = board(api)["current_id"]
    them = sign_in(app, OTHER_EMAIL)
    board(them)  # their own team has sprints, they are its owner
    error_of(them.post(f"/api/sprints/{cur}/close"), 404, "not_found")
    assert all(s.closed_at is None for s in rows(session) if s.team_id == team.id)


def test_a_late_close_after_the_end_date_is_not_early(api, session, team):
    body = board(api)
    sprint = session.get(Sprint, body["current_id"])
    sprint.start_date, sprint.end_date = TODAY - timedelta(days=9), TODAY - timedelta(days=3)
    session.commit()
    api.post(f"/api/sprints/{sprint.id}/close")
    assert board(api)["sprints"][0]["closed_early"] is False


# ---- tasks ----


def test_the_task_list_filters_by_sprint_and_another_teams_id_matches_nothing(
    api, app, session, team, private, outsider
):
    body = board(api)
    a = add_task(session, team, private, title="a", sprint_id=body["current_id"])
    b = add_task(session, team, private, title="b", sprint_id=body["next_id"])
    c = add_task(session, team, private, title="c")

    def ids(value):
        return {t["id"] for t in api.get(f"/api/tasks?sprint={value}").json()["tasks"]}

    assert ids("current") == ids(body["current_id"]) == {a.id}
    assert ids("next") == ids(body["next_id"]) == {b.id}
    assert ids("none") == {c.id}
    theirs = board(sign_in(app, OTHER_EMAIL))["current_id"]
    assert ids(theirs) == set()
    error_of(api.get("/api/tasks?sprint=soon"), 422, "validation_error")


def test_a_task_shows_its_sprint_and_whether_it_was_carried(api, session, team, private):
    cur = board(api)["current_id"]
    task = add_task(session, team, private, sprint_id=cur, carried=True)
    got = api.get(f"/api/tasks/{task.id}").json()
    assert (got["sprint_id"], got["carried"]) == (cur, True)


def test_a_new_task_is_filed_by_its_due_date_unless_the_sprint_is_given(api, session, team):
    body = board(api)
    cur, nxt = body["current_id"], body["next_id"]
    current = session.get(Sprint, cur)
    soon = api.post("/api/tasks", {"title": "soon", "due_date": current.end_date.isoformat()})
    later = api.post(
        "/api/tasks",
        {"title": "later", "due_date": (current.end_date + timedelta(days=1)).isoformat()},
    )
    none = api.post("/api/tasks", {"title": "none"})
    forced = api.post(
        "/api/tasks", {"title": "forced", "sprint_id": nxt, "due_date": TODAY.isoformat()}
    )
    backlog = api.post("/api/tasks", {"title": "b", "sprint_id": None, "due_date": "2026-11-05"})
    got = [r.json()["sprint_id"] for r in (soon, later, none, forced, backlog)]
    assert got == [cur, nxt, None, nxt, None]


def test_a_task_cannot_be_planned_into_another_teams_closed_or_wrong_series_sprint(
    api, app, session, team, outsider
):
    body = board(api)
    theirs = board(sign_in(app, OTHER_EMAIL))["current_id"]
    closed = body["current_id"]
    api.post(f"/api/sprints/{closed}/close")
    for bad in (theirs, closed, 424242):
        error_of(
            api.post("/api/tasks", {"title": "x", "sprint_id": bad}), 422, "sprint_not_on_team"
        )
    made = api.post("/api/tasks", {"title": "ok"}).json()
    for bad in (theirs, closed, 424242):
        error_of(
            api.call("PATCH", f"/api/tasks/{made['id']}", {"sprint_id": bad}),
            422,
            "sprint_not_on_team",
        )
    session.expire_all()
    assert session.get(Task, made["id"]).sprint_id is None
    error_of(api.post("/api/tasks", {"title": "x", "sprint_id": 0}), 422, "validation_error")


def test_a_project_with_its_own_length_only_accepts_its_own_sprints(api, session, team):
    pid = make_project(api, "P")["id"]
    api.call("PATCH", f"/api/projects/{pid}", {"sprint_length": "week14"})
    team_cur = board(api)["current_id"]
    own_cur = board(api, project=pid)["current_id"]
    error_of(
        api.post("/api/tasks", {"title": "x", "project_id": pid, "sprint_id": team_cur}),
        422,
        "sprint_not_on_team",
    )
    ok = api.post("/api/tasks", {"title": "x", "project_id": pid, "sprint_id": own_cur})
    assert ok.status_code == 201 and ok.json()["sprint_id"] == own_cur
    error_of(
        api.post("/api/tasks", {"title": "y", "sprint_id": own_cur}), 422, "sprint_not_on_team"
    )


def test_an_owner_plans_a_task_and_a_person_placing_it_clears_carried(api, session, team, private):
    body = board(api)
    task = add_task(session, team, private, sprint_id=body["current_id"], carried=True)
    got = api.call("PATCH", f"/api/tasks/{task.id}", {"sprint_id": body["next_id"]}).json()
    assert (got["sprint_id"], got["carried"]) == (body["next_id"], False)
    back = api.call("PATCH", f"/api/tasks/{task.id}", {"sprint_id": None}).json()
    assert back["sprint_id"] is None


def test_a_member_given_a_task_can_tick_it_but_not_move_it_between_sprints(
    api, app, session, team, private, member
):
    body = board(api)
    task = add_task(session, team, private, assignee=member, sprint_id=body["current_id"])
    them = as_user(app, MEMBER_EMAIL)
    assert them.call("PATCH", f"/api/tasks/{task.id}", {"status": "doing"}).status_code == 200
    error_of(
        them.call("PATCH", f"/api/tasks/{task.id}", {"sprint_id": body["next_id"]}),
        403,
        "team_forbidden",
    )
    error_of(
        them.call("PATCH", f"/api/tasks/{task.id}", {"sprint_id": None}), 403, "team_forbidden"
    )
    session.expire_all()
    assert session.get(Task, task.id).sprint_id == body["current_id"]


def test_a_member_may_plan_the_task_they_made_themselves(api, app, session, team, member):
    body = board(api)
    them = as_user(app, MEMBER_EMAIL)
    made = them.post("/api/tasks", {"title": "mine"}).json()
    got = them.call("PATCH", f"/api/tasks/{made['id']}", {"sprint_id": body["next_id"]})
    assert got.status_code == 200 and got.json()["sprint_id"] == body["next_id"]


def test_moving_a_task_to_a_project_with_its_own_length_keeps_its_place_in_time(
    api, session, team, private
):
    pid = make_project(api, "P")["id"]
    api.call("PATCH", f"/api/projects/{pid}", {"sprint_length": "week14"})
    team_body, own = board(api), board(api, project=pid)
    task = add_task(session, team, private, sprint_id=team_body["next_id"])
    got = api.call("PATCH", f"/api/tasks/{task.id}", {"project_id": pid}).json()
    assert got["sprint_id"] == own["next_id"]
    back = api.call("PATCH", f"/api/tasks/{task.id}", {"project_id": None}).json()
    assert back["sprint_id"] == team_body["next_id"]


# ---- settings ----


def test_the_team_reads_its_sprint_settings_and_an_admin_changes_them(
    api, app, session, team, admin
):
    got = api.get("/api/team").json()
    assert (got["sprint_length"], got["sprint_custom_days"], got["sprint_align_weekdays"]) == (
        "week5",
        10,
        True,
    )
    board(api)
    them = as_user(app, ADMIN_EMAIL)
    set_ = them.call("PATCH", "/api/team", {"sprint_length": "custom", "sprint_custom_days": 12})
    assert set_.status_code == 200 and set_.json()["sprint_length"] == "custom"
    current = board(api)["sprints"][-2]
    start = datetime.fromisoformat(current["start_date"]).date()
    assert datetime.fromisoformat(current["end_date"]).date() == start + timedelta(days=11)
    nxt = board(api)["sprints"][-1]
    assert datetime.fromisoformat(nxt["start_date"]).date() == start + timedelta(days=12)


def test_a_member_cannot_change_sprint_settings_and_bad_values_are_refused(
    api, app, session, team, member
):
    error_of(
        as_user(app, MEMBER_EMAIL).call("PATCH", "/api/team", {"sprint_length": "week14"}),
        403,
        "team_forbidden",
    )
    assert api.get("/api/team").json()["sprint_length"] == "week5"
    for bad in (
        {"sprint_length": "fortnight"},
        {"sprint_custom_days": 0},
        {"sprint_custom_days": 366},
    ):
        error_of(api.call("PATCH", "/api/team", bad), 422, "validation_error")
    error_of(
        api.call("PATCH", "/api/team", {"sprint_length": "week7", "sprint_align_weekdays": True}),
        422,
        "align_needs_week5",
    )
    assert api.get("/api/team").json()["sprint_length"] == "week5"
    ok = api.call("PATCH", "/api/team", {"sprint_length": "week7", "sprint_align_weekdays": False})
    assert ok.status_code == 200 and ok.json()["sprint_align_weekdays"] is False


# ---- projects ----


def test_a_project_shows_its_sprint_numbers_and_its_own_length_is_a_managers_to_set(
    api, app, session, team, private, member
):
    pid = make_project(api, "P")["id"]
    cur = board(api)["current_id"]
    add_task(session, team, private, title="in", project_id=pid, sprint_id=cur)
    add_task(session, team, private, title="out", project_id=pid)
    add_task(session, team, private, title="done", project_id=pid, sprint_id=cur, status="done")
    got = api.get(f"/api/projects/{pid}").json()
    assert (got["in_current_sprint"], got["in_backlog"], got["sprint_length"]) == (1, 1, None)
    error_of(
        as_user(app, MEMBER_EMAIL).call(
            "PATCH", f"/api/projects/{pid}", {"sprint_length": "week7"}
        ),
        403,
        "team_forbidden",
    )
    error_of(
        api.call("PATCH", f"/api/projects/{pid}", {"sprint_length": "custom"}),
        422,
        "validation_error",
    )
    api.call("PATCH", f"/api/projects/{pid}", {"sprint_length": "week7"})
    own = api.get(f"/api/projects/{pid}").json()
    assert (own["sprint_length"], own["in_current_sprint"], own["in_backlog"]) == ("week7", 1, 1)
    cleared = api.call("PATCH", f"/api/projects/{pid}", {"sprint_length": None}).json()
    assert cleared["sprint_length"] is None and cleared["in_current_sprint"] == 1
    error_of(api.call("PATCH", f"/api/projects/{pid}", {"name": None}), 422, "validation_error")


def test_deleting_a_project_with_its_own_length_keeps_its_tasks_in_the_teams_sprints(
    api, session, team, private
):
    pid = make_project(api, "P")["id"]
    api.call("PATCH", f"/api/projects/{pid}", {"sprint_length": "week14"})
    own = board(api, project=pid)
    task = add_task(session, team, private, project_id=pid, sprint_id=own["current_id"])
    assert api.delete(f"/api/projects/{pid}").status_code == 204
    session.expire_all()
    team_current = board(api)["current_id"]
    assert session.get(Task, task.id).sprint_id == team_current
    assert session.scalars(select(Sprint).where(Sprint.project_id.is_not(None))).all() == []


# ---- team changes ----


def test_joining_another_team_puts_the_tasks_in_its_backlog(session, team, owner, private=None):
    other = Team(owner_user_id=owner.id)
    session.add(other)
    session.flush()
    current, _ = sprints.ensure_series(session, team, None, TODAY)
    task = Task(team_id=team.id, title="t", source="manual", sprint_id=current.id, carried=True)
    session.add(task)
    session.flush()
    teams.move_team_content(session, team.id, other.id)
    session.flush()
    session.refresh(task)
    assert (task.team_id, task.sprint_id, task.carried) == (other.id, None, False)


def test_a_project_row_is_untouched_by_listing_sprints(api, session, team):
    pid = make_project(api, "P")["id"]
    board(api, project=pid)
    session.expire_all()
    assert session.get(Project, pid).sprint_length is None
