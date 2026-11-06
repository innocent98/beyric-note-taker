"""The HTTP plumbing every calendar source shares (lifted out of `app/gcal_client.py` so a second
provider does not copy it).

What it fixes for every caller: one client per call with a timeout on all four stages, no proxy
read from the environment, redirects never followed. A transport failure is reported by its
exception type only (the exception text can hold the URL), and a body that is not a JSON object is
a `TransientError`. The `who` argument names the provider in the message ("Google", "Microsoft");
no message carries a token, a URL or event data.
"""

import httpx

from app.calendar_source import TransientError

TIMEOUT = httpx.Timeout(connect=5.0, read=15.0, write=10.0, pool=5.0)


def client(transport: httpx.BaseTransport | None = None) -> httpx.Client:
    return httpx.Client(
        timeout=TIMEOUT, transport=transport, trust_env=False, follow_redirects=False
    )


def send(http: httpx.Client, who: str, method: str, url: str, **kwargs) -> httpx.Response:
    try:
        return http.request(method, url, **kwargs)
    except httpx.HTTPError as exc:
        # The exception text can hold the URL; only its type is kept.
        raise TransientError(f"{who} request failed ({type(exc).__name__})") from None


def json_object(response: httpx.Response, who: str) -> dict:
    try:
        body = response.json()
    except ValueError:
        raise TransientError(f"{who} answered with something that is not JSON") from None
    if not isinstance(body, dict):
        raise TransientError(f"{who} answered with an unexpected JSON shape")
    return body
