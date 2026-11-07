"""W6 Share notes by email (spec section 10): the routes, the limits, the send-time checks and the
mail itself. Cast from `tests/test_api_tasks.py`: `owner` (records the meetings), `member` on the
owner's team, `outsider` with a team of their own. Nothing here sends a real mail."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app import deletion, mail, shares, translation
from app.models import (
    AuthToken,
    MailOutbox,
    MailSuppression,
    Meeting,
    MeetingShare,
    MeetingShareRecipient,
    ShareUsage,
    User,
)
from tests.test_api import (  # noqa: F401  (fixtures used by name)
    Api,
    anon,
    anon_client,
    api,
    api_routes,
    app,
    client,
    error_of,
    fake_openai,
    fake_vexa,
    owner,
    ready,
    stranger,
    template,
)
from tests.test_api_tasks import (  # noqa: F401
    MEMBER_EMAIL,
    admin,
    as_user,
    member,
    team,
)
from tests.test_mail import FakeMailer
from tests.test_team_visibility import OTHER_EMAIL, outsider, share  # noqa: F401
from tests.test_translation import FakeModel
from tests.test_web import make_settings

NOTES = """\
## Summary
The team agreed to ship on Friday.

## Key points
- Not in the mail.

## Decisions
- Ship on Friday.

## Action items
- Tunde: send the contract

## Open questions
- Who pays for the pilot?

## Please check
- Beyric
"""
TRANSCRIPT = "[Adebayo]: We ship on Friday.\n[Tunde]: I will send the contract."
NOW = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
OUTSIDE = "client@example.test"


def settings(**overrides):
    return make_settings(**{"public_base_url": "https://notes.example.test"} | overrides)


@pytest.fixture
def meeting(session, template) -> Meeting:
    return ready(session, template, notes=NOTES, speaker_transcript=TRANSCRIPT)


def url(meeting) -> str:
    return f"/api/meetings/{meeting.id}/shares"


def body(*emails, **fields) -> dict:
    return {
        "recipients": [{"email": e} for e in (emails or (OUTSIDE,))],
        "language": "en",
        **fields,
    }


def send_all(session, **overrides):
    mailer = FakeMailer()
    count = mail.deliver_pending(session, mailer, settings(**overrides), datetime.now(UTC))
    return count, mailer


# ---- the request ----


def test_a_share_is_202_queued_and_queues_one_mail_per_person(api, session, meeting):
    response = api.post(url(meeting), body(OUTSIDE, "two@example.test", message="Look at this"))
    assert response.status_code == 202
    created = response.json()
    assert set(created) == {"share_id", "status"} and created["status"] == "queued"
    rows = list(session.scalars(select(MailOutbox).where(MailOutbox.template == "notes_shared")))
    assert sorted(r.to_email for r in rows) == [OUTSIDE, "two@example.test"]


def test_the_outbox_holds_ids_only_never_the_notes_or_the_message(api, session, meeting):
    api.post(url(meeting), body(message="Secret words", include_transcript=True))
    row = session.scalars(select(MailOutbox).where(MailOutbox.template == "notes_shared")).one()
    assert set(row.payload) == {"share_id", "recipient_id", "meeting_id"}
    assert all(type(v) is int for v in row.payload.values())
    assert "Secret" not in str(row.payload) and "Friday" not in str(row.payload)


def test_the_list_says_who_got_it_and_where_it_stands(api, session, meeting):
    api.post(url(meeting), body(OUTSIDE, message="Hello", attach="pdf"))
    listed = api.get(url(meeting)).json()
    assert list(listed) == ["shares"] and len(listed["shares"]) == 1
    share_ = listed["shares"][0]
    assert set(share_) == {
        "share_id",
        "status",
        "language",
        "attach",
        "include_transcript",
        "message",
        "created_at",
        "recipients",
    }
    assert share_["recipients"] == [
        {"email": OUTSIDE, "name": None, "status": "pending", "is_team_member": False}
    ]
    assert share_["message"] == "Hello" and share_["attach"] == "pdf"


def test_addresses_are_lowercased_and_duplicates_are_one_person(api, session, meeting):
    api.post(url(meeting), body("Client@Example.test", "client@example.test"))
    assert session.scalars(select(MeetingShareRecipient.email)).all() == [OUTSIDE]


def test_a_teammate_is_recognised_and_gets_their_name(api, session, meeting, member):
    api.post(url(meeting), body(MEMBER_EMAIL.upper(), OUTSIDE))
    people = {r["email"]: r for r in api.get(url(meeting)).json()["shares"][0]["recipients"]}
    assert people[MEMBER_EMAIL]["is_team_member"] and people[MEMBER_EMAIL]["name"] == "Daniel Okoro"
    assert not people[OUTSIDE]["is_team_member"]


@pytest.mark.parametrize("language", ["de", "EN", "", "yo"])
def test_a_language_that_is_not_offered_is_422(api, session, meeting, language):
    response = api.post(url(meeting), body(language=language))
    assert error_of(response, 422, "language_unsupported")
    assert session.scalars(select(MeetingShare)).all() == []


def test_eleven_people_is_422_and_ten_is_fine(api, session, meeting):
    eleven = [f"p{i}@example.test" for i in range(11)]
    error_of(api.post(url(meeting), body(*eleven)), 422, "too_many_recipients")
    assert api.post(url(meeting), body(*eleven[:10])).status_code == 202


def test_a_bad_address_or_nobody_is_refused(api, meeting):
    error_of(api.post(url(meeting), body("not-an-address")), 422, "invalid_recipient")
    error_of(api.post(url(meeting), body("a@b.test\r\nBcc: x@y.test")), 422, "invalid_recipient")
    assert api.post(url(meeting), {"recipients": [], "language": "en"}).status_code == 422


def test_a_message_over_500_characters_is_422_and_nothing_is_made(api, session, meeting):
    assert api.post(url(meeting), body(message="x" * 501)).status_code == 422
    assert api.post(url(meeting), body(message="x" * 500)).status_code == 202


def test_the_twenty_first_recipient_of_the_day_is_429_share_limit(api, session, meeting):
    for batch in range(2):
        people = [f"p{batch}{i}@example.test" for i in range(10)]
        assert api.post(url(meeting), body(*people)).status_code == 202
    error_of(api.post(url(meeting), body(OUTSIDE)), 429, "share_limit")
    assert session.scalar(select(MeetingShare.id).order_by(MeetingShare.id.desc())) == 2


def test_an_older_share_does_not_count_after_a_day(api, session, meeting, owner):
    api.post(url(meeting), body(*[f"p{i}@example.test" for i in range(10)]))
    api.post(url(meeting), body(*[f"q{i}@example.test" for i in range(10)]))
    session.query(ShareUsage).update({"created_at": datetime.now(UTC) - timedelta(hours=25)})
    session.commit()
    assert api.post(url(meeting), body(OUTSIDE)).status_code == 202


def test_deleting_the_meeting_does_not_give_the_days_capacity_back(api, session, meeting, template):
    other = ready(session, template, notes=NOTES, speaker_transcript=TRANSCRIPT)
    api.post(url(meeting), body(*[f"p{i}@example.test" for i in range(10)]))
    api.post(url(meeting), body(*[f"q{i}@example.test" for i in range(10)]))
    assert api.delete(f"/api/meetings/{meeting.id}").status_code == 204
    error_of(api.post(url(other), body(OUTSIDE)), 429, "share_limit")


def test_an_unconfirmed_sender_is_403(api, session, meeting, owner):
    owner.email_verified_at = None
    session.commit()
    error_of(api.post(url(meeting), body()), 403, "email_not_verified")


def test_a_meeting_without_notes_is_409(api, session, template):
    waiting = ready(session, template, notes=None, status="processing", step="notes")
    error_of(api.post(url(waiting), body()), 409, "not_ready")


def test_nobody_but_the_owner_may_share_or_list(app, api, session, meeting, member, outsider):
    share(session, meeting, session.get(User, meeting.owner_user_id))
    for email in (OTHER_EMAIL, MEMBER_EMAIL):  # a stranger, and a teammate who can read it
        who = as_user(app, email)
        error_of(who.post(url(meeting), body()), 404, "not_found")
        error_of(who.get(url(meeting)), 404, "not_found")
    assert session.scalars(select(MeetingShare)).all() == []


def test_a_missing_meeting_is_404(api):
    error_of(api.post("/api/meetings/999999/shares", body()), 404, "not_found")
    error_of(api.get("/api/meetings/999999/shares"), 404, "not_found")


def test_both_routes_are_in_the_route_list(app):
    routes = set(api_routes(app))
    assert {
        ("GET", "/api/meetings/{meeting_id}/shares"),
        ("POST", "/api/meetings/{meeting_id}/shares"),
        ("POST", "/api/mail/opt-out"),
    } <= routes
    with_share = {r for r in routes if "/shares" in r[1]}
    assert len(with_share) == 2  # nothing else under a share without being classified here


# ---- translation first, then the mail ----


def test_an_untranslated_language_waits_for_the_translation_then_queues_the_mails(
    api, session, meeting
):
    response = api.post(url(meeting), body(OUTSIDE, language="fr"))
    assert response.status_code == 202 and response.json()["status"] == "waiting_translation"
    assert session.scalars(select(MailOutbox)).all() == []
    translation.process_next(session, datetime.now(UTC), client=FakeModel())
    session.expire_all()
    assert session.scalars(select(MeetingShare.status)).one() == "queued"
    assert len(session.scalars(select(MailOutbox)).all()) == 1


def test_a_failed_translation_fails_the_share_and_tells_the_sender(api, session, meeting, owner):
    api.post(url(meeting), body(language="es"))
    broken = FakeModel(lambda texts, n: ["wrong count"])
    translation.process_next(session, datetime.now(UTC), client=broken)
    session.expire_all()
    assert session.scalars(select(MeetingShare.status)).one() == "failed"
    assert session.scalars(select(MeetingShareRecipient.status)).one() == "failed"
    assert session.scalars(select(MailOutbox)).all() == []


def test_a_cached_translation_queues_at_once(api, session, meeting):
    api.post(f"/api/meetings/{meeting.id}/translations", {"language": "fr"})
    translation.process_next(session, datetime.now(UTC), client=FakeModel())
    response = api.post(url(meeting), body(language="fr"))
    assert response.json()["status"] == "queued"


def test_the_sweep_settles_a_share_whose_translation_finished_without_the_hook(
    api, session, meeting, monkeypatch
):
    api.post(url(meeting), body(language="fr"))
    monkeypatch.setattr(shares, "on_translation_finished", lambda *a: 0)
    translation.process_next(session, datetime.now(UTC), client=FakeModel())
    session.expire_all()
    assert session.scalars(select(MeetingShare.status)).one() == "waiting_translation"
    shares.sweep(session, datetime.now(UTC))
    assert session.scalars(select(MeetingShare.status)).one() == "queued"


def test_a_share_that_waited_too_long_fails(api, session, meeting):
    api.post(url(meeting), body(language="fr"))
    session.query(MeetingShare).update({"created_at": datetime.now(UTC) - timedelta(hours=3)})
    session.commit()
    shares.sweep(session, datetime.now(UTC))
    assert session.scalars(select(MeetingShare.status)).one() == "failed"


# ---- the mail ----


def only_message(mailer):
    assert len(mailer.sent) == 1
    return mailer.sent[0][0]


def test_the_mail_is_the_designers_and_says_who_sent_it(api, session, meeting, owner):
    owner.name = "Adebayo Tosin"
    session.commit()
    api.post(url(meeting), body(OUTSIDE, message="Look <b>here</b> https://evil.test/x"))
    count, mailer = send_all(session)
    assert count == 1
    message = only_message(mailer)
    assert message.to == OUTSIDE and message.reply_to == owner.email
    assert message.subject.startswith("Adebayo Tosin shared meeting notes: ")
    assert "Adebayo shared meeting notes with you" in message.html
    assert f"Sent by {owner.email} using Beyric Note Taker." in message.html
    assert "The team agreed to ship on Friday." in message.html
    assert "Ship on Friday." in message.html and "Who pays for the pilot?" in message.html
    assert "Not in the mail" not in message.html  # abridged: four sections only
    assert "&lt;b&gt;here&lt;/b&gt;" in message.html and "<b>here" not in message.html
    assert 'href="https://evil.test' not in message.html


def test_an_outside_recipient_gets_no_link_to_the_notes_only_the_opt_out(
    api, session, meeting, owner
):
    api.post(url(meeting), body(OUTSIDE))
    message = only_message(send_all(session)[1])
    assert f"/meetings/{meeting.id}" not in message.html
    assert f"/meetings/{meeting.id}" not in message.text
    assert message.html.count("href=") == 1
    assert "https://notes.example.test/unsubscribe?token=" in message.html


def test_a_teammate_gets_the_signed_in_link_only_when_they_may_read_the_meeting(
    api, session, meeting, owner, member
):
    api.post(url(meeting), body(MEMBER_EMAIL))
    private = only_message(send_all(session)[1])
    assert f"/meetings/{meeting.id}" not in private.html  # the link would be a 404 for them
    share(session, meeting, owner)
    api.post(url(meeting), body(MEMBER_EMAIL))
    shared = send_all(session, mail_recipient_daily_cap=0)[1].sent[0][0]
    assert f"https://notes.example.test/meetings/{meeting.id}" in shared.html


def test_a_transcript_alone_is_a_text_file(api, session, meeting):
    api.post(url(meeting), body(include_transcript=True))
    message = only_message(send_all(session)[1])
    (file,) = message.attachments
    assert file.content_type == "text/plain" and file.filename.endswith("transcript.txt")
    assert file.content.decode() == TRANSCRIPT
    assert "The full transcript is attached." in message.html


def test_a_word_file_carries_every_section_and_the_transcript_inside(api, session, meeting):
    api.post(url(meeting), body(attach="docx", include_transcript=True))
    message = only_message(send_all(session)[1])
    (file,) = message.attachments
    assert file.filename.endswith(".docx") and file.content[:2] == b"PK"
    assert "The full transcript is attached." in message.html


def test_no_attachment_and_no_transcript_says_nothing_about_files(api, session, meeting):
    api.post(url(meeting), body())
    message = only_message(send_all(session)[1])
    assert message.attachments == () and "attached" not in message.html


def test_a_translated_mail_has_the_translation_then_the_original_below_a_divider(
    api, session, meeting
):
    api.post(url(meeting), body(language="fr"))
    translation.process_next(session, datetime.now(UTC), client=FakeModel())
    message = only_message(send_all(session)[1])
    assert "Résumé" in message.html and "<hr" in message.html
    assert message.html.index("Résumé") < message.html.index("Original notes (English)")
    assert message.html.index("Original notes (English)") < message.html.rindex("Summary")
    assert "href" in message.html and f"/meetings/{meeting.id}" not in message.html


def test_a_suppressed_recipient_is_skipped_and_shown_only_as_not_sent(api, session, meeting):
    mail.suppress(session, OUTSIDE, "unsubscribed")
    session.commit()
    api.post(url(meeting), body(OUTSIDE, "other@example.test"))
    count, mailer = send_all(session)
    assert count == 1 and mailer.sent[0][0].to == "other@example.test"
    recipients = api.get(url(meeting)).json()["shares"][0]["recipients"]
    assert {r["email"]: r["status"] for r in recipients} == {
        OUTSIDE: "not_sent",
        "other@example.test": "sent",
    }


def test_the_opt_out_link_stops_notes_mail_to_that_address_once(anon, api, session, meeting):
    api.post(url(meeting), body(OUTSIDE))
    message = only_message(send_all(session)[1])
    token = message.html.split("unsubscribe?token=")[1].split('"')[0]
    stored = session.scalars(select(AuthToken).where(AuthToken.purpose == "mail_optout")).one()
    assert stored.token_hash != token and stored.email == OUTSIDE  # only the hash is kept
    visitor = Api(anon.client.__class__(anon.client.app))
    done = visitor.post("/api/mail/opt-out", {"token": token})
    assert done.status_code == 200 and done.json() == {"status": "unsubscribed"}
    assert session.scalars(select(MailSuppression.reason)).all() == ["unsubscribed"]
    error_of(visitor.post("/api/mail/opt-out", {"token": token}), 400, "token_invalid_or_expired")
    error_of(visitor.post("/api/mail/opt-out", {"token": "nope"}), 400, "token_invalid_or_expired")
    api.post(url(meeting), body(OUTSIDE))
    assert send_all(session)[0] == 0  # skipped now


# ---- send-time checks ----


def outcome(session):
    return [tuple(r) for r in session.execute(select(MailOutbox.status, MailOutbox.last_error))]


def test_a_revoked_share_is_not_sent(api, session, meeting):
    api.post(url(meeting), body())
    session.query(MeetingShare).update({"revoked_at": datetime.now(UTC)})
    session.commit()
    assert send_all(session)[0] == 0
    assert outcome(session) == [("skipped", "share_revoked")]
    assert session.scalars(select(MeetingShareRecipient.status)).one() == "not_sent"


def test_a_deleted_meeting_takes_the_share_and_its_mail_away(api, session, meeting):
    api.post(url(meeting), body())
    api.delete(f"/api/meetings/{meeting.id}")
    session.expire_all()
    assert session.scalars(select(MeetingShare)).all() == []
    assert send_all(session)[0] == 0
    assert outcome(session) == [("skipped", "share_gone")]


def test_an_owner_being_deleted_stops_the_mail(api, session, meeting, owner):
    api.post(url(meeting), body())
    owner.status = "deleted"
    session.commit()
    assert send_all(session)[0] == 0
    assert outcome(session) == [("skipped", "owner_inactive")]


def test_notes_rewritten_after_the_share_are_what_is_mailed(api, session, meeting):
    api.post(url(meeting), body())
    meeting.notes = NOTES.replace("ship on Friday", "ship on Monday")
    session.commit()
    assert "ship on Monday" in only_message(send_all(session)[1]).html


def test_a_translation_gone_stale_before_sending_is_not_mailed_in_the_wrong_words(
    api, session, meeting
):
    api.post(url(meeting), body(language="fr"))
    translation.process_next(session, datetime.now(UTC), client=FakeModel())
    meeting.notes = NOTES + "\n- more"
    session.commit()
    assert send_all(session)[0] == 0
    assert outcome(session) == [("skipped", "translation_missing")]


def test_the_account_daily_cap_of_a_pilot_account_is_not_the_recipient_cap(api, session, meeting):
    """An address already mailed 5 notes mails in a day is skipped (the existing cap)."""
    for _ in range(2):
        api.post(url(meeting), body(OUTSIDE))
    count, _ = send_all(session, mail_recipient_daily_cap=1)
    assert count == 1


def test_deleting_the_sender_removes_their_shares(api, session, meeting, owner):
    api.post(url(meeting), body())
    deletion.purge_user(session, owner, vexa=None, force=True)
    assert session.scalars(select(MeetingShare)).all() == []
    assert session.scalars(select(MeetingShareRecipient)).all() == []


def test_a_web_address_in_the_message_or_the_name_is_not_left_as_one(api, session, meeting, owner):
    owner.name = "Alert at evil.example"
    session.commit()
    api.post(url(meeting), body(message="Log in at https://evil.example/login or evil.example"))
    message = only_message(send_all(session)[1])
    assert "https://evil.example" not in message.html and "evil.example" not in message.html
    assert "evil[.]example" in message.html


def test_shared_mail_does_not_use_up_the_addresses_own_notes_mail(api, session, meeting, owner):
    for _ in range(5):
        api.post(url(meeting), body(owner.email))
        send_all(session)
    row = mail.enqueue(session, owner.email, "notes_ready", {"meeting_id": meeting.id})
    session.commit()
    count, _ = send_all(session)
    assert count == 1 and session.get(MailOutbox, row.id).status == "sent"
