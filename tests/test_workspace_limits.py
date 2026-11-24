"""Workspaces, Task 10: how many team workspaces a person may OWN comes from their live plan
(`Plan.max_owned_teams`: Free 0, Starter 3, Pro 10, Scale unlimited), read through
`entitlements.max_owned_teams`. A trial counts as Free, being a member never counts, and a plan that
lapses or drops never deletes or locks a workspace: it only blocks making more (a plain 409
`team_limit` naming the plan to upgrade to). Real Postgres."""

# ruff: noqa: F811  (fixtures are imported by name and used as arguments)
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app import entitlements, teams
from app.models import HourGrant, Plan, Team
from app.usage import email_hash
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
from tests.test_multi_team_api import call, get
from tests.test_multi_team_leaks import PAT, ZED, new_team, world  # noqa: F401
from tests.test_team_visibility import sign_in
from tests.workspace_plans import seed_plans, subscribe

NOW = datetime.now(UTC)


def owned(session, user) -> list[Team]:
    return list(
        session.scalars(
            select(Team).where(Team.owner_user_id == user.id, Team.is_personal.is_(False))
        )
    )


def make_teams(session, user, count) -> None:
    for n in range(count):
        new_team(session, user, f"{user.id}-{n}")


# ---- the reader: each plan ----


@pytest.mark.parametrize(
    "code, limit",
    [("plan_20h", 3), ("plan_50h", 10), ("plan_100h", None)],
    ids=["starter", "pro", "scale"],
)
def test_each_plan_allows_what_the_plan_row_says(session, world, code, limit):
    seed_plans(session)
    subscribe(session, world.pat, code)
    assert entitlements.max_owned_teams(session, world.pat, NOW) == limit
    assert session.get(Plan, code).max_owned_teams == limit


def test_free_is_zero(session, world):
    assert entitlements.max_owned_teams(session, world.pat, NOW) == 0


def test_a_trial_counts_as_free(session, world):
    session.add(
        HourGrant(
            user_id=world.pat.id,
            email_hash=email_hash(world.pat.email),
            kind="trial",
            seconds=3 * 3600,
            starts_at=NOW - timedelta(days=1),
            expires_at=NOW + timedelta(days=13),
        )
    )
    session.commit()
    assert entitlements.max_owned_teams(session, world.pat, NOW) == 0


def test_the_plan_is_read_live_a_downgrade_waits_for_the_renewal(session, world):
    """The plan on the subscription row now; a scheduled downgrade (`pending_plan_code`) changes
    nothing until the renewal swaps the row's plan."""
    sub = subscribe(session, world.pat, "plan_50h")
    sub.pending_plan_code = "plan_20h"
    session.commit()
    assert entitlements.max_owned_teams(session, world.pat, NOW) == 10
    sub.plan_code, sub.pending_plan_code = "plan_20h", None
    session.commit()
    assert entitlements.max_owned_teams(session, world.pat, NOW) == 3


@pytest.mark.parametrize("status", ["lapsed", "canceled", "ended"])
def test_a_plan_that_ended_counts_as_free(session, world, status):
    subscribe(session, world.pat, "plan_50h", status=status)
    assert entitlements.max_owned_teams(session, world.pat, NOW) == 0


def test_a_plan_in_its_grace_days_keeps_its_allowance(session, world):
    subscribe(session, world.pat, "plan_50h", status="past_due")
    assert entitlements.max_owned_teams(session, world.pat, NOW) == 10


def test_a_plan_past_its_period_and_its_grace_counts_as_free_before_the_scheduler_runs(
    session, world
):
    """A subscription still `active` whose period and 72 hours of grace have passed (the renewal
    pass has not run) is judged by the clock, as everywhere in billing."""
    sub = subscribe(session, world.pat, "plan_50h")
    sub.current_period_start = NOW - timedelta(days=40)
    sub.current_period_end = NOW - timedelta(days=10)
    session.commit()
    assert entitlements.max_owned_teams(session, world.pat, NOW) == 0


def test_an_admin_is_never_limited(session, world):
    world.pat.is_admin = True
    session.commit()
    assert entitlements.max_owned_teams(session, world.pat, NOW) is None


# ---- creating ----


def test_free_cannot_own_a_team_and_is_told_which_plan_to_upgrade_to(app, session, world):
    seed_plans(session)
    pat = sign_in(app, PAT)
    body = error_of(call(pat, "POST", "/api/teams", body={"name": "Mine"}), 409, "team_limit")
    assert body["message"] == (
        "Owning a team needs a paid plan. "
        "Upgrade to the 20 hours a month plan to own up to 3 teams."
    )
    assert owned(session, world.pat) == []


def test_starter_owns_three_then_is_refused_and_pointed_at_pro(app, session, world):
    subscribe(session, world.pat, "plan_20h")
    pat = sign_in(app, PAT)
    for n in range(3):
        assert call(pat, "POST", "/api/teams", body={"name": f"T{n}"}).status_code == 201
    body = error_of(call(pat, "POST", "/api/teams", body={"name": "T3"}), 409, "team_limit")
    assert body["message"] == (
        "Your plan owns up to 3 teams and you have 3. "
        "Upgrade to the Pro plan to own up to 10 teams."
    )


def test_pro_owns_ten_and_is_pointed_at_scale(app, session, world):
    subscribe(session, world.pat, "plan_50h")
    make_teams(session, world.pat, 10)
    pat = sign_in(app, PAT)
    body = error_of(call(pat, "POST", "/api/teams", body={"name": "Eleven"}), 409, "team_limit")
    assert body["message"] == (
        "Your plan owns up to 10 teams and you have 10. Upgrade to the Scale plan for as many "
        "teams as you need."
    )


def test_scale_has_no_limit(session, world):
    subscribe(session, world.pat, "plan_100h")
    make_teams(session, world.pat, 11)
    teams.create_owned_team(session, world.pat, "Twelfth")
    assert len(owned(session, world.pat)) == 12


def test_a_team_one_only_belongs_to_never_counts(app, session, world):
    """Pat is a member of X and Y and owns nothing: all three of Starter's are still free."""
    subscribe(session, world.pat, "plan_20h")
    pat = sign_in(app, PAT)
    body = get(pat, "/api/teams").json()
    assert (body["owned_teams"], body["max_owned_teams"], body["can_create_more"]) == (0, 3, 3)
    for n in range(3):
        assert call(pat, "POST", "/api/teams", body={"name": f"T{n}"}).status_code == 201


def test_the_personal_workspace_never_counts(session, world):
    subscribe(session, world.pat, "plan_20h")
    assert teams.personal_team(session, world.pat.id) is not None
    assert teams.owned_team_count(session, world.pat) == 0


# ---- a plan that lapses or drops never touches a workspace ----


def test_a_lapsed_plan_keeps_every_workspace_and_blocks_new_ones(app, session, world):
    sub = subscribe(session, world.pat, "plan_50h")
    make_teams(session, world.pat, 5)
    kept = sorted(t.id for t in owned(session, world.pat))
    sub.status = "lapsed"
    session.commit()
    pat = sign_in(app, PAT)
    listing = get(pat, "/api/teams").json()
    assert {t["id"] for t in listing["teams"]} >= set(kept)  # all still there
    assert (listing["owned_teams"], listing["max_owned_teams"], listing["can_create_more"]) == (
        5,
        0,
        0,
    )
    assert listing["plan_name"] is None
    for team_id in kept:  # and still usable: the team's own screen answers
        assert get(pat, "/api/team", team=team_id).status_code == 200
    error_of(call(pat, "POST", "/api/teams", body={"name": "More"}), 409, "team_limit")
    assert sorted(t.id for t in owned(session, world.pat)) == kept  # nothing deleted


def test_a_downgrade_below_the_count_keeps_them_all_and_blocks_new_ones(app, session, world):
    sub = subscribe(session, world.pat, "plan_50h")
    make_teams(session, world.pat, 5)
    sub.plan_code = "plan_20h"  # the renewal applied the downgrade
    session.commit()
    pat = sign_in(app, PAT)
    listing = get(pat, "/api/teams").json()
    assert (listing["owned_teams"], listing["max_owned_teams"], listing["can_create_more"]) == (
        5,
        3,
        0,
    )
    assert listing["plan_name"] == "20 hours a month"
    error_of(call(pat, "POST", "/api/teams", body={"name": "More"}), 409, "team_limit")
    assert len(owned(session, world.pat)) == 5


def test_the_teams_list_reports_an_unlimited_plan_as_null(app, session, world):
    subscribe(session, world.pat, "plan_100h")
    body = get(sign_in(app, PAT), "/api/teams").json()
    assert body["max_owned_teams"] is None and body["plan_name"] == "Scale"
    assert body["owned_teams"] == 0 and body["can_create_more"] is None


# ---- ownership moves are checked against the new owner's limit ----


def test_a_hand_over_to_someone_at_their_limit_is_refused(session, world):
    subscribe(session, world.pat, "plan_20h")
    make_teams(session, world.pat, 3)  # Pat is at Starter's three
    with pytest.raises(teams.TeamChangeRefused) as refused:
        teams.transfer_owner(session, world.x.id, world.pat.id)  # Ada's X, Pat is a member
    assert (refused.value.status, refused.value.code) == (409, "team_limit")
    assert refused.value.message.startswith("That person can't take another team")
    session.rollback()
    assert session.get(Team, world.x.id).owner_user_id == world.ada.id


def test_a_hand_over_to_someone_with_room_goes_through(session, world):
    subscribe(session, world.pat, "plan_20h")
    teams.transfer_owner(session, world.x.id, world.pat.id)
    session.commit()
    assert session.get(Team, world.x.id).owner_user_id == world.pat.id


def test_a_hand_over_to_a_free_person_is_refused(session, world):
    with pytest.raises(teams.TeamChangeRefused) as refused:
        teams.transfer_owner(session, world.x.id, world.pat.id)  # Pat has no plan
    assert refused.value.code == "team_limit"


def test_an_owner_deleted_with_members_hands_the_team_to_the_first_member_with_room(session, world):
    """The purge path never refuses (`remove_users`): it prefers a member who has room."""
    team = new_team(session, world.quinn, "Big", world.zed, world.pat)  # joined: Zed then Pat
    subscribe(session, world.pat, "plan_20h")  # Zed is on Free, only Pat can own one
    teams.remove_users(session, [world.quinn.id])
    session.commit()
    assert session.get(Team, team.id).owner_user_id == world.pat.id


def test_an_owner_deleted_with_nobody_having_room_still_hands_the_team_on(session, world):
    """Never destroy a team over billing: the earliest member inherits it past their limit (the
    limit only stops them making more)."""
    team = new_team(session, world.quinn, "Big", world.zed, world.pat)
    teams.remove_users(session, [world.quinn.id])
    session.commit()
    kept = session.get(Team, team.id)
    assert kept is not None and kept.owner_user_id == world.zed.id


def test_the_old_constant_is_gone():
    assert not hasattr(teams, "MAX_OWNED_TEAMS")
