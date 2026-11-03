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

from app import auth, edge, services, usage
from app.auth import LoginLimiter
from app.main import create_app
from tests.test_web import (  # noqa: F401  (fixtures used by name)
    OWNER_EMAIL,
    PASSWORD,
    anon_client,
    app,
    fake_openai,
    fake_vexa,
    login_data,
    make_settings,
    owner,
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
    assert edge.client_address(v6, settings) == "2001:db8::/64"


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
    return client.post("/login", data=login_data(token, password=password), headers=headers)


@pytest.fixture
def secured(app, engine, owner):  # noqa: F811
    """The app with a secret configured, and the owner's account in the database."""
    secured = create_app(make_settings(edge_shared_secret=SECRET, trusted_proxy_ips="172.28.0.1"))
    secured.dependency_overrides = app.dependency_overrides
    return secured


@pytest.fixture
def edge_client(secured):
    """Driven by a peer that looks like the Docker gateway."""
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


# ---- IPv6 addresses count as their /64 ----


def test_ipv6_addresses_are_limited_per_slash_64():
    settings = make_settings(edge_shared_secret=SECRET)

    def key(address):
        request = request_from(
            "10.0.0.5", **{"X-Forwarded-For": address, edge.SECRET_HEADER: SECRET}
        )
        return edge.client_address(request, settings)

    assert key("2001:db8:1:2:aaaa:bbbb:cccc:dddd") == key("2001:db8:1:2::1") == "2001:db8:1:2::/64"
    assert key("2001:db8:1:3::1") != key("2001:db8:1:2::1")
    assert key("::ffff:203.0.113.9") == "203.0.113.9"  # an IPv4 client seen through IPv6
    assert edge.limiter_key("203.0.113.9") == "203.0.113.9"


def test_an_ipv6_peer_is_keyed_on_its_slash_64_too():
    request = request_from("2001:db8:9:9:1:2:3:4")
    assert edge.client_address(request, make_settings(edge_shared_secret=SECRET)) == (
        "2001:db8:9:9::/64"
    )


# ---- the limiter's memory is bounded ----


def test_the_limiter_forgets_the_oldest_key_past_its_cap():
    limiter = LoginLimiter(max_keys=3)
    for i in range(10):
        limiter.try_acquire(f"addr-{i}", f"acct-{i}", now=i)
    assert len(limiter) <= 6  # three addresses and three accounts at most
    assert limiter.try_acquire("addr-9", "acct-9", now=10)  # the newest keys are still there


def test_a_flood_of_new_keys_never_refuses_a_stranger():
    limiter = LoginLimiter(max_keys=3)
    for i in range(50):
        assert limiter.try_acquire(f"flood-{i}", f"flood-acct-{i}", now=i)
    assert limiter.try_acquire("someone", "owner", now=60)


# ---- the known-device cookie ----


def device_settings(**overrides):
    return make_settings(edge_shared_secret=SECRET, **overrides)


def known(key, cookie, account, now=None):
    return auth.device_id(key, cookie, account, now) is not None


@pytest.fixture(autouse=True)
def fresh_throttles():
    edge.missing_secret_throttle.reset()
    services.lockout_log_throttle.reset()


def test_a_signed_device_cookie_is_known_until_it_expires():
    key = auth.device_key(device_settings())
    cookie = auth.sign_device(key, "owner", now=1_000)
    assert known(key, cookie, "owner", now=1_000)
    assert known(key, cookie, "owner", now=1_000 + auth.DEVICE_SECONDS)
    assert not known(key, cookie, "owner", now=1_000 + auth.DEVICE_SECONDS + 1)
    assert not known(key, cookie, "owner", now=999)  # from the future


def test_forged_or_malformed_device_cookies_are_not_known():
    key = auth.device_key(device_settings())
    good = auth.sign_device(key, "owner")
    body, signature = good.split(".")
    other_key = auth.device_key(make_settings(session_secret="y" * 40))
    for bad in (
        None,
        "",
        "garbage",
        "a.b.c",
        good + "x",
        f"{body}.{signature[::-1]}",
        auth.sign_device(other_key, "owner"),
        f"{auth._b64(b'{}')}.{auth._signature(key, auth._b64(b'{}'))}",  # signed, wrong content
    ):
        assert not known(key, bad, "owner"), bad


def test_a_device_cookie_only_counts_for_its_own_account():
    """With accounts, one user's cookie must not skip the account window of another's."""
    key = auth.device_key(device_settings())
    cookie = auth.sign_device(key, "Ada@Example.com")
    assert known(key, cookie, " ada@example.com ")  # same account, normalised
    assert not known(key, cookie, "ben@example.com")
    assert not known(key, cookie, "owner")


def test_a_session_cookie_is_not_a_device_cookie_and_the_reverse():
    settings = device_settings()
    session_cookie = auth.sign_session(auth.signing_key(settings), auth.new_session(uid=1))
    assert not known(auth.device_key(settings), session_cookie, "owner")
    device_cookie = auth.sign_device(auth.device_key(settings), "owner")
    assert auth.read_session(auth.signing_key(settings), device_cookie) is None


def test_a_new_session_secret_retires_every_device_cookie_and_no_password_is_involved():
    old = auth.sign_device(auth.device_key(device_settings()), "owner")
    changed = auth.device_key(device_settings(session_secret="z" * 40))
    assert not known(changed, old, "owner")
    # The owner password is read once by migration 0006; it is not part of any key.
    same = auth.device_key(device_settings(owner_password="a different password"))
    assert known(same, old, "owner")


def test_known_device_skips_the_account_window_but_not_the_address_window():
    limiter = LoginLimiter()
    for i in range(10):  # an attacker spreads ten failures over addresses
        assert limiter.try_acquire(f"attacker-{i}", "owner", now=i)
    assert not limiter.try_acquire("owner-home", "owner", now=11)  # a stranger is locked out
    assert limiter.try_acquire("owner-home", "owner", now=11, device="dev-1")
    # ...and a known device is counted by the address window like anyone else.
    for t in range(12, 16):
        assert limiter.try_acquire("owner-home", "owner", now=t, device="dev-1")
    assert not limiter.try_acquire("owner-home", "owner", now=16, device="dev-1")


def test_known_device_attempts_do_not_spend_the_accounts_budget():
    limiter = LoginLimiter()
    for i in range(30):
        assert limiter.try_acquire(f"home-{i}", "owner", now=i, device=f"dev-{i}")
    assert limiter.try_acquire("stranger", "owner", now=40)  # the account window is still empty


def test_a_stolen_device_cookie_keeps_a_cap_of_its_own():
    limiter = LoginLimiter()
    for i in range(10):
        assert limiter.try_acquire(f"thief-{i}", "owner", now=i, device="stolen")
    assert limiter.check("thief-new", "owner", now=11, device="stolen") == "device"
    assert limiter.try_acquire("owner-home", "owner", now=11, device="owners-own")  # not shared
    assert limiter.try_acquire("stranger", "owner", now=11)  # the account window is untouched


def test_a_known_device_success_does_not_clear_the_account_window():
    limiter = LoginLimiter()
    for i in range(10):
        limiter.try_acquire(f"attacker-{i}", "owner", now=i)
    assert limiter.try_acquire("owner-home", "owner", now=11, device="dev-1")
    limiter.succeeded("owner-home", "owner", device="dev-1")
    assert limiter.check("stranger", "owner", now=12) == "account"  # still locked for strangers
    # ...whereas a success without a known device (the password was proven) clears it.
    assert limiter.try_acquire("new-browser", "owner", now=13) is False
    limiter2 = LoginLimiter()
    for i in range(9):
        limiter2.try_acquire(f"a-{i}", "owner", now=i)
    limiter2.try_acquire("somewhere", "owner", now=10)
    limiter2.succeeded("somewhere", "owner")
    assert limiter2.try_acquire("stranger", "owner", now=11)


def test_check_says_which_limit_refused():
    limiter = LoginLimiter(limit=1, window=100, account_limit=2, account_window=1000)
    assert limiter.check("a", "x", now=0) is None
    assert limiter.check("a", "x", now=1) == "address"
    assert limiter.check("b", "x", now=2) is None
    assert limiter.check("c", "x", now=3) == "account"


def new_visitor(app_, address, cookies=None):
    return TestClient(
        app_, follow_redirects=False, client=("172.28.0.1", 50000), cookies=cookies or {}
    )


def lock_the_account(app_):
    """Ten failures from ten visitor addresses, as an attacker would."""
    for i in range(10):
        with new_visitor(app_, i) as attacker:
            assert bad_login(attacker, f"203.0.113.{i}", SECRET).status_code == 401


def owner_logs_in(app_, address="198.51.100.20", cookies=None):
    with new_visitor(app_, address, cookies) as owner:
        response = bad_login(owner, address, SECRET, password=PASSWORD)
        return response, owner.cookies


def test_the_owner_gets_a_device_cookie_only_by_logging_in(secured):
    with new_visitor(secured, 1) as anon:
        failed = bad_login(anon, "198.51.100.20", SECRET)
        assert failed.status_code == 401
        assert auth.DEVICE_COOKIE_NAME not in failed.headers.get("set-cookie", "")
        assert auth.DEVICE_COOKIE_NAME not in anon.cookies
    ok, jar = owner_logs_in(secured)
    assert ok.status_code == 303
    cookie = next(
        c for c in ok.headers.get_list("set-cookie") if c.startswith(f"{auth.DEVICE_COOKIE_NAME}=")
    )
    low = cookie.lower()
    assert "httponly" in low and "samesite=lax" in low and "path=/" in low
    assert f"max-age={auth.DEVICE_SECONDS}" in low
    assert known(secured.state.device_key, jar.get(auth.DEVICE_COOKIE_NAME), OWNER_EMAIL)


def test_the_device_cookie_is_secure_when_the_site_is_https_only(app, engine, owner):  # noqa: F811
    app_ = create_app(make_settings(edge_shared_secret=SECRET, cookie_secure=True))
    app_.dependency_overrides = app.dependency_overrides
    with TestClient(
        app_, base_url="https://testserver", follow_redirects=False, client=("172.28.0.1", 1)
    ) as c:
        ok = bad_login(c, "198.51.100.20", SECRET, password=PASSWORD)
    device = next(x for x in ok.headers.get_list("set-cookie") if "device=" in x)
    assert device.startswith("__Host-device=")  # the browser then insists on Secure, Path=/
    low = device.lower()
    assert "secure" in low.replace("samesite", "") and "path=/" in low and "domain" not in low
    assert not any(x.startswith("device=") for x in ok.headers.get_list("set-cookie"))


def test_an_attacker_cannot_lock_the_owner_out_of_a_new_login(secured):
    _, jar = owner_logs_in(secured)
    device = jar.get(auth.DEVICE_COOKIE_NAME)
    lock_the_account(secured)
    # A stranger, even with the right password, is refused ...
    with new_visitor(secured, 2) as stranger:
        locked = bad_login(stranger, "198.51.100.77", SECRET, password=PASSWORD)
    assert locked.status_code == 429
    # ... the owner's browser, carrying the device cookie, gets in.
    response, _ = owner_logs_in(secured, "198.51.100.20", {auth.DEVICE_COOKIE_NAME: device})
    assert response.status_code == 303


def test_a_forged_or_expired_device_cookie_gets_no_bypass(secured):
    lock_the_account(secured)
    key = secured.state.device_key
    other = auth.device_key(make_settings(session_secret="y" * 40))
    expired = auth.sign_device(key, OWNER_EMAIL, now=1)
    for forged in ("garbage", auth.sign_device(other, OWNER_EMAIL), expired, "x." + "y" * 43):
        response, _ = owner_logs_in(secured, "198.51.100.77", {auth.DEVICE_COOKIE_NAME: forged})
        assert response.status_code == 429, forged


def test_the_device_cookie_does_not_bypass_the_address_limit(secured):
    _, jar = owner_logs_in(secured)
    cookies = {auth.DEVICE_COOKIE_NAME: jar.get(auth.DEVICE_COOKIE_NAME)}
    with new_visitor(secured, 3, cookies) as c:
        for _ in range(5):
            assert bad_login(c, "203.0.113.50", SECRET).status_code == 401
        assert bad_login(c, "203.0.113.50", SECRET).status_code == 429
        # even with the right password, from the same locked address
        assert bad_login(c, "203.0.113.50", SECRET, password=PASSWORD).status_code == 429


def test_a_device_cookie_from_before_a_new_session_secret_is_ignored(secured, app):  # noqa: F811
    _, jar = owner_logs_in(secured)
    device = jar.get(auth.DEVICE_COOKIE_NAME)
    changed = create_app(
        make_settings(edge_shared_secret=SECRET, session_secret="z" * 40, app_env="test")
    )
    changed.dependency_overrides = app.dependency_overrides
    lock_the_account(changed)
    response, _ = owner_logs_in(changed, "198.51.100.20", {auth.DEVICE_COOKIE_NAME: device})
    assert response.status_code == 429  # the old cookie no longer proves anything


def test_logging_out_keeps_the_device_cookie(secured):
    """Logout is routine and the cookie grants no access by itself; clearing it would put the
    owner back in the lockout window exactly when they need to log in again."""
    _, jar = owner_logs_in(secured)
    with new_visitor(secured, 1, dict(jar)) as owner:
        page = owner.get("/templates")
        assert page.status_code == 200
        token = token_from(page.text)
        out = owner.post(
            "/logout", data={"csrf_token": token}, headers={edge.SECRET_HEADER: SECRET}
        )
        assert out.status_code == 303
        assert not any(
            c.startswith(f"{auth.DEVICE_COOKIE_NAME}=") for c in out.headers.get_list("set-cookie")
        )
        assert owner.cookies.get(auth.DEVICE_COOKIE_NAME)


def test_an_account_lockout_is_logged_without_password_material(secured, caplog):
    with caplog.at_level(logging.WARNING, logger="app.services"):
        lock_the_account(secured)
        with new_visitor(secured, 4) as stranger:
            bad_login(stranger, "198.51.100.77", SECRET, password="guess-that-must-not-leak")
    lines = [r.getMessage() for r in caplog.records if r.name == "app.services"]
    assert len(lines) == 1 and "account" in lines[0] and "198.51.100.77" in lines[0]
    assert "guess-that-must-not-leak" not in " ".join(lines) and PASSWORD not in " ".join(lines)
    # The account is a short hash, never the email address itself.
    assert OWNER_EMAIL not in lines[0] and usage.email_hash(OWNER_EMAIL)[:12] in lines[0]


# ---- a request through next without the secret is noticed ----


@pytest.fixture
def next_peer(secured):
    edge.missing_secret_throttle.reset()
    with TestClient(secured, follow_redirects=False, client=(edge.NEXT_ADDRESS, 40000)) as c:
        yield c


def edge_warnings(caplog):
    return [
        r for r in caplog.records if r.name == "app.edge" and "arrived without" in r.getMessage()
    ]


def test_a_request_through_next_without_the_secret_warns_once_a_minute(next_peer, caplog):
    with caplog.at_level(logging.WARNING, logger="app.edge"):
        for _ in range(5):
            next_peer.get("/login")
        assert len(edge_warnings(caplog)) == 1
        edge.missing_secret_throttle._last -= 61  # a minute later
        next_peer.get("/login")
    assert len(edge_warnings(caplog)) == 2
    assert SECRET not in caplog.text


def test_no_warning_for_genuine_or_unrelated_requests(next_peer, secured, caplog):
    with caplog.at_level(logging.WARNING, logger="app.edge"):
        next_peer.get("/login", headers={edge.SECRET_HEADER: SECRET})
        next_peer.get("/health")  # the deploy check goes through next without the secret
        with new_visitor(secured, 1) as gateway:
            gateway.get("/login")  # not next's address
    assert not edge_warnings(caplog)


def test_no_warning_while_no_secret_is_configured(app, caplog):  # noqa: F811
    edge.missing_secret_throttle.reset()
    with caplog.at_level(logging.WARNING, logger="app.edge"):
        with TestClient(app, client=(edge.NEXT_ADDRESS, 1), follow_redirects=False) as c:
            c.get("/login")
    assert not edge_warnings(caplog)


# ---- /health says whether the secret arrived ----


def test_health_reports_edge_ok_and_never_the_secret(next_peer):
    ok = next_peer.get("/health", headers={edge.SECRET_HEADER: SECRET})
    assert (ok.status_code, ok.text, ok.headers["x-edge-ok"]) == (200, "ok", "true")
    for headers in ({}, {edge.SECRET_HEADER: "wrong"}):
        response = next_peer.get("/health", headers=headers)
        assert (response.status_code, response.text) == (200, "ok")  # deploy.sh still sees 200
        assert response.headers["x-edge-ok"] == "false"
    assert SECRET not in str(ok.headers) + ok.text


def test_health_says_false_when_no_secret_is_configured(anon_client):  # noqa: F811
    assert anon_client.get("/health").headers["x-edge-ok"] == "false"


# ---- nginx: a location block must not drop the inherited secret header ----


def test_no_nginx_location_sets_proxy_headers_of_its_own():
    """`proxy_set_header` in a location replaces every inherited proxy_set_header, silently
    dropping X-Edge-Secret and X-Forwarded-For for that path."""
    text = (ROOT / "deploy" / "nginx.conf").read_text()
    text = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    blocks = 0
    for match in re.finditer(r"\blocation\b[^{;]*\{", text):
        depth, i = 1, match.end()
        while depth:
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            i += 1
        body = text[match.end() : i - 1]
        blocks += 1
        assert "proxy_set_header" not in body, match.group(0)
    assert blocks >= 3  # the parse found the login, regenerate and catch-all locations


# ---- the second review's fixes ----


def test_a_stolen_device_cookie_is_capped_through_the_real_login(secured):
    _, jar = owner_logs_in(secured)
    cookies = {auth.DEVICE_COOKIE_NAME: jar.get(auth.DEVICE_COOKIE_NAME)}
    for i in range(10):  # a thief guesses from ten addresses with the stolen cookie
        with new_visitor(secured, i, cookies) as thief:
            assert bad_login(thief, f"203.0.113.{i}", SECRET).status_code == 401
    with new_visitor(secured, 99, cookies) as thief:
        assert bad_login(thief, "203.0.113.200", SECRET, password=PASSWORD).status_code == 429
    # the account window was never touched, so a stranger is not locked out by the thief
    with new_visitor(secured, 98) as stranger:
        assert bad_login(stranger, "198.51.100.77", SECRET, password=PASSWORD).status_code == 303


def test_a_known_device_login_during_a_lockout_leaves_the_lockout_in_place(secured):
    _, jar = owner_logs_in(secured)
    device = jar.get(auth.DEVICE_COOKIE_NAME)
    lock_the_account(secured)
    response, _ = owner_logs_in(secured, "198.51.100.20", {auth.DEVICE_COOKIE_NAME: device})
    assert response.status_code == 303
    with new_visitor(secured, 2) as stranger:  # the failures are still on the account
        assert bad_login(stranger, "198.51.100.77", SECRET, password=PASSWORD).status_code == 429


def issued_device_cookie(response):
    line = next(
        c for c in response.headers.get_list("set-cookie") if c.startswith(auth.DEVICE_COOKIE_NAME)
    )
    return line.split(";")[0].split("=", 1)[1]


def test_a_device_keeps_its_id_when_it_logs_in_again_and_new_browsers_get_new_ones(secured):
    key = secured.state.device_key
    first, _ = owner_logs_in(secured, "198.51.100.20")
    other, _ = owner_logs_in(secured, "198.51.100.21")
    cookie = issued_device_cookie(first)
    id_first = auth.device_id(key, cookie, OWNER_EMAIL)
    assert id_first and id_first != auth.device_id(key, issued_device_cookie(other), OWNER_EMAIL)
    again, _ = owner_logs_in(secured, "198.51.100.20", {auth.DEVICE_COOKIE_NAME: cookie})
    assert auth.device_id(key, issued_device_cookie(again), OWNER_EMAIL) == id_first


def test_lockout_warnings_are_rate_limited(secured, caplog):
    with caplog.at_level(logging.WARNING, logger="app.services"):
        lock_the_account(secured)
        for i in range(5):
            with new_visitor(secured, i) as stranger:
                bad_login(stranger, f"198.51.100.{100 + i}", SECRET)
        assert len([r for r in caplog.records if r.name == "app.services"]) == 1
        services.lockout_log_throttle._last -= 61
        with new_visitor(secured, 9) as stranger:
            bad_login(stranger, "198.51.100.199", SECRET)
    assert len([r for r in caplog.records if r.name == "app.services"]) == 2


def test_the_missing_secret_warning_logs_the_path_escaped(next_peer, caplog):
    with caplog.at_level(logging.WARNING, logger="app.edge"):
        next_peer.get("/api/x%0AFORGED%20LOG%20LINE")
    [record] = edge_warnings(caplog)
    assert "\n" not in record.getMessage() and "\\n" in record.getMessage()


def test_the_limiter_comment_matches_what_it_evicts():
    limiter = LoginLimiter(max_keys=2)
    limiter.try_acquire("a", "x1", now=0)
    limiter.try_acquire("b", "x2", now=1)
    limiter.try_acquire("c", "x3", now=2)  # "a" and "x1" were created first
    assert limiter._clients.attempts.keys() == {"b", "c"}
