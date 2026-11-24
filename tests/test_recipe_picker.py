# ruff: noqa: F811
"""Choosing the meetings of a cross-meeting draft (`app/recipes/picker.py`, plan T15, spec 5.3b).

The cast: `owner` (owns the team and its meetings), `daniel` (a member of the owner's team, the
caller in most tests: what he cannot read must be left out and counted, never named) and
`outsider` (a team of their own)."""

from datetime import UTC, date, datetime, timedelta

import pytest
from pydantic import ValidationError

from app import teams
from app.models import Project, Series, Sprint, Task, TeamMember
from app.recipes import picker
from app.recipes.picker import PickerSpec
from app.services import Refused
from tests.test_api import ready  # noqa: F401
from tests.test_team_visibility import OTHER_EMAIL, outsider, share  # noqa: F401
from tests.test_web import make_user, owner, stranger, template  # noqa: F401

DANIEL_EMAIL = "daniel@example.test"
DAY0 = datetime(2026, 11, 2, 13, 0, tzinfo=UTC)  # a Monday


@pytest.fixture
def team(session, owner):
    return teams.ensure_team(session, owner)


@pytest.fixture
def daniel(session, owner, team):
    user = make_user(session, DANIEL_EMAIL, name="Daniel Okoro")
    session.add(TeamMember(team_id=team.id, user_id=user.id, role="member"))
    session.commit()
    return user


def meeting(session, template, n=0, *, shared=True, **fields):
    """A ready meeting of the template's owner, `n` days after DAY0, shared to the team unless
    `shared` is false (then it is the owner's private one)."""
    values = {"title": f"Meeting {n}", "created_at": DAY0 + timedelta(days=n)}
    found = ready(session, template, **{**values, **fields})
    if shared:
        found.shared_team_id = teams.team_of(session, template.owner_user_id).id
        session.commit()
    return found


@pytest.fixture
def weekly(session, owner, team):
    found = Series(team_id=team.id, owner_user_id=owner.id, title="Monday planning")
    session.add(found)
    session.commit()
    return found


def titles(resolved):
    return [m.title for m in resolved.meetings]


# ---- the spec names exactly one way of choosing ----


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"series_id": 1, "project_id": 2},
        {"sprint_id": 1, "meeting_ids": [3]},
        {"from_day": date(2026, 11, 1), "series_id": 1},
        {"meeting_ids": []},
    ],
)
def test_the_spec_names_exactly_one_way_of_choosing(fields):
    with pytest.raises(ValidationError):
        PickerSpec(**fields)


def test_either_end_of_a_date_range_is_enough():
    assert PickerSpec(from_day=date(2026, 11, 1)).from_day == date(2026, 11, 1)
    assert PickerSpec(to_day=date(2026, 11, 1)).to_day == date(2026, 11, 1)


# ---- a series ----


def test_a_series_resolves_to_its_own_meetings_newest_first(
    session, owner, daniel, template, weekly
):
    first = meeting(session, template, 0, series_id=weekly.id)
    last = meeting(session, template, 7, series_id=weekly.id)
    meeting(session, template, 3)  # not in the series
    resolved = picker.resolve_meetings(session, daniel, PickerSpec(series_id=weekly.id))
    assert [m.id for m in resolved.meetings] == [last.id, first.id]
    assert (resolved.left_out_count, resolved.capped) == (0, False)


def test_a_series_of_another_team_is_not_found(session, owner, outsider, template, weekly):
    with pytest.raises(Refused) as error:
        picker.resolve_meetings(session, outsider, PickerSpec(series_id=weekly.id))
    assert error.value.status == 404


# ---- the cap ----


def test_at_most_twelve_meetings_newest_first(session, owner, daniel, template, weekly):
    made = [meeting(session, template, n, series_id=weekly.id) for n in range(15)]
    resolved = picker.resolve_meetings(session, daniel, PickerSpec(series_id=weekly.id))
    assert picker.MAX_MEETINGS == 12
    assert [m.id for m in resolved.meetings] == [m.id for m in reversed(made)][:12]
    assert resolved.capped is True and resolved.left_out_count == 0


def test_exactly_twelve_is_not_a_cut(session, owner, daniel, template, weekly):
    for n in range(12):
        meeting(session, template, n, series_id=weekly.id)
    resolved = picker.resolve_meetings(session, daniel, PickerSpec(series_id=weekly.id))
    assert len(resolved.meetings) == 12 and resolved.capped is False


# ---- what the caller cannot read ----


def test_an_unreadable_meeting_is_left_out_and_counted(session, owner, daniel, template, weekly):
    shown = meeting(session, template, 0, series_id=weekly.id, title="Planning")
    meeting(session, template, 1, series_id=weekly.id, shared=False, title="Salary review")
    meeting(session, template, 2, series_id=weekly.id, shared=False, title="Layoffs")
    resolved = picker.resolve_meetings(session, daniel, PickerSpec(series_id=weekly.id))
    assert [m.id for m in resolved.meetings] == [shown.id]
    assert resolved.left_out_count == 2
    # the owner reads all three, so nothing is left out for them
    mine = picker.resolve_meetings(session, owner, PickerSpec(series_id=weekly.id))
    assert (len(mine.meetings), mine.left_out_count) == (3, 0)


def test_unreadable_meetings_do_not_use_up_the_cap(session, owner, daniel, template, weekly):
    for n in range(5):
        meeting(session, template, n + 20, series_id=weekly.id, shared=False)  # newest, hidden
    for n in range(12):
        meeting(session, template, n, series_id=weekly.id)
    resolved = picker.resolve_meetings(session, daniel, PickerSpec(series_id=weekly.id))
    assert len(resolved.meetings) == 12 and resolved.left_out_count == 5


def test_a_meeting_that_is_not_ready_is_not_offered(session, owner, daniel, template, weekly):
    meeting(session, template, 0, series_id=weekly.id)
    meeting(session, template, 1, series_id=weekly.id, status="processing", step="notes")
    meeting(
        session, template, 2, series_id=weekly.id, notes="", transcript="", speaker_transcript=""
    )
    resolved = picker.resolve_meetings(session, daniel, PickerSpec(series_id=weekly.id))
    assert len(resolved.meetings) == 1 and resolved.left_out_count == 0


# ---- hand-picked ----


def test_hand_picked_meetings_resolve_and_unreadable_ones_are_counted(
    session, owner, daniel, template
):
    a = meeting(session, template, 1)
    b = meeting(session, template, 2)
    hidden = meeting(session, template, 3, shared=False)
    spec = PickerSpec(meeting_ids=[a.id, hidden.id, b.id, a.id, 987654])
    resolved = picker.resolve_meetings(session, daniel, spec)
    assert [m.id for m in resolved.meetings] == [b.id, a.id]
    assert resolved.left_out_count == 2  # the hidden one and the one that does not exist


def test_more_than_twelve_hand_picked_are_cut_to_the_newest(session, owner, daniel, template):
    made = [meeting(session, template, n) for n in range(14)]
    resolved = picker.resolve_meetings(
        session, daniel, PickerSpec(meeting_ids=[m.id for m in made])
    )
    assert [m.id for m in resolved.meetings] == [m.id for m in reversed(made)][:12]
    assert resolved.capped is True


# ---- a project ----


def test_a_project_resolves_through_its_tasks_and_its_linked_series(
    session, owner, daniel, team, template, weekly
):
    project = Project(team_id=team.id, name="Onboarding", color="#0E6B5E")
    other = Project(team_id=team.id, name="Billing", color="#0E6B5E")
    session.add_all([project, other])
    session.flush()
    via_task = meeting(session, template, 1)
    via_series = meeting(session, template, 2, series_id=weekly.id)
    unrelated = meeting(session, template, 3)
    weekly.project_id = project.id
    session.add_all(
        [
            Task(
                team_id=team.id,
                meeting_id=via_task.id,
                title="A",
                source="manual",
                project_id=project.id,
            ),
            Task(
                team_id=team.id,
                meeting_id=unrelated.id,
                title="B",
                source="manual",
                project_id=other.id,
            ),
        ]
    )
    session.commit()
    resolved = picker.resolve_meetings(session, daniel, PickerSpec(project_id=project.id))
    assert [m.id for m in resolved.meetings] == [via_series.id, via_task.id]


def test_a_project_counts_what_the_caller_cannot_read(session, owner, daniel, team, template):
    project = Project(team_id=team.id, name="Onboarding", color="#0E6B5E")
    session.add(project)
    session.flush()
    shown = meeting(session, template, 1)
    hidden = meeting(session, template, 2, shared=False, title="Private chat")
    session.add_all(
        Task(team_id=team.id, meeting_id=m.id, title="T", source="manual", project_id=project.id)
        for m in (shown, hidden)
    )
    session.commit()
    resolved = picker.resolve_meetings(session, daniel, PickerSpec(project_id=project.id))
    assert [m.id for m in resolved.meetings] == [shown.id] and resolved.left_out_count == 1


def test_a_project_of_another_team_is_not_found(session, owner, outsider, team):
    project = Project(team_id=team.id, name="Onboarding", color="#0E6B5E")
    session.add(project)
    session.commit()
    with pytest.raises(Refused) as error:
        picker.resolve_meetings(session, outsider, PickerSpec(project_id=project.id))
    assert error.value.status == 404


# ---- a sprint ----


def test_a_sprint_resolves_through_its_tasks_and_its_days(session, owner, daniel, team, template):
    sprint = Sprint(
        team_id=team.id, number=1, start_date=date(2026, 11, 2), end_date=date(2026, 11, 6)
    )
    session.add(sprint)
    session.flush()
    planned = meeting(session, template, -20)  # long before, but a task is planned in the sprint
    inside = meeting(session, template, 2)  # held on the 4th
    outside = meeting(session, template, 10)
    session.add(
        Task(
            team_id=team.id, meeting_id=planned.id, title="A", source="manual", sprint_id=sprint.id
        )
    )
    session.commit()
    resolved = picker.resolve_meetings(session, daniel, PickerSpec(sprint_id=sprint.id))
    assert [m.id for m in resolved.meetings] == [inside.id, planned.id]
    assert outside.id not in [m.id for m in resolved.meetings]


def test_a_sprint_of_another_team_is_not_found(session, owner, outsider, team):
    sprint = Sprint(
        team_id=team.id, number=1, start_date=date(2026, 11, 2), end_date=date(2026, 11, 6)
    )
    session.add(sprint)
    session.commit()
    with pytest.raises(Refused) as error:
        picker.resolve_meetings(session, outsider, PickerSpec(sprint_id=sprint.id))
    assert error.value.status == 404


# ---- a date range ----


def test_a_date_range_keeps_the_meetings_of_those_days_both_ends_included(
    session, owner, daniel, template
):
    before = meeting(session, template, -1)
    first = meeting(session, template, 0)
    last = meeting(session, template, 3)
    after = meeting(session, template, 4)
    spec = PickerSpec(from_day=date(2026, 11, 2), to_day=date(2026, 11, 5))
    resolved = picker.resolve_meetings(session, daniel, spec)
    assert [m.id for m in resolved.meetings] == [last.id, first.id]
    assert before.id not in [m.id for m in resolved.meetings]
    assert after.id not in [m.id for m in resolved.meetings]


def test_a_date_range_follows_the_active_team(session, owner, daniel, team, template):
    """As the Meetings list does: the active team's meetings only, and only those readable."""
    from app.models import Team

    mine = make_user(session, "solo@example.test")
    personal = teams.ensure_team(session, mine)
    session.add(TeamMember(team_id=team.id, user_id=mine.id, role="member"))
    session.commit()
    shared = meeting(session, template, 1)
    private_of_mine = ready(
        session, template, owner_user_id=mine.id, title="Mine", created_at=DAY0 + timedelta(days=2)
    )
    spec = PickerSpec(from_day=date(2026, 11, 1), to_day=date(2026, 11, 30))
    in_team = picker.resolve_meetings(session, mine, spec, team_id=team.id)
    assert [m.id for m in in_team.meetings] == [shared.id]
    assert isinstance(session.get(Team, personal.id), Team)
    in_personal = picker.resolve_meetings(session, mine, spec, team_id=personal.id)
    assert [m.id for m in in_personal.meetings] == [private_of_mine.id]


def test_a_team_the_caller_is_not_in_is_not_found(session, owner, outsider, team, template):
    with pytest.raises(Refused) as error:
        picker.resolve_meetings(
            session, outsider, PickerSpec(from_day=date(2026, 11, 1)), team_id=team.id
        )
    assert error.value.status == 404
