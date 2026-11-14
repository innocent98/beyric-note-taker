"""Multi-team stage 2a: a meeting a TEAM owns (moved), the Move route, and the share route's team.

Written before the code (docs/superpowers/specs/2026-11-13-multi-team-design.md sections 6.3, 6.4,
6.6, 6.8, 9.1; docs/sop/2026-11-13-multi-team-stage-1.md prerequisites 1 and 2). The cast is the
stage-1 leak tests' (`world`), plus `dee`, an admin of team X:

* Ada records the meetings and owns X; Pat is in X and Y; Quinn owns Y and is not in X; Zed owns Z.
* The rule under test: the recorder reads and changes a moved meeting ONLY while a member of the
  owning team; the team's owner and admins may change it; every member reads it; it survives the
  recorder leaving the team and deleting their account (shown as "Former member").
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app import deletion, teams, visibility
from app.models import Meeting, Task, TeamMember, User
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
    ready,
    template,
)
from tests.test_api_tasks import add_task
from tests.test_multi_team_leaks import (  # noqa: F401  (fixtures used by name)
    PAT,
    QUINN,
    ZED,
    ids_of,
    multi_team_on,
    new_team,
    world,
)
from tests.test_team_visibility import OWNER_ONLY, READS, call, sign_in
from tests.test_web import make_user

ADA, DEE = "owner@example.test", "dee@example.test"


class NoVexa:
    """`purge_user(..., force=True)` never calls the provider."""


@pytest.fixture
def dee(session, world):
    """An admin of team X (not the recorder of anything)."""
    user = make_user(session, DEE)
    teams.ensure_personal_team(session, user)
    session.add(TeamMember(team_id=world.x.id, user_id=user.id, role="admin"))
    session.commit()
    return user


def move(handle, meeting, team):
    return handle.post(f"/api/meetings/{meeting.id}/move", {"team_id": team.id})


def moved(session, meeting, team, by=None) -> Meeting:
    """A meeting that is team-owned (set directly: the route has its own tests)."""
    meeting.owner_team_id = team.id
    meeting.shared_team_id = None
    session.commit()
    return meeting


# ---- the one read rule for a team-owned meeting ----


def test_a_person_in_x_and_y_reads_a_moved_meeting_only_through_x(session, world):
    moved(session, world.private, world.x)
    assert world.private.id in ids_of(session, world.pat)
    session.execute(
        TeamMember.__table__.delete().where(
            TeamMember.user_id == world.pat.id, TeamMember.team_id == world.y.id
        )
    )
    session.commit()
    assert world.private.id in ids_of(session, world.pat)  # Y was never the way in
    session.execute(
        TeamMember.__table__.delete().where(
            TeamMember.user_id == world.pat.id, TeamMember.team_id == world.x.id
        )
    )
    session.commit()
    assert world.private.id not in ids_of(session, world.pat)  # X was


def test_a_person_only_in_y_or_z_never_sees_a_moved_meeting_of_x(app, session, world):
    moved(session, world.private, world.x)
    assert world.private.id not in ids_of(session, world.quinn)
    assert world.private.id not in ids_of(session, world.zed)
    for email in (QUINN, ZED):
        other = sign_in(app, email)
        for method, path in sorted(READS | OWNER_ONLY):
            error_of(call(other, method, path, world.private), 404, "not_found")
        assert other.get("/api/meetings").json()["meetings"] == [] or all(
            m["id"] != world.private.id for m in other.get("/api/meetings").json()["meetings"]
        )


def test_the_recorder_reads_and_changes_a_moved_meeting_only_while_in_the_owning_team(
    app, session, world
):
    moved(session, world.private, world.x)
    assert visibility.owns(session, world.ada, world.private)
    assert world.private.id in ids_of(session, world.ada)
    ada = sign_in(app, ADA)
    assert ada.get(f"/api/meetings/{world.private.id}").status_code == 200
    # Ada leaves X (no longer an owner of it: Pat is made the owner first, as the pilot admin does)
    teams.transfer_owner(session, world.x.id, world.pat.id)
    session.commit()
    teams.leave(session, world.ada, datetime.now(UTC), team_id=world.x.id)
    session.commit()
    assert world.private.id not in ids_of(session, world.ada)
    assert not visibility.owns(session, world.ada, world.private)
    for method, path in sorted(READS | OWNER_ONLY):
        error_of(call(ada, method, path, world.private), 404, "not_found")
    assert ada.get("/api/meetings").json()["meetings"] == [] or all(
        m["id"] != world.private.id for m in ada.get("/api/meetings").json()["meetings"]
    )
    # the team still has it
    assert world.private.id in ids_of(session, world.pat)
    session.refresh(world.private)
    assert world.private.owner_team_id == world.x.id and world.private.owner_user_id == world.ada.id


def test_the_recorder_reads_it_again_after_rejoining_the_owning_team(session, world):
    moved(session, world.private, world.x)
    session.execute(
        TeamMember.__table__.delete().where(
            TeamMember.user_id == world.ada.id, TeamMember.team_id == world.x.id
        )
    )
    session.commit()
    assert world.private.id not in ids_of(session, world.ada)
    session.add(TeamMember(team_id=world.x.id, user_id=world.ada.id, role="member"))
    session.commit()
    assert world.private.id in ids_of(session, world.ada)
    assert visibility.owns(session, world.ada, world.private)


# ---- who may change it ----


def test_a_plain_member_who_is_not_the_recorder_reads_but_never_changes_a_moved_meeting(
    app, session, world
):
    moved(session, world.private, world.x)
    pat = sign_in(app, PAT)
    for method, path in sorted(READS):
        assert call(pat, method, path, world.private).status_code == 200, (method, path)
    for method, path in sorted(OWNER_ONLY):
        error_of(call(pat, method, path, world.private), 404, "not_found")
    assert not visibility.owns(session, world.pat, world.private)


def test_the_teams_owner_and_admins_change_a_moved_meeting_they_did_not_record(
    app, session, world, dee
):
    moved(session, world.private, world.x)
    assert visibility.owns(session, dee, world.private)
    assert visibility.owns(session, world.ada, world.private)
    teams.transfer_owner(session, world.x.id, world.pat.id)  # Pat is now the owner of X
    session.commit()
    assert visibility.owns(session, world.pat, world.private)
    admin = sign_in(app, DEE)
    detail = admin.get(f"/api/meetings/{world.private.id}").json()
    assert detail["can_delete"] is True and detail["can_regenerate"] is True
    assert detail["shared_by"] == "owner"  # still the recorder's name: Ada is a member


def test_an_admin_of_a_team_that_does_not_own_the_meeting_changes_nothing(app, session, world):
    moved(session, world.private, world.x)
    other_admin = make_user(session, "other-admin@example.test")
    team_w = new_team(session, other_admin, "W")
    session.add(TeamMember(team_id=team_w.id, user_id=world.pat.id, role="member"))
    session.commit()
    assert not visibility.owns(session, other_admin, world.private)
    handle = sign_in(app, "other-admin@example.test")
    for method, path in sorted(READS | OWNER_ONLY):
        error_of(call(handle, method, path, world.private), 404, "not_found")


def test_a_shared_meeting_is_still_changed_by_its_recorder_only(session, world, dee):
    """Sharing is not moving: the team's admins do not gain a write over a meeting only SHARED."""
    assert world.in_x.shared_team_id == world.x.id
    assert visibility.owns(session, world.ada, world.in_x)
    assert not visibility.owns(session, dee, world.in_x)
    assert not visibility.owns(session, world.pat, world.in_x)


def test_a_private_meeting_is_changed_by_its_recorder_only(session, world, dee):
    assert visibility.owns(session, world.ada, world.private)
    assert not visibility.owns(session, dee, world.private)


# ---- the Move route ----


def test_a_recorder_moves_a_meeting_to_a_team_they_belong_to(app, session, world):
    ada = sign_in(app, ADA)
    body = move(ada, world.in_x, world.x).json()  # shared to X, now owned by X
    session.refresh(world.in_x)
    assert world.in_x.owner_team_id == world.x.id and world.in_x.shared_team_id is None
    assert body["owned_by_team"] is True and body["team"] == {"id": world.x.id, "name": "X"}
    pat = sign_in(app, PAT)
    assert pat.get(f"/api/meetings/{world.in_x.id}").json()["owned_by_team"] is True
    assert world.in_x.id in ids_of(session, world.pat)


def test_moving_a_private_meeting_makes_it_readable_by_the_team_only(app, session, world):
    assert move(sign_in(app, ADA), world.private, world.x).status_code == 200
    assert world.private.id in ids_of(session, world.pat)
    assert world.private.id not in ids_of(session, world.quinn)


def test_moving_twice_to_the_same_team_is_fine_and_to_another_is_refused(app, session, world):
    ada = sign_in(app, ADA)
    other = new_team(session, world.ada, "Other")
    assert move(ada, world.private, world.x).status_code == 200
    assert move(ada, world.private, world.x).status_code == 200
    error_of(move(ada, world.private, other), 409, "already_moved")
    session.refresh(world.private)
    assert world.private.owner_team_id == world.x.id


def test_the_recorder_cannot_move_into_a_team_they_are_not_in(app, session, world):
    ada = sign_in(app, ADA)
    error_of(move(ada, world.private, world.y), 404, "not_found")
    error_of(move(ada, world.private, world.z), 404, "not_found")
    session.refresh(world.private)
    assert world.private.owner_team_id is None


def test_a_personal_team_is_not_a_place_to_move_to(app, session, world):
    personal = teams.personal_team(session, world.ada.id)
    ada = sign_in(app, ADA)
    error_of(move(ada, world.private, personal), 409, "personal_team")


def test_nobody_but_the_recorder_moves_a_not_yet_moved_meeting(app, session, world, dee):
    for email in (PAT, DEE, QUINN):
        handle = sign_in(app, email)
        error_of(move(handle, world.in_x, world.x), 404, "not_found")
        error_of(move(handle, world.private, world.x), 404, "not_found")
    session.refresh(world.private)
    assert world.private.owner_team_id is None


def test_only_a_ready_meeting_can_be_moved(app, session, world, template):
    waiting = ready(session, template, status="bot_sent", step="waiting")
    error_of(move(sign_in(app, ADA), waiting, world.x), 409, "meeting_not_ready")


def test_moving_is_refused_while_the_switch_is_off(app, session, world, monkeypatch):
    monkeypatch.setattr(teams, "multi_team_enabled", lambda: False)
    error_of(move(sign_in(app, ADA), world.private, world.x), 409, "multi_team_disabled")


def test_a_moved_meeting_cannot_be_shared_or_unshared(app, session, world):
    ada = sign_in(app, ADA)
    move(ada, world.private, world.x)
    error_of(
        ada.post(f"/api/meetings/{world.private.id}/share", {"team_id": world.x.id}),
        409,
        "meeting_team_owned",
    )
    error_of(ada.delete(f"/api/meetings/{world.private.id}/share"), 409, "meeting_team_owned")


# ---- moving back ----


def test_the_owner_or_an_admin_moves_a_meeting_back_to_its_recorder(app, session, world, dee):
    move(sign_in(app, ADA), world.private, world.x)
    admin = sign_in(app, DEE)
    body = admin.post(f"/api/meetings/{world.private.id}/move-back").json()
    assert body["owned_by_team"] is False and body["team"] is None
    session.refresh(world.private)
    assert world.private.owner_team_id is None and world.private.shared_team_id is None
    assert world.private.id not in ids_of(session, world.pat)  # private to Ada again
    assert world.private.id in ids_of(session, world.ada)


def test_the_recorder_cannot_pull_a_moved_meeting_back_and_a_plain_member_cannot_either(
    app, session, world
):
    """Pat owns W; Ada is a plain member of it and moves her meeting there."""
    team_w = new_team(session, world.pat, "W", world.ada)
    move(sign_in(app, ADA), world.private, team_w)
    path = f"/api/meetings/{world.private.id}/move-back"
    error_of(sign_in(app, ADA).post(path), 403, "team_forbidden")
    error_of(sign_in(app, QUINN).post(path), 404, "not_found")
    session.refresh(world.private)
    assert world.private.owner_team_id == team_w.id
    assert sign_in(app, PAT).post(path).status_code == 200  # the team's owner may


def test_moving_back_a_meeting_that_was_never_moved_is_refused(app, session, world):
    error_of(
        sign_in(app, ADA).post(f"/api/meetings/{world.private.id}/move-back"), 409, "not_moved"
    )


def test_a_meeting_whose_recorder_is_gone_cannot_be_moved_back(app, session, world, dee):
    moved(session, world.private, world.x)
    world.ada.status = "deleted"
    session.commit()
    error_of(
        sign_in(app, DEE).post(f"/api/meetings/{world.private.id}/move-back"), 409, "recorder_gone"
    )


# ---- the share route takes a team ----


def test_share_takes_the_team_in_the_request(app, session, world, template):
    mine = ready(session, template, owner_user_id=world.pat.id)
    pat = sign_in(app, PAT)
    assert pat.post(f"/api/meetings/{mine.id}/share", {"team_id": world.y.id}).status_code == 200
    session.refresh(mine)
    assert mine.shared_team_id == world.y.id
    assert mine.id in ids_of(session, world.quinn)  # Y members read it
    assert mine.id not in ids_of(session, world.ada)  # X members do not
    # another team: it moves from Y to X (one team per meeting)
    assert pat.post(f"/api/meetings/{mine.id}/share", {"team_id": world.x.id}).status_code == 200
    session.refresh(mine)
    assert mine.shared_team_id == world.x.id
    assert mine.id not in ids_of(session, world.quinn)


def test_share_to_a_team_the_recorder_is_not_in_is_a_404(app, session, world):
    ada = sign_in(app, ADA)
    error_of(
        ada.post(f"/api/meetings/{world.private.id}/share", {"team_id": world.y.id}),
        404,
        "not_found",
    )
    session.refresh(world.private)
    assert world.private.shared_team_id is None


def test_share_to_a_personal_team_is_refused_when_named(app, session, world):
    personal = teams.personal_team(session, world.ada.id)
    error_of(
        sign_in(app, ADA).post(f"/api/meetings/{world.private.id}/share", {"team_id": personal.id}),
        409,
        "personal_team",
    )


def test_share_with_no_team_given_is_what_the_old_frontend_does(app, session, world):
    """Ada has exactly one ordinary team, X: the default, as today."""
    ada = sign_in(app, ADA)
    assert ada.post(f"/api/meetings/{world.private.id}/share").status_code == 200
    session.refresh(world.private)
    assert world.private.shared_team_id == world.x.id


def test_share_with_no_team_given_and_two_ordinary_teams_asks_which(app, session, world, template):
    mine = ready(session, template, owner_user_id=world.pat.id)
    error_of(sign_in(app, PAT).post(f"/api/meetings/{mine.id}/share"), 422, "team_required")
    session.refresh(mine)
    assert mine.shared_team_id is None  # never quietly the personal team


def test_a_person_with_no_ordinary_team_who_shares_with_none_given_changes_nothing_visible(
    app, session, world, template
):
    mine = ready(session, template, owner_user_id=world.zed.id)
    handle = sign_in(app, ZED)
    assert handle.post(f"/api/meetings/{mine.id}/share").status_code == 200
    session.refresh(mine)
    assert mine.shared_team_id == world.z.id


# ---- account deletion and team deletion ----


def test_when_the_recorder_deletes_their_account_the_team_keeps_a_moved_meeting(
    app, session, world
):
    move(sign_in(app, ADA), world.private, world.x)
    gone = session.get(User, world.ada.id)
    deletion.purge_user(session, gone, NoVexa(), force=True)
    kept = session.get(Meeting, world.private.id)
    assert kept is not None and kept.owner_team_id == world.x.id
    assert world.private.id in ids_of(session, world.pat)  # the team still reads it
    # the recorder is a "Former member": no name, no email, no way to sign in again
    former = session.get(User, kept.owner_user_id)
    assert former.status == "deleted" and former.purged_at is not None
    assert former.name is None and former.password_hash is None and former.google_sub is None
    assert "owner@example.test" not in former.email
    pat = sign_in(app, PAT)
    detail = pat.get(f"/api/meetings/{world.private.id}").json()
    assert detail["shared_by"] == "Former member"
    assert "owner@example.test" not in str(detail)
    # what was only Ada's goes with her: the meeting nobody moved
    assert session.get(Meeting, world.in_x.id) is None


def test_deleting_the_account_still_deletes_private_and_shared_meetings(session, world):
    deletion.purge_user(session, session.get(User, world.ada.id), NoVexa(), force=True)
    assert session.get(Meeting, world.private.id) is None
    assert session.get(Meeting, world.in_x.id) is None
    assert session.get(Meeting, world.in_z.id) is not None  # Zed's, untouched


def test_the_purged_account_is_not_purged_twice_and_its_email_is_free_again(session, world):
    moved(session, world.private, world.x)
    gone = session.get(User, world.ada.id)
    deletion.purge_user(session, gone, NoVexa(), force=True)
    assert deletion.purge_deleted(session, NoVexa()) == 0  # the hollow row is not a pending purge
    assert session.scalar(select(User.id).where(User.email == ADA)) is None


def test_a_moved_meeting_goes_with_its_team_when_the_last_member_deletes_their_account(
    session, world
):
    """Ada is alone in team A: deleting her account deletes the team and the meeting moved to it."""
    alone = new_team(session, world.ada, "Alone")
    world.private.owner_team_id = alone.id
    session.commit()
    meeting_id = world.private.id
    deletion.purge_user(session, session.get(User, world.ada.id), NoVexa(), force=True)
    assert session.get(Meeting, meeting_id) is None


def test_a_moved_meeting_returns_to_its_recorder_when_the_team_is_deleted_and_they_still_exist(
    session, world
):
    """Pat owns W alone; Ada's meeting was moved to W, then Ada left. Pat deletes their account:
    W goes, the meeting is Ada's private meeting again (her account still exists)."""
    team_w = new_team(session, world.pat, "W", world.ada)
    world.private.owner_team_id = team_w.id
    session.commit()
    teams.leave(session, world.ada, datetime.now(UTC), team_id=team_w.id)
    session.commit()
    assert world.private.id not in ids_of(session, world.ada)
    deletion.purge_user(session, session.get(User, world.pat.id), NoVexa(), force=True)
    session.refresh(world.private)
    assert world.private.owner_team_id is None and world.private.owner_user_id == world.ada.id
    assert world.private.id in ids_of(session, world.ada)


def test_a_moved_meeting_of_a_former_member_is_deleted_with_its_team(session, world):
    """Ada deleted her account earlier (the team kept the meeting). Then the last member of the
    team deletes theirs: nobody is left to give it back to, so it goes with the team."""
    team_w = new_team(session, world.pat, "W", world.ada)
    world.private.owner_team_id = team_w.id
    session.commit()
    meeting_id = world.private.id
    deletion.purge_user(session, session.get(User, world.ada.id), NoVexa(), force=True)
    assert session.get(Meeting, meeting_id) is not None
    deletion.purge_user(session, session.get(User, world.pat.id), NoVexa(), force=True)
    assert session.get(Meeting, meeting_id) is None


# ---- tasks follow the meeting (switch on) ----


def tasks_of(session, meeting) -> list[Task]:
    return list(session.scalars(select(Task).where(Task.meeting_id == meeting.id)))


def test_moving_re_homes_the_extracted_tasks_to_the_team_and_moving_back_takes_them_home(
    app, session, world
):
    personal = teams.personal_team(session, world.ada.id)
    task = add_task(session, personal, world.private, title="Send the deck", assignee=world.ada)
    other = add_task(session, personal, world.private, title="Book room", assignee=world.pat)
    ada, pat, quinn = sign_in(app, ADA), sign_in(app, PAT), sign_in(app, QUINN)
    assert pat.get("/api/tasks").json()["tasks"] == []

    assert move(ada, world.private, world.x).status_code == 200
    session.expire_all()
    assert {t.team_id for t in tasks_of(session, world.private)} == {world.x.id}
    assert {t["id"] for t in pat.get("/api/tasks").json()["tasks"]} == {task.id, other.id}
    assert quinn.get("/api/tasks").json()["tasks"] == []

    assert ada.post(f"/api/meetings/{world.private.id}/move-back").status_code == 200  # owns X
    session.expire_all()
    assert {t.team_id for t in tasks_of(session, world.private)} == {personal.id}
    assert pat.get("/api/tasks").json()["tasks"] == []  # no longer exposed to the team
    mine = {t["id"] for t in ada.get("/api/tasks").json()["tasks"]}
    assert mine == {task.id, other.id}
    # Pat does not hold a task in a team of one they are not in
    assert session.get(Task, other.id).assignee_user_id is None
    assert session.get(Task, task.id).assignee_user_id == world.ada.id


def test_sharing_and_unsharing_re_home_the_extracted_tasks_without_exposing_them(
    app, session, world, template
):
    personal = teams.personal_team(session, world.ada.id)
    meeting = world.private
    task = add_task(session, personal, meeting, title="Send the deck")
    ada, pat, quinn = sign_in(app, ADA), sign_in(app, PAT), sign_in(app, QUINN)
    assert ada.post(f"/api/meetings/{meeting.id}/share", {"team_id": world.x.id}).status_code == 200
    session.expire_all()
    assert session.get(Task, task.id).team_id == world.x.id
    assert [t["id"] for t in pat.get("/api/tasks").json()["tasks"]] == [task.id]
    assert quinn.get("/api/tasks").json()["tasks"] == []

    assert ada.delete(f"/api/meetings/{meeting.id}/share").status_code == 200
    session.expire_all()
    assert session.get(Task, task.id).team_id == personal.id
    assert pat.get("/api/tasks").json()["tasks"] == []
    error_of(pat.get(f"/api/tasks/{task.id}"), 404, "not_found")


def test_re_homing_clears_the_old_teams_sprint_project_and_assignee(app, session, world):
    from app.models import Project

    personal = teams.personal_team(session, world.ada.id)
    meeting = world.in_x
    project = Project(team_id=world.x.id, name="Launch", color="#0E6B5E")
    session.add(project)
    session.commit()
    task = add_task(
        session, world.x, meeting, title="Ship it", assignee=world.pat, project_id=project.id
    )
    assert sign_in(app, ADA).delete(f"/api/meetings/{meeting.id}/share").status_code == 200
    session.expire_all()
    row = session.get(Task, task.id)
    assert row.team_id == personal.id and row.project_id is None
    assert row.assignee_user_id is None and row.sprint_id is None and row.carried is False


def test_a_manual_task_is_never_re_homed_with_the_meeting(app, session, world):
    manual = add_task(session, world.x, title="Book the room")
    assert sign_in(app, ADA).delete(f"/api/meetings/{world.in_x.id}/share").status_code == 200
    session.expire_all()
    assert session.get(Task, manual.id).team_id == world.x.id


# ---- the mail's "Open notes" button for a moved meeting ----


def test_open_notes_reaches_every_team_member_of_a_moved_meeting_and_nobody_else(
    app, session, world
):
    moved(session, world.in_x, world.x)
    assert visibility.via_team(session, world.pat, world.in_x) is True
    assert visibility.via_team(session, world.quinn, world.in_x) is False
    assert visibility.via_team(session, world.zed, world.in_x) is False


# ---- which team a meeting's extracted tasks are filed in ----


def test_a_meetings_tasks_are_filed_by_owner_then_share_then_home(session, world):
    personal = teams.personal_team(session, world.ada.id)
    assert teams.team_for_meeting(session, world.private).id == personal.id  # private: home
    assert teams.team_for_meeting(session, world.in_x).id == world.x.id  # shared to X
    other = new_team(session, world.ada, "Other")
    moved(session, world.in_x, other)
    assert teams.team_for_meeting(session, world.in_x).id == other.id  # owned beats shared


def test_with_the_switch_off_a_private_meetings_tasks_go_to_the_recorders_default_team(
    session, world, monkeypatch
):
    monkeypatch.setattr(teams, "multi_team_enabled", lambda: False)
    default = teams.team_of(session, world.ada.id)
    assert default.id == world.x.id  # Ada's one ordinary team, as today
    assert teams.team_for_meeting(session, world.private).id == default.id


def test_with_the_switch_off_re_homing_does_nothing(app, session, world, monkeypatch):
    task = add_task(session, world.x, world.in_x, title="Ship it")
    monkeypatch.setattr(teams, "multi_team_enabled", lambda: False)
    assert sign_in(app, ADA).delete(f"/api/meetings/{world.in_x.id}/share").status_code == 200
    session.expire_all()
    assert session.get(Task, task.id).team_id == world.x.id  # the old rule: it stays
