"""`scripts/transcript_compare.py`: Skribby's transcript and ours side by side. No network and no
OpenAI: Skribby is a fake with only `get_bot` and `download` (so a call to anything else, such as a
delete, fails the test), and our transcription is a function that returns canned text."""

import io
import json

import pytest

from scripts import transcript_compare as cmp

KEY = "skr-compare-0123456789abcdef"
OPENAI = "sk-openai-0123456789abcdef"
BOT = "3c09b2aa-6708-42c1-8145-aa55ee223613"
LINK = "https://files.skribby.io/rec.webm?sig=SECRET-LINK-PART"

SEGMENTS = [
    {
        "start": 0,
        "end": 12,
        "speaker": 0,
        "speaker_name": "Ada Obi",
        "transcript": "Good morning everyone, let us start with the Beyric launch plan.",
    },
    {
        "start": 12,
        "end": 20,
        "speaker": 0,
        "speaker_name": "Ada Obi",
        "transcript": "First item is the pilot caps.",
    },
    {
        "start": 20,
        "end": 41,
        "speaker": 1,
        "speaker_name": "Tunde Bello",
        "transcript": "I think the caps are fine but the invite email needs work before Friday.",
    },
    {"start": 41, "end": 50, "speaker": 2, "speaker_name": "", "transcript": "Agreed."},
    {
        "start": 50,
        "end": 70,
        "speaker": 0,
        "speaker_name": "Ada Obi",
        "transcript": "Great, then Tunde owns the invite email and we meet again on Monday.",
    },
]
OUR_TEXT = (
    "Good morning everyone, let's start with the Beyric launch plan. First item is the pilot "
    "caps. I think the caps are fine, but the invite email needs work before Friday. Agreed. "
    "Great, then Tunde owns the invite email, and we meet again on Monday."
)


class FakeSkribby:
    """Only what the tool may call. Anything else (delete, stop) raises AttributeError."""

    def __init__(self, body=None, audio=b"RIFF-fake-audio"):
        self.body = (
            body
            if body is not None
            else {
                "id": BOT,
                "status": "finished",
                "stop_reason": "meeting_ended",
                "transcription_model": "soniox/stt-async-v5",
                "recording_url": LINK,
                "transcript": SEGMENTS,
            }
        )
        self.audio = audio
        self.asked = []
        self.downloaded = []

    def get_bot(self, bot_id):
        self.asked.append(bot_id)
        return self.body

    def download(self, url, path):
        self.downloaded.append(url)
        path.write_bytes(self.audio)


def run(argv, tmp_path, *, client=None, transcribe=None, env=None):
    out = io.StringIO()
    env = {"SKRIBBY_API_KEY": KEY, "OPENAI_API_KEY": OPENAI, **(env or {})}
    code = cmp.main(
        [*argv, "--keys-file", str(tmp_path / "none.env")],
        env=env,
        out=out,
        client=client,
        transcribe=transcribe,
    )
    return code, out.getvalue()


@pytest.fixture(autouse=True)
def no_probe(monkeypatch):
    """No ffprobe here: the recording's length is read from the last segment instead."""
    monkeypatch.setattr(cmp, "probe_seconds", lambda path: None)


# ---- words, turns, lining up ----


def test_word_key_ignores_case_and_punctuation():
    assert cmp.word_key("Let's,") == "let's"
    assert cmp.word_key("let’s") == "let's"
    assert cmp.word_key("—") == ""
    assert cmp.count_words("a  b\nc") == 3


def test_load_segments_keeps_names_and_falls_back_to_a_number():
    segments = cmp.load_segments({"transcript": SEGMENTS})
    assert [s["speaker"] for s in segments] == [
        "Ada Obi",
        "Ada Obi",
        "Tunde Bello",
        "Speaker 2",
        "Ada Obi",
    ]
    assert cmp.load_segments(SEGMENTS)[0]["text"].startswith("Good morning")  # a bare list too
    assert cmp.load_segments({"transcript": None}) == []
    assert cmp.load_segments({"transcript": [{"transcript": "  "}]}) == []


def test_load_segments_refuses_another_shape():
    with pytest.raises(cmp.CompareError):
        cmp.load_segments({"transcript": "words"})
    with pytest.raises(cmp.CompareError):
        cmp.load_segments({"transcript": ["a"]})


def test_time_scale_reads_milliseconds_when_far_longer_than_the_recording():
    segments = cmp.load_segments({"transcript": SEGMENTS})
    assert cmp.time_scale(segments, 70.0) == 1.0
    for seg in segments:
        seg["end"] *= 1000
    assert cmp.time_scale(segments, 70.0) == 1000.0
    assert cmp.time_scale([], None) == 1.0


def test_turns_merge_one_speakers_run():
    turns = cmp.make_turns(cmp.load_segments({"transcript": SEGMENTS}))
    assert [t["speaker"] for t in turns] == ["Ada Obi", "Tunde Bello", "Speaker 2", "Ada Obi"]
    assert turns[0]["start"] == 0 and turns[0]["end"] == 20
    assert "pilot caps" in turns[0]["text"]


def test_every_word_of_ours_lands_in_exactly_one_row_in_order():
    turns = cmp.make_turns(cmp.load_segments({"transcript": SEGMENTS}))
    rows = cmp.build_rows(turns, OUR_TEXT)
    assert " ".join(r["ours"] for r in rows).split() == OUR_TEXT.split()
    assert all(
        r["matched"] for r in rows if r["skribby"] != "Agreed."
    )  # one-word turn: by position
    assert rows[1]["ours"].startswith("I think the caps")
    assert rows[3]["ours"].startswith("Great, then Tunde")
    assert rows[1]["shared"] >= 90


def test_a_turn_whose_words_are_not_found_is_placed_by_position_and_marked():
    segs = [
        {"transcript": "alpha beta gamma delta epsilon", "speaker_name": "A"},
        {"transcript": "zzz yyy xxx www", "speaker_name": "B"},
        {"transcript": "lambda mu nu xi omicron", "speaker_name": "C"},
    ]
    ours = "alpha beta gamma delta epsilon one two three four lambda mu nu xi omicron"
    rows = cmp.build_rows(cmp.make_turns(cmp.load_segments(segs)), ours)
    assert rows[1]["matched"] is False
    assert rows[2]["matched"] is True
    assert " ".join(r["ours"] for r in rows) == ours  # nothing lost, nothing added


def test_ours_longer_than_skribby_and_empty_cases_do_not_break():
    turns = cmp.make_turns(cmp.load_segments({"transcript": SEGMENTS}))
    long_ours = OUR_TEXT + " " + "extra " * 500
    rows = cmp.build_rows(turns, long_ours)
    assert " ".join(r["ours"] for r in rows).split() == long_ours.split()
    empty = cmp.build_rows(turns, "")
    assert [r["ours"] for r in empty] == [""] * len(turns)
    no_skribby = cmp.build_rows([], OUR_TEXT)
    assert len(no_skribby) == 1 and no_skribby[0]["ours"] == OUR_TEXT
    assert no_skribby[0]["speaker"] is None


def test_punctuation_only_turn_at_the_end_does_not_break():
    segs = [
        {"transcript": "hello there friend", "speaker_name": "A"},
        {"transcript": "...", "speaker_name": "B"},
    ]
    rows = cmp.build_rows(cmp.make_turns(cmp.load_segments(segs)), "hello there friend")
    assert len(rows) == 2 and rows[0]["ours"] == "hello there friend"


# ---- cost and output ----


def test_costs_come_from_the_sop_numbers():
    today, switch = cmp.cost_paths(3600)
    assert today["per_hour"] == pytest.approx(0.35 + 0.04 + 0.70)
    assert switch["per_hour"] == pytest.approx(0.35 + 0.10)
    assert today["this_recording"] == pytest.approx(1.09)
    half = cmp.cost_paths(1800)
    assert half[1]["this_recording"] == pytest.approx(0.225)
    assert cmp.cost_paths(None)[0]["this_recording"] is None


def test_clock_reads_as_minutes_and_hours():
    assert cmp.clock(75) == "01:15"
    assert cmp.clock(3725) == "1:02:05"
    assert cmp.clock(None) == ""


def test_html_is_one_file_and_escapes_what_people_say_and_are_called():
    evil = [
        {
            "transcript": "<script>alert(1)</script> hello there friend",
            "speaker_name": "<img src=x onerror=alert(2)>",
        }
    ]
    turns = cmp.make_turns(cmp.load_segments(evil))
    rows = cmp.build_rows(turns, "<b>hello there friend</b>")
    stats = cmp.make_stats(turns, rows, "x", "y", 60, "gpt-transcribe", 0)
    page = cmp.render_html(rows, stats, "note")
    assert "<script>" not in page and "<img src" not in page and "<b>hello" not in page
    assert "&lt;script&gt;" in page
    assert "&lt;img" in page
    assert "<link" not in page and "<script" not in page  # nothing is loaded from anywhere


# ---- the two modes, end to end ----


def fake_ours(recording, work, terms, key, say):
    fake_ours.calls.append({"recording": recording, "terms": terms, "key": key})
    return OUR_TEXT


fake_ours.calls = []


def test_live_mode_fetches_saves_transcribes_and_writes_both_files(tmp_path):
    fake_ours.calls.clear()
    vocab = tmp_path / "vocab.txt"
    vocab.write_text("# my terms\nBeyric\n\nSoniox\n")
    folder = tmp_path / "out"
    client = FakeSkribby()
    code, text = run(
        ["--bot-id", BOT, "--out-dir", str(folder), "--vocabulary", str(vocab)],
        tmp_path,
        client=client,
        transcribe=fake_ours,
    )
    assert code == 0
    assert client.asked == [BOT] and client.downloaded == [LINK]  # only reads: no delete exists
    assert fake_ours.calls[0]["terms"] == ["Beyric", "Soniox"]
    saved = json.loads((folder / "skribby.json").read_text())
    assert "recording_url" not in saved and saved["transcript"][0]["speaker_name"] == "Ada Obi"
    assert (folder / "recording").read_bytes() == b"RIFF-fake-audio"
    assert (folder / "ours.txt").read_text() == OUR_TEXT
    page = (folder / "compare.html").read_text()
    plain = (folder / "compare.txt").read_text()
    assert "Ada Obi" in page and "Tunde Bello" in page and "Speaker 2" in page
    assert "Ada Obi" in plain and "SKRIBBY (" in plain and "OURS" in plain
    assert "1:10" in plain  # 70 s from the last segment: "01:10"
    assert "$1.09" in plain and "$0.45" in plain
    for secret in (KEY, OPENAI, "SECRET-LINK-PART"):
        assert secret not in text and secret not in page and secret not in plain
        assert secret not in (folder / "skribby.json").read_text()


def test_second_live_run_reuses_the_saved_files_and_never_asks_skribby(tmp_path):
    folder = tmp_path / "out"
    run(
        ["--bot-id", BOT, "--out-dir", str(folder)],
        tmp_path,
        client=FakeSkribby(),
        transcribe=fake_ours,
    )
    again = FakeSkribby()
    code, text = run(
        ["--bot-id", BOT, "--out-dir", str(folder), "--our-text", str(folder / "ours.txt")],
        tmp_path,
        client=again,
    )
    assert code == 0 and again.asked == [] and "already saved" in text


def test_offline_mode_needs_no_keys_and_no_network(tmp_path):
    folder = tmp_path / "saved"
    folder.mkdir()
    (folder / "skribby.json").write_text(json.dumps({"transcript": SEGMENTS}))
    (folder / "ours.txt").write_text(OUR_TEXT)
    code, text = run(
        [
            "--skribby-json",
            str(folder / "skribby.json"),
            "--our-text",
            str(folder / "ours.txt"),
            "--out-dir",
            str(folder),
        ],
        tmp_path,
        env={"SKRIBBY_API_KEY": "", "OPENAI_API_KEY": ""},
    )
    assert code == 0 and (folder / "compare.html").is_file()
    assert "no OpenAI call" in text


def test_offline_mode_with_audio_transcribes_it(tmp_path):
    fake_ours.calls.clear()
    audio = tmp_path / "rec.webm"
    audio.write_bytes(b"x")
    skribby = tmp_path / "s.json"
    skribby.write_text(json.dumps(SEGMENTS))
    code, _ = run(
        ["--audio", str(audio), "--skribby-json", str(skribby), "--out-dir", str(tmp_path / "o")],
        tmp_path,
        transcribe=fake_ours,
    )
    assert code == 0 and fake_ours.calls[0]["recording"] == audio
    assert (tmp_path / "o" / "ours.txt").read_text() == OUR_TEXT


@pytest.mark.parametrize(
    "argv, words",
    [
        ([], "either --bot-id"),
        (["--bot-id", BOT, "--audio", "a"], "either --bot-id"),
        (["--audio", "a"], "--skribby-json"),
        (["--skribby-json", "s"], "--audio, or --our-text"),
    ],
)
def test_mixed_up_arguments_are_explained(tmp_path, argv, words):
    code, text = run(argv, tmp_path)
    assert code == 2 and words in text


def test_missing_keys_are_explained_without_a_traceback(tmp_path):
    code, text = run(
        ["--bot-id", BOT, "--out-dir", str(tmp_path / "o")], tmp_path, env={"SKRIBBY_API_KEY": ""}
    )
    assert code == 2 and "SKRIBBY_API_KEY is missing" in text
    code, text = run(
        ["--bot-id", BOT, "--out-dir", str(tmp_path / "o")],
        tmp_path,
        client=FakeSkribby(),
        env={"OPENAI_API_KEY": ""},
    )
    assert code == 2 and "OPENAI_API_KEY is missing" in text


@pytest.mark.parametrize(
    "body, words",
    [
        ({"status": "recording", "transcript": []}, "not `finished`"),
        ({"status": "finished", "transcript": None, "recording_url": LINK}, "no transcript"),
        ({"status": "finished", "transcript": SEGMENTS}, "no recording"),
    ],
)
def test_a_bot_that_cannot_be_compared_says_why(tmp_path, body, words):
    code, text = run(
        ["--bot-id", BOT, "--out-dir", str(tmp_path / "o")],
        tmp_path,
        client=FakeSkribby(body),
        transcribe=fake_ours,
    )
    assert code == 2 and words in text


def test_a_failed_download_leaves_no_half_file(tmp_path):
    class Broken(FakeSkribby):
        def download(self, url, path):
            from app.skribby import SkribbyError

            path.write_bytes(b"half")
            raise SkribbyError(f"download failed for {url}")

    code, text = run(
        ["--bot-id", BOT, "--out-dir", str(tmp_path / "o")],
        tmp_path,
        client=Broken(),
        transcribe=fake_ours,
    )
    assert code == 2 and "could not be downloaded" in text
    assert "SECRET-LINK-PART" not in text
    assert not (tmp_path / "o" / "recording").exists()
    assert not (tmp_path / "o" / "recording.part").exists()


def test_the_openai_key_can_come_from_the_keys_file_and_is_never_printed(tmp_path):
    keys = tmp_path / "keys.env"
    keys.write_text(f"export OPENAI_API_KEY='{OPENAI}'\nSKRIBBY_API_KEY={KEY}\n")
    fake_ours.calls.clear()
    out = io.StringIO()
    code = cmp.main(
        ["--bot-id", BOT, "--out-dir", str(tmp_path / "o"), "--keys-file", str(keys)],
        env={},
        out=out,
        client=FakeSkribby(),
        transcribe=fake_ours,
    )
    assert code == 0 and fake_ours.calls[0]["key"] == OPENAI
    assert OPENAI not in out.getvalue() and KEY not in out.getvalue()
