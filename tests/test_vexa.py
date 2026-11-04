import json
import time
from pathlib import Path

import httpx
import pytest

from app.config import get_settings
from app.vexa import (
    UnsupportedMeetingUrl,
    VexaClient,
    VexaDuplicateBot,
    VexaError,
    VexaSendUncertain,
    parse_meeting_url,
)

FIXTURE = Path(__file__).parent / "fixtures" / "vexa_transcript.json"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("VEXA_BASE_URL", "http://vexa.test")
    monkeypatch.setenv("VEXA_API_KEY", "test-key")
    monkeypatch.setenv("BOT_NAME", "Test Bot")
    get_settings.cache_clear()
    yield VexaClient()
    get_settings.cache_clear()


def fixture_data():
    return json.loads(FIXTURE.read_text())


def test_parse_zoom_url():
    assert parse_meeting_url("https://us05web.zoom.us/j/123456789?pwd=abc") == (
        "zoom",
        "123456789",
    )


def test_parse_plain_zoom_url():
    assert parse_meeting_url("https://zoom.us/j/987654321") == ("zoom", "987654321")


def test_parse_meet_url():
    assert parse_meeting_url("https://meet.google.com/abc-defg-hij") == (
        "google_meet",
        "abc-defg-hij",
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/meeting",
        "https://zoom.us.evil.example/j/1",
        "https://evilzoom.us/j/1",
        "https://meet.google.com.evil.example/abc-defg-hij",
        "https://zoom.us/",
        "https://meet.google.com/",
        "not a url",
        "",
        "https://user:pw@zoom.us/j/1",
        "https://zoom.us@evil.example/j/1",
        "https://zoom.us:8443/j/1",
        "https://zoom.us:badport/j/1",
        "https://zoom.us\\@evil.example/j/1",
        "https://zoom.us/j/1\n2",
        "https://zoom.us/j/1 2",
        "https://meet.google.com/abc-defg-hij\x00",
        "ftp://zoom.us/j/1",
    ],
)
def test_rejects_other_urls(url):
    with pytest.raises(UnsupportedMeetingUrl):
        parse_meeting_url(url)


def test_send_bot_posts_full_url_and_returns_id(httpx_mock, client):
    httpx_mock.add_response(
        method="POST",
        url="http://vexa.test/bots",
        status_code=201,
        json={
            "id": 5,
            "platform": "zoom",
            "native_meeting_id": "123456789",
            "status": "requested",
            "failure_stage": None,
            "completion_reason": None,
        },
    )
    url = "https://us05web.zoom.us/j/123456789?pwd=abc"
    assert client.send_bot(url) == 5
    req = httpx_mock.get_requests()[0]
    assert req.headers["X-API-Key"] == "test-key"
    assert json.loads(req.content) == {
        "meeting_url": url,
        "bot_name": "Test Bot",
        "automatic_leave": {"max_time_left_alone": 300_000},
    }


def _sent_body(httpx_mock, client) -> dict:
    httpx_mock.add_response(
        method="POST", url="http://vexa.test/bots", status_code=201, json={"id": 1}
    )
    client.send_bot("https://meet.google.com/abc-defg-hij")
    return json.loads(httpx_mock.get_requests()[0].content)


def test_send_bot_asks_vexa_to_leave_after_the_configured_silence_in_milliseconds(
    httpx_mock, monkeypatch
):
    monkeypatch.setenv("VEXA_BASE_URL", "http://vexa.test")
    monkeypatch.setenv("VEXA_API_KEY", "test-key")
    monkeypatch.setenv("BOT_LEFT_ALONE_SECONDS", "120")
    get_settings.cache_clear()
    try:
        body = _sent_body(httpx_mock, VexaClient())
    finally:
        get_settings.cache_clear()
    assert body["automatic_leave"] == {"max_time_left_alone": 120_000}


@pytest.mark.parametrize("value", ["0", "-5"])
def test_a_window_of_zero_or_less_leaves_vexas_own_default_in_place(httpx_mock, monkeypatch, value):
    # Vexa answers 422 to a window that is not a positive integer, which would stop every send.
    monkeypatch.setenv("VEXA_BASE_URL", "http://vexa.test")
    monkeypatch.setenv("VEXA_API_KEY", "test-key")
    monkeypatch.setenv("BOT_LEFT_ALONE_SECONDS", value)
    get_settings.cache_clear()
    try:
        body = _sent_body(httpx_mock, VexaClient())
    finally:
        get_settings.cache_clear()
    assert "automatic_leave" not in body


@pytest.mark.parametrize(
    ("raw", "canonical"),
    [
        (
            "HTTP://US05WEB.Zoom.us/j/123456789?pwd=abc&x=1#frag",
            "https://us05web.zoom.us/j/123456789?pwd=abc",
        ),
        ("https://zoom.us/j/123456789?other=1", "https://zoom.us/j/123456789"),
        ("https://meet.google.com/abc-defg-hij?authuser=1", "https://meet.google.com/abc-defg-hij"),
    ],
)
def test_send_bot_sends_canonical_url(httpx_mock, client, raw, canonical):
    httpx_mock.add_response(method="POST", url="http://vexa.test/bots", json={"id": 1})
    client.send_bot(raw)
    assert json.loads(httpx_mock.get_requests()[0].content)["meeting_url"] == canonical


def test_send_bot_rejects_unsupported_url_without_request(httpx_mock, client):
    with pytest.raises(UnsupportedMeetingUrl):
        client.send_bot("https://example.com/x")
    assert httpx_mock.get_requests() == []


def test_send_bot_http_error(httpx_mock, client):
    httpx_mock.add_response(method="POST", url="http://vexa.test/bots", status_code=500)
    with pytest.raises(VexaError):
        client.send_bot("https://meet.google.com/abc-defg-hij")


def test_send_bot_refused_by_the_host_is_a_plain_error_not_an_uncertain_one(httpx_mock, client):
    httpx_mock.add_response(method="POST", url="http://vexa.test/bots", status_code=422)
    with pytest.raises(VexaError) as caught:
        client.send_bot("https://meet.google.com/abc-defg-hij")
    assert not isinstance(caught.value, VexaSendUncertain | VexaDuplicateBot)


def test_send_bot_conflict_is_a_duplicate_bot(httpx_mock, client):
    """Assumed, not captured: Vexa answers 409 when the meeting already has a bot."""
    httpx_mock.add_response(method="POST", url="http://vexa.test/bots", status_code=409)
    with pytest.raises(VexaDuplicateBot):
        client.send_bot("https://meet.google.com/abc-defg-hij")


@pytest.mark.parametrize(
    "error", [httpx.ReadTimeout("slow"), httpx.WriteError("cut"), httpx.RemoteProtocolError("eof")]
)
def test_send_bot_timeout_or_transport_error_is_uncertain(httpx_mock, client, error):
    """The host may have taken the bot before the answer was lost."""
    httpx_mock.add_exception(error, method="POST", url="http://vexa.test/bots")
    with pytest.raises(VexaSendUncertain):
        client.send_bot("https://meet.google.com/abc-defg-hij")


@pytest.mark.parametrize("error", [httpx.ConnectError("down"), httpx.ConnectTimeout("no route")])
def test_send_bot_that_never_connected_is_a_clear_no_not_uncertain(httpx_mock, client, error):
    """No connection was made, so nothing reached the host: no stop, no misleading error log."""
    httpx_mock.add_exception(error, method="POST", url="http://vexa.test/bots")
    with pytest.raises(VexaError) as caught:
        client.send_bot("https://meet.google.com/abc-defg-hij")
    assert not isinstance(caught.value, VexaSendUncertain | VexaDuplicateBot)


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_send_bot_server_error_is_uncertain_a_gateway_may_answer_after_the_bot_manager_acted(
    httpx_mock, client, status
):
    httpx_mock.add_response(method="POST", url="http://vexa.test/bots", status_code=status)
    with pytest.raises(VexaSendUncertain):
        client.send_bot("https://meet.google.com/abc-defg-hij")


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_send_bot_client_error_is_a_clear_no(httpx_mock, client, status):
    httpx_mock.add_response(method="POST", url="http://vexa.test/bots", status_code=status)
    with pytest.raises(VexaError) as caught:
        client.send_bot("https://meet.google.com/abc-defg-hij")
    assert not isinstance(caught.value, VexaSendUncertain | VexaDuplicateBot)


def test_send_bot_unreadable_success_is_uncertain(httpx_mock, client):
    httpx_mock.add_response(method="POST", url="http://vexa.test/bots", json={"no": "id"})
    with pytest.raises(VexaSendUncertain):  # a 2xx we could not read: the bot is probably there
        client.send_bot("https://meet.google.com/abc-defg-hij")


def test_send_bot_has_a_total_deadline_however_slowly_the_host_answers(httpx_mock, monkeypatch):
    monkeypatch.setenv("VEXA_BASE_URL", "http://vexa.test")
    get_settings.cache_clear()
    slow = VexaClient(send_deadline=0.2)

    def trickle(request):
        time.sleep(0.8)  # httpx's own timeout is per phase, so a trickling host never trips it
        return httpx.Response(200, json={"id": 1})

    httpx_mock.add_callback(trickle, method="POST", url="http://vexa.test/bots")
    began = time.monotonic()
    with pytest.raises(VexaSendUncertain):
        slow.send_bot("https://meet.google.com/abc-defg-hij")
    assert time.monotonic() - began < 0.7
    time.sleep(0.8)  # let the abandoned request finish before the mock is torn down
    get_settings.cache_clear()


def test_the_default_send_deadline_is_at_most_thirty_seconds():
    from app import vexa

    assert vexa.SEND_DEADLINE_SECONDS <= 30


def test_get_meeting_parses_fixture(httpx_mock, client):
    httpx_mock.add_response(
        url="http://vexa.test/transcripts/google_meet/abc-defg-hij", json=fixture_data()
    )
    meeting = client.get_meeting("google_meet", "abc-defg-hij")
    assert meeting.status == "completed"
    assert meeting.completion_reason == "stopped"
    assert meeting.recording_id == 7
    assert meeting.media_id == 9
    assert meeting.recording_status == "completed"
    assert [s["speaker"] for s in meeting.segments] == ["Alice Example", "Bob Sample"]
    assert httpx_mock.get_requests()[0].headers["X-API-Key"] == "test-key"


def test_get_meeting_by_id_uses_the_vexa_id_not_the_meeting_code(httpx_mock, client):
    httpx_mock.add_response(url="http://vexa.test/transcripts/by-id/4242", json=fixture_data())
    meeting = client.get_meeting_by_id("4242")
    assert meeting.status == "completed"
    assert meeting.recording_id == 7 and meeting.media_id == 9
    assert [s["speaker"] for s in meeting.segments] == ["Alice Example", "Bob Sample"]
    request = httpx_mock.get_requests()[0]
    assert request.url.path == "/transcripts/by-id/4242"
    assert request.headers["X-API-Key"] == "test-key"


def test_get_meeting_by_id_http_error(httpx_mock, client):
    httpx_mock.add_response(url="http://vexa.test/transcripts/by-id/4242", status_code=404)
    with pytest.raises(VexaError):
        client.get_meeting_by_id(4242)


def test_a_404_for_a_meeting_is_told_apart_from_other_errors(httpx_mock, client):
    from app.vexa import VexaNotFound

    httpx_mock.add_response(url="http://vexa.test/transcripts/by-id/4242", status_code=404)
    with pytest.raises(VexaNotFound):
        client.get_meeting_by_id(4242)  # a VexaError too: callers that retry are unchanged
    httpx_mock.add_response(url="http://vexa.test/transcripts/by-id/4243", status_code=500)
    with pytest.raises(VexaError) as other:
        client.get_meeting_by_id(4243)
    assert not isinstance(other.value, VexaNotFound)


def test_get_meeting_by_id_rejects_non_numeric_id_without_request(httpx_mock, client):
    with pytest.raises(ValueError):
        client.get_meeting_by_id("../bots")
    assert httpx_mock.get_requests() == []


def test_get_meeting_without_recording(httpx_mock, client):
    body = fixture_data()
    body["recordings"] = []
    body["data"] = {}
    body["status"] = "failed"
    httpx_mock.add_response(url="http://vexa.test/transcripts/zoom/1", json=body)
    meeting = client.get_meeting("zoom", "1")
    assert meeting.status == "failed"
    assert meeting.recording_id is None
    assert meeting.media_id is None
    assert meeting.recording_status is None
    assert meeting.completion_reason is None


@pytest.mark.parametrize(
    "media_files", [[], [{"id": 3, "type": "video", "format": "mp4", "file_size_bytes": 1}]]
)
def test_get_meeting_recording_without_audio(httpx_mock, client, media_files):
    body = fixture_data()
    body["recordings"] = [{"id": 7, "status": "in_progress", "media_files": media_files}]
    httpx_mock.add_response(url="http://vexa.test/transcripts/zoom/1", json=body)
    meeting = client.get_meeting("zoom", "1")
    assert meeting.recording_id is None
    assert meeting.media_id is None
    assert meeting.recording_status == "in_progress"


def test_get_meeting_unknown_status_and_missing_fields(httpx_mock, client):
    httpx_mock.add_response(
        url="http://vexa.test/transcripts/zoom/1", json={"status": "brand_new_state"}
    )
    meeting = client.get_meeting("zoom", "1")
    assert meeting.status == "brand_new_state"
    assert meeting.segments == []
    assert meeting.completion_reason is None


def test_get_meeting_http_error(httpx_mock, client):
    httpx_mock.add_response(url="http://vexa.test/transcripts/zoom/1", status_code=404)
    with pytest.raises(VexaError):
        client.get_meeting("zoom", "1")


def test_connection_error_is_vexa_error(httpx_mock, client):
    httpx_mock.add_exception(httpx.ConnectError("down"))
    with pytest.raises(VexaError):
        client.get_meeting("zoom", "1")


def test_download_uses_raw_endpoint(httpx_mock, client, tmp_path):
    httpx_mock.add_response(
        url="http://vexa.test/recordings/1/media/2/raw?type=audio", content=b"RIFFaudio"
    )
    dest = tmp_path / "out.wav"
    client.download_audio(1, 2, dest)
    assert dest.read_bytes() == b"RIFFaudio"
    req = httpx_mock.get_requests()[0]
    assert req.url.path == "/recordings/1/media/2/raw"
    assert "master" not in str(req.url)
    assert req.headers["X-API-Key"] == "test-key"


def test_download_follows_redirects(httpx_mock, client, tmp_path):
    httpx_mock.add_response(
        url="http://vexa.test/recordings/1/media/2/raw?type=audio",
        status_code=302,
        headers={"Location": "http://storage.test/blob"},
    )
    httpx_mock.add_response(url="http://storage.test/blob", content=b"data")
    dest = tmp_path / "out.wav"
    client.download_audio(1, 2, dest)
    assert dest.read_bytes() == b"data"
    first, second = httpx_mock.get_requests()
    assert first.headers["X-API-Key"] == "test-key"
    assert "X-API-Key" not in second.headers


def test_download_error_leaves_no_file(httpx_mock, client, tmp_path):
    httpx_mock.add_response(
        url="http://vexa.test/recordings/1/media/2/raw?type=audio", status_code=404
    )
    dest = tmp_path / "out.wav"
    with pytest.raises(VexaError):
        client.download_audio(1, 2, dest)
    assert not dest.exists()


def test_get_meeting_non_object_body(httpx_mock, client):
    httpx_mock.add_response(url="http://vexa.test/transcripts/zoom/1", json=[1, 2])
    with pytest.raises(VexaError):
        client.get_meeting("zoom", "1")


def test_send_bot_non_object_body(httpx_mock, client):
    httpx_mock.add_response(method="POST", url="http://vexa.test/bots", json=[1])
    with pytest.raises(VexaError):
        client.send_bot("https://meet.google.com/abc-defg-hij")


def test_download_never_sends_key_on_scheme_downgrade(httpx_mock, monkeypatch, tmp_path):
    monkeypatch.setenv("VEXA_BASE_URL", "https://vexa.test")
    monkeypatch.setenv("VEXA_API_KEY", "test-key")
    get_settings.cache_clear()
    try:
        c = VexaClient()
        httpx_mock.add_response(
            url="https://vexa.test/recordings/1/media/2/raw?type=audio",
            status_code=302,
            headers={"Location": "http://vexa.test/blob"},
        )
        httpx_mock.add_response(url="http://vexa.test/blob", content=b"d")
        c.download_audio(1, 2, tmp_path / "o.wav")
    finally:
        get_settings.cache_clear()
    first, second = httpx_mock.get_requests()
    assert first.headers["X-API-Key"] == "test-key"
    assert "X-API-Key" not in second.headers


def test_download_redirect_without_location(httpx_mock, client, tmp_path):
    httpx_mock.add_response(
        url="http://vexa.test/recordings/1/media/2/raw?type=audio", status_code=302
    )
    dest = tmp_path / "out.wav"
    with pytest.raises(VexaError):
        client.download_audio(1, 2, dest)
    assert not dest.exists()


def test_download_oserror_becomes_vexa_error_and_cleans_up(
    httpx_mock, client, tmp_path, monkeypatch
):
    httpx_mock.add_response(
        url="http://vexa.test/recordings/1/media/2/raw?type=audio", content=b"abc"
    )
    dest = tmp_path / "out.wav"

    real_open = Path.open

    def boom(self, *a, **k):
        with real_open(self, "wb") as f:
            f.write(b"partial")
        raise OSError("disk full")

    monkeypatch.setattr(Path, "open", boom)
    with pytest.raises(VexaError):
        client.download_audio(1, 2, dest)
    assert not dest.exists()


def _fetch_with(httpx_mock, client, **changes):
    body = {**fixture_data(), **changes}
    httpx_mock.add_response(url="http://vexa.test/transcripts/zoom/1", json=body)
    return client.get_meeting("zoom", "1")


@pytest.mark.parametrize(
    "changes",
    [
        {"recordings": "oops"},
        {"recordings": {"id": 7}},
        {"recordings": ["oops"]},
        {"recordings": [{"id": 7, "status": "completed", "media_files": "oops"}]},
        {"recordings": [{"id": 7, "status": "completed", "media_files": ["oops"]}]},
        {
            "recordings": [
                {"id": "7", "status": "completed", "media_files": [{"id": 9, "type": "audio"}]}
            ]
        },
        {"segments": "oops"},
        {"segments": ["oops"]},
        {"segments": {"speaker": "Ada"}},
        {"data": "oops"},
        {"data": ["oops"]},
        {"data": {"completion_reason": ["stopped"]}},
    ],
)
def test_get_meeting_wrong_field_types_raise_vexa_error(httpx_mock, client, changes):
    with pytest.raises(VexaError):
        _fetch_with(httpx_mock, client, **changes)


def test_get_meeting_null_fields_are_treated_as_absent(httpx_mock, client):
    meeting = _fetch_with(httpx_mock, client, recordings=None, segments=None, data=None)
    assert meeting.segments == [] and meeting.recording_id is None
    assert meeting.completion_reason is None


# Captured live on 2026-11-02 when Zoom refused the bot.
ZOOM_RTMS_REASON = (
    "[Zoom Web] zoom_requires_rtms: meeting/account blocks automa"
    "ted browser joins and requires Zoom RTMS (Realtime Media Streams); route to the RTMS path"
)


def test_get_meeting_reads_the_failure_reason(httpx_mock, client):
    data = {"reason": ZOOM_RTMS_REASON, "completion_reason": "awaiting_admission_rejected"}
    meeting = _fetch_with(httpx_mock, client, status="failed", data=data)
    assert meeting.failure_reason == ZOOM_RTMS_REASON


def test_get_meeting_falls_back_to_the_last_error_reason(httpx_mock, client):
    data = {"last_error": {"reason": "  bot crashed  ", "exit_code": 1}}
    meeting = _fetch_with(httpx_mock, client, status="failed", data=data)
    assert meeting.failure_reason == "bot crashed"


@pytest.mark.parametrize(
    "data", [{}, {"reason": 42}, {"reason": "   "}, {"last_error": "oops"}, {"last_error": {}}]
)
def test_get_meeting_failure_reason_absent_or_odd_is_none(httpx_mock, client, data):
    assert _fetch_with(httpx_mock, client, data=data).failure_reason is None


def test_get_meeting_failure_reason_is_capped(httpx_mock, client):
    meeting = _fetch_with(httpx_mock, client, data={"reason": "x" * 1000})
    assert len(meeting.failure_reason) == 300


def test_delete_recording_calls_the_recording_route(httpx_mock, client):
    httpx_mock.add_response(method="DELETE", url="http://vexa.test/recordings/7", json={})
    client.delete_recording(7)
    request = httpx_mock.get_requests()[0]
    assert request.headers["X-API-Key"] == "test-key"


def test_delete_recording_not_found_means_already_gone(httpx_mock, client):
    httpx_mock.add_response(method="DELETE", url="http://vexa.test/recordings/7", status_code=404)
    client.delete_recording(7)


@pytest.mark.parametrize("status", [409, 500, 401])
def test_delete_recording_failures_raise(httpx_mock, client, status):
    httpx_mock.add_response(
        method="DELETE", url="http://vexa.test/recordings/7", status_code=status
    )
    with pytest.raises(VexaError):
        client.delete_recording(7)


def test_delete_recording_connection_error(httpx_mock, client):
    httpx_mock.add_exception(httpx.ConnectError("down"))
    with pytest.raises(VexaError):
        client.delete_recording(7)


def test_delete_recording_rejects_non_numeric_id_without_request(httpx_mock, client):
    with pytest.raises(ValueError):
        client.delete_recording("../bots")
    assert httpx_mock.get_requests() == []


def test_stop_bot_deletes_the_bot_for_the_meeting_code(httpx_mock, client):
    httpx_mock.add_response(
        method="DELETE", url="http://vexa.test/bots/google_meet/abc-defg-hij", json={}
    )
    client.stop_bot("google_meet", "abc-defg-hij")
    assert httpx_mock.get_requests()[0].headers["X-API-Key"] == "test-key"


def test_stop_bot_no_active_meeting_counts_as_stopped(httpx_mock, client):
    httpx_mock.add_response(
        method="DELETE", url="http://vexa.test/bots/zoom/12345678901", status_code=404
    )
    client.stop_bot("zoom", "12345678901")


@pytest.mark.parametrize("status", [503, 500])
def test_stop_bot_failure_raises(httpx_mock, client, status):
    httpx_mock.add_response(
        method="DELETE", url="http://vexa.test/bots/zoom/12345678901", status_code=status
    )
    with pytest.raises(VexaError):
        client.stop_bot("zoom", "12345678901")


def test_stop_bot_rejects_odd_platform_or_id_without_request(httpx_mock, client):
    with pytest.raises(ValueError):
        client.stop_bot("zoom", "../x")
    with pytest.raises(ValueError):
        client.stop_bot("teams", "1")
    assert httpx_mock.get_requests() == []
