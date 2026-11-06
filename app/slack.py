"""The Slack incoming webhook (W10): one post per meeting, with the new tasks and their owners.

The webhook URL is a bearer secret (whoever holds it can write in the channel), so it is stored
in the vault (`app/vault.py`, purpose `integration`) and never shown, logged or returned. This
module is the only place that decrypts it and the only place that sends it anywhere.

Outbound hygiene, in the order it runs:

1. The URL is checked when it is pasted: `https`, exactly `hooks.slack.com`, standard port, no
   userinfo, query or fragment, and the path shape Slack hands out (`/services/T../B../token`).
   The host is a constant here, not a setting and not read from the stored value, so no stored
   text can point a post anywhere else.
2. The host is resolved once, every answer must be a public address (the same checks the calendar
   link fetch uses), and the connection goes to that address with the real name as SNI and
   `Host`.
3. No redirect is followed, proxy variables are ignored, 3 s to connect, 10 s in all (the call
   runs in a thread the caller stops waiting for).
4. Only the status code of the answer is read; its text never reaches a log, an error or the
   caller.

What is sent is plain text built from the meeting's name and its tasks, each cleaned by
`app/titles.py` (web addresses removed, bare domains defanged) and then escaped for Slack, so
nothing in a transcript can make a mention (`<!channel>`), a link or formatting.
"""

import logging
import re
import socket
import threading
from urllib.parse import urlsplit

from app import ics_fetch, outbound, titles
from app.config import get_settings

logger = logging.getLogger(__name__)

HOST = "hooks.slack.com"
HTTPS_PORT = 443
TOTAL_TIMEOUT = 10.0
USER_AGENT = "BeyricNoteTaker/1 (slack webhook)"
MAX_URL_CHARS = 200
MAX_TASKS_SHOWN = 15
TASK_CHARS = 120
NAME_CHARS = 60
MESSAGE_CHARS = 3000
# /services/T<workspace>/B<hook>/<token>: the shape of every incoming webhook Slack issues.
PATH = re.compile(r"^/services/T[A-Z0-9]{6,20}/B[A-Z0-9]{6,20}/[A-Za-z0-9]{16,40}$")
_MARKUP = re.compile(r"[*_~`|]")

# What the card shows (fixed sentences: the other service's own words are never repeated).
REASONS = ("rejected", "gone", "rate", "timeout", "unreachable", "refused")
TEXT = {
    "rejected": "Slack did not accept that webhook. Copy the whole URL from the Slack app again.",
    "gone": "Slack no longer accepts this webhook. It may have been removed, so connect it again.",
    "rate": "Slack asked us to slow down. The next meeting will post again.",
    "timeout": "Slack took too long to answer.",
    "unreachable": "We could not reach Slack.",
    "refused": "We could not send to that address.",
}


class SlackError(Exception):
    """A post failed. Carries only a reason from `REASONS`."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason if reason in REASONS else "unreachable"


class WebhookInvalid(Exception):
    """The pasted text is not a Slack incoming-webhook URL."""


def check_webhook(url: object) -> str:
    """The webhook URL, trimmed, or `WebhookInvalid`. Nothing about the text is echoed."""
    value = url.strip() if isinstance(url, str) else ""
    if not value or len(value) > MAX_URL_CHARS or any(c.isspace() or ord(c) < 32 for c in value):
        raise WebhookInvalid
    try:
        parts = urlsplit(value)
        host, port = (parts.hostname or "").lower(), parts.port
    except ValueError:
        raise WebhookInvalid from None
    if (
        parts.scheme != "https"
        or host != HOST
        or port not in (None, HTTPS_PORT)
        or "@" in parts.netloc
        or parts.query
        or parts.fragment
        or not PATH.match(parts.path)
    ):
        raise WebhookInvalid
    return f"https://{HOST}{parts.path}"


def escape(text: str | None, limit: int) -> str:
    """One plain line for Slack's `mrkdwn`: cleaned, web addresses removed, `& < >` escaped (so
    `<!channel>`, `<@U123>` and `<https://...|link>` stay literal), formatting marks dropped."""
    plain = _MARKUP.sub("", titles.mail_line(text, limit))
    return plain.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def message(meeting_id: int, meeting_name: str, lines: list[tuple[str, str | None]]) -> str:
    """The post: the meeting as a link to its page, then one bullet per task with its owner.
    `lines` are (title, owner or None)."""
    base = get_settings().public_base_url.rstrip("/")
    name = escape(meeting_name, NAME_CHARS) or "A meeting"
    count = len(lines)
    head = f"*<{base}/meetings/{int(meeting_id)}|{name}>*: {count} new task{'s' * (count != 1)}"
    body = []
    for title, owner in lines[:MAX_TASKS_SHOWN]:
        who = escape(owner, 40) if owner else "needs an owner"
        body.append(f"• {escape(title, TASK_CHARS)} ({who})")
    if count > MAX_TASKS_SHOWN:
        body.append(f"…and {count - MAX_TASKS_SHOWN} more")
    return "\n".join([head, *body])[:MESSAGE_CHARS]


def test_message() -> str:
    return "Beyric Note Taker is connected. New tasks from your team's meetings will appear here."


def _send(url: str, text: str, resolver, transport) -> None:
    payload = {"text": text, "unfurl_links": False, "unfurl_media": False, "link_names": False}
    try:
        status, _ = outbound.post_json(
            HOST,
            urlsplit(url).path,
            payload,
            resolver=resolver,
            transport=transport,
            max_bytes=1024,
        )
    except outbound.OutboundError as exc:
        raise SlackError(exc.reason if exc.reason in REASONS else "unreachable") from None
    if status == 200:
        return
    if status in (404, 410):
        raise SlackError("gone")
    if status == 429:
        raise SlackError("rate")
    raise SlackError("rejected")  # a redirect (3xx) lands here too: it is never followed


def post(url: str, text: str, *, resolver=socket.getaddrinfo, transport=None) -> None:
    """Send `text` to the webhook `url`, or raise `SlackError`. `url` must have passed
    `check_webhook`. `resolver` and `transport` are for tests."""
    outcome: dict = {}

    def work() -> None:
        # httpx logs "POST <url>" at INFO and the path of a webhook is its secret.
        ics_fetch._quiet.active.on = True
        try:
            _send(check_webhook(url), text, resolver, transport)
        except SlackError as exc:
            outcome["error"] = exc
        except WebhookInvalid:
            outcome["error"] = SlackError("rejected")
        except Exception as exc:  # the type only: an HTTP library error can quote the URL
            logger.error("slack post failed unexpectedly type=%s", type(exc).__name__)
            outcome["error"] = SlackError("unreachable")
        finally:
            ics_fetch._quiet.active.on = False

    worker = threading.Thread(target=work, name="slack-post", daemon=True)
    worker.start()
    worker.join(TOTAL_TIMEOUT)
    if worker.is_alive():
        raise SlackError("timeout")
    if "error" in outcome:
        raise outcome["error"]
