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
  (`mail_title`), and a bare domain or address has its dots written "[.]", because a mail
  client would turn either into a link.

Zero-width joiners and variation selectors are kept: an emoji sequence or a Persian word needs them.
A meeting without a title (a pasted link) is "Google Meet <code>" or "Zoom <code>".
"""

import re
import unicodedata

from app.models import Meeting

TITLE_LIMIT = 200  # what is stored and shown in a list or heading
MAIL_TITLE_LIMIT = 80  # what goes into a mail subject, a notification title or an alert line
LINK_REMOVED = "[link removed]"

# Format characters (Unicode category Cf: bidirectional controls, the invisible operators and
# joiners, tag characters, the byte-order mark...) are all removed EXCEPT the zero-width joiner and
# non-joiner that an emoji sequence or a Persian word needs. Variation selectors are not Cf and
# stay. A few letters draw nothing at all (Hangul fillers) and the Mongolian vowel separator is
# invisible: removed by name.
_KEEP_FORMAT = frozenset(["\u200c", "\u200d"])
_BLANK_LETTERS = frozenset(["\u115f", "\u1160", "\u3164", "\uffa0", "\u180e"])
_SPACE_RUN = re.compile(r"\s+")
# A bare domain or address ("evil.example.test", "a@b.test"): a mail client links these even with
# no scheme, so in mail text each dot of such a token is written "[.]". A last part that is not
# letters ("v2.1", "3.5") is not a domain and is left alone.
_DOMAINISH = re.compile(r"[\w-]+(?:\.[\w-]+)*\.[^\W\d_]{2,}\b")

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
        category = unicodedata.category(char)
        if char in _BLANK_LETTERS or (category == "Cf" and char not in _KEEP_FORMAT):
            continue
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
    return _fallback(meeting.platform, meeting.native_id)


def _fallback(platform: str | None, native_id: str | None) -> str:
    label = {"zoom": "Zoom", "teams": "Microsoft Teams"}.get(platform or "", "Google Meet")
    if platform == "teams":  # a Teams code is a derived token, not something a person knows
        return label
    return f"{label} {native_id}"


def meeting_name(meeting: Meeting) -> str:
    """The name to show for a meeting: its cleaned title, else the platform and code."""
    return clean_title(meeting.title) or fallback_name(meeting)


def alert_name(title: str | None, platform: str | None, native_id: str | None) -> str:
    """The name of an event or meeting for a mail, a notification or an alert: cleaned, short,
    with any web address removed and any bare domain defanged (a mail client would link them)."""
    plain = URL.sub(LINK_REMOVED, clean_title(title, TITLE_LIMIT))
    plain = _DOMAINISH.sub(lambda found: found.group().replace(".", "[.]"), plain)
    return clean_title(plain, MAIL_TITLE_LIMIT) or clean_title(
        _fallback(platform, native_id), MAIL_TITLE_LIMIT
    )


# The top-level domains a person's typed NAME is refused for ("Notes.ai", "acme.com"). Short on
# purpose: a name is not clickable in Meet, Zoom or Teams, and the wide `_DOMAINISH` rule used for
# mail text refused "Dr.Smith", "St.Mary" and any "word.word" in any script. The label may be in
# any script ("词.com" is refused), the TLD must be one of these ASCII ones, whole ("Alex.Coach" is
# fine).
LINK_TLDS = "com net org io ai co app dev me us uk ng info biz xyz link live gg tv ly".split()
_TLD_LINK = re.compile(
    r"(?<![\w-])[\w-]+(?:\.[\w-]+)*\.(?:" + "|".join(LINK_TLDS) + r")(?![\w-])", re.IGNORECASE
)


# Full stops that read as a dot in a web address but are not U+002E: ideographic full stop,
# fullwidth full stop, halfwidth ideographic full stop, small full stop, one dot leader. The link
# check and the chat defanging treat them as dots.
_DOT_LOOKALIKES = str.maketrans(
    {"\u3002": ".", "\uff0e": ".", "\uff61": ".", "\ufe52": ".", "\u2024": "."}
)
# The only first labels left alone in a dotted name: a single letter (an initial, "J.Smith") or a
# title ("Dr.Smith"). Anything else with a dot and letters after it is defanged, in any case.
_NAME_TITLES = frozenset("dr mr mrs ms mx st jr sr prof sgt capt gen col rev hon fr".split())
_NAME_DOMAIN = re.compile(r"(?<![\w-])[\w-]+(?:\.[\w-]+)*\.[^\W\d_]{2,}(?![\w-])")


def defang_name_domains(name: str) -> str:
    """A typed NAME for the meeting chat, with the dots of anything that could be linked written
    "[.]": every `label.tld` token ("evil.click" and "Evil.Click" become "evil[.]click" and
    "Evil[.]Click"), except one dot after a single letter or a title ("J.Smith", "Dr.Smith",
    "St.Mary"), which reads as an initial or an abbreviation: the exemption is that narrow, not a
    test of the case of what follows. Dot look-alikes count as dots."""
    plain = name.translate(_DOT_LOOKALIKES)

    def swap(found: re.Match) -> str:
        token = found.group()
        first, _, rest = token.partition(".")
        abbreviation = "." not in rest and (len(first) == 1 or first.lower() in _NAME_TITLES)
        return token if abbreviation else token.replace(".", "[.]")

    return _NAME_DOMAIN.sub(swap, plain)


def looks_like_link(text: str) -> bool:
    """Whether a typed name reads as a web address: it holds `://`, starts a word with `www.`,
    is a link to a meeting host written bare, or is `label.tld` with a TLD from `LINK_TLDS`.
    Narrower than `mail_line`'s defanging on purpose (see `LINK_TLDS`)."""
    text = text.translate(_DOT_LOOKALIKES)
    return bool(URL.search(text) or "://" in text or _TLD_LINK.search(text))


def mail_line(text: str | None, limit: int) -> str:
    """Words somebody said in a meeting (a task title) as one plain line for a mail: a web address
    is replaced by "[link removed]" and a bare domain has its dots written "[.]", because a mail
    client would link either. Cut at `limit` characters."""
    plain = URL.sub(LINK_REMOVED, clean_title(text, limit * 2))
    plain = _DOMAINISH.sub(lambda found: found.group().replace(".", "[.]"), plain)
    return clean_title(plain, limit)


def mail_title(meeting: Meeting) -> str:
    """`alert_name` for a stored meeting."""
    return alert_name(meeting.title, meeting.platform, meeting.native_id)


def event_title(text: str | None) -> str | None:
    """The title to store on a meeting made from a calendar event: cleaned, or None when nothing
    printable is left (the meeting then falls back to its platform and code)."""
    return clean_title(text) or None
