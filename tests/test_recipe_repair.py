# ruff: noqa: F811
"""The first real-model run (2026-11-25): the proposal failed as `structure`, with no reason given.

What a draft's structure check does now: repair what can be repaired safely instead of refusing the
whole draft, ask the model ONCE more (with the checks' reason) when it still cannot be used, count
that second call in the ledger, and say why in `failure_detail`, in words about the shape only.
Plus the audit no longer flagging the labels the prompt itself prints (`Meeting 4`).

The "suspects" the lead listed are each tried with a realistic proposal reply: only a ragged table
row and a cell that is not an object were ever refused by the old strict check; the rest never were.
"""

import copy
import json

import pytest

from app.models import Document
from app.recipes import audit
from app.recipes import blocks as b
from app.recipes import generate as g
from app.recipes import service as svc
from tests.test_api import (  # noqa: F401  (fixtures used by name)
    anon,
    anon_client,
    api,
    app,
    fake_bot,
    fake_openai,
    stranger,
)
from tests.test_recipe_generate import (  # noqa: F401  (fixtures used by name)
    FakeModel,
    context_of,
    line,
    meeting,
    para,
    reply,
)
from tests.test_recipe_jobs import T0, ask, ready_meeting, runs
from tests.test_web import add_meeting, owner, template  # noqa: F401

SECRET = "SECRET-WORDS-OF-THE-MEETING"
PROPOSAL = g.RecipeSpec(
    name="Proposal", instruction="Write a proposal with a timeline and an investment table.",
    output_type="document",
)  # fmt: skip


def cell(text, sources=()):
    return {"text": text, "sources": list(sources)}


def table(columns, rows):
    return {
        "type": "table", "level": None, "text": None, "sources": None, "items": None,
        "columns": columns, "rows": rows, "title": None, "cards": None,
    }  # fmt: skip


def heading(text):
    return {
        "type": "heading", "level": 2, "text": text, "sources": None, "items": None,
        "columns": None, "rows": None, "title": None, "cards": None,
    }  # fmt: skip


def proposal_blocks(meeting_id, *, investment_rows=None, timeline_rows=None):
    """The shape the proposal starter asks for: headings, paragraphs, a Timeline table and an
    Investment table, every figure from the meeting."""
    quote = [line(meeting_id, 2)]
    timeline = timeline_rows or [
        [cell("Setup"), cell("2026-12-01"), cell("Ada")],
        [cell("Training"), cell("Week 2"), cell("Ada")],
    ]
    investment = investment_rows or [
        [cell("Onboarding package", quote), cell("50,000 naira"), cell("Includes setup")],
        [cell("Driver app"), cell("included"), cell("Up to 40 drivers")],
    ]
    return [
        heading("Background"),
        para("Daniel runs a logistics company.", quote),
        heading("Timeline"),
        table(["Phase", "When", "Owner"], timeline),
        heading("Investment"),
        table(["Item", "Price", "Notes"], investment),
        heading("Next steps"),
        para("Ada sends the plan by Friday.", quote),
    ]


def parse(raw, **kw):
    return b.parse_blocks(raw, allowed_sources={(1, None), (1, 2)}, **kw)


# ---- which suspect was it ----


def test_a_proposal_with_well_formed_tables_was_never_refused():
    assert parse(proposal_blocks(1))  # the control: the old strict check accepts it


@pytest.mark.parametrize(
    "what, rows",
    [
        (
            "a row with fewer cells than columns",
            [[cell("Onboarding package"), cell("50,000 naira")]],
        ),
        (
            "a row with more cells than columns",
            [[cell("A"), cell("B"), cell("C"), cell("an extra cell")]],
        ),
    ],
)
def test_a_ragged_table_row_was_refused_whole_and_is_now_repaired(what, rows):
    reply_blocks = proposal_blocks(1, investment_rows=rows)
    with pytest.raises(b.BlocksInvalid) as strict:  # what the first real run hit
        parse(reply_blocks)
    assert strict.value.code == "table_row"
    assert "block 6" in strict.value.detail and "cells for 3 columns" in strict.value.detail
    fixes: list[str] = []
    repaired = parse(reply_blocks, fixes=fixes)
    investment = repaired[5]
    assert isinstance(investment, b.Table) and all(len(r) == 3 for r in investment.rows)
    assert fixes and "table row 1" in fixes[0]


def test_a_cell_that_is_plain_text_instead_of_an_object_was_refused_and_is_now_read_as_text():
    rows = [["Onboarding package", "50,000 naira", "Includes setup"]]
    with pytest.raises(b.BlocksInvalid) as strict:
        parse(proposal_blocks(1, investment_rows=rows))
    assert strict.value.code == "item"
    fixes: list[str] = []
    repaired = parse(proposal_blocks(1, investment_rows=rows), fixes=fixes)
    texts = [c.text for c in repaired[5].rows[0]]
    assert texts == ["Onboarding package", "50,000 naira", "Includes setup"]


def test_the_other_suspects_were_never_the_cause():
    base = proposal_blocks(1)
    many = [heading(f"Section {i}") for i in range(80)] + base  # more than draft_max_blocks
    assert len(parse(many, max_blocks=60)) == 60  # cut, not refused
    huge = proposal_blocks(1, investment_rows=[[cell("x" * 5000)] * 3])
    assert len(parse(huge)[5].rows[0][0].text) <= b.CELL_CHARS  # an over-long cell is cut
    unknown = copy.deepcopy(base)
    unknown[1]["sources"] = [line(99, 7)] * 50  # 50 source ids the model was never shown
    assert parse(unknown)[1].sources == []  # dropped, the draft stands
    empty_table = proposal_blocks(1, investment_rows=[])
    assert parse(empty_table)  # a table with no rows is fine
    wide = [cell(str(i)) for i in range(20)]
    assert parse([table([str(i) for i in range(20)], [wide])])  # 20 columns: cut to 12, not refused


# ---- repair, only what is safe ----


def test_a_short_row_gets_visible_blanks_never_a_guess_and_the_audit_finds_them():
    fixes: list[str] = []
    blocks = parse(proposal_blocks(1, investment_rows=[[cell("Onboarding package")]]), fixes=fixes)
    row = blocks[5].rows[0]
    assert [c.text for c in row] == ["Onboarding package", "[need: Price]", "[need: Notes]"]
    assert [x.what for x in audit.blanks(blocks)] == ["Price", "Notes"]
    assert fixes == ["table row 1 had 1 cells for 3 columns"]
    assert "Onboarding" not in " ".join(fixes)  # the notes are about the shape, never the text


def test_what_cannot_be_read_is_dropped_when_repairing_and_refused_when_checking_an_edit():
    raw = [{"type": "chart"}, "just a string", para("Kept.")]
    fixes: list[str] = []
    assert [x.text for x in parse(raw, fixes=fixes)] == ["Kept."]
    assert len(fixes) == 2
    with pytest.raises(b.BlocksInvalid):  # a person's edit is still checked strictly
        parse(raw)
    with pytest.raises(b.BlocksInvalid) as nothing:
        parse([{"type": "chart"}], fixes=[])
    assert nothing.value.code == "empty" and nothing.value.detail


def test_a_persons_edit_still_refuses_a_ragged_table():
    with pytest.raises(b.BlocksInvalid) as err:
        parse(proposal_blocks(1, investment_rows=[[cell("one cell only")]]))
    assert err.value.code == "table_row"


# ---- generation: repair, one retry with the reason, a detail when it still fails ----


def test_a_ragged_reply_becomes_a_draft_without_asking_again(session, owner, meeting):
    context = context_of(session, owner, [meeting])
    model = FakeModel(reply(proposal_blocks(meeting.id, investment_rows=[[cell("Setup fee")]])))
    result = g.generate_blocks(PROPOSAL, context, None, client=model)
    assert len(model.calls) == 1 and len(result.attempts) == 1
    assert result.fixes and "table row 1" in result.fixes[0]
    assert "[need: Price]" in b.to_text(result.blocks)


def test_a_reply_that_cannot_be_used_is_asked_for_once_more_with_the_reason(
    session, owner, meeting
):
    context = context_of(session, owner, [meeting])
    good = reply(proposal_blocks(meeting.id))
    model = FakeModel(lambda kwargs, n: reply([{"type": "chart"}]) if n == 1 else good)
    result = g.generate_blocks(PROPOSAL, context, None, client=model)
    assert len(model.calls) == 2 and len(result.attempts) == 2
    first_user = model.calls[0]["messages"][1]["content"]
    second_user = model.calls[1]["messages"][1]["content"]
    assert "refused by the checks" not in first_user
    assert second_user.startswith(first_user)
    assert "refused by the checks: no block was left after cleaning" in second_user
    assert result.attempts[0].refused == "no block was left after cleaning"
    assert result.attempts[1].refused is None
    assert (result.input_tokens, result.output_tokens) == (3000, 600)  # both calls


def test_a_second_refusal_fails_the_draft_with_the_reason_and_both_calls(session, owner, meeting):
    context = context_of(session, owner, [meeting])
    model = FakeModel(reply([{"type": "chart"}]))
    with pytest.raises(g.GenerationFailed) as failed:
        g.generate_blocks(PROPOSAL, context, None, client=model)
    exc = failed.value
    assert exc.code == "structure" and len(model.calls) == 2 and len(exc.attempts) == 2
    assert exc.detail == "no block was left after cleaning"
    assert (exc.input_tokens, exc.output_tokens) == (3000, 600)


def test_only_a_structure_failure_is_asked_again(session, owner, meeting):
    context = context_of(session, owner, [meeting])
    for out in ("not json", ("{}", "length"), {"title": "x"}):
        model = FakeModel(out)
        with pytest.raises(g.GenerationFailed) as failed:
            g.generate_blocks(PROPOSAL, context, None, client=model)
        assert failed.value.code == "model_reply" and len(model.calls) == 1


def test_the_reason_never_holds_a_word_of_the_meeting_or_the_reply(session, owner, meeting):
    context = context_of(session, owner, [meeting])
    leak = reply([{"type": "table", "columns": [], "rows": [[cell(SECRET)]], "text": SECRET}])
    model = FakeModel(leak)
    with pytest.raises(g.GenerationFailed) as failed:
        g.generate_blocks(PROPOSAL, context, None, client=model)
    assert SECRET not in (failed.value.detail or "")
    assert SECRET not in model.calls[1]["messages"][1]["content"]


# ---- the worker: the second call is another attempt in the ledger and the cap ----


def test_the_second_call_is_another_ledger_row_and_counts_toward_the_day(session, owner, template):
    meeting = ready_meeting(session, template)
    doc = ask(session, owner, [meeting.id])
    good = reply(proposal_blocks(meeting.id))
    svc.process_next(
        session, T0, client=FakeModel(lambda kw, n: reply([{"type": "chart"}]) if n == 1 else good)
    )
    session.expire_all()
    stored = session.get(Document, doc.id)
    assert (stored.status, stored.failure_code, stored.failure_detail) == ("ready", None, None)
    first, second = runs(session)
    assert (first.status, second.status) == ("failed", "ok")
    assert first.error == "structure: no block was left after cleaning"
    assert second.error is None and second.user_id == owner.id and second.kind == "recipe"
    assert (first.input_tokens, second.input_tokens) == (1500, 1500)
    assert svc.daily_count(session, owner.id, T0) == 2  # two attempts, two places in the day


def test_a_failed_draft_records_the_reason_on_the_document_and_in_the_ledger(
    session, owner, template
):
    meeting = ready_meeting(session, template)
    doc = ask(session, owner, [meeting.id])
    svc.process_next(session, T0, client=FakeModel(reply([{"type": "chart"}])))
    session.expire_all()
    stored = session.get(Document, doc.id)
    assert (stored.status, stored.failure_code) == ("failed", "structure")
    assert stored.failure_detail == "no block was left after cleaning"
    first, second = runs(session)
    assert (first.status, second.status) == ("failed", "failed")
    assert second.error == "structure: no block was left after cleaning"
    assert svc.daily_count(session, owner.id, T0) == 2
    # the sentence a person sees is unchanged
    sentence = "The writing service sent back something unusable. Try again."
    assert svc.failure_message("structure") == sentence


def test_a_ragged_reply_costs_one_call_and_one_place(session, owner, template):
    meeting = ready_meeting(session, template)
    doc = ask(session, owner, [meeting.id])
    reply_ = reply(proposal_blocks(meeting.id, investment_rows=[[cell("Setup fee")]]))
    svc.process_next(session, T0, client=FakeModel(reply_))
    session.expire_all()
    assert session.get(Document, doc.id).status == "ready"
    assert len(runs(session)) == 1 and svc.daily_count(session, owner.id, T0) == 1


def test_the_detail_reaches_the_api_for_the_creator(api, session, template, monkeypatch):
    from tests.test_api_recipes import STARTER, make, meeting_of, url, work

    monkeypatch.setattr(svc, "starter_for", {"follow_up_email": STARTER}.get)
    meeting = meeting_of(session, template)
    doc_id = make(api, meeting).json()["id"]
    work(session, [{"type": "chart"}])
    got = api.get(url(doc_id)).json()
    assert (got["status"], got["failure_code"]) == ("failed", "structure")
    assert got["failure_detail"] == "no block was left after cleaning"
    assert got["failure_message"] == "The writing service sent back something unusable. Try again."
    assert json.dumps(got).count("chart") == 0


# ---- the audit no longer flags the labels the prompt prints ----


def test_the_status_report_shape_is_not_flagged_for_its_meeting_labels(session, owner, template):
    from app.recipes.blocks import Bullets, Item

    meetings = [
        add_meeting(
            session, template, status="ready", step="done", title=f"Sprint {10 + n} review",
            notes=f"## Summary\nSprint {10 + n} review notes.",
            transcript="",
            speaker_transcript="",
        )
        for n in (1, 2, 3)
    ]  # fmt: skip
    context = g.build_context(session, owner, meetings, whole_transcript=False, brief=None)
    labels = [f"Meeting {m.id} — {m.title}: progress was steady." for m in meetings]
    blocks = [Bullets(items=[Item(text=t, sources=[]) for t in labels])]
    assert audit.audit(blocks, context.corpus) == []
    # other labels the prompt prints, and a figure that really is invented, still behave
    more = [
        Bullets(items=[Item(text="Meeting id: 9 [D3] [T7] and a 450,000 naira fee", sources=[])])
    ]
    assert [f.text for f in audit.audit(more, context.corpus)] == ["450,000 naira"]
    weeks = [Bullets(items=[Item(text="The next meeting is in 6 weeks", sources=[])])]
    assert [f.text for f in audit.audit(weeks, context.corpus)] == ["6 weeks"]


def test_a_meeting_number_next_to_a_unit_is_still_a_figure():
    assert audit.normalise_figures("Meeting 4 weeks later")  # "4 weeks" is read
    assert not audit.normalise_figures("Meeting 4 and meeting id: 5")
    assert not audit.normalise_figures("see [L12], [D3] and [T9]")
