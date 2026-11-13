"""Bake-off: which Skribby transcription model separates the voices of one stored meeting?

A sibling of `scripts/diarize_experiment.py`, run the same way in the `worker` container (it has
the audio volume and the Skribby key in its settings; `web` has neither the audio):

    docker compose exec -T worker python scripts/skribby_model_bakeoff.py 32 --dry-run
    ... 32 --models soniox/stt-async-v6     (the first live run: one model)
    ... 32                                  (all four default models)

(The detached form and how to read the output file are in the SOP.)

Run ONE model first (`--models soniox/stt-async-v6`): the recording upload's request and answer
are read from Skribby's docs, not proven live. It cuts `--window FROM-TO` (seconds into the
meeting, default 400-1000) out of the meeting's own audio (the slice code of diarize_experiment),
uploads that slice to Skribby once per model (`POST /recording`, multipart `recording_file` +
`transcription_model`), polls `GET /recording/{id}` until it is finished, and always deletes the
uploaded recording afterwards (`DELETE /recording/{id}`, in a `finally`).

For each model it prints the speakers it found (turns and seconds), the table speaker x the OLD
Skribby tag (from the kept `skribby_segment_evidence`), and a CHECKPOINT SCORE: facts about who
spoke when (default: meeting 32; `--checkpoints FILE.json` for another meeting). A model PASSES
when every checkpoint of each non-reference person falls in ONE model speaker that is not the
reference person's voice, and the reference person's checkpoints fall in their voice (the model
speaker with the most overlap with the reference old tag).

A second mode, `--score-against-meet --window FROM-TO`, ranks the models against Meet's own speaker
timeline (`skribby_segment_evidence.events`, read with `scripts/skribby_segments_report.py`'s code)
instead of checkpoints: per model the voices found vs the participants who spoke in the slice, the
table model speaker x participant, which speaker holds each participant and how pure it is, and a
one-line verdict. `--list-windows` lists the windows where most participants spoke (database only:
no audio, no key, no cost). Both are in the SOP.

Nothing is written to the database or kept; no key is printed; no text is printed unless
`--show-text FROM-TO`. One attempt per upload, no retry; the first error stops the run and says
what was already sent. `--dry-run` calls nothing and needs no key.

Pages read (2026-11-11): skribby.io/docs/guides/recordings-upload, /docs/rest-api (operations),
/docs/rest-api/recording-operations/createrecording, getrecording, deleterecording,
/docs/guides/transcription-models, and /docs/guides/billing (per-hour prices below). The docs do
not say which models accept `custom_vocabulary`, so none is sent.
"""

import argparse
import json
import sys
import time
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.skribby import BASES  # noqa: E402
from scripts import diarize_experiment as dx  # noqa: E402
from scripts import skribby_segments_report as sr  # noqa: E402
from scripts.diarize_experiment import ExperimentError, Turn  # noqa: E402

DEFAULT_MODELS = (
    "soniox/stt-async-v6",
    "assemblyai/universal-3-5-pro",
    "elevenlabs/scribe-v2",
    "deepgram/nova-3-multilingual",
)
# USD per audio hour, the transcription model's own rate on Skribby's billing page
# (skribby.io/docs/guides/billing, read 2026-11-11). A recording upload also pays the base below.
MODEL_PRICE_PER_HOUR = {
    "soniox/stt-async-v6": 0.10,
    "assemblyai/universal-3-5-pro": 0.23,
    "elevenlabs/scribe-v2": 0.20,
    "deepgram/nova-3-multilingual": 0.32,
    "gladia/async": 0.32,
}
BASE_PRICE_PER_HOUR = 0.10  # "Base processing is $0.10/hour", billed per minute, only on success
DEFAULT_WINDOW = (400.0, 1000.0)
WINDOW_LENGTH = 600.0  # `--list-windows`: how long each candidate window is
WINDOW_STEP = 30.0  # ... and how far apart their starts are
MIN_SPEECH = 2.0  # seconds a Meet participant must speak in the slice to count as having spoken
POLL_SECONDS = 10.0
UPLOAD_TIMEOUT = httpx.Timeout(300.0, connect=15.0)
CALL_TIMEOUT = httpx.Timeout(60.0, connect=15.0)
FINISHED = "finished"
FAILED = ("failed", "invalid_api_key")

# Facts about who spoke when in meeting 32 (meeting seconds, given by Adebayo). The reference
# person is the one whose old Skribby tag is reliable for long stretches. Spoken words are not kept
# here on purpose.
DEFAULT_CHECKPOINTS = {
    "reference_tag": "1",
    "reference_person": "Adebayo",
    "checkpoints": [
        {"person": "Daniel", "start": 427, "end": 428},
        {"person": "Daniel", "start": 431, "end": 436},
        {"person": "Daniel", "start": 678, "end": 679},
        {"person": "Adebayo", "start": 675, "end": 677},
    ],
}


# ---- checkpoints ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Checkpoints:
    reference_tag: str
    reference_person: str
    points: tuple[tuple[str, float, float], ...]  # (person, start, end) in meeting seconds


def parse_checkpoints(spec: object) -> Checkpoints:
    try:
        points = tuple(
            (str(c["person"]), float(c["start"]), float(c["end"])) for c in spec["checkpoints"]
        )
        result = Checkpoints(str(spec["reference_tag"]), str(spec["reference_person"]), points)
    except (KeyError, TypeError, ValueError) as exc:
        raise ExperimentError(
            "checkpoints must be {reference_tag, reference_person, checkpoints: "
            "[{person, start, end}, ...]}"
        ) from exc
    if not points or any(end <= start for _, start, end in points):
        raise ExperimentError("every checkpoint needs start below end, and there must be some")
    if not any(person == result.reference_person for person, _, _ in points):
        raise ExperimentError("no checkpoint is for the reference person")
    return result


def load_checkpoints(path: str | None) -> Checkpoints:
    if path is None:
        return parse_checkpoints(DEFAULT_CHECKPOINTS)
    try:
        return parse_checkpoints(json.loads(Path(path).read_text()))
    except (OSError, ValueError) as exc:
        raise ExperimentError(f"could not read the checkpoints file: {type(exc).__name__}") from exc


@dataclass
class Score:
    voice: str | None  # the model speaker taken as the reference person's voice
    rows: list[tuple[str, float, float, str | None, bool]] = field(default_factory=list)
    people: dict[str, bool] = field(default_factory=dict)  # non-reference: all in one speaker

    @property
    def passed(self) -> bool:
        return self.voice is not None and all(r[4] for r in self.rows) and all(self.people.values())


def _speaker_at(turns: list[Turn], start: float, end: float) -> str | None:
    """The model speaker with the most overlap with [start, end]; None when nobody speaks."""
    seconds: dict[str, float] = {}
    for turn in turns:
        overlap = dx._overlap(turn.start, turn.end, start, end)
        if overlap > 0:
            seconds[turn.speaker] = seconds.get(turn.speaker, 0.0) + overlap
    if not seconds:
        return None
    return max(sorted(seconds), key=lambda speaker: seconds[speaker])


def reference_voice(turns: list[Turn], items: list[dict], tag: str) -> str | None:
    """The model speaker with the most overlap with the old Skribby tag `tag`."""
    table = dx.overlap_table(turns, items)
    best = {speaker: row.get(tag, 0.0) for speaker, row in table.items()}
    best = {speaker: seconds for speaker, seconds in best.items() if seconds > 0}
    return max(sorted(best), key=lambda speaker: best[speaker]) if best else None


def score(turns: list[Turn], items: list[dict], checkpoints: Checkpoints) -> Score:
    result = Score(reference_voice(turns, items, checkpoints.reference_tag))
    found: dict[str, set[str | None]] = {}
    for person, start, end in checkpoints.points:
        speaker = _speaker_at(turns, start, end)
        if person == checkpoints.reference_person:
            ok = speaker is not None and speaker == result.voice
        else:
            ok = speaker is not None and speaker != result.voice
            found.setdefault(person, set()).add(speaker)
        result.rows.append((person, start, end, speaker, ok))
    result.people = {person: len(speakers) == 1 for person, speakers in found.items()}
    return result


def score_text(result: Score, checkpoints: Checkpoints) -> str:
    voice = f"speaker {result.voice}" if result.voice else "none found (no overlap with that tag)"
    lines = [f"Checkpoints ({checkpoints.reference_person}'s voice = {voice}):"]
    for person, start, end, speaker, ok in result.rows:
        where = f"speaker {speaker}" if speaker is not None else "nobody speaking"
        lines.append(f"  {person} {start:g}-{end:g}s -> {where}: {'PASS' if ok else 'FAIL'}")
    for person, one in result.people.items():
        lines.append(f"  {person}: all checkpoints in ONE speaker: {'PASS' if one else 'FAIL'}")
    lines.append(f"Result: {'PASS' if result.passed else 'FAIL'}")
    return "\n".join(lines)


# ---- Meet's own speaker timeline as the reference ---------------------------------------------

Spans = dict[str, list[tuple[float, float]]]  # participant name -> speaking intervals, meeting s


def load_meet_record(meeting_id: int) -> dict | None:
    """The kept `skribby_segment_evidence.events` of the meeting (Meet's speaker timeline, see
    `app/skribby.py::speaker_events_record`); None when there is none. Read only."""
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import SkribbySegmentEvidence

    with SessionLocal() as session:
        row = session.scalar(
            select(SkribbySegmentEvidence).where(SkribbySegmentEvidence.meeting_id == meeting_id)
        )
        return row.events if row is not None and isinstance(row.events, dict) else None


@dataclass(frozen=True)
class MeetTimeline:
    spans: Spans  # on the transcript's (meeting) clock, shifted by `offset`
    how: str  # how the clock was placed, in words
    offset: float


def meet_timeline(record: dict | None, offset: float = 0.0) -> MeetTimeline:
    """Meet's speaking intervals on the meeting's clock, by the same reading and clock placement as
    `scripts/skribby_segments_report.py` (`offset` seconds are added to Meet's times, as its
    `--offset` does). Refuses, in words, when the timeline cannot be used."""
    if not record:
        raise ExperimentError("no Meet speaker events are kept for this meeting")
    spans = sr.speaking_intervals(record)
    if spans is None:
        raise ExperimentError("the kept speaker events are not in a shape that can be read")
    zero, how = sr._anchor(record, spans)
    if zero is None:
        raise ExperimentError(f"{how}, so they cannot be placed on the meeting's clock")
    shift = zero - offset
    placed = {n: sr._merge([(a - shift, b - shift) for a, b in v]) for n, v in spans.items()}
    return MeetTimeline(placed, f"{how}; shifted by {offset:+g} s", offset)


def speech_in(spans: Spans, start: float, end: float, minimum: float) -> dict[str, float]:
    """Participants who spoke at least `minimum` seconds inside [start, end], with those seconds."""
    seconds = {name: sr._overlap(v, start, end) for name, v in spans.items()}
    return {name: s for name, s in seconds.items() if s >= max(minimum, 1e-9)}


def clock_check(timeline: MeetTimeline, items: list[dict]) -> str:
    """One line on whether Meet's clock fits the old Skribby segments' clock (same fit as the
    segments report); a fit off by seconds is how a bad anchor would show."""
    heard = sr.heard_by_tag(items)
    if not heard:
        return (
            "Clock check: no old Skribby segments are kept, so Meet's clock could not be checked."
        )
    best, here, there = sr.clock_fit(timeline.spans, heard)
    if best != 0 and there > here * 1.1:
        return (
            f"Clock check: WARNING, moving Meet's times {best:+d} s would raise the overlap with "
            f"the old Skribby segments from {here:.0f} s to {there:.0f} s: re-run with "
            f"--meet-offset {timeline.offset + best:g} if that looks right."
        )
    return "Clock check: no shift of up to 30 s either way fits the old Skribby segments better."


def best_windows(
    spans: Spans, length: float, minimum: float, top: int, step: float = WINDOW_STEP
) -> list[tuple[float, float, dict[str, float]]]:
    """Up to `top` non-overlapping windows of `length` seconds with the most distinct participants
    speaking (at least `minimum` seconds each), best first: `(start, end, {name: seconds})`. Ties
    go to the window with more speech by those participants, then the earlier one."""
    last = max((b for v in spans.values() for _, b in v), default=0.0)
    if last <= 0:
        return []
    starts = [0.0]
    while starts[-1] + step + length <= last:
        starts.append(starts[-1] + step)
    ranked = []
    for start in starts:
        spoke = speech_in(spans, start, start + length, minimum)
        ranked.append((len(spoke), sum(spoke.values()), -start, start, spoke))
    ranked.sort(key=lambda r: r[:3], reverse=True)
    chosen: list[tuple[float, float, dict[str, float]]] = []
    for count, _, _, start, spoke in ranked:
        if count == 0 or len(chosen) >= top:
            break
        if all(start >= b or start + length <= a for a, b, _ in chosen):
            chosen.append((start, start + length, spoke))
    return chosen


def windows_text(
    meeting_id: int, timeline: MeetTimeline, length: float, minimum: float, top: int
) -> str:
    windows = best_windows(timeline.spans, length, minimum, top)
    lines = [
        f"Meeting {meeting_id}: {len(timeline.spans)} participant(s) in Meet's speaker timeline "
        f"({timeline.how}).",
        f"Windows of {length:g} s with the most distinct participants speaking at least "
        f"{minimum:g} s, best first, none overlapping:",
    ]
    if not windows:
        lines.append("  none: nobody speaks in Meet's timeline.")
    for number, (start, end, spoke) in enumerate(windows, 1):
        who = ", ".join(f"{n} {s:.0f}s" for n, s in sorted(spoke.items(), key=lambda kv: -kv[1]))
        lines.append(f"  {number}. --window {start:g}-{end:g}: {len(spoke)} participant(s): {who}")
    return "\n".join(lines)


@dataclass
class MeetScore:
    voices: list[str]  # model speakers found in the slice
    people: list[str]  # Meet participants who spoke in the slice
    spoke: dict[str, float]  # participant -> seconds speaking in the slice
    matrix: dict[str, dict[str, float]]  # model speaker -> participant -> overlap seconds
    owner: dict[str, str | None]  # participant -> the speaker holding most of their speech
    held: dict[str, float]  # participant -> their seconds inside that speaker
    purity: dict[str, float]  # speaker -> share of its overlap that belongs to its owner
    sharing: dict[str, list[str]]  # participant -> others whose owner is the same speaker

    @property
    def alone(self) -> list[str]:
        return [p for p in self.people if self.owner[p] and not self.sharing[p]]

    @property
    def unheard(self) -> list[str]:
        return [p for p in self.people if self.owner[p] is None]

    def _main(self, speaker: str) -> str:
        group = [p for p in self.people if self.owner[p] == speaker]
        return max(sorted(group), key=lambda p: self.matrix[speaker][p])

    @property
    def merged(self) -> list[tuple[str, str]]:
        """(participant, the participant whose voice holds them): owners that are not the main one
        of a speaker shared by several."""
        pairs = []
        for person in self.people:
            speaker = self.owner[person]
            if speaker and self.sharing[person] and self._main(speaker) != person:
                pairs.append((person, self._main(speaker)))
        return pairs

    @property
    def hosts(self) -> list[str]:
        return sorted({main for _, main in self.merged})

    def ranking(self) -> tuple[int, int, float]:
        """Higher is better: participants separated, then fewer merged, then mean purity."""
        mean = sum(self.purity.values()) / len(self.purity) if self.purity else 0.0
        return (len(self.alone), -len(self.merged), mean)


def meet_score(turns: list[Turn], spans: Spans, start: float, end: float, minimum: float):
    """Compare a model's speakers with who Meet says was speaking inside [start, end]."""
    spoke = speech_in(spans, start, end, minimum)
    people = sorted(spoke)
    voices = list(dx.speaker_stats(turns))
    matrix: dict[str, dict[str, float]] = {v: {} for v in voices}
    for turn in turns:
        for person in people:
            seconds = sr._overlap(spans[person], turn.start, turn.end)
            if seconds > 0:
                row = matrix[turn.speaker]
                row[person] = row.get(person, 0.0) + seconds
    owner: dict[str, str | None] = {}
    held: dict[str, float] = {}
    for person in people:
        cells = {v: matrix[v].get(person, 0.0) for v in voices if matrix[v].get(person, 0.0) > 0}
        top = max(sorted(cells), key=lambda v: cells[v]) if cells else None
        owner[person] = top
        held[person] = cells[top] if top else 0.0
    purity = {}
    for speaker in voices:
        total = sum(matrix[speaker].values())
        mine = [p for p in people if owner[p] == speaker]
        if total > 0 and mine:
            purity[speaker] = max(matrix[speaker][p] for p in mine) / total
    sharing = {
        p: [q for q in people if q != p and owner[q] is not None and owner[q] == owner[p]]
        for p in people
    }
    return MeetScore(voices, people, spoke, matrix, owner, held, purity, sharing)


def meet_score_text(result: MeetScore, model: str) -> str:
    width = sr.NAME_WIDTH
    lines = [
        f"Meet says {len(result.people)} participant(s) spoke in this slice; "
        f"the model found {len(result.voices)} voice(s).",
        "",
        "Seconds of overlap, model speaker (rows) x Meet participant (columns):",
        "  " + "speaker".ljust(8) + "".join(f"{p[:width]:>{width + 2}}" for p in result.people),
    ]
    for speaker in result.voices:
        cells = "".join(
            f"{result.matrix[speaker].get(p, 0.0):>{width + 2}.0f}" for p in result.people
        )
        lines.append("  " + speaker.ljust(8) + cells)
    lines += ["", "Per participant: the speaker holding most of their speech, and its purity:"]
    for person in result.people:
        speaker = result.owner[person]
        if speaker is None:
            lines.append(
                f"  {person}: spoke {result.spoke[person]:.0f} s -> no model speaker talking"
            )
            continue
        share = result.held[person] / result.spoke[person] if result.spoke[person] else 0.0
        shared = (
            f"; SHARED with {', '.join(result.sharing[person])}" if result.sharing[person] else ""
        )
        lines.append(
            f"  {person}: spoke {result.spoke[person]:.0f} s -> speaker {speaker} holds "
            f"{result.held[person]:.0f} s of it ({share:.0%}); that speaker is "
            f"{result.purity.get(speaker, 0.0):.0%} {person}{shared}"
        )
    lines.append(verdict(result, model))
    return "\n".join(lines)


def verdict(result: MeetScore, model: str) -> str:
    """One line: voices found, participants separated, participants merged into another's voice."""
    merged = ", ".join(f"{p} into {main}'s voice" for p, main in result.merged) or "none"
    line = (
        f"Verdict {model}: {len(result.voices)} voice(s) for {len(result.people)} participant(s); "
        f"separated {len(result.alone)} ({', '.join(result.alone) or 'none'}); "
        f"merged into another's voice {len(result.merged)} ({merged})"
    )
    if result.hosts:
        line += f"; voice shared with them, kept by: {', '.join(result.hosts)}"
    if result.unheard:
        line += f"; not heard: {', '.join(result.unheard)}"
    return line


# ---- Skribby ----------------------------------------------------------------------------------


def _failure(what: str, response: httpx.Response, key: str = "") -> ExperimentError:
    """An error from a non-success answer: status and Skribby's own short reason, nothing else."""
    status = response.status_code
    if status == 401:
        return ExperimentError(f"{what}: Skribby refused the API key (HTTP 401)")
    reason = ""
    try:
        body = response.json()
        if isinstance(body, dict):
            parts = []
            for name in ("message", "error", "code", "detail"):
                value = body.get(name)
                if isinstance(value, list | dict):
                    value = json.dumps(value, default=str)
                if isinstance(value, str) and value.strip():
                    parts.append(value.strip())
            reason = " ".join(parts)[:300]
    except ValueError:
        pass
    if key:
        reason = reason.replace(key, "[redacted]")
    return ExperimentError(f"{what}: HTTP {status}" + (f": {reason}" if reason else ""))


def _call(what: str, make) -> httpx.Response:
    """One HTTP call, no retry; a transport error becomes an ExperimentError (class name only)."""
    try:
        return make()
    except httpx.HTTPError as exc:
        raise ExperimentError(f"{what}: {type(exc).__name__}") from exc


def delete_recording(http: httpx.Client, recording_id: str) -> bool:
    """DELETE /recording/{id}. Never raises (it runs in a finally); says so when it fails."""
    try:
        response = http.delete(f"/recording/{recording_id}", timeout=CALL_TIMEOUT)
        if 200 <= response.status_code < 300:  # Skribby answers 204 (no body) on success
            print(f"  deleted recording {recording_id} on Skribby", flush=True)
            return True
        reason = f"HTTP {response.status_code}"
    except httpx.HTTPError as exc:
        reason = type(exc).__name__
    print(
        f"  COULD NOT DELETE recording {recording_id} on Skribby ({reason}): delete it by hand "
        f"(DELETE /recording/{recording_id})",
        file=sys.stderr,
        flush=True,
    )
    return False


def transcribe(
    http: httpx.Client, model: str, chunk: dx.Chunk, *, lang: str | None, poll_timeout: float
) -> dict:
    """Upload the slice once, wait for the transcript, delete the recording. Returns the body."""
    data = {"transcription_model": model}
    if lang:
        data["lang"] = lang
    recording_id = None
    try:
        with chunk.path.open("rb") as handle:
            response = _call(
                f"upload to {model}",
                lambda: http.post(
                    "/recording",
                    data=data,
                    files={"recording_file": (chunk.path.name, handle, "audio/webm")},
                    timeout=UPLOAD_TIMEOUT,
                ),
            )
        if response.status_code >= 300:
            raise _failure(f"upload to {model}", response)
        try:
            recording_id = response.json()["id"]
            if not isinstance(recording_id, str) or not recording_id:
                raise ValueError
        except (ValueError, KeyError, TypeError) as exc:
            raise ExperimentError(f"upload to {model}: the answer has no recording id") from exc
        print(f"  uploaded to {model} as recording {recording_id}", flush=True)
        deadline = time.monotonic() + poll_timeout
        with dx.heartbeat(model):
            while True:
                response = _call(
                    f"waiting for {model}",
                    lambda: http.get(f"/recording/{recording_id}", timeout=CALL_TIMEOUT),
                )
                if response.status_code >= 300:
                    raise _failure(f"waiting for {model}", response)
                try:
                    body = response.json()
                    status = body["status"]
                except (ValueError, KeyError, TypeError) as exc:
                    raise ExperimentError(f"waiting for {model}: the answer has no status") from exc
                if status == FINISHED:
                    return body
                if status in FAILED:
                    raise ExperimentError(f"{model}: Skribby reported status {status!r}")
                if time.monotonic() >= deadline:
                    raise ExperimentError(
                        f"{model}: not finished after {poll_timeout:g}s (last status {status!r})"
                    )
                time.sleep(POLL_SECONDS)
    finally:
        if recording_id:
            delete_recording(http, recording_id)


def model_turns(body: dict, offset: float) -> list[Turn]:
    """Skribby's transcript segments as turns in meeting time: `speaker` (the diarization tag),
    `start` and `end` in seconds into the slice, `transcript` the text."""
    transcript = body.get("transcript")
    if transcript is None:
        return []
    if not isinstance(transcript, list) or not all(isinstance(i, dict) for i in transcript):
        raise ExperimentError("the answer's 'transcript' is not a list of objects")
    turns = []
    for item in transcript:
        start, end = item.get("start"), item.get("end")
        if isinstance(start, bool) or isinstance(end, bool):
            continue
        if not isinstance(start, int | float) or not isinstance(end, int | float):
            continue
        tag = item.get("speaker")
        text = item.get("transcript")
        turns.append(
            Turn(
                speaker=str(tag) if tag is not None and not isinstance(tag, bool) else dx.NO_TAG,
                start=offset + float(start),
                end=offset + float(end),
                text=text if isinstance(text, str) else "",
            )
        )
    return turns


# ---- cost and plan ----------------------------------------------------------------------------


def model_prices(models: list[str], extra: float | None) -> dict[str, float]:
    """The per-hour rate of each model, or refuse: an unknown model needs --price."""
    prices = {}
    for model in models:
        if model in MODEL_PRICE_PER_HOUR:
            prices[model] = MODEL_PRICE_PER_HOUR[model]
        elif extra is not None:
            prices[model] = extra
        else:
            raise ExperimentError(
                f"{model} is not in the price table ({', '.join(MODEL_PRICE_PER_HOUR)}): "
                "give its per-hour price with --price (see skribby.io/docs/guides/billing)"
            )
    return prices


def estimate(seconds: float, rate: float) -> float:
    return seconds / 3600 * (BASE_PRICE_PER_HOUR + rate)


@dataclass
class Sent:
    """What has gone to Skribby so far (for the message when a run stops)."""

    models: list[str] = field(default_factory=list)


# ---- running ----------------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("meeting_id", type=int)
    parser.add_argument("--window", type=dx._window, metavar="FROM-TO", help="default 400-1000")
    parser.add_argument("--models", default=",".join(DEFAULT_MODELS), help="comma-separated ids")
    parser.add_argument("--price", type=float, help="per-hour price of a model not in the table")
    parser.add_argument("--max-minutes", type=float, default=15.0, help="per model")
    parser.add_argument("--poll-timeout", type=float, default=1200.0, help="seconds, per model")
    parser.add_argument("--lang", help="language code for Skribby (optional)")
    parser.add_argument("--checkpoints", metavar="FILE.json")
    parser.add_argument("--show-text", type=dx._window, metavar="FROM-TO")
    parser.add_argument("--dry-run", action="store_true", help="plan and cost only, no upload")
    meet = parser.add_argument_group("Meet's own speaker timeline as the reference")
    meet.add_argument(
        "--score-against-meet",
        action="store_true",
        help="score each model against who Meet says was speaking (needs --window)",
    )
    meet.add_argument(
        "--list-windows",
        action="store_true",
        help="list the windows where most participants spoke; calls nothing, needs no audio or key",
    )
    meet.add_argument("--window-length", type=float, default=WINDOW_LENGTH, help="seconds")
    meet.add_argument("--top", type=int, default=3, help="how many windows --list-windows shows")
    meet.add_argument(
        "--min-speech",
        type=float,
        default=MIN_SPEECH,
        help="seconds a participant must speak in the slice to count (default 2)",
    )
    meet.add_argument(
        "--meet-offset",
        type=float,
        default=0.0,
        help="seconds added to Meet's times (see the clock check line)",
    )
    return parser


def _meet_inputs(args) -> tuple[MeetTimeline, list[dict]]:
    """Meet's timeline on the meeting's clock and the old Skribby items (for the clock check)."""
    timeline = meet_timeline(load_meet_record(args.meeting_id), args.meet_offset)
    return timeline, dx.load_skribby_items(args.meeting_id)


def run(argv: list[str], *, http: httpx.Client | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.list_windows:
        try:
            timeline, items = _meet_inputs(args)
        except ExperimentError as exc:
            print(f"Stopped: {exc}", file=sys.stderr)
            return 2
        print(
            windows_text(args.meeting_id, timeline, args.window_length, args.min_speech, args.top)
        )
        print(clock_check(timeline, items))
        return 0
    models = list(dict.fromkeys(m.strip() for m in args.models.split(",") if m.strip()))
    sent = Sent()
    by_meet = args.score_against_meet
    try:
        if not models:
            raise ExperimentError("no models given")
        if "soniox/stt-async-v5" in models:
            raise ExperimentError("soniox/stt-async-v5 is deprecated and already measured: skip it")
        prices = model_prices(models, args.price)
        if by_meet:
            if args.window is None:
                raise ExperimentError(
                    "--score-against-meet needs --window FROM-TO (the default 400-1000 was picked "
                    "for meeting 32): run --list-windows first to see where most people spoke"
                )
            timeline, items = _meet_inputs(args)
            start, end = args.window
            spoke = speech_in(timeline.spans, start, end, args.min_speech)
            if len(spoke) < 2:
                raise ExperimentError(
                    f"only {len(spoke)} participant(s) spoke at least {args.min_speech:g} s in "
                    f"{start:g}-{end:g} s by Meet's timeline: pick another window "
                    "(--list-windows). Nothing was sent"
                )
            checkpoints = None
        else:
            start, end = args.window or DEFAULT_WINDOW
            checkpoints = load_checkpoints(args.checkpoints)
            outside = [p for p in checkpoints.points if p[1] < start or p[2] > end]
            if outside:
                raise ExperimentError(
                    f"{len(outside)} checkpoint(s) fall outside the window {start:g}-{end:g}s: "
                    "widen the window or change the checkpoints. Nothing was sent"
                )
        with dx.prepared_chunks(args.meeting_id) as (paths, how), ExitStack() as stack:
            chunks = dx.measure(paths)
            total = sum(c.seconds for c in chunks)
            if start >= total:
                raise ExperimentError(
                    f"the window starts at {start:.0f}s but the audio is {total:.0f}s long"
                )
            end = min(end, total)
            folder = dx.audio_path(args.meeting_id).parent
            piece = stack.enter_context(dx.cut_slice(chunks, start, end, folder))
            minutes = piece.seconds / 60
            print(f"Audio: {how}; the slice {dx._clock(start)}-{dx._clock(end)} cut out of it.")
            print(f"Slice: {minutes:.1f} minutes, {piece.size / 1_048_576:.1f} MB, WebM Opus.")
            if minutes > args.max_minutes:
                raise ExperimentError(
                    f"{minutes:.1f} minutes is more than --max-minutes {args.max_minutes:g} "
                    "per model: nothing was sent"
                )
            print("Estimated cost (base $0.10/h + the model's rate, billed on success):")
            for model in models:
                print(f"  {model}: ${estimate(piece.seconds, prices[model]):.3f}")
            print(
                f"  total for {len(models)} model(s): "
                f"${sum(estimate(piece.seconds, prices[m]) for m in models):.3f}"
            )
            if by_meet:
                spoke = speech_in(timeline.spans, start, end, args.min_speech)
                print(
                    f"Meet's timeline ({timeline.how}): {len(spoke)} participant(s) spoke at "
                    f"least {args.min_speech:g} s in the slice."
                )
                check = clock_check(timeline, items)
                print(check)
            else:
                items = dx.load_skribby_items(args.meeting_id)
                print(f"Old Skribby evidence: {len(items)} item(s) kept.")
            if args.dry_run:
                print("Dry run: nothing was sent to Skribby.")
                return 0
            if http is None:
                settings = get_settings()
                key = settings.skribby_api_key.strip()
                if not key:
                    raise ExperimentError("SKRIBBY_API_KEY is not set")
                http = httpx.Client(
                    base_url=BASES[settings.skribby_region],
                    headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
                    follow_redirects=False,
                )
            results: list[tuple[str, int, Score]] = []
            meet_results: list[tuple[str, MeetScore]] = []
            for number, model in enumerate(models, 1):
                print(f"\n== {model} ({number} of {len(models)}) ==", flush=True)
                sent.models.append(model)
                body = transcribe(
                    http, model, piece, lang=args.lang, poll_timeout=args.poll_timeout
                )
                turns = model_turns(body, piece.offset)
                if by_meet:
                    graded = meet_score(turns, timeline.spans, start, end, args.min_speech)
                    print(meet_score_text(graded, model))
                    meet_results.append((model, graded))
                else:
                    print(dx.summary(turns, items))
                    result = score(turns, items, checkpoints)
                    print(score_text(result, checkpoints))
                    results.append((model, len(dx.speaker_stats(turns)), result))
                if args.show_text:
                    print(dx.window_text(turns, items, *args.show_text))
    except ExperimentError as exc:
        print(f"Stopped: {exc}", file=sys.stderr)
        if sent.models:
            print(
                "Already sent to Skribby: " + ", ".join(sent.models) + " (the last one may not "
                "have finished; Skribby bills only successful transcriptions).",
                file=sys.stderr,
            )
        return 2
    print("\nSummary:")
    if by_meet:
        print(f"  {check}")
        ranked = sorted(meet_results, key=lambda r: r[1].ranking(), reverse=True)
        for place, (model, graded) in enumerate(ranked, 1):
            print(f"  {place}. {verdict(graded, model)}")
        return 0
    for model, speakers, result in results:
        print(f"  {model}: {speakers} speaker(s), {'PASS' if result.passed else 'FAIL'}")
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
