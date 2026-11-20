"""A fake bot host for placement tests: it counts the bots it was asked to start.

One section per task, so tasks that add to this file merge cleanly:

- C11: `FakeBotHost`, the one local host `place_bot()` sends a bot to. It has the two calls
  placement makes (`send_bot`, `stop_bot`), counts what it was asked, and can be slowed down or
  made to fail so a test can hold the placement lock open or watch a failed send. Like the real
  host with one API key, it keeps ONE live bot per meeting code: a second send for a code that has
  a live bot is refused (`BotDuplicate`), and `stop_bot` is keyed by code alone, so it takes out
  whichever bot is there. `timeout_after_accept` models a host that took the bot and then did not
  answer in time (`BotSendUncertain`).

The real client is `app.skribby.SkribbyClient`; this fake only has to look like it for those two
calls. The ids it returns are placeholders.
"""

import threading
import time
from collections.abc import Callable

from app.bot_provider import (
    BotDuplicate,
    BotMeeting,
    BotNotFound,
    BotProviderError,
    BotSendUncertain,
)
from app.meeting_url import parse_meeting_url


class FakeBotHost:
    def __init__(self, *, send_delay: float = 0.0, first_id: int = 1000) -> None:
        self.send_delay = send_delay  # seconds each send takes: widens a race window on purpose
        self.error: Exception | None = None  # raised by every send while set
        self.timeout_after_accept = False  # take the bot, then raise BotSendUncertain
        self.late_accept = False  # raise BotSendUncertain now; the bot lands on `land_late_bots()`
        self._late: list[tuple[str, str]] = []
        self.on_send: Callable[[], None] | None = None  # runs inside the send, before it returns
        self.stop_error: Exception | None = None
        self.sent: list[str] = []  # the URLs, in the order the host took them
        self.stopped: list[tuple[str, str]] = []
        self.live: dict[tuple[str, str], int] = {}  # (platform, code) -> the bot there
        self.statuses: dict[
            tuple[str, str], str
        ] = {}  # what the provider reports; default `joining`
        self._next_id = first_id
        self._in_flight = 0
        self.peak_in_flight = 0  # most sends under way at once; 1 means they were serialised
        self._guard = threading.Lock()

    @property
    def bots_sent(self) -> int:
        """The counter: how many bots this host was asked to start (and accepted)."""
        return len(self.sent)

    def seed_live_bot(self, platform: str, native_id: str) -> None:
        """A bot that is in a meeting without our database knowing it (or another user's)."""
        self.live[(platform, native_id)] = 1

    def send_bot(self, meeting_url: str) -> int:
        with self._guard:
            self._in_flight += 1
            self.peak_in_flight = max(self.peak_in_flight, self._in_flight)
        try:
            if self.on_send is not None:
                self.on_send()
            if self.send_delay:
                time.sleep(self.send_delay)
            if self.error is not None:
                raise self.error
            key = parse_meeting_url(meeting_url)
            if self.late_accept:  # the answer is lost; the provider is still handling the request
                self._late.append(key)
                raise BotSendUncertain("send_bot failed: read timed out")
            with self._guard:
                if key in self.live:
                    raise BotDuplicate("send_bot refused: a bot is already in that meeting")
                self.sent.append(meeting_url)
                self._next_id += 1
                self.live[key] = self._next_id
                bot_id = self._next_id
            if self.timeout_after_accept:
                raise BotSendUncertain("send_bot failed: read timed out")
            return bot_id
        finally:
            with self._guard:
                self._in_flight -= 1

    def land_late_bots(self) -> None:
        """The host finishes handling the requests whose answers were lost: the bots join now."""
        for key in self._late:
            self._next_id += 1
            self.live[key] = self._next_id
            self.sent.append(f"late:{key[1]}")
        self._late.clear()

    def get_meeting(self, platform: str, native_id: str) -> BotMeeting:
        """What the worker asks by meeting code: a joining bot if one is live, else nothing."""
        if (platform, native_id) in self.live:
            return BotMeeting(
                status=self.statuses.get((platform, native_id), "joining"),
                provider_id=self.live[(platform, native_id)],
            )
        raise BotNotFound("get_meeting failed: 404")

    def get_meeting_by_id(self, provider_bot_id) -> BotMeeting:
        """What the worker asks once it knows the id: the bot with that id, if it is still live."""
        if int(provider_bot_id) in self.live.values():
            return BotMeeting(status="joining", provider_id=int(provider_bot_id))  # by id: ours
        raise BotNotFound("get_meeting failed: 404")

    def stop_bot(self, platform: str, native_id: str) -> None:
        if self.stop_error is not None:
            raise self.stop_error
        self.stopped.append((platform, native_id))
        self.live.pop(
            (platform, native_id), None
        )  # like the provider: nothing there is not an error


def unreachable() -> BotProviderError:
    """What the real client raises when the host refuses (the text carries no secrets)."""
    return BotProviderError("send_bot failed: placeholder detail")
