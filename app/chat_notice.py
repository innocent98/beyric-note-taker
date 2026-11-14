"""The line the bot posts in the meeting's chat when it joins (checklist C18).

The words are the owner's, approved 2026-11-14: "Hi, I'm {bot name}. {owner name} added me to record
this meeting and write notes. If you'd rather not be recorded, please tell {owner name} before we
start." It tells the other people who the bot is and who asked for it, and gives them a way to say
no.

**Skribby only, and off by default.** Skribby's create-bot page has the optional string parameter
`initial_chat_message` ("Whenever the bot joins the meeting, it'll send this message to the chat").
The page states no length limit and no platform list: Meet, Zoom and Teams support is a vendor claim
nobody has seen live (`docs/sop/2026-11-14-bot-chat-notice.md`, "how to prove it live"). So the
whole feature sits behind `BOT_CHAT_NOTICE_ENABLED` (default false), a provider that does not take a
notice (Vexa) is never given one, and if Skribby refuses a create that carries it the create is made
once more without it (`SkribbyClient.create`): a bad parameter must not stop a bot from being sent.

Both names are typed by somebody (the owner's display name, the `SKRIBBY_BOT_NAME` setting) and end
up in a chat everyone in the meeting reads, so each is cleaned like any other text we put in
front of other people (`app.titles.mail_line`: one plain line, a web address replaced, a bare domain
defanged, cut) and cut short, which also bounds the whole message.
"""

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import User
from app.titles import clean_title, defang_name_domains, mail_line

logger = logging.getLogger(__name__)

NAME_LIMIT = 60  # characters of each name; the whole line then stays under about 250
FALLBACK_OWNER = "the host"


def notice_bot_name(settings: Settings, provider: str = "skribby") -> str:
    """The name the notice gives the bot when the caller names none: the server default. A meeting's
    own name comes from `app.bot_names.shown_name`, passed to `send_options`."""
    return settings.bot_name_for(provider)


def notice_text(bot_name: str | None, owner_name: str | None) -> str:
    """The approved line, with both names cleaned and cut. An empty owner name is "the host"; an
    empty bot name cannot happen with a real setting, but the line would still read ("Hi, I'm the
    note taker.")."""
    # The bot's name was checked when it was set (`app.bot_names`: no web address, no hidden
    # characters) or is the operator's own. The wide mail defanging would print "Dr[.]Smith", so the
    # bot's name is made one plain line, cut, and only a lower-case `label.tld` token has its dots
    # written "[.]" (`defang_name_domains`): a domain the check let through (`evil.click`, not on
    # its short list) must not reach everyone's chat as a link. The OWNER's name (any display name)
    # still gets the full treatment.
    bot = defang_name_domains(clean_title(bot_name, NAME_LIMIT)) or "the note taker"
    owner = mail_line(owner_name, NAME_LIMIT)
    # "The host" begins the second sentence, so it needs its capital; a real name is left as typed.
    first = owner or FALLBACK_OWNER.capitalize()
    owner = owner or FALLBACK_OWNER
    return (
        f"Hi, I'm {bot}. {first} added me to record this meeting and write notes. "
        f"If you'd rather not be recorded, please tell {owner} before we start."
    )


def send_options(
    session: Session | None,
    provider,
    owner_user_id: int,
    settings: Settings,
    bot_name: str | None = None,
    fallback_bot_name: str | None = None,
) -> dict[str, Any]:
    """`{"chat_notice": "<the line>"}` for `provider.send_bot`, or nothing: only with
    `BOT_CHAT_NOTICE_ENABLED`, only for a provider that takes one (`accepts_chat_notice`,
    Skribby's). Reading the owner's name can fail without stopping the send: the line then says
    "the host". `bot_name` is the name the host sees for this meeting's bot (`app.bot_names.
    shown_name`); without one the server default is used. `fallback_bot_name` is the name the host
    sees if the person's own name is refused and the send is made again with the default: when it
    differs, the same line built with it comes as `chat_notice_fallback`, so the consent line never
    names a bot that did not join (`SkribbyClient.create`)."""
    if not settings.bot_chat_notice_enabled or not getattr(
        type(provider), "accepts_chat_notice", False
    ):
        return {}
    name = None
    if session is not None:
        try:
            # A savepoint: a failed read must not abort the caller's transaction (`place_bot` holds
            # the placement lock and the slot in it).
            with session.begin_nested():
                name = session.scalar(select(User.name).where(User.id == owner_user_id))
        except Exception:  # a database hiccup must not stop the bot from being sent
            logger.warning("the owner's name could not be read for the chat notice")
    shown = bot_name or notice_bot_name(settings)
    options = {"chat_notice": notice_text(shown, name)}
    if fallback_bot_name and fallback_bot_name != shown:
        options["chat_notice_fallback"] = notice_text(fallback_bot_name, name)
    return options
