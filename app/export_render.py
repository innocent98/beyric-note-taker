"""Render a `NotesDocument` as PDF (WeasyPrint) or Word (python-docx).

Run as a child process by `app/export.py`:

    python -m app.export_render pdf|docx   (document as JSON on stdin, file on stdout)

so a render that hangs or grows large is killed and its memory returned to the system, and the
renderer never sees the web process's environment (no database password, no API keys). This
module therefore imports nothing from the rest of the app but the document model, and the heavy
libraries only inside the functions that need them.
"""

import sys
from html import escape
from pathlib import Path

from app.notes_document import (
    Block,
    CodeBlock,
    Heading,
    ListBlock,
    NotesDocument,
    Paragraph,
    Quote,
    Run,
)

# Left alone, a runaway render would take whatever memory the shared server has. A one-hour
# transcript needs about 90 MiB and a 40,000-word one about 130 MiB (spike EX1); the limit is on
# address space, which runs higher than resident memory, so it is set well above that.
MEMORY_LIMIT_BYTES = 2 * 1024**3

FONTS = Path(__file__).resolve().parent / "static" / "fonts"

# The brand colours of web/styles/tokens.css, written out because a print stylesheet cannot read
# the app's custom properties.
_CSS = """
@page {
  size: A4;
  margin: 22mm 18mm 20mm;
  @bottom-left { content: "%(footer)s"; font: 8pt "Geist", sans-serif; color: #6E7772; }
  @bottom-right {
    content: counter(page) " / " counter(pages);
    font: 8pt "Geist", sans-serif; color: #6E7772;
  }
}
@font-face { font-family: "Geist"; font-weight: 100 900; src: url("%(geist)s"); }
@font-face { font-family: "Geist Mono"; font-weight: 100 900; src: url("%(geist_mono)s"); }
body { font: 10.5pt/1.55 "Geist", sans-serif; color: #141917; }
h1 { font-size: 21pt; line-height: 1.2; margin: 0 0 4pt; letter-spacing: -0.01em; }
.meta {
  color: #5B6460; margin: 0 0 14pt; padding-bottom: 10pt; border-bottom: 1.5pt solid #0E6B5E;
}
h2 {
  font-size: 13pt; margin: 18pt 0 5pt; color: #0A5248; break-after: avoid;
}
h3, h4, h5, h6 { font-size: 11pt; margin: 10pt 0 3pt; break-after: avoid; }
p { margin: 0 0 6pt; }
ul, ol { margin: 0 0 6pt; padding-left: 16pt; }
li { margin: 2pt 0; }
li > p { margin: 0 0 2pt; }
code, pre { font-family: "Geist Mono", monospace; font-size: 9pt; background: #F3F5F4; }
pre { padding: 6pt 8pt; white-space: pre-wrap; border-radius: 3pt; }
blockquote { margin: 0 0 6pt; padding-left: 10pt; border-left: 2pt solid #D5DBD8; color: #4A524E; }
.transcript h2 { break-before: page; }
.turn { margin: 0 0 7pt; break-inside: avoid; }
.who { font-weight: 600; color: #0A5248; }
"""


def _css_string(text: str) -> str:
    """`text` as the inside of a CSS string literal."""
    return "".join(f"\\{ord(c):x} " if c in '"\\\n\r<>&%' or ord(c) < 32 else c for c in text)


def _runs_html(runs: list[Run]) -> str:
    out = []
    for run in runs:
        text = escape(run.text).replace("\n", "<br>")
        if run.code:
            text = f"<code>{text}</code>"
        if run.bold:
            text = f"<strong>{text}</strong>"
        if run.italic:
            text = f"<em>{text}</em>"
        out.append(text)
    return "".join(out)


def _block_html(block: Block) -> str:
    match block:
        case Paragraph():
            return f"<p>{_runs_html(block.runs)}</p>"
        case Heading():
            level = min(max(block.level, 3), 6)
            return f"<h{level}>{_runs_html(block.runs)}</h{level}>"
        case CodeBlock():
            return f"<pre>{escape(block.text)}</pre>"
        case Quote():
            return f"<blockquote>{''.join(_block_html(b) for b in block.blocks)}</blockquote>"
        case ListBlock():
            tag = "ol" if block.ordered else "ul"
            start = f' start="{block.start}"' if block.ordered and block.start != 1 else ""
            items = "".join(
                f"<li>{''.join(_block_html(b) for b in item)}</li>" for item in block.items
            )
            return f"<{tag}{start}>{items}</{tag}>"
    raise TypeError(type(block))  # pragma: no cover


def render_html(doc: NotesDocument) -> str:
    """The document as one escaped HTML page with a print stylesheet."""
    css = _CSS % {
        "footer": _css_string(doc.footer),
        "geist": (FONTS / "geist-latin-wght-normal.woff2").as_uri(),
        "geist_mono": (FONTS / "geist-mono-latin-wght-normal.woff2").as_uri(),
    }
    parts = [
        '<!doctype html><html lang="en"><head><meta charset="utf-8">',
        f"<title>{escape(doc.title)}</title><style>{css}</style></head><body>",
        f'<h1>{escape(doc.title)}</h1><p class="meta">{escape(doc.meta)}</p>',
    ]
    for section in doc.sections:
        if section.heading:
            parts.append(f"<h2>{escape(section.heading)}</h2>")
        parts.extend(_block_html(block) for block in section.blocks)
    if doc.transcript is not None:
        parts.append('<section class="transcript"><h2>Transcript</h2>')
        for turn in doc.transcript:
            who = f'<span class="who">{escape(turn.speaker)}:</span> ' if turn.speaker else ""
            parts.append(f'<p class="turn">{who}{escape(turn.text).replace(chr(10), "<br>")}</p>')
        parts.append("</section>")
    parts.append("</body></html>")
    return "".join(parts)


def render_pdf(doc: NotesDocument) -> bytes:
    from weasyprint import HTML

    # No base_url and no network: the page only refers to the two font files by absolute path,
    # and every piece of text is escaped, so nothing in it can pull in another resource.
    return HTML(string=render_html(doc)).write_pdf()


# ---- Word ----


def _add_runs(paragraph, runs: list[Run]) -> None:
    from docx.shared import Pt

    for run in runs:
        parts = run.text.split("\n")
        for index, part in enumerate(parts):
            word_run = paragraph.add_run(part)
            word_run.bold = run.bold or None
            word_run.italic = run.italic or None
            if run.code:
                word_run.font.name = "Courier New"
                word_run.font.size = Pt(9.5)
            if index < len(parts) - 1:
                word_run.add_break()


def _add_block(doc, block: Block, *, list_level: int = 0, quoted: bool = False) -> None:
    match block:
        case Paragraph():
            paragraph = doc.add_paragraph(style="Quote" if quoted else None)
            _add_runs(paragraph, block.runs)
        case Heading():
            heading = doc.add_heading(level=min(max(block.level - 1, 2), 4))
            _add_runs(heading, block.runs)
        case CodeBlock():
            paragraph = doc.add_paragraph()
            _add_runs(paragraph, [Run(text=block.text, code=True)])
        case Quote():
            for inner in block.blocks:
                _add_block(doc, inner, list_level=list_level, quoted=True)
        case ListBlock():
            base = "List Number" if block.ordered else "List Bullet"
            style = base if list_level == 0 else f"{base} {min(list_level + 1, 3)}"
            for item in block.items:
                first = True
                for inner in item:
                    if first and isinstance(inner, Paragraph):
                        paragraph = doc.add_paragraph(style=style)
                        _add_runs(paragraph, inner.runs)
                    elif isinstance(inner, ListBlock):
                        _add_block(doc, inner, list_level=list_level + 1)
                    else:
                        _add_block(doc, inner, list_level=list_level)
                    first = False


def render_docx(doc: NotesDocument) -> bytes:
    from io import BytesIO

    from docx import Document
    from docx.shared import Pt, RGBColor

    word = Document()
    word.core_properties.title = doc.title
    word.core_properties.author = "Beyric Note Taker"
    normal = word.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(11)
    teal = RGBColor(0x0A, 0x52, 0x48)
    for name in ("Title", "Heading 1", "Heading 2", "Heading 3", "Heading 4"):
        word.styles[name].font.name = "Calibri"  # not the theme's "Calibri Light"
        word.styles[name].font.color.rgb = teal
    for name in ("List Bullet", "List Bullet 2", "List Bullet 3", "List Number", "List Number 2"):
        word.styles[name].paragraph_format.space_after = Pt(3)

    word.add_heading(doc.title, level=0)
    meta = word.add_paragraph()
    meta_run = meta.add_run(doc.meta)
    meta_run.font.color.rgb = RGBColor(0x5B, 0x64, 0x60)
    for section in doc.sections:
        if section.heading:
            word.add_heading(section.heading, level=1)
        for block in section.blocks:
            _add_block(word, block)
    if doc.transcript is not None:
        word.add_heading("Transcript", level=1).paragraph_format.page_break_before = True
        for turn in doc.transcript:
            paragraph = word.add_paragraph()
            if turn.speaker:
                who = paragraph.add_run(f"{turn.speaker}: ")
                who.bold = True
            _add_runs(paragraph, [Run(text=turn.text)])
    footer = word.sections[0].footer.paragraphs[0]
    footer_run = footer.add_run(doc.footer)
    footer_run.font.size = Pt(8)
    footer_run.font.color.rgb = RGBColor(0x6E, 0x77, 0x72)

    buffer = BytesIO()
    word.save(buffer)
    return buffer.getvalue()


RENDERERS = {"pdf": render_pdf, "docx": render_docx}


def _peak_rss_kb() -> int:
    """This process's peak resident memory. Linux's `ru_maxrss` also counts the parent the child
    was forked from (it survives exec), which would report the web process's size, so read the
    kernel's per-process high-water mark; macOS has no /proc and reports `ru_maxrss` in bytes."""
    try:
        with open("/proc/self/status") as status:
            for line in status:
                if line.startswith("VmHWM:"):
                    return int(line.split()[1])
    except OSError:
        pass
    import resource

    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak // 1024 if sys.platform == "darwin" else peak


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] not in RENDERERS:
        print("usage: python -m app.export_render pdf|docx < document.json", file=sys.stderr)
        return 2
    if sys.platform.startswith("linux"):  # macOS does not enforce it, and developers run there
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (MEMORY_LIMIT_BYTES, MEMORY_LIMIT_BYTES))
    doc = NotesDocument.model_validate_json(sys.stdin.buffer.read())
    sys.stdout.buffer.write(RENDERERS[argv[1]](doc))
    sys.stdout.buffer.flush()
    print(
        f"peak_rss_kb={_peak_rss_kb()}", file=sys.stderr
    )  # read by app/export.py for its log line
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
