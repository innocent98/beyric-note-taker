"""The blocks of a draft: what the model's reply may contain and what is stored.

A draft is a list of blocks (`heading`, `paragraph`, `bullets`, `table`, and for a board `column`
with `card`s). Every piece of text carries the sources it was drafted from. The model writes the
words and names the ids it used; code checks all of it before anything is stored or shown, like
`ask.check_reply`:

* text is made plain (no HTML, no link, no Markdown marks) and cut to a bound;
* a source the model was not sent is dropped (`allowed_sources`);
* sizes are bounded (blocks, bullets, columns, rows, sources);
* a reply that is not the shape asked for raises `BlocksInvalid`: it fails the draft cleanly and is
  never half shown. Content problems (oversize, unknown source, empty text) are repaired by cutting
  or dropping; structural ones (a table row that does not match its columns, an unknown block
  type, nothing left) are refused.

Every renderer escapes for itself. This module renders Markdown and plain text (`to_markdown`,
`to_text`); the screen, Word, PDF, CSV and Excel are S2's. A cell that starts with `=`, `+`, `-`,
`@`, a tab or a carriage return is neutralised by the CSV and Excel exporters, not here.

`allowed_sources` is a set of keys: `(meeting_id, line)` for a transcript line, `(meeting_id,
None)` for a meeting, and `(kind, meeting_id, ref)` for a decision or a task (a decision id and a
line number would otherwise collide). `line_key`, `meeting_key` and `ref_key` build them.
"""

from collections.abc import Iterator
from typing import Annotated, Literal

from pydantic import BaseModel, Field, TypeAdapter
from pydantic import ValidationError as _ValidationError

from app.config import get_settings

HEADING_CHARS = 200
PARAGRAPH_CHARS = 2000
ITEM_CHARS = 500
CELL_CHARS = 300
COLUMN_HEADING_CHARS = 80
CARD_CHARS = 300
OWNER_CHARS = 80
TITLE_CHARS = 100
QUOTE_CHARS = 80
MAX_BULLETS = 40
MAX_COLUMNS = 12
MAX_ROWS = 100
MAX_CARDS = 30
MAX_SOURCES = 6

SOURCE_KINDS = ("line", "meeting", "decision", "task")
CARD_KINDS = ("decision", "action", "question", "note")


class BlocksInvalid(Exception):
    """The reply or the edit is not blocks of the shape asked for. `code` is a short word, never
    anything the model wrote. `detail` is a short sentence about the SHAPE only ("block 7: table 2
    row 3 has 5 cells for 4 columns"): built from positions and counts, never from the text of a
    meeting, a brief or a reply, so it is safe to store and to hand back to the model."""

    def __init__(self, code: str, detail: str | None = None):
        super().__init__(code)
        self.code = code
        self.detail = detail


class Source(BaseModel):
    """Where a piece of text was drafted from. `quote` is the first words of the line, so a line
    that moved can be found again (`ask._quote_row`); `ref` is a decision or task id. `hidden` is
    never stored and never written by the model: `recipes.visibility.redact` puts it in the place
    of a source the reader of a shared draft may not open (nothing else is set on it)."""

    kind: Literal["line", "meeting", "decision", "task", "hidden"]
    meeting_id: int | None = None
    line: int | None = None
    quote: str | None = None
    ref: int | None = None


class Item(BaseModel):
    text: str
    sources: list[Source] = Field(default_factory=list)


Cell = Item


class Heading(BaseModel):
    type: Literal["heading"] = "heading"
    level: int = Field(ge=1, le=3)
    text: str


class Paragraph(BaseModel):
    type: Literal["paragraph"] = "paragraph"
    text: str
    sources: list[Source] = Field(default_factory=list)


class Bullets(BaseModel):
    type: Literal["bullets"] = "bullets"
    items: list[Item]


class Table(BaseModel):
    type: Literal["table"] = "table"
    columns: list[str]
    rows: list[list[Cell]]


class Card(BaseModel):
    text: str
    kind: Literal["decision", "action", "question", "note"] = "note"
    owner: str | None = None
    sources: list[Source] = Field(default_factory=list)


class Column(BaseModel):
    type: Literal["column"] = "column"
    title: str
    cards: list[Card]


Block = Annotated[Heading | Paragraph | Bullets | Table | Column, Field(discriminator="type")]
Blocks = list[Block]
_ADAPTER = TypeAdapter(list[Block])


def line_key(meeting_id: int, line: int) -> tuple:
    return (meeting_id, line)


def meeting_key(meeting_id: int) -> tuple:
    return (meeting_id, None)


def ref_key(kind: str, meeting_id: int, ref: int) -> tuple:
    return (kind, meeting_id, ref)


def plain(text: object, limit: int) -> str:
    """The model's words as one plain line: no Markdown, no HTML, no link, cut at `limit`; "" for
    anything that is not text. The rule of `ask.plain`, which this calls so there is one."""
    from app.ask import plain as ask_plain  # ask imports a lot: keep this module cheap to import

    return ask_plain(text, limit)


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _source(raw: object, allowed: set[tuple]) -> Source | None:
    if not isinstance(raw, dict):
        return None
    kind, meeting_id = raw.get("kind"), _int(raw.get("meeting_id"))
    if kind not in SOURCE_KINDS or meeting_id is None:
        return None
    line, ref = _int(raw.get("line")), _int(raw.get("ref"))
    if kind == "line":
        if line is None or (meeting_id, line) not in allowed:
            return None
        ref = None
    elif kind == "meeting":
        if (meeting_id, None) not in allowed:
            return None
        line = ref = None
    else:
        if ref is None or (kind, meeting_id, ref) not in allowed:
            return None
        line = None
    quote = plain(raw.get("quote"), QUOTE_CHARS) or None if kind == "line" else None
    return Source(kind=kind, meeting_id=meeting_id, line=line, quote=quote, ref=ref)


def _sources(raw: object, allowed: set[tuple]) -> list[Source]:
    if not isinstance(raw, list):
        return []
    out: list[Source] = []
    for item in raw:
        source = _source(item, allowed)
        if source is not None and all(
            (source.kind, source.meeting_id, source.line, source.ref)
            != (s.kind, s.meeting_id, s.line, s.ref)
            for s in out
        ):
            out.append(source)
        if len(out) == MAX_SOURCES:
            break
    return out


def _items(
    raw: object, limit: int, allowed: set[tuple], fixes: list[str] | None = None
) -> list[Item]:
    """Cell-like text with sources: cut, cleaned; empty ones are kept by the caller if it must. When
    repairing (`fixes` is a list), a bare string or number stands for `{"text": it}`."""
    if not isinstance(raw, dict):
        if fixes is not None and isinstance(raw, str | int | float) and not isinstance(raw, bool):
            fixes.append("a cell or item that was plain text was read as text")
            return [Item(text=plain(str(raw), limit), sources=[])]
        raise BlocksInvalid("item", "an item or cell is not an object with text")
    return [Item(text=plain(raw.get("text"), limit), sources=_sources(raw.get("sources"), allowed))]


def _block(raw: object, allowed: set[tuple], fixes: list[str] | None = None):
    """One cleaned block, or None when nothing of it is left. Raises for a wrong shape, except when
    repairing (`fixes` is a list): then what can be read safely is kept, what cannot is dropped, and
    each repair is noted in `fixes` (shape only, never text)."""
    if not isinstance(raw, dict):
        if fixes is not None:
            fixes.append("a block that was not an object was dropped")
            return None
        raise BlocksInvalid("block", "a block is not an object")
    kind = raw.get("type")
    if kind == "heading":
        level = raw.get("level")
        level = 2 if _int(level) is None else min(3, max(1, level))
        text = plain(raw.get("text"), HEADING_CHARS)
        return Heading(level=level, text=text) if text else None
    if kind == "paragraph":
        text = plain(raw.get("text"), PARAGRAPH_CHARS)
        return Paragraph(text=text, sources=_sources(raw.get("sources"), allowed)) if text else None
    if kind == "bullets":
        if not isinstance(raw.get("items"), list):
            if fixes is not None:
                fixes.append("a list without items was dropped")
                return None
            raise BlocksInvalid("bullets", "a list has no items array")
        items = [_items(i, ITEM_CHARS, allowed, fixes)[0] for i in raw["items"]]
        items = [i for i in items if i.text][:MAX_BULLETS]
        return Bullets(items=items) if items else None
    if kind == "table":
        return _table(raw, allowed, fixes)
    if kind == "column":
        return _column(raw, allowed, fixes)
    if fixes is not None:
        fixes.append("a block of an unknown type was dropped")
        return None
    raise BlocksInvalid("block_type", "a block has a type that is not one of the five")


def _table(raw: dict, allowed: set[tuple], fixes: list[str] | None = None) -> Table | None:
    columns, rows = raw.get("columns"), raw.get("rows")
    if not isinstance(columns, list) or not columns or not isinstance(rows, list):
        if fixes is not None:
            fixes.append("a table without columns or rows was dropped")
            return None
        raise BlocksInvalid("table", "a table has no columns array or no rows array")
    width = min(len(columns), MAX_COLUMNS)
    headings = [plain(c, COLUMN_HEADING_CHARS) for c in columns[:width]]
    cells: list[list[Cell]] = []
    for number, row in enumerate(rows[:MAX_ROWS], start=1):
        # A row as long as the columns the model named, even the ones cut above. When writing a
        # draft a ragged row is repaired (short: the missing cells are visible `[need: <column>]`
        # blanks at the end; long: the extra cells are dropped) and the repair is noted; when
        # checking a person's edit it is refused rather than shown under the wrong heading.
        if not isinstance(row, list):
            if fixes is not None:
                fixes.append(f"table row {number} was not a list and was dropped")
                continue
            raise BlocksInvalid("table_row", f"table row {number} is not a list of cells")
        if len(row) != len(columns):
            if fixes is None:
                raise BlocksInvalid(
                    "table_row",
                    f"table row {number} has {len(row)} cells for {len(columns)} columns",
                )
            fixes.append(f"table row {number} had {len(row)} cells for {len(columns)} columns")
        row_cells = [_items(cell, CELL_CHARS, allowed, fixes)[0] for cell in row[:width]]
        for missing in headings[len(row_cells) :]:
            row_cells.append(Cell(text=f"[need: {missing or 'value'}]", sources=[]))
        cells.append(row_cells)
    return Table(columns=headings, rows=cells)


def _column(raw: dict, allowed: set[tuple], fixes: list[str] | None = None) -> Column | None:
    cards_raw = raw.get("cards")
    if not isinstance(cards_raw, list):
        if fixes is not None:
            fixes.append("a column without cards was dropped")
            return None
        raise BlocksInvalid("column", "a column has no cards array")
    title = plain(raw.get("title"), TITLE_CHARS)
    cards: list[Card] = []
    for card in cards_raw:
        if not isinstance(card, dict):
            if fixes is not None:
                fixes.append("a card that was not an object was dropped")
                continue
            raise BlocksInvalid("card", "a card is not an object")
        text = plain(card.get("text"), CARD_CHARS)
        if not text:
            continue
        kind = card.get("kind")
        owner = plain(card.get("owner"), OWNER_CHARS) or None
        cards.append(
            Card(
                text=text,
                kind=kind if kind in CARD_KINDS else "note",
                owner=owner,
                sources=_sources(card.get("sources"), allowed),
            )
        )
        if len(cards) == MAX_CARDS:
            break
    return Column(title=title or "Untitled", cards=cards) if cards or title else None


def parse_blocks(
    raw: object,
    *,
    allowed_sources: set[tuple],
    max_blocks: int | None = None,
    fixes: list[str] | None = None,
) -> list[Block]:
    """The blocks in `raw` (the model's reply, or a person's edit), cleaned and bounded. Raises
    `BlocksInvalid` when `raw` is not a list of blocks of the shapes above, a table row does not
    match its columns, or nothing is left.

    Pass `fixes=[]` for a model's reply to REPAIR what can be repaired safely instead of refusing
    the whole draft (a ragged table row, a plain-text cell, an unreadable block dropped); every
    repair is appended to `fixes` as a short sentence about the shape. A person's edit passes no
    `fixes` and is checked strictly. The `BlocksInvalid` that is raised names the block."""
    if not isinstance(raw, list):
        raise BlocksInvalid("not_a_list", "the blocks are not a list")
    limit = max_blocks if max_blocks is not None else get_settings().draft_max_blocks
    out = []
    for number, item in enumerate(raw, start=1):
        try:
            block = _block(item, allowed_sources, fixes)
        except BlocksInvalid as exc:
            if exc.detail and not exc.detail.startswith("block "):
                exc.detail = f"block {number}: {exc.detail}"
            raise
        if block is not None:
            out.append(block)
        if len(out) == limit:
            break
    if not out:
        raise BlocksInvalid("empty", "no block was left after cleaning")
    return out


def dump_blocks(blocks: list[Block]) -> list[dict]:
    """The JSON-able form kept in `documents.blocks`."""
    return [block.model_dump(mode="json") for block in blocks]


def load_blocks(stored: object) -> list[Block]:
    """Blocks as `dump_blocks` made them, read back without cleaning. `BlocksInvalid` when what is
    stored does not fit the schema."""
    try:
        return _ADAPTER.validate_python(stored)
    except _ValidationError as exc:
        raise BlocksInvalid("stored") from exc


def texts(blocks: list[Block]) -> Iterator[tuple[int, str]]:
    """Every piece of text in the draft with the index of its block: headings, paragraphs, bullet
    items, column headings and cells, a board's titles and cards. What the number audit reads."""
    for index, block in enumerate(blocks):
        if isinstance(block, Heading | Paragraph):
            yield index, block.text
        elif isinstance(block, Bullets):
            for item in block.items:
                yield index, item.text
        elif isinstance(block, Table):
            for heading in block.columns:
                yield index, heading
            for row in block.rows:
                for cell in row:
                    yield index, cell.text
        else:
            yield index, block.title
            for card in block.cards:
                yield index, card.text


# ---- Markdown and plain text ----

_MD_ESCAPE = str.maketrans(
    {c: "\\" + c for c in "\\*_`[]<>|~"},
)


def _md(text: str) -> str:
    """`text` with the characters Markdown would act on written out, so a viewer shows what the
    person sees. Also a leading list or heading marker."""
    text = text.translate(_MD_ESCAPE)
    if text[:1] in ("#", "+", "-"):
        text = "\\" + text
    head = text.lstrip("0123456789")
    if head != text and head[:1] in (".", ")"):
        text = text[: len(text) - len(head)] + "\\" + head
    return text


def _owner(card: Card, escape) -> str:
    return f"{escape(card.text)} ({escape(card.owner)})" if card.owner else escape(card.text)


def to_markdown(blocks: list[Block]) -> str:
    parts: list[str] = []
    for block in blocks:
        if isinstance(block, Heading):
            parts.append("#" * block.level + " " + _md(block.text))
        elif isinstance(block, Paragraph):
            parts.append(_md(block.text))
        elif isinstance(block, Bullets):
            parts.append("\n".join(f"- {_md(item.text)}" for item in block.items))
        elif isinstance(block, Table):
            lines = ["| " + " | ".join(_md(c) for c in block.columns) + " |"]
            lines.append("| " + " | ".join("---" for _ in block.columns) + " |")
            for row in block.rows:
                lines.append("| " + " | ".join(_md(cell.text) for cell in row) + " |")
            parts.append("\n".join(lines))
        else:
            lines = [f"## {_md(block.title)}", ""]
            lines += [f"- {_owner(card, _md)}" for card in block.cards]
            parts.append("\n".join(lines))
    return "\n\n".join(parts)


def to_text(blocks: list[Block]) -> str:
    """Plain text for Copy and the mail app: bullets as "- ", tables as lines joined by " | ", a
    board's columns as a title with its cards under it. Nothing is escaped: it is plain."""

    def same(text: str) -> str:
        return text

    parts: list[str] = []
    for block in blocks:
        if isinstance(block, Heading | Paragraph):
            parts.append(block.text)
        elif isinstance(block, Bullets):
            parts.append("\n".join(f"- {item.text}" for item in block.items))
        elif isinstance(block, Table):
            lines = [" | ".join(block.columns)]
            lines += [" | ".join(cell.text for cell in row) for row in block.rows]
            parts.append("\n".join(lines))
        else:
            parts.append("\n".join([block.title] + [f"- {_owner(c, same)}" for c in block.cards]))
    return "\n\n".join(parts)
