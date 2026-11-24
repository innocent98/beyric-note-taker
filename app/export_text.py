"""The Markdown and plain-text files of a meeting's notes.

Unlike PDF and Word (`app/export.py`) these are made in the request itself: they are text, so there
is no renderer to run, no memory to give back and nothing to wait for, which is why they never
answer `export_busy`. Both come from the same `NotesDocument` the other formats use, so the four
files carry the same title, meta line, sections and footer.

Markdown keeps the notes as they were written (they are stored as Markdown) and puts the title,
meta line and, when asked, the transcript around them. Text is the same content with the marks
taken off: headings underlined, lists as dashes and numbers, bold and italic dropped.
"""

import re

from app.notes_document import (
    Block,
    CodeBlock,
    Heading,
    ListBlock,
    NotesDocument,
    Paragraph,
    Quote,
    Run,
    Turn,
)

# format -> (Content-Disposition media type, file extension)
TEXT_FORMATS = {
    "md": ("text/markdown; charset=utf-8", "md"),
    "txt": ("text/plain; charset=utf-8", "txt"),
}

# A line that Markdown would read as a heading, quote, list or rule if it began this way.
_LINE_START = re.compile(r"^(\s*)([#>+\-*=_`~]|\d+[.)])")
# Marks that must be escaped inside a name or title so they stay literal.
_INLINE_MARKS = re.compile(r"([\\`*_{}\[\]<>#|])")


def _escape_inline(text: str) -> str:
    return _INLINE_MARKS.sub(r"\\\1", text)


def _escape_line_start(line: str) -> str:
    """A transcript line that happens to begin like Markdown stays a plain line of text."""
    return _LINE_START.sub(lambda m: f"{m.group(1)}\\{m.group(2)}", line, count=1)


def _md_turn(turn: Turn) -> str:
    lines = turn.text.split("\n")
    if turn.speaker is not None:
        # After `**Name:** ` nothing can start a block, so the first line is left as spoken.
        head = f"**{_escape_inline(turn.speaker)}:** {lines[0]}"
    else:
        head = _escape_line_start(lines[0])
    rest = [_escape_line_start(line) for line in lines[1:]]
    return "  \n".join([head, *rest])  # two spaces: a line break inside the paragraph


def build_markdown(doc: NotesDocument, notes: str) -> bytes:
    """`notes` is the stored Markdown, verbatim; the rest comes from `doc`."""
    parts = [f"# {_escape_inline(doc.title)}", doc.meta]
    body = (notes or "").strip()
    if body:
        parts.append(body)
    if doc.transcript is not None:
        parts.append("## Transcript")
        parts.extend(_md_turn(turn) for turn in doc.transcript)
    parts.append(f"*{doc.footer}*")
    return ("\n\n".join(p for p in parts if p) + "\n").encode("utf-8")


# ---- plain text ----


def _runs(runs: list[Run]) -> str:
    return "".join(run.text for run in runs).strip()


def _indent(text: str, prefix: str, first: str | None = None) -> str:
    lines = text.split("\n")
    out = [(first if first is not None else prefix) + lines[0]]
    out.extend((prefix + line) if line else "" for line in lines[1:])
    return "\n".join(out)


def _blocks(blocks: list[Block]) -> str:
    return "\n\n".join(text for text in (_block(b) for b in blocks) if text)


def _block(block: Block) -> str:
    if isinstance(block, Paragraph):
        return _runs(block.runs)
    if isinstance(block, Heading):
        return _runs(block.runs)
    if isinstance(block, CodeBlock):
        return _indent(block.text, "    ")
    if isinstance(block, Quote):
        return _indent(_blocks(block.blocks), "> ")
    if isinstance(block, ListBlock):
        items = []
        for number, item in enumerate(block.items, start=block.start):
            marker = f"{number}. " if block.ordered else "- "
            body = _list_item(item)
            items.append(_indent(body, " " * len(marker), first=marker))
        return "\n".join(items)
    return ""


def _list_item(blocks: list[Block]) -> str:
    """An item's text, with a nested list on the next line (no blank line) and any further
    paragraph after a blank one."""
    joined = ""
    for b in blocks:
        text = _block(b)
        if not text:
            continue
        if joined:
            joined += ("\n" if isinstance(b, ListBlock) else "\n\n") + text
        else:
            joined = text
    return joined


def _underlined(text: str, mark: str) -> str:
    return f"{text}\n{mark * max(len(text), 3)}"


def build_text(doc: NotesDocument) -> bytes:
    parts = [_underlined(doc.title, "="), doc.meta]
    for section in doc.sections:
        if section.heading:
            parts.append(_underlined(section.heading, "-"))
        body = _blocks(section.blocks)
        if body:
            parts.append(body)
    if doc.transcript is not None:
        parts.append(_underlined("Transcript", "-"))
        for turn in doc.transcript:
            parts.append(f"{turn.speaker}: {turn.text}" if turn.speaker is not None else turn.text)
    parts.append(doc.footer)
    return ("\n\n".join(p for p in parts if p) + "\n").encode("utf-8")
