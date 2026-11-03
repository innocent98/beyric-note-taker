"""AI1: the extraction eval (spec section 3.1, "Quality gate before tasks ship").

Not part of the test suite's API calls: it runs the real notes step and the real extraction call
on the cases in `tests/eval/extraction/` and scores them against each case's answer key. It costs
a few cents. From the repo root:

    OPENAI_API_KEY=... APP_ENV=development EXTRACT_MODEL=gpt-6-luna \
        .venv/bin/python tests/eval/extraction_eval.py OUT_DIR [--only NAME ...]

Writes, into OUT_DIR, `<case>.notes.md` (reused by later runs, so two models see the same notes),
`<case>.result.json` and `summary.json`, and prints the gate. The gate (all must hold):

- zero invented owners (an owner where the key says none, or a forbidden or wrong person), after
  the code checks that ship;
- at least 95% of the returned quotes are found in the transcript;
- at least 90% of the stated due dates are right;
- the model returned an entry for at least 95% of the action bullets (nothing fell back to the
  parser).
"""

import argparse
import json
import os
import pathlib
import re
import sys
from dataclasses import dataclass
from datetime import date

sys.path.insert(0, os.getcwd())

from app import notes as notes_module  # noqa: E402
from app.extraction import (  # noqa: E402
    ExtractionFailed,
    ModelExtraction,
    ParsedNotes,
    check_extraction,
    extract,
    name_in_transcript,
    normalise,
    parse_notes,
)
from app.models import Template  # noqa: E402

HERE = pathlib.Path(__file__).parent
CASES = HERE / "extraction"
_PLAIN_LABEL = re.compile(r"(?m)^([A-Z][\w .'-]{0,40}): ")

QUOTES_MIN, DATES_MIN, COVERAGE_MIN = 0.95, 0.90, 0.95


@dataclass
class Case:
    name: str
    transcript: str
    meeting_date: date
    timezone: str
    vocabulary: list
    action_items: list
    review: list
    forbidden_owners: list


def load_cases(only: list[str] | None = None) -> list[Case]:
    cases = []
    for key_path in sorted(CASES.glob("*.json")):
        if only and key_path.stem not in only:
            continue
        key = json.loads(key_path.read_text())
        text = (key_path.parent / key["transcript"]).read_text().strip()
        text = _PLAIN_LABEL.sub(lambda m: f"[{m.group(1)}]: ", text)  # the pipeline's label form
        cases.append(
            Case(
                name=key_path.stem,
                transcript=text,
                meeting_date=date.fromisoformat(key["meeting_date"]),
                timezone=key["timezone"],
                vocabulary=key["vocabulary"],
                action_items=key["action_items"],
                review=key["review"],
                forbidden_owners=key["forbidden_owners"],
            )
        )
    return cases


def _same_person(a: str | None, b: str | None) -> bool:
    x, y = normalise(a or ""), normalise(b or "")
    if not x or not y:
        return False
    return x == y or x.split()[0] == y.split()[0]


def _bullet_for(keywords: list[str], bullets: list[str], taken: set[int]) -> int | None:
    for index, bullet in enumerate(bullets):
        if index not in taken and all(k.lower() in bullet.lower() for k in keywords):
            taken.add(index)
            return index
    return None


def score_case(case: Case, parsed: ParsedNotes, raw: ModelExtraction, checked) -> dict:
    """Numbers for one case. `raw` is what the model said, `checked` what code kept of it."""
    by_raw = {a.index: a for a in raw.action_items if 0 <= a.index < len(parsed.action_items)}
    by_checked = {a.index: a for a in checked.action_items}
    s = dict(
        case=case.name,
        bullets=len(parsed.action_items),
        covered=len(by_raw),
        quotes_given=sum(1 for a in by_raw.values() if a.quote),
        quotes_verified=sum(
            1 for i, a in by_raw.items() if a.quote and not by_checked[i].unverified
        ),
        invented_owners=0,
        raw_invented_owners=0,
        wrong_owners=0,
        dates_stated=0,
        dates_right=0,
        dates_invented=0,
        missing_from_notes=0,
        review_expected=len(case.review),
        review_found=0,
        review_extra=0,
        problems=[],
    )
    taken: set[int] = set()
    for key in case.action_items:
        index = _bullet_for(key["match"], parsed.action_items, taken)
        if index is None:
            s["missing_from_notes"] += 1
            s["problems"].append(f"notes have no bullet for {key['match']}")
            continue
        kept, said = by_checked[index], by_raw.get(index)
        if (
            said is not None
            and said.spoken_owner
            and (key["owner"] is None or not name_in_transcript(said.spoken_owner, case.transcript))
        ):
            s["raw_invented_owners"] += 1
        if key["owner"] is None and kept.spoken_owner:
            s["invented_owners"] += 1
            s["problems"].append(f"owner {kept.spoken_owner!r} for unowned {key['match']}")
        elif (
            key["owner"] and kept.spoken_owner and not _same_person(key["owner"], kept.spoken_owner)
        ):
            s["wrong_owners"] += 1
            s["problems"].append(f"owner {kept.spoken_owner!r}, key {key['owner']!r}")
        if key["due_date"]:
            s["dates_stated"] += 1
            if kept.due_date == key["due_date"]:
                s["dates_right"] += 1
            else:
                s["problems"].append(f"date {kept.due_date}, key {key['due_date']} {key['match']}")
        elif kept.due_date:
            s["dates_invented"] += 1
            s["problems"].append(f"date {kept.due_date} for undated {key['match']}")
    for item in checked.action_items:
        if any(_same_person(item.spoken_owner, f) for f in case.forbidden_owners):
            s["invented_owners"] += 1
            s["problems"].append(f"forbidden owner {item.spoken_owner!r} on bullet {item.index}")
    wanted = set()
    for key in case.review:
        index = _bullet_for(key["match"], parsed.please_check, set())
        if index is not None:
            wanted.add(index)
            if any(
                r.index == index and _same_person(r.heard_as, key["heard_as"])
                for r in checked.review
            ):
                s["review_found"] += 1
    s["review_extra"] = sum(1 for r in checked.review if r.index not in wanted)
    return s


def _rate(done: int, total: int) -> float:
    return done / total if total else 1.0


def gate(scores: list[dict]) -> dict:
    total = {
        k: sum(s.get(k, 0) for s in scores) for k in scores[0] if k not in ("case", "problems")
    }
    checks = {
        "zero_invented_owners": total["invented_owners"] + total["wrong_owners"] == 0,
        "quotes_verified_95": _rate(total["quotes_verified"], total["quotes_given"]) >= QUOTES_MIN,
        "dates_right_90": _rate(total["dates_right"], total["dates_stated"]) >= DATES_MIN,
        "every_bullet_covered_95": _rate(total["covered"], total["bullets"]) >= COVERAGE_MIN,
    }
    return dict(
        passed=all(checks.values()),
        checks=checks,
        rates=dict(
            quotes_verified=_rate(total["quotes_verified"], total["quotes_given"]),
            dates_right=_rate(total["dates_right"], total["dates_stated"]),
            coverage=_rate(total["covered"], total["bullets"]),
        ),
        totals=total,
    )


class Counting:
    """Wraps an OpenAI client and totals the tokens of the calls made through it."""

    def __init__(self, client, tally: dict, key: str):
        self._client, self._tally, self._key = client, tally, key
        self.chat = self  # `client.chat.completions.create`
        self.completions = self

    def create(self, **kw):
        response = self._client.chat.completions.create(**kw)
        usage = getattr(response, "usage", None)
        self._tally[self._key + "_input"] += getattr(usage, "prompt_tokens", 0) or 0
        self._tally[self._key + "_output"] += getattr(usage, "completion_tokens", 0) or 0
        return response


TEMPLATE = Template(
    name="General",
    instructions=(
        "Write a summary, key points, decisions, action items with owners, and open questions."
    ),
)


def run_cases(
    cases, client, *, notes_model: str, extract_model: str, out_dir: pathlib.Path
) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    tally = dict(notes_input=0, notes_output=0, extract_input=0, extract_output=0)
    scores, results = [], []
    for case in cases:
        notes_path = out_dir / f"{case.name}.notes.md"
        if notes_path.exists():
            text = notes_path.read_text()
        else:
            os.environ["NOTES_MODEL"] = notes_model
            notes_module.get_settings.cache_clear()
            text = notes_module.generate_notes(
                case.transcript,
                TEMPLATE,
                case.vocabulary,
                client=Counting(client, tally, "notes"),
            )
            notes_path.write_text(text)
        parsed = parse_notes(text)
        record = dict(case=case.name, extraction_failed=False)
        try:
            call = extract(
                parsed,
                case.transcript,
                case.meeting_date,
                case.timezone,
                client=Counting(client, tally, "extract"),
                model=extract_model,
            )
        except ExtractionFailed as exc:
            record.update(extraction_failed=True, error=type(exc).__name__)
            results.append(record)
            scores.append(
                dict(
                    case=case.name,
                    bullets=len(parsed.action_items),
                    covered=0,
                    quotes_given=0,
                    quotes_verified=0,
                    invented_owners=0,
                    raw_invented_owners=0,
                    wrong_owners=0,
                    dates_stated=sum(1 for k in case.action_items if k["due_date"]),
                    dates_right=0,
                    dates_invented=0,
                    missing_from_notes=0,
                    review_expected=len(case.review),
                    review_found=0,
                    review_extra=0,
                    problems=["the extraction call failed"],
                )
            )
            continue
        checked = check_extraction(call.result, parsed, case.transcript, case.meeting_date)
        score = score_case(case, parsed, call.result, checked)
        scores.append(score)
        record.update(score=score, raw=call.result.model_dump())
        results.append(record)
        (out_dir / f"{case.name}.result.json").write_text(json.dumps(record, indent=2))
    summary = dict(
        notes_model=notes_model,
        extract_model=extract_model,
        gate=gate(scores),
        cases=results,
        tokens=tally,
    )
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    return summary


def main() -> None:
    from openai import OpenAI

    parser = argparse.ArgumentParser()
    parser.add_argument("out_dir")
    parser.add_argument("--only", nargs="*")
    args = parser.parse_args()
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"], max_retries=0, timeout=300)
    summary = run_cases(
        load_cases(args.only),
        client,
        notes_model=os.environ.get("NOTES_MODEL", "gpt-6-luna"),
        extract_model=os.environ.get("EXTRACT_MODEL", "gpt-6-luna"),
        out_dir=pathlib.Path(args.out_dir),
    )
    print(json.dumps({k: v for k, v in summary.items() if k != "cases"}, indent=2))
    for case in summary["cases"]:
        for problem in case.get("score", {}).get("problems", []):
            print(f"  {case['case']}: {problem}")


if __name__ == "__main__":
    main()
