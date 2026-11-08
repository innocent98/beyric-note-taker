"""The bot layer's one interface: what the app asks of whatever puts a bot in a meeting.

The worker, the pipeline, `place_bot`, the join runner, deletion and the routes depend on
`BotProvider` and on the names here, never on a vendor's client. Today one provider exists,
`app.vexa.VexaProvider` (the self-hosted Vexa); a hosted vendor is one more class behind the same
six calls. Which one runs is the `BOT_PROVIDER` setting (default `vexa`); `build_provider()` turns
that into an object, and an unknown value stops the process at startup (`app/config.py`).

The interface is exactly the calls the app makes today, with the rules the callers already rely on:

- `send_bot(url)`: put a bot in the meeting; returns the provider's id for it (an int today).
- `get_meeting(platform, code)` and `get_meeting_by_id(id)`: the bot's live status, segments,
  recording and failure text.
- `stop_bot(platform, code)`: take the bot out; nothing there is not an error. Keyed by code alone.
- `delete_recording(recording_id)`: remove the provider's copy; already gone is success.
- `download_audio(recording_id, media_id, path)`: write the recording's audio to `path`.

Nothing else is asked of a provider: there is no health or capacity read (capacity is counted in
our own database by `app/placement.py`) and no user, key or account call.

**Errors.** Every failure is a `BotProviderError`, which callers treat as retryable. Three kinds
have a meaning of their own, in this order of specificity: `BotDuplicate` (the meeting already has
a bot that is not ours), `BotSendUncertain` (a send got no clear answer, so the bot MAY arrive),
and `BotNotFound` (the provider does not know the meeting). A provider that has no such failure
never raises that kind. `stop_bot` raises plain `ValueError` for a platform or code it would
never accept, and `send_bot` raises `UnsupportedMeetingUrl` for a link that is not a meeting.

**One bot per meeting code.** The provider keeps ONE live bot per `(platform, code)`; a second
send for a live code is `BotDuplicate`, and `stop_bot` takes out whichever bot holds the code.
`place_bot` and the worker's adoption logic are written against that rule.

**Bounded.** `send_bot` is called while the placement lock is held, so it must return or raise
within `SEND_DEADLINE_SECONDS` however the network behaves.

**Status vocabulary.** `BotMeeting.status` is Vexa's lifecycle words (`requested`, `joining`,
`awaiting_admission`, `active`, `needs_help`, `stopping`, `completed`, `failed`), and the pipeline
reads Vexa's `completion_reason` and `failure_reason` texts. A new provider maps its own states
into that vocabulary (see the SOP, `docs/sop/2026-11-08-bot-provider-seam.md`); replacing it with
a provider-neutral enum is a later step this seam makes possible.

This module imports nothing from the app at module level (the settings are read inside
`build_provider`), so `app/config.py` can read `PROVIDER_FACTORIES` for the startup check.
"""

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

# `send_bot` is held across by the placement lock, and httpx's timeout is per phase (connect,
# write, each read), so a host that trickles bytes could keep one send going for minutes. This is
# the whole-call limit; the lock, and the database connection under it, are never held longer.
SEND_DEADLINE_SECONDS = 30.0


class BotProviderError(Exception):
    """HTTP failure or connection problem talking to a bot provider. Treat as retryable."""


class BotDuplicate(BotProviderError):
    """The provider refused a bot because the meeting already has one. There is no bot of ours to
    stop: the one there belongs to someone else."""


class BotSendUncertain(BotProviderError):
    """`send_bot` did not get a clear answer: the request timed out after it was sent, the
    connection broke, the deadline ran out, the provider answered 5xx (a gateway can time out after
    the bot manager acted), or said yes in a body we could not read. The bot MAY be in the meeting
    or arrive in a moment (the provider keeps handling a request the client has left), so the
    caller keeps the code reserved and lets the worker adopt the bot or give up
    (`pipeline.SEND_UNCONFIRMED`); a stop sent now could find nothing. A connection that was never
    made is NOT this: that is a plain `BotProviderError`, nothing was sent."""


class BotNotFound(BotProviderError):
    """The provider does not know the meeting (HTTP 404 on Vexa). Still a `BotProviderError`, so
    every caller that retries is unchanged; `app/deletion.py` reads it as "nothing there to wait
    for"."""


@dataclass
class BotMeeting:
    status: str
    segments: list[dict[str, Any]] = field(default_factory=list)
    recording_id: int | None = None
    media_id: int | None = None
    recording_status: str | None = None
    completion_reason: str | None = None
    # The provider's own words for why the bot failed, e.g. "[Zoom Web] zoom_requires_rtms: ...".
    failure_reason: str | None = None
    # The provider's id for this meeting (the one `send_bot` returns), when the body carries one.
    vexa_id: int | None = None
    # The bot has stood in the waiting room (`awaiting_admission` in Vexa's own transition
    # history). Vexa raises `needs_help` there as a warning that its lobby budget is running out,
    # which is not a prompt the bot is stuck on.
    reached_lobby: bool = False


def run_within(seconds: float, call):
    """Run `call()` and give its result, or raise `TimeoutError` once `seconds` have gone by. The
    call runs in a worker thread the caller stops waiting for at the limit (as `ics_fetch` does):
    httpx has no limit on the whole call, only per phase."""
    box: dict[str, Any] = {}

    def run() -> None:
        try:
            box["value"] = call()
        except BaseException as exc:  # handed to the caller, which re-raises it
            box["error"] = exc

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(seconds)
    if worker.is_alive():
        raise TimeoutError("took longer than the deadline")
    if "error" in box:
        raise box["error"]
    return box["value"]


@runtime_checkable
class BotProvider(Protocol):
    """What the app asks of a bot provider. See the module docstring for the rules every
    implementation keeps. The names of the parameters are the ones callers use as keywords."""

    def send_bot(self, meeting_url: str) -> int: ...

    def get_meeting(self, platform: str, native_id: str) -> BotMeeting:
        """By platform and meeting code. A reused link can match an older meeting, so callers
        prefer `get_meeting_by_id` whenever the provider's id is known."""
        ...

    def get_meeting_by_id(self, vexa_meeting_id: str | int) -> BotMeeting: ...

    def stop_bot(self, platform: str, native_id: str) -> None: ...

    def delete_recording(self, recording_id: int) -> None: ...

    def download_audio(self, recording_id: int, media_id: int, path: Path) -> None: ...


def _vexa() -> BotProvider:
    from app.vexa import VexaProvider  # inside: app/vexa.py imports this module

    return VexaProvider()


# One entry per provider the app can run. A new provider adds its factory here and nothing else:
# the startup check in `app/config.py` and `build_provider` both read this table.
PROVIDER_FACTORIES: dict[str, Callable[[], BotProvider]] = {"vexa": _vexa}


def build_provider(name: str | None = None) -> BotProvider:
    """The provider named by the `BOT_PROVIDER` setting (or by `name`, for a test)."""
    if name is None:
        from app.config import get_settings  # inside: app/config.py reads this module

        name = get_settings().bot_provider
    try:
        factory = PROVIDER_FACTORIES[name]
    except KeyError:
        known = ", ".join(sorted(PROVIDER_FACTORIES))
        raise ValueError(f"unknown bot provider {name!r}; expected one of: {known}") from None
    return factory()
