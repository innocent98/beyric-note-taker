"""`HOURS_MODE=enforce` (billing stage S2): the per-person hours rule decides a join, each counted
recording is split across the person's grants, the 80% and 100% notices go once per person and
period, and `off` and `shadow` change nothing for anyone.

Real Postgres, like the placement tests: the sums, the lock and the one-notice rule are the point.
The clock is injected everywhere (`now=`); the one exception is the admin cap edit, which stamps a
grant with the database's own time."""

import logging
from datetime import timedelta

import pytest
from sqlalchemy import select

from app import alerts, calendar_text, entitlements, hours, usage
from app.models import (
    HourGrant,
    MailOutbox,
    Notification,
    Template,
    UsageAllocation,
    UsageEntry,
)
from app.placement import PlacementRefused, reserve_slot
from app.services import placement_refusal
from app.usage import email_hash
from tests.test_api import error_of
from tests.test_gcal_sync import make_world
from tests.test_join_runner import (  # noqa: F401  (fixtures used by name)
    T0,
    at,
    cal,
    host,
    materialise,
    run,
    runner_has_led,
    state_of,
)
from tests.test_join_runner import notes as bell
from tests.test_join_runner import outbox as mailbox
from tests.test_placement import add_live, make_template
from tests.test_web import make_settings, make_user

HOUR = 3600
DAY = timedelta(days=1)
CODE = "abc-defg-hij"


@pytest.fixture(autouse=True)
def mail_can_be_sent(monkeypatch):
    """Mail goes out (console provider outside production), as in the join runner tests."""
    settings = make_settings(admin_alert_email="ops@example.test")
    monkeypatch.setattr(alerts, "get_settings", lambda: settings)


@pytest.fixture
def ada(session):
    return make_user(session, "ada@example.test")


@pytest.fixture
def ben(session):
    return make_user(session, "ben@example.test")


def modes(mode: str):
    return make_settings(hours_mode=mode, bot_max_local=5, user_bot_limit=5)


def grant(session, user, seconds, *, kind="plan_period", starts=T0 - DAY, expires=None, **fields):
    row = HourGrant(
        user_id=user.id,
        email_hash=email_hash(user.email),
        kind=kind,
        seconds=seconds,
        starts_at=starts,
        expires_at=expires,
        **fields,
    )
    session.add(row)
    session.commit()
    return row


def template_of(session, user):
    """The person's one template (the name is unique per person)."""
    return session.scalars(
        select(Template).where(Template.owner_user_id == user.id)
    ).first() or make_template(session, user)


def count(session, user, seconds, *, mode="enforce", now=T0, code=None):
    """A recording of `seconds` counted for `user` the way the pipeline does: a meeting, then
    `usage.record_usage`. Returns the ledger entry."""
    meeting = add_live(
        session,
        user,
        template_of(session, user),
        code or f"c{seconds}-{now.timestamp():.0f}",
        "ready",
    )
    entry = usage.record_usage(session, meeting, seconds, now=now, settings=modes(mode))
    session.commit()
    return entry


def rows_of(session, entry) -> list[tuple[int, int]]:
    """`(grant id, seconds)` for one entry's allocations, by grant id."""
    session.expire_all()
    found = session.scalars(
        select(UsageAllocation)
        .where(UsageAllocation.usage_entry_id == entry.id)
        .order_by(UsageAllocation.grant_id)
    )
    return [(a.grant_id, a.seconds) for a in found]


def status(session, user, now=T0):
    session.expire_all()
    return entitlements.status(session, user, now)


def reserve(session, user, mode="enforce", now=T0):
    reserve_slot(
        session, user, platform="google_meet", native_id=CODE, now=now, settings=modes(mode)
    )


def refusal(session, user, mode="enforce", now=T0) -> PlacementRefused:
    with pytest.raises(PlacementRefused) as caught:
        reserve(session, user, mode, now)
    session.rollback()
    return caught.value


def notices(session, user, kind):
    session.expire_all()
    return list(
        session.scalars(
            select(Notification).where(Notification.user_id == user.id, Notification.kind == kind)
        )
    )


def mails_to(session, user):
    session.expire_all()
    return list(session.scalars(select(MailOutbox).where(MailOutbox.to_email == user.email)))


# ---- the spending order (spec 3.2 as decided for S2) ----


def kinds_in_order(session, user):
    session.expire_all()
    return [g.id for g, _ in entitlements.grants_in_force(session, user, T0)]


def test_hours_are_spent_trial_or_pilot_then_plan_then_gifts_and_packs_by_expiry(session, ada):
    far = grant(session, ada, HOUR, kind="pack")  # never expires: last of the third group
    pack_late = grant(session, ada, HOUR, kind="pack", expires=T0 + 60 * DAY)
    gift_soon = grant(session, ada, HOUR, kind="admin_gift", expires=T0 + 10 * DAY)
    plan = grant(session, ada, HOUR, kind="plan_period")
    pilot = grant(session, ada, HOUR, kind="pilot")
    pack_soon = grant(session, ada, HOUR, kind="pack", expires=T0 + 5 * DAY)
    trial = grant(session, ada, HOUR, kind="trial", expires=T0 + 14 * DAY)
    assert kinds_in_order(session, ada) == [
        trial.id,  # the trial ends before the pilot grant has an end: the sooner expiry first
        pilot.id,
        plan.id,
        pack_soon.id,  # then whatever ends soonest, a gift and a pack together
        gift_soon.id,
        pack_late.id,
        far.id,  # a grant with no expiry last
    ]


def test_a_gift_that_ends_later_than_a_pack_is_spent_after_it(session, ada):
    gift = grant(session, ada, HOUR, kind="admin_gift", expires=T0 + 30 * DAY)
    pack = grant(session, ada, HOUR, kind="pack", expires=T0 + 10 * DAY)
    assert kinds_in_order(session, ada) == [pack.id, gift.id]


def test_plan_periods_are_spent_oldest_first(session, ada):
    newer = grant(session, ada, HOUR, starts=T0 - DAY)
    older = grant(session, ada, HOUR, starts=T0 - 20 * DAY)
    assert kinds_in_order(session, ada) == [older.id, newer.id]


def test_a_paying_person_with_pilot_hours_left_is_active(session, ada):
    """Decided for S2: a plan period in force makes the person `active`, whatever else they hold
    (the S1 review's question: the state used to follow the spending order and showed `pilot`)."""
    grant(session, ada, 2 * HOUR, kind="pilot")
    grant(session, ada, 20 * HOUR, kind="plan_period", expires=T0 + 20 * DAY)
    s = status(session, ada)
    assert (s.state, s.seconds_left, s.holdings_seconds) == ("active", 22 * HOUR, 22 * HOUR)
    assert s.period_ends_at == T0 + 20 * DAY


def test_a_plan_with_its_hours_spent_is_still_active_and_refused_for_hours(session, ada):
    grant(session, ada, HOUR, kind="plan_period")
    count(session, ada, HOUR)
    assert status(session, ada).state == "active"
    assert refusal(session, ada).reason == "no_hours"


# ---- the 80% and 100% boundaries ----

HOLD = 10_000  # seconds held, so a percent is a hundred seconds


@pytest.mark.parametrize(
    ("used", "warning", "allowed"),
    [
        (7_990, None, True),  # 79.9%
        (8_000, "low", True),  # 80%
        (9_990, "low", True),  # 99.9%
        (10_000, "out", False),  # 100%
    ],
)
def test_the_boundaries_at_79_9_80_99_9_and_100_percent(session, ada, used, warning, allowed):
    grant(session, ada, HOLD)
    count(session, ada, used)
    s = status(session, ada)
    assert s.warning == warning
    assert (s.holdings_seconds, s.seconds_left) == (HOLD, HOLD - used)
    if allowed:
        reserve(session, ada)  # nothing raised
    else:
        assert refusal(session, ada).reason == "no_hours"
    kinds = {n.kind for n in session.scalars(select(Notification))}
    expected = {"low": {"usage.hours_low"}, "out": {"usage.hours_out"}}.get(warning, set())
    assert kinds == expected


def test_one_second_left_still_allows_a_recording_and_zero_does_not(session, ada):
    grant(session, ada, HOLD)
    count(session, ada, HOLD - 1)
    reserve(session, ada)
    count(session, ada, 1, code="zzz-zzzz-zzz")
    assert refusal(session, ada).reason == "no_hours"


def test_the_warning_percent_is_the_old_notices_fraction():
    assert entitlements.LOW_PERCENT == usage.LOW_HOURS_FRACTION * 100


# ---- the refusal: reasons and words ----


def test_no_grant_in_force_is_refused_as_no_plan(session, ada):
    refused = refusal(session, ada)
    assert (refused.code, refused.reason) == ("hours_cap_reached", "no_plan")


def test_an_expired_grant_is_no_plan_not_no_hours(session, ada):
    grant(session, ada, HOUR, starts=T0 - 10 * DAY, expires=T0 - DAY)
    assert refusal(session, ada).reason == "no_plan"


def test_the_refusal_words_do_not_say_pilot_or_give_an_address():
    for reason in (None, "no_hours", "no_plan"):
        message = placement_refusal(
            PlacementRefused("hours_cap_reached", cap_seconds=18_000, reason=reason)
        ).message
        assert "pilot" not in message.lower() and "hello@" not in message and "@" not in message
    names_the_cap = placement_refusal(PlacementRefused("hours_cap_reached", cap_seconds=18_000))
    assert "5 recorded hours" in names_the_cap.message and names_the_cap.reason is None
    assert (
        "any recorded hours"
        in placement_refusal(PlacementRefused("hours_cap_reached", reason="no_plan")).message
    )


def test_the_admin_is_never_capped_even_with_no_grants_and_a_lot_recorded(session):
    admin = make_user(session, "admin@example.test", admin=True)
    session.add(
        UsageEntry(user_id=admin.id, email_hash=email_hash(admin.email), seconds=999 * HOUR)
    )
    session.commit()
    reserve(session, admin)  # unlimited is checked before state
    entry = count(session, admin, 5 * HOUR, code="adm-aaaa-aaa")
    assert rows_of(session, entry) == []  # an admin holds no grants and gets no allocation
    assert notices(session, admin, "usage.hours_out") == []


def test_two_accounts_never_share_hours(session, ada, ben):
    grant(session, ada, 3 * HOUR)
    assert refusal(session, ben).reason == "no_plan"  # Ada's hours are not Ben's
    count(session, ben, 10 * HOUR, code="ben-aaaa-aaa")  # whatever Ben records...
    assert status(session, ada).seconds_left == 3 * HOUR  # ...takes nothing from Ada
    count(session, ada, HOUR, code="ada-aaaa-aaa")
    assert status(session, ada).seconds_left == 2 * HOUR
    assert refusal(session, ben).reason == "no_plan"


def test_a_running_meeting_takes_hours_from_the_person_whose_bot_it_is(session, ada, ben):
    from app.models import Meeting

    grant(session, ada, 2 * HOUR)
    grant(session, ben, 2 * HOUR)
    meeting = add_live(session, ada, template_of(session, ada), "run-aaaa-aaa", "bot_sent")
    session.get(Meeting, meeting.id).started_polling_at = T0 - timedelta(hours=1)
    session.commit()
    assert status(session, ada).seconds_left == HOUR
    assert status(session, ben).seconds_left == 2 * HOUR


# ---- a recording is split across grants ----


def test_a_recording_is_split_across_two_grants_in_the_spending_order(session, ada):
    plan = grant(session, ada, HOUR, kind="plan_period")
    pack = grant(session, ada, 5 * HOUR, kind="pack")
    entry = count(session, ada, 3 * HOUR)
    assert rows_of(session, entry) == [(plan.id, HOUR), (pack.id, 2 * HOUR)]
    assert status(session, ada).seconds_left == 3 * HOUR  # 6 h held, 3 h recorded


def test_a_second_recording_continues_where_the_first_stopped(session, ada):
    plan = grant(session, ada, 2 * HOUR, kind="plan_period")
    pack = grant(session, ada, 5 * HOUR, kind="pack")
    count(session, ada, HOUR + 1800, code="one-aaaa-aaa")
    second = count(session, ada, HOUR, code="two-aaaa-aaa")
    assert rows_of(session, second) == [(plan.id, 1800), (pack.id, 1800)]


def test_the_trial_goes_first_then_the_plan(session, ada):
    trial = grant(session, ada, HOUR, kind="trial", expires=T0 + 10 * DAY)
    plan = grant(session, ada, 10 * HOUR, kind="plan_period")
    entry = count(session, ada, 2 * HOUR)
    assert rows_of(session, entry) == [(trial.id, HOUR), (plan.id, HOUR)]


def test_an_allocation_is_written_once_per_entry(session, ada):
    grant(session, ada, 3 * HOUR)
    entry = count(session, ada, HOUR)
    assert hours.allocate(session, ada, entry, T0) == []  # already settled
    assert len(rows_of(session, entry)) == 1


def test_two_recordings_counted_at_once_do_not_spend_the_same_hour_twice(engine, session, ada):
    """Two workers count two of Ada's recordings at the same moment. The second waits for the
    first (the per-person lock), so it sees the plan hour already spent and takes the pack."""
    import threading
    import time

    from sqlalchemy.orm import Session as OrmSession

    plan = grant(session, ada, HOUR, kind="plan_period")
    pack = grant(session, ada, 5 * HOUR, kind="pack")
    ids = {}
    for code in ("one-aaaa-aaa", "two-aaaa-aaa"):
        ids[code] = add_live(session, ada, template_of(session, ada), code, "ready").id
    done = threading.Event()

    def count_second():
        with OrmSession(engine) as other:
            from app.models import Meeting

            usage.record_usage(
                other,
                other.get(Meeting, ids["two-aaaa-aaa"]),
                HOUR,
                now=T0,
                settings=modes("enforce"),
            )
            other.commit()
        done.set()

    with OrmSession(engine) as one:
        from app.models import Meeting

        usage.record_usage(
            one, one.get(Meeting, ids["one-aaaa-aaa"]), HOUR, now=T0, settings=modes("enforce")
        )  # allocated, not committed: Ada's lock is held
        worker = threading.Thread(target=count_second)
        worker.start()
        time.sleep(1.0)
        assert not done.is_set()  # the second count is waiting for the first
        one.commit()
    worker.join(timeout=20)
    assert done.is_set()
    session.expire_all()
    spent = {
        g.id: sum(
            a.seconds
            for a in session.scalars(
                select(UsageAllocation).where(UsageAllocation.grant_id == g.id)
            )
        )
        for g in (plan, pack)
    }
    assert spent == {plan.id: HOUR, pack.id: HOUR}


# ---- a running meeting finishes past zero ----


def test_a_running_meeting_finishes_and_is_counted_in_full_past_zero(session, ada):
    from app.models import Meeting

    plan = grant(session, ada, HOUR, kind="plan_period")
    meeting = add_live(session, ada, template_of(session, ada), "run-bbbb-bbb", "bot_sent")
    session.get(Meeting, meeting.id).started_polling_at = T0 - timedelta(minutes=30)
    session.commit()
    reserve(session, ada)  # 30 minutes held: a new recording would still start
    entry = usage.record_usage(
        session, session.get(Meeting, meeting.id), 2 * HOUR, now=T0, settings=modes("enforce")
    )
    session.commit()
    assert entry.seconds == 2 * HOUR  # counted in full, not cut at what was left
    assert session.get(Meeting, meeting.id).recorded_seconds == 2 * HOUR
    assert rows_of(session, entry) == [
        (plan.id, 2 * HOUR)
    ]  # the hour the grant lacked: written off
    s = status(session, ada)
    assert (s.seconds_left, s.warning) == (0, "out")  # never below zero
    assert refusal(session, ada).reason == "no_hours"  # but nothing new starts


def test_the_shortfall_is_written_off_and_not_taken_from_the_next_period(session, ada):
    grant(session, ada, HOUR, kind="plan_period", expires=T0 + DAY)
    count(session, ada, 3 * HOUR)  # two hours more than the period held
    later = T0 + 2 * DAY
    new = grant(session, ada, 20 * HOUR, kind="plan_period", starts=T0 + DAY)
    assert status(session, ada, later).seconds_left == 20 * HOUR  # nothing carried over
    entry = count(session, ada, HOUR, now=later, code="nxt-aaaa-aaa")
    assert rows_of(session, entry) == [(new.id, HOUR)]


def test_a_recording_with_no_grant_in_force_cannot_be_allocated_and_is_not_billed_later(
    session, ada
):
    entry = count(session, ada, HOUR)  # nothing to take it from
    assert rows_of(session, entry) == []
    grant(session, ada, 20 * HOUR)  # a plan bought afterwards
    assert status(session, ada).seconds_left == 20 * HOUR  # is not charged for it


# ---- the modes ----


def test_off_writes_no_allocation_and_raises_no_new_notice(session, ada):
    grant(session, ada, HOLD)
    entry = count(session, ada, HOLD, mode="off")
    assert rows_of(session, entry) == []
    assert {n.kind for n in session.scalars(select(Notification))} <= {"usage.hours_low"}
    assert notices(session, ada, "usage.hours_out") == []


def test_shadow_only_logs_it_writes_no_allocation_takes_no_lock_and_raises_no_new_notice(
    session, ada, monkeypatch
):
    grant(session, ada, HOLD)
    monkeypatch.setattr(
        hours, "after_counted", lambda *a, **k: pytest.fail("shadow must not write hours")
    )
    entry = count(session, ada, HOLD, mode="shadow")
    assert rows_of(session, entry) == []
    assert notices(session, ada, "usage.hours_out") == []
    assert notices(session, ada, "usage.hours_low") == []  # the old once-ever rule needs a cap


def test_enforce_does_not_raise_the_old_once_ever_notice(session, ada):
    grant(session, ada, HOLD)
    count(session, ada, 9_000)
    assert [n.dedupe_key for n in notices(session, ada, "usage.hours_low")] != ["usage.hours_low"]


def test_a_failure_while_allocating_never_stops_the_transcript(session, ada, caplog, monkeypatch):
    """Enforce: the bookkeeping is best effort. The recording is still counted and the meeting
    goes on; the failure is logged by class and ids, no message (it could carry parameters)."""
    grant(session, ada, HOLD)

    def boom(*args, **kwargs):
        raise RuntimeError("email ada@example.test hash abc")

    monkeypatch.setattr(hours, "allocate", boom)
    with caplog.at_level(logging.ERROR, logger="app.hours"):
        entry = count(session, ada, 100)
    assert entry is not None and entry.seconds == 100  # counted in the ledger
    assert session.get(UsageEntry, entry.id) is not None
    [line] = [r.getMessage() for r in caplog.records if "allocation failed" in r.getMessage()]
    assert line == f"hours allocation failed user={ada.id} entry={entry.id} error=RuntimeError"
    assert "ada@" not in caplog.text
    assert rows_of(session, entry) == []


def test_a_failure_in_the_notices_keeps_the_allocation_and_the_count(
    session, ada, caplog, monkeypatch
):
    plan = grant(session, ada, HOLD)

    def boom(*args, **kwargs):
        raise RuntimeError("no")

    monkeypatch.setattr(hours, "notify_out", boom)
    with caplog.at_level(logging.ERROR, logger="app.hours"):
        entry = count(session, ada, HOLD)
    assert rows_of(session, entry) == [(plan.id, HOLD)]
    assert f"hours notice failed user={ada.id} error=RuntimeError" in caplog.text
    assert session.get(UsageEntry, entry.id) is not None


def test_with_the_flag_off_or_shadow_the_old_cap_rule_alone_decides(session, ada):
    """Hours held and the cap used up: off and shadow refuse like today (no reason); enforce
    allows. No hours held and the cap free: off and shadow allow, enforce refuses."""
    grant(session, ada, 10 * HOUR)
    session.add(UsageEntry(user_id=ada.id, email_hash=email_hash(ada.email), seconds=5 * HOUR))
    session.commit()
    for mode in ("off", "shadow"):
        refused = refusal(session, ada, mode)
        assert (refused.code, refused.reason, refused.cap_seconds) == (
            "hours_cap_reached",
            None,
            5 * HOUR,
        )
    session.query(UsageEntry).delete()
    session.query(HourGrant).delete()
    session.commit()
    for mode in ("off", "shadow"):
        reserve(session, ada, mode)  # the cap is free: allowed, as today
    assert refusal(session, ada, "enforce").reason == "no_plan"


# ---- the notices: once per person and period ----


def test_the_80_percent_notice_goes_once_with_one_mail(session, ada):
    grant(session, ada, HOLD)
    count(session, ada, 7_990, code="a-aaaa-aaa")
    assert notices(session, ada, "usage.hours_low") == [] and mails_to(session, ada) == []
    count(session, ada, 10, code="b-bbbb-bbb")  # exactly 80%
    [low] = notices(session, ada, "usage.hours_low")
    assert low.title == hours.LOW_TITLE
    assert low.body.startswith("You have used 2.2 of the 2.8 recorded hours you have now")
    [mail] = mails_to(session, ada)
    assert (mail.template, mail.payload["title"]) == ("alert", hours.LOW_TITLE)
    count(session, ada, 500, code="c-cccc-ccc")  # still between 80% and 100%
    assert len(notices(session, ada, "usage.hours_low")) == 1 and len(mails_to(session, ada)) == 1


def test_the_100_percent_notice_goes_once_and_replaces_the_80_percent_one(session, ada):
    grant(session, ada, HOLD)
    count(session, ada, 5_000, code="a-aaaa-aaa")
    count(session, ada, 5_000, code="b-bbbb-bbb")  # 50% straight to 100%
    assert notices(session, ada, "usage.hours_low") == []  # the 80% one would be stale
    [out] = notices(session, ada, "usage.hours_out")
    assert (out.title, out.severity) == (hours.OUT_TITLE, "warning")
    assert "pilot" not in out.body and "@" not in out.body
    count(session, ada, 3 * HOUR, code="c-cccc-ccc")  # another recording past zero
    assert len(notices(session, ada, "usage.hours_out")) == 1
    assert [m.payload["title"] for m in mails_to(session, ada)] == [hours.OUT_TITLE]


def test_both_notices_are_raised_in_turn_as_the_hours_go(session, ada):
    grant(session, ada, HOLD)
    count(session, ada, 8_000, code="a-aaaa-aaa")
    count(session, ada, 2_000, code="b-bbbb-bbb")
    assert len(notices(session, ada, "usage.hours_low")) == 1
    assert len(notices(session, ada, "usage.hours_out")) == 1
    assert len(mails_to(session, ada)) == 2


def test_a_new_period_resets_the_notices(session, ada):
    grant(session, ada, HOLD, expires=T0 + 30 * DAY)
    count(session, ada, 10_000, code="a-aaaa-aaa")
    grant(session, ada, HOLD, starts=T0 + 30 * DAY, expires=T0 + 60 * DAY)
    later = T0 + 31 * DAY
    count(session, ada, 8_000, now=later, code="b-bbbb-bbb")
    assert len(notices(session, ada, "usage.hours_low")) == 1  # told again, in the new period
    count(session, ada, 2_000, now=later, code="c-cccc-ccc")
    assert len(notices(session, ada, "usage.hours_out")) == 2


def test_a_person_who_switched_email_off_gets_the_bell_but_no_mail(session, ada):
    from app.models import UserSettings

    session.add(UserSettings(user_id=ada.id, notify_email=False))
    grant(session, ada, HOLD)
    count(session, ada, HOLD, code="a-aaaa-aaa")
    assert len(notices(session, ada, "usage.hours_out")) == 1 and mails_to(session, ada) == []


def test_the_notices_go_to_the_person_whose_bot_recorded_not_to_anyone_else(session, ada, ben):
    grant(session, ada, HOLD)
    grant(session, ben, HOLD)
    count(session, ada, HOLD, code="a-aaaa-aaa")
    assert len(notices(session, ada, "usage.hours_out")) == 1
    assert notices(session, ben, "usage.hours_out") == [] and mails_to(session, ben) == []


# ---- calendar auto-join: the skip reason and one notice for many skipped meetings ----

RUNNER = make_settings(
    autojoin_lead_seconds=60,
    bot_max_local=5,
    user_bot_limit=5,
    teams_join_enabled=False,
    hours_mode="enforce",
)


@pytest.fixture
def world(session):
    return make_world(session, "ada@example.test")


def spend_directly(session, user, seconds, grant_row):
    """Hours used up without going through `record_usage` (so no notice has been raised yet)."""
    entry = UsageEntry(
        user_id=user.id,
        email_hash=email_hash(user.email),
        seconds=seconds,
        counted_at=T0 - timedelta(hours=1),
    )
    session.add(entry)
    session.flush()
    session.add(UsageAllocation(usage_entry_id=entry.id, grant_id=grant_row.id, seconds=seconds))
    session.commit()


def test_skipped_meetings_for_want_of_hours_raise_one_notice_not_one_each(
    session, host, cal, world
):
    plan = grant(session, world.user, HOUR, kind="plan_period", starts=T0 - 10 * DAY)
    spend_directly(session, world.user, HOUR, plan)
    first = materialise(session, world, cal, "evt-a", code="aaa-bbbb-ccc")
    second = materialise(session, world, cal, "evt-b", code="ddd-eeee-fff")
    run(session, host, cal, at(-1), RUNNER)
    run(session, host, cal, at(0), RUNNER)  # and again: still one
    assert state_of(session, first) == ("skipped", "hours_cap")
    assert state_of(session, second) == ("skipped", "hours_cap") and host.bots_sent == 0
    [out] = bell(session, "usage.hours_out")
    assert out.title == hours.OUT_TITLE
    assert bell(session, "calendar.hours_cap") == []  # no per-meeting notice
    assert [m.payload["title"] for m in mailbox(session) if m.to_email == world.user.email] == [
        hours.OUT_TITLE
    ]


def test_no_hours_held_at_all_skips_with_no_plan_and_one_notice(session, host, cal, world):
    first = materialise(session, world, cal, "evt-a", code="aaa-bbbb-ccc")
    second = materialise(session, world, cal, "evt-b", code="ddd-eeee-fff")
    run(session, host, cal, at(-1), RUNNER)
    assert state_of(session, first) == ("skipped", "no_plan")
    assert state_of(session, second) == ("skipped", "no_plan")
    [out] = bell(session, "usage.hours_out")
    assert out.title == hours.NO_PLAN_TITLE
    assert calendar_text.not_recorded_text("skipped", "no_plan") == (
        "You have no recording hours at the moment."
    )
    assert "no_plan" in calendar_text.NOT_RECORDED_SKIP_REASONS


def test_a_skipped_meeting_is_tried_again_once_hours_arrive(session, host, cal, world):
    join = materialise(session, world, cal, "evt-a", code="aaa-bbbb-ccc")
    run(session, host, cal, at(-1), RUNNER)
    assert state_of(session, join) == ("skipped", "no_plan")
    grant(session, world.user, 5 * HOUR, kind="admin_gift", starts=T0 - DAY)
    run(session, host, cal, at(1, 5), RUNNER)
    assert state_of(session, join) == ("joined", None)


def test_with_the_flag_off_each_skipped_meeting_still_has_its_own_notice(session, host, cal, world):
    off = make_settings(
        autojoin_lead_seconds=60, bot_max_local=5, user_bot_limit=5, teams_join_enabled=False
    )
    session.add(
        UsageEntry(user_id=world.user.id, email_hash=email_hash(world.user.email), seconds=5 * HOUR)
    )
    session.commit()
    materialise(session, world, cal, "evt-a", code="aaa-bbbb-ccc")
    materialise(session, world, cal, "evt-b", code="ddd-eeee-fff")
    run(session, host, cal, at(-1), off)
    assert len(bell(session, "calendar.hours_cap")) == 2
    assert bell(session, "usage.hours_out") == []


# ---- the session payload ----


@pytest.fixture
def enforce_app(engine, fake_vexa, fake_openai):
    from sqlalchemy.orm import Session

    from app.db import get_session
    from app.main import create_app
    from app.services import get_openai_client, get_vexa

    app = create_app(make_settings(hours_mode="enforce"))

    def session_override():
        with Session(engine, expire_on_commit=False) as s:
            yield s

    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_vexa] = lambda: fake_vexa
    app.dependency_overrides[get_openai_client] = lambda: fake_openai
    return app


from tests.test_accounts import visitor  # noqa: E402
from tests.test_web import (  # noqa: E402,F401  (fixtures used by name)
    MEET,
    fake_openai,
    fake_vexa,
)


def payload(app, email="ada@example.test"):
    return visitor(app).log_in(email=email).json()["usage"]


def test_the_usage_payload_carries_the_new_fields_and_keeps_the_old_ones(session, ada, enforce_app):
    grant(session, ada, 10 * HOUR, kind="plan_period", starts=T0, expires=T0 + 30 * DAY)
    count(session, ada, 9 * HOUR, now=T0 + timedelta(minutes=1))
    body = payload(enforce_app)
    # Old names, now read from the person's grants: the bar is "used of held".
    assert (body["used_seconds"], body["cap_seconds"]) == (9 * HOUR, 10 * HOUR)
    assert body["state"] == "active" and body["seconds_left"] == HOUR
    assert (body["warning"], body["can_record"], body["unlimited"]) == ("low", True, False)
    assert body["period_ends_at"] is not None and body["trial_ends_at"] is None


def test_the_payload_for_a_person_with_no_hours(session, ada, enforce_app):
    body = payload(enforce_app)
    assert body == {
        "used_seconds": 0,
        "cap_seconds": 0,
        "state": "none",
        "seconds_left": 0,
        "warning": None,
        "can_record": False,
        "unlimited": False,
        "period_ends_at": None,
        "trial_ends_at": None,
    }


def test_the_payload_for_the_admin_is_unlimited(session, enforce_app):
    make_user(session, "admin@example.test", admin=True)
    body = payload(enforce_app, "admin@example.test")
    assert (body["cap_seconds"], body["unlimited"], body["can_record"]) == (None, True, True)
    assert (body["seconds_left"], body["warning"]) == (None, None)


def test_the_http_refusal_carries_the_reason_and_the_new_words(session, ada, enforce_app):
    template = make_template(session, ada)
    api = visitor(enforce_app)
    api.log_in(email="ada@example.test")
    error = error_of(
        api.post("/api/meetings", {"url": MEET, "template_id": template.id}),
        403,
        "hours_cap_reached",
    )
    assert error["reason"] == "no_plan"
    assert "pilot" not in error["message"] and "hello@" not in error["message"]
    grant(session, ada, HOUR)
    count(session, ada, HOUR, now=T0 + timedelta(days=1), code="x-xxxx-xxx")
    error = error_of(
        api.post("/api/meetings", {"url": MEET, "template_id": template.id}),
        403,
        "hours_cap_reached",
    )
    assert error["reason"] == "no_hours"


# ---- the usage counted before allocations existed (the S1 window) ----


def s1_usage(session, user, seconds, at):
    """What S1 left: a ledger row counted after the pilot grant, with no allocation."""
    row = UsageEntry(
        user_id=user.id, email_hash=email_hash(user.email), seconds=seconds, counted_at=at
    )
    session.add(row)
    session.commit()
    return row


def test_reconcile_changes_nobody_s_hours_left_and_is_idempotent(session, ada):
    pilot = grant(session, ada, 3 * HOUR, kind="pilot", starts=T0 - 10 * DAY)
    grant(session, ada, 20 * HOUR, kind="plan_period", starts=T0 - DAY)
    s1_usage(session, ada, HOUR, T0 - 5 * DAY)
    s1_usage(session, ada, 1800, T0 - 4 * DAY)
    before = status(session, ada)
    assert before.seconds_left == 3 * HOUR + 20 * HOUR - HOUR - 1800
    assert hours.reconcile_backlog(session, ada) == HOUR + 1800
    session.commit()
    after = status(session, ada)
    assert (after.seconds_left, after.holdings_seconds, after.state) == (
        before.seconds_left,
        before.holdings_seconds,
        before.state,
    )
    assert sorted(s for _, s in _allocations_on(session, pilot)) == [1800, HOUR]
    assert hours.reconcile_backlog(session, ada) == 0  # nothing more to settle
    assert status(session, ada).seconds_left == before.seconds_left


def _allocations_on(session, grant_row):
    session.expire_all()
    return [
        (a.usage_entry_id, a.seconds)
        for a in session.scalars(
            select(UsageAllocation).where(UsageAllocation.grant_id == grant_row.id)
        )
    ]


def test_without_reconcile_s1_usage_would_come_off_paid_hours_once_the_pilot_ends(session, ada):
    """The S1 review's case: Ada recorded 2 h while the pilot grant was in force, the pilot grant
    ends, she buys a plan. The backlog term would charge the plan; reconciled, it does not."""
    grant(session, ada, 3 * HOUR, kind="pilot", starts=T0 - 10 * DAY, expires=T0 + DAY)
    s1_usage(session, ada, 2 * HOUR, T0 - 5 * DAY)
    later = T0 + 2 * DAY  # the pilot grant has ended
    grant(session, ada, 20 * HOUR, kind="plan_period", starts=T0 + DAY)
    assert status(session, ada, later).seconds_left == 18 * HOUR  # the wrong answer, unreconciled
    hours.reconcile_backlog(session, ada)
    session.commit()
    assert status(session, ada, later).seconds_left == 20 * HOUR  # nobody loses an hour


def test_reconcile_writes_off_what_the_pilot_grant_could_not_cover(session, ada):
    pilot = grant(session, ada, HOUR, kind="pilot", starts=T0 - 10 * DAY)
    entry = s1_usage(session, ada, 3 * HOUR, T0 - 5 * DAY)  # a running meeting that overshot
    hours.reconcile_backlog(session, ada)
    session.commit()
    assert _allocations_on(session, pilot) == [(entry.id, 3 * HOUR)]
    grant(session, ada, 20 * HOUR, kind="plan_period")
    assert status(session, ada).seconds_left == 20 * HOUR


def test_a_recording_counted_now_is_not_taken_off_twice_beside_a_backlog(session, ada):
    pilot = grant(session, ada, 5 * HOUR, kind="pilot", starts=T0 - 10 * DAY)
    s1_usage(session, ada, HOUR, T0 - 5 * DAY)
    entry = count(session, ada, HOUR, code="new-aaaa-aaa")
    assert rows_of(session, entry) == [(pilot.id, HOUR)]  # taken after the backlog's hour
    assert status(session, ada).seconds_left == 3 * HOUR  # 5 - 1 backlog - 1 new, once each


def load_script():
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "hours_reconcile_backlog",
        Path(__file__).parent.parent / "scripts" / "hours_reconcile_backlog.py",
    )
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    return script


def test_the_reconcile_script_shows_a_dry_run_and_keeps_nothing_until_asked(session, ada):
    script = load_script()
    grant(session, ada, 3 * HOUR, kind="pilot", starts=T0 - 10 * DAY)
    s1_usage(session, ada, HOUR, T0 - 5 * DAY)
    rows = script.reconcile(session, T0, 6 * HOUR)
    text = script.render(rows, apply=False)
    session.rollback()  # the dry run
    assert session.scalars(select(UsageAllocation)).all() == []
    assert "settled 1.00 h; left 2.00 h before settling, 2.00 h after" in text
    assert "nothing was kept" in text and "HOURS CHANGE" not in text
    assert "ada" not in text.lower().replace("settled", "")  # a rank, never a name
    rows = script.reconcile(session, T0, 6 * HOUR)
    session.commit()
    assert len(session.scalars(select(UsageAllocation)).all()) == 1
    assert script.reconcile(session, T0, 6 * HOUR) == []


def spill_case(session, ada):
    """The review's case: pilot grant 1 h, 3 h counted during S1, plus 5 h of another grant."""
    grant(session, ada, HOUR, kind="pilot", starts=T0 - 10 * DAY)
    grant(session, ada, 5 * HOUR, kind="admin_gift", starts=T0 - DAY)
    s1_usage(session, ada, 3 * HOUR, T0 - 5 * DAY)


def test_the_script_prints_the_true_before_and_after_and_flags_a_change(session, ada):
    script = load_script()
    spill_case(session, ada)
    [row] = script.reconcile(session, T0, 6 * HOUR)
    assert (row.before, row.after, row.changes_hours) == (3 * HOUR, 5 * HOUR, True)
    text = script.render([row], apply=False)
    assert "left 3.00 h before settling, 5.00 h after  <-- HOURS CHANGE" in text
    assert "1 person(s) would change hours left" in text


def test_the_script_refuses_to_apply_while_any_hours_would_change(
    session, engine, ada, monkeypatch
):
    from sqlalchemy.orm import sessionmaker

    import app.db

    script = load_script()
    spill_case(session, ada)
    monkeypatch.setattr(app.db, "SessionLocal", sessionmaker(bind=engine))
    assert script.main(["--apply"]) == 2
    session.expire_all()
    assert session.scalars(select(UsageAllocation)).all() == []  # nothing was kept
    assert script.main([]) == 0  # the dry run still shows it


def test_the_script_applies_when_nothing_changes(session, engine, ada, monkeypatch):
    from sqlalchemy.orm import sessionmaker

    import app.db

    script = load_script()
    grant(session, ada, 3 * HOUR, kind="pilot", starts=T0 - 10 * DAY)
    s1_usage(session, ada, HOUR, T0 - 5 * DAY)
    monkeypatch.setattr(app.db, "SessionLocal", sessionmaker(bind=engine))
    assert script.main(["--apply"]) == 0
    session.expire_all()
    assert len(session.scalars(select(UsageAllocation)).all()) == 1


def test_a_pilot_grant_without_an_end_keeps_the_backlog_counted_past_another_ones_end(session, ada):
    grant(session, ada, 3 * HOUR, kind="pilot", starts=T0 - 10 * DAY)  # never ends
    grant(session, ada, HOUR, kind="pilot", starts=T0 - 10 * DAY, expires=T0 - 8 * DAY)
    s1_usage(session, ada, HOUR, T0 - 5 * DAY)  # after the second one ended
    assert entitlements.unallocated_seconds(session, ada) == HOUR


# ---- an admin raising a cap becomes a gift (derived from state, never stacked) ----


def gifts(session, user):
    session.expire_all()
    return [
        g.seconds
        for g in session.scalars(select(HourGrant).where(HourGrant.kind == "admin_gift"))
        if g.user_id == user.id
    ]


def edit_cap(session, user, cap, mode="enforce"):
    from app import admin

    admin.update_user(session, user, {"hours_cap_seconds": cap}, modes(mode))


def test_an_admin_raising_a_cap_gives_the_difference_as_a_gift(session, ada):
    edit_cap(session, ada, 10 * HOUR)  # 5 h default -> 10 h
    session.expire_all()
    [gift] = session.scalars(select(HourGrant)).all()
    assert (gift.kind, gift.seconds, gift.expires_at, gift.user_id) == (
        "admin_gift",
        5 * HOUR,
        None,
        ada.id,
    )
    assert gift.reason == hours.CAP_GIFT_REASON


def test_lowering_a_cap_and_raising_it_again_gifts_once_not_twice(session, ada):
    """5 h to 10 h to 3 h to 10 h: the gifts add up to what the cap says (5 h), they do not stack
    to 5 h plus 7 h."""
    for cap in (10 * HOUR, 3 * HOUR, 10 * HOUR):
        edit_cap(session, ada, cap)
    assert gifts(session, ada) == [5 * HOUR]


def test_clearing_a_cap_and_setting_it_again_gifts_once(session, ada):
    for cap in (10 * HOUR, None, 10 * HOUR):
        edit_cap(session, ada, cap)
    assert gifts(session, ada) == [5 * HOUR]


def test_a_higher_cap_later_gifts_only_the_new_difference(session, ada):
    for cap in (10 * HOUR, 3 * HOUR, 12 * HOUR):
        edit_cap(session, ada, cap)
    assert sorted(gifts(session, ada)) == [2 * HOUR, 5 * HOUR]


@pytest.mark.parametrize("mode", ["off", "shadow"])
def test_with_the_flag_off_or_shadow_a_cap_edit_changes_the_cap_and_nothing_else(
    session, ada, mode
):
    edit_cap(session, ada, 10 * HOUR, mode)
    session.expire_all()
    assert session.scalars(select(HourGrant)).all() == []
    assert session.get(type(ada), ada.id).hours_cap_seconds == 10 * HOUR


def test_a_cap_raised_while_s1_was_live_is_gifted_by_the_script_once(session, ada):
    """The cap was raised from 5 h to 8 h before S2 existed: no gift was made. The script makes
    it from the cap and the pilot baseline (1 h grant + 4 h already used = 5 h), once."""
    script = load_script()
    grant(session, ada, HOUR, kind="pilot", starts=T0 - 10 * DAY)
    s1_usage(session, ada, 4 * HOUR, T0 - 11 * DAY)  # used before the pilot grant was made
    ada.hours_cap_seconds = 8 * HOUR
    session.commit()
    [row] = script.reconcile(session, T0 + 400 * DAY, 6 * HOUR)
    assert row.gifted == 3 * HOUR
    session.commit()
    assert script.reconcile(session, T0 + 400 * DAY, 6 * HOUR) == []  # idempotent
    assert gifts(session, ada) == [3 * HOUR]
