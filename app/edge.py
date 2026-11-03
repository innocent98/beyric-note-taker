"""Which client address the app believes.

The app limits logins per client address, so the address must not be forgeable. It comes from
the socket peer unless the request proves it passed through nginx: nginx adds `X-Edge-Secret`
(a value only nginx and this app know) and `X-Forwarded-For` (the visitor's address). Next.js
sits in between and passes both headers on unchanged. Only a request that carries the right
secret has its `X-Forwarded-For` believed.

Why not trust a proxy by its IP address: Docker rewrites the source of any connection from
another network, or from the host's loopback, to the bridge gateway, so "the gateway" is every
container and every process on the shared server, not just nginx.

Uvicorn must run with `--no-proxy-headers`, or it would apply `X-Forwarded-For` to the peer
address itself before this code runs (compose.yaml and a test guard that).
"""

import hmac
import ipaddress
import logging
import threading
import time

from fastapi import Request

from app.config import Settings

logger = logging.getLogger(__name__)

SECRET_HEADER = "X-Edge-Secret"
FORWARDED_HEADER = "X-Forwarded-For"
MIN_SECRET_LENGTH = 32

# Until the operator sets the secret the app trusts the addresses it always has: the bridge
# gateway (nginx, through Docker) from `trusted_proxy_ips`, and the Next.js container, which
# compose.yaml pins to this address. FORGEABLE (see above); removed once the secret is live.
NEXT_ADDRESS = "172.28.1.10"


def _peer(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def has_edge_secret(request: Request, settings: Settings) -> bool:
    if not settings.edge_shared_secret:
        return False
    given = request.headers.get(SECRET_HEADER, "")
    # Bytes, so a non-ASCII header is a mismatch rather than a TypeError.
    return hmac.compare_digest(given.encode(), settings.edge_shared_secret.encode())


def limiter_key(address: str) -> str:
    """What the limits count: an IPv4 address as is, an IPv6 address as its /64 (one customer
    holds a whole /64, so per-address would let them rotate through 2^64 identities).
    IPv4-mapped IPv6 addresses count as the IPv4 address. Anything else is kept as given."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return address
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped:
            return str(ip.ipv4_mapped)
        return str(ipaddress.ip_network(f"{ip}/64", strict=False))
    return str(ip)


def _forwarded_address(request: Request) -> str | None:
    """The last `X-Forwarded-For` entry: the one the trusted edge wrote. Earlier entries came
    from the client. None if it is missing or not an IP address."""
    value = request.headers.get(FORWARDED_HEADER, "")
    last = value.rsplit(",", 1)[-1].strip()
    try:
        return str(ipaddress.ip_address(last))
    except ValueError:
        return None


def _legacy_trusted_peers(settings: Settings) -> set[str]:
    configured = {p.strip() for p in settings.trusted_proxy_ips.split(",") if p.strip()}
    return configured | {NEXT_ADDRESS}


def client_address(request: Request, settings: Settings) -> str:
    """The address to rate-limit this request on."""
    peer = _peer(request)
    if settings.edge_shared_secret:
        trusted = has_edge_secret(request, settings)
    else:
        trusted = peer in _legacy_trusted_peers(settings)
    return limiter_key((_forwarded_address(request) if trusted else None) or peer)


_MISSING_WARNING_EVERY = 60.0
_missing_lock = threading.Lock()
_missing_last = float("-inf")


def note_missing_secret(request: Request, settings: Settings) -> None:
    """With a secret configured, every genuine request reaches Next with it (nginx adds it). One
    from Next's own address without it means nginx lost the include, or something on the server
    is talking to Next directly. Warn, at most once a minute; the secret is never logged."""
    global _missing_last
    if not settings.edge_shared_secret or _peer(request) != NEXT_ADDRESS:
        return
    if has_edge_secret(request, settings):
        return
    now = time.monotonic()
    with _missing_lock:
        if now - _missing_last < _MISSING_WARNING_EVERY:
            return
        _missing_last = now
    logger.warning(
        "A request through the Next.js container arrived without a valid %s header "
        "(path %s). Either nginx is not sending it (check the include in the site file) or "
        "something on this server is calling next directly.",
        SECRET_HEADER,
        request.scope.get("path", "?"),
    )


def warn_if_unsafe(settings: Settings) -> None:
    """Say so at startup when the forwarded address cannot be trusted properly. The app still
    starts: refusing would turn a missed deploy step into an outage."""
    if settings.app_env != "production":
        return
    if not settings.edge_shared_secret:
        logger.warning(
            "EDGE_SHARED_SECRET is not set: the client address for login limits is believed from "
            "X-Forwarded-For when the connection comes from the Docker bridge gateway or the "
            "Next.js container, and any container or process on this server can forge it. Set "
            "the same secret in .env and in nginx (deploy/README.md, 'Edge secret')."
        )
    elif len(settings.edge_shared_secret) < MIN_SECRET_LENGTH:
        logger.warning(
            "EDGE_SHARED_SECRET is short (%d characters, want at least %d): generate one with "
            "`openssl rand -hex 32`.",
            len(settings.edge_shared_secret),
            MIN_SECRET_LENGTH,
        )
