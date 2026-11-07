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
SLATE = "#5B6460"  # the footer link, as in Emails.dc.html
BRAND, PAGE, CARD_LINE = "#0E6B5E", "#F6F7F6", "#E3E7E5"
FONT = "system-ui,-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"


@dataclass(frozen=True)
class Heading:
    text: str
    after: int = 12  # space under it, px (the designer draws 10 in most mails, 4 in reminders)


@dataclass(frozen=True)
class Meta:
    """The small grey line above or below a heading."""

    text: str
    after: int = 16  # space under it, px (the designer draws 20 under the shared-notes line)


@dataclass(frozen=True)
class Para:
    text: str
    muted: bool = False
    after: int | None = None  # space under it in px; None keeps the usual 20
    before: int = 0  # space above it, px (under a button, whose own margin is 4)
    link: tuple[str, str] | None = None  # a teal link (label, URL) at the end of the sentence
    strong: str | None = None  # a part of `text` shown a little bolder (the meeting's name)


@dataclass(frozen=True)
class Label:
    """A section name ("Summary", "Decisions")."""

    text: str


@dataclass(frozen=True)
class Bullets:
    items: tuple[str, ...]
    after: int = 20  # space under the list, px
    gap: int = 4  # space under each item, px (the reminder mails draw 6)


@dataclass(frozen=True)
class Rows:
    """A ruled list where each row is some text and a small note at the right (a task and when
    it is due, as in the designer's "New tasks assigned" mail). Both are plain text."""

    items: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Quote:
    """A box holding someone's own words. Shown as plain text: never linked."""

    text: str


@dataclass(frozen=True)
class Button:
    label: str
    url: str
    secondary: bool = False  # the outlined button next to the main one ("View agenda")


@dataclass(frozen=True)
class ButtonRow:
    """Buttons side by side, as the designer draws "Join meeting" and "View agenda"."""

    buttons: tuple[Button, ...]
    after: int = 28


@dataclass(frozen=True)
class Pill:
    """The small teal tag above a reminder's heading ("Starts in 1 hour")."""

    text: str


@dataclass(frozen=True)
class Subline:
    """The grey line under a reminder's heading: when, and on what."""

    text: str


@dataclass(frozen=True)
class Section:
    """A section name as the reminders draw it: small, capital letters, grey, spaced out. The
    text is given in normal case and shown upper-case."""

    text: str


@dataclass(frozen=True)
class TaskRow:
    title: str
    owner: str  # "Amara Nwosu", or "Needs owner": under the title
    due: str  # "No due date", "Was due Fri 6 Nov", "Due today": at the right
    overdue: bool = False
    dated: bool = False  # has a due date that is not past: shown in amber, as in "New tasks"


@dataclass(frozen=True)
class TaskRows:
    """A ruled list of tasks with the owner under each title and the due text at the right
    (the "Still open from last time" block of the reminders)."""

    items: tuple[TaskRow, ...]


@dataclass(frozen=True)
class SummaryBox:
    """The grey box that closes the one-hour reminder: a few plain sentences, no markup."""

    text: str


@dataclass(frozen=True)
class Divider:
    """A thin rule between two parts of a mail (translated notes, then the original)."""


Block = (
    Heading
    | Meta
    | Para
    | Label
    | Bullets
    | Rows
    | Quote
    | Button
    | ButtonRow
    | Divider
    | Pill
    | Subline
    | Section
    | TaskRows
    | SummaryBox
)


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


def _button_html(button: Button, extra: str = "") -> str:
    href = esc(check_url(button.url))
    if button.secondary:
        look = f"padding:10px 18px;border:1px solid #D5DBD8;color:{INK}"
    else:
        look = f"padding:11px 20px;background:{BRAND};color:#FFFFFF"
    style = f"display:inline-block;{look};border-radius:8px;font-size:14px;font-weight:550;"
    style += f"text-decoration:none{';' + extra if extra else ''}"
    return f'<a href="{href}" style="{style}">{esc(button.label)}</a>'


def _task_rows_html(items: tuple[TaskRow, ...]) -> str:
    """One small table per row, so each row sizes its own two parts the way the designer's flex
    rows do (a shared table would give every row the widest due text's column)."""
    line = "border-bottom:1px solid #EDF0EE"
    rows = []
    for row in items:
        color = "#B42318" if row.overdue else "#9A5B00" if row.dated else MUTED
        rows.append(
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>'
            f'<td style="padding:10px 16px 10px 0;{line};font-size:14px;color:{INK}">'
            f"{esc(row.title)}"
            f'<div style="font-size:12px;color:{MUTED}">{esc(row.owner)}</div></td>'
            f'<td align="right" valign="top" style="padding:10px 0;{line};font-size:12px;'
            f'color:{color};white-space:nowrap">{esc(row.due)}</td></tr></table>'
        )
    return f'<div style="margin:0 0 22px;border-top:1px solid #EDF0EE">{"".join(rows)}</div>'


def _block_html(block: Block) -> str:
    match block:
        case Heading(text, after):
            return (
                f'<h1 style="margin:0 0 {after}px;font-size:22px;font-weight:650;'
                f'letter-spacing:-0.02em;color:{INK}">{esc(text)}</h1>'
            )
        case Meta(text, after):
            return (
                f'<div style="font-size:13px;color:{MUTED};margin:0 0 {after}px">{esc(text)}</div>'
            )
        case Para(text, muted, after, before, link, strong):
            color, size = (MUTED, "13px") if muted else ("#2E3532", "15px")
            shown = esc(text)
            if strong and strong in text:
                head, _, rest = text.partition(strong)
                shown = f'{esc(head)}<span style="font-weight:550">{esc(strong)}</span>{esc(rest)}'
            tail = ""
            if link is not None:
                label, url = link
                tail = f' <a href="{esc(check_url(url))}" style="color:{BRAND}">{esc(label)}</a>'
            return (
                f'<p style="margin:{before}px 0 {20 if after is None else after}px;color:{color};'
                f'font-size:{size};line-height:1.6">{shown}{tail}</p>'
            )
        case Label(text):
            return (
                f'<div style="font-size:13px;font-weight:600;margin:0 0 6px;color:{INK}">'
                f"{esc(text)}</div>"
            )
        case Bullets(items, after, gap):
            rows = "".join(f'<li style="margin-bottom:{gap}px">{esc(i)}</li>' for i in items)
            return (
                f'<ul style="margin:0 0 {after}px;padding-left:20px;color:#2E3532;'
                f'font-size:14px;line-height:1.55">{rows}</ul>'
            )
        case Rows(items):
            rows = "".join(
                f'<tr><td style="padding:12px 16px 12px 0;border-bottom:1px solid #EDF0EE;'
                f'color:{INK};font-size:15px">{esc(text)}</td>'
                f'<td align="right" style="padding:12px 0;border-bottom:1px solid #EDF0EE;'
                f'font-size:13px;color:#9A5B00;white-space:nowrap">{esc(note)}</td></tr>'
                for text, note in items
            )
            return (
                f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
                f'style="margin:0 0 24px;border-top:1px solid #EDF0EE">{rows}</table>'
            )
        case Quote(text):
            return (
                f'<div style="background:{PAGE};border-radius:10px;padding:14px 16px;'
                f'margin:0 0 22px;color:#2E3532;font-size:14px">“{esc(text)}”</div>'
            )
        case Divider():
            return f'<hr style="border:0;border-top:1px solid {CARD_LINE};margin:8px 0 22px">'
        case Button() as button:
            return f'<p style="margin:0 0 4px">{_button_html(button)}</p>'
        case ButtonRow(buttons, after):
            gap = "margin-right:10px"
            row = "".join(_button_html(b, gap) for b in buttons)
            return f'<div style="margin:0 0 {after}px">{row}</div>'
        case Pill(text):
            return (
                f'<div style="margin:0 0 10px"><span style="display:inline-block;font-size:12px;'
                f"font-weight:600;color:#0A5248;background:#E6F1EE;padding:3px 10px;"
                f'border-radius:999px">{esc(text)}</span></div>'
            )
        case Subline(text):
            return f'<div style="font-size:14px;color:{SLATE};margin:0 0 22px">{esc(text)}</div>'
        case Section(text):
            return (
                f'<div style="font-size:12px;font-weight:600;color:{MUTED};'
                f'letter-spacing:0.04em;margin:0 0 8px">{esc(text.upper())}</div>'
            )
        case TaskRows(items):
            return _task_rows_html(items)
        case SummaryBox(text):
            return (
                f'<div style="padding:12px 14px;background:{PAGE};border-radius:10px;'
                f'font-size:13px;color:#4A524E;line-height:1.55">{esc(text)}</div>'
            )
    raise TypeError(type(block).__name__)


def render_html(
    title: str,
    blocks: list[Block],
    footer: str,
    footer_link: tuple[str, str] | None = None,
) -> str:
    """A whole HTML document for `blocks`, with `footer` under the card and, when given,
    `footer_link` (a label and an absolute http(s) URL) at the end of the footer line."""
    link = ""
    if footer_link is not None:
        label, url = footer_link
        # On the footer's own line, after a space, in the designer's slate (Emails.dc.html).
        link = f' <a href="{esc(check_url(url))}" style="color:{SLATE}">{esc(label)}</a>'
    body = "".join(_block_html(b) for b in blocks)
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{esc(title)}</title></head>"
        f'<body style="margin:0;padding:0;background:{PAGE};font-family:{FONT};color:{INK};'
        'font-size:15px;line-height:1.6">'
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
        f"{esc(footer)}{link}</td></tr></table></td></tr></table></body></html>"
    )


def render_text(
    blocks: list[Block], footer: str, footer_link: tuple[str, str] | None = None
) -> str:
    """The plain-text part: the same blocks, one paragraph each."""
    parts: list[str] = []
    for block in blocks:
        match block:
            case Para(text, link=link) if link is not None:
                parts.append(f"{text}\n{link[0]}: {check_url(link[1])}")
            case (
                Heading(text)
                | Meta(text)
                | Para(text)
                | Label(text)
                | Pill(text)
                | Subline(text)
                | Section(text)
                | SummaryBox(text)
            ):
                parts.append(text)
            case TaskRows(items):
                parts.append("\n".join(f"- {r.title} ({r.owner}; {r.due})" for r in items))
            case ButtonRow(buttons):
                parts.append("\n\n".join(f"{b.label}:\n{check_url(b.url)}" for b in buttons))
            case Bullets(items):
                parts.append("\n".join(f"- {item}" for item in items))
            case Rows(items):
                parts.append(
                    "\n".join(f"- {text}" + (f" ({note})" if note else "") for text, note in items)
                )
            case Quote(text):
                parts.append(f"“{text}”")
            case Divider():
                parts.append("----")
            case Button(label, url):
                parts.append(f"{label}:\n{check_url(url)}")
    parts.append(footer)
    if footer_link is not None:
        label, url = footer_link
        parts[-1] = f"{footer}\n{label}: {check_url(url)}"
    return "\n\n".join(p for p in parts if p)
