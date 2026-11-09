"""`app/meeting_url.py` for Microsoft Teams links (T1): what is accepted, what is refused, the code
derived from a link (Teams has no short meeting code) and what is kept for the bot. Placeholder
tenant and thread values only."""

import re

import pytest

from app.meeting_url import UnsupportedMeetingUrl, canonical_meeting_url, parse_meeting_url

THREAD = "19%3ameeting_PLACEHOLDERPLACEHOLDERPLACEHOLDERPLACEH%40thread.v2"
CONTEXT = (
    "context=%7b%22Tid%22%3a%2200000000-0000-0000-0000-000000000000%22%2c"
    "%22Oid%22%3a%2211111111-1111-1111-1111-111111111111%22%7d"
)
JOIN = f"https://teams.microsoft.com/l/meetup-join/{THREAD}/0?{CONTEXT}"
SHORT = "https://teams.microsoft.com/meet/1234567890123?p=PLACEHOLDERpasscode"
LIVE = "https://teams.live.com/meet/9876543210?p=PLACEHOLDERpasscode"


def code(url):
    platform, native_id = parse_meeting_url(url)
    assert platform == "teams"
    return native_id


@pytest.mark.parametrize("url", [JOIN, SHORT, LIVE])
def test_a_teams_link_keeps_its_whole_path_and_query_for_the_bot(url):
    assert canonical_meeting_url(url) == url


def test_the_scheme_host_case_and_fragment_are_tidied_and_nothing_else():
    messy = JOIN.replace("https://teams.microsoft.com", "HTTP://Teams.Microsoft.COM") + "#frag"
    assert canonical_meeting_url(messy) == JOIN


def test_the_code_is_a_fixed_length_token_that_does_not_show_the_thread():
    native_id = code(JOIN)
    assert re.fullmatch(r"teams-[0-9a-f]{24}", native_id)
    assert "PLACEHOLDER" not in native_id and "thread" not in native_id


def test_one_meeting_gives_one_code_however_the_link_is_written():
    other_query = f"https://teams.microsoft.com/l/meetup-join/{THREAD}/0?context=%7b%7d&utm=x"
    raw_thread = "https://teams.microsoft.com/l/meetup-join/19:meeting_PLACEHOLDERPLACEHOLDERPLACEHOLDERPLACEH@thread.v2/0"
    assert code(JOIN) == code(other_query) == code(raw_thread)
    assert code(SHORT) == code(SHORT.split("?")[0]) == code(SHORT.replace("PLACEHOLDER", "OTHER"))


def test_different_meetings_and_different_hosts_give_different_codes():
    other_thread = JOIN.replace("PLACEHOLDERPLACE", "ANOTHERANOTHERAN")
    live_same_digits = "https://teams.live.com/meet/1234567890123?p=x"
    assert (
        len({code(JOIN), code(other_thread), code(SHORT), code(LIVE), code(live_same_digits)}) == 5
    )


def test_a_safe_links_wrapper_of_a_teams_link_is_read_and_the_inner_link_is_sent():
    from urllib.parse import quote

    wrapped = (
        "https://eur01.safelinks.protection.outlook.com/?url="
        f"{quote(JOIN, safe='')}&data=05%7c02%7cplaceholder&reserved=0"
    )
    assert parse_meeting_url(wrapped) == ("teams", code(JOIN))
    assert canonical_meeting_url(wrapped) == JOIN


@pytest.mark.parametrize(
    "url",
    [
        # not Teams' hosts
        "https://teams.microsoft.com.evil.example/meet/1234567890123",
        "https://evilteams.microsoft.com/meet/1234567890123",
        "https://teams.live.com.evil.example/meet/9876543210",
        "https://teams.microsoft.com@evil.example/meet/1234567890123",
        "https://user:pw@teams.microsoft.com/meet/1234567890123",
        "https://teams.microsoft.com:8443/meet/1234567890123",
        "https://teams.microsoft.com\\@evil.example/meet/1234567890123",
        "ftp://teams.microsoft.com/meet/1234567890123",
        # paths that are not a meeting
        "https://teams.microsoft.com/",
        "https://teams.microsoft.com/l/chat/0/0",
        "https://teams.microsoft.com/meet/abc",
        "https://teams.microsoft.com/meet/1234567890123/extra",
        "https://teams.microsoft.com/l/meetup-join/",
        "https://teams.live.com/l/meetup-join/19%3ameeting_x%40thread.v2/0",
        "https://teams.live.com/",
        # hostile characters and size
        "https://teams.microsoft.com/meet/1234567890123?p=a b",
        "https://teams.microsoft.com/meet/1234567890123?p=a\nb",
        "https://teams.microsoft.com/meet/1234567890123?p=<script>",
        'https://teams.microsoft.com/meet/1234567890123?p="x"',
        "https://teams.microsoft.com/meet/1234567890123?p=" + "a" * 2000,
        # a wrapper around something else, or around a wrapper
        "https://eur01.safelinks.protection.outlook.com/?url=https%3A%2F%2Fexample.com%2Fx",
        "https://eur01.safelinks.protection.outlook.com/?url=https%3A%2F%2Fmeet.google.com%2Fabc-defg-hij",
        "https://eur01.safelinks.protection.outlook.com/?data=x",
        "https://safelinks.protection.outlook.com.evil.example/?url=https%3A%2F%2Fteams.live.com%2Fmeet%2F9876543210",
    ],
)
def test_a_link_that_is_not_a_teams_meeting_is_refused(url):
    with pytest.raises(UnsupportedMeetingUrl):
        parse_meeting_url(url)
    with pytest.raises(UnsupportedMeetingUrl):
        canonical_meeting_url(url)


def test_a_wrapper_inside_a_wrapper_is_refused():
    from urllib.parse import quote

    inner = "https://eur01.safelinks.protection.outlook.com/?url=" + quote(JOIN, safe="")
    outer = "https://eur02.safelinks.protection.outlook.com/?url=" + quote(inner, safe="")
    with pytest.raises(UnsupportedMeetingUrl):
        parse_meeting_url(outer)


def test_meet_and_zoom_are_unchanged():
    assert parse_meeting_url("https://meet.google.com/abc-defg-hij") == (
        "google_meet",
        "abc-defg-hij",
    )
    assert canonical_meeting_url("https://zoom.us/j/123456789?pwd=a&x=1") == (
        "https://zoom.us/j/123456789?pwd=a"
    )
