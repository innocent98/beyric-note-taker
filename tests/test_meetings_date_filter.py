# ruff: noqa: F811  (fixtures are imported by name, as in tests/test_api.py)
"""`GET /api/meetings?from=&to=`: the Meetings list limited to calendar days.

The days are the caller's own (their stored time zone, UTC when none), both included, and apply
to `created_at`, the timestamp the list's day headings use. The filter sits inside
`services.list_meetings`, behind the one visibility rule.
"""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app import teams
from app.models import TeamMember
from tests.test_api import (  # noqa: F401  (fixtures used by name)
    Api,
    anon,
    api,
    error_of,
    ready,
)
from tests.test_web import (  # noqa: F401
    OTHER_EMAIL,
    PASSWORD,
    anon_client,
    app,
    fake_openai,
    fake_vexa,
    make_user,
    owner,
    stranger,
    template,
)


def at(day: int, hour: int = 12, minute: int = 0, month: int = 11) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=UTC)


def make(session, template, when: datetime, **fields):
    return ready(session, template, created_at=when, **fields)


def listed(api, query: str = "") -> dict:
    response = api.get("/api/meetings" + query)
    assert response.status_code == 200, response.text
    return response.json()


def ids(body: dict) -> list[int]:
    return [m["id"] for m in body["meetings"]]


def set_zone(api, name: str) -> None:
    assert api.put("/api/settings/time-zone", {"time_zone": name}).status_code == 200


# ---- no parameters: today's behaviour, exactly ----


def test_no_parameters_returns_everything_as_before(api, session, template):
    old = make(session, template, at(1))
    new = make(session, template, at(9))
    body = listed(api)
    assert ids(body) == [new.id, old.id]
    assert body["has_more"] is False and body["next_before_id"] is None
    assert listed(api, "?from=&to=") == body  # empty boxes are the same as none


# ---- inclusive bounds ----


def test_both_days_are_included_whole(api, session, template):
    before = make(session, template, at(6, 23, 59))
    first_instant = make(session, template, at(7, 0, 0))
    last_instant = make(session, template, at(9, 23, 59))
    after = make(session, template, at(10, 0, 0))
    got = ids(listed(api, "?from=2026-11-07&to=2026-11-09"))
    assert got == [last_instant.id, first_instant.id]
    assert before.id not in got and after.id not in got


def test_one_day_is_from_and_to_the_same_day(api, session, template):
    inside = make(session, template, at(8, 15))
    make(session, template, at(7, 15))
    make(session, template, at(9, 15))
    assert ids(listed(api, "?from=2026-11-08&to=2026-11-08")) == [inside.id]


def test_only_from_or_only_to_is_an_open_ended_range(api, session, template):
    a, b, c = (make(session, template, at(d)) for d in (5, 8, 11))
    assert ids(listed(api, "?from=2026-11-08")) == [c.id, b.id]
    assert ids(listed(api, "?to=2026-11-08")) == [b.id, a.id]


def test_a_range_with_no_meetings_is_an_empty_list_not_an_error(api, session, template):
    make(session, template, at(8))
    body = listed(api, "?from=2026-01-01&to=2026-01-31")
    assert body["meetings"] == [] and body["has_more"] is False and body["next_before_id"] is None


# ---- the caller's own days ----


def test_the_days_are_in_the_callers_zone_not_utc(api, session, template):
    """Lagos is UTC+1: 23:30 on the 7th there is 22:30 UTC, and 00:30 on the 8th there is 23:30
    UTC on the 7th. By UTC clock both would be "the 7th"; in Lagos they are two different days."""
    set_zone(api, "Africa/Lagos")
    late = make(session, template, at(7, 22, 30))  # 23:30 on the 7th in Lagos
    early = make(session, template, at(7, 23, 30))  # 00:30 on the 8th in Lagos
    assert ids(listed(api, "?from=2026-11-07&to=2026-11-07")) == [late.id]
    assert ids(listed(api, "?from=2026-11-08&to=2026-11-08")) == [early.id]


def test_a_zone_behind_utc_moves_the_other_way(api, session, template):
    """New York is UTC-5 in November: 02:00 UTC on the 8th is 21:00 on the 7th there."""
    set_zone(api, "America/New_York")
    evening = make(session, template, at(8, 2, 0))
    morning = make(session, template, at(8, 12, 0))
    assert ids(listed(api, "?from=2026-11-07&to=2026-11-07")) == [evening.id]
    assert ids(listed(api, "?from=2026-11-08&to=2026-11-08")) == [morning.id]


def test_no_stored_zone_means_utc(api, session, template):
    late = make(session, template, at(7, 23, 30))
    early = make(session, template, at(8, 0, 30))
    assert ids(listed(api, "?from=2026-11-07&to=2026-11-07")) == [late.id]
    assert ids(listed(api, "?from=2026-11-08&to=2026-11-08")) == [early.id]


def test_a_day_with_a_clock_change_is_still_a_whole_day(api, session, template):
    """Europe/London ends summer time on 2026-10-25 (that day has 25 hours)."""
    set_zone(api, "Europe/London")
    inside_start = make(session, template, datetime(2026, 10, 25, 0, 0, tzinfo=UTC))  # 01:00 BST
    inside_end = make(session, template, datetime(2026, 10, 25, 23, 30, tzinfo=UTC))  # 23:30 GMT
    outside = make(session, template, datetime(2026, 10, 26, 0, 30, tzinfo=UTC))  # 00:30 on 26th
    got = ids(listed(api, "?from=2026-10-25&to=2026-10-25"))
    assert got == [inside_end.id, inside_start.id] and outside.id not in got


# ---- no stored zone: the page says which zone its headings use (`tz`) ----


def test_a_new_york_user_with_no_stored_zone_gets_the_days_their_headings_show(
    api, session, template
):
    """The page writes its day headings in the browser's zone when nothing is stored, so it sends
    that zone as `tz`. 20:00 on Tue 10 Nov in New York (UTC-5) is 01:00 UTC on the 11th, and
    21:00 on the 9th is 02:00 UTC on the 10th: by UTC days the filter would return exactly the
    wrong one."""
    tuesday_evening = make(session, template, datetime(2026, 11, 11, 1, 0, tzinfo=UTC))
    monday_evening = make(session, template, datetime(2026, 11, 10, 2, 0, tzinfo=UTC))
    day = "from=2026-11-10&to=2026-11-10"
    assert ids(listed(api, f"?{day}&tz=America/New_York")) == [tuesday_evening.id]
    assert ids(listed(api, f"?{day}&tz=America/New_York&limit=1")) == [tuesday_evening.id]
    # without `tz` the days are UTC days, as before: the other meeting
    assert ids(listed(api, f"?{day}")) == [monday_evening.id]


def test_a_lagos_user_with_no_stored_zone_gets_lagos_days(api, session, template):
    inside = make(session, template, at(10, 22, 30))  # 23:30 on the 10th in Lagos
    outside = make(session, template, at(10, 23, 30))  # 00:30 on the 11th in Lagos
    got = ids(listed(api, "?from=2026-11-10&to=2026-11-10&tz=Africa/Lagos"))
    assert got == [inside.id] and outside.id not in got


def test_a_stored_zone_wins_over_tz(api, session, template):
    set_zone(api, "Africa/Lagos")
    late = make(session, template, at(7, 22, 30))  # 23:30 on the 7th in Lagos
    early = make(session, template, at(7, 23, 30))  # 00:30 on the 8th in Lagos
    # the page would not send `tz` here, but if it does the stored zone decides
    assert ids(listed(api, "?from=2026-11-07&to=2026-11-07&tz=America/New_York")) == [late.id]
    assert ids(listed(api, "?from=2026-11-08&to=2026-11-08&tz=Pacific/Auckland")) == [early.id]


def test_tz_alone_changes_nothing_and_a_blank_tz_is_none(api, session, template):
    a, b = make(session, template, at(1)), make(session, template, at(9))
    assert ids(listed(api, "?tz=America/New_York")) == [b.id, a.id]
    assert listed(api, "?from=2026-11-01&to=2026-11-01&tz=") == listed(
        api, "?from=2026-11-01&to=2026-11-01"
    )


@pytest.mark.parametrize("tz", ["Mars/Base", "not a zone", "../etc/passwd", "x" * 200, "UTC+99"])
def test_an_unknown_tz_is_a_plain_422_even_when_a_stored_zone_would_win(api, tz):
    error = error_of(api.get(f"/api/meetings?from=2026-11-07&tz={tz}"), 422, "invalid_time_zone")
    assert error["message"].startswith("That time zone is not known.")
    set_zone(api, "Africa/Lagos")
    error_of(api.get(f"/api/meetings?from=2026-11-07&tz={tz}"), 422, "invalid_time_zone")


# ---- paging inside a range ----


def test_paging_stays_inside_the_range_and_reports_has_more(api, session, template):
    make(session, template, at(2))  # outside, older
    inside = [make(session, template, at(8, hour)) for hour in range(5)]
    make(session, template, at(12))  # outside, newer
    query = "from=2026-11-08&to=2026-11-08"
    first = listed(api, f"?{query}&limit=2")
    assert ids(first) == [inside[4].id, inside[3].id]
    assert first["has_more"] is True and first["next_before_id"] == inside[3].id
    second = listed(api, f"?{query}&limit=2&before_id={first['next_before_id']}")
    assert ids(second) == [inside[2].id, inside[1].id] and second["has_more"] is True
    third = listed(api, f"?{query}&limit=2&before_id={second['next_before_id']}")
    assert ids(third) == [inside[0].id]
    assert third["has_more"] is False and third["next_before_id"] is None


def test_has_more_is_false_when_only_out_of_range_meetings_remain(api, session, template):
    inside = make(session, template, at(8))
    make(session, template, at(1))  # older, but outside
    body = listed(api, "?from=2026-11-08&to=2026-11-08&limit=1")
    assert ids(body) == [inside.id]
    assert body["has_more"] is False and body["next_before_id"] is None


# ---- refusals: plain 422s ----


@pytest.mark.parametrize(
    "query,which",
    [
        ("from=2026-02-30", "From"),
        ("from=11/07/2026", "From"),
        ("from=2026-11-7", "From"),
        ("from=20261107", "From"),
        ("from=not-a-date", "From"),
        ("from=2026-11-07T00:00", "From"),
        ("to=2026-13-01", "To"),
        ("to=1700000000", "To"),
        ("to=0001-01-01", "To"),
        ("to=9999-12-31", "To"),
    ],
)
def test_a_bad_date_is_a_plain_422(api, query, which):
    error = error_of(api.get(f"/api/meetings?{query}"), 422, "invalid_date")
    assert (
        error["message"].startswith(f"{which} is not a date.") and "YYYY-MM-DD" in error["message"]
    )


def test_a_date_string_that_is_too_long_is_the_same_plain_422(api):
    error_of(api.get("/api/meetings?from=2026-11-07" + "0" * 400), 422, "invalid_date")


def test_from_after_to_is_a_plain_422_and_nothing_is_listed(api, session, template):
    make(session, template, at(8))
    error = error_of(
        api.get("/api/meetings?from=2026-11-09&to=2026-11-08"), 422, "invalid_date_range"
    )
    assert error["message"] == "From must be on or before To. Swap the two dates."


# ---- visibility never widens ----


@pytest.fixture
def teammate(session, owner):
    user = make_user(session, "teammate@example.test")
    team = teams.ensure_team(session, owner)
    session.add(TeamMember(team_id=team.id, user_id=user.id, role="member"))
    session.commit()
    return user


def sign_in(app, email) -> Api:
    client = TestClient(app, follow_redirects=False)
    client.__enter__()
    handle = Api(client)
    assert handle.log_in(email=email).status_code == 200
    return handle


def test_a_teammate_sees_a_shared_meeting_in_range_and_never_a_private_one(
    app, session, owner, teammate, template
):
    shared = make(session, template, at(8, 10))
    private = make(session, template, at(8, 11))
    outside = make(session, template, at(2))
    shared.shared_team_id = teams.team_of(session, owner.id).id
    outside.shared_team_id = shared.shared_team_id
    session.commit()
    mate = sign_in(app, "teammate@example.test")
    # unfiltered: the two shared ones; the private one is not theirs to see
    assert ids(listed(mate)) == [outside.id, shared.id]
    # filtered: still only what the rule allows, then narrowed by the days
    got = ids(listed(mate, "?from=2026-11-08&to=2026-11-08"))
    assert got == [shared.id] and private.id not in got
    assert ids(listed(mate, "?from=2026-11-01&to=2026-11-03")) == [outside.id]


def test_the_days_are_the_readers_own_not_the_recorders(app, session, owner, teammate, template):
    """The owner is in Lagos, the teammate in UTC. The same shared meeting is on the 7th for the
    teammate (23:30 UTC), but the 8th for the owner (00:30 in Lagos)."""
    shared = make(session, template, at(7, 23, 30))
    shared.shared_team_id = teams.team_of(session, owner.id).id
    session.commit()
    boss = sign_in(app, "owner@example.test")
    set_zone(boss, "Africa/Lagos")
    mate = sign_in(app, "teammate@example.test")
    assert ids(listed(mate, "?from=2026-11-07&to=2026-11-07")) == [shared.id]
    assert ids(listed(boss, "?from=2026-11-07&to=2026-11-07")) == []
    assert ids(listed(boss, "?from=2026-11-08&to=2026-11-08")) == [shared.id]
