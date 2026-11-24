"""The guard logic of `scripts/recipes_live_real.py` (the real-model mode of the live pass): the
key, the spend stop, the recording client and what gets printed. No key, no network, no database."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

_spec = importlib.util.spec_from_file_location(
    "recipes_live_real", Path(__file__).resolve().parent.parent / "scripts" / "recipes_live_real.py"
)
real = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(real)

KEY = "sk-test-0123456789abcdefSECRETSECRET"
PRICES = {"gpt-6-luna": (0.10, 0.50)}


def fake_openai(tokens=(1000, 200), content='{"title": "T", "blocks": []}', fail=None):
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        if fail:
            raise fail
        usage = SimpleNamespace(prompt_tokens=tokens[0], completion_tokens=tokens[1])
        message = SimpleNamespace(content=content)
        choice = SimpleNamespace(message=message, finish_reason="stop")
        return SimpleNamespace(choices=[choice], usage=usage)

    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))), calls


# ---- the key ----


def test_a_missing_key_refuses_with_a_plain_message_and_asks_nothing_else():
    for env in ({}, {"OPENAI_API_KEY": ""}, {"OPENAI_API_KEY": "   "}):
        with pytest.raises(real.LiveRealRefused) as refused:
            real.require_key(env)
        text = str(refused.value)
        assert "OPENAI_API_KEY is not set" in text and "no call was made" in text


def test_the_key_comes_from_the_environment_only():
    assert real.require_key({"OPENAI_API_KEY": f" {KEY} "}) == KEY


def test_the_key_is_hidden_in_anything_echoed():
    shown = real.mask(f"401: Incorrect API key provided: {KEY}. Check {KEY[:20]}...", KEY)
    assert KEY not in shown and "SECRET" not in shown and "sk-...(hidden)" in shown
    # even a different key-shaped string is hidden
    assert "abcdefghijklmnop" not in real.mask("sk-abcdefghijklmnop", None)


def test_the_key_never_appears_in_the_summary_the_results_or_a_recorded_error():
    guard = real.SpendGuard(model="gpt-6-luna", prices=PRICES)
    client, _ = fake_openai(fail=RuntimeError(f"bad key {KEY}"))
    recorder = real.RecordingClient(client, guard, KEY)
    with pytest.raises(RuntimeError):
        recorder.chat.completions.create(model="gpt-6-luna", messages=[])
    assert KEY not in json.dumps(recorder.calls) and "sk-...(hidden)" in json.dumps(recorder.calls)
    row = {
        "key": "proposal", "status": "failed", "input_tokens": 0, "output_tokens": 0,
        "seconds": 1.0,
        "cost_usd_estimated": None, "blanks": [], "flags": [], "sources_dropped": None,
        "errors": recorder.calls[0]["error"],
    }  # fmt: skip
    assert KEY not in real.summary_table([row], guard, None)
    assert KEY not in real.mask(json.dumps({"rows": [row]}), KEY)


# ---- the spend guard ----


def test_the_estimate_uses_real_token_counts_and_the_projects_prices():
    guard = real.SpendGuard(model="gpt-6-luna", prices=real.load_prices())
    assert guard.priced
    spent = guard.add(2_000_000, 1_000_000)  # 2M in at 0.10 and 1M out at 0.50
    assert spent == pytest.approx(0.2 + 0.5) and guard.cost == pytest.approx(0.7)


def test_the_spend_guard_stops_calls_once_the_cap_is_reached():
    guard = real.SpendGuard(model="gpt-6-luna", max_usd=0.0005, prices=PRICES)
    client, calls = fake_openai(tokens=(3000, 400))  # 0.0003 + 0.0002 = 0.0005 a call
    recorder = real.RecordingClient(client, guard, KEY)
    assert guard.reason_to_stop() is None
    recorder.chat.completions.create(model="gpt-6-luna", messages=[])
    assert "reached the cap of $0.00" in guard.reason_to_stop()
    with pytest.raises(real.SpendCapReached):
        recorder.chat.completions.create(model="gpt-6-luna", messages=[])
    assert len(calls) == 1  # the second call never reached the client
    assert recorder.calls[0]["input_tokens"] == 3000 and recorder.calls[0]["output_tokens"] == 400


def test_a_model_without_a_price_is_not_guessed_and_stops_after_a_fixed_number_of_drafts():
    guard = real.SpendGuard(model="gpt-9-unknown", prices=PRICES, max_drafts_unpriced=2)
    assert not guard.priced and guard.price_of(1000, 1000) is None
    assert "NO PRICE" in guard.note()
    guard.add(1000, 1000)
    assert guard.reason_to_stop() is None
    guard.add(1000, 1000)
    stop = guard.reason_to_stop()
    assert "no price" in stop and "stopped after 2 drafts" in stop
    assert guard.cost == 0.0  # nothing was invented


def test_the_cap_and_draft_limit_come_from_the_environment_and_refuse_nonsense():
    assert real.env_float("RECIPE_LIVE_MAX_USD", 3.0, {}) == 3.0
    assert real.env_float("RECIPE_LIVE_MAX_USD", 3.0, {"RECIPE_LIVE_MAX_USD": "1.5"}) == 1.5
    for bad in ("abc", "0", "-2"):
        with pytest.raises(real.LiveRealRefused):
            real.env_float("RECIPE_LIVE_MAX_USD", 3.0, {"RECIPE_LIVE_MAX_USD": bad})


# ---- what is recorded ----


def test_each_call_records_real_usage_time_finish_reason_and_the_raw_reply():
    guard = real.SpendGuard(model="gpt-6-luna", prices=PRICES)
    client, _ = fake_openai(tokens=(1234, 56), content="RAW")
    recorder = real.RecordingClient(client, guard, KEY)
    recorder.chat.completions.create(model="gpt-6-luna", messages=[])
    call = recorder.calls[0]
    assert (call["input_tokens"], call["output_tokens"], call["raw"]) == (1234, 56, "RAW")
    assert call["finish_reason"] == "stop" and call["seconds"] >= 0


def test_sources_dropped_counts_what_the_model_sent_that_did_not_survive():
    src = {"kind": "line", "meeting_id": 1, "line": 2}
    raw = json.dumps(
        {"blocks": [{"type": "paragraph", "text": "x", "sources": [src, {**src, "line": 99}]}]}
    )
    stored = [{"type": "paragraph", "text": "x", "sources": [src]}]
    assert real.sources_dropped(raw, stored) == 1
    assert (
        real.sources_dropped(raw, [{"type": "paragraph", "text": "x", "sources": [src, src]}]) == 0
    )
    assert real.sources_dropped("not json", stored) is None
    nested = {"blocks": [{"type": "table", "rows": [[{"text": "a", "sources": [src]}]]}]}
    assert real.count_sources(nested["blocks"]) == 1


def test_the_preview_is_the_first_300_characters_on_one_line():
    text = "word " * 200
    shown = real.preview(text)
    assert len(shown) == 300 and "\n" not in real.preview("a\n\nb")


def test_the_summary_table_says_what_was_spent_and_when_it_stopped_early():
    guard = real.SpendGuard(model="gpt-6-luna", prices=PRICES)
    guard.add(10_000, 2_000)
    row = {
        "key": "proposal", "status": "ready", "input_tokens": 10_000, "output_tokens": 2_000,
        "seconds": 12.5, "cost_usd_estimated": 0.002, "blanks": ["a"], "flags": [],
        "sources_dropped": 0,
    }  # fmt: skip
    table = real.summary_table([row], guard, "cap reached")
    assert "proposal" in table and "0.00200" in table and "1 drafts" in table
    assert "STOPPED EARLY: cap reached" in table
