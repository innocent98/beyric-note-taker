"""The global per-hour mail budget: sign-in and account mail go first and may use all of it, bulk
(notes) mail may not use the last `reserve`, admin alerts are never held, and a held mail waits
with a reason and tells the admins once an hour."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import mail
from app.models import MailOutbox, Notification
from tests.test_mail import FakeMailer, queue
from tests.test_mail_upgrade import NOW, deliver, plain, plain_row, settings  # noqa: F401
from tests.test_web import make_user, owner  # noqa: F401


@pytest.fixture(autouse=True)
def log_again():
    mail.budget_log_throttle.reset()


def go(session, budget, now=NOW, reserve=0):
    """One delivery pass with an hourly budget and a reserve (none unless asked for)."""
    return deliver(session, now=now, mail_hourly_budget=budget, mail_hourly_reserve=reserve)


def rows(session, status=None):
    session.expire_all()
    query = select(MailOutbox).order_by(MailOutbox.id)
    if status:
        query = query.where(MailOutbox.status == status)
    return list(session.scalars(query))


def links(session, n, to="ada{}@example.test"):
    return [queue(session, to=to.format(i)) for i in range(n)]


def budget_alerts(session):
    return list(
        session.scalars(select(Notification).where(Notification.kind == "admin.mail_budget"))
    )


# ---- the budget ----


def test_under_the_budget_everything_goes(session):
    links(session, 4)
    assert go(session, budget=10)[0] == 4


def test_over_the_budget_mail_waits_pending_with_its_reason_and_no_attempt_used(session):
    links(session, 8)
    sent, _ = go(session, budget=5)
    assert sent == 5
    waiting = rows(session, "pending")
    assert len(waiting) == 3
    assert {(r.attempts, r.last_error) for r in waiting} == {(0, "over_budget")}


def test_the_hour_is_a_rolling_one_and_what_waited_goes_when_room_returns(session):
    links(session, 8)
    go(session, budget=5)
    assert go(session, now=NOW + timedelta(minutes=30), budget=5)[0] == 0
    sent, _ = go(session, now=NOW + timedelta(minutes=61), budget=5)
    assert sent == 3
    assert all(r.last_error is None for r in rows(session, "sent"))  # the reason is cleared on send


def test_a_budget_of_zero_means_no_budget(session):
    links(session, 30)
    assert go(session, budget=0)[0] == 25  # only the pass's own batch limit


def test_every_kind_counts_against_the_budget_as_resend_counts_them(session, plain):
    for n in range(3):
        plain_row(session, n, to=f"p{n}@example.test")
    go(session, budget=4, reserve=0)
    links(session, 3)
    assert go(session, budget=4, reserve=0)[0] == 1


# ---- sign-in and account mail first, and a reserve for it ----


def test_bulk_mail_may_not_use_the_reserve_which_a_reset_link_then_uses(session, plain):
    for n in range(10):
        plain_row(session, n, to=f"p{n}@example.test")
    sent, _ = go(session, budget=10, reserve=4)
    assert sent == 6  # bulk stops at budget - reserve
    held = rows(session, "pending")
    assert len(held) == 4 and {r.last_error for r in held} == {"over_budget"}
    # the reserve is still there for a password reset, which arrives later
    for n in range(4):
        queue(session, "reset_password", {"link": "https://x.test/r"}, to=f"r{n}@example.test")
    sent, mailer = go(session, budget=10, reserve=4)
    assert sent == 4 and all("password" in m.subject.lower() for m, _ in mailer.sent)


def test_a_verification_flood_cannot_use_the_reserve_so_a_reset_link_still_goes(session):
    """Anyone can make the site send verification mail by filling in the sign-up form, so it is
    held back from the reserve; a password reset and an invitation are not."""
    links(session, 20)  # verification mail, queued first
    sent, _ = go(session, budget=10, reserve=4)
    assert sent == 6
    queue(session, "reset_password", {"link": "https://x.test/r"}, to="real@example.test")
    queue(session, "waitlist_invite", {"link": "https://x.test/i"}, to="invited@example.test")
    sent, mailer = go(session, budget=10, reserve=4)
    assert sent == 2 and {m.to for m, _ in mailer.sent} == {
        "real@example.test",
        "invited@example.test",
    }
    assert len(rows(session, "pending")) == 14  # the rest of the flood waits


def test_already_registered_mail_is_held_like_verification_mail(session):
    for n in range(8):
        queue(session, "already_registered", {"link": "https://x.test/l"}, to=f"a{n}@example.test")
    assert go(session, budget=10, reserve=4)[0] == 6


def test_account_mail_goes_before_bulk_mail_queued_earlier(session, plain):
    for n in range(5):
        plain_row(session, n, to=f"p{n}@example.test")  # queued first
    links(session, 3)
    sent, mailer = go(session, budget=4, reserve=0)
    assert sent == 4
    assert [m.subject.startswith("Confirm") for m, _ in mailer.sent].count(True) == 3


def test_admin_alert_mail_is_never_held_by_the_budget(session):
    links(session, 6)
    assert go(session, budget=3)[0] == 3  # the budget is used up
    queue(session, "alert", {"title": "T", "body": "B"}, to="boss@example.test")
    sent, mailer = go(session, budget=3)
    assert sent == 1 and mailer.sent[0][0].to == "boss@example.test"  # only the alert went


def test_admin_alert_mail_goes_before_the_sign_in_mail_queued_ahead_of_it(session):
    links(session, 6)
    queue(session, "alert", {"title": "T", "body": "B"}, to="boss@example.test")
    sent, mailer = go(session, budget=3)
    assert sent == 3 and mailer.sent[0][0].to == "boss@example.test"


# ---- two workers ----


class SlowMailer(FakeMailer):
    """A send takes a moment, as a real one does: without the budget's lock the workers would all
    count the hour before any of them has recorded a send."""

    def send(self, message, key):
        time.sleep(0.05)
        super().send(message, key)


def test_two_workers_at_once_never_send_more_than_the_budget(session, engine):
    links(session, 20)
    mailer = SlowMailer()
    lock = threading.Lock()
    totals: list[int] = []

    def worker(_):
        with Session(engine) as own:
            sent = mail.deliver_pending(
                own,
                mailer,
                settings(mail_hourly_budget=8, mail_hourly_reserve=0),
                NOW + timedelta(minutes=1),
            )
        with lock:
            totals.append(sent)

    with ThreadPoolExecutor(4) as pool:
        list(pool.map(worker, range(4)))
    assert sum(totals) == 8 and len(mailer.sent) == 8


# ---- telling the admins ----


def test_the_admins_are_told_once_an_hour_without_any_address(session):
    make_user(session, "boss@example.test", admin=True)
    links(session, 8)
    go(session, budget=5)
    go(session, now=NOW + timedelta(minutes=5), budget=5)
    (note,) = budget_alerts(session)
    assert note.severity == "warning" and note.title.startswith("Outgoing mail is waiting")
    assert "5" in note.body and "example.test" not in note.title + note.body
    assert "MAIL_HOURLY_BUDGET" in note.body
    # the next clock hour, still held: told again
    go(session, now=NOW + timedelta(minutes=61), budget=1)
    assert len(budget_alerts(session)) == 2


def test_nothing_is_said_when_nothing_was_held(session):
    make_user(session, "boss@example.test", admin=True)
    links(session, 3)
    go(session, budget=5)
    assert budget_alerts(session) == []


def test_with_no_admin_the_pass_still_works_and_only_logs(session, caplog):
    links(session, 6)
    with caplog.at_level("WARNING"):
        sent, _ = go(session, budget=4)
    assert sent == 4
    assert any("hourly mail budget" in r.getMessage() for r in caplog.records)
    assert not any("example.test" in r.getMessage() for r in caplog.records)


def test_a_failure_while_telling_the_admins_never_stops_the_queue(session, monkeypatch):
    from app import alerts

    make_user(session, "boss@example.test", admin=True)
    links(session, 6)

    def broken(*a, **k):
        raise RuntimeError("alert trouble")

    monkeypatch.setattr(alerts, "notify_admins", broken)
    assert go(session, budget=4)[0] == 4


# ---- what a held mail becomes ----


def test_a_held_sign_in_mail_that_runs_out_its_hour_fails_with_the_budget_as_the_reason(session):
    links(session, 4)
    go(session, budget=2)
    mail.sweep_outbox(session, NOW + timedelta(hours=3))
    failed = rows(session, "failed")
    assert len(failed) == 2
    assert {r.last_error for r in failed} == {"over_budget"} and all(
        r.payload == {} for r in failed
    )


def test_a_mail_that_was_never_held_still_expires_as_expired(session):
    queue(session)
    mail.sweep_outbox(session, NOW + timedelta(hours=3))
    assert rows(session, "failed")[0].last_error == "expired"
