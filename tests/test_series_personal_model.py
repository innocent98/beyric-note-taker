"""The simpler recurring model (Adebayo, 2026-11-15/16: "I just don't want us to complicate
things"). Written before the code.

1. A recurring card from a person's OWN calendar lives in their PERSONAL space, always: the hourly
   sync makes the placeholder there, never in an ordinary team, even for a person whose default
   team is one.
2. A TEAM card is made only by an owner or admin of the team: when they share (or move, or auto-
   share) a meeting of a recurring calendar event to it, or by hand. A plain member cannot (403 on
   the hand routes; a share by one makes no card). A meeting a plain member shares still goes INTO
   a card the team already has. Adding a meeting by hand to a team card is owner/admin only too.
3. An explicit owner/admin creation clears the "ignored" mark of that scope.
4. Removing an empty legacy row by its plain-member creator writes no team-wide mark.
5. A cleanup removes EMPTY calendar-derived cards of ordinary teams, writing no marks.
6. A calendar-linked Personal card with one recorded meeting does queue the reminders.

The cast is `tests/test_multi_team_leaks.py`'s `world`: Ada records and owns team X (her only
ordinary team); Pat is a plain member of X and Y; Dee (made here) is an admin of X; Quinn owns Y.
The flag `SERIES_FOLLOW_MEETINGS` is ON unless a test says "flag off".
"""

# ruff: noqa: F811  (fixtures are imported by name and used as arguments)
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app import meeting_teams, series_mail
from app import series as series_rules
from app.models import (
    MailOutbox,
    Meeting,
    Project,
    Series,
    SeriesAgendaItem,
    SeriesIgnored,
    SeriesReminder,
    Team,
    TeamMember,
)
from app.series_backfill import remove_empty_team_cards
from tests.test_api import (  # noqa: F401  (fixtures used by name)
    OWNER_EMAIL,
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
    multi_team_on,
    new_team,
    world,
)
from tests.test_series import MONDAY, calendar_event, join_for
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

TUESDAY = MONDAY + timedelta(days=1)
FORBIDDEN_TEXT = "You can't do that here. Ask an owner or admin of your team."


@pytest.fixture(autouse=True)
def follow_on(monkeypatch):
    monkeypatch.setattr(series_rules, "follow_enabled", lambda: True)


@pytest.fixture
def dee(session, world):
    user = make_user(session, "dee@example.test")
    session.add(TeamMember(team_id=world.x.id, user_id=user.id, role="admin"))
    session.commit()
    return user


def marked(session, team_id, rid=RID) -> bool:
    return series_rules.is_ignored(session, team_id, rid)


def scheduled(session, user, rid, starts=MONDAY):
    """A recurring event on the person's calendar with a join the scheduler will run."""
    event = calendar_event(session, user, rid, starts)
    join_for(session, user, event, state="scheduled")
    return event


def linked(session, template, user, event, **fields) -> Meeting:
    """A recorded meeting of the event, linked by the real pipeline link."""
    meeting = calendar_meeting(session, template, user, event, **fields)
    series_rules.link_from_calendar(session, meeting)
    session.commit()
    return meeting


# ---- 1. the placeholder is the person's own, in their Personal space ----


def test_the_sync_makes_a_calendar_card_in_the_personal_space_never_in_an_ordinary_team(
    session, world
):
    scheduled(session, world.ada, "rid-ada")  # Ada has one ordinary team: X is her default team
    scheduled(session, world.pat, "rid-pat")  # Pat has two: his default is his personal team
    assert series_rules.sync_from_joins(session, MONDAY) == 2
    ada_card = row_of(session, personal_of(session, world.ada), "rid-ada")
    assert ada_card is not None and ada_card.owner_user_id == world.ada.id
    assert row_of(session, world.x.id, "rid-ada") is None
    assert row_of(session, personal_of(session, world.pat), "rid-pat") is not None
    assert (
        session.scalar(
            select(func.count()).select_from(Series).join(Team).where(Team.is_personal.is_(False))
        )
        == 0
    )


def test_the_personal_card_shows_on_her_personal_list_and_not_on_xs(app, session, world):
    scheduled(session, world.ada, "rid-ada")
    series_rules.sync_from_joins(session, MONDAY)
    ada = sign_in(app, OWNER_EMAIL)
    mine = ada.client.get(
        "/api/series", headers={"X-Team-Id": str(personal_of(session, world.ada))}
    ).json()["series"]
    theirs = ada.client.get("/api/series", headers={"X-Team-Id": str(world.x.id)}).json()["series"]
    assert [c["title"] for c in mine] == ["Monday planning"] and theirs == []


def test_flag_off_the_sync_still_makes_the_card_in_the_default_team(session, world, monkeypatch):
    monkeypatch.setattr(series_rules, "follow_enabled", lambda: False)
    scheduled(session, world.ada, "rid-ada")
    assert series_rules.sync_from_joins(session, MONDAY) == 1
    assert row_of(session, world.x.id, "rid-ada") is not None


def test_a_removed_personal_card_is_not_made_again_anywhere(session, world):
    scheduled(session, world.ada, "rid-ada")
    session.add(
        SeriesIgnored(team_id=personal_of(session, world.ada), calendar_recurring_id="rid-ada")
    )
    session.commit()
    assert series_rules.sync_from_joins(session, MONDAY) == 0
    assert session.scalar(select(func.count()).select_from(Series)) == 0


# ---- 2. a team card is made by an owner or admin only ----


def test_an_owner_whose_recorded_meeting_is_shared_to_the_team_makes_the_team_card(
    session, world, template
):
    meeting = linked(
        session,
        template,
        world.ada,
        calendar_event(session, world.ada, RID, MONDAY),
        shared_team_id=world.x.id,
    )
    card = row_of(session, world.x.id)
    assert card is not None and meeting.series_id == card.id and card.owner_user_id == world.ada.id


def test_a_plain_members_shared_meeting_makes_no_team_card_and_is_left_ungrouped(
    session, world, template
):
    meeting = linked(
        session,
        template,
        world.pat,
        calendar_event(session, world.pat, RID, MONDAY),
        shared_team_id=world.x.id,
    )
    assert row_of(session, world.x.id) is None and meeting.series_id is None
    assert meeting.shared_team_id == world.x.id  # shared as ever


def test_a_plain_members_shared_meeting_goes_into_a_card_the_team_already_has(
    session, world, template
):
    card = standup(session, world.x.id, owner_id=world.ada.id)
    meeting = linked(
        session,
        template,
        world.pat,
        calendar_event(session, world.pat, RID, MONDAY),
        shared_team_id=world.x.id,
    )
    assert meeting.series_id == card.id
    assert (
        session.scalar(select(func.count()).select_from(Series).where(Series.team_id == world.x.id))
        == 1
    )


def test_an_owner_sharing_her_private_recurring_meeting_makes_the_team_card(
    session, world, template
):
    meeting = linked(session, template, world.ada, calendar_event(session, world.ada, RID, MONDAY))
    mine = personal_of(session, world.ada)
    assert session.get(Series, meeting.series_id).team_id == mine
    meeting_teams.share(session, world.ada, meeting, world.x.id)
    session.commit()
    card = row_of(session, world.x.id)
    assert card is not None and meeting.series_id == card.id and card.owner_user_id == world.ada.id
    assert row_of(session, mine) is not None  # her personal card stays


def test_an_admin_sharing_makes_the_team_card_too(session, world, template, dee):
    meeting = linked(session, template, dee, calendar_event(session, dee, RID, MONDAY))
    meeting_teams.share(session, dee, meeting, world.x.id)
    session.commit()
    assert row_of(session, world.x.id) is not None and meeting.series_id is not None


def test_a_plain_member_sharing_makes_no_card_but_the_share_works(session, world, template):
    meeting = linked(session, template, world.pat, calendar_event(session, world.pat, RID, MONDAY))
    meeting_teams.share(session, world.pat, meeting, world.x.id)
    session.commit()
    assert row_of(session, world.x.id) is None
    assert meeting.series_id is None and meeting.shared_team_id == world.x.id


def test_a_plain_member_sharing_joins_a_team_card_that_exists(session, world, template):
    card = standup(session, world.x.id, owner_id=world.ada.id)
    meeting = linked(session, template, world.pat, calendar_event(session, world.pat, RID, MONDAY))
    meeting_teams.share(session, world.pat, meeting, world.x.id)
    session.commit()
    assert meeting.series_id == card.id


def test_moving_a_meeting_follows_the_same_rule(session, world, template):
    mine = linked(session, template, world.ada, calendar_event(session, world.ada, RID, MONDAY))
    theirs = linked(
        session, template, world.pat, calendar_event(session, world.pat, "rid-pat", MONDAY)
    )
    meeting_teams.move(session, world.ada, mine, world.x.id)
    meeting_teams.move(session, world.pat, theirs, world.x.id)
    session.commit()
    assert mine.series_id is not None and row_of(session, world.x.id) is not None
    assert theirs.series_id is None and row_of(session, world.x.id, "rid-pat") is None


def test_unsharing_goes_back_to_the_recorders_personal_card_whoever_they_are(
    session, world, template
):
    card = standup(session, world.x.id, owner_id=world.ada.id)
    meeting = linked(
        session,
        template,
        world.pat,
        calendar_event(session, world.pat, RID, MONDAY),
        shared_team_id=world.x.id,
    )
    assert meeting.series_id == card.id
    meeting_teams.unshare(session, meeting)
    session.commit()
    assert session.get(Series, meeting.series_id).team_id == personal_of(session, world.pat)


def test_the_owner_creating_the_card_brings_in_the_meetings_already_shared_to_the_team(
    session, world, template
):
    """Pat's earlier shared meetings of the event were left ungrouped (no card then). When Ada makes
    the card by sharing hers, those of the same recurring event that are shared to X join it. A
    meeting shared to Y, a private one and one of another event do not."""
    earlier = linked(
        session,
        template,
        world.pat,
        calendar_event(session, world.pat, RID, MONDAY - timedelta(days=7)),
        shared_team_id=world.x.id,
    )
    elsewhere = linked(
        session,
        template,
        world.pat,
        calendar_event(session, world.pat, RID, MONDAY - timedelta(days=14)),
        shared_team_id=world.y.id,
    )
    other_event = linked(
        session,
        template,
        world.pat,
        calendar_event(session, world.pat, "rid-other", MONDAY),
        shared_team_id=world.x.id,
    )
    assert earlier.series_id is None and other_event.series_id is None
    mine = linked(session, template, world.ada, calendar_event(session, world.ada, RID, MONDAY))
    meeting_teams.share(session, world.ada, mine, world.x.id)
    session.commit()
    card = row_of(session, world.x.id)
    assert mine.series_id == card.id and earlier.series_id == card.id
    assert other_event.series_id is None
    assert elsewhere.series_id is None  # Y has no card and Pat is a plain member of Y


def test_the_owners_auto_shared_recording_making_the_card_adopts_the_shared_ones_too(
    session, world, template
):
    earlier = linked(
        session,
        template,
        world.pat,
        calendar_event(session, world.pat, RID, MONDAY - timedelta(days=7)),
        shared_team_id=world.x.id,
    )
    assert earlier.series_id is None
    mine = linked(
        session,
        template,
        world.ada,
        calendar_event(session, world.ada, RID, MONDAY),
        shared_team_id=world.x.id,  # auto-shared: the link makes Ada's card
    )
    card = row_of(session, world.x.id)
    assert mine.series_id == card.id and earlier.series_id == card.id


# ---- the Personal calendar card is never re-keyed into a team ----


def personal_cards(session, user) -> list[Series]:
    return list(session.scalars(select(Series).where(Series.team_id == personal_of(session, user))))


def team_cards(session, team_id) -> list[Series]:
    return list(session.scalars(select(Series).where(Series.team_id == team_id)))


def test_a_one_team_owner_keeps_her_personal_card_and_gets_a_separate_team_card(
    session, world, template
):
    """Ada owns X, her only team (X is her default team). The sync makes her Personal card; her
    recording is auto-shared to X. The Personal card stays (with the lines typed into it), and X
    gets
    its own card: exactly two cards, no stranded duplicate and no re-key."""
    scheduled(session, world.ada, "rid-ada")
    series_rules.sync_from_joins(session, MONDAY)
    [mine] = personal_cards(session, world.ada)
    session.add(SeriesAgendaItem(series_id=mine.id, text="Typed in Personal", position=1))
    session.commit()
    event = calendar_event(session, world.ada, "rid-ada", TUESDAY)
    meeting = calendar_meeting(session, template, world.ada, event, shared_team_id=world.x.id)
    series_rules.link_from_calendar(session, meeting)
    session.commit()
    [still_mine] = personal_cards(session, world.ada)
    [team_card] = team_cards(session, world.x.id)
    assert still_mine.id == mine.id and meeting.series_id == team_card.id != mine.id
    assert [i.text for i in series_rules.agenda_items(session, still_mine)] == ["Typed in Personal"]
    assert series_rules.agenda_items(session, team_card) == []


def test_a_two_team_owner_keeps_her_personal_card_the_same_way(session, world, template):
    """Quinn owns Y and is an admin of X: two teams, so his default team is his Personal one."""
    session.add(TeamMember(team_id=world.x.id, user_id=world.quinn.id, role="admin"))
    session.commit()
    scheduled(session, world.quinn, "rid-q")
    series_rules.sync_from_joins(session, MONDAY)
    [mine] = personal_cards(session, world.quinn)
    event = calendar_event(session, world.quinn, "rid-q", TUESDAY)
    meeting = calendar_meeting(session, template, world.quinn, event, shared_team_id=world.x.id)
    series_rules.link_from_calendar(session, meeting)
    session.commit()
    assert [c.id for c in personal_cards(session, world.quinn)] == [mine.id]
    [team_card] = team_cards(session, world.x.id)
    assert meeting.series_id == team_card.id


def test_a_plain_members_auto_shared_recording_makes_no_team_card_and_keeps_his_personal_one(
    session, world, template
):
    """Pat is a plain member of X and Y (his default team is his Personal one). After the sync made
    his Personal card, his recording is auto-shared to X: no team card is made (a plain member makes
    none, and nothing is re-keyed into the team), and his Personal card stays."""
    scheduled(session, world.pat, "rid-pat")
    series_rules.sync_from_joins(session, MONDAY)
    [mine] = personal_cards(session, world.pat)
    event = calendar_event(session, world.pat, "rid-pat", TUESDAY)
    meeting = calendar_meeting(session, template, world.pat, event, shared_team_id=world.x.id)
    assert series_rules.link_from_calendar(session, meeting) is None
    session.commit()
    assert team_cards(session, world.x.id) == [] and meeting.series_id is None
    assert [c.id for c in personal_cards(session, world.pat)] == [mine.id]


def test_a_one_team_plain_members_card_stays_in_personal_too(session, world, template):
    """Mo is a plain member of X and of nothing else: X is his default team."""
    mo = make_user(session, "mo@example.test")
    session.add(TeamMember(team_id=world.x.id, user_id=mo.id, role="member"))
    session.commit()
    scheduled(session, mo, "rid-mo")
    series_rules.sync_from_joins(session, MONDAY)
    [mine] = personal_cards(session, mo)
    meeting = calendar_meeting(
        session,
        template,
        mo,
        calendar_event(session, mo, "rid-mo", TUESDAY),
        shared_team_id=world.x.id,
    )
    assert series_rules.link_from_calendar(session, meeting) is None
    assert team_cards(session, world.x.id) == [] and personal_cards(session, mo)[0].id == mine.id


# ---- the system makes no team card; strays; the adoption lock ----


def test_leaving_the_actor_out_is_an_error_not_the_power_to_make_a_team_card(
    session, world, template
):
    meeting = linked(session, template, world.pat, calendar_event(session, world.pat, RID, MONDAY))
    meeting.shared_team_id = world.x.id
    with pytest.raises(TypeError):
        series_rules.place_meeting(session, meeting)  # no actor named


def test_the_system_attaches_to_a_team_card_that_exists_but_makes_none(session, world, template):
    meeting = linked(session, template, world.pat, calendar_event(session, world.pat, RID, MONDAY))
    meeting.shared_team_id = world.x.id
    placed = series_rules.place_meeting(session, meeting, actor=series_rules.SYSTEM)
    assert placed.series is None and placed.no_card and row_of(session, world.x.id) is None
    card = standup(session, world.x.id, owner_id=world.ada.id)
    meeting.series_id = row_of(session, personal_of(session, world.pat)).id  # back in Personal
    placed = series_rules.place_meeting(session, meeting, actor=series_rules.SYSTEM)
    assert placed.series.id == card.id and not placed.made


def test_removing_a_row_makes_no_card_for_its_strays_in_a_team_the_remover_does_not_manage(
    app, session, world, template
):
    """A legacy X row still holds Pat's meeting shared to Y. Ada (owner of X, in no Y) removes it:
    the stray goes to Y's card if there is one and otherwise is left ungrouped. No card is made."""
    legacy = standup(session, world.x.id, rid="rid-legacy", owner_id=world.ada.id)
    held = put(session, template, legacy, who=world.ada, team=world.x.id)
    stray = put(session, template, legacy, who=world.pat, team=world.y.id, when=TUESDAY)
    legacy_id = legacy.id
    assert sign_in(app, OWNER_EMAIL).delete(f"/api/series/{legacy_id}").status_code == 200
    session.expire_all()
    assert session.get(Meeting, stray.id).series_id is None
    assert session.get(Meeting, held.id).series_id is None
    assert team_cards(session, world.y.id) == []


def test_the_adoption_locks_the_meetings_it_takes_in(session, world, template):
    """`_adopt_shared` writes other people's meetings: it selects them FOR UPDATE."""
    from sqlalchemy import event as sa_event

    linked(
        session,
        template,
        world.pat,
        calendar_event(session, world.pat, RID, MONDAY - timedelta(days=7)),
        shared_team_id=world.x.id,
    )
    mine = linked(session, template, world.ada, calendar_event(session, world.ada, RID, MONDAY))
    seen: list[str] = []
    engine = session.get_bind()

    def spy(conn, cursor, statement, *args):
        seen.append(statement)

    sa_event.listen(engine, "before_cursor_execute", spy)
    try:
        meeting_teams.share(session, world.ada, mine, world.x.id)
        session.commit()
    finally:
        sa_event.remove(engine, "before_cursor_execute", spy)
    adoption = [q for q in seen if "FROM meetings" in q and "scheduled_joins" in q]
    assert adoption and all("FOR UPDATE" in q for q in adoption)


# ---- 3. an explicit owner/admin creation clears the mark; automatic paths respect it ----


def test_an_owner_sharing_again_after_removing_the_card_makes_it_properly_and_clears_the_mark(
    session, world, template
):
    session.add(SeriesIgnored(team_id=world.x.id, calendar_recurring_id=RID))
    session.commit()
    meeting = linked(session, template, world.ada, calendar_event(session, world.ada, RID, MONDAY))
    meeting_teams.share(session, world.ada, meeting, world.x.id)
    session.commit()
    assert row_of(session, world.x.id) is not None and meeting.series_id is not None
    assert not marked(session, world.x.id)


def test_the_automatic_link_respects_the_mark_even_for_an_owner(session, world, template):
    session.add(SeriesIgnored(team_id=world.x.id, calendar_recurring_id=RID))
    session.commit()
    meeting = linked(
        session,
        template,
        world.ada,
        calendar_event(session, world.ada, RID, MONDAY),
        shared_team_id=world.x.id,
    )
    assert meeting.series_id is None and row_of(session, world.x.id) is None
    assert marked(session, world.x.id)


def test_a_plain_member_sharing_never_clears_the_mark(session, world, template):
    session.add(SeriesIgnored(team_id=world.x.id, calendar_recurring_id=RID))
    session.commit()
    meeting = linked(session, template, world.pat, calendar_event(session, world.pat, RID, MONDAY))
    meeting_teams.share(session, world.pat, meeting, world.x.id)
    session.commit()
    assert marked(session, world.x.id) and row_of(session, world.x.id) is None


# ---- hand routes: create, attach, make recurring ----


def team_headers(team_id):
    return {"X-Team-Id": str(team_id)}


def post_series(who, team_id, title="Weekly"):
    return who.client.post(
        "/api/series",
        json={"title": title},
        headers={**team_headers(team_id), "X-CSRF-Token": who.csrf},
    )


def test_a_plain_member_cannot_make_a_team_card_by_hand_and_gets_the_real_403(app, session, world):
    pat = sign_in(app, PAT)
    error = error_of(post_series(pat, world.x.id), 403, "team_forbidden")
    assert error["message"] == FORBIDDEN_TEXT
    assert session.scalar(select(func.count()).select_from(Series)) == 0


def test_an_owner_and_an_admin_make_a_team_card_by_hand(app, session, world, dee):
    assert post_series(sign_in(app, OWNER_EMAIL), world.x.id).status_code == 201
    assert post_series(sign_in(app, "dee@example.test"), world.x.id, "Other").status_code == 201


def test_a_person_makes_their_own_personal_card_by_hand(app, session, world):
    pat = sign_in(app, PAT)
    response = post_series(pat, personal_of(session, world.pat))
    assert response.status_code == 201 and response.json()["team"]["is_personal"] is True


def test_adding_a_meeting_to_a_team_card_by_hand_is_owner_or_admin_only(
    app, session, world, template, dee
):
    card = standup(session, world.x.id, rid=None, owner_id=world.ada.id, title="Founders")
    put(session, template, card, who=world.ada, team=world.x.id)  # so that Pat may see the card
    pats = ready(session, template, owner_user_id=world.pat.id, shared_team_id=world.x.id)
    hers = ready(session, template, shared_team_id=world.x.id)
    pat = sign_in(app, PAT)
    put_url = lambda meeting: f"/api/meetings/{meeting.id}/series"  # noqa: E731
    error_of(pat.put(put_url(pats), {"series_id": card.id}), 403, "team_forbidden")
    assert session.get(Meeting, pats.id).series_id is None
    assert sign_in(app, OWNER_EMAIL).put(put_url(hers), {"series_id": card.id}).status_code == 200
    # taking a meeting out is not creating a grouping: the recorder may
    assert pat.put(put_url(pats), {"series_id": None}).status_code == 200


def test_a_person_adds_their_private_meeting_to_their_personal_card(app, session, world, template):
    mine = personal_of(session, world.pat)
    card = standup(session, mine, rid=None, owner_id=world.pat.id, title="Mine")
    private = ready(session, template, owner_user_id=world.pat.id)
    pat = sign_in(app, PAT)
    assert pat.put(f"/api/meetings/{private.id}/series", {"series_id": card.id}).status_code == 200


def reused(session, template, **fields):
    """A meeting whose link was used before (the 'Make this a recurring meeting?' case)."""
    first = ready(session, template, **fields)
    again = ready(session, template, **fields)
    return first, again


def test_make_recurring_from_a_reused_link_by_a_plain_member_is_403_for_a_shared_meeting(
    app, session, world, template
):
    _, shared = reused(session, template, owner_user_id=world.pat.id, shared_team_id=world.x.id)
    pat = sign_in(app, PAT)
    error_of(pat.post(f"/api/meetings/{shared.id}/recurring"), 403, "team_forbidden")
    assert session.scalar(select(func.count()).select_from(Series)) == 0


def test_make_recurring_from_a_reused_link_works_for_a_private_meeting_and_for_an_owner(
    app, session, world, template
):
    _, private = reused(session, template, owner_user_id=world.pat.id)
    assert sign_in(app, PAT).post(f"/api/meetings/{private.id}/recurring").status_code == 201
    _, shared = reused(session, template, shared_team_id=world.x.id)  # Ada's, shared to X
    assert sign_in(app, OWNER_EMAIL).post(f"/api/meetings/{shared.id}/recurring").status_code == 201


def test_the_meeting_page_says_who_may_group_it(app, session, world, template):
    _, pats = reused(session, template, owner_user_id=world.pat.id, shared_team_id=world.x.id)
    _, pats_private = reused(session, template, owner_user_id=world.pat.id)
    _, hers = reused(session, template, shared_team_id=world.x.id)
    pat, ada = sign_in(app, PAT), sign_in(app, OWNER_EMAIL)
    page = lambda who, m: who.get(f"/api/meetings/{m.id}").json()  # noqa: E731
    shared_page = page(pat, pats)
    assert (shared_page["can_make_recurring"], shared_page["suggest_recurring"]) == (False, False)
    private_page = page(pat, pats_private)
    assert (private_page["can_make_recurring"], private_page["suggest_recurring"]) == (True, True)
    owner_page = page(ada, hers)
    assert (owner_page["can_make_recurring"], owner_page["suggest_recurring"]) == (True, True)
    # Ada reads Pat's shared meeting but it is not hers to group
    assert page(ada, pats)["can_make_recurring"] is False


def test_flag_off_a_plain_member_may_still_make_a_card_as_before(app, session, world, monkeypatch):
    monkeypatch.setattr(series_rules, "follow_enabled", lambda: False)
    assert post_series(sign_in(app, PAT), world.x.id).status_code == 201


# ---- 4. a plain creator's removal of an empty legacy row writes no team-wide mark ----


def test_a_plain_creator_removing_an_empty_legacy_row_writes_no_mark_but_an_owner_does(
    app, session, world
):
    legacy = standup(session, world.x.id, rid="rid-legacy", owner_id=world.pat.id)
    other = standup(session, world.x.id, rid="rid-legacy-2", owner_id=world.pat.id)
    legacy_id, other_id = legacy.id, other.id
    assert sign_in(app, PAT).delete(f"/api/series/{legacy_id}").status_code == 200
    assert not marked(session, world.x.id, "rid-legacy")
    ada_removes = series_rules.remove(session, world.ada, session.get(Series, other_id))
    assert ada_removes.remembered is True
    assert marked(session, world.x.id, "rid-legacy-2")


# ---- 6. the reminders for a calendar-linked Personal card ----


def test_a_calendar_linked_personal_card_with_one_recorded_meeting_queues_both_reminders(
    session, world, template
):
    mine = personal_of(session, world.ada)
    session.get(Team, mine).reminders_on = True
    session.get(Team, mine).reminder_timing = "both"
    session.commit()
    first = calendar_event(session, world.ada, "rid-r", MONDAY)
    recorded = linked(session, template, world.ada, first)  # private: lands in the Personal card
    assert session.get(Series, recorded.series_id).team_id == mine
    nxt = calendar_event(session, world.ada, "rid-r", TUESDAY)  # the next occurrence
    one_hour = nxt.starts_at - timedelta(minutes=55)
    assert series_mail.queue_due_reminders(session, one_hour) == 1
    assert series_mail.queue_due_reminders(session, one_hour) == 0  # once
    half = nxt.starts_at - timedelta(minutes=20)
    assert series_mail.queue_due_reminders(session, half) == 1
    kinds = sorted(session.scalars(select(SeriesReminder.kind)))
    assert kinds == ["1h", "30m"]
    rows = session.scalars(select(MailOutbox).where(MailOutbox.template == "series_reminder")).all()
    assert [r.to_email for r in rows] == ["owner@example.test"] * 2


def test_a_personal_card_with_no_recorded_meeting_sends_nothing(session, world):
    """The placeholder from the sync alone has nothing to say: no past meeting, no agenda line."""
    mine = personal_of(session, world.ada)
    session.get(Team, mine).reminders_on = True
    session.commit()
    scheduled(session, world.ada, "rid-r", MONDAY)
    series_rules.sync_from_joins(session, MONDAY)
    nxt = calendar_event(session, world.ada, "rid-r", TUESDAY)
    assert series_mail.queue_due_reminders(session, nxt.starts_at - timedelta(minutes=55)) == 0


def test_a_card_made_by_hand_has_no_next_occurrence_so_it_never_reminds(session, world, template):
    mine = personal_of(session, world.ada)
    session.get(Team, mine).reminders_on = True
    session.commit()
    hand = standup(session, mine, rid=None, owner_id=world.ada.id, title="By hand")
    put(session, template, hand, who=world.ada)
    session.add(SeriesAgendaItem(series_id=hand.id, text="x", position=1))
    session.commit()
    assert series_mail.queue_due_reminders(session, TUESDAY) == 0
    assert session.scalar(select(func.count()).select_from(SeriesReminder)) == 0


def test_the_personal_team_reminders_are_off_until_switched_on(session, world, template):
    mine = personal_of(session, world.ada)
    assert session.get(Team, mine).reminders_on is False  # the default
    linked(session, template, world.ada, calendar_event(session, world.ada, "rid-r", MONDAY))
    nxt = calendar_event(session, world.ada, "rid-r", TUESDAY)
    assert series_mail.queue_due_reminders(session, nxt.starts_at - timedelta(minutes=55)) == 0


# ---- 5. the cleanup of empty calendar-derived team cards ----


def cards_in_team(session, team_id) -> int:
    return session.scalar(select(func.count()).select_from(Series).where(Series.team_id == team_id))


def test_the_cleanup_removes_only_empty_calendar_cards_of_ordinary_teams_and_writes_no_marks(
    session, world, template
):
    mine = personal_of(session, world.ada)
    empty_calendar = standup(session, world.x.id, rid="rid-1", owner_id=world.ada.id)
    standup(session, world.x.id, rid="rid-2", owner_id=world.ada.id)  # with a meeting
    put(
        session,
        template,
        session.scalar(select(Series).where(Series.calendar_recurring_id == "rid-2")),
        who=world.ada,
        team=world.x.id,
    )
    lines = standup(session, world.x.id, rid="rid-3", owner_id=world.ada.id)
    session.add(SeriesAgendaItem(series_id=lines.id, text="Budget", position=1))
    project = Project(team_id=world.x.id, name="Launch", color="#0E6B5E")
    session.add(project)
    session.flush()
    standup(session, world.x.id, rid="rid-4", owner_id=world.ada.id, project_id=project.id)
    standup(session, world.x.id, rid=None, owner_id=world.ada.id, title="By hand, empty")
    standup(session, mine, rid="rid-5", owner_id=world.ada.id)  # a Personal placeholder
    session.commit()
    empty_id = empty_calendar.id
    dry = remove_empty_team_cards(session, apply=False)
    assert dry.applied is False and dry.cards_removed == 1
    assert session.get(Series, empty_id) is not None  # a dry run changes nothing
    done = remove_empty_team_cards(session, apply=True)
    assert (done.cards_looked_at, done.cards_removed) == (done.cards_looked_at, 1)
    assert session.get(Series, empty_id) is None
    assert cards_in_team(session, world.x.id) == 4  # meeting, line, project, hand-made stay
    assert cards_in_team(session, mine) == 1
    assert session.scalar(select(func.count()).select_from(SeriesIgnored)) == 0
    again = remove_empty_team_cards(session, apply=True)
    assert again.cards_removed == 0  # safe to run twice


def test_the_cleanup_report_is_counts_only_and_says_why_a_card_stayed(session, world, template):
    standup(session, world.x.id, rid="rid-1", owner_id=world.ada.id)
    held = standup(session, world.x.id, rid="rid-2", owner_id=world.ada.id)
    put(session, template, held, who=world.ada, team=world.x.id)
    report = remove_empty_team_cards(session, apply=False)
    text = "\n".join(report.lines())
    assert text.splitlines()[0].startswith("DRY RUN: nothing was changed")
    assert "Monday" not in text and "rid-" not in text and "@" not in text
    assert (report.cards_removed, report.cards_kept) == (1, 1)


def test_after_the_cleanup_the_next_sync_makes_the_personal_placeholder(session, world):
    scheduled(session, world.ada, "rid-1")
    standup(session, world.x.id, rid="rid-1", owner_id=world.ada.id)  # the wrong, old card
    remove_empty_team_cards(session, apply=True)
    assert series_rules.sync_from_joins(session, MONDAY) == 1
    assert row_of(session, personal_of(session, world.ada), "rid-1") is not None
    assert row_of(session, world.x.id, "rid-1") is None
