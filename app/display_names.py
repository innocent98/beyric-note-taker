"""The name a person is known by: `users.name`, "Your name" in Settings.

It is shown to teammates (team lists, shares, tasks) and said by the note taker in the meeting chat
("Hi, I'm ... <name> added me to record this meeting", `app/chat_notice.py`); without it the chat
line says "The host". Sign-up asks for it (`signup.sign_up`, up to 200 characters), Google sign-in
takes Google's name or the start of the address, and the owner's account made by migration 0006 has
none: until this route there was no screen to set or change it.

Cleaned like every other piece of user text (`app/titles.py`): one plain line, whitespace
collapsed, control, bidirectional and invisible format characters removed. Unlike a note taker name
it is cleaned, not refused, because it is never the bot's own name; but a name with nothing
visible left is refused, and so is an empty one: once a person has a name it cannot be blanked.
"""

import re

from app import titles
from app.bot_names import draws_something

NAME_LIMIT = 100  # the cut every screen already applies to a person's name
TOO_LONG = f"Use {NAME_LIMIT} characters or fewer."
EMPTY = "Enter your name."

_SPACES = re.compile(r"\s+")


class DisplayNameInvalid(ValueError):
    """The name was refused; the message is plain words for the person who typed it."""


def clean_display_name(raw: str) -> str:
    """The name to store. Raises `DisplayNameInvalid` for an empty name, a name with nothing visible
    in it, or one longer than `NAME_LIMIT` characters (counted after whitespace is collapsed)."""
    collapsed = _SPACES.sub(" ", str(raw)).strip()
    if len(collapsed) > NAME_LIMIT:
        raise DisplayNameInvalid(TOO_LONG)
    cleaned = titles.clean_title(collapsed, limit=NAME_LIMIT)
    if not any(draws_something(char) for char in cleaned):
        raise DisplayNameInvalid(EMPTY)
    return cleaned
