import json
from pathlib import Path

import httpx
import pytest

from app.config import get_settings
from app.vexa import UnsupportedMeetingUrl, VexaClient, VexaError, parse_meeting_url

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
    assert json.loads(req.content) == {"meeting_url": url, "bot_name": "Test Bot"}


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
