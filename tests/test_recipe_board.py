"""The board of a meeting (`app/recipes/board.py`): shaping what the model returns, the seven edit
operations, and the render model for PDF and Word. Pure functions: no database."""

from datetime import UTC, datetime

import pytest
from pydantic import TypeAdapter, ValidationError

from app.recipes import blocks as b
from app.recipes import board

NOW = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
OP = TypeAdapter(board.Op)


def card(text, kind="note", owner=None, sources=None):
    return b.Card(text=text, kind=kind, owner=owner, sources=sources or [])


def sample():
    return [
        b.Column(
            title="Pricing",
            cards=[card("Fee is 50,000 naira", "decision"), card("Who signs?", "question")],
        ),
        b.Column(title="Next steps", cards=[card("Send the plan", "action", "Ada")]),
        b.Column(title="Later", cards=[]),
    ]


def op(**fields):
    return OP.validate_python(fields)


def titles(blocks):
    return [c.title for c in blocks]


def texts(blocks):
    return [[c.text for c in col.cards] for col in blocks]


# ---- shaping the model's reply ----


def test_shape_keeps_only_columns_and_drops_a_heading_the_model_added():
    shaped = board.shape([b.Heading(level=1, text="Board"), *sample()])
    assert titles(shaped) == ["Pricing", "Next steps", "Later"]


def test_shape_with_no_card_anywhere_is_refused():
    with pytest.raises(b.BlocksInvalid) as refused:
        board.shape([b.Column(title="Empty", cards=[]), b.Paragraph(text="hello")])
    assert refused.value.code == "board"


def test_shape_cuts_to_twelve_columns():
    many = [b.Column(title=f"T{i}", cards=[card("x")]) for i in range(20)]
    assert len(board.shape(many)) == b.MAX_COLUMNS


# ---- move_card ----


def test_move_card_to_another_column_at_an_index():
    moved = board.apply(sample(), op(op="move_card", card=0, to_column=1, index=0))
    assert texts(moved) == [["Who signs?"], ["Fee is 50,000 naira", "Send the plan"], []]


def test_move_card_inside_its_own_column_reorders_it():
    moved = board.apply(sample(), op(op="move_card", card=0, to_column=0, index=1))
    assert texts(moved)[0] == ["Who signs?", "Fee is 50,000 naira"]


def test_move_card_to_the_end_of_an_empty_column():
    moved = board.apply(sample(), op(op="move_card", card=2, to_column=2, index=0))
    assert texts(moved)[2] == ["Send the plan"] and texts(moved)[1] == []


def test_card_numbers_run_across_the_columns_in_order():
    # 0, 1 are in Pricing; 2 is the first card of Next steps
    moved = board.apply(sample(), op(op="move_card", card=2, to_column=0, index=0))
    assert texts(moved)[0][0] == "Send the plan"


def test_move_keeps_the_card_with_its_sources_and_owner():
    source = b.Source(kind="line", meeting_id=1, line=4, quote="Send the plan")
    start = [b.Column(title="A", cards=[card("x", "action", "Ada", [source])]), sample()[2]]
    moved = board.apply(start, op(op="move_card", card=0, to_column=1, index=0))
    kept = moved[1].cards[0]
    assert (kept.owner, kept.kind, kept.sources) == ("Ada", "action", [source])


@pytest.mark.parametrize(
    "fields",
    [
        {"card": 9, "to_column": 0, "index": 0},  # no such card
        {"card": 0, "to_column": 5, "index": 0},  # no such column
        {"card": 0, "to_column": 1, "index": 7},  # past the end of that column
        {"card": 0, "to_column": 0, "index": 2},  # past the end once the card has left
    ],
)
def test_an_index_that_no_longer_matches_is_stale(fields):
    with pytest.raises(board.StaleBoard):
        board.apply(sample(), op(op="move_card", **fields))


# ---- columns ----


def test_rename_column_is_plain_and_bounded():
    renamed = board.apply(sample(), op(op="rename_column", column=1, title="<b>Do</b> [it](x)"))
    assert renamed[1].title == "Do it"
    long = board.apply(sample(), op(op="rename_column", column=0, title="w" * 300))
    assert len(long[0].title) <= b.TITLE_CHARS


def test_rename_to_nothing_is_invalid():
    with pytest.raises(board.BoardInvalid):
        board.apply(sample(), op(op="rename_column", column=0, title="<i></i>  "))


def test_add_column_goes_last_or_at_the_index():
    added = board.apply(sample(), op(op="add_column", title="Risks"))
    assert titles(added)[-1] == "Risks" and added[-1].cards == []
    first = board.apply(sample(), op(op="add_column", title="Risks", index=0))
    assert titles(first)[0] == "Risks"


def test_a_board_holds_at_most_twelve_columns():
    full = [b.Column(title=f"T{i}", cards=[]) for i in range(b.MAX_COLUMNS)]
    with pytest.raises(board.BoardInvalid) as refused:
        board.apply(full, op(op="add_column", title="One more"))
    assert refused.value.code == "board_full"


def test_delete_column_removes_it_and_its_cards_but_never_the_last():
    gone = board.apply(sample(), op(op="delete_column", column=0))
    assert titles(gone) == ["Next steps", "Later"]
    with pytest.raises(board.BoardInvalid):
        board.apply([sample()[0]], op(op="delete_column", column=0))


# ---- cards ----


def test_add_card_defaults_to_a_note_at_the_end_with_no_sources():
    added = board.apply(sample(), op(op="add_card", column=2, text="Revisit in spring"))
    new = added[2].cards[0]
    assert (new.text, new.kind, new.owner, new.sources) == ("Revisit in spring", "note", None, [])


def test_add_card_with_kind_owner_and_index():
    added = board.apply(
        sample(),
        op(op="add_card", column=0, text="Call Daniel", kind="action", owner="Ada", index=0),
    )
    first = added[0].cards[0]
    assert (first.text, first.kind, first.owner) == ("Call Daniel", "action", "Ada")


def test_card_text_is_plain_and_bounded_and_must_not_be_empty():
    added = board.apply(
        sample(), op(op="add_card", column=0, text="<script>x</script> [a](http://e)")
    )
    assert "<" not in added[0].cards[-1].text and "http" not in added[0].cards[-1].text
    cut = board.apply(sample(), op(op="add_card", column=0, text="w" * 900))
    assert len(cut[0].cards[-1].text) <= b.CARD_CHARS
    with pytest.raises(board.BoardInvalid):
        board.apply(sample(), op(op="add_card", column=0, text="  <b></b> "))


def test_a_column_holds_at_most_thirty_cards():
    full = [b.Column(title="A", cards=[card(str(i)) for i in range(b.MAX_CARDS)])]
    with pytest.raises(board.BoardInvalid) as refused:
        board.apply(full, op(op="add_card", column=0, text="one more"))
    assert refused.value.code == "column_full"
    with pytest.raises(board.BoardInvalid) as refused:  # moving a card INTO a full column
        board.apply(
            [*full, b.Column(title="B", cards=[card("y")])],
            op(op="move_card", card=30, to_column=0, index=0),
        )
    assert refused.value.code == "column_full"
    # moving inside a full column is fine: it is not an addition
    inside = board.apply(full, op(op="move_card", card=0, to_column=0, index=29))
    assert inside[0].cards[-1].text == "0"


def test_edit_card_changes_text_owner_and_kind_and_keeps_the_sources():
    source = b.Source(kind="meeting", meeting_id=1)
    start = [b.Column(title="A", cards=[card("old", "note", None, [source])])]
    edited = board.apply(start, op(op="edit_card", card=0, text="new", owner="Bola", kind="action"))
    got = edited[0].cards[0]
    assert (got.text, got.owner, got.kind, got.sources) == ("new", "Bola", "action", [source])


def test_edit_card_leaves_what_is_not_sent_and_clears_an_owner_sent_empty():
    start = [b.Column(title="A", cards=[card("old", "decision", "Ada")])]
    same = board.apply(start, op(op="edit_card", card=0, text="newer"))
    assert (same[0].cards[0].kind, same[0].cards[0].owner) == ("decision", "Ada")
    cleared = board.apply(start, op(op="edit_card", card=0, owner=""))
    assert cleared[0].cards[0].owner is None and cleared[0].cards[0].text == "old"


def test_edit_card_with_nothing_to_change_is_refused():
    with pytest.raises(ValidationError):
        op(op="edit_card", card=0)


def test_delete_card_removes_exactly_that_card():
    gone = board.apply(sample(), op(op="delete_card", card=1))
    assert texts(gone) == [["Fee is 50,000 naira"], ["Send the plan"], []]


def test_recolour_sets_the_kind_of_a_card_and_colour_follows_the_kind():
    recoloured = board.apply(sample(), op(op="recolour", card=1, kind="decision"))
    assert recoloured[0].cards[1].kind == "decision"
    assert recoloured[0].cards[0].kind == "decision"  # untouched


def test_apply_does_not_change_the_blocks_it_was_given():
    start = sample()
    board.apply(start, op(op="delete_card", card=0))
    assert texts(start)[0] == ["Fee is 50,000 naira", "Who signs?"]


# ---- the operations are strict ----


@pytest.mark.parametrize(
    "fields",
    [
        {"op": "move_card", "card": -1, "to_column": 0, "index": 0},
        {"op": "move_card", "card": 0, "to_column": 0, "index": 0, "extra": 1},
        {"op": "move_card", "card": "0", "to_column": 0, "index": 0},
        {"op": "move_card", "card": True, "to_column": 0, "index": 0},
        {"op": "move_card", "card": 0, "to_column": 100000, "index": 0},
        {"op": "rename_column", "column": 0, "title": "x" * 5000},
        {"op": "add_card", "column": 0, "text": "x", "kind": "red"},
        {"op": "add_card", "column": 0, "text": "x", "owner": "o" * 500},
        {"op": "teleport", "card": 0},
        {"card": 0},
    ],
)
def test_a_malformed_op_is_refused_before_it_touches_anything(fields):
    with pytest.raises(ValidationError):
        OP.validate_python(fields)


# ---- PDF and Word go through the existing render model ----


def test_to_notes_document_has_a_section_per_column_with_cards_as_bullets_and_owner():
    doc = board.to_notes_document("Onboarding board", sample(), when=NOW)
    assert doc.title == "Onboarding board"
    assert [s.heading for s in doc.sections] == ["Pricing", "Next steps", "Later"]
    first = doc.sections[0].blocks[0]
    assert first.kind == "list" and not first.ordered and len(first.items) == 2
    run_text = [["".join(r.text for r in p.runs) for p in item] for item in first.items]
    assert run_text == [["Decision: Fee is 50,000 naira"], ["Question: Who signs?"]]
    action = doc.sections[1].blocks[0].items[0][0]
    assert "".join(r.text for r in action.runs) == "Action: Send the plan (Ada)"


def test_a_note_card_has_no_label_and_an_empty_column_says_so():
    doc = board.to_notes_document(
        "B", [b.Column(title="A", cards=[card("hello")]), sample()[2]], when=NOW
    )
    assert "".join(r.text for r in doc.sections[0].blocks[0].items[0][0].runs) == "hello"
    empty = doc.sections[1].blocks[0]
    assert empty.kind == "paragraph" and "".join(r.text for r in empty.runs) == "No cards."
    assert "2 columns" in doc.meta and "1 card" in doc.meta


@pytest.mark.parametrize("fmt", ["pdf", "docx"])
def test_a_board_renders_to_a_file_through_the_existing_renderer(fmt):
    from app import export

    data = export.build_file(board.to_notes_document("Board", sample(), when=NOW), fmt)
    assert data[:4] == (b"%PDF" if fmt == "pdf" else b"PK\x03\x04")


def test_a_board_docx_has_the_cards_as_text():
    import io

    from docx import Document as Docx

    from app import export

    data = export.build_file(board.to_notes_document("Board", sample(), when=NOW), "docx")
    text = "\n".join(p.text for p in Docx(io.BytesIO(data)).paragraphs)
    assert "Pricing" in text and "Decision: Fee is 50,000 naira" in text
    assert "Action: Send the plan (Ada)" in text
