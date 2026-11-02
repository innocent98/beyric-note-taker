import json
import subprocess
from pathlib import Path

import pytest

from app import audio
from app.audio import AudioError, NoSpeechError, prepare_chunks

LIMIT_BYTES = 25_000_000
MAX_SECONDS = 15 * 60


def ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


def duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return float(json.loads(out)["format"]["duration"])


@pytest.fixture(scope="session")
def long_audio(tmp_path_factory) -> Path:
    """One hour of tone, built once per session."""
    path = tmp_path_factory.mktemp("audio") / "hour.webm"
    ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "sine=f=440:d=3600:r=16000",
        "-c:a",
        "libopus",
        "-b:a",
        "32k",
        str(path),
    )
    return path


@pytest.fixture(scope="session")
def audio_with_gap(tmp_path_factory) -> Path:
    """20 minutes of tone with one second of silence at 14:45."""
    path = tmp_path_factory.mktemp("audio") / "gap.webm"
    ffmpeg(
        "-f", "lavfi", "-i", "sine=f=440:d=1200:r=16000",
        "-af", "volume=enable='between(t,885,886)':volume=0",
        "-c:a", "libopus", "-b:a", "32k", str(path),
    )  # fmt: skip
    return path


@pytest.fixture(scope="session")
def silent_audio(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("audio") / "silent.webm"
    ffmpeg(
        "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono", "-t", "10", "-c:a", "libopus", str(path)
    )
    return path


def test_one_hour_audio_splits_under_limits(long_audio, tmp_path):
    chunks = prepare_chunks(long_audio, tmp_path)
    assert len(chunks) >= 4
    assert all(p.suffix == ".webm" and p.parent == tmp_path for p in chunks)
    assert all(p.stat().st_size < LIMIT_BYTES for p in chunks)
    assert all(duration(p) <= MAX_SECONDS for p in chunks)
    assert sum(duration(p) for p in chunks) == pytest.approx(3600, abs=2)


def test_chunks_are_mono_opus(audio_with_gap, tmp_path):
    chunk = prepare_chunks(audio_with_gap, tmp_path)[0]
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
         "stream=codec_name,channels", "-of", "json", str(chunk)],
        check=True, capture_output=True, text=True,
    ).stdout  # fmt: skip
    stream = json.loads(out)["streams"][0]
    assert stream["codec_name"] == "opus"
    assert stream["channels"] == 1


def test_cut_lands_in_silence_near_window_end(audio_with_gap, tmp_path):
    chunks = prepare_chunks(audio_with_gap, tmp_path)
    assert len(chunks) == 2
    assert 885 <= duration(chunks[0]) <= 886.1


def test_cuts_at_window_end_without_silence(tmp_path_factory, tmp_path):
    src = tmp_path_factory.mktemp("audio") / "tone.webm"
    ffmpeg("-f", "lavfi", "-i", "sine=f=440:d=1000:r=16000", "-c:a", "libopus", str(src))
    chunks = prepare_chunks(src, tmp_path)
    assert len(chunks) == 2
    assert 899 <= duration(chunks[0]) <= MAX_SECONDS


def test_short_audio_is_one_chunk(tmp_path_factory, tmp_path):
    src = tmp_path_factory.mktemp("audio") / "short.webm"
    ffmpeg("-f", "lavfi", "-i", "sine=f=440:d=20:r=16000", "-c:a", "libopus", str(src))
    assert len(prepare_chunks(src, tmp_path)) == 1


def test_silent_audio_raises(silent_audio, tmp_path):
    with pytest.raises(NoSpeechError):
        prepare_chunks(silent_audio, tmp_path)


def test_corrupt_input_raises_audio_error_with_stderr(tmp_path):
    bad = tmp_path / "bad.webm"
    bad.write_bytes(b"not audio at all")
    out = tmp_path / "out"
    with pytest.raises(AudioError) as exc:
        prepare_chunks(bad, out)
    assert not isinstance(exc.value, NoSpeechError)
    assert "ffmpeg" in str(exc.value).lower()
    assert "bad.webm" in str(exc.value)


def test_missing_input_raises_audio_error(tmp_path):
    with pytest.raises(AudioError):
        prepare_chunks(tmp_path / "nope.webm", tmp_path / "out")


def test_timeout_raises_audio_error(monkeypatch, silent_audio, tmp_path):
    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])

    monkeypatch.setattr(audio.subprocess, "run", fake_run)
    with pytest.raises(AudioError, match="timed out"):
        prepare_chunks(silent_audio, tmp_path)
