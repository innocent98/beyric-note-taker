"""`GET /api/calendar`: the settings page's one read (plan task C15a, first slice).

Real Postgres and the real connect flow over `FakeGoogle`; events are rows, as the sync job leaves
them. The words in the answer are the server's, so nothing here accepts an internal code.
"""

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app import calendar_api, calendar_text
from app.gcal_rules import REASONS, JoinDecision
from app.models import CalendarConnection, CalendarEvent, UserSettings
from tests import test_calendar_connect as cc
from tests import test_web as tw
from tests.test_api import error_of
from tests.test_calendar_connect import (
    DISCONNECT,
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
# The route reads the real clock (a fixed one would put the OAuth ID token's `iat` in the future),
# so events are placed relative to the moment this module loaded; offsets are hours, not seconds.
NOW = datetime.now(UTC)
ZOOM_WITH_PASSCODE = "https://us02web.zoom.us/j/81234567890?pwd=SECRETPASSCODE123"


def add_event(session, conn, n, *, hours=1.0, length=1.0, **fields) -> CalendarEvent:
    """An event starting `hours` after NOW and lasting `length` hours."""
    start = NOW + timedelta(hours=hours)
    values = {
        "user_id": conn.user_id,
        "connection_id": conn.id,
        "source_event_id": f"evt-{conn.user_id}-{n}",
        "title": f"Standup {n}",
        "starts_at": start,
        "ends_at": start + timedelta(hours=length),
        "platform": "google_meet",
        "meeting_url": f"https://meet.google.com/abc-defg-{n:03d}",
        "native_id": f"abc-defg-{n:03d}",
        "organizer_self": True,
        "self_response": "accepted",
        "status": "confirmed",
        **fields,
    }
    event = CalendarEvent(**values)
    session.add(event)
    session.commit()
    return event


@pytest.fixture
def linked(web, session, owner, google):
    connect_google(web, google)
    return connection_of(session, owner)


# ---- who may ask, and when it is dark ----


def test_it_needs_a_login(capp, owner):
    anon = browser(capp, log_in=False)
    error_of(anon.get(URL), 401, "not_authenticated")


def test_it_is_dark_while_the_calendar_flag_is_off(engine, google, fake_vexa, fake_openai, owner):
    off = make_settings(
        google_client_id="id.apps.example.test",
        google_client_secret="s3cret",
        calendar_enabled=False,
    )
    web = browser(build_app(engine, off, google, fake_vexa, fake_openai))
    error_of(web.get(URL), 404, "calendar_unavailable")


def test_it_is_dark_without_a_google_client(engine, google, fake_vexa, fake_openai, owner):
    bare = make_settings(calendar_enabled=True)
    app = build_app(engine, bare, google, fake_vexa, fake_openai)
    from app import google_oauth

    app.dependency_overrides[google_oauth.get_google] = lambda: None
    error_of(browser(app).get(URL), 404, "calendar_unavailable")


# ---- the operator's emergency stop shows ----


def test_the_answer_says_when_auto_join_is_paused_by_the_operator(
    engine, google, fake_vexa, fake_openai, owner, session
):
    paused = make_settings(
        google_client_id="id.apps.example.test",
        google_client_secret="s3cret",
        calendar_enabled=True,
        autojoin_enabled=False,
        token_enc_key="K" * 43 + "=",
    )
    web = browser(build_app(engine, paused, google, fake_vexa, fake_openai))
    connect_google(web, google)
    body = web.get(URL).json()
    assert body["auto_join_paused"] is True
    assert body["auto_join_paused_text"] == (
        "Auto-join is paused for now. The note taker is not joining meetings from your calendar."
    )
    assert web.put(URL + "/settings", json={"rule": "organizer"}).json()["auto_join_paused"] is True


def test_the_answer_is_not_paused_by_default(web, linked):
    body = web.get(URL).json()
    assert body["auto_join_paused"] is False and body["auto_join_paused_text"] is None


def test_the_notice_follows_the_schedulers_record_not_only_this_process(web, session, linked):
    """The runbook restarts only the scheduler: the scheduler's note that auto-join is off is what
    the page reads, so `web` (still on its old environment) shows it too, and stops showing it
    once the scheduler has recorded that it is back."""
    from app.models import AppState

    assert web.get(URL).json()["auto_join_paused"] is False
    state = session.get(AppState, 1) or AppState(id=1)
    state.autojoin_paused_since = NOW
    session.add(state)
    session.commit()
    body = web.get(URL).json()
    assert body["auto_join_paused"] is True and body["auto_join_paused_text"]
    state.autojoin_paused_since = None
    session.commit()
    assert web.get(URL).json()["auto_join_paused"] is False


def test_upcoming_titles_are_cleaned_like_every_other_title(web, session, linked):
    add_event(session, linked, 1, title="Q3 \u202egnirts\u202c review\r\nBcc: x@example.test")
    add_event(session, linked, 2, hours=2, title="y" * 900)
    titles_seen = [e["title"] for e in web.get(URL).json()["upcoming"]]
    assert titles_seen[0] == "Q3 gnirts review Bcc: x@example.test"
    assert len(titles_seen[1]) == 200 and titles_seen[1].endswith("…")


# ---- not connected ----


def test_a_person_who_never_connected_gets_the_not_connected_answer(web):
    response = web.get(URL)
    assert response.status_code == 200, response.text
    assert response.json() == {
        "connected": False,
        "state": "not_connected",
        "state_text": "Google Calendar is not connected.",
        "google_email": None,
        "last_sync_at": None,
        "last_error": None,
        "rule": "all",
        "rule_text": "Joining all meetings",
        "calendar_template_id": None,
        "auto_join_paused": False,
        "auto_join_paused_text": None,
        "upcoming": [],
    }


def test_after_disconnecting_it_is_not_connected_and_shows_no_events(web, session, linked):
    add_event(session, linked, 1)
    assert web.post(DISCONNECT).status_code == 200
    body = web.get(URL).json()
    assert body["connected"] is False and body["state"] == "not_connected"
    assert body["google_email"] is None and body["upcoming"] == []


def test_a_meet_only_connection_is_not_a_connected_calendar(web, google):
    connect_google(web, google, features=["meet"])
    body = web.get(URL).json()
    assert body["connected"] is False and body["state"] == "not_connected"


# ---- connected ----


def test_a_fresh_connection_shows_the_address_and_no_sync_yet(web, linked):
    body = web.get(URL).json()
    assert body["connected"] is True and body["state"] == "connected"
    assert body["state_text"] == "Connected"
    assert body["google_email"] == "grace@example.test"
    assert body["last_sync_at"] is None and body["last_error"] is None
    assert body["upcoming"] == []


def test_last_sync_is_the_last_good_one(web, session, linked):
    linked.last_sync_at = NOW - timedelta(minutes=3)
    session.commit()
    body = web.get(URL).json()
    assert datetime.fromisoformat(body["last_sync_at"]) == NOW - timedelta(minutes=3)


@pytest.mark.parametrize(
    ("status", "reason", "state", "words"),
    [
        ("degraded", None, "degraded", "Connected, but your calendar has not been read lately."),
        (
            "needs_reconnect",
            "revoked",
            "needs_reconnect",
            "Google access was removed. Connect again to carry on.",
        ),
        (
            "needs_reconnect",
            "expired",
            "needs_reconnect",
            "Google access ran out. Connect again to carry on.",
        ),
        (
            "needs_reconnect",
            "error",
            "needs_reconnect",
            "Google Calendar stopped working. Connect again to carry on.",
        ),
    ],
)
def test_trouble_states_are_named_and_explained(web, session, linked, status, reason, state, words):
    linked.status, linked.needs_reconnect_reason = status, reason
    session.commit()
    body = web.get(URL).json()
    assert (body["state"], body["state_text"]) == (state, words)
    assert body["connected"] is (state != "needs_reconnect")
    assert body["google_email"] == "grace@example.test"  # shown so they know which one to fix


@pytest.mark.parametrize(
    "recorded",
    [
        "AuthRevoked: invalid_grant",
        "TransientError: HTTP 503",
        "CalendarSourceError: the calendar was only partly read",
        "TimeoutException",
        "ValueError",
    ],
)
def test_a_recorded_error_comes_back_as_plain_words_never_the_code(web, session, linked, recorded):
    linked.last_error, linked.last_error_at = recorded, NOW
    session.commit()
    error = web.get(URL).json()["last_error"]
    assert isinstance(error, str) and error
    for leak in ("Error", "Exception", "503", "invalid_grant", "ValueError", "Timeout"):
        assert leak not in error


def test_the_revoked_error_says_to_connect_again(web, session, linked):
    linked.last_error = "AuthRevoked: invalid_grant"
    session.commit()
    assert "connect" in web.get(URL).json()["last_error"].lower()


# ---- the upcoming list ----


def test_upcoming_lists_the_next_seven_days_in_order_and_nothing_else(web, session, linked):
    add_event(session, linked, 1, hours=30)
    add_event(session, linked, 2, hours=2)
    add_event(session, linked, 3, hours=24 * 7 - 1)  # last hour of the window
    add_event(session, linked, 4, hours=24 * 7 + 1)  # after it
    add_event(session, linked, 5, hours=-5, length=1)  # already over
    add_event(session, linked, 6, hours=-0.5, length=1)  # in progress: still shown
    add_event(session, linked, 7, hours=3, status="cancelled")
    titles = [e["title"] for e in web.get(URL).json()["upcoming"]]
    assert titles == ["Standup 6", "Standup 2", "Standup 1", "Standup 3"]


def test_each_item_has_title_times_platform_and_the_join_answer(web, session, linked):
    event = add_event(session, linked, 1, hours=2)
    [item] = web.get(URL).json()["upcoming"]
    starts, ends = item.pop("starts_at"), item.pop("ends_at")
    assert datetime.fromisoformat(starts) == NOW + timedelta(hours=2)
    assert datetime.fromisoformat(ends) == NOW + timedelta(hours=3)
    assert item == {
        "id": event.id,
        "title": "Standup 1",
        "platform": "google_meet",
        "platform_label": "Google Meet",
        "attendee_count": 0,
        "will_join": True,
        "will_join_text": "Will join",
        "reason_text": None,
    }


def test_platforms_have_plain_labels_and_a_missing_link_says_so(web, session, linked):
    add_event(session, linked, 1, hours=1, platform="zoom", meeting_url=ZOOM_WITH_PASSCODE)
    add_event(session, linked, 2, hours=2, platform=None, meeting_url=None, native_id=None)
    add_event(session, linked, 3, hours=3, platform="teams")
    one, two, three = web.get(URL).json()["upcoming"]
    assert (one["platform_label"], one["will_join"]) == ("Zoom", True)
    assert two["platform"] is None and two["platform_label"] is None
    assert (two["will_join"], two["will_join_text"]) == (False, "Cannot join")
    assert two["reason_text"] == "Add a meeting link to this event to record it"
    assert three["platform_label"] == "Microsoft Teams"
    assert three["will_join"] is False
    assert three["will_join_text"] == "Cannot join"
    assert three["reason_text"] == "Microsoft Teams meetings cannot be joined yet"


def test_the_answer_follows_the_users_rule(web, session, owner, linked):
    add_event(session, linked, 1, hours=1, organizer_self=False)
    session.add(UserSettings(user_id=owner.id, auto_join_rule="organizer"))
    session.commit()
    [item] = web.get(URL).json()["upcoming"]
    assert item["will_join"] is False
    assert (item["will_join_text"], item["reason_text"]) == (
        "Off",
        "Skipped because you are not the organiser",
    )
    settings_row = session.scalars(select(UserSettings)).one()
    settings_row.auto_join_rule = "none"
    session.commit()
    [item] = web.get(URL).json()["upcoming"]
    assert (item["will_join_text"], item["reason_text"]) == (
        "Off",
        "Auto-join is set to only when you turn it on",
    )
    body = web.get(URL).json()
    assert (body["rule"], body["rule_text"]) == ("none", "Auto-join is off")


def test_the_answer_follows_the_stored_override(web, session, linked):
    add_event(session, linked, 1, hours=1, override=False)
    add_event(session, linked, 2, hours=2, override=True, organizer_self=False)
    off, on = web.get(URL).json()["upcoming"]
    assert (off["will_join"], off["will_join_text"]) == (False, "Off")
    assert off["reason_text"] == "You switched this meeting off"
    assert (on["will_join"], on["will_join_text"], on["reason_text"]) == (True, "Will join", None)


def test_a_declined_invite_and_an_all_day_event_say_why_not(web, session, linked):
    add_event(session, linked, 1, hours=1, self_response="declined")
    add_event(session, linked, 2, hours=2, all_day=True)
    declined, all_day = web.get(URL).json()["upcoming"]
    assert (declined["will_join_text"], declined["reason_text"]) == (
        "Cannot join",
        "You declined this invite",
    )
    assert (all_day["will_join_text"], all_day["reason_text"]) == (
        "Cannot join",
        "All-day events are not joined",
    )


def test_a_stranger_invite_waits_for_the_user(web, session, linked):
    add_event(session, linked, 1, hours=1, organizer_self=False, self_response="needsAction")
    [item] = web.get(URL).json()["upcoming"]
    assert item["will_join"] is False
    assert (item["will_join_text"], item["reason_text"]) == (
        "Off",
        "Waiting for you to accept the invite",
    )


def test_every_reason_the_rules_can_give_has_plain_words_and_no_code():
    for reason in REASONS:
        decision = JoinDecision(reason == "ok", reason)
        label, why = calendar_text.join_label(decision), calendar_text.join_reason(decision)
        assert label in ("Will join", "Off", "Cannot join")
        assert (why is None) == (reason == "ok")
        for text in (label, why or ""):
            assert text.strip() == text
            assert "_" not in text, (reason, text)  # no snake_case code leaks through
    assert calendar_text.join_label(JoinDecision(True, "ok")) == "Will join"


def test_the_labels_are_the_designers_three_and_the_rule_decides_which(web):
    # Off: the person could switch it on. Cannot join: nothing they switch changes it.
    off = {"rule_none", "not_organizer", "not_accepted", "override_off"}
    for reason in REASONS:
        if reason == "ok":
            continue
        label = calendar_text.join_label(JoinDecision(False, reason))
        assert label == ("Off" if reason in off else "Cannot join"), reason


def test_an_unknown_reason_is_never_shown_as_a_code():
    decision = JoinDecision(False, "something_new")
    for text in (calendar_text.join_label(decision), calendar_text.join_reason(decision)):
        assert text and "something_new" not in text


# ---- what the answer must never carry ----


def test_upcoming_never_returns_attendee_addresses_description_or_zoom_passcode(
    web, session, linked
):
    add_event(
        session,
        linked,
        1,
        hours=1,
        platform="zoom",
        meeting_url=ZOOM_WITH_PASSCODE,
        native_id="81234567890",
        attendee_count=12,
    )
    response = web.get(URL)
    text = response.text
    assert "SECRETPASSCODE123" not in text and "pwd=" not in text
    assert "zoom.us" not in text  # the link is not part of this answer at all
    assert "@" not in json.dumps(response.json()["upcoming"])  # no attendee address
    item = response.json()["upcoming"][0]
    assert set(item) == {
        "id",
        "title",
        "starts_at",
        "ends_at",
        "platform",
        "platform_label",
        "attendee_count",
        "will_join",
        "will_join_text",
        "reason_text",
    }  # no description, location, attendees, link or passcode field exists to fill


def test_the_answer_carries_no_token_or_internal_ids(web, session, linked):
    body = web.get(URL).text
    for secret in ("refresh", "sync_token", "granted_scopes", "google_sub", "g-grace-1"):
        assert secret not in body


def test_emoji_and_non_latin_titles_round_trip(web, session, linked):
    titles = ["Sprint 🚀 planning", "Réunion équipe", "週次ミーティング", "Привет, команда ✅"]
    for n, title in enumerate(titles):
        add_event(session, linked, n, hours=n + 1, title=title)
    got = [e["title"] for e in web.get(URL).json()["upcoming"]]
    assert got == titles


# ---- one person never sees another's calendar ----


def test_another_users_events_and_connection_never_appear(
    capp, web, session, owner, stranger, google, linked
):
    mine = add_event(session, linked, 1, hours=1)
    other = browser(capp, OTHER_EMAIL)
    connect_google(other, google, email="other@example.test", sub="g-other-1")
    theirs = session.scalars(
        select(CalendarConnection).where(CalendarConnection.user_id == stranger.id)
    ).one()
    add_event(session, theirs, 9, hours=1, title="Their private meeting")
    body = web.get(URL).json()
    assert [e["id"] for e in body["upcoming"]] == [mine.id]
    assert body["google_email"] == "grace@example.test"
    assert "Their private meeting" not in web.get(URL).text
    other_body = other.get(URL).json()
    assert [e["title"] for e in other_body["upcoming"]] == ["Their private meeting"]
    assert other_body["google_email"] == "other@example.test"


def test_a_read_changes_nothing(web, session, linked):
    add_event(session, linked, 1, hours=1)
    before = (linked.last_sync_at, linked.status, linked.last_error)
    web.get(URL)
    web.get(URL)
    session.expire_all()
    conn = connection_of(session)
    assert (conn.last_sync_at, conn.status, conn.last_error) == before
    assert len(session.scalars(select(CalendarEvent)).all()) == 1


def test_a_user_with_no_settings_row_gets_the_defaults(web, session, linked):
    # No `user_settings` row (older accounts): the defaults apply, nothing breaks.
    add_event(session, linked, 1, hours=1)
    assert session.scalars(select(UserSettings)).first() is None
    assert web.get(URL).json()["upcoming"][0]["will_join"] is True


def test_the_list_is_capped(web, session, linked):
    for n in range(calendar_api.UPCOMING_LIMIT + 5):
        add_event(session, linked, n, hours=1 + n / 100)
    assert len(web.get(URL).json()["upcoming"]) == calendar_api.UPCOMING_LIMIT


def test_the_far_future_is_not_returned(web, session, linked):
    add_event(session, linked, 1, hours=24 * 30)
    assert web.get(URL).json()["upcoming"] == []


def test_the_guest_count_is_a_number_never_a_list_of_addresses(web, session, linked):
    add_event(session, linked, 1, hours=1, attendee_count=8)
    [item] = web.get(URL).json()["upcoming"]
    assert item["attendee_count"] == 8 and isinstance(item["attendee_count"], int)


@pytest.mark.parametrize(
    ("rule", "words"),
    [
        ("all", "Joining all meetings"),
        ("organizer", "Joining meetings you organise"),
        ("none", "Auto-join is off"),
    ],
)
def test_the_rule_comes_back_with_its_words(web, session, owner, linked, rule, words):
    session.add(UserSettings(user_id=owner.id, auto_join_rule=rule))
    session.commit()
    body = web.get(URL).json()
    assert (body["rule"], body["rule_text"]) == (rule, words)
