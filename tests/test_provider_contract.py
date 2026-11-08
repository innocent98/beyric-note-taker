"""The `BotProvider` contract (task B1): the same scenarios against every provider.

Each scenario runs against `FakeProvider` and against `VexaProvider` over a pretend HTTP layer
(`pytest-httpx`, bodies shaped like the ones the Vexa tests already use), and both must give the
same answer. The expected results are written out by hand, so the test is not the fake agreeing with
itself: it is the fake and the Vexa wrapper agreeing with the contract in `app/bot_provider.py`.

A new provider (Skribby, Recall) adds one `World` below and inherits every scenario.
"""

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
from app.vexa import VexaProvider
from tests.fakes.provider import GOOGLE_REFUSAL, SPEECH, Beat, FakeProvider, beats_for

pytestmark = pytest.mark.httpx_mock(
    assert_all_responses_were_requested=False, can_send_already_matched_responses=True
)

CODE = "abc-defg-hij"
URL = f"https://meet.google.com/{CODE}"
BASE = "http://vexa.test"
VEXA_BOT = 5  # the id the pretend Vexa host hands out first


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
    bot: int,
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
        SPEECH if speech else [],
        (7, 9, "completed") if recording else (None, None, None),
        bot,
    )


# ---- one World per provider: how a scenario is arranged behind the interface ----


class FakeWorld:
    name = "fake"

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

    def audio_missing(self) -> tuple[int, int]:
        return (8, 8)  # a recording the fake never made


class VexaWorld:
    name = "vexa"

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

    def audio_missing(self) -> tuple[int, int]:
        self.mock.add_response(
            method="GET", url=f"{BASE}/recordings/7/media/9/raw?type=audio", status_code=404
        )
        return (7, 9)


@pytest.fixture(params=["fake", "vexa"])
def world(request, httpx_mock, monkeypatch):
    made = FakeWorld() if request.param == "fake" else VexaWorld(httpx_mock, monkeypatch)
    yield made
    if isinstance(made, VexaWorld):
        made.teardown()


def poll(provider: BotProvider, bot: int, times: int) -> list[tuple]:
    return [look(provider.get_meeting_by_id(bot)) for _ in range(times)]


# ---- what the provider reports as a bot goes through a meeting ----


def test_an_admitted_bot_goes_joining_active_completed_with_a_recording(world):
    world.outcome("admitted")
    bot = world.provider.send_bot(URL)
    assert isinstance(bot, int)
    assert poll(world.provider, bot, 3) == [
        expected(bot, "joining"),
        expected(bot, "active", speech=True),
        expected(bot, "completed", speech=True, recording=True),
    ]


def test_a_bot_that_waited_in_the_lobby_reports_it_and_is_then_admitted(world):
    world.outcome("knocked_then_admitted")
    bot = world.provider.send_bot(URL)
    assert poll(world.provider, bot, 5) == [
        expected(bot, "joining"),
        expected(bot, "awaiting_admission", lobby=True),
        expected(bot, "awaiting_admission", lobby=True),
        expected(bot, "active", lobby=True, speech=True),
        expected(bot, "completed", lobby=True, speech=True, recording=True),
    ]


def test_a_host_that_declines_the_bot_ends_it_failed_as_rejected(world):
    world.outcome("refused")
    bot = world.provider.send_bot(URL)
    assert poll(world.provider, bot, 3) == [
        expected(bot, "joining"),
        expected(bot, "awaiting_admission", lobby=True),
        expected(bot, "failed", lobby=True, completion_reason="awaiting_admission_rejected"),
    ]


def test_googles_refusal_to_ask_to_join_comes_with_its_reason_text(world):
    world.outcome("join_refused")
    bot = world.provider.send_bot(URL)
    last = poll(world.provider, bot, 3)[-1]
    assert last == expected(bot, "failed", lobby=True, failure_reason=GOOGLE_REFUSAL)


def test_a_bot_that_never_gets_in_stays_in_a_live_state_and_the_last_look_repeats(world):
    world.outcome("never_joined")
    bot = world.provider.send_bot(URL)
    assert poll(world.provider, bot, 4) == [
        expected(bot, "requested"),
        expected(bot, "joining"),
        expected(bot, "joining"),
        expected(bot, "joining"),
    ]


def test_looking_up_by_meeting_code_finds_the_same_bot(world):
    world.outcome("admitted")
    bot = world.provider.send_bot(URL)
    assert look(world.provider.get_meeting("google_meet", CODE)) == expected(bot, "joining")


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
    assert world.provider.delete_recording(7) is None


def test_downloading_writes_the_audio_where_asked(world, httpx_mock, tmp_path):
    if isinstance(world, VexaWorld):
        httpx_mock.add_response(
            method="GET", url=f"{BASE}/recordings/7/media/9/raw?type=audio", content=b"audio"
        )
    path = tmp_path / "source.webm"
    world.provider.download_audio(7, 9, path)
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
    monkeypatch.setenv("BOT_PROVIDER", "skribby")
    get_settings.cache_clear()
    try:
        with pytest.raises(ValueError, match=r"BOT_PROVIDER.*'skribby'.*must be one of: vexa"):
            get_settings()
    finally:
        get_settings.cache_clear()


def test_build_provider_refuses_an_unknown_name_too():
    with pytest.raises(ValueError, match="unknown bot provider 'nope'; expected one of: vexa"):
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
