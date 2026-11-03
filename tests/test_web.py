import re
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.auth import LoginLimiter
from app.config import Settings
from app.db import get_session
from app.main import create_app
from app.models import AppState, Meeting, Template, User, VocabularyTerm
from app.notes import HEADINGS
from app.passwords import hash_password
from app.pipeline import REASON_NO_SPEECH
from app.vexa import VexaError
from app.web import get_openai_client, get_vexa

PASSWORD = " ".join(["correct", "horse", "battery"])
OWNER_EMAIL = "owner@example.test"  # placeholder addresses, never real ones
OTHER_EMAIL = "other@example.test"
MEET = "https://meet.google.com/abc-defg-hij"
OWNER_ONLY = "/templates"  # "/" is the public landing page when logged out
ZOOM = "https://us02web.zoom.us/j/12345678901?pwd=SECRETPWD"
NOTES = "\n\n".join(f"## {h}\nNone noted" for h in HEADINGS)
TRANSCRIPT = "Ada: Hello everyone thanks for joining. I can ship it on Friday."


def make_settings(**overrides) -> Settings:
    values = {
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

    stop_error: Exception | None = None
    stopped: list

    def stop_bot(self, platform, native_id):
        if self.stop_error:
            raise self.stop_error
        self.stopped = getattr(self, "stopped", []) + [(platform, native_id)]


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


_hashes: dict[str, str] = {}


def make_user(session, email=OWNER_EMAIL, password=PASSWORD, *, admin=False, **fields) -> User:
    """A verified, active account. The hash is cached: argon2 is slow on purpose."""
    values = {
        "email": email,
        "password_hash": _hashes.setdefault(password, hash_password(password))
        if password
        else None,
        "email_verified_at": datetime.now(UTC),
        "is_admin": admin,
        **fields,
    }
    user = User(**values)
    session.add(user)
    session.commit()
    return user


def login_data(token, email=OWNER_EMAIL, password=PASSWORD) -> dict:
    return {"email": email, "password": password, "csrf_token": token}


@pytest.fixture
def owner(session):
    """The account whose data most tests use: an ordinary user, not an admin."""
    return make_user(session)


@pytest.fixture
def stranger(session):
    """A second account, to prove one user never sees another's data."""
    return make_user(session, OTHER_EMAIL)


@pytest.fixture
def template(session, owner):
    template = Template(owner_user_id=owner.id, name="General", instructions="Write general notes.")
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
def client(anon_client, owner):
    token = token_from(anon_client.get("/login").text)
    response = anon_client.post("/login", data=login_data(token))
    assert response.status_code == 303
    return anon_client


def add_meeting(session, template, **fields) -> Meeting:
    values = {
        "owner_user_id": template.owner_user_id,
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
    for path in ("/meetings/1", "/templates", "/vocabulary"):
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
    other = Template(
        owner_user_id=template.owner_user_id, name="Short", instructions="Three bullet points only."
    )
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
    response = anon_client.post("/login", data=login_data(token, password="nope"))
    assert response.status_code == 401
    assert anon_client.get(OWNER_ONLY).status_code == 303


def test_login_cookie_flags(app, owner):
    secure_app = create_app(make_settings(cookie_secure=True))
    secure_app.dependency_overrides = app.dependency_overrides
    for application, expect_secure in ((app, False), (secure_app, True)):
        with TestClient(application, base_url="https://testserver", follow_redirects=False) as c:
            token = token_from(c.get("/login").text)
            response = c.post("/login", data=login_data(token))
            cookie = response.headers["set-cookie"].lower()
            assert "httponly" in cookie
            assert "samesite=lax" in cookie
            assert ("secure" in cookie.replace("samesite", "")) is expect_secure


def test_login_is_rate_limited(anon_client):
    token = token_from(anon_client.get("/login").text)
    for _ in range(5):
        response = anon_client.post("/login", data=login_data(token, password="nope"))
        assert response.status_code == 401
    # Even the right password is refused once the limit is hit.
    response = anon_client.post("/login", data=login_data(token))
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
    other.dependency_overrides = app.dependency_overrides
    with TestClient(other, follow_redirects=False) as stranger:
        token = token_from(stranger.get("/login").text)
        stranger.post("/login", data=login_data(token))
        forged = stranger.cookies.get("session")
    assert forged
    with TestClient(app, follow_redirects=False) as c:
        c.cookies.set("session", forged)
        assert c.get(OWNER_ONLY).status_code == 303


def test_logout_ends_the_session(client):
    response = post(client, "/logout")
    assert response.status_code == 303
    assert client.get(OWNER_ONLY).status_code == 303


def test_production_refuses_to_start_without_a_session_secret():
    with pytest.raises(RuntimeError, match="session_secret"):
        create_app(make_settings(app_env="production", session_secret=""))


@pytest.mark.parametrize("env", ["production", "development"])
@pytest.mark.parametrize("secret", ["", "short", "x" * 31])
def test_a_short_session_secret_never_starts_outside_the_test_environment(env, secret):
    """An empty or short key is a public key: anyone could forge an admin session."""
    with pytest.raises(RuntimeError, match=r"session_secret.*at least 32 characters"):
        create_app(make_settings(app_env=env, session_secret=secret))


@pytest.mark.parametrize("env", ["production", "development"])
def test_a_32_character_session_secret_starts(env):
    assert create_app(make_settings(app_env=env, session_secret="x" * 32)) is not None


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


# ---- fix round 1 ----


@pytest.mark.parametrize("env", ["prod", "Production", "staging", ""])
def test_unknown_app_env_refuses_to_start_even_with_secrets(env):
    with pytest.raises(RuntimeError, match="app_env"):
        create_app(make_settings(app_env=env))


@pytest.mark.parametrize("env", ["production", "prod", "development"])
def test_prod_style_env_with_empty_secrets_never_starts(env):
    with pytest.raises(RuntimeError):
        create_app(make_settings(app_env=env, session_secret=""))


def test_only_the_test_environment_may_start_without_a_secret():
    create_app(make_settings(app_env="test", session_secret=""))


def test_empty_password_never_logs_in_even_in_dev(app, session):
    make_user(session, password=None)  # an account with no password (Google-only)
    dev = create_app(make_settings(session_secret=""))
    dev.dependency_overrides = app.dependency_overrides
    app = dev
    with TestClient(app, follow_redirects=False) as c:
        token = token_from(c.get("/login").text)
        response = c.post("/login", data=login_data(token, password=""))
        assert response.status_code == 401


@pytest.mark.parametrize("path", ["/health", "/login", "/"])
def test_non_ascii_session_cookie_does_not_crash(anon_client, path):
    response = anon_client.get(
        path, headers={"cookie": "session=caf\u00e9.\u00e9\u00e9".encode("latin-1")}
    )
    assert response.status_code in (200, 303)


def test_non_ascii_password_is_a_normal_failure(anon_client):
    token = token_from(anon_client.get("/login").text)
    response = anon_client.post("/login", data=login_data(token, password="p\u00e4ss\u2713"))
    assert response.status_code == 401


def test_logout_revokes_a_copied_session_cookie(app, client):
    stolen = client.cookies.get("session")
    assert post(client, "/logout").status_code == 303
    with TestClient(app, follow_redirects=False) as thief:
        thief.cookies.set("session", stolen)
        assert thief.get(OWNER_ONLY).status_code == 303
        assert thief.post("/meetings", data={}).status_code in (303, 403)


def test_new_login_after_logout_works(client):
    post(client, "/logout")
    token = token_from(client.get("/login").text)
    response = client.post("/login", data=login_data(token))
    assert response.status_code == 303
    assert client.get("/").status_code == 200


def test_changing_the_session_secret_revokes_sessions(app, client):
    old_cookie = client.cookies.get("session")
    changed = create_app(make_settings(session_secret="z" * 40))
    changed.dependency_overrides = app.dependency_overrides
    with TestClient(changed, follow_redirects=False) as c:
        c.cookies.set("session", old_cookie)
        assert c.get(OWNER_ONLY).status_code == 303


def test_limiter_is_safe_under_parallel_attempts():
    limiter = LoginLimiter(limit=5, window=1000)
    allowed: list[bool] = []

    def attempt():
        allowed.append(limiter.try_acquire("same", "owner"))

    threads = [threading.Thread(target=attempt) for _ in range(40)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert allowed.count(True) == 5


def test_right_password_after_some_failures_logs_in(anon_client, owner):
    token = token_from(anon_client.get("/login").text)
    for _ in range(4):
        anon_client.post("/login", data=login_data(token, password="nope"))
    ok = anon_client.post("/login", data=login_data(token))
    assert ok.status_code == 303


def test_retry_restarts_the_polling_clock(client, template, session):
    old = datetime.now(UTC) - timedelta(hours=20)
    meeting = add_meeting(
        session,
        template,
        status="failed",
        step="notes",
        failure_reason="x",
        transcript=TRANSCRIPT,
        started_polling_at=old,
    )
    post(client, f"/meetings/{meeting.id}/retry", page=f"/meetings/{meeting.id}")
    session.refresh(meeting)
    assert meeting.started_polling_at > datetime.now(UTC) - timedelta(minutes=5)


def test_no_speech_failure_cannot_be_retried(client, template, session):
    meeting = add_meeting(
        session,
        template,
        status="failed",
        step="transcribing",
        failure_reason=REASON_NO_SPEECH,
    )
    page = client.get(f"/meetings/{meeting.id}").text
    assert f"/meetings/{meeting.id}/retry" not in page
    assert "no speech was found" in page
    response = post(client, f"/meetings/{meeting.id}/retry", page=f"/meetings/{meeting.id}")
    assert response.status_code == 409
    session.refresh(meeting)
    assert meeting.status == "failed"


@pytest.mark.parametrize("bad", ["abc", "99999999999999999999", "-1", "0", "1.5"])
def test_bad_ids_in_paths_are_a_clean_404(client, bad):
    assert client.get(f"/meetings/{bad}").status_code == 404
    assert post(client, f"/meetings/{bad}/retry").status_code == 404
    assert post(client, f"/meetings/{bad}/regenerate", {"template_id": "1"}).status_code == 404
    assert post(client, f"/templates/{bad}", {"name": "a", "instructions": "b"}).status_code == 404
    assert post(client, f"/vocabulary/{bad}/delete", page="/vocabulary").status_code in (
        303,
        404,
    )


@pytest.mark.parametrize("bad", ["99999999999999999999", "\u00b2", "-3", "x", ""])
def test_bad_template_ids_in_forms_are_a_plain_error(client, template, session, bad):
    meeting = ready_meeting(session, template)
    new = post(client, "/meetings", {"url": MEET, "template_id": bad})
    assert new.status_code == 400
    regen = post(
        client,
        f"/meetings/{meeting.id}/regenerate",
        {"template_id": bad},
        f"/meetings/{meeting.id}",
    )
    assert regen.status_code == 400


def test_refused_submit_does_not_echo_a_zoom_password(client, template, session):
    add_meeting(session, template, status="bot_sent")
    response = post(client, "/meetings", {"url": ZOOM, "template_id": template.id})
    assert response.status_code == 409
    assert "SECRETPWD" not in response.text
    bad = post(client, "/meetings", {"url": ZOOM + "&x=\x01", "template_id": template.id})
    assert "SECRETPWD" not in bad.text


def test_notes_code_blocks_scroll_inside_the_page():
    css = (Path(__file__).parent.parent / "app/static/components.css").read_text()
    assert re.search(r"\.notes pre\s*\{[^}]*overflow-x:\s*auto", css)


HUGE = "9" * 5000


@pytest.mark.parametrize(
    "path",
    [
        f"/meetings/{HUGE}",
        f"/meetings/{HUGE}/retry",
        f"/meetings/{HUGE}/regenerate",
        f"/templates/{HUGE}",
        f"/vocabulary/{HUGE}/delete",
        "/meetings/12345678901",
    ],
)
def test_huge_ids_in_paths_are_404_logged_in_or_out(client, anon_client, path):
    assert client.get(path).status_code == 404
    token = token_from(client.get("/").text)
    assert client.post(path, data={"csrf_token": token, "template_id": "1"}).status_code == 404
    with TestClient(client.app, follow_redirects=False) as outsider:
        assert outsider.get(path).status_code == 404
        assert outsider.post(path, data={}).status_code == 404


def test_huge_template_id_in_forms_is_a_plain_400(client, template, session):
    meeting = ready_meeting(session, template)
    assert post(client, "/meetings", {"url": MEET, "template_id": HUGE}).status_code == 400
    regen = post(
        client,
        f"/meetings/{meeting.id}/regenerate",
        {"template_id": HUGE},
        f"/meetings/{meeting.id}",
    )
    assert regen.status_code == 400


# ---- health ----


def test_health_is_ok_with_the_database_up(anon_client):
    response = anon_client.get("/health")
    assert (response.status_code, response.text) == (200, "ok")


def test_health_is_503_without_detail_when_the_database_is_down(app, anon_client):
    class DownSession:
        def execute(self, *_a, **_k):
            raise OperationalError("select 1", {}, Exception("password authentication failed"))

        def close(self):
            pass

    app.dependency_overrides[get_session] = lambda: DownSession()
    response = anon_client.get("/health")
    assert response.status_code == 503
    assert response.text == "unavailable"
    assert "password" not in response.text
    assert "set-cookie" not in response.headers


# ---- cancel ----


def test_cancel_stops_the_bot_and_fails_the_meeting_without_openai(
    client, fake_vexa, fake_openai, template, session
):
    meeting = add_meeting(session, template)
    page = client.get(f"/meetings/{meeting.id}").text
    assert f"/meetings/{meeting.id}/cancel" in page
    response = post(client, f"/meetings/{meeting.id}/cancel", page=f"/meetings/{meeting.id}")
    assert response.status_code == 303
    assert fake_vexa.stopped == [("google_meet", "abc-defg-hij")]
    session.refresh(meeting)
    assert meeting.status == "failed"
    assert meeting.failure_reason == "You cancelled this meeting before it was recorded."
    assert fake_openai.chat_calls == [] and fake_openai.transcription_calls == 0
    failed = client.get(f"/meetings/{meeting.id}").text
    assert "You cancelled this meeting" in failed
    assert f"/meetings/{meeting.id}/cancel" not in failed


def test_cancel_failure_keeps_the_meeting_waiting(client, fake_vexa, template, session):
    fake_vexa.stop_error = VexaError("stop_bot failed: secret detail")
    meeting = add_meeting(session, template)
    response = post(client, f"/meetings/{meeting.id}/cancel", page=f"/meetings/{meeting.id}")
    assert response.status_code == 502
    assert "secret" not in response.text
    session.refresh(meeting)
    assert meeting.status == "bot_sent"


@pytest.mark.parametrize(
    "status,step", [("processing", "audio"), ("ready", "done"), ("failed", "notes")]
)
def test_cancel_only_applies_to_waiting_meetings(
    client, fake_vexa, template, session, status, step
):
    meeting = add_meeting(session, template, status=status, step=step)
    response = post(client, f"/meetings/{meeting.id}/cancel", page=f"/meetings/{meeting.id}")
    assert response.status_code == 409
    assert getattr(fake_vexa, "stopped", []) == []
    session.refresh(meeting)
    assert meeting.status == status
    assert f"/meetings/{meeting.id}/cancel" not in client.get(f"/meetings/{meeting.id}").text


def test_cancel_unknown_meeting_is_404(client):
    assert post(client, "/meetings/999/cancel").status_code == 404


# ---- notices on the meeting page ----

SPEAKERS_NOTICE = "Speaker names unavailable for part of this transcript."
REMOVED_NOTICE = "The bot left the meeting early or was removed. The notes may be incomplete."


def test_incomplete_speaker_labels_show_a_notice(client, template, session):
    meeting = ready_meeting(session, template, speakers_incomplete=True)
    assert SPEAKERS_NOTICE in client.get(f"/meetings/{meeting.id}").text
    other = ready_meeting(session, template)
    assert SPEAKERS_NOTICE not in client.get(f"/meetings/{other.id}").text


@pytest.mark.parametrize("reason", ["evicted", "max_bot_time_exceeded"])
def test_removal_reasons_show_a_notice_on_a_ready_meeting(client, template, session, reason):
    meeting = ready_meeting(session, template, completion_reason=reason)
    page = client.get(f"/meetings/{meeting.id}").text
    assert REMOVED_NOTICE in page
    assert "Ready" in page


@pytest.mark.parametrize("reason", [None, "stopped", "left_alone", "startup_alone", "odd"])
def test_other_completion_reasons_show_no_notice(client, template, session, reason):
    meeting = ready_meeting(session, template, completion_reason=reason)
    assert REMOVED_NOTICE not in client.get(f"/meetings/{meeting.id}").text


# ---- worker heartbeat ----

STALE = "The background worker has not checked in"


def test_home_page_warns_when_the_worker_has_not_checked_in(client, session):
    assert STALE in client.get("/").text  # no row, no heartbeat yet
    session.add(AppState(id=1, worker_heartbeat_at=datetime.now(UTC) - timedelta(minutes=21)))
    session.commit()
    assert STALE in client.get("/").text


def test_home_page_has_no_warning_during_a_long_worker_pass(client, session):
    # Transcribing an hour-long meeting keeps the worker in one pass for several minutes.
    session.add(AppState(id=1, worker_heartbeat_at=datetime.now(UTC) - timedelta(minutes=19)))
    session.commit()
    assert STALE not in client.get("/").text


def test_regenerate_and_cancel_buttons_disable_on_submit(client, template, session):
    ready = ready_meeting(session, template)
    assert "data-disable-on-submit" in client.get(f"/meetings/{ready.id}").text
    waiting = add_meeting(session, template)
    assert "data-disable-on-submit" in client.get(f"/meetings/{waiting.id}").text


def test_meeting_page_says_when_a_new_bot_was_sent(client, template, session):
    retrying = add_meeting(session, template, join_attempts=2)
    page = client.get(f"/meetings/{retrying.id}").text
    assert "Zoom turned the bot away, so a new one was sent (attempt 2 of 3)." in page
    first = add_meeting(session, template)
    assert "Zoom turned the bot away" not in client.get(f"/meetings/{first.id}").text


# ---- public pages ----

PUBLIC_PAGES = ("/", "/privacy", "/terms")


def test_logged_out_home_is_the_landing_page_and_shows_no_meetings(anon_client, template, session):
    ready_meeting(session, template)
    page = anon_client.get("/")
    assert page.status_code == 200
    assert "Clear notes from every meeting" in page.text
    assert TRANSCRIPT not in page.text
    assert 'href="/login"' in page.text
    assert 'action="/meetings"' not in page.text


def test_logged_in_home_is_still_the_meetings_page(client, template, session):
    page = client.get("/").text
    assert 'action="/meetings"' in page
    assert "Clear notes from every meeting" not in page


@pytest.mark.parametrize("path", PUBLIC_PAGES)
def test_public_pages_need_no_login(anon_client, path):
    page = anon_client.get(path)
    assert page.status_code == 200, path
    assert 'name="viewport"' in page.text
    # Links out (Google's policy pages) are fine; loading anything from elsewhere is not.
    assert not re.search(r'src="https?://|<link[^>]+href="https?://', page.text), path
    assert 'href="/privacy"' in page.text and 'href="/terms"' in page.text


@pytest.mark.parametrize("path", ("/privacy", "/terms"))
def test_legal_pages_name_the_company(anon_client, path):
    page = anon_client.get(path).text
    assert "Beyrichq LTD" in page
    assert "Lagos, Nigeria" in page
    assert "Beyric Technologies" not in page
    assert "[" not in re.sub(r"<[^>]+>", "", page)  # no unfilled placeholders


def test_privacy_page_has_the_google_limited_use_statement(anon_client):
    page = anon_client.get("/privacy").text
    assert "Google API Services User Data Policy" in page
    assert "Limited Use" in page


def test_privacy_page_states_the_real_audio_retention(anon_client, app):
    days = app.state.settings.audio_retention_days
    assert f"{days} days" in anon_client.get("/privacy").text


@pytest.mark.parametrize("path", ("/privacy", "/terms"))
def test_legal_pages_are_public_for_get_only(anon_client, path):
    assert anon_client.post(path, data={}).status_code in (403, 405)


def test_public_pages_do_not_open_other_owner_paths(anon_client):
    for path in ("/meetings/1", "/templates", "/vocabulary", "/privacy/../templates"):
        assert anon_client.get(path).status_code in (303, 404), path


# ---- design system ----


def test_styleguide_needs_a_login(anon_client):
    response = anon_client.get("/styleguide")
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_styleguide_renders_and_is_never_indexed(client):
    response = client.get("/styleguide")
    assert response.status_code == 200
    assert response.headers["x-robots-tag"] == "noindex, nofollow"
    assert '<meta name="robots" content="noindex, nofollow">' in response.text
    for heading in ("Colour tokens", "Buttons", "Form fields", "Menu and dialog"):
        assert heading in response.text


def test_pages_load_the_shared_stylesheets_and_font(anon_client, owner):
    login = anon_client.get("/login").text  # logged out: the centred auth layout
    privacy = anon_client.get("/privacy").text  # public layout
    token = token_from(login)
    anon_client.post("/login", data=login_data(token))
    app_page = anon_client.get("/templates").text  # logged in: the sidebar shell
    for html in (login, privacy, app_page):
        for asset in ("/static/tokens.css", "/static/components.css", "/static/app.css"):
            assert asset in html
        assert "/static/fonts/geist-latin-wght-normal.woff2" in html


def test_csp_allows_only_our_own_fonts(client):
    csp = client.get("/templates").headers["content-security-policy"]
    assert "font-src 'self'" in csp
    assert "'unsafe-inline'" not in csp


def test_static_fonts_and_favicon_are_served(client):
    for path in (
        "/static/fonts/geist-latin-wght-normal.woff2",
        "/static/fonts/geist-latin-ext-wght-normal.woff2",
        "/static/fonts/geist-mono-latin-wght-normal.woff2",
        "/static/favicon.svg",
    ):
        assert client.get(path).status_code == 200, path


def test_the_app_shell_marks_the_current_page(client):
    html = client.get("/templates").text
    assert re.search(r'<a class="nav-link" href="/templates" aria-current="page">', html)
    assert not re.search(r'href="/" aria-current="page"', html)
