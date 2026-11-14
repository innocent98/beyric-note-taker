"""Multi-team stage 2b: the teams list, creating a team, an explicit team per request, series by
their own team, the per-person invitation caps and the one auto-share team.

Same cast as `tests/test_multi_team_leaks.py` (`world`): Ada records and owns team X; Pat is in X
and Y; Quinn owns Y and is only in Y; Zed owns Z; everyone has a personal team; the switch is on.
The team of a request is the `X-Team-Id` header: explicit per request, nothing kept on the server.
"""

# ruff: noqa: F811  (fixtures are imported by name and used as arguments)
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app import team_invites, teams
from app.models import Project, Series, Task, Team, TeamInvite, TeamMember, UserSettings
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
from tests.test_multi_team_leaks import PAT, QUINN, ZED, multi_team_on, new_team, world  # noqa: F401
from tests.test_series import meeting_in
from tests.test_team_visibility import sign_in

ADA = "owner@example.test"


def h(team) -> dict:
    return {"X-Team-Id": str(team.id if hasattr(team, "id") else team)}


def get(handle, path, team=None):
    return handle.client.get(path, headers=h(team) if team is not None else {})


def call(handle, method, path, team=None, body=None):
    headers = {"X-CSRF-Token": handle.csrf, **(h(team) if team is not None else {})}
    return handle.client.request(method, path, json=body, headers=headers)


# ---- GET /api/teams ----


def test_the_teams_list_shows_every_membership_with_role_count_personal_and_default(
    app, session, world
):
    body = get(sign_in(app, PAT), "/api/teams").json()
    by_name = {t["name"]: t for t in body["teams"]}
    assert set(by_name) == {"X", "Y", "My team"}  # the personal team is named "My team"
    assert by_name["X"] | {} == {
        "id": world.x.id,
        "name": "X",
        "role": "member",
        "member_count": 2,
        "is_personal": False,
        "is_default": False,
        "can_manage": False,
    }
    personal = by_name["My team"]
    assert personal["is_personal"] is True and personal["role"] == "owner"
    assert personal["is_default"] is True  # two ordinary teams: the personal one is the default
    assert body["can_create_more"] == 3


def test_a_person_with_one_ordinary_team_has_it_as_the_default(app, session, world):
    ada = {t["name"]: t for t in get(sign_in(app, ADA), "/api/teams").json()["teams"]}
    assert ada["X"]["is_default"] is True and ada["X"]["role"] == "owner"
    assert ada["My team"]["is_default"] is False


def test_the_teams_list_makes_a_missing_personal_team(app, session, world):
    personal = teams.personal_team(session, world.zed.id)
    session.execute(TeamMember.__table__.delete().where(TeamMember.team_id == personal.id))
    session.delete(personal)
    session.commit()
    assert teams.personal_team(session, world.zed.id) is None
    body = get(sign_in(app, ZED), "/api/teams").json()
    assert sum(t["is_personal"] for t in body["teams"]) == 1
    assert teams.personal_team(session, world.zed.id) is not None


def test_the_teams_list_needs_a_login_and_never_shows_someone_elses_team(app, session, world):
    quinn = {t["id"] for t in get(sign_in(app, QUINN), "/api/teams").json()["teams"]}
    assert world.x.id not in quinn and world.z.id not in quinn and world.y.id in quinn


# ---- POST /api/teams ----


def test_creating_a_team_makes_the_caller_its_owner(app, session, world):
    pat = sign_in(app, PAT)
    made = call(pat, "POST", "/api/teams", body={"name": "  Side project "})
    assert made.status_code == 201
    body = made.json()
    assert body["name"] == "Side project" and body["role"] == "owner"
    assert body["member_count"] == 1 and body["is_personal"] is False
    assert any(t["id"] == body["id"] for t in get(pat, "/api/teams").json()["teams"])
    assert get(pat, "/api/teams").json()["can_create_more"] == 2
    assert session.get(Team, body["id"]).owner_user_id == world.pat.id


def test_at_most_three_owned_teams_and_the_personal_one_does_not_count(app, session, world):
    pat = sign_in(app, PAT)  # owns none, member of X and Y
    for n in range(3):
        assert call(pat, "POST", "/api/teams", body={"name": f"T{n}"}).status_code == 201
    error_of(call(pat, "POST", "/api/teams", body={"name": "T3"}), 409, "team_limit")
    assert get(pat, "/api/teams").json()["can_create_more"] == 0
    # a team they only belong to never counted: Ada owns X and may still make two more
    ada = sign_in(app, ADA)
    assert call(ada, "POST", "/api/teams", body={"name": "A1"}).status_code == 201
    assert call(ada, "POST", "/api/teams", body={"name": "A2"}).status_code == 201
    error_of(call(ada, "POST", "/api/teams", body={"name": "A3"}), 409, "team_limit")


def test_an_empty_team_name_is_refused(app, session, world):
    error_of(
        call(sign_in(app, PAT), "POST", "/api/teams", body={"name": "   "}), 422, "invalid_name"
    )


def test_creating_a_team_is_refused_while_the_switch_is_off(app, session, world, monkeypatch):
    monkeypatch.setattr(teams, "multi_team_enabled", lambda: False)
    error_of(
        call(sign_in(app, PAT), "POST", "/api/teams", body={"name": "Nope"}),
        409,
        "multi_team_disabled",
    )


# ---- an explicit team per request ----


def test_the_team_route_reads_the_team_named_and_the_default_without_one(app, session, world):
    pat = sign_in(app, PAT)
    assert get(pat, "/api/team", world.x).json()["name"] == "X"
    assert get(pat, "/api/team", world.y).json()["name"] == "Y"
    default = get(pat, "/api/team").json()  # two ordinary teams: the personal one
    assert default["is_personal"] is True and default["name"] == "My team"


def test_a_team_the_caller_is_not_in_is_a_404_on_every_team_route(app, session, world):
    quinn = sign_in(app, QUINN)
    for path in ("/api/team", "/api/team/members", "/api/projects", "/api/sprints", "/api/tasks"):
        error_of(get(quinn, path, world.x), 404, "not_found")
    error_of(get(quinn, "/api/integrations/slack", world.x), 404, "not_found")
    error_of(get(quinn, "/api/series", world.x), 404, "not_found")
    error_of(call(quinn, "PATCH", "/api/team", world.x, {"name": "Mine now"}), 404, "not_found")
    error_of(call(quinn, "POST", "/api/tasks", world.x, {"title": "Sneak"}), 404, "not_found")
    error_of(call(quinn, "POST", "/api/series", world.x, {"title": "Sneak"}), 404, "not_found")
    assert session.get(Team, world.x.id).name == "X"


def test_a_bad_team_header_is_a_validation_error_not_a_server_error(app, session, world):
    pat = sign_in(app, PAT)
    for value in ("abc", "0", "-3", "99999999999999999999"):
        response = pat.client.get("/api/team", headers={"X-Team-Id": value})
        assert response.status_code == 422, value


def test_what_the_team_screen_may_change_depends_on_the_team_named(app, session, world):
    """Pat is a plain member of X and of Y, owner of nothing: no change is allowed in either, but
    in a team Pat owns it is."""
    pat = sign_in(app, PAT)
    error_of(call(pat, "PATCH", "/api/team", world.x, {"name": "X2"}), 403, "team_forbidden")
    error_of(call(pat, "PATCH", "/api/team", world.y, {"name": "Y2"}), 403, "team_forbidden")
    mine = call(pat, "POST", "/api/teams", body={"name": "Mine"}).json()
    ok = call(pat, "PATCH", "/api/team", mine["id"], {"name": "Mine 2"})
    assert ok.status_code == 200 and ok.json()["name"] == "Mine 2"
    assert session.get(Team, world.x.id).name == "X"


def test_members_projects_and_integrations_follow_the_team_named(app, session, world):
    session.add_all([Project(team_id=world.x.id, name="XP", color="#000000", keywords=[])])
    session.add_all([Project(team_id=world.y.id, name="YP", color="#000000", keywords=[])])
    session.commit()
    pat = sign_in(app, PAT)
    names = lambda r: sorted(p["name"] for p in r.json()["projects"])  # noqa: E731
    assert names(get(pat, "/api/projects", world.x)) == ["XP"]
    assert names(get(pat, "/api/projects", world.y)) == ["YP"]
    members_x = {m["email"] for m in get(pat, "/api/team/members", world.x).json()["members"]}
    members_y = {m["email"] for m in get(pat, "/api/team/members", world.y).json()["members"]}
    assert members_x == {ADA, PAT} and members_y == {QUINN, PAT}


def test_the_tasks_board_is_one_teams_with_the_header_and_all_without_it(
    app, session, template, world
):
    task_x = add_task(session, world.x, title="Book the room")
    task_y = add_task(session, world.y, title="Pay the invoice")
    pat = sign_in(app, PAT)
    ids = lambda r: {t["id"] for t in r.json()["tasks"]}  # noqa: E731
    assert ids(get(pat, "/api/tasks", world.x)) == {task_x.id}
    assert ids(get(pat, "/api/tasks", world.y)) == {task_y.id}
    assert ids(get(pat, "/api/tasks")) == {task_x.id, task_y.id}  # no team: as before
    people_y = {p["name"] for p in get(pat, "/api/tasks", world.y).json()["people"]}
    assert len(people_y) == 2


def test_a_task_made_with_a_team_header_belongs_to_that_team_only(app, session, world):
    pat, quinn, ada = sign_in(app, PAT), sign_in(app, QUINN), sign_in(app, ADA)
    made = call(pat, "POST", "/api/tasks", world.y, {"title": "Pay the invoice"})
    assert made.status_code == 201
    task = session.get(Task, made.json()["id"])
    assert task.team_id == world.y.id
    assert any(t["id"] == task.id for t in get(quinn, "/api/tasks").json()["tasks"])
    assert all(t["id"] != task.id for t in get(ada, "/api/tasks").json()["tasks"])


def test_the_old_frontend_with_one_team_and_no_header_is_unchanged(app, session, world):
    ada = sign_in(app, ADA)  # owns X, her only ordinary team: the default
    assert get(ada, "/api/team").json()["name"] == "X"
    assert get(ada, "/api/team/members").status_code == 200
    made = call(ada, "POST", "/api/tasks", body={"title": "Old client task"})
    assert made.status_code == 201
    assert session.get(Task, made.json()["id"]).team_id == world.x.id
    created = call(ada, "POST", "/api/series", body={"title": "Standup"})
    assert created.status_code == 201
    assert session.get(Series, created.json()["id"]).team_id == world.x.id


# ---- series: by their own team, listed and created per team ----


def test_a_series_made_with_a_team_header_lives_in_that_team(app, session, world):
    pat, quinn, ada = sign_in(app, PAT), sign_in(app, QUINN), sign_in(app, ADA)
    made = call(pat, "POST", "/api/series", world.y, {"title": "Client sync"})
    assert made.status_code == 201
    row = session.get(Series, made.json()["id"])
    assert row.team_id == world.y.id and row.owner_user_id == world.pat.id
    assert [c["id"] for c in get(pat, "/api/series", world.y).json()["series"]] == [row.id]
    assert get(pat, "/api/series", world.x).json()["series"] == []
    # Pat made it, so Pat sees it by id; Quinn (in Y) sees it only once a readable meeting exists
    assert get(pat, f"/api/series/{row.id}").status_code == 200
    error_of(get(ada, f"/api/series/{row.id}"), 404, "not_found")  # Ada is not in Y
    assert quinn is not None


def test_a_meeting_shared_to_another_team_is_not_part_of_this_teams_series(
    app, session, template, world
):
    weekly = Series(team_id=world.x.id, owner_user_id=world.ada.id, title="Monday planning")
    session.add(weekly)
    session.commit()
    meeting = meeting_in(session, template, weekly)
    meeting.shared_team_id = world.y.id  # the recorder shared it to Y, not to the series' team X
    session.commit()
    quinn, pat = sign_in(app, QUINN), sign_in(app, PAT)
    # Quinn reads the meeting (shared to Y) but is not in X: the series is not Quinn's to see
    assert quinn.get(f"/api/meetings/{meeting.id}").status_code == 200
    error_of(quinn.get(f"/api/series/{weekly.id}"), 404, "not_found")
    assert get(quinn, "/api/series", world.y).json()["series"] == []
    # Pat is in both: the series exists for Pat (X's), and shows the meeting only as Pat may read it
    page = pat.get(f"/api/series/{weekly.id}").json()
    assert [m["id"] for m in page["past_meetings"]] == [meeting.id]
    detail = pat.get(f"/api/meetings/{meeting.id}").json()
    assert detail["series"] is not None


def test_a_series_page_never_lists_a_meeting_its_viewer_may_not_read(app, session, template, world):
    weekly = Series(team_id=world.x.id, owner_user_id=world.ada.id, title="Monday planning")
    session.add(weekly)
    session.commit()
    private = meeting_in(session, template, weekly)
    shared = meeting_in(session, template, weekly)
    shared.shared_team_id = world.x.id
    session.commit()
    page = sign_in(app, PAT).get(f"/api/series/{weekly.id}").json()
    listed = {m["id"] for m in page["past_meetings"]}
    assert shared.id in listed and private.id not in listed


# ---- invitations: a limit per person across all their teams ----


def _spread_invites(session, world, now, *, minutes_ago, revoked=False):
    """MAX_PER_PERSON invitations by Pat spread over three teams, so that no single team is over
    its own caps (7 + 7 + 6 pending or created): only the per-person cap can refuse the next."""
    boxes = [new_team(session, world.pat, f"P{n}") for n in range(3)]
    for n in range(team_invites.MAX_PER_PERSON_PER_HOUR):
        session.add(
            TeamInvite(
                team_id=boxes[n % 3].id,
                email=f"x{n}@example.test",
                role="member",
                invited_by=world.pat.id,
                token_hash=f"h{n}",
                expires_at=now + timedelta(days=7),
                created_at=now - timedelta(minutes=minutes_ago),
                revoked_at=now - timedelta(minutes=1) if revoked else None,
            )
        )
    session.commit()
    return boxes, new_team(session, world.pat, "Fresh")


def _refused(session, team, person, now) -> bool:
    try:
        team_invites._check_caps(session, team.id, person.id, now)
    except Exception as exc:
        assert getattr(exc, "code", None) == "invite_limit"
        return True
    return False


def test_the_per_person_hourly_cap_counts_every_team_the_person_invites_from(app, session, world):
    now = datetime.now(UTC)
    boxes, fresh = _spread_invites(session, world, now, minutes_ago=5)
    assert all(not _refused(session, box, world.ada, now) for box in boxes)  # no team is over
    assert _refused(session, fresh, world.pat, now)  # but Pat is, across all of them
    assert not _refused(session, fresh, world.ada, now)  # someone else is not
    # a day and a bit later every window has passed
    assert not _refused(session, fresh, world.pat, now + timedelta(days=1, minutes=1))


def test_the_per_person_daily_cap_counts_revoked_ones_in_every_team(app, session, world):
    now = datetime.now(UTC)
    _, fresh = _spread_invites(session, world, now, minutes_ago=180, revoked=True)
    assert _refused(session, fresh, world.pat, now)  # three hours ago: outside the hour, in the day
    assert not _refused(session, fresh, world.ada, now)


# ---- one auto-share team per person ----


def put_auto(handle, team_id):
    return call(handle, "PUT", "/api/settings/auto-share", body={"team_id": team_id})


def test_the_auto_share_team_is_chosen_read_and_cleared(app, session, world):
    pat = sign_in(app, PAT)
    assert get(pat, "/api/settings/auto-share").json() == {"team_id": None}
    assert put_auto(pat, world.y.id).json() == {"team_id": world.y.id}
    assert get(pat, "/api/settings/auto-share").json() == {"team_id": world.y.id}
    assert put_auto(pat, world.x.id).json() == {"team_id": world.x.id}  # one at most: it moved
    assert put_auto(pat, None).json() == {"team_id": None}


def test_the_auto_share_team_must_be_one_the_person_belongs_to_and_not_personal(
    app, session, world
):
    ada = sign_in(app, ADA)
    error_of(put_auto(ada, world.y.id), 404, "not_found")
    error_of(put_auto(ada, 999999), 404, "not_found")
    error_of(put_auto(ada, teams.personal_team(session, world.ada.id).id), 409, "personal_team")


def test_new_meetings_go_to_the_chosen_team_once_when_the_notes_are_first_written(
    app, session, template, world
):
    mine = ready(session, template, owner_user_id=world.pat.id)  # Pat: plain member of X and Y
    teams.apply_share_default(session, mine)
    assert mine.shared_team_id is None  # nothing chosen, a member: as today (never)
    assert put_auto(sign_in(app, PAT), world.y.id).status_code == 200
    teams.apply_share_default(session, mine)
    assert mine.shared_team_id == world.y.id  # their own choice, whatever their role


def test_leaving_the_chosen_team_ends_the_auto_share_and_the_team_default_is_untouched(
    app, session, template, world
):
    sign = sign_in(app, PAT)
    put_auto(sign, world.y.id)
    session.execute(
        TeamMember.__table__.delete().where(
            TeamMember.team_id == world.y.id, TeamMember.user_id == world.pat.id
        )
    )
    session.commit()
    assert teams.auto_share_team(session, world.pat) is None
    mine = ready(session, template, owner_user_id=world.pat.id)
    teams.apply_share_default(session, mine)
    assert mine.shared_team_id is None
    # a team's own default (owners and admins) still works for someone with no choice
    world.x.share_new_meetings = True
    session.commit()
    ada_meeting = ready(session, template)
    teams.apply_share_default(session, ada_meeting)
    assert ada_meeting.shared_team_id == world.x.id


def test_the_auto_share_choice_is_cleared_when_the_team_is_deleted(app, session, world):
    put_auto(sign_in(app, PAT), world.y.id)
    session.execute(TeamMember.__table__.delete().where(TeamMember.team_id == world.y.id))
    session.execute(Team.__table__.delete().where(Team.id == world.y.id))
    session.commit()
    row = session.scalars(select(UserSettings).where(UserSettings.user_id == world.pat.id)).one()
    assert row.auto_share_team_id is None


# ---- a moved meeting of a deleted recorder is still the team's to work on ----


def _recorder_deleted(session, world, meeting):
    """Ada deleted her account; her hollow row stays `deleted` and the team keeps the meeting."""
    meeting.owner_team_id = world.x.id
    meeting.shared_team_id = None
    world.ada.status = "deleted"
    session.commit()


def test_the_worker_still_translates_and_re_reads_a_moved_meeting_of_a_deleted_recorder(
    session, world
):
    from app import translation, workspace
    from app.models import MeetingTranslation

    _recorder_deleted(session, world, world.private)
    world.private.extraction_status = "pending"
    world.private.notes = "## Summary\nShort.\n"
    session.add(
        MeetingTranslation(
            meeting_id=world.private.id,
            language="es",
            status="translating",
            source_hash="s",
            model="m",
        )
    )
    session.commit()
    now = datetime.now(UTC)
    claimed = workspace.claim_next(session, now)
    assert (
        claimed is not None and claimed.id == world.private.id
    )  # re-read for tasks after a rewrite
    session.commit()
    row = translation._claim(session, now)
    assert row is not None and row.meeting_id == world.private.id  # not stuck "translating"


def test_the_worker_still_leaves_alone_an_unmoved_meeting_of_a_deleted_recorder(session, world):
    from app import translation, workspace

    world.private.extraction_status = "pending"
    world.private.notes = "## Summary\nShort.\n"
    world.ada.status = "deleted"
    session.commit()
    assert workspace.claim_next(session, datetime.now(UTC)) is None  # the purge deletes it
    assert translation._claim(session, datetime.now(UTC)) is None


def test_repeat_topics_still_read_a_moved_meeting_of_a_deleted_recorder(session, template, world):
    from app import topics

    weekly = Series(team_id=world.x.id, owner_user_id=world.pat.id, title="Monday planning")
    session.add(weekly)
    session.commit()
    meeting = meeting_in(session, template, weekly)
    _recorder_deleted(session, world, meeting)
    assert [m.id for m in topics.source_meetings(session, weekly)] == [meeting.id]


def test_nothing_more_is_sent_for_a_meeting_of_a_team_that_goes_with_the_deleted_account(
    session, world
):
    """The team's only live member is the account being deleted: the purge will give the meeting
    back or delete it, so the worker must not translate or re-read it in the meantime."""
    from app import translation, workspace

    alone = new_team(session, world.ada, "Alone")
    world.private.owner_team_id = alone.id
    world.private.extraction_status = "pending"
    world.private.notes = "## Summary\nShort.\n"
    world.ada.status = "deleted"
    session.commit()
    assert workspace.claim_next(session, datetime.now(UTC)) is None
    assert translation._claim(session, datetime.now(UTC)) is None
