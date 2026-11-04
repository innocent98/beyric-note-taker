"""`GET /api/calendar/recurring`: the Recurring cards on the Meetings screen.

Real Postgres and the real connect flow over `FakeGoogle`; events are rows, as the sync job leaves
them. The cadence is worked out from the stored times only, so the pure cases (`describe_cadence`)
are tested with fixed dates and the route with a fixed clock (Wed 4 Nov 2026, 10:00 UTC).
"""

import json
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app import calendar_recurring
from app.models import CalendarConnection, CalendarEvent
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

capp, google, settings, vault_key, web = cc.capp, cc.google, cc.settings, cc.vault_key, cc.web
fake_openai, fake_vexa, owner, stranger = tw.fake_openai, tw.fake_vexa, tw.owner, tw.stranger

URL = "/api/calendar/recurring"
NOW = datetime(2026, 11, 4, 10, 0, tzinfo=UTC)  # a Wednesday
LAGOS = "Africa/Lagos"  # UTC+1, no daylight saving
ZOOM_WITH_PASSCODE = "https://us02web.zoom.us/j/81234567890?pwd=SECRETPASSCODE123"


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr(calendar_recurring, "_now", lambda: NOW)


def at(zone: str, y: int, m: int, d: int, hh: int, mm: int = 0) -> datetime:
    """A wall-clock time in `zone`, as the UTC instant the database holds."""
    return datetime(y, m, d, hh, mm, tzinfo=ZoneInfo(zone)).astimezone(UTC)


def add_series_event(session, conn, series, n, starts, *, length=1.0, **fields) -> CalendarEvent:
    values = {
        "user_id": conn.user_id,
        "connection_id": conn.id,
        "source_event_id": f"{series}_{n}",
        "recurring_event_id": series,
        "title": f"Title of {series}",
        "starts_at": starts,
        "ends_at": starts + timedelta(hours=length),
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


def add_weekly(session, conn, series, weekday_dates, *, zone=LAGOS, hh=14, mm=0, **fields):
    """One event per (y, m, d) at hh:mm local time."""
    return [
        add_series_event(session, conn, series, n, at(zone, *date, hh, mm), **fields)
        for n, date in enumerate(weekday_dates)
    ]


MONDAYS = [(2026, 11, 9), (2026, 11, 16)]
FRIDAYS = [(2026, 11, 6), (2026, 11, 13)]


def ask(client, tz):
    return client.get(f"{URL}?{urlencode({'tz': tz})}")


@pytest.fixture
def linked(web, session, owner, google):
    connect_google(web, google)
    return connection_of(session, owner)


# ---- cadence, as a pure function of the times ----


def lagos(*dates, hh=14, mm=0):
    return [at(LAGOS, *d, hh, mm) for d in dates]


ZONE = ZoneInfo(LAGOS)

CADENCE_CASES = [
    # the designer's own sample, and the plural for two days
    ("weekly on one weekday", lagos((2026, 11, 9), (2026, 11, 16)), "Weekly on Mondays, 14:00"),
    (
        "weekly, three weeks",
        lagos((2026, 11, 6), (2026, 11, 13), (2026, 11, 20)),
        "Weekly on Fridays, 14:00",
    ),
    (
        "weekly on two weekdays",
        lagos((2026, 11, 9), (2026, 11, 11), (2026, 11, 16), (2026, 11, 18)),
        "Weekly on Mondays and Wednesdays, 14:00",
    ),
    (
        "weekly on every weekday",
        lagos(
            *[(2026, 11, d) for d in (9, 10, 11, 12, 13, 16, 17, 18, 19, 20)],
        ),
        "Weekly on Mondays, Tuesdays, Wednesdays, Thursdays and Fridays, 14:00",
    ),
    ("daily", lagos(*[(2026, 11, d) for d in (9, 10, 11, 12)]), "Daily, 14:00"),
    (
        "minutes are kept",
        lagos((2026, 11, 9), (2026, 11, 16), hh=9, mm=30),
        "Weekly on Mondays, 09:30",
    ),
    # what the data cannot prove is only "Recurring"
    ("one occurrence", lagos((2026, 11, 9)), "Recurring"),
    ("nothing at all", [], "Recurring"),
    ("two days in a row is not proof of daily", lagos((2026, 11, 9), (2026, 11, 10)), "Recurring"),
    ("fourteen days apart is not weekly", lagos((2026, 11, 9), (2026, 11, 23)), "Recurring"),
    ("a skipped week", lagos((2026, 11, 9), (2026, 11, 23), (2026, 11, 30)), "Recurring"),
    (
        "one occurrence moved to another weekday",
        lagos((2026, 11, 9), (2026, 11, 17), (2026, 11, 23)),
        "Recurring",
    ),
    (
        "a weekday seen only once",
        lagos((2026, 11, 9), (2026, 11, 11), (2026, 11, 16)),
        "Recurring",
    ),
    (
        "two meetings on the same day",
        lagos((2026, 11, 9), (2026, 11, 9), (2026, 11, 16)),
        "Recurring",
    ),
    (
        "the time of day changes",
        [at(LAGOS, 2026, 11, 9, 14), at(LAGOS, 2026, 11, 16, 15)],
        "Recurring",
    ),
    ("daily with a gap", lagos(*[(2026, 11, d) for d in (9, 10, 12, 13)]), "Recurring"),
]


@pytest.mark.parametrize(
    ("starts", "expected"), [c[1:] for c in CADENCE_CASES], ids=[c[0] for c in CADENCE_CASES]
)
def test_cadence_says_only_what_the_times_prove(starts, expected):
    assert calendar_recurring.describe_cadence(starts, ZONE) == expected


def test_cadence_does_not_depend_on_the_order_the_rows_arrive_in():
    starts = lagos((2026, 11, 16), (2026, 11, 9), (2026, 11, 23))
    assert calendar_recurring.describe_cadence(starts, ZONE) == "Weekly on Mondays, 14:00"


def test_the_weekday_and_time_are_the_ones_in_the_owners_zone():
    # Monday 23:30 UTC is Tuesday 12:30 in Auckland (UTC+13 in November) and Monday 15:30 in LA.
    starts = [datetime(2026, 11, 9, 23, 30, tzinfo=UTC), datetime(2026, 11, 16, 23, 30, tzinfo=UTC)]
    assert (
        calendar_recurring.describe_cadence(starts, ZoneInfo("UTC")) == "Weekly on Mondays, 23:30"
    )
    assert (
        calendar_recurring.describe_cadence(starts, ZoneInfo("Pacific/Auckland"))
        == "Weekly on Tuesdays, 12:30"
    )
    assert (
        calendar_recurring.describe_cadence(starts, ZoneInfo("America/Los_Angeles"))
        == "Weekly on Mondays, 15:30"
    )


def test_a_clock_change_between_occurrences_does_not_break_a_weekly_cadence():
    # US clocks went back on Sunday 1 Nov 2026: 14:00 in New York is 18:00 UTC before, 19:00 after.
    ny = ZoneInfo("America/New_York")
    starts = [at("America/New_York", 2026, 10, 26, 14), at("America/New_York", 2026, 11, 2, 14)]
    assert starts[1] - starts[0] == timedelta(days=7, hours=1)
    assert calendar_recurring.describe_cadence(starts, ny) == "Weekly on Mondays, 14:00"


def test_next_text_is_the_designers_form():
    assert calendar_recurring.next_text(at(LAGOS, 2026, 11, 9, 14), ZONE) == "Mon 9 Nov, 14:00"
    assert calendar_recurring.next_text(at(LAGOS, 2026, 12, 1, 9, 5), ZONE) == "Tue 1 Dec, 09:05"


# ---- who may ask, and when it is dark ----


def test_it_needs_a_login(capp, owner):
    error_of(browser(capp, log_in=False).get(URL), 401, "not_authenticated")


def test_it_is_dark_while_the_calendar_flag_is_off(engine, google, fake_vexa, fake_openai, owner):
    off = make_settings(
        google_client_id="id.apps.example.test",
        google_client_secret="s3cret",
        calendar_enabled=False,
    )
    web = browser(build_app(engine, off, google, fake_vexa, fake_openai))
    error_of(web.get(URL), 404, "calendar_unavailable")


def test_it_is_dark_without_a_google_client(engine, google, fake_vexa, fake_openai, owner):
    from app import google_oauth

    bare = make_settings(calendar_enabled=True)
    app = build_app(engine, bare, google, fake_vexa, fake_openai)
    app.dependency_overrides[google_oauth.get_google] = lambda: None
    error_of(browser(app).get(URL), 404, "calendar_unavailable")


# ---- nothing to draw ----


def test_a_person_who_never_connected_gets_an_empty_list(web):
    response = web.get(URL)
    assert response.status_code == 200, response.text
    assert response.json() == {"timezone": "UTC", "series": []}


def test_after_disconnecting_there_is_nothing(web, session, linked):
    add_weekly(session, linked, "mon", MONDAYS)
    assert web.post(DISCONNECT).status_code == 200
    assert web.get(URL).json()["series"] == []


def test_a_meet_only_connection_is_not_a_calendar(web, session, google):
    connect_google(web, google, features=["meet"])
    assert web.get(URL).json()["series"] == []


def test_connected_with_only_plain_events_there_is_nothing(web, session, linked):
    add_series_event(session, linked, None, 1, NOW + timedelta(days=1), recurring_event_id=None)
    assert web.get(URL).json()["series"] == []


# ---- grouping ----


def test_events_are_grouped_by_recurring_id_into_cards(web, session, linked):
    add_weekly(session, linked, "monday-planning", MONDAYS, title="Beyric Monday planning")
    add_weekly(session, linked, "friday-review", FRIDAYS, hh=15, title="Beyric Friday review")
    add_series_event(session, linked, "x", 9, NOW + timedelta(days=1), recurring_event_id=None)
    response = ask(web, LAGOS)
    assert response.status_code == 200, response.text
    assert response.json() == {
        "timezone": LAGOS,
        "series": [
            {
                "name": "Beyric Friday review",
                "cadence": "Weekly on Fridays, 15:00",
                "next_starts_at": "2026-11-06T14:00:00Z",
                "next_text": "Fri 6 Nov, 15:00",
            },
            {
                "name": "Beyric Monday planning",
                "cadence": "Weekly on Mondays, 14:00",
                "next_starts_at": "2026-11-09T13:00:00Z",
                "next_text": "Mon 9 Nov, 14:00",
            },
        ],
    }


def test_the_default_zone_is_utc_and_says_so(web, session, linked):
    add_weekly(session, linked, "mon", MONDAYS)
    body = web.get(URL).json()
    assert body["timezone"] == "UTC"
    assert body["series"][0]["cadence"] == "Weekly on Mondays, 13:00"
    assert body["series"][0]["next_text"] == "Mon 9 Nov, 13:00"


def test_the_order_is_the_soonest_next_occurrence_first(web, session, linked):
    add_weekly(session, linked, "later", [(2026, 11, 12), (2026, 11, 19)], title="Later")
    add_weekly(session, linked, "sooner", [(2026, 11, 5), (2026, 11, 12)], title="Sooner")
    assert [s["name"] for s in web.get(URL).json()["series"]] == ["Sooner", "Later"]


def test_the_next_occurrence_is_the_first_one_that_has_not_started(web, session, linked):
    add_weekly(session, linked, "mon", [(2026, 11, 2), (2026, 11, 9), (2026, 11, 16)])
    [card] = ask(web, LAGOS).json()["series"]
    assert card["next_text"] == "Mon 9 Nov, 14:00"


def test_a_cancelled_occurrence_is_never_the_next_one(web, session, linked):
    add_weekly(session, linked, "mon", MONDAYS)
    add_series_event(
        session, linked, "mon", 9, at(LAGOS, 2026, 11, 5, 14), status="cancelled"
    )  # earlier than the real next one
    [card] = ask(web, LAGOS).json()["series"]
    assert card["next_text"] == "Mon 9 Nov, 14:00"


def test_a_meeting_in_progress_is_not_the_next_one(web, session, linked):
    # Started 30 minutes ago: it is happening, not "next".
    add_series_event(session, linked, "now", 1, NOW - timedelta(minutes=30), length=2)
    add_series_event(session, linked, "now", 2, NOW + timedelta(days=7))
    [card] = web.get(URL).json()["series"]
    assert card["next_starts_at"] == "2026-11-11T10:00:00Z"


def test_a_series_with_nothing_ahead_is_not_a_card(web, session, linked):
    add_series_event(session, linked, "done", 1, NOW - timedelta(days=1))
    add_series_event(session, linked, "cancelled", 1, NOW + timedelta(days=1), status="cancelled")
    assert web.get(URL).json()["series"] == []


def test_a_series_of_one_occurrence_is_only_recurring(web, session, linked):
    add_series_event(session, linked, "monthly", 1, NOW + timedelta(days=3), title="Board call")
    [card] = web.get(URL).json()["series"]
    assert (card["name"], card["cadence"]) == ("Board call", "Recurring")


def test_the_name_is_the_title_of_the_next_occurrence(web, session, linked):
    add_series_event(session, linked, "s", 1, NOW - timedelta(days=1), title="Old name")
    add_series_event(session, linked, "s", 2, NOW + timedelta(days=1), title="New name")
    add_series_event(session, linked, "s", 3, NOW + timedelta(days=8), title="Later name")
    assert [s["name"] for s in web.get(URL).json()["series"]] == ["New name"]


def test_a_cancelled_week_does_not_count_for_the_cadence(web, session, linked):
    # The third Monday is cancelled: the two that remain are 14 days apart, which proves nothing.
    add_weekly(session, linked, "mon", [(2026, 11, 9), (2026, 11, 23)])
    add_series_event(session, linked, "mon", 9, at(LAGOS, 2026, 11, 16, 14), status="cancelled")
    [card] = ask(web, LAGOS).json()["series"]
    assert card["cadence"] == "Recurring"


@pytest.mark.parametrize(
    "fields",
    [{"all_day": True}, {"event_type": "outOfOffice"}, {"event_type": "focusTime"}],
    ids=["all-day", "out of office", "focus time"],
)
def test_only_meetings_make_a_card_not_all_day_or_out_of_office_events(
    web, session, linked, fields
):
    for n, day in enumerate((9, 16)):
        add_series_event(session, linked, "other", n, at("UTC", 2026, 11, day, 9), **fields)
    assert web.get(URL).json()["series"] == []


def test_the_list_is_capped(web, session, linked):
    for n in range(calendar_recurring.SERIES_LIMIT + 5):
        add_series_event(session, linked, f"s{n}", 1, NOW + timedelta(hours=1 + n))
    assert len(web.get(URL).json()["series"]) == calendar_recurring.SERIES_LIMIT


# ---- the zone ----


def test_the_next_date_moves_with_the_zone_across_midnight(web, session, linked):
    # Monday 23:30 UTC is already Tuesday 12:30 in Auckland.
    add_weekly(session, linked, "late", [(2026, 11, 9), (2026, 11, 16)], zone="UTC", hh=23, mm=30)
    [utc] = ask(web, "UTC").json()["series"]
    [akl] = ask(web, "Pacific/Auckland").json()["series"]
    assert (utc["cadence"], utc["next_text"]) == ("Weekly on Mondays, 23:30", "Mon 9 Nov, 23:30")
    assert (akl["cadence"], akl["next_text"]) == ("Weekly on Tuesdays, 12:30", "Tue 10 Nov, 12:30")
    assert utc["next_starts_at"] == akl["next_starts_at"]  # the same instant


@pytest.mark.parametrize(
    "bad", ["Mars/Olympus", "../etc/passwd", "", "x" * 200, "UTC+1", "Etc/../UTC"]
)
def test_an_unknown_zone_is_refused_not_guessed(web, linked, bad):
    response = ask(web, bad)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] in {"invalid_timezone", "validation_error"}


# ---- what the answer must never carry ----


def test_the_answer_has_only_the_four_fields_and_no_address_link_or_passcode(web, session, linked):
    add_weekly(
        session,
        linked,
        "zoom-series-id-1234",
        MONDAYS,
        title="Weekly sync",
        platform="zoom",
        meeting_url=ZOOM_WITH_PASSCODE,
        native_id="81234567890",
        attendee_count=12,
    )
    response = ask(web, LAGOS)
    text = response.text
    assert "SECRETPASSCODE123" not in text and "pwd=" not in text and "zoom.us" not in text
    assert "@" not in text
    assert "zoom-series-id-1234" not in text  # the source's own id stays on the server
    assert "81234567890" not in text
    [card] = response.json()["series"]
    assert set(card) == {"name", "cadence", "next_starts_at", "next_text"}
    assert set(response.json()) == {"timezone", "series"}


def test_the_answer_carries_no_token_or_internal_ids(web, session, linked):
    add_weekly(session, linked, "mon", MONDAYS)
    body = web.get(URL).text
    for secret in ("refresh", "sync_token", "granted_scopes", "google_sub", "g-grace-1"):
        assert secret not in body


def test_emoji_and_non_latin_titles_round_trip(web, session, linked):
    titles = ["Sprint 🚀 planning", "Réunion équipe", "週次ミーティング", "Привет, команда ✅"]
    for n, title in enumerate(titles):
        add_series_event(session, linked, f"s{n}", 1, NOW + timedelta(hours=n + 1), title=title)
    assert [s["name"] for s in web.get(URL).json()["series"]] == titles


def test_a_series_without_a_title_has_no_name_to_draw_so_no_card(web, session, linked):
    add_series_event(session, linked, "blank", 1, NOW + timedelta(days=1), title="")
    add_series_event(session, linked, "spaces", 1, NOW + timedelta(days=1), title="   ")
    assert web.get(URL).json()["series"] == []


# ---- one person never sees another's calendar ----


def test_another_users_series_never_appear_and_never_merge(
    capp, web, session, owner, stranger, google, linked
):
    add_weekly(session, linked, "shared-id", MONDAYS, title="Mine")
    other = browser(capp, OTHER_EMAIL)
    connect_google(other, google, email="other@example.test", sub="g-other-1")
    theirs = session.scalars(
        select(CalendarConnection).where(CalendarConnection.user_id == stranger.id)
    ).one()
    # The very same recurring id on their calendar: it must stay theirs.
    add_series_event(
        session, theirs, "shared-id", 9, NOW + timedelta(days=2), title="Their private"
    )
    mine = ask(web, LAGOS)
    assert [s["name"] for s in mine.json()["series"]] == ["Mine"]
    assert mine.json()["series"][0]["cadence"] == "Weekly on Mondays, 14:00"
    assert "Their private" not in mine.text
    theirs_body = other.get(URL).json()
    assert [s["name"] for s in theirs_body["series"]] == ["Their private"]
    assert theirs_body["series"][0]["cadence"] == "Recurring"


def test_a_read_changes_nothing(web, session, linked):
    add_weekly(session, linked, "mon", MONDAYS)
    before = (linked.last_sync_at, linked.status, linked.last_error)
    web.get(URL)
    web.get(URL)
    session.expire_all()
    conn = connection_of(session)
    assert (conn.last_sync_at, conn.status, conn.last_error) == before
    assert len(session.scalars(select(CalendarEvent)).all()) == len(MONDAYS)


def test_the_answer_is_plain_json_that_round_trips(web, session, linked):
    add_weekly(session, linked, "mon", MONDAYS)
    text = ask(web, LAGOS).text
    assert json.loads(text)["series"][0]["next_starts_at"].endswith("Z")
