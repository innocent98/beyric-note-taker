"""W0.3: the message checks, the designer's layout, suppression, caps, the send-time checks and
the notes-ready mail."""

import base64
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select, text

from app import admin, mail, mail_layout, mail_templates, pipeline
from app.mail_message import (
    MAX_ATTACHMENT_BYTES,
    Attachment,
    Message,
    clean_line,
    valid_address,
)
from app.models import MailOutbox, MailSuppression, Template, UserSettings
from tests.test_mail import LINK, FakeMailer, queue, resend
from tests.test_web import add_meeting, make_settings, make_user

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
PDF = "application/pdf"
NOTES = """\
## Summary
The team agreed to ship on Friday. Budget was discussed at length and nothing else mattered.

## Key points
- Not in the mail.

## Decisions
- Ship on Friday.
- Keep the price.

## Action items
None noted

## Open questions
- Who pays for the pilot?

## Please check
- Beyric
"""


def settings(**overrides):
    return make_settings(**{"public_base_url": "https://notes.example.test"} | overrides)


@pytest.fixture
def ready(session):
    """A ready meeting with notes, and its owner."""
    owner = make_user(session, "owner@example.test", password=None)
    template = Template(owner_user_id=owner.id, name="General", instructions="x")
    session.add(template)
    session.commit()
    meeting = add_meeting(
        session,
        template,
        status="ready",
        step="done",
        notes=NOTES,
        platform="zoom",
        native_id="88205679674",
        created_at=NOW - timedelta(minutes=30),
    )
    return meeting


def notes_row(session, meeting, created=NOW):
    row = mail.enqueue(session, "owner@example.test", "notes_ready", {"meeting_id": meeting.id})
    row.created_at = created
    session.commit()
    return row


@pytest.fixture
def plain(monkeypatch):
    """A notes template that builds a trivial message: for tests of the caps, which need many rows
    that may all be sent (the real one allows a single mail per meeting)."""

    def build(session, payload, to, settings, now):
        return Message(to, "Plain", "t", "h")

    monkeypatch.setitem(mail_templates.NOTES_TEMPLATES, "test_plain", build)


def plain_row(session, n, to="owner@example.test", created=NOW):
    row = mail.enqueue(session, to, "test_plain", {"n": n})
    row.created_at = created
    session.commit()
    return row


def deliver(session, now=NOW, **overrides):
    mailer = FakeMailer()
    sent = mail.deliver_pending(session, mailer, settings(**overrides), now)
    return sent, mailer


# ---- the message refuses what could change the mail around it ----


@pytest.mark.parametrize(
    "to",
    [
        "a@b.test\r\nBcc: evil@x.test",
        "a@b.test,evil@x.test",
        "Ada <a@b.test>",
        '"a b"@b.test',
        "a@b.test‮",
        "a b@b.test",
        "no-at-sign",
        "@b.test",
        "a@",
        "a@b.test;c@d.test",
        "",
    ],
)
def test_a_recipient_that_is_not_one_plain_address_is_refused(to):
    assert not valid_address(to)
    with pytest.raises(ValueError):
        Message(to, "s", "t", "h")


def test_a_plain_address_is_accepted():
    for address in ("ada@example.test", "a.b+tag@sub.example.test", "李雷@example.test"):
        assert valid_address(address)


@pytest.mark.parametrize("subject", ["Hi\r\nBcc: x@y.test", "Hi\nthere", "Hi\x00", "Hi x"])
def test_a_subject_with_a_line_break_is_refused(subject):
    with pytest.raises(ValueError):
        Message("a@b.test", subject, "t", "h")


def test_reply_to_and_headers_are_checked():
    ok = Message(
        "a@b.test",
        "s",
        "t",
        "h",
        reply_to="x@y.test",
        headers=(("List-Unsubscribe", "<https://example.test/u>"),),
    )
    assert ok.reply_to == "x@y.test"
    for bad in (
        {"reply_to": "x@y.test\r\nBcc: z@y.test"},
        {"headers": (("X-A", "v\r\nBcc: z@y.test"),)},
        {"headers": (("X A", "v"),)},
        {"headers": (("X-A:", "v"),)},
        {"headers": (("X-Custom", "v"),)},
    ):
        with pytest.raises(ValueError):
            Message("a@b.test", "s", "t", "h", **bad)


def test_attachments_are_limited_in_type_name_count_and_size():
    Attachment("notes.pdf", b"%PDF", PDF)
    for args in (
        ("../notes.pdf", b"x", PDF),
        ("a/b.pdf", b"x", PDF),
        ("n\r\n.pdf", b"x", PDF),
        ("", b"x", PDF),
        ("n.exe", b"x", "application/x-msdownload"),
        ("n.html", b"x", "text/html"),
        ("n.pdf", b"", PDF),
    ):
        with pytest.raises(ValueError):
            Attachment(*args)
    big = Attachment("a.pdf", b"x" * (MAX_ATTACHMENT_BYTES // 2 + 1), PDF)
    with pytest.raises(ValueError, match="too large"):
        Message("a@b.test", "s", "t", "h", attachments=(big, big))
    small = Attachment("a.pdf", b"x", PDF)
    with pytest.raises(ValueError, match="too many"):
        Message("a@b.test", "s", "t", "h", attachments=(small,) * 4)


def test_the_repr_of_a_message_holds_no_body_and_no_file_contents():
    message = Message(
        "a@b.test", "Subject", "SECRET-NOTES", "<p>SECRET-NOTES</p>",
        attachments=(Attachment("n.pdf", b"SECRET-FILE", PDF),),
    )  # fmt: skip
    assert "SECRET" not in repr(message)


def test_clean_line_strips_controls_collapses_space_and_clips():
    assert clean_line("A\r\n\tB ‮ C​", 50) == "A B C"
    assert clean_line("x" * 30, 10) == "x" * 9 + "…"


# ---- the Resend call carries attachments, reply-to and headers ----


def test_resend_sends_attachments_as_base64_with_reply_to_and_headers():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "abc"})

    message = Message(
        "a@b.test", "s", "t", "h",
        attachments=(Attachment("n.pdf", b"%PDF-1", PDF),),
        reply_to="sender@example.test",
        headers=(("List-Unsubscribe", "<https://example.test/u>"),),
    )  # fmt: skip
    resend(handler).send(message, "mail-1")
    body = seen["body"]
    assert body["reply_to"] == "sender@example.test"
    assert body["headers"] == {"List-Unsubscribe": "<https://example.test/u>"}
    [attachment] = body["attachments"]
    assert attachment["filename"] == "n.pdf" and attachment["content_type"] == PDF
    assert base64.b64decode(attachment["content"]) == b"%PDF-1"


def test_the_console_mailer_names_attachments_without_printing_them(capsys):
    message = Message(
        "a@b.test", "s", "t", "h", attachments=(Attachment("n.pdf", b"RAWFILE", PDF),)
    )
    mail.ConsoleMailer().send(message, "k")
    out = capsys.readouterr().out
    assert "n.pdf" in out and "RAWFILE" not in out


# ---- the layout escapes everything and links only http(s) ----


def test_every_string_is_escaped_in_the_html_and_plain_in_the_text():
    hostile = '<img src=x onerror=alert(1)> & "quotes"'
    blocks = [
        mail_layout.Heading(hostile),
        mail_layout.Meta(hostile),
        mail_layout.Para(hostile),
        mail_layout.Label(hostile),
        mail_layout.Bullets((hostile,)),
        mail_layout.Quote(hostile),
    ]
    page = mail_layout.render_html(hostile, blocks, hostile)
    assert "<img" not in page and "onerror=alert(1)>" not in page
    assert page.count("&lt;img src=x onerror=alert(1)&gt; &amp; &quot;quotes&quot;") >= 6
    assert hostile in mail_layout.render_text(blocks, hostile)


@pytest.mark.parametrize(
    "url",
    ["javascript:alert(1)", "data:text/html,x", "//evil.test/x", "/relative", "https://", "ftp://a.test/x",
     "https://a.test/x y", "https://a.test/\r\nx"],
)  # fmt: skip
def test_a_button_url_must_be_absolute_http_or_https(url):
    with pytest.raises(ValueError):
        mail_layout.render_html("t", [mail_layout.Button("Go", url)], "f")


def test_the_button_url_is_attribute_escaped():
    page = mail_layout.render_html(
        "t", [mail_layout.Button("Go", 'https://a.test/?a=1&b="2"')], "f"
    )
    assert 'href="https://a.test/?a=1&amp;b=&quot;2&quot;"' in page


def test_the_link_mails_use_the_designers_layout():
    message = mail.render("verify_email", {"link": LINK, "name": "Ada"}, "a@b.test", settings())
    assert "<h1" in message.html and "Confirm your email address" in message.html
    assert "background:#0E6B5E" in message.html  # the button
    assert "Beyrichq LTD" in message.html and "Beyrichq LTD" in message.text
    assert f"Confirm email:\n{LINK}" in message.text
    assert message.html.startswith("<!doctype html>")


# ---- ids only in the outbox ----


@pytest.mark.parametrize(
    "payload",
    [{"meeting_id": "7"}, {"meeting_id": 7, "title": "x"}, {"meeting_id": True}, {"n": 1.5}],
)
def test_a_notes_template_payload_must_hold_integer_ids_only(session, payload):
    with pytest.raises(ValueError, match="ids only"):
        mail.enqueue(session, "a@b.test", "notes_ready", payload)


def test_a_link_payload_is_still_free_form(session):
    row = mail.enqueue(session, "a@b.test", "verify_email", {"link": LINK, "name": "Ada"})
    assert row.payload["link"] == LINK


# ---- the notes-ready mail ----


def test_notes_ready_reads_the_notes_when_it_is_sent_and_stores_none(session, ready):
    row = notes_row(session, ready)
    assert row.payload == {"meeting_id": ready.id}
    sent, mailer = deliver(session)
    assert sent == 1
    [(message, _)] = mailer.sent
    assert message.to == "owner@example.test"
    assert message.subject == "Your notes are ready: Zoom 88205679674"
    for part in (message.text, message.html):
        assert "Your meeting notes are ready" in part
        assert "The team agreed to ship on Friday." in part
        assert "Ship on Friday." in part and "Keep the price." in part
        assert "Who pays for the pilot?" in part
        assert "None noted" in part
        assert "Not in the mail" not in part and "Please check" not in part
        assert "you sent the note taker to this meeting" in part
    assert f"https://notes.example.test/meetings/{ready.id}" in message.text
    assert "left early" not in message.text
    assert "Zoom 88205679674 · 03 Nov 2026, 11:30 UTC" in message.text
    session.refresh(row)
    # the ids stay (no secret in them; they stop a second mail for the meeting), the text never is
    assert (row.status, row.payload, row.last_error) == ("sent", {"meeting_id": ready.id}, None)
    # nothing of the meeting is in any column of any outbox row
    dump = json.dumps(
        [[str(v) for v in r] for r in session.execute(text("select * from mail_outbox")).all()]
    )
    assert "ship on Friday" not in dump.lower() and "Who pays" not in dump


def test_notes_ready_says_so_when_the_bot_left_early(session, ready):
    ready.completion_reason = "evicted"
    session.commit()
    notes_row(session, ready)
    [(message, _)] = deliver(session)[1].sent
    assert "The note taker left early, so these notes may be incomplete." in message.text
    assert "left early" in message.html


def test_the_left_early_reasons_are_the_pipelines_removal_reasons():
    assert mail_templates.LEFT_EARLY_REASONS == pipeline.REMOVAL_REASONS


def test_a_custom_template_without_the_usual_headings_still_gets_its_first_section(session, ready):
    ready.notes = "## Standup\n\nAda is blocked on the API.\n\n## Other\n\nmore"
    session.commit()
    notes_row(session, ready)
    [(message, _)] = deliver(session)[1].sent
    assert "Standup" in message.text and "Ada is blocked on the API." in message.text
    assert "more" not in message.text


def test_long_notes_are_abridged_at_a_sentence_and_a_count(session, ready):
    sentences = " ".join(f"Sentence number {i} is here." for i in range(60))
    items = "\n".join(f"- Decision {i}" for i in range(9))
    ready.notes = f"## Summary\n\n{sentences}\n\n## Decisions\n\n{items}\n"
    session.commit()
    notes_row(session, ready)
    [(message, _)] = deliver(session)[1].sent
    summary = message.text.split("Summary\n\n")[1].split("\n\n")[0]
    assert len(summary) <= mail_templates.MAX_PARAGRAPH_CHARS and summary.endswith("here.")
    assert "- Decision 5" in message.text and "- Decision 6" not in message.text
    assert "and 3 more in the full notes" in message.text


def test_hostile_meeting_text_is_inert_in_the_html(session, ready):
    ready.native_id = "<b>88</b>"
    ready.notes = (
        "## Summary\n\n<script>alert(1)</script> [click](javascript:alert(2)) "
        "https://evil.test/login\n\n## Decisions\n\n- <img src=x onerror=alert(3)>\n"
    )
    session.commit()
    notes_row(session, ready)
    [(message, _)] = deliver(session)[1].sent
    assert (
        "<script" not in message.html and "<img" not in message.html and "<b>" not in message.html
    )
    assert "&lt;script&gt;" in message.html
    hrefs = [h.split('"')[0] for h in message.html.split('href="')[1:]]
    assert hrefs == [  # the button, and the footer's link to the email preferences: no more
        f"https://notes.example.test/meetings/{ready.id}",
        "https://notes.example.test/settings#email-preferences",
    ]
    assert "\n" not in message.subject


def test_the_notes_are_never_a_link_that_logs_anyone_in(session, ready):
    notes_row(session, ready)
    [(message, _)] = deliver(session)[1].sent
    assert "token" not in message.html.lower() and "token" not in message.text.lower()


# ---- the sender decides at send time ----


def skipped(session, row, reason, *, ids_only=True):
    session.refresh(row)
    assert (row.status, row.last_error) == ("skipped", reason)
    if row.template in mail_templates.NOTES_TEMPLATES:  # ids only, never text
        if not ids_only:
            return
        assert all(isinstance(v, int) for v in row.payload.values())
    else:
        assert row.payload == {}  # a link row never keeps its link


def test_a_meeting_deleted_after_queueing_is_not_mailed(session, ready):
    row = notes_row(session, ready)
    session.delete(ready)
    session.commit()
    sent, mailer = deliver(session)
    assert sent == 0 and mailer.sent == []
    skipped(session, row, "meeting_gone")


@pytest.mark.parametrize("status", ["deleted", "suspended"])
def test_an_owner_who_is_leaving_is_not_mailed(session, ready, status):
    row = notes_row(session, ready)
    owner = session.get(mail_templates.User, ready.owner_user_id)
    owner.status = status
    session.commit()
    assert deliver(session)[0] == 0
    skipped(session, row, "owner_inactive")


def test_a_meeting_that_is_not_ready_is_not_mailed(session, ready):
    row = notes_row(session, ready)
    ready.status = "failed"
    session.commit()
    deliver(session)
    skipped(session, row, "not_ready")


def test_the_owners_setting_is_checked_at_send_time(session, ready):
    session.add(UserSettings(user_id=ready.owner_user_id, notify_email=True))
    session.commit()
    row = notes_row(session, ready)
    session.get(UserSettings, ready.owner_user_id).notify_email = False
    session.commit()
    assert deliver(session)[0] == 0
    skipped(session, row, "opted_out")


def test_a_row_for_an_address_that_is_no_longer_the_owners_is_not_mailed(session, ready):
    row = notes_row(session, ready)
    session.get(mail_templates.User, ready.owner_user_id).email = "new@example.test"
    session.commit()
    deliver(session)
    skipped(session, row, "recipient_changed")


def test_a_malformed_meeting_id_in_a_row_is_skipped_not_crashing(session):
    row = MailOutbox(
        to_email="owner@example.test",
        template="notes_ready",
        payload={"meeting_id": "x"},
        created_at=NOW,
    )
    session.add(row)
    session.commit()
    deliver(session)
    skipped(session, row, "meeting_gone", ids_only=False)


def test_a_skipped_row_is_not_a_failure_for_the_admin(session, ready):
    row = notes_row(session, ready)
    session.delete(ready)
    session.commit()
    deliver(session)
    assert row.id not in [r.id for r in admin.mail_failures(session)]


def test_a_guard_registered_by_a_later_phase_stops_its_mail(session, ready, monkeypatch):
    """The share mail of W6 holds only a `share_id` and refuses a revoked share at send time."""
    revoked = {7}

    @mail_templates.notes_template("test_share")
    def build(session, payload, to, settings, now):
        if payload["share_id"] in revoked:
            return mail_templates.Skip("share_revoked")
        return Message(to, "Shared", "t", "h")

    monkeypatch.setitem(mail_templates.NOTES_TEMPLATES, "test_share", build)
    gone = mail.enqueue(session, "x@example.test", "test_share", {"share_id": 7})
    live = mail.enqueue(session, "y@example.test", "test_share", {"share_id": 8})
    gone.created_at = live.created_at = NOW
    session.commit()
    sent, mailer = deliver(session)
    assert sent == 1 and [m.to for m, _ in mailer.sent] == ["y@example.test"]
    skipped(session, gone, "share_revoked")
    session.refresh(live)
    assert live.status == "sent"


def test_a_row_with_an_invalid_address_fails_without_sending(session):
    row = MailOutbox(
        to_email="a@b.test\r\nbcc: x@y.test",
        template="verify_email",
        payload={"link": LINK},
        created_at=NOW,
    )
    session.add(row)
    session.commit()
    sent, mailer = deliver(session)
    assert sent == 0 and mailer.sent == []
    session.refresh(row)
    assert (row.status, row.last_error, row.payload) == ("failed", "invalid address", {})


def test_a_builder_that_makes_an_unsafe_message_fails_the_row(session, monkeypatch):
    def build(session, payload, to, settings, now):
        return Message(to, "bad\nsubject", "t", "h")

    monkeypatch.setitem(mail_templates.NOTES_TEMPLATES, "test_bad", build)
    row = mail.enqueue(session, "a@b.test", "test_bad", {"n": 1})
    row.created_at = NOW
    session.commit()
    deliver(session)
    session.refresh(row)
    assert (row.status, row.last_error) == ("failed", "invalid message")


def test_a_builder_that_raises_is_counted_and_never_blocks_the_next_row(
    session, ready, monkeypatch, caplog
):
    def build(session, payload, to, settings, now):
        raise RuntimeError("leaked owner@example.test and the notes text")

    monkeypatch.setitem(mail_templates.NOTES_TEMPLATES, "test_boom", build)
    bad = mail.enqueue(session, "a@b.test", "test_boom", {"n": 1})
    good = queue(session, to="ok@example.test")
    bad.created_at = NOW
    session.commit()
    sent, mailer = deliver(session)
    assert sent == 1 and mailer.sent[0][0].to == "ok@example.test"
    session.refresh(bad)
    assert (bad.status, bad.attempts, bad.last_error) == ("pending", 1, "RuntimeError")
    for minutes in (1, 6, 36):
        deliver(session, now=NOW + timedelta(minutes=minutes))
    session.refresh(bad)
    assert (bad.status, bad.attempts, bad.payload) == ("failed", 4, {"n": 1})
    assert "leaked" not in caplog.text and "owner@example.test" not in caplog.text
    session.refresh(good)
    assert good.status == "sent"


# ---- suppression ----


def hashed_only(session, address):
    rows = session.scalars(select(MailSuppression)).all()
    assert [r.email_hash for r in rows] == [mail.email_hash(address)]
    assert address not in repr(session.execute(text("select * from mail_suppressions")).all())


def test_suppress_stores_a_hash_and_is_idempotent(session):
    mail.suppress(session, "Ada@Example.test", "bounce")
    mail.suppress(session, "ada@example.test", "complaint")
    session.commit()
    hashed_only(session, "ada@example.test")
    assert mail.suppression_reason(session, "ADA@example.test") == "bounce"  # the first stays
    assert mail.lift_suppression(session, "ada@example.test") is True
    assert mail.lift_suppression(session, "ada@example.test") is False
    assert mail.suppression_reason(session, "ada@example.test") is None


@pytest.mark.parametrize("reason", ["bounce", "complaint"])
def test_a_bounce_or_complaint_stops_all_mail_including_sign_in_mail(session, ready, reason):
    mail.suppress(session, "owner@example.test", reason)
    session.commit()
    link = queue(session, to="owner@example.test")
    notes = notes_row(session, ready)
    sent, mailer = deliver(session)
    assert sent == 0 and mailer.sent == []
    skipped(session, link, "suppressed")
    skipped(session, notes, "suppressed")


def test_an_unsubscribe_stops_notes_mail_but_not_sign_in_mail(session, ready):
    mail.suppress(session, "owner@example.test", "unsubscribed")
    session.commit()
    link = queue(session, to="owner@example.test")
    notes = notes_row(session, ready)
    sent, mailer = deliver(session)
    assert sent == 1 and mailer.sent[0][0].subject.startswith("Confirm your email")
    session.refresh(link)
    assert link.status == "sent"
    skipped(session, notes, "suppressed")


def test_suppression_is_checked_at_send_time_not_when_queued(session, ready):
    row = notes_row(session, ready)  # queued while the address was fine
    mail.suppress(session, "owner@example.test", "complaint")
    session.commit()
    assert deliver(session)[0] == 0
    skipped(session, row, "suppressed")


def test_a_suppressed_row_never_builds_the_message(session, ready, monkeypatch):
    mail.suppress(session, "owner@example.test", "bounce")
    notes_row(session, ready)

    def refuse(*args, **kwargs):
        raise AssertionError("the notes were read for a suppressed address")

    monkeypatch.setattr(mail_templates, "prepare", refuse)
    deliver(session)


# ---- caps ----


def test_one_address_gets_at_most_the_recipient_cap_of_notes_mails_a_day(session, plain):
    rows = [plain_row(session, n) for n in range(3)]
    sent, mailer = deliver(session, mail_recipient_daily_cap=2)
    assert sent == 2
    session.expire_all()
    assert [r.status for r in rows] == ["sent", "sent", "skipped"]
    assert rows[2].last_error == "recipient_cap"
    # a day later the allowance is back
    row = plain_row(session, 9, created=NOW + timedelta(days=1))
    assert (
        deliver(session, now=NOW + timedelta(days=1, minutes=1), mail_recipient_daily_cap=2)[0] == 1
    )
    session.refresh(row)
    assert row.status == "sent"


def test_sign_in_mail_is_not_counted_against_the_recipient_cap(session):
    for _ in range(3):
        queue(session, to="owner@example.test")
    assert deliver(session, mail_recipient_daily_cap=1)[0] == 3


def test_notes_mail_waits_for_tomorrow_when_the_days_quota_is_spent(session, ready):
    queue(session, to="a@example.test")
    queue(session, to="b@example.test")
    notes = notes_row(session, ready)
    sent, _ = deliver(session, mail_daily_cap=2)
    assert sent == 2  # the two link mails went; they are never held back
    session.refresh(notes)
    assert (notes.status, notes.attempts, notes.payload) == ("pending", 0, {"meeting_id": ready.id})
    # the same UTC day, still held; the next UTC day, sent
    assert deliver(session, now=NOW + timedelta(hours=11), mail_daily_cap=2)[0] == 0
    assert deliver(session, now=NOW + timedelta(hours=13), mail_daily_cap=2)[0] == 1
    session.refresh(notes)
    assert notes.status == "sent"


def test_link_mail_goes_even_when_the_daily_quota_is_spent(session, ready):
    for i in range(3):
        queue(session, to=f"u{i}@example.test")
    assert deliver(session, mail_daily_cap=1)[0] == 3


def test_a_cap_of_zero_means_no_cap(session, plain):
    rows = [plain_row(session, n) for n in range(3)]
    assert deliver(session, mail_daily_cap=0, mail_recipient_daily_cap=0)[0] == 3
    assert {r.status for r in rows} == {"sent"}


def test_notes_mail_is_held_a_day_not_an_hour(session, plain):
    held = plain_row(session, 1, created=NOW - timedelta(hours=5))
    old = plain_row(session, 2, created=NOW - timedelta(hours=25))
    link = queue(session, created=NOW - timedelta(hours=2))
    mail.sweep_outbox(session, NOW)
    session.expire_all()
    assert held.status == "pending"
    assert (old.status, old.last_error) == ("failed", "expired")
    assert (link.status, link.last_error) == ("failed", "expired")


def test_skipped_rows_are_deleted_after_30_days(session, ready):
    row = notes_row(session, ready, created=NOW - timedelta(days=31))
    row.status, row.payload = "skipped", {}
    session.commit()
    mail.sweep_outbox(session, NOW)
    assert session.scalars(select(MailOutbox)).all() == []


# ---- queueing the mail when the notes are written ----


def test_queue_notes_ready_queues_ids_once(session, ready):
    row = mail.queue_notes_ready(session, ready)
    assert (row.to_email, row.template, row.payload) == (
        "owner@example.test",
        "notes_ready",
        {"meeting_id": ready.id},
    )
    assert mail.queue_notes_ready(session, ready) is None  # not twice while one is queued


def test_queue_notes_ready_skips_an_owner_who_turned_it_off_or_is_not_active(session, ready):
    session.add(UserSettings(user_id=ready.owner_user_id, notify_email=False))
    session.commit()
    assert mail.queue_notes_ready(session, ready) is None
    session.get(UserSettings, ready.owner_user_id).notify_email = True
    session.get(mail_templates.User, ready.owner_user_id).status = "deleted"
    session.commit()
    assert mail.queue_notes_ready(session, ready) is None
    assert session.scalars(select(MailOutbox)).all() == []


def test_the_worker_sends_notes_mail_with_the_real_settings_path(session, ready, monkeypatch):
    from app import worker

    monkeypatch.setattr(worker, "get_settings", lambda: settings())
    row = notes_row(session, ready)
    assert worker.send_mail(session, FakeMailer(), NOW) == 1
    session.refresh(row)
    assert row.status == "sent"


# ---- the operator script ----


class _Same:
    def __init__(self, session):
        self.session = session

    def __enter__(self):
        return self.session

    def __exit__(self, *exc):
        return False


def test_the_suppress_script_adds_checks_and_removes(session, monkeypatch, capsys):
    from scripts import suppress_email

    monkeypatch.setattr(suppress_email, "SessionLocal", lambda: _Same(session))
    assert suppress_email.run(["check", "Ada@Example.test"]) == 0
    assert "Not stopped" in capsys.readouterr().out
    assert suppress_email.run(["add", "Ada@Example.test", "complaint"]) == 0
    assert mail.suppression_reason(session, "ada@example.test") == "complaint"
    assert suppress_email.run(["check", "ada@example.test"]) == 0
    assert "Stopped: complaint" in capsys.readouterr().out
    assert suppress_email.run(["remove", "ada@example.test"]) == 0
    assert mail.suppression_reason(session, "ada@example.test") is None
    assert suppress_email.run(["check", "not an address"]) == 1
    with pytest.raises(SystemExit):
        suppress_email.run(["add", "a@example.test", "spam"])


# ---- review findings: no second mail, no starvation, hard reasons win, headers allow-listed ----


def test_a_second_notes_ready_mail_is_never_queued_even_after_the_first_was_sent(session, ready):
    row = mail.queue_notes_ready(session, ready)
    session.commit()
    row.created_at = NOW
    session.commit()
    assert deliver(session)[0] == 1
    session.refresh(row)
    assert row.status == "sent" and row.payload == {"meeting_id": ready.id}
    assert mail.queue_notes_ready(session, ready) is None
    session.commit()
    assert len(session.scalars(select(MailOutbox)).all()) == 1


@pytest.mark.parametrize("how", ["skipped", "failed", "expired"])
def test_no_second_mail_after_the_first_was_skipped_failed_or_expired(session, ready, how):
    row = mail.queue_notes_ready(session, ready)
    row.created_at = NOW - timedelta(hours=26 if how == "expired" else 0)
    session.commit()
    if how == "skipped":
        mail.suppress(session, "owner@example.test", "bounce")
        session.commit()
        deliver(session)
    elif how == "failed":
        mail.deliver_pending(
            session, FakeMailer(mail.MailError("HTTP 422", permanent=True)), settings(), NOW
        )
    else:
        mail.sweep_outbox(session, NOW)
    session.refresh(row)
    assert row.status != "pending" and row.payload == {"meeting_id": ready.id}
    assert mail.queue_notes_ready(session, ready) is None


def test_the_database_allows_one_notes_ready_row_per_meeting(session, ready):
    from sqlalchemy.exc import IntegrityError

    mail.enqueue(session, "owner@example.test", "notes_ready", {"meeting_id": ready.id})
    session.commit()
    with pytest.raises(IntegrityError):
        mail.enqueue(session, "owner@example.test", "notes_ready", {"meeting_id": ready.id})
    session.rollback()


def test_a_racing_duplicate_loses_quietly_and_the_transaction_stays_usable(
    session, ready, monkeypatch
):
    """Two transactions both pass any pre-check; the index makes the second one fail inside its
    savepoint, so the caller (the pipeline, in the notes commit) is not harmed."""
    mail.enqueue(session, "owner@example.test", "notes_ready", {"meeting_id": ready.id})
    session.commit()
    assert mail.queue_notes_ready(session, ready) is None
    mail.enqueue(session, "x@example.test", "verify_email", {"link": LINK})  # still usable
    session.commit()
    assert len(session.scalars(select(MailOutbox)).all()) == 2


def test_other_meetings_and_templates_are_not_blocked_by_the_index(session, ready):
    mail.enqueue(session, "owner@example.test", "notes_ready", {"meeting_id": ready.id})
    mail.enqueue(session, "owner@example.test", "notes_ready", {"meeting_id": ready.id + 1})
    mail.enqueue(session, "a@example.test", "verify_email", {"link": LINK})
    mail.enqueue(session, "a@example.test", "verify_email", {"link": LINK})
    session.commit()


def test_held_notes_mail_cannot_starve_a_verification_mail(session):
    """The quota is spent and 130 notes mails wait for tomorrow; a verify mail queued after them
    still goes in the next pass (and is not failed as expired by the sweep)."""
    for i in range(2):
        done = queue(session, to=f"sent{i}@example.test")
        done.status, done.sent_at, done.payload = "sent", NOW, {}
    for meeting_id in range(1, 131):
        row = mail.enqueue(session, "owner@example.test", "notes_ready", {"meeting_id": meeting_id})
        row.created_at = NOW - timedelta(hours=3)
    fresh = queue(session, to="new@example.test")
    session.commit()
    sent, mailer = deliver(session, mail_daily_cap=2)
    assert sent == 1 and mailer.sent[0][0].to == "new@example.test"
    session.refresh(fresh)
    assert fresh.status == "sent"
    held = session.scalars(select(MailOutbox).where(MailOutbox.template == "notes_ready")).all()
    assert len(held) == 130 and {r.status for r in held} == {"pending"}


def test_link_mail_is_loaded_before_a_backlog_of_notes_mail_below_the_cap(session):
    for meeting_id in range(1, 131):
        row = mail.enqueue(session, "owner@example.test", "notes_ready", {"meeting_id": meeting_id})
        row.created_at = NOW - timedelta(hours=3)
    fresh = queue(session, to="new@example.test")
    session.commit()
    sent, mailer = deliver(session, mail_daily_cap=0)
    assert mailer.sent[0][0].to == "new@example.test"  # it went first, ahead of 130 older rows
    session.refresh(fresh)
    assert fresh.status == "sent"


def test_a_bounce_or_complaint_replaces_an_unsubscribe_but_never_the_reverse(session):
    mail.suppress(session, "a@example.test", "unsubscribed")
    mail.suppress(session, "a@example.test", "bounce")
    assert mail.suppression_reason(session, "a@example.test") == "bounce"
    mail.suppress(session, "a@example.test", "unsubscribed")
    mail.suppress(session, "a@example.test", "complaint")
    assert mail.suppression_reason(session, "a@example.test") == "bounce"  # the first hard one
    mail.suppress(session, "b@example.test", "complaint")
    mail.suppress(session, "b@example.test", "unsubscribed")
    assert mail.suppression_reason(session, "b@example.test") == "complaint"
    session.commit()


def test_an_unsubscribed_address_that_then_bounces_stops_getting_sign_in_mail(session):
    mail.suppress(session, "owner@example.test", "unsubscribed")
    session.commit()
    first = queue(session, to="owner@example.test")
    assert deliver(session)[0] == 1  # sign-in mail still goes after an unsubscribe
    mail.suppress(session, "owner@example.test", "bounce")
    second = queue(session, to="owner@example.test")
    assert deliver(session)[0] == 0
    skipped(session, second, "suppressed")
    session.refresh(first)
    assert first.status == "sent"


@pytest.mark.parametrize(
    "name", ["From", "from", "To", "Cc", "Bcc", "Reply-To", "Subject", "Sender", "Content-Type"]
)
def test_headers_cannot_reach_the_address_fields(name):
    with pytest.raises(ValueError):
        Message("a@b.test", "s", "t", "h", headers=((name, "evil@x.test"),))


def test_only_the_opt_out_headers_are_allowed_in_any_case():
    message = Message(
        "a@b.test",
        "s",
        "t",
        "h",
        headers=(
            ("list-unsubscribe", "<https://x.test/u>"),
            ("List-Unsubscribe-Post", "List-Unsubscribe=One-Click"),
        ),
    )
    assert len(message.headers) == 2


def test_could_not_join_mail_draws_send_it_again_as_a_link_to_the_meetings_page(monkeypatch):
    payload = {"meeting": "Google Meet btp-tkio-gtb", "at": "11:58 UTC"}
    settings = make_settings()
    button = mail_templates.render_link(
        mail_templates.NOT_JOINED, payload, "a@example.test", settings
    )
    assert "Google Meet btp-tkio-gtb" in button.html and "11:58 UTC" in button.text
    assert "Send it again" in button.html and "Send it again" in button.text
    assert f'href="{settings.public_base_url}/meetings"' in button.html
    monkeypatch.setattr(mail_templates, "SEND_AGAIN_PATH", None)  # the switch still works
    plain = mail_templates.render_link(
        mail_templates.NOT_JOINED, payload, "a@example.test", settings
    )
    assert "Send it again" not in plain.html and "Send it again" not in plain.text


def test_could_not_join_mail_escapes_what_it_is_given():
    payload = {"meeting": "Zoom <b>1</b>", "at": "11:58 UTC"}
    message = mail_templates.render_link(
        mail_templates.NOT_JOINED, payload, "a@example.test", make_settings()
    )
    assert "<b>1" not in message.html and "&lt;b&gt;1" in message.html


def test_could_not_join_payload_uses_the_stored_zone_utc_when_unset_or_unknown(session):
    from tests.test_web import make_user

    moment = datetime(2026, 11, 4, 11, 58, tzinfo=UTC)
    user = make_user(session, "z@example.test")

    def at(zone):
        row = session.get(UserSettings, user.id) or UserSettings(user_id=user.id)
        row.time_zone = zone
        session.add(row)
        session.commit()
        return mail_templates.not_joined_payload(
            session, user.id, "google_meet", "btp-tkio-gtb", moment
        )

    assert at(None) == {"meeting": "Google Meet btp-tkio-gtb", "at": "11:58 UTC"}
    assert at("Africa/Lagos")["at"] == "12:58 GMT+1"
    assert at("Not/AZone")["at"] == "11:58 UTC"  # a stored name the server does not know


def test_a_teams_meeting_is_named_microsoft_teams_not_google_meet(session):
    from tests.test_web import make_user

    user = make_user(session, "t@example.test")
    payload = mail_templates.not_joined_payload(
        session, user.id, "teams", "1234", datetime(2026, 11, 4, 11, 58, tzinfo=UTC)
    )
    assert payload["meeting"] == "Microsoft Teams"  # the derived code is not a name


def test_a_could_not_join_row_with_no_fields_fails_safely_and_is_not_sent(session):
    row = mail.enqueue(session, "a@example.test", mail_templates.NOT_JOINED, {})
    session.commit()
    mailer = FakeMailer()
    mail.deliver_pending(session, mailer, make_settings(), datetime.now(UTC))
    session.expire_all()
    assert mailer.sent == []
    assert session.get(MailOutbox, row.id).status == "failed"


def test_could_not_join_is_loaded_and_held_like_the_alert_it_replaces():
    assert mail.ALERT_LIKE == (mail.ALERT_TEMPLATE, mail_templates.NOT_JOINED)
    assert mail_templates.NOT_JOINED in mail_templates.OWN_PAYLOAD_TEMPLATES
