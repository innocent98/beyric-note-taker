"""The Microsoft Graph event normaliser (Outlook phase O, M5): `graph_events.parse_event` to the one
`ParsedEvent`, the table of spec 5.4 run against `will_join`, and the attacker-controlled-text
cases of review focus 7 and 8.

**Fixture rule (the M0 run has not happened yet).** Every event in `tests/fixtures/graph/` is a
DOCUMENTED shape, not a capture: `documented_*__replace_with_m0_capture.json`. When Adebayo's M0 run
produces the redacted `calendarView` captures they replace these files, and the assumptions listed
in the SOP under "to confirm with the M0 run" are settled one by one.
"""

import dataclasses
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app import calendar_providers, gcal_sync
from app.calendar_providers import PROVIDERS, Provider
from app.calendar_source import SyncBatch
from app.gcal_events import EventParseError, ParsedEvent
from app.gcal_rules import JoinSettings, will_join
from app.graph_events import SCAN_LIMIT, parse_event
from app.models import CalendarConnection, CalendarEvent, ScheduledJoin, UserSettings
from tests.test_web import make_settings, make_user

FIXTURES = Path(__file__).parent / "fixtures" / "graph"
NOW = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)
ZOOM_PASSCODE = "PLACEHOLDERPASSCODE"
MEET = "https://meet.google.com/abc-defg-hij"
ZOOM = "https://us02web.zoom.us/j/81234567890"
TEAMS = "https://teams.microsoft.com/l/meetup-join/19%3ameeting_PLACEHOLDER%40thread.v2/0"


def fx(name: str, **changes) -> dict:
    raw = json.loads((FIXTURES / f"documented_{name}__replace_with_m0_capture.json").read_text())
    raw.update(changes)
    return raw


def parsed(name: str, *, domains: str | None = None, **changes) -> ParsedEvent:
    return parse_event(fx(name, **changes), tenant_domains=domains)


def decide(event: ParsedEvent, *, rule="all", teams=False, override=None):
    return will_join(
        event, override, JoinSettings(rule=rule, teams_join_enabled=teams), now=NOW
    ).reason


# ---- what each kind of event becomes ----


def test_a_teams_meeting_of_a_work_account():
    """`onlineMeeting.joinUrl` on an online meeting. Platform `teams`, with a derived native id."""
    event = parsed("teams_work")
    assert event.platform == "teams" and event.native_id.startswith("teams-")
    assert event.meeting_url.startswith("https://teams.microsoft.com/l/meetup-join/")
    assert "context=" in event.meeting_url  # the query carries the meeting context a join needs
    assert event.source_event_id == "TEAMS-WORK-ID"
    assert event.status == "confirmed" and event.event_type == "default" and not event.all_day


def test_a_teams_meeting_of_a_personal_account():
    event = parsed("teams_personal")
    assert event.platform == "teams" and event.native_id.startswith("teams-")
    assert event.meeting_url.startswith("https://teams.live.com/meet/")


def test_a_zoom_link_hidden_behind_link_text_in_the_html_body():
    event = parsed("zoom_in_body")
    assert event.platform == "zoom" and event.native_id == "81234567890"
    assert event.meeting_url == f"https://us02web.zoom.us/j/81234567890?pwd={ZOOM_PASSCODE}"


def test_a_meet_link_in_the_location():
    event = parsed("meet_in_location")
    assert event.platform == "google_meet" and event.native_id == "abc-defg-hij"
    assert event.meeting_url == MEET


def test_an_all_day_event_keeps_its_instants_and_is_flagged():
    event = parsed("all_day")
    assert event.all_day is True
    assert event.starts_at == datetime(2026, 11, 12, tzinfo=UTC)
    assert event.ends_at == datetime(2026, 11, 13, tzinfo=UTC)
    assert decide(event) == "all_day"


def test_a_cancelled_event_stays_cancelled():
    event = parsed("cancelled")
    assert event.status == "cancelled" and decide(event) == "cancelled"


def test_a_recurring_occurrence_points_at_its_series():
    event = parsed("recurring_occurrence")
    assert event.recurring_event_id == "SERIES-MASTER-ID"
    assert event.event_type == "default"  # occurrences and exceptions are ordinary meetings
    assert parsed("zoom_in_body").recurring_event_id is None


@pytest.mark.parametrize("kind", ["singleInstance", "occurrence", "exception", "seriesMaster"])
def test_every_graph_type_is_a_default_event(kind):
    assert parsed("zoom_in_body", type=kind).event_type == "default"


def test_a_private_event_is_treated_like_any_other():
    """Parity with Google: the owner sees their own private titles, so nothing special happens."""
    event = parsed("private")
    assert event.platform == "google_meet" and decide(event) == "ok"


def test_a_draft_is_still_a_meeting_the_organiser_means_to_hold():
    assert decide(parsed("zoom_in_body", isDraft=True)) == "ok"


def test_the_title_is_passed_on_for_the_sync_to_clean():
    assert parsed("zoom_in_body", subject="Weekly\x00 planning").title == "Weekly\x00 planning"
    assert parsed("zoom_in_body", subject=None).title == ""
    assert parsed("zoom_in_body", subject=5).title == ""


def test_the_parsed_event_has_no_body_location_or_attendee_address():
    names = {f.name for f in dataclasses.fields(ParsedEvent)}
    assert not names & {"body", "location", "description", "attendees", "organizer_email"}
    event = parsed("stranger_invite")
    text = repr(event)
    assert "evil.example" not in text and "us02web" not in text  # the link stays out of the repr


# ---- ids ----


@pytest.mark.parametrize("bad", [None, "", 5, ["x"]])
def test_an_event_without_a_usable_id_is_unreadable(bad):
    with pytest.raises(EventParseError, match="no id"):
        parse_event(fx("zoom_in_body", id=bad))


def test_a_non_object_is_unreadable():
    for raw in (None, [], "x", 5):
        with pytest.raises(EventParseError, match="no id"):
            parse_event(raw)


# ---- times (spec 5.3) ----


def test_the_documented_seven_digit_time_parses_to_utc():
    """The exact string Graph documents, so a Python version difference cannot slip in."""
    event = parse_event(
        fx(
            "zoom_in_body",
            start={"dateTime": "2026-11-05T10:00:00.0000000", "timeZone": "UTC"},
            end={"dateTime": "2026-11-05T10:45:30.1234567", "timeZone": "UTC"},
        )
    )
    assert event.starts_at == datetime(2026, 11, 5, 10, 0, tzinfo=UTC)
    assert event.ends_at == datetime(2026, 11, 5, 10, 45, 30, 123456, tzinfo=UTC)
    assert event.starts_at.tzinfo is UTC


def test_a_time_without_fractional_digits_parses_too():
    event = parse_event(
        fx("zoom_in_body", start={"dateTime": "2026-11-05T10:00:00", "timeZone": "UTC"})
    )
    assert event.starts_at == datetime(2026, 11, 5, 10, tzinfo=UTC)


@pytest.mark.parametrize(
    "zone",
    ["W. Europe Standard Time", "Pacific Standard Time", "Europe/Berlin", "utc", "UTC+1", "", None],
)
def test_any_zone_but_utc_is_refused_never_converted(zone):
    with pytest.raises(EventParseError, match="not in UTC"):
        parse_event(
            fx("zoom_in_body", start={"dateTime": "2026-11-05T10:00:00.0000000", "timeZone": zone})
        )
    with pytest.raises(EventParseError, match="not in UTC"):
        parse_event(
            fx("zoom_in_body", end={"dateTime": "2026-11-05T10:00:00.0000000", "timeZone": zone})
        )


@pytest.mark.parametrize(
    "point,message",
    [
        (None, "no start or end"),
        ({}, "no start or end"),
        ("2026-11-05T10:00:00", "no start or end"),
        ({"timeZone": "UTC"}, "no dateTime"),
        ({"dateTime": 5, "timeZone": "UTC"}, "no dateTime"),
        ({"dateTime": "not a date", "timeZone": "UTC"}, "not a valid date"),
        ({"dateTime": "2026-13-45T10:00:00", "timeZone": "UTC"}, "not a valid date"),
        ({"dateTime": "2026-11-05T10:00:00+02:00", "timeZone": "UTC"}, "carries an offset"),
    ],
)
def test_unreadable_times_are_refused_with_a_message_that_quotes_nothing(point, message):
    with pytest.raises(EventParseError, match=message) as raised:
        parse_event(fx("zoom_in_body", start=point))
    assert "2026" not in str(raised.value)


def test_a_zero_offset_time_is_accepted():
    event = parse_event(
        fx("zoom_in_body", start={"dateTime": "2026-11-05T10:00:00+00:00", "timeZone": "UTC"})
    )
    assert event.starts_at == datetime(2026, 11, 5, 10, tzinfo=UTC)


def test_a_missing_end_is_refused():
    raw = fx("zoom_in_body")
    del raw["end"]
    with pytest.raises(EventParseError, match="no start or end"):
        parse_event(raw)


# ---- the reply, the organiser, the count (spec 5.4) ----


@pytest.mark.parametrize(
    "graph,ours",
    [
        ("accepted", "accepted"),
        ("tentativelyAccepted", "tentative"),
        ("declined", "declined"),
        ("notResponded", "needsAction"),
        ("organizer", "accepted"),
        ("none", None),
        ("somethingNew", None),
        ("", None),
    ],
)
def test_the_reply_maps_as_the_table_says(graph, ours):
    event = parsed("zoom_in_body", responseStatus={"response": graph})
    assert event.self_response == ours


@pytest.mark.parametrize("status", [None, {}, "accepted", [], {"response": 5}, {"response": None}])
def test_a_reply_we_cannot_read_is_none_never_accepted(status):
    assert parsed("zoom_in_body", responseStatus=status).self_response is None


def test_organizer_self_follows_is_organizer():
    assert parsed("zoom_in_body", isOrganizer=True).organizer_self is True
    assert parsed("zoom_in_body", isOrganizer=False).organizer_self is False


def test_a_missing_organizer_flag_is_read_cautiously():
    """Graph always sends it. If it were missing, other people on the event mean "not mine"; a
    solo event has no organiser information at all."""
    with_people = fx("zoom_in_body")
    del with_people["isOrganizer"]
    assert parse_event(with_people).organizer_self is False
    solo = fx("solo_no_attendees")
    del solo["isOrganizer"]
    assert parse_event(solo).organizer_self is None
    assert parsed("zoom_in_body", isOrganizer="true").organizer_self is False


def test_the_attendee_count_adds_the_organiser_graph_leaves_out():
    """ASSUMED (to confirm in M0): Graph's `attendees` omits the organiser."""
    assert parsed("zoom_in_body", attendees=[]).attendee_count == 0
    assert parsed("zoom_in_body").attendee_count == 2  # one attendee plus the organiser
    people = [{"emailAddress": {"address": f"p{i}@example.test"}} for i in range(4)]
    assert parsed("zoom_in_body", attendees=people).attendee_count == 5
    assert parsed("zoom_in_body", attendees=None).attendee_count == 0
    assert parsed("zoom_in_body", attendees="nobody").attendee_count == 0
    assert parsed("zoom_in_body", attendees=["x", 5, None]).attendee_count == 0
    assert parsed("zoom_in_body", attendees=[{"a": 1}, "x"]).attendee_count == 2


# ---- the same-organisation test (review focus 8) ----


def test_a_personal_account_never_has_a_same_org_organiser():
    event = parsed("stranger_invite", organizer={"emailAddress": {"address": "me@outlook.test"}})
    assert event.organizer_same_org is False


@pytest.mark.parametrize(
    "domains,address,expected",
    [
        ("company.test", "boss@company.test", True),
        ("company.test", "Boss@COMPANY.TEST", True),
        ("other.test company.test", "boss@company.test", True),
        ("other.test,company.test", "boss@company.test", True),
        ("other.test;company.test\ncorp.test", "boss@corp.test", True),
        ("company.test", "boss@mail.company.test", False),  # exact domains only
        ("company.test", "boss@company.test.evil.example", False),
        ("company.test", "boss@evilcompany.test", False),
        ("company.test", "boss@evil.example", False),
        ("company.test", "a@b@company.test", False),
        ("company.test", "company.test", False),
        ("company.test", "", False),
        ("company.test", None, False),
        ("", "boss@company.test", False),
        (None, "boss@company.test", False),
    ],
)
def test_the_organiser_domain_must_be_one_of_the_organisations_domains(domains, address, expected):
    event = parsed(
        "stranger_invite", domains=domains, organizer={"emailAddress": {"address": address}}
    )
    assert event.organizer_same_org is expected


@pytest.mark.parametrize(
    "organizer", [None, {}, "x", {"emailAddress": None}, {"emailAddress": "x"}]
)
def test_an_organiser_we_cannot_read_is_never_the_same_org(organizer):
    assert (
        parsed("stranger_invite", domains="company.test", organizer=organizer).organizer_same_org
        is False
    )


# ---- finding the meeting link (spec 5.5) ----


def test_the_teams_join_url_wins_over_a_zoom_link_in_the_body():
    event = parsed(
        "teams_work",
        body={"contentType": "html", "content": f"<a href='{ZOOM}'>Zoom backup</a>"},
        location={"displayName": MEET},
    )
    assert event.platform == "teams"


def test_the_join_url_is_ignored_unless_the_event_is_an_online_meeting():
    event = parsed("teams_work", isOnlineMeeting=False, body={})
    assert event.platform is None
    event = parsed("teams_work", isOnlineMeeting="true", body={})
    assert event.platform is None  # only a real boolean true counts


def test_the_provider_string_never_decides_the_platform():
    """Only the URL's host does: a `teamsForBusiness` meeting whose join URL is not Teams is not
    called a Teams meeting."""
    event = parsed(
        "teams_work",
        onlineMeeting={"joinUrl": "https://evil.example/l/meetup-join/19%3ameeting_x/0"},
        body={},
    )
    assert event.platform is None
    event = parsed("zoom_in_body", onlineMeetingProvider="teamsForBusiness")
    assert event.platform == "zoom"


def test_the_location_beats_the_body_and_locations_are_all_read():
    event = parsed(
        "zoom_in_body",
        location={"displayName": ""},
        locations=[{"displayName": "Room 4"}, {"displayName": MEET}],
    )
    assert event.platform == "google_meet"


def test_the_first_usable_link_in_the_body_wins_and_unusable_ones_are_skipped():
    body = f"<a href='https://example.test/x'>x</a> <a href='{ZOOM}'>z</a> <a href='{MEET}'>m</a>"
    event = parsed("zoom_in_body", body={"contentType": "html", "content": body})
    assert event.platform == "zoom"


def test_html_entities_in_an_href_are_unescaped():
    body = '<a href="https://us02web.zoom.us/j/81234567890?pwd=ABC&amp;from=addon">x</a>'
    event = parsed("zoom_in_body", body={"contentType": "html", "content": body})
    assert event.meeting_url == "https://us02web.zoom.us/j/81234567890?pwd=ABC"


@pytest.mark.parametrize(
    "link",
    [
        "https://user:pass@us02web.zoom.us/j/81234567890",
        "https://us02web.zoom.us:8443/j/81234567890",
        "https://zoom.us.evil.example/j/81234567890",
        "https://evilzoom.us/j/81234567890",
        "javascript:alert(1)",
        "ftp://us02web.zoom.us/j/81234567890",
        "https://us02web.zoom.us/j/81234567890/../../x",
        "https://meet.google.com/abc-defg-hij@evil.example",
        "https://teams.microsoft.com.evil.example/l/meetup-join/x",
        "https://teams.microsoft.com/l/other/x",
    ],
)
def test_a_link_the_existing_parser_rejects_is_never_used(link):
    event = parsed(
        "zoom_in_body", body={"contentType": "html", "content": f"<a href='{link}'>x</a>"}
    )
    assert event.platform is None and event.meeting_url is None


def test_a_link_beyond_the_scan_limit_is_not_read():
    body = "x" * (SCAN_LIMIT + 10) + f" {ZOOM}"
    event = parsed("zoom_in_body", body={"contentType": "html", "content": body})
    assert event.platform is None
    inside = ("x" * 1000) + f" {ZOOM}"
    assert (
        parsed("zoom_in_body", body={"contentType": "html", "content": inside}).platform == "zoom"
    )


def test_only_the_first_twenty_locations_are_read():
    locations = [{"displayName": f"Room {i}"} for i in range(20)] + [{"displayName": MEET}]
    assert parsed("zoom_in_body", location={}, locations=locations, body={}).platform is None


@pytest.mark.parametrize(
    "fields",
    [
        {"body": None},
        {"body": "text"},
        {"body": {"content": 5}},
        {"body": {"content": None}},
        {"location": None},
        {"location": "Room"},
        {"location": {"displayName": 5}},
        {"locations": None},
        {"locations": "Room"},
        {"locations": [None, 5, {"displayName": None}]},
        {"onlineMeeting": "x"},
        {"onlineMeeting": {"joinUrl": 5}},
    ],
)
def test_odd_shapes_in_the_link_fields_mean_no_link_not_a_crash(fields):
    event = parsed("zoom_in_body", **{"body": {}, "location": {}, "locations": [], **fields})
    assert event.platform is None


# ---- Safe Links (ASSUMED shape; M0 S5 confirms with a work account) ----


def test_a_safe_links_wrapper_is_unwrapped_to_the_real_zoom_link():
    event = parsed("safelinks_work")
    assert event.platform == "zoom" and event.native_id == "81234567890"
    assert event.meeting_url == f"https://us02web.zoom.us/j/81234567890?pwd={ZOOM_PASSCODE}"
    assert "safelinks" not in event.meeting_url


def wrapped(target: str, host: str = "nam02.safelinks.protection.outlook.com", scheme="https"):
    from urllib.parse import quote

    return f"{scheme}://{host}/?url={quote(target, safe='')}&data=05%7C02&reserved=0"


@pytest.mark.parametrize(
    "link",
    [
        wrapped(ZOOM, host="safelinks.protection.outlook.com.evil.example"),
        wrapped(ZOOM, host="evil.example"),
        wrapped(ZOOM, host="xsafelinks.protection.outlook.com"),
        wrapped(ZOOM, host="safelinks.protection.outlook.com"),  # real ones have a region prefix
        wrapped(ZOOM, host="nam02.safelinks.protection.outlook.com.evil.example"),
        wrapped(ZOOM, scheme="http"),
        wrapped("https://evil.example/j/81234567890"),  # target is not a meeting link
        wrapped("javascript:alert(1)"),
        wrapped(wrapped(ZOOM)),  # only one level is ever unwrapped
        "https://nam02.safelinks.protection.outlook.com/?data=05",  # no `url`
        "https://nam02.safelinks.protection.outlook.com/?url=",
    ],
)
def test_a_wrapper_that_is_not_exactly_safe_links_or_hides_no_meeting_is_ignored(link):
    event = parsed(
        "zoom_in_body", body={"contentType": "html", "content": f"<a href='{link}'>x</a>"}
    )
    assert event.platform is None


def test_the_safe_links_host_is_compared_case_insensitively():
    link = wrapped(ZOOM, host="NAM02.SafeLinks.Protection.Outlook.Com")
    event = parsed(
        "zoom_in_body", body={"contentType": "html", "content": f"<a href='{link}'>x</a>"}
    )
    assert event.platform == "zoom"


def test_a_safe_links_wrapped_teams_link_is_a_teams_link():
    event = parsed(
        "zoom_in_body", body={"contentType": "html", "content": f"<a href='{wrapped(TEAMS)}'>x</a>"}
    )
    assert event.platform == "teams" and event.native_id.startswith("teams-")


# ---- the table of spec 5.4 against will_join ----

TABLE = [
    # (fixture, changes, domains, rule, teams on, expected reason)
    ("zoom_in_body", {}, None, "all", False, "ok"),
    ("zoom_in_body", {}, None, "organizer", False, "ok"),
    ("zoom_in_body", {}, None, "none", False, "rule_none"),
    ("meet_in_location", {}, None, "all", False, "ok"),
    ("teams_work", {}, None, "all", False, "teams_unsupported"),
    ("teams_work", {}, None, "all", True, "ok"),
    ("teams_personal", {}, None, "all", False, "teams_unsupported"),
    ("teams_personal", {}, None, "organizer", True, "ok"),
    ("all_day", {}, None, "all", True, "all_day"),
    ("cancelled", {}, None, "all", True, "cancelled"),
    ("declined_invite", {}, None, "all", True, "declined"),
    ("recurring_occurrence", {}, None, "all", False, "ok"),
    ("private", {}, None, "all", False, "ok"),
    ("solo_no_attendees", {}, None, "all", False, "ok"),
    ("zoom_in_body", {"body": {}}, None, "all", False, "no_link"),
    ("zoom_in_body", {"isOnlineMeeting": False}, None, "all", False, "ok"),
    # an invite nobody answered, from outside the organisation, with a Zoom link
    ("stranger_invite", {}, None, "all", False, "not_accepted"),
    ("stranger_invite", {}, "company.test", "all", False, "not_accepted"),
    ("stranger_invite", {}, None, "organizer", False, "not_organizer"),
    # ... answered, it joins under `all` but still not under `organizer`
    (
        "stranger_invite",
        {"responseStatus": {"response": "accepted"}},
        None,
        "all",
        False,
        "ok",
    ),
    (
        "stranger_invite",
        {"responseStatus": {"response": "tentativelyAccepted"}},
        None,
        "all",
        False,
        "ok",
    ),
    (
        "stranger_invite",
        {"responseStatus": {"response": "accepted"}},
        None,
        "organizer",
        False,
        "not_organizer",
    ),
    # a colleague's unanswered invite joins when the organiser is in the organisation's domains
    (
        "stranger_invite",
        {"organizer": {"emailAddress": {"address": "boss@company.test"}}},
        "company.test",
        "all",
        False,
        "ok",
    ),
    # a look-alike domain does not
    (
        "stranger_invite",
        {"organizer": {"emailAddress": {"address": "boss@company.test.evil.example"}}},
        "company.test",
        "all",
        False,
        "not_accepted",
    ),
    # a reply we cannot read from someone else's invite is unanswered too
    (
        "stranger_invite",
        {"responseStatus": None},
        None,
        "all",
        False,
        "not_accepted",
    ),
    # a lost organiser flag on an invite with other people never turns it into "mine"
    (
        "stranger_invite",
        {"isOrganizer": None, "responseStatus": None},
        None,
        "all",
        False,
        "not_accepted",
    ),
]


@pytest.mark.parametrize(
    "name,changes,domains,rule,teams,expected",
    TABLE,
    ids=[f"{t[0]}-{i}-{t[3]}-{t[5]}" for i, t in enumerate(TABLE)],
)
def test_the_normalised_event_decides_as_the_table_says(
    name, changes, domains, rule, teams, expected
):
    changes = {k: v for k, v in changes.items()}
    raw = fx(name, **changes)
    if raw.get("isOrganizer") is None:
        raw.pop("isOrganizer", None)
    event = parse_event(raw, tenant_domains=domains)
    assert decide(event, rule=rule, teams=teams) == expected


def test_a_per_meeting_switch_beats_the_rule_as_for_google():
    event = parsed("stranger_invite")
    assert decide(event, override=True) == "ok"
    assert decide(parsed("zoom_in_body"), override=False) == "override_off"


def test_the_event_is_over_and_too_long_checks_use_the_utc_instants():
    past = parse_event(
        fx(
            "zoom_in_body",
            start={"dateTime": "2026-11-09T07:00:00.0000000", "timeZone": "UTC"},
            end={"dateTime": "2026-11-09T08:00:00.0000000", "timeZone": "UTC"},
        )
    )
    assert decide(past) == "ended"
    long = parse_event(
        fx(
            "zoom_in_body",
            start={"dateTime": "2026-11-12T10:00:00.0000000", "timeZone": "UTC"},
            end={"dateTime": "2026-11-12T17:00:00.0000000", "timeZone": "UTC"},
        )
    )
    assert long.ends_at - long.starts_at > timedelta(hours=6) and decide(long) == "too_long"


# ---- dispatch: the sync reads a Microsoft connection with this parser ----


@pytest.fixture
def microsoft_row(monkeypatch):
    """A temporary `microsoft` registry row (M3 adds the real one with its routes and source)."""
    row = Provider(
        name="microsoft",
        parse_event=lambda raw, conn: parse_event(
            raw, tenant_domains=conn.tenant_domains if conn.account_kind == "work" else None
        ),
        granted_features=lambda conn: {"calendar"},
        enabled=lambda settings: True,
        make_source=lambda settings: pytest.fail("not built here"),
        flow_cookie="calendar_flow_ms",
        flow_cookie_path="/api/calendar/microsoft",
    )
    monkeypatch.setitem(PROVIDERS, "microsoft", row)


def test_the_registry_parses_a_microsoft_connections_events_with_the_graph_parser(microsoft_row):
    conn = CalendarConnection(
        user_id=1, provider="microsoft", account_kind="work", tenant_domains="company.test"
    )
    event = calendar_providers.parse(
        fx("stranger_invite", organizer={"emailAddress": {"address": "boss@company.test"}}), conn
    )
    assert event.organizer_same_org is True and event.platform == "zoom"
    personal = CalendarConnection(
        user_id=1, provider="microsoft", account_kind="personal", tenant_domains="company.test"
    )
    assert (
        calendar_providers.parse(
            fx("stranger_invite", organizer={"emailAddress": {"address": "boss@company.test"}}),
            personal,
        ).organizer_same_org
        is False
    )


def test_a_graph_batch_becomes_rows_and_joins_through_apply_batch(session, microsoft_row):
    """End to end through the sync's own `_parse_all`: a Zoom-in-body event is joined, a stranger's
    unanswered invite waits, an all-day event and a cancelled one are not joined, a Teams event is
    "cannot join" while the Teams switch is off, and an event in a Windows zone is skipped as
    unreadable without touching the others."""
    user = make_user(session, "ada@example.test")
    user_settings = UserSettings(user_id=user.id)
    conn = CalendarConnection(
        user_id=user.id, provider="microsoft", account_kind="personal", tenant_id="t" * 8
    )
    session.add_all([user_settings, conn])
    session.commit()
    start = NOW + timedelta(hours=1)

    def at(name, event_id, **changes):
        times = {
            "start": {"dateTime": start.strftime("%Y-%m-%dT%H:%M:%S.0000000"), "timeZone": "UTC"},
            "end": {
                "dateTime": (start + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%S.0000000"),
                "timeZone": "UTC",
            },
        }
        return fx(name, id=event_id, **{**times, **changes})

    raw = [
        at("zoom_in_body", "e-zoom"),
        at("stranger_invite", "e-stranger"),
        at("teams_work", "e-teams"),
        at("all_day", "e-allday"),
        at("cancelled", "e-cancelled"),
        at(
            "zoom_in_body",
            "e-windows-zone",
            start={
                "dateTime": "2026-11-09T10:00:00.0000000",
                "timeZone": "W. Europe Standard Time",
            },
        ),
    ]
    result = gcal_sync.apply_batch(
        session,
        conn,
        SyncBatch(raw_events=raw, next_sync_token=None, complete=True, full=True),
        user_settings,
        now=NOW,
        settings=make_settings(autojoin_lead_seconds=60),
    )
    rows = {e.source_event_id: e for e in session.query(CalendarEvent).filter_by(user_id=user.id)}
    # a cancelled event never seen before has nothing to cancel: no row
    assert set(rows) == {"e-zoom", "e-stranger", "e-teams", "e-allday"}
    assert result.unreadable == 1
    joined = {j.calendar_event_id for j in session.query(ScheduledJoin).filter_by(user_id=user.id)}
    assert joined == {rows["e-zoom"].id}
    assert rows["e-teams"].platform == "teams"
    assert rows["e-stranger"].self_response == "needsAction"
