"""TR1: the translation eval (spec sections 11 and 14 D8: "French and Spanish with names intact").

Not part of the test suite's API calls: it runs the real translation step on the cases in
`tests/eval/translation/` into French and Spanish, and scores what comes back. It costs a few
cents. From the repo root:

    OPENAI_API_KEY=... APP_ENV=development TRANSLATE_MODEL=gpt-6-luna \
        .venv/bin/python tests/eval/translation_eval.py OUT_DIR [--only NAME ...] [--language fr es]

Never put a key in the repository: pass it in the environment for the one command.

Writes `<case>.<language>.notes.md`, `<case>.<language>.transcript.txt` and `summary.json` into
OUT_DIR, so a person can read them. The code checks that ship (`app/translation.py`) already
reject a drifted answer, so a run that finishes has passed them; this adds what the gate wants:

- the translation was not rejected (no `TranslationFailed`): structure, numbers and names held;
- every name in the case's `must_keep` list that is in the source is in the output verbatim;
- the six headings are the dictionary's, in order, and the line count equals the original's;
- the injected order in the transcript is translated, not obeyed (the line is still a line);
- the cost per 1,000 words, at the prices below (override with --price-in/--price-out, dollars
  per million tokens: the luna prices in docs/sop/2026-11-02-notes-model-gpt-6-luna.md).

Fluency is not machine-checkable: read the files in OUT_DIR and say so in the SOP.
"""

import argparse
import json
import os
import pathlib
import re
import sys
from dataclasses import dataclass, field

sys.path.insert(0, os.getcwd())

from app import translation  # noqa: E402
from app.notes import HEADINGS  # noqa: E402
from app.translation import TranslationFailed  # noqa: E402

HERE = pathlib.Path(__file__).parent
CASES = HERE / "translation"
PRICE_IN, PRICE_OUT = 0.10, 0.50  # dollars per million tokens (gpt-6-luna, 2026-11-02)


@dataclass
class Result:
    case: str
    language: str
    passed: bool
    problems: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    words: int = 0
    cost: float = 0.0
    cost_per_1000_words: float = 0.0


def load_cases(only: list[str] | None = None) -> list[dict]:
    cases = [json.loads(path.read_text()) for path in sorted(CASES.glob("*.json"))]
    return [c for c in cases if not only or c["name"] in only]


def score_texts(case: dict, language: str, notes: str, transcript: str) -> list[str]:
    """What is wrong with a finished translation, as plain sentences (empty when it is right)."""
    problems = []
    headings = re.findall(r"(?m)^## (.+)$", notes)
    wanted = [translation.HEADINGS[language][h] for h in HEADINGS]
    if headings != wanted:
        problems.append(f"headings are {headings}, not the dictionary's {wanted}")
    if notes.count("\n") != case["notes"].count("\n"):
        problems.append("the notes' line count changed")
    if transcript.count("\n") != case["transcript"].count("\n"):
        problems.append("the transcript's line count changed")
    labels = re.findall(r"(?m)^\[([^\]]+)\]:", transcript)
    if labels != re.findall(r"(?m)^\[([^\]]+)\]:", case["transcript"]):
        problems.append("the transcript's speaker labels changed")
    for field_name, source, done in (
        ("notes", case["notes"], notes),
        ("transcript", case["transcript"], transcript),
    ):
        for name in case["must_keep"]:
            if name in source and name not in done:
                problems.append(f"the name {name!r} was changed or lost in the {field_name}")
    return problems


def run_case(
    case: dict, language: str, client, model: str | None, prices
) -> tuple[Result, str, str]:
    speakers = translation.speaker_names(case["transcript"])
    kwargs = {"terms": case["vocabulary"], "speakers": speakers, "client": client, "model": model}
    result = Result(case["name"], language, passed=False)
    notes = transcript = ""
    try:
        done_notes = translation.translate_notes(case["notes"], language, **kwargs)
        done_transcript = translation.translate_transcript(case["transcript"], language, **kwargs)
    except TranslationFailed as exc:
        result.problems.append(f"the translation was rejected by the code checks: {exc.code}")
        result.input_tokens, result.output_tokens = exc.input_tokens, exc.output_tokens
    else:
        notes, transcript = done_notes.text, done_transcript.text
        result.input_tokens = done_notes.input_tokens + done_transcript.input_tokens
        result.output_tokens = done_notes.output_tokens + done_transcript.output_tokens
        result.problems = score_texts(case, language, notes, transcript)
    result.passed = not result.problems
    result.words = len((case["notes"] + " " + case["transcript"]).split())
    result.cost = (result.input_tokens * prices[0] + result.output_tokens * prices[1]) / 1_000_000
    result.cost_per_1000_words = result.cost / max(result.words, 1) * 1000
    return result, notes, transcript


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("out_dir")
    parser.add_argument("--only", nargs="*")
    parser.add_argument("--language", nargs="*", default=list(translation.TARGETS))
    parser.add_argument("--price-in", type=float, default=PRICE_IN)
    parser.add_argument("--price-out", type=float, default=PRICE_OUT)
    args = parser.parse_args(argv)
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY is not set: nothing was run. See the header of this file.")
        return 2
    out = pathlib.Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    results = []
    for case in load_cases(args.only):
        for language in args.language:
            result, notes, transcript = run_case(
                case, language, None, None, (args.price_in, args.price_out)
            )
            (out / f"{case['name']}.{language}.notes.md").write_text(notes)
            (out / f"{case['name']}.{language}.transcript.txt").write_text(transcript)
            results.append(result)
            print(
                f"{case['name']} {language}: {'PASS' if result.passed else 'FAIL'} "
                f"{result.input_tokens} in, {result.output_tokens} out, "
                f"${result.cost:.4f} (${result.cost_per_1000_words:.4f} per 1,000 words)"
            )
            for problem in result.problems:
                print(f"    - {problem}")
    (out / "summary.json").write_text(json.dumps([r.__dict__ for r in results], indent=2))
    gate = all(r.passed for r in results) and bool(results)
    print("GATE:", "PASS" if gate else "FAIL")
    return 0 if gate else 1


if __name__ == "__main__":
    sys.exit(main())
