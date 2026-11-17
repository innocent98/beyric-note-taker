"""The pilot grants' end (billing stage S10): they expire `PILOT_EXPIRY_DAYS` (60) days after paid
plans open, with one mail before, and nothing expires while the date is unset.

Real Postgres. The clock is injected (`now=`), so the 60 days, the notice window and a whole
70-day run are exact and take milliseconds."""

import logging
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from app import alerts, entitlements, pilot_expiry, sweeps
from app.models import HourGrant, MailOutbox, Notification, UsageAllocation, UsageEntry
from app.scheduler import Scheduler
from app.usage import email_hash
from tests.test_web import make_settings, make_user

HOUR = 3600
DAY = timedelta(days=1)
OPENS = date(2027, 2, 1)
# 1 Feb + 60 days = 2 April: the last day the hours can be used; they end as 3 April starts.
LAST_DAY = date(2027, 4, 2)
END = datetime(2027, 4, 3, tzinfo=UTC)
NOTICE_FROM = END - 14 * DAY
BEFORE = datetime(2027, 1, 20, 9, 0, tzinfo=UTC)  # before paid plans open


@pytest.fixture(autouse=True)
def mail_can_be_sent(monkeypatch):
    settings = make_settings(admin_alert_email="ops@example.test")
    monkeypatch.setattr(alerts, "get_settings", lambda: settings)


def on(**over):
    return make_settings(**{"hours_mode": "enforce", "paid_plans_open_on": OPENS, **over})


def pilot_person(session, name, seconds=2 * HOUR, **fields):
    user = make_user(session, f"{name}@example.test", **fields)
    session.add(
        HourGrant(
            user_id=user.id,
            email_hash=email_hash(user.email),
            kind="pilot",
            seconds=seconds,
            starts_at=datetime(2026, 11, 16, tzinfo=UTC),
        )
    )
    session.commit()
    return user


def pilots(session):
    session.expire_all()
    return list(session.scalars(select(HourGrant).where(HourGrant.kind == "pilot")))


def notices(session):
    session.expire_all()
    return list(session.scalars(select(Notification).where(Notification.kind == "pilot.expiry")))


def mails(session):
    session.expire_all()
    return list(session.scalars(select(MailOutbox).order_by(MailOutbox.id)))


# ---- the date ----


def test_the_end_is_the_start_of_the_day_after_sixty_days_utc():
    assert pilot_expiry.end_of(on()) == END
    assert pilot_expiry.last_day_of(END) == LAST_DAY


def test_the_number_of_days_follows_the_setting():
    assert pilot_expiry.end_of(on(pilot_expiry_days=30)) == datetime(2027, 3, 4, tzinfo=UTC)


def test_unset_means_no_end_at_all():
    assert pilot_expiry.end_of(make_settings(hours_mode="enforce")) is None
    assert make_settings(paid_plans_open_on="").paid_plans_open_on is None


# ---- nothing changes until the date is set (or while the rule is off) ----


def test_with_the_date_unset_nothing_expires_and_nobody_is_mailed(session):
    ada = pilot_person(session, "ada")
    settings = make_settings(hours_mode="enforce")
    assert pilot_expiry.run(session, END + 10 * DAY, settings) == (0, 0)
    assert [g.expires_at for g in pilots(session)] == [None]
    assert entitlements.status(session, ada, END + 10 * DAY).state == "pilot"
    assert notices(session) == [] and mails(session) == []


@pytest.mark.parametrize("mode", ["off", "shadow"])
def test_with_the_hours_rule_off_even_a_set_date_does_nothing(session, mode):
    pilot_person(session, "ada")
    assert pilot_expiry.run(session, NOTICE_FROM + DAY, on(hours_mode=mode)) == (0, 0)
    assert [g.expires_at for g in pilots(session)] == [None]
    assert notices(session) == [] and mails(session) == []


# ---- setting the end ----


def test_once_the_date_is_set_every_pilot_grant_gets_the_end_and_nobody_is_mailed_yet(session):
    pilot_person(session, "ada")
    pilot_person(session, "ben")
    changed, told = pilot_expiry.run(session, BEFORE, on())
    assert (changed, told) == (2, 0)
    assert {g.expires_at for g in pilots(session)} == {END}
    assert notices(session) == [] and mails(session) == []
    assert pilot_expiry.run(session, BEFORE + DAY, on()) == (0, 0)  # idempotent


def test_only_pilot_grants_are_touched(session):
    ada = pilot_person(session, "ada")
    session.add(
        HourGrant(user_id=ada.id, email_hash=email_hash(ada.email), kind="admin_gift", seconds=HOUR)
    )
    session.add(
        HourGrant(user_id=ada.id, email_hash=email_hash(ada.email), kind="trial", seconds=HOUR)
    )
    session.commit()
    pilot_expiry.run(session, BEFORE, on())
    session.expire_all()
    others = session.scalars(select(HourGrant).where(HourGrant.kind != "pilot")).all()
    assert [g.expires_at for g in others] == [None, None]


# ---- the expiry day, read through the same rule that decides a join ----


def test_the_last_second_before_the_end_the_hours_still_work_and_at_the_end_they_are_gone(session):
    ada = pilot_person(session, "ada")
    pilot_expiry.run(session, BEFORE, on())
    last = END - timedelta(seconds=1)
    assert entitlements.check_join(session, ada, last).allowed
    assert entitlements.status(session, ada, last).state == "pilot"
    assert entitlements.can(session, ada, entitlements.ASK, last)
    decision = entitlements.check_join(session, ada, END)
    assert (decision.allowed, decision.reason) == (False, entitlements.NO_PLAN)
    after = entitlements.status(session, ada, END)
    assert (after.state, after.seconds_left) == ("none", 0)
    assert entitlements.can(session, ada, entitlements.ASK, END) is False


def test_usage_counted_after_the_end_never_comes_off_other_hours(session):
    """The S2 backlog term stops at the pilot's end: a recording counted after it was counted
    under the allocation rule and must not be taken off a plan's hours as if it were backlog."""
    ada = pilot_person(session, "ada")
    session.add(
        HourGrant(
            user_id=ada.id,
            email_hash=email_hash(ada.email),
            kind="plan_period",
            seconds=10 * HOUR,
            starts_at=END - DAY,
            expires_at=END + 30 * DAY,
        )
    )
    pilot_expiry.run(session, BEFORE, on())
    entry = UsageEntry(user_id=ada.id, email_hash=email_hash(ada.email), seconds=HOUR)
    entry.counted_at = END + DAY
    session.add(entry)
    session.flush()
    session.add(UsageAllocation(usage_entry_id=entry.id, grant_id=_plan(session).id, seconds=HOUR))
    session.commit()
    assert entitlements.status(session, ada, END + 2 * DAY).seconds_left == 9 * HOUR


def _plan(session):
    return session.scalars(select(HourGrant).where(HourGrant.kind == "plan_period")).one()


# ---- the one mail ----


def test_one_mail_goes_to_a_person_with_hours_left_once_the_window_opens_and_never_again(session):
    ada = pilot_person(session, "ada")
    pilot_expiry.run(session, BEFORE, on())
    just_before = NOTICE_FROM - timedelta(seconds=1)
    assert pilot_expiry.run(session, just_before, on()) == (0, 0)
    assert mails(session) == []
    assert pilot_expiry.run(session, NOTICE_FROM, on()) == (0, 1)
    for later in (NOTICE_FROM + timedelta(minutes=1), NOTICE_FROM + 7 * DAY, END - DAY):
        assert pilot_expiry.run(session, later, on()) == (0, 0)
    assert [n.user_id for n in notices(session)] == [ada.id]
    sent = mails(session)
    assert [m.to_email for m in sent] == [ada.email]
    assert len(sent) == 1


def test_the_text_is_exact_with_the_hours_rule_only(session):
    """Only HOURS_MODE=enforce is on: the mail names only the recording rule, never Ask."""
    pilot_person(session, "ada", seconds=int(1.5 * HOUR))
    pilot_expiry.run(session, NOTICE_FROM, on())
    (note,) = notices(session)
    assert note.title == "Your pilot hours will expire"
    assert note.body == (
        "Paid plans opened on 1 February 2027. The recorded hours you still have from the pilot "
        "(1.5 h) can be used until the end of 2 April 2027 (UTC), and then they expire. After "
        "that the note taker will not join new meetings until you take a plan. You can still "
        "read, search and export your notes and transcripts, which we keep until you ask us to "
        "delete them or close your account. If you do not need more hours, you do not need to do "
        "anything."
    )
    for false_promise in ("free", "Nothing is deleted", "exactly as they are", "Ask Beyric"):
        assert false_promise not in note.body


def test_the_text_names_ask_translation_and_rewriting_only_when_the_gates_are_on(session):
    pilot_person(session, "ada", seconds=int(1.5 * HOUR))
    pilot_expiry.run(session, NOTICE_FROM, on(feature_gates_mode="enforce"))
    (note,) = notices(session)
    assert (
        "After that the note taker will not join new meetings, and Ask Beyric, new translations "
        "and rewriting notes with another template will not work, until you take a plan."
    ) in note.body


@pytest.mark.parametrize(
    ("seconds", "shown"), [(30, "1 min"), (45 * 60, "45 min"), (HOUR, "1 h"), (5400, "1.5 h")]
)
def test_a_small_amount_is_not_rounded_down_to_zero_hours(seconds, shown):
    assert pilot_expiry._amount(seconds) == shown


def test_the_hours_in_the_mail_are_what_is_left_not_what_was_granted(session):
    ada = pilot_person(session, "ada", seconds=4 * HOUR)
    entry = UsageEntry(user_id=ada.id, email_hash=email_hash(ada.email), seconds=HOUR)
    entry.counted_at = datetime(2027, 1, 1, tzinfo=UTC)
    session.add(entry)
    session.flush()
    session.add(
        UsageAllocation(usage_entry_id=entry.id, grant_id=pilots(session)[0].id, seconds=HOUR)
    )
    session.commit()
    pilot_expiry.run(session, NOTICE_FROM, on())
    assert "(3 h)" in notices(session)[0].body


def test_nobody_else_is_written_to(session):
    spent = pilot_person(session, "spent", seconds=HOUR)
    entry = UsageEntry(user_id=spent.id, email_hash=email_hash(spent.email), seconds=HOUR)
    entry.counted_at = datetime(2027, 1, 1, tzinfo=UTC)
    session.add(entry)
    session.flush()
    session.add(
        UsageAllocation(usage_entry_id=entry.id, grant_id=pilots(session)[0].id, seconds=HOUR)
    )
    boss = pilot_person(session, "boss", admin=True)
    held = pilot_person(session, "held", status="suspended")
    trialist = make_user(session, "trialist@example.test")
    session.add(
        HourGrant(
            user_id=trialist.id, email_hash=email_hash(trialist.email), kind="trial", seconds=HOUR
        )
    )
    make_user(session, "plain@example.test")
    session.commit()
    pilot_expiry.run(session, NOTICE_FROM, on())
    assert notices(session) == []
    assert mails(session) == []
    assert boss and held


def test_a_person_who_switched_email_off_gets_the_bell_entry_only(session):
    from app.models import UserSettings

    ada = pilot_person(session, "ada")
    session.add(UserSettings(user_id=ada.id, notify_email=False))
    session.commit()
    pilot_expiry.run(session, NOTICE_FROM, on())
    assert len(notices(session)) == 1
    assert mails(session) == []


def test_a_whole_seventy_day_run_with_the_job_every_hour_sends_exactly_one_mail(session):
    ada = pilot_person(session, "ada")
    ben = pilot_person(session, "ben")
    now = BEFORE
    settings = on()
    while now < END + 5 * DAY:
        pilot_expiry.run(session, now, settings)
        now += timedelta(hours=1)
    assert sorted(n.user_id for n in notices(session)) == sorted([ada.id, ben.id])
    assert len(mails(session)) == 2
    assert {g.expires_at for g in pilots(session)} == {END}
    assert entitlements.status(session, ada, now).state == "none"


# ---- moving the date, and the safety rules ----


def test_moving_the_date_moves_the_end_and_sends_one_corrected_mail_not_two_for_one_date(session):
    pilot_person(session, "ada")
    pilot_expiry.run(session, BEFORE, on())
    pilot_expiry.run(session, NOTICE_FROM, on())
    later = on(paid_plans_open_on=OPENS + timedelta(days=30))
    new_end = pilot_expiry.end_of(later)
    assert new_end == END + timedelta(days=30)
    assert pilot_expiry.run(session, NOTICE_FROM + DAY, later) == (
        1,
        0,
    )  # moved, still outside its window
    assert {g.expires_at for g in pilots(session)} == {new_end}
    assert pilot_expiry.run(session, new_end - 14 * DAY, later) == (0, 1)  # a new date, a new mail
    assert pilot_expiry.run(session, new_end - 13 * DAY, later) == (0, 0)
    assert len(notices(session)) == 2


def test_a_grant_that_has_already_ended_is_not_brought_back_by_moving_the_date(session):
    ada = pilot_person(session, "ada")
    pilot_expiry.run(session, BEFORE, on())
    pilot_expiry.run(session, NOTICE_FROM, on())
    later_settings = on(paid_plans_open_on=OPENS + timedelta(days=30))
    pilot_expiry.run(session, END + DAY, later_settings)
    assert {g.expires_at for g in pilots(session)} == {END}
    assert entitlements.status(session, ada, END + DAY).state == "none"


def test_a_date_already_past_with_no_notice_ever_sent_expires_nothing(session, caplog):
    ada = pilot_person(session, "ada")
    with caplog.at_level(logging.WARNING, logger="app.pilot_expiry"):
        result = pilot_expiry.run(session, END + 40 * DAY, on())
    assert result == (0, 0)
    assert [g.expires_at for g in pilots(session)] == [None]
    assert entitlements.status(session, ada, END + 40 * DAY).state == "pilot"
    assert "nothing is expired" in caplog.text


def test_a_date_first_seen_inside_the_minimum_notice_moves_the_end_out_and_the_mail_says_so(
    session,
):
    """Set late: 3 days before the end. The mail goes at once, but the hours end a full week after
    it (the first midnight UTC at least 7 days ahead), and the mail names that date."""
    pilot_person(session, "ada")
    now = END - 3 * DAY
    assert pilot_expiry.run(session, now, on()) == (1, 1)
    pushed = now + 7 * DAY
    assert {g.expires_at for g in pilots(session)} == {pushed}
    assert pushed - now >= timedelta(days=7)
    assert "until the end of 6 April 2027 (UTC)" in notices(session)[0].body


def test_a_scheduler_that_was_down_through_the_window_still_gives_a_full_minimum_notice(session):
    ada = pilot_person(session, "ada")
    pilot_expiry.run(session, BEFORE, on())  # the end is set long before
    back = END - timedelta(hours=2)  # the scheduler comes back two hours before it
    assert pilot_expiry.run(session, back, on()) == (0, 1)
    (grant,) = pilots(session)
    assert grant.expires_at - back >= timedelta(days=7)
    assert grant.expires_at == datetime(2027, 4, 10, tzinfo=UTC)
    assert entitlements.check_join(
        session, ada, END + DAY
    ).allowed  # still in force after the old end
    assert not entitlements.check_join(session, ada, grant.expires_at).allowed
    assert pilot_expiry.run(session, back + timedelta(hours=1), on()) == (0, 0)  # told once


def test_the_end_is_never_moved_closer_for_a_person_who_was_already_told(session):
    pilot_person(session, "ada")
    pilot_expiry.run(session, NOTICE_FROM, on())
    earlier = on(paid_plans_open_on=OPENS - timedelta(days=10))  # the date moved earlier
    assert pilot_expiry.run(session, NOTICE_FROM + DAY, earlier) == (0, 0)
    assert {g.expires_at for g in pilots(session)} == {END}
    assert len(notices(session)) == 1


def test_a_date_moved_earlier_still_applies_to_people_not_yet_told(session):
    pilot_person(session, "ada")
    pilot_expiry.run(session, BEFORE, on())
    earlier = on(paid_plans_open_on=OPENS - timedelta(days=10))
    assert pilot_expiry.run(session, BEFORE + DAY, earlier) == (1, 0)
    assert {g.expires_at for g in pilots(session)} == {END - 10 * DAY}


def test_an_unconfirmed_account_is_not_mailed_although_it_holds_a_pilot_grant(session):
    """Migration 0049 gave a grant to every non-admin row, an unconfirmed one included: it may be
    someone else's address."""
    pilot_person(session, "unconfirmed", email_verified_at=None)
    pilot_person(session, "confirmed")
    pilot_expiry.run(session, NOTICE_FROM, on())
    assert [n.user_id for n in notices(session)] != []
    assert len(notices(session)) == 1
    assert [m.to_email for m in mails(session)] == ["confirmed@example.test"]


def test_the_gates_alone_do_not_start_the_job(session):
    pilot_person(session, "ada")
    settings = on(hours_mode="off", feature_gates_mode="enforce")
    assert pilot_expiry.run(session, NOTICE_FROM, settings) == (0, 0)
    assert [g.expires_at for g in pilots(session)] == [None]


def test_no_warning_after_the_end_when_every_grant_was_given_its_end(session, caplog):
    """Two people, one with nothing left to be told: the job ran, every grant carries the end, so a
    later run logs nothing (the first version warned every hour for ever)."""
    pilot_person(session, "ada")
    pilot_expiry.run(session, NOTICE_FROM, on())
    with caplog.at_level(logging.WARNING, logger="app.pilot_expiry"):
        pilot_expiry.run(session, END + 5 * DAY, on())
        pilot_expiry.run(session, END + 100 * DAY, on())  # past the bell's 90-day pruning too
    assert caplog.text == ""


def test_the_notice_days_are_checked_at_startup():
    with pytest.raises(ValueError, match="PILOT_EXPIRY_NOTICE_DAYS"):
        make_settings(pilot_expiry_notice_days=60)  # not smaller than the 60 days
    with pytest.raises(ValueError, match="PILOT_EXPIRY_NOTICE_DAYS"):
        make_settings(pilot_expiry_notice_days=5)  # under the 7-day minimum
    with pytest.raises(ValueError):
        make_settings(pilot_expiry_notice_days=61)  # beyond the field's own limit (bell pruning)
    assert make_settings(pilot_expiry_notice_days=59).pilot_expiry_notice_days == 59
    assert make_settings(pilot_expiry_min_notice_days="").pilot_expiry_min_notice_days == 7


# ---- the scheduler ----


def job_names(settings):
    scheduler = Scheduler(lambda: None, lambda: None, leader_conn_factory=lambda: None)
    sweeps.register_default_sweeps(scheduler, settings, mailer=object())
    return {job.name for jobs in scheduler._jobs.values() for job in jobs}


def test_the_jobs_exist_only_when_their_settings_are_on():
    assert not {"trial_sweep", "pilot_expiry"} & job_names(make_settings())
    assert "trial_sweep" in job_names(make_settings(trial_seconds=HOUR))
    assert "pilot_expiry" in job_names(make_settings(paid_plans_open_on=OPENS))


def test_setting_the_date_without_enforce_is_logged_once_at_startup(caplog):
    with caplog.at_level(logging.WARNING, logger="app.sweeps"):
        job_names(make_settings(paid_plans_open_on=OPENS, hours_mode="shadow"))
    assert "no pilot hours expire" in caplog.text
