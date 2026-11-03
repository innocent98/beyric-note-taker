"""Which client address the app believes, and the login limits keyed on it.

nginx sets `X-Forwarded-For` and `X-Edge-Secret`; Next passes both on; the app believes the
forwarded address only when the secret matches. Everything else, including a request from the
Docker bridge gateway, is a stranger and is identified by its socket peer.
"""

import logging
import re
import secrets
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.datastructures import Headers

from app import edge
from app.auth import LoginLimiter
from app.main import create_app
from tests.test_web import (  # noqa: F401  (fixtures used by name)
    PASSWORD,
    anon_client,
    app,
    fake_openai,
    fake_vexa,
    make_settings,
    token_from,
)

SECRET = secrets.token_hex(24)  # random per run: nothing secret-shaped in the repository
ROOT = Path(__file__).resolve().parent.parent


def request_from(peer, **headers):
    """Just what `edge.client_address` reads: the socket peer and the headers."""
    from types import SimpleNamespace

    return SimpleNamespace(
        client=SimpleNamespace(host=peer) if peer else None, headers=Headers(headers=headers)
    )


# ---- the address the app believes ----


def test_without_the_secret_the_forwarded_address_is_ignored():
    settings = make_settings(edge_shared_secret=SECRET, trusted_proxy_ips="")
    request = request_from("10.0.0.5", **{"X-Forwarded-For": "203.0.113.9"})
    assert edge.client_address(request, settings) == "10.0.0.5"


def test_a_wrong_secret_is_ignored():
    settings = make_settings(edge_shared_secret=SECRET)
    for given in ("wrong", SECRET[:-1], SECRET + "x", "", " " + SECRET):
        request = request_from(
            "10.0.0.5", **{"X-Forwarded-For": "203.0.113.9", edge.SECRET_HEADER: given}
        )
        assert edge.client_address(request, settings) == "10.0.0.5", given


def test_a_non_ascii_secret_is_just_wrong():
    settings = make_settings(edge_shared_secret=SECRET)
    headers = Headers(
        raw=[
            (b"x-forwarded-for", b"203.0.113.9"),
            (b"x-edge-secret", "sécret".encode("latin-1")),
        ]
    )
    request = request_from("10.0.0.5")
    request.headers = headers
    assert edge.client_address(request, settings) == "10.0.0.5"


def test_the_right_secret_makes_the_forwarded_address_count():
    settings = make_settings(edge_shared_secret=SECRET)
    request = request_from(
        "172.28.0.1", **{"X-Forwarded-For": "203.0.113.9", edge.SECRET_HEADER: SECRET}
    )
    assert edge.client_address(request, settings) == "203.0.113.9"


def test_the_gateway_peer_is_not_trusted_once_a_secret_is_set():
    """The old rule: believe X-Forwarded-For from the Docker gateway. Docker masquerades every
    container and host-loopback connection as that address, so anyone could forge it."""
    settings = make_settings(edge_shared_secret=SECRET, trusted_proxy_ips="172.28.0.1")
    for peer in ("172.28.0.1", "172.28.1.10", "172.28.1.1"):
        request = request_from(peer, **{"X-Forwarded-For": "203.0.113.9"})
        assert edge.client_address(request, settings) == peer


def test_a_valid_secret_with_a_bad_forwarded_value_falls_back_to_the_peer():
    settings = make_settings(edge_shared_secret=SECRET)
    for forwarded in ("", "not-an-ip", "999.1.1.1", "203.0.113.9:80", "<script>"):
        request = request_from(
            "10.0.0.5", **{"X-Forwarded-For": forwarded, edge.SECRET_HEADER: SECRET}
        )
        assert edge.client_address(request, settings) == "10.0.0.5", forwarded
    no_header = request_from("10.0.0.5", **{edge.SECRET_HEADER: SECRET})
    assert edge.client_address(no_header, settings) == "10.0.0.5"


def test_the_nearest_hop_wins_and_ipv6_is_normalised():
    """The last entry is the one the trusted edge wrote; earlier ones came from the client."""
    settings = make_settings(edge_shared_secret=SECRET)
    chained = request_from(
        "10.0.0.5",
        **{"X-Forwarded-For": "198.51.100.1, 203.0.113.9", edge.SECRET_HEADER: SECRET},
    )
    assert edge.client_address(chained, settings) == "203.0.113.9"
    v6 = request_from(
        "10.0.0.5",
        **{"X-Forwarded-For": "2001:0DB8:0:0:0:0:0:1", edge.SECRET_HEADER: SECRET},
    )
    assert edge.client_address(v6, settings) == "2001:db8::1"


def test_no_peer_at_all_is_one_shared_unknown_client():
    assert edge.client_address(request_from(None), make_settings()) == "unknown"


# ---- before the secret is configured: as before, but loud ----


def test_unset_secret_keeps_trusting_the_known_proxy_peers():
    """Transitional: until the operator sets the secret, nothing changes for the owner."""
    settings = make_settings(trusted_proxy_ips="172.28.0.1")
    forwarded = {"X-Forwarded-For": "203.0.113.9"}
    assert edge.client_address(request_from("172.28.0.1", **forwarded), settings) == "203.0.113.9"
    assert edge.client_address(request_from("172.28.1.10", **forwarded), settings) == "203.0.113.9"
    assert edge.client_address(request_from("10.0.0.5", **forwarded), settings) == "10.0.0.5"


def test_unset_secret_trusts_no_proxy_when_the_list_is_emptied():
    settings = make_settings(trusted_proxy_ips="")
    request = request_from("172.28.0.1", **{"X-Forwarded-For": "203.0.113.9"})
    assert edge.client_address(request, settings) == "172.28.0.1"


def test_the_app_warns_loudly_at_startup_when_the_secret_is_unset(caplog):
    with caplog.at_level(logging.WARNING, logger="app.edge"):
        create_app(make_settings(app_env="production"))
    assert any(
        r.levelno == logging.WARNING and "EDGE_SHARED_SECRET" in r.getMessage()
        for r in caplog.records
    )


def test_a_good_secret_means_no_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="app.edge"):
        create_app(make_settings(app_env="production", edge_shared_secret=SECRET))
    assert not [r for r in caplog.records if r.name == "app.edge"]


def test_a_short_secret_is_warned_about(caplog):
    with caplog.at_level(logging.WARNING, logger="app.edge"):
        create_app(make_settings(app_env="production", edge_shared_secret="short"))
    assert any("short" in r.getMessage() for r in caplog.records if r.name == "app.edge")


def test_the_app_still_starts_with_the_secret_unset():
    assert create_app(make_settings(app_env="production")) is not None


# ---- through the real login route ----


def bad_login(client, forwarded=None, secret=None, password="nope", token=None):
    token = token or token_from(client.get("/login").text)
    headers = {}
    if forwarded:
        headers["X-Forwarded-For"] = forwarded
    if secret:
        headers[edge.SECRET_HEADER] = secret
    return client.post("/login", data={"password": password, "csrf_token": token}, headers=headers)


@pytest.fixture
def edge_client(app, engine):  # noqa: F811
    """The app with a secret configured, driven by a peer that looks like the Docker gateway."""
    secured = create_app(make_settings(edge_shared_secret=SECRET, trusted_proxy_ips="172.28.0.1"))
    secured.dependency_overrides = app.dependency_overrides
    with TestClient(secured, follow_redirects=False, client=("172.28.0.1", 50000)) as c:
        yield c


def test_forging_the_address_does_not_reset_the_limit(edge_client):
    token = token_from(edge_client.get("/login").text)
    for i in range(5):
        assert bad_login(edge_client, f"203.0.113.{i}", token=token).status_code == 401
    # A sixth try, claiming yet another address, is still the same stranger.
    assert bad_login(edge_client, "203.0.113.200", token=token).status_code == 429


def test_forging_cannot_burn_someone_elses_attempts(edge_client):
    """Attempts that claim to be the owner's address, with no secret, are counted against the
    forger's own peer; the owner arriving through nginx with the secret is untouched."""
    token = token_from(edge_client.get("/login").text)
    for _ in range(6):
        bad_login(edge_client, "198.51.100.7", token=token)  # no secret: counted against the peer
    ok = bad_login(edge_client, "198.51.100.7", SECRET, password=PASSWORD, token=token)
    assert ok.status_code == 303  # the real address 198.51.100.7 has no failures of its own


def test_the_edge_secret_gives_each_visitor_their_own_limit(edge_client):
    token = token_from(edge_client.get("/login").text)
    for _ in range(5):
        assert bad_login(edge_client, "203.0.113.1", SECRET, token=token).status_code == 401
    assert bad_login(edge_client, "203.0.113.1", SECRET, token=token).status_code == 429
    assert bad_login(edge_client, "203.0.113.2", SECRET, token=token).status_code == 401


def test_a_wrong_secret_does_not_give_a_visitor_their_own_limit(edge_client):
    token = token_from(edge_client.get("/login").text)
    for i in range(5):
        assert bad_login(edge_client, f"203.0.113.{i}", "wrong", token=token).status_code == 401
    assert bad_login(edge_client, "203.0.113.99", "wrong", token=token).status_code == 429


def test_ten_failures_in_an_hour_lock_the_account_even_for_a_new_address(edge_client):
    """Five addresses, two failures each: no address is over its own limit, the account is."""
    token = token_from(edge_client.get("/login").text)
    for i in range(10):
        r = bad_login(edge_client, f"203.0.113.{i // 2}", SECRET, token=token)
        assert r.status_code == 401
    fresh = bad_login(edge_client, "203.0.113.250", SECRET, password=PASSWORD, token=token)
    assert fresh.status_code == 429  # even the right password waits


def test_both_limits_answer_with_the_same_message(edge_client):
    token = token_from(edge_client.get("/login").text)
    for _ in range(5):
        bad_login(edge_client, "203.0.113.1", SECRET, token=token)
    by_address = bad_login(edge_client, "203.0.113.1", SECRET, token=token)
    for i in range(10):
        bad_login(edge_client, f"203.0.113.{10 + i}", SECRET, token=token)
    by_account = bad_login(edge_client, "203.0.113.99", SECRET, token=token)
    assert by_address.status_code == by_account.status_code == 429
    assert by_address.text == by_account.text


# ---- the limiter itself ----


def test_address_limit_is_five_in_five_minutes():
    limiter = LoginLimiter()
    assert all(limiter.try_acquire("a", "owner", now=i) for i in range(5))
    assert not limiter.try_acquire("a", "owner", now=100)
    assert not limiter.try_acquire("a", "owner", now=299)
    assert limiter.try_acquire("a", "owner", now=300.5)  # the first attempt (t=0) has expired


def test_account_limit_is_ten_in_an_hour_across_addresses():
    limiter = LoginLimiter()
    for i in range(10):
        assert limiter.try_acquire(f"addr-{i}", "owner", now=i * 300)  # one address each
    assert not limiter.try_acquire("addr-new", "owner", now=3000)
    assert limiter.try_acquire("addr-new", "owner", now=3600.5)  # t=0 left the hour


def test_unknown_accounts_are_limited_exactly_like_known_ones():
    limiter = LoginLimiter()
    for i in range(10):
        assert limiter.try_acquire(f"addr-{i}", "nobody@example.com", now=i)
    assert not limiter.try_acquire("addr-x", "nobody@example.com", now=11)
    assert limiter.try_acquire("addr-x", "somebody-else@example.com", now=11)


def test_account_names_are_compared_without_case_or_surrounding_space():
    limiter = LoginLimiter(account_limit=2)
    assert limiter.try_acquire("a", "Ada@Example.com", now=0)
    assert limiter.try_acquire("b", " ada@example.com ", now=1)
    assert not limiter.try_acquire("c", "ADA@EXAMPLE.COM", now=2)


def test_a_refused_attempt_is_not_counted_and_does_not_extend_the_wait():
    limiter = LoginLimiter(limit=2, window=100)
    assert limiter.try_acquire("a", "x", now=0) and limiter.try_acquire("a", "x", now=1)
    for t in range(2, 99):
        assert not limiter.try_acquire("a", "x", now=t)  # hammering while locked costs nothing
    assert limiter.try_acquire("a", "x", now=101.5)


def test_an_address_over_its_limit_does_not_spend_the_accounts_budget():
    limiter = LoginLimiter(limit=1, window=100, account_limit=3, account_window=1000)
    assert limiter.try_acquire("a", "x", now=0)
    for t in range(1, 50):
        assert not limiter.try_acquire("a", "x", now=t)
    assert limiter.try_acquire("b", "x", now=60) and limiter.try_acquire("c", "x", now=61)


def test_success_refunds_one_address_attempt_but_not_all_of_them():
    """A visitor with an account of their own must not reset their count by logging in to it
    between guesses at someone else's."""
    limiter = LoginLimiter()
    for t in range(4):
        limiter.try_acquire("a", "owner", now=t)
    limiter.try_acquire("a", "mine", now=4)  # the 5th attempt is a success...
    limiter.succeeded("a", "mine")  # ...so only that one is given back
    assert limiter.try_acquire("a", "owner", now=5)  # 5th failure is allowed
    assert not limiter.try_acquire("a", "owner", now=6)  # the 6th is not


def test_success_clears_that_accounts_failures():
    limiter = LoginLimiter()
    for i in range(9):
        limiter.try_acquire(f"addr-{i}", "owner", now=i)
    limiter.try_acquire("addr-9", "owner", now=9)
    limiter.succeeded("addr-9", "owner")
    for i in range(10):
        assert limiter.try_acquire(f"fresh-{i}", "owner", now=100 + i)


def test_idle_entries_are_forgotten():
    limiter = LoginLimiter(limit=2, window=10, account_limit=2, account_window=10)
    for name in ("a", "b", "c"):
        limiter.try_acquire(name, f"acct-{name}", now=0)
    assert len(limiter) == 6  # three addresses, three accounts
    limiter.try_acquire("d", "acct-d", now=100)
    assert len(limiter) == 2


# ---- the deployment files keep the app from rewriting the peer ----


def test_uvicorn_never_rewrites_the_peer_address():
    """If uvicorn applied X-Forwarded-For itself, the app would never see the real peer and the
    secret check would be bypassed."""
    compose = (ROOT / "compose.yaml").read_text()
    assert "--no-proxy-headers" in compose
    assert "--forwarded-allow-ips" not in compose
    assert "--proxy-headers" not in re.sub(r"--no-proxy-headers", "", compose)


def test_nginx_sets_the_secret_from_a_file_outside_git():
    text = (ROOT / "deploy" / "nginx.conf").read_text()
    nginx = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert re.search(r"^\s*include\s+/etc/nginx/[^;]*edge-secret[^;]*;", nginx, re.M)
    assert re.search(r"^\s*proxy_set_header\s+X-Forwarded-For\s+\$remote_addr;", nginx, re.M)
    # No directive carrying a secret value in the repository (the comment describing it is fine).
    assert "proxy_set_header X-Edge-Secret" not in nginx
