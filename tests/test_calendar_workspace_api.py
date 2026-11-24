"""The calendar routes per workspace: Upcoming, the switch that moves a recording, settings,
Recurring cards and "not recorded" each follow `X-Team-Id` (workspaces design, sections 3 and 4).

Real Postgres and the real routes; connections and events are rows as the sync leaves them (the
connect flow itself is `tests/test_calendar_workspaces.py`). Nothing here ran against a real
Google or Microsoft calendar.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app import calendar_arbitration, calendar_prefs, gcal_sync, teams
from app.models import (
    CalendarConnection,
    CalendarEvent,
    Meeting,
    ScheduledJoin,
    Team,
    Template,
    UserSettings,
)
from tests.test_api import CSRF, error_of
from tests.test_calendar_connect import (  # noqa: F401  (fixtures used by name)
    CALENDAR_SCOPE,
    capp,
    google,
    settings,
    vault_key,
    web,
)
from tests.test_multi_team_leaks import new_team
from tests.test_web import fake_bot, fake_openai, make_settings, owner, stranger  # noqa: F401

CODE = "abc-defg-hij"
SETTINGS = make_settings(autojoin_lead_seconds=60)


def now() -> datetime:
    return datetime.now(UTC)


@pytest.fixture
def two(session, owner):
    """The person with a personal workspace (connected first) and Luran, one Google connection in
    each, and the template their calendar meetings use."""
    home = teams.ensure_personal_team(session, owner)
    session.commit()
    luran = new_team(session, owner, "Luran")
    new_team(
        session, owner, "Side"
    )  # two ordinary teams: the default workspace is the personal one
    template = Template(owner_user_id=owner.id, name="General", instructions="Notes.")
    session.add(template)
    session.flush()
    session.add(UserSettings(user_id=owner.id, calendar_template_id=template.id))
    conns = {}
    for team in (home, luran):
        conn = CalendarConnection(
            user_id=owner.id,
            team_id=team.id,
            provider="google",
            status="connected",
            granted_scopes=CALENDAR_SCOPE,
            account_email="grace@example.test",
            account_subject="g-grace-1",
        )
        session.add(conn)
        session.flush()
        conns[team.id] = conn
    session.commit()
    return type("Two", (), {"home": home, "luran": luran, "conns": conns, "template": template})


def add_event(session, conn, *, code=CODE, hours=2.0, recurring=None, title="Standup"):
    start = now() + timedelta(hours=hours)
    event = CalendarEvent(
        user_id=conn.user_id,
        connection_id=conn.id,
        source_event_id=f"evt-{conn.id}-{code}",
        recurring_event_id=recurring,
        title=title,
        starts_at=start,
        ends_at=start + timedelta(hours=1),
        platform="google_meet",
        meeting_url=f"https://meet.google.com/{code}",
        native_id=code,
        organizer_self=True,
        self_response="accepted",
        status="confirmed",
    )
    session.add(event)
    session.commit()
    return event


def plan(session, conn, *events):
    """What the sync does after storing the events: make their joins by the rules as they are."""
    prefs = calendar_prefs.for_workspace(session, conn.user_id, conn.team_id)
    gcal_sync.rerun_rules(session, conn, prefs, list(events), now=now(), settings=SETTINGS)


def in_ws(web, team):
    return {"X-Team-Id": str(team.id)}


def get_calendar(web, team):
    response = web.client.get("/api/calendar", headers=in_ws(web, team))
    assert response.status_code == 200, response.text
    return response.json()


def override(web, team, event, value):
    return web.client.put(
        f"/api/calendar/events/{event.id}/override",
        json={"override": value},
        headers={CSRF: web.csrf, "X-Team-Id": str(team.id)},
    )


def joins(session):
    session.expire_all()
    return list(session.scalars(select(ScheduledJoin).order_by(ScheduledJoin.id)))


def held_in(session):
    return [(j.team_id, j.state) for j in joins(session) if j.state != "cancelled"]


# ---- Upcoming says where a meeting is recorded ----


def test_the_workspace_that_does_not_record_a_meeting_says_where_it_is_recorded(web, session, two):
    a = add_event(session, two.conns[two.home.id])
    b = add_event(session, two.conns[two.luran.id])
    plan(session, two.conns[two.home.id], a)
    plan(session, two.conns[two.luran.id], b)

    [here] = get_calendar(web, two.home)["upcoming"]
    [there] = get_calendar(web, two.luran)["upcoming"]

    assert (here["will_join"], here["recorded_in"], here["recorded_in_text"]) == (True, None, None)
    assert there["will_join"] is False
    assert there["recorded_in"] == {
        "team_id": two.home.id,
        "name": two.home.name,
        "is_personal": True,
    }
    mine = f"{two.home.name} (personal)"  # a personal workspace is named as the switcher names it
    assert there["recorded_in_text"] == f"Recorded in {mine}. Switch here instead."
    assert there["will_join_text"] == f"Recorded in {mine}"
    assert there["can_send_now"] is False


def test_a_person_with_one_calendar_sees_no_recorded_in(web, session, two):
    add_event(session, two.conns[two.home.id], code="aaa-bbbb-ccc")
    add_event(session, two.conns[two.luran.id], code="ddd-eeee-fff")

    for team in (two.home, two.luran):
        [item] = get_calendar(web, team)["upcoming"]
        assert (item["recorded_in"], item["recorded_in_text"], item["moved_text"]) == (
            None,
            None,
            None,
        )


def test_the_workspace_that_skips_a_meeting_is_not_said_to_record_it(web, session, two):
    row = calendar_prefs.for_update(session, web_user(session).id, two.home.id)
    row.auto_join_rule = "none"
    session.commit()
    add_event(session, two.conns[two.home.id])
    add_event(session, two.conns[two.luran.id])

    [here] = get_calendar(web, two.home)["upcoming"]
    [there] = get_calendar(web, two.luran)["upcoming"]

    assert here["recorded_in"] == {
        "team_id": two.luran.id,
        "name": two.luran.name,
        "is_personal": False,
    }
    assert there["recorded_in"] is None and there["will_join"] is True


def web_user(session):
    from app.models import User

    return session.scalars(select(User)).first()


# ---- the switch moves the recording ----


def test_switching_a_meeting_on_in_the_other_workspace_moves_it_with_a_note(web, session, two):
    a = add_event(session, two.conns[two.home.id])
    b = add_event(session, two.conns[two.luran.id])
    plan(session, two.conns[two.home.id], a)
    plan(session, two.conns[two.luran.id], b)
    assert held_in(session) == [(two.home.id, "scheduled")]

    response = override(web, two.luran, b, True)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["will_join"] is True and body["recorded_in"] is None
    mine = f"{two.home.name} (personal)"
    assert body["moved_text"] == f"Moved here from {mine}. {mine} will not record this meeting."
    assert held_in(session) == [(two.luran.id, "scheduled")]
    [here] = get_calendar(web, two.home)["upcoming"]
    assert here["recorded_in"]["name"] == two.luran.name
    assert here["moved_text"] is None  # the note is said once, to the one who switched


def test_switching_on_a_meeting_nobody_else_records_says_nothing_moved(web, session, two):
    b = add_event(session, two.conns[two.luran.id], code="solo-code-xyz")

    body = override(web, two.luran, b, True).json()

    assert body["moved_text"] is None and body["will_join"] is True


def test_switching_it_off_in_the_recording_workspace_hands_it_to_the_other(web, session, two):
    a = add_event(session, two.conns[two.home.id])
    b = add_event(session, two.conns[two.luran.id])
    plan(session, two.conns[two.home.id], a)
    plan(session, two.conns[two.luran.id], b)

    assert override(web, two.home, a, False).status_code == 200

    assert held_in(session) == [(two.luran.id, "scheduled")]


def test_a_running_note_taker_is_never_moved_and_nothing_changes(web, session, two):
    a = add_event(session, two.conns[two.home.id])
    b = add_event(session, two.conns[two.luran.id])
    plan(session, two.conns[two.home.id], a)
    plan(session, two.conns[two.luran.id], b)
    held = joins(session)[0]
    meeting = Meeting(
        owner_user_id=held.user_id,
        url=held.meeting_url,
        platform=held.platform,
        native_id=held.native_id,
        template_id=two.template.id,
        status="bot_sent",
        workspace_team_id=two.home.id,
    )
    session.add(meeting)
    session.flush()
    held.state, held.meeting_id = "joined", meeting.id
    session.commit()

    refused = override(web, two.luran, b, True)

    error = error_of(refused, 409, "already_recording")
    assert error["message"] == (
        f"{two.home.name} (personal) is already recording this meeting. "
        "A note taker that is already in the meeting is never moved."
    )
    session.expire_all()
    assert session.get(CalendarEvent, b.id).override is None
    assert held_in(session) == [(two.home.id, "joined")]


def test_the_switch_works_only_on_this_workspaces_own_events(web, session, two):
    other = add_event(session, two.conns[two.luran.id], code="zzz-yyyy-xxx")

    error_of(override(web, two.home, other, True), 404, "not_found")


# ---- settings, recurring and not recorded follow the workspace ----


def test_the_rule_is_set_per_workspace(web, session, two):
    response = web.client.put(
        "/api/calendar/settings",
        json={"rule": "none"},
        headers={CSRF: web.csrf, "X-Team-Id": str(two.luran.id)},
    )

    assert response.status_code == 200, response.text
    assert response.json()["rule"] == "none"
    assert get_calendar(web, two.luran)["rule"] == "none"
    assert get_calendar(web, two.home)["rule"] == "all"  # the other workspace is untouched


def test_changing_the_rule_in_one_workspace_never_touches_the_others_events_or_joins(
    web, session, two
):
    a = add_event(session, two.conns[two.home.id], code="aaa-bbbb-ccc")
    b = add_event(session, two.conns[two.luran.id], code="ddd-eeee-fff")
    plan(session, two.conns[two.home.id], a)
    plan(session, two.conns[two.luran.id], b)
    a.override = True
    session.commit()

    web.client.put(
        "/api/calendar/settings",
        json={"rule": "none"},
        headers={CSRF: web.csrf, "X-Team-Id": str(two.luran.id)},
    )

    session.expire_all()
    assert session.get(CalendarEvent, a.id).override is True  # luran's change cleared only luran's
    assert held_in(session) == [
        (two.home.id, "scheduled")
    ]  # luran's join was cancelled by its rule


def test_the_recurring_cards_are_the_workspaces_own(web, session, two):
    add_event(session, two.conns[two.home.id], code="aaa-bbbb-ccc", recurring="rid-1", title="Mon")
    add_event(session, two.conns[two.home.id], code="aaa-bbbb-ddd", recurring="rid-1", title="Mon")
    add_event(
        session, two.conns[two.luran.id], code="eee-ffff-ggg", recurring="rid-2", title="Luran sync"
    )
    add_event(
        session, two.conns[two.luran.id], code="eee-ffff-hhh", recurring="rid-2", title="Luran sync"
    )

    home = web.client.get("/api/calendar/recurring", headers=in_ws(web, two.home)).json()
    luran = web.client.get("/api/calendar/recurring", headers=in_ws(web, two.luran)).json()

    assert [c["name"] for c in home["series"]] == ["Mon"]
    assert [c["name"] for c in luran["series"]] == ["Luran sync"]


def test_a_removed_recurring_meeting_leaves_the_other_workspaces_card(web, session, two):
    from app.models import SeriesIgnored

    add_event(session, two.conns[two.home.id], code="aaa-bbbb-ccc", recurring="rid-1", title="Mon")
    add_event(session, two.conns[two.luran.id], code="aaa-bbbb-ccc", recurring="rid-1", title="Mon")
    session.add(SeriesIgnored(team_id=two.home.id, calendar_recurring_id="rid-1"))
    session.commit()

    home = web.client.get("/api/calendar/recurring", headers=in_ws(web, two.home)).json()
    luran = web.client.get("/api/calendar/recurring", headers=in_ws(web, two.luran)).json()

    assert home["series"] == []  # removed from this workspace
    assert [c["name"] for c in luran["series"]] == ["Mon"]  # still there in the other


def test_not_recorded_lists_only_the_active_workspaces_joins(web, session, two):
    a = add_event(session, two.conns[two.home.id], code="aaa-bbbb-ccc", hours=-0.5)
    b = add_event(session, two.conns[two.luran.id], code="ddd-eeee-fff", hours=-0.5)
    for event, conn in ((a, two.conns[two.home.id]), (b, two.conns[two.luran.id])):
        session.add(
            ScheduledJoin(
                user_id=conn.user_id,
                team_id=conn.team_id,
                calendar_event_id=event.id,
                platform="google_meet",
                native_id=event.native_id,
                meeting_url=event.meeting_url,
                starts_at=event.starts_at,
                ends_at=event.ends_at,
                run_at=event.starts_at - timedelta(minutes=1),
                deadline_at=event.starts_at + timedelta(minutes=15),
                state="failed",
            )
        )
    session.commit()

    home = web.client.get("/api/not-recorded", headers=in_ws(web, two.home)).json()["items"]
    luran = web.client.get("/api/not-recorded", headers=in_ws(web, two.luran)).json()["items"]

    assert [i["title"] for i in home] == ["Standup"] and [i["title"] for i in luran] == ["Standup"]
    assert {i["join_id"] for i in home}.isdisjoint({i["join_id"] for i in luran})


def test_the_arbitration_module_names_its_reason_for_a_cancelled_join():
    assert calendar_arbitration.RECORDED_ELSEWHERE == "recorded_elsewhere"
    assert Team.__tablename__ == "teams"
