"""A person switching workspace never sees another workspace's data (workspaces design, section 9:
"a leak is the worst failure"; plan Task 3). The permanent guard.

`test_every_workspace_data_route_honours_the_header` enumerates every GET route the API serves. Each
must be CLASSIFIED below, so nobody can add a route that returns workspace data and forget to narrow
it: an unclassified route fails the test. A route classed as workspace data is then called as one
person who belongs to three workspaces (the personal one, X and Y), each full of data carrying its
own marker, and must:

* with the header of one workspace, show that workspace's marker and neither of the others';
* with no header, behave as the person's default workspace (`teams.default_membership`);
* answer 404 `not_found` for a team the caller is not in.

Cast (from `tests/test_multi_team_leaks.py`): Ada owns X, Quinn owns Y, Pat is in both and has a
personal workspace; Zed owns Z, which Pat is not in. Every marker is text that appears in titles,
notes, names and notification titles of its workspace only.
"""

# ruff: noqa: F811  (fixtures are imported by name and used as arguments)
from datetime import UTC, date, datetime, timedelta

import pytest

from app import services, teams
from app.models import (
    CalendarConnection,
    CalendarEvent,
    Decision,
    DecisionTask,
    Integration,
    Meeting,
    Milestone,
    Notification,
    Project,
    ReviewItem,
    ScheduledJoin,
    Series,
    SeriesIgnored,
    Task,
    Team,
    TeamMember,
    Template,
    VocabularyTerm,
    WorkspacePrefs,
)
from tests.test_api import (  # noqa: F401  (fixtures used by name)
    anon,
    anon_client,
    api,
    api_routes,
    app,
    client,
    error_of,
    fake_bot,
    fake_openai,
    owner,
    ready,
    template,
)
from tests.test_api_tasks import add_task
from tests.test_ask import FakeAsk, passages_of
from tests.test_multi_team_leaks import PAT, QUINN, ZED, new_team, world  # noqa: F401
from tests.test_team_visibility import sign_in

ADA = "owner@example.test"
MARKS = {"p": "DATA_P", "x": "DATA_X", "y": "DATA_Y"}
NOTES = "## Summary\nA talk about {m} pricing.\n\n## Decisions\n- Decide {m}.\n"
TRANSCRIPT = "[{m} Person]: We spoke about {m} pricing.\n[Ada]: Yes, {m} pricing."

# ---- the classification of every GET route -------------------------------------------------

# Returns data of the ACTIVE workspace: narrowed by `X-Team-Id` (else the default workspace).
WORKSPACE_DATA = {
    "/api/meetings",
    "/api/decisions",
    "/api/review",
    "/api/notifications",
    "/api/tasks",
    "/api/tasks/export",
    "/api/projects",
    "/api/milestones",
    "/api/sprints",
    "/api/series",
    "/api/series/removed",
    "/api/ask/scope",
    "/api/integrations/linear",
    "/api/integrations/slack",
    "/api/team",
    "/api/team/members",
    # W3: a calendar connection belongs to one workspace, with its Upcoming list, its Recurring
    # cards and the meetings it did not get into
    "/api/calendar",
    "/api/calendar/recurring",
    "/api/not-recorded",
    # W2: templates, vocabulary and the person's own settings in the workspace
    "/api/templates",
    "/api/vocabulary",
    "/api/workspace-prefs",
}

# The caller's own account, not a workspace's: sign-in, plan and hours, payments, the personal
# settings that never switch, the list of the caller's workspaces itself, and what only a token or
# the platform admin may read. Nothing of a meeting, task, decision or notification is in them.
ACCOUNT_LEVEL = {
    "/api/session",  # badges and unread counts follow the header; covered by its own test
    "/api/teams",
    "/api/team/invites/preview",
    "/api/pilot",
    "/api/auth/google/start",
    "/api/auth/google/callback",
    "/api/billing/invoices",
    "/api/billing/payments",
    "/api/billing/plans",
    "/api/billing/profile",
    "/api/billing/route",
    "/api/billing/subscription",
    "/api/billing/subscription/change-plan/preview",
    "/api/settings/auto-share",
    "/api/settings/bot-name",
    "/api/settings/email",
    "/api/settings/language",
    "/api/settings/name",
    "/api/settings/reminders",
    "/api/settings/tasks",
    "/api/settings/time-zone",
    "/api/admin/billing/payments",
    "/api/admin/billing/refunds",
    "/api/admin/mail-failures",
    "/api/admin/pilot",
    "/api/admin/users",
    "/api/admin/waitlist",
}

# Nothing is pending any more: W2 moved templates, vocabulary and the per-workspace settings, and W3
# the calendar connection, its upcoming list and its recurring cards. A route that must wait for a
# later slice is added here with the slice's name, and this test then enforces it is moved.
PENDING_SLICE: dict[str, str] = {}


def test_every_get_route_is_classified(app):
    """A new GET route fails here until it is put in one of the three sets above (or has an id in
    its path: those open ONE thing and authorise by readability, tests/test_accounts.py)."""
    found = {path for method, path in api_routes(app) if method == "GET" and "{" not in path}
    known = WORKSPACE_DATA | ACCOUNT_LEVEL | set(PENDING_SLICE)
    assert found - known == set(), f"classify these GET routes: {sorted(found - known)}"
    assert known - found == set(), f"classified but gone: {sorted(known - found)}"
    # a route is in one set only
    assert not (WORKSPACE_DATA & ACCOUNT_LEVEL) and not (WORKSPACE_DATA & set(PENDING_SLICE))
    # nothing that carries a meeting's, task's or decision's text is allowed to wait for a slice
    assert not PENDING_SLICE


# ---- the cast -----------------------------------------------------------------------------


class Cast:
    pass


@pytest.fixture
def cast(session, template, world) -> Cast:
    """Pat's three workspaces, each with a meeting (ready, with notes and a transcript) recorded by
    Pat and one by a teammate, a decision, a review item, a task, a project, a milestone, a sprint,
    a recurring meeting, a notification and (for the teams) an integration."""
    c = Cast()
    c.ada, c.pat, c.quinn, c.zed = world.ada, world.pat, world.quinn, world.zed
    c.x, c.y, c.z = world.x, world.y, world.z
    for team in (c.x, c.y):  # an admin may read the removed-recurring list; a member may not
        session.query(TeamMember).filter_by(team_id=team.id, user_id=c.pat.id).update(
            {"role": "admin"}
        )
    c.team = {"p": teams.personal_team(session, c.pat.id), "x": c.x, "y": c.y}
    for key, team in c.team.items():  # team names carry a TEAM_ marker, apart from the DATA_ ones
        team.name = f"TEAM_{key.upper()}"
    c.meetings = {}
    connections = {}
    for key, team in c.team.items():  # one calendar connection per workspace (W3)
        connections[key] = CalendarConnection(
            user_id=c.pat.id,
            team_id=team.id,
            provider="google",
            status="connected",
            granted_scopes="https://www.googleapis.com/auth/calendar.events.readonly",
            account_email="pat@example.test",
        )
        session.add(connections[key])
    session.flush()
    for key, team in c.team.items():
        connection = connections[key]
        mark = MARKS[key]
        fields = {
            "notes": NOTES.format(m=mark),
            "speaker_transcript": TRANSCRIPT.format(m=mark),
            "title": f"{mark} standup",
        }
        shared = {} if key == "p" else {"shared_team_id": team.id}
        mine = ready(session, template, owner_user_id=c.pat.id, **fields, **shared)
        c.meetings[key] = mine
        if key != "p":  # a teammate's meeting shared to the same team
            boss = c.ada if key == "x" else c.quinn
            ready(
                session,
                template,
                owner_user_id=boss.id,
                shared_team_id=team.id,
                notes=NOTES.format(m=mark),
                speaker_transcript=TRANSCRIPT.format(m=mark),
                title=f"{mark} planning",
            )
        session.add(
            Decision(
                meeting_id=mine.id,
                text=f"Decide {mark}",
                position=0,
                fingerprint=f"d-{key}",
            )
        )
        session.add(
            ReviewItem(
                meeting_id=mine.id,
                owner_user_id=c.pat.id,
                text=f"Check {mark}",
                position=0,
                fingerprint=f"r-{key}",
            )
        )
        session.add(
            Notification(
                user_id=c.pat.id,
                kind="k",
                title=f"{mark} note",
                dedupe_key=f"n-{key}",
                meeting_id=mine.id,
            )
        )
        session.add(
            Template(
                owner_user_id=c.pat.id, team_id=team.id, name=f"{mark} template", instructions="t"
            )
        )
        session.add(VocabularyTerm(owner_user_id=c.pat.id, team_id=team.id, term=f"{mark} term"))
        session.add(WorkspacePrefs(team_id=team.id, user_id=c.pat.id, bot_name=f"{mark} bot"))
        project = Project(team_id=team.id, name=f"{mark} project", color="#0E6B5E", keywords=[])
        session.add(project)
        session.flush()
        session.add(
            Milestone(
                team_id=team.id,
                project_id=project.id,
                name=f"{mark} milestone",
                due_date=date(2026, 12, 1),
                confirmed_at=datetime(2026, 11, 2, tzinfo=UTC),
            )
        )
        # a removed recurring meeting per workspace, named by Pat's own calendar event
        session.add(
            CalendarEvent(
                user_id=c.pat.id,
                connection_id=connection.id,
                source_event_id=f"ev-{key}",
                recurring_event_id=f"rid-{key}",
                title=f"{mark} weekly sync",
                starts_at=datetime(2026, 11, 20, 9, 0, tzinfo=UTC),
                ends_at=datetime(2026, 11, 20, 10, 0, tzinfo=UTC),
            )
        )
        soon = datetime.now(UTC) + timedelta(days=2)  # ahead: Upcoming and a Recurring card
        session.add(
            CalendarEvent(
                user_id=c.pat.id,
                connection_id=connection.id,
                source_event_id=f"up-{key}",
                recurring_event_id=f"card-{key}",
                title=f"{mark} planning call",
                starts_at=soon,
                ends_at=soon + timedelta(hours=1),
                platform="google_meet",
                meeting_url=f"https://meet.google.com/abc-defg-{key}",
                native_id=f"abc-defg-{key}",
                organizer_self=True,
                self_response="accepted",
            )
        )
        gone = datetime.now(UTC) - timedelta(days=1)  # behind: a join the note taker missed
        missed = CalendarEvent(
            user_id=c.pat.id,
            connection_id=connection.id,
            source_event_id=f"past-{key}",
            title=f"{mark} missed call",
            starts_at=gone,
            ends_at=gone + timedelta(hours=1),
            platform="google_meet",
            meeting_url=f"https://meet.google.com/zzz-yyyy-{key}",
            native_id=f"zzz-yyyy-{key}",
        )
        session.add(missed)
        session.flush()
        session.add(
            ScheduledJoin(
                user_id=c.pat.id,
                team_id=team.id,
                calendar_event_id=missed.id,
                platform="google_meet",
                native_id=missed.native_id,
                meeting_url=missed.meeting_url,
                starts_at=gone,
                ends_at=gone + timedelta(hours=1),
                run_at=gone - timedelta(minutes=1),
                deadline_at=gone + timedelta(minutes=15),
                state="failed",
            )
        )
        session.add(
            SeriesIgnored(team_id=team.id, calendar_recurring_id=f"rid-{key}", created_by=c.pat.id)
        )
        session.add(Series(team_id=team.id, owner_user_id=c.pat.id, title=f"{mark} weekly"))
        add_task(session, team, title=f"{mark} task", assignee=c.pat)
        add_task(session, team, mine, title=f"{mark} follow-up", assignee=c.pat)
        if key != "p":
            session.add(
                Integration(
                    team_id=team.id,
                    kind="linear",
                    config_enc=b"x",
                    key_version=1,
                    label=f"{mark} engineering",
                )
            )
    session.add(Integration(team_id=c.x.id, kind="slack_webhook", config_enc=b"x", key_version=1))
    session.commit()
    # a stranger's workspace that Pat is not in, with its own marker
    c.hidden = ready(
        session,
        template,
        owner_user_id=c.zed.id,
        shared_team_id=c.z.id,
        notes=NOTES.format(m="DATA_Z"),
        title="DATA_Z standup",
    )
    session.commit()
    return c


@pytest.fixture
def calendar_on(app):
    """The calendar features are on, so its routes answer 200 instead of `calendar_unavailable`."""
    app.state.settings = app.state.settings.model_copy(
        update={
            "calendar_enabled": True,
            "google_client_id": "id.apps.example.test",
            "google_client_secret": "s3cret",
        }
    )


class NoSuchTeam:
    id = 999_999


def get(handle, path, team=None, **params):
    headers = {"X-Team-Id": str(team.id)} if team is not None else {}
    return handle.client.get(path, headers=headers, params=params)


def leaks(body: str, own: str) -> list[str]:
    """The markers of every workspace but `own` that appear in a response."""
    return [m for m in (*MARKS.values(), "DATA_Z") if m != MARKS[own] and m in body]


# ---- the guard ----------------------------------------------------------------------------


def test_every_workspace_data_route_honours_the_header(app, session, cast, calendar_on):
    pat = sign_in(app, PAT)
    for path in sorted(WORKSPACE_DATA):
        for key, team in cast.team.items():
            response = get(pat, path, team)
            assert response.status_code == 200, (path, key, response.text)
            assert leaks(response.text, key) == [], f"{path} with {key}'s header leaks"
            # the personal workspace's team name is a TEAM_ marker, other team names never leak
            for other in "pxy":
                if other != key and path in ("/api/team", "/api/team/members"):
                    assert f"TEAM_{other.upper()}" not in response.text, (path, key)
        # no header: the default workspace (Pat is in two teams, so it is the personal one)
        bare = get(pat, path)
        assert bare.status_code == 200, (path, bare.text)
        assert leaks(bare.text, "p") == [], f"{path} with no header leaks"
        personal = get(pat, path, cast.team["p"])
        same = (
            bare.json() == personal.json()
            if "json" in bare.headers["content-type"]
            else bare.text == personal.text
        )
        assert same, f"{path}: no header != personal"
        # a team the caller is not in is the same answer for every route: 404 not_found
        error_of(get(pat, path, cast.z), 404, "not_found")
        error_of(get(pat, path, NoSuchTeam), 404, "not_found")


def test_each_workspace_shows_its_own_data_on_the_list_routes(app, session, cast, calendar_on):
    """The leak check above would pass for an empty answer: prove the data IS there."""
    pat = sign_in(app, PAT)
    for key, team in cast.team.items():
        mark = MARKS[key]
        for path in (
            "/api/meetings",
            "/api/decisions",
            "/api/review",
            "/api/notifications",
            "/api/tasks",
            "/api/projects",
            "/api/milestones",
            "/api/series",
            "/api/calendar",
            "/api/calendar/recurring",
            "/api/not-recorded",
            "/api/templates",
            "/api/vocabulary",
            "/api/workspace-prefs",
        ):
            assert mark in get(pat, path, team).text, (path, key)
        assert f"TEAM_{key.upper()}" in get(pat, "/api/team", team).text
        if key != "p":
            assert f"{mark} engineering" in get(pat, "/api/integrations/linear", team).text
        slack = get(pat, "/api/integrations/slack", team).json()
        assert slack["connected"] is (key == "x")  # only X has Slack


def test_the_session_counts_the_active_workspace_and_lists_unread_per_other_one(app, session, cast):
    pat = sign_in(app, PAT)
    state = get(pat, "/api/session", cast.team["x"]).json()
    assert state["active_workspace"]["id"] == cast.x.id
    assert state["active_workspace"]["is_personal"] is False
    assert state["unread_notifications"] == 1  # X's own note only
    assert state["badges"]["review_open"] == 1
    others = {w["id"]: w for w in state["other_workspaces"]}
    assert set(others) == {cast.team["p"].id, cast.y.id}  # not X, not Z
    assert all(w["unread_notifications"] == 1 for w in others.values())
    # reading X's notifications clears X's dot and leaves the others
    assert pat.client.post(
        "/api/notifications/read-all",
        headers={"X-Team-Id": str(cast.x.id), "X-CSRF-Token": pat.csrf},
    ).json() == {"unread_notifications": 0}
    after = get(pat, "/api/session", cast.team["y"]).json()
    assert after["unread_notifications"] == 1
    assert {w["id"]: w["unread_notifications"] for w in after["other_workspaces"]}[cast.x.id] == 0
    assert "DATA_Z" not in str(after)
    # no header: the workspace the web opens on (Pat is in two teams: the personal one)
    bare = get(pat, "/api/session").json()
    assert bare["active_workspace"]["is_personal"] is True
    # ... which for someone in exactly one team is that team, as it has always been
    ada = sign_in(app, ADA)
    assert get(ada, "/api/session").json()["active_workspace"]["id"] == cast.x.id
    # a team the caller is not in quietly gives the default workspace (every page reads this first)
    stale = get(pat, "/api/session", cast.z).json()
    assert stale["active_workspace"]["is_personal"] is True
    assert cast.z.id not in {w["id"] for w in stale["other_workspaces"]}


def test_the_bell_counts_only_the_active_workspace_and_a_meeting_note_follows_its_meeting(
    app, session, cast
):
    pat = sign_in(app, PAT)
    for key, team in cast.team.items():
        rows = get(pat, "/api/notifications", team).json()["notifications"]
        assert [r["title"] for r in rows] == [f"{MARKS[key]} note"]
    # a note with no meeting (hours, an account notice) is in the personal workspace
    session.add(Notification(user_id=cast.pat.id, kind="hours", title="80 percent", dedupe_key="h"))
    session.commit()
    assert len(get(pat, "/api/notifications", cast.team["p"]).json()["notifications"]) == 2
    assert len(get(pat, "/api/notifications", cast.x).json()["notifications"]) == 1


def test_a_meeting_opened_by_id_from_another_workspace_answers_with_its_workspace(
    app, session, cast
):
    pat = sign_in(app, PAT)
    opened = get(pat, f"/api/meetings/{cast.meetings['x'].id}", cast.y)  # Y is the active one
    assert opened.status_code == 200
    body = opened.json()
    assert body["workspace_team_id"] == cast.x.id
    assert body["workspace"] == {
        "id": cast.x.id,
        "name": "TEAM_X",
        "is_personal": False,
        "kind": "team",
    }
    assert get(pat, f"/api/meetings/{cast.hidden.id}", cast.y).status_code == 404  # not Pat's


# ---- Ask --------------------------------------------------------------------------------


def test_ask_answers_only_from_the_active_workspace_even_though_the_person_can_read_both(
    app, session, cast
):
    fake = FakeAsk()
    app.dependency_overrides[services.get_openai_client] = lambda: fake
    pat = sign_in(app, PAT)
    for key, team in cast.team.items():
        fake.calls.clear()
        response = pat.client.post(
            "/api/ask",
            json={"question": "What was said about pricing?"},
            headers={"X-Team-Id": str(team.id), "X-CSRF-Token": pat.csrf},
        )
        assert response.status_code == 200, response.text
        sent = " ".join(passages_of(fake.calls[0]).values())
        assert MARKS[key] in sent, key
        assert leaks(sent, key) == [], f"Ask in {key} drew on another workspace"
    # the scope line counts the same meetings the answer may search
    counts = {
        key: get(pat, "/api/ask/scope", team).json()["meetings"] for key, team in cast.team.items()
    }
    # X also holds the shared meeting `world` made (3); Y has Pat's and Quinn's (2)
    assert counts == {"p": 1, "x": 3, "y": 2}


def test_ask_inside_a_team_workspace_still_obeys_readability(app, session, cast, template):
    """The workspace only narrows which READABLE meetings are searched: a teammate's unshared
    meeting filed in the team's workspace is never in Pat's answer."""
    hidden = ready(
        session,
        template,
        owner_user_id=cast.ada.id,
        shared_team_id=cast.x.id,
        notes=NOTES.format(m="DATA_SECRET"),
        speaker_transcript=TRANSCRIPT.format(m="DATA_SECRET"),
    )
    hidden.shared_team_id = None  # "keep private": still in X's workspace, readable by Ada only
    session.commit()
    fake = FakeAsk()
    app.dependency_overrides[services.get_openai_client] = lambda: fake
    pat = sign_in(app, PAT)
    pat.client.post(
        "/api/ask",
        json={"question": "What was said about pricing?"},
        headers={"X-Team-Id": str(cast.x.id), "X-CSRF-Token": pat.csrf},
    )
    assert "DATA_SECRET" not in " ".join(passages_of(fake.calls[0]).values())
    assert hidden.workspace_team_id == cast.x.id
    ada_meetings = get(sign_in(app, ADA), "/api/meetings", cast.x).json()["meetings"]
    assert hidden.id in {m["id"] for m in ada_meetings}
    assert hidden.id not in {m["id"] for m in get(pat, "/api/meetings", cast.x).json()["meetings"]}


def test_a_task_question_lists_only_the_active_workspaces_tasks(app, session, cast):
    fake = FakeAsk()
    app.dependency_overrides[services.get_openai_client] = lambda: fake
    pat = sign_in(app, PAT)
    for key, team in cast.team.items():
        response = pat.client.post(
            "/api/ask",
            json={"question": "What are my open tasks?"},
            headers={"X-Team-Id": str(team.id), "X-CSRF-Token": pat.csrf},
        )
        assert response.status_code == 200 and response.json()["kind"] == "tasks"
        text = response.text
        assert f"{MARKS[key]} task" in text
        assert leaks(text, key) == []
    assert fake.calls == []  # the tasks path makes no model call


# ---- the cutover guarantees the guard relies on ---------------------------------------------


def test_every_meeting_row_has_a_workspace_and_lists_nowhere_else(session, cast):
    rows = session.query(Meeting).all()
    assert rows and all(m.workspace_team_id is not None for m in rows)
    team_ids = {t.id for t in session.query(Team).all()}
    assert {m.workspace_team_id for m in rows} <= team_ids


def test_the_marker_helper_reports_a_leak():
    """The helper itself: a response that names another workspace's marker is reported."""
    assert leaks("... DATA_X ... DATA_P ...", "p") == ["DATA_X"]
    assert leaks("nothing", "x") == []


# ---- the gaps the first review found ----


def test_the_removed_recurring_list_shows_only_the_workspaces_marks(app, session, cast):
    pat = sign_in(app, PAT)
    for key, team in cast.team.items():
        text = get(pat, "/api/series/removed", team).text
        assert f"{MARKS[key]} weekly sync" in text, key
        assert leaks(text, key) == [], key


def test_the_sprint_boards_count_only_the_workspaces_tasks(app, session, cast):
    pat = sign_in(app, PAT)
    planned = {"p": 1, "x": 2, "y": 3}
    for key, team in cast.team.items():
        current = get(pat, "/api/sprints", team).json()["sprints"]
        sprint_id = next(row["id"] for row in current if row["state"] == "current")
        for n in range(planned[key]):
            add_task(session, team, title=f"{MARKS[key]} planned {n}", sprint_id=sprint_id)
    for key, team in cast.team.items():
        rows = get(pat, "/api/sprints", team).json()["sprints"]
        totals = [row["tasks_total"] for row in rows if row["state"] == "current"]
        assert totals == [planned[key]], (key, totals)


def test_the_ask_chips_are_built_from_the_active_workspace_only(app, session, cast):
    pat = sign_in(app, PAT)
    for key, team in cast.team.items():
        chips = get(pat, "/api/ask/scope", team).json()["suggestions"]
        assert chips, key
        assert leaks(" ".join(chips), key) == [], f"a chip of {key} names another workspace"
        assert any(MARKS[key] in chip for chip in chips), (key, chips)


def test_the_meetings_search_stays_in_the_active_workspace(app, session, cast):
    pat = sign_in(app, PAT)
    for key, team in cast.team.items():
        text = get(pat, "/api/meetings", team, q="pricing").text
        assert MARKS[key] in text, key
        assert leaks(text, key) == [], f"search in {key} leaks"
    assert get(pat, "/api/meetings", cast.z, q="pricing").status_code == 404


def test_a_person_in_exactly_one_team_gets_that_team_without_a_header(
    app, session, cast, calendar_on
):
    """Ada is in X and her own personal workspace only: no header is her default, X."""
    ada = sign_in(app, ADA)
    for path in sorted(WORKSPACE_DATA - {"/api/integrations/linear", "/api/integrations/slack"}):
        bare = get(ada, path)
        assert bare.status_code == 200, (path, bare.text)
        assert leaks(bare.text, "x") == [], f"{path} with no header leaks to Ada"
        explicit = get(ada, path, cast.x)
        same = (
            bare.json() == explicit.json()
            if "json" in bare.headers["content-type"]
            else bare.text == explicit.text
        )
        assert same, f"{path}: no header is not Ada's one team"
    error_of(get(ada, "/api/meetings", cast.y), 404, "not_found")


def test_decision_links_never_name_another_workspaces_titles(app, session, cast):
    """A decision in X replaced by a decision in Y, and linked to a task of Y: Pat reads both
    meetings, but while in X neither title shows (design: a title from another workspace never
    shows)."""
    from sqlalchemy import select

    d_x = session.scalars(select(Decision).where(Decision.fingerprint == "d-x")).one()
    d_y = session.scalars(select(Decision).where(Decision.fingerprint == "d-y")).one()
    d_x.status, d_x.replaced_by_id = "replaced", d_y.id
    task_y = session.scalars(
        select(Task).where(Task.team_id == cast.y.id, Task.title == "DATA_Y task")
    ).one()
    session.add(DecisionTask(decision_id=d_x.id, task_id=task_y.id))
    session.commit()
    pat = sign_in(app, PAT)
    in_x = get(pat, "/api/decisions", cast.x)
    assert in_x.status_code == 200
    assert "DATA_Y" not in in_x.text, "a decision of X names something of Y"
    [shown] = [d for g in in_x.json()["groups"] for d in g["decisions"] if d["id"] == d_x.id]
    assert shown["replaced_by"] is None and shown["related_tasks"] == []
    in_y = get(pat, "/api/decisions", cast.y)
    assert "Decide DATA_Y" in in_y.text and "DATA_X" not in in_y.text
