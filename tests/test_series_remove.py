"""Remove a recurring meeting (`DELETE /api/series/{id}`). LEAD-DESIGNED, approved by Adebayo
2026-11-15. Written before the code: the permission and leak cases first.

The cast is `tests/test_multi_team_leaks.py`'s `world`: Ada records and owns team X; Pat is in X
and Y; Quinn owns Y and is only in Y; Zed owns Z (his own organisation). A removal acts on ONE row
(one team scope, or one personal scope): never on another team's or person's row of the same
recurring event. Who may (Adebayo, 2026-11-15: "Only owners and admins"): the owner or an admin of
the row's team, and for a personal row its owner. The row's creator has no power over a team row
that has anything in it, but may remove one that is completely empty (Adebayo, 2026-11-15: "Yes,
empty rows only"): no meeting, no waiting line, no project, no mail still to go. Any other member
gets 403 `team_forbidden`; a person outside the team gets 404. Past meetings
stay as ordinary meetings (nothing of them changes); a row that came from a calendar recurring
event is remembered as ignored for that scope, so the calendar sync does not make it again and
later meetings of that event are not regrouped into it.
"""

# ruff: noqa: F811  (fixtures are imported by name and used as arguments)
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app import meeting_teams
from app import series as series_rules
from app.models import (
    MailOutbox,
    Meeting,
    OpenQuestion,
    Project,
    Series,
    SeriesAgendaItem,
    SeriesAgendaSend,
    SeriesIgnored,
    SeriesReminder,
    SeriesTopic,
    Task,
    TeamMember,
)
from tests.test_api import (  # noqa: F401  (fixtures used by name)
    OWNER_EMAIL,
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
from tests.test_multi_team_leaks import (  # noqa: F401  (fixtures used by name)
    PAT,
    QUINN,
    ZED,
    ids_of,
    new_team,
    world,
)
from tests.test_series import MONDAY, calendar_event, join_for, question_in, task_in
from tests.test_series_follow import (
    RID,
    calendar_meeting,
    personal_of,
    put,
    row_of,
    standup,
)
from tests.test_team_visibility import sign_in
from tests.test_web import make_user

TUESDAY, WEDNESDAY = MONDAY + timedelta(days=1), MONDAY + timedelta(days=2)
FORBIDDEN_TEXT = "You can't do that here. Ask an owner or admin of your team."


@pytest.fixture(autouse=True)
def follow_on(monkeypatch):
    monkeypatch.setattr(series_rules, "follow_enabled", lambda: True)


def remove(who, row_id):
    return who.delete(f"/api/series/{row_id}")


def ignored(session, team_id, rid=RID) -> bool:
    return (
        session.scalar(
            select(SeriesIgnored.id).where(
                SeriesIgnored.team_id == team_id, SeriesIgnored.calendar_recurring_id == rid
            )
        )
        is not None
    )


def reload(session, row_id) -> Series | None:
    session.expire_all()
    return session.get(Series, row_id)


def add_member(session, team, email, role):
    user = make_user(session, email)
    session.add(TeamMember(team_id=team.id, user_id=user.id, role=role))
    session.commit()
    return user


@pytest.fixture
def rows(session, template, world):
    """Pat's Standup in all three scopes (as the follow rule leaves it), one meeting each with a
    task and a carried question, made by Pat; plus a waiting agenda line on X's."""
    pat = world.pat
    r = type("Rows", (), {})()
    r.me = personal_of(session, pat)
    r.x = standup(session, world.x.id, owner_id=pat.id)
    r.y = standup(session, world.y.id, owner_id=pat.id)
    r.mine = standup(session, r.me, owner_id=pat.id)
    r.monday = put(session, template, r.x, who=pat, team=world.x.id, when=MONDAY)
    r.tuesday = put(session, template, r.mine, who=pat, when=TUESDAY)
    r.wednesday = put(session, template, r.y, who=pat, team=world.y.id, when=WEDNESDAY)
    for meeting, team in ((r.monday, world.x), (r.tuesday, None), (r.wednesday, world.y)):
        task_in(session, team.id if team else r.me, meeting, f"task {meeting.id}")
        question_in(session, meeting, f"question {meeting.id}", carry=True)
    session.add(SeriesAgendaItem(series_id=r.x.id, text="Budget", position=1, created_by=pat.id))
    session.commit()
    return r


# ---- who may remove (the permission decisions) ----


def test_the_owner_of_the_team_may_remove_a_row_someone_else_made(app, session, world, rows):
    ada = sign_in(app, OWNER_EMAIL)  # owner of X; Pat made the row
    response = remove(ada, rows.x.id)
    assert response.status_code == 200, response.text
    assert reload(session, rows.x.id) is None


def test_an_admin_of_the_team_may_remove_it(app, session, world, rows):
    add_member(session, world.x, "dee@example.test", "admin")
    dee = sign_in(app, "dee@example.test")
    assert remove(dee, rows.x.id).status_code == 200
    assert reload(session, rows.x.id) is None


def test_a_plain_member_who_made_a_calendar_row_or_a_hand_made_row_gets_403(
    app, session, world, rows, template
):
    """Pat made X's calendar row (`rows.x`, he recorded first) and now a hand-made one. He is a
    plain member of X: he may rename them but not remove them."""
    pat = sign_in(app, PAT)
    hand = standup(session, world.x.id, rid=None, owner_id=world.pat.id, title="Pat's own")
    put(session, template, hand, who=world.pat, team=world.x.id)
    hand_id = hand.id
    assert rows.x.owner_user_id == hand.owner_user_id == world.pat.id  # he made both
    for row_id in (rows.x.id, hand_id):
        assert pat.get(f"/api/series/{row_id}").status_code == 200
        error = error_of(remove(pat, row_id), 403, "team_forbidden")
        assert error["message"] == FORBIDDEN_TEXT
        assert reload(session, row_id) is not None
    assert not ignored(session, world.x.id)
    assert pat.call("PATCH", f"/api/series/{hand_id}", {"title": "Renamed"}).status_code == 200


def test_a_plain_member_who_did_not_make_it_gets_403_and_nothing_changes(app, session, world, rows):
    add_member(session, world.x, "mia@example.test", "member")
    mia = sign_in(app, "mia@example.test")
    # Mia must be able to see the row to be told "no": she is in X and Monday is shared to X.
    assert mia.get(f"/api/series/{rows.x.id}").status_code == 200
    error = error_of(remove(mia, rows.x.id), 403, "team_forbidden")
    assert error["message"] == FORBIDDEN_TEXT
    assert reload(session, rows.x.id) is not None
    assert not ignored(session, world.x.id)
    assert session.get(Meeting, rows.monday.id).series_id == rows.x.id


def test_someone_outside_the_team_gets_404_and_nothing_changes(app, session, world, rows):
    quinn = sign_in(app, QUINN)  # only in Y
    zed = sign_in(app, ZED)  # his own organisation
    for who in (quinn, zed):
        error_of(remove(who, rows.x.id), 404, "not_found")
    assert reload(session, rows.x.id) is not None
    assert not ignored(session, world.x.id)
    assert session.get(Meeting, rows.monday.id).series_id == rows.x.id


def test_the_creator_who_left_the_team_gets_404(app, session, world, rows):
    session.execute(
        TeamMember.__table__.delete().where(
            TeamMember.user_id == world.pat.id, TeamMember.team_id == world.x.id
        )
    )
    session.commit()
    pat = sign_in(app, PAT)
    error_of(remove(pat, rows.x.id), 404, "not_found")
    assert reload(session, rows.x.id) is not None


def test_the_rule_itself_fails_closed_for_a_creator_who_is_no_longer_a_member(session, world, rows):
    """The route 404s such a person first; the rule must say no on its own too."""
    session.execute(
        TeamMember.__table__.delete().where(
            TeamMember.user_id == world.pat.id, TeamMember.team_id == world.x.id
        )
    )
    session.commit()
    assert rows.x.owner_user_id == world.pat.id
    assert not series_rules.can_remove(session, world.pat, rows.x)
    with pytest.raises(series_rules.SeriesRefused) as refused:
        series_rules.remove(session, world.pat, rows.x)
    assert (refused.value.status, refused.value.code) == (403, "team_forbidden")
    assert series_rules.can_remove(session, world.ada, rows.x)  # the owner of X still may


def test_a_personal_row_is_removed_by_its_owner_and_is_404_to_everyone_else(
    app, session, world, rows
):
    ada = sign_in(app, OWNER_EMAIL)
    error_of(remove(ada, rows.mine.id), 404, "not_found")  # even the owner of Pat's team X
    quinn = sign_in(app, QUINN)
    error_of(remove(quinn, rows.mine.id), 404, "not_found")
    assert reload(session, rows.mine.id) is not None
    pat = sign_in(app, PAT)
    assert remove(pat, rows.mine.id).status_code == 200
    assert reload(session, rows.mine.id) is None


def test_an_unknown_or_malformed_id_is_404_and_a_second_remove_is_404(app, session, world, rows):
    ada = sign_in(app, OWNER_EMAIL)
    error_of(remove(ada, 987654), 404, "not_found")
    assert ada.delete("/api/series/abc").status_code == 404
    assert remove(ada, rows.x.id).status_code == 200
    error_of(remove(ada, rows.x.id), 404, "not_found")


def test_remove_needs_the_csrf_token_and_a_session(app, session, world, rows, anon):
    ada = sign_in(app, OWNER_EMAIL)
    assert ada.call("DELETE", f"/api/series/{rows.x.id}", csrf=False).status_code in (401, 403)
    assert anon.delete(f"/api/series/{rows.x.id}").status_code in (401, 403)
    assert reload(session, rows.x.id) is not None


def test_the_page_says_who_may_remove(app, session, world, rows):
    add_member(session, world.x, "mia@example.test", "member")
    flags = {}
    for name, email in (
        ("ada", OWNER_EMAIL),
        ("pat", PAT),
        ("mia", "mia@example.test"),
    ):
        flags[name] = sign_in(app, email).get(f"/api/series/{rows.x.id}").json()["can_remove"]
    assert flags == {"ada": True, "pat": False, "mia": False}  # Pat made the row: no extra power
    pat = sign_in(app, PAT)
    assert pat.get(f"/api/series/{rows.mine.id}").json()["can_remove"] is True  # his own personal
    assert pat.get(f"/api/series/{rows.y.id}").json()["can_remove"] is False  # he made Y's row too
    quinn = sign_in(app, QUINN)  # owns Y
    assert quinn.get(f"/api/series/{rows.y.id}").json()["can_remove"] is True


# ---- one row only: the sibling rows and the meetings are untouched ----


def test_removing_x_leaves_ys_row_and_the_personal_row_and_every_meeting_as_it_was(
    app, session, world, rows
):
    before = {
        who.id: ids_of(session, who) for who in (world.ada, world.pat, world.quinn, world.zed)
    }
    ada = sign_in(app, OWNER_EMAIL)
    assert remove(ada, rows.x.id).status_code == 200
    assert reload(session, rows.y.id) is not None and reload(session, rows.mine.id) is not None
    assert session.get(Meeting, rows.wednesday.id).series_id == rows.y.id
    assert session.get(Meeting, rows.tuesday.id).series_id == rows.mine.id
    assert ignored(session, world.x.id)
    assert not ignored(session, world.y.id) and not ignored(session, rows.me)
    after = {who.id: ids_of(session, who) for who in (world.ada, world.pat, world.quinn, world.zed)}
    assert after == before  # nobody reads more or fewer meetings
    page = sign_in(app, PAT).get(f"/api/series/{rows.y.id}").json()
    assert [m["id"] for m in page["past_meetings"]] == [rows.wednesday.id]


def test_the_ignored_mark_is_for_one_scope_only_the_same_event_still_groups_in_other_scopes(
    app, session, world, rows, template
):
    ada = sign_in(app, OWNER_EMAIL)
    assert remove(ada, rows.x.id).status_code == 200
    # Pat's next Standup: private (personal scope, not ignored), and one shared to Y (not ignored).
    private = calendar_meeting(
        session, template, world.pat, calendar_event(session, world.pat, RID, MONDAY)
    )
    in_y = calendar_meeting(
        session,
        template,
        world.pat,
        calendar_event(session, world.pat, RID, TUESDAY),
        shared_team_id=world.y.id,
    )
    series_rules.link_from_calendar(session, private)
    series_rules.link_from_calendar(session, in_y)
    session.commit()
    assert private.series_id == rows.mine.id
    assert in_y.series_id == rows.y.id


# ---- past meetings and everything in them stay ----


def test_past_meetings_stay_readable_with_their_tasks_questions_and_sharing(
    app, session, world, rows
):
    tasks_before = session.scalar(select(func.count()).select_from(Task))
    questions_before = session.scalar(select(func.count()).select_from(OpenQuestion))
    fields = lambda m: (m.title, m.status, m.shared_team_id, m.owner_team_id, m.owner_user_id)  # noqa: E731
    before = {m.id: fields(m) for m in (rows.monday, rows.tuesday, rows.wednesday)}
    ada = sign_in(app, OWNER_EMAIL)
    assert remove(ada, rows.x.id).status_code == 200
    session.expire_all()
    monday = session.get(Meeting, rows.monday.id)
    assert monday is not None and monday.series_id is None  # just not grouped any more
    assert fields(monday) == before[monday.id]
    assert monday.id in ids_of(session, world.ada)  # still readable, exactly as before
    assert ada.get(f"/api/meetings/{monday.id}").status_code == 200
    assert session.scalar(select(func.count()).select_from(Task)) == tasks_before
    assert session.scalar(select(func.count()).select_from(OpenQuestion)) == questions_before
    for other in (rows.tuesday, rows.wednesday):
        assert fields(session.get(Meeting, other.id)) == before[other.id]


def test_a_stray_of_another_scope_goes_to_its_own_row_not_into_no_series(app, session, world, rows):
    """A legacy mixed X row still points at Pat's private Tuesday and Wednesday (Y): removing X's
    row is not Pat's Personal or Y's grouping to undo. Each stray moves to the row of its own
    scope, and Ada is told only about the one meeting she may read."""
    for meeting in (rows.tuesday, rows.wednesday):
        meeting.series_id = rows.x.id
    session.commit()
    ada = sign_in(app, OWNER_EMAIL)
    body = remove(ada, rows.x.id).json()
    assert (
        body["meetings_kept"] == 1
    )  # Monday only: the strays are not counted, they are not ungrouped
    session.expire_all()
    assert session.get(Meeting, rows.monday.id).series_id is None
    assert session.get(Meeting, rows.tuesday.id).series_id == rows.mine.id  # Pat's personal row
    assert session.get(Meeting, rows.wednesday.id).series_id == rows.y.id  # Y's row
    assert reload(session, rows.mine.id) is not None and reload(session, rows.y.id) is not None


def test_the_answer_says_what_was_removed_and_remembered(app, session, world, rows):
    ada = sign_in(app, OWNER_EMAIL)
    body = remove(ada, rows.x.id).json()
    assert body == {"meetings_kept": 1, "agenda_lines_removed": 1, "remembered": True}


# ---- what goes with the row: lines, topics, project link; what stays ----


def test_waiting_lines_and_topics_go_the_project_stays_and_its_tasks_stay_filed(
    app, session, world, rows
):
    project = Project(team_id=world.x.id, name="Launch", color="#0E6B5E")
    session.add(project)
    session.commit()
    rows.x.project_id = project.id
    task = session.scalars(select(Task).where(Task.meeting_id == rows.monday.id)).one()
    task.project_id = project.id
    session.add(
        SeriesTopic(
            series_id=rows.x.id,
            label="Budget",
            agenda="Settle the budget",
            reason="no_decision",
            meeting_ids=[rows.monday.id, rows.monday.id],
        )
    )
    session.commit()
    ada = sign_in(app, OWNER_EMAIL)
    assert remove(ada, rows.x.id).status_code == 200
    session.expire_all()
    assert session.scalar(select(func.count()).select_from(SeriesAgendaItem)) == 0
    assert session.scalar(select(func.count()).select_from(SeriesTopic)) == 0
    assert session.get(Project, project.id) is not None  # the project stays
    assert session.get(Task, task.id).project_id == project.id  # and so does where the task went


# ---- the calendar sync does not bring it back ----


def test_the_hourly_sync_does_not_recreate_a_removed_calendar_row(app, session, world, rows):
    event = calendar_event(session, world.ada, "rid-sync", MONDAY)
    join_for(session, world.ada, event, state="scheduled")
    assert series_rules.sync_from_joins(session, MONDAY) == 1
    mine = personal_of(session, world.ada)  # the calendar card is Ada's own, in her personal space
    card = row_of(session, mine, "rid-sync")
    assert card is not None and row_of(session, world.x.id, "rid-sync") is None
    ada = sign_in(app, OWNER_EMAIL)
    card_id = card.id
    assert remove(ada, card_id).status_code == 200
    assert ignored(session, mine, "rid-sync")
    assert series_rules.sync_from_joins(session, MONDAY) == 0
    assert (
        row_of(session, mine, "rid-sync") is None
        and row_of(session, world.x.id, "rid-sync") is None
    )
    listed = ada.client.get("/api/series", headers={"X-Team-Id": str(mine)}).json()["series"]
    assert card_id not in [c["id"] for c in listed]


def test_removing_the_personal_row_of_a_one_team_person_leaves_no_card_in_their_team(
    app, session, world, template
):
    """Ada is in ONE ordinary team (X), so X is her default team. Her private recurring meeting
    is in her Personal row. She removes it. The hourly sync must not make an empty card for the
    event in X (the ghost card, back in another scope)."""
    mine = personal_of(session, world.ada)
    row = standup(session, mine, rid="rid-m1", owner_id=world.ada.id, title="1:1")
    event = calendar_event(session, world.ada, "rid-m1", MONDAY)
    meeting = put(session, template, row, who=world.ada, title="1:1")
    meeting.source = "calendar"
    session.commit()
    join_for(session, world.ada, event, meeting, state="completed")
    row_id = row.id
    ada = sign_in(app, OWNER_EMAIL)
    assert remove(ada, row_id).status_code == 200
    assert ignored(session, mine, "rid-m1")
    assert series_rules.sync_from_joins(session, MONDAY) == 0
    assert row_of(session, world.x.id, "rid-m1") is None
    assert row_of(session, mine, "rid-m1") is None


def test_a_later_calendar_meeting_of_that_event_is_not_regrouped(
    app, session, world, rows, template
):
    ada = sign_in(app, OWNER_EMAIL)
    assert remove(ada, rows.x.id).status_code == 200
    later = calendar_meeting(
        session,
        template,
        world.ada,
        calendar_event(session, world.ada, RID, WEDNESDAY),
        shared_team_id=world.x.id,
    )
    assert series_rules.link_from_calendar(session, later) is None
    session.commit()
    assert later.series_id is None
    assert row_of(session, world.x.id) is None


def test_sharing_a_meeting_of_that_event_to_the_removed_scope_does_not_regroup_it(
    app, session, world, rows
):
    ada = sign_in(app, OWNER_EMAIL)
    assert remove(ada, rows.x.id).status_code == 200
    tuesday = rows.tuesday  # Pat's private Tuesday, in his personal row
    meeting_teams.share(session, world.pat, tuesday, world.x.id)
    session.commit()
    session.expire_all()
    assert session.get(Meeting, tuesday.id).series_id is None  # not put back into X
    assert row_of(session, world.x.id) is None
    assert session.get(Meeting, tuesday.id).shared_team_id == world.x.id  # the share itself worked


def test_a_card_waiting_to_move_is_not_taken_into_an_ignored_scope(app, session, world, template):
    """Ada's empty card in X (before her first recording) already exists. Her personal scope is
    removed; her first meeting is private, so it would go to the personal scope: ignored, so it
    is not linked and the X card that already exists is left where it is (one that does not exist
    yet is not made: see the test about the personal row of a one-team person)."""
    event = calendar_event(session, world.ada, "rid-card", MONDAY)
    scheduled = join_for(session, world.ada, event, state="scheduled")
    card = standup(
        session, world.x.id, rid="rid-card", owner_id=world.ada.id
    )  # a legacy empty card
    mine = personal_of(session, world.ada)
    session.add(SeriesIgnored(team_id=mine, calendar_recurring_id="rid-card"))
    session.commit()
    meeting = ready(session, template, source="calendar", created_at=event.starts_at)  # private
    scheduled.meeting_id, scheduled.state = meeting.id, "completed"
    session.commit()
    assert series_rules.link_from_calendar(session, meeting) is None
    session.commit()
    assert meeting.series_id is None
    assert reload(session, card.id) is not None and row_of(session, mine, "rid-card") is None


def test_with_the_flag_off_it_works_and_the_default_teams_sync_stays_quiet(
    app, session, world, monkeypatch
):
    monkeypatch.setattr(series_rules, "follow_enabled", lambda: False)
    event = calendar_event(session, world.ada, "rid-off", MONDAY)
    join_for(session, world.ada, event, state="scheduled")
    assert series_rules.sync_from_joins(session, MONDAY) == 1
    card = row_of(session, world.x.id, "rid-off")
    ada = sign_in(app, OWNER_EMAIL)
    assert remove(ada, card.id).status_code == 200
    assert ignored(session, world.x.id, "rid-off")
    assert series_rules.sync_from_joins(session, MONDAY) == 0
    assert row_of(session, world.x.id, "rid-off") is None


# ---- hand-made rows, emptied rows ----


def test_a_hand_made_row_is_removed_and_nothing_is_remembered(app, session, world, template):
    hand = standup(session, world.x.id, rid=None, owner_id=world.ada.id, title="Founders sync")
    meeting = put(session, template, hand, who=world.ada, team=world.x.id)
    hand_id = hand.id
    ada = sign_in(app, OWNER_EMAIL)
    body = remove(ada, hand_id).json()
    assert body == {"meetings_kept": 1, "agenda_lines_removed": 0, "remembered": False}
    session.expire_all()
    assert session.get(Series, hand_id) is None
    assert session.get(Meeting, meeting.id).series_id is None
    assert session.scalar(select(func.count()).select_from(SeriesIgnored)) == 0


def test_an_emptied_row_the_ghost_card_is_removed_by_a_manager_who_sees_it(
    app, session, world, rows
):
    """An empty card shows to its creator only. Ada (owner of X) made one: she removes it. Pat (a
    plain member) has a card with a waiting line in it: he sees it, and may not remove it (403)."""
    rows.monday.series_id = None  # Pat's X row lost its last meeting; the card stays, his alone
    session.commit()
    hers = standup(session, world.x.id, rid="rid-ghost", owner_id=world.ada.id, title="Ghost")
    hers_id = hers.id
    headers = {"X-Team-Id": str(world.x.id)}
    ada = sign_in(app, OWNER_EMAIL)
    seen = ada.client.get("/api/series", headers=headers).json()["series"]
    assert [c["id"] for c in seen] == [hers_id]  # not Pat's card
    pat = sign_in(app, PAT)
    seen_by_pat = pat.client.get("/api/series", headers=headers).json()["series"]
    assert [c["id"] for c in seen_by_pat] == [rows.x.id]
    error_of(remove(pat, rows.x.id), 403, "team_forbidden")
    assert remove(ada, hers_id).status_code == 200
    assert ada.client.get("/api/series", headers=headers).json()["series"] == []


# ---- a creator may remove a row that is completely empty (and only then) ----


def empty_row(session, world, *, rid="rid-empty", title="Empty one"):
    """Pat's own row in X with nothing in it: no meeting, no line, no project, no mail."""
    return standup(session, world.x.id, rid=rid, owner_id=world.pat.id, title=title)


def test_the_creator_may_remove_a_completely_empty_row_calendar_or_hand_made(app, session, world):
    pat = sign_in(app, PAT)
    calendar = empty_row(session, world)
    hand = empty_row(session, world, rid=None, title="Hand")
    calendar_id, hand_id = calendar.id, hand.id
    for row_id in (calendar_id, hand_id):
        assert pat.get(f"/api/series/{row_id}").json()["can_remove"] is True
    # a plain creator's removal of an empty row writes no team-wide mark (`remembered` false)
    assert remove(pat, calendar_id).json() == {
        "meetings_kept": 0,
        "agenda_lines_removed": 0,
        "remembered": False,
    }
    assert remove(pat, hand_id).json()["remembered"] is False
    assert reload(session, calendar_id) is None and reload(session, hand_id) is None
    assert not ignored(session, world.x.id, "rid-empty")


def make_not_empty(session, world, row, how, template):
    if how == "meeting":
        put(session, template, row, who=world.pat, team=world.x.id)
    elif how == "line":
        session.add(SeriesAgendaItem(series_id=row.id, text="Budget", position=1))
    elif how == "project":
        project = Project(team_id=world.x.id, name="Launch", color="#0E6B5E")
        session.add(project)
        session.flush()
        row.project_id = project.id
    elif how == "agenda mail":
        send = SeriesAgendaSend(series_id=row.id, sender_user_id=world.pat.id, recipient_count=1)
        session.add(send)
        session.flush()
        session.add(
            MailOutbox(
                to_email="a@example.test",
                template="series_agenda",
                payload={"send_id": send.id, "user_id": world.pat.id},
            )
        )
    elif how == "reminder mail":
        reminder = SeriesReminder(
            series_id=row.id, user_id=world.pat.id, kind="1h", starts_at=MONDAY
        )
        session.add(reminder)
        session.flush()
        session.add(
            MailOutbox(
                to_email="a@example.test",
                template="series_reminder",
                payload={"reminder_id": reminder.id},
            )
        )
    session.commit()


@pytest.mark.parametrize("how", ["meeting", "line", "project", "agenda mail", "reminder mail"])
def test_the_creator_of_a_row_with_anything_in_it_gets_403_and_nothing_changes(
    app, session, world, template, how
):
    row = empty_row(session, world)
    make_not_empty(session, world, row, how, template)
    row_id = row.id
    pat = sign_in(app, PAT)
    assert pat.get(f"/api/series/{row_id}").json()["can_remove"] is False
    error = error_of(remove(pat, row_id), 403, "team_forbidden")
    assert error["message"] == FORBIDDEN_TEXT
    assert reload(session, row_id) is not None
    assert not ignored(session, world.x.id, "rid-empty")
    ada = sign_in(app, OWNER_EMAIL)  # the owner of X still may, whatever is in it
    assert ada.client.get("/api/series", headers={"X-Team-Id": str(world.x.id)}).status_code == 200


def test_a_mail_that_has_gone_does_not_make_a_row_non_empty(app, session, world, template):
    row = empty_row(session, world)
    make_not_empty(session, world, row, "agenda mail", template)
    session.execute(MailOutbox.__table__.update().values(status="sent"))
    session.commit()
    pat = sign_in(app, PAT)
    assert remove(pat, row.id).status_code == 200


def test_another_plain_member_may_not_remove_someone_elses_empty_row(app, session, world):
    """A calendar row (Pat's) is shown to a teammate who has the event on their own calendar; Mia
    is a plain member who did not make it, so 403. Without the event she cannot see it at all."""
    row = empty_row(session, world)
    row_id = row.id
    mia_user = add_member(session, world.x, "mia@example.test", "member")
    mia = sign_in(app, "mia@example.test")
    error_of(remove(mia, row_id), 404, "not_found")
    calendar_event(session, mia_user, "rid-empty", MONDAY)
    assert mia.get(f"/api/series/{row_id}").json()["can_remove"] is False
    error_of(remove(mia, row_id), 403, "team_forbidden")
    assert reload(session, row_id) is not None


@pytest.mark.parametrize("arrives", ["line", "meeting"])
def test_a_line_or_meeting_that_arrives_before_the_lock_makes_a_creators_removal_refuse(
    session, world, template, arrives
):
    """The rule said yes, then something landed in the row. `remove` asks again under the row's
    lock: refuse, do not delete."""
    row = empty_row(session, world)
    assert series_rules.can_remove(session, world.pat, row)
    make_not_empty(session, world, row, arrives, template)
    with pytest.raises(series_rules.SeriesRefused) as refused:
        series_rules.remove(session, world.pat, row)
    assert (refused.value.status, refused.value.code) == (403, "team_forbidden")
    session.rollback()
    assert reload(session, row.id) is not None
    assert not ignored(session, world.x.id, "rid-empty")
    assert series_rules.can_remove(session, world.ada, row)  # a manager is unaffected


@pytest.mark.parametrize("arrives", ["line", "meeting"])
def test_what_arrives_between_the_first_look_and_the_lock_still_refuses(
    session, world, template, arrives, monkeypatch
):
    """The first look says "empty" and only THEN something lands (the window the lock closes): the
    first answer is forced to yes, so only the look under the row's lock can refuse."""
    row = empty_row(session, world)
    make_not_empty(session, world, row, arrives, template)
    real, looks = series_rules.is_empty, []

    def first_look_says_empty(session, found):
        looks.append(1)
        return True if len(looks) == 1 else real(session, found)

    monkeypatch.setattr(series_rules, "is_empty", first_look_says_empty)
    with pytest.raises(series_rules.SeriesRefused) as refused:
        series_rules.remove(session, world.pat, row)
    assert (refused.value.status, refused.value.code) == (403, "team_forbidden")
    assert len(looks) == 2  # asked once before the lock and once under it
    session.rollback()
    assert reload(session, row.id) is not None
    assert not ignored(session, world.x.id, "rid-empty")


def test_the_creator_who_left_the_team_may_not_remove_even_an_empty_row(session, world):
    row = empty_row(session, world)
    session.execute(
        TeamMember.__table__.delete().where(
            TeamMember.user_id == world.pat.id, TeamMember.team_id == world.x.id
        )
    )
    session.commit()
    assert not series_rules.can_remove(session, world.pat, row)


# ---- the ignored mark itself ----


def test_the_mark_is_one_per_scope_and_event_and_records_who_removed_it(app, session, world, rows):
    ada = sign_in(app, OWNER_EMAIL)
    assert remove(ada, rows.x.id).status_code == 200
    [mark] = session.scalars(select(SeriesIgnored)).all()
    assert (mark.team_id, mark.calendar_recurring_id, mark.created_by) == (
        world.x.id,
        RID,
        world.ada.id,
    )
    assert series_rules.is_ignored(session, world.x.id, RID)
    assert not series_rules.is_ignored(session, world.y.id, RID)
    assert not series_rules.is_ignored(session, world.x.id, "another-event")


def test_an_ignored_mark_goes_with_its_team(app, session, world, rows):
    ada = sign_in(app, OWNER_EMAIL)
    assert remove(ada, rows.x.id).status_code == 200
    session.delete(world.x)
    session.commit()
    assert session.scalar(select(func.count()).select_from(SeriesIgnored)) == 0
