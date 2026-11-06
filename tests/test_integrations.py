"""W10 Slack: the webhook check, the outbound post, the routes, and the post after extraction.

Cast (from `tests/test_api_tasks.py`): `owner` owns the team, `admin` and `member` are on it.
The webhook address is a fake placeholder, built from parts so no secret scanner flags it.
"""

import base64
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from app import integrations, slack, tasks, teams, vault
from app.models import Integration, IntegrationPost
from tests.test_api import (  # noqa: F401  (fixtures used by name)
    Api,
    anon,
    anon_client,
    api,
    api_routes,
    app,
    client,
    error_of,
    fake_openai,
    fake_vexa,
    owner,
    ready,
    template,
)
from tests.test_api_tasks import (  # noqa: F401
    ADMIN_EMAIL,
    MEMBER_EMAIL,
    add_task,
    admin,
    as_user,
    member,
)
from tests.test_team_visibility import OTHER_EMAIL, outsider  # noqa: F401
from tests.test_web import add_meeting, make_settings, make_user, stranger  # noqa: F401

SVC = "/".join(
    ["services", "TFAKEFAKE", "BFAKEFAKE", "FAKEFAKEFAKEFAKEFAKEFAKE"]
)  # built, so no scanner mistakes it for a real webhook
HOOK = f"https://hooks.slack.com/{SVC}"
SLACK = "/api/integrations/slack"
NOW = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
KEY = base64.b64encode(b"K" * 32).decode()


@pytest.fixture(autouse=True)
def vault_key(monkeypatch):
    settings = make_settings(token_enc_key=KEY)
    monkeypatch.setattr(vault, "get_settings", lambda: settings)


@pytest.fixture
def team(session, owner):
    team = teams.ensure_team(session, owner)
    session.commit()
    return team


@pytest.fixture
def sent(monkeypatch):
    """Replaces the outbound call; records (url, text) and can be told to fail."""
    calls: list[tuple[str, str]] = []
    state = {"fail": None}

    def fake(url, text, **_):
        calls.append((url, text))
        if state["fail"]:
            raise slack.SlackError(state["fail"])

    monkeypatch.setattr(slack, "post", fake)
    fake.calls, fake.state = calls, state
    return fake


# ---- the address check ----


@pytest.mark.parametrize(
    "url",
    [
        f"http://hooks.slack.com/{SVC}",
        f"https://hooks.slack.com.evil.test/{SVC}",
        f"https://evil.test/{SVC}",
        f"https://hooks.slack.com@evil.test/{SVC}",
        f"https://user:pw@hooks.slack.com/{SVC}",
        f"https://hooks.slack.com:8443/{SVC}",
        f"https://hooks.slack.com/{SVC}?x=1",
        f"https://hooks.slack.com/{SVC}#frag",
        f"https://hooks.slack.com/{SVC.rsplit('/', 1)[0]}/short",
        "https://hooks.slack.com/other/TFAKEFAKE/BFAKEFAKE/FAKEFAKEFAKEFAKEFAKEFAKE",
        f"https://hooks.slack.com/{SVC}/../x",
        f"https://hooks.slack.com/{SVC[:-8]} {SVC[-8:]}",
        "",
        "   ",
    ],
)
def test_only_a_slack_incoming_webhook_address_passes(url):
    with pytest.raises(slack.WebhookInvalid):
        slack.check_webhook(url)


def test_a_real_shaped_address_passes_and_is_trimmed():
    assert slack.check_webhook(f"  {HOOK}\n") == HOOK
    assert (
        slack.check_webhook("https://HOOKS.slack.com:443" + HOOK[len("https://hooks.slack.com") :])
        == HOOK
    )


def test_a_non_string_is_not_an_address():
    with pytest.raises(slack.WebhookInvalid):
        slack.check_webhook(None)


# ---- what is sent ----


def test_nothing_said_in_a_meeting_can_mention_link_or_format():
    hostile = "<!channel> <@U123> <https://evil.test|click> *bold* `code` & see evil.example.com"
    text = slack.message(7, "Sync <!here>", [(hostile, "Ada <!everyone>"), ("Plain", None)])
    assert "<!" not in text.replace("<https://notes.beyrictech.com/meetings/7|", "")
    assert "<@" not in text and "evil.test|" not in text
    assert "&lt;!channel&gt;" in text and "&lt;@U123&gt;" in text
    assert "evil.example.com" not in text and "evil[.]example[.]com" in text
    assert "*bold*" not in text and "`" not in text
    assert text.startswith(
        "*<https://notes.beyrictech.com/meetings/7|Sync &lt;!here&gt;>*: 2 new tasks"
    )
    assert "(needs an owner)" in text


def test_a_long_list_is_cut_and_says_how_many_more():
    lines = [(f"Task {n}", "Ada") for n in range(20)]
    text = slack.message(1, "M", lines)
    assert text.count("• ") == slack.MAX_TASKS_SHOWN and text.endswith("…and 5 more")
    assert len(text) <= slack.MESSAGE_CHARS


# ---- the outbound call ----


def public(host, port, type=None):
    return [(2, 1, 6, "", ("8.8.4.4", port))]


def private(host, port, type=None):
    return [(2, 1, 6, "", ("169.254.169.254", port))]


def transport(status=200, seen=None, location=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        headers = {"location": location} if location else {}
        return httpx.Response(status, headers=headers, text="ok")

    return httpx.MockTransport(handler)


def test_the_post_goes_to_the_resolved_address_with_the_real_host_name():
    seen: list[httpx.Request] = []
    slack.post(HOOK, "hi", resolver=public, transport=transport(seen=seen))
    [request] = seen
    assert request.url.host == "8.8.4.4" and request.url.path.endswith("FAKEFAKEFAKEFAKEFAKEFAKE")
    assert request.headers["host"] == "hooks.slack.com"
    assert request.extensions["sni_hostname"] == "hooks.slack.com"
    assert (
        request.read()
        == b'{"text":"hi","unfurl_links":false,"unfurl_media":false,"link_names":false}'
    )


def test_a_private_answer_from_dns_is_never_connected_to():
    seen: list = []
    with pytest.raises(slack.SlackError) as caught:
        slack.post(HOOK, "hi", resolver=private, transport=transport(seen=seen))
    assert caught.value.reason == "refused" and seen == []


@pytest.mark.parametrize(
    "status, reason",
    [
        (404, "gone"),
        (410, "gone"),
        (429, "rate"),
        (400, "rejected"),
        (500, "rejected"),
        (302, "rejected"),
    ],
)
def test_the_answer_status_becomes_a_fixed_reason(status, reason):
    seen: list = []
    send = transport(status, seen, location="https://evil.test/" if status == 302 else None)
    with pytest.raises(slack.SlackError) as caught:
        slack.post(HOOK, "hi", resolver=public, transport=send)
    assert caught.value.reason == reason and len(seen) == 1  # the redirect was not followed


def test_a_bad_address_never_gets_as_far_as_the_network():
    seen: list = []
    with pytest.raises(slack.SlackError):
        slack.post("https://evil.test/x", "hi", resolver=public, transport=transport(seen=seen))
    assert seen == []


def test_the_secret_is_not_logged_by_a_post(caplog):
    caplog.set_level("DEBUG")
    slack.post(HOOK, "hi", resolver=public, transport=transport())
    assert "FAKEFAKEFAKE" not in caplog.text


# ---- the routes ----


def test_an_owner_connects_and_nothing_secret_comes_back(api, session, team, sent):
    response = api.call("PUT", SLACK, {"webhook_url": HOOK})
    assert response.status_code == 200, response.text
    assert response.json() == {
        "connected": True,
        "status": "connected",
        "last_error": None,
        "last_posted_at": None,
        "can_manage": True,
    }
    assert "FAKEFAKE" not in response.text and "FAKEFAKE" not in api.call("GET", SLACK).text
    assert [c[0] for c in sent.calls] == [HOOK]  # one test message
    row = session.scalars(select(Integration)).one()
    assert HOOK.encode() not in row.config_enc and b"FAKEFAKE" not in row.config_enc
    assert (
        vault.decrypt(
            row.config_enc,
            row.key_version,
            purpose="integration",
            owner=f"team:{team.id}:slack_webhook",
        ).reveal()
        == HOOK
    )


def test_a_blob_made_for_one_team_does_not_open_for_another(api, session, team, sent):
    api.call("PUT", SLACK, {"webhook_url": HOOK})
    row = session.scalars(select(Integration)).one()
    with pytest.raises(vault.VaultError):
        vault.decrypt(
            row.config_enc,
            row.key_version,
            purpose="integration",
            owner=f"team:{team.id + 1}:slack_webhook",
        )


def test_an_admin_may_connect_and_a_member_may_only_look(
    app, api, session, team, owner, admin, member, sent
):
    boss = as_user(app, ADMIN_EMAIL)
    assert boss.call("PUT", SLACK, {"webhook_url": HOOK}).status_code == 200
    me = as_user(app, MEMBER_EMAIL)
    read = me.call("GET", SLACK)
    assert read.status_code == 200 and read.json()["connected"] is True
    assert read.json()["can_manage"] is False
    error_of(me.call("PUT", SLACK, {"webhook_url": HOOK}), 403, "team_forbidden")
    error_of(me.call("DELETE", SLACK), 403, "team_forbidden")
    assert session.scalars(select(Integration)).one() is not None  # still connected


def test_another_teams_slack_is_invisible(app, api, session, team, owner, outsider, sent):
    api.call("PUT", SLACK, {"webhook_url": HOOK})
    them = as_user(app, OTHER_EMAIL)
    assert them.call("GET", SLACK).json()["connected"] is False
    assert them.call("DELETE", SLACK).status_code == 204  # their own (empty) team, not ours
    assert session.scalars(select(Integration)).one().team_id == team.id


def test_a_wrong_address_is_refused_and_nothing_is_stored_or_sent(api, session, team, sent):
    for bad in ("https://evil.test/x", "not a url", ""):
        error_of(api.call("PUT", SLACK, {"webhook_url": bad}), 400, "slack_webhook_invalid")
    assert sent.calls == [] and session.scalars(select(Integration)).all() == []


def test_a_webhook_slack_refuses_is_not_stored(api, session, team, sent):
    sent.state["fail"] = "gone"
    response = api.call("PUT", SLACK, {"webhook_url": HOOK})
    error_of(response, 502, "slack_unreachable")
    assert "FAKEFAKE" not in response.text
    assert session.scalars(select(Integration)).all() == []


def test_connecting_is_rate_limited_per_team(api, session, team, sent):
    for _ in range(integrations.TESTS_PER_HOUR):
        api.call("PUT", SLACK, {"webhook_url": HOOK})
    error_of(api.call("PUT", SLACK, {"webhook_url": HOOK}), 429, "integration_rate")


def test_disconnect_deletes_the_secret_and_is_safe_to_repeat(api, session, team, sent):
    api.call("PUT", SLACK, {"webhook_url": HOOK})
    assert api.call("DELETE", SLACK).status_code == 204
    assert api.call("DELETE", SLACK).status_code == 204
    assert session.scalars(select(Integration)).all() == []
    assert api.call("GET", SLACK).json()["connected"] is False


def test_reconnecting_replaces_the_address_and_clears_a_failure(api, session, team, sent):
    api.call("PUT", SLACK, {"webhook_url": HOOK})
    row = session.scalars(select(Integration)).one()
    row.status, row.last_error = "failing", "x"
    session.commit()
    other = HOOK.replace("FAKEFAKEFAKEFAKEFAKEFAKE", "OTHEROTHEROTHEROTHEROTHER")
    assert api.call("PUT", SLACK, {"webhook_url": other}).json()["status"] == "connected"
    session.expire_all()
    assert session.scalars(select(Integration)).one().last_error is None


def test_every_integrations_route_needs_a_login(app):
    routes = {r for r in api_routes(app) if r[1].startswith("/api/integrations")}
    assert routes == {
        ("GET", SLACK),
        ("PUT", SLACK),
        ("DELETE", SLACK),
        ("GET", "/api/integrations/linear"),
        ("PUT", "/api/integrations/linear"),
        ("DELETE", "/api/integrations/linear"),
        ("POST", "/api/integrations/linear/teams"),
    }


def test_a_stored_key_nobody_can_use_is_a_clean_503(api, session, team, sent, monkeypatch):
    monkeypatch.setattr(vault, "get_settings", lambda: make_settings())  # no TOKEN_ENC_KEY
    error_of(api.call("PUT", SLACK, {"webhook_url": HOOK}), 503, "integrations_unavailable")


# ---- the post after extraction ----


@pytest.fixture
def meeting(session, template, team):
    """Shared with the team: only a shared meeting's tasks are posted."""
    return add_meeting(
        session, template, title="Friday review", status="ready", shared_team_id=team.id
    )


def connect(session, team, owner):
    integrations.connect_slack(session, team.id, owner, HOOK, NOW, sender=lambda *a, **k: None)


def test_new_tasks_are_posted_once_with_their_owners(session, team, owner, member, meeting, sent):
    connect(session, team, owner)
    live = [
        add_task(session, team, meeting, title="Ship checkout", assignee=member, position=0),
        add_task(session, team, meeting, title="Call vendor", spoken_owner="Kemi", position=1),
    ]
    members = tasks.members_of(session, team.id)
    assert integrations.notify_meeting(session, meeting, team.id, live, members, NOW) is True
    [(url, text)] = sent.calls
    assert url == HOOK and "Friday review" in text and "Ship checkout (Daniel Okoro)" in text
    assert "Call vendor (Kemi)" in text
    # A second run for the same meeting posts nothing.
    assert integrations.notify_meeting(session, meeting, team.id, live, members, NOW) is False
    assert len(sent.calls) == 1
    assert session.scalars(select(Integration)).one().last_posted_at is not None


def test_nothing_is_posted_without_slack_or_without_tasks(session, team, owner, meeting, sent):
    live = [add_task(session, team, meeting)]
    assert integrations.notify_meeting(session, meeting, team.id, live, [], NOW) is False
    connect(session, team, owner)
    assert integrations.notify_meeting(session, meeting, team.id, [], [], NOW) is False
    assert sent.calls == []


def test_a_failed_post_marks_the_card_and_raises_nothing(session, team, owner, meeting, sent):
    connect(session, team, owner)
    sent.state["fail"] = "gone"
    live = [add_task(session, team, meeting)]
    assert integrations.notify_meeting(session, meeting, team.id, live, [], NOW) is False
    row = session.scalars(select(Integration)).one()
    assert row.status == "failing" and row.last_error == slack.TEXT["gone"]
    assert "FAKEFAKE" not in row.last_error


def test_the_team_hourly_cap_skips_the_post(session, team, owner, meeting, sent):
    connect(session, team, owner)
    for _ in range(integrations.POSTS_PER_HOUR):
        session.add(
            IntegrationPost(
                team_id=team.id, kind="slack_webhook", created_at=NOW - timedelta(minutes=5)
            )
        )
    session.commit()
    live = [add_task(session, team, meeting)]
    assert integrations.notify_meeting(session, meeting, team.id, live, [], NOW) is False
    assert sent.calls == []


def test_a_crash_inside_the_post_never_reaches_the_caller(
    session, team, owner, meeting, monkeypatch
):
    connect(session, team, owner)
    monkeypatch.setattr(slack, "message", lambda *a, **k: 1 / 0)
    live = [add_task(session, team, meeting)]
    assert integrations.notify_meeting(session, meeting, team.id, live, [], NOW) is False


def test_a_private_meetings_tasks_never_reach_the_channel(session, team, owner, meeting, sent):
    connect(session, team, owner)
    meeting.shared_team_id = None  # teammates cannot see these tasks, so Slack must not either
    session.commit()
    live = [add_task(session, team, meeting)]
    assert integrations.notify_meeting(session, meeting, team.id, live, [], NOW) is False
    assert sent.calls == [] and session.scalars(select(IntegrationPost)).all() == [
        p for p in session.scalars(select(IntegrationPost)) if p.meeting_id is None
    ]


def test_a_webhook_slack_says_is_gone_is_not_tried_again(session, team, owner, meeting, sent):
    connect(session, team, owner)
    sent.state["fail"] = "gone"
    live = [add_task(session, team, meeting)]
    integrations.notify_meeting(session, meeting, team.id, live, [], NOW)
    other = add_meeting(session, meeting_template(session), title="Next", shared_team_id=team.id)
    assert integrations.notify_meeting(session, other, team.id, live, [], NOW) is False
    assert len(sent.calls) == 1  # the second meeting never called Slack


def meeting_template(session):
    from app.models import Template

    return session.scalars(select(Template)).first()


def test_a_disconnect_between_the_claim_and_the_post_stops_the_post(
    session, team, owner, meeting, sent, monkeypatch
):
    connect(session, team, owner)
    live = [add_task(session, team, meeting)]
    original = session.refresh

    def refresh_after_delete(row):
        session.delete(row)
        session.flush()
        original(row)

    monkeypatch.setattr(session, "refresh", refresh_after_delete)
    assert integrations.notify_meeting(session, meeting, team.id, live, [], NOW) is False
    assert sent.calls == []
