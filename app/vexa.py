"""Client for the self-hosted Vexa meeting-bot API."""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

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


def parse_meeting_url(url: str) -> tuple[str, str]:
    """Return (platform, native_meeting_id) for a Zoom or Google Meet URL."""
    try:
        parts = urlsplit(url.strip())
        host = (parts.hostname or "").lower()
    except ValueError as exc:
        raise UnsupportedMeetingUrl(url) from exc
    if parts.scheme not in ("http", "https"):
        raise UnsupportedMeetingUrl(url)
    if host == "zoom.us" or host.endswith(".zoom.us"):
        match = ZOOM_PATH.match(parts.path)
        if match:
            return "zoom", match.group(1)
    elif host == "meet.google.com":
        match = MEET_PATH.match(parts.path)
        if match:
            return "google_meet", match.group(1)
    raise UnsupportedMeetingUrl(url)


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
        parse_meeting_url(meeting_url)  # validate only; the full URL keeps the Zoom pwd
        payload = {"meeting_url": meeting_url, "bot_name": self._bot_name}
        try:
            with self._client() as client:
                response = client.post("/bots", json=payload)
                response.raise_for_status()
                return int(response.json()["id"])
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise VexaError(f"send_bot failed: {exc}") from exc

    def get_meeting(self, platform: str, native_id: str) -> VexaMeeting:
        try:
            with self._client() as client:
                response = client.get(f"/transcripts/{platform}/{native_id}")
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise VexaError(f"get_meeting failed: {exc}") from exc

        recording_id = media_id = recording_status = None
        recordings = body.get("recordings") or []
        if recordings:
            recording = recordings[0]
            recording_id = recording.get("id")
            recording_status = recording.get("status")
            media_files = recording.get("media_files") or []
            audio = [m for m in media_files if m.get("type") == "audio"] or media_files
            if audio:
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

        Redirects are followed by hand so the API key is only sent to the Vexa host,
        never to a storage host the request is redirected to.
        """
        path = Path(path)
        url = httpx.URL(f"{self._base_url}/recordings/{recording_id}/media/{media_id}/raw")
        url = url.copy_merge_params({"type": "audio"})
        try:
            with httpx.Client(timeout=self._timeout) as client:
                for _ in range(MAX_REDIRECTS + 1):
                    same_host = url.netloc == httpx.URL(self._base_url).netloc
                    headers = self._headers if same_host else {}
                    with client.stream("GET", url, headers=headers) as response:
                        if response.is_redirect:
                            url = url.join(response.headers["location"])
                            continue
                        response.raise_for_status()
                        with path.open("wb") as out:
                            for chunk in response.iter_bytes():
                                out.write(chunk)
                        return
                raise VexaError("download_audio failed: too many redirects")
        except httpx.HTTPError as exc:
            path.unlink(missing_ok=True)
            raise VexaError(f"download_audio failed: {exc}") from exc
