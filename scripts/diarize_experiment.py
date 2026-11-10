"""Diagnostic: does an OpenAI diarizing model separate the voices Skribby merged? Read-only.

Run it in the `worker` container (only `worker` mounts the audio volume; `web` does not):

    docker compose exec -T worker python scripts/diarize_experiment.py 32 --window 330-510 --dry-run
    docker compose exec -T worker python scripts/diarize_experiment.py 32 --window 330-510
    (add `--show-text 400-440` to the second one to print the text of that part)

Start with a short `--window FROM-TO` (seconds into the meeting, at most 600 s unless
`--allow-long`): only that slice is cut from the meeting's audio and sent, which costs a few cents.
Without `--window` every chunk of the meeting is sent (about 15 minutes each); a first live run
on meeting 32 timed out on such a chunk three times and cost more than expected.

It takes the meeting's own audio (the chunks the pipeline cut, or, if they are gone, the recording
re-cut the same way into a temporary folder that is removed afterwards), cuts the window out of it
(one file, even when the window spans two chunks, so the speaker labels stay consistent), sends it
to OpenAI's `gpt-4o-transcribe-diarize`, and prints: how many distinct speakers the model found, per
speaker the turns and seconds, and a table of speaker x Skribby tag (seconds of overlap, from the
kept `skribby_segment_evidence`). If one Skribby tag overlaps two of the model's speakers, the
audio does hold two voices there and a diarizing model can tell them apart.

Nothing is written to the database or kept on disk, nothing is deleted except the temporary
folder this script made, and no text is printed unless `--show-text FROM-TO` (seconds) is given:
then the model's turns and Skribby's items in that window are printed, so treat that output like
the transcript. The API key (`OPENAI_API_KEY`) is read from the app settings and never printed.
`--dry-run` lists what would be sent and the estimated cost and calls nothing. `--max-minutes N`
(default 90) refuses to send more audio than that. `--attempts N` (default 1): a timeout or error
stops at once and says the most it can have cost; a retry only happens if you ask for 2 or more.
`--timeout SECONDS` (default 600) is the request timeout. While a request is in flight a line goes
to stderr every 30 seconds. At the end the audio minutes actually sent are printed.

Sources (read 2026-11-10): developers.openai.com/api/docs/guides/speech-to-text (the model needs
`response_format="diarized_json"` and `chunking_strategy="auto"` for audio over 30 seconds, takes
no prompt, uploads are at most 25 MB; segments carry `speaker`, `start`, `end`, `text`) and
developers.openai.com/api/docs/pricing (gpt-4o-transcribe-diarize: $0.006 per minute).
"""

import argparse
import re
import shutil
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openai import OpenAI  # noqa: E402

from app import audio  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.pipeline import audio_path  # noqa: E402
from app.retry import call_with_retry  # noqa: E402

MODEL = "gpt-4o-transcribe-diarize"
PRICE_PER_MINUTE = 0.006  # USD, OpenAI pricing page, estimated cost per audio minute
REQUEST_TIMEOUT = 600.0
MAX_WINDOW_SECONDS = 600.0  # a longer --window needs --allow-long
HEARTBEAT_SECONDS = 30.0
MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # OpenAI's upload limit
NO_TAG = "(no tag)"
_WINDOW = re.compile(r"^(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)$")


class ExperimentError(Exception):
    """Something stops the experiment. The message never carries transcript text."""


@dataclass(frozen=True)
class Turn:
    speaker: str
    start: float  # seconds from the start of the whole recording
    end: float
    text: str


@dataclass(frozen=True)
class Chunk:
    path: Path
    offset: float  # where this chunk starts in the whole recording, in seconds
    seconds: float
    size: int


# ---- finding the audio ------------------------------------------------------------------------


@contextmanager
def prepared_chunks(meeting_id: int) -> Iterator[tuple[list[Path], str]]:
    """The meeting's chunks and where they came from; a temporary re-cut is removed on exit."""
    source = audio_path(meeting_id)
    folder = source.parent
    kept = sorted((folder / "chunks").glob("chunk_*.webm"))
    if kept:
        yield kept, "the chunks the pipeline cut"
        return
    if not source.is_file():
        raise ExperimentError(
            f"no audio kept for meeting {meeting_id} (looked for {source}; audio is deleted after "
            "7 days, and only the worker container has the audio volume)"
        )
    scratch = Path(tempfile.mkdtemp(prefix="diarize-experiment-", dir=folder))
    try:
        try:
            recording = audio.prepare_recording(source, scratch)
        except audio.AudioError as exc:
            raise ExperimentError(f"could not prepare the audio: {exc}") from exc
        yield recording.chunks, f"the recording re-cut into {scratch.name} (removed afterwards)"
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def measure(paths: list[Path], seconds_of: Callable[[Path], float] | None = None) -> list[Chunk]:
    """Each chunk's start in the whole recording: the sum of the chunks before it."""
    seconds_of = seconds_of or audio._duration
    chunks: list[Chunk] = []
    offset = 0.0
    for path in paths:
        try:
            seconds = seconds_of(path)
        except audio.AudioError as exc:
            raise ExperimentError(f"could not measure {path.name}: {exc}") from exc
        chunks.append(Chunk(path, offset, seconds, path.stat().st_size))
        offset += seconds
    return chunks


class Spend:
    """The audio sent so far, one entry per request attempt (a retry sends the audio again)."""

    def __init__(self) -> None:
        self.attempts: list[float] = []

    def record(self, chunk: Chunk) -> None:
        self.attempts.append(chunk.seconds)

    @property
    def minutes(self) -> float:
        return sum(self.attempts) / 60


@contextmanager
def cut_slice(chunks: list[Chunk], start: float, end: float, folder: Path) -> Iterator[Chunk]:
    """The part of the meeting between `start` and `end` seconds as ONE file in a scratch folder.

    The slice is re-encoded the way `app/audio.py` encodes (stream copy drifts); a window that
    spans two chunks is joined in the same ffmpeg run, so the model sees one recording and its
    speaker labels stay consistent. The folder is removed on exit. The yielded chunk's `offset` is
    where the slice starts in the meeting, so the model's times come back in meeting time.
    """
    pieces = []
    for chunk in chunks:
        low, high = max(start, chunk.offset), min(end, chunk.offset + chunk.seconds)
        if high > low:
            pieces.append((chunk, low - chunk.offset, high - low))
    if not pieces:
        raise ExperimentError(f"nothing between {_clock(start)} and {_clock(end)} in this audio")
    scratch = Path(tempfile.mkdtemp(prefix="diarize-experiment-slice-", dir=folder))
    try:
        out = scratch / "slice.webm"
        args: list[str] = []
        for chunk, piece_start, piece_seconds in pieces:
            args += [
                "-ss",
                f"{piece_start:.3f}",
                "-t",
                f"{piece_seconds:.3f}",
                "-i",
                str(chunk.path),
            ]
        if len(pieces) > 1:
            joined = "".join(f"[{n}:a]" for n in range(len(pieces)))
            args += ["-filter_complex", f"{joined}concat=n={len(pieces)}:v=0:a=1[joined]"]
            args += ["-map", "[joined]"]
        try:
            audio._ffmpeg(*args, *audio.ENCODE_ARGS, str(out))
        except audio.AudioError as exc:
            raise ExperimentError(f"could not cut the slice: {exc}") from exc
        yield Chunk(
            out,
            offset=pieces[0][0].offset + pieces[0][1],
            seconds=sum(piece[2] for piece in pieces),
            size=out.stat().st_size,
        )
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


@contextmanager
def heartbeat(what: str) -> Iterator[None]:
    """Say on stderr every HEARTBEAT_SECONDS that a request is still in flight."""
    done = threading.Event()
    started = time.monotonic()

    def beat() -> None:
        while not done.wait(HEARTBEAT_SECONDS):
            print(
                f"  still waiting for {what}: {time.monotonic() - started:.0f}s",
                file=sys.stderr,
                flush=True,
            )

    thread = threading.Thread(target=beat, daemon=True)
    thread.start()
    try:
        yield
    finally:
        done.set()
        thread.join()


# ---- OpenAI -----------------------------------------------------------------------------------


def diarize_chunk(
    client, chunk: Chunk, *, attempts: int = 1, spend: Spend | None = None
) -> list[Turn]:
    """One chunk through the diarizing model; its times are moved to the whole recording's clock.

    Every attempt is recorded in `spend` before it is sent. With `attempts=1` an error stops here.
    """

    def call():
        if spend is not None:
            spend.record(chunk)
        with chunk.path.open("rb") as handle:
            return client.audio.transcriptions.create(
                model=MODEL,
                file=handle,
                response_format="diarized_json",
                chunking_strategy="auto",
            )

    with heartbeat(chunk.path.name):
        result = call_with_retry(
            call, what=chunk.path.name, failure=ExperimentError, attempts=attempts
        )
    return [
        Turn(
            speaker=str(segment.speaker),
            start=chunk.offset + float(segment.start),
            end=chunk.offset + float(segment.end),
            text=segment.text or "",
        )
        for segment in result.segments or []
    ]


# ---- the numbers ------------------------------------------------------------------------------


def _overlap(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    return max(0.0, min(a_end, b_end) - max(a_start, b_start))


def speaker_stats(turns: list[Turn]) -> dict[str, tuple[int, float]]:
    """Per speaker label: (number of turns, total seconds)."""
    stats: dict[str, tuple[int, float]] = {}
    for turn in turns:
        count, seconds = stats.get(turn.speaker, (0, 0.0))
        stats[turn.speaker] = (count + 1, seconds + max(0.0, turn.end - turn.start))
    return dict(sorted(stats.items()))


def _timed(items: list[dict]) -> list[dict]:
    return [
        i
        for i in items
        if isinstance(i.get("start"), int | float) and isinstance(i.get("end"), int | float)
    ]


def tag_names(items: list[dict]) -> dict[str, str]:
    """Each Skribby tag and the first `speaker_name` its items carry ('' when none)."""
    names: dict[str, str] = {}
    for item in items:
        tag = str(item["speaker"]) if item.get("speaker") is not None else NO_TAG
        names.setdefault(tag, "")
        if not names[tag] and item.get("speaker_name"):
            names[tag] = str(item["speaker_name"])
    return dict(sorted(names.items(), key=lambda kv: (kv[0] == NO_TAG, kv[0])))


def overlap_table(turns: list[Turn], items: list[dict]) -> dict[str, dict[str, float]]:
    """Seconds of overlap per (model speaker, Skribby tag)."""
    table: dict[str, dict[str, float]] = {speaker: {} for speaker in speaker_stats(turns)}
    for item in _timed(items):
        tag = str(item["speaker"]) if item.get("speaker") is not None else NO_TAG
        for turn in turns:
            seconds = _overlap(turn.start, turn.end, item["start"], item["end"])
            if seconds > 0:
                row = table[turn.speaker]
                row[tag] = row.get(tag, 0.0) + seconds
    return table


# ---- printing ---------------------------------------------------------------------------------


def _clock(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 60}:{total % 60:02d}"


def _plan(chunks: list[Chunk], how: str, minutes: float) -> str:
    lines = [f"Audio: {how}.", f"To send: {len(chunks)} file(s), {minutes:.1f} minutes in all:"]
    for number, chunk in enumerate(chunks, 1):
        lines.append(
            f"  {number}. {chunk.path.name}  starts {_clock(chunk.offset)}  "
            f"{chunk.seconds:.1f}s  {chunk.size / 1_048_576:.1f} MB"
        )
    lines.append(
        f"Estimated cost: ${minutes * PRICE_PER_MINUTE:.3f} ({MODEL}, ${PRICE_PER_MINUTE}/min)"
    )
    return "\n".join(lines)


def summary(turns: list[Turn], items: list[dict]) -> str:
    stats = speaker_stats(turns)
    lines = [f"The model found {len(stats)} distinct speaker(s) in {len(turns)} turn(s)."]
    for speaker, (count, seconds) in stats.items():
        lines.append(f"  speaker {speaker}: {count} turns, {seconds:.0f}s")
    if not items:
        lines.append("No Skribby evidence is kept for this meeting: no overlap table.")
        return "\n".join(lines)
    tags = tag_names(items)
    table = overlap_table(turns, items)
    heads = [f"tag {tag}" + (f" ({name})" if name else "") for tag, name in tags.items()]
    width = max([len(h) for h in heads] + [8])
    lines += ["", "Seconds of overlap, model speaker (rows) x Skribby tag (columns):"]
    lines.append("  " + "speaker".ljust(8) + "".join(h.rjust(width + 2) for h in heads))
    for speaker in stats:
        cells = "".join(f"{table[speaker].get(tag, 0.0):.0f}".rjust(width + 2) for tag in tags)
        lines.append("  " + speaker.ljust(8) + cells)
    return "\n".join(lines)


def window_text(turns: list[Turn], items: list[dict], start: float, end: float) -> str:
    """The turns and items overlapping [start, end]: the only place text is ever printed."""
    lines = [f"Model turns {_clock(start)}-{_clock(end)}:"]
    for turn in sorted(turns, key=lambda t: t.start):
        if _overlap(turn.start, turn.end, start, end) > 0:
            lines.append(f"  {_clock(turn.start)}-{_clock(turn.end)} {turn.speaker}: {turn.text}")
    lines.append(f"Skribby items {_clock(start)}-{_clock(end)}:")
    for item in sorted(_timed(items), key=lambda i: i["start"]):
        if _overlap(item["start"], item["end"], start, end) > 0:
            tag = item["speaker"] if item.get("speaker") is not None else NO_TAG
            name = f" ({item['speaker_name']})" if item.get("speaker_name") else ""
            lines.append(
                f"  {_clock(item['start'])}-{_clock(item['end'])} tag {tag}{name}: "
                f"{item.get('text', '')}"
            )
    return "\n".join(lines)


# ---- running ----------------------------------------------------------------------------------


def load_skribby_items(meeting_id: int) -> list[dict]:
    """The kept Skribby items for the meeting (empty when none are kept). Read only."""
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import SkribbySegmentEvidence

    with SessionLocal() as session:
        row = session.scalar(
            select(SkribbySegmentEvidence).where(SkribbySegmentEvidence.meeting_id == meeting_id)
        )
        return list(row.items or []) if row is not None else []


def _window(value: str) -> tuple[float, float]:
    match = _WINDOW.match(value)
    if not match or float(match.group(1)) >= float(match.group(2)):
        raise argparse.ArgumentTypeError("use FROM-TO in seconds, FROM below TO, e.g. 400-440")
    return float(match.group(1)), float(match.group(2))


def run(argv: list[str], *, client=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("meeting_id", type=int)
    parser.add_argument(
        "--window", type=_window, metavar="FROM-TO", help="send only this slice (seconds in)"
    )
    parser.add_argument("--allow-long", action="store_true", help="allow a window over 600 s")
    parser.add_argument(
        "--show-text", type=_window, metavar="FROM-TO", help="seconds, e.g. 400-440"
    )
    parser.add_argument("--max-minutes", type=float, default=90.0)
    parser.add_argument("--attempts", type=int, default=1, help="1 = no retry (default)")
    parser.add_argument("--timeout", type=float, default=REQUEST_TIMEOUT, help="seconds")
    parser.add_argument("--dry-run", action="store_true", help="plan and cost only, no OpenAI call")
    args = parser.parse_args(argv)
    if args.attempts < 1 or args.timeout <= 0:
        parser.error("--attempts must be at least 1 and --timeout above 0")
    spend = Spend()
    try:
        if args.window and args.window[1] - args.window[0] > MAX_WINDOW_SECONDS:
            if not args.allow_long:
                raise ExperimentError(
                    f"the window is {args.window[1] - args.window[0]:.0f} s, over "
                    f"{MAX_WINDOW_SECONDS:.0f} s: add --allow-long if you mean it. Nothing was sent"
                )
        with prepared_chunks(args.meeting_id) as (paths, how), ExitStack() as stack:
            chunks = measure(paths)
            if args.window:
                total = sum(c.seconds for c in chunks)
                start, end = args.window[0], min(args.window[1], total)
                if start >= total:
                    raise ExperimentError(
                        f"the window starts at {start:.0f}s but the audio is {total:.0f}s long"
                    )
                folder = audio_path(args.meeting_id).parent
                sendable = [stack.enter_context(cut_slice(chunks, start, end, folder))]
                how = f"{how}; the slice {_clock(start)}-{_clock(end)} cut out of it"
            else:
                sendable = chunks
            minutes = sum(c.seconds for c in sendable) / 60
            print(_plan(sendable, how, minutes))
            if minutes > args.max_minutes:
                raise ExperimentError(
                    f"{minutes:.1f} minutes is more than --max-minutes {args.max_minutes:g}: "
                    "nothing was sent"
                )
            big = [c.path.name for c in sendable if c.size > MAX_UPLOAD_BYTES]
            if big:
                raise ExperimentError(f"over OpenAI's 25 MB upload limit: {', '.join(big)}")
            items = load_skribby_items(args.meeting_id)
            print(f"Skribby evidence: {len(items)} item(s) kept.")
            if args.dry_run:
                print("Dry run: nothing was sent to OpenAI.")
                return 0
            if client is None:
                key = get_settings().openai_api_key
                if not key:
                    raise ExperimentError("OPENAI_API_KEY is not set")
                client = OpenAI(api_key=key, max_retries=0, timeout=args.timeout)
            print(f"Attempts per request: {args.attempts}; timeout {args.timeout:g}s.", flush=True)
            turns: list[Turn] = []
            for number, chunk in enumerate(sendable, 1):
                print(f"Sending {number} of {len(sendable)} ...", flush=True)
                turns += diarize_chunk(client, chunk, attempts=args.attempts, spend=spend)
    except ExperimentError as exc:
        print(f"Stopped: {exc}", file=sys.stderr)
        if spend.attempts:
            print(
                f"At most ${spend.minutes * PRICE_PER_MINUTE:.3f} was spent: "
                f"{len(spend.attempts)} attempt(s), {spend.minutes:.1f} audio minutes sent "
                "(an attempt that failed may or may not have been billed).",
                file=sys.stderr,
            )
        return 2
    print(
        f"Audio sent: {spend.minutes:.1f} minutes in {len(spend.attempts)} request(s), "
        f"about ${spend.minutes * PRICE_PER_MINUTE:.3f}."
    )
    print()
    print(summary(turns, items))
    if len(sendable) > 1:
        print(
            "Note: the model labels speakers (A, B, ...) per request, so a label need not mean "
            "the same person in two chunks. Use --window for one consistent slice."
        )
    if args.show_text:
        print()
        print(window_text(turns, items, *args.show_text))
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
