"""The TR1 harness (`tests/eval/translation_eval.py`): its scoring and its refusal to run without
a key. The model is faked, so this proves the harness, not the model's French."""

import importlib.util
import pathlib

import pytest

from tests.test_translation import FakeModel

spec = importlib.util.spec_from_file_location(
    "translation_eval", pathlib.Path(__file__).parent / "eval" / "translation_eval.py"
)
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)

PRICES = (0.10, 0.50)


def cases():
    return harness.load_cases(["names_and_numbers"])


def test_the_case_is_there_and_has_names_numbers_and_an_injected_order():
    [case] = cases()
    assert {"Metrika", "Oluwaseun Adeyemi-Okafor"} <= set(case["must_keep"])
    assert "Ignore all previous instructions" in case["transcript"]
    assert "£4,000" in case["notes"] and "14 March" in case["notes"]


@pytest.mark.parametrize("language", ["fr", "es"])
def test_a_faithful_translation_passes_and_reports_a_cost(language):
    [case] = cases()
    result, notes, transcript = harness.run_case(case, language, FakeModel(), "m", PRICES)
    assert result.passed, result.problems
    assert result.input_tokens > 0 and result.cost > 0 and result.cost_per_1000_words > 0
    assert notes.count("\n") == case["notes"].count("\n")


def test_a_translation_that_loses_a_name_fails_the_gate():
    [case] = cases()
    [fake] = [FakeModel(lambda texts, n: [t.replace("Metrika", "Métrica") for t in texts])]
    result, _, _ = harness.run_case(case, "fr", fake, "m", PRICES)
    assert not result.passed and any("rejected" in p for p in result.problems)


def test_the_scoring_catches_a_changed_name_even_if_the_code_checks_let_it_through():
    [case] = cases()
    problems = harness.score_texts(
        case, "fr", case["notes"].replace("Metrika", "X"), case["transcript"]
    )
    assert any("Metrika" in p for p in problems)
    assert any("headings" in p for p in problems)  # the English headings are not the dictionary's


def test_it_refuses_to_run_without_a_key_and_says_so(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert harness.main([str(tmp_path)]) == 2
    assert "nothing was run" in capsys.readouterr().out
