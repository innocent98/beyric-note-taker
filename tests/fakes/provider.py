"""`FakeProvider`: a scriptable bot provider for tests (task B1, the `BotProvider` seam).

It is the whole of `app.bot_provider.BotProvider`, with the rules every provider keeps (one live bot
per meeting code, `stop_bot` of nothing is fine, a send for a bad link is `UnsupportedMeetingUrl`,
the three typed errors), so a test can drive a meeting from `send_bot` to notes with no HTTP. The
same scenarios run against it and against `VexaProvider` in `tests/test_provider_contract.py`, so
the two cannot drift.

**Scripting.** Each outcome is a list of *beats*: what the provider reports on one look. A bot
walks its beats, one per `get_meeting` / `get_meeting_by_id` call, and the last beat repeats.

| Outcome | What the bot does |
|---|---|
| `admitted` | `joining`, then `active` with speech, then `completed` with a recording |
| `knocked_then_admitted` | `joining`, waits in the lobby (twice), then as `admitted` |
| `refused` | `joining`, the lobby, then `failed`, rejected (the host said no) |
| `join_refused` | `joining`, the lobby, then `failed` with Google's refusal reason |
| `never_joined` | `requested`, then `joining` for ever |
| `error` | the send itself raises `BotProviderError` (a connection that was never made) |
| `uncertain` | the send raises `BotSendUncertain`; no bot arrives |

`script(code, outcome)` sets one meeting code; `default` is used for any other. `outcome` may also
be a list of `Beat`s, for a case none of the above fits. The statuses and reasons are the ones the
pipeline reads today (Vexa's vocabulary: see the module doc of `app/bot_provider.py`). Ids,
recording ids and audio bytes are placeholders.
"""

import re
import threading
from dataclasses import dataclass
from pathlib import Path

from app.bot_provider import (
    BotDuplicate,
    BotMeeting,
    BotNotFound,
    BotProviderError,
    BotSendUncertain,
)
from app.meeting_url import parse_meeting_url

TERMINAL = ("completed", "failed")
SPEECH = [{"speaker": "Ada", "text": "hello everyone thanks for joining"}]
GOOGLE_REFUSAL = (
    "google_meet_join_refused: Google would not let the bot ask to join (placeholder detail)"
)


@dataclass(frozen=True)
class Beat:
    """What the provider reports on one look at a bot."""

    status: str
    lobby: bool = False  # the bot has stood in the waiting room (`BotMeeting.reached_lobby`)
    speech: bool = False  # transcript segments exist
    recording: bool = False  # a finished audio recording exists
    completion_reason: str | None = None
    failure_reason: str | None = None


def beats_for(outcome: str) -> list[Beat]:
    joined = [
        Beat("active", lobby=False, speech=True),
        Beat("completed", speech=True, recording=True),
    ]
    scripts = {
        "admitted": [Beat("joining"), *joined],
        "knocked_then_admitted": [
            Beat("joining"),
            Beat("awaiting_admission", lobby=True),
            Beat("awaiting_admission", lobby=True),
            Beat("active", lobby=True, speech=True),
            Beat("completed", lobby=True, speech=True, recording=True),
        ],
        "refused": [
            Beat("joining"),
            Beat("awaiting_admission", lobby=True),
            Beat("failed", lobby=True, completion_reason="awaiting_admission_rejected"),
        ],
        "join_refused": [
            Beat("joining"),
            Beat("awaiting_admission", lobby=True),
            Beat("failed", lobby=True, failure_reason=GOOGLE_REFUSAL),
        ],
        "never_joined": [Beat("requested"), Beat("joining")],
    }
    if outcome not in scripts:
        raise ValueError(f"unknown outcome {outcome!r}")
    return scripts[outcome]


class _Bot:
    def __init__(self, bot_id: int, key: tuple[str, str], beats: list[Beat]) -> None:
        self.id = bot_id
        self.key = key
        self.beats = beats
        self.at = 0  # the beat the next look reports
        self.stopped = False
        self.looks = 0

    def look(self) -> Beat:
        beat = self.beats[min(self.at, len(self.beats) - 1)]
        self.at += 1
        self.looks += 1
        return beat

    @property
    def live(self) -> bool:
        """Holds its meeting code: not finished, not stopped. A bot not yet looked at is live."""
        current = self.beats[min(max(self.at - 1, 0), len(self.beats) - 1)]
        return not self.stopped and (self.at == 0 or current.status not in TERMINAL)

    def stop(self) -> None:
        """Like Vexa: a bot already in the call finishes with what it recorded; one that was not
        in yet fails. Either way it no longer holds the code."""
        self.stopped = True
        last = self.beats[-1]
        was_in = any(b.status == "active" for b in self.beats[: self.at])
        self.beats = [last] if was_in and last.status == "completed" else [Beat("failed")]
        self.at = 1


class FakeProvider:
    RECORDING_ID = 7
    MEDIA_ID = 9

    def __init__(self, *, default="admitted", first_id: int = 2000) -> None:
        self.default: str | list[Beat] = default
        self.scripts: dict[str, str | list[Beat]] = {}
        self.sent: list[str] = []  # the links, in the order bots were accepted
        self.stopped: list[tuple[str, str]] = []
        self.deleted_recordings: list[int] = []
        self.downloads: list[tuple[int, int]] = []
        self.get_calls = 0
        self._next_id = first_id
        self._bots: dict[int, _Bot] = {}
        self._guard = threading.Lock()

    @property
    def bots_sent(self) -> int:
        return len(self.sent)

    def script(self, native_id: str, outcome: str | list[Beat]) -> None:
        """What the bot sent to this meeting code does (the code, not the link)."""
        self.scripts[native_id] = outcome

    # ---- the BotProvider interface ----

    def send_bot(self, meeting_url: str) -> int:
        key = parse_meeting_url(meeting_url)  # UnsupportedMeetingUrl for a bad link, as the client
        outcome = self.scripts.get(key[1], self.default)
        if outcome == "error":
            raise BotProviderError("send_bot failed: connection refused (placeholder detail)")
        if outcome == "uncertain":
            raise BotSendUncertain("send_bot failed: read timed out (placeholder detail)")
        beats = beats_for(outcome) if isinstance(outcome, str) else list(outcome)
        with self._guard:
            if any(bot.key == key and bot.live for bot in self._bots.values()):
                raise BotDuplicate("send_bot refused: a bot is already in that meeting")
            self._next_id += 1
            self._bots[self._next_id] = _Bot(self._next_id, key, beats)
            self.sent.append(meeting_url)
            return self._next_id

    def get_meeting(self, platform: str, native_id: str) -> BotMeeting:
        """The newest bot sent for the code, as Vexa answers a lookup by code."""
        with self._guard:
            bots = [b for b in self._bots.values() if b.key == (platform, native_id)]
            if not bots:
                raise BotNotFound("get_meeting failed: 404")
            return self._report(max(bots, key=lambda b: b.id))

    def get_meeting_by_id(self, vexa_meeting_id: str | int) -> BotMeeting:
        with self._guard:
            bot = self._bots.get(int(vexa_meeting_id))
            if bot is None:
                raise BotNotFound("get_meeting failed: 404")
            return self._report(bot)

    def _report(self, bot: _Bot) -> BotMeeting:
        self.get_calls += 1
        beat = bot.look()
        done = beat.recording
        return BotMeeting(
            status=beat.status,
            segments=list(SPEECH) if beat.speech else [],
            recording_id=self.RECORDING_ID if done else None,
            media_id=self.MEDIA_ID if done else None,
            recording_status="completed" if done else None,
            completion_reason=beat.completion_reason,
            failure_reason=beat.failure_reason,
            vexa_id=bot.id,
            reached_lobby=beat.lobby,
        )

    def stop_bot(self, platform: str, native_id: str) -> None:
        if platform not in ("google_meet", "zoom") or not re.fullmatch(r"[A-Za-z0-9-]+", native_id):
            raise ValueError("unsupported platform or meeting code")
        with self._guard:
            self.stopped.append((platform, native_id))
            for bot in self._bots.values():
                if bot.key == (platform, native_id) and bot.live:
                    bot.stop()  # nothing live there is not an error

    def delete_recording(self, recording_id: int) -> None:
        self.deleted_recordings.append(int(recording_id))  # unknown or already gone is success

    def download_audio(self, recording_id: int, media_id: int, path: Path) -> None:
        if (recording_id, media_id) != (self.RECORDING_ID, self.MEDIA_ID):
            raise BotProviderError("download_audio failed: 404 (placeholder detail)")
        self.downloads.append((recording_id, media_id))
        Path(path).write_bytes(b"audio")
