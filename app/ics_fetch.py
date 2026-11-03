"""Fetches a pasted calendar link (an ICS feed) without letting it reach anything internal.

The link is a bearer secret (anyone holding it can read the calendar) and the address is typed by
a user, so this is an SSRF surface. The defences, in the order they run:

1. `https` only, standard port, no userinfo, no control characters.
2. The host must be in `ICS_ALLOWED_HOSTS` (exact, or a listed `.suffix` / `*.suffix` entry for
   its subdomains). Only an admin edits that list, through the environment, never a user.
3. The host is resolved ONCE. If any address is not a public one (loopback, private, link-local,
   the cloud metadata addresses, multicast, reserved, shared, IPv4-mapped / NAT64 / 6to4 / Teredo
   wrappers of those) the whole answer is refused.
4. The connection goes to the resolved IP, with the original `Host` header and SNI (so the
   certificate is still checked against the real name). A second DNS answer cannot redirect it.
5. No redirect is followed, proxy environment variables are ignored, the body is read as a
   stream and abandoned at 2 MB (measured after decompression), with a 3 s connect and a 10 s
   total limit, and it must start with `BEGIN:VCALENDAR`.

A failure is an `IcsRejected` with a short reason; nothing derived from the URL (path, query, the
raw exception text from the HTTP library) reaches an error, a log line or the caller. The only
thing ever logged is the reason and the host.
"""

import ipaddress
import logging
import socket
import threading
import time
import zlib
from urllib.parse import urlsplit

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)


class _QuietDuringFetch(logging.Filter):
    """httpx logs `HTTP Request: GET <full url>` at INFO, and the path of a pasted calendar link is
    the secret. While this thread is inside `fetch_ics`, drop httpx's own records. (httpcore's
    debug lines carry hosts and ports, never the path.)"""

    active = threading.local()

    def filter(self, record: logging.LogRecord) -> bool:
        return not getattr(self.active, "on", False)


_quiet = _QuietDuringFetch()
logging.getLogger("httpx").addFilter(_quiet)

MAX_BYTES = 2 * 1024 * 1024
CONNECT_TIMEOUT = 3.0
TOTAL_TIMEOUT = 10.0
# One stalled read can overrun `TOTAL_TIMEOUT` by at most this much: the deadline is checked
# between chunks, and httpx sets its read timeout per request, not per remaining budget.
READ_TIMEOUT = 5.0
MAX_ADDRESSES_TRIED = 2
HTTPS_PORT = 443
USER_AGENT = "BeyricNoteTaker/1 (calendar sync)"

REASONS = (
    "scheme",
    "host_not_allowed",
    "private_address",
    "redirect",
    "too_large",
    "timeout",
    "bad_status",
    "not_calendar",
    "unreachable",
)

_TEXT = {
    "scheme": "The link has to be a full address starting with https://. Copy it again from "
    "your calendar's settings.",
    "host_not_allowed": "That address isn't from a calendar service we can read. Copy the "
    "private calendar link from your calendar's settings and paste that.",
    "private_address": "That address points somewhere we can't connect to. Copy the private "
    "calendar link again from your calendar's settings.",
    "redirect": "That link sends us to another address, so we didn't follow it. Copy the "
    "private calendar link again from your calendar's settings.",
    "too_large": "That calendar is too big for us to read (the limit is 2 MB). Try a link "
    "that covers fewer events.",
    "timeout": "The calendar took too long to answer. Try again in a minute.",
    "bad_status": "The calendar service turned the link down. It may have been reset or "
    "removed, so copy a fresh private link from your calendar's settings.",
    "not_calendar": "That link doesn't open a calendar. Make sure you copied the private "
    "calendar address, not the page you see in your browser.",
    "unreachable": "We couldn't reach that address. Check the link and try again in a minute.",
}
_FALLBACK_TEXT = "We couldn't read that calendar link. Copy it again from your calendar's settings."


class IcsRejected(Exception):
    """The link was refused or could not be read. Carries only the reason."""

    def __init__(self, reason: str) -> None:
        if reason not in REASONS:
            raise ValueError("unknown reason")
        super().__init__(reason)
        self.reason = reason


def reason_text(reason: str) -> str:
    """The plain words a person sees for a reason; no codes, no technical detail."""
    return _TEXT.get(reason, _FALLBACK_TEXT)


# Address ranges that Python's `ipaddress` flags unevenly, or that wrap an IPv4 address.
_NEVER_PUBLIC = tuple(
    ipaddress.ip_network(net)
    for net in (
        "100.64.0.0/10",  # shared address space (one cloud's metadata address lives here)
        "198.18.0.0/15",  # benchmarking
        "64:ff9b::/96",  # NAT64: wraps any IPv4 address, including 127.0.0.1
        "64:ff9b:1::/48",
        "2002::/16",  # 6to4
        "2001::/32",  # Teredo
    )
)


def _is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return _is_public(ip.ipv4_mapped)
    if any(ip in net for net in _NEVER_PUBLIC):
        return False
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or not ip.is_global
    )


def _allowed(host: str) -> bool:
    for entry in get_settings().ics_allowed_hosts.split(","):
        entry = entry.strip().lower().rstrip(".")
        if not entry:
            continue
        if entry.startswith(("*.", ".")):
            suffix = "." + entry.lstrip("*.")
            if host.endswith(suffix) and len(host) > len(suffix):
                return True
        elif host == entry:
            return True
    return False


def _reject(reason: str, host: str) -> IcsRejected:
    logger.warning("ics fetch rejected reason=%s host=%s", reason, host)
    return IcsRejected(reason)


def _check_url(url: str) -> tuple[str, str]:
    """(host, path-and-query to request), or an `IcsRejected`."""
    if not isinstance(url, str) or not url.strip() or any(c.isspace() or ord(c) < 32 for c in url):
        raise _reject("scheme", "unknown")
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower().rstrip(".")
        port = parts.port
    except ValueError:
        raise _reject("scheme", "unknown") from None
    if parts.scheme != "https" or not host:
        raise _reject("scheme", host or "unknown")
    if not host.isascii() or "@" in parts.netloc or port not in (None, HTTPS_PORT):
        raise _reject("host_not_allowed", host if host.isascii() else "unknown")
    if not _allowed(host):
        raise _reject("host_not_allowed", host)
    target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    return host, target


def _resolve(host: str, resolver) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """Resolve once; refuse the whole answer if any address in it is not public."""
    try:
        rows = resolver(host, HTTPS_PORT, type=socket.SOCK_STREAM)
    except OSError:
        raise _reject("unreachable", host) from None
    addresses = []
    for row in rows:
        try:
            ip = ipaddress.ip_address(str(row[4][0]).split("%")[0])
        except (ValueError, IndexError, TypeError):
            raise _reject("private_address", host) from None
        if not _is_public(ip):
            raise _reject("private_address", host)
        if ip not in addresses:
            addresses.append(ip)
    if not addresses:
        raise _reject("unreachable", host)
    addresses.sort(key=lambda ip: ip.version)  # IPv4 first: a server may have no IPv6 route
    return addresses[:MAX_ADDRESSES_TRIED]


def _decoder(encoding: str | None, host: str):
    encoding = (encoding or "identity").strip().lower()
    if encoding == "identity":
        return None
    if encoding in ("gzip", "x-gzip"):
        return zlib.decompressobj(wbits=31)
    if encoding == "deflate":
        return zlib.decompressobj()
    raise _reject("not_calendar", host)


def _read_body(response: httpx.Response, host: str, deadline: float) -> bytes:
    if response.headers.get("content-length", "").isdigit():
        if int(response.headers["content-length"]) > MAX_BYTES:
            raise _reject("too_large", host)
    decoder = _decoder(response.headers.get("content-encoding"), host)
    body = bytearray()
    for chunk in response.iter_raw():
        if time.monotonic() > deadline:
            raise _reject("timeout", host)
        if decoder is None:
            body += chunk
        else:
            try:
                # Bounded: a 1 MB gzip that opens to 1 GB is cut off at the limit, not inflated.
                body += decoder.decompress(chunk, MAX_BYTES + 1 - len(body))
            except zlib.error:
                raise _reject("not_calendar", host) from None
            if decoder.unconsumed_tail:
                raise _reject("too_large", host)
        if len(body) > MAX_BYTES:
            raise _reject("too_large", host)
    if time.monotonic() > deadline:
        raise _reject("timeout", host)
    return bytes(body)


def fetch_ics(url: str, *, resolver=socket.getaddrinfo, transport=None) -> bytes:
    """The calendar feed behind `url`, or `IcsRejected` with the reason. `resolver` and
    `transport` exist for tests; production uses the system resolver and a verified TLS client."""
    _quiet.active.on = True
    try:
        return _fetch(url, resolver, transport)
    finally:
        _quiet.active.on = False


def _fetch(url: str, resolver, transport) -> bytes:
    host, target = _check_url(url)
    addresses = _resolve(host, resolver)
    deadline = time.monotonic() + TOTAL_TIMEOUT
    headers = {
        "Host": host,
        "User-Agent": USER_AGENT,
        "Accept": "text/calendar, text/plain;q=0.5, */*;q=0.1",
        "Accept-Encoding": "identity",
    }
    timeout = httpx.Timeout(READ_TIMEOUT, connect=CONNECT_TIMEOUT)
    try:
        client = httpx.Client(
            transport=transport, timeout=timeout, follow_redirects=False, trust_env=False
        )
    except Exception:
        raise _reject("unreachable", host) from None

    with client:
        for index, ip in enumerate(addresses):
            if time.monotonic() > deadline:
                raise _reject("timeout", host)
            literal = f"[{ip.compressed}]" if ip.version == 6 else ip.compressed
            request = client.build_request(
                "GET",
                f"https://{literal}{target}",
                headers=headers,
                extensions={"sni_hostname": host},
            )
            try:
                response = client.send(request, stream=True)
            except httpx.ConnectError:
                if index + 1 < len(addresses):
                    continue  # another address from the SAME validated answer, never a new lookup
                raise _reject("unreachable", host) from None
            except httpx.TimeoutException:
                raise _reject("timeout", host) from None
            except (httpx.HTTPError, httpx.InvalidURL, OSError):
                raise _reject("unreachable", host) from None
            break
        try:
            if response.is_redirect or 300 <= response.status_code < 400:
                raise _reject("redirect", host)
            if response.status_code != 200:
                raise _reject("bad_status", host)
            body = _read_body(response, host, deadline)
        except httpx.TimeoutException:
            raise _reject("timeout", host) from None
        except httpx.HTTPError:
            raise _reject("unreachable", host) from None
        finally:
            response.close()
    if not body.lstrip(b"\xef\xbb\xbf \t\r\n")[:15].upper() == b"BEGIN:VCALENDAR":
        raise _reject("not_calendar", host)
    return body
