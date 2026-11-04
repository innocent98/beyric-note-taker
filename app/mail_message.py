"""What goes to a mail provider: a `Message`, its attachments, and the checks that keep a name,
a title or an address from changing the mail around it.

A `Message` refuses to exist when a line field holds a line break or other control character
(subject, address, reply-to, header), when an address is not one plain address, when an
attachment is not on the short list of types we send, or when the attachments together are
bigger than `MAX_ATTACHMENT_BYTES`. Mailers therefore never see a malformed message, whatever
built it. Its `repr` leaves out the body and the file contents, so logging a message by accident
does not log meeting notes.
"""

import re
import unicodedata
from dataclasses import dataclass, field

# Resend accepts 40 MB per mail after Base64, so about 30 MB of files (spike ML1,
# docs/spike/resend-limits-2026-11-04.md). We stay well under it: mailboxes refuse mail much
# smaller than a provider does (Gmail 25 MB, Outlook 20 MB, both counted after encoding).
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_ATTACHMENTS = 3
ATTACHMENT_TYPES = frozenset(
    {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "text/plain",
    }
)
MAX_ADDRESS_CHARS = 254

# One address, nothing else: no name part, no list, no quoting, no whitespace or control
# character. International addresses are allowed: only the characters that could end the address
# or start another header are not.
_ADDRESS = re.compile(r"[^\s<>(),;:\\\"\[\]@]+@[^\s<>(),;:\\\"\[\]@]+")
_HEADER_NAME = re.compile(r"[A-Za-z][A-Za-z0-9-]{0,63}")
_INVISIBLE = ("Cc", "Cf", "Zl", "Zp")  # controls, bidi and zero-width marks, line separators


def has_control(text: str) -> bool:
    """Whether `text` holds a control, format (bidi, zero-width) or line-separator character."""
    return any(unicodedata.category(c) in _INVISIBLE for c in text)


def valid_address(address: str) -> bool:
    return (
        isinstance(address, str)
        and len(address) <= MAX_ADDRESS_CHARS
        and not has_control(address)
        and _ADDRESS.fullmatch(address) is not None
    )


def clean_line(text: str, limit: int) -> str:
    """`text` as one line that is safe in a subject or a greeting: invisible and control
    characters removed, runs of white space made one space, at most `limit` characters (an
    ellipsis marks a cut)."""
    kept = "".join(
        " " if c.isspace() else c
        for c in text
        if c.isspace() or unicodedata.category(c) not in _INVISIBLE
    )
    kept = " ".join(kept.split())
    if len(kept) > limit:
        kept = kept[: max(limit - 1, 0)].rstrip() + "…"
    return kept


class MailError(Exception):
    """A send that failed. `permanent` is True when trying again cannot help (a 4xx other than
    408 and 429: a bad address, a bad key). The text never holds provider detail."""

    def __init__(self, text: str, *, permanent: bool) -> None:
        super().__init__(text)
        self.permanent = permanent


@dataclass(frozen=True)
class Attachment:
    filename: str
    content: bytes = field(repr=False)
    content_type: str

    def __post_init__(self) -> None:
        if (
            not self.filename
            or has_control(self.filename)
            or re.search(r'[/\\:*?"<>|]', self.filename)
        ):
            raise ValueError("attachment file name")
        if self.content_type not in ATTACHMENT_TYPES:
            raise ValueError("attachment type")
        if not self.content:
            raise ValueError("attachment is empty")


@dataclass(frozen=True)
class Message:
    to: str
    subject: str
    text: str = field(repr=False)
    html: str = field(repr=False)
    attachments: tuple[Attachment, ...] = ()
    reply_to: str | None = None
    # Extra headers as (name, value) pairs, for example a `List-Unsubscribe`.
    headers: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not valid_address(self.to):
            raise ValueError("recipient address")
        if has_control(self.subject):
            raise ValueError("subject")
        if self.reply_to is not None and not valid_address(self.reply_to):
            raise ValueError("reply-to address")
        for name, value in self.headers:
            if not _HEADER_NAME.fullmatch(name) or has_control(value):
                raise ValueError("header")
        if len(self.attachments) > MAX_ATTACHMENTS:
            raise ValueError("too many attachments")
        if sum(len(a.content) for a in self.attachments) > MAX_ATTACHMENT_BYTES:
            raise ValueError("attachments too large")
