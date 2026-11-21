"""Billing S2b: the reserve for a second concurrent meeting, the Skribby capacity default, the
original pilot total on screens and mails, and the review list (NULL default cap, a lowered app
default, deleted accounts, a failed allocation told to the admins).

Real Postgres, injected clock (`now=`), like `tests/test_hours_enforce.py` (its helpers)."""

import logging
from datetime import timedelta

import pytest
from sqlalchemy import select

from app import entitlements, hours, services
from app.config import SKRIBBY_BOT_CEILING, Settings
from app.models import (
    AppState,
    HourGrant,
    Meeting,
    Notification,
    ScheduledJoin,
    UsageAllocation,
    UsageEntry,
)
from app.placement import PlacementRefused, reserve_slot
from app.usage import email_hash
from tests.test_hours_enforce import (  # noqa: F401  (fixtures and helpers used by name)
    CODE,
    DAY,
    HOUR,
    RUNNER,
    T0,
    ada,
    ben,
    count,
    enforce_app,
    grant,
    load_script,
    mail_can_be_sent,
    modes,
    notices,
    payload,
    refusal,
    reserve,
    s1_usage,
    status,
    template_of,
    world,
)
from tests.test_join_runner import (  # noqa: F401
    at,
    cal,
    host,
    materialise,
    run,
    runner_has_led,
    state_of,
)
from tests.test_join_runner import notes as bell
from tests.test_placement import add_live
from tests.test_web import fake_bot, fake_openai, make_settings, make_user  # noqa: F401

MIN = 60


def running(session, user, code, started, *, event_seconds=None, status="bot_sent"):
    """A meeting of `user` that is on the call (or just placed), started at `started`; with
    `event_seconds` it was made by a calendar join whose event is that long."""
    meeting = add_live(session, user, template_of(session, user), code, status)
    meeting.started_polling_at = started
    if event_seconds is not None:
        session.add(
            ScheduledJoin(
                user_id=user.id,
                platform="google_meet",
                native_id=code,
                meeting_url=f"https://meet.google.com/{code}",
                starts_at=started,
                ends_at=started + timedelta(seconds=event_seconds),
                run_at=started,
                state="joined",
                deadline_at=started + timedelta(minutes=15),
                template_id=meeting.template_id,
                meeting_id=meeting.id,
            )
        )
    session.commit()
    return meeting


def decide(session, user, *, reserve_minutes=30, expected=None, now=T0):
    session.expire_all()
    return entitlements.check_join(
        session, user, now, reserve_seconds=reserve_minutes * MIN, expected_seconds=expected
    )


# ---- the reserve: a second concurrent meeting ----


def test_a_first_meeting_is_never_held_to_the_reserve(session, ada):
    grant(session, ada, 60)  # 60 seconds left, nothing running
    assert decide(session, ada).allowed


def test_a_second_meeting_on_the_last_minute_is_refused(session, ada):
    """The review's case: 60 s left, a meeting placed 20 s ago (counted as about 0 s)."""
    grant(session, ada, 60)
    running(session, ada, "run-aaaa-aaa", T0 - timedelta(seconds=20))
    d = decide(session, ada)
    assert (d.allowed, d.reason) == (False, entitlements.RESERVE)


@pytest.mark.parametrize(
    ("held_seconds", "allowed"),
    [
        (HOUR, True),  # 1 h: 30 min set aside for the running one, 30 min for the new one
        (HOUR - 1, False),
        (HOUR + 1, True),
    ],
)
def test_the_boundary_is_thirty_minutes_for_each_of_the_two(session, ada, held_seconds, allowed):
    grant(session, ada, held_seconds)
    running(session, ada, "run-aaaa-aaa", T0)
    assert decide(session, ada).allowed is allowed


@pytest.mark.parametrize(("held_minutes", "allowed"), [(120, True), (60, True), (59, False)])
def test_what_has_run_already_counts_toward_the_running_meetings_reserve(
    session, ada, held_minutes, allowed
):
    """20 minutes in: 20 are counted by the estimate, so only 10 more are set aside for it. The
    total need is the same 1 h as at the start (20 run + 10 set aside + 30 for the new one)."""
    grant(session, ada, held_minutes * MIN)
    running(session, ada, "run-aaaa-aaa", T0 - timedelta(minutes=20))
    assert decide(session, ada).allowed is allowed


def test_a_calendar_meetings_own_length_is_reserved_instead_of_thirty_minutes(session, ada):
    grant(session, ada, 2 * HOUR + 30 * MIN)
    running(session, ada, "run-aaaa-aaa", T0, event_seconds=2 * HOUR)  # a 2 h event
    assert decide(session, ada).allowed  # 2h30 - 2h = 30 min: just the new one's 30
    session.query(HourGrant).delete()
    grant(session, ada, 2 * HOUR + 29 * MIN)
    assert not decide(session, ada).allowed


def test_the_new_meetings_own_event_length_is_what_it_reserves(session, ada):
    grant(session, ada, 90 * MIN)
    running(session, ada, "run-aaaa-aaa", T0)  # 30 minutes set aside
    assert decide(session, ada, expected=60 * MIN).allowed  # 60 left after it: needs 60
    assert not decide(session, ada, expected=61 * MIN).allowed


def test_the_reserve_never_exceeds_the_poll_cap(session, ada):
    grant(session, ada, 13 * HOUR)
    running(session, ada, "run-aaaa-aaa", T0, event_seconds=10 * HOUR)  # capped at 6 h
    assert decide(session, ada, expected=10 * HOUR).allowed  # 13 - 6 = 7 >= 6 (capped too)


def test_zero_minutes_turns_the_reserve_off(session, ada):
    grant(session, ada, 60)
    running(session, ada, "run-aaaa-aaa", T0)
    assert decide(session, ada, reserve_minutes=0).allowed


def test_the_admin_and_other_people_are_not_held_by_someone_elses_meetings(session, ada, ben):
    admin = make_user(session, "admin@example.test", admin=True)
    grant(session, ada, 60)
    grant(session, ben, 10 * HOUR)
    running(session, ada, "run-aaaa-aaa", T0)
    running(session, admin, "adm-aaaa-aaa", T0)
    assert decide(session, ben).allowed  # Ada's reserve is not Ben's
    assert decide(session, admin).allowed  # unlimited
    assert not decide(session, ada).allowed


def reserve_in(session, user, mode, **settings):
    reserve_slot(
        session,
        user,
        platform="google_meet",
        native_id="new-aaaa-aaa",
        now=T0,
        settings=make_settings(hours_mode=mode, bot_max_local=5, user_bot_limit=5, **settings),
    )


def test_placement_refuses_a_second_meeting_under_enforce_with_reason_reserve(session, ada):
    grant(session, ada, 60)
    running(session, ada, "run-aaaa-aaa", T0 - timedelta(seconds=20))
    with pytest.raises(PlacementRefused) as caught:
        reserve_in(session, ada, "enforce")
    session.rollback()
    assert (caught.value.code, caught.value.reason) == ("hours_cap_reached", "reserve")
    refused = services.placement_refusal(caught.value)
    assert (refused.status, refused.code, refused.reason) == (403, "hours_cap_reached", "reserve")
    assert "set aside" in refused.message and "pilot" not in refused.message
    assert "@" not in refused.message


def test_placement_reads_the_reserve_from_the_setting(session, ada):
    grant(session, ada, 40 * MIN)
    running(session, ada, "run-aaaa-aaa", T0)
    reserve_in(session, ada, "enforce", hours_reserve_minutes=10)  # 10 + 10 <= 40: allowed
    with pytest.raises(PlacementRefused):
        reserve_in(session, ada, "enforce", hours_reserve_minutes=30)
    session.rollback()


@pytest.mark.parametrize("mode", ["off", "shadow"])
def test_with_the_flag_off_or_shadow_there_is_no_reserve(session, ada, mode):
    """The old cap rule alone decides: a second meeting on the last minute starts as before."""
    session.add(UsageEntry(user_id=ada.id, email_hash=email_hash(ada.email), seconds=5 * HOUR - 60))
    running(session, ada, "run-aaaa-aaa", T0)
    reserve_in(session, ada, mode)  # nothing raised


def test_the_session_says_a_second_meeting_cannot_start(session, ada, enforce_app):
    grant(session, ada, 3 * HOUR, starts=T0 - DAY)
    body = payload(enforce_app)
    assert body["can_record"] is True
    session.query(HourGrant).delete()
    session.commit()
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    grant(session, ada, 40 * MIN, starts=now - DAY)
    running(session, ada, "run-aaaa-aaa", now - timedelta(seconds=5))
    assert payload(enforce_app)["can_record"] is False  # 40 min cannot cover two reserves
    assert payload(enforce_app)["seconds_left"] > 0


def test_a_meeting_whose_bot_has_left_reserves_nothing(session, ada):
    """`processing`: the bot is gone and only the audio is not counted yet. 50 minutes held, a
    25-minute meeting just ended: a new link is a first meeting again, not a second one."""
    grant(session, ada, 50 * MIN)
    running(session, ada, "gone-aaaa-aaa", T0 - timedelta(minutes=25), status="processing")
    assert decide(session, ada).allowed
    session.query(Meeting).delete()
    session.commit()
    running(session, ada, "run-aaaa-aaa", T0 - timedelta(minutes=25))  # still in the call
    assert not decide(session, ada).allowed  # 25 left - 5 still set aside < 30


def test_two_join_rows_on_one_meeting_reserve_it_once(session, ada):
    grant(session, ada, 60 * MIN)
    meeting = running(session, ada, "run-aaaa-aaa", T0, event_seconds=30 * MIN)
    session.add(
        ScheduledJoin(
            user_id=ada.id,
            platform="google_meet",
            native_id="run-aaaa-aaa",
            meeting_url="https://meet.google.com/run-aaaa-aaa",
            starts_at=T0,
            ends_at=T0 + timedelta(minutes=30),
            run_at=T0,
            state="completed",
            deadline_at=T0,
            template_id=meeting.template_id,
            meeting_id=meeting.id,
        )
    )
    session.commit()
    assert decide(session, ada).allowed  # 30 set aside once, 30 for the new one: exactly 60


def test_a_negative_event_length_is_not_a_length(session, ada):
    grant(session, ada, 40 * MIN)
    running(session, ada, "run-aaaa-aaa", T0)
    assert not decide(session, ada, expected=-100).allowed  # falls back to the 30-minute reserve
    assert not decide(session, ada, expected=0).allowed


def test_two_placements_of_one_person_at_once_cannot_both_pass(engine, session, ada):
    """The reserve check and the new meeting row are under one placement lock: the second request
    waits for the first to commit its meeting, then sees it and is refused."""
    import threading
    import time

    from sqlalchemy.orm import Session as OrmSession

    grant(session, ada, 40 * MIN)
    template_id = template_of(session, ada).id
    outcome = {}

    def second():
        with OrmSession(engine) as other:
            try:
                reserve_slot(
                    other,
                    other.get(type(ada), ada.id),
                    platform="google_meet",
                    native_id="two-aaaa-aaa",
                    now=T0,
                    settings=make_settings(hours_mode="enforce", bot_max_local=5, user_bot_limit=5),
                )
                outcome["second"] = "allowed"
            except PlacementRefused as refused:
                outcome["second"] = refused.reason
            other.rollback()

    with OrmSession(engine) as first:
        user = first.get(type(ada), ada.id)
        reserve_slot(
            first,
            user,
            platform="google_meet",
            native_id="one-aaaa-aaa",
            now=T0,
            settings=make_settings(hours_mode="enforce", bot_max_local=5, user_bot_limit=5),
        )  # the first one passes and holds the lock
        worker = threading.Thread(target=second)
        worker.start()
        time.sleep(0.5)
        assert "second" not in outcome  # waiting for the lock
        first.add(
            Meeting(
                owner_user_id=user.id,
                url="https://meet.google.com/one-aaaa-aaa",
                platform="google_meet",
                native_id="one-aaaa-aaa",
                template_id=template_id,
                status="bot_sent",
                started_polling_at=T0,
            )
        )
        first.commit()  # the meeting row and the lock leave together
    worker.join(timeout=20)
    assert outcome == {"second": "reserve"}


# ---- calendar auto-join and the reserve ----


def test_a_skipped_meeting_for_the_reserve_has_its_own_reason_and_no_out_of_hours_notice(
    session, host, cal, world
):
    grant(session, world.user, 60, kind="plan_period", starts=T0 - 10 * DAY)
    running(session, world.user, "run-aaaa-aaa", T0 - timedelta(seconds=20))
    join = materialise(session, world, cal, "evt-a", code="aaa-bbbb-ccc")
    run(session, host, cal, at(-1), RUNNER)
    assert state_of(session, join) == ("skipped", "hours_reserved")
    assert host.bots_sent == 0
    assert bell(session, "usage.hours_out") == []  # they are not out of hours
    [told] = bell(session, "calendar.hours_reserved")
    assert told.title == "Hours are set aside for another meeting"
    assert "set aside" in told.body and "keep trying until" in told.body
    assert "did not join" not in told.body  # it usually does join once the other ends


def test_it_is_tried_again_when_the_other_meeting_has_ended(session, host, cal, world):
    grant(session, world.user, 60, kind="plan_period", starts=T0 - 10 * DAY)
    first = running(session, world.user, "run-aaaa-aaa", T0 - timedelta(seconds=20))
    join = materialise(session, world, cal, "evt-a", code="aaa-bbbb-ccc")
    run(session, host, cal, at(-1), RUNNER)
    assert state_of(session, join) == ("skipped", "hours_reserved")
    session.get(Meeting, first.id).status = "ready"  # the first meeting is over and counted
    session.commit()
    run(session, host, cal, at(1, 5), RUNNER)
    assert state_of(session, join) == ("joined", None)


def test_the_calendar_events_length_goes_to_the_placement(session, host, cal, world, monkeypatch):
    from app import join_runner, placement

    seen = []
    real = placement.place_bot

    def spy(session_, provider, req, **kwargs):
        seen.append(req.expected_seconds)
        return real(session_, provider, req, **kwargs)

    monkeypatch.setattr(join_runner, "place_bot", spy)
    grant(session, world.user, 10 * HOUR, kind="plan_period", starts=T0 - 10 * DAY)
    materialise(session, world, cal, "evt-a", code="aaa-bbbb-ccc", length=timedelta(minutes=45))
    run(session, host, cal, at(-1), RUNNER)
    assert seen == [45 * MIN]


def test_the_reserved_skip_reason_is_listed_like_the_other_hours_reasons():
    from app import calendar_text, gcal_sync, join_runner

    assert "hours_reserved" in join_runner.RETRYABLE_SKIPS
    assert "hours_reserved" not in join_runner.NOT_RECORDED_SKIPS
    assert "hours_reserved" in gcal_sync.MOVABLE_SKIPS
    assert "hours_reserved" in calendar_text.NOT_RECORDED_SKIP_REASONS
    assert "set aside" in calendar_text.not_recorded_text("skipped", "hours_reserved")


# ---- capacity ----


def settings(monkeypatch=None, **fields):
    return Settings(_env_file=None, **{"session_secret": "x" * 40, **fields})


def test_capacity_is_a_high_ceiling_by_default():
    assert settings().bot_max_local == 20
    assert settings(skribby_api_key="k").bot_max_local == 20
    assert SKRIBBY_BOT_CEILING == 20 < 25  # below the 25 concurrent bots our Skribby plan allows


def test_a_value_in_the_environment_wins(monkeypatch):
    monkeypatch.setenv("BOT_MAX_LOCAL", "3")
    assert settings(skribby_api_key="k").bot_max_local == 3
    assert settings().bot_max_local == 3


def test_the_old_name_still_works_and_a_value_of_one_stays_one(monkeypatch):
    assert settings(skribby_api_key="k", max_active_bots=4).bot_max_local == 4
    assert settings(skribby_api_key="k", bot_max_local=1).bot_max_local == 1


def test_the_per_person_limit_stays_two_by_default():
    assert settings(skribby_api_key="k").user_bot_limit == 2


def test_the_default_ceiling_lets_many_people_record_at_once(session, ada):
    others = [make_user(session, f"p{n}@example.test") for n in range(5)]
    for n, other in enumerate(others):
        running(session, other, f"oth-{n}00-aaa", T0)
    skribby = make_settings()
    reserve_slot(
        session, ada, platform="google_meet", native_id="new-aaaa-aaa", now=T0, settings=skribby
    )
    session.rollback()
    with pytest.raises(PlacementRefused) as caught:  # a ceiling of five is full with five running
        reserve_slot(
            session,
            ada,
            platform="google_meet",
            native_id="new-aaaa-aaa",
            now=T0,
            settings=make_settings(bot_max_local=5),
        )
    session.rollback()
    assert caught.value.code == "bot_busy"


# ---- the original pilot total on screens and mails ----


def pilot_after_use(session, user, pre_hours=4, left_hours=1):
    """A pilot account as migration 0049 left it: 4 h used of 5 before, so a 1 h grant."""
    grant(session, user, left_hours * HOUR, kind="pilot", starts=T0 - 10 * DAY)
    s1_usage(session, user, pre_hours * HOUR, T0 - 11 * DAY)


def test_a_pilot_person_sees_four_of_five_hours_not_zero_of_one(session, ada, enforce_app):
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    grant(session, ada, HOUR, kind="pilot", starts=now - 10 * DAY)
    s1_usage(session, ada, 4 * HOUR, now - 11 * DAY)
    body = payload(enforce_app)
    assert (body["used_seconds"], body["cap_seconds"]) == (4 * HOUR, 5 * HOUR)
    assert body["seconds_left"] == HOUR and body["state"] == "pilot"


def test_the_numbers_move_together_and_the_warning_is_still_of_what_is_held(session, ada):
    pilot_after_use(session, ada)
    count(session, ada, 2880, now=T0)  # 0.8 h of the 1 h held: 80%
    s = status(session, ada)
    assert s.warning == entitlements.LOW
    assert (s.display_offset_seconds, s.holdings_seconds) == (4 * HOUR, HOUR)
    [low] = notices(session, ada, "usage.hours_low")
    assert low.body.startswith("You have used 4.8 of your 5 recorded hours")


def test_an_account_over_its_old_cap_shows_the_ledger_as_both_numbers(session, ada):
    """Used 6 h of a 5 h cap: the grant is 0 and the screen reads 6 of 6, not a negative."""
    grant(session, ada, 0, kind="pilot", starts=T0 - 10 * DAY)
    s1_usage(session, ada, 6 * HOUR, T0 - 11 * DAY)
    s = status(session, ada)
    assert (s.display_offset_seconds, s.holdings_seconds, s.seconds_left) == (6 * HOUR, 0, 0)


def test_there_is_no_offset_once_the_pilot_grant_is_gone_or_for_a_person_without_one(
    session, ada, ben
):
    grant(session, ada, HOUR, kind="pilot", starts=T0 - 10 * DAY, expires=T0 - DAY)
    s1_usage(session, ada, 4 * HOUR, T0 - 11 * DAY)
    grant(session, ada, 20 * HOUR, kind="plan_period", starts=T0 - DAY)
    assert status(session, ada).display_offset_seconds == 0  # the pilot grant has ended
    grant(session, ben, 20 * HOUR, kind="plan_period", starts=T0 - DAY)
    assert status(session, ben).display_offset_seconds == 0


def test_a_payer_with_pilot_hours_left_sees_the_pilot_total_inside_both_numbers(session, ada):
    pilot_after_use(session, ada)
    grant(session, ada, 20 * HOUR, kind="plan_period", starts=T0 - DAY)
    s = status(session, ada)
    assert s.state == "active"
    assert (s.holdings_seconds + s.display_offset_seconds, s.seconds_left) == (25 * HOUR, 21 * HOUR)


# ---- the review list ----


def test_a_null_app_default_cap_does_not_crash_the_cap_gift(session, ada):
    from app import admin

    state = session.get(AppState, 1) or AppState(id=1)
    state.default_hours_cap_seconds = None
    session.add(state)
    session.commit()
    admin.update_user(session, ada, {"hours_cap_seconds": 10 * HOUR}, modes("enforce"))
    gifts = [g.seconds for g in session.scalars(select(HourGrant))]
    assert gifts == [5 * HOUR]  # cap 10 h against the fixed 5 h baseline, as migration 0049


def test_lowering_the_app_default_does_not_stack_the_gift_again(session, ada):
    from app import admin

    admin.update_user(session, ada, {"hours_cap_seconds": 10 * HOUR}, modes("enforce"))
    state = session.get(AppState, 1) or AppState(id=1)
    state.default_hours_cap_seconds = 3 * HOUR
    session.add(state)
    session.commit()
    admin.update_user(session, ada, {"hours_cap_seconds": 10 * HOUR}, modes("enforce"))
    assert [g.seconds for g in session.scalars(select(HourGrant))] == [5 * HOUR]


def test_a_deleted_account_gets_no_gift_and_the_script_skips_it(session, ada):
    script = load_script()
    ada.hours_cap_seconds = 10 * HOUR
    ada.status = "deleted"
    session.commit()
    assert hours.sync_cap_gift(session, ada) == 0
    assert script.reconcile(session, T0, 6 * HOUR) == []
    assert session.scalars(select(HourGrant)).all() == []


def test_a_failed_allocation_is_told_to_the_admins_once_an_hour_without_a_name(
    session, ada, caplog, monkeypatch
):
    admin_user = make_user(session, "admin@example.test", admin=True)
    grant(session, ada, 10 * HOUR)

    def boom(*args, **kwargs):
        raise RuntimeError("detail ada@example.test")

    monkeypatch.setattr(hours, "allocate", boom)
    with caplog.at_level(logging.ERROR, logger="app.hours"):
        first = count(session, ada, 100, code="f-aaaa-aaa")
        count(session, ada, 100, code="g-aaaa-aaa")  # the same hour: no second alert
    assert first is not None
    session.expire_all()
    alerts_ = list(
        session.scalars(
            select(Notification).where(
                Notification.user_id == admin_user.id,
                Notification.kind == "admin.hours_allocation_failed",
            )
        )
    )
    assert len(alerts_) == 1
    assert f"user id {ada.id}" in alerts_[0].body and "ada@" not in alerts_[0].body
    assert "reconcile" in alerts_[0].body
    assert "ada@" not in caplog.text


def test_the_spend_order_line_in_the_spec_matches_the_code():
    """Spec 3.2: trial or pilot, plan hours oldest first, then gifts and packs by soonest expiry."""
    from pathlib import Path

    spec = (
        Path(__file__).parent.parent
        / "docs/superpowers/specs/2026-11-15-billing-and-plans-design.md"
    ).read_text()
    assert "then admin gifts and packs together, by earliest `expires_at` first" in spec
    assert entitlements.SPEND_RANK == {
        "trial": 0,
        "pilot": 0,
        "plan_period": 1,
        "admin_gift": 2,
        "pack": 2,
    }


# ---- the reconcile script with the real clock (the cap gift it makes counts at once) ----


def test_the_script_counts_the_gift_it_just_made_on_the_real_clock(
    session, engine, ada, monkeypatch
):
    """1 h pilot grant, 4 h used before it, the cap raised to 8 h during S1, then 2 h used. The
    gift (3 h) is stamped with the database's `now()`; a clock read before the session opened
    would leave it out of "grants in force" for the whole run, and she would end with 3 h left
    instead of 2 h. Left is 2 h in the end."""
    from datetime import UTC, datetime

    from sqlalchemy.orm import sessionmaker

    import app.db

    script = load_script()
    now = datetime.now(UTC)
    grant(session, ada, HOUR, kind="pilot", starts=now - 10 * DAY)
    s1_usage(session, ada, 4 * HOUR, now - 11 * DAY)
    s1_usage(session, ada, 2 * HOUR, now - 3 * DAY)  # used after the raise, during S1
    ada.hours_cap_seconds = 8 * HOUR
    session.commit()
    monkeypatch.setattr(app.db, "SessionLocal", sessionmaker(bind=engine))
    assert script.main([]) == 0  # the dry run keeps nothing
    session.expire_all()
    assert session.scalars(select(HourGrant).where(HourGrant.kind == "admin_gift")).all() == []
    assert script.main(["--apply"]) == 0
    session.expire_all()
    gifts_now = session.scalars(select(HourGrant).where(HourGrant.kind == "admin_gift")).all()
    assert [g.seconds for g in gifts_now] == [3 * HOUR]
    final = entitlements.status(session, ada, datetime.now(UTC))
    assert final.seconds_left == 2 * HOUR  # the old rule: 8 h cap - 6 h used
    spent = sorted(a.seconds for a in session.scalars(select(UsageAllocation)))
    assert spent == [HOUR, HOUR]  # the 2 h backlog: 1 h on the pilot grant, 1 h on the gift


def test_a_second_persons_failed_allocation_in_the_same_hour_is_told_too(
    session, ada, ben, monkeypatch
):
    admin_user = make_user(session, "admin@example.test", admin=True)
    grant(session, ada, 10 * HOUR)
    grant(session, ben, 10 * HOUR)

    def boom(*args, **kwargs):
        raise RuntimeError("x")

    monkeypatch.setattr(hours, "allocate", boom)
    count(session, ada, 100, code="f-aaaa-aaa")
    count(session, ben, 100, code="g-aaaa-aaa")
    session.expire_all()
    told = list(
        session.scalars(
            select(Notification).where(
                Notification.user_id == admin_user.id,
                Notification.kind == "admin.hours_allocation_failed",
            )
        )
    )
    assert sorted(
        f"user id {u.id}" in n.body for n in told for u in (ada, ben) if f"user id {u.id}" in n.body
    ) == [True, True]
    assert len(told) == 2
    assert all("manual look" in n.body for n in told)
