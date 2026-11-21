"""The one-time closeout of everything made with the payment providers' TEST keys, run just BEFORE
billing goes live (`docs/sop/2026-11-21-go-live-closeout.md`).

Why: the database does not know which mode a row was made in. The day the keys flip, every test
subscription would be renewed against the LIVE provider with a test card token (a Paystack row gets
stuck for ever, a Flutterwave row sends three false "payment failed" mails), and test packs and
plans would keep their free hours. This script closes those rows first. Run it where the app's
`.env` is (the web container):

    docker compose exec -T web python scripts/billing_close_test_data.py --before <T> </dev/null

`<T>` is an ISO 8601 timestamp WITH its zone, in the past, for example `2026-11-22T09:30:00Z`: take
"now" in UTC when you start. Everything created BEFORE `<T>` is test-era; nothing created at or
after it is touched. The SAME value then goes in `BILLING_LIVE_SINCE` in the `.env` edit that
turns `BILLING_LIVE` on, so the app hides what the script closed.

1. `deploy/backup.sh` first (this script does not run it): the closeout cannot be undone by
   itself, the backup is the undo.
2. Run it WITHOUT `--apply`: a dry run that prints counts only. Read it.
3. Run it again WITH `--apply`. It ends with `PROOF: OK` or says what is left.

Pause billing FIRST (`BILLING_ENABLED=false`, recreate web, worker and scheduler: checkouts, the
webhook route and the billing clock stop) and take `<T>` AFTER that, so no test row can be made
between `<T>` and the flip. The script enforces it (see "REFUSES").

With `--end-pilot` (owner decision 2026-11-21: nothing from the pilot is to carry over), every
`pilot` hour grant still in force ALSO ends now: an administrator's never, trial, plan, pack and
admin-gift grants never, no mail, no bell entry. Its dry run counts the grants and the people
holding them. It refuses while a meeting is being recorded or a join is due within 10 minutes
(the deploy gate's two questions), because it would end hours a recording is using. A person who
held a pilot grant never gets a trial (`app/trial.py`), so after this they have no hours and no
trial and must buy a plan.

What `--apply` does (and nothing else, plus the pilot grants under `--end-pilot`):

- subscriptions made before `<T>` that are `active` or `past_due` become `ended` (no next charge,
  no grace, no scheduled plan change; `canceled_at` is set when it was empty). No mail goes: this
  is not `renewals._close`, which tells the person their plan ended;
- hour grants of kind `plan_period` or `pack` that came from a payment or a subscription made
  before `<T>` end now. Trial, pilot and admin-gift grants are never touched;
- saved cards made before `<T>` are deleted;
- payments still `pending` that were made before `<T>` become `failed` (no provider is asked);
- payments, invoices and refunds are NEVER deleted or edited otherwise.

One transaction per person: a failure rolls that person back, is counted, and the run goes on; the
exit code is 1 if any person failed or the proof does not hold. It is safe to run twice (the
second `--apply` changes nothing).

It REFUSES to run (exit 2, nothing changed) unless `--before` is a timestamp with a zone that is
not in the future and not older than 60 minutes (`--max-age-minutes N` changes that),
`BILLING_LIVE` is false in this process (it must run BEFORE the flip), no payment, subscription or
saved card was created at or after `<T>` (a row made after `<T>` is a test row the app would treat
as live), no refund of a test-era payment is still open (`pending` or `unknown`: resolve it in the
admin screen first), and no payment made in the last 30 minutes is still `pending` (someone may be
paying: wait and run again).

The output is counts and times only: no name, email, token, reference or id is ever printed.
"""

import argparse
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, func, or_, select, text, true, update  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from app import hours  # noqa: E402
from app.billing import states  # noqa: E402
from app.billing.cutover import parse_instant  # noqa: E402
from app.config import Settings  # noqa: E402
from app.models import (  # noqa: E402
    HourGrant,
    Invoice,
    Meeting,
    Payment,
    PaymentMethod,
    Refund,
    Subscription,
    User,
)

# A payment still `pending` that is younger than this: someone may be at the provider's page.
PAYING_NOW = timedelta(minutes=30)
# `--before` older than this many minutes is refused (override with `--max-age-minutes`): a stale
# `T` would leave every row made between it and now alive.
MAX_AGE_MINUTES = 60
EXIT_OK, EXIT_FAILED, EXIT_REFUSED = 0, 1, 2
# the rows the table counts, in the order it prints them
KINDS = ("subscriptions", "grants", "cards", "payments", "pilot")
LABELS = {
    "subscriptions": "live test-era subscriptions (active or past due)",
    "grants": "plan or pack hours still in force from test-era payments",
    "cards": "saved cards made before T",
    "payments": "payments still pending that were made before T",
    "pilot": "pilot hour grants still in force (only with --end-pilot)",
}


class Refusal(Exception):
    """The run must not start; the message is safe to print."""


@dataclass
class Counts:
    subscriptions: int = 0
    grants: int = 0
    cards: int = 0
    payments: int = 0
    pilot: int = 0  # counted only under --end-pilot

    def add(self, other: "Counts") -> None:
        for kind in KINDS:
            setattr(self, kind, getattr(self, kind) + getattr(other, kind))

    def total(self) -> int:
        return sum(getattr(self, kind) for kind in KINDS)


@dataclass
class Report:
    before: Counts
    changed: Counts = field(default_factory=Counts)
    after: Counts | None = None
    people: int = 0
    end_pilot: bool = False
    pilot_people: int = 0
    failed_people: int = 0


# ---- what is test-era ----

EVERYONE = object()  # `who`: no person filter


def _owner(column, who):
    """A person's rows: `who` is a user id, `None` (the rows nobody owns any more: the person was
    deleted) or `EVERYONE`."""
    if who is EVERYONE:
        return true()
    return column.is_(None) if who is None else column == who


def _test_payment_ids(before: datetime):
    return select(Payment.id).where(Payment.created_at < before)


def _test_subscription_ids(before: datetime):
    return select(Subscription.id).where(Subscription.created_at < before)


# Each of these is a full-row select of what the closeout would close; the caller picks the columns
# (`.with_only_columns(...)`), so the count, the list of people and the update share ONE definition.


def _live_test_subscriptions(before: datetime, who):
    return select(Subscription).where(
        Subscription.created_at < before,
        Subscription.status.in_(states.LIVE),
        _owner(Subscription.user_id, who),
    )


def _in_force_test_grants(before: datetime, now: datetime, who):
    """Plan-period and pack hours bought with a test payment (or on a test subscription) that have
    not ended. A trial, pilot or admin-gift grant has no payment and is not in this set."""
    return select(HourGrant).where(
        HourGrant.kind.in_(("plan_period", "pack")),
        or_(HourGrant.expires_at.is_(None), HourGrant.expires_at > now),
        or_(
            HourGrant.source_payment_id.in_(_test_payment_ids(before)),
            HourGrant.subscription_id.in_(_test_subscription_ids(before)),
        ),
        _owner(HourGrant.user_id, who),
    )


def _test_cards(before: datetime, who):
    return select(PaymentMethod).where(
        PaymentMethod.created_at < before, _owner(PaymentMethod.user_id, who)
    )


def _pending_test_payments(before: datetime, who):
    return select(Payment).where(
        Payment.status == "pending",
        Payment.created_at < before,
        _owner(Payment.payer_user_id, who),
    )


def _pilot_grants_in_force(now: datetime, who):
    """Pilot hour grants not yet ended, except an administrator's (an admin's anything is never
    touched). Grants whose person was deleted (no owner) are included."""
    admins = select(User.id).where(User.is_admin.is_(True))
    return select(HourGrant).where(
        HourGrant.kind == "pilot",
        or_(HourGrant.expires_at.is_(None), HourGrant.expires_at > now),
        or_(HourGrant.user_id.is_(None), HourGrant.user_id.not_in(admins)),
        _owner(HourGrant.user_id, who),
    )


def count(
    session: Session, before: datetime, now: datetime, who=EVERYONE, end_pilot: bool = False
) -> Counts:
    """How many rows of each kind are still open for this person (default: everyone)."""

    def n(query, id_column) -> int:
        return (
            session.scalar(
                select(func.count()).select_from(query.with_only_columns(id_column).subquery())
            )
            or 0
        )

    return Counts(
        subscriptions=n(_live_test_subscriptions(before, who), Subscription.id),
        grants=n(_in_force_test_grants(before, now, who), HourGrant.id),
        cards=n(_test_cards(before, who), PaymentMethod.id),
        payments=n(_pending_test_payments(before, who), Payment.id),
        pilot=n(_pilot_grants_in_force(now, who), HourGrant.id) if end_pilot else 0,
    )


def people(
    session: Session, before: datetime, now: datetime, end_pilot: bool = False
) -> list[int | None]:
    """Everyone who has at least one row to close, as user ids (`None` stands for rows whose
    person was deleted). Ordered, so a run is repeatable."""
    found: set[int | None] = set()
    for query, owner in (
        (_live_test_subscriptions(before, EVERYONE), Subscription.user_id),
        (_in_force_test_grants(before, now, EVERYONE), HourGrant.user_id),
        (_test_cards(before, EVERYONE), PaymentMethod.user_id),
        (_pending_test_payments(before, EVERYONE), Payment.payer_user_id),
    ):
        found.update(session.scalars(query.with_only_columns(owner).distinct()).all())
    if end_pilot:
        found.update(
            session.scalars(
                _pilot_grants_in_force(now, EVERYONE)
                .with_only_columns(HourGrant.user_id)
                .distinct()
            ).all()
        )
    return sorted(found, key=lambda u: (u is None, u or 0))


# ---- closing one person ----


def close_person(
    session: Session,
    user_id: int | None,
    before: datetime,
    now: datetime,
    end_pilot: bool = False,
) -> Counts:
    """Close this person's test-era rows in the caller's transaction (the caller commits). Each
    update re-checks the condition it selected on, so a row another process changed in between is
    left alone. Returns how many rows of each kind it changed."""
    user = session.get(User, user_id) if user_id is not None else None
    if user is not None:
        hours.lock_person(session, user)  # no recording is counted against these hours meanwhile
    changed = Counts()
    ids = session.scalars(
        _live_test_subscriptions(before, user_id)
        .with_only_columns(Subscription.id)
        .with_for_update()
    ).all()
    if ids:
        changed.subscriptions = len(
            session.execute(
                update(Subscription)
                .where(Subscription.id.in_(ids), Subscription.status.in_(states.LIVE))
                .values(
                    status=states.ENDED,
                    next_charge_at=None,
                    grace_ends_at=None,
                    pending_plan_code=None,
                    canceled_at=func.coalesce(Subscription.canceled_at, now),
                    updated_at=now,
                )
                .returning(Subscription.id)
            ).all()
        )
    grant_ids = session.scalars(
        _in_force_test_grants(before, now, user_id).with_only_columns(HourGrant.id)
    ).all()
    if grant_ids:
        changed.grants = len(
            session.execute(
                update(HourGrant)
                .where(
                    HourGrant.id.in_(grant_ids),
                    or_(HourGrant.expires_at.is_(None), HourGrant.expires_at > now),
                )
                .values(expires_at=now)
                .returning(HourGrant.id)
            ).all()
        )
    card_ids = session.scalars(
        _test_cards(before, user_id).with_only_columns(PaymentMethod.id)
    ).all()
    if card_ids:
        changed.cards = len(
            session.execute(
                delete(PaymentMethod)
                .where(PaymentMethod.id.in_(card_ids))
                .returning(PaymentMethod.id)
            ).all()
        )
    payment_ids = session.scalars(
        _pending_test_payments(before, user_id).with_only_columns(Payment.id)
    ).all()
    if payment_ids:
        changed.payments = len(
            session.execute(
                update(Payment)
                .where(Payment.id.in_(payment_ids), Payment.status == "pending")
                .values(status="failed")
                .returning(Payment.id)
            ).all()
        )
    if end_pilot and not (user is not None and user.is_admin):
        pilot_ids = session.scalars(
            _pilot_grants_in_force(now, user_id).with_only_columns(HourGrant.id)
        ).all()
        if pilot_ids:
            changed.pilot = len(
                session.execute(
                    update(HourGrant)
                    .where(
                        HourGrant.id.in_(pilot_ids),
                        HourGrant.kind == "pilot",
                        or_(HourGrant.expires_at.is_(None), HourGrant.expires_at > now),
                    )
                    .values(expires_at=now)
                    .returning(HourGrant.id)
                ).all()
            )
    return changed


# ---- the run ----


def check_guards(
    session: Session,
    settings: Settings,
    before_text: str | None,
    now: datetime,
    max_age_minutes: int = MAX_AGE_MINUTES,
    end_pilot: bool = False,
) -> datetime:
    """Everything that must hold before anything is read or changed. Returns `T` as UTC. Counts
    only in every message."""
    if not before_text or not before_text.strip():
        raise Refusal(
            "--before is required: the moment the closeout starts, as an ISO 8601 timestamp with "
            "its zone, for example 2026-11-22T09:30:00Z. The same value goes in BILLING_LIVE_SINCE."
        )
    if max_age_minutes < 1:
        raise Refusal("--max-age-minutes must be 1 or more.")
    try:
        before = parse_instant(before_text, name="--before")
    except ValueError as exc:
        raise Refusal(str(exc)) from None
    if before > now:
        raise Refusal(
            "--before is in the future: it must be a moment that has already passed "
            "(use the current time in UTC)."
        )
    if now - before > timedelta(minutes=max_age_minutes):
        raise Refusal(
            f"--before is older than {max_age_minutes} minutes: take a fresh time AFTER billing "
            "was paused (or pass --max-age-minutes N if you really mean it). A stale time leaves "
            "every test row made since it open."
        )
    if settings.billing_live:
        raise Refusal(
            "BILLING_LIVE is true in this process: the closeout runs BEFORE the flip, while the "
            "app is still on test keys. Nothing was changed."
        )
    since = {
        "payments": session.scalar(select(func.count()).where(Payment.created_at >= before)),
        "subscriptions": session.scalar(
            select(func.count()).where(Subscription.created_at >= before)
        ),
        "saved cards": session.scalar(
            select(func.count()).where(PaymentMethod.created_at >= before)
        ),
    }
    if any(since.values()):
        found = ", ".join(f"{n} {kind}" for kind, n in since.items() if n)
        raise Refusal(
            f"billing rows were made at or after --before ({found}). While BILLING_LIVE is false "
            "they are test rows the closeout would skip and the app would then treat as live. "
            "Pause billing first (BILLING_ENABLED=false, recreate web, worker and scheduler), "
            "take a new T after that, and run again; if rows were already made, ask the lead."
        )
    open_refunds = session.scalar(
        select(func.count())
        .select_from(Refund)
        .where(
            Refund.status.in_(("pending", "unknown")),
            Refund.payment_id.in_(_test_payment_ids(before)),
        )
    )
    if open_refunds:
        raise Refusal(
            f"{open_refunds} refund(s) of test-era payments are still open (outcome pending or "
            "unknown). They cannot be closed now: the pause switched the admin screen off. "
            "Un-pause "
            "(BILLING_ENABLED=true, recreate web, worker and scheduler), resolve each in Admin, "
            "Billing, Refunds as made or not made, pause again, take a NEW T, and run again. "
            "(The runbook's step 2 gate counts them BEFORE the pause so this does not happen.)"
        )
    if end_pilot:
        busy = _meetings_running_or_due(session)
        if busy:
            raise Refusal(
                f"{busy} meeting(s) are being recorded, or a join is due within 10 minutes: "
                "--end-pilot would end hours a recording is using. Wait until none is "
                "(the runbook's gate check) and run again."
            )
    paying = session.scalar(
        select(func.count()).where(
            Payment.status == "pending", Payment.created_at > now - PAYING_NOW
        )
    )
    session.rollback()
    if paying:
        minutes = int(PAYING_NOW.total_seconds() // 60)
        raise Refusal(
            f"{paying} payment(s) made in the last {minutes} minutes are still pending: someone "
            f"may be paying. Wait until {minutes} minutes after the last one, and run it again."
        )
    return before


def _meetings_running_or_due(session: Session) -> int:
    """The deploy gate's two questions (`deploy/gate.sh`): a meeting in progress, or a calendar
    join waiting that is due within 10 minutes and not past its deadline."""
    running = session.scalar(
        select(func.count())
        .select_from(Meeting)
        .where(Meeting.status.in_(("bot_sent", "processing")))
    )
    due = session.scalar(
        text(
            "select count(*) from scheduled_joins "
            "where state in ('scheduled', 'no_capacity', 'dispatching') "
            "and run_at <= now() + interval '10 minutes' and deadline_at >= now()"
        )
    )
    return (running or 0) + (due or 0)


def without_invoice(session: Session, before: datetime) -> int:
    """Succeeded, used, test-era payments that have no invoice. Information only: the app never
    invoices a test-era payment once the cutover is set (`invoices.issue_missing`)."""
    return (
        session.scalar(
            select(func.count()).where(
                Payment.status == "succeeded",
                Payment.needs_refund_reason.is_(None),
                Payment.created_at < before,
                ~select(Invoice.id).where(Invoice.payment_id == Payment.id).exists(),
            )
        )
        or 0
    )


def run(
    factory: sessionmaker[Session],
    settings: Settings,
    before_text: str | None,
    *,
    apply: bool,
    now: datetime | None = None,
    out: Callable[[str], None] = print,
    max_age_minutes: int = MAX_AGE_MINUTES,
    end_pilot: bool = False,
) -> int:
    """The whole closeout. Returns the exit code. A database error outside one person's
    transaction prints its type only (a SQLAlchemy message carries the statement's values)."""
    try:
        return _run(
            factory,
            settings,
            before_text,
            apply,
            now or datetime.now(UTC),
            out,
            max_age_minutes,
            end_pilot,
        )
    except Exception as exc:
        out(f"FAILED ({type(exc).__name__}): nothing further was done. Read the lines above.")
        return EXIT_FAILED


def _run(factory, settings, before_text, apply, now, out, max_age_minutes, end_pilot) -> int:
    try:
        with factory() as session:
            before = check_guards(session, settings, before_text, now, max_age_minutes, end_pilot)
    except Refusal as exc:
        out(f"REFUSED: {exc}")
        return EXIT_REFUSED

    mode = "APPLY" if apply else "DRY RUN (nothing is changed)"
    out(f"Billing closeout of test-era data, {mode}")
    out(f"T = {before:%Y-%m-%d %H:%M:%S} UTC: everything created before it is test-era.")
    out("Run deploy/backup.sh first: this cannot be undone by itself, the backup is the undo.")
    if end_pilot:
        out(
            "--end-pilot: every pilot hour grant still in force also ends now (an administrator's "
            "never; no mail, no bell entry). Those people will have no hours and no trial."
        )
    if not apply:
        out("Read the counts below, then run again with --apply.")

    with factory() as session:
        report = Report(before=count(session, before, now, end_pilot=end_pilot))
        who = people(session, before, now, end_pilot)
        uninvoiced = without_invoice(session, before)
        session.rollback()
    report.people = len(who)
    report.end_pilot = end_pilot
    if end_pilot:
        with factory() as session:
            report.pilot_people = len(
                set(
                    session.scalars(
                        _pilot_grants_in_force(now, EVERYONE)
                        .with_only_columns(HourGrant.user_id)
                        .distinct()
                    ).all()
                )
            )
            session.rollback()

    if apply:
        for user_id in who:
            with factory() as session:
                try:
                    changed = close_person(session, user_id, before, now, end_pilot)
                    session.commit()
                except Exception as exc:  # one person's failure never stops the others
                    session.rollback()
                    report.failed_people += 1
                    out(f"FAILED for one person ({type(exc).__name__}): rolled back, continuing.")
                    continue
            report.changed.add(changed)
        with factory() as session:
            report.after = count(session, before, now, end_pilot=end_pilot)
            session.rollback()

    _print_table(report, apply, out)
    out(
        f"Information: {uninvoiced} succeeded test payment(s) have no invoice. The app never "
        "invoices a test-era payment after go-live, so none will be issued."
    )
    if not apply:
        out("PROOF: not run (a dry run changes nothing).")
        return EXIT_OK
    assert report.after is not None
    if report.failed_people == 0 and report.after.total() == 0:
        out(
            "PROOF: OK (no live test-era subscription, test card, expiring test hours or "
            "pending test payment is left"
            + (", and no pilot hours are in force)" if end_pilot else ")")
        )
        return EXIT_OK
    out(
        f"PROOF: FAILED ({report.failed_people} person(s) failed, "
        f"{report.after.total()} row(s) left). Read the lines above, fix, run again."
    )
    return EXIT_FAILED


def _print_table(report: Report, apply: bool, out: Callable[[str], None]) -> None:
    out("")
    out(f"People with rows to close: {report.people}")
    if report.end_pilot:
        out(f"People holding pilot hours in force (counted before): {report.pilot_people}")
    header = f"{'':56}{'before':>8}"
    header += f"{'changed':>9}{'after':>7}" if apply else f"{'to change':>11}"
    out(header)
    for kind in KINDS:
        if kind == "pilot" and not report.end_pilot:
            continue
        row = f"{LABELS[kind]:56}{getattr(report.before, kind):>8}"
        if apply:
            assert report.after is not None
            row += f"{getattr(report.changed, kind):>9}{getattr(report.after, kind):>7}"
        else:
            row += f"{getattr(report.before, kind):>11}"
        out(row)
    out("")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--before", help="T: ISO 8601 with a zone, in the past")
    parser.add_argument("--apply", action="store_true", help="change data (default: dry run)")
    parser.add_argument(
        "--end-pilot",
        action="store_true",
        help="also end every pilot hour grant still in force (owner decision; off by default)",
    )
    parser.add_argument(
        "--max-age-minutes",
        type=int,
        default=MAX_AGE_MINUTES,
        help=f"refuse a --before older than this (default {MAX_AGE_MINUTES})",
    )
    args = parser.parse_args(argv)

    from app.config import get_settings
    from app.db import SessionLocal

    return run(
        SessionLocal,
        get_settings(),
        args.before,
        apply=args.apply,
        max_age_minutes=args.max_age_minutes,
        end_pilot=args.end_pilot,
    )


if __name__ == "__main__":
    raise SystemExit(main())
