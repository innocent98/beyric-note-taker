"""Multi-team stage 1: the teams module and the `MULTI_TEAM_ENABLED` switch.

Flag off (the default, and every account today): a second membership is refused exactly as before.
Flag on: joining adds a membership, leaving creates no team of one, and a personal team is never
left, never joined, never invited into. Also the schema rules the migration adds and the one
helper that picks the default team for routes that name none.
"""

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app import team_admin, team_invites, teams
from app.models import Meeting, Task, Team, TeamInvite, TeamMember
from app.services import Refused
from tests.test_api import owner, ready, template  # noqa: F401
from tests.test_web import make_settings, make_user

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)


@pytest.fixture
def flag_on(monkeypatch):
    monkeypatch.setattr(teams, "multi_team_enabled", lambda: True)


def person(session, email) -> object:
    user = make_user(session, email)
    teams.ensure_personal_team(session, user)
    session.commit()
    return user


def ordinary_team(session, boss, *members, name="Team") -> Team:
    team = Team(owner_user_id=boss.id, name=name, is_personal=False)
    session.add(team)
    session.flush()
    session.add(TeamMember(team_id=team.id, user_id=boss.id, role="owner"))
    for user in members:
        session.add(TeamMember(team_id=team.id, user_id=user.id, role="member"))
    session.commit()
    return team


def team_ids(session, user) -> list[int]:
    return sorted(
        session.scalars(select(TeamMember.team_id).where(TeamMember.user_id == user.id)).all()
    )


def put_task(session, team, title="Task", **fields) -> Task:
    task = Task(
        team_id=team.id,
        title=title,
        source="manual",
        assigned_on=date(2026, 11, 13),
        **fields,
    )
    session.add(task)
    session.commit()
    return task


# ---- the switch ----


def test_the_switch_is_off_by_default():
    assert make_settings().multi_team_enabled is False
    assert teams.multi_team_enabled() is False


# ---- flag off: today's behaviour, exactly ----


def test_off_a_person_on_a_team_with_others_cannot_join_another(session, owner):
    ada, bola, chi = (
        owner,
        person(session, "bola@example.test"),
        person(session, "chi@example.test"),
    )
    first = ordinary_team(session, ada, bola)
    second = ordinary_team(session, chi)
    with pytest.raises(teams.TeamChangeRefused) as caught:
        teams.join(session, bola, second.id, "member", None, NOW)
    assert (caught.value.status, caught.value.code) == (409, "already_on_team")
    assert first.id in team_ids(session, bola) and second.id not in team_ids(session, bola)


def test_off_a_solo_ordinary_team_is_dissolved_into_the_team_joined_and_the_personal_team_stays(
    session, owner
):
    chi, bola = person(session, "chi@example.test"), person(session, "bola@example.test")
    solo = ordinary_team(session, bola)  # her own team of one: the one she lives in today
    put_task(session, solo, "Mine")
    target = ordinary_team(session, chi)
    teams.join(session, bola, target.id, "member", None, NOW)
    session.commit()
    assert session.get(Team, solo.id) is None
    assert session.scalars(select(Task.team_id)).all() == [target.id]  # the task moved
    assert target.id in team_ids(session, bola)
    assert teams.personal_team(session, bola.id) is not None  # the personal one is not touched


def test_off_a_person_with_only_a_personal_team_dissolves_it_as_today(session, owner):
    chi, bola = person(session, "chi@example.test"), person(session, "bola@example.test")
    personal = teams.personal_team(session, bola.id)
    put_task(session, personal, "Mine")
    target = ordinary_team(session, chi)
    teams.join(session, bola, target.id, "member", None, NOW)
    session.commit()
    assert session.get(Team, personal.id) is None
    assert team_ids(session, bola) == [target.id]
    assert session.scalars(select(Task.team_id)).all() == [target.id]


def test_off_joining_a_personal_team_makes_it_an_ordinary_team_and_changes_nothing_else(
    session, owner
):
    """Today a person invites people into their team of one: that must keep working, and the team
    count must not move (the owner's new personal team is made later, when something needs it)."""
    ada, bola = person(session, "ada@example.test"), person(session, "bola@example.test")
    ada_personal = teams.personal_team(session, ada.id)
    before = session.scalar(select(func.count()).select_from(Team))
    teams.join(session, bola, ada_personal.id, "member", None, NOW)
    session.commit()
    session.refresh(ada_personal)
    assert ada_personal.is_personal is False
    assert session.scalar(select(func.count()).select_from(Team)) == before - 1  # Bola's own went
    assert teams.personal_team(session, ada.id) is None
    assert teams.team_of(session, ada.id).id == ada_personal.id  # still lands on the team she runs
    assert teams.team_of(session, bola.id).id == ada_personal.id
    assert teams.ensure_personal_team(session, ada).is_personal  # made when something needs it
    assert teams.team_of(session, ada.id).id == ada_personal.id  # and she still lands on her team


# ---- flag on: a second membership ----


def test_on_joining_adds_a_membership_and_dissolves_nothing(session, owner, flag_on):
    chi, bola = person(session, "chi@example.test"), person(session, "bola@example.test")
    mine = ordinary_team(session, bola, name="Mine")
    personal = teams.personal_team(session, bola.id)
    put_task(session, personal, "In personal")
    put_task(session, mine, "In mine")
    target = ordinary_team(session, chi)
    member = teams.join(session, bola, target.id, "member", "Bo", NOW)
    session.commit()
    assert member.aliases == ["Bo"]
    assert team_ids(session, bola) == sorted([personal.id, mine.id, target.id])
    assert {t.title: t.team_id for t in session.scalars(select(Task))} == {
        "In personal": personal.id,
        "In mine": mine.id,
    }  # nothing was moved


def test_on_a_person_on_a_team_with_others_may_join_another(session, owner, flag_on):
    ada, bola, chi = (
        owner,
        person(session, "bola@example.test"),
        person(session, "chi@example.test"),
    )
    ordinary_team(session, ada, bola)
    second = ordinary_team(session, chi)
    teams.join(session, bola, second.id, "member", None, NOW)  # no 409 `already_on_team`
    session.commit()
    assert second.id in team_ids(session, bola)


def test_on_joining_the_same_team_twice_keeps_one_membership(session, owner, flag_on):
    chi, bola = person(session, "chi@example.test"), person(session, "bola@example.test")
    target = ordinary_team(session, chi)
    first = teams.join(session, bola, target.id, "member", None, NOW)
    again = teams.join(session, bola, target.id, "admin", None, NOW)
    session.commit()
    assert again.id == first.id and again.role == "member"
    assert (
        session.scalar(
            select(func.count()).select_from(TeamMember).where(TeamMember.team_id == target.id)
        )
        == 2
    )


def test_on_a_person_with_no_personal_team_gets_one_when_they_join(session, owner, flag_on):
    """A flag-off join dissolved it; the switch being turned on mends that."""
    chi, bola = person(session, "chi@example.test"), make_user(session, "bola@example.test")
    target = ordinary_team(session, chi)
    teams.join(session, bola, target.id, "member", None, NOW)
    session.commit()
    assert teams.personal_team(session, bola.id) is not None


def test_on_nobody_joins_a_personal_team(session, owner, flag_on):
    ada, bola = person(session, "ada@example.test"), person(session, "bola@example.test")
    personal = teams.personal_team(session, ada.id)
    with pytest.raises(teams.TeamChangeRefused) as caught:
        teams.join(session, bola, personal.id, "member", None, NOW)
    assert caught.value.status == 409 and caught.value.code == "personal_team"
    assert team_ids(session, bola) == [teams.personal_team(session, bola.id).id]


def test_on_nobody_is_invited_into_a_personal_team(session, owner, flag_on):
    ada = person(session, "ada@example.test")
    personal = teams.personal_team(session, ada.id)
    with pytest.raises(Refused) as caught:
        team_invites.create(
            session,
            make_settings(),
            personal.id,
            ada,
            email="bola@example.test",
            name=None,
            role="member",
            now=NOW,
            has_room=True,
        )
    assert (caught.value.status, caught.value.code) == (409, "personal_team")
    assert session.scalar(select(func.count()).select_from(TeamInvite)) == 0


def test_off_inviting_from_a_team_of_one_still_works(session, owner):
    ada = person(session, "ada@example.test")
    personal = teams.personal_team(session, ada.id)
    result = team_invites.create(
        session,
        make_settings(),
        personal.id,
        ada,
        email="bola@example.test",
        name=None,
        role="member",
        now=NOW,
        has_room=True,
    )
    assert result.status == "sent"


# ---- leaving and being removed ----


@pytest.mark.parametrize("on", [False, True])
def test_leaving_creates_no_team_of_one_for_someone_who_has_a_personal_team(
    session, owner, monkeypatch, on
):
    monkeypatch.setattr(teams, "multi_team_enabled", lambda: on)
    ada, bola = person(session, "ada@example.test"), person(session, "bola@example.test")
    joint = ordinary_team(session, ada, bola)
    before = session.scalar(select(func.count()).select_from(Team))
    personal = teams.personal_team(session, bola.id)
    back = teams.leave(session, bola, NOW)
    session.commit()
    assert back.id == personal.id
    assert session.scalar(select(func.count()).select_from(Team)) == before
    assert team_ids(session, bola) == [personal.id]
    assert joint.id not in team_ids(session, bola)


def test_leaving_unshares_what_was_shared_and_unassigns_open_tasks(session, owner, template):
    ada, bola = person(session, "ada@example.test"), person(session, "bola@example.test")
    joint = ordinary_team(session, ada, bola)
    meeting = ready(session, template, owner_user_id=bola.id, shared_team_id=joint.id)
    open_task = put_task(session, joint, "Open", assignee_user_id=bola.id)
    teams.leave(session, bola, NOW)
    session.commit()
    session.refresh(meeting)
    session.refresh(open_task)
    assert meeting.shared_team_id is None and open_task.assignee_user_id is None


def test_leaving_mends_a_person_who_has_no_personal_team(session, owner):
    """A flag-off join dissolved theirs: leaving today gives a fresh team of one; it still does."""
    ada, bola = person(session, "ada@example.test"), make_user(session, "bola@example.test")
    joint = ordinary_team(session, ada, bola)
    assert teams.personal_team(session, bola.id) is None
    back = teams.leave(session, bola, NOW)
    session.commit()
    assert back.is_personal and back.owner_user_id == bola.id
    assert team_ids(session, bola) == [back.id] and joint.id not in team_ids(session, bola)


def test_leaving_one_of_two_ordinary_teams_leaves_the_other_and_the_personal_team(
    session, owner, flag_on
):
    ada, chi, bola = (
        person(session, "ada@example.test"),
        person(session, "chi@example.test"),
        person(session, "bola@example.test"),
    )
    one, two = ordinary_team(session, ada, bola), ordinary_team(session, chi, bola)
    teams.leave(session, bola, NOW, team_id=one.id)
    session.commit()
    assert team_ids(session, bola) == sorted([two.id, teams.personal_team(session, bola.id).id])


def test_a_personal_team_cannot_be_left(session, owner):
    ada = person(session, "ada@example.test")
    personal = teams.personal_team(session, ada.id)
    with pytest.raises(teams.TeamChangeRefused) as caught:
        teams.leave(session, ada, NOW, team_id=personal.id)
    assert (caught.value.status, caught.value.code) == (409, "personal_team")
    assert team_ids(session, ada) == [personal.id]


def test_the_owner_of_a_personal_team_cannot_be_removed_from_it(session, owner):
    ada = person(session, "ada@example.test")
    me = team_admin.my_membership(session, ada)
    with pytest.raises(Refused) as caught:
        team_admin.remove_member(session, me, me, NOW)
    assert caught.value.status == 409
    assert team_ids(session, ada) == [me.team_id]


def test_removing_a_member_gives_them_no_new_team(session, owner):
    ada, bola = person(session, "ada@example.test"), person(session, "bola@example.test")
    joint = ordinary_team(session, ada, bola)
    me = session.scalars(
        select(TeamMember).where(TeamMember.team_id == joint.id, TeamMember.user_id == ada.id)
    ).one()
    target = session.scalars(
        select(TeamMember).where(TeamMember.team_id == joint.id, TeamMember.user_id == bola.id)
    ).one()
    before = session.scalar(select(func.count()).select_from(Team))
    team_admin.remove_member(session, me, target, NOW)
    session.commit()
    assert session.scalar(select(func.count()).select_from(Team)) == before
    assert team_ids(session, bola) == [teams.personal_team(session, bola.id).id]


def test_deleting_accounts_works_for_people_in_several_teams(session, owner):
    ada, bola = person(session, "ada@example.test"), person(session, "bola@example.test")
    joint = ordinary_team(session, ada, bola)
    teams.remove_users(session, [ada.id])
    session.commit()
    assert session.get(Team, joint.id).owner_user_id == bola.id  # handed to the one who stayed
    assert team_ids(session, ada) == []
    assert len(team_ids(session, bola)) == 2


# ---- the default team of a route that names none ----


def test_the_default_team_is_the_single_ordinary_team_else_the_personal_one(session, owner):
    bola = person(session, "bola@example.test")
    personal = teams.personal_team(session, bola.id)
    assert teams.team_of(session, bola.id).id == personal.id  # no ordinary team
    one = ordinary_team(session, person(session, "ada@example.test"), bola)
    assert teams.team_of(session, bola.id).id == one.id  # exactly one: what an old client sees
    ordinary_team(session, person(session, "chi@example.test"), bola)
    assert teams.team_of(session, bola.id).id == personal.id  # two: the personal one
    assert teams.default_membership(session, bola.id).team_id == personal.id


def test_a_single_membership_in_a_team_flagged_personal_is_still_their_team(session, owner):
    """Hand-built fixtures (and any flag-off joiner) sit in a team that is another person's
    personal team: with one membership that is their team, whatever the flag says."""
    ada = person(session, "ada@example.test")
    bola = make_user(session, "bola@example.test")
    ada_team = teams.personal_team(session, ada.id)
    session.add(TeamMember(team_id=ada_team.id, user_id=bola.id, role="member"))
    session.commit()
    assert teams.team_of(session, bola.id).id == ada_team.id
    assert teams.ensure_team(session, bola).id == ada_team.id


def test_a_person_with_no_team_at_all_gets_a_personal_one_from_ensure_team(session, owner):
    bola = make_user(session, "bola@example.test")
    assert teams.team_of(session, bola.id) is None
    made = teams.ensure_team(session, bola)
    assert made.is_personal and made.owner_user_id == bola.id
    assert teams.ensure_team(session, bola).id == made.id


# ---- the schema rules ----


def test_a_person_may_be_in_two_teams_but_not_twice_in_one(session, owner):
    ada, bola = person(session, "ada@example.test"), person(session, "bola@example.test")
    joint = ordinary_team(session, ada, bola)
    session.add(TeamMember(team_id=joint.id, user_id=bola.id, role="admin"))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_a_person_has_at_most_one_personal_team(session, owner):
    ada = person(session, "ada@example.test")
    session.add(Team(owner_user_id=ada.id, is_personal=True))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()
    session.add(Team(owner_user_id=ada.id, is_personal=False))  # ordinary teams are unlimited here
    session.commit()


def test_a_meeting_can_name_its_owning_team_and_loses_it_when_the_team_goes(
    session, owner, template
):
    ada = person(session, "ada@example.test")
    team = ordinary_team(session, ada)
    meeting = ready(session, template, owner_team_id=team.id)
    teams.remove_users(session, [ada.id])
    session.commit()
    session.refresh(meeting)
    assert meeting.owner_team_id is None
    assert session.get(Meeting, meeting.id) is not None
