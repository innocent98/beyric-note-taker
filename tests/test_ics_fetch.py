import gzip
import logging
import re
import socket
import time

import httpx
import pytest

from app import ics_fetch
from app.config import Settings
from app.ics_fetch import REASONS, IcsRejected, fetch_ics, reason_text

HOST = "calendar.google.com"
TOKEN = "PLACEHOLDERSECRETTOKEN"
URL = f"https://{HOST}/calendar/ical/me%40example.test/private-{TOKEN}/basic.ics"
PUBLIC_IP = "142.250.0.10"
OTHER_PUBLIC_IP = "142.250.0.11"
FEED = b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:a@example.test\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"  # noqa: E501


@pytest.fixture(autouse=True)
def allowlist(monkeypatch):
    settings = Settings(_env_file=None, ics_allowed_hosts=f"{HOST}, outlook.live.com, .example.org")
    monkeypatch.setattr(ics_fetch, "get_settings", lambda: settings)


def answer(*ips):
    """What `socket.getaddrinfo` returns for these addresses."""
    rows = []
    for ip in ips:
        if ":" in ip:
            rows.append((socket.AF_INET6, socket.SOCK_STREAM, 6, "", (ip, 443, 0, 0)))
        else:
            rows.append((socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443)))
    return rows


def resolver_for(*ips):
    calls = []

    def resolver(host, port, *args, **kwargs):
        calls.append(host)
        return answer(*ips)

    resolver.calls = calls
    return resolver


class Recorder:
    """A MockTransport handler that remembers every request it was given."""

    def __init__(self, respond):
        self.respond = respond
        self.requests: list[httpx.Request] = []

    def __call__(self, request):
        self.requests.append(request)
        return self.respond(request)

    def transport(self):
        return httpx.MockTransport(self)


def streamed(body: bytes, size: int = 65536):
    """The body as an iterator, so httpx treats it as a stream read on demand, as a real socket is
    (a plain `bytes` body is read eagerly by httpx and cannot be iterated again)."""
    return (body[i : i + size] for i in range(0, len(body), size))


def ok(body=FEED, **kwargs):
    return lambda request: httpx.Response(200, content=streamed(body), **kwargs)


def fetch(url=URL, *, ips=(PUBLIC_IP,), respond=None):
    recorder = Recorder(respond or ok())
    result = fetch_ics(url, resolver=resolver_for(*ips), transport=recorder.transport())
    return result, recorder


def rejected(url=URL, *, ips=(PUBLIC_IP,), respond=None) -> tuple[IcsRejected, Recorder]:
    recorder = Recorder(respond or ok())
    with pytest.raises(IcsRejected) as raised:
        fetch_ics(url, resolver=resolver_for(*ips), transport=recorder.transport())
    return raised.value, recorder


# ---- the happy path, and what the request looks like ----


def test_an_allowlisted_calendar_is_fetched_and_returned_as_bytes():
    body, recorder = fetch()
    assert body == FEED
    assert len(recorder.requests) == 1


def test_the_connection_goes_to_the_resolved_ip_with_the_original_host_and_sni():
    _, recorder = fetch()
    request = recorder.requests[0]
    assert request.url.host == PUBLIC_IP and request.url.scheme == "https"
    raw = f"/calendar/ical/me%40example.test/private-{TOKEN}/basic.ics".encode()
    assert request.url.raw_path == raw  # the path is sent exactly as pasted
    assert request.headers["host"] == HOST
    assert request.extensions["sni_hostname"] == HOST
    assert request.method == "GET"
    assert request.headers["accept-encoding"] == "identity"


def test_an_ipv6_answer_is_connected_to_as_a_bracketed_literal():
    _, recorder = fetch(ips=("2607:f8b0:4004:c1b::64",))
    assert recorder.requests[0].url.host == "2607:f8b0:4004:c1b::64"
    assert recorder.requests[0].headers["host"] == HOST


def test_a_query_string_is_kept_and_the_fragment_dropped():
    _, recorder = fetch(f"https://{HOST}/feed.ics?token={TOKEN}&x=1#frag")
    assert recorder.requests[0].url.query == f"token={TOKEN}&x=1".encode()
    assert recorder.requests[0].url.fragment == ""


@pytest.mark.parametrize("scheme", ["webcal", "WEBCAL", "webcals"])
def test_webcal_links_are_read_as_https(scheme):
    body, recorder = fetch(f"{scheme}://{HOST}/calendar/ical/x/private-{TOKEN}/basic.ics")
    assert body == FEED and recorder.requests[0].url.scheme == "https"
    assert rejected("webcal://evil.test/feed.ics")[0].reason == "host_not_allowed"
    assert rejected("webcal://10.0.0.1/feed.ics")[0].reason == "host_not_allowed"


def test_pasted_whitespace_around_the_link_is_trimmed():
    assert fetch(f"  {URL}\n")[0] == FEED


def test_a_byte_order_mark_and_lowercase_header_are_accepted():
    assert fetch(respond=ok(b"\xef\xbb\xbfbegin:vcalendar\r\nEND:VCALENDAR"))[0].endswith(
        b"VCALENDAR"
    )


def test_the_client_never_follows_redirects_and_ignores_proxy_settings(monkeypatch):
    seen = {}
    real = httpx.Client

    def spy(*args, **kwargs):
        seen.update(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(ics_fetch.httpx, "Client", spy)
    fetch()
    assert seen["follow_redirects"] is False
    assert seen["trust_env"] is False  # a proxy would bypass the address checks


# ---- scheme and host ----


@pytest.mark.parametrize(
    "url",
    [
        f"http://{HOST}/feed.ics",
        f"ftp://{HOST}/feed.ics",
        "file:///etc/passwd",
        f"//{HOST}/feed.ics",
        f"{HOST}/feed.ics",
        "javascript:alert(1)",
        "",
        "   ",
        f"https://{HOST}/feed\r\nHost: evil.test\r\n.ics",
        f"https://{HOST}/feed .ics",
    ],
)
def test_http_scheme_rejected(url):
    error, recorder = rejected(url)
    assert error.reason == "scheme"
    assert recorder.requests == []


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.test/feed.ics",
        f"https://{HOST}.evil.test/feed.ics",  # a lookalike under another domain
        f"https://evil{HOST}/feed.ics",  # an exact match is needed, not a suffix of the text
        f"https://sub.{HOST}/feed.ics",  # a listed host is not a wildcard
        f"https://{HOST}@evil.test/feed.ics",  # userinfo trick: the host is evil.test
        f"https://evil.test@{HOST}/feed.ics",  # userinfo at all is refused
        f"https://{HOST}:8443/feed.ics",  # only the standard port
        "https://127.0.0.1/feed.ics",
        "https://[::1]/feed.ics",
        "https://169.254.169.254/latest/meta-data/",
        "https://localhost/feed.ics",
        "https://org/feed.ics",  # a bare suffix entry's own name is not a subdomain of it
        "https://calendar.google.com。evil.test/feed.ics",  # a unicode dot
        "https://caléndar.google.com/feed.ics",
        "https://evil.test\\.example.org/feed.ics",  # a backslash must not slip past a suffix entry
        "https://ev_il.example.org/feed.ics",
        "https://-evil.example.org/feed.ics",
        "https://.example.org/feed.ics",
        "https://a..example.org/feed.ics",
    ],
)
def test_host_outside_allowlist_rejected(url):
    error, recorder = rejected(url)
    assert error.reason == "host_not_allowed"
    assert recorder.requests == []


def test_a_listed_suffix_matches_its_subdomains_and_a_trailing_dot_and_case_are_ignored():
    for url in (
        "https://cal.example.org/a.ics",
        "https://deep.cal.example.org/a.ics",
        f"https://{HOST.upper()}/feed.ics",
        f"https://{HOST}./feed.ics",
        "https://outlook.live.com/owa/calendar/x/calendar.ics",
    ):
        assert fetch(url)[0] == FEED
    assert rejected("https://example.org/a.ics")[0].reason == "host_not_allowed"


def test_the_allowlist_is_read_from_the_environment_setting_and_has_sensible_defaults():
    defaults = Settings(_env_file=None).ics_allowed_hosts
    assert {h.strip() for h in defaults.split(",")} == {
        "calendar.google.com",
        "outlook.office365.com",
        "outlook.live.com",
    }
    assert (
        Settings(_env_file=None, ics_allowed_hosts="a.test,b.test").ics_allowed_hosts
        == "a.test,b.test"
    )


# ---- addresses (the SSRF core) ----


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "127.1.2.3",
        "10.0.0.5",
        "10.255.255.255",
        "172.16.0.1",
        "172.31.255.1",
        "192.168.1.1",
        "169.254.169.254",  # the cloud metadata address
        "169.254.0.1",
        "100.64.0.1",  # shared address space
        "100.100.100.200",  # another cloud's metadata address
        "0.0.0.0",
        "0.1.2.3",
        "224.0.0.1",  # multicast
        "239.255.255.250",
        "240.0.0.1",  # reserved
        "255.255.255.255",
        "192.0.2.1",  # documentation range
        "198.18.0.1",  # benchmarking
        "::1",
        "::",
        "fe80::1",
        "fc00::1",
        "fd00:ec2::254",  # the cloud metadata address over IPv6
        "ff02::1",
        "::ffff:127.0.0.1",  # IPv4-mapped loopback
        "::ffff:169.254.169.254",
        "::ffff:10.0.0.1",
        "64:ff9b::7f00:1",  # NAT64 wrapping 127.0.0.1
        "2002:7f00:1::",  # 6to4 wrapping 127.0.0.1
        "2001:0:4136:e378:8000:63bf:3fff:fdd2",  # Teredo
        "not-an-ip",
    ],
)
def test_allowlisted_host_resolving_to_127_0_0_1_10_x_169_254_169_254_and_ipv6_loopback_rejected(
    ip,
):
    error, recorder = rejected(ips=(ip,))
    assert error.reason == "private_address"
    assert recorder.requests == []  # nothing was sent


def test_one_private_address_among_public_ones_rejects_the_whole_answer():
    error, recorder = rejected(ips=(PUBLIC_IP, "10.0.0.1"))
    assert error.reason == "private_address" and recorder.requests == []
    error, recorder = rejected(ips=("10.0.0.1", PUBLIC_IP))
    assert error.reason == "private_address" and recorder.requests == []


def test_dns_rebinding_second_answer_is_not_used():
    answers = iter([answer(PUBLIC_IP), answer("127.0.0.1"), answer("127.0.0.1")])
    calls = []

    def rebinding(host, port, *args, **kwargs):
        calls.append(host)
        return next(answers)

    recorder = Recorder(ok())
    assert fetch_ics(URL, resolver=rebinding, transport=recorder.transport()) == FEED
    assert calls == [HOST]  # resolved once, never again
    assert [r.url.host for r in recorder.requests] == [PUBLIC_IP]


def test_the_second_validated_address_is_tried_when_the_first_cannot_connect():
    def respond(request):
        if request.url.host == PUBLIC_IP:
            raise httpx.ConnectError("unreachable")
        return httpx.Response(200, content=streamed(FEED))

    body, recorder = fetch(ips=(PUBLIC_IP, OTHER_PUBLIC_IP), respond=respond)
    assert body == FEED
    assert [r.url.host for r in recorder.requests] == [PUBLIC_IP, OTHER_PUBLIC_IP]


def test_ipv4_is_tried_before_ipv6():
    _, recorder = fetch(ips=("2607:f8b0:4004:c1b::64", PUBLIC_IP))
    assert recorder.requests[0].url.host == PUBLIC_IP


def test_no_answer_or_a_failed_lookup_is_unreachable_not_a_crash():
    error, recorder = rejected(ips=())
    assert error.reason == "unreachable" and recorder.requests == []

    def failing(host, port, *args, **kwargs):
        raise socket.gaierror("name not known")

    with pytest.raises(IcsRejected) as raised:
        fetch_ics(URL, resolver=failing, transport=Recorder(ok()).transport())
    assert raised.value.reason == "unreachable"


# ---- redirects, status, size, time, content ----


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_redirect_to_internal_host_not_followed(status):
    def respond(request):
        return httpx.Response(status, headers={"Location": "http://169.254.169.254/latest/"})

    error, recorder = rejected(respond=respond)
    assert error.reason == "redirect"
    assert len(recorder.requests) == 1  # the Location was never requested


@pytest.mark.parametrize("status", [400, 401, 403, 404, 410, 429, 500, 503])
def test_an_error_status_is_bad_status(status):
    error, _ = rejected(respond=lambda request: httpx.Response(status, content=b"nope"))
    assert error.reason == "bad_status"


def test_body_over_two_megabytes_aborts_early():
    sent = []

    def chunks():
        for _ in range(64):  # 64 x 100 KB = 6.4 MB on offer
            sent.append(1)
            yield b"BEGIN:VCALENDAR" + b"x" * (100_000 - 15)

    error, _ = rejected(respond=lambda request: httpx.Response(200, content=chunks()))
    assert error.reason == "too_large"
    assert len(sent) < 64  # it stopped reading, it did not download everything first


def test_a_declared_length_over_the_limit_is_refused_before_reading_the_body():
    def respond(request):
        return httpx.Response(200, headers={"Content-Length": str(3 * 1024 * 1024)}, content=b"")

    assert rejected(respond=respond)[0].reason == "too_large"


def test_a_body_of_exactly_two_megabytes_is_accepted_and_one_byte_more_is_not():
    limit = 2 * 1024 * 1024
    exact = b"BEGIN:VCALENDAR\r\n" + b"x" * (limit - 17)
    assert len(fetch(respond=ok(exact))[0]) == limit
    assert rejected(respond=ok(exact + b"y"))[0].reason == "too_large"


def test_a_compressed_body_is_limited_by_its_decompressed_size():
    bomb = gzip.compress(b"BEGIN:VCALENDAR\r\n" + b"0" * (50 * 1024 * 1024))
    assert len(bomb) < 100_000  # tiny on the wire, 50 MB once opened
    error, _ = rejected(respond=ok(bomb, headers={"Content-Encoding": "gzip"}))
    assert error.reason == "too_large"
    body, _ = fetch(respond=ok(gzip.compress(FEED), headers={"Content-Encoding": "gzip"}))
    assert body == FEED


def test_an_unknown_content_encoding_is_not_a_calendar():
    error, _ = rejected(respond=ok(FEED, headers={"Content-Encoding": "br"}))
    assert error.reason == "not_calendar"


def test_slow_response_times_out(monkeypatch):
    monkeypatch.setattr(ics_fetch, "TOTAL_TIMEOUT", 0.3)

    def drip():
        yield b"BEGIN:VCALENDAR\r\n"
        for _ in range(50):
            time.sleep(0.1)
            yield b"X-SLOW:1\r\n"

    started = time.monotonic()
    error, _ = rejected(respond=lambda request: httpx.Response(200, content=drip()))
    assert error.reason == "timeout"
    assert time.monotonic() - started < 2


def test_a_server_that_never_sends_headers_is_cut_off_at_the_total_limit(monkeypatch):
    monkeypatch.setattr(ics_fetch, "TOTAL_TIMEOUT", 0.3)

    def respond(request):
        time.sleep(2)  # holds the connection open before any header
        return httpx.Response(200, content=streamed(FEED))

    started = time.monotonic()
    error, _ = rejected(respond=respond)
    assert error.reason == "timeout"
    assert time.monotonic() - started < 1.0  # the caller is freed at the limit, not at 2 s


def test_a_resolver_that_hangs_is_cut_off_at_the_total_limit(monkeypatch):
    monkeypatch.setattr(ics_fetch, "TOTAL_TIMEOUT", 0.3)

    def hanging(host, port, *args, **kwargs):
        time.sleep(2)
        return answer(PUBLIC_IP)

    recorder = Recorder(ok())
    started = time.monotonic()
    with pytest.raises(IcsRejected) as raised:
        fetch_ics(URL, resolver=hanging, transport=recorder.transport())
    assert raised.value.reason == "timeout" and time.monotonic() - started < 1.0
    assert recorder.requests == []


def test_an_unexpected_failure_inside_the_fetch_is_unreachable_and_leaks_nothing(caplog):
    def respond(request):
        raise RuntimeError(f"boom {URL}")

    error, _ = rejected(respond=respond)
    assert error.reason == "unreachable"
    assert TOKEN not in caplog.text and TOKEN not in repr(error) + repr(error.__cause__)


def test_a_url_httpx_refuses_is_rejected_not_raised(monkeypatch):
    def refuse(self, *args, **kwargs):
        raise httpx.InvalidURL(f"bad {URL}")

    monkeypatch.setattr(httpx.Client, "build_request", refuse)
    error, _ = rejected()
    assert error.reason == "unreachable" and TOKEN not in f"{error!r} {error.__cause__!r}"


def test_an_overlong_link_is_refused_before_anything_happens():
    long_url = f"https://{HOST}/" + "a" * 2100
    error, recorder = rejected(long_url)
    assert error.reason == "scheme" and recorder.requests == []
    assert fetch(f"https://{HOST}/" + "a" * 1900)[0] == FEED


def test_a_transport_timeout_is_a_timeout_and_the_limits_are_the_documented_ones():
    def respond(request):
        raise httpx.ReadTimeout("slow")

    assert rejected(respond=respond)[0].reason == "timeout"
    assert (ics_fetch.CONNECT_TIMEOUT, ics_fetch.TOTAL_TIMEOUT) == (3.0, 10.0)
    assert ics_fetch.MAX_BYTES == 2 * 1024 * 1024


def test_a_connect_timeout_on_every_address_is_a_timeout():
    def respond(request):
        raise httpx.ConnectTimeout("no answer")

    error, recorder = rejected(ips=(PUBLIC_IP, OTHER_PUBLIC_IP), respond=respond)
    assert error.reason == "timeout" and len(recorder.requests) == 2


def test_a_connection_error_is_unreachable():
    def respond(request):
        raise httpx.ConnectError("connection refused")

    assert rejected(respond=respond)[0].reason == "unreachable"


@pytest.mark.parametrize(
    "body", [b"", b"<html><body>Sign in</body></html>", b'{"error": "nope"}', b"BEGIN:VEVENT\r\n"]
)
def test_non_calendar_body_rejected(body):
    assert rejected(respond=ok(body))[0].reason == "not_calendar"


# ---- the link is a secret ----


def test_url_never_appears_in_error_or_log(caplog):
    caplog.set_level(logging.DEBUG)
    failures = [
        (f"https://evil.test/private-{TOKEN}.ics", {}),
        (f"http://{HOST}/private-{TOKEN}.ics", {}),
        (URL, {"ips": ("10.0.0.1",)}),
        (URL, {"respond": lambda r: httpx.Response(302, headers={"Location": URL})}),
        (URL, {"respond": lambda r: httpx.Response(404)}),
        (URL, {"respond": ok(b"<html>")}),
        (URL, {"respond": lambda r: (_ for _ in ()).throw(httpx.ConnectError(f"failed {URL}"))}),
        (URL, {"respond": lambda r: (_ for _ in ()).throw(httpx.ReadTimeout(f"slow {URL}"))}),
    ]
    for url, options in failures:
        error, _ = rejected(url, **options)
        shown = f"{error!s} {error!r} {error.args} {error.reason} {error.__cause__!r}"
        assert TOKEN not in shown and "private-" not in shown and "me%40example" not in shown
        assert error.__suppress_context__ or error.__context__ is None
    fetch()  # and the success path logs nothing secret either
    assert TOKEN not in caplog.text and "private-" not in caplog.text
    assert "me%40example" not in caplog.text and "basic.ics" not in caplog.text
    assert all(HOST in line or "evil.test" in line or "reason=" in line for line in caplog.messages)


def test_a_rejection_is_logged_with_the_reason_and_host_only(caplog):
    caplog.set_level(logging.INFO)
    rejected(URL, ips=("10.0.0.1",))
    assert any("private_address" in m and HOST in m for m in caplog.messages)


def test_every_reason_has_plain_words_and_no_code():
    assert set(REASONS) == {
        "scheme",
        "host_not_allowed",
        "private_address",
        "redirect",
        "too_large",
        "timeout",
        "bad_status",
        "not_calendar",
        "unreachable",
    }
    texts = {reason: reason_text(reason) for reason in REASONS}
    assert len(set(texts.values())) == len(REASONS)  # each says something different
    for reason, text in texts.items():
        assert len(text) > 20 and text.endswith(".")
        assert reason not in text and "_" not in text
        assert not re.search(r"\b[1-5]\d\d\b", text)  # no HTTP status
        assert not re.search(r"exception|error code|traceback|ssrf|socket|dns", text, re.I)
        assert "outlook" not in text.lower() and "microsoft" not in text.lower()
    assert reason_text("something-new")  # an unknown reason still gets plain words


def test_ics_rejected_carries_only_the_reason():
    error = IcsRejected("redirect")
    assert error.reason == "redirect" and str(error) == "redirect"
    with pytest.raises(ValueError):
        IcsRejected("made-up")
