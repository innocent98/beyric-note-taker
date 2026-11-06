"""Linear (W10): the team's tracker. A pasted personal API key, one fixed host, one-way push.

The key is a bearer secret (it acts as the person who made it), so it lives in the vault (purpose
`integration`) and never leaves this module and `app/integrations.py`: not in a response, a log
line, an error or the OpenAPI file. The host is the constant `HOST`; the call goes through
`app/outbound.py` (resolve once, public addresses only, pinned IP, no redirects, timeouts, bounded
answer). Only fixed sentences ever reach a caller: Linear's own error text is not passed on.

What is sent for a task: its title, its due date, and a description with the owner's name and a
link to the meeting in this app. No transcript, no quote and no notes.
"""

import json
import re

from app import outbound, titles
from app.config import get_settings

HOST = "api.linear.app"
PATH = "/graphql"
MAX_ANSWER_BYTES = 64 * 1024
MAX_TEAMS = 50
TITLE_CHARS = 200
# A personal key is `lin_api_` and letters, digits and underscores.
KEY = re.compile(r"^lin_api_[A-Za-z0-9_]{16,80}$")
ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")

REASONS = ("auth", "rate", "rejected", "timeout", "unreachable", "refused", "no_team")
TEXT = {
    "auth": "Linear did not accept that key. Create a new personal API key in Linear and paste it.",
    "rate": "Linear asked us to slow down. The next meeting will sync again.",
    "rejected": "Linear refused the request. Check that the key can create issues in that team.",
    "timeout": "Linear took too long to answer.",
    "unreachable": "We could not reach Linear.",
    "refused": "We could not send to that address.",
    "no_team": "That team is not one this key can use.",
}


class LinearError(Exception):
    """A call failed. Carries only a reason from `REASONS`."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason if reason in REASONS else "unreachable"


class KeyInvalid(Exception):
    """The pasted text is not a Linear personal API key."""


def check_key(value: object) -> str:
    key = value.strip() if isinstance(value, str) else ""
    if not KEY.match(key):
        raise KeyInvalid
    return key


def _call(key: str, query: str, variables: dict, *, resolver=None, transport=None) -> dict:
    extra = {} if resolver is None else {"resolver": resolver}
    try:
        status, body = outbound.post_json(
            HOST,
            PATH,
            {"query": query, "variables": variables},
            headers={"Authorization": key},
            max_bytes=MAX_ANSWER_BYTES,
            transport=transport,
            **extra,
        )
    except outbound.OutboundError as exc:
        raise LinearError(exc.reason if exc.reason in REASONS else "unreachable") from None
    if status in (401, 403):
        raise LinearError("auth")
    if status == 429:
        raise LinearError("rate")
    try:
        data = json.loads(body)
    except ValueError:
        raise LinearError("rejected") from None
    if not isinstance(data, dict):
        raise LinearError("rejected")
    errors = data.get("errors")
    if errors:
        codes = {
            str(((e or {}).get("extensions") or {}).get("code", ""))
            for e in errors
            if isinstance(e, dict)
        }
        if "AUTHENTICATION_ERROR" in codes:
            raise LinearError("auth")
        raise LinearError("rate" if "RATELIMITED" in codes else "rejected")
    if status != 200 or not isinstance(data.get("data"), dict):
        raise LinearError("rejected")
    return data["data"]


TEAMS = f"query {{ teams(first: {MAX_TEAMS}) {{ nodes {{ id key name }} }} }}"
ISSUE = (
    "mutation($input: IssueCreateInput!) { issueCreate(input: $input) "
    "{ success issue { id identifier url } } }"
)


def teams(key: str, **kw) -> list[dict]:
    """The teams the key can reach, as `{"id", "key", "name"}` (names cleaned: they are shown)."""
    nodes = (_call(key, TEAMS, {}, **kw).get("teams") or {}).get("nodes") or []
    found = []
    for node in nodes if isinstance(nodes, list) else []:
        if isinstance(node, dict) and ID.match(str(node.get("id", ""))):
            found.append(
                {
                    "id": node["id"],
                    "key": titles.clean_title(str(node.get("key", "")), 10),
                    "name": titles.clean_title(str(node.get("name", "")), 100),
                }
            )
    return found


def description(owner: str | None, meeting_id: int, meeting_name: str) -> str:
    """Plain text for the issue body: no markdown from a transcript can survive `mail_line`
    (addresses removed) and the link is ours."""
    base = get_settings().public_base_url.rstrip("/")
    who = titles.mail_line(owner, 60) if owner else "needs an owner"
    name = titles.mail_line(meeting_name, 80) or "A meeting"
    return f"Owner in the meeting: {who}\n\nFrom {name}: {base}/meetings/{int(meeting_id)}"


def create_issue(
    key: str,
    team_id: str,
    title: str,
    body: str,
    due: str | None,
    **kw,
) -> dict:
    """Create one issue; returns `{"id", "identifier", "url"}`."""
    issue_input = {
        "teamId": team_id,
        "title": titles.mail_line(title, TITLE_CHARS),
        "description": body,
    }
    if due:
        issue_input["dueDate"] = due
    data = _call(key, ISSUE, {"input": issue_input}, **kw).get("issueCreate") or {}
    issue = data.get("issue") or {}
    if not data.get("success") or not ID.match(str(issue.get("id", ""))):
        raise LinearError("rejected")
    return {
        "id": issue["id"],
        "identifier": titles.clean_title(str(issue.get("identifier", "")), 30),
        "url": str(issue.get("url", ""))[:300]
        if str(issue.get("url", "")).startswith("https://linear.app/")
        else "",
    }
