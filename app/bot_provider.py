"""The bot layer's one interface: what the app asks of whatever puts a bot in a meeting.

The worker, the pipeline, `place_bot`, the join runner, deletion and the routes depend on
`BotProvider` and on the names here, never on a vendor's client. Two providers exist:
`app.vexa.VexaProvider` (the self-hosted Vexa, the default) and `app.skribby.SkribbyProvider` (the
hosted Skribby). Which one runs is the `BOT_PROVIDER` setting (default `vexa`); `build_provider()`
turns that into an object, and an unknown value stops the process at startup (`app/config.py`).
A meeting remembers which provider ran it (`meetings.bot_provider`), and `provider_for` hands the
callers that work on an existing meeting (the pipeline, deletion, cancel and end) that provider,
whatever the setting says now, so flipping `BOT_PROVIDER` never strands a meeting in flight.

The interface is exactly the calls the app makes today, with the rules the callers already rely on:

- `send_bot(url)`: put a bot in the meeting; returns the provider's id for it (an int for Vexa, a
  UUID string for Skribby; callers only ever store it as text and hand it back).
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

import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

# The provider's id for a bot: whatever `send_bot` returned, stored as text on the meeting.
BotId = int | str

# `send_bot` is held across by the placement lock, and httpx's timeout is per phase (connect,
# write, each read), so a host that trickles bytes could keep one send going for minutes. This is
# the whole-call limit; the lock, and the database connection under it, are never held longer.
SEND_DEADLINE_SECONDS = 30.0


class BotProviderError(Exception):
    """HTTP failure or connection problem talking to a bot provider. Treat as retryable."""


_LINKS = re.compile(
    r"https?://\S+|\b(?:[a-z0-9-]+\.)*(?:teams\.microsoft\.com|teams\.live\.com|zoom\.us"
    r"|meet\.google\.com)\S*",
    re.IGNORECASE,
)
_PASSCODES = re.compile(r"\b(pwd|password|p|context)=[^\s&\"']+", re.IGNORECASE)


def scrub_links(text: str) -> str:
    """`text` with every meeting link replaced by `[link]` (any scheme case, or none) and any
    leftover `pwd=`, `password=`, `p=` or `context=` value hidden: a meeting link can carry a
    Zoom or Teams passcode or a tenant id."""
    return _PASSCODES.sub(r"\1=[redacted]", _LINKS.sub("[link]", text))


def describe_failure(exc: BaseException) -> str:
    """A provider failure for a log line: its type and its own words, cut short, with every web
    address replaced (a meeting link can carry a Zoom passcode or a Teams passcode). Provider
    errors are written without the API key; this is only for the reason, which the type alone
    hides."""
    text = scrub_links(str(exc))
    return f"{type(exc).__name__}: {text[:300]}" if text else type(exc).__name__


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


class BotProviderNotConfigured(BotProviderError):
    """A provider was asked for that has no settings here (a meeting that Skribby ran, with the
    Skribby key since removed). Retryable like every provider error: the worker leaves the meeting
    where it is and looks again on the next pass."""


class BotRegionMismatch(BotProviderError):
    """A meeting's bot is in another region of a hosted provider than the one configured now. The
    provider cannot see it (it would answer 404), so nothing is asked of it: the caller waits and
    tries again, and the log says why."""


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
    vexa_id: BotId | None = None
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

    # A real provider also has a class attribute `name` (`vexa`, `skribby`), the value stored in
    # `meetings.bot_provider`; it is not part of this check so a test double needs none
    # (`provider_name`, `provider_for`).

    def send_bot(self, meeting_url: str) -> BotId: ...

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


# Skribby keeps what it has seen of its bots in the process (`SkribbyProvider`): one object per
# settings object, so a send and the stop that follows it in the same process agree.
_skribby_cache: dict[str, tuple[object, BotProvider]] = {}


def _skribby() -> BotProvider:
    from app.config import get_settings  # inside: app/config.py reads this module
    from app.skribby import SkribbyProvider  # inside: app/skribby.py imports this module

    settings = get_settings()
    cached = _skribby_cache.get("provider")
    if cached is not None and cached[0] is settings:
        return cached[1]
    provider = SkribbyProvider.from_settings(settings)
    _skribby_cache["provider"] = (settings, provider)
    return provider


# One entry per provider the app can run. A new provider adds its factory here and nothing else:
# the startup check in `app/config.py` and `build_provider` both read this table.
PROVIDER_FACTORIES: dict[str, Callable[[], BotProvider]] = {"vexa": _vexa, "skribby": _skribby}


def build_provider(name: str | None = None) -> BotProvider:
    """The provider named by the `BOT_PROVIDER` setting (or by `name`, for a test)."""
    if name is None:
        from app.config import get_settings  # inside: app/config.py reads this module

        name = get_settings().bot_provider
    name = name.strip().lower()  # as the setting is read, so an explicit name is not stricter
    try:
        factory = PROVIDER_FACTORIES[name]
    except KeyError:
        known = ", ".join(sorted(PROVIDER_FACTORIES))
        raise ValueError(f"unknown bot provider {name!r}; expected one of: {known}") from None
    return factory()


def provider_name(provider: object) -> str:
    """The name to store on a new meeting for the provider that sent its bot. A provider has a
    `name`; a test double that has none counts as whatever the setting says."""
    name = getattr(provider, "name", None)
    if isinstance(name, str) and name:
        return name
    from app.config import get_settings

    return get_settings().bot_provider


def provider_region(provider: object) -> str | None:
    """The region a hosted provider's bot is sent to (a real provider has `region`), else None."""
    region = getattr(provider, "region", None)
    return region if isinstance(region, str) and region else None


def provider_for(
    default: BotProvider | None, meeting_provider: str | None, meeting_region: str | None = None
) -> BotProvider:
    """The provider that ran a meeting, for work on a meeting that already exists.

    `default` is the provider the caller was given (the setting's, or a test's). It is used unless
    it is a real provider with a different `name` than the one the meeting remembers: then the
    meeting's own provider is built (a Vexa meeting still in flight after the setting moved to
    Skribby, or the other way round). A `default` with no name (a test double) is always used, and
    so is the default for a meeting that remembers a name this build does not know.

    A hosted provider's region is part of where a bot is: when the meeting remembers a region and
    the provider talks to another, `BotRegionMismatch` is raised instead of asking a provider that
    would answer 404.
    """
    wanted = (meeting_provider or "").strip().lower()
    have = getattr(default, "name", None) if default is not None else None
    if default is not None and (not isinstance(have, str) or not wanted or have == wanted):
        provider = default
    elif wanted in PROVIDER_FACTORIES:
        provider = build_provider(wanted)
    else:
        provider = default if default is not None else build_provider()
    region = provider_region(provider)
    if meeting_region and region and meeting_region != region:
        raise BotRegionMismatch(
            f"this meeting's bot is in region {meeting_region} but the provider is set to {region}"
        )
    return provider
