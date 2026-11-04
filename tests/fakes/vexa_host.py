"""A fake Vexa host for placement tests: it counts the bots it was asked to start.

One section per task, so tasks that add to this file merge cleanly:

- C11: `FakeVexaHost`, the one local host `place_bot()` sends a bot to. It has the two calls
  placement makes (`send_bot`, `stop_bot`), counts what it was asked, and can be slowed down or
  made to fail so a test can hold the placement lock open or watch a failed send.

The real client is `app.vexa.VexaClient`; this fake only has to look like it for those two calls.
The ids it returns are placeholders.
"""

import threading
import time
from collections.abc import Callable

from app.vexa import VexaError


class FakeVexaHost:
    def __init__(self, *, send_delay: float = 0.0, first_id: int = 1000) -> None:
        self.send_delay = send_delay  # seconds each send takes: widens a race window on purpose
        self.error: Exception | None = None  # raised by every send while set
        self.on_send: Callable[[], None] | None = None  # runs inside the send, before it returns
        self.stop_error: Exception | None = None
        self.sent: list[str] = []  # the URLs, in the order the host took them
        self.stopped: list[tuple[str, str]] = []
        self._next_id = first_id
        self._in_flight = 0
        self.peak_in_flight = 0  # most sends under way at once; 1 means they were serialised
        self._guard = threading.Lock()

    @property
    def bots_sent(self) -> int:
        """The counter: how many bots this host was asked to start (and accepted)."""
        return len(self.sent)

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
            with self._guard:
                self.sent.append(meeting_url)
                self._next_id += 1
                return self._next_id
        finally:
            with self._guard:
                self._in_flight -= 1

    def stop_bot(self, platform: str, native_id: str) -> None:
        if self.stop_error is not None:
            raise self.stop_error
        self.stopped.append((platform, native_id))


def unreachable() -> VexaError:
    """What the real client raises when the host does not answer (the text carries no secrets)."""
    return VexaError("send_bot failed: placeholder detail")
