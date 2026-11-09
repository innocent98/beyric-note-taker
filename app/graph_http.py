"""The one place a Microsoft Graph URL is built and the one way a Graph call is sent.

What it fixes for every Graph call (the connect probe in `app/microsoft_connect.py` and, in M4, the
event reads): the URL is `GRAPH_BASE` plus a path this code wrote, never a host, scheme or path
taken from an answer or from event data; the `Authorization` header goes to that host and nowhere
else; redirects are not followed and no proxy is read from the environment (`calendar_http`); and a
transport failure is reported by its exception type only. A caller that must follow a URL Graph
sent (`@odata.nextLink`) first passes it through `under_graph_base`.
"""

import httpx

from app import calendar_http

GRAPH_BASE = "https://graph.microsoft.com/v1.0"


def under_graph_base(url: object) -> bool:
    """True only for a URL that starts with `GRAPH_BASE/` or is exactly `GRAPH_BASE` plus a path
    or query, with no userinfo trick (`https://graph.microsoft.com@evil/`), backslash or control
    character in it. A `nextLink` that fails this is never followed."""
    if not isinstance(url, str) or not url.startswith(GRAPH_BASE + "/"):
        return False
    return not any(ch.isspace() or ord(ch) < 32 or ch == "\\" for ch in url)


def graph_url(path: str) -> str:
    """`GRAPH_BASE` plus `path` (which must start with one slash and name no other host)."""
    if not path.startswith("/") or path.startswith("//") or "://" in path or "\\" in path:
        raise ValueError("a Graph path starts with one slash and names no host")
    return GRAPH_BASE + path


def get(
    access_token: str,
    url: str,
    *,
    params: dict | None = None,
    headers: dict[str, str] | None = None,
    transport: httpx.BaseTransport | None = None,
) -> httpx.Response:
    """One authorised GET. `url` must be a Graph URL (`graph_url(...)` or a vetted `nextLink`);
    anything else is a bug and raises before a request is made, so the token cannot leave."""
    if not under_graph_base(url):
        raise ValueError("not a Graph URL")
    with calendar_http.client(transport) as http:
        return calendar_http.send(
            http,
            "Microsoft",
            "GET",
            url,
            params=params,
            headers={**(headers or {}), "Authorization": f"Bearer {access_token}"},
        )
