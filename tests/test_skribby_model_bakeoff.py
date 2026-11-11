"""`scripts/skribby_model_bakeoff.py`: upload one slice of a meeting to Skribby once per model and
score who separated the voices. Here Skribby is a pretend (httpx.MockTransport), the audio folder is
a temp one and ffmpeg is faked: no network, no database, no real audio."""

import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app import audio
from scripts import diarize_experiment as dx
from scripts import skribby_model_bakeoff as bake

SECRET_WORDS = "the budget is two million"
SOLO = "soniox/stt-async-v6"
DUO = "assemblyai/universal-3-5-pro"


def seg(speaker, start, end, text=SECRET_WORDS):
    return {"speaker": speaker, "start": start, "end": end, "transcript": text, "speaker_name": "x"}


# The old Skribby evidence for the meeting (meeting seconds): tag 1 is Adebayo, and Daniel's speech
# (427-436 and 678-679) sits inside tag 2, Paul's.
ITEMS = [
    {"speaker": 1, "speaker_name": "Adebayo", "start": 400.0, "end": 427.0, "text": "a"},
    {"speaker": 2, "speaker_name": "Paul", "start": 427.0, "end": 437.0, "text": "b"},
    {"speaker": 1, "speaker_name": "Adebayo", "start": 437.0, "end": 678.0, "text": "c"},
    {"speaker": 2, "speaker_name": "Paul", "start": 678.0, "end": 680.0, "text": "d"},
]
# What a model that separates all three voices answers, in SLICE time (the slice starts at 400 s).
GOOD = [
    seg(7, 0, 26),  # Adebayo
    seg(7, 100, 270),  # Adebayo, a long stretch
    seg(8, 27, 28),  # Daniel (427-428)
    seg(8, 31, 36),  # Daniel (431-436)
    seg(9, 40, 60),  # Paul
    seg(7, 275, 277),  # Adebayo (675-677)
    seg(8, 278, 279),  # Daniel (678-679)
]
# A model that leaves Daniel inside Adebayo's voice for one checkpoint, as Skribby's old one did.
MERGED = [
    seg(7, 0, 26),
    seg(7, 100, 270),
    seg(8, 27, 28),
    seg(8, 31, 36),
    seg(9, 40, 60),
    seg(7, 275, 277),
    seg(7, 278, 279),
]


class FakeSkribby:
    """Records every call and answers like the documented recording operations."""

    def __init__(self, answers=None, *, polls=1, upload_status=200, delete_status=200, status=None):
        self.answers = answers or {}
        self.polls = polls  # GETs that say "transcribing" before the final one
        self.upload_status = upload_status
        self.delete_status = delete_status
        self.final_status = status or "finished"
        self.calls = []  # (method, path)
        self.models = {}  # recording id -> model
        self.bodies = []  # the raw upload bodies
        self.seen = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.method, request.url.path))
        if request.method == "POST":
            self.bodies.append(request.content)
            if self.upload_status != 200:
                return httpx.Response(self.upload_status, json={"message": "no capacity"})
            text = request.content.decode("latin-1")
            model = next(m for m in (*bake.MODEL_PRICE_PER_HOUR, "weird/model") if m in text)
            rec = f"rec-{len(self.models) + 1}"
            self.models[rec] = model
            return httpx.Response(200, json={"id": rec, "status": "transcribing"})
        rec = request.url.path.rsplit("/", 1)[1]
        if request.method == "DELETE":
            return httpx.Response(self.delete_status, json={"message": "Recording deleted"})
        count = self.seen[rec] = self.seen.get(rec, 0) + 1
        if count <= self.polls:
            return httpx.Response(200, json={"id": rec, "status": "transcribing"})
        transcript = self.answers.get(self.models[rec], GOOD)
        return httpx.Response(
            200, json={"id": rec, "status": self.final_status, "transcript": transcript}
        )

    def client(self):
        transport = httpx.MockTransport(self)
        return httpx.Client(transport=transport, base_url="https://skribby.test/api/v1")

    def count(self, method):
        return sum(1 for m, _ in self.calls if m == method)


@pytest.fixture
def meeting(tmp_path, monkeypatch):
    """Two kept chunks (600 s and 300 s) for meeting 7, the old evidence, a fake ffmpeg."""
    folder = tmp_path / "7"
    (folder / "chunks").mkdir(parents=True)
    for name in ("chunk_000.webm", "chunk_001.webm"):
        (folder / "chunks" / name).write_bytes(b"opus")
    monkeypatch.setattr(dx, "audio_path", lambda meeting_id: folder / "source.webm")
    seconds = {"chunk_000.webm": 600.0, "chunk_001.webm": 300.0}
    monkeypatch.setattr(audio, "_duration", lambda path: seconds[path.name])
    monkeypatch.setattr(dx, "load_skribby_items", lambda meeting_id: ITEMS)
    monkeypatch.setattr(bake, "POLL_SECONDS", 0)
    monkeypatch.setattr(dx, "HEARTBEAT_SECONDS", 3600)

    def fake_ffmpeg(*args):
        Path(args[-1]).write_bytes(b"opus-slice")

    monkeypatch.setattr(audio, "_ffmpeg", fake_ffmpeg)
    return folder


def no_scratch(folder):
    return [p.name for p in folder.iterdir() if p.name.startswith("diarize-experiment")] == []


def go(argv, fake=None, **kwargs):
    fake = fake or FakeSkribby()
    return bake.run(["7", *argv], http=fake.client(), **kwargs), fake


# ---- the checkpoint score ---------------------------------------------------------------------


def turns_of(segments, offset=400.0):
    return bake.model_turns({"transcript": segments}, offset)


def test_turns_are_moved_to_meeting_time_and_odd_segments_are_skipped():
    turns = bake.model_turns(
        {
            "transcript": [
                seg(1, 27, 28),
                {"speaker": None, "start": 1, "end": 2, "transcript": "x"},
                {"speaker": 3, "start": None, "end": 2},
                {"speaker": 3, "start": True, "end": 2},
            ]
        },
        400.0,
    )
    assert [(t.speaker, t.start, t.end) for t in turns] == [
        ("1", 427.0, 428.0),
        ("(no tag)", 401.0, 402.0),
    ]
    assert bake.model_turns({}, 0.0) == []
    with pytest.raises(dx.ExperimentError):
        bake.model_turns({"transcript": "oops"}, 0.0)


def test_the_reference_voice_is_the_model_speaker_most_overlapping_old_tag_1():
    assert bake.reference_voice(turns_of(GOOD), ITEMS, "1") == "7"
    assert bake.reference_voice(turns_of(GOOD), ITEMS, "2") == "8"  # Daniel sits in old tag 2
    assert bake.reference_voice(turns_of(GOOD), ITEMS, "9") is None


def test_a_model_that_puts_daniel_in_one_other_speaker_passes():
    result = bake.score(turns_of(GOOD), ITEMS, bake.load_checkpoints(None))
    assert result.voice == "7"
    assert [row[3] for row in result.rows] == ["8", "8", "8", "7"]
    assert result.people == {"Daniel": True}
    assert result.passed


def test_a_daniel_checkpoint_inside_the_adebayo_voice_fails():
    result = bake.score(turns_of(MERGED), ITEMS, bake.load_checkpoints(None))
    assert [r[4] for r in result.rows] == [True, True, False, True]
    assert result.people == {"Daniel": False}  # also not all in ONE speaker
    assert not result.passed


def test_daniel_split_over_two_non_adebayo_speakers_fails():
    split = [*GOOD[:3], seg(5, 31, 36), *GOOD[4:]]
    result = bake.score(turns_of(split), ITEMS, bake.load_checkpoints(None))
    assert all(r[4] for r in result.rows) and result.people == {"Daniel": False}
    assert not result.passed


def test_the_adebayo_checkpoint_outside_the_adebayo_voice_fails():
    wrong = [*GOOD[:5], seg(9, 275, 277), GOOD[6]]
    result = bake.score(turns_of(wrong), ITEMS, bake.load_checkpoints(None))
    assert [r[4] for r in result.rows] == [True, True, True, False]
    assert not result.passed


def test_a_checkpoint_where_nobody_speaks_fails():
    result = bake.score(turns_of(GOOD[:4]), ITEMS, bake.load_checkpoints(None))
    assert result.rows[2][3] is None and not result.passed
    assert "nobody speaking" in bake.score_text(result, bake.load_checkpoints(None))


def test_the_default_checkpoints_are_meeting_32s_and_a_file_can_replace_them(tmp_path):
    default = bake.load_checkpoints(None)
    assert default.reference_tag == "1" and default.reference_person == "Adebayo"
    assert default.points == (
        ("Daniel", 427.0, 428.0),
        ("Daniel", 431.0, 436.0),
        ("Daniel", 678.0, 679.0),
        ("Adebayo", 675.0, 677.0),
    )
    path = tmp_path / "c.json"
    path.write_text(
        json.dumps(
            {
                "reference_tag": "3",
                "reference_person": "Ann",
                "checkpoints": [{"person": "Ann", "start": 1, "end": 2}],
            }
        )
    )
    assert bake.load_checkpoints(str(path)).points == (("Ann", 1.0, 2.0),)
    path.write_text("{}")
    with pytest.raises(dx.ExperimentError):
        bake.load_checkpoints(str(path))
    with pytest.raises(dx.ExperimentError):
        bake.load_checkpoints(str(tmp_path / "missing.json"))


def test_score_text_prints_pass_and_fail_per_checkpoint_and_per_model():
    text = bake.score_text(
        bake.score(turns_of(MERGED), ITEMS, bake.load_checkpoints(None)),
        bake.load_checkpoints(None),
    )
    assert "Daniel 427-428s -> speaker 8: PASS" in text
    assert "Daniel 678-679s -> speaker 7: FAIL" in text
    assert "Daniel: all checkpoints in ONE speaker: FAIL" in text
    assert text.endswith("Result: FAIL")


# ---- the plan, the cost, the refusals ---------------------------------------------------------


def test_estimated_cost_is_the_slice_hours_times_base_plus_the_models_rate():
    assert bake.estimate(3600, 0.10) == pytest.approx(0.20)
    assert bake.estimate(600, 0.32) == pytest.approx(0.07)  # 10 minutes of Deepgram nova-3


def test_dry_run_shows_the_cost_per_model_and_makes_no_call_and_needs_no_key(
    meeting, capsys, monkeypatch
):
    def no_settings():
        raise AssertionError("the key was read in a dry run")

    monkeypatch.setattr(bake, "get_settings", no_settings)
    fake = FakeSkribby()
    assert bake.run(["7", "--dry-run"], http=fake.client()) == 0
    out = capsys.readouterr().out
    assert fake.calls == []
    assert "Slice: 8.3 minutes" in out  # the default window 400-1000 ends at the audio's end, 900 s
    assert "soniox/stt-async-v6: $0.028" in out  # 500 s: 500/3600 x (0.10 + 0.10)
    assert "deepgram/nova-3-multilingual: $0.058" in out
    assert "total for 4 model(s): $0.174" in out
    assert "Dry run: nothing was sent to Skribby." in out
    assert no_scratch(meeting)


def test_an_unknown_model_is_refused_unless_a_price_is_given(meeting, capsys):
    code, fake = go(["--models", "weird/model"])
    assert code == 2 and fake.calls == []
    assert "weird/model is not in the price table" in capsys.readouterr().err
    code, fake = go(["--models", "weird/model", "--price", "0.5", "--dry-run"])
    assert code == 0
    assert "weird/model: $0.083" in capsys.readouterr().out


def test_the_deprecated_soniox_v5_is_refused(meeting, capsys):
    code, fake = go(["--models", "soniox/stt-async-v5", "--price", "0.1"])
    assert code == 2 and fake.calls == []
    assert "deprecated" in capsys.readouterr().err


def test_more_than_max_minutes_per_model_is_refused_before_any_upload(meeting, capsys):
    code, fake = go(["--max-minutes", "5"])
    assert code == 2 and fake.calls == []
    assert "more than --max-minutes 5 per model" in capsys.readouterr().err
    assert no_scratch(meeting)


def test_checkpoints_outside_the_window_are_refused_before_anything_is_cut(meeting, capsys):
    code, fake = go(["--window", "500-700"])
    assert code == 2 and fake.calls == []
    assert "fall outside the window" in capsys.readouterr().err


# ---- the live path against a pretend Skribby --------------------------------------------------


def test_one_model_is_uploaded_once_polled_scored_and_deleted(meeting, capsys):
    code, fake = go(["--models", SOLO])
    assert code == 0
    assert fake.calls == [
        ("POST", "/api/v1/recording"),
        ("GET", "/api/v1/recording/rec-1"),
        ("GET", "/api/v1/recording/rec-1"),
        ("DELETE", "/api/v1/recording/rec-1"),
    ]
    body = fake.bodies[0]
    assert b'name="recording_file"' in body and b"opus-slice" in body
    assert b'name="transcription_model"' in body and SOLO.encode() in body
    assert b"custom_vocabulary" not in body  # the docs do not say which models accept it
    out = capsys.readouterr().out
    assert "The model found 3 distinct speaker(s)" in out
    assert "Result: PASS" in out and f"{SOLO}: 3 speaker(s), PASS" in out
    assert "deleted recording rec-1" in out
    assert "tag 1 (Adebayo)" in out  # the old-tag table
    assert SECRET_WORDS not in out  # no text without --show-text
    assert no_scratch(meeting)


def test_each_model_gets_its_own_upload_and_its_own_verdict(meeting, capsys):
    fake = FakeSkribby({SOLO: GOOD, DUO: MERGED})
    code, _ = go(["--models", f"{SOLO},{DUO}"], fake)
    assert code == 0
    assert fake.count("POST") == 2 and fake.count("DELETE") == 2
    out = capsys.readouterr().out
    assert f"{SOLO}: 3 speaker(s), PASS" in out and f"{DUO}: 3 speaker(s), FAIL" in out


def test_times_come_back_in_meeting_time_so_the_old_tag_table_lines_up(meeting, capsys):
    go(["--models", SOLO])
    out = capsys.readouterr().out
    rows = {
        line.split()[0]: line.split()[1:]
        for line in out.splitlines()
        if line[:3] in {"  7", "  8", "  9"}
    }
    # speaker 8 (Daniel) overlaps old tag 2 (Paul) for 1 + 5 + 1 s: only possible at 427, 431, 678
    assert rows["8"] == ["0", "7"]


def test_text_is_printed_only_with_show_text(meeting, capsys):
    go(["--models", SOLO, "--show-text", "425-430"])
    out = capsys.readouterr().out
    assert f"7:07-7:08 8: {SECRET_WORDS}" in out


def test_a_failed_upload_stops_the_run_with_one_attempt_and_says_what_was_sent(meeting, capsys):
    fake = FakeSkribby(upload_status=500)
    code, _ = go(["--models", f"{SOLO},{DUO}"], fake)
    assert code == 2
    assert fake.count("POST") == 1  # no retry and the second model is never tried
    assert fake.count("DELETE") == 0  # nothing was created, so nothing to delete
    err = capsys.readouterr().err
    assert "upload to soniox/stt-async-v6: HTTP 500: no capacity" in err
    assert f"Already sent to Skribby: {SOLO}" in err and DUO not in err
    assert no_scratch(meeting)


def test_a_failed_transcription_still_deletes_the_recording_and_stops(meeting, capsys):
    fake = FakeSkribby(status="failed")
    code, _ = go(["--models", f"{SOLO},{DUO}"], fake)
    assert code == 2 and fake.count("POST") == 1 and fake.count("DELETE") == 1
    assert "Skribby reported status 'failed'" in capsys.readouterr().err


def test_a_poll_timeout_still_deletes_the_recording(meeting, capsys):
    fake = FakeSkribby(polls=99)
    code, _ = go(["--models", SOLO, "--poll-timeout", "0"], fake)
    assert code == 2 and fake.count("DELETE") == 1
    assert "not finished after 0s (last status 'transcribing')" in capsys.readouterr().err


def test_a_delete_that_fails_is_said_loudly_and_does_not_hide_the_result(meeting, capsys):
    fake = FakeSkribby(delete_status=500)
    code, _ = go(["--models", SOLO], fake)
    assert code == 0
    captured = capsys.readouterr()
    assert "COULD NOT DELETE recording rec-1" in captured.err
    assert "Result: PASS" in captured.out


def test_an_error_message_never_carries_the_key():
    response = httpx.Response(400, json={"message": "bad key sk-secret-123 here"})
    error = bake._failure("upload", response, key="sk-secret-123")
    assert "sk-secret-123" not in str(error) and "[redacted]" in str(error)
    assert "refused the API key" in str(bake._failure("x", httpx.Response(401)))


def test_a_missing_key_stops_before_any_upload(meeting, monkeypatch, capsys):
    monkeypatch.setattr(
        bake, "get_settings", lambda: SimpleNamespace(skribby_api_key=" ", skribby_region="eu")
    )
    assert bake.run(["7", "--models", SOLO]) == 2
    assert "SKRIBBY_API_KEY is not set" in capsys.readouterr().err
