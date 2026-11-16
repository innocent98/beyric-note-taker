"""`app/entitlements.py` (billing stage S1): the person's state and hours left, and the decision the
new rule would make for a recording, read from grants with the injected clock.

Real Postgres, like the placement tests: the sums and the sorting are the point."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from app import entitlements
from app.config import Settings
from app.entitlements import NO_HOURS, NO_PLAN
from app.models import HourGrant, UsageAllocation, UsageEntry
from app.usage import email_hash
from tests.helpers import FakeClock
from tests.test_placement import add_live, make_template
from tests.test_web import make_settings, make_user

T0 = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
HOUR = 3600


@pytest.fixture
def clock():
    return FakeClock(T0)


@pytest.fixture
def ada(session):
    return make_user(session, "ada@example.test")


def grant(session, user, seconds, *, kind="pilot", starts=T0 - timedelta(days=1), expires=None):
    row = HourGrant(
        user_id=user.id,
        email_hash=email_hash(user.email),
        kind=kind,
        seconds=seconds,
        starts_at=starts,
        expires_at=expires,
    )
    session.add(row)
    session.commit()
    return row


def used(session, user, seconds, *, at=T0 - timedelta(hours=1), user_id=True) -> UsageEntry:
    row = UsageEntry(
        user_id=user.id if user_id else None,
        email_hash=email_hash(user.email),
        seconds=seconds,
        counted_at=at,
    )
    session.add(row)
    session.commit()
    return row


# ---- the person's hours ----


def test_a_pilot_grant_shows_pilot_and_its_hours(session, ada, clock):
    grant(session, ada, 3 * HOUR)
    s = entitlements.status(session, ada, clock.now())
    assert (s.state, s.seconds_left, s.unlimited, s.has_grant) == ("pilot", 3 * HOUR, False, True)
    d = entitlements.check_join(session, ada, clock.now())
    assert (d.allowed, d.reason, d.seconds_left) == (True, None, 3 * HOUR)


def test_usage_counted_after_the_grant_began_and_not_allocated_comes_off(session, ada, clock):
    grant(session, ada, 3 * HOUR)
    used(session, ada, HOUR, at=T0 - timedelta(hours=2))  # after the grant began (a day before)
    assert entitlements.status(session, ada, clock.now()).seconds_left == 2 * HOUR


def test_usage_counted_before_the_grant_began_is_already_netted_out(session, ada, clock):
    grant(session, ada, 3 * HOUR, starts=T0 - timedelta(hours=1))
    used(session, ada, HOUR, at=T0 - timedelta(hours=5))  # the migration took this off the grant
    assert entitlements.status(session, ada, clock.now()).seconds_left == 3 * HOUR


def test_an_allocated_recording_comes_off_its_grant_once_not_twice(session, ada, clock):
    g = grant(session, ada, 3 * HOUR)
    entry = used(session, ada, HOUR)
    session.add(UsageAllocation(usage_entry_id=entry.id, grant_id=g.id, seconds=HOUR))
    session.commit()
    assert entitlements.status(session, ada, clock.now()).seconds_left == 2 * HOUR


def test_a_spent_grant_refuses_with_no_hours_and_the_state_falls_to_none(session, ada, clock):
    grant(session, ada, HOUR)
    used(session, ada, HOUR)
    s = entitlements.status(session, ada, clock.now())
    assert (s.state, s.seconds_left, s.has_grant) == ("none", 0, True)
    d = entitlements.check_join(session, ada, clock.now())
    assert (d.allowed, d.reason, d.seconds_left) == (False, NO_HOURS, 0)


def test_usage_past_the_grant_never_shows_negative_hours(session, ada, clock):
    grant(session, ada, HOUR)
    used(session, ada, 3 * HOUR)  # a running meeting finished past zero
    assert entitlements.status(session, ada, clock.now()).seconds_left == 0


def test_a_person_with_no_grant_refuses_with_no_plan(session, ada, clock):
    s = entitlements.status(session, ada, clock.now())
    assert (s.state, s.seconds_left, s.has_grant) == ("none", 0, False)
    d = entitlements.check_join(session, ada, clock.now())
    assert (d.allowed, d.reason) == (False, NO_PLAN)


def test_a_grant_ends_at_its_expiry_and_not_before_its_start_as_the_clock_moves(
    session, ada, clock
):
    grant(session, ada, 3 * HOUR, starts=T0 + timedelta(hours=1), expires=T0 + timedelta(hours=5))
    assert entitlements.check_join(session, ada, clock.now()).reason == NO_PLAN  # not started
    clock.advance(2 * HOUR)
    assert entitlements.check_join(session, ada, clock.now()).allowed
    clock.advance(3 * HOUR)  # exactly the expiry: it has ended
    assert entitlements.check_join(session, ada, clock.now()).reason == NO_PLAN


def test_an_admin_has_no_limit(session, clock):
    admin = make_user(session, "admin@example.test", admin=True)
    used(session, admin, 50 * HOUR)
    s = entitlements.status(session, admin, clock.now())
    assert s.unlimited and s.seconds_left is None
    assert entitlements.check_join(session, admin, clock.now()).allowed


def test_a_meeting_still_running_comes_off_like_the_old_rule_counts_it(session, ada, clock):
    grant(session, ada, 3 * HOUR)
    live = add_live(session, ada, make_template(session, ada), "abc-defg-hij")
    live.started_polling_at = T0 - timedelta(hours=1)
    session.commit()
    assert entitlements.status(session, ada, clock.now()).seconds_left == 2 * HOUR


def test_a_running_meeting_is_capped_at_the_poll_limit(session, ada, clock):
    grant(session, ada, 10 * HOUR)
    live = add_live(session, ada, make_template(session, ada), "abc-defg-hij")
    live.started_polling_at = T0 - timedelta(hours=9)
    session.commit()
    s = entitlements.status(session, ada, clock.now(), max_poll_seconds=2 * HOUR)
    assert s.seconds_left == 8 * HOUR


# ---- whose hours they are ----


def test_two_accounts_never_share_hours(session, ada, clock):
    ben = make_user(session, "ben@example.test")
    grant(session, ada, 3 * HOUR)
    grant(session, ben, HOUR)
    used(session, ben, HOUR)
    assert entitlements.status(session, ada, clock.now()).seconds_left == 3 * HOUR
    assert entitlements.status(session, ben, clock.now()).seconds_left == 0


def test_grants_and_usage_are_found_by_the_email_fingerprint_too(session, ada, clock):
    """A deleted account's rows have no user id; a person who signed up again has theirs back."""
    session.add(
        HourGrant(
            user_id=None,
            email_hash=email_hash(ada.email),
            kind="pilot",
            seconds=3 * HOUR,
            starts_at=T0 - timedelta(days=1),
        )
    )
    session.commit()
    used(session, ada, HOUR, user_id=False)
    assert entitlements.status(session, ada, clock.now()).seconds_left == 2 * HOUR


# ---- the order hours are spent, and the states ----


def test_the_waterfall_takes_the_unallocated_spending_in_that_order(session, ada, clock):
    grant(session, ada, HOUR, kind="pack")
    grant(session, ada, 2 * HOUR, kind="plan_period")
    grant(session, ada, HOUR, kind="pilot")
    used(session, ada, HOUR)  # the pilot grant is spent
    s = entitlements.status(session, ada, clock.now())
    assert (s.state, s.seconds_left) == ("active", 3 * HOUR)  # plan hours show next
    used(session, ada, 2 * HOUR)
    s = entitlements.status(session, ada, clock.now())
    # the plan period is in force, so the person is still `active` (they pay): the pack hour is
    # all that is left to record with
    assert (s.state, s.seconds_left) == ("active", HOUR)
    assert entitlements.check_join(session, ada, clock.now()).allowed


def test_the_soonest_expiring_pack_is_spent_first(session, ada, clock):
    grant(session, ada, HOUR, kind="pilot")
    soon = grant(session, ada, HOUR, kind="pack", expires=T0 + timedelta(days=1))
    later = grant(session, ada, HOUR, kind="pack", expires=T0 + timedelta(days=30))
    used(session, ada, 2 * HOUR)  # the pilot hour, then the soonest pack
    held = entitlements.grants_in_force(session, ada, clock.now())
    assert [g.kind for g, _ in held] == ["pilot", "pack", "pack"]
    assert [g.id for g, _ in held[1:]] == [soon.id, later.id]
    assert entitlements.status(session, ada, clock.now()).seconds_left == HOUR  # the later pack


# ---- it only reads ----


def test_status_and_check_join_run_in_a_read_only_transaction(session, ada, clock):
    grant(session, ada, 3 * HOUR)
    used(session, ada, HOUR)
    session.execute(text("SET TRANSACTION READ ONLY"))  # Postgres refuses any write after this
    assert entitlements.status(session, ada, clock.now()).seconds_left == 2 * HOUR
    assert entitlements.check_join(session, ada, clock.now()).allowed
    session.rollback()


def test_the_module_imports_no_route():
    source = open(entitlements.__file__).read()
    assert "app.api" not in source and "app.web" not in source and "fastapi" not in source


# ---- the HOURS_MODE setting ----


def test_hours_mode_defaults_to_off_and_a_blank_line_means_off():
    assert make_settings().hours_mode == "off"
    assert make_settings(hours_mode="  ").hours_mode == "off"


@pytest.mark.parametrize("mode", ["off", "shadow", "enforce"])
def test_hours_mode_accepts_its_three_values(mode):
    assert make_settings(hours_mode=mode.upper()).hours_mode == mode


def test_hours_mode_refuses_anything_else_at_startup():
    with pytest.raises(ValueError, match="HOURS_MODE is 'on'"):
        Settings(_env_file=None, session_secret="x" * 40, app_env="test", hours_mode="on")
