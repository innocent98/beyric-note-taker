"""The one outbound JSON POST the integrations use (Slack, Linear), with the SSRF defences of the
calendar link fetch (`app/ics_fetch.py`): the caller names a FIXED host (a constant in its own
module, never read from stored or typed text), the host is resolved once, every address in the
answer must be public, the connection goes to that address with the real name as SNI and `Host`,
no redirect is followed, proxy variables are ignored, 3 s to connect, 10 s in all (the call runs in
a thread the caller stops waiting for), and the body is read as a stream and cut at `max_bytes`.

Nothing derived from the path, the headers or the library's exception text reaches an error or a
log line: a failure is an `OutboundError` with a reason only. httpx logs "POST <url>" at INFO and a
path can be a secret, so httpx's own records are dropped for the worker thread.
"""

import socket
import threading
import time

import httpx

from app import ics_fetch

HTTPS_PORT = 443
CONNECT_TIMEOUT = 3.0
READ_TIMEOUT = 5.0
TOTAL_TIMEOUT = 10.0
USER_AGENT = "BeyricNoteTaker/1 (integration)"
REASONS = ("timeout", "unreachable", "refused", "too_large")


class OutboundError(Exception):
    """The call failed before an answer, or the answer was unusable. Carries only a reason."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason if reason in REASONS else "unreachable"


def _send(host, path, payload, headers, max_bytes, resolver, transport) -> tuple[int, bytes]:
    try:
        addresses = ics_fetch._resolve(host, resolver)
    except ics_fetch.IcsRejected as exc:
        raise OutboundError(
            "refused" if exc.reason == "private_address" else "unreachable"
        ) from None
    deadline = time.monotonic() + TOTAL_TIMEOUT
    timeout = httpx.Timeout(READ_TIMEOUT, connect=CONNECT_TIMEOUT)
    sent = {
        "Host": host,
        "User-Agent": USER_AGENT,
        "Content-Type": "application/json",
        # httpx would otherwise ask for gzip, and the raw body is read (its size is cut before any
        # decompression could inflate it).
        "Accept-Encoding": "identity",
        **headers,
    }
    with httpx.Client(
        transport=transport, timeout=timeout, follow_redirects=False, trust_env=False
    ) as client:
        for index, ip in enumerate(addresses):
            if time.monotonic() > deadline:
                raise OutboundError("timeout")
            literal = f"[{ip.compressed}]" if ip.version == 6 else ip.compressed
            try:
                request = client.build_request(
                    "POST",
                    f"https://{literal}{path}",
                    json=payload,
                    headers=sent,
                    extensions={"sni_hostname": host},
                )
                response = client.send(request, stream=True)
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                if index + 1 < len(addresses):
                    continue  # the other address of the same validated answer, never a new lookup
                raise OutboundError(
                    "timeout" if isinstance(exc, httpx.ConnectTimeout) else "unreachable"
                ) from None
            except httpx.TimeoutException:
                raise OutboundError("timeout") from None
            except (httpx.HTTPError, httpx.InvalidURL, OSError):
                raise OutboundError("unreachable") from None
            break
        try:
            body = bytearray()
            # (a test transport answers with the body already read)
            chunks = [response.content] if response.is_stream_consumed else response.iter_raw()
            for chunk in chunks:
                body += chunk
                if len(body) > max_bytes or time.monotonic() > deadline:
                    raise OutboundError("too_large" if len(body) > max_bytes else "timeout")
            status = response.status_code
        except httpx.TimeoutException:
            raise OutboundError("timeout") from None
        except httpx.HTTPError:
            raise OutboundError("unreachable") from None
        finally:
            response.close()
    return status, bytes(body)


def post_json(
    host: str,
    path: str,
    payload: dict,
    *,
    headers: dict | None = None,
    max_bytes: int = 65536,
    resolver=socket.getaddrinfo,
    transport=None,
) -> tuple[int, bytes]:
    """POST `payload` as JSON to `https://host/path` and return `(status, body)`. A 3xx is returned
    as a status, never followed. The request asks for `identity`, so the body is not compressed."""
    outcome: dict = {}

    def work() -> None:
        ics_fetch._quiet.active.on = True
        try:
            outcome["result"] = _send(
                host, path, payload, headers or {}, max_bytes, resolver, transport
            )
        except OutboundError as exc:
            outcome["error"] = exc
        except Exception:  # the type is not even logged: the message could quote the path
            outcome["error"] = OutboundError("unreachable")
        finally:
            ics_fetch._quiet.active.on = False

    worker = threading.Thread(target=work, name="outbound-post", daemon=True)
    worker.start()
    worker.join(TOTAL_TIMEOUT)
    if worker.is_alive():
        raise OutboundError("timeout")
    if "error" in outcome:
        raise outcome["error"]
    return outcome["result"]
