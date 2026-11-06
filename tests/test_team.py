"""W3 Team: invitations, roles, aliases, sharing a meeting, leaving, the pilot's places.

Cast (from `tests/test_api_tasks.py`): `owner` (owns the team and the meetings), `admin` and
`member` on that team, and `outsider` with a team of their own.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app import admin as pilot_admin
from app import deletion, mail, signup, team_invites, teams, tokens
from app.models import (
    Meeting,
    Task,
    Team,
    TeamInvite,
    TeamMember,
    User,
)
from app.services import Refused
from tests.test_accounts import visitor
from tests.test_api import (  # noqa: F401  (fixtures used by name)
    Api,
    anon,
    anon_client,
    api,
    api_routes,
    app,
    client,
    error_of,
    fake_openai,
    fake_vexa,
    owner,
    ready,
    template,
)
from tests.test_api_tasks import (  # noqa: F401
    ADMIN_EMAIL,
    MEMBER_EMAIL,
    add_task,
    admin,
    as_user,
    join,
    member,
)
from tests.test_signup import open_pilot, queued, sign_up, token_of, verify
from tests.test_team_visibility import OTHER_EMAIL, outsider, share, sign_in  # noqa: F401
from tests.test_web import OWNER_EMAIL, make_user, stranger  # noqa: F401

NEW = "ada@example.test"


@pytest.fixture
def team(session, owner):
    """The owner's team, committed (the app's own connection must be able to see it)."""
    team = teams.ensure_team(session, owner)
    session.commit()
    return team


def invite(api, *emails, role="member", name=None):
    body = {"invitees": [{"email": e, "name": name} for e in emails], "role": role}
    return api.post("/api/team/invites", body)


def link_token(session, to=NEW) -> str:
    """The raw token in the newest queued Team invite mail to this address."""
    rows = queued(session, "team_invite", to)
    assert rows, "no team invite mail queued"
    return token_of(rows[-1])


def fresh(session):
    session.expire_all()
    return session


def now():
    return datetime.now(UTC)


# ---- inviting ----


def test_an_owner_invites_a_new_person_and_the_mail_carries_a_link_that_is_hashed_at_rest(
    api, session, owner, team
):
    response = invite(api, "Ada@Example.test", name="Ada L")
    assert response.status_code == 200, response.text
    [result] = response.json()["results"]
    assert result["status"] == "sent" and result["email"] == NEW
    assert result["invite"]["role"] == "member" and result["invite"]["status"] == "pending"
    raw = link_token(session)
    row = session.scalars(select(TeamInvite)).one()
    assert row.token_hash == tokens.digest(raw) and raw not in row.token_hash
    assert row.name == "Ada L" and row.team_id == team.id and row.invited_by == owner.id
    assert row.expires_at - row.last_sent_at == team_invites.TTL  # 7 days
    link = queued(session, "team_invite")[0].payload["link"]
    assert "/accept-invite?token=" in link


def test_the_mail_is_the_designers_team_invite_and_never_trusts_the_inviters_name(
    api, session, owner, team
):
    owner.name = "Adebayo Tosin"
    session.commit()
    invite(api, NEW)
    row = queued(session, "team_invite")[0]
    message = mail.render(row.template, row.payload, row.to_email, api.client.app.state.settings)
    assert message.subject == "Adebayo Tosin invited you to join their team"
    assert "Accept invite" in message.html and "expires in 7 days" in message.text
    hostile = mail.render(
        "team_invite",
        {"link": "https://x.test/", "inviter": "Click evil.example now: 1"},
        NEW,
        api.client.app.state.settings,
    )
    assert hostile.subject == "You have been invited to join a team"
    assert "evil.example" not in hostile.subject + hostile.text


def test_a_member_cannot_invite_and_an_admin_cannot_invite_an_admin(
    app, session, owner, admin, member
):
    error_of(invite(as_user(app, MEMBER_EMAIL), NEW), 403, "team_forbidden")
    boss = as_user(app, ADMIN_EMAIL)
    error_of(invite(boss, NEW, role="admin"), 403, "team_forbidden")
    assert invite(boss, NEW).status_code == 200
    assert invite(as_user(app, OWNER_EMAIL), "bo@example.test", role="admin").status_code == 200


def test_an_address_that_is_not_an_email_is_reported_not_fatal_and_duplicates_collapse(
    api, session
):
    results = invite(api, "not an email", NEW, NEW.upper()).json()["results"]
    assert [r["status"] for r in results] == ["invalid_email", "sent"]
    assert len(queued(session, "team_invite")) == 1


def test_inviting_a_member_says_already_member_and_inviting_again_resends(
    api, session, owner, member
):
    assert invite(api, MEMBER_EMAIL).json()["results"][0]["status"] == "already_member"
    invite(api, NEW)
    again = invite(api, NEW).json()["results"][0]
    assert again["status"] == "already_invited"  # a mail went out a moment ago
    row = session.scalars(select(TeamInvite)).one()
    row.last_sent_at = now() - timedelta(minutes=5)
    session.commit()
    first = link_token(session)
    assert invite(api, NEW).json()["results"][0]["status"] == "resent"
    assert link_token(session) != first
    assert len(queued(session, "team_invite")) == 2


def test_the_answer_does_not_say_whether_an_address_has_an_account(api, session, stranger):
    known = invite(api, OTHER_EMAIL).json()["results"][0]
    unknown = invite(api, NEW).json()["results"][0]
    assert known["status"] == unknown["status"] == "sent"
    assert set(known) == set(unknown) and set(known["invite"]) == set(unknown["invite"])


def test_a_team_holds_ten_open_invitations_and_twenty_a_day(api, session):
    for n in range(team_invites.MAX_PENDING):
        assert invite(api, f"p{n}@example.test").status_code == 200
    error_of(invite(api, "one-more@example.test"), 409, "invite_limit")
    # revoked ones free the open count but still count toward the day
    ids = [r.id for r in session.scalars(select(TeamInvite))]
    for invite_id in ids:
        assert api.delete(f"/api/team/invites/{invite_id}").status_code == 204
    for n in range(team_invites.MAX_PER_DAY - team_invites.MAX_PENDING):
        assert invite(api, f"q{n}@example.test").status_code == 200
        api.delete(f"/api/team/invites/{session.scalars(select(func.max(TeamInvite.id))).one()}")
    error_of(invite(api, "late@example.test"), 409, "invite_limit")


# ---- the pilot's places (D2) ----


def test_an_invitation_to_a_new_person_holds_a_place_and_an_existing_account_does_not(
    api, session, owner, stranger
):
    open_pilot(session, max_accounts=10)
    before = signup.slots_taken(session, now())
    invite(api, OTHER_EMAIL)  # has an account: no place needed
    assert signup.slots_taken(session, now()) == before
    invite(api, NEW)
    assert signup.slots_taken(session, now()) == before + 1
    invite(api, "second@example.test")
    assert signup.slots_taken(session, now()) == before + 2


def test_no_free_place_means_409_pilot_full_for_a_new_person_only(api, session, owner, stranger):
    open_pilot(session, max_accounts=2)  # owner and stranger fill it
    assert invite(api, OTHER_EMAIL).status_code == 200  # an account: fine
    error = error_of(invite(api, NEW), 409, "pilot_full")
    assert error["message"] == "The pilot has no free places. Ask the pilot admin."
    assert session.scalars(select(TeamInvite).where(TeamInvite.email == NEW)).first() is None
    open_pilot(session, max_accounts=3)
    assert invite(api, NEW).status_code == 200


def test_revoking_or_letting_an_invitation_expire_frees_its_place(api, session, owner):
    open_pilot(session, max_accounts=2)
    invite(api, NEW)
    assert signup.slots_taken(session, now()) == 2
    error_of(invite(api, "other@example.test"), 409, "pilot_full")
    row = session.scalars(select(TeamInvite)).one()
    row.expires_at = now() - timedelta(minutes=1)
    session.commit()
    assert signup.slots_taken(session, now()) == 1
    row.expires_at = now() + timedelta(days=1)
    session.commit()
    assert api.delete(f"/api/team/invites/{row.id}").status_code == 204
    assert signup.slots_taken(session, now()) == 1


def test_a_place_is_held_once_whichever_way_it_was_promised(api, session, owner):
    open_pilot(session, max_accounts=10)
    invite(api, NEW)
    base = signup.slots_taken(session, now())
    # a waiting-list invitation for the same address replaces the team's hold: one place, not two
    tokens.issue(session, purpose="waitlist_invite", email=NEW, user_id=None, now=now())
    session.commit()
    assert signup.slots_taken(session, now()) == base


def test_a_place_is_held_once_when_the_person_already_has_a_confirmed_account(api, session, owner):
    open_pilot(session, max_accounts=10)
    invite(api, NEW)
    base = signup.slots_taken(session, now())
    make_user(session, NEW)  # a confirmed account: counted as an account, once
    assert signup.slots_taken(session, now()) == base


# ---- a new person signs up with the link ----


def test_a_new_person_signs_up_through_a_closed_pilot_that_the_invite_fills_and_joins_on_confirming(
    app, api, session, owner, team
):
    open_pilot(session, max_accounts=3, signups_open=False)
    make_user(session, "x@example.test")  # owner + x = 2: one place left, held by the invitation
    invite(api, NEW, name="Ada L")
    assert signup.pilot_state(session, now()) == "closed"  # and it is full for anyone else
    raw = link_token(session)
    web = visitor(app, "198.51.100.7")
    assert sign_up(web, invite_token=raw).status_code == 202
    ada = fresh(session).scalars(select(User).where(User.email == NEW)).one()
    assert ada.email_verified_at is None
    assert signup.slots_taken(session, now()) == 3  # reserved by the invitation, not counted twice
    verification = token_of(queued(session, "verify_email", NEW)[0])
    assert verify(web, verification).status_code == 200
    fresh(session)
    member_row = teams.membership(session, ada.id)
    assert member_row.team_id == team.id and member_row.role == "member"
    assert member_row.aliases == ["Ada L"]
    assert session.scalars(select(TeamInvite)).one().accepted_at is not None
    assert signup.slots_taken(session, now()) == 3  # now a confirmed account: still once
    assert (
        session.scalars(select(func.count()).select_from(Team)).one() == 1
    )  # her own team of one is gone: only the owner's team is left


def test_the_link_works_for_its_own_address_only_and_once(app, api, session, owner):
    open_pilot(session, max_accounts=10, signups_open=False)
    invite(api, NEW)
    raw = link_token(session)
    web = visitor(app, "198.51.100.7")
    error_of(sign_up(web, email="eve@example.test", invite_token=raw), 400, "invite_invalid")
    error_of(sign_up(web, invite_token="x" * 43), 400, "invite_invalid")
    row = session.scalars(select(TeamInvite)).one()
    row.revoked_at = now()
    session.commit()
    error_of(sign_up(web, invite_token=raw), 400, "invite_invalid")


def test_signing_up_without_a_link_gets_no_special_treatment_in_a_closed_pilot(
    app, api, session, owner
):
    open_pilot(session, max_accounts=10, signups_open=False)
    invite(api, NEW)
    web = visitor(app, "198.51.100.7")
    error_of(sign_up(web), 403, "pilot_closed")


def test_an_unconfirmed_account_from_a_link_is_not_swept_away_with_its_place(
    app, api, session, owner
):
    open_pilot(session, max_accounts=3, signups_open=False)
    invite(api, NEW)
    web = visitor(app, "198.51.100.7")
    sign_up(web, invite_token=link_token(session))
    assert signup.slots_taken(session, now()) == 2
    assert signup.may_confirm(
        session, fresh(session).scalars(select(User).where(User.email == NEW)).one(), now()
    )


# ---- Google ----


def test_a_new_person_signing_in_with_google_joins_the_team_without_a_token(
    app, api, session, owner, team
):
    from app import google_signin
    from app.google_oauth import Identity

    open_pilot(session, max_accounts=3, signups_open=False)
    make_user(session, "x@example.test")
    invite(api, NEW, name="Ada L")
    outcome = google_signin.complete(
        session,
        Identity(sub="g-ada", email=NEW, name="Ada Lovelace"),
        accepted_terms=True,
        now=now(),
    )
    assert outcome.user is not None and outcome.error is None
    assert teams.membership(session, outcome.user.id).team_id == team.id
    assert signup.slots_taken(session, now()) == 3


# ---- accepting as an existing account ----


@pytest.fixture
def invitee(session):
    """An existing account with a team of one, a meeting of theirs and a task from it."""
    user = make_user(session, NEW, name="Ada Lovelace")
    mine = teams.ensure_team(session, user)
    session.commit()
    return user, mine


def accept(handle, token):
    return handle.post("/api/team/invites/accept", {"token": token})


def test_an_existing_account_accepts_and_its_team_of_one_dissolves_into_the_new_one(
    app, api, session, owner, team, template, invitee
):
    ada, own_team = invitee
    own_team_id = own_team.id
    meeting = ready(session, template, owner_user_id=ada.id)
    shared_before = ready(session, template, owner_user_id=ada.id)
    shared_before.shared_team_id = own_team.id
    task = add_task(session, own_team, meeting, title="Ada's task", assignee=ada)
    manual = add_task(session, own_team, title="Ada's own list", assignee=ada)
    invite(api, NEW, name="Lovelace")
    raw = link_token(session)
    ada_api = visitor(app, "198.51.100.9")
    assert ada_api.log_in(email=NEW).status_code == 200
    response = accept(ada_api, raw)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == team.id and body["my_role"] == "member"
    fresh(session)
    assert teams.membership(session, ada.id).team_id == team.id
    assert session.get(Team, own_team_id) is None  # dissolved
    assert {session.get(Task, task.id).team_id, session.get(Task, manual.id).team_id} == {team.id}
    # their meetings stay theirs and stay private
    assert session.get(Meeting, meeting.id).owner_user_id == ada.id
    assert session.get(Meeting, shared_before.id).shared_team_id is None
    assert [m["id"] for m in api.get("/api/meetings").json()["meetings"]] == []
    error_of(api.get(f"/api/meetings/{meeting.id}"), 404, "not_found")
    assert teams.membership(session, ada.id).aliases == ["Lovelace"]


def test_the_link_is_single_use_and_only_for_the_account_it_was_sent_to(
    app, api, session, owner, invitee, stranger
):
    ada, _ = invitee
    invite(api, NEW)
    raw = link_token(session)
    other = visitor(app, "198.51.100.10")
    other.log_in(email=OTHER_EMAIL)
    error_of(accept(other, raw), 400, "invite_invalid")  # someone else's link
    mine = visitor(app, "198.51.100.9")
    mine.log_in(email=NEW)
    assert accept(mine, raw).status_code == 200
    error_of(accept(mine, raw), 400, "invite_invalid")  # used


@pytest.mark.parametrize("how", ["expired", "revoked", "unknown", "resent"])
def test_a_dead_link_gets_the_one_answer(app, api, session, owner, invitee, how):
    invite(api, NEW)
    raw = link_token(session)
    row = session.scalars(select(TeamInvite)).one()
    if how == "expired":
        row.expires_at = now() - timedelta(seconds=1)
    elif how == "revoked":
        row.revoked_at = now()
    elif how == "resent":
        row.last_sent_at = now() - timedelta(minutes=5)
        session.commit()
        api.post(f"/api/team/invites/{row.id}/resend")  # the old link dies with the new one
    else:
        raw = "nope" * 8
    session.commit()
    mine = visitor(app, "198.51.100.9")
    mine.log_in(email=NEW)
    error = error_of(accept(mine, raw), 400, "invite_invalid")
    assert "not valid any more" in error["message"]


def test_someone_on_a_team_with_other_people_must_leave_it_first(app, api, session, owner, invitee):
    ada, own_team = invitee
    friend = make_user(session, "friend@example.test")
    session.add(TeamMember(team_id=own_team.id, user_id=friend.id, role="member"))
    session.commit()
    invite(api, NEW)
    mine = visitor(app, "198.51.100.9")
    mine.log_in(email=NEW)
    error_of(accept(mine, link_token(session)), 409, "already_on_team")
    assert fresh(session).scalars(select(TeamInvite)).one().accepted_at is None  # not used up
    assert teams.membership(session, ada.id).team_id == own_team.id


def test_what_a_team_of_one_had_invited_is_revoked_when_it_joins_another(
    app, api, session, owner, invitee
):
    ada, _ = invitee
    ada_api = visitor(app, "198.51.100.9")
    ada_api.log_in(email=NEW)
    invite(ada_api, "someone@example.test")
    invite(api, NEW)
    assert accept(ada_api, link_token(session)).status_code == 200
    # gone with the team they were sent from: the link no longer exists, and its place is free
    assert (
        fresh(session)
        .scalars(select(TeamInvite).where(TeamInvite.email == "someone@example.test"))
        .first()
        is None
    )


# ---- the Team screen's reads and settings ----


def test_the_team_and_its_members_as_the_screen_needs_them(
    api, session, owner, admin, member, team
):
    body = api.get("/api/team").json()
    assert body == {
        "id": team.id,
        "name": "My team",
        "share_new_meetings": False,
        "sprint_length": "week5",
        "sprint_custom_days": 10,
        "sprint_align_weekdays": True,
        "my_role": "owner",
        "member_count": 3,
        "pending_invites": 0,
        "can_manage": True,
        "can_change_roles": True,
    }
    add_task(session, team, title="Open one", assignee=member)
    add_task(session, team, title="Done", assignee=member, status="done")
    invite(api, NEW)
    listing = api.get("/api/team/members").json()
    by_email = {m["email"]: m for m in listing["members"]}
    assert [m["role"] for m in listing["members"]] == ["owner", "admin", "member"]
    assert by_email[MEMBER_EMAIL]["open_tasks"] == 1
    assert by_email[OWNER_EMAIL]["is_you"] is True and by_email[OWNER_EMAIL]["can_remove"] is False
    assert by_email[MEMBER_EMAIL]["can_remove"] and by_email[ADMIN_EMAIL]["can_change_role"]
    [pending] = listing["invites"]
    assert pending["email"] == NEW and pending["status"] == "pending"
    assert "token" not in str(listing)


def test_only_an_owner_or_admin_edits_the_team_and_sharing_defaults_to_off(
    app, api, session, owner, admin, member
):
    assert (
        api.call("PATCH", "/api/team", {"name": "Product", "share_new_meetings": True}).status_code
        == 200
    )
    assert api.get("/api/team").json()["share_new_meetings"] is True
    assert (
        as_user(app, ADMIN_EMAIL).call("PATCH", "/api/team", {"name": "Product team"}).status_code
        == 200
    )
    error_of(
        as_user(app, MEMBER_EMAIL).call("PATCH", "/api/team", {"name": "x"}), 403, "team_forbidden"
    )
    error_of(api.call("PATCH", "/api/team", {"name": ""}), 422, "validation_error")
    error_of(api.call("PATCH", "/api/team", {"name": "  "}), 422, "invalid_name")
    error_of(api.call("PATCH", "/api/team", {"name": "​​"}), 422, "invalid_name")


def test_roles_only_the_owner_changes_them_and_never_the_owners(
    app, api, session, owner, admin, member
):
    peer = as_user(app, ADMIN_EMAIL)
    error_of(
        peer.call("PATCH", f"/api/team/members/{member.id}", {"role": "admin"}),
        403,
        "team_forbidden",
    )
    assert api.call("PATCH", f"/api/team/members/{member.id}", {"role": "admin"}).status_code == 200
    error_of(
        api.call("PATCH", f"/api/team/members/{owner.id}", {"role": "member"}),
        403,
        "team_forbidden",
    )
    error_of(
        api.call("PATCH", f"/api/team/members/{member.id}", {"role": "owner"}),
        422,
        "validation_error",
    )
    assert teams.membership(fresh(session), member.id).role == "admin"


def test_aliases_the_owner_and_admin_edit_anyones_a_member_only_their_own(
    app, api, session, owner, admin, member
):
    path = f"/api/team/members/{member.id}"
    done = api.call("PATCH", path, {"aliases": ["Dan", " dan ", "Danny  O", "", "x" * 90]}).json()
    row = next(m for m in done["members"] if m["user_id"] == member.id)
    assert row["aliases"] == ["Dan", "Danny O", "x" * 59 + "…"]
    me = as_user(app, MEMBER_EMAIL)
    assert me.call("PATCH", path, {"aliases": ["Daniel"]}).status_code == 200
    error_of(
        me.call("PATCH", f"/api/team/members/{owner.id}", {"aliases": ["Boss"]}),
        403,
        "team_forbidden",
    )
    error_of(me.call("PATCH", path, {"aliases": ["a"] * 21}), 422, "validation_error")
    assert len(teams.membership(fresh(session), member.id).aliases) == 1


def test_an_alias_set_here_is_what_matches_a_name_said_in_a_meeting(
    api, session, owner, member, team
):
    from app import tasks

    api.call("PATCH", f"/api/team/members/{member.id}", {"aliases": ["Dan"]})
    people = tasks.members_of(session, team.id)
    assert tasks.match_owner("Dan", people) == member.id


def test_owner_matching_ignores_invited_people_and_suspended_accounts(
    api, session, owner, member, team
):
    from app import tasks

    invite(api, "kemi@example.test", name="Kemi Adeyemi")
    assert tasks.match_owner("Kemi", tasks.members_of(session, team.id)) is None
    member.status = "suspended"
    session.commit()
    assert tasks.match_owner("Daniel", tasks.members_of(session, team.id)) is None


# ---- removing and leaving ----


def test_removing_a_member_unassigns_tasks_unshares_meetings_and_gives_a_fresh_team(
    api, session, owner, member, team, template
):
    mine = ready(session, template)  # the owner's meeting, shared
    share(session, mine, owner)
    theirs = ready(session, template, owner_user_id=member.id)
    theirs.shared_team_id = team.id
    session.commit()
    open_task = add_task(session, team, mine, title="Open", assignee=member)
    done_task = add_task(session, team, mine, title="Done", assignee=member, status="done")
    from_theirs = add_task(session, team, theirs, title="From their meeting", assignee=owner)
    from_theirs_mine = add_task(session, team, theirs, title="Theirs, theirs", assignee=member)
    assert api.delete(f"/api/team/members/{member.id}").status_code == 204
    fresh(session)
    assert session.get(Task, open_task.id).assignee_user_id is None
    assert session.get(Task, done_task.id).assignee_user_id == member.id  # history keeps the name
    assert session.get(Meeting, theirs.id).shared_team_id is None
    assert session.get(Meeting, theirs.id).owner_user_id == member.id  # meetings never move
    assert session.get(Meeting, mine.id).shared_team_id == team.id
    new_team = teams.team_of(session, member.id)
    assert new_team.id != team.id and new_team.owner_user_id == member.id
    assert teams.membership(session, member.id).role == "owner"
    # the tasks read from their meeting go with them; nobody else holds them
    assert session.get(Task, from_theirs.id).team_id == new_team.id
    assert session.get(Task, from_theirs.id).assignee_user_id is None
    assert session.get(Task, from_theirs_mine.id).assignee_user_id == member.id


def test_a_removed_person_keeps_no_window_into_the_old_teams_tasks(
    app, api, session, owner, member, team, template
):
    mine = ready(session, template)
    done = add_task(session, team, mine, title="Done", assignee=member, status="done")
    mate = as_user(app, MEMBER_EMAIL)
    assert mate.get(f"/api/tasks/{done.id}").status_code == 200
    api.delete(f"/api/team/members/{member.id}")
    error_of(mate.get(f"/api/tasks/{done.id}"), 404, "not_found")


def test_an_admin_removes_members_not_admins_and_nobody_removes_the_owner(
    app, api, session, owner, admin, member
):
    boss = as_user(app, ADMIN_EMAIL)
    error_of(boss.delete(f"/api/team/members/{owner.id}"), 403, "team_forbidden")
    other_admin = make_user(session, "a2@example.test")
    session.add(
        TeamMember(
            team_id=teams.team_of(session, owner.id).id, user_id=other_admin.id, role="admin"
        )
    )
    session.commit()
    error_of(boss.delete(f"/api/team/members/{other_admin.id}"), 403, "team_forbidden")
    assert boss.delete(f"/api/team/members/{member.id}").status_code == 204
    assert api.delete(f"/api/team/members/{other_admin.id}").status_code == 204  # the owner may
    error_of(api.delete(f"/api/team/members/{owner.id}"), 409, "owner_cannot_leave")


def test_a_member_leaves_on_their_own_and_cannot_remove_anyone_else(
    app, session, owner, admin, member
):
    me = as_user(app, MEMBER_EMAIL)
    error_of(me.delete(f"/api/team/members/{admin.id}"), 403, "team_forbidden")
    assert me.delete(f"/api/team/members/{member.id}").status_code == 204
    assert me.get("/api/team/members").json()["members"][0]["role"] == "owner"  # a team of one
    assert me.get("/api/team").json()["member_count"] == 1


def test_leaving_stops_the_invitations_you_sent(app, api, session, owner, admin, member):
    boss = as_user(app, ADMIN_EMAIL)
    invite(boss, NEW)
    boss.delete(f"/api/team/members/{admin.id}")
    assert fresh(session).scalars(select(TeamInvite)).one().revoked_at is not None


# ---- invitations: resend and revoke ----


def test_resend_gives_a_new_link_extends_the_expiry_and_waits_a_minute(api, session, owner):
    invite(api, NEW)
    row = session.scalars(select(TeamInvite)).one()
    old = link_token(session)
    error_of(api.post(f"/api/team/invites/{row.id}/resend"), 429, "too_soon")
    row.last_sent_at = now() - timedelta(minutes=2)
    row.expires_at = now() + timedelta(hours=1)
    session.commit()
    done = api.post(f"/api/team/invites/{row.id}/resend")
    assert done.status_code == 200 and done.json()["status"] == "pending"
    fresh(session)
    assert row.expires_at > now() + timedelta(days=6)
    assert link_token(session) != old
    assert row.token_hash == tokens.digest(link_token(session))


def test_an_expired_invitation_shows_as_expired_and_resend_revives_it(api, session, owner):
    invite(api, NEW)
    row = session.scalars(select(TeamInvite)).one()
    row.expires_at, row.last_sent_at = now() - timedelta(days=1), now() - timedelta(days=8)
    session.commit()
    [shown] = api.get("/api/team/members").json()["invites"]
    assert shown["status"] == "expired"
    assert api.get("/api/team").json()["pending_invites"] == 0
    assert api.post(f"/api/team/invites/{row.id}/resend").json()["status"] == "pending"


def test_resending_an_expired_invitation_to_a_new_person_needs_a_place_again(api, session, owner):
    open_pilot(session, max_accounts=2)
    invite(api, NEW)
    row = session.scalars(select(TeamInvite)).one()
    row.expires_at, row.last_sent_at = now() - timedelta(days=1), now() - timedelta(days=8)
    session.commit()
    make_user(session, "x@example.test")  # took the freed place
    error_of(api.post(f"/api/team/invites/{row.id}/resend"), 409, "pilot_full")


# ---- sharing a meeting ----


def test_the_owner_shares_and_unshares_and_a_teammate_reads_it_while_shared(
    app, api, session, owner, member, template
):
    meeting = ready(session, template)
    mate = as_user(app, MEMBER_EMAIL)
    error_of(mate.get(f"/api/meetings/{meeting.id}"), 404, "not_found")
    shared = api.post(f"/api/meetings/{meeting.id}/share")
    assert shared.status_code == 200 and shared.json()["shared_with_team"] is True
    assert api.post(f"/api/meetings/{meeting.id}/share").status_code == 200  # twice is fine
    seen = mate.get(f"/api/meetings/{meeting.id}").json()
    assert seen["shared_with_team"] is True and seen["can_share"] is False
    assert seen["shared_by"] == "owner"  # no name on the account: the part before the @
    assert api.get(f"/api/meetings/{meeting.id}").json()["shared_by"] is None
    assert api.delete(f"/api/meetings/{meeting.id}/share").json()["shared_with_team"] is False
    error_of(mate.get(f"/api/meetings/{meeting.id}"), 404, "not_found")


def test_a_teammate_cannot_share_or_unshare_the_owners_meeting_and_the_answer_is_404(
    app, session, owner, member, template, outsider
):
    meeting = ready(session, template)
    share(session, meeting, owner)
    for who in (as_user(app, MEMBER_EMAIL), sign_in(app, OTHER_EMAIL)):
        error_of(who.post(f"/api/meetings/{meeting.id}/share"), 404, "not_found")
        error_of(who.delete(f"/api/meetings/{meeting.id}/share"), 404, "not_found")
    assert fresh(session).get(Meeting, meeting.id).shared_team_id is not None


# ---- the team default, read when the notes are first written ----


def test_the_default_shares_the_meetings_of_owners_and_admins_only_and_only_at_first_notes(
    session, owner, admin, member, team, template
):
    team.share_new_meetings = True
    session.commit()
    results = {}
    for user in (owner, admin, member):
        meeting = ready(session, template, owner_user_id=user.id, shared_team_id=None)
        teams.apply_share_default(session, meeting)
        results[user.email] = meeting.shared_team_id
    assert results == {OWNER_EMAIL: team.id, ADMIN_EMAIL: team.id, MEMBER_EMAIL: None}
    team.share_new_meetings = False
    other = ready(session, template, owner_user_id=owner.id, shared_team_id=None)
    teams.apply_share_default(session, other)
    assert other.shared_team_id is None


# ---- deleting an account ----


def test_an_owner_with_other_members_cannot_be_deleted_until_the_team_is_handed_over(
    session, owner, member, team
):
    with pytest.raises(Refused) as refused:
        pilot_admin.schedule_deletion(session, owner)
    assert refused.value.code == "team_owner_has_members"
    assert fresh(session).get(User, owner.id).status == "active"
    teams.transfer_owner(session, team.id, member.id)
    session.commit()
    pilot_admin.schedule_deletion(session, owner)
    assert fresh(session).get(User, owner.id).status == "deleted"


def test_transferring_a_team_makes_the_old_owner_an_admin(session, owner, member, team):
    teams.transfer_owner(session, team.id, member.id)
    session.commit()
    assert teams.membership(session, member.id).role == "owner"
    assert teams.membership(session, owner.id).role == "admin"
    assert session.get(Team, team.id).owner_user_id == member.id
    with pytest.raises(teams.TeamChangeRefused):
        teams.transfer_owner(session, team.id, 999_999)


def test_deleting_an_account_removes_the_invitations_sent_to_it_and_frees_the_place(
    api, session, owner, stranger
):
    invite(api, OTHER_EMAIL)
    assert session.scalars(select(TeamInvite)).one().email == OTHER_EMAIL

    class NoVexa:
        pass

    stranger_row = session.get(User, stranger.id)
    deletion.purge_user(session, stranger_row, NoVexa(), force=True)
    assert session.scalars(select(TeamInvite)).all() == []


# ---- every team route, as someone it is not for ----

TEAM_READS = {("GET", "/api/team"), ("GET", "/api/team/members")}
TEAM_PUBLIC = {("GET", "/api/team/invites/preview")}
TEAM_MANAGER_ONLY = {
    ("PATCH", "/api/team"),
    ("POST", "/api/team/invites"),
    ("POST", "/api/team/invites/{invite_id}/resend"),
    ("DELETE", "/api/team/invites/{invite_id}"),
}
TEAM_ID_ROUTES = {
    ("PATCH", "/api/team/members/{user_id}"),
    ("DELETE", "/api/team/members/{user_id}"),
}
TEAM_OTHER = {("POST", "/api/team/invites/accept")}


def test_every_team_route_is_classified(app):
    found = {r for r in api_routes(app) if r[1].startswith("/api/team")}
    known = TEAM_READS | TEAM_MANAGER_ONLY | TEAM_ID_ROUTES | TEAM_OTHER | TEAM_PUBLIC
    assert found == known, found ^ known


def test_an_id_on_another_team_is_404_on_every_team_id_route(app, session, owner, member, outsider):
    them = sign_in(app, OTHER_EMAIL)
    invite(as_user(app, OWNER_EMAIL), NEW)
    invite_id = session.scalars(select(TeamInvite.id)).one()
    for method, path in sorted(TEAM_ID_ROUTES):
        url = path.replace("{user_id}", str(member.id))
        body = {"aliases": ["x"]} if method == "PATCH" else None
        error_of(them.call(method, url, body), 404, "not_found")
    error_of(them.post(f"/api/team/invites/{invite_id}/resend"), 404, "not_found")
    error_of(them.delete(f"/api/team/invites/{invite_id}"), 404, "not_found")
    assert fresh(session).scalars(select(TeamInvite)).one().revoked_at is None


def test_a_member_gets_403_on_every_owner_or_admin_route(app, session, owner, member):
    invite(as_user(app, OWNER_EMAIL), NEW)
    invite_id = session.scalars(select(TeamInvite.id)).one()
    me = as_user(app, MEMBER_EMAIL)
    bodies = {
        ("PATCH", "/api/team"): {"name": "x"},
        ("POST", "/api/team/invites"): {"invitees": [{"email": "z@example.test"}]},
    }
    for method, path in sorted(TEAM_MANAGER_ONLY):
        url = path.replace("{invite_id}", str(invite_id))
        error_of(me.call(method, url, bodies.get((method, path))), 403, "team_forbidden")
    assert fresh(session).scalars(select(TeamInvite)).one().revoked_at is None


def test_a_member_may_read_the_team(app, session, owner, member):
    me = as_user(app, MEMBER_EMAIL)
    assert me.get("/api/team").json()["my_role"] == "member"
    assert me.get("/api/team").json()["can_manage"] is False
    listing = me.get("/api/team/members").json()
    assert all(not m["can_remove"] for m in listing["members"] if not m["is_you"])


def test_an_outsider_sees_only_their_own_team(app, session, owner, member, outsider):
    them = sign_in(app, OTHER_EMAIL)
    listing = them.get("/api/team/members").json()
    assert [m["email"] for m in listing["members"]] == [OTHER_EMAIL]
    assert listing["invites"] == []


def test_the_hourly_sweep_drops_old_finished_invitations_and_keeps_the_rest(api, session, owner):
    from app import maintenance

    for email in ("a@example.test", "b@example.test", "c@example.test"):
        invite(api, email)
    a, b, c = session.scalars(select(TeamInvite).order_by(TeamInvite.id)).all()
    old = now() - timedelta(days=8)
    a.expires_at = old
    b.revoked_at = old
    session.commit()
    maintenance.sweep_auth_tokens(session, now())
    assert [i.email for i in session.scalars(select(TeamInvite))] == [c.email]


def test_a_link_can_be_read_by_its_holder_before_signing_in(app, api, session, owner, stranger):
    owner.name = "Adebayo Tosin"
    session.commit()
    invite(api, NEW, name="Ada L")
    invite(api, OTHER_EMAIL)
    visitor_api = visitor(app, "198.51.100.20")  # not signed in
    seen = visitor_api.get(f"/api/team/invites/preview?token={link_token(session)}").json()
    assert seen["email"] == NEW and seen["has_account"] is False
    assert seen["inviter_name"] == "Adebayo Tosin" and seen["team_name"] == "My team"
    assert seen["name"] == "Ada L" and seen["role"] == "member"
    again = visitor_api.get(f"/api/team/invites/preview?token={link_token(session, OTHER_EMAIL)}")
    assert again.json()["has_account"] is True
    error_of(visitor_api.get("/api/team/invites/preview?token=nope"), 400, "invite_invalid")
    error_of(visitor_api.get("/api/team/invites/preview"), 422, "validation_error")
    session.scalars(select(TeamInvite)).first().revoked_at = now()
    session.commit()
    error_of(
        visitor_api.get(f"/api/team/invites/preview?token={link_token(session)}"),
        400,
        "invite_invalid",
    )


def test_an_unconfirmed_account_needs_a_place_so_a_full_pilot_refuses_its_invitation(
    api, session, owner
):
    open_pilot(session, max_accounts=2)
    make_user(session, "x@example.test")  # owner + x = full
    make_user(session, NEW, email_verified_at=None)  # signed up anyway: holds no place yet
    error_of(invite(api, NEW), 409, "pilot_full")


def test_an_alias_cannot_steal_another_members_name(app, api, session, owner, member, admin):
    member.name, admin.name = "Daniel Okoro", "Amara Nwosu"
    session.commit()
    path = f"/api/team/members/{admin.id}"
    me = as_user(app, MEMBER_EMAIL)
    error_of(
        me.call("PATCH", f"/api/team/members/{member.id}", {"aliases": ["Amara"]}),
        422,
        "alias_taken",
    )
    for stolen in ("Daniel", "okoro", "Daniel Okoro"):
        error_of(api.call("PATCH", path, {"aliases": [stolen]}), 422, "alias_taken")
    assert api.call("PATCH", path, {"aliases": ["Mara"]}).status_code == 200


def test_two_managers_inviting_the_last_place_at_once_cannot_both_take_it(
    app, api, session, owner, admin
):
    import threading

    open_pilot(session, max_accounts=4)
    make_user(session, "x@example.test")  # owner, admin and x: one place left
    results = {}

    def go(email, who):
        results[email] = invite(who, email).status_code

    boss = as_user(app, ADMIN_EMAIL)
    threads = [
        threading.Thread(target=go, args=("a@example.test", api)),
        threading.Thread(target=go, args=("b@example.test", boss)),
    ]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(results.values()) == [200, 409]
