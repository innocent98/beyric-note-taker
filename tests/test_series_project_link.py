"""Linking a recurring meeting to a project (`PUT /api/series/{id}/project`), the filing rule it
feeds (spec 6.2 step 1), the project list's "M recurring meetings", and the sprint line on the
Series page ("Opens Sprint 13", the reminder mail's own rule).

The cast is `tests/test_series.py`'s: `owner` (owner of the team), `daniel` (a plain member) and
`outsider` (a team of their own); `weekly` is the owner's "Monday planning" series.
"""

# ruff: noqa: F811  (fixtures are imported by name and used as arguments)

import threading
import time
from datetime import date, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import api_series, mail_summary, projects, series, teams
from app.models import Project, Series, TeamMember, User
from tests.test_api import error_of  # noqa: F401
from tests.test_mail_summary import mail_of, make_sprint
from tests.test_projects_api import make_project
from tests.test_series import (  # noqa: F401  (fixtures used by name)
    DANIEL_EMAIL,
    NOW,
    anon,
    anon_client,
    api,
    app,
    calendar_event,
    client,
    daniel,
    fake_openai,
    fake_vexa,
    fixed_clock,
    meeting_in,
    outsider,
    owner,
    sign_in,
    stranger,
    template,
    weekly,
)
from tests.test_series_mail import busy, due  # noqa: F401
from tests.test_team_visibility import OTHER_EMAIL


def link(who, series_id, project_id):
    return who.put(f"/api/series/{series_id}/project", {"project_id": project_id})


def card_of(who, series_id):
    return next(c for c in who.get("/api/series").json()["series"] if c["id"] == series_id)


# ---- who may link ----


def test_an_owner_links_and_unlinks_and_the_card_says_which_project(api, session, weekly):
    project = make_project(api, "Client Portal")
    assert card_of(api, weekly.id)["project"] is None
    response = link(api, weekly.id, project["id"])
    assert response.status_code == 200, response.text
    assert response.json()["project"] == {"id": project["id"], "name": "Client Portal"}
    assert card_of(api, weekly.id)["project"] == {"id": project["id"], "name": "Client Portal"}
    session.refresh(weekly)
    assert weekly.project_id == project["id"]
    assert link(api, weekly.id, None).json()["project"] is None  # unlink
    session.refresh(weekly)
    assert weekly.project_id is None


def test_the_same_link_twice_changes_nothing(api, session, weekly):
    project = make_project(api)
    first = link(api, weekly.id, project["id"]).json()
    assert link(api, weekly.id, project["id"]).json() == first
    assert link(api, weekly.id, None).json()["project"] is None
    assert link(api, weekly.id, None).json()["project"] is None


def test_a_series_is_in_one_project_linking_it_again_moves_it(api, session, weekly):
    portal, billing = make_project(api, "Portal"), make_project(api, "Billing")
    link(api, weekly.id, portal["id"])
    assert link(api, weekly.id, billing["id"]).json()["project"]["id"] == billing["id"]
    counts = {p["id"]: p["recurring_meetings"] for p in api.get("/api/projects").json()["projects"]}
    assert counts == {portal["id"]: 0, billing["id"]: 1}


def test_a_plain_member_cannot_link_even_a_series_they_can_see(api, app, session, daniel, weekly):
    project = make_project(api)
    weekly.owner_user_id = daniel.id  # Daniel made it, so he sees it; the creator is not enough
    session.commit()
    member = sign_in(app, DANIEL_EMAIL)
    assert card_of(member, weekly.id)["id"] == weekly.id
    error_of(link(member, weekly.id, project["id"]), 403, "team_forbidden")
    error_of(link(member, weekly.id, None), 403, "team_forbidden")
    session.refresh(weekly)
    assert weekly.project_id is None


def test_an_admin_may_link(api, app, session, daniel, weekly):
    project = make_project(api)
    session.scalars(select(TeamMember).where(TeamMember.user_id == daniel.id)).one().role = "admin"
    weekly.owner_user_id = daniel.id
    session.commit()
    assert link(sign_in(app, DANIEL_EMAIL), weekly.id, project["id"]).status_code == 200


def test_a_manager_cannot_link_a_series_they_cannot_see(api, app, session, owner, daniel, weekly):
    # Daniel (a member) made a private recurring call; the owner has no meeting of it, no event.
    private = Series(team_id=weekly.team_id, owner_user_id=daniel.id, title="1:1")
    session.add(private)
    session.commit()
    project = make_project(api)
    error_of(link(api, private.id, project["id"]), 404, "not_found")
    session.refresh(private)
    assert private.project_id is None


def test_another_teams_series_and_projects_are_not_found(api, app, session, outsider, weekly):
    mine = make_project(api)
    other = sign_in(app, OTHER_EMAIL)
    theirs = make_project(other, "Theirs")
    error_of(link(other, weekly.id, theirs["id"]), 404, "not_found")  # not their team's series
    error_of(link(api, weekly.id, theirs["id"]), 404, "not_found")  # not my team's project
    error_of(link(api, 999_999, mine["id"]), 404, "not_found")
    error_of(link(api, weekly.id, 999_999), 404, "not_found")
    session.refresh(weekly)
    assert weekly.project_id is None


def test_the_body_must_name_a_project_or_null(api, weekly):
    for body in ({}, {"project_id": 0}, {"project_id": "1"}, {"project_id": 1, "x": 1}):
        assert api.put(f"/api/series/{weekly.id}/project", body).status_code == 422
    assert api.put(
        f"/api/series/{weekly.id}/project", {"project_id": 1}, csrf=False
    ).status_code in (
        401,
        403,
    )


def test_deleting_a_project_unlinks_its_series(api, session, weekly):
    project = make_project(api)
    link(api, weekly.id, project["id"])
    assert api.delete(f"/api/projects/{project['id']}").status_code == 204
    session.refresh(weekly)
    assert weekly.project_id is None
    assert card_of(api, weekly.id)["project"] is None


def test_a_stray_project_id_on_a_series_is_no_link(api, session, outsider, weekly):
    foreign = Project(team_id=teams.team_of(session, outsider.id).id, name="Theirs", color="#000")
    session.add(foreign)
    session.commit()
    weekly.project_id = (
        foreign.id
    )  # no foreign key: whatever was written must not file across teams
    session.commit()
    assert card_of(api, weekly.id)["project"] is None


def test_a_link_and_a_delete_of_the_same_project_queue_up_and_leave_no_link(
    engine, session, owner, weekly
):
    """Two connections: the link holds the project (FOR SHARE) and the series row, uncommitted; the
    delete must wait for it (not clear the links first and cross it, which deadlocks), and then
    clear the link it finds. Nothing is left pointing at the deleted project."""
    project = Project(team_id=weekly.team_id, name="Portal", color="#0E6B5E", keywords=[])
    session.add(project)
    session.commit()
    project_id = project.id
    with Session(engine) as linker, Session(engine) as deleter:
        series.link_project(
            linker, linker.get(User, owner.id), linker.get(Series, weekly.id), project.id
        )
        errors = []

        def delete():
            try:
                projects.delete(deleter, deleter.get(Project, project.id))
            except Exception as exc:  # noqa: BLE001 (reported below)
                errors.append(exc)

        worker = threading.Thread(target=delete)
        worker.start()
        time.sleep(0.5)
        assert worker.is_alive(), "the delete waits for the link instead of crossing it"
        linker.commit()
        worker.join(10)
    assert not worker.is_alive() and errors == []
    session.expire_all()
    assert session.get(Project, project_id) is None
    assert session.get(Series, weekly.id).project_id is None


# ---- the project list ----


def test_the_list_counts_only_the_recurring_meetings_the_caller_may_see(
    api, app, session, owner, daniel, weekly
):
    project = make_project(api, "Portal")
    private = Series(team_id=weekly.team_id, owner_user_id=daniel.id, title="1:1", project_id=None)
    session.add(private)
    session.commit()
    link(api, weekly.id, project["id"])
    private.project_id = project["id"]  # a manager linked it when they could see it, say
    session.commit()
    mine = {p["id"]: p for p in api.get("/api/projects").json()["projects"]}
    assert mine[project["id"]]["recurring_meetings"] == 1  # the owner cannot see Daniel's 1:1
    theirs = {
        p["id"]: p for p in sign_in(app, DANIEL_EMAIL).get("/api/projects").json()["projects"]
    }
    assert (
        theirs[project["id"]]["recurring_meetings"] == 1
    )  # Daniel sees only his own, not "Monday planning"


# ---- the sprint line ----


@pytest.fixture
def opening(session, weekly):
    """Sprint 12 closed Tue 3 Nov; sprint 13 starts today, Wed 4 Nov (these tests' clock)."""
    make_sprint(session, weekly.team_id, 12, date(2026, 10, 21), date(2026, 11, 3), closed_at=NOW)
    return make_sprint(session, weekly.team_id, 13, date(2026, 11, 4), date(2026, 11, 17))


def page(who, series_id):
    return who.get(f"/api/series/{series_id}").json()


def test_the_page_says_which_sprint_the_next_meeting_opens_as_the_mail_does(
    api, session, owner, daniel, weekly, busy, due, opening
):
    assert page(api, weekly.id)["next_text"] == "Wed 4 Nov, 10:55"
    assert page(api, weekly.id)["sprint_note"] == "Opens Sprint 13"
    assert "Sprint 13 opens" in mail_of(session, owner.email).text, "the mail agrees with the page"
    session.refresh(weekly)
    found = mail_summary.opening_sprint(session, owner, weekly, NOW + timedelta(minutes=55), None)
    assert found is not None and found.id == opening.id


def test_no_sprint_line_without_a_next_meeting_or_sprints_or_a_matching_day(
    api, session, owner, weekly, busy
):
    assert page(api, weekly.id)["sprint_note"] is None  # no calendar event: no next meeting
    weekly.calendar_recurring_id = "rid-1"
    session.commit()
    calendar_event(session, owner, "rid-1", NOW + timedelta(minutes=55))
    assert page(api, weekly.id)["sprint_note"] is None  # the team has no sprints
    make_sprint(session, weekly.team_id, 5, date(2026, 11, 2), date(2026, 11, 15))
    assert page(api, weekly.id)["sprint_note"] is None  # none starts on that day


def test_a_meeting_already_held_in_the_sprint_means_it_is_not_the_opening_one(
    api, session, owner, template, weekly, due, opening
):
    meeting_in(
        session, template, weekly, when=NOW - timedelta(hours=2)
    )  # today, before the sprint's
    assert page(api, weekly.id)["sprint_note"] is None


def test_a_failing_sprint_line_leaves_the_page_served_without_it(
    api, session, owner, weekly, busy, due, opening, monkeypatch
):
    def boom(*args, **kwargs):
        raise RuntimeError("no")

    monkeypatch.setattr(mail_summary, "opening_sprint", boom)
    body = page(api, weekly.id)
    assert body["sprint_note"] is None and body["title"] == "Monday planning"
    assert api_series._now() == NOW
