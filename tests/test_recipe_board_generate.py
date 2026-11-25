# ruff: noqa: F811
"""Generating a board (`generate.generate_blocks` for the output type `board`): columns of cards,
sources checked like every other draft, blanks and the number audit applied. The recipe is the real
`board` starter of slice S3 (`app/recipes/library.py`)."""

import pytest

from app.recipes import audit, library
from app.recipes import blocks as blocks_mod
from app.recipes import generate as g
from tests.test_recipe_generate import (  # noqa: F401  (fixtures used by name)
    FakeModel,
    context_of,
    line,
    meeting,
    owner,
    para,
    reply,
    system_and_user,
    template,
)

# The real starter of slice S3 (`app/recipes/library.py`): its instruction text is what the model
# is given for a board.
STARTER = library.starter_by_key("board")
BOARD = g.spec_of(STARTER)


def card(text, kind="note", owner_name=None, sources=()):
    return {"text": text, "kind": kind, "owner": owner_name, "sources": list(sources)}


def column(title, cards):
    return {
        "type": "column",
        "title": title,
        "cards": cards,
        "text": None,
        "level": None,
        "sources": None,
        "items": None,
        "columns": None,
        "rows": None,
    }


def make(session, owner, meeting, blocks):
    context = context_of(session, owner, [meeting])
    return g.generate_blocks(BOARD, context, {}, client=FakeModel(reply(blocks, "Onboarding")))


def test_the_model_is_told_to_make_a_board_of_columns_and_cards(session, owner, meeting):
    model = FakeModel(reply([column("Pricing", [card("Fee", "decision")])]))
    g.generate_blocks(BOARD, context_of(session, owner, [meeting]), {}, client=model)
    system, user = system_and_user(model.calls[0])
    assert "Output type: board." in user and "only `column` blocks" in user
    assert STARTER.instruction in user  # the real starter's own words, outside any fence
    assert "Group the decisions, action items, open questions and key points" in user
    assert "column (a board column with cards)" in system


def test_the_real_board_starter_is_a_board_that_reads_one_meeting(session, owner, meeting):
    assert (STARTER.output_type, STARTER.scope, STARTER.uses_brief) == ("board", "one", False)
    assert BOARD.output_type == "board" and BOARD.key == "board"
    # what its instruction asks for is what the engine can make: three to six themes of cards with
    # a kind, an owner and a source line
    result = make(
        session,
        owner,
        meeting,
        [
            column(
                "Pricing", [card("Fee is 50,000 naira", "decision", None, [line(meeting.id, 2)])]
            ),
            column("Plan", [card("Send the plan", "action", "Ada", [line(meeting.id, 3)])]),
            column("Open", [card("When do we start?", "question")]),
        ],
    )
    assert [c.title for c in result.blocks] == ["Pricing", "Plan", "Open"]
    assert [c.cards[0].kind for c in result.blocks] == ["decision", "action", "question"]
    assert result.blocks[1].cards[0].owner == "Ada" and result.blocks[0].cards[0].sources


def test_a_board_comes_back_as_columns_with_cards_owner_and_kind(session, owner, meeting):
    result = make(
        session,
        owner,
        meeting,
        [
            column(
                "Pricing",
                [card("Onboarding is 50,000 naira", "decision", None, [line(meeting.id, 2)])],
            ),
            column("Next steps", [card("Send the plan", "action", "Ada", [line(meeting.id, 3)])]),
        ],
    )
    assert [type(x).__name__ for x in result.blocks] == ["Column", "Column"]
    first = result.blocks[0].cards[0]
    assert (first.kind, first.owner) == ("decision", None)
    assert first.sources[0].line == 2
    assert first.sources[0].quote.startswith("We want onboarding")  # the real line, not the model's
    assert result.blocks[1].cards[0].owner == "Ada" and result.title == "Onboarding"


def test_a_heading_or_paragraph_the_model_adds_is_dropped(session, owner, meeting):
    result = make(
        session,
        owner,
        meeting,
        [para("Here is your board"), column("Pricing", [card("Fee", "decision")])],
    )
    assert all(isinstance(x, blocks_mod.Column) for x in result.blocks) and len(result.blocks) == 1


def test_a_source_the_model_was_not_shown_is_dropped_from_a_card(session, owner, meeting):
    result = make(
        session,
        owner,
        meeting,
        [column("A", [card("x", "note", None, [line(meeting.id, 99), line(meeting.id, 1)])])],
    )
    assert [s.line for s in result.blocks[0].cards[0].sources] == [1]


def test_a_reply_with_no_card_at_all_fails_as_structure(session, owner, meeting):
    with pytest.raises(g.GenerationFailed) as failed:
        make(session, owner, meeting, [column("Empty", [])])
    assert failed.value.code == g.STRUCTURE


def test_a_reply_with_no_column_fails_as_structure(session, owner, meeting):
    with pytest.raises(g.GenerationFailed) as failed:
        make(session, owner, meeting, [para("just words")])
    assert failed.value.code == g.STRUCTURE


def test_a_document_recipe_keeps_its_headings_and_paragraphs(session, owner, meeting):
    doc = g.RecipeSpec(name="Memo", instruction="x", output_type="document")
    out = g.generate_blocks(
        doc, context_of(session, owner, [meeting]), {}, client=FakeModel(reply([para("Hello")]))
    )
    assert [type(x).__name__ for x in out.blocks] == ["Paragraph"]


def test_blanks_and_the_number_audit_apply_to_cards(session, owner, meeting):
    context = context_of(session, owner, [meeting])
    result = make(
        session,
        owner,
        meeting,
        [
            column(
                "Pricing",
                [
                    card("Setup costs ₦450,000", "decision"),
                    card("Start on [need: start date]", "action", "Ada"),
                    card("Onboarding is 50,000 naira", "decision"),
                ],
            )
        ],
    )
    flags = audit.audit(result.blocks, context.corpus)
    assert [f.text for f in flags if f.kind == "figure"] == ["₦450,000"]
    assert [x.what for x in audit.blanks(result.blocks)] == ["start date"]
