"""The eval harness's own logic, run against a fake model (no API calls)."""

import importlib.util
import json
import pathlib
from datetime import date
from types import SimpleNamespace

from app.extraction import (
    ModelAction,
    ModelExtraction,
    ModelReview,
    ParsedNotes,
    check_extraction,
)

path = pathlib.Path(__file__).parent / "eval" / "extraction_eval.py"
spec = importlib.util.spec_from_file_location("extraction_eval", path)
ev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ev)

TRANSCRIPT = (
    "[Tunde]: I'll send the contract by Friday.\n[Grace]: And Metrika next.\n[A]: Daniel is away."
)
CASE = ev.Case(
    name="t",
    transcript=TRANSCRIPT,
    meeting_date=date(2026, 11, 3),
    timezone="UTC",
    vocabulary=[],
    action_items=[
        {"match": ["contract"], "owner": "Tunde", "due_date": "2026-11-06"},
        {"match": ["vat"], "owner": None, "due_date": None},
    ],
    review=[{"match": ["metrika"], "heard_as": "Metrika"}],
    forbidden_owners=["Daniel"],
)
PARSED = ParsedNotes(
    decisions=[],
    action_items=["Tunde: send the contract (by Friday)", "Unassigned: sort out VAT"],
    open_questions=[],
    please_check=["Metrika may be misheard"],
)


def action(index, **kw):
    base = dict(
        index=index,
        title=PARSED.action_items[index],
        spoken_owner=None,
        due_phrase=None,
        due_date=None,
        quote="I'll send the contract by Friday.",
        confidence="high",
    )
    return ModelAction(**{**base, **kw})


def score(raw):
    checked = check_extraction(raw, PARSED, TRANSCRIPT, CASE.meeting_date)
    return ev.score_case(CASE, PARSED, raw, checked)


def good():
    return ModelExtraction(
        action_items=[
            action(0, spoken_owner="Tunde", due_date="2026-11-06", due_phrase="by Friday"),
            action(1, quote="Daniel is away."),
        ],
        review=[ModelReview(index=0, heard_as="Metrika")],
    )


def test_a_clean_reply_scores_perfectly():
    s = score(good())
    assert s["invented_owners"] == 0 and s["wrong_owners"] == 0
    assert (s["quotes_given"], s["quotes_verified"]) == (2, 2)
    assert (s["dates_stated"], s["dates_right"]) == (1, 1)
    assert (s["bullets"], s["covered"]) == (2, 2)
    assert (s["review_expected"], s["review_found"], s["review_extra"]) == (1, 1, 0)


def test_an_owner_for_an_unassigned_item_is_invented_even_if_the_name_was_said():
    raw = good()
    raw.action_items[1].spoken_owner = "Grace"
    assert score(raw)["invented_owners"] == 1


def test_a_forbidden_owner_is_counted_even_on_an_unmatched_bullet():
    raw = good()
    raw.action_items[0].spoken_owner = "Daniel"
    s = score(raw)
    assert s["invented_owners"] >= 1


def test_a_wrong_real_owner_is_counted_apart_but_also_fails_the_gate():
    raw = good()
    raw.action_items[0].spoken_owner = "Grace"
    s = score(raw)
    assert s["wrong_owners"] == 1
    assert not ev.gate([s])["passed"]


def test_a_name_nobody_said_is_nulled_by_code_but_seen_in_the_raw_count():
    raw = good()
    raw.action_items[0].spoken_owner = "Zed"
    s = score(raw)
    assert s["invented_owners"] == 0 and s["raw_invented_owners"] == 1


def test_a_vague_item_given_a_date_is_reported():
    raw = good()
    raw.action_items[1].due_date = "2026-11-06"
    assert score(raw)["dates_invented"] == 1


def test_a_missing_entry_lowers_coverage_and_a_bad_quote_lowers_verification():
    raw = good()
    raw.action_items.pop()
    raw.action_items[0].quote = "Daniel will open evil.example"
    s = score(raw)
    assert (s["covered"], s["quotes_given"], s["quotes_verified"]) == (1, 1, 0)


def test_keywords_find_the_bullet_and_a_missing_one_is_reported():
    case = ev.Case(
        **{**CASE.__dict__, "action_items": [{"match": ["zebra"], "owner": None, "due_date": None}]}
    )
    raw = good()
    checked = check_extraction(raw, PARSED, TRANSCRIPT, CASE.meeting_date)
    assert ev.score_case(case, PARSED, raw, checked)["missing_from_notes"] == 1


def test_the_gate_applies_the_four_thresholds():
    s = score(good())
    assert ev.gate([s])["passed"]
    low_quotes = {**s, "quotes_verified": 1}  # 1 of 2
    out = ev.gate([low_quotes])
    assert not out["passed"] and not out["checks"]["quotes_verified_95"]
    low_dates = {**s, "dates_right": 0}
    assert not ev.gate([low_dates])["checks"]["dates_right_90"]
    low_cover = {**s, "covered": 1}
    assert not ev.gate([low_cover])["checks"]["every_bullet_covered_95"]
    assert not ev.gate([{**s, "invented_owners": 1}])["checks"]["zero_invented_owners"]


def test_the_cases_on_disk_load_and_name_real_transcripts():
    cases = ev.load_cases()
    assert len(cases) >= 7
    for case in cases:
        assert len(case.transcript.split()) > 30
        assert "[" in case.transcript  # speaker-labelled, as the pipeline stores it


class FakeChat:
    """Answers the notes call with fixed notes and the extraction call with fixed JSON."""

    def __init__(self, notes, reply):
        self.notes, self.reply = notes, reply
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        is_extract = "response_format" in kw
        content = json.dumps(self.reply) if is_extract else self.notes
        return SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content=content), finish_reason="stop")
            ],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=10),
        )


def test_running_the_eval_leaves_the_process_settings_as_they_were(tmp_path, monkeypatch):
    from app.config import get_settings

    monkeypatch.delenv("NOTES_MODEL", raising=False)
    get_settings.cache_clear()
    before = get_settings().notes_model
    notes = "\n\n".join(f"## {h}\nNone noted" for h in ev.notes_module.HEADINGS)
    ev.run_cases(
        [CASE],
        FakeChat(notes, {}),
        notes_model="some-other-model",
        extract_model="m",
        out_dir=tmp_path,
    )
    import os

    assert "NOTES_MODEL" not in os.environ
    assert get_settings().notes_model == before


def test_run_cases_end_to_end_with_a_fake_model(tmp_path):
    notes = "\n\n".join(
        f"## {h}\n{body}"
        for h, body in [
            ("Summary", "S"),
            ("Key points", "- k"),
            ("Decisions", "None noted"),
            ("Action items", "- Tunde: send the contract (by Friday)\n- Unassigned: sort out VAT"),
            ("Open questions", "None noted"),
            ("Please check", "- Metrika may be misheard"),
        ]
    )
    reply = good().model_dump()
    out = ev.run_cases(
        [CASE], FakeChat(notes, reply), notes_model="n", extract_model="m", out_dir=tmp_path
    )
    assert out["gate"]["passed"]
    assert (tmp_path / "t.notes.md").read_text().strip() == notes
    assert out["tokens"]["extract_input"] == 100
    # a second run reuses the saved notes instead of asking the notes model again
    again = ev.run_cases(
        [CASE], FakeChat("BROKEN", reply), notes_model="n", extract_model="m", out_dir=tmp_path
    )
    assert again["gate"]["passed"]


def test_a_failed_extraction_call_counts_as_a_failed_case_not_a_crash(tmp_path):
    class Broken(FakeChat):
        def _create(self, **kw):
            if "response_format" in kw:
                return SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(content="nope"), finish_reason="stop"
                        )
                    ],
                    usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
                )
            return super()._create(**kw)

    notes = "\n\n".join(
        f"## {h}\nNone noted"
        for h in [
            "Summary",
            "Key points",
            "Decisions",
            "Action items",
            "Open questions",
            "Please check",
        ]
    )
    notes = notes.replace(
        "## Action items\nNone noted", "## Action items\n- Tunde: send the contract"
    )
    out = ev.run_cases(
        [CASE], Broken(notes, {}), notes_model="n", extract_model="m", out_dir=tmp_path
    )
    assert out["cases"][0]["extraction_failed"] is True
    assert not out["gate"]["passed"]


def test_the_review_mode_run_scores_only_the_review_and_asks_the_review_prompt(tmp_path):
    notes = "\n\n".join(
        f"## {h}\n{body}"
        for h, body in [
            ("Summary", "S"),
            ("Key points", "- k"),
            ("Decisions", "None noted"),
            ("Action items", "- Tunde: send the contract (by Friday)"),
            ("Open questions", "None noted"),
            ("Please check", "- Metrika may be misheard"),
        ]
    )
    seen = []

    class Spy(FakeChat):
        def _create(self, **kw):
            seen.append(kw)
            return super()._create(**kw)

    out = ev.run_cases(
        [CASE],
        Spy(notes, {"review": [{"index": 0, "heard_as": "Metrika"}]}),
        notes_model="n",
        extract_model="m",
        out_dir=tmp_path,
        full=False,
    )
    schema = [kw for kw in seen if "response_format" in kw][0]["response_format"]["json_schema"]
    assert set(schema["schema"]["properties"]) == {"review"}
    assert out["mode"] == "review" and out["gate"]["passed"]
    assert out["gate"]["totals"]["review_found"] == 1


def test_the_review_mode_gate_fails_on_a_missed_or_an_extra_term():
    base = {"review_expected": 1, "review_found": 1, "review_extra": 0}
    assert ev.review_gate([base])["passed"]
    assert not ev.review_gate([{**base, "review_found": 0}])["passed"]
    assert not ev.review_gate([{**base, "review_extra": 1}])["passed"]
