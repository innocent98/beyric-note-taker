"""The board of a meeting: a static, one-page, editable picture, never a live one (spec 5.3c, 5.4a).

A board is a draft whose output type is `board`. Its blocks are `Column`s (a theme, with cards) and
nothing else; a card is a `decision`, an `action`, a `question` or a `note`, with the owner when the
meeting names one and the sources it was drafted from. It is not a drawing canvas: no free shapes,
no arrows.

This module owns the three board-specific pieces; the model call, the queue and the audit are the
engine's:

* `shape(blocks)`: what the model returned, made a board (columns only, at most 12, at least one
  card somewhere, or the draft fails as `structure`). `generate.generate_blocks` calls it for the
  output type `board`.
* `apply(blocks, op)`: ONE edit by the creator, as a pure function. `Op` is the strict body of
  `PATCH /api/documents/{id}/board` (`app/api_recipe_board.py`). Text is made plain and bounded
  as at generation; a card keeps its sources when it moves or is edited and a new card has none.
  A card or column that is not where the person's screen thinks it is raises `StaleBoard`.
* `to_notes_document(title, blocks, when=...)`: the render model, so PDF and Word go through the
  existing renderer (`app/export.py`) like every other export: a column becomes a section and its
  cards a bullet list, each with its owner.

**Numbers.** `column` is the position of a column on the board. `card` is the position of a card
counting through the columns in order, from 0 (so card 2 is the first card of the second column when
the first holds two). `index` is a position inside the target column. Colour is a function of the
card's kind, so recolouring a card is giving it another kind.
"""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from app.notes_document import ListBlock, NotesDocument, Paragraph, Run, Section, _date
from app.recipes import blocks as b
from app.recipes.blocks import Block, BlocksInvalid, Card, Column

# What a request may carry before cleaning cuts it to the stored bounds (`blocks.CARD_CHARS`...).
_MAX_INPUT = 2000
_MAX_NUMBER = 1000  # a board holds at most 12 columns of 30 cards

KIND_LABEL = {"decision": "Decision", "action": "Action", "question": "Question", "note": ""}


class BoardInvalid(Exception):
    """The operation cannot be done to this board. `code` is a short word: `board_full`,
    `column_full`, `title_empty`, `card_empty`, `last_column`, `not_a_board`."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class StaleBoard(Exception):
    """A column or card the operation names is not where the person's screen thinks it is."""


# ---- the operations (the strict body of PATCH .../board) ----

Number = Annotated[int, Field(ge=0, le=_MAX_NUMBER)]
Kind = Literal["decision", "action", "question", "note"]


class _Op(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    # The `updated_at` of the draft as the person's screen last saw it. `apply` does not read it;
    # the route requires it (`api_recipe_board`) to refuse an edit made on an out-of-date screen.
    revision: str | None = Field(default=None, max_length=64)


class MoveCard(_Op):
    op: Literal["move_card"]
    card: Number
    to_column: Number
    index: Number


class RenameColumn(_Op):
    op: Literal["rename_column"]
    column: Number
    title: str = Field(max_length=_MAX_INPUT)


class AddColumn(_Op):
    op: Literal["add_column"]
    title: str = Field(max_length=_MAX_INPUT)
    index: Number | None = None


class DeleteColumn(_Op):
    op: Literal["delete_column"]
    column: Number


class AddCard(_Op):
    op: Literal["add_card"]
    column: Number
    text: str = Field(max_length=_MAX_INPUT)
    kind: Kind = "note"
    owner: str | None = Field(default=None, max_length=200)
    index: Number | None = None


class EditCard(_Op):
    op: Literal["edit_card"]
    card: Number
    text: str | None = Field(default=None, max_length=_MAX_INPUT)
    owner: str | None = Field(default=None, max_length=200)
    kind: Kind | None = None

    @model_validator(mode="after")
    def _something_to_change(self):
        if not self.model_fields_set & {"text", "owner", "kind"}:
            raise ValueError("edit_card changes the text, the owner or the kind")
        return self


class DeleteCard(_Op):
    op: Literal["delete_card"]
    card: Number


class Recolour(_Op):
    op: Literal["recolour"]
    card: Number
    kind: Kind


Op = Annotated[
    MoveCard | RenameColumn | AddColumn | DeleteColumn | AddCard | EditCard | DeleteCard | Recolour,
    Field(discriminator="op"),
]
OP_ADAPTER = TypeAdapter(Op)


# ---- shaping what the model returned ----


def shape(blocks: list[Block]) -> list[Block]:
    """The model's blocks as a board: the columns, at most 12. A reply with no card in any column
    is not a board (`BlocksInvalid("board")`, the draft fails as `structure`)."""
    columns = [block for block in blocks if isinstance(block, Column)][: b.MAX_COLUMNS]
    if not any(column.cards for column in columns):
        raise BlocksInvalid("board")
    return columns


# ---- one edit ----


def _columns(blocks: list[Block]) -> list[Column]:
    if not all(isinstance(block, Column) for block in blocks):
        raise BoardInvalid("not_a_board")
    return [block.model_copy(deep=True) for block in blocks]


def _locate(columns: list[Column], card: int) -> tuple[int, int]:
    """The column and the place in it of card number `card` (counted through the columns)."""
    seen = 0
    for c, column in enumerate(columns):
        if card < seen + len(column.cards):
            return c, card - seen
        seen += len(column.cards)
    raise StaleBoard


def _column(columns: list[Column], column: int) -> Column:
    if column >= len(columns):
        raise StaleBoard
    return columns[column]


def _title(text: str) -> str:
    title = b.plain(text, b.TITLE_CHARS)
    if not title:
        raise BoardInvalid("title_empty")
    return title


def _card_text(text: str) -> str:
    cleaned = b.plain(text, b.CARD_CHARS)
    if not cleaned:
        raise BoardInvalid("card_empty")
    return cleaned


def _owner(owner: str | None) -> str | None:
    return b.plain(owner, b.OWNER_CHARS) or None if owner else None


def _room(column: Column) -> None:
    if len(column.cards) >= b.MAX_CARDS:
        raise BoardInvalid("column_full")


def _place(items: list, index: int | None) -> int:
    """Where in `items` a new entry goes: the end when `index` is None; past the end is stale."""
    if index is None:
        return len(items)
    if index > len(items):
        raise StaleBoard
    return index


def apply(blocks: list[Block], op: Op) -> list[Block]:
    """The board after `op`. `blocks` is not changed. Raises `StaleBoard` for a position that no
    longer exists and `BoardInvalid` for anything else that cannot be done."""
    columns = _columns(blocks)
    if isinstance(op, MoveCard):
        c, i = _locate(columns, op.card)
        moving = columns[c].cards.pop(i)
        target = _column(columns, op.to_column)
        if op.to_column != c:
            _room(target)
        target.cards.insert(_place(target.cards, op.index), moving)
    elif isinstance(op, RenameColumn):
        _column(columns, op.column).title = _title(op.title)
    elif isinstance(op, AddColumn):
        if len(columns) >= b.MAX_COLUMNS:
            raise BoardInvalid("board_full")
        columns.insert(_place(columns, op.index), Column(title=_title(op.title), cards=[]))
    elif isinstance(op, DeleteColumn):
        _column(columns, op.column)
        if len(columns) == 1:
            raise BoardInvalid("last_column")
        del columns[op.column]
    elif isinstance(op, AddCard):
        target = _column(columns, op.column)
        _room(target)
        card = Card(text=_card_text(op.text), kind=op.kind, owner=_owner(op.owner), sources=[])
        target.cards.insert(_place(target.cards, op.index), card)
    elif isinstance(op, EditCard):
        c, i = _locate(columns, op.card)
        card = columns[c].cards[i]
        if "text" in op.model_fields_set and op.text is not None:
            card.text = _card_text(op.text)
        if "owner" in op.model_fields_set:
            card.owner = _owner(op.owner)
        if op.kind is not None:
            card.kind = op.kind
    elif isinstance(op, DeleteCard):
        c, i = _locate(columns, op.card)
        del columns[c].cards[i]
    else:  # Recolour
        c, i = _locate(columns, op.card)
        columns[c].cards[i].kind = op.kind
    return columns


# ---- PDF and Word ----


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" + ("" if count == 1 else "s")


def _card_runs(card: Card) -> list[Run]:
    runs: list[Run] = []
    if KIND_LABEL[card.kind]:
        runs.append(Run(text=f"{KIND_LABEL[card.kind]}: ", bold=True))
    runs.append(Run(text=card.text))
    if card.owner:
        runs.append(Run(text=f" ({card.owner})", italic=True))
    return runs


def to_notes_document(title: str, blocks: list[Block], *, when: datetime) -> NotesDocument:
    """The board as the render model of PDF and Word: a section for each column (the title is the
    heading) holding its cards as bullets, each with its kind and owner. Plain text only: each
    renderer escapes for itself."""
    columns = [block for block in blocks if isinstance(block, Column)]
    sections: list[Section] = []
    for column in columns:
        if column.cards:
            body = [
                ListBlock(
                    ordered=False, items=[[Paragraph(runs=_card_runs(c))] for c in column.cards]
                )
            ]
        else:
            body = [Paragraph(runs=[Run(text="No cards.", italic=True)])]
        sections.append(Section(heading=column.title, blocks=body))
    cards = sum(len(column.cards) for column in columns)
    meta = " · ".join((_date(when), _plural(len(columns), "column"), _plural(cards, "card")))
    return NotesDocument(
        title=title,
        meta=meta,
        sections=sections,
        footer=f"Generated by Beyric Note Taker, {_date(when)}",
    )
