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
    plain = defang_text(URL.sub(LINK_REMOVED, clean_title(title, TITLE_LIMIT)))
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
_DOT_CHARS = "." + "".join(chr(code) for code in (0x3002, 0xFF0E, 0xFF61, 0xFE52, 0x2024))
_DOT = "[" + re.escape(_DOT_CHARS) + "]"
# A zero-width joiner or non-joiner right next to a dot ("evil<ZWJ>.com"): it is kept in a name
# (an emoji sequence or a Persian word needs it) but between a label and its dot it only hides the
# link from the checks, so the checks and the defanging look at the text without it.
_JOINERS_AT_DOTS = re.compile(rf"[\u200c\u200d]+(?={_DOT})|(?<={_DOT})[\u200c\u200d]+")
# A link written with a listed domain ("evil。com", "evil.com"), any dot character: used on running
# text (mail), where a dot look-alike is common punctuation (a CJK full stop) unless a listed TLD
# follows it.
_ANY_DOT_LINK = re.compile(
    rf"(?<![\w-])[\w-]+(?:{_DOT}[\w-]+)*{_DOT}(?:" + "|".join(LINK_TLDS) + r")(?![\w-])",
    re.IGNORECASE,
)


_JOINERS = re.compile("[\u200c\u200d]")


def tidy_dots(text: str) -> str:
    """`text` without the joiners that sit next to a dot (see `_JOINERS_AT_DOTS`)."""
    return _JOINERS_AT_DOTS.sub("", text)


def without_joiners(text: str) -> str:
    """`text` with EVERY zero-width joiner and non-joiner removed: the copy the link checks and the
    defanging look at, because a joiner inside the label or the top-level domain ("evil.c<ZWJ>om")
    draws nothing and splits the word for a regex while a person still reads "evil.com"."""
    return _JOINERS.sub("", text)


def defang_text(plain: str) -> str:
    """Running text for a mail or an alert with the dots of anything that could be linked written
    "[.]": a bare domain (`_DOMAINISH`) and a listed domain written with a dot look-alike or a
    joiner next to the dot ("evil。com", "evil<ZWJ>.com"). Dot look-alikes are not touched
    elsewhere: in CJK text they are ordinary full stops. The checks run on a copy without any
    joiner (`without_joiners`); the text keeps its joiners (an emoji sequence) unless that copy
    held something to defang."""

    def fang(text: str) -> str:
        text = _ANY_DOT_LINK.sub(lambda found: re.sub(_DOT, "[.]", found.group()), text)
        return _DOMAINISH.sub(lambda found: found.group().replace(".", "[.]"), text)

    bare = without_joiners(plain)
    fanged = fang(bare)
    return fanged if fanged != bare else fang(tidy_dots(plain))


# The only first labels left alone in a dotted name: a single letter (an initial, "J.Smith") or a
# title ("Dr.Smith"). Anything else with a dot and letters after it is defanged, in any case.
_NAME_TITLES = frozenset("dr mr mrs ms mx st jr sr prof sgt capt gen col rev hon fr".split())
_NAME_DOMAIN = re.compile(r"(?<![\w-])[\w-]+(?:\.[\w-]+)*\.[^\W\d_]{2,}(?![\w-])")


def defang_name_domains(name: str) -> str:
    """A typed NAME for the meeting chat, with the dots of anything that could be linked written
    "[.]": every `label.tld` token ("evil.click" and "Evil.Click" become "evil[.]click" and
    "Evil[.]Click"), except one dot after a single letter or a title with a capitalised word after
    it ("J.Smith", "Dr.Smith", "St.Mary"), which reads as an initial or an abbreviation. "x.click",
    "t.co", "dr.click" and "J.Co" are defanged. Dot look-alikes count as dots, and the check runs on
    a copy without any joiner (`without_joiners`): the name keeps its joiners (an emoji sequence)
    unless that copy held something to defang."""
    bare = without_joiners(name).translate(_DOT_LOOKALIKES)
    fanged = _defang_name(bare)
    return fanged if fanged != bare else _defang_name(tidy_dots(name).translate(_DOT_LOOKALIKES))


def _defang_name(plain: str) -> str:
    def swap(found: re.Match) -> str:
        token = found.group()
        first, _, rest = token.partition(".")
        # An initial or a title, ONE dot, and what follows starts with a capital and is not a listed
        # TLD: "J.Smith", "Dr.Smith". Never "x.click", "t.co", "dr.click" or "J.Co".
        abbreviation = (
            "." not in rest
            and (len(first) == 1 or first.lower() in _NAME_TITLES)
            and rest[:1].isupper()
            and rest.lower() not in LINK_TLDS
        )
        return token if abbreviation else token.replace(".", "[.]")

    return _NAME_DOMAIN.sub(swap, plain)


def looks_like_link(text: str) -> bool:
    """Whether a typed name reads as a web address: it holds `://`, starts a word with `www.`,
    is a link to a meeting host written bare, or is `label.tld` with a TLD from `LINK_TLDS`.
    Narrower than `mail_line`'s defanging on purpose (see `LINK_TLDS`). Checked on the text as typed
    and on a copy without any joiner (`without_joiners`), so "evil.c<ZWJ>om" is a link."""
    for seen in (tidy_dots(text), without_joiners(text)):
        seen = seen.translate(_DOT_LOOKALIKES)
        if URL.search(seen) or "://" in seen or _TLD_LINK.search(seen):
            return True
    return False


def mail_line(text: str | None, limit: int) -> str:
    """Words somebody said in a meeting (a task title) as one plain line for a mail: a web address
    is replaced by "[link removed]" and a bare domain has its dots written "[.]", because a mail
    client would link either. Cut at `limit` characters."""
    plain = defang_text(URL.sub(LINK_REMOVED, clean_title(text, limit * 2)))
    return clean_title(plain, limit)


def mail_title(meeting: Meeting) -> str:
    """`alert_name` for a stored meeting."""
    return alert_name(meeting.title, meeting.platform, meeting.native_id)


def event_title(text: str | None) -> str | None:
    """The title to store on a meeting made from a calendar event: cleaned, or None when nothing
    printable is left (the meeting then falls back to its platform and code)."""
    return clean_title(text) or None
