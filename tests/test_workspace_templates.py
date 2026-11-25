"""Templates and vocabulary belong to a workspace (workspaces design, section 3; plan Task 5).

Cast: Ada owns team X with Pat in it as a member; Quinn is an admin there. Everyone also has a
personal workspace. A workspace's owners and admins write its templates and terms; members read
and use them. Another workspace's rows are a 404, never a 403. A new workspace starts with the
seeded default templates. "Copy from my personal workspace" fills a workspace without overwriting
a name that is there.
"""

# ruff: noqa: F811  (fixtures are imported by name and used as arguments)
import pytest
from sqlalchemy import select

from app import accounts, teams
from app.models import Meeting, Team, TeamMember, Template, VocabularyTerm
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
    ready,
    template,
)
from tests.test_team_visibility import sign_in
from tests.test_web import make_user
from tests.workspace_plans import subscribe

ADA = "owner@example.test"
PAT, QUINN = "pat@example.test", "quinn@example.test"


def header(team) -> dict:
    return {"X-Team-Id": str(team.id)}


class Cast:
    pass


@pytest.fixture
def cast(session, template, owner) -> Cast:
    c = Cast()
    c.ada = owner
    c.pat, c.quinn = make_user(session, PAT), make_user(session, QUINN)
    for user in (c.pat, c.quinn):
        accounts.create_user_defaults(session, user)
    subscribe(session, c.ada, "plan_20h")  # owning a team needs a plan
    c.x = teams.create_owned_team(session, c.ada, "X")
    session.add_all(
        [
            TeamMember(team_id=c.x.id, user_id=c.pat.id, role="member"),
            TeamMember(team_id=c.x.id, user_id=c.quinn.id, role="admin"),
        ]
    )
    session.commit()
    c.personal = {u.id: teams.personal_team(session, u.id) for u in (c.ada, c.pat, c.quinn)}
    c.ada_session = None
    return c


def call(handle, method, path, team, json=None):
    return (
        handle.call(method, path, json=json)
        if team is None
        else handle.client.request(
            method, path, json=json, headers={**header(team), "X-CSRF-Token": handle.csrf}
        )
    )


# ---- a new workspace starts with the seeded defaults ----------------------------------------


def test_a_new_workspace_starts_with_the_seeded_default_templates(session, cast):
    rows = session.scalars(
        select(Template).where(Template.team_id == cast.x.id, Template.deleted_at.is_(None))
    ).all()
    assert sorted(r.name for r in rows) == sorted(name for name, _ in accounts.DEFAULT_TEMPLATES)
    assert {r.owner_user_id for r in rows} == {cast.ada.id}  # the creator made them


def test_a_new_workspace_shares_new_meetings_by_default_but_a_personal_one_does_not(session, cast):
    assert cast.x.share_new_meetings is True
    assert teams.personal_team(session, cast.ada.id).share_new_meetings is False


# ---- who may write -------------------------------------------------------------------------


def test_a_member_lists_and_reads_but_cannot_write_templates(app, session, cast):
    pat = sign_in(app, PAT)
    listed = call(pat, "GET", "/api/templates", cast.x)
    assert listed.status_code == 200
    names = {t["name"] for t in listed.json()["templates"]}
    assert names == {name for name, _ in accounts.DEFAULT_TEMPLATES}
    one = listed.json()["templates"][0]["id"]
    body = {"name": "Mine", "instructions": "Do it."}
    error_of(call(pat, "POST", "/api/templates", cast.x, body), 403, "team_forbidden")
    error_of(call(pat, "PUT", f"/api/templates/{one}", cast.x, body), 403, "team_forbidden")
    error_of(call(pat, "DELETE", f"/api/templates/{one}", cast.x), 403, "team_forbidden")


def test_an_owner_and_an_admin_write_the_workspaces_templates(app, session, cast):
    ada, quinn = sign_in(app, ADA), sign_in(app, QUINN)
    made = call(ada, "POST", "/api/templates", cast.x, {"name": "Board", "instructions": "Votes."})
    assert made.status_code == 201, made.text
    tid = made.json()["id"]
    changed = call(
        quinn, "PUT", f"/api/templates/{tid}", cast.x, {"name": "Board", "instructions": "x"}
    )
    assert changed.status_code == 200 and changed.json()["instructions"] == "x"
    assert call(quinn, "DELETE", f"/api/templates/{tid}", cast.x).status_code == 204
    # the admin's own personal workspace stays fully theirs
    own = call(
        quinn,
        "POST",
        "/api/templates",
        cast.personal[cast.quinn.id],
        {"name": "Z", "instructions": "z"},
    )
    assert own.status_code == 201


def test_a_name_is_unique_in_the_workspace_whoever_wrote_it(app, session, cast):
    ada, quinn = sign_in(app, ADA), sign_in(app, QUINN)
    assert (
        call(
            ada, "POST", "/api/templates", cast.x, {"name": "Board", "instructions": "a"}
        ).status_code
        == 201
    )
    again = call(quinn, "POST", "/api/templates", cast.x, {"name": "Board", "instructions": "b"})
    error_of(again, 400, "duplicate_name")


def test_a_member_lists_and_cannot_write_vocabulary_but_an_admin_can(app, session, cast):
    pat, quinn = sign_in(app, PAT), sign_in(app, QUINN)
    error_of(call(pat, "POST", "/api/vocabulary", cast.x, {"term": "Luran"}), 403, "team_forbidden")
    added = call(quinn, "POST", "/api/vocabulary", cast.x, {"term": "Luran"})
    assert added.status_code == 201, added.text
    seen = call(pat, "GET", "/api/vocabulary", cast.x).json()["terms"]
    assert [t["term"] for t in seen] == ["Luran"]
    error_of(
        call(pat, "DELETE", f"/api/vocabulary/{added.json()['id']}", cast.x), 403, "team_forbidden"
    )
    assert call(quinn, "DELETE", f"/api/vocabulary/{added.json()['id']}", cast.x).status_code == 204


# ---- the workspace's rows only ---------------------------------------------------------------


def test_templates_and_terms_list_per_workspace_and_another_workspaces_row_is_a_404(
    app, session, cast
):
    pat = sign_in(app, PAT)
    mine = cast.personal[cast.pat.id]
    own = call(
        pat, "POST", "/api/templates", mine, {"name": "Only mine", "instructions": "m"}
    ).json()
    call(pat, "POST", "/api/vocabulary", mine, {"term": "PERSONAL_TERM"})
    in_x = {t["name"] for t in call(pat, "GET", "/api/templates", cast.x).json()["templates"]}
    assert "Only mine" not in in_x
    in_p = {t["name"] for t in call(pat, "GET", "/api/templates", mine).json()["templates"]}
    assert "Only mine" in in_p
    assert "PERSONAL_TERM" not in call(pat, "GET", "/api/vocabulary", cast.x).text
    # the personal template addressed through X's header is not X's: 404, not 403
    error_of(
        call(pat, "PUT", f"/api/templates/{own['id']}", cast.x, {"name": "N", "instructions": "i"}),
        404,
        "not_found",
    )
    error_of(call(pat, "DELETE", f"/api/templates/{own['id']}", cast.x), 404, "not_found")
    # and a team the caller is not in is a 404 on the list
    stranger = Team(owner_user_id=cast.ada.id, name="Elsewhere", is_personal=False)
    session.add(stranger)
    session.commit()
    error_of(call(pat, "GET", "/api/templates", stranger), 404, "not_found")
    error_of(call(pat, "GET", "/api/vocabulary", stranger), 404, "not_found")


def test_the_last_template_of_a_workspace_cannot_be_deleted(app, session, cast):
    ada = sign_in(app, ADA)
    rows = call(ada, "GET", "/api/templates", cast.x).json()["templates"]
    for row in rows[:-1]:
        assert call(ada, "DELETE", f"/api/templates/{row['id']}", cast.x).status_code == 204
    error_of(call(ada, "DELETE", f"/api/templates/{rows[-1]['id']}", cast.x), 409, "last_template")


def test_deleting_a_template_a_meeting_used_keeps_the_row_marked_deleted(app, session, cast):
    ada = sign_in(app, ADA)
    rows = call(ada, "GET", "/api/templates", cast.x).json()["templates"]
    target = rows[0]["id"]
    meeting = Meeting(
        owner_user_id=cast.pat.id,
        url="https://meet.google.com/abc-defg-hij",
        platform="google_meet",
        native_id="abc-defg-hij",
        template_id=target,
        workspace_team_id=cast.x.id,
    )
    session.add(meeting)
    session.commit()
    assert call(ada, "DELETE", f"/api/templates/{target}", cast.x).status_code == 204
    session.expire_all()
    row = session.get(Template, target)
    assert row is not None and row.deleted_at is not None


# ---- members use the workspace's templates ----------------------------------------------------


def test_a_member_records_with_the_workspaces_template_but_not_a_personal_one(
    app, session, cast, fake_bot
):
    pat = sign_in(app, PAT)
    in_x = call(pat, "GET", "/api/templates", cast.x).json()["templates"][0]["id"]
    personal = session.scalars(
        select(Template).where(Template.team_id == cast.personal[cast.pat.id].id)
    ).first()
    url = "https://meet.google.com/abc-defg-hij"
    refused = call(pat, "POST", "/api/meetings", cast.x, {"url": url, "template_id": personal.id})
    error_of(refused, 400, "template_required")
    made = call(pat, "POST", "/api/meetings", cast.x, {"url": url, "template_id": in_x})
    assert made.status_code == 201, made.text


# ---- copy from my personal workspace ----------------------------------------------------------


def test_copy_templates_from_personal_skips_a_name_that_is_there(app, session, cast):
    ada = sign_in(app, ADA)
    mine = cast.personal[cast.ada.id]
    custom = call(
        ada, "POST", "/api/templates", mine, {"name": "Custom", "instructions": "personal"}
    )
    assert custom.status_code == 201
    # "General" exists in both (the seeded default), so it is skipped; "Custom" is copied
    done = call(ada, "POST", "/api/templates/copy-from-personal", cast.x)
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["copied"] == 1
    personal_names = {
        t["name"] for t in call(ada, "GET", "/api/templates", mine).json()["templates"]
    }
    in_x = {name for name, _ in accounts.DEFAULT_TEMPLATES}
    assert body["skipped"] == len(personal_names & in_x) >= 1
    names = {t["name"] for t in call(ada, "GET", "/api/templates", cast.x).json()["templates"]}
    assert "Custom" in names
    again = call(ada, "POST", "/api/templates/copy-from-personal", cast.x).json()
    assert again["copied"] == 0  # nothing is overwritten or doubled


def test_copy_from_personal_is_for_a_manager_of_a_team_workspace_only(app, session, cast):
    pat = sign_in(app, PAT)
    error_of(call(pat, "POST", "/api/templates/copy-from-personal", cast.x), 403, "team_forbidden")
    error_of(
        call(pat, "POST", "/api/templates/copy-from-personal", cast.personal[cast.pat.id]),
        409,
        "personal_team",
    )
    error_of(
        call(pat, "POST", "/api/vocabulary/copy-from-personal", cast.personal[cast.pat.id]),
        409,
        "personal_team",
    )


def test_copy_vocabulary_from_personal_skips_a_term_that_is_there(app, session, cast):
    ada = sign_in(app, ADA)
    mine = cast.personal[cast.ada.id]
    for term in ("Luran", "Beyric"):
        call(ada, "POST", "/api/vocabulary", mine, {"term": term})
    call(ada, "POST", "/api/vocabulary", cast.x, {"term": "Luran"})
    body = call(ada, "POST", "/api/vocabulary/copy-from-personal", cast.x).json()
    assert body["copied"] == 1 and body["skipped"] == 1
    terms = {t["term"] for t in call(ada, "GET", "/api/vocabulary", cast.x).json()["terms"]}
    assert terms == {"Luran", "Beyric"}
    rows = session.scalars(select(VocabularyTerm).where(VocabularyTerm.team_id == cast.x.id)).all()
    assert len(rows) == 2


def test_copying_leaves_the_personal_rows_where_they_are(app, session, cast):
    ada = sign_in(app, ADA)
    mine = cast.personal[cast.ada.id]
    call(ada, "POST", "/api/vocabulary", mine, {"term": "Keep"})
    call(ada, "POST", "/api/vocabulary/copy-from-personal", cast.x)
    assert [t["term"] for t in call(ada, "GET", "/api/vocabulary", mine).json()["terms"]] == [
        "Keep"
    ]
