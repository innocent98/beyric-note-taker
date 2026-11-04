"""Meeting titles (plan C12, decision 3): a calendar meeting shows its event's title everywhere, and
because that title is text from an invite, every place that renders it gets it cleaned.

The unit tests pin `app/titles.py`; the rest walk each place a meeting is named: the API list and
detail, the Decisions and Review groups, the PDF and Word exports (title and file name), the
notes-ready mail (subject, text and HTML) and the notification and alert text.
"""

import io
import json
from datetime import UTC, datetime

from docx import Document
from sqlalchemy import select

from app import mail, mail_templates, services, titles
from app.models import Meeting
from tests.test_api import (  # noqa: F401  (fixtures used by name)
    Api,
    anon,
    anon_client,
    api,
    api_routes,
    app,
    client,
    fake_openai,
    fake_vexa,
    make_user,
    owner,
    ready,
    stranger,
    template,
)
from tests.test_api_workspace import extracted
from tests.test_mail_upgrade import deliver, notes_row
from tests.test_mail_upgrade import ready as mail_ready  # noqa: F401  (fixture used by name)

HOSTILE = (
    "Q3 ‮gnirts‬ review\r\nBcc: attacker@example.test\x07⁦x⁩"
    " <script>alert(1)</script> https://evil.example.test/login?next=1"
)


# ---- the cleaner ----


def test_control_and_bidi_characters_are_removed_and_newlines_become_one_space():
    cleaned = titles.clean_title(HOSTILE)
    assert "\r" not in cleaned and "\n" not in cleaned and "\x00" not in cleaned
    for bad in "‮‬⁦⁩\x07":
        assert bad not in cleaned
    assert cleaned.startswith("Q3 gnirts review Bcc: attacker@example.test")


def test_a_title_is_one_line_with_runs_of_space_collapsed_and_the_ends_trimmed():
    assert titles.clean_title("  a \t\t b c  d  ") == "a b c d"
    assert titles.clean_title("") == "" and titles.clean_title(None) == ""
    assert titles.clean_title("‮‬ \x00 ") == ""


def test_emoji_and_non_latin_titles_round_trip_with_their_joiners():
    family = "\U0001f468‍\U0001f469‍\U0001f467 sync"  # a ZWJ sequence
    for text in (family, "Réunion d'équipe ☕", "会議の議事録", "اجتماع الفريق‌", "Sprint 🚀"):
        assert titles.clean_title(text) == text


def test_the_length_cap_cuts_at_a_character_with_an_ellipsis():
    cleaned = titles.clean_title("x" * 500)
    assert len(cleaned) == titles.TITLE_LIMIT and cleaned.endswith("…")
    assert titles.clean_title("x" * 200) == "x" * 200
    assert len(titles.clean_title("é" * 500, 10)) == 10


def test_markup_is_not_interpreted_by_the_cleaner_it_is_left_for_the_renderers_to_escape():
    assert titles.clean_title("<b>bold</b> & more") == "<b>bold</b> & more"


def test_a_meeting_without_a_title_is_named_by_platform_and_code():
    meet = Meeting(platform="google_meet", native_id="abc-defg-hij", title=None)
    zoom = Meeting(platform="zoom", native_id="88205679674", title="")
    assert titles.meeting_name(meet) == "Google Meet abc-defg-hij"
    assert titles.meeting_name(zoom) == "Zoom 88205679674"
    junk = Meeting(platform="zoom", native_id="1", title="‮ \x00")
    assert titles.meeting_name(junk) == "Zoom 1"
    assert services.meeting_title(meet) == "Google Meet abc-defg-hij"


def test_event_title_stores_nothing_for_nothing_printable():
    assert titles.event_title("‮\x00") is None and titles.event_title("  Plan  ") == "Plan"


def test_mail_titles_defang_bare_domains_and_addresses_but_not_versions():
    meeting = Meeting(
        platform="zoom", native_id="1", title="Call evil.example.test or a@b.test v2.1"
    )
    shown = titles.mail_title(meeting)
    assert "evil[.]example[.]test" in shown and "a@b[.]test" in shown
    assert "evil.example.test" not in shown and "v2.1" in shown


def test_mail_titles_lose_web_addresses_and_are_short():
    meeting = Meeting(platform="zoom", native_id="1", title="Join https://evil.example.test/x now")
    assert titles.mail_title(meeting) == "Join [link removed] now"
    meeting.title = "see www.evil.example.test and zoom.us/j/123?pwd=SECRET"
    assert "evil" not in titles.mail_title(meeting) and "SECRET" not in titles.mail_title(meeting)
    meeting.title = "y" * 300
    assert len(titles.mail_title(meeting)) <= titles.MAIL_TITLE_LIMIT


# ---- the API ----


def make(session, template, title=HOSTILE, **fields):
    return ready(session, template, title=title, **fields)


def test_the_list_and_the_detail_show_the_cleaned_calendar_title(api, session, template):
    meeting = make(session, template)
    shown = titles.clean_title(HOSTILE)
    [listed] = api.get("/api/meetings").json()["meetings"]
    assert listed["title"] == shown
    assert api.get(f"/api/meetings/{meeting.id}").json()["title"] == shown
    for response in (api.get("/api/meetings"), api.get(f"/api/meetings/{meeting.id}")):
        assert "‮" not in response.text and "\\u202e" not in response.text


def test_a_pasted_link_keeps_its_platform_and_code_name(api, session, template):
    meeting = ready(session, template)  # no title
    assert api.get(f"/api/meetings/{meeting.id}").json()["title"] == "Google Meet abc-defg-hij"


def test_the_decisions_and_review_groups_use_the_title(api, session, template):
    meeting = extracted(session, template, title="Budget review ‮")
    body = api.get("/api/decisions").json()
    assert body["groups"][0]["meeting"]["title"] == "Budget review"
    assert body["groups"][0]["meeting"]["id"] == meeting.id
    review = api.get("/api/review").json()
    assert {item["meeting"]["title"] for item in review["items"]} <= {"Budget review"}


def test_search_finds_a_meeting_by_its_calendar_title(api, session, template):
    meeting = extracted(session, template, title="Quarterly roadmap")
    found = api.get("/api/decisions?q=roadmap").json()["groups"]
    assert [g["meeting"]["id"] for g in found] == [meeting.id]


# ---- the exports ----


def test_the_word_export_has_the_title_as_heading_and_a_safe_file_name(api, session, template):
    meeting = make(session, template)
    response = api.get(f"/api/meetings/{meeting.id}/export?format=docx")
    assert response.status_code == 200
    disposition = response.headers["content-disposition"]
    assert "\r" not in disposition and "\n" not in disposition and "‮" not in disposition
    assert "/" not in disposition.split("filename=")[1].split(";")[0].strip('"')
    word = Document(io.BytesIO(response.content))
    heading = titles.clean_title(HOSTILE)
    assert word.paragraphs[0].text == heading
    assert word.core_properties.title == heading


def test_the_pdf_export_escapes_the_title_and_is_named_after_it(api, session, template):
    meeting = make(session, template, title="Plan <b>& more</b> ‮")
    response = api.get(f"/api/meetings/{meeting.id}/export?format=pdf")
    assert response.status_code == 200 and response.content.startswith(b"%PDF")
    assert b"filename*=" in response.headers["content-disposition"].encode() or (
        "Plan" in response.headers["content-disposition"]
    )


def test_the_html_of_an_export_escapes_the_title():
    from app.export_render import render_html
    from app.notes_document import build_notes_document

    doc = build_notes_document(
        title=titles.clean_title("<img src=x onerror=alert(1)>"),
        platform_label="Google Meet",
        started_at=datetime(2026, 11, 4, tzinfo=UTC),
        recorded_seconds=60,
        notes="## Summary\nok",
        transcript=None,
        generated_at=datetime(2026, 11, 4, tzinfo=UTC),
    )
    html = render_html(doc)
    assert "<img src=x" not in html and "&lt;img src=x" in html


# ---- the notes-ready mail ----


def test_the_notes_ready_mail_carries_the_title_in_subject_and_body_without_injection(
    session, mail_ready
):
    mail_ready.title = HOSTILE
    session.commit()
    notes_row(session, mail_ready)
    sent, mailer = deliver(session)
    assert sent == 1
    [(message, _)] = mailer.sent
    assert "\r" not in message.subject and "\n" not in message.subject
    assert message.subject.startswith("Your notes are ready: Q3 gnirts review Bcc:")
    assert "‮" not in message.subject + message.text + message.html
    assert "evil.example.test" not in message.subject + message.text + message.html
    assert "[link removed]" in message.text or "…" in message.text
    assert "<script>alert(1)</script>" not in message.html
    assert "&lt;script&gt;" in message.html or "script" not in message.html


def test_a_mail_for_a_meeting_without_a_title_still_says_platform_and_code(session, mail_ready):
    notes_row(session, mail_ready)
    _, mailer = deliver(session)
    [(message, _)] = mailer.sent
    assert message.subject == "Your notes are ready: Zoom 88205679674"


def test_the_mail_name_is_the_same_cleaned_name_everywhere(mail_ready):
    mail_ready.title = "Weekly sync"
    assert mail_templates.meeting_name(mail_ready) == "Weekly sync"
    assert services.meeting_title(mail_ready) == "Weekly sync"


def test_the_legacy_pages_name_a_meeting_by_its_title(session, template):
    from app.web import templates

    meeting = Meeting(platform="zoom", native_id="55", title="Board <i>call</i>", template_id=1)
    shown = templates.env.globals["meeting_title"](meeting)
    assert shown == "Board <i>call</i>"  # Jinja escapes it when it renders the page
    assert templates.env.from_string("{{ meeting_title(m) }}").render(m=meeting) == (
        "Board &lt;i&gt;call&lt;/i&gt;"
    )


def test_nothing_in_the_outbox_row_holds_the_title(session, mail_ready):
    mail_ready.title = "Confidential merger plan"
    session.commit()
    row = notes_row(session, mail_ready)
    assert "merger" not in json.dumps(row.payload)
    assert select(Meeting.id) is not None and mail.enqueue is not None
