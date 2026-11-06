"""W9 sprint rules (`app/sprints.py`): the windows, the two open sprints of a series, closing and
carrying over, filing by due date, and a project's own length. No HTTP here; `test_sprints_api.py`
has the routes and who may do what."""

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from app import sprints, teams
from app.models import Project, Sprint, Task, Team
from tests.test_api import owner  # noqa: F401  (fixture used by name)
from tests.test_web import OWNER_EMAIL  # noqa: F401

MON = date(2026, 11, 2)  # a Monday
NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)  # a Wednesday


@pytest.fixture
def team(session, owner):
    row = teams.ensure_team(session, owner)
    session.commit()
    return row


def mk_task(session, team, **fields):
    task = Task(team_id=team.id, title=fields.pop("title", "T"), source="manual", **fields)
    session.add(task)
    session.flush()
    return task


# ---- the windows ----


@pytest.mark.parametrize(
    ("length", "days", "end"),
    [
        ("week5", 10, date(2026, 11, 6)),
        ("week7", 10, date(2026, 11, 8)),
        ("week14", 10, date(2026, 11, 15)),
        ("month1", 10, date(2026, 12, 1)),
        ("month3", 10, date(2027, 2, 1)),
        ("month6", 10, date(2027, 5, 1)),
        ("custom", 10, date(2026, 11, 11)),
        ("custom", 1, date(2026, 11, 2)),
        ("custom", 365, date(2027, 11, 1)),
    ],
)
def test_a_window_ends_after_its_length(length, days, end):
    assert sprints.window_end(length, days, MON) == end


def test_months_clamp_to_the_end_of_a_shorter_month():
    assert sprints.add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert sprints.window_end("month3", 10, date(2026, 11, 30)) == date(2027, 2, 27)


def test_an_aligned_five_day_sprint_opens_on_the_next_monday():
    friday = date(2026, 11, 6)
    assert sprints.next_start("week5", True, friday) == date(2026, 11, 9)
    assert sprints.next_start("week5", False, friday) == date(2026, 11, 7)
    assert sprints.next_start("week7", True, date(2026, 11, 8)) == date(2026, 11, 9)


def test_a_new_series_begins_on_this_weeks_monday_or_the_next_one_at_a_weekend():
    assert sprints.first_start("week5", True, date(2026, 11, 4)) == MON
    assert sprints.first_start("week5", True, date(2026, 11, 7)) == date(2026, 11, 9)
    assert sprints.first_start("week5", False, date(2026, 11, 4)) == date(2026, 11, 4)
    assert sprints.first_start("week14", True, date(2026, 11, 4)) == date(2026, 11, 4)


# ---- a series ----


def test_a_series_keeps_a_current_and_a_next_sprint_and_making_them_twice_changes_nothing(
    session, team
):
    first = sprints.ensure_series(session, team, None, date(2026, 11, 4))
    again = sprints.ensure_series(session, team, None, date(2026, 11, 20))
    assert [s.id for s in first] == [s.id for s in again]
    current, nxt = first
    assert (current.number, current.start_date, current.end_date) == (1, MON, date(2026, 11, 6))
    assert (nxt.number, nxt.start_date, nxt.end_date) == (2, date(2026, 11, 9), date(2026, 11, 13))
    assert sprints.name_of(current) == "Sprint 1"
    assert session.query(Sprint).count() == 2


def test_the_default_team_settings_are_one_week_five_days_aligned(team):
    assert (team.sprint_length, team.sprint_custom_days, team.sprint_align_weekdays) == (
        "week5",
        10,
        True,
    )


# ---- closing ----


def test_rollover_closes_a_sprint_whose_end_passed_and_carries_unfinished_tasks(session, team):
    current, nxt = sprints.ensure_series(session, team, None, date(2026, 11, 4))
    todo = mk_task(session, team, sprint_id=current.id, status="todo")
    doing = mk_task(session, team, sprint_id=current.id, status="doing")
    done = mk_task(session, team, sprint_id=current.id, status="done")
    gone = mk_task(session, team, sprint_id=current.id, deleted_at=NOW)
    session.commit()
    after_friday = datetime(2026, 11, 7, 9, 0, tzinfo=UTC)
    assert sprints.rollover_team(session, team, after_friday) == 1
    session.expire_all()
    assert session.get(Sprint, current.id).closed_at is not None
    assert session.get(Sprint, current.id).closed_early is False
    assert (todo.sprint_id, todo.carried) == (nxt.id, True)
    assert (doing.sprint_id, doing.carried) == (nxt.id, True)
    assert (done.sprint_id, done.carried) == (current.id, False)
    assert gone.sprint_id == current.id  # a deleted task is not moved
    opened = sprints.open_sprints(session, team.id, None)
    assert [s.number for s in opened] == [2, 3]
    assert opened[1].start_date == date(2026, 11, 16)


def test_rollover_does_nothing_on_the_last_day_and_twice_is_the_same_as_once(session, team):
    sprints.ensure_series(session, team, None, date(2026, 11, 4))
    session.commit()
    assert sprints.rollover_team(session, team, datetime(2026, 11, 6, 23, 0, tzinfo=UTC)) == 0
    over = datetime(2026, 11, 7, 0, 5, tzinfo=UTC)
    assert sprints.rollover_team(session, team, over) == 1
    assert sprints.rollover_team(session, team, over) == 0
    assert [s.number for s in sprints.open_sprints(session, team.id, None)] == [2, 3]


def test_rollover_catches_up_after_a_long_gap(session, team):
    sprints.ensure_series(session, team, None, date(2026, 11, 4))
    session.commit()
    late = datetime(2026, 12, 1, 9, 0, tzinfo=UTC)
    assert sprints.rollover_team(session, team, late) == 4
    current = sprints.open_sprints(session, team.id, None)[0]
    assert current.start_date <= date(2026, 12, 1) <= current.end_date


def test_closing_early_starts_the_next_sprint_today_and_keeps_its_end(session, team):
    current, nxt = sprints.ensure_series(session, team, None, date(2026, 11, 4))
    ends = nxt.end_date
    task = mk_task(session, team, sprint_id=current.id)
    moved = sprints.close_current(session, team, None, NOW, date(2026, 11, 4), early=True)
    session.commit()
    assert moved == 1
    assert (current.closed_early, nxt.start_date, nxt.end_date) == (True, date(2026, 11, 4), ends)
    assert (task.sprint_id, task.carried) == (nxt.id, True)
    assert sprints.open_sprints(session, team.id, None)[1].number == 3


def test_the_rollover_job_covers_every_team_and_survives_a_bad_one(session, team, monkeypatch):
    other = Team(owner_user_id=team.owner_user_id)
    session.add(other)
    session.commit()
    sprints.ensure_series(session, team, None, date(2026, 11, 4))
    sprints.ensure_series(session, other, None, date(2026, 11, 4))
    session.commit()
    real = sprints.rollover_team

    def flaky(s, t, now):
        if t.id == team.id:
            raise RuntimeError("boom")
        return real(s, t, now)

    monkeypatch.setattr(sprints, "rollover_team", flaky)
    assert sprints.rollover_all(session, datetime(2026, 11, 9, 9, 0, tzinfo=UTC)) == 1
    session.expire_all()
    assert len(sprints.open_sprints(session, other.id, None)) == 2
    assert [s.number for s in sprints.open_sprints(session, team.id, None)] == [1, 2]


# ---- filing by due date (ambiguity row 3) ----


def test_a_task_is_filed_by_its_due_date(session, team):
    current, nxt = sprints.ensure_series(session, team, None, date(2026, 11, 4))
    placer = sprints.Placer(session, team.id, date(2026, 11, 4))
    assert placer.sprint_for(None, None) is None
    assert placer.sprint_for(None, date(2026, 10, 1)) == current.id  # past due
    assert placer.sprint_for(None, current.end_date) == current.id
    assert placer.sprint_for(None, current.end_date + timedelta(days=1)) == nxt.id
    assert placer.sprint_for(None, date(2027, 6, 1)) == nxt.id


# ---- settings ----


def test_a_new_length_redraws_the_open_sprints_and_leaves_closed_ones(session, team):
    current, nxt = sprints.ensure_series(session, team, None, date(2026, 11, 4))
    sprints.close_current(session, team, None, NOW, date(2026, 11, 7), early=False)
    session.commit()
    opened = sprints.open_sprints(session, team.id, None)
    closed_end = current.end_date
    team.sprint_length = "week14"
    sprints.reflow(session, team)
    session.commit()
    assert current.end_date == closed_end
    assert opened[0].end_date == opened[0].start_date + timedelta(days=13)
    assert opened[1].start_date == opened[0].end_date + timedelta(days=1)
    assert opened[1].end_date == opened[1].start_date + timedelta(days=13)


# ---- a project's own length ----


def test_a_project_with_its_own_length_gets_its_own_series_and_its_tasks_follow(session, team):
    project = Project(team_id=team.id, name="P", color="#0E6B5E")
    session.add(project)
    session.flush()
    current, nxt = sprints.ensure_series(session, team, None, date(2026, 11, 4))
    mine = mk_task(session, team, project_id=project.id, sprint_id=current.id)
    later = mk_task(session, team, project_id=project.id, sprint_id=nxt.id)
    other = mk_task(session, team, sprint_id=current.id)
    sprints.set_project_length(session, team, project, "week14")
    session.commit()
    own = sprints.open_sprints(session, team.id, project.id)
    assert (own[0].end_date - own[0].start_date).days == 13
    assert (mine.sprint_id, later.sprint_id) == (own[0].id, own[1].id)
    assert other.sprint_id == current.id
    assert sprints.series_key(session, team.id, project.id) == project.id

    sprints.set_project_length(session, team, project, None)
    session.commit()
    assert (mine.sprint_id, later.sprint_id) == (current.id, nxt.id)
    assert sprints.open_sprints(session, team.id, project.id) == []


def test_a_task_moved_to_a_project_in_another_series_keeps_its_place_in_time(session, team):
    project = Project(team_id=team.id, name="P", color="#0E6B5E", sprint_length="week14")
    session.add(project)
    session.flush()
    current, nxt = sprints.ensure_series(session, team, None, date(2026, 11, 4))
    own = sprints.ensure_series(session, team, project.id, date(2026, 11, 4))
    task = mk_task(session, team, sprint_id=nxt.id)
    sprints.rehome(session, task, project.id)
    assert task.sprint_id == own[1].id
    sprints.rehome(session, task, None)
    assert task.sprint_id == nxt.id
    backlog = mk_task(session, team)
    sprints.rehome(session, backlog, project.id)
    assert backlog.sprint_id is None


def test_the_sprint_filter_reads_none_current_next_and_ids_of_the_team_only(session, team):
    current, nxt = sprints.ensure_series(session, team, None, date(2026, 11, 4))
    a = mk_task(session, team, sprint_id=current.id)
    b = mk_task(session, team, sprint_id=nxt.id)
    c = mk_task(session, team)
    session.commit()

    def ids(value):
        flt = sprints.sprint_filter(session, team.id, value)
        return {t.id for t in session.scalars(select(Task).where(flt))}

    assert ids("none") == {c.id}
    assert ids("current") == ids(str(current.id)) == {a.id}
    assert ids("next") == ids(str(nxt.id)) == {b.id}
    assert ids("999999") == set()
    assert sprints.sprint_filter(session, None, "current") is not None
