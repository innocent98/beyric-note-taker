"""The Tasks API (`app/api_tasks.py`): who sees which task, who may change it, and its shapes.

The cast: `owner` (owner of the team and of the meetings), `admin` and `member` (on the owner's
team, built by hand because invites are W3) and `outsider` (a team of their own).
"""

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select, text

from app import deletion, teams
from app.models import Meeting, Task, TeamMember
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
from tests.test_team_visibility import (  # noqa: F401
    OTHER_EMAIL,
    outsider,
    share,
    sign_in,
)
from tests.test_web import make_user, stranger  # noqa: F401

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
ADMIN_EMAIL, MEMBER_EMAIL = "admin@example.test", "member@example.test"


def join(session, owner, email, role, name):
    user = make_user(session, email, name=name)
    team = teams.ensure_team(session, owner)
    session.add(TeamMember(team_id=team.id, user_id=user.id, role=role))
    session.commit()
    return user


@pytest.fixture
def team(session, owner):
    return teams.ensure_team(session, owner)


@pytest.fixture
def admin(session, owner, team):
    return join(session, owner, ADMIN_EMAIL, "admin", "Amara Nwosu")


@pytest.fixture
def member(session, owner, team):
    return join(session, owner, MEMBER_EMAIL, "member", "Daniel Okoro")


def add_task(session, team, meeting=None, *, title="Finish checkout", assignee=None, **fields):
    values = {
        "team_id": team.id,
        "meeting_id": meeting.id if meeting else None,
        "title": title,
        "assignee_user_id": assignee.id if assignee else None,
        "source": "ai" if meeting else "manual",
        "ai_fingerprint": f"fp-{title}" if meeting else None,
        "quote": "I'll take checkout." if meeting else None,
        "assigned_on": date(2026, 11, 3),
        "is_new": True,
        **fields,
    }
    task = Task(**values)
    session.add(task)
    session.commit()
    return task


@pytest.fixture
def private(session, template):
    return ready(session, template)


@pytest.fixture
def shared_meeting(session, template, owner):
    meeting = ready(session, template)
    share(session, meeting, owner)
    return meeting


def as_user(app, email):
    return sign_in(app, email)


# ---- the list and its shapes ----


def test_the_list_carries_tasks_people_and_the_attention_counts(api, session, team, private, owner):
    mine = add_task(session, team, private, title="Mine", assignee=owner)
    nobody = add_task(session, team, private, title="Nobody", unverified=True, quote=None)
    done = add_task(session, team, private, title="Done", assignee=owner, status="done")
    body = api.get("/api/tasks").json()
    assert [t["id"] for t in body["tasks"]] == [done.id, nobody.id, mine.id]
    assert body["people"] == [{"user_id": owner.id, "name": "owner", "is_you": True}]
    assert body["attention"] == {
        "needs_owner": 1,
        "unverified": 1,
        "check_project": 0,
    }  # open tasks only
    assert body["has_more"] is False and body["next_before_id"] is None
    task = next(t for t in body["tasks"] if t["id"] == mine.id)
    assert task == {
        "id": mine.id,
        "title": "Mine",
        "status": "todo",
        "assignee": {"user_id": owner.id, "name": "owner", "is_you": True},
        "spoken_owner": None,
        "due_date": None,
        "due_phrase": None,
        "quote": "I'll take checkout.",
        "is_new": True,
        "unverified": False,
        "source": "ai",
        "assigned_on": "2026-11-03",
        "done_at": None,
        "created_at": task["created_at"],
        "from_meeting": True,
        "meeting": {
            "id": private.id,
            "title": f"Google Meet {private.native_id}",
            "platform": "google_meet",
            "created_at": task["meeting"]["created_at"],
        },
        "project_id": None,
        "milestone_id": None,
        "check_project": False,
        "can_update": True,
        "can_edit": True,
        "can_assign": True,
        "can_claim": False,
        "can_delete": True,
    }
    assert task["created_at"].endswith("Z")


def test_the_list_filters_by_assignee_and_status_and_pages(api, session, team, private, owner):
    ids = [add_task(session, team, private, title=f"T{i}", assignee=owner).id for i in range(3)]
    nobody = add_task(session, team, private, title="Nobody")
    assert [t["id"] for t in api.get("/api/tasks?assignee=none").json()["tasks"]] == [nobody.id]
    assert len(api.get("/api/tasks?assignee=me").json()["tasks"]) == 3
    assert len(api.get(f"/api/tasks?assignee={owner.id}").json()["tasks"]) == 3
    page = api.get("/api/tasks?limit=2").json()
    assert [t["id"] for t in page["tasks"]] == [nobody.id, ids[2]]
    assert page["has_more"] and page["next_before_id"] == ids[2]
    rest = api.get(f"/api/tasks?limit=2&before_id={ids[2]}").json()
    assert [t["id"] for t in rest["tasks"]] == [ids[1], ids[0]] and not rest["has_more"]
    assert api.get("/api/tasks?status=done").json()["tasks"] == []
    for bad in ("assignee=x", "status=nope", "limit=0", "limit=501", "before_id=0"):
        error_of(api.get(f"/api/tasks?{bad}"), 422, "validation_error")


# ---- who sees a task ----


def test_an_outsider_sees_nothing_and_gets_404_everywhere(
    app, api, session, team, private, owner, outsider
):
    task = add_task(session, team, private, assignee=owner)
    manual = add_task(session, team, None, title="Manual")
    them = as_user(app, OTHER_EMAIL)
    assert them.get("/api/tasks").json()["tasks"] == []
    for t in (task, manual):
        error_of(them.get(f"/api/tasks/{t.id}"), 404, "not_found")
        error_of(them.call("PATCH", f"/api/tasks/{t.id}", {"status": "done"}), 404, "not_found")
        error_of(them.call("DELETE", f"/api/tasks/{t.id}"), 404, "not_found")


def test_a_teammate_does_not_see_a_task_from_a_private_meeting(
    app, session, team, private, owner, member
):
    task = add_task(session, team, private, assignee=owner)
    me = as_user(app, MEMBER_EMAIL)
    assert me.get("/api/tasks").json()["tasks"] == []
    error_of(me.get(f"/api/tasks/{task.id}"), 404, "not_found")
    error_of(me.get(f"/api/meetings/{private.id}/tasks"), 404, "not_found")


def test_an_assignee_sees_the_task_and_its_quote_but_not_the_private_meeting(
    app, session, team, private, owner, member
):
    task = add_task(session, team, private, assignee=member)
    me = as_user(app, MEMBER_EMAIL)
    body = me.get(f"/api/tasks/{task.id}").json()
    assert body["quote"] == "I'll take checkout."
    assert body["from_meeting"] is True and body["meeting"] is None
    assert body["assignee"]["is_you"] is True
    error_of(me.get(f"/api/meetings/{private.id}"), 404, "not_found")  # the meeting stays private


def test_a_teammate_sees_the_tasks_of_a_shared_meeting_with_its_title(
    app, session, team, shared_meeting, owner, member
):
    task = add_task(session, team, shared_meeting, assignee=owner)
    me = as_user(app, MEMBER_EMAIL)
    body = me.get(f"/api/tasks/{task.id}").json()
    assert body["meeting"]["id"] == shared_meeting.id
    listed = me.get(f"/api/meetings/{shared_meeting.id}/tasks").json()
    assert [t["id"] for t in listed["tasks"]] == [task.id] and listed["assigned_people"] == 1


def test_a_manual_task_belongs_to_the_team(app, session, team, owner, member):
    manual = add_task(session, team, None, title="Book the venue")
    me = as_user(app, MEMBER_EMAIL)
    assert [t["id"] for t in me.get("/api/tasks").json()["tasks"]] == [manual.id]
    assert me.get(f"/api/tasks/{manual.id}").json()["from_meeting"] is False


def test_a_deleted_tasks_account_owner_is_not_a_route_into_it(app, session, team, private, owner):
    task = add_task(session, team, private)
    admin = make_user(session, "root@example.test", admin=True)
    assert admin.is_admin
    them = as_user(app, "root@example.test")
    error_of(them.get(f"/api/tasks/{task.id}"), 404, "not_found")  # the platform admin gets none


# ---- the route enumeration (spec 8.2) ----

TASK_READS = {("GET", "/api/tasks/{task_id}")}
TASK_WRITES = {("PATCH", "/api/tasks/{task_id}"), ("DELETE", "/api/tasks/{task_id}")}
TASK_LISTS = {("GET", "/api/tasks"), ("POST", "/api/tasks")}


def test_every_route_with_a_task_id_is_classified(app):
    found = {r for r in api_routes(app) if "{task_id}" in r[1]}
    assert found == TASK_READS | TASK_WRITES, found ^ (TASK_READS | TASK_WRITES)
    assert TASK_LISTS <= set(api_routes(app))


def test_every_task_route_is_404_for_outsiders_and_teammates_without_access(
    app, session, team, private, owner, member, outsider
):
    task = add_task(session, team, private, assignee=owner)
    for email in (OTHER_EMAIL, MEMBER_EMAIL):
        who = as_user(app, email)
        for method, path in sorted(TASK_READS | TASK_WRITES):
            body = {"status": "done"} if method == "PATCH" else None
            response = who.call(method, path.replace("{task_id}", str(task.id)), body)
            error_of(response, 404, "not_found")
    session.refresh(task)
    assert task.status == "todo" and task.deleted_at is None


# ---- roles (spec 6.1) ----


def patch(who, task, **body):
    return who.call("PATCH", f"/api/tasks/{task.id}", body)


def test_owners_and_admins_may_change_everything_they_can_see(
    app, api, session, team, shared_meeting, owner, admin, member
):
    task = add_task(session, team, shared_meeting)
    first = patch(api, task, title="New", status="doing", assignee_user_id=member.id)
    assert first.status_code == 200 and first.json()["assignee"]["name"] == "Daniel Okoro"
    boss = as_user(app, ADMIN_EMAIL)
    again = patch(boss, task, assignee_user_id=admin.id, due_date="2026-11-06")
    assert again.status_code == 200 and again.json()["due_date"] == "2026-11-06"
    assert boss.call("DELETE", f"/api/tasks/{task.id}").status_code == 204


def test_a_member_changes_the_status_and_due_date_of_their_own_task_only(
    app, session, team, shared_meeting, owner, member
):
    mine = add_task(session, team, shared_meeting, title="Mine", assignee=member)
    theirs = add_task(session, team, shared_meeting, title="Theirs", assignee=owner)
    me = as_user(app, MEMBER_EMAIL)
    ok = patch(me, mine, status="done", due_date="2026-11-09")
    assert ok.status_code == 200
    assert ok.json()["status"] == "done" and ok.json()["done_at"].endswith("Z")
    assert ok.json()["is_new"] is False  # a status change clears New
    error_of(patch(me, theirs, status="doing"), 403, "team_forbidden")
    error_of(patch(me, mine, title="Renamed"), 403, "team_forbidden")
    error_of(patch(me, mine, assignee_user_id=owner.id), 403, "team_forbidden")
    error_of(patch(me, mine, assignee_user_id=None), 403, "team_forbidden")
    error_of(me.call("DELETE", f"/api/tasks/{mine.id}"), 403, "team_forbidden")
    session.refresh(mine)
    session.refresh(theirs)
    assert (mine.title, mine.assignee_user_id, mine.deleted_at) == ("Mine", member.id, None)
    assert theirs.status == "todo"


def test_a_forbidden_request_changes_nothing_even_for_its_allowed_fields(
    app, session, team, shared_meeting, owner, member
):
    mine = add_task(session, team, shared_meeting, assignee=member)
    me = as_user(app, MEMBER_EMAIL)
    error_of(patch(me, mine, status="done", title="Sneaky"), 403, "team_forbidden")
    session.refresh(mine)
    assert mine.status == "todo" and mine.edited_at is None


def test_a_field_sent_with_its_current_value_is_not_a_change(
    app, session, team, shared_meeting, owner, member
):
    mine = add_task(session, team, shared_meeting, title="Mine", assignee=member)
    me = as_user(app, MEMBER_EMAIL)
    same = patch(me, mine, title="Mine", assignee_user_id=member.id, status="doing")
    assert same.status_code == 200 and same.json()["status"] == "doing"


def test_a_member_may_claim_a_task_nobody_has_and_only_for_themselves(
    app, session, team, shared_meeting, owner, member, admin
):
    free = add_task(session, team, shared_meeting, title="Free")
    me = as_user(app, MEMBER_EMAIL)
    listed = me.get(f"/api/tasks/{free.id}").json()
    assert listed["can_claim"] is True and listed["can_assign"] is False
    error_of(patch(me, free, assignee_user_id=admin.id), 403, "team_forbidden")
    claimed = patch(me, free, assignee_user_id=member.id)
    assert claimed.status_code == 200 and claimed.json()["assignee"]["is_you"]
    other = add_task(session, team, shared_meeting, title="Taken", assignee=owner)
    error_of(patch(me, other, assignee_user_id=member.id), 403, "team_forbidden")


def test_a_member_runs_the_tasks_they_made(app, session, team, owner, member):
    me = as_user(app, MEMBER_EMAIL)
    made = me.call("POST", "/api/tasks", {"title": "Book the venue", "assignee_user_id": owner.id})
    assert made.status_code == 201
    task_id = made.json()["id"]
    assert made.json()["can_edit"] and made.json()["can_assign"] and made.json()["can_delete"]
    assert me.call("PATCH", f"/api/tasks/{task_id}", {"title": "Book the hall"}).status_code == 200
    assert me.call("DELETE", f"/api/tasks/{task_id}").status_code == 204
    assert session.get(Task, task_id) is None  # a manual task is really gone


def test_a_member_cannot_touch_a_manual_task_someone_else_made(
    app, api, session, team, owner, member
):
    made = api.call("POST", "/api/tasks", {"title": "Owner's task"}).json()
    me = as_user(app, MEMBER_EMAIL)
    error_of(me.call("PATCH", f"/api/tasks/{made['id']}", {"title": "x"}), 403, "team_forbidden")
    error_of(me.call("DELETE", f"/api/tasks/{made['id']}"), 403, "team_forbidden")


# ---- changing a task ----


def test_status_moves_set_and_clear_done_at(api, session, team, private, owner):
    task = add_task(session, team, private, assignee=owner)
    done = patch(api, task, status="done").json()
    assert done["status"] == "done" and done["done_at"] and done["is_new"] is False
    reopened = patch(api, task, status="todo").json()
    assert reopened["status"] == "todo" and reopened["done_at"] is None


def test_titles_are_cleaned_and_limited(api, session, team, private):
    task = add_task(session, team, private)
    cleaned = patch(api, task, title="  Line one\nline\ttwo ‮  ").json()
    assert cleaned["title"] == "Line one line two"
    error_of(patch(api, task, title="x" * 201), 422, "validation_error")
    error_of(patch(api, task, title=None), 422, "validation_error")
    error_of(patch(api, task, status=None), 422, "validation_error")
    error_of(patch(api, task, status="blocked"), 422, "validation_error")
    error_of(patch(api, task, nope=1), 422, "validation_error")


def test_due_date_can_be_set_and_cleared(api, session, team, private):
    task = add_task(session, team, private, due_phrase="before Friday")
    assert patch(api, task, due_date="2026-11-06").json()["due_date"] == "2026-11-06"
    cleared = patch(api, task, due_date=None).json()
    assert cleared["due_date"] is None and cleared["due_phrase"] == "before Friday"
    error_of(patch(api, task, due_date="soon"), 422, "validation_error")


def test_assigning_to_someone_off_the_team_is_422(api, session, team, private, outsider):
    task = add_task(session, team, private)
    error_of(patch(api, task, assignee_user_id=outsider.id), 422, "assignee_not_on_team")
    error_of(patch(api, task, assignee_user_id=999999), 422, "assignee_not_on_team")


def test_assigning_a_spoken_name_teaches_the_alias(api, session, team, private, owner, member):
    task = add_task(session, team, private, spoken_owner="Dan")
    assert patch(api, task, assignee_user_id=member.id).status_code == 200
    row = session.scalars(select(TeamMember).where(TeamMember.user_id == member.id)).one()
    session.refresh(row)
    assert row.aliases == ["Dan"]
    again = add_task(session, team, private, title="Second", spoken_owner="dan")
    patch(api, again, assignee_user_id=member.id)
    session.refresh(row)
    assert row.aliases == ["Dan"]  # not twice


def test_editing_marks_the_task_so_a_rerun_leaves_it(api, session, team, private):
    task = add_task(session, team, private)
    assert task.edited_at is None
    patch(api, task, title="Mine now")
    session.refresh(task)
    assert task.edited_at is not None


def test_create_makes_a_manual_task_in_the_callers_team(api, session, team, owner, member):
    created = api.call(
        "POST",
        "/api/tasks",
        {"title": "Order cake", "assignee_user_id": member.id, "due_date": "2026-11-10"},
    )
    assert created.status_code == 201
    body = created.json()
    assert (body["source"], body["from_meeting"], body["meeting"], body["quote"]) == (
        "manual",
        False,
        None,
        None,
    )
    assert body["assignee"]["user_id"] == member.id and body["due_date"] == "2026-11-10"
    blank = api.call("POST", "/api/tasks", {}).json()
    assert blank["title"] == "" and blank["status"] == "todo"
    row = session.get(Task, body["id"])
    assert row.team_id == team.id and row.created_by_user_id == owner.id and row.is_new is False


def test_create_refuses_what_is_not_valid(api, outsider):
    error_of(
        api.call("POST", "/api/tasks", {"title": "x", "assignee_user_id": outsider.id}),
        422,
        "assignee_not_on_team",
    )
    error_of(api.call("POST", "/api/tasks", {"title": "x" * 201}), 422, "validation_error")
    error_of(api.call("POST", "/api/tasks", {"status": "later"}), 422, "validation_error")
    error_of(api.call("POST", "/api/tasks", {"nope": 1}), 422, "validation_error")


def test_task_writes_need_the_csrf_token(api, session, team, private):
    task = add_task(session, team, private)
    assert (
        api.call("PATCH", f"/api/tasks/{task.id}", {"status": "done"}, csrf=False).status_code
        == 403
    )
    assert api.call("POST", "/api/tasks", {}, csrf=False).status_code == 403
    assert api.call("DELETE", f"/api/tasks/{task.id}", csrf=False).status_code == 403


def test_deleting_an_ai_task_leaves_a_tombstone_nobody_can_see(api, session, team, private):
    task = add_task(session, team, private)
    assert api.call("DELETE", f"/api/tasks/{task.id}").status_code == 204
    error_of(api.get(f"/api/tasks/{task.id}"), 404, "not_found")
    assert api.get("/api/tasks").json()["tasks"] == []
    row = session.get(Task, task.id)
    session.refresh(row)
    assert row.deleted_at and row.title == "" and row.quote is None


# ---- the meeting page ----


def test_the_meeting_lists_its_tasks_in_the_order_of_its_notes(
    api, session, team, private, owner, member
):
    add_task(session, team, private, title="Second", assignee=member, position=1)
    add_task(session, team, private, title="First", assignee=owner, position=0)
    add_task(session, team, private, title="Third", position=2)
    body = api.get(f"/api/meetings/{private.id}/tasks").json()
    assert [t["title"] for t in body["tasks"]] == ["First", "Second", "Third"]
    assert body["assigned_people"] == 2 and body["extraction_status"] is None
    detail = api.get(f"/api/meetings/{private.id}").json()
    assert [t["title"] for t in detail["tasks"]] == ["First", "Second", "Third"]
    assert detail["tasks_count"] == 3
    listed = api.get("/api/meetings").json()["meetings"][0]
    assert listed["tasks_count"] == 3


def test_a_meeting_with_no_tasks_says_so(api, private):
    assert api.get(f"/api/meetings/{private.id}/tasks").json()["tasks"] == []
    assert api.get(f"/api/meetings/{private.id}").json()["tasks"] == []


def test_the_session_counts_new_open_tasks_for_the_badge(api, session, team, private, owner):
    assert api.get("/api/session").json()["badges"]["tasks_new"] == 0
    first = add_task(session, team, private, title="A")
    add_task(session, team, private, title="B")
    add_task(session, team, private, title="C", is_new=False)
    assert api.get("/api/session").json()["badges"]["tasks_new"] == 2
    patch(api, first, status="doing")
    assert api.get("/api/session").json()["badges"]["tasks_new"] == 1


def test_extract_on_demand_is_owner_only_and_quiet(api, session, private, owner):
    private.extraction_status = "done"
    session.commit()
    response = api.call("POST", f"/api/meetings/{private.id}/tasks/extract")
    assert response.status_code == 202 and response.json() == {"extraction_status": "pending"}
    session.refresh(private)
    assert private.extraction_status == "pending" and private.extraction_quiet is True
    error_of(
        api.call("POST", f"/api/meetings/{private.id}/tasks/extract"), 409, "extraction_pending"
    )


def test_extract_on_demand_is_refused_when_tasks_are_off_or_there_are_no_notes(
    api, session, private, template
):
    api.put("/api/settings/tasks", {"auto_create_tasks": False, "email_assignees": True})
    error_of(api.call("POST", f"/api/meetings/{private.id}/tasks/extract"), 409, "tasks_off")
    waiting = ready(session, template, status="processing", step="notes", notes=None)
    error_of(api.call("POST", f"/api/meetings/{waiting.id}/tasks/extract"), 409, "not_ready")


# ---- Settings > Tasks ----


def test_the_tasks_settings_default_on_and_round_trip(api):
    assert api.get("/api/settings/tasks").json() == {
        "auto_create_tasks": True,
        "email_assignees": True,
    }
    saved = api.put("/api/settings/tasks", {"auto_create_tasks": True, "email_assignees": False})
    assert saved.status_code == 200 and saved.json()["email_assignees"] is False
    assert api.get("/api/settings/tasks").json() == {
        "auto_create_tasks": True,
        "email_assignees": False,
    }
    error_of(api.put("/api/settings/tasks", {"auto_create_tasks": "yes"}), 422, "validation_error")


# ---- deleting a meeting or an account ----


def test_deleting_a_meeting_deletes_its_tasks(session, team, private):
    add_task(session, team, private)
    manual = add_task(session, team, None, title="Manual")
    session.execute(text("delete from meetings where id = :id"), {"id": private.id})
    session.commit()
    assert [t.id for t in session.scalars(select(Task))] == [manual.id]


def test_deleting_a_team_deletes_its_tasks(session, team, private):
    add_task(session, team, private)
    session.execute(text("delete from team_members where team_id = :id"), {"id": team.id})
    session.execute(text("update meetings set shared_team_id = null"))
    session.execute(text("delete from teams where id = :id"), {"id": team.id})
    session.commit()
    assert session.scalars(select(Task)).all() == []


def test_deleting_an_account_removes_the_tasks_they_made_and_unassigns_the_rest(
    session, team, private, owner, member
):
    add_task(session, team, None, title="Theirs", created_by_user_id=member.id)
    kept = add_task(session, team, None, title="Kept", created_by_user_id=owner.id, assignee=member)
    from_meeting = add_task(session, team, private, title="From the meeting", assignee=member)
    deletion.purge_user(session, member, vexa=None, force=True)
    session.expire_all()
    left = {t.title: t.assignee_user_id for t in session.scalars(select(Task))}
    assert left == {"Kept": None, "From the meeting": None}  # "Theirs" went with its maker
    session.refresh(kept)
    session.refresh(from_meeting)
    assert kept.assignee_user_id is None and from_meeting.assignee_user_id is None


def test_meetings_a_user_owns_take_their_tasks_when_the_account_is_purged(
    session, team, private, owner, member
):
    add_task(session, team, private, assignee=member)
    deletion.purge_user(session, owner, vexa=None, force=True)
    assert session.scalars(select(Task)).all() == []
    assert session.scalars(select(Meeting)).all() == []
