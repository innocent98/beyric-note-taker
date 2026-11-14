"""The lists and the counts follow the active team (the `X-Team-Id` header).

Written before the code. The Meetings list (the dashboard at `/`), the Decisions log, the Review
list and the two sidebar badges (`badges.tasks_new`, `badges.review_open`) show ONE team at a time:

* an ordinary team X: the meetings shared to X or owned by (moved to) X, whoever recorded them;
* the person's personal team: only the meetings they recorded that are neither shared nor moved.

The header only NARROWS a list, through `app/visibility.py`: it never widens who may read a meeting
(a meeting opened by its link still opens whatever team is active). With no header, the person's
default team applies while `MULTI_TEAM_ENABLED` is on; with it off nothing changes.

Cast (as `tests/test_multi_team_leaks.py`): Ada owns X; Pat is in X and Y and has a personal team;
Quinn owns Y; Zed owns Z. Pat's meetings: `p_private` (nobody has it), `p_in_x` (shared to X),
`p_moved_y` (moved to Y). Ada's `in_x` is shared to X; Quinn's `q_in_y` to Y.
"""

# ruff: noqa: F811  (fixtures are imported by name and used as arguments)
import pytest

from app import teams
from app.models import Decision, ReviewItem
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
from tests.test_multi_team_leaks import PAT, multi_team_on, world  # noqa: F401
from tests.test_team_visibility import sign_in

ADA = "owner@example.test"


def h(team) -> dict:
    return {"X-Team-Id": str(team.id if hasattr(team, "id") else team)}


def get(handle, path, team=None):
    return handle.client.get(path, headers=h(team) if team is not None else {})


@pytest.fixture
def cast(session, template, world):
    """`world` plus Pat's and Quinn's meetings, one decision and one review item each."""
    w = world
    w.personal_pat = teams.personal_team(session, w.pat.id)
    w.personal_ada = teams.personal_team(session, w.ada.id)
    w.p_private = ready(session, template, owner_user_id=w.pat.id, title="Pat private")
    w.p_in_x = ready(session, template, owner_user_id=w.pat.id, shared_team_id=w.x.id)
    w.p_moved_y = ready(session, template, owner_user_id=w.pat.id, owner_team_id=w.y.id)
    w.q_in_y = ready(session, template, owner_user_id=w.quinn.id, shared_team_id=w.y.id)
    everything = [w.in_x, w.private, w.p_private, w.p_in_x, w.p_moved_y, w.q_in_y]
    for meeting in everything:
        session.add(
            Decision(
                meeting_id=meeting.id,
                text=f"Decision of {meeting.id}",
                position=0,
                fingerprint=f"d{meeting.id}",
            )
        )
    for meeting in (w.p_private, w.p_in_x, w.p_moved_y):  # the recorder's own review items
        session.add(
            ReviewItem(
                meeting_id=meeting.id,
                owner_user_id=w.pat.id,
                text=f"Check {meeting.id}",
                position=0,
                fingerprint=f"r{meeting.id}",
            )
        )
    session.commit()
    return w


def meeting_ids(handle, team=None, path="/api/meetings") -> list[int]:
    response = get(handle, path, team)
    assert response.status_code == 200, response.text
    return sorted(m["id"] for m in response.json()["meetings"])


# ---- the Meetings list and the dashboard ----


def test_the_personal_team_lists_only_unshared_unmoved_meetings_the_person_recorded(app, cast):
    pat = sign_in(app, PAT)
    assert meeting_ids(pat, cast.personal_pat) == [cast.p_private.id]


def test_an_ordinary_team_lists_its_meetings_whoever_recorded_them(app, cast):
    pat = sign_in(app, PAT)
    # X: Pat's own shared one and Ada's. Y: the one Pat moved to it and Quinn's.
    assert meeting_ids(pat, cast.x) == sorted([cast.p_in_x.id, cast.in_x.id])
    assert meeting_ids(pat, cast.y) == sorted([cast.p_moved_y.id, cast.q_in_y.id])


def test_the_three_lists_of_a_person_in_two_teams_do_not_overlap_or_lose_anything(app, cast):
    pat = sign_in(app, PAT)
    each = [meeting_ids(pat, t) for t in (cast.personal_pat, cast.x, cast.y)]
    flat = [i for ids in each for i in ids]
    assert len(flat) == len(set(flat))  # no meeting in two lists
    # Everything Pat may read is on exactly one team's list.
    assert sorted(flat) == sorted(
        [cast.p_private.id, cast.p_in_x.id, cast.p_moved_y.id, cast.in_x.id, cast.q_in_y.id]
    )


def test_no_header_means_the_default_team_while_the_switch_is_on(app, cast):
    # Pat has two ordinary teams: the personal one is the default.
    assert meeting_ids(sign_in(app, PAT)) == [cast.p_private.id]
    # Ada has exactly one ordinary team, X: it is the default.
    assert meeting_ids(sign_in(app, ADA)) == sorted([cast.in_x.id, cast.p_in_x.id])


def test_with_the_switch_off_the_list_is_what_it_always_was(app, cast, monkeypatch):
    monkeypatch.setattr(teams, "multi_team_enabled", lambda: False)
    assert meeting_ids(sign_in(app, PAT)) == sorted(
        [cast.p_private.id, cast.p_in_x.id, cast.p_moved_y.id, cast.in_x.id, cast.q_in_y.id]
    )


def test_a_team_the_caller_is_not_in_is_404_and_a_team_never_widens_the_read(app, cast):
    pat = sign_in(app, PAT)
    error_of(get(pat, "/api/meetings", cast.z), 404, "not_found")
    # Ada's private meeting is on nobody's list but hers, whatever team Pat names.
    for team in (cast.personal_pat, cast.x, cast.y):
        assert cast.private.id not in meeting_ids(pat, team)
        assert cast.in_z.id not in meeting_ids(pat, team)


def test_a_meeting_opened_by_its_link_opens_whatever_team_is_active(app, cast):
    pat = sign_in(app, PAT)
    # Pat is working on the personal team and opens Ada's meeting of X by its link.
    for team in (cast.personal_pat, cast.y):
        response = get(pat, f"/api/meetings/{cast.in_x.id}", team)
        assert response.status_code == 200, response.text
    error_of(get(pat, f"/api/meetings/{cast.private.id}", cast.x), 404, "not_found")


def test_has_more_and_paging_work_inside_a_team(app, cast, session, template):
    extra = [ready(session, template, owner_user_id=cast.pat.id) for _ in range(3)]
    pat = sign_in(app, PAT)
    first = get(pat, "/api/meetings?limit=2", cast.personal_pat).json()
    assert first["has_more"] is True and len(first["meetings"]) == 2
    second = get(
        pat, f"/api/meetings?limit=2&before_id={first['next_before_id']}", cast.personal_pat
    ).json()
    shown = [m["id"] for m in first["meetings"] + second["meetings"]]
    assert sorted(shown) == sorted([cast.p_private.id, *(m.id for m in extra)])


def test_unsharing_moves_a_meeting_between_the_teams_lists(app, cast, session):
    pat = sign_in(app, PAT)
    cast.p_in_x.shared_team_id = None
    session.commit()
    assert cast.p_in_x.id in meeting_ids(pat, cast.personal_pat)
    assert cast.p_in_x.id not in meeting_ids(pat, cast.x)


# ---- decisions ----


def decision_meetings(handle, team=None) -> list[int]:
    response = get(handle, "/api/decisions", team)
    assert response.status_code == 200, response.text
    return sorted(g["meeting"]["id"] for g in response.json()["groups"])


def test_decisions_follow_the_team_of_their_meeting(app, cast):
    pat = sign_in(app, PAT)
    assert decision_meetings(pat, cast.personal_pat) == [cast.p_private.id]
    assert decision_meetings(pat, cast.x) == sorted([cast.p_in_x.id, cast.in_x.id])
    assert decision_meetings(pat, cast.y) == sorted([cast.p_moved_y.id, cast.q_in_y.id])
    error_of(get(pat, "/api/decisions", cast.z), 404, "not_found")


def test_the_decision_counts_match_the_active_teams_list(app, cast):
    pat = sign_in(app, PAT)
    for team, expected in ((cast.personal_pat, 1), (cast.x, 2), (cast.y, 2)):
        body = get(pat, "/api/decisions", team).json()
        assert body["counts"]["all"] == expected
        assert sum(len(g["decisions"]) for g in body["groups"]) == expected


def test_decisions_with_no_header_use_the_default_team(app, cast):
    assert decision_meetings(sign_in(app, PAT)) == [cast.p_private.id]


# ---- review ----


def review_items(handle, team=None) -> dict:
    response = get(handle, "/api/review", team)
    assert response.status_code == 200, response.text
    return response.json()


def test_review_follows_the_team_of_the_meeting_and_its_count_matches(app, cast):
    pat = sign_in(app, PAT)
    for team, meeting in (
        (cast.personal_pat, cast.p_private),
        (cast.x, cast.p_in_x),
        (cast.y, cast.p_moved_y),
    ):
        body = review_items(pat, team)
        assert [i["meeting"]["id"] for i in body["items"]] == [meeting.id]
        assert body["open_count"] == 1
    error_of(get(pat, "/api/review", cast.z), 404, "not_found")


def test_review_with_no_header_uses_the_default_team(app, cast):
    body = review_items(sign_in(app, PAT))
    assert [i["meeting"]["id"] for i in body["items"]] == [cast.p_private.id]


def test_a_review_item_is_only_ever_the_recorders(app, cast):
    ada = sign_in(app, ADA)
    for team in (cast.x, cast.personal_ada):
        assert review_items(ada, team)["items"] == []


# ---- the sidebar badges ----


def badges(handle, team=None) -> dict:
    response = get(handle, "/api/session", team)
    assert response.status_code == 200, response.text
    return response.json()["badges"]


def test_the_review_badge_counts_only_the_active_team(app, cast):
    pat = sign_in(app, PAT)
    assert badges(pat, cast.personal_pat)["review_open"] == 1
    assert badges(pat, cast.x)["review_open"] == 1
    assert badges(pat, cast.y)["review_open"] == 1
    cast_checked = review_items(pat, cast.x)["items"][0]["id"]
    ok = pat.client.post(
        f"/api/review/{cast_checked}/check", headers={"X-CSRF-Token": pat.csrf, **h(cast.x)}
    )
    assert ok.status_code in (200, 204), ok.text
    assert badges(pat, cast.x)["review_open"] == 0
    assert badges(pat, cast.y)["review_open"] == 1


def test_a_team_that_shows_no_tasks_shows_no_new_count(app, cast, session):
    """The bug he saw: 41 beside Tasks and an empty list on the personal team."""
    add_task(session, cast.x, title="X one", is_new=True)
    add_task(session, cast.x, title="X two", is_new=True)
    add_task(session, cast.y, title="Y one", is_new=True)
    pat = sign_in(app, PAT)
    for team in (cast.personal_pat, cast.x, cast.y):
        listed = get(pat, "/api/tasks", team).json()["tasks"]
        expected = len([t for t in listed if t["is_new"] and t["status"] != "done"])
        assert badges(pat, team)["tasks_new"] == expected, team.id
    assert badges(pat, cast.personal_pat)["tasks_new"] == 0
    assert badges(pat, cast.x)["tasks_new"] == 2
    assert badges(pat, cast.y)["tasks_new"] == 1


def test_the_task_badge_ignores_done_and_suggested_tasks_as_the_list_does(app, cast, session):
    add_task(session, cast.x, title="Done", is_new=True, status="done")
    add_task(session, cast.x, title="Maybe", is_new=True, suggested=True)
    add_task(session, cast.x, title="Real", is_new=True)
    assert badges(sign_in(app, PAT), cast.x)["tasks_new"] == 1


def test_the_session_with_no_header_counts_the_default_team(app, cast, session):
    add_task(session, cast.x, title="X one", is_new=True)
    # Pat's default is the personal team, which has none; Ada's default is X, which has one.
    assert badges(sign_in(app, PAT))["tasks_new"] == 0
    assert badges(sign_in(app, ADA))["tasks_new"] == 1


def test_a_stale_team_does_not_break_the_session(app, cast):
    """The page is always loadable: a team the caller left falls back to their default team."""
    pat = sign_in(app, PAT)
    response = get(pat, "/api/session", cast.z)
    assert response.status_code == 200
    assert response.json()["badges"]["review_open"] == 1  # the personal default's


def test_the_badges_with_the_switch_off_count_everything_as_before(app, cast, monkeypatch):
    monkeypatch.setattr(teams, "multi_team_enabled", lambda: False)
    assert badges(sign_in(app, PAT))["review_open"] == 3


def test_a_logged_out_session_with_a_header_is_still_a_plain_session(anon_client):
    body = anon_client.get("/api/session", headers={"X-Team-Id": "1"}).json()
    assert body["authenticated"] is False and body["badges"] is None


# ---- renaming a team (the Team screen's name field) ----


def patch_team(handle, team, body):
    return handle.client.patch(
        "/api/team", json=body, headers={"X-CSRF-Token": handle.csrf, **h(team)}
    )


def test_an_owner_renames_an_ordinary_team_and_the_list_shows_it(app, cast):
    ada = sign_in(app, ADA)
    response = patch_team(ada, cast.x, {"name": "  Platform   team "})
    assert response.status_code == 200, response.text
    assert response.json()["name"] == "Platform team"
    names = {t["id"]: t["name"] for t in get(ada, "/api/teams").json()["teams"]}
    assert names[cast.x.id] == response.json()["name"]


def test_a_plain_member_cannot_rename_and_an_empty_name_is_422(app, cast):
    error_of(patch_team(sign_in(app, PAT), cast.x, {"name": "Mine now"}), 403, "team_forbidden")
    error_of(patch_team(sign_in(app, ADA), cast.x, {"name": "   "}), 422, "invalid_name")


def test_the_personal_team_keeps_its_fixed_name(app, cast):
    pat = sign_in(app, PAT)
    before = get(pat, "/api/team", cast.personal_pat).json()["name"]
    error = error_of(patch_team(pat, cast.personal_pat, {"name": "Renamed"}), 409, "personal_team")
    assert "name" in error["message"].lower()
    assert get(pat, "/api/team", cast.personal_pat).json()["name"] == before
    # The other settings of the personal team still change (the switch is not about a name).
    ok = patch_team(pat, cast.personal_pat, {"share_new_meetings": False})
    assert ok.status_code == 200, ok.text


def test_with_the_switch_off_a_team_of_one_still_renames_as_it_always_did(app, cast, monkeypatch):
    monkeypatch.setattr(teams, "multi_team_enabled", lambda: False)
    assert patch_team(sign_in(app, PAT), cast.personal_pat, {"name": "Renamed"}).status_code == 200
