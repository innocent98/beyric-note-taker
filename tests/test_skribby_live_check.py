"""`scripts/skribby_live_check.py`: the one-meeting check Adebayo runs by hand. Here it runs against
a pretend Skribby: the keys are read from the environment or the file and never printed, the bot is
always stopped, and the status table says what the app would read."""

import io
import itertools
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from scripts import skribby_live_check as check

pytestmark = pytest.mark.httpx_mock(
    assert_all_responses_were_requested=False, can_send_already_matched_responses=True
)

KEY = "skr-live-0123456789abcdef"
ACCOUNT = "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d"
API = "https://platform.skribby.io/api/v1"
BOT = "3c09b2aa-6708-42c1-8145-aa55ee223613"
MEET = "https://meet.google.com/abc-defg-hij"
FILES = "https://files.skribby.io/rec.webm"


FROZEN = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)  # the time the status mapping reads, fixed


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch):
    monkeypatch.setattr(check, "utcnow", lambda: FROZEN)


def ago(seconds):
    return (FROZEN - timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


def bot(status, *steps, **fields):
    events = [
        {"data": {"new_status": s}, "created_at": ago(a), "event": "status_update"}
        for s, a in steps
    ]
    return {"id": BOT, "service": "gmeet", "status": status, "events": events, **fields}


def run(argv, env, keys_file=None, tmp_path=None):
    out = io.StringIO()
    clock = itertools.count(0, 5)  # every look is 5 seconds later
    code = check.main(
        [*argv, "--keys-file", str(keys_file or "/nonexistent"), "--interval", "5"],
        env=env,
        out=out,
        sleep=lambda seconds: None,
        clock=lambda: next(clock),
    )
    return code, out.getvalue()


def accept(mock):
    mock.add_response(method="POST", url=f"{API}/bot", status_code=201, json={"id": BOT})
    mock.add_response(method="POST", url=f"{API}/bot/{BOT}/stop", json={"message": "ok"})


def serve(mock, bodies):
    """GET /bot answers the bodies in turn (the last repeats) until a DELETE succeeds, then 404."""
    state = {"n": 0, "gone": False}

    def look(request):
        if state["gone"]:
            return httpx.Response(404, json={})
        item = bodies[min(state["n"], len(bodies) - 1)]
        state["n"] += 1
        return httpx.Response(200, json=item)

    def delete(request):
        state["gone"] = True
        return httpx.Response(200, json={"message": "deleted"})

    mock.add_callback(look, method="GET", url=f"{API}/bot/{BOT}")
    mock.add_callback(delete, method="DELETE", url=f"{API}/bot/{BOT}")
    return state


def test_a_good_run_prints_the_mapping_stops_the_bot_proves_the_delete_and_never_prints_a_key(
    httpx_mock,
):
    accept(httpx_mock)
    finished = bot(
        "finished",
        ("joining", 120),
        ("recording", 90),
        stop_reason="manually_stopped",
        recording_url=FILES,
        transcript=[{"speaker_name": "Ada", "transcript": "hello there"}],
    )
    state = serve(
        httpx_mock,
        [
            bot("booting", ("booting", 3)),
            bot("joining", ("joining", 30)),
            bot("recording", ("joining", 60), ("recording", 30)),
            bot("recording", ("joining", 90), ("recording", 60)),
            finished,
        ],
    )
    httpx_mock.add_callback(
        lambda request: httpx.Response(404 if state["gone"] else 200, content=b"audio"),
        method="GET",
        url=FILES,
    )

    code, text = run(
        ["--meet-url", MEET, "--stay", "5", "--download", "--delete"],
        {"SKRIBBY_API_KEY": KEY, "SKRIBBY_ACCOUNT_ID": ACCOUNT},
    )

    assert code == 0, text
    for words in (
        "signed in bot",
        f"Bot created: {BOT}",
        "booting",
        "awaiting_admission",
        "active",
        "stopping the bot (stay time reached)",
        "Over: Skribby `completed`",
        "speakers: Ada",
        "Recording host (the only part of the link printed): files.skribby.io",
        "Audio downloaded: 5 bytes",
        "GET /bot/{id}: 404 (the bot is gone)",
        "the recording link now answers HTTP 404",
        "-> gone.",
    ):
        assert words in text
    assert state["gone"]
    assert KEY not in text and ACCOUNT not in text and MEET not in text and "rec.webm" not in text
    stops = [r for r in httpx_mock.get_requests() if str(r.url).endswith("/stop")]
    assert stops  # the bot was stopped by the run
    created = [
        r for r in httpx_mock.get_requests() if r.method == "POST" and r.url.path.endswith("/bot")
    ]
    assert b"always_authenticate" in created[0].content  # signed in, since the account id was given


def test_a_recording_link_that_still_answers_after_the_delete_is_reported_as_not_gone(httpx_mock):
    accept(httpx_mock)
    finished = bot("finished", ("joining", 20), ("recording", 10), recording_url=FILES)
    state = serve(httpx_mock, [finished])
    httpx_mock.add_response(method="GET", url=FILES, content=b"audio")  # still there

    code, text = run(["--meet-url", MEET, "--delete"], {"SKRIBBY_API_KEY": KEY})

    assert state["gone"] and code == 1
    assert "the recording link now answers HTTP 200" in text
    assert "NOT shown to be gone: do not promise deletion yet" in text


def test_a_403_on_the_recording_link_is_inconclusive_and_a_404_bot_is_still_gone(httpx_mock):
    accept(httpx_mock)
    finished = bot("finished", ("joining", 20), ("recording", 10), recording_url=FILES)
    serve(httpx_mock, [finished])  # the bot answers 404 once deleted
    httpx_mock.add_response(method="GET", url=FILES, status_code=403)

    code, text = run(["--meet-url", MEET, "--delete"], {"SKRIBBY_API_KEY": KEY})

    assert "the recording link now answers HTTP 403" in text
    assert "inconclusive: an expired link answers 403 too" in text
    assert "GET /bot/{id}: 404 (the bot is gone)" in text and "-> gone." in text  # the bot's 404
    assert code == 0


def test_a_403_link_with_a_bot_that_still_answers_is_inconclusive_not_gone(httpx_mock):
    accept(httpx_mock)
    stripped = bot(
        "finished", ("joining", 20), ("recording", 10), recording_url=None, transcript=[]
    )
    with_link = bot("finished", ("joining", 20), ("recording", 10), recording_url=FILES)
    state = {"deleted": False}
    httpx_mock.add_callback(
        lambda r: httpx.Response(200, json=stripped if state["deleted"] else with_link),
        method="GET",
        url=f"{API}/bot/{BOT}",
    )

    def delete(request):
        state["deleted"] = True
        return httpx.Response(200, json={})

    httpx_mock.add_callback(delete, method="DELETE", url=f"{API}/bot/{BOT}")
    httpx_mock.add_response(method="GET", url=FILES, status_code=403)

    code, text = run(["--meet-url", MEET, "--delete"], {"SKRIBBY_API_KEY": KEY})

    assert "-> inconclusive" in text and "-> gone." not in text
    assert code == 1


def test_a_delete_that_leaves_the_data_in_place_is_reported_as_not_gone(httpx_mock):
    accept(httpx_mock)
    finished = bot("finished", ("joining", 20), ("recording", 10), recording_url=FILES)
    httpx_mock.add_response(method="GET", url=f"{API}/bot/{BOT}", json=finished)
    httpx_mock.add_response(method="DELETE", url=f"{API}/bot/{BOT}", json={})  # but nothing goes

    code, text = run(["--meet-url", MEET, "--delete"], {"SKRIBBY_API_KEY": KEY})

    assert code == 1
    assert "still shows the recording or transcript" in text and KEY not in text


def test_a_run_with_no_speaker_names_says_so_plainly(httpx_mock):
    accept(httpx_mock)
    nameless = bot(
        "finished",
        ("joining", 20),
        ("recording", 10),
        recording_url=FILES,
        transcript=[{"speaker_name": None, "transcript": "hello there"}],
    )
    httpx_mock.add_response(method="GET", url=f"{API}/bot/{BOT}", json=nameless)
    code, text = run(["--meet-url", MEET], {"SKRIBBY_API_KEY": KEY})
    assert "NO speaker name: meetings would be unlabelled" in text


def test_guest_mode_ignores_the_account_id(httpx_mock):
    accept(httpx_mock)
    httpx_mock.add_response(
        method="GET", url=f"{API}/bot/{BOT}", json=bot("not_admitted", stop_reason="request_denied")
    )
    code, text = run(
        ["--meet-url", MEET, "--guest"], {"SKRIBBY_API_KEY": KEY, "SKRIBBY_ACCOUNT_ID": ACCOUNT}
    )
    created = [
        r for r in httpx_mock.get_requests() if r.method == "POST" and r.url.path.endswith("/bot")
    ]
    assert b"authentication" not in created[0].content
    assert "guest bot" in text and "awaiting_admission_rejected" in text
    assert code == 1  # never admitted


def test_a_bot_that_is_never_let_in_is_stopped_at_the_lobby_limit(httpx_mock):
    accept(httpx_mock)
    httpx_mock.add_response(
        method="GET", url=f"{API}/bot/{BOT}", json=bot("joining", ("joining", 10))
    )
    # the stop leads to the end: the second answer onward says not admitted
    code, text = run(["--meet-url", MEET, "--lobby-limit", "20"], {"SKRIBBY_API_KEY": KEY})
    assert "not admitted within the lobby limit" in text
    assert any(str(r.url).endswith("/stop") for r in httpx_mock.get_requests())


def test_keys_are_read_from_the_file_when_the_environment_has_none(tmp_path):
    keys = tmp_path / "keys.env"
    keys.write_text(
        "# the bake-off's file\nexport SKRIBBY_API_KEY='skr-from-file'\n"
        "SKRIBBY_REGION=jp\nOTHER=1\n"
    )
    assert check.load_keys({}, keys) == {"SKRIBBY_API_KEY": "skr-from-file", "SKRIBBY_REGION": "jp"}
    assert check.load_keys({"SKRIBBY_API_KEY": "from-env"}, keys)["SKRIBBY_API_KEY"] == "from-env"


def test_a_missing_key_is_said_plainly_and_nothing_is_sent(httpx_mock):
    code, text = run(["--meet-url", MEET], {})
    assert code == 2 and "SKRIBBY_API_KEY is missing" in text
    assert httpx_mock.get_requests() == []


def test_a_teams_link_is_sent_whole_as_a_guest_even_with_an_account_id(httpx_mock):
    link = (
        "https://teams.microsoft.com/l/meetup-join/19%3ameeting_PLACEHOLDER%40thread.v2/0"
        "?context=%7b%22Tid%22%3a%22placeholder%22%7d"
    )
    accept(httpx_mock)
    httpx_mock.add_response(
        method="GET", url=f"{API}/bot/{BOT}", json=bot("not_admitted", stop_reason="request_denied")
    )
    code, text = run(["--meet-url", link], {"SKRIBBY_API_KEY": KEY, "SKRIBBY_ACCOUNT_ID": ACCOUNT})
    (created,) = [
        r for r in httpx_mock.get_requests() if r.method == "POST" and r.url.path.endswith("/bot")
    ]
    import json

    body = json.loads(created.content)
    assert body["service"] == "teams" and body["meeting_url"] == link
    assert "authentication" not in body
    assert "guest bot to teams meeting teams-" in text
    assert "PLACEHOLDER" not in text  # the link is never printed
    assert code == 1  # never admitted


def test_a_link_that_is_not_a_meeting_is_refused_before_any_request(httpx_mock):
    code, text = run(["--meet-url", "https://example.com/x"], {"SKRIBBY_API_KEY": KEY})
    assert code == 2 and "not a Google Meet, Zoom or Microsoft Teams" in text
    assert httpx_mock.get_requests() == []


def test_an_error_from_skribby_that_echoes_the_key_is_printed_without_it(httpx_mock):
    httpx_mock.add_response(
        method="POST", url=f"{API}/bot", status_code=400, json={"message": f"bad {KEY}"}
    )
    code, text = run(["--meet-url", MEET], {"SKRIBBY_API_KEY": KEY})
    assert code == 1 and "Skribby problem" in text and KEY not in text
