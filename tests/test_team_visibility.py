"""W0.4: a team of one per account, and the one rule for who may read which meeting.

`visible_meetings(user)` is the only way a read learns what a caller may see: their own meetings
plus those shared with their team. Three accounts are used throughout: the owner of the meetings,
a teammate (same team, meetings private unless shared) and an outsider (another team entirely).
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import accounts, teams, visibility
from app.models import (
    Decision,
    Meeting,
    MeetingTranslation,
    Team,
    TeamMember,
    Template,
)
from tests.test_api import (  # noqa: F401  (fixtures used by name)
    Api,
    anon,
    api,
    api_routes,
    error_of,
    ready,
)
from tests.test_web import (  # noqa: F401
    OTHER_EMAIL,
    PASSWORD,
    anon_client,
    app,
    fake_openai,
    fake_vexa,
    make_user,
    owner,
    stranger,
    template,
)

TEAMMATE_EMAIL = "teammate@example.test"


def sign_in(app, email) -> Api:
    client = TestClient(app, follow_redirects=False)
    client.__enter__()
    handle = Api(client)
    assert handle.log_in(email=email).status_code == 200
    return handle


@pytest.fixture
def teammate(session, owner):
    """Joined to the owner's team by hand (invites are W3). Their own team of one is gone."""
    user = make_user(session, TEAMMATE_EMAIL)
    team = teams.ensure_team(session, owner)
    session.add(TeamMember(team_id=team.id, user_id=user.id, role="member"))
    session.commit()
    return user


@pytest.fixture
def outsider(session, stranger):
    teams.ensure_team(session, stranger)
    session.commit()
    return stranger


def share(session, meeting, owner_user) -> None:
    meeting.shared_team_id = teams.team_of(session, owner_user.id).id
    session.commit()


def ids_of(session, user) -> list[int]:
    return [m.id for m in session.scalars(visibility.visible_meetings(user))]


# ---- a team of one ----


def test_new_accounts_get_a_team_of_one_with_themselves_as_owner(session):
    user = make_user(session, "new@example.test")
    accounts.add_user_defaults(session, user)
    session.commit()
    team = teams.team_of(session, user.id)
    assert team is not None and team.owner_user_id == user.id
    member = session.scalars(select(TeamMember).where(TeamMember.user_id == user.id)).one()
    assert (member.team_id, member.role, member.aliases) == (team.id, "owner", [])


def test_ensure_team_is_idempotent(session):
    user = make_user(session, "again@example.test")
    first = teams.ensure_team(session, user)
    assert teams.ensure_team(session, user).id == first.id
    session.commit()
    assert session.scalars(select(Team)).all() == [first]


def test_a_user_cannot_be_in_two_teams(session, owner, stranger):
    from sqlalchemy.exc import IntegrityError

    one, two = teams.ensure_team(session, owner), teams.ensure_team(session, stranger)
    session.commit()
    session.add(TeamMember(team_id=two.id, user_id=owner.id, role="member"))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()
    assert one.id != two.id


def test_a_role_outside_owner_admin_member_is_refused(session, owner):
    from sqlalchemy.exc import IntegrityError

    team = teams.ensure_team(session, owner)
    session.commit()
    other = make_user(session, "x@example.test")
    session.add(TeamMember(team_id=team.id, user_id=other.id, role="boss"))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_removing_users_drops_their_empty_team_and_hands_a_shared_one_on(session, owner, teammate):
    own_team = teams.team_of(session, owner.id)
    teams.remove_users(session, [owner.id])
    session.commit()
    assert session.get(Team, own_team.id).owner_user_id == teammate.id  # not blocked, not lost
    assert session.scalars(select(TeamMember.role)).all() == ["owner"]
    teams.remove_users(session, [teammate.id])
    session.commit()
    assert session.scalars(select(Team)).all() == []


# ---- visible_meetings ----


def test_a_meeting_is_private_by_default(session, template, owner, teammate, outsider):
    meeting = ready(session, template)
    assert meeting.shared_team_id is None
    assert ids_of(session, owner) == [meeting.id]
    assert ids_of(session, teammate) == []
    assert ids_of(session, outsider) == []


def test_a_shared_meeting_is_visible_to_the_team_and_only_the_team(
    session, template, owner, teammate, outsider
):
    meeting = ready(session, template)
    share(session, meeting, owner)
    assert ids_of(session, owner) == [meeting.id]
    assert ids_of(session, teammate) == [meeting.id]
    assert ids_of(session, outsider) == []


def test_an_account_with_no_team_sees_only_its_own_meetings(session, template, owner):
    """Tests and half-made accounts have no team row: no team means nothing shared reaches them,
    and the null never matches a meeting's null `shared_team_id`."""
    solo = make_user(session, "solo@example.test")
    ready(session, template)  # private, someone else's
    assert ids_of(session, solo) == []


def test_the_platform_admin_has_no_route_into_team_content(session, template, owner, teammate):
    admin = make_user(session, "admin@example.test", admin=True)
    teams.ensure_team(session, admin)
    session.commit()
    meeting = ready(session, template)
    share(session, meeting, owner)
    assert ids_of(session, admin) == []


def test_a_user_who_left_sees_nothing_more(session, template, owner, teammate):
    meeting = ready(session, template)
    share(session, meeting, owner)
    session.query(TeamMember).filter_by(user_id=teammate.id).delete()
    session.commit()
    assert ids_of(session, teammate) == []


# ---- every meeting-scoped route ----

READS = {
    ("GET", "/api/meetings/{meeting_id}"),
    ("GET", "/api/meetings/{meeting_id}/export"),
    ("GET", "/api/meetings/{meeting_id}/translations/{language}"),
}
OWNER_ONLY = {
    ("DELETE", "/api/meetings/{meeting_id}"),
    ("POST", "/api/meetings/{meeting_id}/cancel"),
    ("POST", "/api/meetings/{meeting_id}/end"),
    ("POST", "/api/meetings/{meeting_id}/retry"),
    ("POST", "/api/meetings/{meeting_id}/regenerate"),
    ("POST", "/api/meetings/{meeting_id}/translations"),
    ("DELETE", "/api/meetings/{meeting_id}/translations/{language}"),
}
# Routes with no meeting id in the path that still read meeting content, through the same rule.
LISTS = {("GET", "/api/meetings"), ("GET", "/api/decisions")}


def meeting_routes(app):
    return [r for r in api_routes(app) if "{meeting_id}" in r[1]]


def test_every_route_with_a_meeting_id_is_classified(app):
    """A new meeting route fails here until someone decides: a read (through visible_meetings)
    or owner-only."""
    found = set(meeting_routes(app))
    assert found == READS | OWNER_ONLY, found ^ (READS | OWNER_ONLY)


def call(handle, method, path, meeting):
    url = path.replace("{meeting_id}", str(meeting.id)).replace("{language}", "fr")
    body = {"language": "fr"} if path.endswith("/translations") else None
    if path.endswith("/regenerate"):
        body = {"template_id": meeting.template_id}
    return handle.call(method, url, body)


@pytest.fixture
def world(session, template, owner, teammate, outsider):
    private, shared = ready(session, template), ready(session, template)
    share(session, shared, owner)
    for meeting in (private, shared):
        session.add(
            MeetingTranslation(
                meeting_id=meeting.id,
                language="fr",
                status="ready",
                notes_md="Notes",
                transcript="Texte",
                source_hash="x",
                model="m",
            )
        )
    session.commit()
    return private, shared


def test_an_outsider_gets_404_on_every_meeting_route(app, world, outsider):
    them = sign_in(app, OTHER_EMAIL)
    for meeting in world:
        for method, path in sorted(READS | OWNER_ONLY):
            error_of(call(them, method, path, meeting), 404, "not_found")


def test_a_teammate_gets_404_on_every_route_for_a_private_meeting(app, world, teammate):
    mate = sign_in(app, TEAMMATE_EMAIL)
    private, _ = world
    for method, path in sorted(READS | OWNER_ONLY):
        error_of(call(mate, method, path, private), 404, "not_found")


def test_a_teammate_reads_a_shared_meeting(app, world, teammate):
    mate = sign_in(app, TEAMMATE_EMAIL)
    _, shared = world
    detail = mate.get(f"/api/meetings/{shared.id}")
    assert detail.status_code == 200 and detail.json()["notes"]
    assert mate.get(f"/api/meetings/{shared.id}/export").status_code == 200
    assert mate.get(f"/api/meetings/{shared.id}/export?transcript=true").status_code == 200
    got = mate.get(f"/api/meetings/{shared.id}/translations/fr")
    assert got.status_code == 200 and got.json()["status"] == "ready"
    assert [m["id"] for m in mate.get("/api/meetings").json()["meetings"]] == [shared.id]


def test_a_teammate_is_offered_no_action_on_a_shared_meeting(app, api, world, teammate):
    """The page must not show buttons whose routes answer a teammate 404."""
    _, shared = world
    flags = ("can_cancel", "can_end", "can_retry", "can_regenerate", "can_delete")
    mine = api.get(f"/api/meetings/{shared.id}").json()
    assert mine["can_regenerate"] is True
    theirs = sign_in(app, TEAMMATE_EMAIL).get(f"/api/meetings/{shared.id}").json()
    assert {flag: theirs[flag] for flag in flags} == dict.fromkeys(flags, False)


def test_a_teammate_cannot_change_a_shared_meeting_and_the_answer_is_404(
    app, world, teammate, session
):
    mate = sign_in(app, TEAMMATE_EMAIL)
    _, shared = world
    for method, path in sorted(OWNER_ONLY):
        error_of(call(mate, method, path, shared), 404, "not_found")
    session.expire_all()
    assert session.scalars(
        select(MeetingTranslation).where(MeetingTranslation.meeting_id == shared.id)
    ).one()
    assert session.get(Meeting, shared.id).status == "ready"


def test_the_owner_still_reaches_every_route(api, world):
    private, shared = world
    for meeting in (private, shared):
        for method, path in sorted(READS):
            assert call(api, method, path, meeting).status_code == 200, (method, path)


def test_the_owner_lists_only_what_they_can_read(api, world, session, outsider, template):
    foreign_template = Template(owner_user_id=outsider.id, name="G", instructions="x")
    session.add(foreign_template)
    session.commit()
    ready(session, foreign_template)
    listed = [m["id"] for m in api.get("/api/meetings").json()["meetings"]]
    assert sorted(listed) == sorted(m.id for m in world)


# ---- decisions follow the meeting ----


def add_decision(session, meeting, text):
    session.add(Decision(meeting_id=meeting.id, text=text, position=0, fingerprint=text))
    session.commit()


def test_decisions_of_a_shared_meeting_reach_the_team_and_a_private_ones_do_not(
    app, world, teammate, session
):
    private, shared = world
    add_decision(session, private, "private choice")
    add_decision(session, shared, "shared choice")
    mate = sign_in(app, TEAMMATE_EMAIL)
    groups = mate.get("/api/decisions").json()["groups"]
    assert [g["meeting"]["id"] for g in groups] == [shared.id]
    assert [d["text"] for d in groups[0]["decisions"]] == ["shared choice"]
    searched = mate.get("/api/decisions?q=private").json()["groups"]
    assert searched == []


def test_a_teammate_cannot_hide_the_owners_decision(app, world, teammate, session):
    _, shared = world
    add_decision(session, shared, "shared choice")
    decision_id = session.scalars(select(Decision.id)).one()
    mate = sign_in(app, TEAMMATE_EMAIL)
    assert mate.post(f"/api/decisions/{decision_id}/hide").status_code == 404
    session.expire_all()
    assert session.get(Decision, decision_id).hidden_at is None


def test_an_outsider_sees_no_decisions(app, world, outsider, session):
    add_decision(session, world[1], "shared choice")
    them = sign_in(app, OTHER_EMAIL)
    assert them.get("/api/decisions").json()["groups"] == []


def test_review_items_stay_with_the_owner_even_when_the_meeting_is_shared(
    app, world, teammate, session
):
    from app.models import ReviewItem

    _, shared = world
    session.add(
        ReviewItem(
            meeting_id=shared.id,
            owner_user_id=shared.owner_user_id,
            text="check",
            position=0,
            fingerprint="f",
        )
    )
    session.commit()
    mate = sign_in(app, TEAMMATE_EMAIL)
    assert mate.get("/api/review").json()["items"] == []
