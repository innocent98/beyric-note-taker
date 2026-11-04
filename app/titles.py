"""The name a meeting is shown under (plan C12, decision 3).

A calendar meeting carries the title of its event, and an event title is **attacker-controlled
text**: anyone can invite the user with any title. So every title passes through `clean_title`
before it is stored (`Meeting.title`) and again before it is shown, and the rules are the same
everywhere it is rendered (the API, the workspace screens, the PDF and Word exports, the
notes-ready mail, notifications and alerts):

* plain text only: no markup is interpreted anywhere (HTML is escaped by Jinja, React, the export
  renderer and the mail layout); nothing here turns a title into a link;
* no control characters (newlines and tabs become one space, so a title can never start a new
  line in a mail header, a log line or a file name), no line or paragraph separators and no
  bidirectional override or isolate characters (a title that reorders the text around it);
* a length cap, cut at a character, with an ellipsis;
* in mail and alert text a web address inside a title is replaced by "[link removed]"
  (`mail_title`), because a mail client would turn it into a link.

Zero-width joiners and variation selectors are kept: an emoji sequence or a Persian word needs them.
A meeting without a title (a pasted link) is "Google Meet <code>" or "Zoom <code>".
"""

import re
import unicodedata

from app.models import Meeting

TITLE_LIMIT = 200  # what is stored and shown in a list or heading
MAIL_TITLE_LIMIT = 80  # what goes into a mail subject, a notification title or an alert line
LINK_REMOVED = "[link removed]"

# Bidirectional controls (marks, embeddings, overrides, isolates, the Arabic letter mark), the
# byte-order mark and the word joiner family that is invisible and used to hide text.
_BIDI_AND_INVISIBLE = frozenset(
    ["؜", "‎", "‏", "﻿", "⁠"]
    + [chr(c) for c in range(0x202A, 0x202F)]
    + [chr(c) for c in range(0x2066, 0x206A)]
)
_SPACE_RUN = re.compile(r"\s+")

# A web address with a scheme or `www.`, or a link to a meeting host written without one. The same
# pattern `app.alerts` uses for admin text.
URL = re.compile(
    r"""(?ix)
    (?:\b(?:https?://|www\.)|
       \b(?:[\w-]+\.)*(?:zoom\.us|zoom\.com|meet\.google\.com|teams\.microsoft\.com|teams\.live\.com)/)
    \S*
    """
)


def _printable(text: str) -> str:
    out = []
    for char in text:
        if char in _BIDI_AND_INVISIBLE:
            continue
        category = unicodedata.category(char)
        if category in ("Cc", "Zl", "Zp"):  # control, line separator, paragraph separator
            out.append(" ")
        elif category in ("Cs", "Co", "Cn"):  # lone surrogate, private use, unassigned
            continue
        else:
            out.append(char)
    return "".join(out)


def clean_title(text: str | None, limit: int = TITLE_LIMIT) -> str:
    """`text` as one plain line of at most `limit` characters; "" for nothing (or only junk)."""
    if not text:
        return ""
    line = _SPACE_RUN.sub(" ", _printable(str(text))).strip()
    if len(line) > limit:
        line = line[: max(limit - 1, 0)].rstrip() + "…"
    return line


def fallback_name(meeting: Meeting) -> str:
    """ "Zoom 88205679674" or "Google Meet abc-defg-hij": the name of a meeting without a title."""
    label = "Zoom" if meeting.platform == "zoom" else "Google Meet"
    return f"{label} {meeting.native_id}"


def meeting_name(meeting: Meeting) -> str:
    """The name to show for a meeting: its cleaned title, else the platform and code."""
    return clean_title(meeting.title) or fallback_name(meeting)


def mail_title(meeting: Meeting) -> str:
    """The name for a mail subject or body, an alert or a notification: cleaned, short, and with
    any web address in it removed (a mail client would link it)."""
    plain = URL.sub(LINK_REMOVED, clean_title(meeting.title, TITLE_LIMIT))
    title = clean_title(plain, MAIL_TITLE_LIMIT)
    return title or clean_title(fallback_name(meeting), MAIL_TITLE_LIMIT)


def event_title(text: str | None) -> str | None:
    """The title to store on a meeting made from a calendar event: cleaned, or None when nothing
    printable is left (the meeting then falls back to its platform and code)."""
    return clean_title(text) or None
