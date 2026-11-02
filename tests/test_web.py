import re
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import get_session
from app.main import create_app
from app.models import Meeting, Template, VocabularyTerm
from app.notes import HEADINGS
from app.vexa import VexaError
from app.web import get_openai_client, get_vexa

PASSWORD = "correct horse battery"
MEET = "https://meet.google.com/abc-defg-hij"
ZOOM = "https://us02web.zoom.us/j/12345678901?pwd=SECRETPWD"
NOTES = "\n\n".join(f"## {h}\nNone noted" for h in HEADINGS)
TRANSCRIPT = "Ada: Hello everyone thanks for joining. I can ship it on Friday."


def make_settings(**overrides) -> Settings:
    values = {
        "owner_password": PASSWORD,
        "session_secret": "x" * 40,
        "cookie_secure": False,
        "app_env": "test",
    }
    return Settings(_env_file=None, **{**values, **overrides})


class FakeVexa:
    def __init__(self, fail=False):
        self.fail = fail
        self.sent: list[str] = []

    def send_bot(self, url):
        if self.fail:
            raise VexaError("send_bot failed: secret detail")
        self.sent.append(url)
        return 4242


class FakeOpenAI:
    """Counts every call. Any audio transcription call is a test failure."""

    def __init__(self, notes=NOTES):
        self.notes = notes
        self.chat_calls: list[dict] = []
        self.transcription_calls = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._chat))
        self.audio = SimpleNamespace(
            transcriptions=SimpleNamespace(create=self._transcribe),
        )

    def _chat(self, **kwargs):
        self.chat_calls.append(kwargs)
        message = SimpleNamespace(content=self.notes)
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")])

    def _transcribe(self, **kwargs):
        self.transcription_calls += 1
        raise AssertionError("regenerate must not transcribe")


@pytest.fixture
def fake_vexa():
    return FakeVexa()


@pytest.fixture
def fake_openai():
    return FakeOpenAI()


@pytest.fixture
def template(session):
    template = Template(name="General", instructions="Write general notes.")
    session.add(template)
    session.commit()
    return template


@pytest.fixture
def app(engine, fake_vexa, fake_openai):
    app = create_app(make_settings())

    def session_override():
        with Session(engine, expire_on_commit=False) as s:
            yield s

    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_vexa] = lambda: fake_vexa
    app.dependency_overrides[get_openai_client] = lambda: fake_openai
    return app


@pytest.fixture
def anon_client(app):
    with TestClient(app, follow_redirects=False) as client:
        yield client


def token_from(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "no csrf token on page"
    return match.group(1)


def post(client, url, data=None, page="/"):
    token = token_from(client.get(page).text)
    return client.post(url, data={**(data or {}), "csrf_token": token})


@pytest.fixture
def client(anon_client):
    token = token_from(anon_client.get("/login").text)
    response = anon_client.post("/login", data={"password": PASSWORD, "csrf_token": token})
    assert response.status_code == 303
    return anon_client


def add_meeting(session, template, **fields) -> Meeting:
    values = {
        "url": MEET,
        "platform": "google_meet",
        "native_id": "abc-defg-hij",
        "template_id": template.id,
    }
    meeting = Meeting(**{**values, **fields})
    session.add(meeting)
    session.commit()
    return meeting


def ready_meeting(session, template, **fields) -> Meeting:
    return add_meeting(
        session,
        template,
        status="ready",
        step="done",
        transcript=TRANSCRIPT,
        speaker_transcript=TRANSCRIPT,
        notes=NOTES,
        **fields,
    )


# ---- required by the brief ----


def test_pages_require_login(anon_client):
    for path in ("/", "/meetings/1", "/templates", "/vocabulary"):
        response = anon_client.get(path)
        assert response.status_code == 303, path
        assert response.headers["location"] == "/login"
    for path in ("/meetings", "/meetings/1/regenerate", "/meetings/1/retry", "/templates"):
        response = anon_client.post(path, data={})
        assert response.status_code in (303, 403), path
    assert anon_client.get("/login").status_code == 200


def test_paste_link_sends_bot(client, fake_vexa, template, session):
    response = post(client, "/meetings", {"url": MEET, "template_id": template.id})
    assert response.status_code == 303
    meeting = session.scalars(select(Meeting)).one()
    assert response.headers["location"] == f"/meetings/{meeting.id}"
    assert fake_vexa.sent == [MEET]
    assert (meeting.platform, meeting.native_id) == ("google_meet", "abc-defg-hij")
    assert meeting.vexa_meeting_id == "4242"
    assert (meeting.status, meeting.step) == ("bot_sent", "waiting")
    assert meeting.template_id == template.id
    assert meeting.started_polling_at is not None


def test_second_active_meeting_is_refused(client, fake_vexa, template, session):
    add_meeting(session, template, status="processing", step="notes")
    response = post(client, "/meetings", {"url": MEET, "template_id": template.id})
    assert response.status_code == 409
    assert "already" in response.text
    assert fake_vexa.sent == []
    assert len(session.scalars(select(Meeting)).all()) == 1


def test_invalid_link_shows_plain_error(client, fake_vexa, template, session):
    response = post(
        client, "/meetings", {"url": "https://example.com/x", "template_id": template.id}
    )
    assert response.status_code == 400
    assert "Google Meet or Zoom" in response.text
    assert fake_vexa.sent == []
    assert session.scalars(select(Meeting)).all() == []


def test_status_text_says_bot_sent_not_recording(client, template, session):
    meeting = add_meeting(session, template)
    page = client.get(f"/meetings/{meeting.id}").text
    assert "Bot sent" in page
    assert "recording" not in page.lower()
    listing = client.get("/").text
    assert "Bot sent" in listing
    assert "recording" not in listing.lower()


def test_regenerate_makes_no_transcription_call(client, fake_openai, template, session):
    other = Template(name="Short", instructions="Three bullet points only.")
    session.add(other)
    session.commit()
    meeting = ready_meeting(session, template)
    fake_openai.notes = NOTES.replace("None noted", "Fresh notes", 1)
    response = post(
        client,
        f"/meetings/{meeting.id}/regenerate",
        {"template_id": other.id},
        page=f"/meetings/{meeting.id}",
    )
    assert response.status_code == 303
    assert fake_openai.transcription_calls == 0
    assert len(fake_openai.chat_calls) == 1
    assert "Three bullet points only." in fake_openai.chat_calls[0]["messages"][1]["content"]
    session.refresh(meeting)
    assert "Fresh notes" in meeting.notes
    assert meeting.template_id == other.id
    assert meeting.status == "ready"


def test_failed_meeting_shows_reason(client, template, session):
    reason = "The bot could not record this meeting. It may not have been admitted."
    meeting = add_meeting(session, template, status="failed", step="waiting", failure_reason=reason)
    assert reason in client.get(f"/meetings/{meeting.id}").text


# ---- more behaviour ----


def test_vexa_failure_saves_nothing_and_leaks_nothing(app, client, template, session):
    app.dependency_overrides[get_vexa] = lambda: FakeVexa(fail=True)
    response = post(client, "/meetings", {"url": MEET, "template_id": template.id})
    assert response.status_code == 502
    assert "secret detail" not in response.text
    assert "could not" in response.text.lower()
    assert session.scalars(select(Meeting)).all() == []


def test_zoom_password_never_appears_in_pages_or_urls(client, template, session):
    response = post(client, "/meetings", {"url": ZOOM, "template_id": template.id})
    assert response.status_code == 303
    assert "?" not in response.headers["location"]
    meeting = session.scalars(select(Meeting)).one()
    assert "SECRETPWD" not in client.get(f"/meetings/{meeting.id}").text
    assert "SECRETPWD" not in client.get("/").text


def test_processing_shows_current_step_in_plain_words(client, template, session):
    meeting = add_meeting(session, template, status="processing", step="speakers")
    page = client.get(f"/meetings/{meeting.id}").text
    assert "Working out who said what" in page
    assert "recording" not in page.lower()


def test_page_refreshes_until_done(client, template, session):
    waiting = add_meeting(session, template)
    done = ready_meeting(session, template)
    failed = add_meeting(session, template, status="failed", step="waiting", failure_reason="x")
    assert 'http-equiv="refresh" content="30"' in client.get(f"/meetings/{waiting.id}").text
    assert "refresh" not in client.get(f"/meetings/{done.id}").text
    assert "refresh" not in client.get(f"/meetings/{failed.id}").text


def test_ready_meeting_has_copy_buttons_and_rendered_notes(client, template, session):
    meeting = ready_meeting(session, template)
    page = client.get(f"/meetings/{meeting.id}").text
    assert page.count("data-copy=") == 2
    assert "<h2>Summary</h2>" in page
    assert "Hello everyone thanks for joining" in page


def test_untrusted_text_is_escaped(client, template, session):
    evil = "<script>alert(1)</script><img src=x onerror=alert(2)>"
    notes = f"## Summary\n{evil}\n\n[click](javascript:alert(3)) [ok](https://example.com)"
    meeting = ready_meeting(session, template)
    meeting.notes = notes
    meeting.speaker_transcript = f"Mallory: {evil}"
    session.commit()
    page = client.get(f"/meetings/{meeting.id}").text
    assert "<script>alert" not in page
    assert "<img src=x" not in page
    assert 'href="javascript' not in page  # shown as plain text, never a link
    assert 'href="https://example.com"' in page
    assert "noopener" in page


def test_failed_meeting_can_be_retried_when_transcript_remains(client, template, session):
    meeting = add_meeting(
        session,
        template,
        status="failed",
        step="notes",
        failure_reason="The notes could not be written. The transcript was saved.",
        transcript=TRANSCRIPT,
        audio_deleted_at=datetime.now(UTC),
    )
    page = client.get(f"/meetings/{meeting.id}").text
    assert f"/meetings/{meeting.id}/retry" in page
    response = post(client, f"/meetings/{meeting.id}/retry", page=f"/meetings/{meeting.id}")
    assert response.status_code == 303
    session.refresh(meeting)
    assert (meeting.status, meeting.step, meeting.failure_reason) == ("processing", "notes", None)


def test_retry_refused_when_nothing_is_left_to_retry(client, template, session):
    meeting = add_meeting(
        session,
        template,
        status="failed",
        step="transcribing",
        failure_reason="The recording could not be transcribed. Please try again later.",
        audio_deleted_at=datetime.now(UTC),
    )
    page = client.get(f"/meetings/{meeting.id}").text
    assert f"/meetings/{meeting.id}/retry" not in page
    assert "can't be retried" in page
    response = post(client, f"/meetings/{meeting.id}/retry", page=f"/meetings/{meeting.id}")
    assert response.status_code == 409
    session.refresh(meeting)
    assert meeting.status == "failed"


def test_retry_refused_for_bot_that_never_recorded(client, template, session):
    meeting = add_meeting(session, template, status="failed", step="waiting", failure_reason="x")
    response = post(client, f"/meetings/{meeting.id}/retry", page=f"/meetings/{meeting.id}")
    assert response.status_code == 409
    session.refresh(meeting)
    assert meeting.status == "failed"


def test_regenerate_only_works_on_ready_meetings(client, fake_openai, template, session):
    meeting = add_meeting(session, template, transcript=TRANSCRIPT)
    response = post(
        client,
        f"/meetings/{meeting.id}/regenerate",
        {"template_id": template.id},
        page=f"/meetings/{meeting.id}",
    )
    assert response.status_code == 409
    assert fake_openai.chat_calls == []


def test_regenerate_failure_keeps_old_notes(client, fake_openai, template, session):
    meeting = ready_meeting(session, template)
    fake_openai.notes = ""  # an empty reply makes generate_notes raise NotesFailed
    response = post(
        client,
        f"/meetings/{meeting.id}/regenerate",
        {"template_id": template.id},
        page=f"/meetings/{meeting.id}",
    )
    assert response.status_code == 502
    assert "notes could not be written" in response.text.lower()
    session.refresh(meeting)
    assert meeting.notes == NOTES


def test_unknown_meeting_is_404(client):
    assert client.get("/meetings/999").status_code == 404


# ---- templates and vocabulary ----


def test_templates_list_add_and_edit(client, template, session):
    assert "General" in client.get("/templates").text
    response = post(
        client, "/templates", {"name": "Standup", "instructions": "Blockers first."}, "/templates"
    )
    assert response.status_code == 303
    added = session.scalars(select(Template).where(Template.name == "Standup")).one()
    response = post(
        client,
        f"/templates/{added.id}",
        {"name": "Daily", "instructions": "Keep it short."},
        "/templates",
    )
    assert response.status_code == 303
    session.refresh(added)
    assert (added.name, added.instructions) == ("Daily", "Keep it short.")


def test_template_rejects_blank_and_duplicate(client, template, session):
    blank = post(client, "/templates", {"name": " ", "instructions": "x"}, "/templates")
    assert blank.status_code == 400
    dup = post(client, "/templates", {"name": "General", "instructions": "x"}, "/templates")
    assert dup.status_code == 400
    assert "already" in dup.text
    assert len(session.scalars(select(Template)).all()) == 1


def test_vocabulary_add_list_delete(client, session):
    response = post(client, "/vocabulary", {"term": "  Beyric  "}, "/vocabulary")
    assert response.status_code == 303
    term = session.scalars(select(VocabularyTerm)).one()
    assert term.term == "Beyric"
    assert "Beyric" in client.get("/vocabulary").text
    response = post(client, f"/vocabulary/{term.id}/delete", page="/vocabulary")
    assert response.status_code == 303
    assert session.scalars(select(VocabularyTerm)).all() == []


@pytest.mark.parametrize("bad", ["", "   ", "a<b", "a>b", "line\nbreak", "x" * 101])
def test_vocabulary_rejects_bad_terms_with_clear_error(client, session, bad):
    response = post(client, "/vocabulary", {"term": bad}, "/vocabulary")
    assert response.status_code == 400
    assert "term" in response.text.lower()
    assert session.scalars(select(VocabularyTerm)).all() == []


def test_vocabulary_rejects_duplicate(client, session):
    post(client, "/vocabulary", {"term": "Beyric"}, "/vocabulary")
    response = post(client, "/vocabulary", {"term": "Beyric"}, "/vocabulary")
    assert response.status_code == 400
    assert "already" in response.text
    assert len(session.scalars(select(VocabularyTerm)).all()) == 1


# ---- auth ----


def test_health_needs_no_login(anon_client):
    response = anon_client.get("/health")
    assert response.status_code == 200


def test_wrong_password_is_refused(anon_client):
    token = token_from(anon_client.get("/login").text)
    response = anon_client.post("/login", data={"password": "nope", "csrf_token": token})
    assert response.status_code == 401
    assert anon_client.get("/").status_code == 303


def test_login_cookie_flags(app):
    secure_app = create_app(make_settings(cookie_secure=True))
    secure_app.dependency_overrides = app.dependency_overrides
    for application, expect_secure in ((app, False), (secure_app, True)):
        with TestClient(application, base_url="https://testserver", follow_redirects=False) as c:
            token = token_from(c.get("/login").text)
            response = c.post("/login", data={"password": PASSWORD, "csrf_token": token})
            cookie = response.headers["set-cookie"].lower()
            assert "httponly" in cookie
            assert "samesite=lax" in cookie
            assert ("secure" in cookie.replace("samesite", "")) is expect_secure


def test_login_is_rate_limited(anon_client):
    token = token_from(anon_client.get("/login").text)
    for _ in range(5):
        response = anon_client.post("/login", data={"password": "nope", "csrf_token": token})
        assert response.status_code == 401
    # Even the right password is refused once the limit is hit.
    response = anon_client.post("/login", data={"password": PASSWORD, "csrf_token": token})
    assert response.status_code == 429


def test_post_without_valid_csrf_token_is_refused(client, template, session):
    no_token = client.post("/meetings", data={"url": MEET, "template_id": template.id})
    assert no_token.status_code == 403
    wrong = client.post(
        "/meetings", data={"url": MEET, "template_id": template.id, "csrf_token": "forged"}
    )
    assert wrong.status_code == 403
    assert session.scalars(select(Meeting)).all() == []


def test_forged_session_cookie_is_rejected(app):
    other = create_app(make_settings(session_secret="y" * 40))
    with TestClient(other, follow_redirects=False) as stranger:
        token = token_from(stranger.get("/login").text)
        stranger.post("/login", data={"password": PASSWORD, "csrf_token": token})
        forged = stranger.cookies.get("session")
    assert forged
    with TestClient(app, follow_redirects=False) as c:
        c.cookies.set("session", forged)
        assert c.get("/").status_code == 303


def test_logout_ends_the_session(client):
    response = post(client, "/logout")
    assert response.status_code == 303
    assert client.get("/").status_code == 303


@pytest.mark.parametrize("field", ["owner_password", "session_secret"])
def test_production_refuses_to_start_without_secrets(field):
    with pytest.raises(RuntimeError, match=field):
        create_app(make_settings(app_env="production", **{field: ""}))


def test_security_headers(anon_client):
    response = anon_client.get("/login")
    assert "script-src 'self'" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"


def test_pages_have_viewport_and_no_external_resources(client, template, session):
    meeting = ready_meeting(session, template)
    for path in ("/", f"/meetings/{meeting.id}", "/templates", "/vocabulary"):
        page = client.get(path).text
        assert 'name="viewport"' in page
        assert not re.search(r'(src|href)="https?://(?!example\.com)', page), path


def test_form_shows_recording_notice(client, template):
    assert "Tell participants that this meeting will be recorded." in client.get("/").text
