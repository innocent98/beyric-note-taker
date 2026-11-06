"""W4: filing a task into a project (spec 6.2) and milestone proposals (spec 3.1, 6.3, D14),
through the real extraction run. The cast is `tests/test_tasks.py`'s (`owner` Ada, a team)."""

from datetime import date

import pytest
from sqlalchemy import select

from app import filing, projects
from app.models import Milestone, Project, Task
from tests.test_tasks import (  # noqa: F401  (fixtures used by name)
    GOOD,
    NOW,
    all_tasks,
    daniel,
    make_meeting,
    owner,
    run,
    team,
)


def project(session, team, name, *keywords, color="#0E6B5E"):
    row = Project(team_id=team.id, name=name, color=color, keywords=list(keywords))
    session.add(row)
    session.commit()
    return row


def fake(id_, *keywords):
    return Project(id=id_, team_id=1, name=str(id_), color="#000000", keywords=list(keywords))


# ---- the rule, on its own ----


def test_a_series_project_wins_and_is_confident():
    chosen = filing.file_task([fake(1, "a"), fake(2, "b")], series_project_id=2, text="a a")
    assert chosen == filing.Filing(2, False)


def test_a_team_with_one_project_files_there_unflagged_even_with_no_hit():
    assert filing.file_task([fake(7, "portal")], series_project_id=None, text="lunch") == (
        filing.Filing(7, False)
    )


def test_no_hit_in_a_team_of_several_projects_files_nowhere():
    assert filing.file_task(
        [fake(1, "portal"), fake(2, "billing")], series_project_id=None, text="lunch"
    ) == filing.Filing(None, False)


def test_one_clear_winner_with_two_hits_is_confident():
    got = filing.file_task(
        [fake(1, "portal", "checkout"), fake(2, "billing")],
        series_project_id=None,
        text="Portal sync: finish the checkout and billing",
    )
    assert got == filing.Filing(1, False)


def test_one_hit_files_to_the_best_and_asks_for_a_check():
    got = filing.file_task(
        [fake(1, "portal"), fake(2, "billing")], series_project_id=None, text="the portal"
    )
    assert got == filing.Filing(1, True)


def test_a_tie_files_to_the_oldest_and_asks_for_a_check():
    got = filing.file_task(
        [fake(1, "portal"), fake(2, "billing")], series_project_id=None, text="portal billing"
    )
    assert got == filing.Filing(1, True)


def test_two_hits_that_tie_with_two_hits_still_ask():
    got = filing.file_task(
        [fake(1, "a1", "b1"), fake(2, "a2", "b2")], series_project_id=None, text="a1 b1 a2 b2"
    )
    assert got == filing.Filing(1, True)


def test_keywords_match_whole_words_and_phrases_ignoring_case_and_accents():
    assert filing.hits(["port"], "the portal") == 0
    assert filing.hits(["Portal"], "THE PORTAL.") == 1
    assert filing.hits(["client portal"], "Client  portal launch") == 1
    assert filing.hits(["cafe"], "Café opening") == 1
    assert filing.hits(["portal", "portal"], "portal portal") == 2  # per keyword, never per mention


def test_keywords_are_cleaned_deduplicated_and_bounded():
    assert projects.clean_keywords(["  Portal ", "portal", "", "Billing"]) == ["Portal", "Billing"]
    with pytest.raises(projects.ProjectInvalid) as long:
        projects.clean_keywords(["x" * 41])
    assert long.value.code == "keyword_too_long"
    with pytest.raises(projects.ProjectInvalid) as many:
        projects.clean_keywords([f"k{i}" for i in range(21)])
    assert many.value.code == "too_many_keywords"


# ---- through a real extraction run ----


def test_a_new_task_is_filed_by_its_words_and_a_weak_match_is_flagged(session, owner, team):
    portal = project(session, team, "Portal", "checkout", "payment")
    project(session, team, "Ops", "demo")
    meeting = make_meeting(session, owner)
    run(session, meeting)
    by_title = {t.title: t for t in all_tasks(session, meeting)}
    checkout = by_title["Finish payment checkout"]
    assert (checkout.project_id, checkout.check_project) == (portal.id, False)  # two hits
    demo = by_title["Set up the demo"]
    assert demo.project_id is not None and demo.check_project is True  # one hit: Ops
    contract = by_title["Send the contract to Kemi"]
    assert (contract.project_id, contract.check_project) == (None, False)  # no hit, two projects


def test_a_team_with_one_project_files_every_task_there_unflagged(session, owner, team):
    only = project(session, team, "Everything")
    run(session, make_meeting(session, owner))
    tasks = all_tasks(session)
    assert tasks and all((t.project_id, t.check_project) == (only.id, False) for t in tasks)


def test_a_team_with_no_project_leaves_tasks_without_one(session, owner, team):
    run(session, make_meeting(session, owner))
    assert all((t.project_id, t.check_project) == (None, False) for t in all_tasks(session))


def test_a_task_a_person_filed_is_not_refiled_by_a_re_run(session, owner, team):
    portal = project(session, team, "Portal", "checkout", "payment")
    other = project(session, team, "Other", "xyz")
    meeting = make_meeting(session, owner)
    run(session, meeting)
    task = next(t for t in all_tasks(session, meeting) if t.title == "Finish payment checkout")
    task.project_id, task.edited_at = other.id, NOW  # what PATCH does
    session.commit()
    run(session, meeting)
    session.refresh(task)
    assert task.project_id == other.id != portal.id


def test_deleting_a_project_leaves_its_tasks_without_one_and_clears_their_flag(
    session, owner, team
):
    portal = project(session, team, "Portal", "checkout", "payment")
    project(session, team, "Ops", "demo")
    run(session, make_meeting(session, owner))
    assert session.scalars(select(Task).where(Task.check_project.is_(True))).all()
    for p in session.scalars(select(Project)).all():
        projects.delete(session, p)
    session.expire_all()
    assert all(t.project_id is None and not t.check_project for t in all_tasks(session))
    assert portal.id not in {p.id for p in session.scalars(select(Project))}


# ---- milestone proposals ----

SAID = "[Ada]: Launch is December 8."
MS = {"name": "Client Portal launch", "date": "2026-12-08", "quote": "Launch is December 8."}


def with_milestones(*items):
    return {**GOOD, "milestones": list(items)}


def test_a_stated_date_is_stored_as_a_proposal_and_never_confirmed(session, owner, team):
    meeting = make_meeting(session, owner)
    meeting.speaker_transcript += "\n[Ada]: Launch is December 8."
    session.commit()
    run(session, meeting, [with_milestones(MS)])
    row = session.scalars(select(Milestone)).one()
    assert (row.name, row.due_date, row.confirmed_at) == (
        "Client Portal launch",
        date(2026, 12, 8),
        None,
    )
    assert (row.quote, row.source_meeting_id, row.team_id) == (MS["quote"], meeting.id, team.id)


def test_a_made_up_quote_or_a_bad_date_makes_no_proposal(session, owner, team):
    meeting = make_meeting(session, owner)
    run(
        session,
        meeting,
        [with_milestones({**MS, "quote": "We will launch on Mars."}, {**MS, "date": "2031-01-01"})],
    )
    assert session.scalars(select(Milestone)).all() == []


def test_a_rewrite_replaces_the_proposals_but_keeps_a_confirmed_one(session, owner, team):
    meeting = make_meeting(session, owner)
    meeting.speaker_transcript += "\n[Ada]: Launch is December 8.\n[Ada]: Beta is November 20."
    session.commit()
    run(session, meeting, [with_milestones(MS)])
    first = session.scalars(select(Milestone)).one()
    first.confirmed_at = NOW
    session.commit()
    beta = {"name": "Beta", "date": "2026-11-20", "quote": "Beta is November 20."}
    run(session, meeting, [with_milestones(MS, beta)])
    rows = session.scalars(select(Milestone).order_by(Milestone.id)).all()
    assert [(r.name, r.confirmed_at is not None) for r in rows] == [
        ("Client Portal launch", True),  # confirmed: untouched, and not proposed a second time
        ("Beta", False),
    ]


def test_a_proposal_is_filed_to_a_project_by_the_same_rule(session, owner, team):
    portal = project(session, team, "Portal", "client", "portal")
    project(session, team, "Ops", "demo")
    meeting = make_meeting(session, owner)
    meeting.speaker_transcript += "\n[Ada]: Launch is December 8."
    session.commit()
    run(session, meeting, [with_milestones(MS)])
    assert session.scalars(select(Milestone.project_id)).one() == portal.id


def test_a_name_is_plain_one_line_text_and_an_over_long_name_is_clipped(session, owner, team):
    meeting = make_meeting(session, owner)
    meeting.speaker_transcript += "\n[Ada]: Launch is December 8."
    session.commit()
    name = "Go\x00 live\n now " + "y" * 300
    run(session, meeting, [with_milestones({**MS, "name": name})])
    row = session.scalars(select(Milestone)).one()
    assert row.name.startswith("Go live now y") and len(row.name) <= 200
    assert "\n" not in row.name and "\x00" not in row.name


def test_another_meetings_unconfirmed_proposal_does_not_hide_this_ones(session, owner, team):
    """Opus review: only a confirmed milestone counts as already there."""
    first = make_meeting(session, owner)
    first.speaker_transcript += "\n[Ada]: Launch is December 8."
    other = make_meeting(session, owner)
    other.speaker_transcript += "\n[Ada]: Launch is December 8."
    session.commit()
    run(session, first, [with_milestones(MS)])
    run(session, other, [with_milestones(MS)])
    sources = {m.source_meeting_id for m in session.scalars(select(Milestone))}
    assert sources == {first.id, other.id}


def test_a_task_that_moved_team_during_the_model_call_is_not_filed_into_the_old_teams_project(
    session, owner, team
):
    """Opus review: the owner left the team while the model was asked."""
    from app import teams as teams_module

    project(session, team, "Portal", "checkout", "payment")
    meeting = make_meeting(session, owner)
    run(session, meeting)  # tasks exist, filed in `team`
    fresh = teams_module.Team(owner_user_id=owner.id)
    session.add(fresh)
    session.flush()
    for task in all_tasks(session, meeting):
        task.team_id, task.project_id, task.check_project = fresh.id, None, False
    session.commit()
    tasks_now = all_tasks(session, meeting)
    from app import tasks as tasks_module

    tasks_module.reconcile(
        session, meeting, team.id, [t.title for t in tasks_now], [], [], NOW.date(), quiet=True
    )
    session.expire_all()
    assert all(t.project_id is None for t in all_tasks(session, meeting))
