"""Decision D4 (workspaces design, section 2 and 12.1): a meeting BORN in a team workspace is shared
with that workspace by default, readable by every member; "keep private" is the existing unshare;
the personal workspace stays private; existing meetings are never changed by it.

Cast: Ada owns team X, Pat is a plain member of it, Quinn is an admin. Team W was made before the
cutover with "share new meetings" off (its stored value is kept).
"""

# ruff: noqa: F811  (fixtures are imported by name and used as arguments)
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app import accounts, teams
from app.models import Meeting, Team, TeamMember, Template
from app.placement import PlacementRequest, place_bot
from tests.test_api import (  # noqa: F401  (fixtures used by name)
    anon,
    anon_client,
    api,
    app,
    client,
    error_of,
    fake_bot,
    fake_openai,
    owner,
    ready,
    template,
)
from tests.test_team_visibility import sign_in
from tests.test_web import make_settings, make_user

ADA, PAT, QUINN = "owner@example.test", "pat@example.test", "quinn@example.test"
LINK = "https://meet.google.com/abc-defg-hij"


class Cast:
    pass


@pytest.fixture
def cast(session, template, owner) -> Cast:
    c = Cast()
    c.ada = owner
    c.pat, c.quinn = make_user(session, PAT), make_user(session, QUINN)
    for user in (c.pat, c.quinn):
        accounts.create_user_defaults(session, user)
    c.x = teams.create_owned_team(session, c.ada, "X")
    # a team from before the cutover: it keeps the value it had (off)
    c.w = Team(owner_user_id=c.ada.id, name="W", is_personal=False, share_new_meetings=False)
    session.add(c.w)
    session.flush()
    session.add(TeamMember(team_id=c.w.id, user_id=c.ada.id, role="owner"))
    session.add(Template(owner_user_id=c.ada.id, team_id=c.w.id, name="G", instructions="g"))
    session.add_all(
        [
            TeamMember(team_id=c.x.id, user_id=c.pat.id, role="member"),
            TeamMember(team_id=c.x.id, user_id=c.quinn.id, role="admin"),
            TeamMember(team_id=c.w.id, user_id=c.pat.id, role="member"),
        ]
    )
    session.commit()
    return c


def record(handle, team, template_id):
    """Send the bot to a pasted link into `team` (the header); returns the meeting id."""
    response = handle.client.post(
        "/api/meetings",
        json={"url": LINK, "template_id": template_id},
        headers={"X-Team-Id": str(team.id), "X-CSRF-Token": handle.csrf},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def template_in(session, team):
    return session.scalars(
        select(Template).where(Template.team_id == team.id, Template.deleted_at.is_(None))
    ).first()


def test_a_meeting_born_in_a_team_workspace_is_shared_with_it_from_birth(
    app, session, cast, fake_bot
):
    ada = sign_in(app, ADA)
    meeting_id = record(ada, cast.x, template_in(session, cast.x).id)
    row = session.get(Meeting, meeting_id)
    assert (row.workspace_team_id, row.shared_team_id) == (cast.x.id, cast.x.id)
    # a member reads it at once, with the bot still in the meeting
    pat = sign_in(app, PAT)
    assert pat.get(f"/api/meetings/{meeting_id}").status_code == 200


def test_a_member_recording_into_a_team_workspace_shares_it_the_same_way(
    app, session, cast, fake_bot
):
    pat = sign_in(app, PAT)
    meeting_id = record(pat, cast.x, template_in(session, cast.x).id)
    row = session.get(Meeting, meeting_id)
    assert row.owner_user_id == cast.pat.id
    assert (row.workspace_team_id, row.shared_team_id) == (cast.x.id, cast.x.id)
    assert sign_in(app, ADA).get(f"/api/meetings/{meeting_id}").status_code == 200
    assert sign_in(app, QUINN).get(f"/api/meetings/{meeting_id}").status_code == 200


def test_the_personal_workspace_stays_private(app, session, cast, fake_bot):
    pat = sign_in(app, PAT)
    personal = teams.personal_team(session, cast.pat.id)
    meeting_id = record(pat, personal, template_in(session, personal).id)
    row = session.get(Meeting, meeting_id)
    assert (row.workspace_team_id, row.shared_team_id) == (personal.id, None)


def test_a_team_that_switched_sharing_off_keeps_its_meetings_private_at_birth(
    app, session, cast, fake_bot
):
    """An existing team kept the value it had: W was made with sharing off and stays that way."""
    assert session.get(Team, cast.w.id).share_new_meetings is False
    pat = sign_in(app, PAT)
    meeting_id = record(pat, cast.w, template_in(session, cast.w).id)
    row = session.get(Meeting, meeting_id)
    assert (row.workspace_team_id, row.shared_team_id) == (cast.w.id, None)


def test_existing_meetings_are_never_changed_by_the_new_default(
    app, session, cast, template, fake_bot
):
    """A private meeting filed in X before stays private when new ones are born shared, and a team
    switching the default on or off later does not touch any meeting already made."""
    old = ready(session, template, workspace_team_id=cast.x.id)  # Ada's, private, in X
    assert old.shared_team_id is None
    ada = sign_in(app, ADA)
    new_id = record(ada, cast.x, template_in(session, cast.x).id)
    session.expire_all()
    assert session.get(Meeting, old.id).shared_team_id is None
    assert session.get(Meeting, new_id).shared_team_id == cast.x.id
    # switching the default off now leaves the shared one shared
    patched = ada.client.patch(
        "/api/team",
        json={"share_new_meetings": False},
        headers={"X-Team-Id": str(cast.x.id), "X-CSRF-Token": ada.csrf},
    )
    assert patched.status_code == 200
    session.expire_all()
    assert session.get(Meeting, new_id).shared_team_id == cast.x.id
    assert session.get(Meeting, old.id).shared_team_id is None


def test_keep_private_is_the_existing_unshare_and_nothing_shares_it_again(
    app, session, cast, fake_bot
):
    ada = sign_in(app, ADA)
    meeting_id = record(ada, cast.x, template_in(session, cast.x).id)
    gone = ada.delete(f"/api/meetings/{meeting_id}/share")
    assert gone.status_code in (200, 204), gone.text
    session.expire_all()
    row = session.get(Meeting, meeting_id)
    assert row.shared_team_id is None and row.kept_private is True
    assert row.workspace_team_id == cast.x.id  # still filed in X: readable by Ada only
    # the first notes are written later: the team default must not publish it again
    teams.apply_share_default(session, row)
    assert row.shared_team_id is None
    assert sign_in(app, PAT).get(f"/api/meetings/{meeting_id}").status_code == 404
    # sharing again by hand is the recorder's choice and clears the mark
    again = ada.post(f"/api/meetings/{meeting_id}/share", {"team_id": cast.x.id})
    assert again.status_code in (200, 204), again.text
    session.expire_all()
    assert session.get(Meeting, meeting_id).kept_private is False


def test_a_calendar_join_into_a_team_workspace_is_shared_too(session, cast, fake_bot):
    """Every way a meeting is born goes through the placement: a calendar join as well."""
    template = template_in(session, cast.x)
    request = PlacementRequest(
        user_id=cast.pat.id,
        platform="google_meet",
        native_id="abc-defg-hij",
        meeting_url=LINK,
        template_id=template.id,
        source="calendar",
        workspace_team_id=cast.x.id,
    )
    meeting = place_bot(session, fake_bot, request, now=datetime.now(UTC), settings=make_settings())
    assert (meeting.workspace_team_id, meeting.shared_team_id) == (cast.x.id, cast.x.id)


def test_a_person_who_is_not_in_the_team_never_shares_into_it(session, cast):
    stranger = make_user(session, "zed@example.test")
    accounts.create_user_defaults(session, stranger)
    assert teams.birth_share_team_id(session, stranger, cast.x.id) is None
    assert teams.birth_share_team_id(session, cast.pat, cast.x.id) == cast.x.id
    assert (
        teams.birth_share_team_id(session, cast.pat, teams.personal_team(session, cast.pat.id).id)
        is None
    )
