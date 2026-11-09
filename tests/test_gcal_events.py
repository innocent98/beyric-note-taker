import dataclasses
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.gcal_events import NO_TIME, EventParseError, ParsedEvent, parse_event

FIXTURES = Path(__file__).parent / "fixtures" / "gcal"
PASSCODE = "PLACEHOLDERPASSCODE"
ZOOM_URL = f"https://us02web.zoom.us/j/81234567890?pwd={PASSCODE}"


def load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def event(**fields) -> dict:
    """A minimal confirmed event with a start and an end; `fields` override or add keys."""
    base = {
        "id": "evt-1",
        "status": "confirmed",
        "start": {"dateTime": "2026-11-04T10:00:00Z"},
        "end": {"dateTime": "2026-11-04T11:00:00Z"},
    }
    return {**base, **fields}


def entry(uri: str, kind: str = "video") -> dict:
    return {"entryPointType": kind, "uri": uri}


MEET = "https://meet.google.com/abc-defg-hij"
OTHER_MEET = "https://meet.google.com/zzz-yyyy-xxx"
TEAMS = "https://teams.microsoft.com/l/meetup-join/19%3ameeting_PLACEHOLDER%40thread.v2/0"


@pytest.mark.parametrize(
    "name",
    ["meet_event", "zoom_event", "teams_event", "all_day_event", "cancelled_stub", "focus_time"],
)
def test_every_fixture_parses_to_aware_utc_times(name):
    parsed = parse_event(load(name))
    for instant in (parsed.starts_at, parsed.ends_at):
        assert instant.utcoffset().total_seconds() == 0 and instant.tzinfo is not None


def test_a_real_shaped_meet_event_is_parsed_in_full():
    parsed = parse_event(load("meet_event"))
    assert parsed == ParsedEvent(
        source_event_id="synthmeet0001",
        recurring_event_id="synthmeet0001_series",
        title="Weekly planning",
        starts_at=datetime(2026, 11, 4, 9, 0, tzinfo=UTC),
        ends_at=datetime(2026, 11, 4, 9, 30, tzinfo=UTC),
        platform="google_meet",
        meeting_url=MEET,
        native_id="abc-defg-hij",
        organizer_self=False,
        attendee_count=3,
        self_response="needsAction",
        status="confirmed",
        event_type="default",
        all_day=False,
        organizer_same_org=False,
    )


def test_parsed_event_has_no_description_location_or_address_field():
    names = {f.name for f in dataclasses.fields(ParsedEvent)}
    assert names == {
        "source_event_id",
        "recurring_event_id",
        "title",
        "starts_at",
        "ends_at",
        "platform",
        "meeting_url",
        "native_id",
        "organizer_self",
        "attendee_count",
        "self_response",
        "status",
        "event_type",
        "all_day",
        "organizer_same_org",
    }
    with pytest.raises(dataclasses.FrozenInstanceError):
        parse_event(event()).title = "changed"


# ---- the link: entry point, then hangoutLink, then the location and description ----


def test_link_priority_entrypoint_then_hangoutlink_then_text():
    everywhere = event(
        conferenceData={"entryPoints": [entry(ZOOM_URL)]},
        hangoutLink=MEET,
        location=OTHER_MEET,
        description=f"Join {TEAMS}",
    )
    assert parse_event(everywhere).platform == "zoom"  # the entry point wins

    del everywhere["conferenceData"]
    got = parse_event(everywhere)
    assert (got.platform, got.native_id) == ("google_meet", "abc-defg-hij")  # then hangoutLink

    del everywhere["hangoutLink"]
    got = parse_event(everywhere)
    assert (got.platform, got.native_id) == ("google_meet", "zzz-yyyy-xxx")  # then the location

    del everywhere["location"]
    assert parse_event(everywhere).platform == "teams"  # then the description


def test_only_video_entry_points_count_and_unsupported_ones_are_skipped():
    raw = event(
        conferenceData={
            "entryPoints": [
                entry("tel:+1-555-0100", "phone"),
                entry("https://tel.meet/abc-defg-hij?pin=1", "more"),
                entry("https://example.test/webex/room"),  # video, but not a supported platform
                entry(MEET),
            ]
        }
    )
    assert parse_event(raw).native_id == "abc-defg-hij"
    only_phone = event(conferenceData={"entryPoints": [entry(MEET, "phone")]})
    assert parse_event(only_phone).platform is None


def test_first_supported_link_wins_when_event_has_meet_and_zoom():
    meet_first = event(conferenceData={"entryPoints": [entry(MEET), entry(ZOOM_URL)]})
    assert parse_event(meet_first).platform == "google_meet"
    zoom_first = event(conferenceData={"entryPoints": [entry(ZOOM_URL), entry(MEET)]})
    assert parse_event(zoom_first).platform == "zoom"
    in_text = event(description=f"Zoom: {ZOOM_URL} or Meet: {MEET}")
    assert parse_event(in_text).platform == "zoom"  # the first one in the text
    mixed = event(hangoutLink=MEET, description=f"Prefer Zoom: {ZOOM_URL}")
    assert parse_event(mixed).platform == "google_meet"  # a Meet link outranks text


def test_links_in_html_and_punctuation_are_found_cleanly():
    html = f'<p>Join: <a href="{MEET}">here</a>.</p>'
    assert parse_event(event(description=html)).meeting_url == MEET
    sentence = f"Call us on ({MEET}), thanks."
    assert parse_event(event(description=sentence)).meeting_url == MEET
    escaped = "https://us02web.zoom.us/j/81234567890?pwd=abc&amp;uname=Ada"
    got = parse_event(event(description=f'<a href="{escaped}">x</a>'))
    assert got.platform == "zoom" and "&amp;" not in got.meeting_url


def test_no_link_gives_platform_none():
    for raw in (
        event(),
        event(description="Lunch at the usual place", location="Cafe"),
        event(location="https://example.test/not-a-meeting"),
        event(description="https://meet.google.com/lookup/abc123"),  # not a meeting code
    ):
        got = parse_event(raw)
        assert (got.platform, got.meeting_url, got.native_id) == (None, None, None)


def test_the_stored_link_is_the_canonical_one_not_the_raw_text():
    raw = "http://us02web.zoom.us/j/81234567890?pwd=abc&utm_source=cal&uname=Ada#top"
    got = parse_event(event(description=f"Join {raw}"))
    assert got.meeting_url == "https://us02web.zoom.us/j/81234567890?pwd=abc"  # https, pwd only
    meet = parse_event(event(hangoutLink="http://meet.google.com/abc-defg-hij?authuser=1&hs=1"))
    assert meet.meeting_url == "https://meet.google.com/abc-defg-hij"
    padded = parse_event(event(conferenceData={"entryPoints": [entry(f"  {MEET}  ")]}))
    assert padded.meeting_url == MEET
    teams = parse_event(event(description="http://teams.microsoft.com/meet/9876543210?p=abc"))
    assert teams.meeting_url == "https://teams.microsoft.com/meet/9876543210?p=abc"
    assert parse_event(event(description="https://us02web.zoom.us/j/123456789")).meeting_url == (
        "https://us02web.zoom.us/j/123456789"
    )


# ---- Zoom passcodes (Review Focus 3) ----


def test_zoom_link_with_pwd_keeps_passcode_in_meeting_url_only():
    parsed = parse_event(load("zoom_event"))
    assert (parsed.platform, parsed.native_id) == ("zoom", "81234567890")
    assert parsed.meeting_url == ZOOM_URL  # Retry needs the passcode, so it stays here
    others = [
        str(getattr(parsed, f.name)) for f in dataclasses.fields(parsed) if f.name != "meeting_url"
    ]
    assert not any(PASSCODE in value for value in others)


def test_repr_and_log_of_parsed_event_never_contain_the_passcode(caplog):
    caplog.set_level(logging.DEBUG)
    parsed = parse_event(load("zoom_event"))
    logging.getLogger("test").info("parsed %s / %r / %s", parsed, parsed, [parsed])
    logging.getLogger("test").info(f"parsed {parsed} {parsed!r}")
    assert PASSCODE not in repr(parsed) and PASSCODE not in str(parsed)
    assert PASSCODE not in caplog.text and "pwd=" not in caplog.text
    assert parsed.meeting_url == ZOOM_URL  # repr hides it, the field still has it


def test_a_malformed_event_error_never_carries_the_event_body():
    raw = event(description=ZOOM_URL, start={"dateTime": "not a time"})
    with pytest.raises(EventParseError) as raised:
        parse_event(raw)
    assert PASSCODE not in f"{raised.value} {raised.value!r}"


# ---- Teams: a joinable link, with a derived meeting code (T1) ----


def test_teams_link_in_entrypoints_and_in_description_sets_platform_teams():
    in_entry = parse_event(event(conferenceData={"entryPoints": [entry(TEAMS)]}))
    in_text = parse_event(load("teams_event"))
    for parsed in (in_entry, in_text):
        assert parsed.platform == "teams" and parsed.native_id.startswith("teams-")
        assert parsed.meeting_url.startswith("https://teams.microsoft.com/l/meetup-join/")
    new_style = parse_event(event(description="https://teams.microsoft.com/meet/9876543210?p=abc"))
    live = parse_event(event(description="https://teams.live.com/meet/9876543210?p=abc"))
    assert new_style.platform == "teams" and live.platform == "teams"
    lookalike = parse_event(
        event(description="https://teams.microsoft.com.evil.test/l/meetup-join/x")
    )
    assert lookalike.platform is None


# ---- flags: all-day, event type, status, responses ----


def test_all_day_event_flagged():
    parsed = parse_event(load("all_day_event"))
    assert parsed.all_day is True
    assert parsed.starts_at == datetime(2026, 11, 6, tzinfo=UTC)
    assert parsed.ends_at == datetime(2026, 11, 7, tzinfo=UTC)
    assert parse_event(event()).all_day is False


@pytest.mark.parametrize("kind", ["focusTime", "outOfOffice", "workingLocation", "birthday"])
def test_event_types_focus_out_of_office_working_location_birthday_flagged(kind):
    assert parse_event(event(eventType=kind)).event_type == kind
    assert parse_event(load("focus_time")).event_type == "focusTime"
    assert parse_event(event()).event_type == "default"  # absent means a normal event


def test_cancelled_and_declined():
    assert parse_event(event(status="cancelled")).status == "cancelled"
    assert parse_event(event(status="tentative")).status == "tentative"
    declined = event(
        attendees=[
            {"email": "other@example.test", "responseStatus": "accepted"},
            {"email": "me@example.test", "self": True, "responseStatus": "declined"},
        ]
    )
    assert parse_event(declined).self_response == "declined"


def test_a_cancelled_stub_from_an_incremental_sync_has_no_times_and_still_parses():
    parsed = parse_event(load("cancelled_stub"))
    assert (parsed.source_event_id, parsed.status) == ("synthgone0001", "cancelled")
    assert parsed.starts_at == parsed.ends_at == NO_TIME
    assert (parsed.platform, parsed.title, parsed.attendee_count) == (None, "", 0)


def test_organizer_and_response_rules():
    cases = [
        ({"organizer": {"email": "me@example.test", "self": True}}, True),
        ({"organizer": {"email": "boss@example.test"}}, False),  # Google omits `self` when false
        ({}, None),  # no organizer at all: unknown
    ]
    for fields, expected in cases:
        assert parse_event(event(**fields)).organizer_self is expected
    no_self_attendee = event(attendees=[{"email": "a@example.test", "responseStatus": "accepted"}])
    assert parse_event(no_self_attendee).self_response is None
    odd = event(attendees=[{"self": True, "responseStatus": "something-new"}])
    assert parse_event(odd).self_response is None  # an unknown value is not guessed


def test_same_org_is_true_only_for_the_connected_workspace_domain():
    def organized_by(email, hosted_domain):
        raw = event(organizer={"email": email})
        return parse_event(raw, hosted_domain=hosted_domain).organizer_same_org

    assert organized_by("boss@corp.example", "corp.example") is True
    assert organized_by("Boss@CORP.Example", "Corp.Example") is True  # case never matters
    assert organized_by("boss@corp.example", None) is False  # a personal account has no domain
    assert organized_by("boss@gmail.com", None) is False  # never "same domain" for gmail.com
    assert organized_by("boss@sub.corp.example", "corp.example") is False  # exact, not a suffix
    assert organized_by("boss@evilcorp.example", "corp.example") is False
    assert organized_by("boss@corp.example.evil.test", "corp.example") is False
    assert organized_by("not-an-address", "corp.example") is False
    assert parse_event(event(), hosted_domain="corp.example").organizer_same_org is False
    assert "corp.example" not in repr(parse_event(event(organizer={"email": "a@corp.example"})))


# ---- time zones (Review Focus 2) ----


def test_offset_datetime_is_stored_as_utc():
    raw = event(
        start={"dateTime": "2026-11-03T10:00:00+01:00"},
        end={"dateTime": "2026-11-03T11:00:00+01:00"},
    )
    parsed = parse_event(raw)
    assert parsed.starts_at == datetime(2026, 11, 3, 9, 0, tzinfo=UTC)
    assert parsed.starts_at.utcoffset().total_seconds() == 0
    west = event(
        start={"dateTime": "2026-11-03T22:00:00-05:00"},
        end={"dateTime": "2026-11-03T23:00:00-05:00"},
    )
    assert parse_event(west).starts_at == datetime(2026, 11, 4, 3, 0, tzinfo=UTC)


def test_event_crossing_midnight_keeps_both_instants():
    raw = event(
        start={"dateTime": "2026-11-03T23:30:00+01:00"},
        end={"dateTime": "2026-11-04T00:30:00+01:00"},
    )
    parsed = parse_event(raw)
    assert parsed.starts_at == datetime(2026, 11, 3, 22, 30, tzinfo=UTC)
    assert parsed.ends_at == datetime(2026, 11, 3, 23, 30, tzinfo=UTC)
    assert (parsed.ends_at - parsed.starts_at).total_seconds() == 3600
    utc_midnight = event(
        start={"dateTime": "2026-11-03T23:30:00Z"}, end={"dateTime": "2026-11-04T00:30:00Z"}
    )
    assert parse_event(utc_midnight).ends_at == datetime(2026, 11, 4, 0, 30, tzinfo=UTC)


def test_timezone_field_is_ignored_in_favour_of_the_offset():
    # The offset is the truth; a `timeZone` that disagrees with it (or is junk) changes nothing.
    for zone in ("America/New_York", "Europe/Lagos", "Not/AZone"):
        raw = event(
            start={"dateTime": "2026-11-03T10:00:00+01:00", "timeZone": zone},
            end={"dateTime": "2026-11-03T11:00:00+01:00", "timeZone": zone},
        )
        assert parse_event(raw).starts_at == datetime(2026, 11, 3, 9, 0, tzinfo=UTC)


def test_dst_change_day_uses_each_instants_own_offset():
    # Europe/London leaves summer time on 2026-10-25: 01:30 happens twice, +01:00 then +00:00.
    raw = event(
        start={"dateTime": "2026-10-25T01:30:00+01:00"},
        end={"dateTime": "2026-10-25T01:30:00+00:00"},
    )
    parsed = parse_event(raw)
    assert parsed.starts_at == datetime(2026, 10, 25, 0, 30, tzinfo=UTC)
    assert parsed.ends_at == datetime(2026, 10, 25, 1, 30, tzinfo=UTC)


def test_a_time_with_no_offset_uses_its_zone_and_without_one_is_refused():
    zoned = event(
        start={"dateTime": "2026-11-03T10:00:00", "timeZone": "Africa/Lagos"},
        end={"dateTime": "2026-11-03T11:00:00", "timeZone": "Africa/Lagos"},
    )
    assert parse_event(zoned).starts_at == datetime(2026, 11, 3, 9, 0, tzinfo=UTC)
    for start in (
        {"dateTime": "2026-11-03T10:00:00"},
        {"dateTime": "2026-11-03T10:00:00", "timeZone": "Not/AZone"},
    ):
        with pytest.raises(EventParseError):  # a guessed time would send the bot at the wrong hour
            parse_event(event(start=start))


# ---- text and counts ----


@pytest.mark.parametrize(
    "title", ["Réunion équipe 🚀", "週次ミーティング", "Привет, мир", "مرحبا بالعالم"]
)
def test_emoji_and_non_latin_title_round_trip(title):
    assert parse_event(event(summary=title)).title == title
    assert json.loads(json.dumps(parse_event(event(summary=title)).title)) == title


def test_missing_title_becomes_empty_string():
    assert parse_event(event()).title == ""
    assert parse_event(event(summary=None)).title == ""


def test_attendee_count_is_a_number_and_addresses_are_dropped():
    raw = load("meet_event")
    parsed = parse_event(raw)
    assert parsed.attendee_count == 3 and isinstance(parsed.attendee_count, int)
    blob = " ".join(str(getattr(parsed, f.name)) for f in dataclasses.fields(parsed))
    assert "@" not in blob
    assert parse_event(event()).attendee_count == 0
    assert parse_event(event(attendees=None)).attendee_count == 0


# ---- malformed input ----


@pytest.mark.parametrize(
    "raw",
    [
        {},
        {"status": "confirmed"},  # no id
        event(id=""),
        event(id=123),
        event(start=None),
        {"id": "x", "status": "confirmed", "start": {"dateTime": "2026-11-04T10:00:00Z"}},  # no end
        event(start={}),
        event(end={"dateTime": "garbage"}),
        event(start={"date": "2026-13-45"}),
    ],
)
def test_a_malformed_event_raises_a_named_error(raw):
    with pytest.raises(EventParseError):
        parse_event(raw)
