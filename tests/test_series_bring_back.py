"""Bring back a removed recurring meeting, and the hint on a Personal card. Written before the code.

Follow-up of #205 (remove) and #207 (the simpler model). A removed calendar recurring meeting is
remembered as `series_ignored` (one mark per scope: a team's, or one person's Personal space).
"Bring back" clears that ONE mark: it does not make the card, it lets the writers make it again
(the hourly sync for a Personal scope; an owner's or admin's recording that is auto-shared, or their
explicit share, for a team's). Who may follows the removal rule exactly: a team's mark, only an
owner or admin of that team (a plain member gets 403 `team_forbidden`); a Personal mark, only the
person; anyone else 404. It never deletes or changes a meeting, task, note, agenda line or row.
A hand-made recurring meeting leaves no mark, so there is nothing to bring back for it.

The hint: lines typed into the agenda of a Personal card are not used by the meetings that go to a
team's card of the same recurring meeting, so the Personal page says where the team's card is, and
only when the viewer can see one (`SeriesPage.team_card`).

The cast is `tests/test_multi_team_leaks.py`'s `world`: Ada owns X and records; Pat is in X and Y;
Quinn owns Y; Zed owns Z, his own organisation.
"""

# ruff: noqa: F811  (fixtures are imported by name and used as arguments)
from sqlalchemy import func, select

from app import series as series_rules
from app.models import (
    Meeting,
    OpenQuestion,
    Series,
    SeriesAgendaItem,
    SeriesIgnored,
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
    multi_team_on,
    world,
)
from tests.test_series import MONDAY, calendar_event, join_for, question_in, task_in
from tests.test_series_follow import RID, calendar_meeting, personal_of, put, row_of, standup
from tests.test_series_remove import FORBIDDEN_TEXT, add_member, follow_on, ignored  # noqa: F401
from tests.test_team_visibility import sign_in


def removed_of(who, team_id=None):
    headers = {"X-Team-Id": str(team_id)} if team_id is not None else {}
    return who.client.get("/api/series/removed", headers=headers)


def bring_back(who, mark_id):
    return who.post(f"/api/series/removed/{mark_id}/bring-back")


def mark_of(session, team_id, rid=RID) -> SeriesIgnored | None:
    session.expire_all()
    return session.scalars(
        select(SeriesIgnored).where(
            SeriesIgnored.team_id == team_id, SeriesIgnored.calendar_recurring_id == rid
        )
    ).first()


def counts(session) -> dict[str, int]:
    return {
        model.__tablename__: session.scalar(select(func.count()).select_from(model))
        for model in (Meeting, Task, OpenQuestion, Series, SeriesAgendaItem)
    }


def removed_x(app, session, world, *, owner_id=None):
    """X's calendar card of RID, removed by Ada (its owner). Returns the mark."""
    row = standup(session, world.x.id, owner_id=owner_id or world.ada.id)
    ada = sign_in(app, OWNER_EMAIL)
    assert ada.delete(f"/api/series/{row.id}").status_code == 200
    mark = mark_of(session, world.x.id)
    assert mark is not None
    return mark


# ---- the list: who sees which marks ----


def test_an_owner_sees_the_removed_recurring_meetings_of_her_team(app, session, world):
    calendar_event(session, world.ada, RID, MONDAY, title="Tech standup")
    mark = removed_x(app, session, world)
    response = removed_of(sign_in(app, OWNER_EMAIL), world.x.id)
    assert response.status_code == 200, response.text
    items = response.json()["removed"]
    assert [i["id"] for i in items] == [mark.id]
    item = items[0]
    assert item["title"] == "Tech standup"  # from her own calendar
    assert item["team"] == {"id": world.x.id, "name": "X", "is_personal": False}
    assert item["removed_at"] and item["removed_by"]
    assert "@" not in item["removed_by"]  # a name, never an address
    assert set(item) == {"id", "title", "team", "removed_at", "removed_by"}


def test_an_admin_sees_them_and_a_plain_member_gets_403(app, session, world):
    removed_x(app, session, world)
    add_member(session, world.x, "dee@example.test", "admin")
    add_member(session, world.x, "mia@example.test", "member")
    assert len(removed_of(sign_in(app, "dee@example.test"), world.x.id).json()["removed"]) == 1
    pat = sign_in(app, PAT)  # a plain member of X
    error = error_of(removed_of(pat, world.x.id), 403, "team_forbidden")
    assert error["message"] == FORBIDDEN_TEXT
    error_of(removed_of(sign_in(app, "mia@example.test"), world.x.id), 403, "team_forbidden")


def test_an_outsider_asking_for_a_team_that_is_not_theirs_gets_404(app, session, world):
    removed_x(app, session, world)
    for email in (QUINN, ZED):  # Quinn is only in Y; Zed has his own organisation
        error_of(removed_of(sign_in(app, email), world.x.id), 404, "not_found")


def test_a_personal_mark_is_listed_for_the_person_only(app, session, world):
    mine = personal_of(session, world.pat)
    row = standup(session, mine, owner_id=world.pat.id)
    pat = sign_in(app, PAT)
    assert pat.delete(f"/api/series/{row.id}").status_code == 200
    items = removed_of(pat, mine).json()["removed"]
    assert len(items) == 1 and items[0]["team"]["is_personal"] is True
    ada = sign_in(app, OWNER_EMAIL)  # owner of Pat's team X, still not Pat's Personal space
    error_of(removed_of(ada, mine), 404, "not_found")


def test_without_a_team_header_it_is_the_default_teams(app, session, world):
    """Ada is in one ordinary team (X), which is her default team."""
    removed_x(app, session, world)
    ada = sign_in(app, OWNER_EMAIL)
    assert len(removed_of(ada).json()["removed"]) == 1


def test_only_the_asked_scopes_marks_are_listed(app, session, world):
    removed_x(app, session, world)
    mine = personal_of(session, world.ada)
    session.add(SeriesIgnored(team_id=mine, calendar_recurring_id="rid-private"))
    session.add(SeriesIgnored(team_id=world.y.id, calendar_recurring_id="rid-y"))
    session.commit()
    ada = sign_in(app, OWNER_EMAIL)
    assert [i["title"] for i in removed_of(ada, mine).json()["removed"]] == [series_rules.UNTITLED]
    assert len(removed_of(ada, world.x.id).json()["removed"]) == 1


def test_a_title_comes_only_from_the_viewers_own_calendar_never_a_teammates(app, session, world):
    """Pat has "Pat's private 1:1" on his calendar; Ada, who removed the team's card, does not. The
    team's list says nothing about what Pat's event is called."""
    calendar_event(session, world.pat, "rid-pat", MONDAY, title="Pat's private 1:1")
    session.add(SeriesIgnored(team_id=world.x.id, calendar_recurring_id="rid-pat"))
    session.commit()
    body = removed_of(sign_in(app, OWNER_EMAIL), world.x.id)
    assert body.json()["removed"][0]["title"] == series_rules.UNTITLED
    assert "private 1:1" not in body.text
    calendar_event(session, world.ada, "rid-pat", MONDAY, title="Ada's own name for it")
    again = removed_of(sign_in(app, OWNER_EMAIL), world.x.id).json()["removed"][0]
    assert again["title"] == "Ada's own name for it"


def test_a_hand_made_recurring_meeting_leaves_nothing_to_bring_back(app, session, world):
    row = standup(session, world.x.id, rid=None, owner_id=world.ada.id, title="Hand made")
    ada = sign_in(app, OWNER_EMAIL)
    assert ada.delete(f"/api/series/{row.id}").status_code == 200
    assert removed_of(ada, world.x.id).json() == {"removed": []}


def test_a_plain_creators_removal_of_an_empty_legacy_row_leaves_no_mark(app, session, world):
    row = standup(session, world.x.id, owner_id=world.pat.id)  # Pat made it, it is empty
    pat = sign_in(app, PAT)
    assert pat.delete(f"/api/series/{row.id}").status_code == 200
    ada = sign_in(app, OWNER_EMAIL)
    assert removed_of(ada, world.x.id).json() == {"removed": []}


# ---- bringing one back: the permission table ----


def test_the_owner_brings_it_back_and_the_one_mark_is_gone(app, session, world):
    mark = removed_x(app, session, world)
    mark_id = mark.id
    response = bring_back(sign_in(app, OWNER_EMAIL), mark_id)
    assert response.status_code == 200, response.text
    assert response.json()["team"] == {"id": world.x.id, "name": "X", "is_personal": False}
    assert mark_of(session, world.x.id) is None
    assert not ignored(session, world.x.id)


def test_an_admin_brings_it_back(app, session, world):
    mark = removed_x(app, session, world)
    add_member(session, world.x, "dee@example.test", "admin")
    assert bring_back(sign_in(app, "dee@example.test"), mark.id).status_code == 200
    assert mark_of(session, world.x.id) is None


def test_a_plain_member_gets_403_and_nothing_changes(app, session, world):
    mark = removed_x(app, session, world)
    mark_id = mark.id
    add_member(session, world.x, "mia@example.test", "member")
    for email in (PAT, "mia@example.test"):
        error = error_of(bring_back(sign_in(app, email), mark_id), 403, "team_forbidden")
        assert error["message"] == FORBIDDEN_TEXT
    assert mark_of(session, world.x.id) is not None


def test_an_outsider_gets_404_and_nothing_changes(app, session, world):
    mark = removed_x(app, session, world)
    for email in (QUINN, ZED):
        error_of(bring_back(sign_in(app, email), mark.id), 404, "not_found")
    assert mark_of(session, world.x.id) is not None


def test_a_member_who_left_the_team_gets_404(app, session, world):
    mark = removed_x(app, session, world)
    session.execute(
        TeamMember.__table__.delete().where(
            TeamMember.user_id == world.pat.id, TeamMember.team_id == world.x.id
        )
    )
    session.commit()
    error_of(bring_back(sign_in(app, PAT), mark.id), 404, "not_found")
    assert mark_of(session, world.x.id) is not None


def test_the_person_brings_back_their_own_personal_mark_and_nobody_else_may(app, session, world):
    mine = personal_of(session, world.pat)
    mark = SeriesIgnored(team_id=mine, calendar_recurring_id=RID, created_by=world.pat.id)
    session.add(mark)
    session.commit()
    mark_id = mark.id
    for email in (OWNER_EMAIL, QUINN, ZED):  # even Ada, who owns Pat's team X
        error_of(bring_back(sign_in(app, email), mark_id), 404, "not_found")
    assert mark_of(session, mine) is not None
    assert bring_back(sign_in(app, PAT), mark_id).status_code == 200
    assert mark_of(session, mine) is None


def test_unknown_malformed_and_second_calls_are_404(app, session, world):
    mark = removed_x(app, session, world)
    ada = sign_in(app, OWNER_EMAIL)
    error_of(bring_back(ada, 987654), 404, "not_found")
    assert ada.post("/api/series/removed/abc/bring-back").status_code == 404
    assert bring_back(ada, mark.id).status_code == 200
    error_of(bring_back(ada, mark.id), 404, "not_found")  # already brought back


def test_it_needs_the_csrf_token_and_a_session(app, session, world, anon):
    mark = removed_x(app, session, world)
    ada = sign_in(app, OWNER_EMAIL)
    posted = ada.call("POST", f"/api/series/removed/{mark.id}/bring-back", csrf=False)
    assert posted.status_code in (401, 403)
    assert anon.post(f"/api/series/removed/{mark.id}/bring-back").status_code in (401, 403)
    assert anon.get("/api/series/removed").status_code in (401, 403)
    assert mark_of(session, world.x.id) is not None


# ---- what it does and does not do ----


def test_it_clears_exactly_one_mark_and_changes_nothing_else(app, session, world, template):
    """The same event removed in X, Y and Ada's Personal space; bringing back X's leaves the other
    two, and no meeting, task, question, row or agenda line is touched."""
    meeting = put(session, template, standup(session, personal_of(session, world.ada)),
                  who=world.ada, title="Kept")  # fmt: skip
    task_in(session, personal_of(session, world.ada), meeting, "a task")
    question_in(session, meeting, "a question", carry=True)
    mark = removed_x(app, session, world)
    mine = personal_of(session, world.ada)
    session.add(SeriesIgnored(team_id=mine, calendar_recurring_id=RID))
    session.add(SeriesIgnored(team_id=world.y.id, calendar_recurring_id=RID))
    session.add(SeriesAgendaItem(series_id=meeting.series_id, text="Budget", position=1))
    session.commit()
    before = counts(session)
    assert bring_back(sign_in(app, OWNER_EMAIL), mark.id).status_code == 200
    assert counts(session) == before
    assert mark_of(session, world.x.id) is None
    assert mark_of(session, mine) is not None and mark_of(session, world.y.id) is not None
    assert row_of(session, world.x.id) is None  # it lets the card be made; it does not make it


def test_the_hourly_sync_makes_a_personal_card_again_once_it_is_brought_back(app, session, world):
    event = calendar_event(session, world.ada, RID, MONDAY, title="Standup")
    join_for(session, world.ada, event, state="scheduled")
    mine = personal_of(session, world.ada)
    assert series_rules.sync_from_joins(session, MONDAY) == 1
    ada = sign_in(app, OWNER_EMAIL)
    assert ada.delete(f"/api/series/{row_of(session, mine).id}").status_code == 200
    assert series_rules.sync_from_joins(session, MONDAY) == 0  # removed: not made again
    mark = mark_of(session, mine)
    assert bring_back(ada, mark.id).status_code == 200
    assert series_rules.sync_from_joins(session, MONDAY) == 1  # now it is
    session.expire_all()
    assert row_of(session, mine) is not None
    assert row_of(session, world.x.id) is None  # never in the team


def test_a_teams_card_is_made_again_by_the_next_automatic_link_of_an_owners_recording(
    app, session, world, template
):
    """A team's card is not made by the hourly sync (that makes Personal cards). Ada, the owner,
    has a recording of the event auto-shared to X: refused while the mark stands, made after.
    The meetings of the event the team already has join the new card (the existing adoption)."""
    mark = removed_x(app, session, world)
    event = calendar_event(session, world.ada, RID, MONDAY)
    first = calendar_meeting(session, template, world.ada, event, shared_team_id=world.x.id)
    assert series_rules.link_from_calendar(session, first) is None
    session.commit()
    assert bring_back(sign_in(app, OWNER_EMAIL), mark.id).status_code == 200
    second = calendar_meeting(
        session,
        template,
        world.ada,
        calendar_event(session, world.ada, RID, MONDAY.replace(day=9)),
        shared_team_id=world.x.id,
    )
    made = series_rules.link_from_calendar(session, second)
    session.commit()
    assert made is not None and made.team_id == world.x.id
    session.expire_all()
    # #207's adoption, not new: making the card also groups the meetings of the same event that
    # the team already has (the first one, shared to X while the mark stood).
    assert session.get(Meeting, first.id).series_id == made.id
    assert session.get(Meeting, second.id).series_id == made.id


def test_with_the_flag_off_it_works_on_the_default_teams_mark(app, session, world, monkeypatch):
    monkeypatch.setattr(series_rules, "follow_enabled", lambda: False)
    mark = removed_x(app, session, world)
    ada = sign_in(app, OWNER_EMAIL)
    assert len(removed_of(ada, world.x.id).json()["removed"]) == 1
    assert bring_back(ada, mark.id).status_code == 200
    assert mark_of(session, world.x.id) is None


# ---- the hint on a Personal card ----


def page_of(who, row_id) -> dict:
    response = who.get(f"/api/series/{row_id}")
    assert response.status_code == 200, response.text
    return response.json()


def test_a_personal_page_names_the_team_card_of_the_same_recurring_meeting(
    app, session, world, template
):
    mine = standup(session, personal_of(session, world.ada), owner_id=world.ada.id)
    card = standup(session, world.x.id, owner_id=world.ada.id)
    ada = sign_in(app, OWNER_EMAIL)
    page = page_of(ada, mine.id)
    assert page["team_card"] == {
        "id": card.id,
        "team": {"id": world.x.id, "name": "X", "is_personal": False},
    }


def test_there_is_no_hint_without_a_team_card_or_on_other_pages(app, session, world):
    mine = standup(session, personal_of(session, world.ada), owner_id=world.ada.id)
    ada = sign_in(app, OWNER_EMAIL)
    assert page_of(ada, mine.id)["team_card"] is None  # no team card yet
    other = standup(session, world.x.id, rid="other", owner_id=world.ada.id)
    assert page_of(ada, mine.id)["team_card"] is None  # a team card of ANOTHER event
    team_page = standup(session, world.x.id, owner_id=world.ada.id)
    assert page_of(ada, team_page.id)["team_card"] is None  # a team's own page never has one
    assert page_of(ada, other.id)["team_card"] is None


def test_a_hand_made_personal_card_has_no_hint(app, session, world):
    mine = standup(session, personal_of(session, world.ada), rid=None, owner_id=world.ada.id)
    standup(session, world.x.id, rid=None, owner_id=world.ada.id)
    assert page_of(sign_in(app, OWNER_EMAIL), mine.id)["team_card"] is None


def test_the_hint_names_only_a_team_card_the_viewer_may_see(app, session, world):
    """Pat's Personal card; X has a card of the event too, but Pat is not shown it (it holds a
    meeting only Ada can read); Y's is another team he is in and may see (empty, his own event)."""
    mine = standup(session, personal_of(session, world.pat), owner_id=world.pat.id)
    hidden = standup(session, world.x.id, owner_id=world.ada.id)
    private = Meeting.__table__
    session.execute(  # a meeting of X's card that Pat cannot read: Ada's, private
        private.update().where(private.c.id == world.private.id).values(series_id=hidden.id)
    )
    session.commit()
    pat = sign_in(app, PAT)
    assert pat.get(f"/api/series/{hidden.id}").status_code == 404  # he cannot see X's card
    assert page_of(pat, mine.id)["team_card"] is None
    calendar_event(session, world.pat, RID, MONDAY)
    visible = standup(session, world.y.id, owner_id=world.quinn.id)
    assert page_of(pat, mine.id)["team_card"]["id"] == visible.id  # empty, on his own calendar


def test_with_the_flag_off_there_is_no_hint(app, session, world, monkeypatch):
    monkeypatch.setattr(series_rules, "follow_enabled", lambda: False)
    mine = standup(session, personal_of(session, world.ada), owner_id=world.ada.id)
    standup(session, world.x.id, owner_id=world.ada.id)
    assert page_of(sign_in(app, OWNER_EMAIL), mine.id)["team_card"] is None
