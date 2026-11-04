"""scripts/extraction_review.py: the read-only page for judging the full extraction on a real
meeting. The model call is faked the way the other extraction tests fake it."""

import io
import re
from datetime import UTC, datetime
from html.parser import HTMLParser

import httpx
import openai
import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import Base, Meeting, Template, User
from scripts import extraction_review
from tests.test_extraction_call import FakeChat

TRANSCRIPT = (
    "[Tunde]: I'll take payment checkout before Friday.\n"
    "[Grace]: We need Metrika for the dashboard.\n"
    "[Tunde]: Fine. Launch is the fourteenth of March."
)
NOTES = """\
## Summary
Short.

## Decisions
- Launch moves to 14 March.

## Action items
- Tunde: finish payment checkout (by Friday)
- Unassigned: sort out VAT

## Open questions
- None noted

## Please check
- Metrika may be misheard
"""
REPLY = {
    "action_items": [
        {
            "index": 0,
            "title": "Finish payment checkout",
            "spoken_owner": "Tunde",
            "due_phrase": "before Friday",
            "due_date": "2026-11-06",
            "quote": "I'll take payment checkout before Friday.",
            "confidence": "high",
        },
        {
            "index": 1,
            "title": "Sort out VAT",
            "spoken_owner": "Zed Nobody",
            "due_phrase": None,
            "due_date": None,
            "quote": "Somebody must sort out the VAT forms.",
            "confidence": "low",
        },
    ],
    "review": [{"index": 0, "heard_as": "Metrika"}],
    "milestones": [
        {"name": "Launch", "date": "2026-03-14", "quote": "Launch is the fourteenth of March."}
    ],
}
NOW = datetime(2026, 11, 3, 10, 0, tzinfo=UTC)


@pytest.fixture
def user(session):
    row = User(email="owner@example.test")
    session.add(row)
    session.flush()
    session.add(Template(owner_user_id=row.id, name="General", instructions="x"))
    session.commit()
    return row


def make_meeting(session, user, **fields):
    template = session.scalars(select(Template)).first()
    values = dict(
        owner_user_id=user.id,
        url="https://zoom.us/j/111",
        platform="zoom",
        native_id="111",
        template_id=template.id,
        status="ready",
        step="done",
        notes=NOTES,
        speaker_transcript=TRANSCRIPT,
        created_at=NOW,
    )
    values.update(fields)
    meeting = Meeting(**values)
    session.add(meeting)
    session.commit()
    return meeting


@pytest.fixture(autouse=True)
def wired(engine, monkeypatch):
    monkeypatch.setattr(extraction_review, "SessionLocal", lambda: Session(engine))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-key-0123456789")


def review(argv, client=None):
    out, err = io.StringIO(), io.StringIO()
    code = extraction_review.run(argv, client=client, out=out, err=err)
    return code, out.getvalue(), err.getvalue()


class Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    handle_startendtag = handle_starttag


def tags_of(html):
    parser = Tags()
    parser.feed(html)
    return parser.tags


# ---- search ----


def test_search_finds_a_meeting_by_title_code_or_url_and_prints_what_to_pick_by(session, user):
    make_meeting(session, user, title="MyWay weekly sync", native_id="900", url="https://x/1")
    make_meeting(
        session, user, title=None, native_id="8765432100", url="https://zoom.us/j/myway-77"
    )
    make_meeting(session, user, title="Other", native_id="42", url="https://x/3")
    code, out, _ = review(["--search", "myway"])
    assert code == 0
    lines = [
        line for line in out.splitlines() if "MyWay weekly sync" in line or "8765432100" in line
    ]
    assert len(lines) == 2
    assert "Other" not in out
    first = next(line for line in lines if "MyWay weekly sync" in line)
    assert re.search(r"\b1\b", first)  # id
    assert "2026-11-03" in first  # date
    assert str(len(TRANSCRIPT)) in first  # transcript length
    code, out, _ = review(["--search", "8765432100"])  # by meeting code
    assert "8765432100" in out and "MyWay weekly sync" not in out


def test_search_treats_wildcard_characters_literally(session, user):
    make_meeting(session, user, title="100% done")
    make_meeting(session, user, title="Plain")
    _, out, _ = review(["--search", "%"])
    assert "100% done" in out and "Plain" not in out
    _, out, _ = review(["--search", "_"])
    assert "No meeting matches" in out


def test_search_with_no_match_says_so(session, user):
    make_meeting(session, user)
    code, out, _ = review(["--search", "nothing-like-this"])
    assert code == 0 and "No meeting matches" in out


def test_search_counts_the_transcript_the_extraction_would_read(session, user):
    make_meeting(session, user, title="Only live text", speaker_transcript=None, transcript="abcde")
    _, out, _ = review(["--search", "Only live"])
    assert re.search(r"\b5\b", out)


def test_it_needs_a_meeting_or_a_search():
    code, _, err = review([])
    assert code == 2 and "--meeting" in err


# ---- the page ----


def test_the_page_shows_the_transcript_and_each_extracted_item(session, user):
    meeting = make_meeting(session, user)
    client = FakeChat([REPLY])
    code, html, _ = review(["--meeting", str(meeting.id)], client)
    assert code == 0
    # transcript on the left: speaker and line number per line
    assert "Tunde" in html and "Grace" in html
    assert "payment checkout before Friday" in html
    # action items: the owner as the model named it, the due date, the quote, the confidence
    assert "Finish payment checkout" in html
    assert "2026-11-06" in html and "before Friday" in html
    assert "I&#x27;ll take payment checkout before Friday." in html
    # a decision (read from the notes), a misheard term, a milestone
    assert "Launch moves to 14 March." in html
    assert "Metrika" in html
    assert "2026-03-14" in html
    # three verdict buttons per item, and a Missing box
    assert html.count(">Right<") == html.count(">Wrong<") == html.count(">Partly<")
    items = [t for t in tags_of(html) if "data-item" in t[1]]
    assert len(items) == 2 + 1 + 1 + 1  # two actions, a decision, a review item, a milestone
    assert html.count(">Right<") == len(items)
    assert "Copy my verdicts" in html
    assert "localStorage" in html
    assert "Missing" in html
    # the call was the full one
    assert client.calls[0]["response_format"]["json_schema"]["schema"]["required"] == [
        "action_items",
        "review",
        "milestones",
    ]


def test_an_item_with_a_quote_in_the_transcript_links_to_its_line(session, user):
    meeting = make_meeting(session, user)
    _, html, _ = review(["--meeting", str(meeting.id)], FakeChat([REPLY]))
    tags = tags_of(html)
    lines = {t[1]["id"]: t for t in tags if t[1].get("id", "").startswith("l-")}
    assert len(lines) == 3
    quoted = [t[1] for t in tags if "data-line" in t[1]]
    targets = {q["data-line"] for q in quoted}
    assert f"l-{meeting.id}-1" in targets  # Tunde's first line holds the action's quote
    assert f"l-{meeting.id}-3" in targets  # the milestone's quote


def test_checks_the_app_would_make_are_shown_not_hidden(session, user):
    meeting = make_meeting(session, user)
    _, html, _ = review(["--meeting", str(meeting.id)], FakeChat([REPLY]))
    # "Zed Nobody" is not in the transcript and the second quote was never said
    assert "Zed Nobody" in html
    assert "owner not found in the transcript" in html.lower()
    assert "quote not found in the transcript" in html.lower()
    # the good first item has neither warning beside it
    first = html.split('data-item="a0"')[1].split('data-item="a1"')[0]
    assert "not found in the transcript" not in first


def test_the_header_names_the_model_prompt_version_tokens_and_the_meeting(session, user):
    meeting = make_meeting(session, user, title="MyWay weekly sync")
    _, html, _ = review(["--meeting", str(meeting.id), "--model", "m-test"], FakeChat([REPLY]))
    assert "m-test" in html
    assert "extract-v1" in html
    assert "1,200" in html and "300" in html  # the fake's token counts
    assert "MyWay weekly sync" in html


def test_a_cost_estimate_appears_only_when_prices_are_given(session, user):
    meeting = make_meeting(session, user)
    _, plain, _ = review(["--meeting", str(meeting.id)], FakeChat([REPLY]))
    assert "$" not in plain.split("<main")[0]
    _, priced, _ = review(
        ["--meeting", str(meeting.id), "--price-in", "2", "--price-out", "10"], FakeChat([REPLY])
    )
    assert "$0.0054" in priced  # 1200 * 2/1e6 + 300 * 10/1e6


def test_several_meetings_go_into_one_page(session, user):
    a = make_meeting(session, user, title="First meeting")
    b = make_meeting(session, user, title="Second meeting")
    client = FakeChat([REPLY, REPLY])
    code, html, _ = review(["--meeting", str(a.id), "--meeting", str(b.id)], client)
    assert code == 0 and len(client.calls) == 2
    assert "First meeting" in html and "Second meeting" in html
    assert html.count("Copy my verdicts") == 1  # one footer for the whole page


def test_a_meeting_that_does_not_exist_is_said_plainly(session, user):
    code, html, err = review(["--meeting", "9999"], FakeChat([]))
    assert code == 1
    assert "No meeting with id 9999" in html


def test_a_meeting_with_no_transcript_makes_no_model_call(session, user):
    meeting = make_meeting(session, user, speaker_transcript=None, transcript=None)
    client = FakeChat([])
    code, html, _ = review(["--meeting", str(meeting.id)], client)
    assert client.calls == []
    assert "no transcript" in html.lower()
    assert code == 1


def test_a_meeting_with_a_blank_transcript_counts_as_empty(session, user):
    meeting = make_meeting(session, user, speaker_transcript="  \n ")
    client = FakeChat([])
    review(["--meeting", str(meeting.id)], client)
    assert client.calls == []


def test_a_meeting_with_no_notes_says_the_action_items_come_from_the_notes(session, user):
    meeting = make_meeting(session, user, notes=None)
    client = FakeChat([{"action_items": [], "review": [], "milestones": []}])
    code, html, _ = review(["--meeting", str(meeting.id)], client)
    assert code == 0
    assert "no notes" in html.lower()


def test_the_prompt_is_the_apps_own_not_a_copy(session, user):
    from app import extraction

    meeting = make_meeting(session, user)
    client = FakeChat([REPLY])
    review(["--meeting", str(meeting.id)], client)
    assert client.calls[0]["messages"][0]["content"] == extraction.EXTRACT_SYSTEM_FULL


def test_a_very_long_transcript_is_sent_whole_and_the_page_says_so(session, user):
    line = "[Grace]: " + "word " * 40 + "\n"
    long_text = TRANSCRIPT + "\n" + line * 3000  # about 600,000 characters
    meeting = make_meeting(session, user, speaker_transcript=long_text)
    client = FakeChat([REPLY])
    _, html, _ = review(["--meeting", str(meeting.id)], client)
    sent = client.calls[0]["messages"][1]["content"]
    assert "word word" in sent and len(sent) > 500_000  # the pipeline does not shorten
    assert "sent whole" in html.lower()
    assert "does not shorten" in html.lower()


# ---- failed call ----


def _api_error():
    return openai.APIConnectionError(request=httpx.Request("POST", "https://api.openai.com/v1"))


def test_a_failed_call_prints_a_clear_page_without_a_traceback_or_secrets(session, user):
    meeting = make_meeting(session, user)
    code, html, err = review(["--meeting", str(meeting.id)], FakeChat([_api_error()]))
    assert code == 1
    assert "model call failed" in html.lower()
    assert "APIConnectionError" in html
    assert "Traceback" not in html + err
    assert "sk-secret" not in html + err
    assert "payment checkout" in html  # the transcript is still there to read


def test_an_unexpected_error_is_reduced_to_its_class_name(session, user):
    meeting = make_meeting(session, user)
    boom = RuntimeError("password=sk-secret-key-0123456789 at /srv/app/x.py")
    code, html, err = review(["--meeting", str(meeting.id)], FakeChat([boom]))
    assert code == 1
    assert "RuntimeError" in html
    assert "sk-secret" not in html + err and "/srv/app" not in html + err
    assert "Traceback" not in html + err


def test_an_unfinished_reply_is_a_failed_call_that_keeps_its_tokens(session, user):
    meeting = make_meeting(session, user)
    code, html, _ = review(["--meeting", str(meeting.id)], FakeChat([("{}", "length")]))
    assert code == 1
    assert "did not finish normally" in html
    assert "1,200" in html  # the call cost something


def test_one_failed_meeting_does_not_stop_the_others(session, user):
    a = make_meeting(session, user, title="Fails")
    b = make_meeting(session, user, title="Works")
    code, html, _ = review(
        ["--meeting", str(a.id), "--meeting", str(b.id)], FakeChat([_api_error(), REPLY])
    )
    assert code == 1
    assert "model call failed" in html.lower()
    assert "Finish payment checkout" in html


# ---- everything from the transcript and the model is inert ----

SCRIPT = "<script>alert('PWNED1')</script>"
TEXTAREA = "</textarea><img src=x onerror=alert('PWNED2')>"
STYLE = "</style><svg onload=alert('PWNED3')>"


def hostile_meeting(session, user):
    transcript = f"[Eve{SCRIPT}]: I will do it {TEXTAREA}\n[Mallory]: {STYLE} and 'quote\" & more"
    notes = f"""\
## Decisions
- {SCRIPT} {TEXTAREA}

## Action items
- Eve: do the thing {STYLE}

## Please check
- {TEXTAREA}
"""
    return make_meeting(
        session,
        user,
        title=f"Title {SCRIPT}",
        native_id="hostile",
        url=f"https://x/?q={TEXTAREA}",
        speaker_transcript=transcript,
        notes=notes,
    )


def hostile_reply(transcript):
    return {
        "action_items": [
            {
                "index": 0,
                "title": f"do the thing {SCRIPT}",
                "spoken_owner": f"Eve{SCRIPT}",
                "due_phrase": TEXTAREA,
                "due_date": "2026-11-06",
                "quote": f"I will do it {TEXTAREA}",
                "confidence": "high",
            }
        ],
        "review": [{"index": 0, "heard_as": STYLE}],
        "milestones": [{"name": SCRIPT, "date": "2026-12-01", "quote": TEXTAREA}],
    }


def test_nothing_from_the_transcript_or_the_model_can_run_or_close_an_element(session, user):
    meeting = hostile_meeting(session, user)
    code, html, _ = review(["--meeting", str(meeting.id)], FakeChat([hostile_reply("")]))
    assert code == 0
    for marker in ("PWNED1", "PWNED2", "PWNED3"):
        assert marker in html  # it is shown, as text
    assert "<script>alert" not in html
    assert "<img" not in html and "<svg" not in html
    assert TEXTAREA not in html and STYLE not in html
    tags = tags_of(html)
    assert [t[1].get("src") for t in tags if t[0] == "script"] == [None]  # only our own script
    assert sum(1 for t in tags if t[0] == "script") == 1
    for _, attrs in tags:
        assert not any(name.startswith("on") for name in attrs), attrs
    assert "onerror" not in {a for _, attrs in tags for a in attrs}


def test_the_hostile_text_is_still_what_the_copy_button_reports(session, user):
    meeting = hostile_meeting(session, user)
    _, html, _ = review(["--meeting", str(meeting.id)], FakeChat([hostile_reply("")]))
    items = [t[1] for t in tags_of(html) if "data-item" in t[1]]
    texts = " ".join(i.get("data-text", "") for i in items)
    assert "PWNED1" in texts  # attribute values are decoded by the browser, so the paste is exact


def test_the_page_makes_no_requests_and_blocks_any_that_slip_in(session, user):
    meeting = make_meeting(session, user)
    _, html, _ = review(["--meeting", str(meeting.id)], FakeChat([REPLY]))
    tags = tags_of(html)
    for tag, attrs in tags:
        assert "src" not in attrs, tag
        assert "href" not in attrs or attrs["href"].startswith("#"), attrs
        assert tag not in ("link", "iframe", "img", "object", "embed", "form"), tag
    assert "@import" not in html and "url(" not in html
    assert "fetch(" not in html and "XMLHttpRequest" not in html
    csp = next(a["content"] for t, a in tags if t == "meta" and a.get("http-equiv"))
    assert "default-src 'none'" in csp and "'nonce-" in csp
    nonce = re.search(r"'nonce-([^']+)'", csp).group(1)
    script = next(a for t, a in tags if t == "script")
    assert script["nonce"] == nonce
    fonts = re.findall(r"font-family:([^;]+);", html)
    assert fonts and all("system-ui" in f or "monospace" in f for f in fonts)


def test_each_run_has_its_own_nonce(session, user):
    meeting = make_meeting(session, user)
    _, one, _ = review(["--meeting", str(meeting.id)], FakeChat([REPLY]))
    _, two, _ = review(["--meeting", str(meeting.id)], FakeChat([REPLY]))
    assert re.search(r"nonce-([^']+)", one).group(1) != re.search(r"nonce-([^']+)", two).group(1)


# ---- read only ----


def checksums(engine):
    out = {}
    with engine.connect() as conn:
        for table in Base.metadata.sorted_tables:
            name = table.name
            count = conn.execute(text(f'select count(*) from "{name}"')).scalar()
            digest = conn.execute(
                text(
                    f"select coalesce(md5(string_agg(t::text, '|' order by t::text)), '') "
                    f'from "{name}" t'
                )
            ).scalar()
            out[name] = (count, digest)
    return out


def test_a_review_changes_nothing_in_the_database(session, user, engine):
    meeting = make_meeting(session, user, extraction_status="done", extraction_version="x")
    other = make_meeting(session, user, native_id="222")
    before = checksums(engine)
    assert before["meetings"][0] == 2
    review(["--meeting", str(meeting.id), "--meeting", str(other.id)], FakeChat([REPLY, REPLY]))
    review(["--meeting", str(meeting.id)], FakeChat([_api_error()]))  # a failure writes nothing
    review(["--search", "111"])
    assert checksums(engine) == before
    # no ledger row either: the tool is read-only, so it leaves no `ai_runs` entry
    assert before["ai_runs"][0] == 0


def test_the_session_it_uses_refuses_writes(session, user, engine):
    meeting = make_meeting(session, user)
    with extraction_review.open_readonly() as guarded:
        assert guarded.get(Meeting, meeting.id) is not None
        with pytest.raises(Exception, match="read-only"):
            guarded.execute(text("update meetings set notes = 'changed'"))
    with Session(engine) as fresh:
        assert fresh.get(Meeting, meeting.id).notes == NOTES


def test_a_failed_write_inside_the_tool_would_leave_the_data_untouched(session, user, engine):
    """The guard is on the database side, so code that tried to write could not."""
    meeting = make_meeting(session, user)
    with extraction_review.open_readonly() as guarded:
        row = guarded.get(Meeting, meeting.id)
        row.notes = "changed"
        with pytest.raises(Exception, match="read-only"):
            guarded.commit()
    with Session(engine) as fresh:
        assert fresh.get(Meeting, meeting.id).notes == NOTES
