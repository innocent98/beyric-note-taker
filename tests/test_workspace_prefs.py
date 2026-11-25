"""What a person sets once per workspace (workspaces design, section 3; plan Task 7): the note
taker's name, the auto-join rule and its two modifiers, and the template their calendar's
meetings use.

One row per (workspace, person), falling back to the person's own defaults. Two workspaces hold
different rules for the same person, changing one never touches the other, and `JoinSettings` reads
the rule of the workspace a calendar connection lives in.
"""

# ruff: noqa: F811  (fixtures are imported by name and used as arguments)
import pytest
from sqlalchemy import select

from app import accounts, teams, workspace_prefs
from app.gcal_rules import JoinSettings
from app.models import CalendarConnection, TeamMember, Template, UserSettings, WorkspacePrefs
from tests.test_api import (  # noqa: F401  (fixtures used by name)
    anon,
    anon_client,
    api,
    app,
    client,
    error_of,
    fake_bot,
    fake_openai,
    owner,
    template,
)
from tests.test_team_visibility import sign_in
from tests.test_web import make_user
from tests.workspace_plans import subscribe

ADA, PAT = "owner@example.test", "pat@example.test"


class Cast:
    pass


@pytest.fixture
def cast(session, template, owner) -> Cast:
    c = Cast()
    c.ada = owner
    c.pat = make_user(session, PAT, bot_name="Pat default")
    accounts.create_user_defaults(session, c.pat)
    subscribe(session, c.ada, "plan_20h")  # owning a team needs a plan
    c.x = teams.create_owned_team(session, c.ada, "X")
    session.add(TeamMember(team_id=c.x.id, user_id=c.pat.id, role="member"))
    session.commit()
    c.p = teams.personal_team(session, c.pat.id)
    return c


def hdr(team, handle=None) -> dict:
    h = {"X-Team-Id": str(team.id)}
    if handle is not None:
        h["X-CSRF-Token"] = handle.csrf
    return h


def put(handle, team, body):
    return handle.client.put("/api/workspace-prefs", json=body, headers=hdr(team, handle))


def get(handle, team):
    return handle.client.get("/api/workspace-prefs", headers=hdr(team))


# ---- the model: a row, else the person's own defaults -----------------------------------------


def test_with_no_row_a_person_has_their_own_defaults(session, cast):
    own = session.get(UserSettings, cast.pat.id)  # sign-up made it
    own.auto_join_rule, own.skip_one_on_ones = "organizer", True
    session.commit()
    prefs = workspace_prefs.for_user(session, cast.pat.id, cast.x.id)
    assert prefs.auto_join_rule == "organizer" and prefs.skip_one_on_ones is True
    assert prefs.bot_name == "Pat default" and prefs.own_bot_name is None
    assert prefs.join_unanswered is False and prefs.calendar_template_id is None


def test_a_row_wins_and_a_missing_bot_name_falls_back_to_the_persons_default(session, cast):
    session.add(WorkspacePrefs(team_id=cast.x.id, user_id=cast.pat.id, auto_join_rule="none"))
    session.commit()
    prefs = workspace_prefs.for_user(session, cast.pat.id, cast.x.id)
    assert prefs.auto_join_rule == "none"
    assert prefs.bot_name == "Pat default"  # the workspace set none: their default applies
    row = session.get(WorkspacePrefs, (cast.x.id, cast.pat.id))
    row.bot_name = "Pat at X"
    session.commit()
    again = workspace_prefs.for_user(session, cast.pat.id, cast.x.id)
    assert again.bot_name == "Pat at X" and again.own_bot_name == "Pat at X"


def test_a_template_of_another_workspace_is_no_choice(session, cast):
    personal = session.scalars(select(Template).where(Template.team_id == cast.p.id)).first()
    session.add(
        WorkspacePrefs(team_id=cast.x.id, user_id=cast.pat.id, calendar_template_id=personal.id)
    )
    session.commit()
    assert workspace_prefs.for_user(session, cast.pat.id, cast.x.id).calendar_template_id is None
    # in the personal workspace it is a perfectly good choice
    here = workspace_prefs.for_user(session, cast.pat.id, cast.p.id).calendar_template_id
    assert here == personal.id


def test_join_settings_read_the_rule_of_the_workspace_asked_for(session, cast):
    session.add_all(
        [
            WorkspacePrefs(team_id=cast.x.id, user_id=cast.pat.id, auto_join_rule="none"),
            WorkspacePrefs(
                team_id=cast.p.id,
                user_id=cast.pat.id,
                auto_join_rule="organizer",
                join_unanswered=True,
                skip_one_on_ones=True,
            ),
        ]
    )
    session.commit()
    for_x = JoinSettings.for_user(
        workspace_prefs.for_user(session, cast.pat.id, cast.x.id), teams_join_enabled=True
    )
    for_p = JoinSettings.for_user(
        workspace_prefs.for_user(session, cast.pat.id, cast.p.id), teams_join_enabled=True
    )
    assert (for_x.rule, for_x.join_unanswered, for_x.skip_one_on_ones) == ("none", False, False)
    assert (for_p.rule, for_p.join_unanswered, for_p.skip_one_on_ones) == ("organizer", True, True)


# ---- the routes -------------------------------------------------------------------------------


def test_the_prefs_of_a_workspace_the_person_never_changed_are_their_defaults(app, session, cast):
    pat = sign_in(app, PAT)
    body = get(pat, cast.x).json()
    assert body == {
        "team_id": cast.x.id,
        "bot_name": None,
        "bot_name_default": "Pat default",
        "warning": None,
        "auto_join_rule": "all",
        "join_unanswered": False,
        "skip_one_on_ones": False,
        "calendar_template_id": None,
    }


def test_two_workspaces_hold_different_rules_and_changing_one_leaves_the_other(app, session, cast):
    pat = sign_in(app, PAT)
    assert put(pat, cast.x, {"auto_join_rule": "none", "bot_name": "Pat at X"}).status_code == 200
    assert (
        put(pat, cast.p, {"auto_join_rule": "organizer", "skip_one_on_ones": True}).status_code
        == 200
    )
    in_x, in_p = get(pat, cast.x).json(), get(pat, cast.p).json()
    assert (in_x["auto_join_rule"], in_x["bot_name"], in_x["skip_one_on_ones"]) == (
        "none",
        "Pat at X",
        False,
    )
    assert (in_p["auto_join_rule"], in_p["bot_name"], in_p["skip_one_on_ones"]) == (
        "organizer",
        None,
        True,
    )
    # a later change in X touches nothing in the personal workspace
    put(pat, cast.x, {"join_unanswered": True})
    assert get(pat, cast.p).json() == in_p
    assert get(pat, cast.x).json()["join_unanswered"] is True
    assert get(pat, cast.x).json()["auto_join_rule"] == "none"  # a field left out stays


def test_a_field_left_out_stays_and_null_clears_a_name(app, session, cast):
    pat = sign_in(app, PAT)
    put(pat, cast.x, {"bot_name": "Pat at X", "auto_join_rule": "none"})
    put(pat, cast.x, {"skip_one_on_ones": True})
    kept = get(pat, cast.x).json()
    assert kept["bot_name"] == "Pat at X" and kept["auto_join_rule"] == "none"
    put(pat, cast.x, {"bot_name": None})
    cleared = get(pat, cast.x).json()
    assert cleared["bot_name"] is None and cleared["bot_name_default"] == "Pat default"
    assert cleared["auto_join_rule"] == "none"


def test_a_bad_name_rule_or_template_is_refused_and_changes_nothing(app, session, cast):
    pat = sign_in(app, PAT)
    error_of(put(pat, cast.x, {"bot_name": "see https://evil.test now"}), 422, "bot_name_invalid")
    assert put(pat, cast.x, {"auto_join_rule": "sometimes"}).status_code == 422
    personal = session.scalars(select(Template).where(Template.team_id == cast.p.id)).first()
    error_of(put(pat, cast.x, {"calendar_template_id": personal.id}), 404, "not_found")
    assert get(pat, cast.x).json()["auto_join_rule"] == "all"
    assert session.get(WorkspacePrefs, (cast.x.id, cast.pat.id)) is None  # not even a row


def test_the_calendar_template_is_one_of_the_workspaces_own(app, session, cast):
    pat = sign_in(app, PAT)
    in_x = session.scalars(select(Template).where(Template.team_id == cast.x.id)).first()
    done = put(pat, cast.x, {"calendar_template_id": in_x.id})
    assert done.status_code == 200 and done.json()["calendar_template_id"] == in_x.id
    assert get(pat, cast.p).json()["calendar_template_id"] != in_x.id


def test_a_team_the_caller_is_not_in_is_a_404_and_a_write_needs_the_csrf_token(app, session, cast):
    pat = sign_in(app, PAT)
    other = teams.create_owned_team(session, cast.ada, "Other")
    session.commit()
    error_of(get(pat, other), 404, "not_found")
    error_of(put(pat, other, {"auto_join_rule": "none"}), 404, "not_found")
    bare = pat.client.put(
        "/api/workspace-prefs",
        json={"auto_join_rule": "none"},
        headers={"X-Team-Id": str(cast.x.id)},
    )
    assert bare.status_code == 403


def test_a_member_changes_their_own_row_only(app, session, cast):
    """Prefs are per person: Ada's row in X is not Pat's."""
    pat, ada = sign_in(app, PAT), sign_in(app, ADA)
    put(pat, cast.x, {"auto_join_rule": "none"})
    assert get(ada, cast.x).json()["auto_join_rule"] == "all"


# ---- the calendar screen follows the connection's workspace ---------------------------------


def test_the_calendar_settings_change_the_connections_workspace_row(session, cast):
    """`PUT /api/calendar/settings` and the sync read the rule of the workspace the connection
    lives in: here the person's calendar is connected in X."""
    from app import calendar_api

    connection = CalendarConnection(user_id=cast.pat.id, team_id=cast.x.id, provider="google")
    session.add(connection)
    session.commit()
    from tests.test_web import make_settings

    app_settings = make_settings()
    calendar_api.apply_prefs(
        session, cast.pat, connection, cast.x.id, app_settings, rule="none", skip_one_on_ones=True
    )
    assert workspace_prefs.for_user(session, cast.pat.id, cast.x.id).auto_join_rule == "none"
    assert workspace_prefs.for_user(session, cast.pat.id, cast.p.id).auto_join_rule == "all"
    assert session.get(UserSettings, cast.pat.id).auto_join_rule == "all"  # the default is intact
