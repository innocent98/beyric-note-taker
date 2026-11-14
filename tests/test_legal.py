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
        "form) or, if you sign in with Google, your Google account identifier. If you connect "
        "Outlook, we also keep an identifier that Microsoft gives your account (a tenant and user "
        "identifier) and the email address of that Microsoft account. We also keep a "
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
        "and team invitations and notices that an address already has an account, an email when "
        "the notes of a meeting you sent the note taker to are ready, and an email to a teammate "
        "listing the tasks from a meeting that were assigned to them, and an email with a "
        "meeting's notes to the people you choose to share them with." in page
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


def test_the_policy_says_what_is_read_from_outlook_and_how_to_disconnect(anon_client):
    """Outlook phase O, M7: merged only at switch-on, so the policy never runs ahead."""
    page = text(anon_client, PRIVACY)
    assert (
        "Outlook calendar: we read the title, start and end time, whether you are the organiser, "
        "your reply, the number of attendees (not their addresses) and the meeting link of events "
        "on your main Outlook calendar. We read an event's description and location only to find "
        "its meeting link, and do not keep them." in page
    )
    assert "We do not create, change or delete calendar events." in page
    assert (
        "You can disconnect Outlook in the app, which deletes the calendar data we hold and our "
        "stored Microsoft access credential. Microsoft does not let us withdraw that permission "
        "for you: to remove Beyric from your Microsoft account, remove it in your Microsoft "
        "account's app permissions." in page
    )
    assert "(Google, Microsoft and Zoom)" in page
    assert "Calendar access credentials are encrypted before they are stored" in page
    # Teams joining is not built: the policy says nothing of joining Teams meetings
    assert "Microsoft Teams" not in page


def test_the_terms_name_outlook_as_a_third_party_platform_and_not_teams(anon_client):
    page = text(anon_client, "/terms")
    assert "including Google Meet, Google Calendar, Microsoft Outlook and Zoom." in page
    assert "Microsoft Teams" not in page


def test_resend_is_named_as_a_recipient_with_what_it_receives(anon_client):
    page = text(anon_client, PRIVACY)
    assert (
        "Resend sends our service emails (account verification, password resets, waiting-list "
        "and team invitations, notices that an address already has an account, notes-ready "
        "emails and task assignment emails). "
        "It receives the email address and, if given, the name on each of those emails, and "
        "their content, which includes one-time links and, in a notes-ready email, a short "
        "extract of your notes (the summary, decisions, action items and open questions) and, "
        "in a task email, the titles and due dates of the tasks assigned to the recipient." in page
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


def test_the_pages_do_not_promise_to_tell_anyone_about_a_change(anon_client):
    """The policy and terms may change without notice: they say only that they may be updated and
    that the date at the top shows when. A promise of notice would be false (nothing sends one)."""
    for path in (PRIVACY, TERMS):
        page = text(anon_client, path)
        assert "from time to time" in page
        assert "shows when" in page
        for promise in ("material change", "we will tell you by email", "before they take effect"):
            assert promise not in page, (path, promise)
        assert "30 days before it takes effect" not in page


def test_the_pages_say_what_happens_to_a_meeting_moved_to_a_team(anon_client):
    """Multi-team (the owner's wording, 2026-11-14): a moved meeting belongs to the team and stays
    with it when its recorder leaves or closes their account (shown as a "Former member"); a
    meeting only shared is deleted with the account. The retention list and terms say the same."""
    privacy = text(anon_client, PRIVACY)
    assert (
        "A moved meeting stays with the team even if you leave the team or close your account. "
        "If you close your account, your name and email address are removed from it and it is "
        'shown as from a "Former member".' in privacy
    )
    assert (
        "Meetings you moved to a team are an exception: they stay with the team when you close "
        "your account" in privacy
    )
    assert (
        "If you are the only member of a team when you close your account, the team and the "
        "meetings moved to it are deleted with your account." in privacy
    )
    assert (
        "A team whose only member closes their account is deleted with it, and so are the "
        "meetings moved to it." in privacy
    )
    assert "Meetings you only shared with a team are deleted when you close your account" in privacy
    assert (
        "We keep only an empty record with no name or email address, so that meetings moved to "
        'a team remain linked to a "Former member".' in privacy
    )
    terms = text(anon_client, TERMS)
    assert "A meeting you move to a team belongs to that team" in terms
    assert "This does not apply to meetings you moved to a team" in terms
    assert "who you invite to your team and for what you choose to share" not in terms
