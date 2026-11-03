"""Prepare meeting recordings for transcription.

OpenAI rejects uploads over 25 MB and an hour of Vexa audio is about 56 MB, so the
recording is compressed to mono 32 kbps Opus and split into chunks of at most 15 minutes.
Each cut goes in a silence near the end of its window so words are not sliced in half.
"""

import math
import re
import subprocess
from pathlib import Path
from typing import NamedTuple

MAX_CHUNK_SECONDS = 15 * 60
# Container rounding can add a few ms to a chunk, so aim just under the hard limit.
CHUNK_SECONDS = MAX_CHUNK_SECONDS - 1
CUT_SEARCH_SECONDS = 30
ENCODE_ARGS = ("-vn", "-ac", "1", "-c:a", "libopus", "-b:a", "32k", "-application", "voip")
SILENCE_NOISE_DB = -35
SILENCE_MIN_SECONDS = 0.3
NO_SPEECH_MAX_DB = -50.0
FFMPEG_TIMEOUT = 600
FFPROBE_TIMEOUT = 60

_SILENCE_START = re.compile(r"silence_start: (-?\d+(?:\.\d+)?)")
_SILENCE_END = re.compile(r"silence_end: (-?\d+(?:\.\d+)?)")
_MAX_VOLUME = re.compile(r"max_volume: (-?inf|-?\d+(?:\.\d+)?) dB")


class AudioError(Exception):
    """ffmpeg or ffprobe failed, timed out, or is missing."""


class NoSpeechError(AudioError):
    """The recording contains no usable sound."""


def _run(cmd: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise AudioError(f"{cmd[0]} timed out after {timeout}s: {' '.join(cmd)}") from exc
    except OSError as exc:
        raise AudioError(f"could not run {cmd[0]}: {exc}") from exc
    if result.returncode != 0:
        raise AudioError(
            f"{cmd[0]} exited with {result.returncode}: {' '.join(cmd)}\n{result.stderr.strip()}"
        )
    return result


def _ffmpeg(*args: str) -> subprocess.CompletedProcess[str]:
    return _run(["ffmpeg", "-nostdin", "-hide_banner", "-y", *args], FFMPEG_TIMEOUT)


def _duration(path: Path) -> float:
    result = _run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        FFPROBE_TIMEOUT,
    )
    try:
        return float(result.stdout.strip())
    except ValueError as exc:
        raise AudioError(f"ffprobe gave no duration for {path}: {result.stdout!r}") from exc


def _analyse(path: Path) -> tuple[float, list[tuple[float, float]]]:
    """One pass: loudest sample in dB and the (start, end) of each silence."""
    filters = f"volumedetect,silencedetect=noise={SILENCE_NOISE_DB}dB:d={SILENCE_MIN_SECONDS}"
    log = _ffmpeg("-i", str(path), "-af", filters, "-f", "null", "-").stderr
    match = _MAX_VOLUME.search(log)
    if not match:
        raise AudioError(f"ffmpeg found no audio stream in {path}\n{log.strip()}")
    max_db = float(match.group(1))
    starts = [float(s) for s in _SILENCE_START.findall(log)]
    ends = [float(e) for e in _SILENCE_END.findall(log)]
    return max_db, list(zip(starts, ends, strict=False))


def _cut_points(total: float, silences: list[tuple[float, float]]) -> list[float]:
    """Cut times, each at most CHUNK_SECONDS after the previous one."""
    cuts: list[float] = []
    position = 0.0
    while total - position > CHUNK_SECONDS:
        limit = position + CHUNK_SECONDS
        earliest = limit - CUT_SEARCH_SECONDS
        best: float | None = None
        for start, end in silences:
            lo, hi = max(start, earliest), min(end, limit)
            if lo < hi:
                best = (lo + hi) / 2  # silences are ordered, so the last overlap is nearest
        position = best if best is not None else limit
        cuts.append(position)
    return cuts


class Recording(NamedTuple):
    chunks: list[Path]
    seconds: int  # length of the whole recording, rounded up: a started second is a second


def prepare_chunks(source: Path, out_dir: Path) -> list[Path]:
    return prepare_recording(source, out_dir).chunks


def prepare_recording(source: Path, out_dir: Path) -> Recording:
    """Compress `source` and split it into Opus WebM chunks in `out_dir`; also measures it.

    Raises NoSpeechError for silent audio and AudioError when ffmpeg fails.
    """
    source = Path(source)
    out_dir = Path(out_dir)
    if not source.is_file():
        raise AudioError(f"audio file not found: {source}")
    out_dir.mkdir(parents=True, exist_ok=True)

    full = out_dir / "_full.webm"
    chunks: list[Path] = []
    try:
        _ffmpeg("-i", str(source), *ENCODE_ARGS, str(full))
        max_db, silences = _analyse(full)
        if max_db < NO_SPEECH_MAX_DB:
            raise NoSpeechError(f"no usable sound in {source} (loudest sample {max_db} dB)")
        total = _duration(full)
        bounds = [0.0, *_cut_points(total, silences), total]
        for index, (start, end) in enumerate(zip(bounds, bounds[1:], strict=False)):
            chunk = out_dir / f"chunk_{index:03d}.webm"
            chunks.append(chunk)
            # Encode from the original rather than stream-copying `full`: copy cuts land on
            # container clusters and chunk lengths drift by seconds.
            _ffmpeg(
                *("-ss", f"{start:.3f}", "-i", str(source), "-t", f"{end - start:.3f}"),
                *ENCODE_ARGS,
                str(chunk),
            )
    except AudioError:
        for chunk in chunks:
            chunk.unlink(missing_ok=True)
        raise
    finally:
        full.unlink(missing_ok=True)
    return Recording(chunks, math.ceil(total))
