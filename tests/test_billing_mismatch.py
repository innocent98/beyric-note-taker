"""The card-country note on a settled payment (billing stage S5, spec section 5.2).

Through the real webhook route with `FakeProvider`: a payment that the provider reports
successful is paid, and its hours are granted, whatever the card's country is. The note
(`card_country` and `country_mismatch`) is written beside it and can never refuse, reverse or
delay the payment."""

from datetime import UTC, datetime, timedelta

import pytest

from app.billing import mismatch
from app.billing import profile as profiles
from app.models import Payment, User
from tests.test_billing_inbox import (  # noqa: F401  (fixtures used by name)
    ASKED,
    client,
    event_for,
    fake,
    grants,
    payment,
    reload_payment,
    send,
    tx_for,
)

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


def seed_country(session, user, country):
    profiles.change_country(session, user, country, now=NOW - timedelta(days=100))
    session.commit()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("NG", "NG"),
        ("ng", "NG"),
        (" US ", "US"),
        ("NIGERIA NG", "NG"),
        ("UNITED KINGDOM UK", None),  # UK is not a code (GB is): a stray word is not a country
        ("CARD OF", None),  # Flutterwave's verify answer
        ("UNITED STATES US", "US"),
        ("", None),
        ("NGA", None),
        ("NIGERIA", None),
        ("N", None),
        ("NG1", None),
        (None, None),
        (7, None),
    ],
)
def test_the_card_country_is_the_trailing_two_letters_or_nothing(raw, expected):
    assert mismatch.normalize_card_country(raw) == expected


@pytest.mark.parametrize(
    ("card", "billing", "flagged"),
    [
        ("US", "NG", True),  # a foreign card for a person billed in Nigeria
        ("NG", "US", True),  # a Nigerian card for a person billed abroad
        ("NG", "NG", False),
        ("US", "US", False),
        ("US", None, False),  # no billing country yet: nothing to compare
        (None, "NG", False),  # no card (a bank transfer): nothing to compare
        (None, None, False),
    ],
)
def test_the_note_needs_two_countries_that_differ(card, billing, flagged):
    paid = Payment(card_country=card)

    mismatch.apply_country_note(paid, billing)

    assert paid.country_mismatch is flagged and paid.card_country == card


def setup_profile(session, payment, country):
    payer = session.get(User, payment.payer_user_id)
    if country:
        seed_country(session, payer, country)


@pytest.mark.parametrize(
    ("billing", "card", "flagged", "stored"),
    [
        ("NG", "US", True, "US"),
        ("US", "NG", True, "NG"),
        ("NG", "NG", False, "NG"),
        ("NG", "NIGERIA NG", False, "NG"),  # Flutterwave's other format
        ("US", "UNITED STATES NG", True, "NG"),
        (None, "US", False, "US"),  # no profile at all: still paid, still noted, not flagged
        ("NG", None, False, None),
        ("NG", "", False, None),
    ],
)
def test_a_settled_payment_is_noted_and_never_refused(
    client, fake, grants, session, payment, billing, card, flagged, stored
):
    setup_profile(session, payment, billing)
    fake.add_transaction(tx_for(payment, card_country=card))

    response = send(client, fake, event_for(payment))

    assert response.status_code == 200 and grants == [payment.id]  # paid, hours granted
    paid = reload_payment(session, payment)
    assert paid.status == "succeeded" and paid.paid_at is not None
    assert (paid.card_country, paid.country_mismatch) == (stored, flagged)


def seed_pending(session, user, *, billing, pending, effective_at):
    """A person billed in `billing` whose change to `pending` is scheduled for `effective_at` (the
    state a live subscription will make in S8; written directly, as nothing makes it yet)."""
    seed_country(session, user, billing)
    row = profiles.get_profile(session, user)
    row.pending_billing_country = pending
    row.pending_effective_at = effective_at
    session.commit()


def test_a_change_still_waiting_is_not_what_the_card_is_compared_with(
    client, fake, grants, session, payment
):
    payer = session.get(User, payment.payer_user_id)
    seed_pending(
        session,
        payer,
        billing="NG",
        pending="US",
        effective_at=datetime.now(UTC) + timedelta(days=5),
    )
    fake.add_transaction(tx_for(payment, card_country="US"))

    send(client, fake, event_for(payment))

    # The payment was taken under the Nigerian route, so a US card is the odd one.
    assert reload_payment(session, payment).country_mismatch is True


def test_a_change_that_came_due_is_the_country_even_if_nothing_has_written_it_yet(
    client, fake, grants, session, payment
):
    """The period ended yesterday and nobody has read or changed the profile since: the first
    payment through the new route is compared with the new country, not the old one."""
    payer = session.get(User, payment.payer_user_id)
    seed_pending(
        session,
        payer,
        billing="NG",
        pending="US",
        effective_at=datetime.now(UTC) - timedelta(days=1),
    )
    fake.add_transaction(tx_for(payment, card_country="US"))

    send(client, fake, event_for(payment))

    paid = reload_payment(session, payment)
    assert paid.status == "succeeded" and paid.country_mismatch is False  # US card, billed in US
    # the note wrote nothing to the profile: applying is the change's and the renewal's job
    row = profiles.get_profile(session, payer)
    assert (row.billing_country, row.pending_billing_country) == ("NG", "US")


def test_a_failure_in_the_note_cannot_stop_a_paid_customer(
    client, fake, grants, session, payment, monkeypatch
):
    def broken(*args, **kwargs):
        raise RuntimeError("the comparison fell over")

    monkeypatch.setattr(mismatch, "apply_country_note", broken)
    fake.add_transaction(tx_for(payment, card_country="US"))

    response = send(client, fake, event_for(payment))

    assert response.status_code == 200 and grants == [payment.id]
    paid = reload_payment(session, payment)
    assert paid.status == "succeeded" and paid.country_mismatch is False


def test_a_payment_that_was_refused_gets_no_note(client, fake, grants, session, payment):
    setup_profile(session, payment, "NG")
    fake.add_transaction(tx_for(payment, card_country="US", amount_minor=ASKED - 1))

    send(client, fake, event_for(payment))

    refused = reload_payment(session, payment)
    assert refused.status == "pending" and grants == []
    assert (refused.card_country, refused.country_mismatch) == (None, False)
