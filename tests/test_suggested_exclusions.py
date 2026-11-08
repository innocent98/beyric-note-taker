"""A suggested task is counted nowhere and mailed to nobody (spec 2026-11-08, section 2): one test
for each place the Board, a series, a project, a sprint, the team, the sidebar and the mails count
or list tasks. Each puts one suggested task next to one that was made, and asks the place about
both. The places checked and left (they should not change) are in the SOP.
"""

from sqlalchemy import select

from app import mail, mail_summary, projects, series, series_mail, team_admin
from app.mail_templates import Skip
from app.models import Milestone, Task
from tests.test_api_tasks import (  # noqa: F401  (fixtures used by name)
    MEMBER_EMAIL,
    add_task,
    admin,
    api,
    app,
    as_user,
    member,
    private,
    shared_meeting,
    team,
)
from tests.test_mail_summary import (  # noqa: F401
    OWNER_EMAIL,
    box,
    fill_sprint,
    mail_of,
    put,
    sprints_on,
)
from tests.test_projects_api import make_project
from tests.test_series import (  # noqa: F401
    MONDAY,
    NOW,
    anon,
    anon_client,
    client,
    daniel,
    fake_openai,
    fake_vexa,
    fixed_clock,
    meeting_in,
    owner,
    ready,
    task_in,
    template,
    weekly,
)
from tests.test_series_mail import SETTINGS, busy, due, queued  # noqa: F401
from tests.test_sprints_api import board


def waiting(session, task):
    """Make an existing task a suggestion (what the extraction does to an unclear one)."""
    task.suggested, task.confidence, task.reason = True, "low", "Said as someone should."
    session.commit()
    return task


# ---- the series ----


def test_a_series_leaves_a_suggestion_out_of_unfinished_the_card_and_the_meeting_counts(
    api, session, owner, template, weekly
):
    meeting = meeting_in(session, template, weekly)
    task_in(session, weekly.team_id, meeting, "Made")
    waiting(session, task_in(session, weekly.team_id, meeting, "Suggested"))
    waiting(session, task_in(session, weekly.team_id, meeting, "Done suggestion", status="done"))
    session.query(Task).filter(Task.title == "Done suggestion").update({"done_at": NOW})
    session.commit()
    assert [t.title for t in series.unfinished_tasks(session, owner, weekly)] == ["Made"]
    assert series.counts_for(session, owner, [weekly.id])[weekly.id][0] == 1
    assert series.finished_since(session, owner, weekly, MONDAY.date(), None) == []
    page = api.get(f"/api/series/{weekly.id}").json()
    assert [t["title"] for t in page["unfinished"]] == ["Made"] and page["finished"] == []
    assert [m["tasks_count"] for m in page["past_meetings"]] == [1]
    card = next(c for c in api.get("/api/series").json()["series"] if c["id"] == weekly.id)
    assert card["unfinished_tasks"] == 1


def test_the_one_hour_reminder_does_not_list_a_suggestion_under_still_open(
    session, owner, daniel, weekly, busy, due
):
    waiting(session, task_in(session, weekly.team_id, busy, "Maybe renew the licence"))
    message = mail_of(session, OWNER_EMAIL)
    assert "Finish checkout" in message.text
    assert "Maybe renew the licence" not in message.text
    assert "Maybe renew the licence" not in message.html
    rows, total = series_mail._task_rows(session, owner, weekly, NOW.date())
    assert total == 2 and "Maybe renew the licence" not in [r.title for r in rows]


def test_the_summary_box_counts_a_closed_sprint_without_its_suggestions(
    session, owner, daniel, weekly, busy, due, sprints_on
):
    closed, opening = sprints_on
    fill_sprint(session, weekly, busy, closed, opening, daniel, done=3, carried=1)
    sentence = mail_summary.closed_sprint_sentence(session, owner, weekly, NOW)
    assert sentence == "Sprint 12 closed with 3 of 4 tasks done."
    extra_done = task_in(session, weekly.team_id, busy, "A suggestion in the sprint")
    extra_carried = task_in(session, weekly.team_id, busy, "A carried suggestion")
    put(session, waiting(session, extra_done), closed, status="done")
    put(session, waiting(session, extra_carried), opening, carried=True)
    assert mail_summary.closed_sprint_sentence(session, owner, weekly, NOW) == sentence


# ---- projects, milestones, sprints, people ----


def test_a_project_counts_only_the_tasks_that_were_made(api, session, team, private, member):
    pid = make_project(api)["id"]
    add_task(session, team, private, title="Made", project_id=pid)
    waiting(session, add_task(session, team, private, title="Maybe", project_id=pid))
    shown = api.get(f"/api/projects/{pid}").json()
    assert (shown["open_tasks"], shown["in_backlog"]) == (1, 1)
    assert projects.open_task_counts(session, api_user(session), [pid]) == {pid: 1}


def api_user(session):
    from app.models import User

    return session.scalars(select(User).order_by(User.id)).first()


def test_a_milestone_progress_is_over_the_tasks_that_were_made(api, session, team, private):
    pid = make_project(api)["id"]
    ms = api.post("/api/milestones", {"name": "L", "due_date": "2026-12-08", "project_id": pid})
    mid = ms.json()["id"]
    add_task(session, team, private, title="A", milestone_id=mid)
    waiting(session, add_task(session, team, private, title="B", milestone_id=mid))
    waiting(session, add_task(session, team, private, title="C", milestone_id=mid, status="done"))
    row = api.get(f"/api/milestones?project_id={pid}").json()["milestones"][0]
    assert (row["task_count"], row["done_count"]) == (1, 0)
    assert session.get(Milestone, mid) is not None


def test_a_sprint_counts_and_the_closing_count_leave_a_suggestion_out(api, session, team, private):
    cur = board(api)["current_id"]
    add_task(session, team, private, title="Made", sprint_id=cur)
    waiting(session, add_task(session, team, private, title="Maybe", sprint_id=cur))
    waiting(session, add_task(session, team, private, title="Done", sprint_id=cur, status="done"))
    current = board(api)["sprints"][-2]
    assert (current["tasks_total"], current["tasks_done"]) == (1, 0)
    closed = api.post(f"/api/sprints/{cur}/close").json()
    assert closed["moved"] == 1  # the count a person sees; a suggestion in a sprint moves with it


def test_the_team_page_counts_a_persons_open_tasks_without_suggestions(
    api, session, team, private, owner, member
):
    add_task(session, team, private, title="Made", assignee=member)
    waiting(session, add_task(session, team, private, title="Maybe", assignee=member))
    assert team_admin.open_task_counts(session, team.id) == {member.id: 1}
    people = api.get("/api/team/members").json()["members"]
    assert next(m for m in people if m["user_id"] == member.id)["open_tasks"] == 1


# ---- the mail ----


def test_the_assignee_mail_never_lists_a_suggestion_even_one_that_turned_after_queueing(
    session, team, private, owner, member
):
    add_task(session, team, private, title="Made", assignee=member)
    maybe = waiting(session, add_task(session, team, private, title="Maybe", assignee=member))
    mail.queue_tasks_assigned(session, private, [member.id])
    session.commit()
    row = queued(session, "tasks_assigned")[0]
    from app import mail_templates

    message = mail_templates.prepare(
        session, row.template, row.payload, row.to_email, SETTINGS, NOW
    )
    assert not isinstance(message, Skip)
    assert "Made" in message.text and "Maybe" not in message.text
    session.query(Task).filter(Task.id != maybe.id).delete()
    session.commit()
    again = mail_templates.prepare(session, row.template, row.payload, row.to_email, SETTINGS, NOW)
    assert isinstance(again, Skip) and again.reason == "no_tasks"
