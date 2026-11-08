"""The `BotProvider` contract (task B1): the same scenarios against every provider.

Each scenario runs against `FakeProvider`, `VexaProvider` and `SkribbyProvider`, the last two over
a pretend HTTP layer (`pytest-httpx`; the Vexa bodies are the ones the Vexa tests already use, the
Skribby bodies and error answers are the ones in Skribby's API pages and the bake-off tool), and all
three must give the same answer. The expected results are written out by hand, so the test is not
the fake agreeing with itself: it is each provider agreeing with the contract in
`app/bot_provider.py`.

A new provider (Recall) adds one `World` below and inherits every scenario.
"""

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.bot_provider import (
    PROVIDER_FACTORIES,
    BotDuplicate,
    BotMeeting,
    BotNotFound,
    BotProvider,
    BotProviderError,
    BotSendUncertain,
    build_provider,
)
from app.config import Settings, get_settings
from app.meeting_url import UnsupportedMeetingUrl
from app.skribby import SkribbyClient, SkribbyProvider, recording_handle
from app.vexa import VexaProvider
from tests.fakes.provider import SPEECH, Beat, FakeProvider, beats_for

pytestmark = pytest.mark.httpx_mock(
    assert_all_responses_were_requested=False, can_send_already_matched_responses=True
)

CODE = "abc-defg-hij"
URL = f"https://meet.google.com/{CODE}"
BASE = "http://vexa.test"
VEXA_BOT = 5  # the id the pretend Vexa host hands out first
SKRIBBY_BASE = "https://skribby.test/api/v1"
SKRIBBY_FILES = "https://files.skribby.test/recording.webm"
NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)


def look(info: BotMeeting) -> tuple:
    """Everything the pipeline reads from one look at a bot."""
    return (
        info.status,
        info.reached_lobby,
        info.completion_reason,
        info.failure_reason,
        info.segments,
        (info.recording_id, info.media_id, info.recording_status),
        info.vexa_id,
    )


def expected(
    world,
    bot,
    status: str,
    *,
    lobby: bool = False,
    completion_reason: str | None = None,
    failure_reason: str | None = None,
    speech: bool = False,
    recording: bool = False,
) -> tuple:
    return (
        status,
        lobby,
        completion_reason,
        failure_reason,
        # Skribby's transcript comes after the call (a post-call model): no segments while `active`.
        SPEECH if speech and (status != "active" or world.live_speech) else [],
        (*world.ids_for(bot), "completed") if recording else (None, None, None),
        bot,
    )


# ---- one World per provider: how a scenario is arranged behind the interface ----


class FakeWorld:
    name = "fake"
    live_speech = True  # segments are there while the bot is in the call

    def __init__(self) -> None:
        self.provider: BotProvider = FakeProvider()

    def outcome(self, outcome: str | list[Beat]) -> None:
        self.provider.script(CODE, outcome)

    def send_fails(self, kind: str) -> None:
        self.provider.script(CODE, {"never_connected": "error", "uncertain": "uncertain"}[kind])

    def duplicate_on_second_send(self) -> None:
        self.provider.script(CODE, "admitted")  # the first send is accepted and is live

    def unknown_meeting(self) -> None:
        pass  # nothing was sent, so the provider does not know it

    def nothing_to_stop(self) -> None:
        pass

    def recording_gone(self) -> None:
        pass

    def ids_for(self, bot) -> tuple[int, int]:
        return (7, 9)

    def audio_ready(self) -> tuple[int, int]:
        return (7, 9)

    def audio_missing(self) -> tuple[int, int]:
        return (8, 8)  # a recording the fake never made


class VexaWorld:
    name = "vexa"
    live_speech = True

    def __init__(self, httpx_mock, monkeypatch) -> None:
        monkeypatch.setenv("VEXA_BASE_URL", BASE)
        monkeypatch.setenv("VEXA_API_KEY", "test-key")
        monkeypatch.setenv("BOT_NAME", "Test Bot")
        get_settings.cache_clear()
        self.mock = httpx_mock
        self.provider: BotProvider = VexaProvider()
        self.next_id = VEXA_BOT

    def teardown(self) -> None:
        get_settings.cache_clear()

    def _body(self, beat: Beat, bot: int) -> dict:
        data: dict = {}
        if beat.completion_reason:
            data["completion_reason"] = beat.completion_reason
        if beat.failure_reason:
            data["reason"] = beat.failure_reason
        if beat.lobby:
            data["status_transition"] = [{"from": "joining", "to": "awaiting_admission"}]
        body: dict = {
            "id": bot,
            "platform": "google_meet",
            "native_meeting_id": CODE,
            "status": beat.status,
            "segments": list(SPEECH) if beat.speech else [],
            "data": data,
        }
        if beat.recording:
            body["recordings"] = [
                {"id": 7, "status": "completed", "media_files": [{"id": 9, "type": "audio"}]}
            ]
        return body

    def outcome(self, outcome: str | list[Beat]) -> None:
        beats = beats_for(outcome) if isinstance(outcome, str) else outcome
        bot = self.next_id
        self.mock.add_response(method="POST", url=f"{BASE}/bots", status_code=201, json={"id": bot})
        for beat in beats:
            self.mock.add_response(
                method="GET", url=f"{BASE}/transcripts/by-id/{bot}", json=self._body(beat, bot)
            )
            self.mock.add_response(
                method="GET",
                url=f"{BASE}/transcripts/google_meet/{CODE}",
                json=self._body(beat, bot),
            )
        self.next_id += 1

    def send_fails(self, kind: str) -> None:
        if kind == "never_connected":
            self.mock.add_exception(httpx.ConnectError("refused"))
        else:  # the gateway timed out after the bot manager may have acted
            self.mock.add_response(method="POST", url=f"{BASE}/bots", status_code=504)

    def duplicate_on_second_send(self) -> None:
        self.mock.add_response(method="POST", url=f"{BASE}/bots", status_code=201, json={"id": 5})
        self.mock.add_response(method="POST", url=f"{BASE}/bots", status_code=409)

    def unknown_meeting(self) -> None:
        self.mock.add_response(
            method="GET", url=f"{BASE}/transcripts/by-id/999", status_code=404, json={}
        )
        self.mock.add_response(
            method="GET", url=f"{BASE}/transcripts/google_meet/{CODE}", status_code=404, json={}
        )

    def nothing_to_stop(self) -> None:
        self.mock.add_response(
            method="DELETE", url=f"{BASE}/bots/google_meet/{CODE}", status_code=404
        )

    def recording_gone(self) -> None:
        self.mock.add_response(method="DELETE", url=f"{BASE}/recordings/7", status_code=404)

    def ids_for(self, bot) -> tuple[int, int]:
        return (7, 9)

    def audio_ready(self) -> tuple[int, int]:
        self.mock.add_response(
            method="GET", url=f"{BASE}/recordings/7/media/9/raw?type=audio", content=b"audio"
        )
        return (7, 9)

    def audio_missing(self) -> tuple[int, int]:
        self.mock.add_response(
            method="GET", url=f"{BASE}/recordings/7/media/9/raw?type=audio", status_code=404
        )
        return (7, 9)


class StubLedger:
    """Our own `meetings` table as the Skribby provider sees it, for the contract: nothing live (the
    provider's own memory of what it sent is what the scenarios rely on) and the bots whose
    recording a scenario looks up by handle."""

    def __init__(self) -> None:
        self.known: list[str] = []

    def live_bots(self, platform: str, code: str) -> list[str]:
        return []

    def bots_for_prefix(self, prefix: str) -> list[str]:
        return [bot for bot in self.known if bot.startswith(prefix)]


class SkribbyWorld:
    """`SkribbyProvider` over a pretend Skribby. A beat in Vexa's words is written as the Skribby
    bot body that must be read as that beat: the door and the knock are told by how long the bot's
    `joining` lasted (the provider's clock is fixed at `NOW`)."""

    name = "skribby"
    live_speech = False  # Skribby hands the transcript over once the bot has finished
    REC_BOT = "11111111-2222-4333-8444-555555555555"

    def __init__(self, httpx_mock) -> None:
        self.mock = httpx_mock
        self.ledger = StubLedger()
        client = SkribbyClient(
            api_key="skr-test-key", base_url=SKRIBBY_BASE, bot_name="Test Bot", max_poll_hours=6
        )
        self.provider: BotProvider = SkribbyProvider(client, self.ledger, clock=lambda: NOW)
        self.counter = 0

    def teardown(self) -> None:
        pass

    def new_bot(self) -> str:
        self.counter += 1
        return str(uuid.UUID(int=(0xA1B2C3D4E5F60718 << 64) + self.counter))

    def ids_for(self, bot) -> tuple[int, int]:
        handle = recording_handle(str(bot))
        return (handle, handle)

    def _events(self, *steps: tuple[str, int]) -> list[dict]:
        """`(status, seconds ago)` as Skribby's `events`, oldest first."""
        return [
            {
                "event": "status_update",
                "data": {"old_status": "x", "new_status": status},
                "created_at": (NOW - timedelta(seconds=ago)).isoformat(),
            }
            for status, ago in steps
        ]

    def _body(self, beat: Beat, bot: str) -> dict:
        speech = (
            [
                {
                    "start": 1,
                    "end": 2,
                    "speaker": 0,
                    "speaker_name": "Ada",
                    "transcript": SPEECH[0]["text"],
                }
            ]
            if beat.speech
            else []
        )
        base = {"id": bot, "service": "gmeet", "transcript": speech, "recording_url": None}
        if beat.status == "requested":
            return {**base, "status": "booting", "events": self._events(("booting", 3))}
        if beat.status == "joining":
            return {
                **base,
                "status": "joining",
                "events": self._events(("booting", 30), ("joining", 5)),
            }
        if beat.status == "awaiting_admission":
            return {
                **base,
                "status": "joining",
                "events": self._events(("booting", 90), ("joining", 60)),
            }
        in_call = (
            self._events(("joining", 120), ("recording", 60))
            if beat.lobby
            else self._events(("joining", 30), ("recording", 28))
        )
        if beat.status == "active":
            return {**base, "status": "recording", "events": in_call, "transcript": []}
        if beat.status == "completed":
            return {
                **base,
                "status": "finished",
                "stop_reason": "meeting_ended",
                "events": in_call,
                "recording_url": SKRIBBY_FILES if beat.recording else None,
            }
        if beat.completion_reason == "awaiting_admission_rejected":
            return {
                **base,
                "status": "not_admitted",
                "stop_reason": "request_denied",
                "events": self._events(("joining", 200)),
            }
        # Google's refusal: Skribby gave up after its own retries
        return {
            **base,
            "status": "bot_detected",
            "events": self._events(*[("bot_detected", 100 - n) for n in range(4)]),
        }

    def outcome(self, outcome: str | list[Beat]) -> None:
        beats = beats_for(outcome) if isinstance(outcome, str) else outcome
        bot = self.new_bot()
        self.mock.add_response(
            method="POST", url=f"{SKRIBBY_BASE}/bot", status_code=201, json={"id": bot}
        )
        for beat in beats:
            self.mock.add_response(
                method="GET", url=f"{SKRIBBY_BASE}/bot/{bot}", json=self._body(beat, bot)
            )

    def send_fails(self, kind: str) -> None:
        if kind == "never_connected":
            self.mock.add_exception(httpx.ConnectError("refused"))
        else:  # the gateway timed out after Skribby may have made the bot
            self.mock.add_response(method="POST", url=f"{SKRIBBY_BASE}/bot", status_code=504)

    def duplicate_on_second_send(self) -> None:
        bot = self.new_bot()
        self.mock.add_response(
            method="POST", url=f"{SKRIBBY_BASE}/bot", status_code=201, json={"id": bot}
        )
        self.mock.add_response(
            method="GET", url=f"{SKRIBBY_BASE}/bot/{bot}", json=self._body(Beat("joining"), bot)
        )

    def unknown_meeting(self) -> None:
        pass  # nothing was sent, so no bot of ours is known

    def nothing_to_stop(self) -> None:
        pass

    def recording_gone(self) -> None:
        self.ledger.known.append(self.REC_BOT)
        self.mock.add_response(
            method="DELETE", url=f"{SKRIBBY_BASE}/bot/{self.REC_BOT}", status_code=404
        )

    def audio_ready(self) -> tuple[int, int]:
        self.ledger.known.append(self.REC_BOT)
        done = Beat("completed", speech=True, recording=True)
        self.mock.add_response(
            method="GET",
            url=f"{SKRIBBY_BASE}/bot/{self.REC_BOT}",
            json=self._body(done, self.REC_BOT),
        )
        self.mock.add_response(method="GET", url=SKRIBBY_FILES, content=b"audio")
        return self.ids_for(self.REC_BOT)

    def audio_missing(self) -> tuple[int, int]:
        self.ledger.known.append(self.REC_BOT)
        self.mock.add_response(
            method="GET", url=f"{SKRIBBY_BASE}/bot/{self.REC_BOT}", status_code=404, json={}
        )
        return self.ids_for(self.REC_BOT)


@pytest.fixture(params=["fake", "vexa", "skribby"])
def world(request, httpx_mock, monkeypatch):
    if request.param == "fake":
        made = FakeWorld()
    elif request.param == "vexa":
        made = VexaWorld(httpx_mock, monkeypatch)
    else:
        made = SkribbyWorld(httpx_mock)
    yield made
    if hasattr(made, "teardown"):
        made.teardown()


def poll(provider: BotProvider, bot: int, times: int) -> list[tuple]:
    return [look(provider.get_meeting_by_id(bot)) for _ in range(times)]


# ---- what the provider reports as a bot goes through a meeting ----


def test_an_admitted_bot_goes_joining_active_completed_with_a_recording(world):
    world.outcome("admitted")
    bot = world.provider.send_bot(URL)
    assert isinstance(bot, int | str)
    assert poll(world.provider, bot, 3) == [
        expected(world, bot, "joining"),
        expected(world, bot, "active", speech=True),
        expected(world, bot, "completed", speech=True, recording=True),
    ]


def test_a_bot_that_waited_in_the_lobby_reports_it_and_is_then_admitted(world):
    world.outcome("knocked_then_admitted")
    bot = world.provider.send_bot(URL)
    assert poll(world.provider, bot, 5) == [
        expected(world, bot, "joining"),
        expected(world, bot, "awaiting_admission", lobby=True),
        expected(world, bot, "awaiting_admission", lobby=True),
        expected(world, bot, "active", lobby=True, speech=True),
        expected(world, bot, "completed", lobby=True, speech=True, recording=True),
    ]


def test_a_host_that_declines_the_bot_ends_it_failed_as_rejected(world):
    world.outcome("refused")
    bot = world.provider.send_bot(URL)
    assert poll(world.provider, bot, 3) == [
        expected(world, bot, "joining"),
        expected(world, bot, "awaiting_admission", lobby=True),
        expected(world, bot, "failed", lobby=True, completion_reason="awaiting_admission_rejected"),
    ]


def test_googles_refusal_to_ask_to_join_comes_with_its_reason_text(world):
    world.outcome("join_refused")
    bot = world.provider.send_bot(URL)
    last = poll(world.provider, bot, 3)[-1]
    # The interface's rule: a refusal is `failed` with a reason that STARTS with this code, because
    # the pipeline's retry and bell logic read exactly that. The rest of the text is the provider's.
    assert last[0] == "failed"
    assert last[3].startswith("google_meet_join_refused")
    assert last[2] is None and last[4] == [] and last[5] == (None, None, None)


def test_a_bot_that_never_gets_in_stays_in_a_live_state_and_the_last_look_repeats(world):
    world.outcome("never_joined")
    bot = world.provider.send_bot(URL)
    assert poll(world.provider, bot, 4) == [
        expected(world, bot, "requested"),
        expected(world, bot, "joining"),
        expected(world, bot, "joining"),
        expected(world, bot, "joining"),
    ]


def test_looking_up_by_meeting_code_finds_the_same_bot(world):
    world.outcome("admitted")
    bot = world.provider.send_bot(URL)
    assert look(world.provider.get_meeting("google_meet", CODE)) == expected(world, bot, "joining")


def test_looking_up_an_unknown_meeting_is_not_found(world):
    world.unknown_meeting()
    with pytest.raises(BotNotFound):
        world.provider.get_meeting_by_id(999)
    with pytest.raises(BotNotFound):
        world.provider.get_meeting("google_meet", CODE)


# ---- sending ----


def test_a_second_bot_for_a_live_meeting_code_is_a_duplicate(world):
    world.duplicate_on_second_send()
    world.provider.send_bot(URL)
    with pytest.raises(BotDuplicate):
        world.provider.send_bot(URL)


def test_the_code_is_free_again_once_the_bot_is_over(world):
    world.outcome("admitted")
    first = world.provider.send_bot(URL)
    poll(world.provider, first, 3)  # through `completed`
    world.outcome("admitted")
    second = world.provider.send_bot(URL)
    assert second != first


def test_a_send_that_never_connected_is_an_error_and_not_an_uncertain_send(world):
    world.send_fails("never_connected")
    with pytest.raises(BotProviderError) as caught:
        world.provider.send_bot(URL)
    assert not isinstance(caught.value, BotSendUncertain | BotDuplicate | BotNotFound)


def test_a_send_with_no_clear_answer_is_uncertain(world):
    world.send_fails("uncertain")
    with pytest.raises(BotSendUncertain):
        world.provider.send_bot(URL)


@pytest.mark.parametrize(
    "link",
    ["https://example.com/meeting", "not a url", "https://zoom.us.evil.example/j/1", ""],
)
def test_a_link_that_is_not_a_meeting_is_refused_before_anything_is_sent(world, link):
    with pytest.raises(UnsupportedMeetingUrl):
        world.provider.send_bot(link)


# ---- stopping, deleting, downloading ----


def test_stopping_a_meeting_with_no_bot_in_it_is_not_an_error(world):
    world.nothing_to_stop()
    assert world.provider.stop_bot("google_meet", CODE) is None


@pytest.mark.parametrize(
    ("platform", "code"), [("teams", CODE), ("google_meet", "../bots"), ("zoom", "a b")]
)
def test_stopping_with_a_platform_or_code_it_would_never_accept_is_a_value_error(
    world, platform, code
):
    with pytest.raises(ValueError):
        world.provider.stop_bot(platform, code)


def test_deleting_a_recording_that_is_already_gone_is_success(world):
    world.recording_gone()
    recording_id, _ = world.ids_for(getattr(world, "REC_BOT", None))
    assert world.provider.delete_recording(recording_id) is None


def test_downloading_writes_the_audio_where_asked(world, tmp_path):
    recording_id, media_id = world.audio_ready()
    path = tmp_path / "source.webm"
    world.provider.download_audio(recording_id, media_id, path)
    assert path.read_bytes() == b"audio"


def test_a_download_that_fails_is_an_error_and_leaves_no_file(world, tmp_path):
    recording_id, media_id = world.audio_missing()
    path = tmp_path / "source.webm"
    with pytest.raises(BotProviderError):
        world.provider.download_audio(recording_id, media_id, path)
    assert not path.exists()


# ---- the interface itself ----


def test_every_provider_has_the_whole_interface():
    assert isinstance(FakeProvider(), BotProvider)
    assert isinstance(VexaProvider(), BotProvider)
    client = SkribbyClient(api_key="skr-test-key", base_url=SKRIBBY_BASE)
    assert isinstance(SkribbyProvider(client, StubLedger()), BotProvider)


# ---- choosing the provider ----


def test_the_default_provider_is_vexa(monkeypatch):
    monkeypatch.delenv("BOT_PROVIDER", raising=False)
    get_settings.cache_clear()
    try:
        assert Settings(_env_file=None).bot_provider == "vexa"
        assert isinstance(build_provider(), VexaProvider)
    finally:
        get_settings.cache_clear()


def test_an_unknown_provider_stops_startup_with_a_message_naming_the_setting_and_the_choices(
    monkeypatch,
):
    monkeypatch.setenv("BOT_PROVIDER", "nope")
    get_settings.cache_clear()
    try:
        with pytest.raises(
            ValueError, match=r"BOT_PROVIDER.*'nope'.*must be one of: skribby, vexa"
        ):
            get_settings()
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize("blank", ["", "   "])
def test_an_empty_bot_provider_line_means_the_default_and_does_not_stop_startup(monkeypatch, blank):
    monkeypatch.setenv("BOT_PROVIDER", blank)
    get_settings.cache_clear()
    try:
        assert get_settings().bot_provider == "vexa"
        assert isinstance(build_provider(), VexaProvider)
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize("name", ["vexa", "Vexa", "  VEXA "])
def test_build_provider_reads_an_explicit_name_as_the_setting_does(name):
    assert isinstance(build_provider(name), VexaProvider)


def test_build_provider_refuses_an_unknown_name_too():
    with pytest.raises(
        ValueError, match="unknown bot provider 'nope'; expected one of: skribby, vexa"
    ):
        build_provider("nope")


def test_the_setting_picks_the_provider_a_new_factory_registered(monkeypatch):
    fake = FakeProvider()
    monkeypatch.setitem(PROVIDER_FACTORIES, "fake", lambda: fake)
    monkeypatch.setenv("BOT_PROVIDER", " Fake ")
    get_settings.cache_clear()
    try:
        assert get_settings().bot_provider == "fake"
        assert build_provider() is fake
    finally:
        get_settings.cache_clear()
