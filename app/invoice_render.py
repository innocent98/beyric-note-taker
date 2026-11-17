"""Render an invoice as a PDF (WeasyPrint). Run as a child process by `app/billing/invoice_pdf.py`:

    python -m app.invoice_render   (the invoice document as JSON on stdin, the PDF on stdout)

for the same reasons as `app/export_render.py`: a render that hangs or grows is killed and its
memory returned, and the renderer never sees the web process's environment (no database password,
no API keys). This module imports nothing from the rest of the app, and every piece of text is
escaped, so nothing in a buyer's name or address can pull in another resource or change the page.

The document is plain text prepared by the parent (money and dates already written out), so the
page does no arithmetic of its own: what the invoice says is what `app/billing/invoices.py` stored.
"""

import json
import resource
import sys
from html import escape
from pathlib import Path

MEMORY_LIMIT_BYTES = 2 * 1024**3
FONTS = Path(__file__).resolve().parent / "static" / "fonts"

_CSS = """
@page {
  size: A4;
  margin: 20mm 18mm 18mm;
  @bottom-left { content: "%(footer)s"; font: 8pt "Geist", sans-serif; color: #6E7772; }
  @bottom-right {
    content: counter(page) " / " counter(pages);
    font: 8pt "Geist", sans-serif; color: #6E7772;
  }
}
@font-face { font-family: "Geist"; font-weight: 100 900; src: url("%(geist)s"); }
@font-face { font-family: "Geist Mono"; font-weight: 100 900; src: url("%(geist_mono)s"); }
body { font: 10pt/1.5 "Geist", sans-serif; color: #141917; }
h1 { font-size: 24pt; margin: 0; letter-spacing: -0.01em; }
.number { font: 11pt "Geist Mono", monospace; color: #0A5248; margin: 2pt 0 0; }
.head { border-bottom: 1.5pt solid #0E6B5E; padding-bottom: 10pt; margin-bottom: 16pt; }
.banner {
  border: 1pt solid #B45309; color: #7C2D12; background: #FFF7ED; padding: 6pt 9pt;
  margin: 0 0 14pt; font-weight: 600;
}
.parties { width: 100%%; border-collapse: collapse; margin-bottom: 18pt; }
.parties td { width: 50%%; vertical-align: top; padding: 0 12pt 0 0; }
.label {
  font-size: 8pt; text-transform: uppercase; letter-spacing: 0.06em; color: #5B6460;
  margin: 0 0 3pt;
}
.party p { margin: 0 0 1pt; }
.party .name { font-weight: 600; }
table.items { width: 100%%; border-collapse: collapse; margin-bottom: 4pt; }
table.items th {
  text-align: left; font-size: 8pt; text-transform: uppercase; letter-spacing: 0.06em;
  color: #5B6460; border-bottom: 1pt solid #D5DBD8; padding: 4pt 0;
}
table.items td { padding: 7pt 0; border-bottom: 0.5pt solid #E6EAE8; vertical-align: top; }
.right { text-align: right; }
table.totals { margin: 8pt 0 18pt auto; border-collapse: collapse; min-width: 55%%; }
table.totals td { padding: 3pt 0 3pt 14pt; }
table.totals tr.total td { border-top: 1pt solid #141917; font-weight: 700; font-size: 11.5pt; }
.meta { color: #5B6460; font-size: 9pt; margin: 0 0 2pt; }
.note { color: #4A524E; font-size: 9pt; margin: 10pt 0 0; }
"""


def _css_string(text: str) -> str:
    return "".join(f"\\{ord(c):x} " if c in '"\\\n\r<>&%' or ord(c) < 32 else c for c in text)


def _lines(lines: list[str], name_first: bool = True) -> str:
    out = []
    for index, line in enumerate(lines):
        css = ' class="name"' if name_first and index == 0 else ""
        out.append(f"<p{css}>{escape(line)}</p>")
    return "".join(out)


def render_html(doc: dict) -> str:
    """The invoice as one escaped HTML page with a print stylesheet."""
    css = _CSS % {
        "footer": _css_string(doc["footer"]),
        "geist": (FONTS / "geist-latin-wght-normal.woff2").as_uri(),
        "geist_mono": (FONTS / "geist-mono-latin-wght-normal.woff2").as_uri(),
    }
    parts = [
        '<!doctype html><html lang="en"><head><meta charset="utf-8">',
        f"<title>Invoice {escape(doc['number'])}</title><style>{css}</style></head><body>",
        f'<div class="head"><h1>Invoice</h1><p class="number">{escape(doc["number"])}</p>',
        f'<p class="meta">Issued {escape(doc["issued"])}</p></div>',
    ]
    for banner in doc.get("banners", []):
        parts.append(f'<p class="banner">{escape(banner)}</p>')
    parts.append(
        '<table class="parties"><tr>'
        f'<td class="party"><p class="label">From</p>{_lines(doc["seller"])}</td>'
        f'<td class="party"><p class="label">Billed to</p>{_lines(doc["buyer"])}</td>'
        "</tr></table>"
    )
    parts.append(
        '<table class="items"><tr><th>Description</th><th class="right">Amount</th></tr>'
        f"<tr><td>{escape(doc['description'])}"
        + (f'<p class="meta">{escape(doc["period"])}</p>' if doc.get("period") else "")
        + f'</td><td class="right">{escape(doc["subtotal"])}</td></tr></table>'
    )
    rows = [
        f'<tr><td>{escape(label)}</td><td class="right">{escape(value)}</td></tr>'
        for label, value in doc["totals"]
    ]
    parts.append(
        '<table class="totals">'
        + "".join(rows)
        + f'<tr class="total"><td>{escape(doc["total_label"])}</td>'
        f'<td class="right">{escape(doc["total"])}</td></tr></table>'
    )
    for note in doc.get("notes", []):
        parts.append(f'<p class="note">{escape(note)}</p>')
    parts.append("</body></html>")
    return "".join(parts)


def render_pdf(doc: dict) -> bytes:
    from weasyprint import HTML

    # No base_url and no network: the page refers only to the two font files by absolute path.
    return HTML(string=render_html(doc)).write_pdf()


def main() -> int:
    if sys.platform.startswith("linux"):  # macOS does not enforce it, and developers run there
        resource.setrlimit(resource.RLIMIT_AS, (MEMORY_LIMIT_BYTES, MEMORY_LIMIT_BYTES))
    doc = json.loads(sys.stdin.buffer.read())
    sys.stdout.buffer.write(render_pdf(doc))
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
