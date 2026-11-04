"""Email preferences: GET and PUT /api/settings/email (`user_settings.notify_email`), the footer
link in the notes-ready mail that points at them, and the owner being told when the per-recipient
cap swallowed a mail."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app import mail
from app.models import MailOutbox, Notification, Template, UserSettings
from tests.test_api import Api, anon, api, error_of  # noqa: F401  (fixtures used by name)
from tests.test_mail_upgrade import NOTES, NOW, deliver, plain, plain_row, settings  # noqa: F401
from tests.test_web import (  # noqa: F401
    OTHER_EMAIL,
    add_meeting,
    anon_client,
    app,
    fake_openai,
    fake_vexa,
    make_user,
    owner,
    stranger,
)

URL = "/api/settings/email"


def stored(session, user):
    session.expire_all()
    row = session.get(UserSettings, user.id)
    return None if row is None else row.notify_email


# ---- the API ----


def test_the_default_is_on_for_an_account_with_no_settings_row(api, session, owner):
    assert stored(session, owner) is None
    assert api.get(URL).json() == {"notify_email": True}


def test_turning_it_off_and_on_is_stored_and_read_back(api, session, owner):
    assert api.put(URL, {"notify_email": False}).json() == {"notify_email": False}
    assert stored(session, owner) is False
    assert api.get(URL).json() == {"notify_email": False}
    assert api.put(URL, {"notify_email": True}).json() == {"notify_email": True}
    assert stored(session, owner) is True


def test_it_updates_an_existing_row_and_touches_nothing_else_on_it(api, session, owner):
    session.add(UserSettings(user_id=owner.id, auto_join_rule="organizer", join_instant_meets=True))
    session.commit()
    api.put(URL, {"notify_email": False})
    session.expire_all()
    row = session.get(UserSettings, owner.id)
    assert (row.notify_email, row.auto_join_rule, row.join_instant_meets) == (
        False,
        "organizer",
        True,
    )


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"notify_email": None},
        {"notify_email": "false"},  # a string would be read as true by a lax parser
        {"notify_email": 0},
        {"notify_email": [False]},
        {"notify_email": False, "auto_join_rule": "none"},  # no other setting rides along
        {"notify_email": False, "user_id": 2},
    ],
)
def test_only_a_real_boolean_and_nothing_else_is_accepted(api, session, owner, body):
    error_of(api.put(URL, body), 422, "validation_error")
    assert stored(session, owner) is None


def test_it_needs_login(anon, owner, session):
    error_of(anon.get(URL), 401, "not_authenticated")
    error_of(anon.put(URL, {"notify_email": False}), 401, "not_authenticated")
    assert stored(session, owner) is None


def test_a_change_needs_the_csrf_header(api, owner, session):
    error_of(api.put(URL, {"notify_email": False}, csrf=False), 403, "csrf_rejected")
    error_of(api.put(URL, {"notify_email": False}, csrf="wrong"), 403, "csrf_rejected")
    assert stored(session, owner) is None


def test_it_can_only_change_the_callers_own_row(api, session, owner, stranger):
    """There is no id in the path or the body: whoever is logged in is the one changed."""
    api.put(URL, {"notify_email": False})
    assert stored(session, owner) is False and stored(session, stranger) is None
    other = Api(api.client.__class__(api.client.app, follow_redirects=False))
    assert other.log_in(email=stranger.email).status_code == 200
    assert other.get(URL).json() == {"notify_email": True}  # not the owner's "off"
    other.put(URL, {"notify_email": False})
    other.put(URL, {"notify_email": True})
    assert stored(session, owner) is False and stored(session, stranger) is True


def test_two_first_writes_at_once_do_not_collide_on_the_row(api, session, owner):
    """The first PUT creates the row; a second one racing it must update, not fail on the key."""
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(4) as pool:
        codes = list(
            pool.map(lambda v: api.put(URL, {"notify_email": v}).status_code, [True, False] * 3)
        )
    assert codes == [200] * 6
    assert stored(session, owner) in (True, False)


def test_turning_it_off_stops_the_notes_mail_at_send_time(api, session, owner):
    template = Template(owner_user_id=owner.id, name="General", instructions="x")
    session.add(template)
    session.commit()
    meeting = add_meeting(
        session, template, status="ready", step="done", notes=NOTES, platform="zoom",
        native_id="88205679674",
    )  # fmt: skip
    row = mail.enqueue(session, owner.email, "notes_ready", {"meeting_id": meeting.id})
    session.commit()
    api.put(URL, {"notify_email": False})
    sent, mailer = deliver(session, now=datetime.now(UTC) + timedelta(minutes=1))
    assert sent == 0 and mailer.sent == []
    session.refresh(row)
    assert (row.status, row.last_error) == ("skipped", "opted_out")


# ---- the footer link ----


def queue_at_noon(session, to, template, payload):
    row = mail.enqueue(session, to, template, payload)
    row.created_at = NOW  # the time `deliver` runs at
    session.commit()
    return row


def ready_meeting(session, owner, native_id="88205679674"):
    template = session.scalars(select(Template).where(Template.owner_user_id == owner.id)).first()
    if template is None:
        template = Template(owner_user_id=owner.id, name="General", instructions="x")
        session.add(template)
        session.commit()
    return add_meeting(
        session, template, status="ready", step="done", notes=NOTES, platform="zoom",
        native_id=native_id, created_at=NOW - timedelta(minutes=30),
    )  # fmt: skip


def test_the_notes_mail_links_to_email_preferences_and_carries_only_ids(session, owner):
    meeting = ready_meeting(session, owner)
    queue_at_noon(session, owner.email, "notes_ready", {"meeting_id": meeting.id})
    session.commit()
    sent, mailer = deliver(session)
    assert sent == 1
    message = mailer.sent[0][0]
    prefs = "https://notes.example.test/settings#email-preferences"
    assert f"Email preferences: {prefs}" in message.text
    hrefs = [h.split('"')[0] for h in message.html.split('href="')[1:]]
    assert hrefs == [f"https://notes.example.test/meetings/{meeting.id}", prefs]
    assert ">Email preferences</a>" in message.html
    # as in Emails.dc.html: the link ends the footer's own sentence, in the designer's slate
    assert (
        'You received this because you sent the note taker to this meeting. <a href="'
        f'{prefs}" style="color:#5B6460">Email preferences</a>' in message.html
    )
    # the link names no person, token or meeting: it is the same for every recipient
    assert str(meeting.id) not in prefs and owner.email not in message.html.split(prefs)[1]


def test_the_footer_link_must_be_a_web_address(session):
    from app import mail_layout

    with pytest.raises(ValueError):
        mail_layout.render_text([], "footer", footer_link=("Preferences", "javascript:alert(1)"))
    with pytest.raises(ValueError):
        mail_layout.render_html("t", [], "footer", footer_link=("P", "data:text/html,x"))


def test_the_footer_link_label_is_escaped(session):
    from app import mail_layout

    page = mail_layout.render_html(
        "t", [], "footer", footer_link=("<b>x</b>", "https://notes.example.test/s")
    )
    assert "<b>x</b>" not in page and "&lt;b&gt;x&lt;/b&gt;" in page


# ---- a mail skipped for the per-recipient cap is shown to the meeting's owner ----


def skip_notes(session):
    return list(
        session.scalars(select(Notification).where(Notification.kind == "mail.recipient_cap"))
    )


def test_the_owner_is_told_when_the_cap_swallowed_the_notes_mail(session, owner):
    first, second = ready_meeting(session, owner, "111"), ready_meeting(session, owner, "222")
    for meeting in (first, second):
        queue_at_noon(session, owner.email, "notes_ready", {"meeting_id": meeting.id})
    session.commit()
    sent, _ = deliver(session, mail_recipient_daily_cap=1)
    assert sent == 1
    (note,) = skip_notes(session)
    assert (note.user_id, note.meeting_id, note.severity) == (owner.id, second.id, "warning")
    assert note.title == "A notes email was not sent"
    assert "Zoom 222" in note.body and "1 notes email" in note.body
    assert f"/meetings/{second.id}" in note.body  # where the notes are


def test_nothing_is_said_when_the_mail_went(session, owner):
    meeting = ready_meeting(session, owner)
    queue_at_noon(session, owner.email, "notes_ready", {"meeting_id": meeting.id})
    session.commit()
    assert deliver(session, mail_recipient_daily_cap=1)[0] == 1
    assert skip_notes(session) == []


def test_the_notice_goes_to_the_meetings_owner_and_never_names_the_address(session, owner, plain):
    """A later template mails someone else about the owner's meeting: the owner is told, and the
    notice does not reveal the other person's address."""
    meeting = ready_meeting(session, owner)
    for n in range(2):
        plain_row(session, n, to="friend@example.test")
    row = queue_at_noon(session, "friend@example.test", "test_plain", {"meeting_id": meeting.id})
    session.commit()
    deliver(session, mail_recipient_daily_cap=2)
    session.refresh(row)
    assert row.last_error == "recipient_cap"
    (note,) = skip_notes(session)
    assert note.user_id == owner.id
    assert "friend@example.test" not in note.title + note.body


def test_one_notice_per_meeting_however_often_it_is_skipped(session, owner, plain):
    meeting = ready_meeting(session, owner)
    plain_row(session, 0)
    for _ in range(3):
        queue_at_noon(session, "owner@example.test", "test_plain", {"meeting_id": meeting.id})
    session.commit()
    deliver(session, mail_recipient_daily_cap=1)
    assert len(skip_notes(session)) == 1


def test_a_skip_with_no_meeting_to_tell_anyone_about_makes_no_notice(session, owner, plain):
    plain_row(session, 0)
    plain_row(session, 1)  # no meeting id in the payload
    queue_at_noon(session, "owner@example.test", "test_plain", {"meeting_id": 999_999})
    session.commit()
    sent, _ = deliver(session, mail_recipient_daily_cap=1)
    assert sent == 1
    assert skip_notes(session) == []
    assert session.scalar(select(Notification.id).limit(1)) is None


def test_sign_in_mail_over_the_cap_is_never_skipped_so_never_reported(session, owner):
    for _ in range(3):
        queue_at_noon(session, "owner@example.test", "verify_email", {"link": "https://x.test/v"})
    session.commit()
    assert deliver(session, mail_recipient_daily_cap=1)[0] == 3
    assert skip_notes(session) == []


def test_a_notice_that_cannot_be_written_never_blocks_the_mail_queue(
    session, owner, plain, monkeypatch
):
    meeting = ready_meeting(session, owner)
    plain_row(session, 0)
    queue_at_noon(session, "owner@example.test", "test_plain", {"meeting_id": meeting.id})
    other = queue_at_noon(session, "someone@example.test", "test_plain", {"n": 7})
    session.commit()

    def broken(*args, **kwargs):
        raise RuntimeError("database trouble")

    monkeypatch.setattr(mail.mail_templates, "meeting_name", broken)
    sent, _ = deliver(session, mail_recipient_daily_cap=1)
    session.expire_all()
    assert sent == 2  # the first and the other address's; the failure cost nothing else
    assert session.get(MailOutbox, other.id).status == "sent"


# ---- the cap is checked after the send-time checks, so its notice is never misleading ----


def at_the_cap(session, owner, plain):
    """One notes mail already went to the owner's address: with a cap of 1 the next is over it."""
    plain_row(session, 0, to=owner.email)
    assert deliver(session, mail_recipient_daily_cap=1)[0] == 1


def queue_notes_ready(session, owner, meeting):
    row = queue_at_noon(session, owner.email, "notes_ready", {"meeting_id": meeting.id})
    return row


def test_an_owner_who_turned_the_mail_off_is_not_told_it_was_over_the_cap(session, owner, plain):
    meeting = ready_meeting(session, owner)
    session.add(UserSettings(user_id=owner.id, notify_email=False))
    session.commit()
    at_the_cap(session, owner, plain)
    row = queue_notes_ready(session, owner, meeting)
    deliver(session, mail_recipient_daily_cap=1)
    session.refresh(row)
    assert (row.status, row.last_error) == ("skipped", "opted_out")  # the real reason wins
    assert skip_notes(session) == []


def test_a_suspended_owner_is_not_told_anything_about_the_cap(session, owner, plain):
    meeting = ready_meeting(session, owner)
    at_the_cap(session, owner, plain)
    row = queue_notes_ready(session, owner, meeting)
    owner.status = "suspended"
    session.commit()
    deliver(session, mail_recipient_daily_cap=1)
    session.refresh(row)
    assert (row.status, row.last_error) == ("skipped", "owner_inactive")
    assert skip_notes(session) == []


def test_a_meeting_deleted_while_queued_leaves_no_cap_notice(session, owner, plain):
    meeting = ready_meeting(session, owner)
    at_the_cap(session, owner, plain)
    row = queue_notes_ready(session, owner, meeting)
    session.delete(meeting)
    session.commit()
    deliver(session, mail_recipient_daily_cap=1)
    session.refresh(row)
    assert (row.status, row.last_error) == ("skipped", "meeting_gone")
    assert skip_notes(session) == []


def test_over_the_cap_with_everything_else_fine_is_still_reported(session, owner, plain):
    meeting = ready_meeting(session, owner)
    session.add(UserSettings(user_id=owner.id, notify_email=True))
    session.commit()
    at_the_cap(session, owner, plain)
    row = queue_notes_ready(session, owner, meeting)
    deliver(session, mail_recipient_daily_cap=1)
    session.refresh(row)
    assert (row.status, row.last_error) == ("skipped", "recipient_cap")
    assert len(skip_notes(session)) == 1


# ---- a bounce or complaint wins over the preference that says "send" ----


@pytest.mark.parametrize("reason", ["bounce", "complaint"])
def test_a_bounce_or_complaint_stops_notes_mail_though_the_switch_is_on(session, owner, reason):
    meeting = ready_meeting(session, owner)
    session.add(UserSettings(user_id=owner.id, notify_email=True))
    session.commit()
    mail.suppress(session, owner.email, reason)
    row = queue_at_noon(session, owner.email, "notes_ready", {"meeting_id": meeting.id})
    sent, mailer = deliver(session)
    session.refresh(row)
    assert sent == 0 and mailer.sent == []
    assert (row.status, row.last_error) == ("skipped", "suppressed")


@pytest.mark.parametrize("reason", ["bounce", "complaint"])
def test_a_bounce_or_complaint_stops_sign_in_mail_too_though_the_switch_is_on(
    session, owner, reason
):
    session.add(UserSettings(user_id=owner.id, notify_email=True))
    session.commit()
    mail.suppress(session, owner.email, reason)
    row = queue_at_noon(session, owner.email, "verify_email", {"link": "https://x.test/v"})
    sent, mailer = deliver(session)
    session.refresh(row)
    assert sent == 0 and mailer.sent == []
    assert (row.status, row.last_error) == ("skipped", "suppressed")


def test_an_unsubscribe_stops_notes_mail_but_not_sign_in_mail(session, owner):
    """Contrast: only a bounce or complaint reaches sign-in mail."""
    meeting = ready_meeting(session, owner)
    mail.suppress(session, owner.email, "unsubscribed")
    notes = queue_at_noon(session, owner.email, "notes_ready", {"meeting_id": meeting.id})
    link = queue_at_noon(session, owner.email, "verify_email", {"link": "https://x.test/v"})
    assert deliver(session)[0] == 1
    session.refresh(notes), session.refresh(link)
    assert (notes.last_error, link.status) == ("suppressed", "sent")
