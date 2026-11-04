"""The privacy policy and terms say only what the app does for accounts (spec 12.3), and the
version a new account records is the date the pages show."""

import re
from datetime import datetime

import pytest

from app import signup
from tests.test_web import anon_client, app, fake_openai, fake_vexa  # noqa: F401

PRIVACY = "/privacy"
TERMS = "/terms"


def text(client, path: str) -> str:
    page = client.get(path).text
    return " ".join(re.sub(r"<[^>]+>", " ", page).replace("&middot;", "").split())


@pytest.mark.parametrize("path", [PRIVACY, TERMS])
def test_the_terms_version_is_the_last_updated_date_on_both_pages(anon_client, path):
    match = re.search(r"Last updated (\d{1,2} \w+ \d{4})", text(anon_client, path))
    assert match, "the page has no 'Last updated' date"
    shown = datetime.strptime(match[1], "%d %B %Y").date().isoformat()
    assert shown == signup.TERMS_VERSION


def test_the_account_details_say_password_or_google_hours_and_waiting_list(anon_client):
    page = text(anon_client, PRIVACY)
    assert (
        "Account details: your name and email address, and a password (stored only in hashed "
        "form) or, if you sign in with Google, your Google account identifier. We also keep a "
        "count of the hours of meetings recorded for your account, and, if you join the waiting "
        "list, your email address." in page
    )


def test_google_sign_in_is_described_as_identity_only(anon_client):
    page = text(anon_client, PRIVACY)
    assert (
        "If you sign in with Google, we receive your name, email address and Google account "
        "identifier, and nothing else, unless you also connect your calendar." in page
    )


def test_the_email_use_names_only_the_mail_that_is_sent(anon_client):
    page = text(anon_client, PRIVACY)
    assert (
        "To send service emails, such as account verification, password resets, waiting-list "
        "invitations and notices that an address already has an account, and an email when the "
        "notes of a meeting you sent the note taker to are ready." in page
    )
    assert "notices about meetings" not in page  # not sent yet: calendar auto-join is later


def test_the_retention_covers_unconfirmed_accounts_the_waiting_list_and_the_hours_ledger(
    anon_client,
):
    page = text(anon_client, PRIVACY)
    assert "An account whose email address is not confirmed within 48 hours is deleted." in page
    assert "Waiting list: your email address is kept until" in page
    assert "we keep only the number of recorded hours counted for it" in page
    assert "Mail log: the address, kind and time of each email we send, without its content" in page
    assert "we keep a one-way fingerprint of the address, and not the address itself" in page


def test_the_terms_say_the_pilot_has_a_limited_number_of_accounts(anon_client):
    page = text(anon_client, TERMS)
    assert (
        "The pilot has a limited number of accounts. When it is full you can join a waiting "
        "list." in page
    )
    assert "Each pilot account has a limit on recorded hours, shown in the app" in page


def test_nothing_is_claimed_about_calendar_features_that_are_not_built(anon_client):
    """Accounts only: the calendar and Meet sentences stay as they were until those ship."""
    page = text(anon_client, PRIVACY)
    assert "Outlook" not in page and "Microsoft" not in page


def test_resend_is_named_as_a_recipient_with_what_it_receives(anon_client):
    page = text(anon_client, PRIVACY)
    assert (
        "Resend sends our service emails (account verification, password resets, waiting-list "
        "invitations, notices that an address already has an account, and notes-ready emails). "
        "It receives the email address and, if given, the name on each of those emails, and "
        "their content, which includes one-time links and, in a notes-ready email, a short "
        "extract of your notes (the summary, decisions, action items and open questions)." in page
    )
    assert "and Resend the email addresses and names it sends mail to" in page


def test_administrator_access_is_stated_plainly(anon_client):
    page = text(anon_client, PRIVACY)
    assert (
        "Our administrator can see account details (name, email, status and hours used) and, if "
        "you ask for help signing in, can create a one-time password reset link for your "
        "account, which would give access to it. You are notified in the app when this happens, "
        "and the action is logged. The administrator's account screens do not show your "
        "meetings or notes." in page
    )
