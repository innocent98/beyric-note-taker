"""Meeting recipes, slice S3 (plan T10): the eighteen starters and the recipe library routes.

Cast: `owner` owns the team and (through `team`) is its owner; `admin` and `member` are on it;
`stranger` has a team of their own. In `world` (multi-team): Ada owns X, Pat is in X and Y.
"""

# ruff: noqa: F811  (fixtures are imported by name and used as arguments)
import pytest
from sqlalchemy import select

from app.models import RECIPE_OUTPUT_TYPES, RECIPE_SCOPES, Recipe
from app.recipes import library
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
from tests.test_api_tasks import ADMIN_EMAIL, MEMBER_EMAIL, admin, as_user, member, team  # noqa: F401
from tests.test_multi_team_leaks import PAT, multi_team_on, world  # noqa: F401
from tests.test_team_visibility import OTHER_EMAIL, outsider  # noqa: F401
from tests.test_web import stranger  # noqa: F401

GENERAL = (
    "follow_up_email",
    "proposal",
    "requirements",
    "marketing_post",
    "decision_memo",
    "sop",
    "action_tracker",
    "board",
    "status_report",
    "client_briefing",
)
SCHOOL = (
    "study_guide",
    "flashcards",
    "practice_quiz",
    "glossary",
    "assignments",
    "revision_plan",
    "class_recap",
    "class_faq",
)


def call(handle, method, path, body=None, team=None):
    headers = {"X-CSRF-Token": handle.csrf}
    if team is not None:
        headers["X-Team-Id"] = str(team.id if hasattr(team, "id") else team)
    return handle.client.request(method, path, json=body, headers=headers)


def listed(handle, team=None) -> list[dict]:
    headers = {"X-Team-Id": str(team.id if hasattr(team, "id") else team)} if team else {}
    response = handle.client.get("/api/recipes", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["recipes"]


def mine(body: dict | None = None, **fields) -> dict:
    return {
        "name": "Weekly note",
        "instruction": "Write a short note of what was said. Use only the meeting.",
        "output_type": "document",
        **(body or {}),
        **fields,
    }


# ---- the starters (code constants) ----


def test_there_are_eighteen_starters_ten_general_and_eight_school_in_the_planned_order():
    assert tuple(s.key for s in library.STARTERS) == GENERAL + SCHOOL
    assert tuple(s.key for s in library.STARTERS if s.pack == "general") == GENERAL
    assert tuple(s.key for s in library.STARTERS if s.pack == "school") == SCHOOL
    assert len({s.name for s in library.STARTERS}) == 18


def test_every_starter_has_valid_fields_that_fit_the_recipes_table():
    for s in library.STARTERS:
        assert s.output_type in RECIPE_OUTPUT_TYPES and s.scope in RECIPE_SCOPES, s.key
        assert 0 < len(s.key) <= 32 and 0 < len(s.name) <= 100, s.key
        assert 0 < len(s.instruction) <= library.MAX_INSTRUCTION_CHARS, s.key
        assert library.clean_options(s.options) == s.options, s.key  # the dialog can draw them


def test_the_output_types_and_scopes_follow_the_spec_tables():
    by_key = {s.key: s for s in library.STARTERS}
    assert {k for k, s in by_key.items() if s.output_type == "email"} == {
        "follow_up_email",
        "class_recap",
    }
    assert {k for k, s in by_key.items() if s.output_type == "table"} == {
        "action_tracker",
        "flashcards",
        "glossary",
        "assignments",
    }
    assert {k for k, s in by_key.items() if s.output_type == "board"} == {"board"}
    assert {k for k, s in by_key.items() if s.scope == "many"} == {
        "status_report",
        "client_briefing",
        "revision_plan",
    }
    assert by_key["proposal"].uses_brief is True


def test_every_starter_is_grounded_and_asks_for_blanks_not_guesses():
    for s in library.STARTERS:
        text = s.instruction
        assert "[need:" in text, s.key  # a missing fact is a blank
        assert "Use only" in text, s.key  # the meeting, notes, tasks and the brief, nothing else
        assert "never invent" in text.lower(), s.key


def test_a_school_recipe_leaves_an_answer_or_due_date_the_class_never_gave_as_a_blank():
    by_key = {s.key: s.instruction for s in library.STARTERS}
    assert "[need: due date]" in by_key["assignments"]
    assert "[need: answer]" in by_key["practice_quiz"]
    assert "[need: answer given in class]" in by_key["class_faq"]
    assert (
        "[need: homework]" in by_key["class_recap"] or "[need: due date]" in by_key["class_recap"]
    )
    assert "[need: exam date]" in by_key["revision_plan"]


def test_a_starter_does_not_ask_for_things_the_engine_cannot_render():
    for s in library.STARTERS:
        low = s.instruction.lower()
        assert "html" not in low and "markdown" not in low and "hyperlink" not in low, s.key


def test_option_definitions_are_cleaned_and_bounded():
    clean = library.clean_options(
        {
            "tone": {"label": " Tone ", "kind": "choice", "choices": ["Friendly", "Formal"]},
            "who": {"label": "Recipient", "kind": "text"},
        }
    )
    assert clean["tone"] == {
        "label": "Tone",
        "kind": "choice",
        "choices": ["Friendly", "Formal"],
        "default": "Friendly",
    }
    assert clean["who"] == {"label": "Recipient", "kind": "text"}
    for bad in (
        {"x": {"label": "", "kind": "text"}},
        {"x": {"label": "X", "kind": "slider"}},
        {"x": {"label": "X", "kind": "choice", "choices": []}},
        {"x": {"label": "X", "kind": "choice", "choices": ["a", "a"]}},
        {f"k{i}": {"label": "X", "kind": "text"} for i in range(library.MAX_OPTIONS + 1)},
        {"bad key!": {"label": "X", "kind": "text"}},
        {"x": "text"},
    ):
        with pytest.raises(ValueError):
            library.clean_options(bad)


# ---- the list ----


def test_the_list_carries_the_starters_first_with_their_pack_and_nothing_else_for_a_new_person(api):
    rows = listed(api)
    assert [r["key"] for r in rows] == list(GENERAL + SCHOOL)
    first = rows[0]
    assert first["source"] == "starter" and first["pack"] == "general" and first["id"] is None
    assert first["can_edit"] is False and first["name"] == "Follow-up email"
    assert rows[-1]["pack"] == "school"
    body = api.get("/api/recipes").json()
    assert (
        body["team"]["can_manage"] is True and body["team"]["is_personal"] is True
    )  # alone: the personal team
    assert isinstance(body["team"]["id"], int) and body["team"]["name"]


def test_a_logged_out_caller_is_refused(anon):
    assert anon.get("/api/recipes").status_code == 401


# ---- a personal recipe ----


def test_a_person_saves_edits_lists_and_archives_a_recipe_of_their_own(api, session, owner):
    made = api.post("/api/recipes", mine(options={"who": {"label": "Client", "kind": "text"}}))
    assert made.status_code == 201, made.text
    recipe = made.json()
    assert recipe["source"] == "personal" and recipe["pack"] is None and recipe["can_edit"] is True
    assert recipe["key"] is None and recipe["uses_brief"] is True and recipe["scope"] == "one"
    assert recipe["options"] == {
        "who": {"label": "Client", "kind": "text", "choices": [], "default": None}
    }
    assert [r["id"] for r in listed(api) if r["source"] == "personal"] == [recipe["id"]]

    edited = api.put(f"/api/recipes/{recipe['id']}", mine(name="Monthly note", uses_brief=False))
    assert edited.status_code == 200 and edited.json()["name"] == "Monthly note"
    assert edited.json()["uses_brief"] is False

    assert api.delete(f"/api/recipes/{recipe['id']}").status_code == 204
    assert [r for r in listed(api) if r["source"] != "starter"] == []  # archived: not listed
    row = session.get(Recipe, recipe["id"])
    session.refresh(row)
    assert row.archived_at is not None and row.name == "Monthly note"  # kept, drafts point at it
    error_of(api.put(f"/api/recipes/{recipe['id']}", mine()), 404, "not_found")
    error_of(api.delete(f"/api/recipes/{recipe['id']}"), 404, "not_found")


def test_a_personal_recipe_is_invisible_to_everyone_else(api, app, session, owner, outsider):
    made = api.post("/api/recipes", mine()).json()
    other = as_user(app, OTHER_EMAIL)
    assert [r for r in listed(other) if r["source"] != "starter"] == []
    error_of(other.put(f"/api/recipes/{made['id']}", mine(name="Mine now")), 404, "not_found")
    error_of(other.delete(f"/api/recipes/{made['id']}"), 404, "not_found")
    session.expire_all()
    assert session.get(Recipe, made["id"]).name == "Weekly note"
    assert session.get(Recipe, made["id"]).archived_at is None


def test_a_teammate_does_not_see_a_personal_recipe_either(api, app, member):
    made = api.post("/api/recipes", mine()).json()
    teammate = as_user(app, MEMBER_EMAIL)
    assert made["id"] not in [r["id"] for r in listed(teammate)]
    error_of(teammate.delete(f"/api/recipes/{made['id']}"), 404, "not_found")


def test_a_copy_of_a_starter_is_editable_and_remembers_which_starter_it_came_from(api, session):
    made = api.post("/api/recipes", {"key": "follow_up_email", "name": "Our follow-up"})
    assert made.status_code == 201, made.text
    copy = made.json()
    starter = next(s for s in library.STARTERS if s.key == "follow_up_email")
    assert copy["key"] == "follow_up_email" and copy["source"] == "personal"
    assert copy["instruction"] == starter.instruction  # not typed: taken from the starter
    assert copy["output_type"] == "email" and set(copy["options"]) == set(starter.options)
    assert copy["options"]["tone"]["choices"] == ["Friendly", "Formal"]
    changed = api.put(f"/api/recipes/{copy['id']}", mine(output_type="email", instruction="Mine."))
    assert changed.status_code == 200 and changed.json()["instruction"] == "Mine."
    assert changed.json()["key"] == "follow_up_email"
    assert session.scalars(select(Recipe.key).where(Recipe.id == copy["id"])).one() == (
        "follow_up_email"
    )


def test_a_copy_of_a_starter_that_does_not_exist_is_refused(api):
    error_of(api.post("/api/recipes", mine(key="nope")), 404, "not_found")


def test_a_recipe_needs_a_name_an_instruction_and_a_known_output_type(api):
    for body in (
        mine(name="  "),
        mine(instruction=""),
        mine(instruction="x" * (library.MAX_INSTRUCTION_CHARS + 1)),
        mine(output_type="video"),
        mine(scope="few"),
        mine(options={"x": {"label": "X", "kind": "slider"}}),
        {"name": "No instruction"},
    ):
        assert api.post("/api/recipes", body).status_code == 422, body
    assert [r for r in listed(api) if r["source"] != "starter"] == []


def test_the_number_of_recipes_a_person_keeps_is_bounded(api, monkeypatch):
    monkeypatch.setattr(library, "MAX_RECIPES", 2)
    assert api.post("/api/recipes", mine()).status_code == 201
    assert api.post("/api/recipes", mine()).status_code == 201
    error_of(api.post("/api/recipes", mine()), 422, "too_many_recipes")
    first = [r for r in listed(api) if r["source"] == "personal"][0]
    assert api.delete(f"/api/recipes/{first['id']}").status_code == 204
    assert api.post("/api/recipes", mine()).status_code == 201  # an archived one does not count


def test_a_write_without_the_csrf_token_is_refused(api):
    assert api.post("/api/recipes", mine(), csrf=False).status_code == 403


# ---- a team recipe: owners and admins write, members read and use ----


def test_the_owner_and_an_admin_write_team_recipes_and_a_member_reads_them(
    api, app, admin, member, session
):
    made = api.post("/api/recipes", mine(visibility="team", name="House proposal"))
    assert made.status_code == 201, made.text
    assert made.json()["source"] == "team" and made.json()["can_edit"] is True

    boss = as_user(app, ADMIN_EMAIL)
    both = boss.post("/api/recipes", mine(visibility="team", name="Admin's recipe"))
    assert both.status_code == 201
    names = [r["name"] for r in listed(boss) if r["source"] == "team"]
    assert names == ["House proposal", "Admin's recipe"]
    assert boss.put(f"/api/recipes/{made.json()['id']}", mine(name="House v2")).status_code == 200

    reader = as_user(app, MEMBER_EMAIL)
    seen = {r["name"]: r for r in listed(reader) if r["source"] == "team"}
    assert set(seen) == {"House v2", "Admin's recipe"}
    assert all(r["can_edit"] is False for r in seen.values())  # read, use, copy: not edit
    assert seen["House v2"]["instruction"].startswith("Write a short note")  # may read it


def test_a_member_cannot_create_edit_or_delete_a_team_recipe(api, app, admin, member, session):
    team_recipe = api.post("/api/recipes", mine(visibility="team")).json()
    reader = as_user(app, MEMBER_EMAIL)
    error_of(reader.post("/api/recipes", mine(visibility="team")), 403, "team_forbidden")
    error_of(
        reader.put(f"/api/recipes/{team_recipe['id']}", mine(name="Hijack")), 403, "team_forbidden"
    )
    error_of(reader.delete(f"/api/recipes/{team_recipe['id']}"), 403, "team_forbidden")
    session.expire_all()
    row = session.get(Recipe, team_recipe["id"])
    assert row.name == "Weekly note" and row.archived_at is None
    # a member can still write their own, and copy a team recipe's text into it
    assert reader.post("/api/recipes", mine(name="Mine")).status_code == 201


def test_a_stranger_gets_404_for_a_team_recipe_of_another_team(api, app, outsider):
    team_recipe = api.post("/api/recipes", mine(visibility="team")).json()
    other = as_user(app, OTHER_EMAIL)
    assert team_recipe["id"] not in [r["id"] for r in listed(other)]
    error_of(other.put(f"/api/recipes/{team_recipe['id']}", mine()), 404, "not_found")
    error_of(other.delete(f"/api/recipes/{team_recipe['id']}"), 404, "not_found")


def test_a_person_in_two_teams_sees_only_the_active_teams_recipes(
    app, session, world, multi_team_on
):
    ada = as_user(app, "owner@example.test")
    quinn = as_user(app, "quinn@example.test")
    assert call(ada, "POST", "/api/recipes", mine(visibility="team", name="X recipe"), world.x)
    assert call(quinn, "POST", "/api/recipes", mine(visibility="team", name="Y recipe"), world.y)
    pat = as_user(app, PAT)
    in_x = [r["name"] for r in listed(pat, world.x) if r["source"] == "team"]
    in_y = [r["name"] for r in listed(pat, world.y) if r["source"] == "team"]
    assert (in_x, in_y) == (["X recipe"], ["Y recipe"])
    assert pat.client.get("/api/recipes", headers={"X-Team-Id": str(world.z.id)}).status_code == 404
    # naming Y, Pat is only a member there: no team recipe from Y is theirs to write
    error_of(
        call(pat, "POST", "/api/recipes", mine(visibility="team"), world.y), 403, "team_forbidden"
    )
    assert listed(pat, world.x)[0]["key"] == "follow_up_email"
    assert listed(ada, world.x)[-1]["name"] == "X recipe"
    assert [r["can_edit"] for r in listed(ada, world.x) if r["source"] == "team"] == [True]


def test_only_a_team_recipe_made_on_the_active_team_goes_to_that_team(
    app, session, world, multi_team_on
):
    ada = as_user(app, "owner@example.test")
    made = call(ada, "POST", "/api/recipes", mine(visibility="team"), world.x).json()
    row = session.get(Recipe, made["id"])
    assert row.team_id == world.x.id and row.owner_user_id == world.ada.id


def test_the_engine_resolves_every_starter_and_a_copy_of_one(api, session, owner):
    """`service.resolve_recipe` (slice S1) reads `library.STARTERS`: all eighteen resolve with the
    scope and output type the list shows, and a copy resolves by its row id."""
    from app import teams
    from app.recipes import service

    team = teams.ensure_team(session, owner)
    session.commit()  # the app's own connection must see the team
    for s in library.STARTERS:
        got = service.resolve_recipe(session, owner, s.key, team.id)
        assert got.key == s.key and got.recipe_id is None
        assert (got.scope, got.uses_brief) == (s.scope, s.uses_brief)
        assert got.spec.output_type == s.output_type and got.spec.instruction == s.instruction
    copy = api.post("/api/recipes", {"key": "proposal", "name": "Ours"}).json()
    got = service.resolve_recipe(session, owner, copy["id"], team.id)
    assert got.recipe_id == copy["id"] and got.key == "proposal" and got.spec.name == "Ours"
