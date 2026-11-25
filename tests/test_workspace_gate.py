"""Workspaces, Task 12 (spec 12.6, decided by Adebayo): `ask` and `make` pass when the CALLER has a
live plan or trial OR the workspace's OWNER has a live plan. `translate` and `rewrite` stay
caller-only. Daily caps stay per caller. A lapsed owner plan refuses, a free person in their own
personal workspace refuses, and the three `FEATURE_GATES_MODE`s all behave. Hours and plans stay per
person: this only widens who passes the gate on someone else's paid workspace. Real Postgres."""

# ruff: noqa: F811  (fixtures are imported by name and used as arguments)
import logging
from datetime import UTC, datetime

import pytest

from app import ask, entitlements, feature_gates, teams
from app.models import AiRun
from app.services import Refused
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
from tests.test_ask import meeting as ask_meeting
from tests.test_ask import model  # noqa: F401  (fixture used by name)
from tests.test_ask import post as post_ask
from tests.test_feature_gates import LOGGER, gate_lines, put_in_state, settings_for
from tests.test_multi_team_leaks import PAT, world  # noqa: F401
from tests.test_team_visibility import sign_in
from tests.test_web import make_settings
from tests.workspace_plans import subscribe

NOW = datetime.now(UTC)
SHARED = ("ask", "make")
CALLER_ONLY = ("translate", "rewrite")


def allowed(session, user, feature, workspace=None) -> bool:
    return entitlements.gate(session, user, feature, NOW, workspace=workspace).allowed


def paid_owner(session, world):
    """Ada owns X and holds a plan; Pat is a member of X on Free."""
    put_in_state(session, world.ada, "active", NOW)
    return world.ada


# ---- the rule ----


def test_make_is_a_gated_feature_with_its_own_words():
    assert entitlements.MAKE in entitlements.FEATURES
    assert feature_gates.plan_inactive_message("make").startswith("Making documents needs")


@pytest.mark.parametrize("feature", SHARED)
def test_a_free_member_passes_ask_and_make_on_a_workspace_whose_owner_has_a_plan(
    session, world, feature
):
    paid_owner(session, world)
    assert allowed(session, world.pat, feature) is False  # on their own: no plan, no pass
    assert allowed(session, world.pat, feature, workspace=world.x) is True


@pytest.mark.parametrize("feature", CALLER_ONLY)
def test_translate_and_rewrite_stay_caller_only(session, world, feature):
    paid_owner(session, world)
    assert allowed(session, world.pat, feature, workspace=world.x) is False
    put_in_state(session, world.pat, "active", NOW)  # the day they pay for themselves
    assert allowed(session, world.pat, feature, workspace=world.x) is True


@pytest.mark.parametrize("feature", SHARED)
def test_a_lapsed_owner_plan_refuses_the_member(session, world, feature):
    subscribe(session, world.ada, "plan_20h", status="lapsed")
    assert allowed(session, world.pat, feature, workspace=world.x) is False


@pytest.mark.parametrize(
    "owner_state, covers",
    [
        ("active", True),
        ("active_hours_spent", True),  # they pay: Ask and Make use no recorded hours
        ("admin", True),
        ("trial", False),  # a trial is not a plan: it covers its owner, not a class
        ("pilot", False),
        ("none", False),
        ("trial_ended", False),
        ("pack_only", False),
    ],
)
def test_which_owner_states_cover_a_member(session, world, monkeypatch, owner_state, covers):
    from tests.test_feature_gates import in_state

    in_state(session, world.ada, owner_state, monkeypatch, now=NOW)
    assert entitlements.owner_has_live_plan(session, world.x, NOW) is covers
    for feature in SHARED:
        assert allowed(session, world.pat, feature, workspace=world.x) is covers


def test_an_owner_in_the_grace_days_still_covers_the_member(session, world):
    subscribe(session, world.ada, "plan_20h", status="past_due")
    assert entitlements.owner_has_live_plan(session, world.x, NOW) is True
    assert allowed(session, world.pat, "ask", workspace=world.x) is True


def test_a_free_person_in_their_own_personal_workspace_is_refused(session, world):
    personal = teams.personal_team(session, world.pat.id)
    for feature in SHARED:
        assert allowed(session, world.pat, feature, workspace=personal) is False
    assert entitlements.owner_has_live_plan(session, personal, NOW) is False


def test_another_workspace_with_a_free_owner_does_not_borrow_a_plan(session, world):
    paid_owner(session, world)  # Ada's plan covers X only; Pat's other team Y is Quinn's
    assert allowed(session, world.pat, "ask", workspace=world.y) is False
    assert allowed(session, world.pat, "ask", workspace=world.x) is True


def test_a_person_outside_the_workspace_is_not_covered_by_naming_it(session, world):
    """Defence in depth: the route has checked membership, the gate checks it again."""
    paid_owner(session, world)
    assert allowed(session, world.quinn, "ask", workspace=world.x) is False  # Quinn is not in X


def test_the_callers_own_plan_still_passes_wherever_they_are(session, world):
    put_in_state(session, world.pat, "active", NOW)
    for feature in entitlements.FEATURES:
        assert allowed(session, world.pat, feature) is True
        assert allowed(session, world.pat, feature, workspace=world.z) is True


def test_a_workspace_can_be_named_by_its_id_or_as_the_active_workspace(session, world):
    paid_owner(session, world)
    active = ask.ActiveTeam(id=world.x.id, personal=False)
    assert allowed(session, world.pat, "ask", workspace=active) is True
    assert allowed(session, world.pat, "ask", workspace=world.x.id) is True
    assert allowed(session, world.pat, "ask", workspace=999_999) is False


# ---- the three modes ----


@pytest.mark.parametrize("mode_name", ["off", "shadow", "enforce"])
def test_every_mode_with_a_paid_owner(session, world, caplog, mode_name):
    paid_owner(session, world)
    settings = settings_for(mode_name)
    for feature in SHARED:
        caplog.clear()
        with caplog.at_level(logging.INFO, logger=LOGGER):
            feature_gates.require(
                session, world.pat, feature, workspace=world.x, now=NOW, settings=settings
            )
        assert gate_lines(caplog) == []  # covered by the owner: nothing to log, nothing refused


@pytest.mark.parametrize("mode_name", ["off", "shadow", "enforce"])
def test_every_mode_with_a_lapsed_owner(session, world, caplog, mode_name):
    subscribe(session, world.ada, "plan_20h", status="lapsed")
    settings = settings_for(mode_name)
    for feature in SHARED:
        caplog.clear()
        with caplog.at_level(logging.INFO, logger=LOGGER):
            if mode_name == "enforce":
                with pytest.raises(Refused) as refused:
                    feature_gates.require(
                        session, world.pat, feature, workspace=world.x, now=NOW, settings=settings
                    )
                assert (refused.value.status, refused.value.code) == (403, "plan_inactive")
            else:
                feature_gates.require(
                    session, world.pat, feature, workspace=world.x, now=NOW, settings=settings
                )
        lines = gate_lines(caplog)
        assert len(lines) == (1 if mode_name == "shadow" else 0)


def test_caller_only_features_ignore_the_workspace_in_every_mode(session, world):
    paid_owner(session, world)
    for feature in CALLER_ONLY:
        feature_gates.require(
            session, world.pat, feature, workspace=world.x, now=NOW, settings=settings_for("off")
        )
        with pytest.raises(Refused):
            feature_gates.require(
                session,
                world.pat,
                feature,
                workspace=world.x,
                now=NOW,
                settings=settings_for("enforce"),
            )


# ---- Ask, end to end ----


@pytest.fixture
def member_asks(app, session, template, world, model, monkeypatch):
    """Pat (Free) asking in X, where Ada holds a plan and has shared a meeting; enforce mode."""
    monkeypatch.setattr(feature_gates, "get_settings", lambda: settings_for("enforce"))
    ask_meeting(
        session,
        template,
        title="Pricing review",
        owner_user_id=world.ada.id,
        shared_team_id=world.x.id,
    )
    reader = sign_in(app, PAT)
    reader.client.headers["X-Team-Id"] = str(world.x.id)
    return reader


def test_a_free_member_asks_on_a_paid_workspace_and_the_answer_comes_from_it(
    session, world, member_asks, model
):
    paid_owner(session, world)
    answered = post_ask(member_asks, "What did we decide about pricing?").json()
    assert answered["kind"] == "answer"
    assert model.calls  # the model ran, on the workspace's meetings


def test_the_same_member_in_their_personal_workspace_is_refused(session, world, member_asks, model):
    paid_owner(session, world)
    member_asks.client.headers["X-Team-Id"] = str(teams.personal_team(session, world.pat.id).id)
    error_of(post_ask(member_asks, "What did we decide about pricing?"), 403, "plan_inactive")
    assert model.calls == []


def test_a_lapsed_owner_refuses_the_member_end_to_end(session, world, member_asks, model):
    subscribe(session, world.ada, "plan_20h", status="lapsed")
    error_of(post_ask(member_asks, "What did we decide about pricing?"), 403, "plan_inactive")
    assert model.calls == []


def test_the_daily_cap_is_the_members_own_not_the_owners(
    session, world, member_asks, model, monkeypatch
):
    paid_owner(session, world)
    monkeypatch.setattr(ask, "get_settings", lambda: make_settings(ask_daily_limit=1))
    assert post_ask(member_asks, "What did we decide about pricing?").status_code == 200
    error_of(post_ask(member_asks, "What did we decide about pricing?"), 429, "ask_limit")
    runs = session.query(AiRun).filter(AiRun.kind == "ask").all()
    assert {r.user_id for r in runs} == {world.pat.id}  # counted against Pat, never against Ada
    assert ask.daily_count(session, world.ada.id, NOW) == 0
