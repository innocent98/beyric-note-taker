"""The name the note taker joins a meeting with: one place that decides it.

Each person may set their own ("Note taker name" in Settings, `users.bot_name`); empty means the
server's default (`Settings.skribby_bot_name`). The name that was ACTUALLY sent is stored on the
meeting when the bot is created (`meetings.bot_name`), because three later steps must know which
name this bot joined with even after the person changes the setting: the speaker naming by
elimination (`app/skribby.py`), the owner's "Admit '...'" line (`app/services.py`) and the line the
bot posts in the meeting chat (`app/chat_notice.py`). A null on a meeting means the server default
(every meeting made before migration 0047, and any meeting of a provider that takes no name).

Two different questions, kept apart on purpose:

* `sent_name` is what we put in Skribby's `bot_name`. It is the person's name, else the default.
* `shown_name` is what the HOST will see in the participant list, which is only the same thing when
  the name is known to apply. On Google Meet the bot signs in as a Google account
  (`SKRIBBY_ACCOUNT_ID`), and the earlier leads recorded that Skribby ignores `bot_name` once the
  bot is signed in. Skribby's own pages do not say (see `docs/sop/2026-11-14-per-user-bot-name.md`),
  and nobody has tested it live, so the "Admit" line keeps naming the account's own name on a signed
  in Meet bot until `BOT_NAME_APPLIES_WHEN_SIGNED_IN` is turned on after the live check. Zoom and
  Teams bots always join as guests (`SkribbyClient.payload` authenticates Meet only), so there the
  name is the one we sent.

Validation is server-side and refuses rather than quietly rewriting, except for whitespace (trimmed
and collapsed, so a pasted name with a newline is one line). The rules are in `clean_bot_name`.
"""

import re
import unicodedata

from app import titles
from app.config import Settings

# Teams' limit (strictly enforced; Meet allows 60 and Zoom 64). Skribby cuts a longer name at the
# last space before the limit, so a shorter limit here keeps what the person typed intact on every
# platform.
NAME_LIMIT = 50

PROVIDERS_WITH_A_NAME = ("skribby",)

_SPACES = re.compile(r"\s+")
# One character on each side of an at sign: "alex@acme.com" and "alex@acme" alike.
_EMAIL = re.compile(r"\S@\S")

# Characters that draw nothing but are not format characters (`titles.clean_title` keeps them):
# a name made only of these (and spaces) would be a blank name in the participant list.
_DRAWS_NOTHING = frozenset("\u2800\u034f\u115f\u1160\u3164\uffa0\u180e\u200c\u200d")

# Words that make Google Meet and Zoom more likely to refuse a bot (Skribby's bot-naming guide:
# names with "Bot", "Notetaker", "Recording", "Transcription" and the like raise name detection).
# A SOFT warning only: the name still saves. Whole words, any case.
_RECORDER_WORDS = re.compile(
    r"(?<![\w])(?:bot|notetaker|note\s*taker|recording|transcription|ai)(?![\w])", re.IGNORECASE
)
RECORDER_WARNING = "Google Meet and Zoom may refuse bots with names like this."

TOO_LONG = f"Use {NAME_LIMIT} characters or fewer."
HAS_JUNK = (
    "Use plain letters, numbers and punctuation only: that name has hidden or control characters."
)
HAS_EMAIL = "Don't put an email address in the name."
HAS_LINK = "Don't put a web address in the name."


class BotNameInvalid(ValueError):
    """The name was refused; the message is plain words for the person who typed it."""


def clean_bot_name(raw: str | None) -> str | None:
    """The name to store: trimmed, inner whitespace collapsed to single spaces. None when nothing is
    left (clearing the setting). Raises `BotNameInvalid` for a name that is longer than
    `NAME_LIMIT`, holds a control or bidirectional character (anything `titles.clean_title` would
    strip: a name that reorders the text around it, or hides a character), or looks like an email
    address or a web address."""
    if raw is None:
        return None
    collapsed = _SPACES.sub(" ", str(raw)).strip()
    if not collapsed:
        return None
    # `clean_title` removes what must never reach another person's screen; if it removed anything
    # the name was not plain, and we say so instead of storing something the person did not type.
    cleaned = titles.clean_title(collapsed, limit=len(collapsed) + 1)
    if cleaned != collapsed:
        raise BotNameInvalid(HAS_JUNK)
    if not any(_draws_something(char) for char in collapsed):
        raise BotNameInvalid(HAS_JUNK)
    if len(collapsed) > NAME_LIMIT:
        raise BotNameInvalid(TOO_LONG)
    if _EMAIL.search(collapsed):
        raise BotNameInvalid(HAS_EMAIL)
    if titles.looks_like_link(collapsed):
        raise BotNameInvalid(HAS_LINK)
    return collapsed


def _draws_something(char: str) -> bool:
    """Whether `char` shows in a participant list: not a space, not a bare combining mark or
    variation selector, not one of the characters that draw nothing."""
    if char in _DRAWS_NOTHING or char.isspace():
        return False
    return unicodedata.category(char) not in ("Mn", "Me", "Mc") and not "\ufe00" <= char <= "\ufe0f"


def recorder_warning(name: str | None) -> str | None:
    """The soft warning for a stored name that sounds like a recorder, else None."""
    return RECORDER_WARNING if name and _RECORDER_WORDS.search(name) else None


def default_name(settings: Settings, provider: str | None) -> str:
    """The name used when the person set none: Skribby's guest name, or Vexa's for a meeting Vexa
    runs (Vexa takes no per-user name)."""
    provider = provider or settings.bot_provider
    return settings.skribby_bot_name if provider == "skribby" else settings.bot_name


def takes_name(provider: str | None) -> bool:
    """Whether the provider is sent a per-meeting name (Skribby's `bot_name`)."""
    return provider in PROVIDERS_WITH_A_NAME


def bot_name_for_user(settings: Settings, user, provider: str | None) -> str:
    """The name to SEND for this person's bot: their own when set and the provider takes one, else
    the server default. `user` may be None (the default is returned)."""
    own = getattr(user, "bot_name", None)
    if own and takes_name(provider or settings.bot_provider):
        return own
    return default_name(settings, provider)


def stored_name(settings: Settings, user, provider: str | None) -> str | None:
    """What goes in `meetings.bot_name`: the name sent, or None for a provider that takes no
    name (so a Vexa meeting never claims a name Vexa did not get)."""
    if not takes_name(provider or settings.bot_provider):
        return None
    return bot_name_for_user(settings, user, provider)


def shown_name(
    settings: Settings, *, provider: str | None, platform: str | None, stored: str | None
) -> str:
    """The name the host sees in the participant list, for the owner's "Admit '...'" line and the
    chat notice. `stored` is `meetings.bot_name` (None = the server default). See the module doc for
    why a signed-in Google Meet bot keeps showing the account's own name."""
    provider = provider or settings.bot_provider
    if provider != "skribby":
        return settings.bot_name
    name = stored or settings.skribby_bot_name
    signed_in = bool(settings.skribby_account_id) and platform == "google_meet"
    if signed_in and not settings.bot_name_applies_when_signed_in:
        return settings.skribby_account_name.strip() or name
    return name


def own_names(settings: Settings, stored: str | None) -> tuple[str, ...]:
    """Every name the bot goes by, for the speaker naming by elimination: never a speaker. The
    meeting's own stored name (null = the default) plus the account's own name."""
    names = (stored or settings.skribby_bot_name, settings.skribby_account_name)
    return tuple(dict.fromkeys(n for n in names if n and n.strip()))
