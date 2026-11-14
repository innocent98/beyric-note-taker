"""Multi-team stage 1 with the switch OFF, on the shape the migration really leaves behind.

After migration 0043 every member of a team T holds T plus a personal team P of their own (the
backfill made P for people who sit in a team with others). With `MULTI_TEAM_ENABLED` off the app
must behave as it did: the team routes see T, leaving lands in the existing P (no new team of one),
a second invitation is refused with `already_on_team`, and two accepts at once cannot give anyone
two teams. Nothing here turns the switch on.
"""

# ruff: noqa: F811  (fixtures are imported by name and used as arguments)
import threading
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import team_invites, teams
from app.models import Team, TeamInvite, TeamMember, User
from app.services import Refused
from tests.test_api import (  # noqa: F401  (fixtures used by name)
    anon,
    anon_client,
    api,
    app,
    client,
    error_of,
    fake_openai,
    fake_vexa,
    owner,
    template,
)
from tests.test_multi_team_rules import ordinary_team, person, team_ids
from tests.test_team import accept, invite, link_token
from tests.test_team_visibility import sign_in
from tests.test_web import make_settings, make_user

BOLA, DAYO = "bola@example.test", "dayo@example.test"


@pytest.fixture
def shape(session, owner):
    """T owned by Ada (the `owner` account) with Bola and Chi in it; all three also own a personal
    team; Dayo runs another team U with a member, so U is a team with others."""
    ada = owner
    teams.ensure_personal_team(session, ada)
    bola, chi = person(session, BOLA), person(session, "chi@example.test")
    dayo, eze = person(session, DAYO), person(session, "eze@example.test")
    t = ordinary_team(session, ada, bola, chi, name="T")
    u = ordinary_team(session, dayo, eze, name="U")
    session.commit()
    return type("Shape", (), {"ada": ada, "bola": bola, "chi": chi, "dayo": dayo, "t": t, "u": u})


def test_off_the_team_routes_see_the_team_not_the_personal_one(app, session, shape):
    bola = sign_in(app, BOLA)
    got = bola.get("/api/team").json()
    assert (got["id"], got["name"], got["my_role"], got["member_count"]) == (
        shape.t.id,
        "T",
        "member",
        3,
    )
    members = bola.get("/api/team/members").json()["members"]
    assert len(members) == 3  # the personal team's one member is not mixed in


def test_off_leaving_lands_in_the_existing_personal_team_and_makes_no_team(app, session, shape):
    personal = teams.personal_team(session, shape.bola.id)
    before = session.scalar(select(func.count()).select_from(Team))
    bola = sign_in(app, BOLA)
    assert bola.delete(f"/api/team/members/{shape.bola.id}").status_code == 204
    session.expire_all()
    assert team_ids(session, shape.bola) == [personal.id]
    assert session.scalar(select(func.count()).select_from(Team)) == before
    after = bola.get("/api/team").json()
    assert (after["id"], after["my_role"], after["member_count"]) == (personal.id, "owner", 1)


def test_off_a_second_invitation_is_still_refused_with_already_on_team(app, session, shape):
    dayo = sign_in(app, DAYO)
    assert invite(dayo, BOLA).status_code == 200
    bola = sign_in(app, BOLA)
    error_of(accept(bola, link_token(session, BOLA)), 409, "already_on_team")
    session.expire_all()
    assert session.scalars(select(TeamInvite)).one().accepted_at is None  # not used up
    assert shape.t.id in team_ids(session, shape.bola) and shape.u.id not in team_ids(
        session, shape.bola
    )


def test_off_two_accepts_at_once_leave_a_person_in_exactly_one_new_team(engine, session, owner):
    """Someone with only a personal team gets two invitations and accepts both at the same moment:
    one wins, the other is refused (`already_on_team`), and they never end in two teams."""
    settings, now = make_settings(), datetime.now(UTC)
    solo = person(session, "solo@example.test")
    one = ordinary_team(session, person(session, "boss1@example.test"), name="One")
    two = ordinary_team(session, person(session, "boss2@example.test"), name="Two")
    # a team of one is not "with others": give each a second member so the refusal applies
    for team_ in (one, two):
        session.add(
            TeamMember(
                team_id=team_.id,
                user_id=make_user(session, f"m{team_.id}@e.test").id,
                role="member",
            )
        )
    session.commit()
    ids = []
    for team_ in (one, two):
        boss = session.get(User, team_.owner_user_id)
        result = team_invites.create(
            session, settings, team_.id, boss,
            email="solo@example.test", name=None, role="member", now=now, has_room=True,
        )  # fmt: skip
        session.commit()
        ids.append(result.invite.id)
    outcomes: list[str] = []
    gate = threading.Barrier(2)

    def accept_one(invite_id: int) -> None:
        with Session(engine) as mine:
            user, found = mine.get(User, solo.id), mine.get(TeamInvite, invite_id)
            gate.wait()
            try:
                team_invites.accept(mine, user, found, now)
                mine.commit()
                outcomes.append("joined")
            except Refused as exc:
                mine.rollback()
                outcomes.append(exc.code)

    threads = [threading.Thread(target=accept_one, args=(i,)) for i in ids]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert sorted(outcomes) == ["already_on_team", "joined"], outcomes
    session.expire_all()
    mine_now = team_ids(session, solo)
    assert len(mine_now) == 1 and mine_now[0] in (one.id, two.id)
