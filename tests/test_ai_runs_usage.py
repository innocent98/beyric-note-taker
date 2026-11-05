from types import SimpleNamespace

from app import ai_runs, notes
from app.models import AiRun


def test_nothing_is_recorded_when_no_call_was_made(session):
    assert ai_runs.record_usage(session, None, "notes", "m", []) is None
    assert session.query(AiRun).count() == 0


def test_the_calls_of_one_step_are_summed_into_one_row(session):
    run = ai_runs.record_usage(session, None, "label", "m", [(10, 2), (5, None), (None, 3)])
    assert (run.kind, run.input_tokens, run.output_tokens) == ("label", 15, 5)
    assert run.prompt_version == ai_runs.LABEL_PROMPT_VERSION


def test_a_count_no_reply_gave_stays_null(session):
    run = ai_runs.record_usage(session, None, "notes", "m", [(None, None)])
    assert (run.input_tokens, run.output_tokens) == (None, None)
    assert run.prompt_version == ai_runs.NOTES_PROMPT_VERSION


def reply(content, finish_reason="stop", usage=None):
    message = SimpleNamespace(content=content)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish_reason)], usage=usage
    )


def client_returning(response):
    return SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **_: response))
    )


def test_a_refused_reply_is_still_counted_because_it_was_paid_for():
    spent: list = []
    cut = reply("half", "length", SimpleNamespace(prompt_tokens=7, completion_tokens=9))
    try:
        notes._complete(client_returning(cut), "s", "u", what="notes", usage=spent)
    except notes.NotesFailed:
        pass
    assert spent == [(7, 9)]


def test_a_reply_without_usage_is_counted_as_unknown():
    spent: list = []
    assert notes._complete(client_returning(reply("ok")), "s", "u", what="x", usage=spent) == "ok"
    assert spent == [(None, None)]
