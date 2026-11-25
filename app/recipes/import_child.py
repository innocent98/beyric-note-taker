"""The part of a brief import that opens a file: `python -m app.recipes.import_child <kind>
<max_pages> <max_chars>`, a child process of `app/recipes/importer.py`.

The file arrives on stdin and one line of JSON leaves on stdout: `{"text", "pages", "truncated"}`
or `{"error": "<code>"}`. It runs in a child so that a file made to hang or to eat memory is killed
by the parent's time limit and its memory goes back to the system, as the export renderer does
(`app/export.py`). It imports nothing from the app (no settings, no database) and is given no
environment but PATH; on Linux it also caps its own memory and CPU time.

`clean_text` is shared with the parent (plain `.txt` and `.md` files) and with the brief route.
"""

import io
import json
import sys
import unicodedata
import zipfile

MAX_ZIP_MEMBERS = 500
MAX_UNPACKED_BYTES = 40_000_000  # all members together, as declared in the zip's directory
MAX_MEMBER_BYTES = 25_000_000
RATIO_FROM_BYTES = 1_000_000  # a member this big must not be a thousand-fold squeeze
MAX_RATIO = 100
MAX_TABLE_DEPTH = 3
CELL_JOINER = " | "

# `Cf` is mostly invisible marks: the bidirectional overrides and zero-width characters can hide
# text from the person reviewing it, so they go. A joiner inside a word (U+200D, U+200C) is kept:
# Persian, Indic and emoji text needs it.
_KEPT_FORMAT_MARKS = {"‌", "‍"}


class Refused(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def clean_text(text: str, max_chars: int | None = None) -> tuple[str, bool]:
    """Plain text with one kind of newline, no control or hidden-direction characters, no trailing
    spaces and no run of blank lines. Cut at `max_chars` when given: the second value says whether
    anything was cut."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    kept = []
    for ch in text:
        if ch in "\n\t":
            kept.append(ch)
        elif unicodedata.category(ch) == "Cc":
            continue
        elif unicodedata.category(ch) == "Cf" and ch not in _KEPT_FORMAT_MARKS:
            continue
        else:
            kept.append(ch)
    lines = [line.rstrip() for line in "".join(kept).split("\n")]
    out: list[str] = []
    blank = 0
    for line in lines:
        blank = blank + 1 if not line else 0
        if blank <= 1:
            out.append(line)
    text = "\n".join(out).strip()
    if max_chars is not None and len(text) > max_chars:
        return text[:max_chars].rstrip(), True
    return text, False


def check_zip(data: bytes) -> zipfile.ZipFile:
    """Open a zip and refuse a zip bomb BEFORE anything is inflated: too many members, a member or
    a total that declares too much, or a member that squeezes by more than `MAX_RATIO`. The sizes
    are the zip directory's; `zipfile` stops reading a member at its declared size, so a directory
    that lies about it gains nothing."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
        members = archive.infolist()
    except (zipfile.BadZipFile, OSError, ValueError, NotImplementedError) as exc:
        raise Refused("unreadable") from exc
    if len(members) > MAX_ZIP_MEMBERS:
        raise Refused("too_big")
    if sum(m.file_size for m in members) > MAX_UNPACKED_BYTES:
        raise Refused("too_big")
    for m in members:
        if m.file_size > MAX_MEMBER_BYTES:
            raise Refused("too_big")
        if m.file_size > RATIO_FROM_BYTES and m.file_size > MAX_RATIO * max(m.compress_size, 1):
            raise Refused("too_big")
    return archive


def _table_lines(table, depth: int) -> list[str]:
    lines: list[str] = []
    for row in table.rows:
        cells, last = [], None
        for cell in row.cells:
            if last is not None and cell._tc is last:
                continue  # a merged cell is handed back once per column it spans
            last = cell._tc
            parts = [p.text.strip() for p in cell.paragraphs if p.text.strip()]
            if depth < MAX_TABLE_DEPTH:
                for inner in cell.tables:
                    parts.extend(_table_lines(inner, depth + 1))
            cells.append(" ".join(parts))
        if any(cells):
            lines.append(CELL_JOINER.join(cells))
    return lines


def read_docx(data: bytes, max_chars: int) -> dict:
    archive = check_zip(data)
    if "word/document.xml" not in archive.namelist():
        raise Refused("unreadable")
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    try:
        document = Document(io.BytesIO(data))
    except Exception as exc:  # a damaged part: python-docx raises many kinds
        raise Refused("unreadable") from exc
    lines: list[str] = []
    for child in document.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            lines.append(Paragraph(child, document).text)
        elif tag == "tbl":
            lines.extend(_table_lines(Table(child, document), 1))
        if sum(len(line) + 1 for line in lines) > max_chars * 2:
            break  # far more than will be kept: stop reading
    text, cut = clean_text("\n".join(lines), max_chars)
    return {"text": text, "pages": None, "truncated": cut}


def read_pdf(data: bytes, max_pages: int, max_chars: int) -> dict:
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise Refused("unreadable")
        pages = len(reader.pages)
        if pages > max_pages:
            raise Refused("too_many_pages")
        chunks: list[str] = []
        size = 0
        for page in reader.pages:
            piece = page.extract_text() or ""
            chunks.append(piece)
            size += len(piece)
            if size > max_chars * 2:
                break
    except Refused:
        raise
    except (
        PyPdfError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        OSError,
        RecursionError,
    ) as exc:
        raise Refused("unreadable") from exc
    text, cut = clean_text("\n\n".join(chunks), max_chars)
    return {"text": text, "pages": pages, "truncated": cut}


def _limit_resources() -> None:
    """Linux only (the container): a memory and a CPU ceiling for this process, so a file that
    would exhaust either dies here instead of taking the web process down with it."""
    if not sys.platform.startswith("linux"):
        return
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (1_500_000_000, 1_500_000_000))
        resource.setrlimit(resource.RLIMIT_CPU, (30, 30))
    except (ImportError, ValueError, OSError):
        pass


def main(argv: list[str]) -> int:
    _limit_resources()
    kind, max_pages, max_chars = argv[1], int(argv[2]), int(argv[3])
    data = sys.stdin.buffer.read()
    try:
        if kind == "docx":
            result = read_docx(data, max_chars)
        elif kind == "pdf":
            result = read_pdf(data, max_pages, max_chars)
        else:
            raise Refused("unsupported")
    except Refused as exc:
        result = {"error": exc.code}
    except Exception:  # anything else is a file we cannot read; say nothing about its content
        result = {"error": "unreadable"}
    sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
