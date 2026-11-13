"""`scripts/skribby_model_bakeoff.py`: upload one slice of a meeting to Skribby once per model and
score who separated the voices. Here Skribby is a pretend (httpx.MockTransport), the audio folder is
a temp one and ffmpeg is faked: no network, no database, no real audio."""

import json
from datetime import UTC, datetime
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
            if self.delete_status == 204:  # Skribby's real answer: no body
                return httpx.Response(204)
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


# ---- the checkpoint score ----------------------------------------------------------------------


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


# ---- the plan, the cost, the refusals ----------------------------------------------------------


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


# ---- the live path against a pretend Skribby ---------------------------------------------------


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


# ---- the delete: any 2xx is a success ----------------------------------------------------------


@pytest.mark.parametrize("status", [200, 202, 204])
def test_any_2xx_delete_is_a_success(meeting, capsys, status):
    fake = FakeSkribby(delete_status=status)
    code, _ = go(["--models", SOLO], fake)
    captured = capsys.readouterr()
    assert code == 0 and fake.count("DELETE") == 1
    assert "deleted recording rec-1" in captured.out
    assert "COULD NOT DELETE" not in captured.err


@pytest.mark.parametrize("status", [301, 404])
def test_a_non_2xx_delete_still_says_it_could_not_delete(meeting, capsys, status):
    go(["--models", SOLO], FakeSkribby(delete_status=status))
    err = capsys.readouterr().err
    assert f"COULD NOT DELETE recording rec-1 on Skribby (HTTP {status})" in err


# ---- Meet's own speaker timeline as the reference ----------------------------------------------

# Who Meet says spoke, in meeting seconds. Ann talks most; Ben 427-437; Cy 678-681 (3 s); Dee only
# 1 s at 700, under the 2 s that counts as having spoken in the slice.
PEOPLE = {
    "Ann": [(400.0, 427.0), (437.0, 678.0)],
    "Ben": [(427.0, 437.0)],
    "Cy": [(678.0, 681.0)],
    "Dee": [(700.0, 701.0)],
}
WINDOW = "400-900"


def record(people=PEOPLE, **extra):
    """The shape `app/skribby.py::speaker_events_record` keeps: participants with events."""
    return {
        "participants": [
            {
                "name": name,
                "events": [
                    e
                    for a, b in spans
                    for e in (
                        {"type": "started-speaking", "timestamp": a},
                        {"type": "stopped-speaking", "timestamp": b},
                    )
                ],
            }
            for name, spans in people.items()
        ],
        **extra,
    }


@pytest.fixture
def meet(monkeypatch):
    """The kept Meet record for meeting 7, replaceable per test."""
    holder = {"record": record()}
    monkeypatch.setattr(bake, "load_meet_record", lambda meeting_id: holder["record"])
    return holder


# What a model answers, in slice time (the slice starts at 400 s): every voice apart ...
SEPARATE = [seg(7, 0, 27), seg(7, 37, 278), seg(8, 27, 37), seg(9, 278, 281)]
# ... and Cy inside Ann's voice.
CY_IN_ANN = [seg(7, 0, 27), seg(7, 37, 278), seg(8, 27, 37), seg(7, 278, 281)]


def timeline_of(people=PEOPLE, offset=0.0):
    return bake.meet_timeline(record(people), offset)


def graded(segments, minimum=2.0, start=400.0, end=900.0):
    return bake.meet_score(turns_of(segments), timeline_of().spans, start, end, minimum)


def test_meet_times_in_seconds_are_read_as_they_are():
    timeline = timeline_of()
    assert timeline.spans["Ben"] == [(427.0, 437.0)]
    assert "seconds from the recording start" in timeline.how


def test_unix_millisecond_times_are_placed_from_the_recording_status_time_and_shifted():
    base = 1_700_000_000.0
    stamped = {n: [((base + a) * 1000, (base + b) * 1000) for a, b in v] for n, v in PEOPLE.items()}
    started = datetime.fromtimestamp(base, tz=UTC).isoformat()
    timeline = bake.meet_timeline(record(stamped, recording_started_at=started))
    assert timeline.spans["Ben"][0] == pytest.approx((427.0, 437.0))
    shifted = bake.meet_timeline(record(stamped, recording_started_at=started), 5.0)
    assert shifted.spans["Ben"][0] == pytest.approx(
        (432.0, 442.0)
    )  # offset is ADDED, as in the report
    with pytest.raises(dx.ExperimentError, match="cannot be placed"):
        bake.meet_timeline(record(stamped))  # Unix time but no `recording` status time kept


def test_a_missing_or_unreadable_timeline_is_refused_in_words():
    with pytest.raises(dx.ExperimentError, match="no Meet speaker events are kept"):
        bake.meet_timeline(None)
    with pytest.raises(dx.ExperimentError, match="not in a shape"):
        bake.meet_timeline({"participants": "oops"})


def test_a_participant_counts_only_with_enough_speech_in_the_slice():
    spans = timeline_of().spans
    assert set(bake.speech_in(spans, 400, 900, 2.0)) == {"Ann", "Ben", "Cy"}  # Dee: 1 s
    assert set(bake.speech_in(spans, 400, 900, 0.5)) == {"Ann", "Ben", "Cy", "Dee"}
    assert set(bake.speech_in(spans, 400, 430, 2.0)) == {"Ann", "Ben"}  # Ben 3 s of 427-437
    assert bake.speech_in(spans, 0, 100, 2.0) == {}


def test_every_voice_apart_is_separated_and_pure():
    result = graded(SEPARATE)
    assert result.people == ["Ann", "Ben", "Cy"] and result.voices == ["7", "8", "9"]
    assert result.matrix["7"] == {"Ann": 268.0}  # 27 s + 241 s
    assert result.matrix["8"] == {"Ben": 10.0} and result.matrix["9"] == {"Cy": 3.0}
    assert result.owner == {"Ann": "7", "Ben": "8", "Cy": "9"}
    assert result.purity == {"7": 1.0, "8": 1.0, "9": 1.0}
    assert result.alone == ["Ann", "Ben", "Cy"] and result.merged == []
    line = bake.verdict(result, SOLO)
    assert line == (
        f"Verdict {SOLO}: 3 voice(s) for 3 participant(s); separated 3 (Ann, Ben, Cy); "
        "merged into another's voice 0 (none)"
    )


def test_a_participant_inside_another_ones_voice_is_merged_into_it():
    result = graded(CY_IN_ANN)
    assert result.voices == ["7", "8"] and result.owner["Cy"] == "7"
    assert result.sharing == {"Ann": ["Cy"], "Ben": [], "Cy": ["Ann"]}
    assert result.merged == [("Cy", "Ann")] and result.hosts == ["Ann"]
    assert result.alone == ["Ben"]
    assert result.purity["7"] == pytest.approx(268 / 271)  # Ann's share of speaker 7's overlap
    line = bake.verdict(result, DUO)
    assert "2 voice(s) for 3 participant(s); separated 1 (Ben)" in line
    assert "merged into another's voice 1 (Cy into Ann's voice)" in line
    assert "voice shared with them, kept by: Ann" in line


def test_a_participant_nobody_was_talking_for_is_not_heard():
    result = graded(SEPARATE[:3])  # nothing at 678-681
    assert result.owner["Cy"] is None and result.unheard == ["Cy"]
    assert "not heard: Cy" in bake.verdict(result, SOLO)
    assert "no model speaker talking" in bake.meet_score_text(result, SOLO)


def test_a_split_voice_shows_how_much_of_the_speech_the_owner_holds():
    split = [seg(7, 0, 27), seg(6, 37, 278), seg(8, 27, 37), seg(9, 278, 281)]
    text = bake.meet_score_text(graded(split), SOLO)
    assert "Ann: spoke 268 s -> speaker 6 holds 241 s of it (90%); that speaker is 100% Ann" in text


def test_the_score_text_has_the_table_the_per_participant_lines_and_the_verdict():
    text = bake.meet_score_text(graded(CY_IN_ANN), DUO)
    assert "Meet says 3 participant(s) spoke in this slice; the model found 2 voice(s)." in text
    assert "model speaker (rows) x Meet participant (columns)" in text
    row = next(line for line in text.splitlines() if line.startswith("  7 "))
    assert row.split() == ["7", "268", "0", "3"]  # Ann, Ben, Cy
    assert (
        "Cy: spoke 3 s -> speaker 7 holds 3 s of it (100%); that speaker is 99% Cy; SHARED with Ann"
        in text
    )
    assert text.splitlines()[-1].startswith(f"Verdict {DUO}:")
    assert SECRET_WORDS not in text


def test_better_separation_ranks_higher():
    assert graded(SEPARATE).ranking() > graded(CY_IN_ANN).ranking()
    assert graded(CY_IN_ANN).ranking() > graded([seg(7, 0, 281)]).ranking()  # one voice for all


# ---- the window helper -------------------------------------------------------------------------

BUSY = {
    "Ann": [(0.0, 100.0), (600.0, 700.0)],
    "Ben": [(50.0, 120.0), (650.0, 720.0)],
    "Cy": [(640.0, 660.0)],
    "Dee": [(690.0, 710.0)],
}


def test_the_windows_with_most_distinct_speakers_come_first_and_do_not_overlap():
    windows = bake.best_windows(timeline_of(BUSY).spans, 300.0, 2.0, 3)
    assert [(round(a), round(b), len(who)) for a, b, who in windows] == [(420, 720, 4), (0, 300, 2)]
    assert windows[0][0] <= 640 and windows[0][1] >= 710  # holds Cy and Dee


def test_equal_counts_go_to_the_window_with_more_speech_then_the_earlier_one():
    spans = {"Ann": [(0.0, 10.0), (500.0, 560.0)], "Ben": [(0.0, 10.0), (500.0, 510.0)]}
    first = bake.best_windows(spans, 100.0, 2.0, 1)[0]
    # 60 s of speech in 450-550 (the last start that ends by the last speech) beats 20 s at 0
    assert (first[0], len(first[2])) == (450.0, 2)


def test_a_short_meeting_gets_one_window_and_a_silent_one_none():
    assert [w[:2] for w in bake.best_windows(timeline_of().spans, 3600.0, 2.0, 5)] == [
        (0.0, 3600.0)
    ]
    assert bake.best_windows({}, 600.0, 2.0, 3) == []


def test_list_windows_prints_the_choices_and_calls_nothing(meet, capsys, monkeypatch):
    meet["record"] = record(BUSY)
    monkeypatch.setattr(dx, "load_skribby_items", lambda meeting_id: ITEMS)

    def no_settings():
        raise AssertionError("the key was read to list windows")

    monkeypatch.setattr(bake, "get_settings", no_settings)
    fake = FakeSkribby()
    code = bake.run(["7", "--list-windows", "--window-length", "300"], http=fake.client())
    out = capsys.readouterr().out
    assert code == 0 and fake.calls == []
    assert "4 participant(s) in Meet's speaker timeline" in out
    assert "1. --window 420-720: 4 participant(s): Ann 100s, Ben 70s" in out
    assert "2. --window 0-300: 2 participant(s)" in out
    assert "Clock check:" in out
    assert "Estimated cost" not in out  # no audio was touched


def test_list_windows_without_events_stops_in_words(meet, capsys):
    meet["record"] = None
    assert bake.run(["7", "--list-windows"]) == 2
    assert "no Meet speaker events are kept" in capsys.readouterr().err


# ---- the clock check ---------------------------------------------------------------------------


def test_a_clock_that_is_off_by_seconds_is_flagged_with_the_offset_to_use():
    items = [
        {"speaker": 1, "start": 100.0, "end": 110.0},
        {"speaker": 1, "start": 200.0, "end": 210.0},
    ]
    late = bake.MeetTimeline({"Ann": [(105.0, 115.0), (205.0, 215.0)]}, "x", 0.0)
    assert "WARNING" in bake.clock_check(late, items)
    assert "--meet-offset -5" in bake.clock_check(late, items)
    exact = bake.MeetTimeline({"Ann": [(100.0, 110.0), (200.0, 210.0)]}, "x", 0.0)
    assert "no shift of up to 30 s" in bake.clock_check(exact, items)
    assert "could not be checked" in bake.clock_check(exact, [])


# ---- scoring a run against Meet ----------------------------------------------------------------


def test_the_meet_run_scores_each_model_and_ranks_the_summary(meeting, meet, capsys):
    fake = FakeSkribby({SOLO: CY_IN_ANN, DUO: SEPARATE})
    code, _ = go(["--score-against-meet", "--window", WINDOW, "--models", f"{SOLO},{DUO}"], fake)
    out = capsys.readouterr().out
    assert code == 0 and fake.count("POST") == 2 and fake.count("DELETE") == 2
    assert "Meet's timeline" in out and "3 participant(s) spoke at least 2 s" in out
    assert "Checkpoints" not in out  # the old mode's score is not printed
    summary = out.split("Summary:")[1].splitlines()
    ranked = [line for line in summary if line.startswith(("  1.", "  2."))]
    assert DUO in ranked[0] and "separated 3" in ranked[0]  # the better model is first
    assert SOLO in ranked[1] and "Cy into Ann's voice" in ranked[1]
    assert "Clock check" in summary[1]
    assert SECRET_WORDS not in out  # no text without --show-text
    assert no_scratch(meeting)


def test_the_meet_run_prints_text_only_with_show_text(meeting, meet, capsys):
    go(["--score-against-meet", "--window", WINDOW, "--models", SOLO, "--show-text", "425-430"])
    assert SECRET_WORDS in capsys.readouterr().out


def test_the_meet_run_needs_a_window_and_says_how_to_choose_one(meeting, meet, capsys):
    code, fake = go(["--score-against-meet"])
    assert code == 2 and fake.calls == []
    err = capsys.readouterr().err
    assert "needs --window" in err and "--list-windows" in err


def test_the_meet_run_refuses_a_window_with_fewer_than_two_speakers(meeting, meet, capsys):
    code, fake = go(["--score-against-meet", "--window", "440-600"])  # only Ann
    assert code == 2 and fake.calls == []
    err = capsys.readouterr().err
    assert "only 1 participant(s) spoke at least 2 s in 440-600 s" in err
    assert "Nothing was sent" in err


def test_the_meet_run_refuses_a_meeting_without_events_before_any_upload(meeting, meet, capsys):
    meet["record"] = None
    code, fake = go(["--score-against-meet", "--window", WINDOW])
    assert code == 2 and fake.calls == []
    assert "no Meet speaker events are kept" in capsys.readouterr().err
    assert no_scratch(meeting)


def test_the_meet_dry_run_shows_the_participants_and_sends_nothing(meeting, meet, capsys):
    code, fake = go(["--score-against-meet", "--window", WINDOW, "--dry-run"])
    out = capsys.readouterr().out
    assert code == 0 and fake.calls == []
    assert "3 participant(s) spoke at least 2 s in the slice" in out
    assert "Dry run: nothing was sent to Skribby." in out


def test_the_meet_run_ignores_the_checkpoints_and_honours_min_speech(meeting, meet, capsys):
    go(["--score-against-meet", "--window", WINDOW, "--dry-run", "--min-speech", "0.5"])
    assert "4 participant(s) spoke at least 0.5 s in the slice" in capsys.readouterr().out


def test_the_checkpoint_mode_still_works_and_its_default_window_is_unchanged(meeting, capsys):
    code, _ = go(["--models", SOLO])
    out = capsys.readouterr().out
    assert code == 0 and "Result: PASS" in out and "Meet" not in out
