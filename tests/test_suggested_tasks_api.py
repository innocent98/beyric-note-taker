"""Suggested tasks, the API side (spec 2026-11-08, section 2): the list, accept, dismiss, who may,
the mail, the settings and what the caller is shown of a quote.

The cast is the Tasks API tests': `owner` (owner of the team and the meetings), `admin` and `member`
on the owner's team, `outsider` on a team of their own.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import MailOutbox, Project, UserSettings
from tests.test_api_tasks import (  # noqa: F401  (fixtures used by name)
    MEMBER_EMAIL,
    Api,
    add_task,
    admin,
    anon,
    anon_client,
    api,
    api_routes,
    app,
    as_user,
    client,
    error_of,
    fake_openai,
    fake_vexa,
    member,
    outsider,
    owner,
    private,
    ready,
    share,
    shared_meeting,
    stranger,
    team,
    template,
)
from tests.test_web import stranger  # noqa: F401

REASON = 'Offered on a condition: "if you need it".'


def suggest(session, team, meeting, **fields):
    values = {"suggested": True, "confidence": "medium", "reason": REASON, "title": "Maybe do X"}
    return add_task(session, team, meeting, **(values | fields))


def mails(session):
    session.expire_all()
    return list(session.scalars(select(MailOutbox).order_by(MailOutbox.id)))


# ---- the lists ----


def test_the_board_list_leaves_suggested_tasks_out_and_the_filter_lists_only_them(
    api, session, team, private
):
    clear = add_task(session, team, private, title="Clear", confidence="high")
    waiting = suggest(session, team, private)
    listed = api.get("/api/tasks").json()
    assert [t["id"] for t in listed["tasks"]] == [clear.id]
    only = api.get("/api/tasks?suggested=true").json()
    assert [t["id"] for t in only["tasks"]] == [waiting.id]
    shown = only["tasks"][0]
    assert shown["suggested"] is True and shown["confidence"] == "medium"
    assert shown["reason"] == REASON
    assert api.get("/api/tasks?suggested=false").json()["tasks"][0]["id"] == clear.id
    # the other filters act inside the one list they are asked of
    assert api.get("/api/tasks?assignee=none").json()["tasks"][0]["id"] == clear.id


def test_a_suggested_task_is_in_no_attention_count_and_not_in_the_sidebar_badge(
    api, session, team, private
):
    suggest(session, team, private, unverified=True, check_project=True, quote=None)
    base = api.get("/api/tasks").json()["attention"]
    assert base == {"needs_owner": 0, "unverified": 0, "check_project": 0}
    assert api.get("/api/session").json()["badges"]["tasks_new"] == 0
    add_task(session, team, private, title="Clear", unverified=True, check_project=True)
    assert api.get("/api/tasks").json()["attention"] == {
        "needs_owner": 1,
        "unverified": 1,
        "check_project": 1,
    }
    assert api.get("/api/session").json()["badges"]["tasks_new"] == 1


def test_a_meetings_task_list_marks_the_suggested_ones_and_counts_them(
    api, session, team, private, owner, member
):
    add_task(session, team, private, title="One", assignee=owner)
    add_task(session, team, private, title="Two", assignee=owner)
    suggest(session, team, private, title="Maybe", assignee=member)
    body = api.get(f"/api/meetings/{private.id}/tasks").json()
    assert [t["suggested"] for t in body["tasks"]].count(True) == 1
    assert body["to_confirm"] == 1
    assert body["assigned_people"] == 1  # the suggested task's person is not assigned yet
    assert len(body["tasks"]) == 3


def test_the_meetings_list_and_page_do_not_count_a_suggestion_as_a_task(
    api, session, team, private
):
    add_task(session, team, private, title="One")
    suggest(session, team, private)
    listed = next(m for m in api.get("/api/meetings").json()["meetings"] if m["id"] == private.id)
    assert listed["tasks_count"] == 1
    detail = api.get(f"/api/meetings/{private.id}").json()
    assert detail["tasks_count"] == 1 and len(detail["tasks"]) == 2  # the page still lists both


# ---- accept ----


def test_accepting_files_the_task_mails_the_assignee_and_clears_the_flag(
    api, session, team, private, member
):
    session.add(Project(team_id=team.id, name="Only", color="#000000", keywords=[]))
    session.commit()
    due = (datetime.now(UTC) + timedelta(days=2)).date()
    task = suggest(session, team, private, assignee=member, due_date=due)
    assert mails(session) == []
    response = api.post(f"/api/tasks/{task.id}/accept")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["suggested"] is False and body["confidence"] == "medium"
    assert body["project_id"] is not None and body["sprint_id"] is not None
    queued = mails(session)
    assert [(m.to_email, m.template, m.payload) for m in queued] == [
        (
            "member@example.test",
            "tasks_assigned",
            {"meeting_id": private.id, "user_id": member.id},
        )
    ]
    assert [t["id"] for t in api.get("/api/tasks").json()["tasks"]] == [task.id]
    assert api.get("/api/tasks?suggested=true").json()["tasks"] == []


def test_accepting_twice_is_the_same_answer_and_one_mail(api, session, team, private, member):
    task = suggest(session, team, private, assignee=member)
    first = api.post(f"/api/tasks/{task.id}/accept")
    second = api.post(f"/api/tasks/{task.id}/accept")
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert len(mails(session)) == 1


def test_a_task_that_never_was_a_suggestion_is_409(api, session, team, private):
    clear = add_task(session, team, private, title="Clear", confidence="high")
    old = add_task(session, team, private, title="Old")  # made before suggestions: no confidence
    manual = add_task(session, team, None, title="By hand")
    for task in (clear, old, manual):
        error = error_of(api.post(f"/api/tasks/{task.id}/accept"), 409, "not_suggested")
        assert error["message"] == "This task is not waiting to be confirmed."


def test_a_dismissed_task_cannot_be_accepted(api, session, team, private):
    task = suggest(session, team, private)
    assert api.delete(f"/api/tasks/{task.id}").status_code == 204
    error_of(api.post(f"/api/tasks/{task.id}/accept"), 404, "not_found")


def test_a_task_whose_meeting_you_cannot_read_is_404_and_nothing_changes(
    app, session, team, private, owner, member, outsider
):
    task = suggest(session, team, private, assignee=owner)
    for email in ("other@example.test", MEMBER_EMAIL):
        who = as_user(app, email)
        error_of(who.call("POST", f"/api/tasks/{task.id}/accept"), 404, "not_found")
        error_of(who.call("DELETE", f"/api/tasks/{task.id}"), 404, "not_found")
    session.refresh(task)
    assert task.suggested and task.deleted_at is None and mails(session) == []


def test_only_whoever_may_edit_the_task_may_accept_or_dismiss_it(
    app, session, team, shared_meeting, owner, member, admin
):
    task = suggest(session, team, shared_meeting, assignee=member)
    plain = as_user(app, MEMBER_EMAIL)  # a reader and the assignee, not a manager
    error_of(plain.call("POST", f"/api/tasks/{task.id}/accept"), 403, "team_forbidden")
    error_of(plain.call("DELETE", f"/api/tasks/{task.id}"), 403, "team_forbidden")
    session.refresh(task)
    assert task.suggested and task.deleted_at is None and mails(session) == []
    boss = as_user(app, "admin@example.test")  # an admin of the team may
    assert boss.call("POST", f"/api/tasks/{task.id}/accept").status_code == 200


def test_the_owner_of_a_team_dismisses_and_the_task_is_a_tombstone(api, session, team, private):
    task = suggest(session, team, private)
    assert api.delete(f"/api/tasks/{task.id}").status_code == 204
    session.refresh(task)
    assert task.deleted_at is not None and task.title == "" and task.reason is None
    assert api.get("/api/tasks?suggested=true").json()["tasks"] == []


@pytest.mark.parametrize(
    ("change", "setup"),
    [
        ("emailing off", {"owner_email_off": True}),
        ("a quiet meeting", {"quiet": True}),
        ("a meeting older than the outbox keeps", {"old": True}),
        ("the owner's own task", {"own": True}),
        ("nobody to tell", {"nobody": True}),
    ],
)
def test_accepting_mails_nobody_when_creation_would_not_have(
    api, session, team, template, owner, member, change, setup
):
    fields = {}
    if setup.get("quiet"):
        fields["extraction_quiet"] = True
    if setup.get("old"):
        fields["created_at"] = datetime.now(UTC) - timedelta(days=40)
    meeting = ready(session, template, **fields)
    if setup.get("owner_email_off"):
        session.add(UserSettings(user_id=owner.id, email_assignees=False))
        session.commit()
    who = owner if setup.get("own") else None if setup.get("nobody") else member
    task = suggest(session, team, meeting, assignee=who, is_new=not setup.get("quiet"))
    assert api.post(f"/api/tasks/{task.id}/accept").status_code == 200, change
    session.refresh(task)
    assert task.suggested is False and mails(session) == []


def test_someone_already_mailed_about_the_meeting_is_not_mailed_again(
    api, session, team, private, member
):
    add_task(session, team, private, title="Clear", assignee=member, confidence="high")
    from app import mail

    mail.queue_tasks_assigned(session, private, [member.id])
    session.commit()
    task = suggest(session, team, private, assignee=member)
    assert api.post(f"/api/tasks/{task.id}/accept").status_code == 200
    assert len(mails(session)) == 1  # the once-per-person-per-meeting rule stands


def test_editing_a_suggested_task_leaves_it_suggested_and_the_flag_cannot_be_patched(
    api, session, team, private
):
    task = suggest(session, team, private)
    response = api.call("PATCH", f"/api/tasks/{task.id}", {"title": "New words"})
    assert response.status_code == 200 and response.json()["suggested"] is True
    assert response.json()["title"] == "New words"
    assert api.call("PATCH", f"/api/tasks/{task.id}", {"suggested": False}).status_code == 422
    session.refresh(task)
    assert task.suggested is True


# ---- what the evidence shows ----

SAID = "[Ada]: Okay, start.\n[Daniel]: I'll take checkout.\n[Ada]: Good."


def test_the_quote_comes_with_its_speaker_and_the_row_of_the_transcript_page(
    api, session, team, template
):
    meeting = ready(session, template, speaker_transcript=SAID)
    task = suggest(session, team, meeting, quote="I'll take checkout.")
    shown = api.get(f"/api/tasks/{task.id}").json()
    assert (shown["quote"], shown["quote_speaker"], shown["quote_line"]) == (
        "I'll take checkout.",
        "Daniel",
        2,
    )


def test_a_quote_not_in_the_transcript_has_no_speaker_and_no_row(api, session, team, template):
    meeting = ready(session, template, speaker_transcript=SAID)
    task = suggest(session, team, meeting, quote="Something nobody said at all")
    shown = api.get(f"/api/tasks/{task.id}").json()
    assert shown["quote_speaker"] is None and shown["quote_line"] is None


def test_someone_who_cannot_read_the_meeting_gets_the_quote_and_nothing_around_it(
    app, session, team, template, member
):
    meeting = ready(session, template, speaker_transcript=SAID)  # private to the owner
    task = suggest(session, team, meeting, assignee=member, quote="I'll take checkout.")
    shown = as_user(app, MEMBER_EMAIL).get(f"/api/tasks/{task.id}").json()
    assert shown["quote"] == "I'll take checkout." and shown["meeting"] is None
    assert shown["quote_speaker"] is None and shown["quote_line"] is None


# ---- Settings > Tasks ----


def test_ask_before_unclear_is_on_by_default_and_round_trips(api):
    assert api.get("/api/settings/tasks").json() == {
        "auto_create_tasks": True,
        "email_assignees": True,
        "ask_before_unclear": True,
    }
    saved = api.put(
        "/api/settings/tasks",
        {"auto_create_tasks": True, "email_assignees": True, "ask_before_unclear": False},
    )
    assert saved.status_code == 200 and saved.json()["ask_before_unclear"] is False
    assert api.get("/api/settings/tasks").json()["ask_before_unclear"] is False
    error_of(
        api.put(
            "/api/settings/tasks",
            {"auto_create_tasks": True, "email_assignees": True, "ask_before_unclear": "maybe"},
        ),
        422,
        "validation_error",
    )


def test_a_client_that_does_not_send_the_new_switch_leaves_it_as_it_is(api):
    api.put(
        "/api/settings/tasks",
        {"auto_create_tasks": True, "email_assignees": True, "ask_before_unclear": False},
    )
    again = api.put("/api/settings/tasks", {"auto_create_tasks": False, "email_assignees": True})
    assert again.json() == {
        "auto_create_tasks": False,
        "email_assignees": True,
        "ask_before_unclear": False,
    }


def test_the_first_write_with_only_the_old_fields_still_defaults_the_switch_on(api, session):
    saved = api.put("/api/settings/tasks", {"auto_create_tasks": True, "email_assignees": False})
    assert saved.json()["ask_before_unclear"] is True
    assert session.scalar(select(UserSettings.ask_before_unclear)) is True


def test_a_status_change_on_a_suggested_task_leaves_it_suggested_and_uncounted(
    api, session, team, private
):
    task = suggest(session, team, private)
    response = api.call("PATCH", f"/api/tasks/{task.id}", {"status": "done"})
    assert response.status_code == 200 and response.json()["suggested"] is True
    assert api.get("/api/tasks").json()["tasks"] == []  # still on no board


def test_accepting_keeps_the_project_and_sprint_a_person_chose_while_it_waited(
    api, session, team, private
):
    chosen = Project(team_id=team.id, name="Chosen", color="#000000", keywords=[])
    other = Project(team_id=team.id, name="Other", color="#111111", keywords=[])
    session.add_all([chosen, other])
    session.commit()
    task = suggest(session, team, private, project_id=chosen.id, title="Chosen project")
    body = api.post(f"/api/tasks/{task.id}/accept").json()
    assert body["project_id"] == chosen.id  # two projects: nothing would have filed it there
