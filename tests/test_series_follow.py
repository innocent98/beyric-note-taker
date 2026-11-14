"""Recurring meetings follow the meetings, PR 1 (docs/superpowers/specs/2026-11-14-series-follow-
meetings.md, sections 3 to 10): the flag, the scope rule, the writers that keep series rows pure
and the backfill. Written before the code. Leak cases of section 9 that this PR touches: 1, 10, 11,
13, 14, 15, 17, 18, 19 (the readers, 2 to 9, 12 and 16, are PR 2).

The cast is `tests/test_multi_team_leaks.py`'s `world`: Ada records and owns team X; Pat is in X and
Y; Quinn owns Y and is only in Y; Zed owns Z (his own organisation). One recurring event, `rid-1`,
is the Standup of every one of them. The flag `SERIES_FOLLOW_MEETINGS` is ON in every test but the
ones that say "flag off".
"""

# ruff: noqa: F811  (fixtures are imported by name and used as arguments)
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app import deletion, meeting_teams, teams, visibility
from app import series as series_rules
from app.config import Settings
from app.models import Meeting, Series, SeriesAgendaItem, User
from app.series_backfill import split_mixed_series
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
from tests.test_multi_team_leaks import (  # noqa: F401  (fixtures used by name)
    PAT,
    QUINN,
    ZED,
    ids_of,
    multi_team_on,
    new_team,
    world,
)
from tests.test_multi_team_moved import NoVexa
from tests.test_series import MONDAY, calendar_event, join_for, meeting_in

RID = "rid-1"
LATER = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def follow_on(monkeypatch):
    monkeypatch.setattr(series_rules, "follow_enabled", lambda: True)


def flag_off(monkeypatch):
    monkeypatch.setattr(series_rules, "follow_enabled", lambda: False)


def standup(session, team_id, *, rid=RID, owner_id=None, title="Standup", **fields) -> Series:
    found = Series(
        team_id=team_id, calendar_recurring_id=rid, title=title, owner_user_id=owner_id, **fields
    )
    session.add(found)
    session.commit()
    return found


def row_of(session, team_id, rid=RID) -> Series | None:
    return session.scalars(
        select(Series).where(Series.team_id == team_id, Series.calendar_recurring_id == rid)
    ).first()


def personal_of(session, user) -> int:
    return teams.ensure_personal_team(session, user).id


def put(session, template, row, *, who, when=MONDAY, team=None, moved=None) -> Meeting:
    """A ready meeting of `who` inside `row`, shared to `team` (or moved to `moved`)."""
    meeting = meeting_in(session, template, row, when=when, user=who)
    meeting.shared_team_id = team
    meeting.owner_team_id = moved
    session.commit()
    return meeting


def stale(session, row) -> bool:
    session.refresh(row)
    return row.topics_checked_at is None


def mark_fresh(session, *rows) -> None:
    for row in rows:
        row.topics_checked_at = datetime(2026, 11, 1, tzinfo=UTC)
        row.topics_attempts = 3
    session.commit()


# ---- the flag ----


def test_the_flag_is_off_by_default_and_needs_multi_team_too(monkeypatch):
    assert Settings(_env_file=None).series_follow_meetings is False
    monkeypatch.undo()
    monkeypatch.setattr(series_rules, "get_settings", lambda: Settings(series_follow_meetings=True))
    monkeypatch.setattr(teams, "multi_team_enabled", lambda: False)
    assert series_rules.follow_enabled() is False
    monkeypatch.setattr(teams, "multi_team_enabled", lambda: True)
    assert series_rules.follow_enabled() is True
    monkeypatch.setattr(
        series_rules, "get_settings", lambda: Settings(series_follow_meetings=False)
    )
    assert series_rules.follow_enabled() is False


# ---- the one scope clause (visibility.py) ----


def test_the_scope_clause_and_the_python_rule_agree_on_every_state(session, world, template):
    """`scope_clause(team)` and `scope_team_of(meeting)` are one rule written twice (a query and a
    value): they must never disagree, for an ordinary team and for a personal one."""
    personal = teams.personal_team(session, world.ada.id)
    states = {
        "private": {},
        "shared_x": {"shared_team_id": world.x.id},
        "shared_y": {"shared_team_id": world.y.id},
        "moved_x": {"owner_team_id": world.x.id},
        "moved_y": {"owner_team_id": world.y.id},
    }
    made = {name: ready(session, template, **fields) for name, fields in states.items()}
    session.commit()
    for team in (session.get(type(personal), world.x.id), personal):
        in_scope = set(
            session.scalars(
                select(Meeting.id).where(
                    Meeting.id.in_([m.id for m in made.values()]),
                    visibility.series_scope_clause(team),
                )
            )
        )
        for name, meeting in made.items():
            home = visibility.scope_team_of(meeting)  # a team id, or None = recorder's personal
            expected = home == team.id if home is not None else team.is_personal
            assert (meeting.id in in_scope) == expected, (name, team.id)
    assert {n: visibility.scope_team_of(m) for n, m in made.items()} == {
        "private": None,
        "shared_x": world.x.id,
        "shared_y": world.y.id,
        "moved_x": world.x.id,
        "moved_y": world.y.id,
    }


def test_the_personal_scope_is_the_recorders_own_private_meetings_only(session, world, template):
    personal = teams.personal_team(session, world.ada.id)
    mine = ready(session, template)  # Ada's, private
    shared = ready(session, template, shared_team_id=world.x.id)
    moved = ready(session, template, owner_team_id=world.x.id)
    pats = ready(session, template, owner_user_id=world.pat.id)  # private, but Pat's
    session.commit()
    found = set(
        session.scalars(
            select(Meeting.id).where(
                Meeting.id.in_([mine.id, shared.id, moved.id, pats.id]),
                visibility.series_scope_clause(personal),
            )
        )
    )
    assert found == {mine.id}


# ---- case 1: Monday to X, Tuesday private, Wednesday to Y: one row each, one meeting each ----


def test_each_change_of_team_puts_the_meeting_into_its_scopes_row_and_marks_both_rows_stale(
    session, world, template
):
    ada_personal = personal_of(session, world.ada)
    legacy = standup(session, world.x.id, owner_id=world.ada.id)
    monday = put(session, template, legacy, who=world.ada, team=world.x.id)
    tuesday = put(session, template, legacy, who=world.ada, when=MONDAY + timedelta(days=1))
    mark_fresh(session, legacy)
    meeting_teams.unshare(session, tuesday)  # already private: nothing changes team
    session.commit()
    assert tuesday.series_id != legacy.id  # a private meeting follows to the personal row
    personal_row = row_of(session, ada_personal)
    assert tuesday.series_id == personal_row.id
    assert monday.series_id == legacy.id
    assert stale(session, legacy) and stale(session, personal_row)
    assert personal_row.title == "Standup" and personal_row.owner_user_id == world.ada.id


def test_one_standup_one_row_per_team_with_exactly_that_teams_meetings(session, world, template):
    pat = world.pat
    pat_personal = personal_of(session, pat)
    legacy = standup(session, world.x.id, owner_id=pat.id)
    monday = put(session, template, legacy, who=pat, team=world.x.id)
    tuesday = put(session, template, legacy, who=pat, when=MONDAY + timedelta(days=1))
    wednesday = put(session, template, legacy, who=pat, when=MONDAY + timedelta(days=2))
    meeting_teams.share(session, pat, wednesday, world.y.id)  # X's row -> Y's row
    meeting_teams.unshare(session, tuesday)
    session.commit()
    x_row, y_row = row_of(session, world.x.id), row_of(session, world.y.id)
    me_row = row_of(session, pat_personal)
    held = {
        r.id: sorted(
            m.id for m in session.scalars(select(Meeting).where(Meeting.series_id == r.id))
        )
        for r in (x_row, y_row, me_row)
    }
    assert held == {
        x_row.id: [monday.id],
        y_row.id: [wednesday.id],
        me_row.id: [tuesday.id],
    }
    assert session.scalar(select(func.count()).select_from(Series)) == 3  # one row each, no more
    # the readers that exist today already show each page its own meeting
    assert [m.id for m in series_rules.meetings_of(session, x_row, pat)] == [monday.id]
    assert [m.id for m in series_rules.meetings_of(session, y_row, pat)] == [wednesday.id]
    assert [m.id for m in series_rules.meetings_of(session, me_row, pat)] == [tuesday.id]


def test_moving_and_moving_back_carry_the_series_with_the_meeting(session, world, template):
    legacy = standup(session, world.x.id, owner_id=world.ada.id)
    meeting = put(session, template, legacy, who=world.ada, team=world.x.id)
    ada_personal = personal_of(session, world.ada)
    meeting_teams.unshare(session, meeting)
    assert meeting.series_id == row_of(session, ada_personal).id
    meeting_teams.move(session, world.ada, meeting, world.x.id)
    assert meeting.series_id == row_of(session, world.x.id).id  # back to X's row, found not remade
    assert row_of(session, world.x.id).id == legacy.id
    meeting_teams.move_back(session, world.ada, meeting)
    assert meeting.series_id == row_of(session, ada_personal).id
    assert session.scalar(select(func.count()).select_from(Series)) == 2


def test_a_meeting_with_no_series_is_left_alone(session, world, template):
    meeting = ready(session, template, shared_team_id=world.x.id)
    meeting_teams.unshare(session, meeting)
    assert meeting.series_id is None
    assert session.scalar(select(func.count()).select_from(Series)) == 0


def test_following_never_copies_the_project_link_or_the_agenda_lines(session, world, template):
    legacy = standup(session, world.x.id, owner_id=world.ada.id, project_id=4242)
    session.add(SeriesAgendaItem(series_id=legacy.id, text="Tech's line", created_by=world.ada.id))
    meeting = put(session, template, legacy, who=world.ada, team=world.x.id)
    meeting_teams.unshare(session, meeting)
    session.commit()
    sibling = session.get(Series, meeting.series_id)
    assert sibling.id != legacy.id and sibling.project_id is None  # case 15
    assert series_rules.agenda_items(session, sibling) == []  # case 6: lines stay on their row
    assert [i.text for i in series_rules.agenda_items(session, legacy)] == ["Tech's line"]
    assert legacy.project_id == 4242


# ---- case 14: the same recurring id in two organisations never merges ----


def test_the_same_recurring_id_in_two_organisations_stays_two_rows(session, world, template):
    ada_row = standup(session, world.x.id, owner_id=world.ada.id, title="Tech standup")
    zed_row = standup(session, world.z.id, owner_id=world.zed.id, title="Zed's standup")
    session.add(SeriesAgendaItem(series_id=zed_row.id, text="Zed's line", created_by=world.zed.id))
    mine = put(session, template, ada_row, who=world.ada, team=world.x.id)
    theirs = put(session, template, zed_row, who=world.zed, team=world.z.id)
    meeting_teams.unshare(session, mine)
    meeting_teams.unshare(session, theirs)
    session.commit()
    rows = {(s.team_id, s.title) for s in session.scalars(select(Series))}
    assert rows == {
        (world.x.id, "Tech standup"),
        (world.z.id, "Zed's standup"),
        (personal_of(session, world.ada), "Tech standup"),
        (personal_of(session, world.zed), "Zed's standup"),
    }
    assert [i.text for i in series_rules.agenda_items(session, zed_row)] == ["Zed's line"]


# ---- hand-made rows follow a same-titled row (open question 6) ----


def test_a_hand_made_series_follows_a_same_titled_row_and_never_a_calendar_one(
    session, world, template
):
    made = Series(team_id=world.x.id, owner_user_id=world.ada.id, title="Weekly sync")
    session.add(made)
    session.commit()
    calendar_twin = standup(
        session, personal_of(session, world.ada), rid="rid-9", title="Weekly sync"
    )
    first = put(session, template, made, who=world.ada, team=world.x.id)
    second = put(session, template, made, who=world.ada, team=world.x.id, when=LATER)
    meeting_teams.unshare(session, first)
    meeting_teams.unshare(session, second)
    session.commit()
    assert first.series_id == second.series_id  # found the second time, not made twice
    sibling = session.get(Series, first.series_id)
    assert sibling.id != calendar_twin.id  # a calendar row is not a hand-made one's twin
    assert sibling.team_id == personal_of(session, world.ada)
    assert sibling.calendar_recurring_id is None and sibling.title == "Weekly sync"
    # a third meeting goes to a row that already exists
    third = put(session, template, made, who=world.ada, team=world.x.id, when=LATER)
    meeting_teams.unshare(session, third)
    assert third.series_id == sibling.id


def test_a_meeting_is_taken_out_of_its_series_when_the_new_team_is_full(
    session, world, template, monkeypatch
):
    monkeypatch.setattr(series_rules, "MAX_SERIES_PER_TEAM", 1)
    legacy = standup(session, world.x.id, owner_id=world.ada.id)
    mark_fresh(session, legacy)
    ada_personal = personal_of(session, world.ada)
    standup(session, ada_personal, rid="other", title="Another")  # personal team is at its limit
    meeting = put(session, template, legacy, who=world.ada, team=world.x.id)
    meeting_teams.unshare(session, meeting)
    session.commit()
    assert meeting.series_id is None  # never left mixed into X's row
    assert stale(session, legacy)
    assert session.scalar(select(func.count()).select_from(Series)) == 2


# ---- cases 10 and 11: a person who left X ----


def test_a_person_who_left_x_loses_x_and_their_meetings_follow_them_to_their_personal_row(
    session, world, template
):
    x_row = standup(session, world.x.id, owner_id=world.ada.id)
    pat_meeting = put(session, template, x_row, who=world.pat, team=world.x.id)
    ada_meeting = put(session, template, x_row, who=world.ada, team=world.x.id, when=LATER)
    mark_fresh(session, x_row)
    teams.leave(session, world.pat, datetime.now(UTC), team_id=world.x.id)
    session.commit()
    session.expire_all()
    pat_row = row_of(session, personal_of(session, world.pat))
    assert pat_row is not None and pat_row.calendar_recurring_id == RID
    assert session.get(Meeting, pat_meeting.id).series_id == pat_row.id  # not None, not X's row
    assert session.get(Meeting, ada_meeting.id).series_id == x_row.id
    assert series_rules.get(session, world.pat, x_row.id) is None  # X's row is 404 to Pat now
    assert series_rules.get(session, world.pat, pat_row.id) is not None
    assert stale(session, x_row)  # X's topics no longer cite Pat's meeting
    # no X mail for him: he is not an active member of X
    assert world.pat.id not in {a.user_id for a in series_rules.recipients(session, x_row)}


def test_a_moved_meeting_stays_in_xs_row_when_its_recorder_leaves(session, world, template):
    x_row = standup(session, world.x.id, owner_id=world.ada.id)
    own = put(session, template, x_row, who=world.pat, team=world.x.id)
    kept = put(session, template, x_row, who=world.pat, moved=world.x.id, when=LATER)
    teams.leave(session, world.pat, datetime.now(UTC), team_id=world.x.id)
    session.commit()
    session.expire_all()
    assert session.get(Meeting, kept.id).series_id == x_row.id  # case 11
    assert session.get(Meeting, own.id).series_id != x_row.id
    # X's members read it; the ex-recorder reads nothing of it
    assert kept.id in ids_of(session, world.ada)
    assert kept.id not in ids_of(session, world.pat)
    assert series_rules.meetings_of(session, x_row, world.pat) == []
    assert [m.id for m in series_rules.meetings_of(session, x_row, world.ada)] == [kept.id]
    assert world.pat.id not in {a.user_id for a in series_rules.recipients(session, x_row)}


def test_a_moved_meeting_stays_in_the_row_when_its_recorder_deletes_their_account(
    session, world, template
):
    x_row = standup(session, world.x.id, owner_id=world.ada.id)
    kept = put(session, template, x_row, who=world.pat, moved=world.x.id)
    private = put(session, template, x_row, who=world.pat, when=LATER)
    deletion.purge_user(session, session.get(User, world.pat.id), NoVexa(), force=True)
    session.expire_all()
    still = session.get(Meeting, kept.id)
    assert still is not None and still.series_id == x_row.id
    assert session.get(Series, x_row.id) is not None
    assert session.get(Meeting, private.id) is None  # what was only his goes with him
    assert world.pat.id not in {a.user_id for a in series_rules.recipients(session, x_row)}


# ---- case 13: a team is deleted and the moved meeting is given back ----


def test_a_meeting_given_back_when_its_team_is_deleted_lands_in_its_recorders_personal_row(
    session, world, template
):
    team_w = new_team(session, world.pat, "W", world.ada)
    w_row = standup(session, team_w.id, owner_id=world.pat.id)
    meeting = put(session, template, w_row, who=world.ada, moved=team_w.id)
    teams.leave(session, world.ada, datetime.now(UTC), team_id=team_w.id)  # moved: it stays
    session.commit()
    assert meeting.series_id == w_row.id
    deletion.purge_user(session, session.get(User, world.pat.id), NoVexa(), force=True)
    session.expire_all()
    back = session.get(Meeting, meeting.id)
    assert back.owner_team_id is None and back.owner_user_id == world.ada.id
    assert back.series_id is not None  # not nulled by the team's cascade
    row = session.get(Series, back.series_id)
    assert row.team_id == personal_of(session, world.ada) and row.calendar_recurring_id == RID
    assert back.id in ids_of(session, world.ada)


# ---- the pipeline link and the empty pre-recording cards (cases 14, 19) ----


def calendar_meeting(session, template, who, event, **fields) -> Meeting:
    meeting = ready(session, template, source="calendar", created_at=event.starts_at, **fields)
    meeting.owner_user_id = who.id
    session.commit()
    join_for(session, who, event, meeting, state="completed")
    return meeting


def test_the_link_puts_a_calendar_meeting_into_the_row_of_its_scope(session, world, template):
    shared = calendar_meeting(
        session,
        template,
        world.ada,
        calendar_event(session, world.ada, RID, MONDAY),
        shared_team_id=world.x.id,
    )
    private = calendar_meeting(
        session, template, world.ada, calendar_event(session, world.ada, RID, LATER)
    )
    in_y = calendar_meeting(
        session,
        template,
        world.pat,
        calendar_event(session, world.pat, RID, LATER + timedelta(days=1)),
        shared_team_id=world.y.id,
    )
    for meeting in (shared, private, in_y):
        series_rules.link_from_calendar(session, meeting)
    session.commit()
    rows = {m.id: session.get(Series, m.series_id).team_id for m in (shared, private, in_y)}
    assert rows == {
        shared.id: world.x.id,
        private.id: personal_of(session, world.ada),  # Ada's default team is X, but it is private
        in_y.id: world.y.id,
    }
    assert session.scalar(select(func.count()).select_from(Series)) == 3


def test_a_moved_calendar_meeting_goes_to_the_owning_teams_row(session, world, template):
    meeting = calendar_meeting(
        session,
        template,
        world.pat,
        calendar_event(session, world.pat, RID, MONDAY),
        owner_team_id=world.y.id,
    )
    assert series_rules.link_from_calendar(session, meeting).team_id == world.y.id


def test_a_card_made_before_the_first_recording_moves_with_a_private_first_meeting(
    session, world, template
):
    event = calendar_event(session, world.ada, RID, MONDAY)
    scheduled = join_for(session, world.ada, event, state="scheduled")
    assert series_rules.sync_from_joins(session, MONDAY) == 1
    card = row_of(session, world.x.id)  # Ada's default team, empty
    assert card is not None and card.owner_user_id == world.ada.id
    meeting = ready(session, template, source="calendar", created_at=event.starts_at)  # private
    scheduled.meeting_id, scheduled.state = meeting.id, "completed"
    session.commit()
    series_rules.link_from_calendar(session, meeting)
    session.commit()
    assert row_of(session, world.x.id) is None  # case 19: gone from X's list
    mine = row_of(session, personal_of(session, world.ada))
    assert mine.id == card.id and meeting.series_id == card.id  # the card itself was re-keyed
    assert (
        series_rules.sync_from_joins(session, MONDAY) == 0
    )  # and the hourly sync does not remake it
    assert row_of(session, world.x.id) is None


def test_a_card_with_waiting_lines_or_a_project_or_a_teammates_hand_stays_where_it_was(
    session, world, template
):
    cases = {}
    for name in ("lines", "project", "teammate"):
        rid = f"rid-{name}"
        who = world.pat if name == "teammate" else world.ada
        card = standup(
            session,
            world.x.id,
            rid=rid,
            owner_id=world.pat.id if name == "teammate" else world.ada.id,
            project_id=7 if name == "project" else None,
        )
        if name == "lines":
            session.add(
                SeriesAgendaItem(series_id=card.id, text="Wait for X", created_by=world.pat.id)
            )
        event = calendar_event(session, world.ada, rid, MONDAY)
        meeting = calendar_meeting(session, template, world.ada, event)
        series_rules.link_from_calendar(session, meeting)
        cases[name] = (card, meeting)
        del who
    session.commit()
    for name, (card, meeting) in cases.items():
        assert row_of(session, world.x.id, f"rid-{name}").id == card.id, name  # card kept in X
        assert meeting.series_id != card.id, name  # the private meeting made its own personal row
        assert session.get(Series, meeting.series_id).team_id == personal_of(session, world.ada)
    lines_card = cases["lines"][0]
    assert [i.text for i in series_rules.agenda_items(session, lines_card)] == ["Wait for X"]


def test_an_empty_card_shows_to_its_creator_only_while_it_has_no_meetings(session, world, template):
    """Case 19: Ada made X's Standup card; only Pat's private meeting is in it (a straggler row).
    Once a row has meetings, only a meeting the viewer may read lists it: Ada made it, and still
    does not see it."""
    card = standup(session, world.x.id, owner_id=world.ada.id)
    assert card in series_rules.list_for(session, world.ada, world.x.id)  # empty: the creator's
    put(session, template, card, who=world.pat)  # Pat's private meeting
    assert card not in series_rules.list_for(session, world.ada, world.x.id)
    assert series_rules.get(session, world.ada, card.id) is None


def test_with_the_flag_off_a_creator_still_sees_a_row_that_holds_only_a_meeting_they_cannot_read(
    session, world, template, monkeypatch
):
    flag_off(monkeypatch)
    card = standup(session, world.x.id, owner_id=world.ada.id)
    put(session, template, card, who=world.pat)
    assert card in series_rules.list_for(session, world.ada, world.x.id)  # today's rule


# ---- attaching by hand uses the same scope ----


def test_a_private_meeting_can_only_join_a_personal_series_and_a_shared_one_its_teams(
    session, world, template
):
    x_row = standup(session, world.x.id, owner_id=world.ada.id)
    mine = Series(team_id=personal_of(session, world.ada), owner_user_id=world.ada.id, title="Mine")
    session.add(mine)
    private = ready(session, template)
    shared = ready(session, template, shared_team_id=world.x.id)
    session.commit()
    with pytest.raises(series_rules.SeriesRefused) as refused:
        series_rules.attach(session, world.ada, private, x_row.id)
    assert refused.value.status == 404
    assert series_rules.attach(session, world.ada, private, mine.id).id == mine.id
    assert series_rules.attach(session, world.ada, shared, x_row.id).id == x_row.id
    with pytest.raises(series_rules.SeriesRefused):
        series_rules.attach(session, world.ada, shared, mine.id)


def test_making_a_recurring_meeting_from_a_reused_link_keeps_each_scope_apart(
    session, world, template
):
    private = ready(session, template, native_id="reused")
    other_private = ready(session, template, native_id="reused")
    other_shared = ready(session, template, native_id="reused", shared_team_id=world.x.id)
    session.commit()
    made = series_rules.make_from_reused_link(session, world.ada, private)
    assert made.team_id == personal_of(session, world.ada)
    session.refresh(other_private)
    session.refresh(other_shared)
    assert other_private.series_id == made.id and other_shared.series_id is None


# ---- case 18: flag off is exactly today ----


def test_flag_off_a_change_of_team_changes_no_series_and_a_leaver_is_taken_out(
    session, world, template, monkeypatch
):
    flag_off(monkeypatch)
    x_row = standup(session, world.x.id, owner_id=world.ada.id)
    meeting = put(session, template, x_row, who=world.ada, team=world.x.id)
    mark_fresh(session, x_row)
    meeting_teams.unshare(session, meeting)
    assert meeting.series_id == x_row.id  # still X's row, as before
    assert stale(session, x_row)  # and only the topics were marked
    assert session.scalar(select(func.count()).select_from(Series)) == 1
    pat_meeting = put(session, template, x_row, who=world.pat, team=world.x.id)
    teams.leave(session, world.pat, datetime.now(UTC), team_id=world.x.id)
    session.commit()
    session.refresh(pat_meeting)
    assert pat_meeting.series_id is None  # taken out, not moved to a personal row
    assert session.scalar(select(func.count()).select_from(Series)) == 1


def test_flag_off_the_link_and_the_attach_rule_use_the_default_team(
    session, world, template, monkeypatch
):
    flag_off(monkeypatch)
    meeting = calendar_meeting(
        session, template, world.ada, calendar_event(session, world.ada, RID, MONDAY)
    )  # private
    assert series_rules.link_from_calendar(session, meeting).team_id == world.x.id
    assert series_rules.meeting_team_id(session, world.ada, meeting) == world.x.id


def test_flag_off_a_card_is_remade_by_the_sync_even_after_a_meeting_is_recorded(
    session, world, template, monkeypatch
):
    flag_off(monkeypatch)
    event = calendar_event(session, world.ada, RID, MONDAY)
    join_for(session, world.ada, event, state="scheduled")
    assert series_rules.sync_from_joins(session, MONDAY) == 1
    assert series_rules.sync_from_joins(session, MONDAY) == 0
    assert row_of(session, world.x.id) is not None


def test_flag_off_a_meeting_given_back_loses_its_series_as_before(
    session, world, template, monkeypatch
):
    flag_off(monkeypatch)
    team_w = new_team(session, world.pat, "W", world.ada)
    w_row = standup(session, team_w.id, owner_id=world.pat.id)
    meeting = put(session, template, w_row, who=world.ada, moved=team_w.id)
    teams.leave(session, world.ada, datetime.now(UTC), team_id=team_w.id)
    session.commit()
    deletion.purge_user(session, session.get(User, world.pat.id), NoVexa(), force=True)
    session.expire_all()
    assert session.get(Meeting, meeting.id).series_id is None


# ---- case 17: the backfill ----


def build_mixed_world(session, world, template):
    """The table of section 8: a mixed row, a personal row holding team meetings, an empty card, a
    hand-made row, a moved meeting, and another organisation's row with the same recurring id."""
    ada, pat, zed = world.ada, world.pat, world.zed
    ada_personal = personal_of(session, ada)
    mixed = standup(session, world.x.id, owner_id=ada.id, project_id=4242)
    session.add(SeriesAgendaItem(series_id=mixed.id, text="Tech's line", created_by=ada.id))
    legacy = {
        "monday_x": put(session, template, mixed, who=ada, team=world.x.id),
        "tuesday_private": put(session, template, mixed, who=ada, when=MONDAY + timedelta(days=1)),
        "wednesday_y": put(session, template, mixed, who=pat, team=world.y.id, when=LATER),
        "pat_moved": put(session, template, mixed, who=pat, moved=world.x.id, when=LATER),
    }
    on_personal = standup(session, ada_personal, rid="rid-2", title="Planning", owner_id=ada.id)
    legacy["p_shared"] = put(session, template, on_personal, who=ada, team=world.x.id)
    legacy["p_moved"] = put(session, template, on_personal, who=ada, moved=world.x.id, when=LATER)
    legacy["p_private"] = put(session, template, on_personal, who=ada)
    empty = standup(session, world.x.id, rid="rid-3", title="Retro", owner_id=ada.id)
    by_hand = Series(team_id=world.x.id, owner_user_id=ada.id, title="Weekly sync")
    session.add(by_hand)
    session.commit()
    legacy["hand_private"] = put(session, template, by_hand, who=ada)
    legacy["hand_shared"] = put(session, template, by_hand, who=ada, team=world.x.id)
    zed_row = standup(session, world.z.id, owner_id=zed.id, title="Zed's standup")
    legacy["zed"] = put(session, template, zed_row, who=zed, team=world.z.id)
    return legacy, {"mixed": mixed, "personal": on_personal, "empty": empty, "hand": by_hand}


def snapshot(session):
    session.expire_all()
    return (
        sorted((m.id, m.series_id) for m in session.scalars(select(Meeting))),
        sorted((s.id, s.team_id, s.project_id) for s in session.scalars(select(Series))),
        sorted(
            (i.id, i.series_id, i.consumed_at) for i in session.scalars(select(SeriesAgendaItem))
        ),
    )


def test_a_dry_run_changes_nothing_and_reports_what_it_would_do(session, world, template):
    build_mixed_world(session, world, template)
    before = snapshot(session)
    report = split_mixed_series(session, apply=False)
    assert snapshot(session) == before
    assert report.applied is False
    assert report.meetings_moved == 5 and report.rows_split == 3
    assert report.rows_made == 4


def test_the_backfill_puts_every_meeting_in_the_row_of_its_scope(session, world, template):
    legacy, rows = build_mixed_world(session, world, template)
    ada_personal = personal_of(session, world.ada)
    pat_personal = personal_of(session, world.pat)
    report = split_mixed_series(session, apply=True)
    session.expire_all()
    where = {n: (session.get(Series, m.series_id).team_id) for n, m in legacy.items()}
    y_row = row_of(session, world.y.id)
    assert where == {
        "monday_x": world.x.id,
        "tuesday_private": ada_personal,
        "wednesday_y": world.y.id,
        "pat_moved": world.x.id,  # a moved meeting never leaves its team's row
        "p_shared": world.x.id,
        "p_moved": world.x.id,
        "p_private": ada_personal,
        "hand_private": ada_personal,
        "hand_shared": world.x.id,
        "zed": world.z.id,
    }
    assert y_row is not None and y_row.title == "Standup" and y_row.project_id is None
    assert row_of(session, pat_personal) is None  # Pat has no private meeting: no row made
    assert legacy["monday_x"].series_id == rows["mixed"].id  # the team's row kept its meetings
    assert report.applied is True and report.meetings_moved == 5
    assert report.meetings_cut_loose == 0


def test_a_second_backfill_changes_nothing(session, world, template):
    build_mixed_world(session, world, template)
    split_mixed_series(session, apply=True)
    after_first = snapshot(session)
    again = split_mixed_series(session, apply=True)
    assert snapshot(session) == after_first
    assert again.meetings_moved == 0 and again.rows_split == 0 and again.rows_made == 0


def test_the_backfill_keeps_the_agenda_lines_the_empty_card_and_the_other_organisations_row(
    session, world, template
):
    legacy, rows = build_mixed_world(session, world, template)
    zed_before = snapshot(session)[1]
    split_mixed_series(session, apply=True)
    session.expire_all()
    assert [i.text for i in series_rules.agenda_items(session, rows["mixed"])] == ["Tech's line"]
    assert session.scalar(select(func.count()).select_from(SeriesAgendaItem)) == 1
    assert session.get(Series, rows["empty"].id).team_id == world.x.id  # empty cards stay
    assert session.get(Series, rows["mixed"].id).project_id == 4242  # the team's link stays
    zed_row = session.get(Meeting, legacy["zed"].id).series_id
    assert any(row[0] == zed_row for row in zed_before)
    assert session.get(Series, zed_row).title == "Zed's standup"


def test_the_backfill_marks_both_rows_stale_and_works_with_the_flag_off(
    session, world, template, monkeypatch
):
    flag_off(monkeypatch)  # it is safe to run before anything is switched on
    legacy, rows = build_mixed_world(session, world, template)
    mark_fresh(session, rows["mixed"])
    split_mixed_series(session, apply=True)
    assert stale(session, rows["mixed"])
    sibling = session.get(Series, legacy["tuesday_private"].series_id)
    assert stale(session, sibling) and sibling.team_id == personal_of(session, world.ada)


def test_the_backfill_report_has_counts_only(session, world, template):
    build_mixed_world(session, world, template)
    text = "\n".join(split_mixed_series(session, apply=False).lines())
    for private_word in ("Standup", "Planning", "Weekly", "Zed", "Ada", "Pat", "example.test"):
        assert private_word not in text
    assert "DRY RUN" in text and "nothing was changed" in text.lower()
    applied = "\n".join(split_mixed_series(session, apply=True).lines())
    assert "DRY RUN" not in applied and "changed" in applied.lower()


def test_the_backfill_cuts_a_meeting_loose_rather_than_overfill_a_team(
    session, world, template, monkeypatch
):
    monkeypatch.setattr(series_rules, "MAX_SERIES_PER_TEAM", 1)
    mixed = standup(session, world.x.id, owner_id=world.ada.id)
    standup(session, personal_of(session, world.ada), rid="other", title="Other")
    stray = put(session, template, mixed, who=world.ada)  # private: wants a personal row
    report = split_mixed_series(session, apply=True)
    session.refresh(stray)
    assert stray.series_id is None and report.meetings_cut_loose == 1


def test_the_backfill_skips_a_meeting_whose_recorder_is_gone(session, world, template):
    mixed = standup(session, world.x.id, owner_id=world.ada.id)
    gone = put(session, template, mixed, who=world.pat)
    session.get(User, world.pat.id).status = "deleted"
    session.commit()
    split_mixed_series(session, apply=True)
    session.refresh(gone)
    assert gone.series_id == mixed.id
