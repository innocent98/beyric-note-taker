"""Read Skribby's transcript and ours of the SAME meeting, side by side, to judge whether Skribby's
could replace ours (it costs about $0.64 an hour less). It changes nothing in the app.

    # one meeting still kept at Skribby (the bot id is the one `skribby_live_check.py` printed):
    PYTHONPATH=. python scripts/transcript_compare.py --bot-id <bot id>

    # again, later, with no Skribby and no OpenAI (everything was saved in the output folder):
    PYTHONPATH=. python scripts/transcript_compare.py --audio <folder>/recording \\
        --skribby-json <folder>/skribby.json --our-text <folder>/ours.txt

What it does: (1) asks Skribby for the bot's transcript (speaker name and text per segment) and
downloads the recording, (2) runs OUR transcription on that same audio with the pipeline's own
functions (`app.audio.prepare_recording`, `app.transcribe.transcribe_chunks`) and the same model
setting, plus the terms in `--vocabulary` if given, (3) writes `compare.html` (one self-contained
page) and `compare.txt` (the same in plain text). It NEVER deletes anything at Skribby.

How the two are lined up. Our transcript is plain text with no times, so each row of the page is a
Skribby turn (a speaker's run of segments) and beside it the stretch of OUR words that matches it:
the turn's first words are looked for in our text near where they should fall; when they are found
that is where our stretch starts ("matched"), when not the start is worked out from the turns around
it ("by position", marked). Every word of both transcripts appears exactly once; nothing is
written by this tool.

Keys (never printed): `SKRIBBY_API_KEY`, `SKRIBBY_REGION` and `OPENAI_API_KEY`, from the environment
first, else from `~/.config/beyric-bakeoff/keys.env` (`NAME=value` per line), as the live check.
"""

import argparse
import html
import json
import os
import re
import shutil
import sys
import textwrap
from collections import Counter
from pathlib import Path

from app import audio as audio_tools
from app.bot_provider import BotProviderError
from app.config import get_settings
from app.skribby import _recording_url
from app.transcribe import TranscriptionFailed, transcribe_chunks
from scripts import skribby_live_check as live

# Estimated cost, USD per meeting hour (not measured): docs/sop/2026-11-08-skribby-provider.md
# section 1.4 (Skribby bot 0.35 base, 0.04 for groq/whisper-large-v3-turbo, 0.10 for Soniox) and
# docs/HANDOFF-master.md (our OpenAI transcription about 0.70).
BOT_PER_HOUR = 0.35
SKRIBBY_GROQ_PER_HOUR = 0.04
SKRIBBY_SONIOX_PER_HOUR = 0.10
OUR_TRANSCRIPTION_PER_HOUR = 0.70

MIN_ANCHOR_WORDS = 2
ANCHOR_WORDS = 3
MIN_WINDOW = 40
WINDOW_SHARE = 0.05
DEFAULT_OUT_ROOT = Path.home() / "transcript-compare"
RECORDING_NAME = "recording"
SKRIBBY_NAME = "skribby.json"
OURS_NAME = "ours.txt"

_WORD = re.compile(r"[^\W_]+(?:'[^\W_]+)*")


class CompareError(Exception):
    """Something the person running it can fix; printed plainly, no traceback."""


# ---- words and turns ----


def word_key(token: str) -> str:
    """A token reduced to what it says: lower case, no punctuation ('' for a bare dash)."""
    return "".join(_WORD.findall(token.lower().replace("’", "'")))


def count_words(text: str) -> int:
    """Words the way the pipeline counts them (`len(text.split())`)."""
    return len(text.split())


def _to_float(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _display_name(segment: dict) -> str:
    name = segment.get("speaker_name")
    if isinstance(name, str) and name.strip():
        return " ".join(name.split())
    number = segment.get("speaker")
    if isinstance(number, int) and not isinstance(number, bool):
        return f"Speaker {number}"
    return "(no name)"


def load_segments(body) -> list[dict]:
    """The transcript items of a saved or live Skribby answer (a bot body, or the bare list).
    Items without text are skipped; any other shape is an error, never a guess."""
    items = body.get("transcript") if isinstance(body, dict) else body
    if items is None:
        return []
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise CompareError("The Skribby transcript is not a list of segments; cannot read it.")
    segments = []
    for item in items:
        text = item.get("transcript")
        if isinstance(text, str) and text.strip():
            segments.append(
                {
                    "speaker": _display_name(item),
                    "text": " ".join(text.split()),
                    "start": _to_float(item.get("start")),
                    "end": _to_float(item.get("end")),
                }
            )
    return segments


def time_scale(segments: list[dict], duration: float | None) -> float:
    """Skribby's page does not say the unit of `start`/`end`. Seconds unless the numbers are far
    larger than the recording (milliseconds); returned as the divisor that gives seconds."""
    ends = [s["end"] for s in segments if s["end"] is not None]
    if not ends:
        return 1.0
    limit = duration * 5 if duration else 36000.0
    return 1000.0 if max(ends) > limit else 1.0


def make_turns(segments: list[dict], scale: float = 1.0) -> list[dict]:
    """Consecutive segments of one speaker as one turn, with the turn's start and end (seconds)."""
    turns: list[dict] = []
    for seg in segments:
        start = None if seg["start"] is None else seg["start"] / scale
        end = None if seg["end"] is None else seg["end"] / scale
        if turns and turns[-1]["speaker"] == seg["speaker"]:
            turns[-1]["text"] += " " + seg["text"]
            if end is not None:
                turns[-1]["end"] = end
            continue
        turns.append({"speaker": seg["speaker"], "text": seg["text"], "start": start, "end": end})
    return turns


# ---- lining the two transcripts up ----


def align(turn_keys: list[list[str]], ours: list[str]) -> tuple[list[int], list[bool]]:
    """Where in OUR words (`ours`, normalised, no blanks) each Skribby turn begins.

    `turn_keys[i]` are the normalised words of turn i. Returns `(cuts, matched)`: `cuts[i]` is the
    index in `ours` where turn i's stretch starts (turn i ends where turn i+1 starts, the last at
    the end; `cuts[0]` is 0 so nothing before the first turn is lost), `matched[i]` is True when
    that start was found by the turn's own first words and False when worked out from its
    neighbours by position. Always non-decreasing."""
    n_turns = len(turn_keys)
    n_ours = len(ours)
    total = sum(len(k) for k in turn_keys)
    if n_turns == 0:
        return [], []
    cuts = [0] * n_turns
    matched = [False] * n_turns
    ratio = n_ours / total if total else 0.0
    window = max(MIN_WINDOW, int(n_ours * WINDOW_SHARE))
    before = 0  # Skribby words before this turn
    last_s, last_cut = 0, 0
    starts = []
    for keys in turn_keys:
        starts.append(before)
        before += len(keys)
    for i in range(1, n_turns):
        keys = turn_keys[i]
        gram = keys[:ANCHOR_WORDS]
        expected = last_cut + round((starts[i] - last_s) * ratio)
        if len(gram) >= MIN_ANCHOR_WORDS:
            best = None
            low = max(last_cut + 1, expected - window)
            high = min(n_ours - len(gram), expected + window)
            for j in range(low, high + 1):
                if ours[j : j + len(gram)] == gram and (
                    best is None or abs(j - expected) < abs(best - expected)
                ):
                    best = j
            if best is not None:
                cuts[i], matched[i] = best, True
                last_s, last_cut = starts[i], best
                continue
        cuts[i] = min(max(expected, last_cut), n_ours)
    # Unmatched turns: place them between the matched ones around them, by Skribby's own words.
    anchors = [(0, 0)] + [(starts[i], cuts[i]) for i in range(1, n_turns) if matched[i]]
    anchors.append((total, n_ours))
    for i in range(1, n_turns):
        if matched[i]:
            continue
        low = max(a for a in anchors if a[0] <= starts[i])
        high = min((a for a in anchors if a[0] > starts[i]), default=(total, n_ours))
        span = high[0] - low[0]
        cuts[i] = (
            low[1] + round((starts[i] - low[0]) / span * (high[1] - low[1])) if span else low[1]
        )
    for i in range(1, n_turns):
        cuts[i] = min(max(cuts[i], cuts[i - 1]), n_ours)
    return cuts, matched


def build_rows(turns: list[dict], our_text: str) -> list[dict]:
    """One row per Skribby turn, each with our matching stretch of words (all of our words are used
    exactly once). With no Skribby turns there is one row holding all of our text."""
    tokens = our_text.split()
    positions = [i for i, tok in enumerate(tokens) if word_key(tok)]
    ours = [word_key(tokens[i]) for i in positions]
    if not turns:
        return [
            {
                "speaker": None,
                "start": None,
                "end": None,
                "skribby": "",
                "ours": " ".join(tokens),
                "matched": False,
                "shared": None,
            }
        ]
    turn_keys = [[k for k in map(word_key, t["text"].split()) if k] for t in turns]
    cuts, matched = align(turn_keys, ours)
    token_cuts = [
        0 if i == 0 else (positions[c] if c < len(positions) else len(tokens))
        for i, c in enumerate(cuts)
    ]
    rows = []
    for i, turn in enumerate(turns):
        end = token_cuts[i + 1] if i + 1 < len(turns) else len(tokens)
        mine = " ".join(tokens[token_cuts[i] : end])
        mine_keys = [k for k in map(word_key, mine.split()) if k]
        longest = max(len(turn_keys[i]), len(mine_keys))
        shared = (
            round(100 * sum((Counter(turn_keys[i]) & Counter(mine_keys)).values()) / longest)
            if longest
            else None
        )
        rows.append(
            {
                "speaker": turn["speaker"],
                "start": turn["start"],
                "end": turn["end"],
                "skribby": turn["text"],
                "ours": mine,
                "matched": matched[i] or i == 0,
                "shared": shared,
            }
        )
    return rows


# ---- cost and stats ----


def cost_paths(duration_seconds: float | None) -> list[dict]:
    """The two paths with their estimated cost an hour and for this recording."""
    hours = duration_seconds / 3600 if duration_seconds else None
    paths = [
        (
            "Today: Skribby bot and names (groq) + our OpenAI transcription",
            BOT_PER_HOUR + SKRIBBY_GROQ_PER_HOUR + OUR_TRANSCRIPTION_PER_HOUR,
        ),
        (
            "Switch: Skribby bot and its own transcript (Soniox)",
            BOT_PER_HOUR + SKRIBBY_SONIOX_PER_HOUR,
        ),
    ]
    return [
        {"name": name, "per_hour": rate, "this_recording": None if hours is None else rate * hours}
        for name, rate in paths
    ]


def clock(seconds: float | None) -> str:
    if seconds is None:
        return ""
    seconds = int(seconds)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def make_stats(
    turns, rows, skribby_text, our_text, duration, model, vocab_terms, bot_model=None
) -> dict:
    speakers = Counter(t["speaker"] for t in turns)
    return {
        "duration": duration,
        "skribby_words": count_words(skribby_text),
        "our_words": count_words(our_text),
        "turns": len(turns),
        "matched": sum(1 for r in rows if r["matched"]) if turns else 0,
        "speakers": speakers,
        "our_model": model,
        "vocab_terms": vocab_terms,
        "skribby_model": bot_model,
        "costs": cost_paths(duration),
    }


# ---- the two outputs ----


def _span(row) -> str:
    if row["start"] is None:
        return ""
    end = f"-{clock(row['end'])}" if row["end"] is not None else ""
    return f"{clock(row['start'])}{end}"


def render_text(rows, stats, source_note: str) -> str:
    lines = [
        "SKRIBBY TRANSCRIPT vs OUR TRANSCRIPT",
        source_note,
        "",
        f"Recording length: {clock(stats['duration']) or 'unknown'}",
        f"Words: Skribby {stats['skribby_words']}, ours {stats['our_words']}",
        f"Our model: {stats['our_model']}; vocabulary terms given: {stats['vocab_terms']}",
        "Speakers on Skribby's side: "
        + (", ".join(f"{n} ({c} turns)" for n, c in stats["speakers"].items()) or "none"),
        f"Rows lined up by matching words: {stats['matched']} of {stats['turns']}"
        " (the rest placed by position)",
        "Estimated cost (not measured):",
    ]
    for cost in stats["costs"]:
        here = (
            ""
            if cost["this_recording"] is None
            else f", this recording ${cost['this_recording']:.2f}"
        )
        lines.append(f"  {cost['name']}: ${cost['per_hour']:.2f} an hour{here}")
    lines += ["", "=" * 78]
    for number, row in enumerate(rows, 1):
        who = row["speaker"] or "(Skribby returned no transcript)"
        head = f"#{number}  {who}  {_span(row)}".rstrip()
        lines += ["", head]
        for label, text in (("SKRIBBY", row["skribby"]), ("OURS   ", row["ours"])):
            count = count_words(text)
            tag = ""
            if label == "OURS   " and row["speaker"] is not None:
                how = "matched" if row["matched"] else "placed by position"
                share = f", {row['shared']}% of words shared" if row["shared"] is not None else ""
                tag = f" [{how}{share}]"
            lines.append(f"  {label} ({count} words){tag}:")
            body = text or "(nothing)"
            lines += textwrap.wrap(body, 90, initial_indent="    ", subsequent_indent="    ")
    return "\n".join(lines) + "\n"


_CSS = """
:root{--bg:#fafaf7;--fg:#1d1d1b;--mut:#6b6b66;--card:#fff;--line:#e2e1da;--a:#2257c4;--b:#b45309;
--warn:#fef3c7}
@media(prefers-color-scheme:dark){:root{--bg:#161614;--fg:#ecebe6;--mut:#9a9992;--card:#1f1f1c;
--line:#33332f;--a:#7aa2f7;--b:#f0a14a;--warn:#3a3217}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.55 system-ui,-apple-system,Segoe UI,sans-serif;padding:24px 16px}
main{max-width:1200px;margin:0 auto}h1{font-size:22px;margin:0 0 4px}
.sub{color:var(--mut);margin:0 0 20px}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;margin-bottom:24px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.card h2{font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:var(--mut);
margin:0 0 6px}
.big{font-size:22px;font-weight:600}.card p{margin:2px 0}.mut{color:var(--mut)}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:12px;position:sticky;top:0;
background:var(--bg);padding:8px 0;font-weight:600;z-index:1}
.cols .a,.cell.a h3{color:var(--a)}.cols .b,.cell.b h3{color:var(--b)}
.row{border-top:1px solid var(--line);padding:12px 0}
.meta{font-size:13px;color:var(--mut);margin-bottom:6px}.meta b{color:var(--fg)}
.pair{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.cell{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px 12px;
overflow-wrap:anywhere}.cell h3{font-size:12px;margin:0 0 4px}
.tag{font-size:12px;border-radius:999px;padding:1px 8px;border:1px solid var(--line)}
.pos{background:var(--warn)}
@media(max-width:760px){.cols{display:none}.pair{grid-template-columns:1fr}}
"""


def render_html(rows, stats, source_note: str) -> str:
    e = html.escape
    cost_lines = "".join(
        f"<p><b>${c['per_hour']:.2f}</b> an hour"
        + ("" if c["this_recording"] is None else f" (this recording ${c['this_recording']:.2f})")
        + f"<br><span class='mut'>{e(c['name'])}</span></p>"
        for c in stats["costs"]
    )
    speakers = (
        "".join(f"<p>{e(n)}: {c} turns</p>" for n, c in stats["speakers"].items()) or "<p>none</p>"
    )
    out = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        "<title>Transcript comparison</title>",
        f"<style>{_CSS}</style></head><body><main>",
        "<h1>Skribby transcript vs our transcript</h1>",
        f"<p class='sub'>{e(source_note)}</p>",
        "<div class='stats'>",
        "<div class='card'><h2>Recording length</h2>"
        f"<div class='big'>{e(clock(stats['duration']) or 'unknown')}</div></div>",
        "<div class='card'><h2>Words</h2>"
        f"<p><span class='big' style='color:var(--a)'>{stats['skribby_words']}</span> Skribby</p>"
        f"<p><span class='big' style='color:var(--b)'>{stats['our_words']}</span> ours</p></div>",
        "<div class='card'><h2>Estimated cost (not measured)</h2>" + cost_lines + "</div>",
        "<div class='card'><h2>Skribby's speakers</h2>" + speakers + "</div>",
        "<div class='card'><h2>Our settings</h2>"
        f"<p>Model: {e(str(stats['our_model']))}</p>"
        f"<p>Vocabulary terms given: {stats['vocab_terms']}</p>"
        + (f"<p>Skribby model: {e(stats['skribby_model'])}</p>" if stats["skribby_model"] else "")
        + "</div>",
        "<div class='card'><h2>Lining up</h2>"
        f"<p>{stats['matched']} of {stats['turns']} rows matched by their own words; the rest"
        " are placed by position (marked).</p></div>",
        "</div>",
        "<div class='cols'><div class='a'>Skribby (with speaker names)</div>"
        "<div class='b'>Ours (the pipeline's transcription)</div></div>",
    ]
    for number, row in enumerate(rows, 1):
        who = e(row["speaker"] or "Skribby returned no transcript")
        span = e(_span(row))
        placed = ""
        if row["speaker"] is not None:
            if row["matched"]:
                share = (
                    f" &middot; {row['shared']}% of words shared"
                    if row["shared"] is not None
                    else ""
                )
                placed = f"<span class='tag'>matched{share}</span>"
            else:
                share = f" &middot; {row['shared']}% shared" if row["shared"] is not None else ""
                placed = f"<span class='tag pos'>placed by position{share}</span>"
        out.append(
            f"<section class='row'><div class='meta'>#{number} <b>{who}</b> {span} {placed}</div>"
            "<div class='pair'>"
            f"<div class='cell a'><h3>SKRIBBY &middot; {count_words(row['skribby'])} words</h3>"
            f"{e(row['skribby']) or '<span class=mut>(nothing)</span>'}</div>"
            f"<div class='cell b'><h3>OURS &middot; {count_words(row['ours'])} words</h3>"
            f"{e(row['ours']) or '<span class=mut>(nothing)</span>'}</div>"
            "</div></section>"
        )
    out.append("</main></body></html>")
    return "\n".join(out)


# ---- inputs ----


def read_vocabulary(path) -> list[str]:
    """One term per line; blank lines and lines starting with # are skipped."""
    terms = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            terms.append(line)
    return terms


def fetch_skribby(args, keys, out_dir: Path, say, client=None) -> tuple[dict, Path]:
    """The transcript and the recording of one bot kept at Skribby, saved in `out_dir` (a file that
    is already there is reused, so a second run needs no Skribby). Nothing is deleted at Skribby.
    The saved JSON keeps the bot's id, status, model and transcript, never the recording link."""
    recording = out_dir / RECORDING_NAME
    saved = out_dir / SKRIBBY_NAME
    if saved.is_file() and recording.is_file():
        say(f"Using the transcript and recording already saved in {out_dir}.")
        return read_json(saved), recording
    if client is None:
        if not keys.get("SKRIBBY_API_KEY"):
            raise CompareError(
                "SKRIBBY_API_KEY is missing. Put it in ~/.config/beyric-bakeoff/keys.env "
                "or set it as an environment variable."
            )
        client = live.build(
            argparse.Namespace(guest=True, bot_name="compare", model="none"), keys
        )._client
    try:
        raw = client.get_bot(args.bot_id)
    except BotProviderError as exc:
        raise CompareError(
            f"Skribby has no such bot, or could not be asked ({type(exc).__name__}): {exc}. "
            "If the app already handled this meeting, it deleted Skribby's copy (see the SOP)."
        ) from None
    status = raw.get("status")
    if status != "finished":
        raise CompareError(f"Skribby says this bot is `{status}`, not `finished`. Try again later.")
    if not isinstance(raw.get("transcript"), list) or not raw["transcript"]:
        raise CompareError(
            "Skribby kept no transcript for this bot (was its transcription model `none`?)."
        )
    url = _recording_url(raw)
    if url is None:
        raise CompareError("Skribby has no recording for this bot (deleted, or it did not record).")
    body = {k: raw[k] for k in ("id", "status", "stop_reason", "transcription_model") if k in raw}
    body["transcript"] = raw["transcript"]
    out_dir.mkdir(parents=True, exist_ok=True)
    say("Skribby answered with a finished bot. Downloading the recording (link not shown).")
    partial = recording.with_name(recording.name + ".part")
    try:
        client.download(url, partial)
        os.replace(partial, recording)
    except BotProviderError as exc:
        raise CompareError(
            f"The recording could not be downloaded ({type(exc).__name__})."
        ) from None
    finally:
        partial.unlink(missing_ok=True)
    saved.write_text(json.dumps(body, indent=2, ensure_ascii=False), encoding="utf-8")
    say(f"Saved {saved.name} and {recording.name} ({recording.stat().st_size} bytes).")
    return body, recording


def read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CompareError(f"Could not read {path} as JSON: {exc}") from None


def our_transcription(recording: Path, work: Path, terms, openai_key: str, say) -> str:
    """OUR transcript of `recording`: the pipeline's own `prepare_recording` and
    `transcribe_chunks`, with the same model setting (`TRANSCRIPTION_MODEL`)."""
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise CompareError("ffmpeg is needed (brew install ffmpeg).")
    from openai import OpenAI

    chunks_dir = work / "chunks"
    try:
        say("Preparing the audio (ffmpeg) the way the app does.")
        prepared = audio_tools.prepare_recording(recording, chunks_dir)
        say(
            f"Transcribing {len(prepared.chunks)} chunk(s) with "
            f"{get_settings().transcription_model} and {len(terms)} vocabulary term(s)."
        )
        client = OpenAI(api_key=openai_key, max_retries=0, timeout=300.0)
        return transcribe_chunks(prepared.chunks, terms, client=client)
    except audio_tools.AudioError as exc:
        raise CompareError(f"The audio could not be prepared: {exc}") from None
    except TranscriptionFailed as exc:
        raise CompareError(f"Our transcription failed: {exc}") from None
    finally:
        shutil.rmtree(chunks_dir, ignore_errors=True)


def probe_seconds(path: Path) -> float | None:
    try:
        return audio_tools._duration(Path(path))
    except audio_tools.AudioError:
        return None


def main(argv=None, *, env=None, out=None, client=None, transcribe=None) -> int:
    out = out or sys.stdout
    parser = argparse.ArgumentParser(
        description="Skribby's transcript and ours of one meeting, side by side."
    )
    parser.add_argument("--bot-id", help="a Skribby bot still kept there (fetches, then compares)")
    parser.add_argument("--audio", help="an already-downloaded recording (offline mode)")
    parser.add_argument("--skribby-json", help="the saved Skribby transcript (offline mode)")
    parser.add_argument("--our-text", help="a saved copy of OUR transcript: skips OpenAI")
    parser.add_argument("--vocabulary", help="a file of terms, one per line, like your vocabulary")
    parser.add_argument("--keys-file", default=None, help=f"default {live.DEFAULT_KEYS_FILE}")
    parser.add_argument("--out-dir", default=None, help=f"default {DEFAULT_OUT_ROOT}/<name>")
    args = parser.parse_args(argv)
    keys = live.load_keys(env, args.keys_file)

    def say(text: str = "") -> None:
        print(live.scrub(text, keys), file=out, flush=True)

    try:
        return run(args, keys, say, client, transcribe)
    except CompareError as exc:
        say(str(exc))
        return 2


def run(args, keys, say, client=None, transcribe=None) -> int:
    offline = bool(args.audio or args.skribby_json)
    if bool(args.bot_id) == offline:
        raise CompareError("Give either --bot-id, or --skribby-json (with --audio or --our-text).")
    if offline and not args.skribby_json:
        raise CompareError("Offline mode needs --skribby-json too.")
    if offline and not (args.audio or args.our_text):
        raise CompareError("Offline mode needs --audio, or --our-text to skip transcribing.")
    if args.bot_id:
        name = args.bot_id[:8]
    else:
        name = Path(args.audio or args.skribby_json).stem
    out_dir = Path(args.out_dir) if args.out_dir else DEFAULT_OUT_ROOT / name
    out_dir.mkdir(parents=True, exist_ok=True)
    terms = read_vocabulary(args.vocabulary) if args.vocabulary else []

    if offline:
        body = read_json(Path(args.skribby_json))
        recording = Path(args.audio) if args.audio else None
        if recording is not None and not recording.is_file():
            raise CompareError(f"Audio file not found: {recording}")
        source = "Offline: saved Skribby transcript"
    else:
        body, recording = fetch_skribby(args, keys, out_dir, say, client)
        source = f"Skribby bot {args.bot_id}"
    segments = load_segments(body)

    if args.our_text:
        try:
            our_text = Path(args.our_text).read_text(encoding="utf-8")
        except OSError as exc:
            raise CompareError(f"Could not read {args.our_text}: {exc}") from None
        say("Using the saved copy of our transcript (no OpenAI call).")
    else:
        key = keys.get("OPENAI_API_KEY") or ""
        if not key:
            raise CompareError(
                "OPENAI_API_KEY is missing. Put it in ~/.config/beyric-bakeoff/keys.env "
                "or set it as an environment variable."
            )
        runner = transcribe or our_transcription
        our_text = runner(recording, out_dir, terms, key, say)
        (out_dir / OURS_NAME).write_text(our_text, encoding="utf-8")
        say(f"Saved our transcript as {OURS_NAME}.")

    duration = probe_seconds(recording) if recording is not None else None
    scale = time_scale(segments, duration)
    if duration is None:
        last = [s["end"] for s in segments if s["end"] is not None]
        duration = max(last) / scale if last else None
    turns = make_turns(segments, scale)
    rows = build_rows(turns, our_text)
    skribby_text = " ".join(s["text"] for s in segments)
    model = get_settings().transcription_model
    bot_model = body.get("transcription_model") if isinstance(body, dict) else None
    stats = make_stats(
        turns,
        rows,
        skribby_text,
        our_text,
        duration,
        model,
        len(terms),
        bot_model if isinstance(bot_model, str) else None,
    )
    note = f"{source}. Times and cost are estimates; see the SOP for how to read this."
    (out_dir / "compare.html").write_text(render_html(rows, stats, note), encoding="utf-8")
    (out_dir / "compare.txt").write_text(render_text(rows, stats, note), encoding="utf-8")
    say()
    say(
        f"Skribby {stats['skribby_words']} words, ours {stats['our_words']} words, "
        f"{len(rows)} rows, {stats['matched']} matched by words."
    )
    say(f"Open this in a browser: {out_dir / 'compare.html'}")
    say(f"Plain text version:     {out_dir / 'compare.txt'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
