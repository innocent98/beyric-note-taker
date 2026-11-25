"""The meeting page can say why a meeting is shared: `shared_by_auto_share` on the meeting detail.

A saved "Share my new meetings with <team>" choice publishes a meeting recorded in the person's
Personal workspace on purpose (`teams.apply_share_default`; tests/test_workspace_scope.py). People
read Personal as private, so the page says so. The flag is true only for the recorder, only while
the meeting is shared, and only when the rule was what shared it. Cast:
`tests/test_multi_team_leaks.py` (Pat is a plain member of X and Y; Ada owns X).
"""

# ruff: noqa: F811  (fixtures are imported by name and used as arguments)
from app import teams
from tests.test_api import (  # noqa: F401  (fixtures used by name)
    anon,
    anon_client,
    api,
    app,
    client,
    fake_bot,
    fake_openai,
    owner,
    ready,
    template,
)
from tests.test_multi_team_leaks import PAT, world  # noqa: F401
from tests.test_team_visibility import sign_in
from tests.test_workspace_scope import choose


def detail(handle, meeting) -> dict:
    response = handle.client.get(f"/api/meetings/{meeting.id}")
    assert response.status_code == 200, response.text
    return response.json()


def shared_by_rule(session, template, world, **fields):
    """Pat's meeting in her personal workspace, then the rule runs as the pipeline runs it."""
    choose(session, world.pat, world.y)
    meeting = ready(session, template, owner_user_id=world.pat.id, **fields)
    teams.apply_share_default(session, meeting)
    session.commit()
    return meeting


def test_a_meeting_the_rule_shared_says_so_to_its_recorder(app, session, template, world):
    meeting = shared_by_rule(session, template, world)
    body = detail(sign_in(app, PAT), meeting)
    assert body["shared_by_auto_share"] is True
    assert body["shared_with_team"] is True and body["team"]["id"] == world.y.id


def test_a_teammate_reading_it_is_not_told_about_someone_elses_rule(app, session, template, world):
    meeting = shared_by_rule(session, template, world)
    quinn = sign_in(app, "quinn@example.test")  # owns Y, reads the shared meeting
    body = detail(quinn, meeting)
    assert body["shared_with_team"] is True
    assert body["shared_by_auto_share"] is False


def test_unsharing_clears_it_and_sharing_again_by_hand_does_not_bring_it_back(
    app, session, template, world
):
    meeting = shared_by_rule(session, template, world)
    pat = sign_in(app, PAT)
    assert pat.call("DELETE", f"/api/meetings/{meeting.id}/share", None).status_code == 200
    body = detail(pat, meeting)
    assert body["shared_with_team"] is False and body["shared_by_auto_share"] is False
    shared = pat.call("POST", f"/api/meetings/{meeting.id}/share", {"team_id": world.y.id})
    assert shared.status_code == 200
    assert shared.json()["shared_with_team"] is True
    assert shared.json()["shared_by_auto_share"] is False  # her own act now, not the rule's
    assert detail(pat, meeting)["shared_by_auto_share"] is False


def test_sharing_by_hand_with_a_rule_saved_is_not_labelled_as_the_rule(
    app, session, template, world
):
    choose(session, world.pat, world.y)
    meeting = ready(session, template, owner_user_id=world.pat.id)  # the rule has not run for it
    shared = sign_in(app, PAT).call(
        "POST", f"/api/meetings/{meeting.id}/share", {"team_id": world.x.id}
    )
    assert shared.status_code == 200
    assert shared.json()["shared_by_auto_share"] is False


def test_a_private_meeting_and_a_meeting_with_no_rule_are_not_labelled(
    app, session, template, world
):
    private = ready(session, template, owner_user_id=world.pat.id)
    teams.apply_share_default(session, private)  # no rule saved: nothing happens
    assert detail(sign_in(app, PAT), private)["shared_by_auto_share"] is False
    assert private.shared_team_id is None


def test_a_meeting_born_shared_by_its_workspace_is_not_labelled_as_the_rule(
    app, session, template, world
):
    """Pat's rule names X and X shares what is recorded in it by default: the workspace shared it
    at birth, so the rule did not."""
    choose(session, world.pat, world.x)
    meeting = ready(
        session,
        template,
        owner_user_id=world.pat.id,
        workspace_team_id=world.x.id,
        shared_team_id=world.x.id,
    )
    teams.apply_share_default(session, meeting)
    session.commit()
    assert meeting.shared_team_id == world.x.id
    assert detail(sign_in(app, PAT), meeting)["shared_by_auto_share"] is False


def test_a_rule_that_shares_into_its_own_team_workspace_is_labelled(app, session, template, world):
    """Recorded into Y's workspace, not born shared, with Pat's rule naming Y."""
    choose(session, world.pat, world.y)
    meeting = ready(
        session, template, owner_user_id=world.pat.id, workspace_team_id=world.y.id
    )  # not born shared
    teams.apply_share_default(session, meeting)
    session.commit()
    assert detail(sign_in(app, PAT), meeting)["shared_by_auto_share"] is True


def test_the_flag_is_false_when_the_team_share_has_gone(app, session, template, world):
    meeting = shared_by_rule(session, template, world)
    session.refresh(meeting)
    meeting.shared_team_id = None  # a team deleted or left (set null in the database)
    session.commit()
    assert detail(sign_in(app, PAT), meeting)["shared_by_auto_share"] is False


def test_the_flag_is_present_on_every_meeting_detail(api, session, template, world):
    own = ready(session, template)  # the owner's own, private
    assert api.get(f"/api/meetings/{own.id}").json()["shared_by_auto_share"] is False
