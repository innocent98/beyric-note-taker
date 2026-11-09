"""`PUT /api/calendar/settings` and `PUT /api/calendar/events/{id}/override` (plan task C15a, the
settings and override slice).

Real Postgres and the real connect flow over `FakeGoogle`. Events are rows, as the sync job leaves
them; where a test is about what the scheduler will read, it checks the `scheduled_joins` rows,
which is what C12 will act on. Nothing here sends a bot.
"""

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.models import CalendarEvent, ScheduledJoin, Template, UserSettings
from tests import test_calendar_connect as cc
from tests import test_web as tw
from tests.test_api import error_of
from tests.test_calendar_api import add_event
from tests.test_calendar_connect import (
    browser,
    build_app,
    connect_google,
    connection_of,
)
from tests.test_web import OTHER_EMAIL, make_settings

# The fixtures of the connect tests and the web tests, shared by name.
capp, google, settings, vault_key, web = cc.capp, cc.google, cc.settings, cc.vault_key, cc.web
fake_openai, fake_vexa, owner, stranger = tw.fake_openai, tw.fake_vexa, tw.owner, tw.stranger

URL = "/api/calendar"
SETTINGS_URL = "/api/calendar/settings"


def override_url(event_id: int) -> str:
    return f"/api/calendar/events/{event_id}/override"


@pytest.fixture
def linked(web, session, owner, google):
    connect_google(web, google)
    return connection_of(session, owner)


def make_template(session, user, name="General") -> Template:
    template = Template(owner_user_id=user.id, name=name, instructions="Write notes.")
    session.add(template)
    session.commit()
    return template


def add_join(session, event: CalendarEvent, state="scheduled", **fields) -> ScheduledJoin:
    """The scheduled-join row the materialiser leaves for an event that will be joined."""
    join = ScheduledJoin(
        user_id=event.user_id,
        source="calendar",
        calendar_event_id=event.id,
        platform=event.platform,
        native_id=event.native_id,
        meeting_url=event.meeting_url,
        starts_at=event.starts_at,
        ends_at=event.ends_at,
        run_at=event.starts_at - timedelta(minutes=1),
        deadline_at=event.starts_at + timedelta(minutes=15),
        state=state,
        **fields,
    )
    session.add(join)
    session.commit()
    return join


def stored_rule(session, user) -> str | None:
    session.expire_all()
    row = session.get(UserSettings, user.id)
    return row.auto_join_rule if row else None


def stored_override(session, event) -> bool | None:
    session.expire_all()
    return session.get(CalendarEvent, event.id).override


def join_of(session, event) -> ScheduledJoin | None:
    session.expire_all()
    return session.scalars(
        select(ScheduledJoin).where(ScheduledJoin.calendar_event_id == event.id)
    ).first()


def upcoming_of(web, event) -> dict:
    return next(e for e in web.get(URL).json()["upcoming"] if e["id"] == event.id)


# ---- the settings route: who may use it ----


def test_settings_needs_a_login(capp, owner):
    anon = browser(capp, log_in=False)
    error_of(anon.put(SETTINGS_URL, {"rule": "none"}), 401, "not_authenticated")


def test_settings_is_dark_while_the_calendar_flag_is_off(
    engine, google, fake_vexa, fake_openai, owner
):
    off = make_settings(
        google_client_id="id.apps.example.test",
        google_client_secret="s3cret",
        calendar_enabled=False,
    )
    web = browser(build_app(engine, off, google, fake_vexa, fake_openai))
    error_of(web.put(SETTINGS_URL, {"rule": "none"}), 404, "calendar_unavailable")


def test_settings_needs_the_csrf_header(web):
    error_of(web.put(SETTINGS_URL, {"rule": "none"}, csrf=False), 403, "csrf_rejected")


# ---- the rule ----


def test_get_returns_no_template_until_one_is_chosen(web, linked):
    assert web.get(URL).json()["calendar_template_id"] is None


def test_putting_a_rule_stores_it_and_answers_with_the_whole_state(web, session, owner, linked):
    response = web.put(SETTINGS_URL, {"rule": "organizer"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["rule"], body["rule_text"]) == ("organizer", "Joining meetings you organise")
    assert body["connected"] is True and body["account_email"] == "grace@example.test"
    assert stored_rule(session, owner) == "organizer"
    assert web.get(URL).json()["rule"] == "organizer"


@pytest.mark.parametrize("rule", ["all", "organizer", "none"])
def test_each_of_the_three_rules_can_be_chosen(web, session, owner, linked, rule):
    assert web.put(SETTINGS_URL, {"rule": rule}).status_code == 200
    assert stored_rule(session, owner) == rule


@pytest.mark.parametrize(
    "body",
    [
        {"rule": "sometimes"},
        {"rule": None},
        {"rule": 1},
        {},
        {"rule": "all", "join_instant_meets": True},
        {"rule": "all", "auto_join_rule": "none"},
        {"calendar_template_id": "7"},
        {"calendar_template_id": True},
    ],
)
def test_a_bad_settings_body_is_422_and_stores_nothing(web, session, owner, linked, body):
    error_of(web.put(SETTINGS_URL, body), 422, "validation_error")
    assert stored_rule(session, owner) in (None, "all")
    session.expire_all()
    row = session.get(UserSettings, owner.id)
    assert row is None or row.calendar_template_id is None


def test_a_user_without_a_settings_row_gets_one(web, session, owner, linked):
    session.expire_all()
    assert session.get(UserSettings, owner.id) is None
    assert web.put(SETTINGS_URL, {"rule": "none"}).status_code == 200
    assert stored_rule(session, owner) == "none"


def test_the_rule_can_be_set_before_a_calendar_is_connected(web, session, owner):
    response = web.put(SETTINGS_URL, {"rule": "none"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["connected"] is False and body["rule"] == "none" and body["upcoming"] == []
    assert stored_rule(session, owner) == "none"


# ---- the template for calendar meetings ----


def test_putting_a_template_stores_it_and_get_returns_it(web, session, owner, linked):
    template = make_template(session, owner, "Client call")
    response = web.put(SETTINGS_URL, {"calendar_template_id": template.id})
    assert response.status_code == 200, response.text
    assert response.json()["calendar_template_id"] == template.id
    assert web.get(URL).json()["calendar_template_id"] == template.id


def test_null_puts_the_template_back_to_the_default(web, session, owner, linked):
    template = make_template(session, owner)
    web.put(SETTINGS_URL, {"calendar_template_id": template.id})
    response = web.put(SETTINGS_URL, {"calendar_template_id": None})
    assert response.status_code == 200, response.text
    assert response.json()["calendar_template_id"] is None
    session.expire_all()
    assert session.get(UserSettings, owner.id).calendar_template_id is None


def test_another_users_template_is_refused_and_nothing_is_stored(
    web, session, owner, stranger, linked
):
    theirs = make_template(session, stranger, "Their secret template")
    mine = make_template(session, owner)
    web.put(SETTINGS_URL, {"calendar_template_id": mine.id})
    response = web.put(SETTINGS_URL, {"calendar_template_id": theirs.id})
    error_of(response, 404, "not_found")
    assert "secret" not in response.text
    assert web.get(URL).json()["calendar_template_id"] == mine.id


def test_a_deleted_template_is_refused_like_one_that_never_existed(web, session, owner, linked):
    kept, gone = make_template(session, owner), make_template(session, owner, "Interview")
    assert web.delete(f"/api/templates/{gone.id}").status_code == 204
    error_of(web.put(SETTINGS_URL, {"calendar_template_id": gone.id}), 404, "not_found")
    assert web.put(SETTINGS_URL, {"calendar_template_id": kept.id}).status_code == 200


def test_deleting_the_calendar_template_moves_the_setting_to_the_oldest_left(
    web, session, owner, linked
):
    oldest, chosen = make_template(session, owner), make_template(session, owner, "Interview")
    web.put(SETTINGS_URL, {"calendar_template_id": chosen.id})
    assert web.delete(f"/api/templates/{chosen.id}").status_code == 204
    assert web.get(URL).json()["calendar_template_id"] == oldest.id


@pytest.mark.parametrize("template_id", [0, -1, 999_999, 2**62])
def test_a_template_that_does_not_exist_is_refused(web, linked, template_id):
    error_of(web.put(SETTINGS_URL, {"calendar_template_id": template_id}), 404, "not_found")


def test_a_refused_template_does_not_let_the_rule_in_the_same_body_through(
    web, session, owner, stranger, linked
):
    theirs = make_template(session, stranger)
    body = {"rule": "none", "calendar_template_id": theirs.id}
    error_of(web.put(SETTINGS_URL, body), 404, "not_found")
    assert stored_rule(session, owner) in (None, "all")


def test_changing_only_one_setting_leaves_the_other_alone(web, session, owner, linked):
    template = make_template(session, owner)
    web.put(SETTINGS_URL, {"rule": "organizer", "calendar_template_id": template.id})
    web.put(SETTINGS_URL, {"rule": "none"})
    body = web.get(URL).json()
    assert (body["rule"], body["calendar_template_id"]) == ("none", template.id)
    web.put(SETTINGS_URL, {"calendar_template_id": None})
    assert web.get(URL).json()["rule"] == "none"


def test_a_template_change_reaches_the_joins_that_have_not_started_a_bot(
    web, session, owner, linked
):
    old, new = make_template(session, owner, "Old"), make_template(session, owner, "New")
    waiting = add_event(session, linked, 1, hours=2)
    retrying = add_event(session, linked, 2, hours=3)
    running = add_event(session, linked, 3, hours=4)
    add_join(session, waiting, template_id=old.id)
    add_join(session, retrying, "no_capacity", template_id=old.id)
    add_join(session, running, "joined", template_id=old.id)
    assert web.put(SETTINGS_URL, {"calendar_template_id": new.id}).status_code == 200
    assert join_of(session, waiting).template_id == new.id
    assert join_of(session, retrying).template_id == new.id
    assert (
        join_of(session, running).template_id == old.id
    )  # a bot is in it: the meeting is the truth
    web.put(SETTINGS_URL, {"calendar_template_id": None})
    assert join_of(session, waiting).template_id is None


# ---- a rule change re-runs the rule over the stored events ----


def test_a_rule_change_cancels_the_joins_it_now_forbids_and_revives_them_back(web, session, linked):
    mine = add_event(session, linked, 1, hours=2, organizer_self=True)
    theirs = add_event(session, linked, 2, hours=3, organizer_self=False)
    add_join(session, mine)
    add_join(session, theirs)

    assert web.put(SETTINGS_URL, {"rule": "organizer"}).status_code == 200
    assert join_of(session, mine).state == "scheduled"
    cancelled = join_of(session, theirs)
    assert (cancelled.state, cancelled.skip_reason) == ("cancelled", "not_organizer")

    assert web.put(SETTINGS_URL, {"rule": "all"}).status_code == 200
    revived = join_of(session, theirs)
    assert revived.id == cancelled.id  # the same row: the unique index forbids a second
    assert (revived.state, revived.skip_reason) == ("scheduled", None)

    assert web.put(SETTINGS_URL, {"rule": "none"}).status_code == 200
    assert {join_of(session, mine).state, join_of(session, theirs).state} == {"cancelled"}
    assert join_of(session, mine).skip_reason == "rule_none"


def test_a_rule_change_creates_the_join_an_event_never_had(web, session, owner, linked):
    template = make_template(session, owner)
    web.put(SETTINGS_URL, {"rule": "none", "calendar_template_id": template.id})
    event = add_event(session, linked, 1, hours=2)
    assert join_of(session, event) is None
    web.put(SETTINGS_URL, {"rule": "all"})
    join = join_of(session, event)
    assert join is not None and join.state == "scheduled"
    assert join.template_id == template.id
    assert join.starts_at == event.starts_at and join.native_id == event.native_id


def test_a_rule_change_never_touches_a_join_whose_bot_is_in(web, session, linked):
    event = add_event(session, linked, 1, hours=-0.2, length=1, organizer_self=False)
    add_join(session, event, "joined")
    web.put(SETTINGS_URL, {"rule": "none"})
    assert join_of(session, event).state == "joined"


def test_a_rule_change_clears_overrides_on_events_not_started(web, session, linked):
    future_on = add_event(session, linked, 1, hours=2, override=True)
    future_off = add_event(session, linked, 2, hours=3, override=False)
    running = add_event(session, linked, 3, hours=-0.5, length=1, override=True)
    assert web.put(SETTINGS_URL, {"rule": "organizer"}).status_code == 200
    assert stored_override(session, future_on) is None
    assert stored_override(session, future_off) is None
    assert stored_override(session, running) is True  # it has started: the switch stays


def test_choosing_the_rule_that_is_already_set_keeps_the_overrides(web, session, owner, linked):
    web.put(SETTINGS_URL, {"rule": "organizer"})
    event = add_event(session, linked, 1, hours=2, override=True)
    assert web.put(SETTINGS_URL, {"rule": "organizer"}).status_code == 200
    assert stored_override(session, event) is True


def test_a_template_change_alone_keeps_the_overrides(web, session, owner, linked):
    event = add_event(session, linked, 1, hours=2, override=False)
    template = make_template(session, owner)
    assert web.put(SETTINGS_URL, {"calendar_template_id": template.id}).status_code == 200
    assert stored_override(session, event) is False


def test_the_answer_to_a_rule_change_already_shows_the_new_words(web, session, linked):
    add_event(session, linked, 1, hours=2, organizer_self=False, override=True)
    body = web.put(SETTINGS_URL, {"rule": "organizer"}).json()
    [item] = body["upcoming"]
    assert (item["will_join_text"], item["reason_text"]) == (
        "Off",
        "Skipped because you are not the organiser",
    )


def test_one_users_rule_change_never_touches_another_users_events(
    capp, web, session, owner, stranger, google, linked
):
    other = browser(capp, OTHER_EMAIL)
    connect_google(other, google, email="other@example.test", sub="g-other-1")
    theirs_conn = connection_of(session, stranger)
    theirs = add_event(session, theirs_conn, 9, hours=2, override=True)
    theirs_join = add_join(session, theirs)
    mine = add_event(session, linked, 1, hours=2, override=True)
    add_join(session, mine)
    assert web.put(SETTINGS_URL, {"rule": "none"}).status_code == 200
    assert stored_override(session, mine) is None
    assert stored_override(session, theirs) is True
    assert join_of(session, theirs).state == theirs_join.state == "scheduled"
    assert stored_rule(session, stranger) in (None, "all")


# ---- the per-meeting switch ----


def test_override_needs_a_login(capp, owner):
    anon = browser(capp, log_in=False)
    error_of(anon.put(override_url(1), {"override": True}), 401, "not_authenticated")


def test_override_is_dark_while_the_calendar_flag_is_off(
    engine, google, fake_vexa, fake_openai, owner
):
    off = make_settings(
        google_client_id="id.apps.example.test",
        google_client_secret="s3cret",
        calendar_enabled=False,
    )
    web = browser(build_app(engine, off, google, fake_vexa, fake_openai))
    error_of(web.put(override_url(1), {"override": True}), 404, "calendar_unavailable")


def test_foreign_event_id_is_404_for_override(capp, web, session, owner, stranger, google, linked):
    other = browser(capp, OTHER_EMAIL)
    connect_google(other, google, email="other@example.test", sub="g-other-1")
    theirs = add_event(session, connection_of(session, stranger), 9, hours=2)
    response = web.put(override_url(theirs.id), {"override": False})
    error_of(response, 404, "not_found")
    assert stored_override(session, theirs) is None
    assert join_of(session, theirs) is None


def test_an_event_that_does_not_exist_is_404_for_override(web, linked):
    error_of(web.put(override_url(987_654), {"override": True}), 404, "not_found")


def test_override_after_disconnecting_is_404(web, session, linked):
    event_id = add_event(session, linked, 1, hours=2).id
    assert web.post(cc.DISCONNECT).status_code == 200
    error_of(web.put(override_url(event_id), {"override": True}), 404, "not_found")


@pytest.mark.parametrize(
    ("fields", "hours", "length", "reason"),
    [
        ({"self_response": "declined"}, 2, 1, "You declined this invite"),
        ({}, -3, 1, "Already over"),
        ({"platform": "teams"}, 2, 1, "Microsoft Teams meetings cannot be joined yet"),
        (
            {"platform": None, "meeting_url": None, "native_id": None},
            2,
            1,
            "Add a meeting link to this event to record it",
        ),
        ({"status": "cancelled"}, 2, 1, "This event was cancelled"),
        ({"all_day": True}, 2, 1, "All-day events are not joined"),
        ({}, 2, 7, "Too long to join (over 6 hours)"),
    ],
)
def test_override_on_declined_or_ended_or_teams_flag_off_is_409_not_joinable(
    web, session, linked, fields, hours, length, reason
):
    event = add_event(session, linked, 1, hours=hours, length=length, **fields)
    for value in (True, False, None):
        response = web.put(override_url(event.id), {"override": value})
        error = error_of(response, 409, "not_joinable")
        assert error["message"] == reason
    assert stored_override(session, event) is None  # nothing was stored
    assert join_of(session, event) is None  # and nothing was scheduled


def test_a_teams_event_can_be_switched_once_the_server_can_join_teams(
    engine, google, fake_vexa, fake_openai, owner, session
):
    on = make_settings(
        google_client_id=cc.CLIENT_ID,
        google_client_secret="s3cret",
        calendar_enabled=True,
        token_enc_key=cc.KEY,
        bot_provider="skribby",  # Teams follows the provider: only Skribby can join it
        skribby_api_key="placeholder-key",
    )
    web = browser(build_app(engine, on, google, fake_vexa, fake_openai))
    connect_google(web, google)
    conn = connection_of(session, owner)
    event = add_event(session, conn, 1, hours=2, platform="teams", organizer_self=False)
    web.put(SETTINGS_URL, {"rule": "none"})
    assert web.put(override_url(event.id), {"override": True}).status_code == 200


@pytest.mark.parametrize("body", [{}, {"override": "yes"}, {"override": 1}, {"on": True}, []])
def test_a_bad_override_body_is_422_and_stores_nothing(web, session, linked, body):
    event = add_event(session, linked, 1, hours=2)
    error_of(web.put(override_url(event.id), body), 422, "validation_error")
    assert stored_override(session, event) is None


def test_switching_a_meeting_on_stores_it_and_schedules_the_join(web, session, owner, linked):
    template = make_template(session, owner)
    web.put(SETTINGS_URL, {"rule": "none", "calendar_template_id": template.id})
    event = add_event(session, linked, 1, hours=2)
    assert upcoming_of(web, event)["will_join_text"] == "Off"

    response = web.put(override_url(event.id), {"override": True})
    assert response.status_code == 200, response.text
    item = response.json()
    assert item["id"] == event.id
    assert (item["will_join"], item["will_join_text"], item["reason_text"]) == (
        True,
        "Will join",
        None,
    )
    assert stored_override(session, event) is True
    join = join_of(session, event)
    assert join.state == "scheduled" and join.user_id == event.user_id
    assert join.template_id == template.id
    assert join.run_at == event.starts_at - timedelta(seconds=60)
    assert upcoming_of(web, event)["will_join_text"] == "Will join"


def test_switching_a_meeting_off_cancels_its_join_and_back_on_revives_the_same_row(
    web, session, linked
):
    event = add_event(session, linked, 1, hours=2)
    first = add_join(session, event)

    item = web.put(override_url(event.id), {"override": False}).json()
    assert (item["will_join_text"], item["reason_text"]) == ("Off", "You switched this meeting off")
    cancelled = join_of(session, event)
    assert (cancelled.state, cancelled.skip_reason) == ("cancelled", "override_off")

    assert web.put(override_url(event.id), {"override": True}).status_code == 200
    revived = join_of(session, event)
    assert revived.id == first.id
    assert (revived.state, revived.skip_reason) == ("scheduled", None)


def test_null_clears_the_switch_and_the_rule_decides_again(web, session, linked):
    event = add_event(session, linked, 1, hours=2, organizer_self=False)
    web.put(SETTINGS_URL, {"rule": "organizer"})
    web.put(override_url(event.id), {"override": True})
    assert join_of(session, event).state == "scheduled"

    item = web.put(override_url(event.id), {"override": None}).json()
    assert (item["will_join_text"], item["reason_text"]) == (
        "Off",
        "Skipped because you are not the organiser",
    )
    assert stored_override(session, event) is None
    assert join_of(session, event).state == "cancelled"


def test_a_switch_never_touches_a_join_whose_bot_is_in(web, session, linked):
    event = add_event(session, linked, 1, hours=-0.2, length=1)
    add_join(session, event, "joined")
    assert web.put(override_url(event.id), {"override": False}).status_code == 200
    assert stored_override(session, event) is False  # the choice is kept
    assert join_of(session, event).state == "joined"  # the meeting is the truth


def test_a_switch_on_a_waiting_join_that_has_no_slot_yet_stops_it(web, session, linked):
    event = add_event(session, linked, 1, hours=2)
    add_join(session, event, "no_capacity")
    web.put(override_url(event.id), {"override": False})
    assert join_of(session, event).state == "cancelled"


def test_the_unanswered_invite_rule_is_unchanged_and_the_switch_is_the_one_click_way_in(
    web, session, linked
):
    stranger_invite = add_event(
        session, linked, 1, hours=2, organizer_self=False, self_response="needsAction"
    )
    colleague_invite = add_event(
        session,
        linked,
        2,
        hours=3,
        organizer_self=False,
        organizer_same_org=True,
        self_response="needsAction",
    )
    mine_unanswered = add_event(
        session, linked, 3, hours=4, organizer_self=True, self_response="needsAction"
    )
    waiting = upcoming_of(web, stranger_invite)
    assert (waiting["will_join_text"], waiting["reason_text"]) == (
        "Off",
        "Waiting for you to accept the invite",
    )
    assert upcoming_of(web, colleague_invite)["will_join"] is True
    assert upcoming_of(web, mine_unanswered)["will_join"] is True

    item = web.put(override_url(stranger_invite.id), {"override": True}).json()
    assert item["will_join"] is True
    assert join_of(session, stranger_invite).state == "scheduled"


def test_the_switch_answer_has_exactly_the_upcoming_keys(web, session, linked):
    event = add_event(session, linked, 1, hours=2)
    item = web.put(override_url(event.id), {"override": True}).json()
    assert set(item) == set(upcoming_of(web, event))
    assert "zoom.us" not in str(item) and "meet.google.com" not in str(item)


def test_the_switch_works_for_an_event_that_is_in_progress(web, session, linked):
    event = add_event(session, linked, 1, hours=-0.25, length=1)
    assert web.put(override_url(event.id), {"override": False}).status_code == 200
    assert stored_override(session, event) is False


def test_override_needs_the_csrf_header(web, session, linked):
    event = add_event(session, linked, 1, hours=2)
    error_of(web.put(override_url(event.id), {"override": True}, csrf=False), 403, "csrf_rejected")
