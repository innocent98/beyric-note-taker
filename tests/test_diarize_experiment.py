"""`scripts/diarize_experiment.py`: the read-only check of whether a diarizing model separates the
voices Skribby merged. Here it runs against a fake OpenAI client and a temporary audio folder: no
network, no database, no ffmpeg."""

from types import SimpleNamespace

import pytest

from app import audio
from scripts import diarize_experiment as exp

SECRET_WORDS = "the budget is two million"


def segment(speaker, start, end, text=SECRET_WORDS):
    return SimpleNamespace(speaker=speaker, start=start, end=end, text=text)


class FakeClient:
    """Answers each create() call with the next scripted list of segments."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []
        self.audio = SimpleNamespace(transcriptions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls.append({k: v for k, v in kwargs.items() if k != "file"})
        return SimpleNamespace(segments=self.answers.pop(0))


@pytest.fixture
def meeting_audio(tmp_path, monkeypatch):
    """Two kept chunks of 600s and 300s for meeting 7, and nothing to read from the database."""
    folder = tmp_path / "7"
    (folder / "chunks").mkdir(parents=True)
    for name in ("chunk_000.webm", "chunk_001.webm"):
        (folder / "chunks" / name).write_bytes(b"opus")
    monkeypatch.setattr(exp, "audio_path", lambda meeting_id: folder / "source.webm")
    seconds = {"chunk_000.webm": 600.0, "chunk_001.webm": 300.0}
    monkeypatch.setattr(audio, "_duration", lambda path: seconds[path.name])
    monkeypatch.setattr(exp, "load_skribby_items", lambda meeting_id: ITEMS)
    return folder


# Skribby put both people under tag "speaker_0" from 0 to 100s; "speaker_1" is the third voice.
ITEMS = [
    {"speaker": "speaker_0", "speaker_name": "Paul", "start": 0.0, "end": 100.0, "text": "x"},
    {"speaker": "speaker_1", "speaker_name": "Ada", "start": 100.0, "end": 200.0, "text": "y"},
]


def test_speaker_stats_counts_turns_and_seconds():
    turns = [
        exp.Turn("A", 0, 10, ""),
        exp.Turn("B", 10, 15, ""),
        exp.Turn("A", 20, 25, ""),
    ]
    assert exp.speaker_stats(turns) == {"A": (2, 15.0), "B": (1, 5.0)}


def test_overlap_table_shows_one_tag_holding_two_model_voices():
    turns = [exp.Turn("A", 0, 60, ""), exp.Turn("B", 60, 100, ""), exp.Turn("C", 110, 130, "")]
    table = exp.overlap_table(turns, ITEMS)
    assert table["A"] == {"speaker_0": 60.0}
    assert table["B"] == {"speaker_0": 40.0}  # A and B both sit inside tag speaker_0
    assert table["C"] == {"speaker_1": 20.0}


def test_overlap_counts_partial_overlap_and_skips_items_without_times():
    turns = [exp.Turn("A", 90, 110, "")]
    items = [*ITEMS, {"speaker": "speaker_2", "start": None, "end": None}, {"speaker_name": "?"}]
    assert exp.overlap_table(turns, items) == {"A": {"speaker_0": 10.0, "speaker_1": 10.0}}


def test_an_item_with_no_tag_is_its_own_column():
    turns = [exp.Turn("A", 0, 10, "")]
    items = [{"speaker": None, "start": 0, "end": 5}]
    assert exp.overlap_table(turns, items) == {"A": {exp.NO_TAG: 5.0}}


def test_each_chunk_is_offset_by_the_chunks_before_it(meeting_audio, capsys):
    client = FakeClient([segment("A", 1.0, 4.0)], [segment("A", 2.0, 5.0), segment("B", 6.0, 9.0)])
    assert exp.run(["7"], client=client) == 0
    chunks = exp.measure(sorted((meeting_audio / "chunks").glob("chunk_*.webm")))
    assert [c.offset for c in chunks] == [0.0, 600.0]
    out = capsys.readouterr().out
    assert "The model found 2 distinct speaker(s) in 3 turn(s)." in out
    assert "speaker A: 2 turns, 6s" in out
    turns = exp.diarize_chunk(FakeClient([segment("A", 2.0, 5.0)]), chunks[1])
    assert (turns[0].start, turns[0].end) == (602.0, 605.0)


def test_the_model_is_called_as_openais_guide_says(meeting_audio):
    client = FakeClient([], [])
    exp.run(["7"], client=client)
    assert len(client.calls) == 2
    assert client.calls[0] == {
        "model": "gpt-4o-transcribe-diarize",
        "response_format": "diarized_json",
        "chunking_strategy": "auto",
    }


def test_the_table_has_one_row_per_voice_and_one_column_per_tag(meeting_audio, capsys):
    client = FakeClient([segment("A", 0, 50), segment("B", 50, 100), segment("C", 100, 150)], [])
    exp.run(["7"], client=client)
    out = capsys.readouterr().out
    assert "tag speaker_0 (Paul)" in out and "tag speaker_1 (Ada)" in out
    rows = {
        line.split()[0]: line.split()[1:]
        for line in out.splitlines()
        if line[:3] in {"  A", "  B", "  C"}
    }
    assert rows == {"A": ["50", "0"], "B": ["50", "0"], "C": ["0", "50"]}


def test_nothing_is_sent_over_max_minutes(meeting_audio, capsys):
    client = FakeClient()
    assert exp.run(["7", "--max-minutes", "10"], client=client) == 2
    assert client.calls == []
    captured = capsys.readouterr()
    assert "more than --max-minutes 10" in captured.err
    assert "Estimated cost: $0.090" in captured.out  # 15 minutes at $0.006, shown before refusing


def test_dry_run_lists_chunks_and_cost_and_makes_no_call(meeting_audio, capsys, monkeypatch):
    def no_client(**kwargs):
        raise AssertionError("a client was built in a dry run")

    monkeypatch.setattr(exp, "OpenAI", no_client)
    assert exp.run(["7", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "2 chunk(s), 15.0 minutes" in out
    assert "chunk_001.webm  starts 10:00  300.0s" in out
    assert "Estimated cost: $0.090" in out
    assert "Dry run: nothing was sent to OpenAI." in out


def test_no_text_is_printed_without_show_text(meeting_audio, capsys):
    client = FakeClient([segment("A", 0, 50)], [segment("B", 0, 5)])
    exp.run(["7"], client=client)
    captured = capsys.readouterr()
    assert SECRET_WORDS not in captured.out + captured.err
    assert ": x" not in captured.out and ": y" not in captured.out  # Skribby's own text too


def test_show_text_prints_only_the_window(meeting_audio, capsys):
    client = FakeClient([segment("A", 10, 20, "inside"), segment("B", 150, 160, "far away")], [])
    exp.run(["7", "--show-text", "0-30"], client=client)
    out = capsys.readouterr().out
    assert "0:10-0:20 A: inside" in out
    assert "far away" not in out
    assert "0:00-1:40 tag speaker_0 (Paul): x" in out
    assert "tag speaker_1" not in out.split("Skribby items")[1]


def test_a_bad_window_is_refused(meeting_audio):
    with pytest.raises(SystemExit):
        exp.run(["7", "--show-text", "440-400"], client=FakeClient())


def test_no_kept_audio_stops_with_a_plain_message(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(exp, "audio_path", lambda meeting_id: tmp_path / "9" / "source.webm")
    (tmp_path / "9").mkdir()
    assert exp.run(["9", "--dry-run"]) == 2
    assert "no audio kept for meeting 9" in capsys.readouterr().err


def test_without_chunks_the_recording_is_recut_into_a_scratch_folder_and_removed(
    tmp_path, monkeypatch
):
    folder = tmp_path / "7"
    folder.mkdir()
    (folder / "source.webm").write_bytes(b"raw")
    monkeypatch.setattr(exp, "audio_path", lambda meeting_id: folder / "source.webm")
    seen = {}

    def fake_prepare(source, out_dir):
        seen["dir"] = out_dir
        chunk = out_dir / "chunk_000.webm"
        chunk.write_bytes(b"opus")
        return audio.Recording([chunk], 60)

    monkeypatch.setattr(audio, "prepare_recording", fake_prepare)
    with exp.prepared_chunks(7) as (paths, how):
        assert paths == [seen["dir"] / "chunk_000.webm"] and paths[0].exists()
        assert "re-cut" in how
        assert seen["dir"].parent == folder
    assert not seen["dir"].exists()
    assert sorted(p.name for p in folder.iterdir()) == ["source.webm"]  # the recording is untouched


def test_a_missing_key_stops_before_any_call(meeting_audio, monkeypatch, capsys):
    monkeypatch.setattr(exp, "get_settings", lambda: SimpleNamespace(openai_api_key=""))
    assert exp.run(["7"]) == 2
    assert "OPENAI_API_KEY is not set" in capsys.readouterr().err
