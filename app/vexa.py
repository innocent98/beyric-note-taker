"""Client for the self-hosted Vexa meeting-bot API."""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

from app.config import get_settings

ZOOM_PATH = re.compile(r"^/(?:j|wc/join)/(\d+)/?$")
MAX_REDIRECTS = 5
MEET_PATH = re.compile(r"^/([a-z]{3}-[a-z]{4}-[a-z]{3})/?$")


class VexaError(Exception):
    """HTTP failure or connection problem talking to Vexa. Treat as retryable."""


class UnsupportedMeetingUrl(ValueError):
    """The URL is not a Google Meet or Zoom meeting link."""


@dataclass
class VexaMeeting:
    status: str
    segments: list[dict[str, Any]] = field(default_factory=list)
    recording_id: int | None = None
    media_id: int | None = None
    recording_status: str | None = None
    completion_reason: str | None = None


def _parse(url: str) -> tuple[str, str, str]:
    """Return (platform, native_id, canonical_url), rejecting ambiguous input."""
    if not isinstance(url, str):
        raise UnsupportedMeetingUrl("not a string")
    url = url.strip()
    if "\\" in url or any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in url):
        raise UnsupportedMeetingUrl("unsupported characters in meeting URL")
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError as exc:
        raise UnsupportedMeetingUrl("malformed meeting URL") from exc
    if parts.scheme not in ("http", "https") or "@" in parts.netloc or port is not None:
        raise UnsupportedMeetingUrl("unsupported meeting URL")
    if host == "zoom.us" or host.endswith(".zoom.us"):
        match = ZOOM_PATH.match(parts.path)
        if match:
            canonical = f"https://{host}{parts.path}"
            pwd = parse_qs(parts.query).get("pwd")
            if pwd and pwd[0]:
                canonical += "?" + urlencode({"pwd": pwd[0]})
            return "zoom", match.group(1), canonical
    elif host == "meet.google.com":
        match = MEET_PATH.match(parts.path)
        if match:
            return "google_meet", match.group(1), f"https://{host}{parts.path}"
    raise UnsupportedMeetingUrl("unsupported meeting URL")


def parse_meeting_url(url: str) -> tuple[str, str]:
    """Return (platform, native_meeting_id) for a Zoom or Google Meet URL."""
    platform, native_id, _ = _parse(url)
    return platform, native_id


class VexaClient:
    def __init__(self, timeout: float = 30.0) -> None:
        settings = get_settings()
        self._base_url = settings.vexa_base_url.rstrip("/")
        self._headers = {"X-API-Key": settings.vexa_api_key}
        self._bot_name = settings.bot_name
        self._timeout = timeout

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self._base_url,
            headers=self._headers,
            timeout=self._timeout,
            follow_redirects=True,
        )

    def send_bot(self, meeting_url: str) -> int:
        _, _, canonical = _parse(meeting_url)  # rebuilt from parsed parts; keeps Zoom pwd
        payload = {"meeting_url": canonical, "bot_name": self._bot_name}
        try:
            with self._client() as client:
                response = client.post("/bots", json=payload)
                response.raise_for_status()
                return int(response.json()["id"])
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise VexaError(f"send_bot failed: {exc}") from exc

    def get_meeting(self, platform: str, native_id: str) -> VexaMeeting:
        """Look a meeting up by platform and meeting code. A reused link can match an older
        meeting, so prefer `get_meeting_by_id` whenever the Vexa id is known."""
        return self._fetch(f"/transcripts/{platform}/{native_id}")

    def get_meeting_by_id(self, vexa_meeting_id: str | int) -> VexaMeeting:
        """Look up exactly the meeting that `send_bot` returned the id of."""
        return self._fetch(f"/transcripts/by-id/{int(vexa_meeting_id)}")

    def _fetch(self, path: str) -> VexaMeeting:
        try:
            with self._client() as client:
                response = client.get(path)
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise VexaError(f"get_meeting failed: {exc}") from exc
        if not isinstance(body, dict):
            raise VexaError("get_meeting failed: response body is not a JSON object")

        recording_id = media_id = recording_status = None
        recordings = body.get("recordings") or []
        if recordings:
            recording = recordings[0]
            recording_status = recording.get("status")
            media_files = recording.get("media_files") or []
            audio = [m for m in media_files if m.get("type") == "audio"]
            if audio:
                recording_id = recording.get("id")
                media_id = audio[0].get("id")
        data = body.get("data") or {}
        return VexaMeeting(
            status=str(body.get("status", "")),
            segments=body.get("segments") or [],
            recording_id=recording_id,
            media_id=media_id,
            recording_status=recording_status,
            completion_reason=data.get("completion_reason"),
        )

    def download_audio(self, recording_id: int, media_id: int, path: Path) -> None:
        """Stream the raw audio to `path`.

        Redirects are followed by hand so the API key is only sent to the Vexa origin
        (same scheme and host), never to a storage host or over a downgrade to http.
        """
        path = Path(path)
        base = httpx.URL(self._base_url)
        url = httpx.URL(f"{self._base_url}/recordings/{recording_id}/media/{media_id}/raw")
        url = url.copy_merge_params({"type": "audio"})
        try:
            with httpx.Client(timeout=self._timeout) as client:
                for _ in range(MAX_REDIRECTS + 1):
                    same_origin = (url.scheme, url.netloc) == (base.scheme, base.netloc)
                    headers = self._headers if same_origin else {}
                    with client.stream("GET", url, headers=headers) as response:
                        if response.is_redirect:
                            location = response.headers.get("location")
                            if not location:
                                raise VexaError("download_audio failed: redirect without Location")
                            url = url.join(location)
                            continue
                        response.raise_for_status()
                        with path.open("wb") as out:
                            for chunk in response.iter_bytes():
                                out.write(chunk)
                        return
                raise VexaError("download_audio failed: too many redirects")
        except VexaError:
            path.unlink(missing_ok=True)
            raise
        except (httpx.HTTPError, OSError) as exc:
            path.unlink(missing_ok=True)
            raise VexaError(f"download_audio failed: {exc}") from exc
