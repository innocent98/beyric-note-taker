"""Review S2 (2026-11-25): hours a person paid for follow their ACCOUNT, never an email address.

Grants carry the fingerprint of the address they were made under (`email_hash`) so a deleted
account's free hours cannot be made again (the trial and the pilot checks). That fingerprint must
not also decide who may SPEND a grant: an address changes hands (a Google account's address is
rewritten, a company mailbox is given to someone new), and a plan period, a pack or an admin gift
belongs to the person who paid for or was given it.

Real Postgres; the clock is the `now` each call is given."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update

from app import entitlements, google_signin, hours
from app.models import HourGrant
from app.usage import email_hash
from tests.test_web import make_settings, make_user

T0 = datetime(2026, 12, 1, 9, 0, tzinfo=UTC)
HOUR = 3600
ADDRESS = "ada@example.test"


def grant(session, user, kind, seconds=5 * HOUR, *, address=ADDRESS, user_id=True, **over):
    row = HourGrant(
        user_id=user.id if user_id else None,
        email_hash=email_hash(address),
        kind=kind,
        seconds=seconds,
        starts_at=T0 - timedelta(days=1),
        expires_at=T0 + timedelta(days=20),
        **over,
    )
    session.add(row)
    session.commit()
    return row


@pytest.fixture
def ada(session):
    return make_user(session, ADDRESS)


@pytest.mark.parametrize("kind", ["plan_period", "pack", "admin_gift"])
def test_after_an_address_change_the_next_owner_of_the_old_address_spends_nothing(
    session, ada, kind
):
    """Scenario A: the person's address is rewritten (a Google sign-in), and someone else signs up
    with the address they left. The paid grant carries the old fingerprint."""
    paid = grant(session, ada, kind)
    outcome = google_signin._sign_in_by_sub(session, ada, "ada.new@example.test")
    assert outcome.user.email == "ada.new@example.test"
    session.commit()
    newcomer = make_user(session, ADDRESS)

    assert entitlements.grants_in_force(session, newcomer, T0) == []
    assert entitlements.status(session, newcomer, T0).has_grant is False
    assert entitlements.check_join(session, newcomer, T0).allowed is False
    [(mine, left)] = entitlements.grants_in_force(session, ada, T0)  # the payer keeps all of it
    assert (mine.id, left) == (paid.id, 5 * HOUR)


@pytest.mark.parametrize("kind", ["plan_period", "pack", "admin_gift"])
def test_after_a_deletion_someone_given_the_same_address_spends_nothing(session, ada, kind):
    """Scenario B: the account is deleted (the foreign key empties `user_id`, the fingerprint
    stays) and the address is later given to someone else."""
    grant(session, ada, kind)
    session.execute(update(HourGrant).values(user_id=None))  # what `ON DELETE SET NULL` does
    session.delete(ada)
    session.commit()
    newcomer = make_user(session, ADDRESS)  # the address is theirs now

    assert entitlements.grants_in_force(session, newcomer, T0) == []
    assert entitlements.check_join(session, newcomer, T0).allowed is False


def test_a_trial_follows_the_account_after_an_address_change_too(session, ada):
    """Free hours are bound to the account the same way, once the account exists: only a grant a
    deleted account left (no `user_id`) is found again by the fingerprint, as it always was."""
    grant(session, ada, "trial", 3 * HOUR)
    google_signin._sign_in_by_sub(session, ada, "ada.new@example.test")
    session.commit()
    newcomer = make_user(session, ADDRESS)

    assert entitlements.grants_in_force(session, newcomer, T0) == []
    assert len(entitlements.grants_in_force(session, ada, T0)) == 1


def test_an_orphaned_pilot_or_trial_is_still_found_again_by_the_fingerprint(session, ada):
    """The documented design (a deleted account's FREE hours are not made again, and the person
    who signs up again gets what was left): kept, for the trial and the pilot only."""
    for kind in ("pilot", "trial"):
        grant(session, ada, kind, HOUR, user_id=False)

    held = entitlements.grants_in_force(session, ada, T0)

    assert sorted(g.kind for g, _ in held) == ["pilot", "trial"]


def test_an_admins_cap_gift_made_to_someone_else_is_not_counted_for_the_next_owner(session, ada):
    """`sync_cap_gift` adds what a raised cap is short of; a gift of the same reason made to the
    person who held the address before must not count as this person's."""
    previous = grant(session, ada, "admin_gift", 2 * HOUR, reason=hours.CAP_GIFT_REASON)
    google_signin._sign_in_by_sub(session, ada, "ada.new@example.test")
    session.commit()
    newcomer = make_user(session, ADDRESS, hours_cap_seconds=5 * HOUR + 2 * HOUR)
    assert previous.user_id == ada.id

    gifted = hours.sync_cap_gift(session, newcomer)

    assert gifted == 2 * HOUR  # the newcomer's own gift: nothing of the old one counts


def test_the_trial_is_still_refused_to_an_address_that_already_had_one(session, ada):
    """The abuse check is the one place the fingerprint decides: deleting an account and signing
    up again does not make a second trial."""
    from app import trial

    grant(session, ada, "trial", 3 * HOUR, user_id=False)
    session.delete(ada)
    session.commit()
    again = make_user(session, ADDRESS)

    assert trial.had_one(session, again) is True
    assert trial.give(session, again, T0, make_settings(trial_seconds=3 * HOUR)) is None
