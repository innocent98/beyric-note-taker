"""The bake-off runner (tools/bakeoff/bakeoff.py), tested with a fake network: no vendor is called.

The fake vendors answer the way the raw vendor pages describe (docs/spike/primary-pages-2026-11-07):
Skribby's GET /bot carries events[] with data.new_status and created_at; Recall's GET /bot/{id}/
carries status_changes[] with code, sub_code and created_at. The Skribby `event` name inside
events[] is a guess (the page shows an empty string); the tool does not read it.
"""

import csv
import io
import json
from datetime import datetime, timedelta

import pytest

from tools.bakeoff import bakeoff
from tools.bakeoff.bakeoff import UTC, Context, Request, Response

MEET = "https://meet.google.com/abc-defg-hij"
START = datetime(2026, 11, 7, 10, 45, 0, tzinfo=UTC)
BASE = datetime(2026, 11, 7, 10, 45, 1, tzinfo=UTC)
SECRET_SKRIBBY = "sk_live_SKRIBBY_SECRET_0001"
SECRET_RECALL = "RECALL_SECRET_KEY_0002"


class FakeClock:
    def __init__(self):
        self.t = 0.0
        self.slept = []

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.t += seconds


class FakeTransport:
    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    def send(self, req, timeout=30.0):
        self.calls.append(req)
        answer = self.handler(req, len(self.calls))
        if isinstance(answer, Exception):
            raise answer
        return answer


def never(req, n):
    raise AssertionError(f"the network must not be used here: {req.method} {req.url}")


def iso(seconds):
    return (BASE + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def reply(status=200, body=None, headers=None):
    return Response(status, headers or {}, json.dumps(body) if body is not None else "")


class SkribbyFake:
    """Plays a timeline of (seconds after creation, status). Stopping adds `after_stop` steps."""

    def __init__(self, clock, timeline, after_stop):
        self.clock, self.timeline, self.after_stop = clock, timeline, after_stop
        self.created = None
        self.stopped = None
        self.stops = 0

    def __call__(self, req, n):
        if req.method == "POST" and req.url.endswith("/bot"):
            self.created = self.clock.t
            return reply(201, {"id": "bot-1", "status": "booting"})
        if req.url.endswith("/stop"):
            self.stops += 1
            self.stopped = self.stopped if self.stopped is not None else self.clock.t - self.created
            return reply(200, {"message": "Meeting bot is shutting down."})
        age = self.clock.t - self.created
        steps = [(at, status, None) for at, status in self.timeline if at <= age]
        if self.stopped is not None:
            steps += [
                (self.stopped + dt, s, r)
                for dt, s, r in self.after_stop
                if self.stopped + dt <= age
            ]
        events, prev = [], None
        for at, status, _ in steps:
            events.append(
                {
                    "event": "status_update",
                    "data": {"old_status": prev, "new_status": status},
                    "created_at": iso(at),
                }
            )
            prev = status
        body = {
            "id": "bot-1",
            "status": steps[-1][1],
            "stop_reason": steps[-1][2],
            "created_at": iso(0),
            "events": events,
            "finished_at": None,
        }
        if steps[-1][1] in ("finished", "not_admitted"):
            body["finished_at"] = iso(steps[-1][0])
        return reply(200, body)


ADMITTED_AFTER_STOP = [
    (1, "leaving", None),
    (2, "processing", None),
    (5, "finished", "manually_stopped"),
]
LOBBY_AFTER_STOP = [(2, "not_admitted", "manually_stopped")]


class RecallFake:
    def __init__(self, clock, timeline, after_stop):
        self.clock, self.timeline, self.after_stop = clock, timeline, after_stop
        self.created = None
        self.stopped = None
        self.leaves = 0

    def __call__(self, req, n):
        if req.method == "POST" and req.url.endswith("/api/v1/bot/"):
            self.created = self.clock.t
            return reply(201, {"id": "rbot-1", "status_changes": []})
        if req.url.endswith("/leave_call/"):
            self.leaves += 1
            self.stopped = self.stopped if self.stopped is not None else self.clock.t - self.created
            return reply(200, {"id": "rbot-1"})
        age = self.clock.t - self.created
        steps = [(at, code, sub) for at, code, sub in self.timeline if at <= age]
        if self.stopped is not None:
            steps += [
                (self.stopped + dt, c, s)
                for dt, c, s in self.after_stop
                if self.stopped + dt <= age
            ]
        changes = [
            {"code": c, "message": None, "created_at": iso(at), "sub_code": s} for at, c, s in steps
        ]
        return reply(200, {"id": "rbot-1", "status_changes": changes})


RECALL_AFTER_STOP = [(1, "call_ended", "bot_received_leave_call"), (3, "done", None)]


def keys_env(tmp_path, **extra):
    env = {
        "SKRIBBY_API_KEY": SECRET_SKRIBBY,
        "SKRIBBY_ACCOUNT_ID": "11111111-2222-3333-4444-555555555555",
        "RECALL_API_KEY": SECRET_RECALL,
        "RECALL_REGION": "us-west-2",
        "BAKEOFF_KEYS_FILE": str(tmp_path / "no-such-keys.env"),
    }
    env.update(extra)
    return env


def cli(argv, tmp_path, handler=never, clock=None, env=None):
    clock = clock or FakeClock()
    transport = FakeTransport(handler)
    out = io.StringIO()
    log = tmp_path / "logs" / "bakeoff.csv"
    code = bakeoff.main(
        ["--log", str(log), *argv],
        transport=transport,
        clock=clock,
        sleep=clock.sleep,
        now=lambda: START,
        out=out,
        env=env if env is not None else keys_env(tmp_path),
    )
    return code, out.getvalue(), transport, log


def rows_of(log):
    with open(log, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# ---------------------------------------------------------------------------------- time


def test_parse_ts_reads_z_offsets_and_long_fractions():
    assert bakeoff.parse_ts("2026-11-07T10:00:00Z") == datetime(2026, 11, 7, 10, 0, tzinfo=UTC)
    assert bakeoff.parse_ts("2026-11-07T10:00:00.1234567Z").microsecond == 123456
    assert bakeoff.parse_ts("2026-11-07T11:00:00+01:00") == datetime(2026, 11, 7, 10, 0, tzinfo=UTC)
    assert bakeoff.parse_ts("garbage") is None and bakeoff.parse_ts(None) is None


def test_seconds_come_from_vendor_timestamps_not_from_when_we_polled():
    data = {
        "id": "b",
        "status": "recording",
        "created_at": iso(0),
        "events": [
            {"data": {"new_status": "booting"}, "created_at": iso(0)},
            {"data": {"new_status": "joining"}, "created_at": iso(7.5)},
            {"data": {"new_status": "recording"}, "created_at": iso(41.25)},
        ],
    }
    snap = bakeoff.Skribby({}).snapshot(data, local_now=999.0)
    assert [(e.code, e.at) for e in snap.events] == [
        ("booting", 0.0),
        ("joining", 7.5),
        ("recording", 41.25),
    ]
    changes = {
        "status_changes": [
            {"code": "joining_call", "created_at": iso(10)},
            {"code": "bot.in_waiting_room", "created_at": iso(22.5)},
        ]
    }
    snap = bakeoff.Recall({}).snapshot(changes, local_now=999.0)
    assert [(e.code, e.at) for e in snap.events] == [
        ("joining_call", 0.0),
        ("in_waiting_room", 12.5),
    ]


# ---------------------------------------------------------------------------------- status mapping


def skribby_outcome(steps, status, stop=None, knock=20.0, lobby_hit=False):
    events = [{"data": {"new_status": s}, "created_at": iso(at)} for at, s in steps]
    data = {"status": status, "stop_reason": stop, "created_at": iso(0), "events": events}
    v = bakeoff.Skribby({})
    return v.classify(v.snapshot(data, 0.0), Context(knock, lobby_hit))


def test_skribby_quick_admission_is_not_a_knock():
    o = skribby_outcome([(0, "booting"), (5, "joining"), (15, "recording")], "recording")
    assert (o.result, o.seconds_to_admission, o.seconds_to_lobby) == ("admitted", 15.0, None)


def test_skribby_long_joining_is_read_as_a_knock_and_labelled_inferred():
    o = skribby_outcome([(0, "booting"), (5, "joining"), (65, "recording")], "recording")
    assert o.result == "knocked-then-admitted" and o.seconds_to_admission == 65.0
    assert any("inferred, not observed" in n for n in o.notes)


@pytest.mark.parametrize(
    ("status", "stop", "lobby_hit", "expected"),
    [
        ("not_admitted", "request_denied", False, "refused"),
        ("not_admitted", "waiting_room_timeout", False, "never-joined"),
        ("not_admitted", "manually_stopped", True, "never-joined"),
        ("not_admitted", "invalid_meeting_url", False, "error"),
        ("bot_detected", None, False, "refused"),
        ("auth_required", None, False, "refused"),
        ("invalid_credentials", None, False, "error"),
        ("failed", None, False, "error"),
        ("joining", None, True, "never-joined"),
    ],
)
def test_skribby_failure_mapping(status, stop, lobby_hit, expected):
    o = skribby_outcome([(0, "booting"), (5, "joining")], status, stop, lobby_hit=lobby_hit)
    assert o.result == expected


def test_skribby_bot_detected_is_not_final_until_it_stops_retrying():
    v = bakeoff.Skribby({})
    detected = {
        "status": "bot_detected",
        "created_at": iso(0),
        "events": [
            {"data": {"new_status": "booting"}, "created_at": iso(0)},
            {"data": {"new_status": "bot_detected"}, "created_at": iso(20)},
        ],
    }
    assert v.is_terminal(v.snapshot(detected, 0), quiet_for=10) is False
    assert v.is_terminal(v.snapshot(detected, 0), quiet_for=bakeoff.BOT_DETECTED_QUIET) is True
    four = dict(
        detected,
        events=[{"data": {"new_status": "bot_detected"}, "created_at": iso(i)} for i in range(4)],
    )
    assert v.is_terminal(v.snapshot(four, 0), quiet_for=0) is True


def test_skribby_recovering_after_bot_detected_counts_as_admitted_and_says_so():
    o = skribby_outcome(
        [
            (0, "booting"),
            (8, "joining"),
            (20, "bot_detected"),
            (40, "booting"),
            (48, "joining"),
            (60, "recording"),
        ],
        "recording",
    )
    assert o.result == "admitted" and o.seconds_to_admission == 60.0
    assert any("bot_detected x1" in n for n in o.notes)


def recall_outcome(steps, lobby_hit=False):
    changes = [{"code": c, "sub_code": s, "created_at": iso(at)} for at, c, s in steps]
    v = bakeoff.Recall({})
    return v.classify(
        v.snapshot({"status_changes": changes}, 0.0), Context(lobby_limit_hit=lobby_hit)
    )


def test_recall_straight_in_has_no_knock():
    o = recall_outcome([(0, "joining_call", None), (9, "in_call_recording", None)])
    assert (o.result, o.seconds_to_admission, o.seconds_to_lobby) == ("admitted", 9.0, None)


def test_recall_waiting_room_before_admission_is_an_observed_knock():
    o = recall_outcome(
        [
            (0, "joining_call", None),
            (6, "in_waiting_room", None),
            (31, "in_call_not_recording", None),
        ]
    )
    assert (o.result, o.seconds_to_lobby, o.seconds_to_admission) == (
        "knocked-then-admitted",
        6.0,
        31.0,
    )


@pytest.mark.parametrize(
    ("steps", "lobby_hit", "expected", "final"),
    [
        (
            [(0, "joining_call", None), (4, "fatal", "google_meet_bot_blocked"), (5, "done", None)],
            False,
            "refused",
            "fatal/google_meet_bot_blocked",
        ),
        (
            [(0, "joining_call", None), (4, "fatal", "google_meet_knocking_disabled")],
            False,
            "refused",
            None,
        ),
        (
            [(0, "joining_call", None), (4, "fatal", "google_meet_internal_error")],
            False,
            "error",
            None,
        ),
        (
            [(0, "joining_call", None), (4, "fatal", "some_new_code_we_do_not_know")],
            False,
            "error",
            None,
        ),
        (
            [
                (0, "joining_call", None),
                (3, "in_waiting_room", None),
                (63, "call_ended", "timeout_exceeded_waiting_room"),
            ],
            False,
            "never-joined",
            "call_ended/timeout_exceeded_waiting_room",
        ),
        (
            [
                (0, "joining_call", None),
                (3, "in_waiting_room", None),
                (9, "call_ended", "bot_kicked_from_waiting_room"),
            ],
            False,
            "refused",
            None,
        ),
        (
            [
                (0, "joining_call", None),
                (3, "in_waiting_room", None),
                (20, "call_ended", "bot_received_leave_call"),
            ],
            True,
            "never-joined",
            None,
        ),
    ],
)
def test_recall_failure_mapping(steps, lobby_hit, expected, final):
    o = recall_outcome(steps, lobby_hit)
    assert o.result == expected
    if final:
        assert o.final_status == final


# ---------------------------------------------------------------------------------- whole trials


def run_skribby(tmp_path, timeline, after_stop=ADMITTED_AFTER_STOP, extra=(), arm="skribby-guest"):
    clock = FakeClock()
    fake = SkribbyFake(clock, timeline, after_stop)
    code, out, transport, log = cli(
        ["run", "--arm", arm, "--meet-url", MEET, "--host-type", "self", *extra],
        tmp_path,
        fake,
        clock,
    )
    return code, out, transport, log, fake, clock


def test_skribby_trial_admitted_stays_a_minute_then_leaves(tmp_path):
    code, out, transport, log, fake, clock = run_skribby(
        tmp_path, [(0, "booting"), (6, "joining"), (40, "recording")], extra=["--note", "test call"]
    )
    assert code == 0 and fake.stops == 1
    (row,) = rows_of(log)
    assert row["result"] == "knocked-then-admitted"  # 34 s in joining, over the 20 s threshold
    assert row["seconds_to_admission"] == "40.0" and row["seconds_to_lobby"] == ""
    assert row["final_status"] == "finished/manually_stopped"
    assert (
        row["bot_minutes"] == "2.00"
    )  # 40 s + 60 s stay + 5 s to finish = 105 s, billed per started minute
    assert "stayed 60s then asked to leave" in row["notes"] and "test call" in row["notes"]
    assert "timeline booting+0s joining+6s recording+40s" in row["notes"]
    stop_after_admission = fake.stopped - 40
    assert 60 <= stop_after_admission < 60 + 5 + 1  # left at the first poll after 60 s, not before
    create = transport.calls[0]
    assert create.body["meeting_url"] == MEET and create.body["bot_name"] == "Ada W"
    assert "authentication" not in create.body
    assert create.body["stop_options"]["waiting_room_timeout"] == 10
    assert set(clock.slept) == {5.0}


def test_skribby_auth_arm_sends_the_account_and_always_authenticate(tmp_path):
    code, out, transport, log, fake, clock = run_skribby(
        tmp_path, [(0, "booting"), (6, "joining"), (12, "recording")], arm="skribby-auth"
    )
    body = transport.calls[0].body
    assert body["authentication"] == {
        "account_id": "11111111-2222-3333-4444-555555555555",
        "always_authenticate": True,
    }
    assert rows_of(log)[0]["result"] == "admitted"


def test_trial_that_waits_too_long_at_the_door_is_stopped_and_logged_never_joined(tmp_path):
    code, out, transport, log, fake, clock = run_skribby(
        tmp_path,
        [(0, "booting"), (6, "joining")],
        after_stop=LOBBY_AFTER_STOP,
        extra=["--lobby-limit", "60"],
    )
    assert fake.stops == 1
    (row,) = rows_of(log)
    assert (
        row["result"] == "never-joined" and row["final_status"] == "not_admitted/manually_stopped"
    )
    assert "lobby limit 60s reached" in row["notes"]
    assert transport.calls[0].body["stop_options"]["waiting_room_timeout"] == 1


def test_skribby_refusal_is_logged_with_its_reason(tmp_path):
    clock = FakeClock()
    steps = [(0, "booting"), (6, "joining")]

    class Refused(SkribbyFake):
        def __call__(self, req, n):
            resp = super().__call__(req, n)
            if req.method == "GET" and self.clock.t - self.created >= 30:
                body = resp.json()
                body.update(status="not_admitted", stop_reason="request_denied")
                body["events"].append(
                    {"data": {"new_status": "not_admitted"}, "created_at": iso(30)}
                )
                return reply(200, body)
            return resp

    code, out, *_, log = cli(
        ["run", "--arm", "skribby-guest", "--meet-url", MEET],
        tmp_path,
        Refused(clock, steps, []),
        clock,
    )
    assert code == 0
    (row,) = rows_of(log)
    assert row["result"] == "refused" and row["final_status"] == "not_admitted/request_denied"


def test_recall_trial_records_the_waiting_room(tmp_path):
    clock = FakeClock()
    fake = RecallFake(
        clock,
        [
            (0, "joining_call", None),
            (8, "in_waiting_room", None),
            (30, "in_call_not_recording", None),
        ],
        RECALL_AFTER_STOP,
    )
    code, out, transport, log = cli(
        [
            "run",
            "--arm",
            "recall-guest",
            "--meet-url",
            MEET,
            "--host-type",
            "other",
            "--stay",
            "30",
        ],
        tmp_path,
        fake,
        clock,
    )
    assert code == 0 and fake.leaves == 1
    (row,) = rows_of(log)
    assert row["result"] == "knocked-then-admitted"
    assert (row["seconds_to_lobby"], row["seconds_to_admission"]) == ("8.0", "30.0")
    assert (
        row["host_type"] == "other" and row["final_status"] == "call_ended/bot_received_leave_call"
    )
    assert (
        row["bot_minutes"] == "1.05"
    )  # joining_call to done: asked to leave at 60 s, done at 63 s
    create = transport.calls[0]
    assert create.url == "https://us-west-2.recall.ai/api/v1/bot/"
    assert create.headers["Authorization"] == f"Token {SECRET_RECALL}"
    assert create.body["bot_name"] == "Sam K" and create.body["automatic_leave"] == {
        "waiting_room_timeout": 600
    }
    assert create.body["recording_config"]["retention"] == {"type": "timed", "hours": 1}
    assert set(clock.slept) == {10.0}


def test_recall_fatal_block_ends_the_trial_at_once_as_refused(tmp_path):
    clock = FakeClock()
    fake = RecallFake(
        clock, [(0, "joining_call", None), (5, "fatal", "google_meet_bot_blocked")], []
    )
    code, out, transport, log = cli(
        ["run", "--arm", "recall-guest", "--meet-url", MEET], tmp_path, fake, clock
    )
    (row,) = rows_of(log)
    assert row["result"] == "refused" and row["final_status"] == "fatal/google_meet_bot_blocked"
    assert fake.leaves == 0  # the bot was already gone; nothing to ask
    assert "host-type was not given" in out and row["host_type"] == "unknown"


def test_create_failure_is_logged_as_an_error_with_no_minutes(tmp_path):
    def handler(req, n):
        return reply(
            403, {"message": "You have reached the limit of concurrent meeting bots for your plan."}
        )

    code, out, transport, log = cli(
        ["run", "--arm", "skribby-guest", "--meet-url", MEET], tmp_path, handler
    )
    (row,) = rows_of(log)
    assert row["result"] == "error" and row["bot_minutes"] == "0.00"
    assert (
        "concurrent meeting bots" in row["notes"] and len(transport.calls) == 1
    )  # 403 is not retried


def test_ctrl_c_asks_the_bot_to_leave_and_logs_an_error_row(tmp_path):
    clock = FakeClock()
    fake = SkribbyFake(clock, [(0, "booting"), (6, "joining")], LOBBY_AFTER_STOP)

    def sleeper(seconds):
        clock.sleep(seconds)
        if len(clock.slept) == 3:
            raise KeyboardInterrupt

    transport = FakeTransport(fake)
    out = io.StringIO()
    log = tmp_path / "bakeoff.csv"
    bakeoff.main(
        ["--log", str(log), "run", "--arm", "skribby-guest", "--meet-url", MEET],
        transport=transport,
        clock=clock,
        sleep=sleeper,
        now=lambda: START,
        out=out,
        env=keys_env(tmp_path),
    )
    assert fake.stops == 1
    (row,) = rows_of(log)
    assert row["result"] == "error" and row["final_status"] == "interrupted"


# ---------------------------------------------------------------------------------- politeness


def test_429_waits_for_retry_after_then_retries():
    clock = FakeClock()
    answers = [reply(429, {}, {"retry-after": "7"}), reply(200, {"ok": True})]
    client = bakeoff.Client(FakeTransport(lambda r, n: answers[n - 1]), clock.sleep)
    assert client.call(Request("GET", "https://x", {})).json() == {"ok": True}
    assert clock.slept == [7.0]


def test_busy_answers_are_retried_a_few_times_then_reported():
    clock = FakeClock()
    transport = FakeTransport(lambda r, n: reply(507, {"detail": "no bots"}))
    client = bakeoff.Client(transport, clock.sleep)
    with pytest.raises(bakeoff.ApiError) as err:
        client.call(Request("POST", "https://x", {}))
    assert err.value.status == 507 and len(transport.calls) == 5 and clock.slept == [30.0] * 4


def test_network_errors_back_off_then_give_up():
    clock = FakeClock()
    transport = FakeTransport(lambda r, n: bakeoff.NetworkError("down"))
    with pytest.raises(bakeoff.NetworkError):
        bakeoff.Client(transport, clock.sleep).call(Request("GET", "https://x", {}))
    assert len(transport.calls) == 4 and clock.slept == [2, 4, 8]


# ---------------------------------------------------------------------------------- budget


def seed(log, arm, minutes, day="2026-11-01"):
    bakeoff.append_row(
        log,
        {
            "time_utc": f"{day}T10:00:00Z",
            "arm": arm,
            "result": "admitted",
            "bot_minutes": f"{minutes:.2f}",
        },
    )


def test_used_minutes_adds_up_per_vendor_and_ignores_vexa(tmp_path):
    log = tmp_path / "l.csv"
    seed(log, "skribby-guest", 100)
    seed(log, "skribby-auth", 50.5)
    seed(log, "recall-guest", 30)
    seed(log, "vexa", 0)
    rows = bakeoff.read_rows(log)
    assert bakeoff.used_minutes(rows, "skribby") == 150.5
    assert bakeoff.used_minutes(rows, "recall") == 30
    assert bakeoff.used_minutes(rows, "skribby", since="2026-11-05") == 0


def test_cap_refuses_a_new_trial_without_touching_the_network_and_force_overrides(tmp_path):
    log = tmp_path / "logs" / "bakeoff.csv"
    seed(log, "skribby-guest", 270)  # 4.5 h
    seed(log, "recall-guest", 10)
    code, out, transport, _ = cli(["run", "--arm", "skribby-auth", "--meet-url", MEET], tmp_path)
    assert code == 3 and "4.5 h cap" in out and transport.calls == []
    code, out, transport, _ = cli(
        ["run", "--arm", "recall-guest", "--meet-url", MEET],
        tmp_path,
        RecallFake(FakeClock(), [], []),
    )
    assert code != 3  # Recall has its own, untouched budget
    clock = FakeClock()
    code, out, transport, _ = cli(
        ["run", "--arm", "skribby-auth", "--meet-url", MEET, "--force"],
        tmp_path,
        SkribbyFake(clock, [(0, "booting"), (3, "joining"), (6, "recording")], ADMITTED_AFTER_STOP),
        clock,
    )
    assert code == 0 and transport.calls


def test_cap_hours_option_and_environment_setting(tmp_path):
    log = tmp_path / "logs" / "bakeoff.csv"
    seed(log, "recall-guest", 61)
    code, *_ = cli(
        ["run", "--arm", "recall-guest", "--meet-url", MEET, "--cap-hours", "1"], tmp_path
    )
    assert code == 3
    code, *_ = cli(
        ["run", "--arm", "recall-guest", "--meet-url", MEET],
        tmp_path,
        env=keys_env(tmp_path, BAKEOFF_CAP_HOURS="1"),
    )
    assert code == 3


# ---------------------------------------------------------------------------------- the log file


def test_log_has_the_agreed_columns_and_is_append_only(tmp_path):
    for _ in range(2):
        clock = FakeClock()
        cli(
            ["run", "--arm", "skribby-guest", "--meet-url", MEET, "--host-type", "self"],
            tmp_path,
            SkribbyFake(
                clock, [(0, "booting"), (4, "joining"), (9, "recording")], ADMITTED_AFTER_STOP
            ),
            clock,
        )
    log = tmp_path / "logs" / "bakeoff.csv"
    text = log.read_text(encoding="utf-8")
    assert text.splitlines()[0] == (
        "time_utc,time_lagos,trial_id,arm,host_type,meeting_code,result,seconds_to_lobby,"
        "seconds_to_admission,final_status,bot_minutes,notes"
    )
    assert text.count("time_utc") == 1
    rows = rows_of(log)
    assert len(rows) == 2
    r = rows[0]
    assert r["time_utc"] == "2026-11-07T10:45:00Z" and r["time_lagos"] == "2026-11-07 11:45:00"
    assert r["trial_id"] == "20261107T104500Z-skribby-guest"
    assert r["meeting_code"] == "ghij" and "abc" not in text and "meet.google.com" not in text


def test_note_command_records_a_vexa_trial(tmp_path):
    code, out, _, log = cli(
        [
            "note",
            "--arm",
            "vexa",
            "--meet-url",
            MEET,
            "--result",
            "knocked-then-admitted",
            "--seconds-to-door",
            "47",
            "--host-type",
            "external",
        ],
        tmp_path,
    )
    assert code == 0
    code, out, _, log = cli(
        [
            "note",
            "--arm",
            "vexa",
            "--meet-url",
            MEET,
            "--result",
            "refused",
            "--seconds-to-door",
            "12",
            "--reason",
            "Google said it cannot join",
        ],
        tmp_path,
    )
    first, second = rows_of(log)
    assert (first["arm"], first["result"], first["seconds_to_admission"], first["host_type"]) == (
        "vexa",
        "knocked-then-admitted",
        "47.0",
        "external",
    )
    assert second["result"] == "refused" and second["final_status"] == "Google said it cannot join"
    assert second["seconds_to_admission"] == "" and "12s until the bot's answer" in second["notes"]
    assert "entered by hand" in second["notes"]


def test_note_rejects_other_arms_and_bad_results(tmp_path):
    with pytest.raises(SystemExit):
        cli(
            ["note", "--arm", "skribby-guest", "--meet-url", MEET, "--result", "admitted"], tmp_path
        )
    with pytest.raises(SystemExit):
        cli(["note", "--arm", "vexa", "--meet-url", MEET, "--result", "maybe"], tmp_path)


# ---------------------------------------------------------------------------------- the report


def test_report_numbers_and_plain_words(tmp_path):
    log = tmp_path / "logs" / "bakeoff.csv"

    def add(arm, result, secs="", final="", minutes=""):
        bakeoff.append_row(
            log,
            {
                "time_utc": "2026-11-07T10:00:00Z",
                "arm": arm,
                "result": result,
                "seconds_to_admission": secs,
                "final_status": final,
                "bot_minutes": minutes,
            },
        )

    add("recall-guest", "admitted", "10", "call_ended/bot_received_leave_call", "1.5")
    add("recall-guest", "knocked-then-admitted", "40", "", "2")
    add("recall-guest", "knocked-then-admitted", "120", "", "3")
    add("recall-guest", "refused", "", "fatal/google_meet_bot_blocked", "0.1")
    add("recall-guest", "refused", "", "fatal/google_meet_bot_blocked", "0.1")
    add("recall-guest", "error", "", "create-failed", "0")
    add("skribby-guest", "admitted", "20", "", "2")
    add("skribby-guest", "never-joined", "", "not_admitted/waiting_room_timeout", "10")
    add("vexa", "refused", "", "403", "")
    code, out, *_ = cli(["report"], tmp_path)
    assert code == 0
    assert "recall-guest" in out and "skribby-guest" in out and "vexa" in out
    assert "trials: 6 (5 counted, 1 errors" in out
    assert "admitted: 3 of 5 = 60%" in out
    assert "needed a knock: 2 of 3 admitted (seen as a waiting-room status)" in out
    assert "median 40, slowest 120" in out
    assert "fatal/google_meet_bot_blocked x2" in out
    assert "never joined: not_admitted/waiting_room_timeout x1" in out
    assert "inferred from time in 'joining'" in out
    assert "recall: 6.7 min" in out and "skribby: 12.0 min" in out
    assert "1 counted trial(s) in the smallest arm is too few to say anything yet" in out


def test_report_sentence_about_how_big_a_gap_must_be(tmp_path):
    log = tmp_path / "logs" / "bakeoff.csv"
    for arm in ("recall-guest", "skribby-guest"):
        for i in range(10):
            bakeoff.append_row(
                log,
                {
                    "time_utc": "2026-11-07T10:00:00Z",
                    "arm": arm,
                    "result": "admitted" if i < 7 else "refused",
                    "seconds_to_admission": "30",
                },
            )
    code, out, *_ = cli(["report"], tmp_path)
    assert (
        "with 10 trials in the smallest arm, only a big gap (about 38 percentage points or more)"
        in out
    )
    assert "10 trials per arm shows a big gap, not a small one" in out


def test_report_with_no_log_says_so(tmp_path):
    code, out, *_ = cli(["report"], tmp_path)
    assert code == 0 and "No trials yet" in out


def test_sample_size_helpers():
    assert bakeoff.gap_needed(10) == 38
    low, high = bakeoff.wilson(8, 10)
    assert 0.48 < low < 0.50 and 0.90 < high < 0.95
    assert bakeoff.wilson(0, 0) == (0.0, 0.0)


# ---------------------------------------------------------------------------------- secrets


def test_dry_run_prints_the_exact_request_hides_the_key_and_sends_nothing(tmp_path):
    code, out, transport, log = cli(
        ["run", "--arm", "skribby-auth", "--meet-url", MEET, "--dry-run"], tmp_path
    )
    assert code == 0 and transport.calls == [] and not log.exists()
    assert "POST https://platform.skribby.io/api/v1/bot\n" in out
    assert "Authorization: [redacted]" in out
    assert (
        '"always_authenticate": true' in out
        and '"meeting_url": "https://meet.google.com/abc-defg-hij"' in out
    )
    assert "GET https://platform.skribby.io/api/v1/bot/<bot id>" in out
    assert "POST https://platform.skribby.io/api/v1/bot/<bot id>/stop" in out
    assert SECRET_SKRIBBY not in out


def test_dry_run_needs_no_keys_and_shows_the_recall_request(tmp_path):
    code, out, transport, log = cli(
        ["run", "--arm", "recall-guest", "--meet-url", MEET, "--dry-run"],
        tmp_path,
        env={"BAKEOFF_KEYS_FILE": str(tmp_path / "none.env")},
    )
    assert code == 0 and transport.calls == []
    assert "POST https://<RECALL_REGION>.recall.ai/api/v1/bot/" in out and "[redacted]" in out
    assert '"waiting_room_timeout": 600' in out and '"bot_name": "Sam K"' in out


def test_no_key_ever_reaches_the_log_or_the_screen(tmp_path):
    def handler(req, n):  # a vendor error page that echoes the key it was sent
        return reply(401, {"detail": f"bad key {SECRET_SKRIBBY} sent as Bearer {SECRET_SKRIBBY}"})

    code, out, transport, log = cli(
        ["run", "--arm", "skribby-guest", "--meet-url", MEET], tmp_path, handler
    )
    everything = out + log.read_text(encoding="utf-8")
    assert SECRET_SKRIBBY not in everything and "[redacted]" in everything


def test_redaction_helpers():
    assert bakeoff.redact_headers(
        {"Authorization": "Bearer abc", "X-Api-Key": "k", "Accept": "x"}
    ) == {"Authorization": "[redacted]", "X-Api-Key": "[redacted]", "Accept": "x"}
    assert "abcdefgh12345" not in bakeoff.scrub("sent Token abcdefgh12345 ok")
    assert bakeoff.scrub("a SECRETVALUE b", ["SECRETVALUE"]) == "a [redacted] b"


def test_keys_file_is_read_and_the_environment_wins(tmp_path):
    f = tmp_path / "keys.env"
    f.write_text(
        "# my keys\nSKRIBBY_API_KEY='from-file'\nexport RECALL_REGION=eu-central-1\n"
        "OTHER=1\n\nRECALL_API_KEY=\n"
    )
    keys = bakeoff.load_keys({}, f)
    assert keys == {"SKRIBBY_API_KEY": "from-file", "RECALL_REGION": "eu-central-1"}
    keys = bakeoff.load_keys({"SKRIBBY_API_KEY": "from-env"}, f)
    assert keys["SKRIBBY_API_KEY"] == "from-env" and keys["RECALL_REGION"] == "eu-central-1"
    assert bakeoff.load_keys({}, tmp_path / "missing.env") == {}


def test_missing_key_is_a_plain_message_not_a_traceback(tmp_path):
    code, out, transport, _ = cli(
        ["run", "--arm", "recall-guest", "--meet-url", MEET],
        tmp_path,
        env={"BAKEOFF_KEYS_FILE": str(tmp_path / "none.env"), "RECALL_REGION": "us-west-2"},
    )
    assert code == 2 and "RECALL_API_KEY is missing" in out and "README step 3" in out
    assert transport.calls == []


def test_an_unknown_region_is_refused_so_the_key_cannot_go_to_another_host(tmp_path):
    code, out, transport, _ = cli(
        ["run", "--arm", "recall-guest", "--meet-url", MEET],
        tmp_path,
        env=keys_env(tmp_path, RECALL_REGION="evil.example.com/x"),
    )
    assert code == 2 and "RECALL_REGION must be one of" in out and transport.calls == []


# ---------------------------------------------------------------------------------- inputs


@pytest.mark.parametrize(
    "url",
    [
        "https://meet.google.com/lookup/abcd1234",
        "https://g.co/meet/abc",
        "http://meet.google.com/abc-defg-hij",
        "https://meet.google.com/",
        "https://zoom.us/j/123456789",
        "https://meet.google.com.evil.com/abc-defg-hij",
    ],
)
def test_only_standard_meet_links_are_accepted(url, tmp_path):
    code, out, transport, _ = cli(
        ["run", "--arm", "recall-guest", "--meet-url", url, "--dry-run"], tmp_path
    )
    assert code == 2 and "not a Google Meet link" in out and transport.calls == []


def test_meet_link_with_a_query_is_accepted_and_only_its_tail_is_kept():
    assert bakeoff.meeting_code("https://meet.google.com/abc-defg-hij?authuser=0") == "abc-defg-hij"
    assert bakeoff.code_tail("abc-defg-hij") == "ghij"


def test_lobby_limit_below_recalls_minimum_is_refused(tmp_path):
    code, out, *_ = cli(
        ["run", "--arm", "recall-guest", "--meet-url", MEET, "--lobby-limit", "10", "--dry-run"],
        tmp_path,
    )
    assert code == 2 and "at least 30" in out


# ---------------------------------------------------------------------------------- real transport


def test_real_transport_sends_json_and_headers_and_reads_error_answers():
    """The only test that opens a socket: a throwaway server on this computer, no vendor."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    seen = {}

    class Handler(BaseHTTPRequestHandler):
        def _answer(self):
            length = int(self.headers.get("Content-Length") or 0)
            seen["body"] = self.rfile.read(length).decode() if length else ""
            seen["auth"] = self.headers.get("Authorization")
            seen["agent"] = self.headers.get("User-Agent")
            status = 404 if self.path == "/missing" else 201
            self.send_response(status)
            self.send_header("Retry-After", "3")
            self.end_headers()
            self.wfile.write(b'{"id": "abc"}')

        do_POST = do_GET = _answer

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        transport = bakeoff.UrllibTransport()
        ok = transport.send(Request("POST", base + "/bot", {"Authorization": "Bearer k"}, {"a": 1}))
        assert ok.status == 201 and ok.json() == {"id": "abc"} and ok.headers["retry-after"] == "3"
        assert json.loads(seen["body"]) == {"a": 1} and seen["auth"] == "Bearer k"
        assert seen["agent"].startswith("beyric-bakeoff")
        assert transport.send(Request("GET", base + "/missing", {})).status == 404
    finally:
        server.shutdown()
        server.server_close()
    with pytest.raises(bakeoff.NetworkError):
        bakeoff.UrllibTransport().send(Request("GET", base + "/x", {}), timeout=2)
