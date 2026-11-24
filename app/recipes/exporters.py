"""Every export of a draft, built from its CURRENT blocks (the person's edits included, never the
text as first generated): Markdown, plain text, CSV, Excel, Word and PDF.

Markdown and text are `blocks.to_markdown` and `blocks.to_text`. Word and PDF go through
`export.build_file`, the isolated one-at-a-time child process of the notes export, from the render
model `notes_document.build_document_from_blocks`. CSV and Excel hold the draft's tables and
refuse a draft without one (409 `no_table`).

Spreadsheet formula injection: a cell whose text, after leading whitespace, starts with `=`, `+`,
`-` or `@`, or that starts with a tab or a carriage return, is written with a leading apostrophe,
so a program that opens the file reads text and never runs a formula. Excel cells are also written
as strings, so openpyxl never turns one into a formula either.
"""

import csv
import io
import re
from datetime import UTC, datetime
from typing import Literal

from app import export as renderer
from app import services
from app.models import Document
from app.notes_document import build_document_from_blocks, export_filename
from app.recipes import blocks as b

Format = Literal["md", "txt", "csv", "xlsx", "docx", "pdf"]
FORMATS = ("md", "txt", "csv", "xlsx", "docx", "pdf")

_TYPES = {
    "md": "text/markdown; charset=utf-8",
    "txt": "text/plain; charset=utf-8",
    "csv": "text/csv; charset=utf-8",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
_FORMULA_START = ("=", "+", "-", "@")
# What a sheet name may not contain, and its limit in Excel.
_SHEET_BAD = re.compile(r"[\[\]:*?/\\]")
_SHEET_CHARS = 31
# Control characters Excel refuses in a cell (tab, line feed and carriage return are fine).
_XML_BAD = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def neutralise(text: str) -> str:
    """`text` as a spreadsheet cell: a leading apostrophe when it could run as a formula."""
    if text[:1] in ("\t", "\r") or text.lstrip().startswith(_FORMULA_START):
        return "'" + text
    return text


def _tables(blocks: list[b.Block]) -> list[tuple[str | None, b.Table]]:
    """Each table with the heading above it (the nearest one), if any."""
    found: list[tuple[str | None, b.Table]] = []
    heading: str | None = None
    for block in blocks:
        if isinstance(block, b.Heading):
            heading = block.text
        elif isinstance(block, b.Table):
            found.append((heading, block))
    return found


def _no_table() -> services.Refused:
    return services.Refused(409, "no_table", "This draft has no table to export that way.")


def _grid(table: b.Table) -> list[list[str]]:
    return [[neutralise(c) for c in table.columns]] + [
        [neutralise(cell.text) for cell in row] for row in table.rows
    ]


def _csv(table: b.Table) -> bytes:
    out = io.StringIO(newline="")
    csv.writer(out, lineterminator="\r\n").writerows(_grid(table))
    return out.getvalue().encode("utf-8-sig")


def _sheet_name(heading: str | None, number: int, taken: set[str]) -> str:
    name = _SHEET_BAD.sub("", heading or "").strip().strip("'")[:_SHEET_CHARS].strip()
    if not name or name.lower() in taken:
        name = f"Table {number}"
    taken.add(name.lower())
    return name


def _xlsx(tables: list[tuple[str | None, b.Table]]) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    book = Workbook()
    book.remove(book.active)
    taken: set[str] = set()
    for number, (heading, table) in enumerate(tables, start=1):
        sheet = book.create_sheet(_sheet_name(heading, number, taken))
        for r, row in enumerate(_grid(table), start=1):
            for c, text in enumerate(row, start=1):
                cell = sheet.cell(row=r, column=c)
                cell.value = _XML_BAD.sub("", text)
                cell.data_type = "s"  # text, whatever it looks like
                if r == 1:
                    cell.font = Font(bold=True)
        sheet.freeze_panes = "A2"
        for c, column in enumerate(table.columns, start=1):
            longest = max([len(column)] + [len(row[c - 1].text) for row in table.rows])
            sheet.column_dimensions[sheet.cell(row=1, column=c).column_letter].width = min(
                max(longest + 2, 10), 60
            )
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def export(document: Document, fmt: str, *, table: int = 0) -> tuple[bytes, str, str]:
    """`(file bytes, media type, file name)` of the draft as `fmt`.

    `table` picks which table a CSV holds (the first by default); Excel gets every table, one sheet
    each. Raises `services.Refused` 409 `not_ready` for a draft that is not written, 409
    `no_table` for CSV or Excel without that table, `ValueError` for an unknown format, and
    `export.ExportBusy` / `export.ExportFailed` (`app/export.py`) from the Word and PDF render."""
    if fmt not in FORMATS:
        raise ValueError(fmt)
    if document.status != "ready":
        raise services.Refused(409, "not_ready", "This draft is not ready yet.")
    blocks = b.load_blocks(document.blocks)
    name = export_filename(document.title, fmt)
    if fmt == "md":
        data = b.to_markdown(blocks).encode()
    elif fmt == "txt":
        data = b.to_text(blocks).encode()
    elif fmt in ("csv", "xlsx"):
        tables = _tables(blocks)
        if not tables or not 0 <= table < len(tables):
            raise _no_table()
        data = _csv(tables[table][1]) if fmt == "csv" else _xlsx(tables)
    else:
        if (
            document.output_type == "board"
        ):  # columns as sections, each card with its kind and owner
            from app.recipes import board

            model = board.to_notes_document(
                document.title, blocks, when=document.created_at or datetime.now(UTC)
            )
        else:
            model = build_document_from_blocks(document)
        data = renderer.build_file(model, fmt)
        return data, renderer.FORMATS[fmt][0], name
    return data, _TYPES[fmt], name
