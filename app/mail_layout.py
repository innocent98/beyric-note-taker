"""The designer's mail layout (`docs/ui/Beyric Note Taker redesign/Emails.dc.html`) as blocks.

A mail is a list of blocks of plain text (`Heading`, `Para`, `Bullets`, ...). `render_html` and
`render_text` turn the same list into the two parts of the message, so they cannot disagree, and
`render_html` escapes every string it is given: there is no way to hand it markup, which is what
keeps a meeting title, a speaker name or a line of notes (all written by other people) from
changing the page. The only attribute that holds a URL is a button's, and it must be http or
https.

Deviations from the drawing, on purpose: the logo is a green square with a "B" made of HTML,
because Gmail drops inline SVG and many clients block remote images; the font stack is the
system's, because the web font would be a remote load. Layout uses a centred 600 px table with
inline styles, which every client renders.
"""

import html
from dataclasses import dataclass
from urllib.parse import urlsplit

from app.mail_message import has_control

INK, MUTED, FAINT = "#141917", "#6E7772", "#8A928E"
BRAND, PAGE, CARD_LINE = "#0E6B5E", "#F6F7F6", "#E3E7E5"
FONT = "system-ui,-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"


@dataclass(frozen=True)
class Heading:
    text: str


@dataclass(frozen=True)
class Meta:
    """The small grey line above or below a heading."""

    text: str


@dataclass(frozen=True)
class Para:
    text: str
    muted: bool = False


@dataclass(frozen=True)
class Label:
    """A section name ("Summary", "Decisions")."""

    text: str


@dataclass(frozen=True)
class Bullets:
    items: tuple[str, ...]


@dataclass(frozen=True)
class Quote:
    """A box holding someone's own words. Shown as plain text: never linked."""

    text: str


@dataclass(frozen=True)
class Button:
    label: str
    url: str


Block = Heading | Meta | Para | Label | Bullets | Quote | Button


def check_url(url: str) -> str:
    """`url` unchanged when it is an absolute http(s) address with a host and no control
    characters or spaces, else ValueError. Nothing else may reach an `href`."""
    parts = urlsplit(url)
    if (
        parts.scheme not in ("http", "https")
        or not parts.netloc
        or has_control(url)
        or any(c.isspace() for c in url)
    ):
        raise ValueError("url")
    return url


def esc(text: str) -> str:
    return html.escape(text, quote=True)


def _block_html(block: Block) -> str:
    match block:
        case Heading(text):
            return (
                f'<h1 style="margin:0 0 12px;font-size:22px;font-weight:650;'
                f'letter-spacing:-0.02em;color:{INK}">{esc(text)}</h1>'
            )
        case Meta(text):
            return f'<div style="font-size:13px;color:{MUTED};margin:0 0 16px">{esc(text)}</div>'
        case Para(text, muted):
            color, size = (MUTED, "13px") if muted else ("#2E3532", "15px")
            return (
                f'<p style="margin:0 0 20px;color:{color};font-size:{size};line-height:1.6">'
                f"{esc(text)}</p>"
            )
        case Label(text):
            return (
                f'<div style="font-size:13px;font-weight:600;margin:0 0 6px;color:{INK}">'
                f"{esc(text)}</div>"
            )
        case Bullets(items):
            rows = "".join(f'<li style="margin-bottom:4px">{esc(i)}</li>' for i in items)
            return (
                f'<ul style="margin:0 0 20px;padding-left:20px;color:#2E3532;font-size:14px;'
                f'line-height:1.55">{rows}</ul>'
            )
        case Quote(text):
            return (
                f'<div style="background:{PAGE};border-radius:10px;padding:14px 16px;'
                f'margin:0 0 22px;color:#2E3532;font-size:14px">“{esc(text)}”</div>'
            )
        case Button(label, url):
            return (
                f'<p style="margin:0 0 4px"><a href="{esc(check_url(url))}" '
                f'style="display:inline-block;padding:11px 20px;background:{BRAND};color:#FFFFFF;'
                f'border-radius:8px;font-size:14px;font-weight:550;text-decoration:none">'
                f"{esc(label)}</a></p>"
            )
    raise TypeError(type(block).__name__)


def render_html(title: str, blocks: list[Block], footer: str) -> str:
    """A whole HTML document for `blocks`, with `footer` under the card."""
    body = "".join(_block_html(b) for b in blocks)
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{esc(title)}</title></head>"
        f'<body style="margin:0;padding:0;background:{PAGE};font-family:{FONT};color:{INK}">'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="background:{PAGE}"><tr><td align="center" style="padding:32px 16px">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="max-width:600px">'
        '<tr><td style="padding:0 4px 18px;font-size:14px">'
        f'<span style="display:inline-block;width:24px;height:24px;line-height:24px;'
        f"text-align:center;border-radius:7px;background:{BRAND};color:#FFFFFF;"
        'font-weight:700;vertical-align:middle">B</span> '
        '<span style="font-weight:650;vertical-align:middle">Beyric</span> '
        '<span style="color:#5B6460;vertical-align:middle">Note Taker</span></td></tr>'
        f'<tr><td style="background:#FFFFFF;border:1px solid {CARD_LINE};border-radius:12px;'
        f'padding:32px">{body}</td></tr>'
        f'<tr><td style="padding:18px 4px 0;font-size:12px;color:{FAINT};line-height:1.6">'
        f"{esc(footer)}</td></tr></table></td></tr></table></body></html>"
    )


def render_text(blocks: list[Block], footer: str) -> str:
    """The plain-text part: the same blocks, one paragraph each."""
    parts: list[str] = []
    for block in blocks:
        match block:
            case Heading(text) | Meta(text) | Para(text) | Label(text):
                parts.append(text)
            case Bullets(items):
                parts.append("\n".join(f"- {item}" for item in items))
            case Quote(text):
                parts.append(f"“{text}”")
            case Button(label, url):
                parts.append(f"{label}:\n{check_url(url)}")
    parts.append(footer)
    return "\n\n".join(p for p in parts if p)
