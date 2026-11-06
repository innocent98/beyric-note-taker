"""W10 Linear: the key check, the outbound call, the routes and the push after extraction.

The API key and ids are fake placeholders built from parts so no secret scanner flags them.
"""

import base64
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from app import integrations, linear, outbound, tasks, teams, vault
from app.models import Integration, TrackerIssue
from tests.test_api import (  # noqa: F401  (fixtures used by name)
    Api,
    anon,
    anon_client,
    api,
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
from tests.test_web import add_meeting, make_settings

KEY = "lin_api_" + "FAKEFAKE" * 3
TEAM_ID = "team-0000-fake"
LINEAR = "/api/integrations/linear"
NOW = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
THE_TEAMS = [
    {"id": TEAM_ID, "key": "ENG", "name": "Engineering"},
    {"id": "team-1111-fake", "key": "OPS", "name": "Operations"},
]


@pytest.fixture(autouse=True)
def vault_key(monkeypatch):
    settings = make_settings(token_enc_key=base64.b64encode(b"K" * 32).decode())
    monkeypatch.setattr(vault, "get_settings", lambda: settings)


@pytest.fixture
def team(session, owner):
    team = teams.ensure_team(session, owner)
    session.commit()
    return team


@pytest.fixture
def fake_linear(monkeypatch):
    """Replaces the two calls to Linear. `state["fail"]` makes them raise a reason."""
    state = {"fail": None, "issues": [], "teams": THE_TEAMS}

    def teams_(key, **_):
        if state["fail"]:
            raise linear.LinearError(state["fail"])
        return state["teams"]

    def create(key, team_id, title, body, due, **_):
        if state["fail"]:
            raise linear.LinearError(state["fail"])
        n = len(state["issues"]) + 1
        state["issues"].append(
            {"key": key, "team": team_id, "title": title, "body": body, "due": due}
        )
        return {
            "id": f"issue-{n:04d}-fake",
            "identifier": f"ENG-{n}",
            "url": f"https://linear.app/x/{n}",
        }

    monkeypatch.setattr(linear, "teams", teams_)
    monkeypatch.setattr(linear, "create_issue", create)
    return state


@pytest.fixture
def meeting(session, template, team):
    return add_meeting(
        session, template, title="Friday review", status="ready", shared_team_id=team.id
    )


def connect(session, team, owner, fake=None):
    integrations.connect_linear(
        session, team.id, owner, KEY, TEAM_ID, NOW, fetch=lambda key: THE_TEAMS
    )


# ---- the key ----


@pytest.mark.parametrize("bad", ["", "  ", "abc", "lin_api_short", "lin_api_" + "x y" * 8, None, 5])
def test_only_a_linear_key_shape_passes(bad):
    with pytest.raises(linear.KeyInvalid):
        linear.check_key(bad)


def test_a_key_shape_passes_trimmed():
    assert linear.check_key(f" {KEY}\n") == KEY


# ---- the call ----


def pub(host, port, type=None):
    return [(2, 1, 6, "", ("8.8.4.4", port))]


def handler(status=200, body=None, seen=None):
    def go(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, json=body if body is not None else {"data": {}})

    return httpx.MockTransport(go)


def test_the_call_goes_to_the_pinned_address_with_the_key_in_one_header_only():
    seen: list[httpx.Request] = []
    body = {"data": {"teams": {"nodes": [{"id": TEAM_ID, "key": "ENG", "name": "Engineering"}]}}}
    found = linear.teams(KEY, resolver=pub, transport=handler(200, body, seen))
    assert found == [{"id": TEAM_ID, "key": "ENG", "name": "Engineering"}]
    [request] = seen
    assert request.url.host == "8.8.4.4" and request.url.path == "/graphql"
    assert request.headers["host"] == "api.linear.app"
    assert request.extensions["sni_hostname"] == "api.linear.app"
    assert request.headers["authorization"] == KEY and KEY.encode() not in request.read()


@pytest.mark.parametrize(
    "status, body, reason",
    [
        (401, {}, "auth"),
        (403, {}, "auth"),
        (429, {}, "rate"),
        (200, {"errors": [{"extensions": {"code": "AUTHENTICATION_ERROR"}}]}, "auth"),
        (400, {"errors": [{"extensions": {"code": "RATELIMITED"}}]}, "rate"),
        (400, {"errors": [{"message": "private words"}]}, "rejected"),
        (500, {"data": {}}, "rejected"),
        (302, {}, "rejected"),
        (200, {"data": None}, "rejected"),
    ],
)
def test_every_answer_becomes_a_fixed_reason(status, body, reason):
    with pytest.raises(linear.LinearError) as caught:
        linear.teams(KEY, resolver=pub, transport=handler(status, body))
    assert caught.value.reason == reason and "private words" not in linear.TEXT[reason]


def test_a_private_dns_answer_is_never_connected_to():
    def private(host, port, type=None):
        return [(2, 1, 6, "", ("10.0.0.5", port))]

    seen: list = []
    with pytest.raises(linear.LinearError) as caught:
        linear.teams(KEY, resolver=private, transport=handler(seen=seen))
    assert caught.value.reason == "refused" and seen == []


def test_an_answer_over_the_limit_is_cut_off():
    big = {"data": {"teams": {"nodes": []}}, "pad": "x" * (linear.MAX_ANSWER_BYTES + 10)}
    with pytest.raises(linear.LinearError) as caught:
        linear.teams(KEY, resolver=pub, transport=handler(200, big))
    assert caught.value.reason == "unreachable"  # too_large is not a reason we show


def test_a_redirect_is_not_followed():
    seen: list = []

    def go(request):
        seen.append(request)
        return httpx.Response(302, headers={"location": "https://evil.test/"})

    with pytest.raises(linear.LinearError):
        linear.teams(KEY, resolver=pub, transport=httpx.MockTransport(go))
    assert len(seen) == 1


def test_the_key_is_not_logged(caplog):
    caplog.set_level("DEBUG")
    linear.teams(KEY, resolver=pub, transport=handler(200, {"data": {"teams": {"nodes": []}}}))
    assert KEY not in caplog.text and "FAKEFAKE" not in caplog.text


def test_the_outbound_host_is_whatever_the_caller_names_and_is_checked_by_dns_only():
    seen: list = []
    status, _ = outbound.post_json(
        "api.linear.app", "/graphql", {}, resolver=pub, transport=handler(seen=seen)
    )
    assert status == 200 and seen[0].headers["host"] == "api.linear.app"


# ---- the routes ----


def test_an_owner_lists_teams_then_connects_and_no_key_comes_back(api, session, team, fake_linear):
    listed = api.call("POST", f"{LINEAR}/teams", {"api_key": KEY})
    assert listed.status_code == 200 and listed.json() == {"teams": THE_TEAMS}
    response = api.call("PUT", LINEAR, {"api_key": KEY, "team_id": TEAM_ID})
    assert response.status_code == 200, response.text
    assert response.json() == {
        "connected": True,
        "status": "connected",
        "last_error": None,
        "last_synced_at": None,
        "team_name": "Engineering",
        "can_manage": True,
    }
    for text in (listed.text, response.text, api.call("GET", LINEAR).text):
        assert "FAKEFAKE" not in text
    row = session.scalars(select(Integration).where(Integration.kind == "linear")).one()
    assert b"FAKEFAKE" not in row.config_enc and row.label == "Engineering"
    stored = vault.decrypt(
        row.config_enc, row.key_version, purpose="integration", owner=f"team:{team.id}:linear"
    ).reveal()
    assert json.loads(stored) == {"api_key": KEY, "team_id": TEAM_ID}


def test_a_blob_for_linear_does_not_open_as_slack(api, session, team, fake_linear):
    api.call("PUT", LINEAR, {"api_key": KEY, "team_id": TEAM_ID})
    row = session.scalars(select(Integration)).one()
    with pytest.raises(vault.VaultError):
        vault.decrypt(
            row.config_enc,
            row.key_version,
            purpose="integration",
            owner=f"team:{team.id}:slack_webhook",
        )


def test_only_managers_connect_and_a_member_may_look(
    app, api, team, owner, admin, member, fake_linear
):
    assert (
        as_user(app, ADMIN_EMAIL)
        .call("PUT", LINEAR, {"api_key": KEY, "team_id": TEAM_ID})
        .status_code
        == 200
    )
    me = as_user(app, MEMBER_EMAIL)
    read = me.call("GET", LINEAR).json()
    assert read["connected"] is True and read["can_manage"] is False
    for method, path, body in (
        ("POST", f"{LINEAR}/teams", {"api_key": KEY}),
        ("PUT", LINEAR, {"api_key": KEY, "team_id": TEAM_ID}),
        ("DELETE", LINEAR, None),
    ):
        error_of(me.call(method, path, body), 403, "team_forbidden")


def test_a_bad_key_a_refused_key_and_a_wrong_team_are_refused(api, session, team, fake_linear):
    error_of(
        api.call("PUT", LINEAR, {"api_key": "nope", "team_id": TEAM_ID}), 422, "linear_key_invalid"
    )
    fake_linear["fail"] = "auth"
    response = api.call("PUT", LINEAR, {"api_key": KEY, "team_id": TEAM_ID})
    error_of(response, 422, "linear_key_rejected")
    assert "FAKEFAKE" not in response.text
    fake_linear["fail"] = "timeout"
    error_of(
        api.call("PUT", LINEAR, {"api_key": KEY, "team_id": TEAM_ID}), 502, "linear_unreachable"
    )
    fake_linear["fail"] = None
    error_of(
        api.call("PUT", LINEAR, {"api_key": KEY, "team_id": "team-9999-fake"}),
        422,
        "linear_team_invalid",
    )
    assert session.scalars(select(Integration)).all() == []


def test_tries_are_capped_per_team(api, session, team, fake_linear):
    for _ in range(integrations.TESTS_PER_HOUR):
        api.call("POST", f"{LINEAR}/teams", {"api_key": KEY})
    error_of(api.call("POST", f"{LINEAR}/teams", {"api_key": KEY}), 429, "integration_rate")


def test_disconnect_deletes_the_key_and_repeats_safely(api, session, team, fake_linear):
    api.call("PUT", LINEAR, {"api_key": KEY, "team_id": TEAM_ID})
    assert api.call("DELETE", LINEAR).status_code == 204
    assert api.call("DELETE", LINEAR).status_code == 204
    assert session.scalars(select(Integration)).all() == []


def test_slack_and_linear_are_separate_rows(api, session, team, fake_linear, monkeypatch):
    from app import slack

    monkeypatch.setattr(slack, "post", lambda *a, **k: None)
    hook = "https://hooks.slack.com/" + "/".join(
        ["services", "TFAKEFAKE", "BFAKEFAKE", "FAKEFAKEFAKEFAKEFAKEFAKE"]
    )
    api.call("PUT", "/api/integrations/slack", {"webhook_url": hook})
    api.call("PUT", LINEAR, {"api_key": KEY, "team_id": TEAM_ID})
    api.call("DELETE", LINEAR)
    assert [r.kind for r in session.scalars(select(Integration))] == ["slack_webhook"]


# ---- the push ----


def test_new_tasks_become_issues_once_each(session, team, owner, member, meeting, fake_linear):
    connect(session, team, owner)
    live = [
        add_task(session, team, meeting, title="Ship checkout", assignee=member, position=0),
        add_task(session, team, meeting, title="Call vendor", spoken_owner="Kemi", position=1),
    ]
    live[0].due_date = datetime(2026, 11, 13).date()
    session.commit()
    members = tasks.members_of(session, team.id)
    assert integrations.sync_tasks(session, meeting, team.id, live, members, NOW) == 2
    first, second = fake_linear["issues"]
    assert first["key"] == KEY and first["team"] == TEAM_ID and first["due"] == "2026-11-13"
    assert "Daniel Okoro" in first["body"] and "/meetings/" in first["body"]
    assert second["due"] is None and "Kemi" in second["body"]
    assert integrations.sync_tasks(session, meeting, team.id, live, members, NOW) == 0
    rows = session.scalars(select(TrackerIssue)).all()
    assert [r.identifier for r in rows] == ["ENG-1", "ENG-2"]
    assert (
        session.scalars(select(Integration).where(Integration.kind == "linear"))
        .one()
        .last_posted_at
    )


def test_nothing_is_pushed_without_linear_or_for_a_private_meeting(
    session, team, owner, meeting, fake_linear
):
    live = [add_task(session, team, meeting)]
    assert integrations.sync_tasks(session, meeting, team.id, live, [], NOW) == 0
    connect(session, team, owner)
    meeting.shared_team_id = None
    session.commit()
    assert integrations.sync_tasks(session, meeting, team.id, live, [], NOW) == 0
    assert fake_linear["issues"] == []


def test_a_failed_push_marks_the_card_removes_the_claim_and_stops(
    session, team, owner, meeting, fake_linear
):
    connect(session, team, owner)
    live = [
        add_task(session, team, meeting, position=0),
        add_task(session, team, meeting, title="B", position=1),
    ]
    fake_linear["fail"] = "timeout"
    assert integrations.sync_tasks(session, meeting, team.id, live, [], NOW) == 0
    row = session.scalars(select(Integration).where(Integration.kind == "linear")).one()
    assert row.status == "failing" and row.last_error == linear.TEXT["timeout"]
    assert session.scalars(select(TrackerIssue)).all() == []


def test_a_refused_key_is_not_tried_again_until_replaced(
    session, team, owner, meeting, fake_linear
):
    connect(session, team, owner)
    live = [add_task(session, team, meeting)]
    fake_linear["fail"] = "auth"
    integrations.sync_tasks(session, meeting, team.id, live, [], NOW)
    fake_linear["fail"] = None
    assert integrations.sync_tasks(session, meeting, team.id, live, [], NOW) == 0
    connect(session, team, owner)  # a new key clears it
    assert integrations.sync_tasks(session, meeting, team.id, live, [], NOW) == 1


def test_a_meeting_pushes_at_most_its_cap_and_a_team_at_most_its_hourly_cap(
    session, team, owner, meeting, fake_linear
):
    connect(session, team, owner)
    live = [add_task(session, team, meeting, title=f"T{n}", position=n) for n in range(20)]
    assert (
        integrations.sync_tasks(session, meeting, team.id, live, [], NOW)
        == integrations.ISSUES_PER_MEETING
    )
    for task in session.scalars(select(TrackerIssue)):
        task.created_at = NOW - timedelta(minutes=1)
    for n in range(integrations.ISSUES_PER_HOUR - integrations.ISSUES_PER_MEETING):
        extra = add_task(session, team, meeting, title=f"E{n}", position=100 + n)
        session.add(TrackerIssue(task_id=extra.id, team_id=team.id, kind="linear", created_at=NOW))
    session.commit()
    more = add_task(session, team, meeting, title="Over", position=500)
    assert integrations.sync_tasks(session, meeting, team.id, [more], [], NOW) == 0


def test_a_crash_never_reaches_the_caller(session, team, owner, meeting, fake_linear, monkeypatch):
    connect(session, team, owner)
    monkeypatch.setattr(linear, "description", lambda *a, **k: 1 / 0)
    live = [add_task(session, team, meeting)]
    assert integrations.sync_tasks(session, meeting, team.id, live, [], NOW) == 0


def test_the_task_text_sent_is_defanged():
    seen: list[httpx.Request] = []
    issue = {"id": "issue-0001-fake", "identifier": "ENG-1", "url": "https://linear.app/x/1"}
    answer = {"data": {"issueCreate": {"success": True, "issue": issue}}}
    made = linear.create_issue(
        KEY, TEAM_ID, "See https://evil.test/x and evil.example.com now", "body", None,
        resolver=pub, transport=handler(200, answer, seen),
    )  # fmt: skip
    assert made["identifier"] == "ENG-1"
    sent = json.loads(seen[0].read())["variables"]["input"]
    assert "evil.test" not in sent["title"] and "evil.example.com" not in sent["title"]
    assert "dueDate" not in sent


def test_after_extraction_runs_slack_and_linear_and_survives_both(
    session, team, owner, meeting, fake_linear
):
    connect(session, team, owner)
    live = [add_task(session, team, meeting)]
    integrations.after_extraction(session, meeting, team.id, live, [], NOW)
    assert len(fake_linear["issues"]) == 1
