"""Compare notes models on the planted meeting (and, when present, speaker labels on a real call).

Not part of the test suite: it calls OpenAI and costs a few cents. From the repo root:

    OPENAI_API_KEY=... NOTES_MODEL=gpt-6-luna APP_ENV=development \
        .venv/bin/python tests/eval/compare_notes_models.py OUT_DIR

Writes <model>-notes.md (and <model>-speakers.md) into OUT_DIR and prints timing and token counts.
Score the notes by hand against the answer key in planted_meeting_key.md.
"""

import json
import os
import pathlib
import sys
import time

sys.path.insert(0, os.getcwd())
from openai import OpenAI  # noqa: E402

from app import notes  # noqa: E402
from app.models import Template  # noqa: E402

here = pathlib.Path(__file__).parent
out_dir = pathlib.Path(sys.argv[1])
out_dir.mkdir(parents=True, exist_ok=True)
model = os.environ["NOTES_MODEL"]
usage = {"input": 0, "output": 0}
real = OpenAI(api_key=os.environ["OPENAI_API_KEY"], max_retries=0, timeout=300)


class Counting:
    """Stands in for the OpenAI client and totals token usage across calls."""

    class chat:  # noqa: N801
        class completions:  # noqa: N801
            @staticmethod
            def create(**kw):
                r = real.chat.completions.create(**kw)
                usage["input"] += r.usage.prompt_tokens
                usage["output"] += r.usage.completion_tokens
                return r


template = Template(
    name="General",
    instructions=(
        "Write a summary, key points, decisions, action items with owners, and open questions."
    ),
)
start = time.time()
text = notes.generate_notes(
    (here / "planted_meeting.txt").read_text(),
    template,
    ["Beyric", "Postmark", "Resend"],
    client=Counting,
)
(out_dir / f"{model}-notes.md").write_text(text)
result = {
    "model": model,
    "notes_seconds": round(time.time() - start, 1),
    "notes_tokens": dict(usage),
}

# A real call's transcript and Vexa segments, kept out of git (personal data).
raw = pathlib.Path("spike/captures/raw")
if (raw / "zoom-transcript.json").exists():
    segments = json.loads((raw / "zoom-transcript.json").read_text())
    if isinstance(segments, dict):
        segments = segments.get("segments", [])
    usage.update(input=0, output=0)
    start = time.time()
    try:
        labelled, incomplete = notes.label_transcript(
            (raw / "zoom-gpt-transcribe.txt").read_text(), segments, client=Counting
        )
        (out_dir / f"{model}-speakers.md").write_text(labelled)
        result["labels"] = "unlabelled (drift check)" if incomplete else "labelled"
    except Exception as exc:  # report it, never hide it
        result["labels"] = f"failed: {type(exc).__name__}: {exc}"
    result.update(label_seconds=round(time.time() - start, 1), label_tokens=dict(usage))

print(json.dumps(result))
