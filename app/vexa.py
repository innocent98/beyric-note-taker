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


class VexaNotFound(VexaError):
    """Vexa answered 404 for a meeting: it does not know it. Still a `VexaError`, so every caller
    that retries is unchanged; `app/deletion.py` reads it as "nothing there to wait for"."""


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
    # Vexa's own words for why the bot failed, e.g. "[Zoom Web] zoom_requires_rtms: ...".
    failure_reason: str | None = None


MAX_FAILURE_REASON_CHARS = 300


def _failure_reason(data: dict) -> str | None:
    """Vexa's reason for a failed bot: `data.reason`, else `data.last_error.reason`. Anything
    that is not text is ignored rather than refused, because it is only ever shown, never used
    to decide."""
    reason = data.get("reason")
    if not isinstance(reason, str):
        last_error = data.get("last_error")
        reason = last_error.get("reason") if isinstance(last_error, dict) else None
    if not isinstance(reason, str) or not reason.strip():
        return None
    return reason.strip()[:MAX_FAILURE_REASON_CHARS]


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


def canonical_meeting_url(url: str) -> str:
    """The URL the bot is sent to: https, rebuilt from its parsed parts, the Zoom passcode kept and
    every other query parameter dropped. Raises `UnsupportedMeetingUrl` like `parse_meeting_url`."""
    return _parse(url)[2]


def _list_of_dicts(container: dict, key: str) -> list[dict[str, Any]]:
    """`container[key]` as a list of objects. Absent or null is an empty list; any other shape
    means Vexa changed its response, which we refuse to guess about."""
    value = container.get(key)
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise VexaError(f"get_meeting failed: '{key}' is not a list of JSON objects")
    return value


def _optional_int(container: dict, key: str) -> int | None:
    value = container.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise VexaError(f"get_meeting failed: '{key}' is not an integer")
    return value


class VexaClient:
    def __init__(self, timeout: float = 30.0) -> None:
        settings = get_settings()
        self._base_url = settings.vexa_base_url.rstrip("/")
        self._headers = {"X-API-Key": settings.vexa_api_key}
        self._bot_name = settings.bot_name
        self._left_alone_seconds = settings.bot_left_alone_seconds
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
        payload: dict[str, Any] = {"meeting_url": canonical, "bot_name": self._bot_name}
        if self._left_alone_seconds > 0:
            # Milliseconds, a positive integer: Vexa answers 422 to anything else.
            payload["automatic_leave"] = {"max_time_left_alone": self._left_alone_seconds * 1000}
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
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise VexaNotFound(f"get_meeting failed: {exc}") from exc
            raise VexaError(f"get_meeting failed: {exc}") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise VexaError(f"get_meeting failed: {exc}") from exc
        if not isinstance(body, dict):
            raise VexaError("get_meeting failed: response body is not a JSON object")

        recording_id = media_id = recording_status = None
        recordings = _list_of_dicts(body, "recordings")
        if recordings:
            recording = recordings[0]
            recording_status = recording.get("status")
            audio = [
                m for m in _list_of_dicts(recording, "media_files") if m.get("type") == "audio"
            ]
            if audio:
                recording_id = _optional_int(recording, "id")
                media_id = _optional_int(audio[0], "id")
        data = body.get("data")
        if data is None:
            data = {}
        if not isinstance(data, dict):
            raise VexaError("get_meeting failed: 'data' is not a JSON object")
        completion_reason = data.get("completion_reason")
        if completion_reason is not None and not isinstance(completion_reason, str):
            raise VexaError("get_meeting failed: 'completion_reason' is not a string")
        return VexaMeeting(
            status=str(body.get("status", "")),
            segments=_list_of_dicts(body, "segments"),
            recording_id=recording_id,
            media_id=media_id,
            recording_status=recording_status,
            completion_reason=completion_reason,
            failure_reason=_failure_reason(data),
        )

    def delete_recording(self, recording_id: int) -> None:
        """Delete Vexa's copy of a recording (`DELETE /recordings/{id}`).

        Vexa answers 404 for an id that is unknown, already deleted or not ours, which is the
        state we want, so that counts as success. 409 means the meeting is not over yet; it and
        every other failure raise VexaError so the caller tries again later.
        """
        self._delete(f"/recordings/{int(recording_id)}", what="delete_recording")

    def stop_bot(self, platform: str, native_id: str) -> None:
        """Ask Vexa to take the bot out of a meeting (`DELETE /bots/{platform}/{native_id}`).

        A 404 means there is no active bot for that meeting (already gone), which counts as stopped.
        """
        if platform not in ("google_meet", "zoom") or not re.fullmatch(r"[A-Za-z0-9-]+", native_id):
            raise ValueError("unsupported platform or meeting code")
        self._delete(f"/bots/{platform}/{native_id}", what="stop_bot")

    def _delete(self, path: str, *, what: str) -> None:
        try:
            with self._client() as client:
                response = client.delete(path)
            if response.status_code == 404:
                return
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise VexaError(f"{what} failed: {exc}") from exc

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
